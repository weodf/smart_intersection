"""Fixed-time vs adaptive control, plus ambulance pre-emption, in the fast simulation.

Writes results/signal_performance.csv, results/signal_performance_summary.csv
and results/preemption.csv.
Run: python -m experiments.exp_signal_performance [--seeds 5] [--duration 1800]
"""
from __future__ import annotations

import argparse
import statistics
import time

from common.logging_utils import write_csv
from experiments import sim
from experiments.harness import print_table, results_path

METRICS = ["vehicles_crossed", "avg_wait_s", "p95_wait_s", "max_wait_s", "avg_queue", "max_queue",
           "throughput_vph"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--duration", type=float, default=1800.0)
    ap.add_argument("--warmup", type=float, default=120.0)
    args = ap.parse_args()
    t0 = time.perf_counter()
    rows, results = [], {}
    for scenario, rates in sim.PRESETS.items():
        for mode in ("fixed", "adaptive"):
            for seed in range(1, args.seeds + 1):
                r = sim.run(rates, mode, seed, args.duration, args.warmup)
                assert r.safety_violations == 0
                results.setdefault((scenario, mode), []).append(r)
                rows.append([scenario, mode, seed] + [round(getattr(r, m), 3) for m in METRICS])
    write_csv(results_path("signal_performance.csv"), ["scenario", "mode", "seed"] + METRICS, rows)

    summary = []
    for (scenario, mode), rs in results.items():
        line = [scenario, mode, len(rs)]
        for m in METRICS:
            vals = [getattr(r, m) for r in rs]
            line += [round(statistics.fmean(vals), 3), round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0]
        summary.append(line)
    sh = ["scenario", "mode", "runs"] + [f"{m}_{s}" for m in METRICS for s in ("mean", "sd")]
    write_csv(results_path("signal_performance_summary.csv"), sh, summary)
    print_table(["scenario", "mode", "avg_wait_s", "sd", "p95_wait_s", "avg_queue", "throughput_vph"],
                [[s[0], s[1], s[5], s[6], s[7], s[11], s[15]] for s in summary])

    # Pre-emption: an ambulance enters at t=600 s on an approach that is red at that moment.
    pre_rows = []
    for scenario in ("balanced_high", "asymmetric"):
        for mode in ("fixed", "adaptive"):
            for seed in range(1, args.seeds + 1):
                for pre in (False, True):
                    r = sim.run(sim.PRESETS[scenario], mode, seed, 900.0, args.warmup,
                                ambulance_at=600.0, preemption=pre)
                    pre_rows.append([scenario, mode, seed, "on" if pre else "off",
                                     round(r.ambulance_wait_s, 2), round(r.ambulance_travel_s, 2),
                                     round(r.avg_wait_s, 2)])
    write_csv(results_path("preemption.csv"), ["scenario", "mode", "seed", "preemption",
                                               "ambulance_wait_s", "ambulance_travel_s",
                                               "others_avg_wait_s"], pre_rows)
    print()
    agg = {}
    for row in pre_rows:
        agg.setdefault((row[0], row[1], row[3]), []).append(row)
    print_table(["scenario", "mode", "preemption", "amb_wait_mean_s", "amb_travel_mean_s"],
                [[k[0], k[1], k[2], round(statistics.fmean(r[4] for r in v), 2),
                  round(statistics.fmean(r[5] for r in v), 2)] for k, v in agg.items()])
    print(f"\nWrote results/signal_performance.csv, signal_performance_summary.csv, preemption.csv "
          f"({time.perf_counter() - t0:.0f}s)")


if __name__ == "__main__":
    main()
