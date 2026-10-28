"""Turn results/*.csv into results/*.svg charts.

Run: python -m experiments.make_charts
"""
from __future__ import annotations

import os
import statistics

from common.logging_utils import read_csv
from experiments.harness import results_path
from experiments.svg_chart import bar_chart, line_chart

SCEN = ["balanced_low", "balanced_high", "asymmetric"]


def chart_signal() -> None:
    rows = read_csv(results_path("signal_performance_summary.csv"))
    get = {(r["scenario"], r["mode"]): r for r in rows}
    bar_chart(results_path("avg_wait_fixed_vs_adaptive.svg"),
              "Average vehicle wait: fixed-time vs adaptive (mean ± SD, 5 seeds)", SCEN,
              {m: [float(get[(s, m)]["avg_wait_s_mean"]) for s in SCEN] for m in ("fixed", "adaptive")},
              "Average wait per vehicle (s)", "Demand scenario",
              errors={m: [float(get[(s, m)]["avg_wait_s_sd"]) for s in SCEN] for m in ("fixed", "adaptive")})


def chart_latency() -> None:
    rows = read_csv(results_path("latency.csv"))
    clients = sorted({int(r["clients"]) for r in rows})
    val = {(r["security"], int(r["clients"])): r for r in rows}
    series = {}
    for sec in ("off", "on"):
        series[f"mean, security {sec}"] = [float(val[(sec, c)]["mean_ms"]) for c in clients]
    for sec in ("off", "on"):
        series[f"p95, security {sec}"] = [float(val[(sec, c)]["p95_ms"]) for c in clients]
    line_chart(results_path("latency_by_clients.svg"), "PING round-trip time vs connected clients",
               clients, series, "Round-trip time (ms)", "Concurrent clients",
               dashed=[False, False, True, True])


def chart_hmac() -> None:
    rows = read_csv(results_path("hmac_overhead.csv"))
    short = {"sign": "sign", "verify": "verify", "encode+decode (unsigned)": "encode+decode",
             "full round trip unsigned (make+encode+decode)": "round trip unsigned",
             "full round trip signed (make+sign+encode+decode+verify)": "round trip signed"}
    rows = [r for r in rows if r["operation"] in short]
    bar_chart(results_path("hmac_overhead.svg"), "Per-message cost of HMAC-SHA256 (POSITION_UPDATE)",
              [short[r["operation"]] for r in rows], {"time per message": [float(r["mean_us"]) for r in rows]},
              "Microseconds per message", "Operation")


def chart_attacks() -> None:
    rows = read_csv(results_path("attacks.csv"))
    scen = []
    for r in rows:
        if r["scenario"] not in scen:
            scen.append(r["scenario"])
    get = {(r["scenario"], r["security"]): r for r in rows}

    def pct(s, sec):
        r = get[(s, sec)]
        return 100.0 * int(r["accepted"]) / max(1, int(r["attempts"]))
    bar_chart(results_path("attack_results.svg"), "Attack messages accepted by the target (% of attempts)",
              scen, {"security off": [pct(s, "off") for s in scen], "security on": [pct(s, "on") for s in scen]},
              "Accepted (%)", "Attack scenario", y_max=100)


def chart_preemption() -> None:
    rows = read_csv(results_path("preemption.csv"))
    cats, off, on = [], [], []
    for sc in ("balanced_high", "asymmetric"):
        for mode in ("fixed", "adaptive"):
            cats.append(f"{sc} / {mode}")
            for pre, lst in (("off", off), ("on", on)):
                lst.append(statistics.fmean(float(r["ambulance_wait_s"]) for r in rows
                                            if r["scenario"] == sc and r["mode"] == mode and r["preemption"] == pre))
    bar_chart(results_path("preemption_ambulance_wait.svg"), "Ambulance wait at the intersection (mean of 5 seeds)",
              cats, {"no pre-emption": off, "with pre-emption": on}, "Ambulance wait (s)", "Scenario / signal mode")


def chart_flood() -> None:
    rows = read_csv(results_path("flood.csv"))
    cats = [f"{r['scenario']} / security {r['security']}" for r in rows]
    bar_chart(results_path("flood_latency.svg"), "Normal clients' PING p95 latency, before vs during flood",
              cats, {"before flood (p95)": [float(r["baseline_p95_ms"]) for r in rows],
                     "during flood (p95)": [float(r["during_p95_ms"]) for r in rows]},
              "Round-trip time p95 (ms)", "Flood scenario (A5 = 2,000-message burst, A5s = sustained 10 s)")


def main() -> None:
    for name, fn in (("signal_performance_summary.csv", chart_signal), ("latency.csv", chart_latency),
                     ("hmac_overhead.csv", chart_hmac), ("attacks.csv", chart_attacks),
                     ("preemption.csv", chart_preemption), ("flood.csv", chart_flood)):
        if os.path.exists(results_path(name)):
            fn()
            print(f"chart from {name}")
        else:
            print(f"skipped (missing {name}) - run the experiment first")
    print("SVG charts written to results/")


if __name__ == "__main__":
    main()
