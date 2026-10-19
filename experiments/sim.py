"""Fast, headless, discrete-time simulation (no sockets).

Uses the same ``SignalController`` and ``vehicle_model`` as the live system,
so 30 simulated minutes run in a few seconds.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Optional

import config
from common import vehicle_model as vm
from server.controller import SignalController

PRESETS = {
    "balanced_low": {"N": 300, "S": 300, "E": 300, "W": 300},
    "balanced_high": {"N": 600, "S": 600, "E": 600, "W": 600},
    "asymmetric": {"N": 700, "S": 700, "E": 200, "W": 200},
}


def poisson_arrivals(rates_vph: dict, duration_s: float, rng: random.Random) -> list[tuple[float, str]]:
    """Sorted (time, approach) arrivals from independent Poisson processes."""
    out = []
    for approach in config.APPROACHES:
        rate = rates_vph.get(approach, 0) / 3600.0
        t = 0.0
        while rate > 0:
            t += rng.expovariate(rate)
            if t >= duration_s:
                break
            out.append((t, approach))
    out.sort()
    return out


@dataclass
class SimVehicle:
    vid: int
    approach: str
    spawn_t: float
    emergency: bool = False
    state: vm.VehicleState = field(default_factory=vm.VehicleState)
    requested: bool = False


@dataclass
class SimResult:
    vehicles_crossed: int
    avg_wait_s: float
    p95_wait_s: float
    max_wait_s: float
    avg_queue: float
    max_queue: int
    throughput_vph: float
    ambulance_wait_s: Optional[float] = None
    ambulance_travel_s: Optional[float] = None
    safety_violations: int = 0


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q / 100 * len(s) + 0.5)) - 1))
    return s[k]


def run(rates_vph: dict, mode: str, seed: int, duration_s: float = 1800.0,
        warmup_s: float = 120.0, dt: float = 0.1, ambulance_at: Optional[float] = None,
        preemption: bool = True) -> SimResult:
    """Run one simulation and return its statistics.

    Wait-time statistics include vehicles that spawned after the warm-up and
    crossed before the end. If ``ambulance_at`` is set, an ambulance enters at
    that time on an approach that is red at that moment.
    """
    rng = random.Random(seed)
    arrivals = poisson_arrivals(rates_vph, duration_s, rng)
    ctrl = SignalController(mode, now=0.0)
    lanes: dict[str, list[SimVehicle]] = {a: [] for a in config.APPROACHES}
    pending: list[SimVehicle] = []  # arrived but the entry point is still occupied
    waits: list[float] = []
    queue_samples: list[int] = []
    max_queue = 0
    crossed_after_warmup = 0
    amb: Optional[SimVehicle] = None
    amb_result: Optional[SimVehicle] = None
    violations = 0
    next_id = 0
    ai = 0
    steps = int(round(duration_s / dt))
    for i in range(steps + 1):
        t = i * dt
        # 1. arrivals
        while ai < len(arrivals) and arrivals[ai][0] <= t:
            pending.append(SimVehicle(next_id, arrivals[ai][1], arrivals[ai][0]))
            next_id += 1
            ai += 1
        sig = ctrl.state(t)
        if ambulance_at is not None and amb is None and t >= ambulance_at:
            red = [a for a in config.APPROACHES if sig.light_for(a) == "red"] or ["N"]
            amb = SimVehicle(-1, sorted(red)[0], t, emergency=True)
            pending.insert(0, amb)
        still = []
        for v in pending:
            lane = lanes[v.approach]
            if lane and lane[-1].state.distance_m > config.SPAWN_DISTANCE_M - config.MIN_GAP_M:
                still.append(v)
                continue
            lane.append(v)
        pending = still
        # 2. detection -> controller
        queues = {a: sum(1 for v in lanes[a] if v.state.distance_m >= 0 and v.state.stopped)
                  for a in config.APPROACHES}
        zone = {a: sum(1 for v in lanes[a] if 0 <= v.state.distance_m <= config.DETECTION_ZONE_M)
                for a in config.APPROACHES}
        ctrl.update_detection(queues, zone, t)
        if amb is not None and preemption and not amb.requested and amb in lanes[amb.approach] \
                and amb.state.distance_m <= config.EMERGENCY_REQUEST_DIST_M:
            amb.requested = ctrl.request_preemption(amb.approach, t)
        sig = ctrl.tick(t)
        if sig.ns != "red" and sig.ew != "red":
            violations += 1
        # 3. kinematics (lanes are ordered front-to-back)
        for a in config.APPROACHES:
            light = sig.light_for(a)
            ahead: Optional[float] = None
            for v in lanes[a]:
                vm.step(v.state, dt, light, ahead)
                ahead = v.state.distance_m
            done = [v for v in lanes[a] if v.state.crossed]
            if done:
                lanes[a] = [v for v in lanes[a] if not v.state.crossed]
            for v in done:
                if v.emergency:
                    amb_result = v
                    if v.requested:
                        ctrl.clear_preemption(v.approach, t)
                    continue
                if v.spawn_t >= warmup_s:
                    waits.append(v.state.wait_time_s)
                if t >= warmup_s:
                    crossed_after_warmup += 1
        if amb is not None and amb.requested and amb.state.distance_m < 0 and ctrl.preempt:
            ctrl.clear_preemption(amb.approach, t)  # EMERGENCY_CLEAR once past the line
        # 4. queue statistics, sampled once per simulated second
        if t >= warmup_s and i % int(round(1 / dt)) == 0:
            total = sum(queues.values()) + len(pending)
            queue_samples.append(total)
            max_queue = max(max_queue, max(queues.values()))
        ctrl.drain_events()
    measured_s = duration_s - warmup_s
    return SimResult(
        vehicles_crossed=crossed_after_warmup,
        avg_wait_s=statistics.fmean(waits) if waits else 0.0,
        p95_wait_s=percentile(waits, 95),
        max_wait_s=max(waits) if waits else 0.0,
        avg_queue=statistics.fmean(queue_samples) if queue_samples else 0.0,
        max_queue=max_queue,
        throughput_vph=crossed_after_warmup / measured_s * 3600 if measured_s > 0 else 0.0,
        ambulance_wait_s=amb_result.state.wait_time_s if amb_result else None,
        ambulance_travel_s=amb_result.state.travel_time_s if amb_result else None,
        safety_violations=violations,
    )


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    r = run(PRESETS["balanced_high"], "adaptive", seed=1)
    print(r, f"({time.perf_counter() - t0:.1f}s wall)")
