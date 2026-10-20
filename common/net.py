"""Small client-side connection helper shared by vehicles, the dashboard,
the attacker and the experiments."""
from __future__ import annotations

import select
import socket
from typing import Optional

import config
from common.protocol import LineBuffer, ProtocolError, decode_line, encode, make_envelope
from common.security import attach_sig, verify


class ClientConnection:
    """One TCP connection that sends signed envelopes and receives parsed ones.

    ``key`` signs outgoing messages (None = unsigned). ``server_key`` verifies
    incoming messages (None = accept unsigned). Messages failing verification
    are counted in ``rejected`` and not returned.
    """

    def __init__(self, host: str, port: int, sender: str, key: Optional[bytes] = None,
                 server_key: Optional[bytes] = None, record_path: Optional[str] = None,
                 max_in_bytes: int = config.MAX_MSG_BYTES):
        self.host, self.port, self.sender = host, port, sender
        self.key, self.server_key = key, server_key
        self.record_path = record_path
        self.sock: Optional[socket.socket] = None
        self.buf = LineBuffer(max_in_bytes)
        self.max_in_bytes = max_in_bytes
        self.rejected: list[dict] = []

    def connect(self, timeout: float = 3.0) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(config.SEND_TIMEOUT_S * 3)
        self.buf = LineBuffer(self.max_in_bytes)

    @property
    def connected(self) -> bool:
        return self.sock is not None

    def send(self, msg_type: str, payload: Optional[dict] = None) -> dict:
        """Build, sign and send a message; returns the envelope sent."""
        env = attach_sig(make_envelope(msg_type, self.sender, payload), self.key)
        self.send_env(env)
        return env

    def send_env(self, env: dict) -> None:
        line = encode(env)
        self.send_raw(line)
        if self.record_path:
            with open(self.record_path, "ab") as fh:
                fh.write(line)

    def send_raw(self, data: bytes) -> None:
        if self.sock is None:
            raise ConnectionError("not connected")
        self.sock.sendall(data)

    def recv(self, timeout: float = 0.0) -> list[dict]:
        """Return all messages available within ``timeout`` seconds.

        Raises ConnectionError if the server closed the connection.
        """
        if self.sock is None:
            raise ConnectionError("not connected")
        out: list[dict] = []
        ready, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        while ready:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("server closed the connection")
            for line in self.buf.feed(data):
                try:
                    env = decode_line(line, self.max_in_bytes)
                except ProtocolError:
                    continue
                if self.server_key is not None and not verify(env, self.server_key):
                    self.rejected.append(env)
                    continue
                out.append(env)
            ready, _, _ = select.select([self.sock], [], [], 0)
        return out

    def wait_for(self, msg_type: str, timeout: float = 3.0) -> Optional[dict]:
        """Block until a message of ``msg_type`` arrives (others are discarded)."""
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for env in self.recv(end - time.monotonic()):
                if env["type"] == msg_type:
                    return env
        return None

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
