"""Helpers shared by the experiment scripts."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import config
from common.security import KeyStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_keys() -> KeyStore:
    """Load keys.json, generating it first if it does not exist."""
    path = os.path.join(ROOT, config.KEYS_FILE)
    if not os.path.exists(path):
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_keys.py"), "--out", path],
                       check=True)
    return KeyStore.load(path)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"server on port {port} did not start")


class ServerProcess:
    """Run the real server as a separate process (no shared GIL with the clients)."""

    def __init__(self, security: bool, signal: str = "fixed", port: int = 0, log_dir: str = "logs"):
        self.port = port or free_port()
        self.args = [sys.executable, "-m", "server.intersection_server", "--port", str(self.port),
                     "--security", "on" if security else "off", "--signal", signal,
                     "--keys", os.path.join(ROOT, config.KEYS_FILE),
                     "--log-dir", os.path.join(ROOT, log_dir)]
        self.proc = None

    def start(self) -> "ServerProcess":
        self.proc = subprocess.Popen(self.args, cwd=ROOT, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        wait_port(self.port)
        return self

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def results_path(name: str) -> str:
    os.makedirs(os.path.join(ROOT, config.RESULTS_DIR), exist_ok=True)
    return os.path.join(ROOT, config.RESULTS_DIR, name)


def print_table(header: list, rows: list) -> None:
    """Print a simple aligned console table."""
    cells = [[str(h) for h in header]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    for n, r in enumerate(cells):
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
        if n == 0:
            print("  ".join("-" * w for w in widths))
