import json
import os
import tempfile
import unittest

from common.protocol import make_envelope
from common.security import KeyStore, ReplayCache, TokenBucket, attach_sig, sign, verify
from tools.gen_keys import generate


def demo_keys() -> KeyStore:
    raw = generate(["AMB01"])
    return KeyStore(bytes.fromhex(raw["server_key"]), bytes.fromhex(raw["fleet_key"]),
                    {k: bytes.fromhex(v) for k, v in raw["emergency_keys"].items()})


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.keys = demo_keys()

    def test_sign_verify_round_trip_after_serialisation(self):
        env = attach_sig(make_envelope("POSITION_UPDATE", "V1", {
            "approach": "N", "distance_m": 12.5, "speed_mps": 3.0, "state": "moving"}),
            self.keys.fleet_key)
        # Key order changes after a JSON round trip must not matter (canonical form).
        reparsed = json.loads(json.dumps(env, sort_keys=False))
        self.assertTrue(verify(reparsed, self.keys.fleet_key))

    def test_tamper_fails(self):
        env = attach_sig(make_envelope("EMERGENCY_REQUEST", "AMB01", {"approach": "N", "eta_s": 9}),
                         self.keys.emergency_keys["AMB01"])
        env["payload"]["approach"] = "E"
        self.assertFalse(verify(env, self.keys.emergency_keys["AMB01"]))

    def test_wrong_key_and_missing_sig_fail(self):
        env = make_envelope("PING", "V1", {"t_send": 1.0})
        self.assertFalse(verify(env, self.keys.fleet_key))
        env["sig"] = sign(env, b"wrong-key")
        self.assertFalse(verify(env, self.keys.fleet_key))

    def test_identify_role_from_key(self):
        amb = attach_sig(make_envelope("EMERGENCY_REQUEST", "AMB01", {"approach": "N", "eta_s": 5}),
                         self.keys.emergency_keys["AMB01"])
        self.assertEqual(self.keys.identify(amb), "emergency")
        fake = attach_sig(make_envelope("EMERGENCY_REQUEST", "AMB99", {"approach": "N", "eta_s": 5}),
                          self.keys.fleet_key)
        self.assertEqual(self.keys.identify(fake), "vehicle")
        spoof = attach_sig(make_envelope("EMERGENCY_REQUEST", "AMB01", {"approach": "N", "eta_s": 5}),
                           self.keys.fleet_key)
        self.assertEqual(self.keys.identify(spoof), "vehicle")  # fleet key, not AMB01's key
        unsigned = make_envelope("PING", "V1", {"t_send": 1.0})
        self.assertIsNone(self.keys.identify(unsigned))

    def test_keystore_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "k.json")
            with open(path, "w") as fh:
                json.dump(generate(["AMB01"]), fh)
            ks = KeyStore.load(path)
            self.assertEqual(len(ks.fleet_key), 32)
            self.assertIn("AMB01", ks.emergency_keys)


class ReplayTests(unittest.TestCase):
    def test_stale_rejected(self):
        rc = ReplayCache(window_s=2.0)
        self.assertEqual(rc.check("V1", "n1", ts=100.0, now=102.5), "STALE")
        self.assertEqual(rc.check("V1", "n1", ts=105.0, now=102.5), "STALE")

    def test_duplicate_nonce_rejected(self):
        rc = ReplayCache(window_s=2.0)
        self.assertIsNone(rc.check("V1", "n1", ts=100.0, now=100.1))
        self.assertEqual(rc.check("V1", "n1", ts=100.0, now=100.2), "REPLAY")
        self.assertIsNone(rc.check("V2", "n1", ts=100.0, now=100.2))  # per sender

    def test_purge(self):
        rc = ReplayCache(window_s=2.0)
        rc.check("V1", "n1", 100.0, 100.0)
        rc.purge(103.0)
        self.assertEqual(len(rc), 1)
        rc.purge(104.1)
        self.assertEqual(len(rc), 0)


class RateLimitTests(unittest.TestCase):
    def test_token_bucket(self):
        t = [0.0]
        tb = TokenBucket(rate=20, burst=40, clock=lambda: t[0])
        self.assertTrue(all(tb.allow() for _ in range(40)))
        self.assertFalse(tb.allow())
        t[0] += 0.5  # refills 10 tokens
        self.assertEqual(sum(tb.allow() for _ in range(20)), 10)

    def test_violation_disconnect_threshold(self):
        t = [0.0]
        tb = TokenBucket(rate=1, burst=1, clock=lambda: t[0])
        tb.allow()
        tb.allow()
        tb.allow()
        self.assertFalse(tb.too_many_violations(3, 10))
        tb.allow()
        self.assertTrue(tb.too_many_violations(3, 10))
        t[0] += 11
        self.assertFalse(tb.too_many_violations(3, 10))


if __name__ == "__main__":
    unittest.main()
