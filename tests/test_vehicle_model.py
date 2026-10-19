import unittest

import config
from common.vehicle_model import VehicleState, step
from experiments import sim


def drive(state, light, seconds, ahead=None, dt=0.1):
    for _ in range(int(round(seconds / dt))):
        step(state, dt, light, ahead)
    return state


class KinematicsTests(unittest.TestCase):
    def test_free_flow_reaches_max_speed_and_crosses(self):
        s = VehicleState(speed_mps=0.0)
        drive(s, "green", 25)
        self.assertAlmostEqual(s.speed_mps, config.MAX_SPEED_MPS, places=3)
        self.assertTrue(s.crossed)

    def test_stops_before_line_on_red(self):
        s = drive(VehicleState(), "red", 40)
        self.assertGreaterEqual(s.distance_m, 0.0)
        self.assertLess(s.distance_m, 0.5)
        self.assertEqual(s.speed_mps, 0.0)
        self.assertGreater(s.wait_time_s, 20)

    def test_dilemma_zone_proceeds_on_yellow(self):
        s = VehicleState(distance_m=10.0, speed_mps=13.9)  # needs 24 m to stop
        drive(s, "yellow", 3)
        self.assertLess(s.distance_m, 0)

    def test_car_following_gap(self):
        s = drive(VehicleState(), "green", 30, ahead=50.0)  # vehicle ahead stopped at 50 m
        self.assertAlmostEqual(s.distance_m, 50.0 + config.MIN_GAP_M, delta=0.5)
        self.assertGreaterEqual(s.distance_m, 50.0 + config.MIN_GAP_M - 1e-9)

    def test_queue_forms_in_simulation(self):
        """In the fast simulation, a red approach forms a queue spaced by MIN_GAP_M."""
        lane = [VehicleState(distance_m=200.0 + 40 * i) for i in range(4)]
        for _ in range(600):
            ahead = None
            for v in lane:
                step(v, 0.1, "red", ahead)
                ahead = v.distance_m
        gaps = [b.distance_m - a.distance_m for a, b in zip(lane, lane[1:])]
        for g in gaps:
            self.assertAlmostEqual(g, config.MIN_GAP_M, delta=0.5)

    def test_sim_runs_and_is_deterministic(self):
        a = sim.run(sim.PRESETS["balanced_low"], "adaptive", seed=3, duration_s=600, warmup_s=60)
        b = sim.run(sim.PRESETS["balanced_low"], "adaptive", seed=3, duration_s=600, warmup_s=60)
        self.assertEqual(a, b)
        self.assertGreater(a.vehicles_crossed, 50)
        self.assertEqual(a.safety_violations, 0)

    def test_preemption_reduces_ambulance_wait(self):
        on = sim.run(sim.PRESETS["balanced_high"], "fixed", 1, 900, 60, ambulance_at=600, preemption=True)
        off = sim.run(sim.PRESETS["balanced_high"], "fixed", 1, 900, 60, ambulance_at=600, preemption=False)
        self.assertIsNotNone(on.ambulance_wait_s)
        self.assertLess(on.ambulance_wait_s, off.ambulance_wait_s)


if __name__ == "__main__":
    unittest.main()
