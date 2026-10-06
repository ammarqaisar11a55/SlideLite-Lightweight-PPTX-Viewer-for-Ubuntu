"""Windows metafiles (EMF and WMF) to SVG.

PowerPoint decks frequently embed clip art, equation previews and OLE
previews as metafiles, which browsers cannot display.  This converter
interprets the GDI drawing records that matter for display: shapes, paths,
pens, brushes, fonts and text, clipping rectangles and device-independent
bitmaps (re-wrapped as BMP data URIs, which WebKit decodes natively).
EMF+ records embedded in comments are skipped; their EMF fallback records
are rendered instead.

The input is untrusted: every read is bounds-checked, record and output
sizes are capped, and any malformed record simply ends the conversion with
whatever was drawn so far.
"""

from __future__ import annotations

import base64
import math
import struct
from dataclasses import dataclass, field, replace

from slidelite.presentation.fonts import family_stack, translate_symbol

MAX_RECORDS = 500_000
MAX_OUTPUT = 24 * 1024 * 1024
MAX_BITMAP = 64 * 1024 * 1024


class MetafileError(ValueError):
    pass


def _f(v: float) -> str:
    if not math.isfinite(v):
        return "0"
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _colorref(value: int) -> str:
    r, g, b = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass
class Pen:
    style: int = 0
    width: float = 1.0
    color: str = "#000000"

    @property
    def none(self) -> bool:
        return (self.style & 0x0F) == 5


@dataclass
class Brush:
    style: int = 0
    color: str = "#ffffff"
    pattern: str | None = None  # data URI for DIB pattern brushes

    @property
    def none(self) -> bool:
        return self.style == 1


@dataclass
class Font:
    height: float = -12.0
    weight: int = 400
    italic: bool = False
    underline: bool = False
    strike: bool = False
    escapement: float = 0.0
    face: str = "Arial"
    charset: int = 0


@dataclass
class DC:
    pen: Pen = field(default_factory=lambda: Pen(0, 0, "#000000"))
    brush: Brush = field(default_factory=Brush)
    font: Font = field(default_factory=Font)
    text_color: str = "#000000"
    bk_color: str = "#ffffff"
    bk_mode: int = 2
    text_align: int = 0
    poly_fill: int = 1  # ALTERNATE
    map_mode: int = 1
    win_org: tuple[float, float] = (0.0, 0.0)
    win_ext: tuple[float, float] = (1.0, 1.0)
    vp_org: tuple[float, float] = (0.0, 0.0)
    vp_ext: tuple[float, float] = (1.0, 1.0)
    world: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    pos: tuple[float, float] = (0.0, 0.0)
    clip: tuple[float, float, float, float] | None = None  # device rect


STOCK_BRUSHES = {
    0: Brush(0, "#ffffff"),
    1: Brush(0, "#c0c0c0"),
    2: Brush(0, "#808080"),
    3: Brush(0, "#404040"),
    4: Brush(0, "#000000"),
    5: Brush(1),
}
STOCK_PENS = {6: Pen(0, 0, "#ffffff"), 7: Pen(0, 0, "#000000"), 8: Pen(5)}


def _dib_to_data_uri(bmi: bytes, bits: bytes) -> tuple[str, int, int] | None:
    """Wrap a DIB (BITMAPINFO + bits) into a BMP data URI."""
    if len(bmi) < 16 or len(bmi) + len(bits) > MAX_BITMAP:
        return None
    header_size = struct.unpack_from("<I", bmi, 0)[0]
    if header_size == 12:  # BITMAPCOREHEADER
        w, h = struct.unpack_from("<HH", bmi, 4)
    else:
        w, h = struct.unpack_from("<ii", bmi, 4)
    if w == 0 or h == 0 or abs(w) > 30000 or abs(h) > 30000:
        return None
    file_header = b"BM" + struct.pack("<IHHI", 14 + len(bmi) + len(bits), 0, 0, 14 + len(bmi))
    data = base64.b64encode(file_header + bmi + bits).decode("ascii")
    return f"data:image/bmp;base64,{data}", abs(w), abs(h)


class _SVG:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.defs: list[str] = []
        self.clip_ids: dict[tuple, str] = {}
        self.current_clip: tuple | None = None
        self.group_open = False

    def add(self, markup: str) -> None:
        self.size += len(markup)
        if self.size > MAX_OUTPUT:
            raise MetafileError("metafile output too large")
        self.parts.append(markup)

    def set_clip(self, clip) -> None:
        if clip == self.current_clip:
            return
        if self.group_open:
            self.parts.append("</g>")
            self.group_open = False
        self.current_clip = clip
        if clip is not None:
            cid = self.clip_ids.get(clip)
            if cid is None:
                cid = f"mfc{len(self.clip_ids)}"
                self.clip_ids[clip] = cid
                x0, y0, x1, y1 = clip
                self.defs.append(
                    f'<clipPath id="{cid}"><rect x="{_f(min(x0, x1))}" y="{_f(min(y0, y1))}" '
                    f'width="{_f(abs(x1 - x0))}" height="{_f(abs(y1 - y0))}"/></clipPath>'
                )
            self.parts.append(f'<g clip-path="url(#{cid})">')
            self.group_open = True

    def finish(self, view: tuple[float, float, float, float], width: float, height: float) -> str:
        if self.group_open:
            self.parts.append("</g>")
        x, y, w, h = view
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{_f(width)}" height="{_f(height)}" viewBox="{_f(x)} {_f(y)} {_f(w)} {_f(h)}" '
            f'preserveAspectRatio="none" overflow="hidden">{defs}{"".join(self.parts)}</svg>'
        )


