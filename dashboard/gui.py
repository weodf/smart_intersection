"""Tkinter dashboard: an observer client that visualises SNAPSHOTs.

A network thread puts SNAPSHOTs into a queue; the Tk main loop polls that
queue with ``root.after``. Tk widgets are only ever touched from the main thread.

Run: python -m dashboard.gui --security on
"""
from __future__ import annotations

import argparse
import queue
import threading
import time
from typing import Optional

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - depends on the local Python build
    raise SystemExit("This Python has no Tk. Homebrew: 'brew install python-tk@<version>' "
                     "(e.g. python-tk@3.14); python.org installers include Tk.")

import config
from common.net import ClientConnection
from common.security import KeyStore

SIZE = 620
C = SIZE / 2
ROAD_HALF = 34          # half road width in px
STOP_OFF = 44           # stop line distance from centre in px
SCALE = (C - STOP_OFF - 10) / config.SPAWN_DISTANCE_M  # px per metre
LANE_OFF = 17           # lane centre offset (NZ keeps left)
COLOURS = {"red": "#e53935", "yellow": "#fdd835", "green": "#43a047"}
BG, PANEL = "#1e2a33", "#f4f6f8"


def vehicle_xy(approach: str, d: float) -> tuple[float, float, bool]:
    """Canvas centre of a vehicle at distance ``d`` on ``approach``; bool = vertical."""
    off = STOP_OFF + d * SCALE
    if approach == "N":   # from the north, driving south, keep left (east side)
        return C + LANE_OFF, C - off, True
    if approach == "S":
        return C - LANE_OFF, C + off, True
    if approach == "E":   # from the east, driving west, keep left (south side)
        return C + off, C + LANE_OFF, False
    return C - off, C - LANE_OFF, False


