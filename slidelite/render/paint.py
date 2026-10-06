"""Fills, lines and effects rendered as SVG paint servers and CSS filters."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from slidelite.presentation.color import Color
from slidelite.presentation.xmlsafe import attr_int, child, local, q
from slidelite.render.context import RenderContext, emu
from slidelite.render.markup import esc, num

FILL_TAGS = {"noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"}


def find_fill(el):
    """First fill child of ``el`` (spPr, bgPr, tcPr, rPr...)."""
    if el is None:
        return None
    for c in el:
        if local(c.tag) in FILL_TAGS:
            return c
    return None


@dataclass
class Paint:
    """Result of resolving a fill: SVG attribute value + opacity."""

    value: str = "none"  # 'none', '#rrggbb' or 'url(#id)'
    opacity: float = 1.0
    color: Color | None = None  # representative colour (text, fallbacks)

    @property
    def is_none(self) -> bool:
        return self.value == "none"

    def attrs(self, prefix: str = "fill") -> str:
        out = f'{prefix}="{self.value}"'
        if self.opacity < 0.999 and not self.is_none:
            out += f' {prefix}-opacity="{num(self.opacity, 3)}"'
        return out


NONE = Paint()


# -- fills ----------------------------------------------------------------------------


def resolve_fill(
    ctx: RenderContext,
    fill_el,
    defs: list[str],
    w: float,
    h: float,
    placeholder: Color | None = None,
) -> Paint:
    """Resolve a fill element into an SVG paint for a w x h (px) box."""
    if fill_el is None:
        return NONE
    colors = ctx.colors.with_placeholder(placeholder) if placeholder is not None else ctx.colors
    kind = local(fill_el.tag)
    if kind == "noFill":
        return NONE
    if kind == "solidFill":
        color = colors.resolve(fill_el)
        if color is None:
            return NONE
        return Paint(color.rgb_hex(), color.a, color)
    if kind == "grpFill":
        if ctx.group_fill is not None and local(ctx.group_fill.tag) != "grpFill":
            return resolve_fill(ctx, ctx.group_fill, defs, w, h, placeholder)
        return NONE
    if kind == "gradFill":
        return _gradient(ctx, colors, fill_el, defs, w, h)
    if kind == "pattFill":
        return _pattern(ctx, colors, fill_el, defs)
    if kind == "blipFill":
        return _blip_paint(ctx, fill_el, defs, w, h)
    return NONE


def gradient_stops(colors, grad) -> list[tuple[float, Color]]:
    stops = []
    gs_lst = grad.find(q("a:gsLst"))
    for gs in gs_lst if gs_lst is not None else []:
        color = colors.resolve(gs)
        if color is None:
            continue
        pos = (attr_int(gs, "pos", 0) or 0) / 100000.0
        stops.append((max(0.0, min(1.0, pos)), color))
    stops.sort(key=lambda s: s[0])
    return stops


def _stops_markup(stops) -> str:
    return "".join(
        f'<stop offset="{num(pos, 4)}" stop-color="{c.rgb_hex()}"'
        + (f' stop-opacity="{num(c.a, 3)}"' if c.a < 0.999 else "")
        + "/>"
        for pos, c in stops
    )


def _gradient(ctx, colors, grad, defs, w, h) -> Paint:
    stops = gradient_stops(colors, grad)
    if not stops:
        return NONE
    if len(stops) == 1:
        c = stops[0][1]
        return Paint(c.rgb_hex(), c.a, c)
    gid = ctx.ids("g")
    mid = stops[len(stops) // 2][1]
    path = grad.find(q("a:path"))
    if path is not None:
        rect = path.find(q("a:fillToRect"))
        l, t, r, b = ((attr_int(rect, k, 0) or 0) / 100000.0 for k in ("l", "t", "r", "b"))
        cx = (l + 1 - r) / 2
        cy = (t + 1 - b) / 2
        radius = max(math.hypot(cx - x, cy - y) for x in (0, 1) for y in (0, 1))
        defs.append(
            f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{num(cx, 4)}" '
            f'cy="{num(cy, 4)}" r="{num(radius, 4)}" gradientTransform="scale({num(w)} {num(h)})">'
            f"{_stops_markup(stops)}</radialGradient>"
        )
        return Paint(f"url(#{gid})", 1.0, mid)
    lin = grad.find(q("a:lin"))
    angle = (attr_int(lin, "ang", 0) or 0) / 60000.0 if lin is not None else 90.0
    scaled = lin is not None and lin.get("scaled") in ("1", "true")
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)
    if scaled:
        bw, bh, transform = 1.0, 1.0, f' gradientTransform="scale({num(w)} {num(h)})"'
    else:
        bw, bh, transform = w, h, ""
    half = (abs(dx) * bw + abs(dy) * bh) / 2
    cx, cy = bw / 2, bh / 2
    digits = 4 if scaled else 2
    defs.append(
        f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
        f'x1="{num(cx - dx * half, digits)}" y1="{num(cy - dy * half, digits)}" '
        f'x2="{num(cx + dx * half, digits)}" y2="{num(cy + dy * half, digits)}"{transform}>'
        f"{_stops_markup(stops)}</linearGradient>"
    )
    return Paint(f"url(#{gid})", 1.0, mid)


def css_gradient(colors, grad) -> str | None:
    """CSS linear/radial gradient for text fills and HTML backgrounds."""
    stops = gradient_stops(colors, grad)
    if len(stops) < 2:
        return None
    parts = ", ".join(f"{c.css()} {num(pos * 100)}%" for pos, c in stops)
    if grad.find(q("a:path")) is not None:
        return f"radial-gradient(circle, {parts})"
    lin = grad.find(q("a:lin"))
    angle = (attr_int(lin, "ang", 0) or 0) / 60000.0 if lin is not None else 90.0
    return f"linear-gradient({num(angle + 90)}deg, {parts})"


# 8x8 bitmaps (rows, '#' = foreground) approximating PowerPoint's patterns.
_PATTERNS = {
    "horz": ["########", "........", "........", "........"] * 2,
    "vert": ["#.......", "#.......", "#.......", "#......."] * 2,
    "ltHorz": ["########", "........", "........", "........"] * 2,
    "ltVert": ["#...#...", "#...#...", "#...#...", "#...#..."] * 2,
    "dkHorz": ["########", "########", "........", "........"] * 2,
    "dkVert": ["##..##..", "##..##..", "##..##..", "##..##.."] * 2,
    "narHorz": ["########", "........"] * 4,
    "narVert": ["#.#.#.#."] * 8,
    "dashHorz": [
        "####....",
        "........",
        "........",
        "........",
        "....####",
        "........",
        "........",
        "........",
    ],
    "dashVert": [
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "....#...",
        "....#...",
        "....#...",
        "....#...",
    ],
    "cross": [
        "########",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
    ],
    "smGrid": ["########", "#...#...", "#...#...", "#...#..."] * 2,
    "lgGrid": [
        "########",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
        "#.......",
    ],
    "dotGrid": [
        "#.#.#.#.",
        "........",
        "#.......",
        "........",
        "#.......",
        "........",
        "#.......",
        "........",
    ],
    "dnDiag": ["#...#...", ".#...#..", "..#...#.", "...#...#"] * 2,
    "upDiag": ["...#...#", "..#...#.", ".#...#..", "#...#..."] * 2,
    "ltDnDiag": ["#...#...", ".#...#..", "..#...#.", "...#...#"] * 2,
    "ltUpDiag": ["...#...#", "..#...#.", ".#...#..", "#...#..."] * 2,
    "dkDnDiag": ["##..##..", ".##..##.", "..##..##", "#..##..#"] * 2,
    "dkUpDiag": ["..##..##", ".##..##.", "##..##..", "#..##..#"] * 2,
    "wdDnDiag": [
        "###.....",
        ".###....",
        "..###...",
        "...###..",
        "....###.",
        ".....###",
        "#.....##",
        "##.....#",
    ],
    "wdUpDiag": [
        ".....###",
        "....###.",
        "...###..",
        "..###...",
        ".###....",
        "###.....",
        "##.....#",
        "#.....##",
    ],
    "dashDnDiag": [
        "#.......",
        ".#......",
        "..#.....",
        "...#....",
        "....#...",
        ".....#..",
        "........",
        "........",
    ],
    "dashUpDiag": [
        ".......#",
        "......#.",
        ".....#..",
        "....#...",
        "...#....",
        "..#.....",
        "........",
        "........",
    ],
    "diagCross": [
        "#.....#.",
        ".#...#..",
        "..#.#...",
        "...#....",
        "..#.#...",
        ".#...#..",
        "#.....#.",
        "........",
    ],
    "smCheck": ["#..##..#", ".##..##.", ".##..##.", "#..##..#"] * 2,
    "lgCheck": [
        "####....",
        "####....",
        "####....",
        "####....",
        "....####",
        "....####",
        "....####",
        "....####",
    ],
    "smConfetti": [
        "#.......",
        "....#...",
        ".#......",
        "......#.",
        "...#....",
        ".......#",
        "..#.....",
        ".....#..",
    ],
    "lgConfetti": [
        "##....#.",
        "##......",
        "....##..",
        "....##..",
        ".#.....#",
        "......##",
        "..##....",
        "..##....",
    ],
    "horzBrick": [
        "########",
        "#.......",
        "#.......",
        "#.......",
        "########",
        "....#...",
        "....#...",
        "....#...",
    ],
    "diagBrick": [
        "#.......",
        ".#......",
        "..#.....",
        "...#....",
        "....##..",
        "...#..#.",
        "..#....#",
        ".#......",
    ],
    "solidDmnd": [
        "...#....",
        "..###...",
        ".#####..",
        "#######.",
        ".#####..",
        "..###...",
        "...#....",
        "........",
    ],
    "openDmnd": [
        "...#....",
        "..#.#...",
        ".#...#..",
        "#.....#.",
        ".#...#..",
        "..#.#...",
        "...#....",
        "........",
    ],
    "dotDmnd": [
        "#.......",
        "........",
        "..#.#...",
        "........",
        "#.......",
        "........",
        "..#.#...",
        "........",
    ],
    "plaid": [
        "#.#.#.#.",
        ".#.#.#.#",
        "#.#.#.#.",
        ".#.#.#.#",
        "####....",
        "####....",
        "####....",
        "####....",
    ],
    "sphere": [
        "..####..",
        ".#....#.",
        "#......#",
        "#..##..#",
        "#..##..#",
        "#......#",
        ".#....#.",
        "..####..",
    ],
    "weave": [
        "#...#...",
        ".#.#.#..",
        "..#...#.",
        ".#.#.#.#",
        "#...#...",
        "...#.#.#",
        "..#...#.",
        ".#.#...#",
    ],
    "divot": [
        "........",
        "...#....",
        "....#...",
        "...#....",
        "........",
        "#.......",
        ".#......",
        "#.......",
    ],
    "shingle": [
        "#......#",
        ".#....#.",
        "..####..",
        "........",
        "....#...",
        "...#.#..",
        "##.....#",
        "........",
    ],
    "wave": [
        "........",
        "...##...",
        "..#..#.#",
        "##....#.",
        "........",
        "...##...",
        "..#..#.#",
        "##....#.",
    ],
    "trellis": ["####.##.", ".##.####", "####.##.", ".##.####"] * 2,
    "zigZag": [
        "#......#",
        ".#....#.",
        "..#..#..",
        "...##...",
        "#......#",
        ".#....#.",
        "..#..#..",
        "...##...",
    ],
}
_BAYER = [
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
]


def _pattern_bits(prst: str) -> list[str]:
    if prst in _PATTERNS:
        return _PATTERNS[prst]
    if prst.startswith("pct"):
        try:
            density = int(prst[3:]) / 100.0
        except ValueError:
            density = 0.5
        threshold = density * 64
        return [
            "".join("#" if _BAYER[y][x] < threshold else "." for x in range(8)) for y in range(8)
        ]
    return _PATTERNS["smGrid"]


def _pattern(ctx, colors, patt, defs) -> Paint:
    fg = colors.resolve(patt.find(q("a:fgClr"))) or Color(0, 0, 0)
    bg = colors.resolve(patt.find(q("a:bgClr"))) or Color(1, 1, 1)
    cell = 0.75  # one bitmap pixel at 96 dpi, in pt
    pid = ctx.ids("p")
    rects = []
    for y, row in enumerate(_pattern_bits(patt.get("prst", "pct50"))):
        for x, bit in enumerate(row[:8]):
            if bit == "#":
                rects.append(
                    f'<rect x="{num(x * cell)}" y="{num(y * cell)}" width="{cell}" height="{cell}"/>'
                )
    size = num(cell * 8)
    defs.append(
        f'<pattern id="{pid}" patternUnits="userSpaceOnUse" width="{size}" height="{size}">'
        f'<rect width="{size}" height="{size}" fill="{bg.rgb_hex()}" fill-opacity="{num(bg.a, 3)}"/>'
        f'<g fill="{fg.rgb_hex()}" fill-opacity="{num(fg.a, 3)}" shape-rendering="crispEdges">{"".join(rects)}</g>'
        f"</pattern>"
    )
    return Paint(f"url(#{pid})", 1.0, fg)


@dataclass
class BlipInfo:
    url: str | None
    crop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # l, t, r, b fractions
    opacity: float = 1.0
    tile: object = None
    filters: list[str] = field(default_factory=list)


def blip_info(ctx: RenderContext, blip_fill) -> BlipInfo:
    blip = blip_fill.find(q("a:blip"))
    info = BlipInfo(ctx.image_url(blip))
    src = blip_fill.find(q("a:srcRect"))
    if src is not None:
        info.crop = tuple((attr_int(src, k, 0) or 0) / 100000.0 for k in ("l", "t", "r", "b"))
    info.tile = blip_fill.find(q("a:tile"))
    if blip is not None:
        for eff in blip:
            name = local(eff.tag)
            if name == "alphaModFix":
                info.opacity *= (attr_int(eff, "amt", 100000) or 0) / 100000.0
            elif name in ("grayscl", "duotone", "lum", "biLevel", "clrChange", "alphaMod"):
                info.filters.append(name)
    return info


def image_filter(ctx: RenderContext, blip_fill, defs: list[str]) -> str | None:
    """SVG filter reproducing blip colour effects (grayscale, duotone, brightness)."""
    blip = blip_fill.find(q("a:blip"))
    if blip is None:
        return None
    primitives: list[str] = []
    for eff in blip:
        name = local(eff.tag)
        if name == "grayscl":
            primitives.append('<feColorMatrix type="saturate" values="0"/>')
        elif name == "biLevel":
            thresh = (attr_int(eff, "thresh", 50000) or 50000) / 100000.0
            primitives.append('<feColorMatrix type="saturate" values="0"/>')
            # A steep linear ramp around the threshold approximates a step.
            funcs = "".join(
                f'<feFunc{c} type="linear" slope="255" intercept="{num(-255 * thresh, 2)}"/>'
                for c in "RGB"
            )
            primitives.append(f"<feComponentTransfer>{funcs}</feComponentTransfer>")
        elif name == "lum":
            bright = (attr_int(eff, "bright", 0) or 0) / 100000.0
            contrast = (attr_int(eff, "contrast", 0) or 0) / 100000.0
            slope = 1 / max(0.01, 1 - contrast) if contrast > 0 else 1 + contrast
            intercept = bright + (1 - slope) / 2
            funcs = "".join(
                f'<feFunc{c} type="linear" slope="{num(slope, 3)}" intercept="{num(intercept, 3)}"/>'
                for c in "RGB"
            )
            primitives.append(f"<feComponentTransfer>{funcs}</feComponentTransfer>")
        elif name == "duotone":
            cols = [
                ctx.colors.resolve(c)
                for c in eff
                if local(c.tag)
                in ("srgbClr", "schemeClr", "prstClr", "sysClr", "scrgbClr", "hslClr")
            ]
            cols = [c for c in cols if c is not None]
            if len(cols) >= 2:
                a, b = cols[0], cols[1]
                primitives.append('<feColorMatrix type="saturate" values="0"/>')
                primitives.append(
                    "<feComponentTransfer>"
                    f'<feFuncR type="table" tableValues="{num(a.r, 3)} {num(b.r, 3)}"/>'
                    f'<feFuncG type="table" tableValues="{num(a.g, 3)} {num(b.g, 3)}"/>'
                    f'<feFuncB type="table" tableValues="{num(a.b, 3)} {num(b.b, 3)}"/>'
                    "</feComponentTransfer>"
                )
    if not primitives:
        return None
    fid = ctx.ids("f")
    defs.append(
        f'<filter id="{fid}" color-interpolation-filters="sRGB">{"".join(primitives)}</filter>'
    )
    return fid


def _blip_paint(ctx, blip_fill, defs, w, h) -> Paint:
    info = blip_info(ctx, blip_fill)
    if not info.url:
        return Paint("#d9d9d9", 1.0, Color(0.85, 0.85, 0.85))
    pid = ctx.ids("i")
    filt = image_filter(ctx, blip_fill, defs)
    fattr = f' filter="url(#{filt})"' if filt else ""
    if info.tile is not None:
        sx = (attr_int(info.tile, "sx", 100000) or 100000) / 100000.0
        sy = (attr_int(info.tile, "sy", 100000) or 100000) / 100000.0
        tw, th = _image_size_pt(ctx, blip_fill)
        tw, th = max(1.0, tw * abs(sx)), max(1.0, th * abs(sy))
        tx, ty = emu(info.tile.get("tx", 0)), emu(info.tile.get("ty", 0))
        defs.append(
            f'<pattern id="{pid}" patternUnits="userSpaceOnUse" x="{num(tx)}" y="{num(ty)}" '
            f'width="{num(tw)}" height="{num(th)}"><image href="{esc(info.url)}" width="{num(tw)}" '
            f'height="{num(th)}" preserveAspectRatio="none"{fattr}/></pattern>'
        )
    else:
        l, t, r, b = info.crop
        iw = w / max(0.0001, 1 - l - r)
        ih = h / max(0.0001, 1 - t - b)
        defs.append(
            f'<pattern id="{pid}" patternUnits="userSpaceOnUse" width="{num(w)}" height="{num(h)}">'
            f'<image href="{esc(info.url)}" x="{num(-l * iw)}" y="{num(-t * ih)}" width="{num(iw)}" '
            f'height="{num(ih)}" preserveAspectRatio="none"{fattr}/></pattern>'
        )
    return Paint(f"url(#{pid})", info.opacity, None)


def _image_size_pt(ctx: RenderContext, blip_fill) -> tuple[float, float]:
    from slidelite.render.images import image_size

    partname = (
        ctx.related_part(
            blip_fill.find(q("a:blip")).get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
            )
        )
        if blip_fill.find(q("a:blip")) is not None
        else None
    )
    if partname is None:
        return 72.0, 72.0
    try:
        size = image_size(ctx.deck.package.read(partname))
    except Exception:
        size = None
    if not size:
        return 72.0, 72.0
    wpx, hpx, dpi_x, dpi_y = size
    return wpx * 72.0 / (dpi_x or 96.0), hpx * 72.0 / (dpi_y or 96.0)


# -- lines ------------------------------------------------------------------------------


@dataclass
class LineStyle:
    paint: Paint = field(default_factory=Paint)
    width: float = 0.75
    dash: str | None = None
    cap: str = "butt"
    join: str = "round"
    head: tuple[str, str, str] | None = None  # type, w, len
    tail: tuple[str, str, str] | None = None

    @property
    def visible(self) -> bool:
        return not self.paint.is_none and self.width > 0


_DASHES = {
    "solid": None,
    "dot": (1, 3),
    "dash": (4, 3),
    "lgDash": (8, 3),
    "dashDot": (4, 3, 1, 3),
    "lgDashDot": (8, 3, 1, 3),
    "lgDashDotDot": (8, 3, 1, 3, 1, 3),
    "sysDash": (3, 1),
    "sysDot": (1, 1),
    "sysDashDot": (3, 1, 1, 1),
    "sysDashDotDot": (3, 1, 1, 1, 1, 1),
}
_LINE_FILL_TAGS = {"noFill", "solidFill", "gradFill", "pattFill"}
_JOIN_TAGS = {"round", "bevel", "miter"}


def merge_lines(*lns) -> dict:
    """Merge ``a:ln`` elements from lowest to highest priority."""
    merged: dict = {}
    for ln in lns:
        if ln is None:
            continue
        for key in ("w", "cap", "cmpd", "algn"):
            if ln.get(key) is not None:
                merged[key] = ln.get(key)
        for c in ln:
            name = local(c.tag)
            if name in _LINE_FILL_TAGS:
                merged["fill"] = c
            elif name in ("prstDash", "custDash"):
                merged["dash"] = c
            elif name in _JOIN_TAGS:
                merged["join"] = name
            elif name in ("headEnd", "tailEnd"):
                merged[name] = c
    return merged


def resolve_line(
    ctx: RenderContext,
    merged: dict,
    defs: list[str],
    w: float,
    h: float,
    placeholder: Color | None = None,
) -> LineStyle:
    style = LineStyle()
    fill = merged.get("fill")
    if fill is None:
        style.paint = NONE
        return style
    style.paint = resolve_fill(ctx, fill, defs, w, h, placeholder)
    try:
        width_emu = float(merged.get("w", 9525))
    except ValueError:
        width_emu = 9525.0
    style.width = max(emu(width_emu), 0.5) if width_emu > 0 else 0.5
    style.cap = {"rnd": "round", "sq": "square", "flat": "butt"}.get(
        merged.get("cap", "flat"), "butt"
    )
    style.join = merged.get("join", "round")
    dash = merged.get("dash")
    if dash is not None:
        if local(dash.tag) == "prstDash":
            pattern = _DASHES.get(dash.get("val", "solid"))
            if pattern:
                # round caps extend dashes; shorten them so dots stay dots
                adjust = style.width if style.cap != "butt" else 0.0
                values = []
                for i, v in enumerate(pattern):
                    length = v * style.width
                    if i % 2 == 0:
                        length = max(0.01, length - adjust)
                    else:
                        length += adjust
                    values.append(num(length))
                style.dash = " ".join(values)
        else:
            values = []
            for ds in dash:
                d = (attr_int(ds, "d", 100000) or 0) / 100000.0 * style.width
                sp = (attr_int(ds, "sp", 100000) or 0) / 100000.0 * style.width
                values += [num(max(d, 0.01)), num(max(sp, 0.01))]
            style.dash = " ".join(values) or None
    for end in ("headEnd", "tailEnd"):
        el = merged.get(end)
        if el is not None and el.get("type", "none") != "none":
            value = (el.get("type"), el.get("w", "med"), el.get("len", "med"))
            if end == "headEnd":
                style.head = value
            else:
                style.tail = value
    return style


_END_SCALE = {"sm": 2.0, "med": 3.0, "lg": 5.0}


def marker_defs(ctx: RenderContext, line: LineStyle, defs: list[str]) -> str:
    """SVG marker attributes for arrow heads/tails."""
    attrs = ""
    for which, spec in (("start", line.head), ("end", line.tail)):
        if spec is None:
            continue
        kind, wsz, lsz = spec
        mw = _END_SCALE.get(wsz, 3.0)
        ml = _END_SCALE.get(lsz, 3.0)
        mid = ctx.ids("m")
        color = (
            line.paint.value
            if line.paint.value.startswith("#")
            else (line.paint.color.rgb_hex() if line.paint.color else "#000000")
        )
        opacity = (
            f' fill-opacity="{num(line.paint.opacity, 3)}"' if line.paint.opacity < 0.999 else ""
        )
        # Marker drawn pointing along +x; tip at (ml, mw/2).
        if kind == "triangle":
            shape = f'<path d="M0 0L{num(ml)} {num(mw / 2)}L0 {num(mw)}Z" fill="{color}"{opacity}/>'
            ref = ml - 0.5
        elif kind == "stealth":
            shape = (
                f'<path d="M0 0L{num(ml)} {num(mw / 2)}L0 {num(mw)}L{num(ml * 0.3)} {num(mw / 2)}Z" '
                f'fill="{color}"{opacity}/>'
            )
            ref = ml - 0.5
        elif kind == "diamond":
            shape = (
                f'<path d="M0 {num(mw / 2)}L{num(ml / 2)} 0L{num(ml)} {num(mw / 2)}L{num(ml / 2)} {num(mw)}Z" '
                f'fill="{color}"{opacity}/>'
            )
            ref = ml / 2
        elif kind == "oval":
            shape = (
                f'<ellipse cx="{num(ml / 2)}" cy="{num(mw / 2)}" rx="{num(ml / 2)}" ry="{num(mw / 2)}" '
                f'fill="{color}"{opacity}/>'
            )
            ref = ml / 2
        else:  # "arrow": open chevron
            shape = (
                f'<path d="M0.3 0.3L{num(ml - 0.3)} {num(mw / 2)}L0.3 {num(mw - 0.3)}" fill="none" '
                f'stroke="{color}" stroke-width="0.6" stroke-linecap="round" stroke-linejoin="round"/>'
            )
            ref = ml - 0.4
        orient = "auto-start-reverse" if which == "start" else "auto"
        defs.append(
            f'<marker id="{mid}" markerUnits="strokeWidth" markerWidth="{num(ml)}" markerHeight="{num(mw)}" '
            f'viewBox="0 0 {num(ml)} {num(mw)}" refX="{num(ref)}" refY="{num(mw / 2)}" orient="{orient}" '
            f'overflow="visible">{shape}</marker>'
        )
        attrs += f' marker-{which}="url(#{mid})"'
    return attrs


def line_attrs(line: LineStyle) -> str:
    if not line.visible:
        return 'stroke="none"'
    out = f'{line.paint.attrs("stroke")} stroke-width="{num(line.width)}"'
    out += f' stroke-linecap="{line.cap}" stroke-linejoin="{line.join}"'
    if line.join == "miter":
        out += ' stroke-miterlimit="8"'
    if line.dash:
        out += f' stroke-dasharray="{line.dash}"'
    return out


# -- effects ------------------------------------------------------------------------------


def effect_css(ctx: RenderContext, effect_lst, placeholder: Color | None = None) -> dict[str, str]:
    """CSS for an ``a:effectLst``: filter (shadow/glow/blur) and box reflection."""
    if effect_lst is None:
        return {}
    colors = ctx.colors.with_placeholder(placeholder) if placeholder is not None else ctx.colors
    filters: list[str] = []
    css: dict[str, str] = {}
    for eff in effect_lst:
        name = local(eff.tag)
        if name in ("outerShdw", "prstShdw"):
            color = colors.resolve(eff) or Color(0, 0, 0, 0.35)
            dist = emu(eff.get("dist", 0))
            angle = math.radians((attr_int(eff, "dir", 0) or 0) / 60000.0)
            blur = emu(eff.get("blurRad", 0))
            dx, dy = dist * math.cos(angle), dist * math.sin(angle)
            filters.append(f"drop-shadow({num(dx)}px {num(dy)}px {num(blur / 2)}px {color.css()})")
        elif name == "glow":
            color = colors.resolve(eff) or Color(1, 1, 0, 0.6)
            rad = emu(eff.get("rad", 0))
            if rad > 0:
                filters.append(f"drop-shadow(0 0 {num(rad / 2)}px {color.css()})")
                filters.append(f"drop-shadow(0 0 {num(rad / 4)}px {color.css()})")
        elif name == "blur":
            rad = emu(eff.get("rad", 0))
            if rad > 0:
                filters.append(f"blur({num(rad / 2)}px)")
        elif name == "reflection":
            st = (attr_int(eff, "stA", 50000) or 0) / 100000.0
            end = (attr_int(eff, "endA", 0) or 0) / 100000.0
            end_pos = (attr_int(eff, "endPos", 100000) or 100000) / 100000.0
            dist = emu(eff.get("dist", 0))
            css["-webkit-box-reflect"] = (
                f"below {num(dist)}px linear-gradient(to bottom, rgba(0,0,0,{num(end, 3)}) 0%, "
                f"rgba(0,0,0,{num(end, 3)}) {num((1 - end_pos) * 100)}%, rgba(0,0,0,{num(st, 3)}) 100%)"
            )
    if filters:
        css["filter"] = " ".join(filters)
    return css


def text_shadow_css(ctx: RenderContext, effect_lst) -> str | None:
    if effect_lst is None:
        return None
    shadows = []
    for eff in effect_lst:
        if local(eff.tag) == "outerShdw":
            color = ctx.colors.resolve(eff) or Color(0, 0, 0, 0.35)
            dist = emu(eff.get("dist", 0))
            angle = math.radians((attr_int(eff, "dir", 0) or 0) / 60000.0)
            blur = emu(eff.get("blurRad", 0))
            shadows.append(
                f"{num(dist * math.cos(angle))}px {num(dist * math.sin(angle))}px {num(blur / 2)}px {color.css()}"
            )
        elif local(eff.tag) == "glow":
            color = ctx.colors.resolve(eff) or Color(1, 1, 0, 0.6)
            shadows.append(f"0 0 {num(emu(eff.get('rad', 0)) / 2)}px {color.css()}")
    return ", ".join(shadows) or None


def style_ref_color(ctx: RenderContext, ref) -> Color | None:
    """Colour carried by a style-matrix reference (lnRef/fillRef/effectRef/fontRef)."""
    if ref is None:
        return None
    return ctx.colors.resolve(ref)


def theme_fill(ctx: RenderContext, ref):
    """(fill element, phClr colour) for an ``a:fillRef`` or ``p:bgRef``."""
    if ref is None:
        return None, None
    idx = attr_int(ref, "idx", 0) or 0
    color = style_ref_color(ctx, ref)
    theme = ctx.theme
    if idx >= 1001:
        styles = theme.bg_fill_styles
        idx -= 1001
    elif idx >= 1:
        styles = theme.fill_styles
        idx -= 1
    else:
        return None, color
    if 0 <= idx < len(styles):
        return styles[idx], color
    if color is not None:
        return _synthetic_solid(), color
    return None, color


def _synthetic_solid():
    import xml.etree.ElementTree as ET

    solid = ET.Element(q("a:solidFill"))
    ET.SubElement(solid, q("a:schemeClr"), {"val": "phClr"})
    return solid


def theme_line(ctx: RenderContext, ref):
    if ref is None:
        return None, None
    idx = attr_int(ref, "idx", 0) or 0
    color = style_ref_color(ctx, ref)
    styles = ctx.theme.line_styles
    if 1 <= idx <= len(styles):
        return styles[idx - 1], color
    return None, color


def theme_effect(ctx: RenderContext, ref):
    if ref is None:
        return None, None
    idx = attr_int(ref, "idx", 0) or 0
    color = style_ref_color(ctx, ref)
    styles = ctx.theme.effect_styles
    if 1 <= idx <= len(styles):
        return child(styles[idx - 1], "a:effectLst"), color
    return None, color