class _Renderer:
    """Shared GDI state machine for EMF and WMF."""

    def __init__(self) -> None:
        self.dc = DC()
        self.stack: list[DC] = []
        self.objects: dict[int, object] = {}
        self.svg = _SVG()
        self.path: list[str] | None = None
        self.path_parts: list[str] = []

    # -- coordinate mapping -------------------------------------------------------
    def tx(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.dc.world
        wx = a * x + c * y + e
        wy = b * x + d * y + f
        return self.to_device(wx, wy)

    def to_device(self, x: float, y: float) -> tuple[float, float]:
        dc = self.dc
        if dc.map_mode in (7, 8):  # MM_ISOTROPIC / MM_ANISOTROPIC
            sx = dc.vp_ext[0] / dc.win_ext[0] if dc.win_ext[0] else 1.0
            sy = dc.vp_ext[1] / dc.win_ext[1] if dc.win_ext[1] else 1.0
            if dc.map_mode == 7:
                s = min(abs(sx), abs(sy))
                sx, sy = math.copysign(s, sx), math.copysign(s, sy)
        else:
            sx = sy = 1.0
        return ((x - dc.win_org[0]) * sx + dc.vp_org[0], (y - dc.win_org[1]) * sy + dc.vp_org[1])

    def scale(self) -> float:
        x0, y0 = self.tx(0, 0)
        x1, y1 = self.tx(1, 0)
        x2, y2 = self.tx(0, 1)
        return (math.hypot(x1 - x0, y1 - y0) + math.hypot(x2 - x0, y2 - y0)) / 2 or 1.0

    # -- style ------------------------------------------------------------------------
    def stroke_attrs(self) -> str:
        pen = self.dc.pen
        if pen.none:
            return 'stroke="none"'
        width = max(pen.width * self.scale(), 0.0) if pen.width > 0 else 0.0
        if width == 0:
            # cosmetic pen: one device pixel
            return f'stroke="{pen.color}" stroke-width="1" vector-effect="non-scaling-stroke"'
        dash = ""
        style = pen.style & 0x0F
        if style in (1, 2, 3, 4):
            unit = max(width, 1.0)
            pattern = {1: (3, 1), 2: (1, 1), 3: (3, 1, 1, 1), 4: (3, 1, 1, 1, 1, 1)}[style]
            dash = f' stroke-dasharray="{" ".join(_f(p * unit) for p in pattern)}"'
        cap = {0: "round", 0x100: "square", 0x200: "butt"}.get(pen.style & 0xF00, "round")
        join = {0: "round", 0x1000: "bevel", 0x2000: "miter"}.get(pen.style & 0xF000, "round")
        return (
            f'stroke="{pen.color}" stroke-width="{_f(width)}" stroke-linecap="{cap}" '
            f'stroke-linejoin="{join}"{dash}'
        )

    def fill_attrs(self) -> str:
        brush = self.dc.brush
        rule = "evenodd" if self.dc.poly_fill == 1 else "nonzero"
        if brush.none:
            return 'fill="none"'
        if brush.pattern:
            pid = f"mfp{len(self.svg.defs)}"
            self.svg.defs.append(
                f'<pattern id="{pid}" patternUnits="userSpaceOnUse" width="8" height="8">'
                f'<image href="{brush.pattern}" width="8" height="8"/></pattern>'
            )
            return f'fill="url(#{pid})" fill-rule="{rule}"'
        return f'fill="{brush.color}" fill-rule="{rule}"'

    # -- drawing -------------------------------------------------------------------------
    def emit_path(self, d: str, fill: bool, stroke: bool) -> None:
        if not d:
            return
        if self.path is not None:
            self.path.append(d)
            return
        self.svg.set_clip(self.dc.clip)
        f = self.fill_attrs() if fill else 'fill="none"'
        s = self.stroke_attrs() if stroke else 'stroke="none"'
        if f == 'fill="none"' and s == 'stroke="none"':
            return
        self.svg.add(f'<path d="{d}" {f} {s}/>')

    def poly_d(self, points, close: bool) -> str:
        if not points:
            return ""
        pts = [self.tx(x, y) for x, y in points]
        d = f"M{_f(pts[0][0])} {_f(pts[0][1])}" + "".join(f"L{_f(x)} {_f(y)}" for x, y in pts[1:])
        return d + ("Z" if close else "")

    def bezier_d(self, points, start=None) -> str:
        pts = [self.tx(x, y) for x, y in points]
        d = ""
        if start is not None:
            sx, sy = self.tx(*start)
            d = f"M{_f(sx)} {_f(sy)}"
        elif pts:
            d = f"M{_f(pts[0][0])} {_f(pts[0][1])}"
            pts = pts[1:]
        for i in range(0, len(pts) - 2, 3):
            (x1, y1), (x2, y2), (x3, y3) = pts[i], pts[i + 1], pts[i + 2]
            d += f"C{_f(x1)} {_f(y1)} {_f(x2)} {_f(y2)} {_f(x3)} {_f(y3)}"
        return d

    def rect_d(self, l, t, r, b) -> str:
        return self.poly_d([(l, t), (r, t), (r, b), (l, b)], True)

    def ellipse_d(self, l, t, r, b) -> str:
        cx, cy = (l + r) / 2, (t + b) / 2
        rx, ry = abs(r - l) / 2, abs(b - t) / 2
        k = 0.5522847498
        pts = [
            (cx + rx, cy),
            (cx + rx, cy + ry * k),
            (cx + rx * k, cy + ry),
            (cx, cy + ry),
            (cx - rx * k, cy + ry),
            (cx - rx, cy + ry * k),
            (cx - rx, cy),
            (cx - rx, cy - ry * k),
            (cx - rx * k, cy - ry),
            (cx, cy - ry),
            (cx + rx * k, cy - ry),
            (cx + rx, cy - ry * k),
            (cx + rx, cy),
        ]
        return self.bezier_d(pts) + "Z"

    def round_rect_d(self, l, t, r, b, w, h) -> str:
        rx, ry = abs(w) / 2, abs(h) / 2
        if rx <= 0 or ry <= 0:
            return self.rect_d(l, t, r, b)
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        k = 0.5522847498
        pts = [(l + rx, t), (r - rx, t)]
        d = self.poly_d(pts, False)
        segs = [
            ((r - rx + rx * k, t), (r, t + ry - ry * k), (r, t + ry)),
            ((r, b - ry),),
            ((r, b - ry + ry * k), (r - rx + rx * k, b), (r - rx, b)),
            ((l + rx, b),),
            ((l + rx - rx * k, b), (l, b - ry + ry * k), (l, b - ry)),
            ((l, t + ry),),
            ((l, t + ry - ry * k), (l + rx - rx * k, t), (l + rx, t)),
        ]
        for seg in segs:
            pts = [self.tx(*p) for p in seg]
            if len(pts) == 1:
                d += f"L{_f(pts[0][0])} {_f(pts[0][1])}"
            else:
                d += "C" + " ".join(f"{_f(x)} {_f(y)}" for x, y in pts)
        return d + "Z"

    def arc_d(self, l, t, r, b, xs, ys, xe, ye, kind: str) -> str:
        cx, cy = (l + r) / 2, (t + b) / 2
        rx, ry = abs(r - l) / 2, abs(b - t) / 2
        if rx == 0 or ry == 0:
            return ""
        a0 = math.atan2((ys - cy) / ry, (xs - cx) / rx)
        a1 = math.atan2((ye - cy) / ry, (xe - cx) / rx)
        # GDI arcs run counter-clockwise (in a y-down space: decreasing angle)
        if a1 >= a0:
            a1 -= 2 * math.pi
        steps = max(4, int(abs(a1 - a0) / (math.pi / 16)))
        pts = [
            (
                cx + rx * math.cos(a0 + (a1 - a0) * i / steps),
                cy + ry * math.sin(a0 + (a1 - a0) * i / steps),
            )
            for i in range(steps + 1)
        ]
        if kind == "pie":
            pts = [(cx, cy), *pts]
        return self.poly_d(pts, kind in ("pie", "chord"))

    def text(self, x: float, y: float, text: str, dx: list[float] | None = None) -> None:
        text = text.replace("\x00", "")
        if not text.strip():
            return
        dc = self.dc
        font = dc.font
        if font.charset == 2 or font.face.lower() in (
            "symbol",
            "wingdings",
            "wingdings 2",
            "wingdings 3",
        ):
            text, _ = translate_symbol(text, font.face)
        px, py = self.tx(x, y)
        size = abs(font.height) * self.scale()
        if font.height > 0:
            size *= 0.85  # cell height -> character height
        size = max(size, 0.5)
        align = dc.text_align
        anchor = "middle" if (align & 6) == 6 else "end" if align & 2 else "start"
        if (align & 24) == 24:
            baseline = "alphabetic"
        elif align & 8:
            baseline = "text-after-edge"
            py -= size * 0.2
        else:
            baseline = "text-before-edge"
        attrs = [
            f'x="{_f(px)}"',
            f'y="{_f(py)}"',
            f'font-size="{_f(size)}"',
            f'font-family="{_esc(family_stack(font.face))}"',
            f'fill="{dc.text_color}"',
            f'text-anchor="{anchor}"',
            f'dominant-baseline="{baseline}"',
            'xml:space="preserve"',
        ]
        if font.weight >= 600:
            attrs.append('font-weight="700"')
        if font.italic:
            attrs.append('font-style="italic"')
        deco = [n for n, on in (("underline", font.underline), ("line-through", font.strike)) if on]
        if deco:
            attrs.append(f'text-decoration="{" ".join(deco)}"')
        if dx and len(dx) >= len(text) - 1 and anchor == "start" and len(text) > 1:
            xs, acc = [], 0.0
            s = self.scale()
            for i in range(len(text)):
                xs.append(px + acc)
                acc += (dx[i] if i < len(dx) else 0) * s
            attrs[0] = f'x="{" ".join(_f(v) for v in xs)}"'
        if font.escapement:
            attrs.append(f'transform="rotate({_f(-font.escapement / 10)} {_f(px)} {_f(py)})"')
        self.svg.set_clip(dc.clip)
        self.svg.add(f"<text {' '.join(attrs)}>{_esc(text)}</text>")

    def image(
        self,
        uri: str,
        x: float,
        y: float,
        w: float,
        h: float,
        src: tuple[float, float, float, float] | None = None,
        bmp_size=None,
    ) -> None:
        x0, y0 = self.tx(x, y)
        x1, y1 = self.tx(x + w, y + h)
        dx, dy = min(x0, x1), min(y0, y1)
        dw, dh = abs(x1 - x0), abs(y1 - y0)
        if dw <= 0 or dh <= 0:
            return
        flip = ""
        if (x1 < x0) or (y1 < y0):
            sx = -1 if x1 < x0 else 1
            sy = -1 if y1 < y0 else 1
            flip = f' transform="translate({_f(dx + (dw if sx < 0 else 0))} {_f(dy + (dh if sy < 0 else 0))}) scale({sx} {sy}) translate({_f(-dx)} {_f(-dy)})"'
        self.svg.set_clip(self.dc.clip)
        if (
            src
            and bmp_size
            and (src[2] != bmp_size[0] or src[3] != bmp_size[1] or src[0] or src[1])
        ):
            sx0, sy0, sw, sh = src
            if sw > 0 and sh > 0:
                self.svg.add(
                    f'<svg x="{_f(dx)}" y="{_f(dy)}" width="{_f(dw)}" height="{_f(dh)}" '
                    f'viewBox="{_f(sx0)} {_f(sy0)} {_f(sw)} {_f(sh)}" preserveAspectRatio="none"{flip}>'
                    f'<image href="{uri}" width="{bmp_size[0]}" height="{bmp_size[1]}"/></svg>'
                )
                return
        self.svg.add(
            f'<image href="{uri}" x="{_f(dx)}" y="{_f(dy)}" width="{_f(dw)}" height="{_f(dh)}" '
            f'preserveAspectRatio="none"{flip}/>'
        )

    # -- state ------------------------------------------------------------------------------
    def save(self) -> None:
        if len(self.stack) < 256:
            self.stack.append(replace(self.dc))

    def restore(self, n: int = -1) -> None:
        if not self.stack:
            return
        # negative: relative to the current state; positive: absolute index
        count = -n if n < 0 else len(self.stack) - n + 1
        for _ in range(min(count, len(self.stack))):
            self.dc = self.stack.pop()

    def select(self, obj) -> None:
        if isinstance(obj, Pen):
            self.dc.pen = obj
        elif isinstance(obj, Brush):
            self.dc.brush = obj
        elif isinstance(obj, Font):
            self.dc.font = obj

    def intersect_clip(self, l, t, r, b) -> None:
        x0, y0 = self.tx(l, t)
        x1, y1 = self.tx(r, b)
        rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if self.dc.clip is not None:
            c = self.dc.clip
            rect = (max(rect[0], c[0]), max(rect[1], c[1]), min(rect[2], c[2]), min(rect[3], c[3]))
        self.dc.clip = rect


# -- EMF ------------------------------------------------------------------------------------------


def emf_to_svg(data: bytes) -> str:
    if len(data) < 88 or struct.unpack_from("<I", data, 0)[0] != 1 or data[40:44] != b" EMF":
        raise MetafileError("not an EMF file")
    r = _Renderer()
    bl, bt, br, bb = struct.unpack_from("<4i", data, 8)
    fl, ft, fr, fb = struct.unpack_from("<4i", data, 24)
    dev_px = struct.unpack_from("<2i", data, 72)
    dev_mm = struct.unpack_from("<2i", data, 80)
    # Frame is in 0.01 mm; map it to device pixels for the viewBox.
    if dev_mm[0] > 0 and dev_mm[1] > 0 and fr > fl and fb > ft:
        ppmx, ppmy = dev_px[0] / dev_mm[0], dev_px[1] / dev_mm[1]
        view = (fl / 100 * ppmx, ft / 100 * ppmy, (fr - fl) / 100 * ppmx, (fb - ft) / 100 * ppmy)
    else:
        view = (bl, bt, max(1, br - bl), max(1, bb - bt))
    width_pt = (fr - fl) / 100 / 25.4 * 72 if fr > fl else view[2]
    height_pt = (fb - ft) / 100 / 25.4 * 72 if fb > ft else view[3]
    pos = 0
    count = 0
    try:
        while pos + 8 <= len(data) and count < MAX_RECORDS:
            rtype, size = struct.unpack_from("<II", data, pos)
            if size < 8 or pos + size > len(data):
                break
            rec = data[pos : pos + size]
            count += 1
            if rtype == 14:  # EOF
                break
            _emf_record(r, rtype, rec)
            pos += (size + 3) & ~3
    except (struct.error, IndexError, ValueError, ZeroDivisionError, OverflowError):
        pass
    return r.svg.finish(view, max(width_pt, 1), max(height_pt, 1))


def _points32(rec, off, n):
    return [struct.unpack_from("<2i", rec, off + 8 * i) for i in range(n)]


def _points16(rec, off, n):
    return [struct.unpack_from("<2h", rec, off + 4 * i) for i in range(n)]


def _emf_record(r: _Renderer, t: int, rec: bytes) -> None:  # noqa: C901 - a record dispatcher
    dc = r.dc
    if t in (2, 3, 4, 5, 6, 85, 86, 87, 88, 89):
        wide = t in (85, 86, 87, 88, 89)
        n = struct.unpack_from("<I", rec, 24)[0]
        n = min(n, (len(rec) - 28) // (4 if wide else 8))
        pts = _points16(rec, 28, n) if wide else _points32(rec, 28, n)
        kind = {
            2: "bez",
            85: "bez",
            3: "poly",
            86: "poly",
            4: "line",
            87: "line",
            5: "bezto",
            88: "bezto",
            6: "lineto",
            89: "lineto",
        }[t]
        if kind == "poly":
            r.emit_path(r.poly_d(pts, True), True, True)
        elif kind == "line":
            r.emit_path(r.poly_d(pts, False), False, True)
        elif kind == "bez":
            r.emit_path(r.bezier_d(pts), False, True)
        elif kind == "bezto":
            d = r.bezier_d(pts, start=dc.pos)
            if r.path is not None and r.path:
                d = d[d.index("C") :] if "C" in d else ""
            r.emit_path(d, False, True)
            if pts:
                dc.pos = pts[-1]
        else:
            d = r.poly_d([dc.pos, *pts], False)
            if r.path is not None and r.path:
                d = d[d.index("L") :] if "L" in d else ""
            r.emit_path(d, False, True)
            if pts:
                dc.pos = pts[-1]
    elif t in (7, 8, 90, 91):  # POLYPOLYLINE/POLYPOLYGON (16)
        wide = t in (90, 91)
        n_polys, total = struct.unpack_from("<II", rec, 24)
        n_polys = min(n_polys, 100000)
        counts = struct.unpack_from(f"<{n_polys}I", rec, 32)
        off = 32 + 4 * n_polys
        pts = _points16(rec, off, total) if wide else _points32(rec, off, total)
        d, i = "", 0
        for cnt in counts:
            d += r.poly_d(pts[i : i + cnt], t in (8, 91))
            i += cnt
        r.emit_path(d, t in (8, 91), True)
    elif t == 9:
        dc.win_ext = struct.unpack_from("<2i", rec, 8)
    elif t == 10:
        dc.win_org = struct.unpack_from("<2i", rec, 8)
    elif t == 11:
        dc.vp_ext = struct.unpack_from("<2i", rec, 8)
    elif t == 12:
        dc.vp_org = struct.unpack_from("<2i", rec, 8)
    elif t == 17:
        dc.map_mode = struct.unpack_from("<I", rec, 8)[0]
    elif t == 18:
        dc.bk_mode = struct.unpack_from("<I", rec, 8)[0]
    elif t == 19:
        dc.poly_fill = struct.unpack_from("<I", rec, 8)[0]
    elif t == 22:
        dc.text_align = struct.unpack_from("<I", rec, 8)[0]
    elif t == 24:
        dc.text_color = _colorref(struct.unpack_from("<I", rec, 8)[0])
    elif t == 25:
        dc.bk_color = _colorref(struct.unpack_from("<I", rec, 8)[0])
    elif t == 27:
        dc.pos = struct.unpack_from("<2i", rec, 8)
        if r.path is not None:
            x, y = r.tx(*dc.pos)
            r.path.append(f"M{_f(x)} {_f(y)}")
    elif t == 54:
        x, y = struct.unpack_from("<2i", rec, 8)
        if r.path is not None:
            px, py = r.tx(x, y)
            if not r.path:
                sx, sy = r.tx(*dc.pos)
                r.path.append(f"M{_f(sx)} {_f(sy)}")
            r.path.append(f"L{_f(px)} {_f(py)}")
        else:
            r.emit_path(r.poly_d([dc.pos, (x, y)], False), False, True)
        dc.pos = (x, y)
    elif t == 30:
        r.intersect_clip(*struct.unpack_from("<4i", rec, 8))
    elif t == 33:
        r.save()
    elif t == 34:
        r.restore(struct.unpack_from("<i", rec, 8)[0])
    elif t == 35:
        dc.world = struct.unpack_from("<6f", rec, 8)
    elif t == 36:
        m = struct.unpack_from("<6f", rec, 8)
        mode = struct.unpack_from("<I", rec, 32)[0]
        if mode == 1:
            dc.world = (1, 0, 0, 1, 0, 0)
        elif mode in (2, 3):
            a = dc.world
            first, second = (m, a) if mode == 2 else (a, m)
            dc.world = (
                first[0] * second[0] + first[1] * second[2],
                first[0] * second[1] + first[1] * second[3],
                first[2] * second[0] + first[3] * second[2],
                first[2] * second[1] + first[3] * second[3],
                first[4] * second[0] + first[5] * second[2] + second[4],
                first[4] * second[1] + first[5] * second[3] + second[5],
            )
        elif mode == 4:
            dc.world = m
    elif t == 37:
        index = struct.unpack_from("<I", rec, 8)[0]
        if index & 0x80000000:
            stock = index & 0x7FFFFFFF
            r.select(
                STOCK_BRUSHES.get(stock)
                or STOCK_PENS.get(stock)
                or (Font() if stock in (10, 11, 12, 13, 14, 16, 17) else None)
            )
        else:
            r.select(r.objects.get(index))
    elif t == 38:
        idx, style, wx, _wy, color = struct.unpack_from("<IIiiI", rec, 8)
        r.objects[idx] = Pen(style, float(wx), _colorref(color))
    elif t == 95:  # EXTCREATEPEN
        idx = struct.unpack_from("<I", rec, 8)[0]
        style, width, _bstyle, color = struct.unpack_from("<IIII", rec, 28)
        r.objects[idx] = Pen(style, float(width) if style & 0x10000 else 0.0, _colorref(color))
    elif t == 39:
        idx, style, color, _hatch = struct.unpack_from("<IIII", rec, 8)
        r.objects[idx] = Brush(1 if style == 1 else 0, _colorref(color))
    elif t == 94:  # CREATEDIBPATTERNBRUSHPT
        idx, _usage, off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<6I", rec, 8)
        dib = _dib_to_data_uri(rec[off_bmi : off_bmi + cb_bmi], rec[off_bits : off_bits + cb_bits])
        r.objects[idx] = Brush(0, "#808080", dib[0] if dib else None)
    elif t == 40:
        r.objects.pop(struct.unpack_from("<I", rec, 8)[0], None)
    elif t == 82:
        idx = struct.unpack_from("<I", rec, 8)[0]
        height, _width, esc, _orient, weight = struct.unpack_from("<5i", rec, 12)
        italic, underline, strike, charset = rec[32], rec[33], rec[34], rec[35]
        face = rec[40:104].decode("utf-16-le", errors="ignore").split("\x00")[0] or "Arial"
        r.objects[idx] = Font(
            float(height),
            weight,
            bool(italic),
            bool(underline),
            bool(strike),
            float(esc),
            face,
            charset,
        )
    elif t == 42:
        r.emit_path(r.ellipse_d(*struct.unpack_from("<4i", rec, 8)), True, True)
    elif t == 43:
        r.emit_path(r.rect_d(*struct.unpack_from("<4i", rec, 8)), True, True)
    elif t == 44:
        l, tt, rr, b = struct.unpack_from("<4i", rec, 8)
        w, h = struct.unpack_from("<2i", rec, 24)
        r.emit_path(r.round_rect_d(l, tt, rr, b, w, h), True, True)
    elif t in (45, 46, 47, 55):
        l, tt, rr, b, xs, ys, xe, ye = struct.unpack_from("<8i", rec, 8)
        kind = {45: "arc", 46: "chord", 47: "pie", 55: "arc"}[t]
        r.emit_path(r.arc_d(l, tt, rr, b, xs, ys, xe, ye, kind), kind != "arc", True)
    elif t == 59:
        r.path = []
    elif t == 60:
        r.path_parts = r.path or []
        r.path = None
    elif t == 61:
        if r.path is not None:
            r.path.append("Z")
    elif t in (62, 63, 64):
        d = "".join(r.path_parts)
        r.path_parts = []
        r.emit_path(d, t in (62, 63), t in (63, 64))
    elif t == 67:  # SELECTCLIPPATH: approximate with the path's bounds
        r.path_parts = []
    elif t in (83, 84):  # EXTTEXTOUTA / W
        ref_x, ref_y, n_chars, off_string, _options = struct.unpack_from("<iiIII", rec, 36)
        off_dx = struct.unpack_from("<I", rec, 72)[0]
        n_chars = min(n_chars, 10000)
        if t == 84:
            text = rec[off_string : off_string + 2 * n_chars].decode("utf-16-le", errors="replace")
        else:
            text = rec[off_string : off_string + n_chars].decode("cp1252", errors="replace")
        dx = None
        if off_dx and off_dx + 4 * n_chars <= len(rec):
            dx = list(struct.unpack_from(f"<{n_chars}i", rec, off_dx))
        if dc.text_align & 1:  # TA_UPDATECP
            ref_x, ref_y = dc.pos
        r.text(ref_x, ref_y, text, dx)
    elif t in (81, 80):  # STRETCHDIBITS / SETDIBITSTODEVICE
        if t == 81:
            x, y, xs, ys, cxs, cys, off_bmi, cb_bmi, off_bits, cb_bits, _usage, _rop, cx, cy = (
                struct.unpack_from("<6i4I2I2i", rec, 24)
            )
        else:
            x, y, xs, ys, cxs, cys, off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from(
                "<6i4I", rec, 24
            )
            cx, cy = cxs, cys
        dib = _dib_to_data_uri(rec[off_bmi : off_bmi + cb_bmi], rec[off_bits : off_bits + cb_bits])
        if dib:
            r.image(dib[0], x, y, cx, cy, (xs, ys, cxs, cys), dib[1:])
    elif t in (76, 77, 114, 116):  # BITBLT / STRETCHBLT / ALPHABLEND / TRANSPARENTBLT
        x, y, cx, cy = struct.unpack_from("<4i", rec, 24)
        if t == 76:
            _rop, xs, ys = struct.unpack_from("<Iii", rec, 40)
            off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<4I", rec, 84)
            cxs, cys = cx, cy
        elif t == 77:
            _rop, xs, ys = struct.unpack_from("<Iii", rec, 40)
            off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<4I", rec, 84)
            cxs, cys = struct.unpack_from("<2i", rec, 100)
        else:
            _rop, xs, ys = struct.unpack_from("<Iii", rec, 40)
            off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<4I", rec, 84)
            cxs, cys = struct.unpack_from("<2i", rec, 100)
        if cb_bmi == 0:
            _fill_rop(r, _rop, x, y, cx, cy)
        else:
            dib = _dib_to_data_uri(
                rec[off_bmi : off_bmi + cb_bmi], rec[off_bits : off_bits + cb_bits]
            )
            if dib:
                r.image(dib[0], x, y, cx, cy, (xs, ys, cxs, cys), dib[1:])


# -- WMF ------------------------------------------------------------------------------------------


def wmf_to_svg(data: bytes) -> str:
    r = _Renderer()
    pos = 0
    view = None
    inch = 1440
    if len(data) >= 22 and struct.unpack_from("<I", data, 0)[0] == 0x9AC6CDD7:
        l, t, rr, b, inch = struct.unpack_from("<4hH", data, 6)
        view = (min(l, rr), min(t, b), abs(rr - l) or 1, abs(b - t) or 1)
        pos = 22
    if pos + 18 > len(data):
        raise MetafileError("not a WMF file")
    header_words = struct.unpack_from("<H", data, pos + 2)[0]
    pos += header_words * 2
    r.dc.map_mode = 8
    win = {"org": None, "ext": None}
    count = 0
    try:
        while pos + 6 <= len(data) and count < MAX_RECORDS:
            size_words, func = struct.unpack_from("<IH", data, pos)
            size = size_words * 2
            if size < 6 or pos + size > len(data):
                break
            rec = data[pos + 6 : pos + size]
            count += 1
            if func == 0:
                break
            _wmf_record(r, func, rec, win)
            pos += size
    except (struct.error, IndexError, ValueError, ZeroDivisionError, OverflowError):
        pass
    if win["org"] is not None and win["ext"] is not None:
        ox, oy = win["org"]
        ex, ey = win["ext"]
        view = (min(ox, ox + ex), min(oy, oy + ey), abs(ex) or 1, abs(ey) or 1)
    if view is None:
        view = (0, 0, 1000, 1000)
    width_pt = view[2] / inch * 72 if inch else view[2]
    height_pt = view[3] / inch * 72 if inch else view[3]
    return r.svg.finish(view, max(width_pt, 1), max(height_pt, 1))


def _wmf_add_object(r: _Renderer, obj) -> None:
    index = 0
    while index in r.objects:
        index += 1
    r.objects[index] = obj


def _wmf_record(r: _Renderer, func: int, rec: bytes, win: dict) -> None:  # noqa: C901
    dc = r.dc
    # WMF output stays in logical units; the viewBox maps the window.
    dc.win_org, dc.win_ext, dc.vp_org, dc.vp_ext = (0, 0), (1, 1), (0, 0), (1, 1)
    if func == 0x020B:
        y, x = struct.unpack_from("<2h", rec, 0)
        win["org"] = (x, y)
    elif func == 0x020C:
        y, x = struct.unpack_from("<2h", rec, 0)
        win["ext"] = (x, y)
    elif func == 0x0102:
        dc.bk_mode = struct.unpack_from("<H", rec, 0)[0]
    elif func == 0x0106:
        dc.poly_fill = struct.unpack_from("<H", rec, 0)[0]
    elif func == 0x012E:
        dc.text_align = struct.unpack_from("<H", rec, 0)[0]
    elif func == 0x0209:
        dc.text_color = _colorref(struct.unpack_from("<I", rec, 0)[0])
    elif func == 0x0201:
        dc.bk_color = _colorref(struct.unpack_from("<I", rec, 0)[0])
    elif func == 0x0214:
        y, x = struct.unpack_from("<2h", rec, 0)
        dc.pos = (x, y)
    elif func == 0x0213:
        y, x = struct.unpack_from("<2h", rec, 0)
        r.emit_path(r.poly_d([dc.pos, (x, y)], False), False, True)
        dc.pos = (x, y)
    elif func in (0x0324, 0x0325):
        n = struct.unpack_from("<h", rec, 0)[0]
        n = max(0, min(n, (len(rec) - 2) // 4))
        pts = _points16(rec, 2, n)
        r.emit_path(r.poly_d(pts, func == 0x0324), func == 0x0324, True)
    elif func == 0x0538:
        n_polys = struct.unpack_from("<H", rec, 0)[0]
        counts = struct.unpack_from(f"<{n_polys}H", rec, 2)
        off = 2 + 2 * n_polys
        d = ""
        for cnt in counts:
            d += r.poly_d(_points16(rec, off, cnt), True)
            off += 4 * cnt
        r.emit_path(d, True, True)
    elif func == 0x041B:
        b, rr, t, l = struct.unpack_from("<4h", rec, 0)
        r.emit_path(r.rect_d(l, t, rr, b), True, True)
    elif func == 0x0418:
        b, rr, t, l = struct.unpack_from("<4h", rec, 0)
        r.emit_path(r.ellipse_d(l, t, rr, b), True, True)
    elif func == 0x061C:
        h, w, b, rr, t, l = struct.unpack_from("<6h", rec, 0)
        r.emit_path(r.round_rect_d(l, t, rr, b, w, h), True, True)
    elif func in (0x0817, 0x081A, 0x0830):
        ye, xe, ys, xs, b, rr, t, l = struct.unpack_from("<8h", rec, 0)
        kind = {0x0817: "arc", 0x081A: "pie", 0x0830: "chord"}[func]
        r.emit_path(r.arc_d(l, t, rr, b, xs, ys, xe, ye, kind), kind != "arc", True)
    elif func == 0x02FA:
        style, wx, _wy = struct.unpack_from("<Hhh", rec, 0)
        color = struct.unpack_from("<I", rec, 6)[0]
        _wmf_add_object(r, Pen(style, float(wx), _colorref(color)))
    elif func == 0x02FC:
        style = struct.unpack_from("<H", rec, 0)[0]
        color = struct.unpack_from("<I", rec, 2)[0]
        _wmf_add_object(r, Brush(1 if style == 1 else 0, _colorref(color)))
    elif func == 0x0142:  # DIBCREATEPATTERNBRUSH
        dib = rec[4:]
        header = struct.unpack_from("<I", dib, 0)[0] if len(dib) >= 4 else 0
        bmi_len = header + _palette_size(dib)
        uri = _dib_to_data_uri(dib[:bmi_len], dib[bmi_len:])
        _wmf_add_object(r, Brush(0, "#808080", uri[0] if uri else None))
    elif func == 0x02FB:
        height, _width, esc, _orient, weight = struct.unpack_from("<5h", rec, 0)
        italic, underline, strike, charset = rec[10], rec[11], rec[12], rec[13]
        face = rec[18:50].split(b"\x00")[0].decode("cp1252", errors="ignore") or "Arial"
        _wmf_add_object(
            r,
            Font(
                float(height),
                weight,
                bool(italic),
                bool(underline),
                bool(strike),
                float(esc),
                face,
                charset,
            ),
        )
    elif func in (0x00F7, 0x01F9, 0x06FF):  # palette / pattern brush / region: placeholders
        _wmf_add_object(r, None)
    elif func == 0x012D:
        r.select(r.objects.get(struct.unpack_from("<H", rec, 0)[0]))
    elif func == 0x01F0:
        r.objects.pop(struct.unpack_from("<H", rec, 0)[0], None)
    elif func == 0x001E:
        r.save()
    elif func == 0x0127:
        r.restore(struct.unpack_from("<h", rec, 0)[0])
    elif func == 0x0416:
        b, rr, t, l = struct.unpack_from("<4h", rec, 0)
        r.intersect_clip(l, t, rr, b)
    elif func == 0x0521:
        n = struct.unpack_from("<h", rec, 0)[0]
        raw = rec[2 : 2 + n]
        off = 2 + n + (n & 1)
        y, x = struct.unpack_from("<2h", rec, off)
        r.text(x, y, _decode_ansi(raw, dc.font))
    elif func == 0x0A32:
        y, x, n, options = struct.unpack_from("<hhhH", rec, 0)
        off = 8 + (8 if options & 0x0006 else 0)
        raw = rec[off : off + n]
        dx_off = off + n + (n & 1)
        dx = None
        if dx_off + 2 * n <= len(rec):
            dx = list(struct.unpack_from(f"<{n}h", rec, dx_off))
        if dc.text_align & 1:
            x, y = dc.pos
        r.text(x, y, _decode_ansi(raw, dc.font), dx)
    elif func in (0x0F43, 0x0B41, 0x0940):
        if func == 0x0F43:
            _rop, _usage, sh, sw, ys, xs, dh, dw, yd, xd = struct.unpack_from("<IH8h", rec, 0)
            dib = rec[22:]
        elif func == 0x0B41:
            _rop, sh, sw, ys, xs, dh, dw, yd, xd = struct.unpack_from("<I8h", rec, 0)
            dib = rec[20:]
        else:
            _rop, ys, xs, dh, dw, yd, xd = struct.unpack_from("<I6h", rec, 0)
            sh, sw = dh, dw
            dib = rec[16:]
        if len(dib) >= 40:
            header = struct.unpack_from("<I", dib, 0)[0]
            bmi_len = header + _palette_size(dib)
            uri = _dib_to_data_uri(dib[:bmi_len], dib[bmi_len:])
            if uri:
                r.image(uri[0], xd, yd, dw, dh, (xs, ys, sw, sh), uri[1:])
        else:
            _fill_rop(r, _rop, xd, yd, dw, dh)


_ROP_PATCOPY, _ROP_BLACKNESS, _ROP_WHITENESS = 0x00F00021, 0x00000042, 0x00FF0062


def _fill_rop(r: _Renderer, rop: int, x: float, y: float, w: float, h: float) -> None:
    """Bitmap-less blits: only pattern/constant raster operations paint;
    everything else (e.g. NOP 0x00AA0029) leaves the destination alone."""
    if rop == _ROP_PATCOPY:
        brush = r.dc.brush
    elif rop == _ROP_BLACKNESS:
        brush = Brush(0, "#000000")
    elif rop == _ROP_WHITENESS:
        brush = Brush(0, "#ffffff")
    else:
        return
    if brush.none:
        return
    saved_pen, saved_brush = r.dc.pen, r.dc.brush
    r.dc.pen, r.dc.brush = Pen(5), brush
    r.emit_path(r.rect_d(x, y, x + w, y + h), True, False)
    r.dc.pen, r.dc.brush = saved_pen, saved_brush


def _palette_size(dib: bytes) -> int:
    if len(dib) < 16:
        return 0
    header = struct.unpack_from("<I", dib, 0)[0]
    if header == 12:
        bits = struct.unpack_from("<H", dib, 10)[0]
        return 3 * (1 << bits) if bits <= 8 else 0
    bits = struct.unpack_from("<H", dib, 14)[0]
    compression = struct.unpack_from("<I", dib, 16)[0] if len(dib) >= 20 else 0
    used = struct.unpack_from("<I", dib, 32)[0] if len(dib) >= 36 else 0
    colors = used or ((1 << bits) if bits <= 8 else 0)
    extra = 12 if compression == 3 and header == 40 else 0
    return 4 * colors + extra


def _decode_ansi(raw: bytes, font: Font) -> str:
    codec = {
        128: "cp932",
        129: "cp949",
        134: "gbk",
        136: "cp950",
        161: "cp1253",
        162: "cp1254",
        177: "cp1255",
        178: "cp1256",
        186: "cp1257",
        204: "cp1251",
        238: "cp1250",
    }.get(font.charset, "cp1252")
    try:
        return raw.decode(codec, errors="replace")
    except LookupError:
        return raw.decode("cp1252", errors="replace")


def to_svg(data: bytes) -> str:
    """Convert an EMF or WMF blob to SVG markup (raises MetafileError)."""
    if len(data) >= 44 and data[40:44] == b" EMF":
        return emf_to_svg(data)
    return wmf_to_svg(data)
