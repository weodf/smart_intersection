import random
import unittest

from server.controller import PHASES, SignalController

ALLOWED = {"NS_GREEN": {"NS_YELLOW"}, "NS_YELLOW": {"ALL_RED"}, "EW_GREEN": {"EW_YELLOW"},
           "EW_YELLOW": {"ALL_RED"}, "ALL_RED": {"NS_GREEN", "EW_GREEN"}}
NONE = {a: 0 for a in "NSEW"}


def run_until(ctrl, start, end, dt=0.1, queues=None, approaching=None, feed=True, clients=1):
    """Tick the controller and return [(t, state)] for each step."""
    out = []
    n = int(round((end - start) / dt))
    for i in range(n + 1):
        t = start + i * dt
        if feed:
            ctrl.update_detection(queues or NONE, approaching or NONE, t)
        out.append((t, ctrl.tick(t, clients)))
    return out


def transitions(trace):
    """List of (time, from_phase, to_phase) from a trace."""
    res = []
    for (t0, s0), (t1, s1) in zip(trace, trace[1:]):
        if s0.phase != s1.phase or (s1.phase_elapsed_s < s0.phase_elapsed_s):
            res.append((t1 - s1.phase_elapsed_s, s0.phase, s1.phase))
    return res


class SafetyTests(unittest.TestCase):
    def test_safety_invariant_long_random_run(self):
        rng = random.Random(42)
        for mode in ("fixed", "adaptive"):
            ctrl = SignalController(mode, now=0.0)
            t, dt = 0.0, 0.1
            last_phase, phase_since = "NS_GREEN", 0.0
            pre = None
            while t < 2 * 3600:
                if rng.random() < 0.05:
                    q = {a: rng.choice([0, 0, 1, 3, 8]) for a in "NSEW"}
                    z = {a: rng.choice([0, 0, 1, 2]) for a in "NSEW"}
                    ctrl.update_detection(q, z, t)
                if pre is None and rng.random() < 0.002:
                    pre = rng.choice("NSEW")
                    ctrl.request_preemption(pre, t)
                elif pre is not None and rng.random() < 0.01:
                    ctrl.clear_preemption(pre, t)
                    pre = None
                s = ctrl.tick(t, clients_connected=1)
                self.assertFalse(s.ns != "red" and s.ew != "red", f"conflict at t={t}")
                if s.phase != last_phase:
                    self.assertIn(s.phase, ALLOWED[last_phase], f"{last_phase}->{s.phase}")
                    held = t - phase_since
                    if last_phase.endswith("YELLOW"):
                        self.assertGreaterEqual(held + 1e-6, 3.0 - dt)
                    if last_phase == "ALL_RED":
                        self.assertGreaterEqual(held + 1e-6, 2.0 - dt)
                    last_phase, phase_since = s.phase, t
                t = round(t + dt, 6)


class FixedTimeTests(unittest.TestCase):
    def test_exact_fixed_timings(self):
        ctrl = SignalController("fixed", now=0.0)
        tr = transitions(run_until(ctrl, 0, 140))
        starts = [(round(t, 3), b) for t, a, b in tr]
        self.assertEqual(starts[:6], [(30.0, "NS_YELLOW"), (33.0, "ALL_RED"), (35.0, "EW_GREEN"),
                                      (65.0, "EW_YELLOW"), (68.0, "ALL_RED"), (70.0, "NS_GREEN")])


