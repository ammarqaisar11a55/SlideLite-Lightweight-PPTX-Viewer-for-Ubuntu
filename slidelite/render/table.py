"""Tables: grid, merged cells, table styles (built-in and custom), cell
borders, fills, margins and text."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from slidelite.presentation.tablestyles import builtin_style
from slidelite.presentation.xmlsafe import attr_bool, attr_int, local, q
from slidelite.render import paint
from slidelite.render.context import RenderContext, emu
from slidelite.render.markup import num, px
from slidelite.render.text import StyleChain, merge_body_pr, render_paragraphs

_ANCHOR = {"t": "top", "ctr": "middle", "b": "bottom"}
# Lowest to highest priority, as PowerPoint applies conditional formatting.
_PART_ORDER = [
    "wholeTbl",
    "band1V",
    "band2V",
    "band1H",
    "band2H",
    "firstCol",
    "lastCol",
    "firstRow",
    "lastRow",
    "seCell",
    "swCell",
    "neCell",
    "nwCell",
]


def _style_for(ctx: RenderContext, tbl_pr):
    style_id = None
    if tbl_pr is not None:
        sid = tbl_pr.find(q("a:tableStyleId"))
        if sid is not None and sid.text:
            style_id = sid.text.strip()
        inline = tbl_pr.find(q("a:tableStyle"))
        if inline is not None:
            return inline
    if style_id is None:
        return None
    custom = ctx.deck.table_styles.get(style_id)
    return custom if custom is not None else builtin_style(style_id)


class _Part:
    """Resolved style part: fill element, text props, border lines."""

    def __init__(self, el) -> None:
        self.fill = None
        self.borders: dict[str, object] = {}
        self.text: dict = {}
        if el is None:
            return
        tx = el.find(q("a:tcTxStyle"))
        if tx is not None:
            for key in ("b", "i"):
                if tx.get(key) in ("on", "1", "true"):
                    self.text[key] = "1"
                elif tx.get(key) in ("off", "0", "false"):
                    self.text[key] = "0"
            for c in tx:
                name = local(c.tag)
                if name in ("srgbClr", "schemeClr", "prstClr", "sysClr", "scrgbClr", "hslClr"):
                    solid = ET.Element(q("a:solidFill"))
                    solid.append(c)
                    self.text["fill"] = solid
                elif name == "font":
                    latin = c.find(q("a:latin"))
                    if latin is not None and latin.get("typeface"):
                        self.text["latin"] = latin.get("typeface")
                elif name == "fontRef":
                    idx = c.get("idx", "minor")
                    self.text["latin"] = "+mj-lt" if idx == "major" else "+mn-lt"
                    color = next(iter(c), None)
                    if color is not None:
                        solid = ET.Element(q("a:solidFill"))
                        solid.append(color)
                        self.text["fill"] = solid
        tc = el.find(q("a:tcStyle"))
        if tc is not None:
            bdr = tc.find(q("a:tcBdr"))
            for edge in bdr if bdr is not None else []:
                ln = edge.find(q("a:ln"))
                if ln is not None:
                    self.borders[local(edge.tag)] = ln
            fill = tc.find(q("a:fill"))
            if fill is not None:
                self.fill = paint.find_fill(fill)


def render_table(ctx: RenderContext, tbl, w: float, h: float) -> str:
    if tbl is None:
        return ""
    tbl_pr = tbl.find(q("a:tblPr"))
    style = _style_for(ctx, tbl_pr)
    parts = {
        name: _Part(style.find(q(f"a:{name}")) if style is not None else None)
        for name in _PART_ORDER
    }
    flags = {
        k: attr_bool(tbl_pr, k)
        for k in ("firstRow", "lastRow", "firstCol", "lastCol", "bandRow", "bandCol")
    }
    grid = tbl.find(q("a:tblGrid"))
    cols = [emu(g.get("w", 0)) for g in (grid.findall(q("a:gridCol")) if grid is not None else [])]
    rows = tbl.findall(q("a:tr"))
    if not cols or not rows:
        return ""
    n_rows, n_cols = len(rows), len(cols)

    # Table background (style tblBg and direct tblPr fill).
    defs: list[str] = []
    background = ""
    bg_fill = paint.find_fill(tbl_pr)
    if bg_fill is None and style is not None:
        bg = style.find(q("a:tblBg"))
        bg_fill = paint.find_fill(bg) if bg is not None else None
    if bg_fill is not None:
        bg_paint = paint.resolve_fill(ctx, bg_fill, defs, w, h)
        if bg_paint.color is not None and not bg_paint.is_none:
            background = f"background:{bg_paint.color.with_alpha(bg_paint.opacity * bg_paint.color.a).css()};"

    def parts_for(r: int, c: int) -> list[_Part]:
        names = ["wholeTbl"]
        first_body_row = 1 if flags["firstRow"] else 0
        first_body_col = 1 if flags["firstCol"] else 0
        if flags["bandCol"]:
            names.append("band1V" if (c - first_body_col) % 2 == 0 else "band2V")
        if (
            flags["bandRow"]
            and not (flags["firstRow"] and r == 0)
            and not (flags["lastRow"] and r == n_rows - 1)
        ):
            names.append("band1H" if (r - first_body_row) % 2 == 0 else "band2H")
        if flags["firstCol"] and c == 0:
            names.append("firstCol")
        if flags["lastCol"] and c == n_cols - 1:
            names.append("lastCol")
        if flags["firstRow"] and r == 0:
            names.append("firstRow")
        if flags["lastRow"] and r == n_rows - 1:
            names.append("lastRow")
        return [parts[n] for n in _PART_ORDER if n in names]

    def edge_line(r, c, row_span, col_span, edge, applied):
        """Style border for one edge of a cell, honouring outer/inner edges."""
        outer = {
            "left": c == 0,
            "right": c + col_span >= n_cols,
            "top": r == 0,
            "bottom": r + row_span >= n_rows,
        }[edge]
        inner_key = "insideV" if edge in ("left", "right") else "insideH"
        line = None
        for part, part_name in applied:
            # A part's own outer edges apply at the boundary of the part.
            if part_name in ("firstRow", "lastRow", "band1H", "band2H"):
                part_outer = edge in ("top", "bottom") or outer
            elif part_name in ("firstCol", "lastCol", "band1V", "band2V"):
                part_outer = edge in ("left", "right") or outer
            else:
                part_outer = outer
            key = edge if part_outer else inner_key
            if key in part.borders:
                line = part.borders[key]
        return line

    rows_html = []
    covered: set[tuple[int, int]] = set()
    from slidelite.presentation.parts import Master

    master = ctx.part.master
    base_sources = [ctx.deck.default_text_style]
    if isinstance(master, Master):
        base_sources.append(master.other_style)
    for r, tr in enumerate(rows):
        height = emu(tr.get("h", 0))
        cells_html = []
        for c, tc in enumerate(tr.findall(q("a:tc"))[:n_cols]):
            if (r, c) in covered or attr_bool(tc, "hMerge") or attr_bool(tc, "vMerge"):
                continue
            col_span = max(1, attr_int(tc, "gridSpan", 1) or 1)
            row_span = max(1, attr_int(tc, "rowSpan", 1) or 1)
            for rr in range(r, r + row_span):
                for cc in range(c, c + col_span):
                    if (rr, cc) != (r, c):
                        covered.add((rr, cc))
            names = []
            applied = []
            for name in _PART_ORDER:
                part = parts[name]
                if part in parts_for(r, c):
                    names.append(name)
                    applied.append((part, name))
            tc_pr = tc.find(q("a:tcPr"))
            cell_w = sum(cols[c : c + col_span])
            # fill: direct > style parts (highest priority last)
            fill_el = paint.find_fill(tc_pr)
            if fill_el is None:
                for part, _ in applied:
                    if part.fill is not None:
                        fill_el = part.fill
            css = []
            if fill_el is not None:
                p = paint.resolve_fill(ctx, fill_el, defs, cell_w, height)
                if not p.is_none and p.color is not None:
                    css.append(f"background:{p.color.with_alpha(p.opacity * p.color.a).css()}")
                elif not p.is_none and p.value.startswith("url("):
                    gradient = (
                        paint.css_gradient(ctx.colors, fill_el)
                        if local(fill_el.tag) == "gradFill"
                        else None
                    )
                    if gradient:
                        css.append(f"background:{gradient}")
            # borders: direct lnL/lnR/lnT/lnB > style
            for edge, tag in (("left", "lnL"), ("right", "lnR"), ("top", "lnT"), ("bottom", "lnB")):
                direct = tc_pr.find(q(f"a:{tag}")) if tc_pr is not None else None
                line_el = (
                    direct
                    if direct is not None
                    else edge_line(r, c, row_span, col_span, edge, applied)
                )
                css.append(f"border-{edge}:{_border_css(ctx, line_el)}")
            mar = {
                k: emu(tc_pr.get(k, d)) if tc_pr is not None else emu(d)
                for k, d in (("marL", 91440), ("marR", 91440), ("marT", 45720), ("marB", 45720))
            }
            css.append(
                f"padding:{px(mar['marT'])} {px(mar['marR'])} {px(mar['marB'])} {px(mar['marL'])}"
            )
            anchor = tc_pr.get("anchor", "t") if tc_pr is not None else "t"
            css.append(f"vertical-align:{_ANCHOR.get(anchor, 'top')}")
            # text: style text props act like a fontRef for the cell
            text_props: dict = {}
            for part, _ in applied:
                text_props.update(part.text)
            chain = StyleChain(base_sources, text_props)
            tx_body = tc.find(q("a:txBody"))
            body = merge_body_pr(tx_body.find(q("a:bodyPr")) if tx_body is not None else None)
            content = render_paragraphs(ctx, tx_body, chain, body) if tx_body is not None else ""
            vert = tc_pr.get("vert") if tc_pr is not None else None
            if vert in ("vert", "eaVert"):
                content = f'<div style="writing-mode:vertical-rl">{content}</div>'
            elif vert == "vert270":
                content = f'<div style="writing-mode:vertical-rl;transform:rotate(180deg)">{content}</div>'
            span = (f' colspan="{col_span}"' if col_span > 1 else "") + (
                f' rowspan="{row_span}"' if row_span > 1 else ""
            )
            cells_html.append(f'<td{span} style="{";".join(css)}">{content}</td>')
        rows_html.append(f'<tr style="height:{px(height)}">{"".join(cells_html)}</tr>')
    colgroup = "".join(f'<col style="width:{px(cw)}">' for cw in cols)
    total_w = sum(cols)
    defs_svg = (
        f'<svg width="0" height="0" style="position:absolute"><defs>{"".join(defs)}</defs></svg>'
        if defs
        else ""
    )
    return (
        f'{defs_svg}<table class="tbl" style="{background}width:{px(total_w)}">'
        f"<colgroup>{colgroup}</colgroup><tbody>{''.join(rows_html)}</tbody></table>"
    )


_DASH_CSS = {
    "dot": "dotted",
    "sysDot": "dotted",
    "dash": "dashed",
    "lgDash": "dashed",
    "sysDash": "dashed",
    "dashDot": "dashed",
    "lgDashDot": "dashed",
    "lgDashDotDot": "dashed",
    "sysDashDot": "dashed",
    "sysDashDotDot": "dashed",
}


def _border_css(ctx: RenderContext, ln) -> str:
    if ln is None:
        return "none"
    fill = paint.find_fill(ln)
    if fill is None or local(fill.tag) == "noFill":
        return "none"
    color = None
    if local(fill.tag) == "solidFill":
        color = ctx.colors.resolve(fill)
    elif local(fill.tag) == "gradFill":
        stops = paint.gradient_stops(ctx.colors, fill)
        color = stops[0][1] if stops else None
    if color is None:
        return "none"
    width = max(0.5, emu(ln.get("w", 12700)))
    dash = ln.find(q("a:prstDash"))
    style = _DASH_CSS.get(dash.get("val", "solid"), "solid") if dash is not None else "solid"
    if ln.get("cmpd") in ("dbl", "thickThin", "thinThick") and width >= 2:
        style = "double"
    return f"{num(width)}px {style} {color.css()}"
