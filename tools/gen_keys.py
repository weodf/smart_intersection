"""Generate random pre-shared demo keys and write them to keys.json.

Usage: python tools/gen_keys.py [--out keys.json] [--emergency AMB01,AMB02]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402


def generate(emergency_ids: list[str]) -> dict:
    """Return a dict of fresh 256-bit hex keys."""
    return {
        "server_key": secrets.token_hex(32),
        "fleet_key": secrets.token_hex(32),
        "emergency_keys": {eid: secrets.token_hex(32) for eid in emergency_ids},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=config.KEYS_FILE)
    ap.add_argument("--emergency", default="AMB01", help="comma-separated emergency vehicle IDs")
    args = ap.parse_args()
    keys = generate([e.strip() for e in args.emergency.split(",") if e.strip()])
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(keys, fh, indent=2)
    print(f"Wrote {args.out} with emergency IDs: {', '.join(keys['emergency_keys'])}")


if __name__ == "__main__":
    main()
