"""Generate the ML dataset by running the project's own traffic simulator.

For N random demand scenarios (vehicles per hour on each of the four
approaches, sampled uniformly between 100 and 800), the headless simulator
(experiments/sim.py) is run once in fixed-time mode and
once in adaptive mode with the same seed. One row per scenario is written to
ml_dataset.csv.

Run (from the project root):  python ml/ml_generate_dataset.py [--n 1000]
Uses the project's own simulator (experiments/sim.py). Standard library only.
Output: ml/data/ml_dataset.csv
"""
import argparse
import csv
import os
import random
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # project root
from experiments import sim  # noqa: E402

LOW, HIGH = 100, 800          # demand range, veh/h per approach
DURATION, WARMUP = 1800.0, 120.0


def one(args):
    idx, seed, rates = args
    fixed = sim.run(rates, "fixed", seed, DURATION, WARMUP)
    adapt = sim.run(rates, "adaptive", seed, DURATION, WARMUP)
    return [idx, seed, rates["N"], rates["S"], rates["E"], rates["W"],
            round(fixed.avg_wait_s, 3), round(adapt.avg_wait_s, 3),
            round(fixed.throughput_vph, 1), round(adapt.throughput_vph, 1),
            fixed.safety_violations + adapt.safety_violations]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "ml_dataset.csv"))
    a = ap.parse_args()
    rng = random.Random(a.seed)
    jobs = []
    for i in range(a.n):
        rates = {k: rng.randint(LOW, HIGH) for k in ("N", "S", "E", "W")}
        jobs.append((i, 1000 + i, rates))
    with Pool() as pool:
        rows = pool.map(one, jobs)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "sim_seed", "N_vph", "S_vph", "E_vph", "W_vph",
                    "fixed_avg_wait_s", "adaptive_avg_wait_s",
                    "fixed_throughput_vph", "adaptive_throughput_vph", "safety_violations"])
        w.writerows(rows)
    print(f"wrote {len(rows)} scenarios to {a.out}")


if __name__ == "__main__":
    main()
