"""Pure signal-control logic: no sockets, no threads, no real clock.

Every method takes ``now`` explicitly so the controller can be driven by the
live server (wall clock) or by the fast simulation (simulated clock).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import config

# Fixed phase cycle. Index 2 and 5 are both all-red clearance phases.
PHASES = ("NS_GREEN", "NS_YELLOW", "ALL_RED", "EW_GREEN", "EW_YELLOW", "ALL_RED")
LIGHTS = {  # phase index -> (ns colour, ew colour)
    0: ("green", "red"), 1: ("yellow", "red"), 2: ("red", "red"),
    3: ("red", "green"), 4: ("red", "yellow"), 5: ("red", "red"),
}
GREEN_IDX = {"ns": 0, "ew": 3}


@dataclass
class SignalState:
    """Snapshot of the signal, as broadcast in SIGNAL_STATE."""
    phase: str
    ns: str
    ew: str
    phase_elapsed_s: float
    signal_mode: str
    preemption: Optional[str]

    def light_for(self, approach: str) -> str:
        """Colour shown to a given approach (N/S use ns, E/W use ew)."""
        return self.ns if config.DIRECTION_OF[approach] == "ns" else self.ew

    def as_payload(self) -> dict:
        return {"phase": self.phase, "ns": self.ns, "ew": self.ew,
                "phase_elapsed_s": round(self.phase_elapsed_s, 2),
                "signal_mode": self.signal_mode, "preemption": self.preemption}


@dataclass
class ControllerConfig:
    """Timing parameters (defaults from config.py)."""
    yellow_s: float = config.YELLOW_S
    all_red_s: float = config.ALL_RED_S
    fixed_green_s: float = config.FIXED_GREEN_S
    min_green_s: float = config.MIN_GREEN_S
    max_green_s: float = config.MAX_GREEN_S
    gap_s: float = config.GAP_S
    preempt_max_hold_s: float = config.PREEMPT_MAX_HOLD_S
    detection_timeout_s: float = config.DETECTION_TIMEOUT_S


@dataclass
class _Preempt:
    approach: str
    requested_at: float
    green_since: Optional[float] = None


class SignalController:
    """Fixed-time or actuated (gap-out / max-out) control with emergency pre-emption."""

    def __init__(self, mode: str, cfg: Optional[ControllerConfig] = None, now: float = 0.0):
        if mode not in ("fixed", "adaptive"):
            raise ValueError("mode must be 'fixed' or 'adaptive'")
        self.mode = mode
        self.cfg = cfg or ControllerConfig()
        self.idx = 0
        self.phase_start = now
        self.queues = {a: 0 for a in config.APPROACHES}
        self.approaching = {a: 0 for a in config.APPROACHES}
        self.last_detection = now          # last time a vehicle was in the zone of a green approach
        self.last_data = now               # last time any detection data arrived
        self.fallback = False              # adaptive -> fixed fail-safe active
        self.preempt: Optional[_Preempt] = None
        self.events: list[dict] = []       # drained by the caller for logging
        self.last_tick = now

    # ----------------------------------------------------------------- inputs
    def update_detection(self, queues: dict, approaching: dict, now: float) -> None:
        """Feed detector data: stopped vehicles and vehicles in the detection zone, per approach."""
        self.queues = {a: int(queues.get(a, 0)) for a in config.APPROACHES}
        self.approaching = {a: int(approaching.get(a, 0)) for a in config.APPROACHES}
        self.last_data = now
        if self.fallback:
            self.fallback = False
            self._event(now, "RESUME_ADAPTIVE", None)

    def request_preemption(self, approach: str, now: float) -> bool:
        """Start (or refresh) emergency pre-emption for ``approach``. Caller must authorise."""
        if approach not in config.APPROACHES:
            return False
        if self.preempt is not None:
            return self.preempt.approach == approach  # one emergency at a time
        self.preempt = _Preempt(approach, now)
        self._event(now, "PREEMPT_START", approach)
        direction = config.DIRECTION_OF[approach]
        if self._green_direction() not in (None, direction):
            self._advance(now)  # end the conflicting green immediately (-> yellow)
        self._check_preempt_green(now)
        return True

    def clear_preemption(self, approach: str, now: float) -> None:
        """End pre-emption; normal operation resumes from the next phase."""
        if self.preempt is not None and self.preempt.approach == approach:
            self.preempt = None
            self._event(now, "PREEMPT_END", approach, "cleared")

    # ------------------------------------------------------------------ clock
    def tick(self, now: float, clients_connected: int = 0) -> SignalState:
        """Advance the controller to ``now`` and return the current signal state."""
        if (self.mode == "adaptive" and not self.fallback and clients_connected > 0
                and now - self.last_data > self.cfg.detection_timeout_s):
            self.fallback = True
            self._event(now, "FALLBACK_FIXED", None)
        for _ in range(12):  # bounded: at most a couple of transitions per tick
            if self.preempt is not None and self.preempt.green_since is not None and \
                    now - self.preempt.green_since >= self.cfg.preempt_max_hold_s:
                approach = self.preempt.approach
                self.preempt = None
                self._event(now, "PREEMPT_END", approach, "timeout")
            if not self._phase_should_end(now):
                break
            self._advance(self._transition_time(now))
        self.last_tick = now
        return self.state(now)

    def state(self, now: float) -> SignalState:
        ns, ew = LIGHTS[self.idx]
        return SignalState(PHASES[self.idx], ns, ew, max(0.0, now - self.phase_start),
                           self.effective_mode, self.preempt.approach if self.preempt else None)

    @property
    def effective_mode(self) -> str:
        return "fixed" if (self.mode == "fixed" or self.fallback) else "adaptive"

    # --------------------------------------------------------------- internals
    def _green_direction(self) -> Optional[str]:
        return {0: "ns", 3: "ew"}.get(self.idx)

    def _transition_time(self, now: float) -> float:
        """Exact end time of a timed phase if it fell inside this tick, otherwise ``now``.

        Back-dating is never allowed before the previous tick, so a yellow or
        all-red is never shortened in real time.
        """
        if self.idx in (1, 4):
            end = self.phase_start + self.cfg.yellow_s
        elif self.idx in (2, 5):
            end = self.phase_start + self.cfg.all_red_s
        elif self.preempt is None and self.effective_mode == "fixed":
            end = self.phase_start + self.cfg.fixed_green_s
        else:
            return now
        return end if self.last_tick <= end <= now else now

    def _phase_should_end(self, now: float) -> bool:
        elapsed = now - self.phase_start
        if self.idx in (1, 4):
            return elapsed >= self.cfg.yellow_s
        if self.idx in (2, 5):
            return elapsed >= self.cfg.all_red_s
        direction = self._green_direction()
        if self.preempt is not None:
            # Hold the emergency approach's green; end any other green at once.
            return config.DIRECTION_OF[self.preempt.approach] != direction
        if self.effective_mode == "fixed":
            return elapsed >= self.cfg.fixed_green_s
        # Adaptive: gap-out / max-out, only when the conflicting direction has demand.
        mine = ("N", "S") if direction == "ns" else ("E", "W")
        other = ("E", "W") if direction == "ns" else ("N", "S")
        if any(self.approaching[a] > 0 for a in mine):
            self.last_detection = now
        demand = any(self.queues[a] > 0 or self.approaching[a] > 0 for a in other)
        if elapsed < self.cfg.min_green_s or not demand:
            return False
        gap_out = now - max(self.last_detection, self.phase_start) >= self.cfg.gap_s
        max_out = elapsed >= self.cfg.max_green_s
        return gap_out or max_out

    def _advance(self, at: float) -> None:
        """Move to the next phase. After all-red, an active pre-emption picks the green."""
        if self.idx in (2, 5) and self.preempt is not None:
            self.idx = GREEN_IDX[config.DIRECTION_OF[self.preempt.approach]]
        else:
            self.idx = (self.idx + 1) % len(PHASES)
        self.phase_start = at
        self.last_detection = at
        self._check_preempt_green(at)

    def _check_preempt_green(self, now: float) -> None:
        p = self.preempt
        if p is not None and p.green_since is None and \
                self._green_direction() == config.DIRECTION_OF[p.approach]:
            p.green_since = now

    def _event(self, now: float, kind: str, approach: Optional[str], detail: str = "") -> None:
        self.events.append({"time": now, "event": kind, "approach": approach, "detail": detail})

    def drain_events(self) -> list[dict]:
        """Return and clear pending events."""
        ev, self.events = self.events, []
        return ev
