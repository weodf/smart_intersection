"""Shared vehicle kinematics, used by live clients and by the fast simulation.

Positions are measured as ``distance_m`` to the stop line: positive while
approaching, zero at the line and negative after crossing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class VehicleState:
    """Kinematic state of one vehicle."""
    distance_m: float = config.SPAWN_DISTANCE_M
    speed_mps: float = config.MAX_SPEED_MPS  # vehicles enter at cruising speed
    wait_time_s: float = 0.0
    travel_time_s: float = 0.0
    stopping_for_signal: bool = False  # remembered decision so it does not flip-flop

    @property
    def crossed(self) -> bool:
        """True once the vehicle is far enough past the stop line."""
        return self.distance_m <= -config.CROSSED_PAST_M

    @property
    def stopped(self) -> bool:
        return self.speed_mps < config.STOPPED_SPEED_MPS


def must_stop_for_signal(state: VehicleState, light: str, decel: float = config.DECEL_MPS2) -> bool:
    """Dilemma-zone rule: stop on red/yellow only if a comfortable stop is possible."""
    if light == "green" or state.distance_m < 0:
        return False
    if state.stopping_for_signal:
        return True  # already committed to stopping
    stopping_distance = state.speed_mps ** 2 / (2 * decel)
    return stopping_distance <= state.distance_m


def step(state: VehicleState, dt: float, light: str, ahead_distance_m: Optional[float],
         max_speed: float = config.MAX_SPEED_MPS, accel: float = config.ACCEL_MPS2,
         decel: float = config.DECEL_MPS2) -> VehicleState:
    """Advance one vehicle by ``dt`` seconds (mutates and returns ``state``).

    ``light`` is the colour for this vehicle's approach. ``ahead_distance_m`` is
    the ``distance_m`` of the vehicle directly ahead on the same approach (or
    None if the road ahead is clear); this vehicle keeps ``MIN_GAP_M`` behind it.
    """
    # Find the nearest point this vehicle must stop at (metres of road ahead).
    limit = math.inf
    state.stopping_for_signal = must_stop_for_signal(state, light, decel)
    if state.stopping_for_signal:
        limit = state.distance_m
    if ahead_distance_m is not None:
        limit = min(limit, state.distance_m - ahead_distance_m - config.MIN_GAP_M)
    limit = max(limit, 0.0)

    v = min(state.speed_mps + accel * dt, max_speed)
    if math.isfinite(limit):
        v = min(v, math.sqrt(2 * decel * limit))  # can still stop comfortably in `limit`
    move = min(v * dt, limit)
    state.speed_mps = v if move < limit else (move / dt if dt > 0 else 0.0)
    if limit < 0.05:
        state.speed_mps = 0.0
    state.distance_m -= move
    state.travel_time_s += dt
    if state.speed_mps < config.STOPPED_SPEED_MPS:
        state.wait_time_s += dt
    return state
