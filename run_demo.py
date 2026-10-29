"""Launch the full demo: server + dashboard + traffic (+ optional ambulance).

Examples:
  python run_demo.py --security on --signal adaptive --traffic asymmetric
  python run_demo.py --security on --signal adaptive --traffic asymmetric --ambulance-at 30
Press Ctrl+C to stop every process.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import config
from experiments.harness import wait_port
from experiments.sim import PRESETS

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--security", choices=["on", "off"], default="on")
    ap.add_argument("--signal", choices=["fixed", "adaptive"], default="adaptive")
    ap.add_argument("--traffic", choices=list(PRESETS), default="asymmetric")
    ap.add_argument("--duration", type=float, default=1800, help="traffic generation time (s)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--ambulance-at", type=float, help="send the ambulance after this many seconds")
    ap.add_argument("--ambulance-approach", choices=list(config.APPROACHES), default="E")
    ap.add_argument("--record", help="ambulance writes its messages here (for the A2/A3 demo)",
                    default="captured.jsonl")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="demo speed-up (e.g. 4 = traffic and signals run 4x faster; shown on the dashboard)")
    ap.add_argument("--no-gui", action="store_true")
    args = ap.parse_args()

    if args.security == "on" and not os.path.exists(os.path.join(ROOT, config.KEYS_FILE)):
        sys.exit("keys.json not found: run 'python tools/gen_keys.py' first")
    py, common = sys.executable, ["--port", str(args.port), "--security", args.security]
    procs: list[subprocess.Popen] = []

    def launch(*cmd: str) -> subprocess.Popen:
        p = subprocess.Popen([py, "-m", *cmd], cwd=ROOT)
        procs.append(p)
        return p

    try:
        launch("server.intersection_server", "--signal", args.signal, "--speed", str(args.speed), *common)
        wait_port(args.port)
        if not args.no_gui:
            try:
                import tkinter  # noqa: F401  (check Tk is available before starting the GUI)
                launch("dashboard.gui", *common)
            except ImportError:
                print("WARNING: this Python has no Tk, so the dashboard cannot open.\n"
                      "  Homebrew: brew install python-tk@%d.%d   (python.org installers include Tk)\n"
                      "  Continuing without the dashboard." % sys.version_info[:2])
        launch("clients.traffic_generator", "--preset", args.traffic, "--duration", str(args.duration),
               "--seed", str(args.seed), "--speed", str(args.speed), *common)
        start = time.monotonic()
        amb_sent = args.ambulance_at is None
        print(f"Demo running (security {args.security}, {args.signal}, {args.traffic}). Ctrl+C to stop.")
        while True:
            if not amb_sent and time.monotonic() - start >= args.ambulance_at / args.speed:
                launch("clients.emergency_vehicle", "--approach", args.ambulance_approach,
                       "--record", args.record, *common)
                amb_sent = True
                print(f"Ambulance AMB01 dispatched on approach {args.ambulance_approach}")
            if procs[0].poll() is not None:
                print("Server exited.")
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        for p in reversed(procs):
            if p.poll() is None:
                p.terminate()
        deadline = time.monotonic() + 5
        for p in procs:
            try:
                p.wait(max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
