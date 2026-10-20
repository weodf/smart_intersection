"""TCP intersection controller server.

Threads: one accept loop, one control loop (signal ticks and broadcasts), and
per client one reader and one writer. Writers drain a bounded queue, so a slow
client is dropped instead of blocking anyone else.

Run: python -m server.intersection_server --security on --signal adaptive
"""
from __future__ import annotations

import argparse
import os
import queue
import socket
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Optional

import config
from common.logging_utils import CsvLog
from common.protocol import LineBuffer, ProtocolError, decode_line, encode, make_envelope
from common.security import KeyStore
from server.controller import ControllerConfig, SignalController

SERVER_ID = "SERVER"


@dataclass
class VehicleRecord:
    """Server-side view of one vehicle; survives short disconnects (session grace)."""
    vid: str
    approach: str
    emergency: bool
    seq: int
    session: Optional["Session"] = None
    distance_m: Optional[float] = None
    speed_mps: float = 0.0
    state: str = "moving"
    disconnected_at: Optional[float] = None


class Session:
    """One client connection."""

    def __init__(self, server: "IntersectionServer", sock: socket.socket, addr):
        self.server = server
        self.sock = sock
        self.peer = f"{addr[0]}:{addr[1]}"
        self.sid: Optional[str] = None
        self.role: Optional[str] = None
        self.outq: queue.Queue = queue.Queue(maxsize=config.SEND_QUEUE_MAX)
        self.closed = False
        self.accepted = 0  # messages that passed every check and were dispatched

    def send(self, data: bytes) -> None:
        """Queue bytes for the writer thread; drop the client if it cannot keep up."""
        if self.closed:
            return
        try:
            self.outq.put_nowait(data)
        except queue.Full:
            self.server.close_session(self, "send queue full (slow client)")

    def writer(self) -> None:
        """Send queued messages, batching whatever is waiting into one sendall."""
        while True:
            batch = [self.outq.get()]
            while batch[-1] is not None and len(batch) < 256:
                try:
                    batch.append(self.outq.get_nowait())
                except queue.Empty:
                    break
            done = batch[-1] is None
            data = b"".join(b for b in batch if b is not None)
            try:
                if data:
                    self.sock.sendall(data)
            except OSError:
                self.server.close_session(self, "send failed")
                return
            if done:
                return


