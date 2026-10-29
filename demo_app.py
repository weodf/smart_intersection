"""One-window demo control panel: dashboard + buttons + operation log.

Every demo step is a button: start/restart the system with chosen settings
(security, signal mode, traffic, speed-up), send the ambulance, restart the
server (reconnect test) and run attacks A1–A5. The operation log shows what
each click did, the output of every process, and live server events
(pre-emption, fallbacks, rejected messages).

Run: python3 demo_app.py
"""
from __future__ import annotations

import csv
import io
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - depends on the local Python build
    raise SystemExit("This Python has no Tk. Homebrew: 'brew install python-tk@<version>' "
                     "(e.g. python-tk@3.14); python.org installers include Tk.")

import config
from dashboard.gui import PANEL, Dashboard
from experiments.harness import ensure_keys, free_port
from experiments.sim import PRESETS

ROOT = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(ROOT, "captured.jsonl")
LOG_DIR = os.path.join(ROOT, config.LOG_DIR)
SPEEDS = (1, 2, 4, 8)

ATTACKS = {  # code: (button label, what it does, expected with security off, expected with security on)
    "A1": ("A1  Fake ambulance", "AMB99 claims to be an ambulance and asks for a green light",
           "accepted - the fake ambulance takes the green (Pre-emption ACTIVE)",
           "rejected - UNAUTHORISED / BAD_SIGNATURE, no pre-emption"),
    "A2": ("A2  Replay", "re-sends the real ambulance's recorded messages 10 s later",
           "accepted - the old request triggers pre-emption again",
           "rejected - STALE (timestamp too old)"),
    "A3": ("A3  Tampering", "changes the approach in a recorded signed message, keeps the old signature",
           "accepted - pre-emption on the attacker's chosen approach",
           "rejected - BAD_SIGNATURE"),
    "A4": ("A4  Fake signal", "injects a forged 'green' SIGNAL_STATE into a vehicle client",
           "the vehicle believes the fake green",
           "rejected by the vehicle - BAD_SIGNATURE, it keeps the real (red) state"),
    "A5": ("A5  Flooding (5 s)", "floods the server for 5 s while 10 normal clients measure latency",
           "server processes the flood; normal clients' latency rises",
           "flooder rate-limited and disconnected; normal latency unaffected"),
}


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class DemoApp:
    def __init__(self) -> None:
        self.keys = ensure_keys()
        self.port = config.PORT if port_free(config.PORT) else free_port()
        self.procs: dict[str, subprocess.Popen] = {}
        self.running: Optional[dict] = None          # settings of the running system
        self.logq: queue.Queue = queue.Queue()
        self.stop_evt = threading.Event()

        self.root = tk.Tk()
        self.root.title("Smart Intersection – Demo Control Panel")
        self.root.configure(bg=PANEL)
        dash_frame = tk.Frame(self.root, bg=PANEL)
        dash_frame.grid(row=0, column=0, sticky="n")
        self.dash = Dashboard(dash_frame, config.HOST, self.port, self.keys, True)
        self._build_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._log_tail_init()
        threading.Thread(target=self._tail_server_logs, daemon=True).start()
        self.root.after(100, self._drain_log)
        self.log("Control panel ready. Choose settings and press  ▶ Start / Restart system.", "action")

    # ------------------------------------------------------------------ UI
    def _build_controls(self) -> None:
        f = tk.Frame(self.root, bg=PANEL)
        f.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=8)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        h = ("Helvetica", 13, "bold")

        def section(text):
            tk.Label(f, text=text, font=h, bg=PANEL, anchor="w").pack(fill="x", pady=(8, 2))

        section("1. System settings")
        self.v_sec = tk.StringVar(value="on")
        self.v_sig = tk.StringVar(value="adaptive")
        self.v_traffic = tk.StringVar(value="asymmetric")
        self.v_speed = tk.IntVar(value=4)
        for label, var, options in (("Security", self.v_sec, (("ON", "on"), ("OFF", "off"))),
                                    ("Signal", self.v_sig, (("Fixed", "fixed"), ("Adaptive", "adaptive"))),
                                    ("Speed", self.v_speed, tuple((f"{s}×", s) for s in SPEEDS))):
            row = tk.Frame(f, bg=PANEL)
            row.pack(fill="x")
            tk.Label(row, text=label + ":", width=9, anchor="w", bg=PANEL).pack(side="left")
            for text, val in options:
                tk.Radiobutton(row, text=text, variable=var, value=val, bg=PANEL).pack(side="left")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Traffic:", width=9, anchor="w", bg=PANEL).pack(side="left")
        tk.OptionMenu(row, self.v_traffic, *PRESETS.keys()).pack(side="left")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", pady=(4, 0))
        tk.Button(row, text="▶  Start / Restart system", font=h, width=22,
                  command=lambda: self._bg(self.start_system)).pack(side="left")
        tk.Button(row, text="■  Stop all", width=10,
                  command=lambda: self._bg(self.stop_all)).pack(side="left", padx=4)
        self.status = tk.Label(f, text="System stopped", bg=PANEL, fg="#c62828", anchor="w",
                               font=("Helvetica", 12, "bold"))
        self.status.pack(fill="x", pady=(2, 0))

        section("2. Scenarios")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x")
        tk.Button(row, text="\U0001F691  Send ambulance", width=18,
                  command=lambda: self._bg(self.send_ambulance)).pack(side="left")
        tk.Button(row, text="⟳  Restart server (reconnect test)", width=26,
                  command=lambda: self._bg(self.restart_server)).pack(side="left", padx=4)

        section("3. Attacks (run against the current security mode)")
        grid = tk.Frame(f, bg=PANEL)
        grid.pack(fill="x")
        for i, (code, (label, *_)) in enumerate(ATTACKS.items()):
            tk.Button(grid, text=label, width=19, anchor="w",
                      command=lambda c=code: self._bg(self.run_attack, c)).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 4), pady=1)

        top = tk.Frame(f, bg=PANEL)
        top.pack(fill="x", pady=(10, 2))
        tk.Label(top, text="Operation log", font=h, bg=PANEL).pack(side="left")
        tk.Button(top, text="Clear", command=lambda: self.text.delete("1.0", tk.END)).pack(side="right")
        box = tk.Frame(f)
        box.pack(fill="both", expand=True)
        self.text = tk.Text(box, width=58, height=22, wrap="word", font=("Menlo", 10),
                            bg="#101418", fg="#cfd8dc", insertbackground="white")
        sb = tk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, colour in (("action", "#82b1ff"), ("ok", "#69f0ae"), ("bad", "#ff5252"),
                            ("warn", "#ffd740"), ("out", "#b0bec5"), ("event", "#ea80fc"),
                            ("sec", "#ff8a80"), ("info", "#eceff1")):
            self.text.tag_configure(tag, foreground=colour)
        self.text.tag_configure("action", font=("Menlo", 10, "bold"))

    def _bg(self, fn, *args) -> None:
        """Run a button action in a worker thread so the window never freezes."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ------------------------------------------------------------- logging
    def log(self, msg: str, tag: str = "info") -> None:
        """Thread-safe: queue a line for the operation log."""
        self.logq.put((time.strftime("%H:%M:%S"), msg, tag))

    def _drain_log(self) -> None:
        added = False
        while True:
            try:
                ts, msg, tag = self.logq.get_nowait()
            except queue.Empty:
                break
            self.text.insert(tk.END, f"{ts}  {msg}\n", tag)
            added = True
        if added:
            lines = int(self.text.index("end-1c").split(".")[0])
            if lines > 1500:
                self.text.delete("1.0", f"{lines - 1500}.0")
            self.text.see(tk.END)
        if self.running:
            r = self.running
            self.status.config(text=f"Running: security {r['security'].upper()} | {r['signal']} | "
                                    f"{r['traffic']} | {r['speed']}× speed | port {self.port}",
                               fg="#2e7d32")
        else:
            self.status.config(text="System stopped", fg="#c62828")
        self.root.after(100, self._drain_log)

    # ----------------------------------------------------------- processes
    def _spawn(self, name: str, module: str, *args: str, tag: str = "out") -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-u", "-m", module, *args], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.procs[name] = p
        threading.Thread(target=self._pipe, args=(name, p, tag), daemon=True).start()
        return p

    def _pipe(self, name: str, p: subprocess.Popen, tag: str) -> None:
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            t = tag
            if "RESULT: BLOCKED" in line:
                t = "ok"
            elif "RESULT: SUCCEEDED" in line:
                t = "bad"
            self.log(f"[{name}] {line}", t)

    def _kill(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    def _wait_port(self, timeout: float = 8.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.3).close()
                return True
            except OSError:
                time.sleep(0.1)
        return False

    def _start_server(self, r: dict) -> bool:
        self._spawn("server", "server.intersection_server", "--port", str(self.port),
                    "--security", r["security"], "--signal", r["signal"], "--speed", str(r["speed"]))
        ok = self._wait_port()
        if not ok:
            self.log("Server did not start (see messages above).", "bad")
        return ok

    # ------------------------------------------------------------- actions
    def start_system(self) -> None:
        r = {"security": self.v_sec.get(), "signal": self.v_sig.get(),
             "traffic": self.v_traffic.get(), "speed": int(self.v_speed.get())}
        self.log(f"▶ START: security {r['security'].upper()}, {r['signal']} signal, "
                 f"{r['traffic']} traffic, {r['speed']}× speed", "action")
        for name in list(self.procs):
            self._kill(name)
        self.running = None
        self.dash.set_security(r["security"] == "on")
        if not self._start_server(r):
            return
        self.log(f"  server listening on port {self.port}; dashboard connecting as observer")
        rates = PRESETS[r["traffic"]]
        self.log(f"  traffic generator: Poisson arrivals {rates} veh/h"
                 + (f", time accelerated {r['speed']}×" if r["speed"] > 1 else ""))
        self._spawn("traffic", "clients.traffic_generator", "--preset", r["traffic"],
                    "--duration", "36000", "--speed", str(r["speed"]), "--port", str(self.port),
                    "--security", r["security"])
        self.running = r
        hint = ("fixed 30 s greens regardless of demand" if r["signal"] == "fixed"
                else "greens follow demand (min 10 s, gap-out 3 s, max 45 s)")
        self.log(f"  signal control: {hint}")
        self.log("  vehicles start 200 m out and reach the stop line after ~15 s (simulated)", "info")

    def stop_all(self) -> None:
        self.log("■ STOP: terminating server, traffic, ambulance and attacks", "action")
        for name in list(self.procs):
            self._kill(name)
        self.running = None

    def send_ambulance(self) -> None:
        if not self._need_running():
            return
        snap = self.dash.latest
        approach = "E"
        if snap and snap["signal"]["ew"] == "green":
            approach = "N"  # pick an approach that is currently red, so pre-emption is visible
        if os.path.exists(CAPTURE):
            os.remove(CAPTURE)
        self.log(f"\U0001F691 AMBULANCE AMB01 dispatched on approach {approach} (currently red)", "action")
        self.log("  it requests pre-emption at 150 m: conflicting green -> yellow -> all-red -> "
                 f"{approach} green; watch the flashing red vehicle and 'Pre-emption'")
        self.log("  its messages are also recorded to captured.jsonl (an eavesdropper's capture, "
                 "used by attacks A2/A3)")
        self._spawn("ambulance", "clients.emergency_vehicle", "--approach", approach,
                    "--record", CAPTURE, "--port", str(self.port), "--security", self.running["security"])

    def restart_server(self) -> None:
        if not self._need_running():
            return
        r = self.running
        self.log("⟳ RESTART SERVER: stopping the server for 3 s", "action")
        self._kill("server")
        self.log("  server down - vehicles treat the signal as red after 3 s and stop; "
                 "dashboard shows 'Disconnected - retrying'", "warn")
        time.sleep(3.0)
        if self._start_server(r):
            self.log("  server back up - vehicles reconnect with back-off (0.5/1/2/4 s) and "
                     "re-register with resume=true", "ok")

    def run_attack(self, code: str) -> None:
        label, what, exp_off, exp_on = ATTACKS[code]
        if code != "A4" and not self._need_running():
            return
        sec = self.running["security"] if self.running else self.v_sec.get()
        if code in ("A2", "A3") and not self._capture_ready():
            self.log(f"{code}: no recorded ambulance messages yet - press 'Send ambulance' first "
                     "and wait until it has passed.", "warn")
            return
        self.log(f"☠ ATTACK {label.strip()} (security {sec.upper()}): {what}", "action")
        self.log(f"  expected: {exp_on if sec == 'on' else exp_off}", "info")
        args = ["--scenario", code, "--security", sec, "--port", str(self.port), "--capture", CAPTURE]
        if code == "A5":
            args += ["--sustain", "5"]
            self.log("  running for about 10 s (3 s baseline + 5 s flood + 2 s after)...", "info")
        self._spawn(f"attack {code}", "attacker.attacker", *args)

    def _need_running(self) -> bool:
        if self.running is None:
            self.log("Start the system first (▶ Start / Restart system).", "warn")
            return False
        return True

    def _capture_ready(self) -> bool:
        try:
            with open(CAPTURE, encoding="utf-8") as fh:
                return "EMERGENCY_REQUEST" in fh.read()
        except OSError:
            return False

    # ------------------------------------------------- live server log tail
    def _log_tail_init(self) -> None:
        self._tail_pos = {}
        for name in ("events.csv", "security_events.csv"):
            path = os.path.join(LOG_DIR, name)
            self._tail_pos[name] = os.path.getsize(path) if os.path.exists(path) else 0

    def _tail_server_logs(self) -> None:
        shown = {"PREEMPT_START": ("Pre-emption STARTED on approach {a} by {s}", "event"),
                 "PREEMPT_END": ("Pre-emption ENDED on approach {a} ({d})", "event"),
                 "FALLBACK_FIXED": ("No detection data - adaptive control fell back to fixed-time", "warn"),
                 "RESUME_ADAPTIVE": ("Detection data back - adaptive control resumed", "event"),
                 "RATE_LIMIT_DISCONNECT": ("Rate limit: {s} disconnected", "sec")}
        while not self.stop_evt.wait(0.5):
            for name in ("events.csv", "security_events.csv"):
                path = os.path.join(LOG_DIR, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                pos = self._tail_pos.get(name, 0)
                if size < pos:
                    pos = 0
                if size == pos:
                    continue
                with open(path, encoding="utf-8") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    self._tail_pos[name] = fh.tell()
                for row in csv.reader(io.StringIO(chunk)):
                    if not row or row[0] == "wall_time":
                        continue
                    if name == "events.csv" and len(row) >= 5 and row[1] in shown:
                        text, tag = shown[row[1]]
                        self.log("[server] " + text.format(a=row[2], s=row[3], d=row[4]), tag)
                    elif name == "security_events.csv" and len(row) >= 5:
                        self.log(f"[security] REJECTED {row[4].split(':')[0]} from {row[1]} "
                                 f"({row[3]}) - {row[4].split(':', 1)[-1].strip()}", "sec")

    # ---------------------------------------------------------------- main
    def quit(self) -> None:
        self.stop_evt.set()
        self.dash.stop.set()
        for name in list(self.procs):
            self._kill(name)
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()


if __name__ == "__main__":
    DemoApp().run()
