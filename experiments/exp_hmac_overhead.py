"""Cost of security: HMAC sign/verify time for a typical POSITION_UPDATE.

Writes results/hmac_overhead.csv.
Run: python -m experiments.exp_hmac_overhead [--n 100000]
"""
from __future__ import annotations

import argparse
import timeit

from common.logging_utils import write_csv
from common.protocol import decode_line, encode, make_envelope
from common.security import attach_sig, sign, verify
from experiments.harness import ensure_keys, print_table, results_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    args = ap.parse_args()
    key = ensure_keys().fleet_key
    payload = {"approach": "N", "distance_m": 123.45, "speed_mps": 11.2, "state": "moving"}
    env = make_envelope("POSITION_UPDATE", "V0042", payload)
    signed = attach_sig(dict(env), key)
    line_plain = encode(env)[:-1]
    line_signed = encode(signed)[:-1]

    def full_plain():
        decode_line(encode(make_envelope("POSITION_UPDATE", "V0042", payload))[:-1])

    def full_signed():
        e = attach_sig(make_envelope("POSITION_UPDATE", "V0042", payload), key)
        d = decode_line(encode(e)[:-1])
        assert verify(d, key)

    cases = [
        ("sign", lambda: sign(env, key)),
        ("verify", lambda: verify(signed, key)),
        ("encode+decode (unsigned)", lambda: decode_line(encode(env)[:-1])),
        ("full round trip unsigned (make+encode+decode)", full_plain),
        ("full round trip signed (make+sign+encode+decode+verify)", full_signed),
    ]
    rows = []
    for name, fn in cases:
        best = min(timeit.repeat(fn, number=args.n, repeat=3)) / args.n
        rows.append([name, round(best * 1e6, 3), round(1 / best)])
    overhead = rows[4][1] - rows[3][1]
    rows.append(["security overhead per message (signed - unsigned)", round(overhead, 3),
                 round(1e6 / overhead) if overhead > 0 else ""])
    header = ["operation", "mean_us", "ops_per_s"]
    write_csv(results_path("hmac_overhead.csv"), header, rows)
    print_table(header, rows)
    print(f"\nMessage size: {len(line_plain)} bytes unsigned, {len(line_signed)} bytes signed "
          f"(+{len(line_signed) - len(line_plain)} bytes)")
    print("Wrote results/hmac_overhead.csv")


if __name__ == "__main__":
    main()
