"""DrawingML geometry: the shape-guide formula language, the 187 preset
shapes (ECMA-376 Annex D, compiled to data/presets.json) and custom geometry.

All computation happens in shape-local EMU-like units (the shape's width and
height); callers scale the resulting SVG path data.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from slidelite.presentation.xmlsafe import local, q

_DATA = Path(__file__).resolve().parent.parent / "data" / "presets.json"
_ANGLE = 60000.0  # angles are expressed in 1/60000 degree
_DEG = math.pi / 180.0


@lru_cache(maxsize=1)
def presets() -> dict:
    return json.loads(_DATA.read_text())


@dataclass
class GeomPath:
    d: str
    fill: str = "norm"  # norm | none | lighten | lightenLess | darken | darkenLess
    stroke: bool = True


@dataclass
class Geometry:
    paths: list[GeomPath] = field(default_factory=list)
    text_rect: tuple[float, float, float, float] | None = None  # l, t, r, b

    @property
    def is_empty(self) -> bool:
        return not self.paths


class Guides:
    """Evaluates shape guides for a shape of size ``w`` x ``h``."""

    def __init__(self, w: float, h: float) -> None:
        w = float(w)
        h = float(h)
        ss = min(w, h)
        self.v: dict[str, float] = {
            "w": w,
            "h": h,
            "l": 0.0,
            "t": 0.0,
            "r": w,
            "b": h,
            "hc": w / 2,
            "vc": h / 2,
            "ss": ss,
            "ls": max(w, h),
            "cd2": 10800000.0,
            "cd4": 5400000.0,
            "cd8": 2700000.0,
            "3cd4": 16200000.0,
            "3cd8": 8100000.0,
            "5cd8": 13500000.0,
            "7cd8": 18900000.0,
        }
        for n in (2, 3, 4, 5, 6, 8, 10, 12, 32):
            self.v[f"wd{n}"] = w / n
        for n in (2, 3, 4, 5, 6, 8, 10, 12):
            self.v[f"hd{n}"] = h / n
        for n in (2, 4, 6, 8, 16, 32):
            self.v[f"ssd{n}"] = ss / n

    def val(self, token: str | None) -> float:
        if token is None:
            return 0.0
        found = self.v.get(token)
        if found is not None:
            return found
        try:
            return float(token)
        except ValueError:
            return 0.0

    def define(self, name: str, fmla: str) -> None:
        self.v[name] = self.evaluate(fmla)

    def evaluate(self, fmla: str) -> float:
        parts = fmla.split()
        if not parts:
            return 0.0
        op, args = parts[0], [self.val(a) for a in parts[1:]]
        x, y, z = (args + [0.0, 0.0, 0.0])[:3]
        try:
            if op == "val":
                return x
            if op == "*/":
                return x * y / z if z else 0.0
            if op == "+-":
                return x + y - z
            if op == "+/":
                return (x + y) / z if z else 0.0
            if op == "?:":
                return y if x > 0 else z
            if op == "abs":
                return abs(x)
            if op == "at2":
                return math.atan2(y, x) / _DEG * _ANGLE
            if op == "cat2":
                return x * math.cos(math.atan2(z, y))
            if op == "sat2":
                return x * math.sin(math.atan2(z, y))
            if op == "cos":
                return x * math.cos(y / _ANGLE * _DEG)
            if op == "sin":
                return x * math.sin(y / _ANGLE * _DEG)
            if op == "tan":
                return x * math.tan(y / _ANGLE * _DEG)
            if op == "max":
                return max(x, y)
            if op == "min":
                return min(x, y)
            if op == "mod":
                return math.sqrt(x * x + y * y + z * z)
            if op == "pin":
                return x if y < x else z if y > z else y
            if op == "sqrt":
                return math.sqrt(x) if x > 0 else 0.0
        except (OverflowError, ValueError, ZeroDivisionError):
            return 0.0
        return 0.0


def _fmt(value: float) -> str:
    if not math.isfinite(value):
        value = 0.0
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class _PathBuilder:
    def __init__(self, sx: float, sy: float) -> None:
        self.sx = sx
        self.sy = sy
        self.parts: list[str] = []
        self.x = 0.0
        self.y = 0.0
        self.start = (0.0, 0.0)

    def move(self, x: float, y: float) -> None:
        self.x, self.y = x, y
        self.start = (x, y)
        self.parts.append(f"M{_fmt(x * self.sx)} {_fmt(y * self.sy)}")

    def line(self, x: float, y: float) -> None:
        self.x, self.y = x, y
        self.parts.append(f"L{_fmt(x * self.sx)} {_fmt(y * self.sy)}")

    def quad(self, x1, y1, x, y) -> None:
        self.x, self.y = x, y
        s = self.sx, self.sy
        self.parts.append(f"Q{_fmt(x1 * s[0])} {_fmt(y1 * s[1])} {_fmt(x * s[0])} {_fmt(y * s[1])}")

    def cubic(self, x1, y1, x2, y2, x, y) -> None:
        self.x, self.y = x, y
        sx, sy = self.sx, self.sy
        self.parts.append(
            f"C{_fmt(x1 * sx)} {_fmt(y1 * sy)} {_fmt(x2 * sx)} {_fmt(y2 * sy)} {_fmt(x * sx)} {_fmt(y * sy)}"
        )

    def arc(self, wr: float, hr: float, st_ang: float, sw_ang: float) -> None:
        """DrawingML arcTo: angles are visual angles of an ellipse that
        passes through the current point."""
        if wr == 0 or hr == 0:
            return
        st = st_ang / _ANGLE * _DEG
        sw = sw_ang / _ANGLE * _DEG

        def param(theta: float) -> float:
            # visual angle -> parametric angle on the ellipse
            return math.atan2(wr * math.sin(theta), hr * math.cos(theta))

        t0 = param(st)
        cx = self.x - wr * math.cos(t0)
        cy = self.y - hr * math.sin(t0)
        # Split long sweeps so each SVG arc is unambiguous.
        segments = max(1, math.ceil(abs(sw) / (math.pi / 2) - 1e-9))
        for i in range(1, segments + 1):
            theta = st + sw * i / segments
            # keep parametric angles continuous with the visual sweep
            t = param(theta)
            turns = round(((st + sw * i / segments) - t) / (2 * math.pi))
            t += turns * 2 * math.pi
            x = cx + wr * math.cos(t)
            y = cy + hr * math.sin(t)
            seg = sw / segments
            large = 1 if abs(seg) > math.pi else 0
            sweep = 1 if seg > 0 else 0
            self.parts.append(
                f"A{_fmt(abs(wr) * self.sx)} {_fmt(abs(hr) * self.sy)} 0 {large} {sweep} "
                f"{_fmt(x * self.sx)} {_fmt(y * self.sy)}"
            )
            self.x, self.y = x, y

    def close(self) -> None:
        self.parts.append("Z")
        self.x, self.y = self.start

    def data(self) -> str:
        return "".join(self.parts)


def _build(guides: Guides, path_defs, w: float, h: float, sx: float, sy: float) -> list[GeomPath]:
    out: list[GeomPath] = []
    for p in path_defs:
        pw = p.get("w") or 0
        ph = p.get("h") or 0
        # Path coordinates are in the path's own w x h space when given.
        kx = (w / pw) if pw else 1.0
        ky = (h / ph) if ph else 1.0
        b = _PathBuilder(sx * kx, sy * ky)
        val = guides.val
        for cmd in p["c"]:
            op = cmd[0]
            if op == "M":
                b.move(val(cmd[1]), val(cmd[2]))
            elif op == "L":
                b.line(val(cmd[1]), val(cmd[2]))
            elif op == "A":
                b.arc(val(cmd[1]), val(cmd[2]), val(cmd[3]), val(cmd[4]))
            elif op == "Q":
                b.quad(val(cmd[1]), val(cmd[2]), val(cmd[3]), val(cmd[4]))
            elif op == "C":
                b.cubic(*(val(c) for c in cmd[1:7]))
            elif op == "Z":
                b.close()
        d = b.data()
        if d:
            out.append(GeomPath(d, p.get("fill", "norm"), p.get("stroke", True)))
    return out


def _text_rect(
    guides: Guides, rect, w: float, h: float
) -> tuple[float, float, float, float] | None:
    if not rect:
        return None
    l, t, r, b = (guides.val(v) for v in rect)
    if r < l:
        l, r = r, l
    if b < t:
        t, b = b, t
    return (
        max(0.0, l) / w if w else 0.0,
        max(0.0, t) / h if h else 0.0,
        r / w if w else 1.0,
        b / h if h else 1.0,
    )


def preset(
    name: str,
    w_emu: float,
    h_emu: float,
    adjustments: dict[str, str] | None = None,
    scale: float = 1.0,
) -> Geometry:
    """Evaluate preset ``name`` for a w x h EMU shape; paths are scaled by ``scale``."""
    definition = presets().get(name) or presets()["rect"]
    w = max(float(w_emu), 1.0)
    h = max(float(h_emu), 1.0)
    g = Guides(w, h)
    for gname, fmla in definition["av"]:
        override = (adjustments or {}).get(gname)
        g.define(gname, override if override else fmla)
    for gname, fmla in definition["gd"]:
        g.define(gname, fmla)
    paths = _build(g, definition["paths"], w, h, scale, scale)
    rect = _text_rect(g, definition.get("rect"), w, h)
    return Geometry(paths, rect)


def adjustments_from(av_lst) -> dict[str, str]:
    out: dict[str, str] = {}
    if av_lst is None:
        return out
    for gd in av_lst:
        if local(gd.tag) == "gd" and gd.get("name") and gd.get("fmla"):
            out[gd.get("name")] = gd.get("fmla")
    return out


def custom(cust_geom, w_emu: float, h_emu: float, scale: float = 1.0) -> Geometry:
    """Evaluate an ``a:custGeom`` element."""
    w = max(float(w_emu), 1.0)
    h = max(float(h_emu), 1.0)
    g = Guides(w, h)
    for container in ("a:avLst", "a:gdLst"):
        el = cust_geom.find(q(container))
        if el is None:
            continue
        for gd in el:
            if local(gd.tag) == "gd":
                g.define(gd.get("name", ""), gd.get("fmla", "val 0"))
    path_defs = []
    path_lst = cust_geom.find(q("a:pathLst"))
    for path in path_lst if path_lst is not None else []:
        cmds = []
        for cmd in path:
            name = local(cmd.tag)
            pts = [(pt.get("x"), pt.get("y")) for pt in cmd if local(pt.tag) == "pt"]
            if name == "moveTo" and pts:
                cmds.append(["M", *pts[0]])
            elif name == "lnTo" and pts:
                cmds.append(["L", *pts[0]])
            elif name == "arcTo":
                cmds.append(["A", cmd.get("wR"), cmd.get("hR"), cmd.get("stAng"), cmd.get("swAng")])
            elif name == "quadBezTo" and len(pts) >= 2:
                cmds.append(["Q", *pts[0], *pts[1]])
            elif name == "cubicBezTo" and len(pts) >= 3:
                cmds.append(["C", *pts[0], *pts[1], *pts[2]])
            elif name == "close":
                cmds.append(["Z"])
        entry = {"c": cmds}
        for key in ("w", "h"):
            try:
                if path.get(key):
                    entry[key] = int(path.get(key))
            except ValueError:
                pass
        if path.get("fill"):
            entry["fill"] = path.get("fill")
        if path.get("stroke") in ("0", "false"):
            entry["stroke"] = False
        path_defs.append(entry)
    paths = _build(g, path_defs, w, h, scale, scale)
    rect_el = cust_geom.find(q("a:rect"))
    rect = None
    if rect_el is not None:
        rect = _text_rect(
            g, [rect_el.get("l"), rect_el.get("t"), rect_el.get("r"), rect_el.get("b")], w, h
        )
    return Geometry(paths, rect)
