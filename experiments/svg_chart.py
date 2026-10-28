"""Tiny stdlib-only SVG chart writer: grouped bar charts and line charts.

White background, title, axis labels, legend, recessive grid. Series colours
follow a fixed categorical order (never cycled past the palette length).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence
from xml.sax.saxutils import escape

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, INK2, GRID, AXIS = "#0b0b0b", "#52514e", "#e6e5e0", "#9b9a94"
FONT = "font-family='Helvetica, Arial, sans-serif'"


def nice_max(v: float) -> tuple[float, float]:
    """A round axis maximum (and tick step, 4-6 ticks) covering ``v``."""
    if v <= 0:
        return 1.0, 0.2
    exp = 10 ** math.floor(math.log10(v))
    for m, ticks in ((1, 5), (1.2, 6), (1.5, 5), (2, 5), (2.5, 5), (3, 6), (4, 4), (5, 5),
                     (6, 6), (8, 4), (10, 5)):
        if m * exp >= v:
            return m * exp, m * exp / ticks
    return 10 * exp, 2 * exp


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v) >= 100 or v == int(v) else f"{v:.1f}"


class _Canvas:
    def __init__(self, width: int, height: int, title: str, ylabel: str, xlabel: str):
        self.w, self.h = width, height
        self.left, self.right, self.top, self.bottom = 72, 24, 80, 78
        self.parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
                      f"viewBox='0 0 {width} {height}'>",
                      f"<rect width='100%' height='100%' fill='#ffffff'/>",
                      f"<text x='{self.left}' y='28' {FONT} font-size='18' font-weight='bold' "
                      f"fill='{INK}'>{escape(title)}</text>"]
        self.ylabel, self.xlabel = ylabel, xlabel

    @property
    def pw(self):
        return self.w - self.left - self.right

    @property
    def ph(self):
        return self.h - self.top - self.bottom

    def y(self, v: float, ymax: float) -> float:
        return self.top + self.ph * (1 - v / ymax)

    def axes(self, ymax: float, step: float) -> None:
        v = 0.0
        while v <= ymax + 1e-9:
            yy = self.y(v, ymax)
            self.parts.append(f"<line x1='{self.left}' x2='{self.left + self.pw}' y1='{yy:.1f}' "
                              f"y2='{yy:.1f}' stroke='{GRID if v else AXIS}' stroke-width='1'/>")
            self.parts.append(f"<text x='{self.left - 8}' y='{yy + 4:.1f}' {FONT} font-size='12' "
                              f"text-anchor='end' fill='{INK2}'>{_fmt(v)}</text>")
            v += step
        cy = self.top + self.ph / 2
        self.parts.append(f"<text x='18' y='{cy}' {FONT} font-size='13' fill='{INK2}' "
                          f"text-anchor='middle' transform='rotate(-90 18 {cy})'>{escape(self.ylabel)}</text>")
        self.parts.append(f"<text x='{self.left + self.pw / 2}' y='{self.h - 14}' {FONT} font-size='13' "
                          f"fill='{INK2}' text-anchor='middle'>{escape(self.xlabel)}</text>")

    def legend(self, names: Sequence[str], dashed: Sequence[bool] = ()) -> None:
        x = self.left
        for i, name in enumerate(names):
            c = PALETTE[i]
            dash = " stroke-dasharray='5 3'" if (dashed and dashed[i]) else ""
            self.parts.append(f"<line x1='{x}' x2='{x + 18}' y1='46' y2='46' stroke='{c}' "
                              f"stroke-width='8' stroke-linecap='round'{dash}/>")
            self.parts.append(f"<text x='{x + 26}' y='50' {FONT} font-size='13' fill='{INK}'>"
                              f"{escape(name)}</text>")
            x += 40 + 7.5 * len(name)

    def save(self, path: str) -> None:
        self.parts.append("</svg>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.parts))


def bar_chart(path: str, title: str, categories: Sequence[str], series: dict[str, Sequence[float]],
              ylabel: str, xlabel: str = "", errors: Optional[dict[str, Sequence[float]]] = None,
              width: int = 820, height: int = 460, value_labels: bool = True,
              y_max: Optional[float] = None) -> None:
    """Grouped vertical bars; optional symmetric error bars per series."""
    names = list(series)[:len(PALETTE)]
    cv = _Canvas(width, height, title, ylabel, xlabel)
    top = max(v + (errors[n][i] if errors and n in errors else 0)
              for n in names for i, v in enumerate(series[n]))
    ymax, step = (y_max, y_max / 5) if y_max else nice_max(top * 1.08)
    cv.axes(ymax, step)
    group_w = cv.pw / len(categories)
    bar_w = min(46.0, group_w * 0.7 / len(names))
    for ci, cat in enumerate(categories):
        gx = cv.left + group_w * ci + (group_w - bar_w * len(names) - 2 * (len(names) - 1)) / 2
        for si, n in enumerate(names):
            v = series[n][ci]
            x = gx + si * (bar_w + 2)
            y0, y1 = cv.y(0, ymax), cv.y(v, ymax)
            hgt = max(0.0, y0 - y1)
            r = min(4.0, hgt / 2, bar_w / 2)
            # rounded top, square base
            cv.parts.append(f"<path d='M{x:.1f},{y0:.1f} V{y1 + r:.1f} Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f} "
                            f"H{x + bar_w - r:.1f} Q{x + bar_w:.1f},{y1:.1f} {x + bar_w:.1f},{y1 + r:.1f} "
                            f"V{y0:.1f} Z' fill='{PALETTE[si]}'><title>{escape(n)} – {escape(cat)}: "
                            f"{v:.3g}</title></path>")
            if errors and n in errors:
                e = errors[n][ci]
                xm = x + bar_w / 2
                ya, yb = cv.y(v + e, ymax), cv.y(max(0, v - e), ymax)
                cv.parts.append(f"<path d='M{xm:.1f},{ya:.1f} V{yb:.1f} M{xm - 5:.1f},{ya:.1f} H{xm + 5:.1f} "
                                f"M{xm - 5:.1f},{yb:.1f} H{xm + 5:.1f}' stroke='{INK}' stroke-width='1.5' fill='none'/>")
            if value_labels:
                ly = (cv.y(v + (errors[n][ci] if errors and n in errors else 0), ymax)) - 6
                cv.parts.append(f"<text x='{x + bar_w / 2:.1f}' y='{ly:.1f}' {FONT} font-size='11' "
                                f"text-anchor='middle' fill='{INK2}'>{_fmt(v)}</text>")
        cv.parts.append(f"<text x='{cv.left + group_w * (ci + 0.5):.1f}' y='{cv.top + cv.ph + 22}' {FONT} "
                        f"font-size='13' text-anchor='middle' fill='{INK}'>{escape(cat)}</text>")
    if len(names) > 1:  # a single series is named by the title
        cv.legend(names)
    cv.save(path)


def line_chart(path: str, title: str, xs: Sequence[float], series: dict[str, Sequence[float]],
               ylabel: str, xlabel: str, dashed: Sequence[bool] = (), width: int = 820,
               height: int = 460) -> None:
    """Simple line chart with markers on a categorical-spaced x axis."""
    names = list(series)[:len(PALETTE)]
    cv = _Canvas(width, height, title, ylabel, xlabel)
    ymax, step = nice_max(max(max(series[n]) for n in names) * 1.08)
    cv.axes(ymax, step)
    xpos = [cv.left + cv.pw * (0.08 + 0.84 * i / max(1, len(xs) - 1)) for i in range(len(xs))]
    for i, x in enumerate(xs):
        cv.parts.append(f"<text x='{xpos[i]:.1f}' y='{cv.top + cv.ph + 22}' {FONT} font-size='13' "
                        f"text-anchor='middle' fill='{INK}'>{_fmt(x)}</text>")
    for si, n in enumerate(names):
        pts = [(xpos[i], cv.y(v, ymax)) for i, v in enumerate(series[n])]
        dash = " stroke-dasharray='6 4'" if (dashed and dashed[si]) else ""
        cv.parts.append(f"<polyline points='{' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)}' fill='none' "
                        f"stroke='{PALETTE[si]}' stroke-width='2'{dash}/>")
        for (x, y), v in zip(pts, series[n]):
            cv.parts.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4.5' fill='{PALETTE[si]}' stroke='#ffffff' "
                            f"stroke-width='2'><title>{escape(n)}: {v:.3g}</title></circle>")
        lx, ly = pts[-1]
        cv.parts.append(f"<text x='{lx + 8:.1f}' y='{ly + 4:.1f}' {FONT} font-size='11' fill='{INK2}'>"
                        f"{_fmt(series[n][-1])}</text>")
    cv.legend(names, dashed)
    cv.save(path)
