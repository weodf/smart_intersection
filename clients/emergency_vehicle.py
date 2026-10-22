"""Legitimate ambulance (AMB01) holding its own emergency key.

It requests pre-emption at 150 m, drives through, then sends EMERGENCY_CLEAR.
``--record captured.jsonl`` writes every message it sends to a file. This
simulates an eavesdropper on an unencrypted network; the replay attack (A2)
uses that file.

Example: python -m clients.emergency_vehicle --approach E --security on --record captured.jsonl
"""
from __future__ import annotations

import argparse

import config
from clients.vehicle_agent import VehicleAgent
from common.security import KeyStore


def make_ambulance(approach: str, host: str = config.HOST, port: int = config.PORT,
                   keys=None, security: bool = True, vid: str = "AMB01",
                   record_path=None) -> VehicleAgent:
    """Create (but do not start) an ambulance agent."""
    return VehicleAgent(vid, approach, host, port, keys, security, emergency=True,
                        record_path=record_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the legitimate ambulance")
    ap.add_argument("--id", default="AMB01")
    ap.add_argument("--approach", choices=list(config.APPROACHES), default="E")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--security", choices=["on", "off"], default="on")
    ap.add_argument("--keys", default=config.KEYS_FILE)
    ap.add_argument("--record", help="write every sent message to this .jsonl file")
    args = ap.parse_args()
    keys = KeyStore.load(args.keys) if args.security == "on" else None
    amb = make_ambulance(args.approach, args.host, args.port, keys, args.security == "on",
                         args.id, args.record)
    amb.run()
    print(f"{args.id}: crossed={amb.crossed_sent} wait={amb.state.wait_time_s:.1f}s "
          f"travel={amb.state.travel_time_s:.1f}s requested={amb.emergency_requested}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