class Dashboard:
    """Intersection view + statistics. ``parent`` may be the Tk root or a Frame."""

    def __init__(self, parent, host: str, port: int, keys: Optional[KeyStore], security: bool):
        self.root = parent.winfo_toplevel()
        self.host, self.port, self.keys, self.security = host, port, keys, security
        self.inbox: queue.Queue = queue.Queue(maxsize=50)
        self.status = "Connecting…"
        self.flash = False
        self.latest: Optional[dict] = None   # last SNAPSHOT (read by the demo control panel)
        self.stop = threading.Event()
        self._reconnect = threading.Event()
        if parent is self.root:
            self.root.title("Smart Intersection – Controller Dashboard")
        parent.configure(bg=PANEL)
        self.canvas = tk.Canvas(parent, width=SIZE, height=SIZE, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
        side = tk.Frame(parent, bg=PANEL)
        side.grid(row=0, column=1, sticky="n", padx=(0, 10), pady=8)
        self.sec_label = tk.Label(side, text="SECURITY ?", font=("Helvetica", 22, "bold"),
                                  fg="white", bg="#777", width=14, pady=6)
        self.sec_label.pack(fill="x")
        self.conn_label = tk.Label(side, text=self.status, font=("Helvetica", 12), bg=PANEL, fg="#555")
        self.conn_label.pack(anchor="w", pady=(4, 6))
        self.info = {}
        for key in ("Speed", "Signal mode", "Phase", "Pre-emption", "Queues N/S/E/W", "Connected vehicles",
                    "Vehicles crossed", "Average wait", "Rejected messages"):
            row = tk.Frame(side, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=key + ":", font=("Helvetica", 12), bg=PANEL, width=18,
                     anchor="w").pack(side="left")
            lbl = tk.Label(row, text="–", font=("Helvetica", 12, "bold"), bg=PANEL, anchor="w")
            lbl.pack(side="left")
            self.info[key] = lbl
        tk.Label(side, text="Security event log (last 10 rejected)", font=("Helvetica", 13, "bold"),
                 bg=PANEL).pack(anchor="w", pady=(14, 2))
        self.log = tk.Listbox(side, width=44, height=11, font=("Courier", 10), bg="#111",
                              fg="#ff8a80", highlightthickness=0)
        self.log.pack(fill="x")
        self._draw_static()
        threading.Thread(target=self._network, daemon=True).start()
        self.root.after(50, self._poll)
        self.root.after(400, self._blink)

    def set_security(self, security: bool) -> None:
        """Switch the observer to a server running in another security mode (reconnects)."""
        self.security = security
        self._reconnect.set()

    # ------------------------------------------------------------- network
    def _network(self) -> None:
        """Network thread: connect as observer, push SNAPSHOTs into the queue, reconnect."""
        while not self.stop.is_set():
            self._reconnect.clear()
            key = self.keys.fleet_key if (self.security and self.keys) else None
            server_key = self.keys.server_key if (self.security and self.keys) else None
            conn = ClientConnection(self.host, self.port, "DASH1", key, server_key,
                                    max_in_bytes=config.MAX_SNAPSHOT_BYTES)
            try:
                conn.connect()
                conn.send("REGISTER", {"role": "observer", "resume": False})
                self._push(("status", "Connected"))
                last_beat = time.monotonic()
                while not self.stop.is_set() and not self._reconnect.is_set():
                    for env in conn.recv(0.2):
                        if env["type"] == "SNAPSHOT":
                            self._push(("snap", env["payload"]))
                        elif env["type"] == "ERROR":
                            self._push(("status", f"Server error: {env['payload']['code']}"))
                    if time.monotonic() - last_beat > config.HEARTBEAT_PERIOD_S:
                        conn.send("HEARTBEAT")
                        last_beat = time.monotonic()
            except (OSError, ValueError):
                pass
            finally:
                conn.close()
            if self._reconnect.is_set():
                continue
            self._push(("status", "Disconnected – retrying…"))
            self.stop.wait(1.0)

    def _push(self, item) -> None:
        try:
            self.inbox.put_nowait(item)
        except queue.Full:
            try:
                self.inbox.get_nowait()
                self.inbox.put_nowait(item)
            except (queue.Empty, queue.Full):
                pass

    # ------------------------------------------------------------- Tk side
    def _poll(self) -> None:
        latest = None
        while True:
            try:
                kind, data = self.inbox.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status = data
                self.conn_label.config(text=data, fg="#2e7d32" if data == "Connected" else "#c62828")
            else:
                latest = data
        if latest is not None:
            self._render(latest)
        self.root.after(50, self._poll)

    def _blink(self) -> None:
        self.flash = not self.flash
        self.canvas.itemconfigure("amb", fill="#ff1744" if self.flash else "#ffffff")
        self.root.after(400, self._blink)

    def _draw_static(self) -> None:
        cv = self.canvas
        cv.create_rectangle(C - ROAD_HALF, 0, C + ROAD_HALF, SIZE, fill="#3b4750", outline="")
        cv.create_rectangle(0, C - ROAD_HALF, SIZE, C + ROAD_HALF, fill="#3b4750", outline="")
        for i in range(0, SIZE, 24):  # centre dashes
            if abs(i - C) > ROAD_HALF:
                cv.create_line(C, i, C, i + 12, fill="#cfd8dc")
                cv.create_line(i, C, i + 12, C, fill="#cfd8dc")
        # speed-up badge (top-left), filled in by _render
        cv.create_rectangle(10, 10, 10, 10, fill="", outline="", tags=("speed_bg",))
        cv.create_text(20, 26, text="", anchor="w", fill="#1e2a33",
                       font=("Helvetica", 18, "bold"), tags=("speed_txt",))
        cv.create_text(20, 52, text="", anchor="w", fill="#ffcc80",
                       font=("Helvetica", 12), tags=("speed_sub",))
        # stop lines on the approach lanes
        cv.create_line(C, C - STOP_OFF, C + ROAD_HALF, C - STOP_OFF, fill="white", width=3)
        cv.create_line(C - ROAD_HALF, C + STOP_OFF, C, C + STOP_OFF, fill="white", width=3)
        cv.create_line(C + STOP_OFF, C, C + STOP_OFF, C + ROAD_HALF, fill="white", width=3)
        cv.create_line(C - STOP_OFF, C - ROAD_HALF, C - STOP_OFF, C, fill="white", width=3)
        for a, (x, y) in {"N": (C + 58, C - 62), "S": (C - 58, C + 62),
                          "E": (C + 62, C + 58), "W": (C - 62, C - 58)}.items():
            cv.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#555", outline="black", width=2,
                           tags=("head", "head_" + a))
            lx, ly = {"N": (C + 20, 14), "S": (C - 20, SIZE - 14), "E": (SIZE - 14, C + 20),
                      "W": (14, C - 20)}[a]
            cv.create_text(lx, ly, text=a, fill="white", font=("Helvetica", 14, "bold"))

    def _render(self, snap: dict) -> None:
        cv = self.canvas
        self.latest = snap
        sig = snap["signal"]
        scale = float(snap["stats"].get("time_scale", 1.0))
        sim_t = snap["stats"].get("sim_elapsed_s")
        if scale > 1.0:
            cv.itemconfigure("speed_txt", text=f"\u25B6\u25B6  {scale:g}\u00d7 SPEED")
            cv.itemconfigure("speed_sub", text=f"sim time {sim_t:.0f} s"
                             if sim_t is not None else "accelerated demo")
            cv.coords("speed_bg", 10, 10, 206, 42)
            cv.itemconfigure("speed_bg", fill="#ffb300", outline="")
        else:
            cv.itemconfigure("speed_txt", text="")
            cv.itemconfigure("speed_sub", text="")
            cv.itemconfigure("speed_bg", fill="", outline="")
        for a in config.APPROACHES:
            colour = sig["ns"] if config.DIRECTION_OF[a] == "ns" else sig["ew"]
            cv.itemconfigure("head_" + a, fill=COLOURS.get(colour, "#555"))
        cv.delete("veh")
        for v in snap["vehicles"]:
            if v["distance_m"] < -config.CROSSED_PAST_M:
                continue
            x, y, vertical = vehicle_xy(v["approach"], v["distance_m"])
            w, h = (4, 5) if vertical else (5, 4)   # ~7 m long, so queued cars do not overlap
            stopped = v["state"] == "stopped"
            fill = "#ff1744" if v["emergency"] else ("#0d47a1" if stopped else "#42a5f5")
            tags = ("veh", "amb") if v["emergency"] else ("veh",)
            cv.create_rectangle(x - w, y - h, x + w, y + h, fill=fill, outline="white" if
                                v["emergency"] else "#0b2545", tags=tags)
        stats, q = snap["stats"], snap["queues"]
        sec_on = stats.get("security_mode") == "on"
        self.sec_label.config(text="SECURITY ON" if sec_on else "SECURITY OFF",
                              bg="#2e7d32" if sec_on else "#c62828")
        self.info["Speed"].config(text=f"{scale:g}\u00d7 (accelerated)" if scale > 1 else "1\u00d7 real time",
                                  fg="#e65100" if scale > 1 else "black")
        self.info["Signal mode"].config(text=sig["signal_mode"])
        self.info["Phase"].config(text=f"{sig['phase']}  ({sig['phase_elapsed_s']:.0f}s)")
        pre = sig.get("preemption")
        self.info["Pre-emption"].config(text=f"ACTIVE ({pre})" if pre else "none",
                                        fg="#c62828" if pre else "black")
        self.info["Queues N/S/E/W"].config(text=" / ".join(str(q[a]) for a in "NSEW"))
        self.info["Connected vehicles"].config(text=str(stats["connected"]))
        self.info["Vehicles crossed"].config(text=str(stats["crossed"]))
        self.info["Average wait"].config(text=f"{stats['avg_wait_s']:.1f} s")
        self.info["Rejected messages"].config(text=str(stats.get("rejected", 0)))
        self.log.delete(0, tk.END)
        for e in reversed(snap["security_events"]):
            t = time.strftime("%H:%M:%S", time.localtime(e["time"]))
            self.log.insert(tk.END, f"{t} {e['reason']:<13} {e['sender']:<6} {e['type']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Intersection dashboard (observer)")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--security", choices=["on", "off"], default="on")
    ap.add_argument("--keys", default=config.KEYS_FILE)
    args = ap.parse_args()
    keys = KeyStore.load(args.keys) if args.security == "on" else None
    root = tk.Tk()
    dash = Dashboard(root, args.host, args.port, keys, args.security == "on")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        dash.stop.set()


if __name__ == "__main__":
    main()
