"""Charts (DrawingML chart parts) rendered to SVG.

Supported: bar/column (clustered, stacked, 100% stacked), line, area, pie,
doughnut, scatter, bubble (as scatter), radar and combinations of these on
primary/secondary axes.  3-D variants are drawn as their 2-D equivalents.
Anything that cannot be rendered falls back to a neutral placeholder rather
than breaking the slide.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from slidelite.presentation import fonts
from slidelite.presentation.color import Color
from slidelite.presentation.xmlsafe import attr_int, local, q, rid
from slidelite.render import paint
from slidelite.render.context import RenderContext, emu
from slidelite.render.markup import esc, num

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"


def c(name: str) -> str:
    return C + name


def cval(el, name: str, default=None):
    child = el.find(c(name)) if el is not None else None
    if child is None:
        return default
    return child.get("val", default)


def cbool(el, name: str, default: bool = False) -> bool:
    value = cval(el, name)
    if value is None:
        # <c:x/> without val means true
        if el is not None and el.find(c(name)) is not None:
            return True
        return default
    return value in ("1", "true")


# -- data model ---------------------------------------------------------------------


@dataclass
class Series:
    index: int
    order: int
    name: str
    categories: list[str]
    values: list[float | None]
    x_values: list[float | None] = field(default_factory=list)
    sizes: list[float | None] = field(default_factory=list)
    sp_pr: object = None
    points: dict[int, object] = field(default_factory=dict)  # idx -> spPr
    explosion: dict[int, float] = field(default_factory=dict)
    marker: str | None = None
    marker_size: float = 5
    marker_sp_pr: object = None
    smooth: bool = False
    labels: object = None
    format_code: str = "General"


@dataclass
class Plot:
    kind: str
    el: object
    series: list[Series]
    bar_dir: str = "col"
    grouping: str = "clustered"
    vary_colors: bool = False
    gap: float = 150
    overlap: float = 0
    hole: float = 50
    first_slice: float = 0
    ax_ids: list[str] = field(default_factory=list)
    labels: object = None


def _cache_points(ref) -> tuple[list, str]:
    """Values from a strRef/numRef/strLit/numLit (using the cached values)."""
    if ref is None:
        return [], "General"
    cache = None
    for tag in ("numRef", "strRef", "multiLvlStrRef"):
        r = ref.find(c(tag))
        if r is not None:
            cache = next((k for k in r if local(k.tag).endswith("Cache")), None)
            break
    if cache is None:
        for tag in ("numLit", "strLit"):
            lit = ref.find(c(tag))
            if lit is not None:
                cache = lit
    if cache is None:
        return [], "General"
    if local(cache.tag) == "multiLvlStrCache":
        lvl = cache.find(c("lvl"))
        cache_pts = lvl if lvl is not None else cache
        count = attr_int(cache.find(c("ptCount")), "val", 0) or 0
    else:
        cache_pts = cache
        count = attr_int(cache.find(c("ptCount")), "val", 0) or 0
    fmt_el = cache.find(c("formatCode"))
    format_code = fmt_el.text if fmt_el is not None and fmt_el.text else "General"
    pts = cache_pts.findall(c("pt"))
    count = max(count, max((attr_int(p, "idx", 0) or 0) + 1 for p in pts) if pts else 0)
    count = min(count, 100000)
    values: list = [None] * count
    for p in pts:
        idx = attr_int(p, "idx", 0) or 0
        v = p.find(c("v"))
        if 0 <= idx < count and v is not None:
            values[idx] = v.text or ""
    return values, format_code


def _numbers(values: list) -> list[float | None]:
    out: list[float | None] = []
    for v in values:
        try:
            f = float(v) if v not in (None, "") else None
            out.append(f if f is None or math.isfinite(f) else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _text_of(tx) -> str:
    if tx is None:
        return ""
    ref_values, _ = _cache_points(tx)
    if ref_values:
        return " ".join(str(v) for v in ref_values if v)
    v = tx.find(c("v"))
    if v is not None and v.text:
        return v.text
    rich = tx.find(c("rich"))
    if rich is not None:
        return "\n".join(
            "".join(t.text or "" for t in p.iter(q("a:t"))) for p in rich.findall(q("a:p"))
        )
    return ""


def parse_series(el, plot_kind: str) -> Series:
    cats, _ = _cache_points(el.find(c("cat")))
    vals_raw, fmt = _cache_points(el.find(c("val")))
    s = Series(
        index=attr_int(el.find(c("idx")), "val", 0) or 0,
        order=attr_int(el.find(c("order")), "val", 0) or 0,
        name=_text_of(el.find(c("tx"))),
        categories=[str(x) if x is not None else "" for x in cats],
        values=_numbers(vals_raw),
        sp_pr=el.find(c("spPr")),
        format_code=fmt,
        labels=el.find(c("dLbls")),
        smooth=cbool(el, "smooth"),
    )
    if plot_kind in ("scatter", "bubble"):
        xs_raw, _ = _cache_points(el.find(c("xVal")))
        ys_raw, fmt = _cache_points(el.find(c("yVal")))
        s.x_values = _numbers(xs_raw)
        s.values = _numbers(ys_raw)
        s.format_code = fmt
        if not s.categories:
            s.categories = [str(x) if x is not None else "" for x in xs_raw]
        sizes, _ = _cache_points(el.find(c("bubbleSize")))
        s.sizes = _numbers(sizes)
    for dpt in el.findall(c("dPt")):
        idx = attr_int(dpt.find(c("idx")), "val", 0) or 0
        if dpt.find(c("spPr")) is not None:
            s.points[idx] = dpt.find(c("spPr"))
        exp = attr_int(dpt.find(c("explosion")), "val", 0)
        if exp:
            s.explosion[idx] = exp / 100.0
    exp = attr_int(el.find(c("explosion")), "val", 0)
    if exp:
        for i in range(len(s.values)):
            s.explosion.setdefault(i, exp / 100.0)
    marker = el.find(c("marker"))
    if marker is not None:
        s.marker = cval(marker, "symbol", "auto")
        s.marker_size = attr_int(marker.find(c("size")), "val", 5) or 5
        s.marker_sp_pr = marker.find(c("spPr"))
    return s


_PLOT_KINDS = {
    "barChart": "bar",
    "bar3DChart": "bar",
    "lineChart": "line",
    "line3DChart": "line",
    "stockChart": "line",
    "areaChart": "area",
    "area3DChart": "area",
    "pieChart": "pie",
    "pie3DChart": "pie",
    "ofPieChart": "pie",
    "doughnutChart": "doughnut",
    "scatterChart": "scatter",
    "bubbleChart": "bubble",
    "radarChart": "radar",
    "surfaceChart": "area",
    "surface3DChart": "area",
}


def parse_plots(plot_area) -> list[Plot]:
    plots = []
    for el in plot_area:
        kind = _PLOT_KINDS.get(local(el.tag))
        if kind is None:
            continue
        series = sorted(
            (parse_series(s, kind) for s in el.findall(c("ser"))), key=lambda s: s.order
        )
        plot = Plot(kind, el, series)
        plot.bar_dir = cval(el, "barDir", "col")
        plot.grouping = cval(el, "grouping", "clustered" if kind == "bar" else "standard")
        plot.vary_colors = cbool(el, "varyColors", kind in ("pie", "doughnut"))
        plot.gap = float(cval(el, "gapWidth", "150") or 150)
        plot.overlap = float(
            cval(el, "overlap", "100" if plot.grouping in ("stacked", "percentStacked") else "0")
            or 0
        )
        plot.hole = float(cval(el, "holeSize", "50") or 50)
        plot.first_slice = float(cval(el, "firstSliceAng", "0") or 0)
        plot.ax_ids = [a.get("val", "") for a in el.findall(c("axId"))]
        plot.labels = el.find(c("dLbls"))
        plots.append(plot)
    return plots


# -- number formatting -----------------------------------------------------------------


def format_number(value: float, code: str | None) -> str:
    code = (code or "General").split(";")[0]
    code = re.sub(r"\[[^\]]*\]", "", code)  # colours, locales, conditions
    if code.strip().lower() in ("general", "@", ""):
        if abs(value) >= 1e15 or (value and abs(value) < 1e-4):
            return f"{value:.3g}"
        text = f"{value:.10g}"
        return text
    percent = "%" in code
    if percent:
        value *= 100
    match = re.search(r"[0#?][0#?,]*(\.[0#?]+)?", code)
    if not match:
        return f"{value:g}"
    number_part = match.group(0)
    decimals = len(match.group(1)) - 1 if match.group(1) else 0
    grouping = "," in number_part.split(".")[0]
    # Office rounds half away from zero (Python's format() rounds half to even).
    rounded = Decimal(str(abs(value))).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
    )
    formatted = f"{rounded:,.{decimals}f}" if grouping else f"{rounded:.{decimals}f}"
    prefix = code[: match.start()]
    suffix = code[match.end() :]

    def literal(s: str) -> str:
        s = re.sub(r'"([^"]*)"', r"\1", s)
        s = s.replace("\\", "").replace("_)", "").replace("_(", "").replace("*", "")
        return re.sub(r"_.", "", s)

    sign = "-" if value < 0 and round(abs(value), decimals) != 0 else ""
    return f"{sign}{literal(prefix)}{formatted}{literal(suffix)}".strip()


def nice_scale(
    lo: float,
    hi: float,
    target: int = 6,
    force_zero: bool = True,
    fixed_min: float | None = None,
    fixed_max: float | None = None,
    major: float | None = None,
) -> tuple[float, float, float]:
    if fixed_min is not None:
        lo = fixed_min
    elif force_zero and lo > 0 and lo < hi * 5 / 6:
        lo = 0.0
    if fixed_max is not None:
        hi = fixed_max
    elif force_zero and hi < 0 and fixed_min is None:
        hi = 0.0
    if hi <= lo:
        hi = lo + (abs(lo) or 1.0)
    span = hi - lo
    if major is None or major <= 0:
        raw = span / max(1, target)
        mag = 10 ** math.floor(math.log10(raw))
        for m in (1, 2, 2.5, 5, 10):
            if raw <= m * mag:
                major = m * mag
                break
    if fixed_min is None:
        lo = math.floor(lo / major + 1e-9) * major
    if fixed_max is None:
        hi = math.ceil(hi / major - 1e-9) * major
        if hi <= lo:
            hi = lo + major
    return lo, hi, major


# -- rendering helpers -----------------------------------------------------------------


def _accent_cycle(ctx: RenderContext, i: int) -> Color:
    base = ctx.colors.scheme_color(f"accent{i % 6 + 1}")
    round_ = i // 6
    if round_ == 0:
        return base
    # Later series use darker/lighter variants of the accents, like Office.
    factor = [0.6, 1.4, 0.8, 1.2][(round_ - 1) % 4]
    return base.scaled_lum(factor)


@dataclass
class SeriesStyle:
    fill: paint.Paint
    line: paint.LineStyle


def series_style(ctx, sp_pr, defs, w, h, fallback: Color, kind: str) -> SeriesStyle:
    fill_el = paint.find_fill(sp_pr)
    if fill_el is not None:
        fill = paint.resolve_fill(ctx, fill_el, defs, w, h)
    elif kind in ("line", "scatter", "radar"):
        fill = paint.NONE
    else:
        fill = paint.Paint(fallback.rgb_hex(), fallback.a, fallback)
    ln = sp_pr.find(q("a:ln")) if sp_pr is not None else None
    merged = paint.merge_lines(ln)
    if kind in ("line", "scatter", "radar") and "fill" not in merged:
        import xml.etree.ElementTree as ET

        solid = ET.Element(q("a:solidFill"))
        ET.SubElement(solid, q("a:srgbClr"), {"val": fallback.rgb_hex()[1:]})
        merged["fill"] = solid
        merged.setdefault("w", "28575")
        merged.setdefault("cap", "rnd")
    line = paint.resolve_line(ctx, merged, defs, w, h)
    if kind in ("pie", "doughnut") and "fill" not in merged:
        line = paint.LineStyle(paint.Paint("#ffffff"), 0.75)
    return SeriesStyle(fill, line)


class TextStyle:
    def __init__(
        self, ctx: RenderContext, base_size: float, color: Color, family: str, bold: bool = False
    ):
        self.ctx = ctx
        self.size = base_size
        self.color = color
        self.family = family
        self.bold = bold

    def derive(self, tx_pr, scale_default: float | None = None) -> TextStyle:
        out = TextStyle(self.ctx, scale_default or self.size, self.color, self.family, self.bold)
        if tx_pr is None:
            return out
        rpr = None
        for p in tx_pr.iter(q("a:defRPr")):
            rpr = p
            break
        if rpr is None:
            for p in tx_pr.iter(q("a:rPr")):
                rpr = p
                break
        if rpr is not None:
            if rpr.get("sz"):
                try:
                    out.size = int(rpr.get("sz")) / 100.0
                except ValueError:
                    pass
            if rpr.get("b") in ("1", "true"):
                out.bold = True
            elif rpr.get("b") in ("0", "false"):
                out.bold = False
            fill = paint.find_fill(rpr)
            if fill is not None and local(fill.tag) == "solidFill":
                out.color = self.ctx.colors.resolve(fill) or out.color
            latin = rpr.find(q("a:latin"))
            if latin is not None and latin.get("typeface"):
                out.family = fonts.family_stack(
                    self.ctx.theme.resolve_typeface(latin.get("typeface"))
                )
        return out

    def attrs(self, anchor: str = "middle") -> str:
        weight = ' font-weight="700"' if self.bold else ""
        return (
            f'font-family="{esc(self.family)}" font-size="{num(self.size)}" fill="{self.color.rgb_hex()}"'
            f'{weight} text-anchor="{anchor}"'
        )

    def width(self, text: str) -> float:
        return len(text) * self.size * 0.52


def _text(
    x, y, text, style: TextStyle, anchor="middle", baseline="central", rotate: float = 0
) -> str:
    transform = f' transform="rotate({num(rotate)} {num(x)} {num(y)})"' if rotate else ""
    return (
        f'<text x="{num(x)}" y="{num(y)}" {style.attrs(anchor)} dominant-baseline="{baseline}"'
        f"{transform}>{esc(text)}</text>"
    )


# -- main entry ------------------------------------------------------------------------------


def render_chart_frame(ctx: RenderContext, graphic_data, w: float, h: float) -> str:
    chart_ref = graphic_data.find(c("chart"))
    partname = ctx.related_part(rid(chart_ref, "id")) if chart_ref is not None else None
    if partname is None:
        return _placeholder(w, h, "Chart data is missing")
    try:
        root = ctx.deck.parse(partname)
        from slidelite.render.diagram import PartView

        chart_ctx = ctx.for_part(PartView(ctx.deck, partname, root, ctx.part))
        return ChartRenderer(chart_ctx, root, w, h).render()
    except Exception as exc:  # never break the slide because of a chart
        ctx.warn(f"chart {partname}: {type(exc).__name__}: {exc}")
        return _placeholder(w, h, "This chart could not be displayed")


def _placeholder(w: float, h: float, message: str) -> str:
    return (
        f'<svg width="{num(w)}" height="{num(h)}"><rect width="{num(w)}" height="{num(h)}" fill="#f2f2f2" '
        f'stroke="#bfbfbf" stroke-dasharray="4 3"/><text x="{num(w / 2)}" y="{num(h / 2)}" '
        f'font-family="sans-serif" font-size="{num(max(8, min(14, w / 30)))}" fill="#7f7f7f" '
        f'text-anchor="middle" dominant-baseline="central">{esc(message)}</text></svg>'
    )


class ChartRenderer:
    def __init__(self, ctx: RenderContext, root, w: float, h: float) -> None:
        self.ctx = ctx
        self.root = root
        self.w = w
        self.h = h
        self.defs: list[str] = []
        self.out: list[str] = []
        self.chart = root.find(c("chart"))
        family = fonts.family_stack(ctx.theme.fonts.minor.get("latin"))
        gray = ctx.colors.scheme_color("tx1").scaled_lum(1.0)
        base = TextStyle(ctx, 10.0, Color(0.35, 0.35, 0.35) if gray.r < 0.5 else gray, family)
        self.text = base.derive(root.find(c("txPr")))

    def render(self) -> str:
        chart = self.chart
        if chart is None:
            return _placeholder(self.w, self.h, "Empty chart")
        plot_area = chart.find(c("plotArea"))
        plots = parse_plots(plot_area) if plot_area is not None else []
        if not plots or not any(p.series for p in plots):
            return _placeholder(self.w, self.h, "Chart type not supported")
        w, h = self.w, self.h
        # chart area background/border
        sp_pr = self.root.find(c("spPr"))
        bg_fill = (
            paint.resolve_fill(self.ctx, paint.find_fill(sp_pr), self.defs, w, h)
            if sp_pr is not None
            else paint.NONE
        )
        bg_line = paint.resolve_line(
            self.ctx,
            paint.merge_lines(sp_pr.find(q("a:ln")) if sp_pr is not None else None),
            self.defs,
            w,
            h,
        )
        self.out.append(
            f'<rect width="{num(w)}" height="{num(h)}" {bg_fill.attrs()} {paint.line_attrs(bg_line)}/>'
        )

        pad = max(4.0, min(w, h) * 0.03)
        top, bottom, left, right = pad, h - pad, pad, w - pad
        # title
        title_el = chart.find(c("title"))
        auto_deleted = cbool(chart, "autoTitleDeleted")
        title_text = None
        if title_el is not None:
            title_text = _text_of(title_el.find(c("tx"))) or (
                plots[0].series[0].name if len(plots[0].series) == 1 else "Chart Title"
            )
        elif (
            not auto_deleted
            and len(plots) == 1
            and len(plots[0].series) == 1
            and plots[0].kind in ("pie", "doughnut")
        ):
            title_text = plots[0].series[0].name
        if title_text:
            tstyle = self.text.derive(None, self.text.size * 1.4)
            tstyle.bold = False
            if title_el is not None:
                tstyle = tstyle.derive(
                    title_el.find(c("tx"))
                    if title_el.find(c("tx")) is not None
                    else title_el.find(c("txPr")),
                    tstyle.size,
                )
                if title_el.find(c("txPr")) is not None:
                    tstyle = tstyle.derive(title_el.find(c("txPr")))
            lines = title_text.split("\n")
            for i, line in enumerate(lines):
                self.out.append(_text(w / 2, top + tstyle.size * (0.6 + i * 1.2), line, tstyle))
            top += tstyle.size * (1.2 * len(lines) + 0.4)

        # legend
        legend = chart.find(c("legend"))
        entries = self.legend_entries(plots) if legend is not None else []
        if entries:
            pos = cval(legend, "legendPos", "r")
            lstyle = self.text.derive(legend.find(c("txPr")))
            overlay = cbool(legend, "overlay")
            box = self.draw_legend(entries, pos, lstyle, (left, top, right, bottom))
            if not overlay:
                if pos == "b":
                    bottom = box[1] - pad / 2
                elif pos == "t":
                    top = box[3] + pad / 2
                elif pos == "l":
                    left = box[2] + pad / 2
                else:
                    right = box[0] - pad / 2

        # plot area (manual layout wins)
        layout = plot_area.find(c("layout"))
        manual = layout.find(c("manualLayout")) if layout is not None else None
        inner = None
        if manual is not None and manual.find(c("x")) is not None:
            try:
                fx = float(cval(manual, "x", "0"))
                fy = float(cval(manual, "y", "0"))
                fw = float(cval(manual, "w", "1"))
                fh = float(cval(manual, "h", "1"))
                inner = (fx * w, fy * h, (fx + fw) * w, (fy + fh) * h)
                inner_mode = cval(manual, "layoutTarget", "outer") == "inner"
            except ValueError:
                inner = None
        area = (left, top, right, bottom)
        kinds = {p.kind for p in plots}
        if kinds <= {"pie", "doughnut"}:
            target = inner or area
            for plot in plots:
                self.draw_pie(plot, target)
        elif kinds & {"radar"}:
            self.draw_radar(plots[0], inner or area)
        else:
            self.draw_axes_chart(
                plots, plot_area, area, inner, inner is not None and inner_mode if inner else False
            )
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return f'<svg class="chart-svg" width="{num(w)}" height="{num(h)}" overflow="hidden">{defs}{"".join(self.out)}</svg>'

    # -- legend --------------------------------------------------------------------------
    def legend_entries(self, plots):
        entries = []
        for plot in plots:
            if plot.kind in ("pie", "doughnut") or (
                plot.vary_colors and len(plot.series) == 1 and plot.kind != "line"
            ):
                s = plot.series[0]
                for i, cat in enumerate(s.categories or [str(i + 1) for i in range(len(s.values))]):
                    style = series_style(
                        self.ctx,
                        s.points.get(i, s.sp_pr) if i in s.points else None,
                        self.defs,
                        10,
                        10,
                        _accent_cycle(self.ctx, i),
                        plot.kind,
                    )
                    entries.append((cat, style, plot.kind))
            else:
                for s in plot.series:
                    style = series_style(
                        self.ctx,
                        s.sp_pr,
                        self.defs,
                        10,
                        10,
                        _accent_cycle(self.ctx, s.index),
                        plot.kind,
                    )
                    entries.append((s.name or f"Series {s.index + 1}", style, plot.kind))
        return entries

    def draw_legend(self, entries, pos, style: TextStyle, area):
        left, top, right, bottom = area
        sw = style.size * 0.8
        row_h = style.size * 1.5
        if pos in ("b", "t"):
            widths = [sw + 4 + style.width(name) + style.size for name, _, _ in entries]
            total = sum(widths)
            max_w = right - left
            rows = [[]]
            acc = 0.0
            for e, wd in zip(entries, widths, strict=False):
                if acc + wd > max_w and rows[-1]:
                    rows.append([])
                    acc = 0.0
                rows[-1].append((e, wd))
                acc += wd
            height = row_h * len(rows)
            y0 = bottom - height if pos == "b" else top
            for r, row in enumerate(rows):
                rw = sum(wd for _, wd in row)
                x = left + (max_w - rw) / 2
                y = y0 + r * row_h + row_h / 2
                for (name, st, kind), wd in row:
                    self._legend_key(x, y, sw, st, kind)
                    self.out.append(_text(x + sw + 4, y, name, style, "start"))
                    x += wd
            del total
            return (left, y0, right, y0 + height)
        width = min(
            (right - left) * 0.4, max(sw + 4 + style.width(name) for name, _, _ in entries) + 6
        )
        height = row_h * len(entries)
        x0 = right - width if pos in ("r", "tr") else left
        y0 = top + max(0, (bottom - top - height) / 2) if pos in ("r", "l") else top
        for i, (name, st, kind) in enumerate(entries):
            y = y0 + i * row_h + row_h / 2
            self._legend_key(x0, y, sw, st, kind)
            max_chars = max(3, int((width - sw - 6) / (style.size * 0.52)))
            label = name if len(name) <= max_chars else name[: max_chars - 1] + "…"
            self.out.append(_text(x0 + sw + 4, y, label, style, "start"))
        return (x0, y0, x0 + width, y0 + height)

    def _legend_key(self, x, y, size, style: SeriesStyle, kind):
        if kind in ("line", "scatter", "radar") and style.fill.is_none:
            color = style.line.paint.value if style.line.visible else "#888"
            self.out.append(
                f'<line x1="{num(x)}" y1="{num(y)}" x2="{num(x + size)}" y2="{num(y)}" stroke="{color}" '
                f'stroke-width="{num(min(3, max(1, style.line.width)))}"/>'
            )
        else:
            self.out.append(
                f'<rect x="{num(x)}" y="{num(y - size / 2)}" width="{num(size)}" height="{num(size)}" '
                f"{style.fill.attrs()}/>"
            )

    # -- pie / doughnut -------------------------------------------------------------------
    def draw_pie(self, plot: Plot, area):
        left, top, right, bottom = area
        s = plot.series[0]
        values = [max(0.0, v or 0.0) for v in s.values]
        total = sum(values)
        if total <= 0:
            return
        cx, cy = (left + right) / 2, (top + bottom) / 2
        radius = max(1.0, min(right - left, bottom - top) / 2 * 0.92)
        hole = radius * (plot.hole / 100.0) if plot.kind == "doughnut" else 0.0
        angle = math.radians(plot.first_slice) - math.pi / 2
        labels = s.labels if s.labels is not None else plot.labels
        lstyle = self.text.derive(labels.find(c("txPr")) if labels is not None else None)
        for i, v in enumerate(values):
            if v <= 0:
                continue
            sweep = v / total * 2 * math.pi
            mid = angle + sweep / 2
            offset = s.explosion.get(i, 0.0) * radius
            ox, oy = cx + math.cos(mid) * offset, cy + math.sin(mid) * offset
            style = series_style(
                self.ctx,
                s.points.get(i),
                self.defs,
                radius * 2,
                radius * 2,
                _accent_cycle(self.ctx, i)
                if plot.vary_colors
                else _accent_cycle(self.ctx, s.index),
                "pie",
            )
            if s.sp_pr is not None and i not in s.points and not plot.vary_colors:
                style = series_style(
                    self.ctx,
                    s.sp_pr,
                    self.defs,
                    radius * 2,
                    radius * 2,
                    _accent_cycle(self.ctx, s.index),
                    "pie",
                )
            self.out.append(
                f'<path d="{_sector(ox, oy, radius, hole, angle, angle + sweep)}" {style.fill.attrs()} '
                f"{paint.line_attrs(style.line)}/>"
            )
            label = self.point_label(labels, s, i, v, v / total)
            if label:
                r = (radius + hole) / 2 if plot.kind == "doughnut" else radius * 0.65
                pos = cval(labels, "dLblPos", "bestFit")
                if pos == "outEnd":
                    r = radius * 1.12
                self.out.append(
                    _text(ox + math.cos(mid) * r, oy + math.sin(mid) * r, label, lstyle)
                )
            angle += sweep

    # -- labels ------------------------------------------------------------------------------
    def point_label(
        self, labels, s: Series, i: int, value: float, percent: float | None = None
    ) -> str:
        if labels is None or cbool(labels, "delete"):
            return ""
        for dlbl in labels.findall(c("dLbl")):
            if (attr_int(dlbl.find(c("idx")), "val", -1)) == i:
                if cbool(dlbl, "delete"):
                    return ""
                labels = dlbl
                break
        parts = []
        if cbool(labels, "showSerName"):
            parts.append(s.name)
        if cbool(labels, "showCatName") and i < len(s.categories):
            parts.append(s.categories[i])
        if cbool(labels, "showVal"):
            code = labels.find(c("numFmt"))
            parts.append(
                format_number(value, code.get("formatCode") if code is not None else s.format_code)
            )
        if cbool(labels, "showPercent") and percent is not None:
            parts.append(f"{round(percent * 100)}%")
        sep = labels.find(c("separator"))
        return (sep.text if sep is not None and sep.text else ", ").join(p for p in parts if p)

    # -- radar -----------------------------------------------------------------------------------
    def draw_radar(self, plot: Plot, area):
        left, top, right, bottom = area
        cx, cy = (left + right) / 2, (top + bottom) / 2
        radius = min(right - left, bottom - top) / 2 * 0.8
        n = max((len(s.values) for s in plot.series), default=0)
        if n < 3:
            return
        values = [v for s in plot.series for v in s.values if v is not None]
        lo, hi, major = nice_scale(min(values + [0]), max(values + [1]), 5)
        grid = "#d9d9d9"
        steps = int(round((hi - lo) / major))
        for k in range(1, steps + 1):
            r = radius * k / steps
            pts = " ".join(
                f"{num(cx + r * math.sin(2 * math.pi * j / n))},{num(cy - r * math.cos(2 * math.pi * j / n))}"
                for j in range(n)
            )
            self.out.append(
                f'<polygon points="{pts}" fill="none" stroke="{grid}" stroke-width="0.75"/>'
            )
        cats = plot.series[0].categories
        for j in range(n):
            ax, ay = (
                cx + radius * math.sin(2 * math.pi * j / n),
                cy - radius * math.cos(2 * math.pi * j / n),
            )
            self.out.append(
                f'<line x1="{num(cx)}" y1="{num(cy)}" x2="{num(ax)}" y2="{num(ay)}" stroke="{grid}" stroke-width="0.75"/>'
            )
            if j < len(cats):
                lx, ly = (
                    cx + radius * 1.12 * math.sin(2 * math.pi * j / n),
                    cy - radius * 1.12 * math.cos(2 * math.pi * j / n),
                )
                self.out.append(_text(lx, ly, cats[j], self.text))
        filled = cval(plot.el, "radarStyle", "marker") == "filled"
        for s in plot.series:
            style = series_style(
                self.ctx,
                s.sp_pr,
                self.defs,
                radius,
                radius,
                _accent_cycle(self.ctx, s.index),
                "area" if filled else "radar",
            )
            pts = []
            for j, v in enumerate(s.values[:n]):
                r = radius * ((v or 0) - lo) / (hi - lo)
                pts.append(
                    f"{num(cx + r * math.sin(2 * math.pi * j / n))},{num(cy - r * math.cos(2 * math.pi * j / n))}"
                )
            fill = style.fill.attrs() if filled else 'fill="none"'
            self.out.append(
                f'<polygon points="{" ".join(pts)}" {fill} {paint.line_attrs(style.line)}/>'
            )

    # -- axis based charts ---------------------------------------------------------------------
    def draw_axes_chart(self, plots: list[Plot], plot_area, area, inner, inner_is_plot: bool):
        axes = {}
        for ax in plot_area:
            name = local(ax.tag)
            if name in ("catAx", "valAx", "dateAx", "serAx"):
                axes[cval(ax, "axId", "")] = ax
        horizontal = any(p.kind == "bar" and p.bar_dir == "bar" for p in plots)
        primary = plots[0]
        cat_ax = val_ax = None
        for ax_id in primary.ax_ids:
            ax = axes.get(ax_id)
            if ax is None:
                continue
            if local(ax.tag) in ("catAx", "dateAx") or (
                primary.kind in ("scatter", "bubble") and cat_ax is None
            ):
                cat_ax = ax
            elif local(ax.tag) == "valAx" and val_ax is None:
                val_ax = ax
        scatter = all(p.kind in ("scatter", "bubble") for p in plots)
        # group plots by value axis (primary / secondary)
        groups: dict[str, list[Plot]] = {}
        for p in plots:
            v_id = next(
                (
                    a
                    for a in p.ax_ids
                    if a in axes and local(axes[a].tag) == "valAx" and axes[a] is not cat_ax
                ),
                None,
            )
            groups.setdefault(v_id or "", []).append(p)
        primary_vid = cval(val_ax, "axId", "") if val_ax is not None else ""
        scales = {}
        for vid, group in groups.items():
            ax = axes.get(vid)
            lo, hi = self.value_range(group)
            scaling = ax.find(c("scaling")) if ax is not None else None
            fmin = _float(cval(scaling, "min"))
            fmax = _float(cval(scaling, "max"))
            major = _float(cval(ax, "majorUnit")) if ax is not None else None
            percent = any(p.grouping == "percentStacked" for p in group)
            if percent:
                lo, hi = (min(lo, 0.0), 1.0) if fmin is None else (lo, hi)
            log_base = _float(cval(scaling, "logBase"))
            scale = nice_scale(lo, hi, 6, True, fmin, fmax, major)
            reverse = cval(scaling, "orientation", "minMax") == "maxMin"
            scales[vid] = (scale, reverse, ax, log_base)
        n_cats = max((len(s.values) for p in plots for s in p.series), default=0)
        categories = next(
            (s.categories for p in plots for s in p.series if s.categories),
            [str(i + 1) for i in range(n_cats)],
        )

        # measure axis labels to reserve room
        left, top, right, bottom = area
        tstyle = self.text
        cat_deleted = cat_ax is None or cbool(cat_ax, "delete")
        val_deleted = val_ax is None or cbool(val_ax, "delete")
        if cat_ax is not None and cval(cat_ax, "tickLblPos") == "none":
            cat_deleted = True
        if val_ax is not None and cval(val_ax, "tickLblPos") == "none":
            val_deleted = True
        cat_style = tstyle.derive(cat_ax.find(c("txPr")) if cat_ax is not None else None)
        val_style = tstyle.derive(val_ax.find(c("txPr")) if val_ax is not None else None)
        (vlo, vhi, vmajor), vrev, vax, _ = scales.get(primary_vid) or next(iter(scales.values()))
        vcode = self.axis_format(vax, plots)
        tick_values = _ticks(vlo, vhi, vmajor)
        val_label_w = (
            max((val_style.width(format_number(t, vcode)) for t in tick_values), default=0) + 6
        )
        secondary = [vid for vid in scales if vid and vid != primary_vid]
        if inner is not None and inner_is_plot:
            pl, pt, pr, pb = inner
        else:
            if inner is not None:
                left, top, right, bottom = inner
            if horizontal:
                cat_w = (
                    max((cat_style.width(cat) for cat in categories), default=0) + 6
                    if not cat_deleted
                    else 0
                )
                pl = left + min(cat_w, (right - left) * 0.4)
                pb = bottom - (val_style.size * 1.6 if not val_deleted else 0)
            else:
                pl = left + (val_label_w if not val_deleted else 0)
                pb = bottom - (cat_style.size * 1.6 if not cat_deleted else 0)
            pt = top + 4
            pr = right - (val_label_w if secondary else 4)
            for ax, side in ((vax, "v"), (cat_ax, "c")):
                title = ax.find(c("title")) if ax is not None else None
                if title is not None:
                    text = _text_of(title.find(c("tx"))) or "Axis Title"
                    ts = tstyle.derive(
                        _first(title.find(c("txPr")), title.find(c("tx"))), tstyle.size * 1.1
                    )
                    vertical_title = (side == "v") != horizontal
                    if vertical_title:
                        self.out.append(
                            _text(left + ts.size * 0.6, (pt + pb) / 2, text, ts, rotate=-90)
                        )
                        pl += ts.size * 1.4
                    else:
                        self.out.append(_text((pl + pr) / 2, bottom - ts.size * 0.6, text, ts))
                        pb -= ts.size * 1.4
        pw, ph = max(1.0, pr - pl), max(1.0, pb - pt)
        # plot area fill
        pa_sp = plot_area.find(c("spPr"))
        if pa_sp is not None:
            fill = paint.resolve_fill(self.ctx, paint.find_fill(pa_sp), self.defs, pw, ph)
            line = paint.resolve_line(
                self.ctx, paint.merge_lines(pa_sp.find(q("a:ln"))), self.defs, pw, ph
            )
            self.out.append(
                f'<rect x="{num(pl)}" y="{num(pt)}" width="{num(pw)}" height="{num(ph)}" {fill.attrs()} {paint.line_attrs(line)}/>'
            )

        def vpos(value, vid=primary_vid):
            (lo, hi, _m), rev, _ax, log_base = scales.get(vid) or scales[next(iter(scales))]
            if log_base and log_base > 1 and lo > 0:
                value = max(value, lo)
                t = (math.log(value) - math.log(lo)) / (math.log(hi) - math.log(lo))
            else:
                t = (value - lo) / (hi - lo) if hi != lo else 0.0
            if rev:
                t = 1 - t
            return (pl + t * pw) if horizontal or scatter_x else (pb - t * ph)

        scatter_x = False
        # gridlines + value axis labels
        grid_el = vax.find(c("majorGridlines")) if vax is not None else None
        grid_line = None
        if grid_el is not None:
            gl_sp = grid_el.find(c("spPr"))
            merged = paint.merge_lines(gl_sp.find(q("a:ln")) if gl_sp is not None else None)
            if "fill" not in merged:
                import xml.etree.ElementTree as ET

                solid = ET.Element(q("a:solidFill"))
                ET.SubElement(solid, q("a:srgbClr"), {"val": "D9D9D9"})
                merged["fill"] = solid
                merged.setdefault("w", "9525")
            grid_line = paint.resolve_line(self.ctx, merged, self.defs, pw, ph)
        for t in tick_values:
            pos = vpos(t)
            if grid_line is not None and grid_line.visible:
                if horizontal:
                    self.out.append(
                        f'<line x1="{num(pos)}" y1="{num(pt)}" x2="{num(pos)}" y2="{num(pb)}" {paint.line_attrs(grid_line)}/>'
                    )
                else:
                    self.out.append(
                        f'<line x1="{num(pl)}" y1="{num(pos)}" x2="{num(pr)}" y2="{num(pos)}" {paint.line_attrs(grid_line)}/>'
                    )
            if not val_deleted:
                label = format_number(t, vcode)
                if horizontal:
                    self.out.append(_text(pos, pb + val_style.size * 0.9, label, val_style))
                else:
                    self.out.append(_text(pl - 4, pos, label, val_style, "end"))
        for vid in secondary:
            (lo, hi, m), _r, ax, _l = scales[vid]
            if ax is not None and not cbool(ax, "delete") and cval(ax, "tickLblPos") != "none":
                code = self.axis_format(ax, groups[vid])
                for t in _ticks(lo, hi, m):
                    self.out.append(
                        _text(pr + 4, vpos(t, vid), format_number(t, code), val_style, "start")
                    )

        # axis lines
        axis_color = "#bfbfbf"
        cat_sp = cat_ax.find(c("spPr")) if cat_ax is not None else None
        cat_line = paint.resolve_line(
            self.ctx,
            paint.merge_lines(cat_sp.find(q("a:ln")) if cat_sp is not None else None),
            self.defs,
            pw,
            ph,
        )
        zero = vpos(min(max(0.0, vlo), vhi))
        if cat_ax is not None and not cbool(cat_ax, "delete"):
            attrs = (
                paint.line_attrs(cat_line)
                if cat_sp is not None and cat_line.visible
                else f'stroke="{axis_color}" stroke-width="0.75"'
            )
            if cat_sp is None or cat_line.visible:
                if horizontal:
                    self.out.append(
                        f'<line x1="{num(zero)}" y1="{num(pt)}" x2="{num(zero)}" y2="{num(pb)}" {attrs}/>'
                    )
                elif not scatter:
                    self.out.append(
                        f'<line x1="{num(pl)}" y1="{num(zero)}" x2="{num(pr)}" y2="{num(zero)}" {attrs}/>'
                    )

        if scatter:
            self.draw_scatter(plots, cat_ax, (pl, pt, pr, pb), vpos, cat_style, cat_deleted)
        else:
            slot_count = max(1, n_cats)
            cat_rev = (
                cat_ax is not None
                and cval(cat_ax.find(c("scaling")), "orientation", "minMax") == "maxMin"
            )
            slot = (ph if horizontal else pw) / slot_count

            def cpos(i, frac=0.5):
                k = (slot_count - 1 - i) if cat_rev else i
                if horizontal:
                    return (
                        pt + (slot_count - 1 - k + frac) * slot
                        if not cat_rev
                        else pt + (k + frac) * slot
                    )
                return pl + (k + frac) * slot

            if not cat_deleted:
                step = 1
                max_label = slot * 0.95
                while (
                    step < slot_count
                    and max(
                        cat_style.width(categories[i])
                        for i in range(0, slot_count, step)
                        if i < len(categories)
                    )
                    > max_label * step
                ):
                    step += 1
                for i in range(0, min(slot_count, len(categories)), step):
                    label = categories[i]
                    if horizontal:
                        self.out.append(_text(pl - 4, cpos(i), label, cat_style, "end"))
                    else:
                        self.out.append(_text(cpos(i), pb + cat_style.size * 0.9, label, cat_style))
            for vid, group in groups.items():
                for plot in group:
                    if plot.kind == "bar":
                        self.draw_bars(
                            plot,
                            slot,
                            cpos,
                            lambda v, vid=vid: vpos(v, vid),
                            horizontal,
                            (pl, pt, pr, pb),
                        )
                    elif plot.kind in ("line", "area"):
                        self.draw_lines(
                            plot, cpos, lambda v, vid=vid: vpos(v, vid), (pl, pt, pr, pb)
                        )

    def axis_format(self, ax, plots) -> str:
        if ax is not None:
            fmt = ax.find(c("numFmt"))
            if (
                fmt is not None
                and fmt.get("formatCode")
                and fmt.get("sourceLinked") not in ("1", "true")
            ):
                return fmt.get("formatCode")
        if any(p.grouping == "percentStacked" for p in plots):
            return "0%"
        for p in plots:
            for s in p.series:
                if s.format_code and s.format_code != "General":
                    return s.format_code
        return "General"

    def value_range(self, plots: list[Plot]) -> tuple[float, float]:
        lo, hi = math.inf, -math.inf
        for p in plots:
            if p.grouping in ("stacked", "percentStacked"):
                n = max((len(s.values) for s in p.series), default=0)
                for i in range(n):
                    pos = sum(max(0.0, s.values[i] or 0.0) for s in p.series if i < len(s.values))
                    neg = sum(min(0.0, s.values[i] or 0.0) for s in p.series if i < len(s.values))
                    if p.grouping == "percentStacked":
                        total = pos - neg or 1.0
                        pos, neg = pos / total, neg / total
                    hi, lo = max(hi, pos), min(lo, neg)
            else:
                for s in p.series:
                    for v in s.values:
                        if v is not None:
                            lo, hi = min(lo, v), max(hi, v)
        if lo == math.inf:
            return 0.0, 1.0
        return lo, hi

    def draw_bars(self, plot: Plot, slot, cpos, vpos, horizontal, box):
        series = plot.series
        stacked = plot.grouping in ("stacked", "percentStacked")
        count = 1 if stacked else max(1, len(series))
        gap = plot.gap / 100.0
        overlap = -plot.overlap / 100.0 if not stacked else -1.0
        # bar width w satisfies: count*w + (count-1)*overlap*w + gap*w = slot
        bw = (
            slot / (count + max(-0.99, overlap) * (count - 1) + gap)
            if count > 1
            else slot / (1 + gap)
        )
        step = bw * (1 + overlap) if count > 1 else 0
        group_w = bw + step * (count - 1)
        n = max((len(s.values) for s in series), default=0)
        totals = [0.0] * n
        if plot.grouping == "percentStacked":
            for i in range(n):
                totals[i] = sum(abs(s.values[i] or 0) for s in series if i < len(s.values)) or 1.0
        pos_acc = [0.0] * n
        neg_acc = [0.0] * n
        labels_out = []
        for k, s in enumerate(series):
            style = series_style(
                self.ctx, s.sp_pr, self.defs, 10, 10, _accent_cycle(self.ctx, s.index), "bar"
            )
            for i, v in enumerate(s.values):
                if v is None:
                    continue
                value = v / totals[i] if plot.grouping == "percentStacked" else v
                if stacked:
                    base = pos_acc[i] if value >= 0 else neg_acc[i]
                    end = base + value
                    if value >= 0:
                        pos_acc[i] = end
                    else:
                        neg_acc[i] = end
                else:
                    base, end = 0.0, value
                pstyle = style
                if i in s.points:
                    pstyle = series_style(
                        self.ctx,
                        s.points[i],
                        self.defs,
                        10,
                        10,
                        _accent_cycle(self.ctx, s.index),
                        "bar",
                    )
                elif plot.vary_colors and len(series) == 1:
                    pstyle = series_style(
                        self.ctx, None, self.defs, 10, 10, _accent_cycle(self.ctx, i), "bar"
                    )
                center = cpos(i)
                offset = -group_w / 2 + (0 if stacked else k * step)
                a, b = vpos(base), vpos(end)
                lo_v, hi_v = min(a, b), max(a, b)
                if horizontal:
                    x, y, w, h = lo_v, center + offset, hi_v - lo_v, bw
                else:
                    x, y, w, h = center + offset, lo_v, bw, hi_v - lo_v
                self.out.append(
                    f'<rect x="{num(x)}" y="{num(y)}" width="{num(max(0, w))}" height="{num(max(0, h))}" '
                    f"{pstyle.fill.attrs()} {paint.line_attrs(pstyle.line)}/>"
                )
                labels = s.labels if s.labels is not None else plot.labels
                text = self.point_label(labels, s, i, v)
                if text:
                    lstyle = self.text.derive(labels.find(c("txPr")))
                    pos = cval(labels, "dLblPos", "ctr" if stacked else "outEnd")
                    if horizontal:
                        lx = (x + w + 3) if pos == "outEnd" else (x + w / 2)
                        labels_out.append(
                            _text(
                                lx,
                                y + h / 2,
                                text,
                                lstyle,
                                "start" if pos == "outEnd" else "middle",
                            )
                        )
                    else:
                        ly = (y - lstyle.size * 0.7) if pos == "outEnd" else (y + h / 2)
                        if v < 0 and pos == "outEnd":
                            ly = y + h + lstyle.size * 0.7
                        labels_out.append(_text(x + w / 2, ly, text, lstyle))
        self.out.extend(labels_out)

    def draw_lines(self, plot: Plot, cpos, vpos, box):
        pl, pt, pr, pb = box
        stacked = plot.grouping in ("stacked", "percentStacked")
        n = max((len(s.values) for s in plot.series), default=0)
        totals = [0.0] * n
        if plot.grouping == "percentStacked":
            for i in range(n):
                totals[i] = (
                    sum(abs(s.values[i] or 0) for s in plot.series if i < len(s.values)) or 1.0
                )
        acc = [0.0] * n
        baseline = [vpos(0.0)] * n
        default_marker = plot.kind == "line" and cbool(plot.el, "marker", True)
        layers = []
        for s in plot.series:
            style = series_style(
                self.ctx,
                s.sp_pr,
                self.defs,
                pr - pl,
                pb - pt,
                _accent_cycle(self.ctx, s.index),
                plot.kind,
            )
            points = []
            for i, v in enumerate(s.values):
                if v is None and not stacked:
                    points.append(None)
                    continue
                value = (v or 0.0) / totals[i] if plot.grouping == "percentStacked" else (v or 0.0)
                if stacked:
                    acc[i] += value
                    value = acc[i]
                points.append((cpos(i), vpos(value)))
            if plot.kind == "area":
                valid = [p for p in points if p is not None]
                if valid:
                    lower = baseline[: len(points)]
                    d = "M" + " L".join(f"{num(x)} {num(y)}" for x, y in valid)
                    d += (
                        "".join(
                            f" L{num(valid[j][0])} {num(lower[j])}"
                            for j in range(len(valid) - 1, -1, -1)
                        )
                        + "Z"
                    )
                    fill = (
                        style.fill
                        if not style.fill.is_none
                        else paint.Paint(_accent_cycle(self.ctx, s.index).rgb_hex())
                    )
                    layers.append(f'<path d="{d}" {fill.attrs()} {paint.line_attrs(style.line)}/>')
                    if stacked:
                        baseline = [
                            p[1] if p is not None else baseline[j] for j, p in enumerate(points)
                        ]
                continue
            for segment in _segments(points):
                if len(segment) < 1:
                    continue
                d = (
                    _smooth_path(segment)
                    if s.smooth
                    else "M" + " L".join(f"{num(x)} {num(y)}" for x, y in segment)
                )
                if style.line.visible:
                    layers.append(f'<path d="{d}" fill="none" {paint.line_attrs(style.line)}/>')
            marker = s.marker if s.marker is not None else ("auto" if default_marker else "none")
            if marker != "none":
                color = (
                    style.line.paint.value
                    if style.line.visible
                    else _accent_cycle(self.ctx, s.index).rgb_hex()
                )
                mstyle = None
                if s.marker_sp_pr is not None:
                    mstyle = series_style(
                        self.ctx,
                        s.marker_sp_pr,
                        self.defs,
                        10,
                        10,
                        _accent_cycle(self.ctx, s.index),
                        "bar",
                    )
                for p in points:
                    if p is not None:
                        layers.append(_marker(p[0], p[1], marker, s.marker_size, color, mstyle))
            labels = s.labels if s.labels is not None else plot.labels
            for i, p in enumerate(points):
                if p is None or labels is None:
                    continue
                text = self.point_label(labels, s, i, s.values[i] or 0.0)
                if text:
                    lstyle = self.text.derive(labels.find(c("txPr")))
                    layers.append(_text(p[0], p[1] - lstyle.size * 0.9, text, lstyle))
        self.out.extend(layers)

    def draw_scatter(self, plots, x_ax, box, vpos, style, deleted):
        pl, pt, pr, pb = box
        xs = [x for p in plots for s in p.series for x in s.x_values if x is not None]
        if not xs:
            xs = [float(i + 1) for i in range(max(len(s.values) for p in plots for s in p.series))]
        scaling = x_ax.find(c("scaling")) if x_ax is not None else None
        lo, hi, major = nice_scale(
            min(xs),
            max(xs),
            6,
            True,
            _float(cval(scaling, "min")),
            _float(cval(scaling, "max")),
            _float(cval(x_ax, "majorUnit")) if x_ax is not None else None,
        )

        def xpos(v):
            return pl + (v - lo) / (hi - lo) * (pr - pl) if hi != lo else pl

        code = "General"
        if x_ax is not None and x_ax.find(c("numFmt")) is not None:
            code = x_ax.find(c("numFmt")).get("formatCode", "General")
        grid = x_ax.find(c("majorGridlines")) if x_ax is not None else None
        for t in _ticks(lo, hi, major):
            if grid is not None:
                self.out.append(
                    f'<line x1="{num(xpos(t))}" y1="{num(pt)}" x2="{num(xpos(t))}" y2="{num(pb)}" stroke="#d9d9d9" stroke-width="0.75"/>'
                )
            if not deleted:
                self.out.append(
                    _text(xpos(t), pb + style.size * 0.9, format_number(t, code), style)
                )
        self.out.append(
            f'<line x1="{num(pl)}" y1="{num(pb)}" x2="{num(pr)}" y2="{num(pb)}" stroke="#bfbfbf" stroke-width="0.75"/>'
        )
        for plot in plots:
            scatter_style = cval(plot.el, "scatterStyle", "marker")
            for s in plot.series:
                st = series_style(
                    self.ctx,
                    s.sp_pr,
                    self.defs,
                    10,
                    10,
                    _accent_cycle(self.ctx, s.index),
                    "scatter",
                )
                pts = []
                for i, v in enumerate(s.values):
                    x = (
                        s.x_values[i]
                        if i < len(s.x_values) and s.x_values[i] is not None
                        else float(i + 1)
                    )
                    pts.append(None if v is None else (xpos(x), vpos(v)))
                draw_line = st.line.visible and (
                    scatter_style in ("lineMarker", "line", "smooth", "smoothMarker")
                    or (s.sp_pr is not None and s.sp_pr.find(q("a:ln")) is not None)
                )
                if s.sp_pr is not None:
                    ln = s.sp_pr.find(q("a:ln"))
                    if ln is not None and ln.find(q("a:noFill")) is not None:
                        draw_line = False
                if draw_line:
                    for seg in _segments(pts):
                        d = (
                            _smooth_path(seg)
                            if (s.smooth or "smooth" in scatter_style)
                            else "M" + " L".join(f"{num(x)} {num(y)}" for x, y in seg)
                        )
                        self.out.append(f'<path d="{d}" fill="none" {paint.line_attrs(st.line)}/>')
                marker = s.marker or ("none" if scatter_style in ("line", "smooth") else "circle")
                if plot.kind == "bubble":
                    biggest = max((b for b in s.sizes if b), default=1.0)
                    for i, p in enumerate(pts):
                        if p is not None:
                            size = (
                                s.sizes[i] if i < len(s.sizes) and s.sizes[i] else 1.0
                            ) / biggest
                            r = max(2.0, math.sqrt(size) * min(pr - pl, pb - pt) * 0.08)
                            self.out.append(
                                f'<circle cx="{num(p[0])}" cy="{num(p[1])}" r="{num(r)}" fill="{_accent_cycle(self.ctx, s.index).rgb_hex()}" fill-opacity="0.75"/>'
                            )
                elif marker != "none":
                    color = (
                        st.line.paint.value
                        if st.line.visible
                        else _accent_cycle(self.ctx, s.index).rgb_hex()
                    )
                    mstyle = (
                        series_style(
                            self.ctx,
                            s.marker_sp_pr,
                            self.defs,
                            10,
                            10,
                            _accent_cycle(self.ctx, s.index),
                            "bar",
                        )
                        if s.marker_sp_pr is not None
                        else None
                    )
                    for p in pts:
                        if p is not None:
                            self.out.append(
                                _marker(p[0], p[1], marker, s.marker_size, color, mstyle)
                            )


def _first(*els):
    return next((e for e in els if e is not None), None)


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _ticks(lo: float, hi: float, major: float) -> list[float]:
    if major <= 0 or not math.isfinite(major):
        return [lo, hi]
    count = int(round((hi - lo) / major))
    if count > 200:
        return [lo, hi]
    return [lo + i * major for i in range(count + 1)]


def _segments(points):
    seg = []
    for p in points:
        if p is None:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(p)
    if seg:
        yield seg


def _smooth_path(points) -> str:
    if len(points) < 3:
        return "M" + " L".join(f"{num(x)} {num(y)}" for x, y in points)
    d = f"M{num(points[0][0])} {num(points[0][1])}"
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{num(c1[0])} {num(c1[1])} {num(c2[0])} {num(c2[1])} {num(p2[0])} {num(p2[1])}"
    return d


def _marker(x, y, symbol, size, color, style: SeriesStyle | None) -> str:
    r = max(1.5, size / 2)
    if style is not None:
        fill = style.fill.attrs()
        stroke = (
            paint.line_attrs(style.line)
            if style.line.visible
            else f'stroke="{color}" stroke-width="0.75"'
        )
    else:
        fill = f'fill="{color}"'
        stroke = f'stroke="{color}" stroke-width="0.75"'
    if symbol in ("square", "auto"):
        if symbol == "auto":
            return f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(r)}" {fill} {stroke}/>'
        return f'<rect x="{num(x - r)}" y="{num(y - r)}" width="{num(2 * r)}" height="{num(2 * r)}" {fill} {stroke}/>'
    if symbol == "diamond":
        return f'<path d="M{num(x)} {num(y - r)}L{num(x + r)} {num(y)}L{num(x)} {num(y + r)}L{num(x - r)} {num(y)}Z" {fill} {stroke}/>'
    if symbol == "triangle":
        return f'<path d="M{num(x)} {num(y - r)}L{num(x + r)} {num(y + r)}L{num(x - r)} {num(y + r)}Z" {fill} {stroke}/>'
    if symbol in ("x", "star", "plus", "dash", "dot"):
        return f'<path d="M{num(x - r)} {num(y - r)}L{num(x + r)} {num(y + r)}M{num(x + r)} {num(y - r)}L{num(x - r)} {num(y + r)}" fill="none" stroke="{color}" stroke-width="1"/>'
    return f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(r)}" {fill} {stroke}/>'


def _sector(cx, cy, r, hole, a0, a1) -> str:
    if a1 - a0 >= 2 * math.pi - 1e-6:
        # full circle: two half arcs
        mid = a0 + math.pi
        outer = (
            f"M{num(cx + r * math.cos(a0))} {num(cy + r * math.sin(a0))}"
            f"A{num(r)} {num(r)} 0 1 1 {num(cx + r * math.cos(mid))} {num(cy + r * math.sin(mid))}"
            f"A{num(r)} {num(r)} 0 1 1 {num(cx + r * math.cos(a0))} {num(cy + r * math.sin(a0))}Z"
        )
        if hole > 0:
            outer += (
                f"M{num(cx + hole * math.cos(a0))} {num(cy + hole * math.sin(a0))}"
                f"A{num(hole)} {num(hole)} 0 1 0 {num(cx + hole * math.cos(mid))} {num(cy + hole * math.sin(mid))}"
                f"A{num(hole)} {num(hole)} 0 1 0 {num(cx + hole * math.cos(a0))} {num(cy + hole * math.sin(a0))}Z"
            )
        return outer
    large = 1 if a1 - a0 > math.pi else 0
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    if hole <= 0:
        return f"M{num(cx)} {num(cy)}L{num(x0)} {num(y0)}A{num(r)} {num(r)} 0 {large} 1 {num(x1)} {num(y1)}Z"
    hx0, hy0 = cx + hole * math.cos(a0), cy + hole * math.sin(a0)
    hx1, hy1 = cx + hole * math.cos(a1), cy + hole * math.sin(a1)
    return (
        f"M{num(x0)} {num(y0)}A{num(r)} {num(r)} 0 {large} 1 {num(x1)} {num(y1)}"
        f"L{num(hx1)} {num(hy1)}A{num(hole)} {num(hole)} 0 {large} 0 {num(hx0)} {num(hy0)}Z"
    )


_ = emu  # re-exported helper kept for chart extensions
