"""Integration tests: a real server on a free port with real TCP clients."""
import json
import socket
import tempfile
import time
import unittest

from clients.emergency_vehicle import make_ambulance
from clients.vehicle_agent import VehicleAgent
from common.net import ClientConnection
from common.protocol import make_envelope
from common.security import attach_sig
from server.controller import ControllerConfig
from server.intersection_server import IntersectionServer
from tests.test_security import demo_keys

FAST = ControllerConfig(fixed_green_s=8.0)


def wait_until(pred, timeout=10.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class ServerTestCase(unittest.TestCase):
    security = True
    mode = "fixed"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = demo_keys()
        self.server = IntersectionServer("127.0.0.1", 0, self.security, self.mode, self.keys,
                                         self.tmp.name, FAST).start()
        self.port = self.server.port

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def client(self, sender, key=None, role="vehicle", approach="N"):
        c = ClientConnection("127.0.0.1", self.port, sender, key)
        c.connect()
        c.send("REGISTER", {"role": role, "approach": approach, "resume": False})
        return c


class VehiclesCrossTests(ServerTestCase):
    def test_ten_vehicles_cross(self):
        agents = [VehicleAgent(f"V{i:04d}", "NSEW"[i % 4], port=self.port, keys=self.keys,
                               security=True, start_distance=60.0 + 10 * (i // 4)).start()
                  for i in range(10)]
        for a in agents:
            a.thread.join(timeout=60)
        self.assertTrue(all(a.crossed_sent for a in agents), [a.vid for a in agents if not a.crossed_sent])
        self.assertTrue(wait_until(lambda: self.server.crossed == 10, 3))
        self.assertEqual(sum(self.server.security_counts.values()), 0)

    def test_queue_info_spacing(self):
        a = self.client("V1", self.keys.fleet_key, approach="E")
        b = self.client("V2", self.keys.fleet_key, approach="E")
        a.send("POSITION_UPDATE", {"approach": "E", "distance_m": 0.2, "speed_mps": 0, "state": "stopped"})
        time.sleep(0.1)
        b.send("POSITION_UPDATE", {"approach": "E", "distance_m": 30, "speed_mps": 5, "state": "moving"})
        info = b.wait_for("QUEUE_INFO")
        self.assertEqual(info["payload"], {"ahead": 1, "ahead_distance_m": 0.2})
        a.close(), b.close()


class SecurityOnTests(ServerTestCase):
    def test_malformed_and_bad_signature_do_not_kill_server(self):
        c = ClientConnection("127.0.0.1", self.port, "X")
        c.connect()
        c.send_raw(b"{garbage\n")
        err = c.wait_for("ERROR")
        self.assertEqual(err["payload"]["code"], "MALFORMED")
        c.send("REGISTER", {"role": "vehicle", "approach": "N", "resume": False})  # unsigned
        self.assertEqual(c.wait_for("ERROR")["payload"]["code"], "BAD_SIGNATURE")
        c.send_raw(b"a" * 5000 + b"\n")
        self.assertEqual(c.wait_for("ERROR")["payload"]["code"], "MALFORMED")
        good = self.client("V9", self.keys.fleet_key)
        self.assertIsNotNone(good.wait_for("REGISTER_ACK"))
        good.send("PING", {"t_send": time.time()})
        self.assertIsNotNone(good.wait_for("PONG"))
        c.close(), good.close()

    def test_fake_ambulance_unauthorised(self):
        fake = self.client("AMB99", self.keys.fleet_key, role="emergency")
        self.assertEqual(fake.wait_for("ERROR")["payload"]["code"], "UNAUTHORISED")
        fake.send("EMERGENCY_REQUEST", {"approach": "E", "eta_s": 5})
        self.assertEqual(fake.wait_for("ERROR")["payload"]["code"], "UNAUTHORISED")
        self.assertIsNone(self.server.controller.preempt)
        fake.close()

    def test_replay_rejected(self):
        c = ClientConnection("127.0.0.1", self.port, "V5", self.keys.fleet_key)
        c.connect()
        env = attach_sig(make_envelope("REGISTER", "V5", {"role": "vehicle", "approach": "S",
                                                          "resume": False}), self.keys.fleet_key)
        c.send_env(env)
        self.assertIsNotNone(c.wait_for("REGISTER_ACK"))
        c.send_env(env)
        self.assertEqual(c.wait_for("ERROR")["payload"]["code"], "REPLAY")
        old = attach_sig(make_envelope("PING", "V5", {"t_send": 0.0}, ts=time.time() - 10),
                         self.keys.fleet_key)
        c.send_env(old)
        self.assertEqual(c.wait_for("ERROR")["payload"]["code"], "STALE")
        c.close()

    def test_real_ambulance_preempts(self):
        amb = make_ambulance("E", port=self.port, keys=self.keys, security=True).start()
        self.assertTrue(wait_until(lambda: self.server.controller.preempt is not None, 15))
        self.assertTrue(wait_until(lambda: self.server.controller.state(time.time()).ew == "green", 8))
        amb.thread.join(30)
        self.assertTrue(amb.crossed_sent)
        self.assertEqual(self.server.preemptions, 1)
        self.assertIsNone(self.server.controller.preempt)

    def test_session_resume(self):
        agent = VehicleAgent("V0042", "W", port=self.port, keys=self.keys, security=True).start()
        self.assertTrue(wait_until(lambda: "V0042" in self.server.vehicles and agent.registered))
        with self.server.lock:
            sess = self.server.vehicles["V0042"].session
        self.server.close_session(sess, "test drop")
        self.assertTrue(wait_until(lambda: len(agent.registrations) >= 2, 5))
        self.assertEqual(self.server.vehicles["V0042"].seq, 1)  # same record kept (resume)
        agent.stop()
        agent.thread.join(3)

    def test_fake_signal_rejected_by_client(self):
        agent = VehicleAgent("V7", "N", keys=self.keys, security=True)
        agent.handle_message(attach_sig(make_envelope("SIGNAL_STATE", "SERVER", {
            "phase": "NS_GREEN", "ns": "green", "ew": "red", "phase_elapsed_s": 0,
            "signal_mode": "fixed", "preemption": None}), b"wrong"))
        self.assertIsNone(agent.signal)
        self.assertEqual(agent.light(), "red")
        self.assertEqual(len(agent.security_rejections), 1)


class SpeedUpTests(unittest.TestCase):
    def test_time_scale_speeds_up_signals_and_vehicles(self):
        tmp = tempfile.TemporaryDirectory()
        keys = demo_keys()
        srv = IntersectionServer("127.0.0.1", 0, True, "fixed", keys, tmp.name, FAST, time_scale=4.0).start()
        try:
            t0 = time.monotonic()
            # FAST fixed green is 8 simulated s -> about 2 wall s at 4x
            self.assertTrue(wait_until(lambda: srv.controller.state(srv.sim_time()).phase != "NS_GREEN", 4))
            self.assertLess(time.monotonic() - t0, 3.0)
            agent = VehicleAgent("V0100", "E", port=srv.port, keys=keys, security=True,
                                 start_distance=60.0).start()
            self.assertTrue(wait_until(lambda: agent.registered, 3))
            self.assertEqual(agent.time_scale, 4.0)
            snap = srv.snapshot_payload(time.time())
            self.assertEqual(snap["stats"]["time_scale"], 4.0)
            agent.thread.join(20)
            self.assertTrue(agent.crossed_sent)
            self.assertGreater(agent.state.travel_time_s, 4.0)  # reported in simulated seconds
        finally:
            srv.stop()
            tmp.cleanup()


class SecurityOffTests(ServerTestCase):
    security = False

    def test_fake_ambulance_accepted_when_off(self):
        fake = self.client("AMB99", role="emergency", approach="E")
        self.assertIsNotNone(fake.wait_for("REGISTER_ACK"))
        fake.send("EMERGENCY_REQUEST", {"approach": "E", "eta_s": 5})
        self.assertTrue(wait_until(lambda: self.server.controller.preempt is not None, 2))
        fake.close()


if __name__ == "__main__":
    unittest.main()