class AdaptiveTests(unittest.TestCase):
    def test_rests_without_conflicting_demand(self):
        ctrl = SignalController("adaptive", now=0.0)
        trace = run_until(ctrl, 0, 120)
        self.assertTrue(all(s.phase == "NS_GREEN" for _, s in trace))

    def test_gap_out_after_min_green(self):
        ctrl = SignalController("adaptive", now=0.0)
        q = {"N": 0, "S": 0, "E": 2, "W": 0}
        tr = transitions(run_until(ctrl, 0, 30, queues=q, approaching={"N": 0, "S": 0, "E": 2, "W": 0}))
        self.assertAlmostEqual(tr[0][0], 10.0, delta=0.11)  # min green, gap already satisfied
        self.assertEqual(tr[0][2], "NS_YELLOW")

    def test_gap_out_waits_for_gap(self):
        ctrl = SignalController("adaptive", now=0.0)
        q = {"N": 0, "S": 0, "E": 3, "W": 0}
        # Vehicles keep arriving on N until t=20, then nothing.
        trace = []
        for i in range(0, 401):
            t = i * 0.1
            z = {"N": 1 if t < 20 else 0, "S": 0, "E": 3, "W": 0}
            ctrl.update_detection(q, z, t)
            trace.append((t, ctrl.tick(t, 1)))
        tr = transitions(trace)
        self.assertAlmostEqual(tr[0][0], 23.0, delta=0.15)

    def test_max_out(self):
        ctrl = SignalController("adaptive", now=0.0)
        busy = {a: 1 for a in "NSEW"}
        tr = transitions(run_until(ctrl, 0, 60, queues=busy, approaching=busy))
        self.assertAlmostEqual(tr[0][0], 45.0, delta=0.11)

    def test_min_green_respected(self):
        ctrl = SignalController("adaptive", now=0.0)
        busy = {a: 5 for a in "NSEW"}
        tr = transitions(run_until(ctrl, 0, 600, queues=busy, approaching={a: 0 for a in "NSEW"}))
        greens = [t for t, _, b in tr if b.endswith("GREEN")]
        yellows = [t for t, _, b in tr if b.endswith("YELLOW")]
        for g, y in zip(greens, yellows[1:]):
            self.assertGreaterEqual(y - g, 10.0 - 0.11)


class PreemptionTests(unittest.TestCase):
    def test_preemption_goes_through_yellow_and_all_red(self):
        ctrl = SignalController("fixed", now=0.0)
        before = run_until(ctrl, 0, 4.9)  # NS green, under 5 s in (before MIN_GREEN)
        self.assertTrue(ctrl.request_preemption("E", 5.0))
        trace = run_until(ctrl, 5.0, 30)
        tr = [(round(t, 2), b) for t, a, b in transitions(before + trace)]
        self.assertEqual(tr[:3], [(5.0, "NS_YELLOW"), (8.0, "ALL_RED"), (10.0, "EW_GREEN")])
        self.assertTrue(all(s.phase == "EW_GREEN" for t, s in trace if t >= 10.0))

    def test_hold_until_clear_then_resume(self):
        ctrl = SignalController("fixed", now=0.0)
        ctrl.request_preemption("N", 20.0)
        trace = run_until(ctrl, 0, 45)
        self.assertTrue(all(s.phase == "NS_GREEN" for _, s in trace))  # held beyond 30 s
        ctrl.clear_preemption("N", 45.0)
        self.assertEqual(ctrl.tick(45.1).phase, "NS_YELLOW")
        kinds = [e["event"] for e in ctrl.drain_events()]
        self.assertEqual(kinds, ["PREEMPT_START", "PREEMPT_END"])

    def test_hold_timeout(self):
        ctrl = SignalController("fixed", now=0.0)
        ctrl.request_preemption("N", 1.0)
        run_until(ctrl, 0, 40)
        self.assertIsNone(ctrl.preempt)
        self.assertIn("timeout", [e["detail"] for e in ctrl.drain_events()])


class FailSafeTests(unittest.TestCase):
    def test_fallback_and_resume(self):
        ctrl = SignalController("adaptive", now=0.0)
        busy = {a: 1 for a in "NSEW"}
        ctrl.update_detection(busy, busy, 0.0)
        run_until(ctrl, 0, 11, feed=False, clients=3)
        self.assertEqual(ctrl.effective_mode, "fixed")
        ctrl.update_detection(busy, busy, 11.1)
        self.assertEqual(ctrl.tick(11.1, 3).signal_mode, "adaptive")
        kinds = [e["event"] for e in ctrl.drain_events()]
        self.assertEqual(kinds, ["FALLBACK_FIXED", "RESUME_ADAPTIVE"])

    def test_no_fallback_without_clients(self):
        ctrl = SignalController("adaptive", now=0.0)
        run_until(ctrl, 0, 30, feed=False, clients=0)
        self.assertEqual(ctrl.effective_mode, "adaptive")


if __name__ == "__main__":
    unittest.main()
