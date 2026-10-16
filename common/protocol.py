"""Wire protocol: message types, envelope, framing and validation.

Every message is one UTF-8 JSON object terminated by a newline. The envelope
fields are ``v, type, sender, ts, nonce, payload`` and an optional ``sig``.
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Any, Optional

import config

NUM = (int, float)
OPT_NUM = (int, float, type(None))
OPT_STR = (str, type(None))

# Required payload fields and their allowed Python types, per message type.
SCHEMA: dict[str, dict[str, tuple]] = {
    "REGISTER": {"role": (str,), "resume": (bool,)},
    "REGISTER_ACK": {"accepted": (bool,), "reason": (str,), "security_mode": (str,),
                     "signal_mode": (str,)},
    "POSITION_UPDATE": {"approach": (str,), "distance_m": NUM, "speed_mps": NUM, "state": (str,)},
    "CROSSED": {"approach": (str,), "wait_time_s": NUM, "travel_time_s": NUM},
    "EMERGENCY_REQUEST": {"approach": (str,), "eta_s": NUM},
    "EMERGENCY_CLEAR": {"approach": (str,)},
    "PING": {"t_send": NUM},
    "PONG": {"t_send": NUM, "t_server": NUM},
    "HEARTBEAT": {},
    "SIGNAL_STATE": {"phase": (str,), "ns": (str,), "ew": (str,), "phase_elapsed_s": NUM,
                     "signal_mode": (str,), "preemption": OPT_STR},
    "SNAPSHOT": {"signal": (dict,), "vehicles": (list,), "queues": (dict,), "stats": (dict,),
                 "security_events": (list,)},
    "QUEUE_INFO": {"ahead": (int,), "ahead_distance_m": OPT_NUM},
    "ERROR": {"code": (str,), "reason": (str,)},
}

ROLES = ("vehicle", "emergency", "observer")
ERROR_CODES = ("MALFORMED", "BAD_SIGNATURE", "STALE", "REPLAY", "RATE_LIMITED",
               "UNAUTHORISED", "NOT_REGISTERED")


class ProtocolError(Exception):
    """Raised for any message that cannot be accepted at the protocol level."""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def make_envelope(msg_type: str, sender: str, payload: Optional[dict] = None,
                  ts: Optional[float] = None, nonce: Optional[str] = None) -> dict:
    """Build an unsigned envelope with a fresh timestamp and nonce."""
    return {
        "v": config.PROTOCOL_VERSION,
        "type": msg_type,
        "sender": sender,
        "ts": time.time() if ts is None else ts,
        "nonce": secrets.token_hex(8) if nonce is None else nonce,
        "payload": payload or {},
    }


def canonical_bytes(env: dict) -> bytes:
    """Canonical JSON of the envelope without ``sig`` (the bytes that get signed)."""
    body = {k: v for k, v in env.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encode(env: dict, max_bytes: int = config.MAX_MSG_BYTES) -> bytes:
    """Serialise an envelope to one newline-terminated line."""
    data = json.dumps(env, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > max_bytes:
        raise ProtocolError("MALFORMED", "message too large to send")
    return data


def validate(env: Any) -> dict:
    """Check envelope structure and payload schema; return the envelope or raise."""
    if not isinstance(env, dict):
        raise ProtocolError("MALFORMED", "message is not a JSON object")
    for field, types in (("v", (int,)), ("type", (str,)), ("sender", (str,)),
                         ("ts", NUM), ("nonce", (str,)), ("payload", (dict,))):
        if field not in env:
            raise ProtocolError("MALFORMED", f"missing field '{field}'")
        if not isinstance(env[field], types) or isinstance(env[field], bool):
            raise ProtocolError("MALFORMED", f"field '{field}' has wrong type")
    if env["v"] != config.PROTOCOL_VERSION:
        raise ProtocolError("MALFORMED", "unsupported protocol version")
    if "sig" in env and not isinstance(env["sig"], (str, type(None))):
        raise ProtocolError("MALFORMED", "field 'sig' has wrong type")
    if not 0 < len(env["sender"]) <= 32:
        raise ProtocolError("MALFORMED", "bad sender id")
    schema = SCHEMA.get(env["type"])
    if schema is None:
        raise ProtocolError("MALFORMED", f"unknown type '{env['type']}'")
    payload = env["payload"]
    for field, types in schema.items():
        if field not in payload:
            raise ProtocolError("MALFORMED", f"payload missing '{field}'")
        value = payload[field]
        if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
            raise ProtocolError("MALFORMED", f"payload field '{field}' has wrong type")
    if env["type"] == "REGISTER":
        if payload["role"] not in ROLES:
            raise ProtocolError("MALFORMED", "unknown role")
        if payload["role"] != "observer" and payload.get("approach") not in config.APPROACHES:
            raise ProtocolError("MALFORMED", "bad approach")
    elif "approach" in schema and payload["approach"] not in config.APPROACHES:
        raise ProtocolError("MALFORMED", "bad approach")
    return env


def decode_line(line: bytes, max_bytes: int = config.MAX_MSG_BYTES) -> dict:
    """Parse and validate one line (without the trailing newline)."""
    if len(line) > max_bytes:
        raise ProtocolError("MALFORMED", "message too large")
    try:
        env = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProtocolError("MALFORMED", "invalid JSON") from None
    return validate(env)


class LineBuffer:
    """Accumulates bytes from a stream socket and splits them into lines.

    ``feed`` raises ``ProtocolError`` if a line grows beyond ``MAX_MSG_BYTES``.
    """

    def __init__(self, max_bytes: int = config.MAX_MSG_BYTES):
        self.max_bytes = max_bytes
        self._buf = b""

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes; return every complete line (newline stripped).

        If a line exceeds the limit, a ``ProtocolError`` is raised whose
        ``lines`` attribute holds the complete lines that came before it.
        """
        self._buf += data
        lines = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            line, self._buf = self._buf[:idx], self._buf[idx + 1:]
            if len(line) > self.max_bytes:
                raise _overflow(lines)
            if line.strip():
                lines.append(line)
        if len(self._buf) > self.max_bytes:
            raise _overflow(lines)
        return lines


def _overflow(lines: list[bytes]) -> ProtocolError:
    err = ProtocolError("MALFORMED", "message too large")
    err.lines = lines
    return err
