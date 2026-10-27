"""Reliability features: reconnect, malformed input, fail-safes, session resume.

Writes results/reliability.csv.
Run: python -m experiments.exp_reliability
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import tempfile
import time

from clients.vehicle_agent import VehicleAgent
from common.logging_utils import write_csv
from common.net import ClientConnection
from common.protocol import make_envelope
from common.security import attach_sig
from experiments.harness import ensure_keys, free_port, print_table, results_path
from server.intersection_server import IntersectionServer


def wait_until(pred, timeout, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def test_reconnect(keys, logdir) -> list:
    port = free_port()
    srv = IntersectionServer("127.0.0.1", port, True, "fixed", keys, logdir).start()
    agents = [VehicleAgent(f"R{i:03d}", "NSEW"[i % 4], port=port, keys=keys, security=True).start()
              for i in range(20)]
    wait_until(lambda: all(a.registrations for a in agents), 10)
    time.sleep(1.0)
    srv.stop()
    t_down = time.monotonic()
    time.sleep(3.0)
    srv = IntersectionServer("127.0.0.1", port, True, "fixed", keys, logdir).start()
    t_up = time.monotonic()
    ok = wait_until(lambda: all(len(a.registrations) >= 2 for a in agents), 15)
    times = [a.registrations[1] - t_up for a in agents if len(a.registrations) >= 2]
    outage = [a.registrations[1] - t_down for a in agents if len(a.registrations) >= 2]
    n = sum(len(a.registrations) >= 2 for a in agents)
    for a in agents:
        a.stop()
    srv.stop()
    return [["reconnect", "vehicles_reregistered", f"{n}/20", ok],
            ["reconnect", "mean_s_after_restart", round(statistics.fmean(times), 3) if times else "", ok],
            ["reconnect", "max_s_after_restart", round(max(times), 3) if times else "", ok],
            ["reconnect", "max_total_outage_s", round(max(outage), 3) if outage else "", ok]]


def malformed_lines(rng: random.Random, n: int) -> list[bytes]:
    good = make_envelope("PING", "V1", {"t_send": 1.0})
    kinds = []
    for _ in range(n):
        k = rng.randrange(8)
        if k == 0:
            line = b"{bad json " + bytes(rng.randrange(97, 123) for _ in range(rng.randrange(1, 40)))
        elif k == 1:
            line = bytes(rng.randrange(1, 255) for _ in range(rng.randrange(1, 200))).replace(b"\n", b"")
        elif k == 2:
            e = dict(good)
            e.pop(rng.choice(list(e)))
            line = json.dumps(e).encode()
        elif k == 3:
            e = dict(good, **{rng.choice(["ts", "v", "sender", "payload"]): rng.choice([[], "x", None, -1])})
            line = json.dumps(e).encode()
        elif k == 4:
            line = json.dumps(dict(good, type=rng.choice(["HACK", "", "ping"]))).encode()
        elif k == 5:
            line = json.dumps(rng.choice([[1, 2], "str", 42, None])).encode()
        elif k == 6:
            line = json.dumps(dict(good, payload={"t_send": "now"})).encode()
        else:
            line = b"A" * rng.randrange(4097, 9000)  # oversize -> server closes the connection
        kinds.append(line + b"\n")
    return kinds


def test_malformed(keys, logdir) -> list:
    port = free_port()
    srv = IntersectionServer("127.0.0.1", port, True, "fixed", keys, logdir).start()
    lines = malformed_lines(random.Random(7), 1000)
    closes = 0
    conn = ClientConnection("127.0.0.1", port, "FUZZ")
    conn.connect()
    for line in lines:
        conn.send_raw(line)
        if len(line) > 4097:  # oversize: the server answers MALFORMED and closes; reconnect
            try:
                end = time.monotonic() + 3.0
                while time.monotonic() < end:
                    conn.recv(0.2)
            except (OSError, ValueError):
                closes += 1
            conn.close()
            conn = ClientConnection("127.0.0.1", port, "FUZZ")
            conn.connect()
    time.sleep(0.5)
    rejected = sum(srv.security_counts.values())
    conn.close()
    good = ClientConnection("127.0.0.1", port, "V0001", keys.fleet_key, keys.server_key)
    good.connect()
    good.send("REGISTER", {"role": "vehicle", "approach": "N", "resume": False})
    ack = good.wait_for("REGISTER_ACK", 2)
    t0 = time.perf_counter()
    good.send("PING", {"t_send": t0})
    pong = good.wait_for("PONG", 2)
    rtt = (time.perf_counter() - t0) * 1000
    good.close()
    alive = srv.running and all(t.is_alive() for t in srv._threads)
    srv.stop()
    ok = alive and ack is not None and pong is not None
    return [["malformed_input", "lines_sent", len(lines), ok],
            ["malformed_input", "rejected_by_server", rejected, rejected >= len(lines) * 0.99],
            ["malformed_input", "connections_closed_for_oversize", closes,
             closes == sum(len(l) > 4097 for l in lines)],
            ["malformed_input", "server_alive_and_serving", alive and pong is not None, ok],
            ["malformed_input", "normal_client_rtt_ms_after", round(rtt, 3), ok]]


def test_signal_loss(keys, logdir) -> list:
    port = free_port()
    srv = IntersectionServer("127.0.0.1", port, True, "fixed", keys, logdir).start()
    v = VehicleAgent("S0001", "N", port=port, keys=keys, security=True).start()
    wait_until(lambda: v.signal is not None, 5)
    green_before = v.light() == "green"
    srv.withhold_signal = True
    t_cut = time.monotonic()
    went_red = wait_until(lambda: v.light() == "red", 6, 0.01)
    t_red = time.monotonic() - t_cut
    stopped = wait_until(lambda: v.state.speed_mps == 0.0, 25)
    time.sleep(0.3)
    dist = v.state.distance_m
    time.sleep(2.0)
    still = v.state.speed_mps == 0.0 and v.state.distance_m >= 0
    srv.withhold_signal = False
    srv._broadcast_signal(time.time())
    moved = wait_until(lambda: v.state.speed_mps > 1.0, 5)
    v.stop()
    srv.stop()
    return [["signal_loss_failsafe", "light_was_green_before_cut", green_before, green_before],
            ["signal_loss_failsafe", "seconds_until_client_treats_signal_as_red", round(t_red, 2),
             went_red and 2.9 <= t_red <= 3.3],
            ["signal_loss_failsafe", "vehicle_stopped_at_or_before_line", stopped and dist >= 0,
             stopped and dist >= 0],
            ["signal_loss_failsafe", "stop_position_m_before_line", round(dist, 2), dist >= 0],
            ["signal_loss_failsafe", "stays_stopped_while_withheld", still, still],
            ["signal_loss_failsafe", "moves_again_when_state_returns", moved, moved]]


def test_detection_fallback(keys, logdir) -> list:
    port = free_port()
    srv = IntersectionServer("127.0.0.1", port, True, "adaptive", keys, logdir).start()
    c = ClientConnection("127.0.0.1", port, "F0001", keys.fleet_key)
    c.connect()
    c.send("REGISTER", {"role": "vehicle", "approach": "E", "resume": False})
    t0 = time.monotonic()
    fell = wait_until(lambda: srv.controller.effective_mode == "fixed", 15, 0.05)
    t_fall = time.monotonic() - t0
    c.send("POSITION_UPDATE", {"approach": "E", "distance_m": 50, "speed_mps": 5, "state": "moving"})
    back = wait_until(lambda: srv.controller.effective_mode == "adaptive", 3)
    c.close()
    srv.stop()
    return [["detection_fallback", "seconds_until_fixed_fallback", round(t_fall, 2), fell],
            ["detection_fallback", "returns_to_adaptive_on_data", back, back]]


def test_session_resume(keys, logdir) -> list:
    port = free_port()
    srv = IntersectionServer("127.0.0.1", port, True, "fixed", keys, logdir).start()
    v = VehicleAgent("G0001", "W", port=port, keys=keys, security=True).start()
    wait_until(lambda: "G0001" in srv.vehicles and v.registered, 5)
    time.sleep(1.0)
    seq = srv.vehicles["G0001"].seq
    srv.close_session(srv.vehicles["G0001"].session, "experiment: drop link")
    t0 = time.monotonic()
    ok = wait_until(lambda: len(v.registrations) >= 2, 8)
    t = time.monotonic() - t0
    kept = "G0001" in srv.vehicles and srv.vehicles["G0001"].seq == seq
    v.stop()
    srv.stop()
    return [["session_resume", "seconds_to_resume", round(t, 2), ok],
            ["session_resume", "server_kept_vehicle_state", kept, kept]]


def main() -> None:
    keys = ensure_keys()
    logdir = tempfile.mkdtemp(prefix="rel_logs_")
    rows = []
    for fn in (test_reconnect, test_malformed, test_signal_loss, test_detection_fallback,
               test_session_resume):
        rows += fn(keys, logdir)
        print(f"done: {fn.__name__}")
    shutil.rmtree(logdir, ignore_errors=True)
    header = ["test", "metric", "value", "passed"]
    write_csv(results_path("reliability.csv"), header, rows)
    print_table(header, rows)
    print("\nWrote results/reliability.csv")


if __name__ == "__main__":
    main()
