"""Security primitives: HMAC-SHA256 signing, key store, replay cache, rate limiter.

No custom cryptography: only ``hmac`` with ``hashlib.sha256``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from typing import Callable, Optional

import config
from common.protocol import canonical_bytes


def sign(env: dict, key: bytes) -> str:
    """Return the hex HMAC-SHA256 of the envelope's canonical form."""
    return hmac.new(key, canonical_bytes(env), hashlib.sha256).hexdigest()


def attach_sig(env: dict, key: Optional[bytes]) -> dict:
    """Sign the envelope in place (no-op when ``key`` is None) and return it."""
    if key is not None:
        env["sig"] = sign(env, key)
    return env


def verify(env: dict, key: bytes) -> bool:
    """Constant-time check that ``env['sig']`` is valid for ``key``."""
    sig = env.get("sig")
    if not isinstance(sig, str) or not sig:
        return False
    return hmac.compare_digest(sig, sign(env, key))


class KeyStore:
    """Pre-shared demo keys loaded from ``keys.json``.

    ``server_key`` signs server->client traffic, ``fleet_key`` is shared by all
    normal vehicles (and observers), and ``emergency_keys`` maps each emergency
    vehicle ID to its own key.
    """

    def __init__(self, server_key: bytes, fleet_key: bytes, emergency_keys: dict[str, bytes]):
        self.server_key = server_key
        self.fleet_key = fleet_key
        self.emergency_keys = emergency_keys

    @classmethod
    def load(cls, path: str = config.KEYS_FILE) -> "KeyStore":
        """Load keys from a JSON file of hex strings."""
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(bytes.fromhex(raw["server_key"]), bytes.fromhex(raw["fleet_key"]),
                   {k: bytes.fromhex(v) for k, v in raw["emergency_keys"].items()})

    def key_for(self, sender: str) -> bytes:
        """Key a legitimate client with this ID signs with."""
        return self.emergency_keys.get(sender, self.fleet_key)

    def identify(self, env: dict) -> Optional[str]:
        """Return the role proven by the signature ('emergency' or 'vehicle'), or None.

        The role comes from *which key verifies*, never from the message body.
        """
        ekey = self.emergency_keys.get(env.get("sender", ""))
        if ekey is not None and verify(env, ekey):
            return "emergency"
        if verify(env, self.fleet_key):
            return "vehicle"
        return None


class ReplayCache:
    """Freshness window plus per-sender nonce cache with expiry."""

    def __init__(self, window_s: float = config.FRESHNESS_WINDOW_S):
        self.window_s = window_s
        self.ttl_s = 2 * window_s
        self._seen: dict[str, dict[str, float]] = {}

    def check(self, sender: str, nonce: str, ts: float, now: float) -> Optional[str]:
        """Return None if fresh and new (and remember it), else 'STALE' or 'REPLAY'."""
        if abs(now - ts) > self.window_s:
            return "STALE"
        nonces = self._seen.setdefault(sender, {})
        if nonce in nonces:
            return "REPLAY"
        nonces[nonce] = now + self.ttl_s
        return None

    def purge(self, now: float) -> None:
        """Drop expired nonces and empty senders."""
        for sender in list(self._seen):
            nonces = self._seen[sender]
            for n in [n for n, exp in nonces.items() if exp < now]:
                del nonces[n]
            if not nonces:
                del self._seen[sender]

    def __len__(self) -> int:
        return sum(len(v) for v in self._seen.values())


class TokenBucket:
    """Classic token bucket; also counts recent violations."""

    def __init__(self, rate: float = config.RATE_LIMIT_PER_S, burst: int = config.RATE_BURST,
                 clock: Callable[[], float] = time.monotonic):
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.tokens = float(burst)
        self.last = clock()
        self.violations: deque[float] = deque()

    def allow(self) -> bool:
        """Consume one token if available."""
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        self.violations.append(now)
        return False

    def too_many_violations(self, limit: int = config.RATE_VIOLATIONS_MAX,
                            window_s: float = config.RATE_VIOLATION_WINDOW_S) -> bool:
        """True once ``limit`` violations happened within ``window_s``."""
        now = self.clock()
        while self.violations and now - self.violations[0] > window_s:
            self.violations.popleft()
        return len(self.violations) >= limit