class IntersectionServer:
    """The intersection controller: protocol handling, security and signal control."""

    def __init__(self, host: str = config.HOST, port: int = config.PORT, security: bool = True,
                 signal_mode: str = "adaptive", keys: Optional[KeyStore] = None,
                 log_dir: str = config.LOG_DIR, controller_cfg: Optional[ControllerConfig] = None,
                 time_scale: float = 1.0):
        if security and keys is None:
            raise ValueError("security on requires keys")
        self.host, self.port = host, port
        self.security = security
        self.keys = keys
        self.signal_mode = signal_mode
        self.lock = threading.RLock()
        # Demo speed-up: the signal controller runs on a simulated clock that advances
        # ``time_scale`` times faster than the wall clock. Security timing (freshness
        # window, rate limits, session grace) always stays in real time.
        self.time_scale = max(0.1, float(time_scale))
        self._t0 = time.time()
        self.controller = SignalController(signal_mode, controller_cfg, now=self._t0)
        self.sessions: set[Session] = set()
        self.vehicles: dict[str, VehicleRecord] = {}
        self.seq = 0
        self.last_position_t = 0.0
        self.last_fed_t = 0.0
        self.waits: list[float] = []
        self.crossed = 0
        self.preemptions = 0
        self.security_counts: Counter = Counter()
        self.recent_security: deque = deque(maxlen=10)
        self.sec_log = CsvLog(os.path.join(log_dir, "security_events.csv"),
                              ["wall_time", "sender", "peer_addr", "msg_type", "reason"])
        self.event_log = CsvLog(os.path.join(log_dir, "events.csv"),
                                ["wall_time", "event", "approach", "sender", "detail"])
        self.withhold_signal = False  # test hook: stop sending SIGNAL_STATE (fail-safe experiment)
        self.running = False
        self._listener: Optional[socket.socket] = None
        self._threads: list[threading.Thread] = []
        self._last_state = None
        self._preempt_sender: Optional[str] = None

    def sim_time(self, wall: Optional[float] = None) -> float:
        """Simulated time used by the signal controller (equals wall time at 1x)."""
        wall = time.time() if wall is None else wall
        return self._t0 + (wall - self._t0) * self.time_scale

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "IntersectionServer":
        """Bind, listen and start the background threads (returns immediately)."""
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        ls.listen(256)
        ls.settimeout(0.2)
        self.port = ls.getsockname()[1]
        self._listener = ls
        self.running = True
        for target in (self._accept_loop, self._control_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        self._log_event("SERVER_START", None, SERVER_ID,
                        f"security={'on' if self.security else 'off'} signal={self.signal_mode} "
                        f"speed={self.time_scale:g}x")
        return self

    def stop(self) -> None:
        """Stop accepting, close every client and join the background threads."""
        self.running = False
        for t in self._threads:
            t.join(timeout=2)
        if self._listener:
            self._listener.close()
        with self.lock:
            sessions = list(self.sessions)
        for s in sessions:
            self.close_session(s, "server stopping", log=False)

    def serve_forever(self) -> None:
        self.start()
        print(f"Intersection server on {self.host}:{self.port} security="
              f"{'on' if self.security else 'off'} signal={self.signal_mode} speed={self.time_scale:g}x",
              flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # ------------------------------------------------------------- network
    def _accept_loop(self) -> None:
        while self.running:
            try:
                sock, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(config.SEND_TIMEOUT_S)
            session = Session(self, sock, addr)
            with self.lock:
                self.sessions.add(session)
            threading.Thread(target=session.writer, daemon=True).start()
            threading.Thread(target=self._reader, args=(session,), daemon=True).start()

    def _reader(self, s: Session) -> None:
        buf = LineBuffer()
        while self.running and not s.closed:
            try:
                data = s.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            overflow = None
            try:
                lines = buf.feed(data)
            except ProtocolError as e:
                overflow, lines = e, getattr(e, "lines", [])
            for line in lines:
                if s.closed:
                    break
                try:
                    self.handle_line(s, line)
                except Exception as e:  # one bad message must never kill the server
                    self._reject(s, None, "MALFORMED", f"internal error: {type(e).__name__}")
            if overflow is not None:
                self._reject(s, None, overflow.code, overflow.reason)
                time.sleep(0.05)  # let the writer flush the error first
                break
        self.close_session(s, "connection closed")

    def close_session(self, s: Session, reason: str, log: bool = True) -> None:
        with self.lock:
            if s.closed:
                return
            s.closed = True
            self.sessions.discard(s)
            rec = self.vehicles.get(s.sid) if s.sid else None
            if rec is not None and rec.session is s:
                rec.session = None
                rec.disconnected_at = time.time()
        if log:
            self._log_event("DISCONNECT", None, s.sid or "?", f"{reason}; accepted={s.accepted}")
        try:
            s.outq.put_nowait(None)
        except queue.Full:
            pass
        try:
            s.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        s.sock.close()

    def _out(self, msg_type: str, payload: dict, max_bytes: int = config.MAX_MSG_BYTES) -> bytes:
        env = make_envelope(msg_type, SERVER_ID, payload)
        # TODO(security): sign outgoing messages with the server key
        return encode(env, max_bytes)

    # ------------------------------------------------------------ errors
    def _reject(self, s: Session, env: Optional[dict], code: str, reason: str) -> None:
        sender = env.get("sender", "?") if isinstance(env, dict) else (s.sid or "?")
        mtype = env.get("type", "?") if isinstance(env, dict) else "?"
        now = time.time()
        self.sec_log.write([f"{now:.3f}", sender, s.peer, mtype, f"{code}: {reason}"])
        with self.lock:
            self.security_counts[code] += 1
            self.recent_security.append({"time": round(now, 2), "sender": sender, "type": mtype,
                                         "reason": code, "detail": reason})
        s.send(self._out("ERROR", {"code": code, "reason": reason}))

    # ------------------------------------------------------------ messages
    def handle_line(self, s: Session, line: bytes) -> None:
        """Validate, authenticate and dispatch one received line."""
        now = time.time()
        try:
            env = decode_line(line)
        except ProtocolError as e:
            self._reject(s, None, e.code, e.reason)
            return
        proven = None
        # TODO(security): rate limit, HMAC verification, freshness/replay and authorisation
        s.accepted += 1
        mtype, p = env["type"], env["payload"]
        if mtype == "REGISTER":
            self._register(s, env, proven)
            return
        if s.sid is None:
            self._reject(s, env, "NOT_REGISTERED", "send REGISTER first")
            return
        if mtype == "POSITION_UPDATE":
            s.send(self._out("QUEUE_INFO", self._position(s, p, now)))
        elif mtype == "PING":
            s.send(self._out("PONG", {"t_send": p["t_send"], "t_server": now}))
        elif mtype == "CROSSED":
            with self.lock:
                self.vehicles.pop(s.sid, None)
                self.crossed += 1
                self.waits.append(float(p["wait_time_s"]))
        elif mtype in ("EMERGENCY_REQUEST", "EMERGENCY_CLEAR"):
            if s.role != "emergency":
                self._reject(s, env, "UNAUTHORISED", "not an emergency vehicle")
                return
            with self.lock:
                if mtype == "EMERGENCY_REQUEST":
                    already = self.controller.preempt is not None
                    if self.controller.request_preemption(p["approach"], self.sim_time(now)) \
                            and not already:
                        self.preemptions += 1
                        self._preempt_sender = s.sid
                else:
                    self.controller.clear_preemption(p["approach"], self.sim_time(now))
            self._flush_controller_events()
            self._broadcast_signal(now)
        # HEARTBEAT and anything else valid: nothing to do beyond liveness.

    def _register(self, s: Session, env: dict, proven: Optional[str]) -> None:
        p = env["payload"]
        role = p["role"]
        sid = env["sender"]
        old_session = None
        with self.lock:
            s.sid, s.role = sid, role
            if role != "observer":
                rec = self.vehicles.get(sid)
                if rec is not None and rec.session is not None and rec.session is not s:
                    old_session = rec.session
                if rec is None or not p.get("resume"):
                    self.seq += 1
                    rec = VehicleRecord(sid, p["approach"], role == "emergency", self.seq)
                    self.vehicles[sid] = rec
                rec.session, rec.disconnected_at = s, None
            state = self.controller.state(self.sim_time())
        if old_session is not None:
            self.close_session(old_session, "replaced by new connection with same ID")
        self._log_event("REGISTER", p.get("approach"), sid,
                        f"role={role} resume={bool(p.get('resume'))} peer={s.peer}")
        s.send(self._out("REGISTER_ACK", {
            "accepted": True, "reason": "ok", "security_mode": "on" if self.security else "off",
            "signal_mode": self.signal_mode, "time_scale": self.time_scale}))
        if not self.withhold_signal:
            s.send(self._out("SIGNAL_STATE", state.as_payload()))

    def _position(self, s: Session, p: dict, now: float) -> dict:
        """Store a position report and compute QUEUE_INFO for the sender."""
        with self.lock:
            rec = self.vehicles.get(s.sid)
            if rec is None:
                return {"ahead": 0, "ahead_distance_m": None}
            rec.distance_m = float(p["distance_m"])
            rec.speed_mps = float(p["speed_mps"])
            rec.state = p["state"]
            self.last_position_t = now
            me = (rec.distance_m, rec.seq)
            ahead = [v.distance_m for v in self.vehicles.values()
                     if v is not rec and v.approach == rec.approach and v.distance_m is not None
                     and (v.distance_m, v.seq) < me]
        return {"ahead": len(ahead), "ahead_distance_m": max(ahead) if ahead else None}

    # ------------------------------------------------------------ control loop
    def _control_loop(self) -> None:
        period = 1.0 / config.CONTROL_HZ
        next_signal = next_snap = 0.0
        while self.running:
            now = time.time()
            with self.lock:
                for vid in [v for v, r in self.vehicles.items() if r.disconnected_at is not None
                            and now - r.disconnected_at > config.SESSION_GRACE_S]:
                    del self.vehicles[vid]
                connected = sum(1 for r in self.vehicles.values() if r.session is not None)
                if self.last_position_t > self.last_fed_t:
                    queues, zone = self._detection()
                    self.controller.update_detection(queues, zone, self.sim_time(now))
                    self.last_fed_t = self.last_position_t
                state = self.controller.tick(self.sim_time(now), connected)
            self._flush_controller_events()
            key = (state.phase, state.preemption, state.signal_mode)
            if key != self._last_state or now >= next_signal:
                self._last_state = key
                self._broadcast_signal(now)
                next_signal = now + 1.0 / config.SIGNAL_BROADCAST_HZ
            if now >= next_snap:
                self._send_snapshots(now)
                next_snap = now + 1.0 / config.SNAPSHOT_HZ
            time.sleep(max(0.0, period - (time.time() - now)))

    def _detection(self) -> tuple[dict, dict]:
        queues = {a: 0 for a in config.APPROACHES}
        zone = {a: 0 for a in config.APPROACHES}
        for r in self.vehicles.values():
            if r.distance_m is None or r.distance_m < 0:
                continue
            if r.speed_mps < config.STOPPED_SPEED_MPS:
                queues[r.approach] += 1
            if r.distance_m <= config.DETECTION_ZONE_M:
                zone[r.approach] += 1
        return queues, zone

    def _broadcast_signal(self, now: float) -> None:
        if self.withhold_signal:
            return
        with self.lock:
            data = self._out("SIGNAL_STATE", self.controller.state(self.sim_time(now)).as_payload())
            targets = [s for s in self.sessions if s.sid is not None]
        for s in targets:
            s.send(data)

    def snapshot_payload(self, now: float) -> dict:
        with self.lock:
            queues, _ = self._detection()
            vehicles = [{"id": r.vid, "approach": r.approach, "distance_m": round(r.distance_m, 1),
                         "state": r.state, "emergency": r.emergency}
                        for r in self.vehicles.values() if r.distance_m is not None]
            return {
                "signal": self.controller.state(self.sim_time(now)).as_payload(),
                "vehicles": vehicles,
                "queues": queues,
                "stats": {"avg_wait_s": round(sum(self.waits) / len(self.waits), 2) if self.waits else 0.0,
                          "crossed": self.crossed,
                          "connected": sum(1 for r in self.vehicles.values() if r.session),
                          "security_mode": "on" if self.security else "off",
                          "rejected": sum(self.security_counts.values()),
                          "time_scale": self.time_scale,
                          "sim_elapsed_s": round(self.sim_time(now) - self._t0, 1)},
                "security_events": list(self.recent_security),
            }

    def _send_snapshots(self, now: float) -> None:
        with self.lock:
            observers = [s for s in self.sessions if s.role == "observer"]
        if not observers:
            return
        data = self._out("SNAPSHOT", self.snapshot_payload(now), config.MAX_SNAPSHOT_BYTES)
        for s in observers:
            s.send(data)

    # ------------------------------------------------------------ event log
    def _flush_controller_events(self) -> None:
        with self.lock:
            events = self.controller.drain_events()
            sender = self._preempt_sender
            if any(e["event"] == "PREEMPT_END" for e in events):
                self._preempt_sender = None
        for e in events:
            self._log_event(e["event"], e["approach"],
                            sender if e["event"].startswith("PREEMPT") else SERVER_ID, e["detail"])

    def _log_event(self, event: str, approach: Optional[str], sender: str, detail: str = "") -> None:
        self.event_log.write([f"{time.time():.3f}", event, approach or "", sender, detail])


def main() -> None:
    ap = argparse.ArgumentParser(description="Intersection controller server")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--security", choices=["on", "off"], default="on")
    ap.add_argument("--signal", choices=["fixed", "adaptive"], default="adaptive")
    ap.add_argument("--keys", default=config.KEYS_FILE)
    ap.add_argument("--log-dir", default=config.LOG_DIR)
    ap.add_argument("--speed", type=float, default=1.0,
                    help="demo speed-up: traffic and signal timing run this many times faster")
    args = ap.parse_args()
    keys = KeyStore.load(args.keys) if os.path.exists(args.keys) else None
    if args.security == "on" and keys is None:
        raise SystemExit(f"{args.keys} not found: run 'python tools/gen_keys.py' first")
    IntersectionServer(args.host, args.port, args.security == "on", args.signal, keys,
                       args.log_dir, time_scale=args.speed).serve_forever()


if __name__ == "__main__":
    main()
