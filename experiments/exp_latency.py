"""Communication performance: PING/PONG round-trip time vs number of clients.

The server runs as a separate process on a free port (security off, then on).
N clients (10, 50, 100) each send 50 PINGs at 5 Hz.
Writes results/latency.csv.
Run: python -m experiments.exp_latency
"""
from __future__ import annotations

import argparse
import statistics
import threading
import time

from common.logging_utils import write_csv
from common.net import ClientConnection
from experiments.harness import ServerProcess, ensure_keys, print_table, results_path
from experiments.sim import percentile


def client(port: int, vid: str, key, server_key, n_pings: int, rate_hz: float, out: list,
           barrier: threading.Barrier) -> None:
    c = ClientConnection("127.0.0.1", port, vid, key, server_key)
    c.connect(5)
    c.send("REGISTER", {"role": "vehicle", "approach": "NSEW"[hash(vid) % 4], "resume": False})
    c.wait_for("REGISTER_ACK", 5)
    barrier.wait(10)
    rtts, lost = [], 0
    next_t = time.perf_counter()
    for _ in range(n_pings):
        t_send = time.perf_counter()
        c.send("PING", {"t_send": t_send})
        got = None
        end = time.monotonic() + 2.0
        while got is None and time.monotonic() < end:
            for env in c.recv(max(0.0, end - time.monotonic())):
                if env["type"] == "PONG" and env["payload"]["t_send"] == t_send:
                    got = time.perf_counter()
        if got is None:
            lost += 1
        else:
            rtts.append((got - t_send) * 1000)
        next_t += 1.0 / rate_hz
        time.sleep(max(0.0, next_t - time.perf_counter()))
    c.close()
    out.append((rtts, lost))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", default="10,50,100")
    ap.add_argument("--pings", type=int, default=50)
    args = ap.parse_args()
    keys = ensure_keys()
    rows = []
    for security in (False, True):
        for n in [int(x) for x in args.clients.split(",")]:
            with ServerProcess(security, log_dir="logs/exp_latency") as srv:
                out: list = []
                barrier = threading.Barrier(n)
                ths = [threading.Thread(target=client, args=(
                    srv.port, f"L{i:04d}", keys.fleet_key if security else None,
                    keys.server_key if security else None, args.pings, 5.0, out, barrier))
                    for i in range(n)]
                for t in ths:
                    t.start()
                for t in ths:
                    t.join(120)
            rtts = [x for r, _ in out for x in r]
            lost = sum(l for _, l in out) + (n - len(out)) * args.pings
            rows.append(["on" if security else "off", n, round(statistics.fmean(rtts), 3),
                         round(percentile(rtts, 50), 3), round(percentile(rtts, 95), 3),
                         round(percentile(rtts, 99), 3), round(max(rtts), 3), lost])
    header = ["security", "clients", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "lost"]
    write_csv(results_path("latency.csv"), header, rows)
    print_table(header, rows)
    print("\nWrote results/latency.csv")


if __name__ == "__main__":
    main()
