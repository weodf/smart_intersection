"""Security assessment: run attacks A1–A5 with security off and on.

Writes results/attacks.csv and results/flood.csv.
Run: python -m experiments.exp_attacks
"""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter

from attacker import attacker as atk
from clients.emergency_vehicle import make_ambulance
from common.logging_utils import read_csv, write_csv
from experiments.harness import ROOT, ServerProcess, ensure_keys, print_table, results_path


def _wait(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.05)
    return pred()


def run_mode(security: bool, keys) -> list[dict]:
    mode = "on" if security else "off"
    log_dir = f"logs/exp_attacks_{mode}"
    shutil.rmtree(os.path.join(ROOT, log_dir), ignore_errors=True)
    ckeys = keys if security else None
    out = []

    # A1 – fake ambulance
    with ServerProcess(security, log_dir=log_dir) as srv:
        mon = atk.Monitor("127.0.0.1", srv.port, ckeys)
        time.sleep(0.3)
        r = atk.a1_fake_ambulance("127.0.0.1", srv.port, keys.fleet_key, "N")
        time.sleep(1.0)
        r["preemption_triggered"] = mon.triggered
        mon.close()
        out.append(r)

    # A2b (replay inside the window) and A2 (replay 10 s later) – needs a real capture
    capture = results_path(f"captured_{mode}.jsonl")
    if os.path.exists(capture):
        os.remove(capture)
    with ServerProcess(security, log_dir=log_dir) as srv:
        mon = atk.Monitor("127.0.0.1", srv.port, ckeys)
        amb = make_ambulance("E", port=srv.port, keys=ckeys, security=security,
                             record_path=capture).start()
        _wait(lambda: os.path.exists(capture) and "EMERGENCY_REQUEST" in open(capture).read(), 20)
        r2b = atk.a2b_immediate_replay("127.0.0.1", srv.port, atk.load_capture(capture))
        r2b["preemption_triggered"] = "n/a"
        r2b["notes"] += " (real ambulance pre-emption already active)"
        amb.thread.join(40)
        _wait(lambda: not mon.preempt_seen or time.sleep(0.3) or False, 2)
        time.sleep(1.0)
        mon.reset()
        r2 = atk.a2_replay("127.0.0.1", srv.port, atk.load_capture(capture), min_age_s=10.0)
        time.sleep(1.0)
        r2["preemption_triggered"] = mon.triggered
        mon.close()
        out += [r2, r2b]

    # A3 – tampering with a captured, validly signed message
    with ServerProcess(security, log_dir=log_dir) as srv:
        mon = atk.Monitor("127.0.0.1", srv.port, ckeys)
        time.sleep(0.3)
        r = atk.a3_tamper("127.0.0.1", srv.port, atk.load_capture(capture), "N")
        time.sleep(1.0)
        r["preemption_triggered"] = mon.triggered
        mon.close()
        out.append(r)

    # A4 – fake signal broadcast, tested at the client
    r = atk.a4_fake_signal(security, keys)
    r["preemption_triggered"] = "n/a"
    out.append(r)

    # A5 – flooding: one 2,000-message burst (spec) and a sustained 10 s flood (A5s).
    # Rejection reasons are taken from the server's own security log.
    for sustain in (0.0, 10.0):
        sub = os.path.join(log_dir, "a5s" if sustain else "a5")
        with ServerProcess(security, log_dir=sub) as srv:
            r = atk.a5_flood("127.0.0.1", srv.port, ckeys, sustain_s=sustain)
            r["preemption_triggered"] = "n/a"
            out.append(r)
        time.sleep(0.2)
        sec = read_csv(os.path.join(ROOT, sub, "security_events.csv"))
        ev = read_csv(os.path.join(ROOT, sub, "events.csv"))
        r["reasons"] = Counter(row["reason"].split(":")[0] for row in sec if row["sender"] == "V6666")
        r["rejected"] = sum(r["reasons"].values())
        closes = [row["detail"] for row in ev if row["sender"] == "V6666" and row["event"] == "DISCONNECT"]
        detail = closes[0] if closes else "connection closed; accepted=0"
        r["accepted"] = max(0, int(detail.rsplit("accepted=", 1)[1]) - 1)  # minus the REGISTER
        reason = detail.split(";")[0]
        r["notes"] += f"; server disconnected flooder: {reason if reason != 'connection closed' else 'no'}"
        dropped = r["attempts"] - r["accepted"] - r["rejected"]
        if dropped:
            r["notes"] += f"; {dropped} flood messages never processed (connection closed)"

    for r in out:
        r["security"] = mode
    return out


def main() -> None:
    keys = ensure_keys()
    results = run_mode(False, keys) + run_mode(True, keys)
    order = {"A1": 0, "A2": 1, "A2b": 2, "A3": 3, "A4": 4, "A5": 5, "A5s": 6}
    results.sort(key=lambda r: (order[r["scenario"]], r["security"]))
    header = ["scenario", "security", "attempts", "accepted", "rejected", "rejection_reasons",
              "preemption_triggered", "notes"]
    rows = [[r["scenario"], r["security"], r["attempts"], r["accepted"], r["rejected"],
             ";".join(f"{k}:{v}" for k, v in sorted(r["reasons"].items())) or "-",
             r["preemption_triggered"], r["notes"]] for r in results]
    path = results_path("attacks.csv")
    write_csv(path, header, rows)
    flood = [r for r in results if r["scenario"].startswith("A5")]
    fh = ["scenario", "security", "sent", "send_duration_s", "baseline_p50_ms", "baseline_p95_ms",
          "during_p50_ms", "during_p95_ms", "during_max_ms", "during_lost", "during_samples"]
    write_csv(results_path("flood.csv"), fh,
              [[r["scenario"], r["security"]] + [_round(r["flood"][k]) for k in fh[2:]] for r in flood])
    print_table(header[:7], [row[:7] for row in rows])
    for r in flood:
        print(f"{r['scenario']} security={r['security']}: {r['notes']}")
    print(f"\nWrote {path} and {results_path('flood.csv')}")


def _round(x):
    return round(x, 3) if isinstance(x, float) else x


if __name__ == "__main__":
    main()
