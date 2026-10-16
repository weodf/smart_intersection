import json
import unittest

import config
from common.protocol import LineBuffer, ProtocolError, decode_line, encode, make_envelope


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        env = make_envelope("PING", "V0001", {"t_send": 1.5})
        line = encode(env)
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(decode_line(line[:-1]), env)

    def test_invalid_json(self):
        with self.assertRaises(ProtocolError) as cm:
            decode_line(b"{not json")
        self.assertEqual(cm.exception.code, "MALFORMED")

    def test_missing_field(self):
        env = make_envelope("PING", "V0001", {"t_send": 1.0})
        del env["nonce"]
        with self.assertRaises(ProtocolError):
            decode_line(json.dumps(env).encode())

    def test_unknown_type_and_wrong_types(self):
        for mutate in (lambda e: e.update(type="NOPE"),
                       lambda e: e.update(ts="yesterday"),
                       lambda e: e["payload"].update(t_send="x"),
                       lambda e: e.update(payload=[]),
                       lambda e: e.update(v=99)):
            env = make_envelope("PING", "V0001", {"t_send": 1.0})
            mutate(env)
            with self.assertRaises(ProtocolError):
                decode_line(json.dumps(env).encode())

    def test_register_validation(self):
        good = make_envelope("REGISTER", "V1", {"role": "vehicle", "approach": "N", "resume": False})
        decode_line(json.dumps(good).encode())
        obs = make_envelope("REGISTER", "D1", {"role": "observer", "resume": False})
        decode_line(json.dumps(obs).encode())
        bad = make_envelope("REGISTER", "V1", {"role": "vehicle", "approach": "X", "resume": False})
        with self.assertRaises(ProtocolError):
            decode_line(json.dumps(bad).encode())
        bad_role = make_envelope("REGISTER", "V1", {"role": "god", "approach": "N", "resume": False})
        with self.assertRaises(ProtocolError):
            decode_line(json.dumps(bad_role).encode())

    def test_oversize(self):
        with self.assertRaises(ProtocolError):
            decode_line(b"x" * (config.MAX_MSG_BYTES + 1))
        buf = LineBuffer()
        with self.assertRaises(ProtocolError):
            buf.feed(b"a" * (config.MAX_MSG_BYTES + 10))
        with self.assertRaises(ProtocolError):
            encode(make_envelope("PING", "V1", {"t_send": 1.0, "pad": "x" * 5000}))

    def test_line_buffer_split(self):
        buf = LineBuffer()
        self.assertEqual(buf.feed(b'{"a":1}\n{"b"'), [b'{"a":1}'])
        self.assertEqual(buf.feed(b':2}\n\n'), [b'{"b":2}'])


if __name__ == "__main__":
    unittest.main()
