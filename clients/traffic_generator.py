"""Spawn vehicle clients with Poisson arrivals per approach.

Example: python -m clients.traffic_generator --preset asymmetric --duration 300 --security on
"""
from __future__ import annotations

import argparse
import random
import threading
import time
from typing import Optional

import config
from clients.vehicle_agent import VehicleAgent
from common.security import KeyStore
from experiments.sim import PRESETS, poisson_arrivals


def parse_rates(text: str) -> dict:
    """Parse 'N=400,S=400,E=150,W=150' into a dict of vehicles per hour."""
    rates = {a: 0.0 for a in config.APPROACHES}
    for part in text.split(","):
        k, v = part.split("=")
        rates[k.strip().upper()] = float(v)
    return rates


class TrafficGenerator:
    """Starts VehicleAgents at Poisson arrival times, limited to ``max_concurrent``."""

    def __init__(self, rates: dict, duration_s: float, seed: int = 1, host: str = config.HOST,
                 port: int = config.PORT, keys: Optional[KeyStore] = None, security: bool = True,
                 max_concurrent: int = 120, id_prefix: str = "V", speed: float = 1.0):
        self.arrivals = poisson_arrivals(rates, duration_s, random.Random(seed))
        self.host, self.port, self.keys, self.security = host, port, keys, security
        self.max_concurrent = max_concurrent
        self.id_prefix = id_prefix
        self.speed = max(0.1, speed)  # arrival times are in simulated seconds
        self.agents: list[VehicleAgent] = []
        self._active = threading.Semaphore(max_concurrent)
        self._stop = threading.Event()

    def run(self) -> list[VehicleAgent]:
        """Spawn all vehicles, then wait for them to finish."""
        t0 = time.monotonic()
        for n, (t_sim, approach) in enumerate(self.arrivals, start=1):
            t_arr = t_sim / self.speed
            while not self._stop.is_set() and time.monotonic() - t0 < t_arr:
                time.sleep(min(0.1, t_arr - (time.monotonic() - t0)))
            if self._stop.is_set():
                break
            self._active.acquire()
            agent = VehicleAgent(f"{self.id_prefix}{n:04d}", approach, self.host, self.port,
                                 self.keys, self.security, on_done=lambda a: self._active.release())
            self.agents.append(agent.start())
        for a in self.agents:
            while a.thread.is_alive() and not self._stop.is_set():
                a.thread.join(0.2)
        return self.agents

    def stop(self) -> None:
        self._stop.set()
        for a in self.agents:
            a.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Poisson traffic generator")
    ap.add_argument("--preset", choices=list(PRESETS), default="balanced_low")
    ap.add_argument("--rates", help="custom rates, e.g. N=400,S=400,E=150,W=150")
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--security", choices=["on", "off"], default="on")
    ap.add_argument("--keys", default=config.KEYS_FILE)
    ap.add_argument("--max-concurrent", type=int, default=120)
    ap.add_argument("--speed", type=float, default=1.0, help="must match the server's --speed")
    args = ap.parse_args()
    rates = parse_rates(args.rates) if args.rates else PRESETS[args.preset]
    keys = KeyStore.load(args.keys) if args.security == "on" else None
    gen = TrafficGenerator(rates, args.duration, args.seed, args.host, args.port, keys,
                           args.security == "on", args.max_concurrent, speed=args.speed)
    print(f"Spawning {len(gen.arrivals)} vehicles over {args.duration:.0f} simulated s "
          f"at {args.speed:g}x ({rates})", flush=True)
    try:
        agents = gen.run()
    except KeyboardInterrupt:
        gen.stop()
        agents = gen.agents
    done = sum(a.crossed_sent for a in agents)
    print(f"Done: {done}/{len(agents)} vehicles crossed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
