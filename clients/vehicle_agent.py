"""One vehicle = one thread with its own TCP connection.

Run standalone: python -m clients.vehicle_agent --id V0001 --approach N --security on
"""
from __future__ import annotations

import argparse
import threading
import time
from typing import Callable, Optional

import config
from common import vehicle_model as vm
from common.net import ClientConnection
from common.security import KeyStore, verify


class VehicleAgent:
    """A simulated connected vehicle.

    It registers, reports its position every 0.5 s, obeys the signal (treating
    it as red if no valid SIGNAL_STATE arrived for 3 s), keeps the gap given by
    QUEUE_INFO, reconnects with exponential back-off, and sends CROSSED.
    """

    def __init__(self, vid: str, approach: str, host: str = config.HOST, port: int = config.PORT,
                 keys: Optional[KeyStore] = None, security: bool = True, emergency: bool = False,
                 record_path: Optional[str] = None, on_done: Optional[Callable] = None,
                 start_distance: float = config.SPAWN_DISTANCE_M, max_lifetime_s: float = 600.0):
        self.vid, self.approach = vid, approach
        self.host, self.port = host, port
        self.security = security
        self.keys = keys
        self.emergency = emergency
        self.record_path = record_path
        self.on_done = on_done
        self.max_lifetime_s = max_lifetime_s
        self.state = vm.VehicleState(distance_m=start_distance)
        self.signal: Optional[dict] = None        # last valid SIGNAL_STATE payload
        self.signal_t = 0.0                       # monotonic time it arrived
        self.ahead_distance: Optional[float] = None
        self.conn: Optional[ClientConnection] = None
        self.registered = False
        self.done = False
        self.crossed_sent = False
        self.emergency_requested = False
        self.emergency_cleared = False
        self.registrations: list[float] = []      # monotonic times of REGISTER_ACKs
        self.time_scale = 1.0                     # demo speed-up, taken from REGISTER_ACK
        self.disconnects = 0
        self.security_rejections: list[str] = []  # e.g. fake SIGNAL_STATE rejected
        self.errors: list[str] = []
        self._backoff_i = 0
        self._next_connect = 0.0
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- helpers
    def _key(self) -> Optional[bytes]:
        if not self.security or self.keys is None:
            return None
        return self.keys.key_for(self.vid) if self.emergency else self.keys.fleet_key

    def light(self, now: Optional[float] = None) -> str:
        """Signal colour for this approach; red if state is missing or older than 3 s."""
        now = time.monotonic() if now is None else now
        if self.signal is None or now - self.signal_t > config.CLIENT_SIGNAL_TIMEOUT_S:
            return "red"
        return self.signal["ns"] if config.DIRECTION_OF[self.approach] == "ns" else self.signal["ew"]

    def handle_message(self, env: dict) -> None:
        """Process one message from the server (public so tests can inject messages)."""
        if self.security and self.keys is not None and not verify(env, self.keys.server_key):
            self.security_rejections.append(f"BAD_SIGNATURE {env.get('type')}")
            return  # keep the last valid state
        t = env.get("type")
        p = env.get("payload", {})
        if t == "SIGNAL_STATE":
            self.signal, self.signal_t = p, time.monotonic()
        elif t == "QUEUE_INFO":
            self.ahead_distance = p.get("ahead_distance_m")
        elif t == "REGISTER_ACK" and p.get("accepted"):
            self.time_scale = float(p.get("time_scale", 1.0) or 1.0)
            self.registered = True
            self._backoff_i = 0
            self.registrations.append(time.monotonic())
        elif t == "ERROR":
            self.errors.append(p.get("code", "?"))

    def _send(self, msg_type: str, payload: Optional[dict] = None) -> None:
        if self.conn is None:
            return
        try:
            self.conn.send(msg_type, payload)
        except OSError:
            self._drop()

    def _drop(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
            self.disconnects += 1
        self.registered = False
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        """Exponential back-off: 0.5, 1, 2, 4, then 5 s."""
        steps = config.BACKOFF_STEPS_S
        delay = steps[self._backoff_i] if self._backoff_i < len(steps) else config.BACKOFF_MAX_S
        self._backoff_i += 1
        self._next_connect = time.monotonic() + min(delay, config.BACKOFF_MAX_S)

    def _try_connect(self) -> None:
        conn = ClientConnection(self.host, self.port, self.vid, self._key(),
                                record_path=self.record_path)
        try:
            conn.connect(timeout=1.0)
        except OSError:
            self._schedule_retry()
            return
        self.conn = conn
        role = "emergency" if self.emergency else "vehicle"
        self._send("REGISTER", {"role": role, "approach": self.approach,
                                "resume": bool(self.registrations)})

    # ------------------------------------------------------------- main loop
    def run(self) -> None:
        dt = 1.0 / config.CLIENT_TICK_HZ  # wall-clock tick; doubled to 20 Hz when sped up
        start = time.monotonic()
        last_pos = last_sent = -1e9
        last_tick = time.monotonic()
        while not self._stop.is_set() and not self.done:
            now = time.monotonic()
            if now - start > self.max_lifetime_s:
                break
            if self.conn is None and now >= self._next_connect:
                self._try_connect()
            if self.conn is not None:
                try:
                    for env in self.conn.recv(timeout=max(0.0, last_tick + dt - time.monotonic())):
                        self.handle_message(env)
                except (OSError, ValueError):
                    self._drop()
            else:
                time.sleep(max(0.0, last_tick + dt - time.monotonic()))
            now = time.monotonic()
            dt = 1.0 / (config.CLIENT_TICK_HZ * (2 if self.time_scale > 2 else 1))
            if now - last_tick < dt:
                continue
            # Kinematics advance in simulated seconds (wall time x speed-up).
            step_dt, last_tick = min(now - last_tick, 0.5) * self.time_scale, now
            if self.registered or self.signal is not None or self.registrations:
                vm.step(self.state, step_dt, self.light(now), self.ahead_distance)
            if not self.registered:
                continue
            if self.emergency:
                if not self.emergency_requested and self.state.distance_m <= config.EMERGENCY_REQUEST_DIST_M:
                    eta = self.state.distance_m / max(self.state.speed_mps, 1.0)
                    self._send("EMERGENCY_REQUEST", {"approach": self.approach, "eta_s": round(eta, 2)})
                    self.emergency_requested = True
                if self.emergency_requested and not self.emergency_cleared and self.state.distance_m < 0:
                    self._send("EMERGENCY_CLEAR", {"approach": self.approach})
                    self.emergency_cleared = True
            if self.state.crossed:
                self._send("CROSSED", {"approach": self.approach,
                                       "wait_time_s": round(self.state.wait_time_s, 2),
                                       "travel_time_s": round(self.state.travel_time_s, 2)})
                if self.conn is not None:
                    self.crossed_sent = True
                    self.done = True
                continue
            if now - last_pos >= max(config.POSITION_PERIOD_S / self.time_scale, 0.1):
                self._send("POSITION_UPDATE", {
                    "approach": self.approach, "distance_m": round(self.state.distance_m, 2),
                    "speed_mps": round(self.state.speed_mps, 2),
                    "state": "stopped" if self.state.stopped else "moving"})
                last_pos = last_sent = now
            elif now - last_sent >= config.HEARTBEAT_PERIOD_S:
                self._send("HEARTBEAT")
                last_sent = now
        if self.conn is not None:
            time.sleep(0.05)
            self.conn.close()
            self.conn = None
        if self.on_done:
            self.on_done(self)

    def start(self) -> "VehicleAgent":
        self.thread = threading.Thread(target=self.run, name=self.vid, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one vehicle client")
    ap.add_argument("--id", default="V0001")
    ap.add_argument("--approach", choices=list(config.APPROACHES), default="N")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--security", choices=["on", "off"], default="on")
    ap.add_argument("--keys", default=config.KEYS_FILE)
    args = ap.parse_args()
    keys = KeyStore.load(args.keys) if args.security == "on" else None
    agent = VehicleAgent(args.id, args.approach, args.host, args.port, keys, args.security == "on")
    agent.run()
    print(f"{args.id}: crossed={agent.crossed_sent} wait={agent.state.wait_time_s:.1f}s "
          f"travel={agent.state.travel_time_s:.1f}s reconnects={max(0, len(agent.registrations) - 1)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
