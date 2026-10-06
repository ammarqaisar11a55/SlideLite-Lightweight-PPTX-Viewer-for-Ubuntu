"""Text bodies: style inheritance and HTML generation.

Inheritance chain for a paragraph at level N (lowest priority first):

1. presentation ``defaultTextStyle``
2. master ``txStyles`` (title / body / other, by placeholder type)
3. master placeholder ``lstStyle``, layout placeholder ``lstStyle``
4. the shape style's ``fontRef`` (font + colour)
5. the shape's own ``lstStyle``
6. the paragraph ``pPr`` and finally each run's ``rPr``
"""

from __future__ import annotations

from dataclasses import dataclass

from slidelite.presentation import fonts
from slidelite.presentation.color import Color
from slidelite.presentation.xmlsafe import attr_int, local, q, rid
from slidelite.render.context import RenderContext, emu
from slidelite.render.markup import esc, num, px
from slidelite.render.paint import css_gradient, find_fill, text_shadow_css

LINE_FACTOR = 1.2  # PowerPoint "single" spacing relative to the font size
DEFAULT_SIZE = 1800  # hundredths of a point

_RUN_ATTRS = ("sz", "b", "i", "u", "strike", "baseline", "cap", "spc", "lang")
_PARA_ATTRS = ("algn", "marL", "indent", "lvl", "rtl", "defTabSz", "fontAlgn")


# -- parsing ------------------------------------------------------------------------------


def parse_rpr(el) -> dict:
    out: dict = {}
    if el is None:
        return out
    for key in _RUN_ATTRS:
        if el.get(key) is not None:
            out[key] = el.get(key)
    for c in el:
        name = local(c.tag)
        if name in ("solidFill", "gradFill", "noFill", "pattFill", "blipFill", "grpFill"):
            out["fill"] = c
        elif name in ("latin", "ea", "cs", "sym"):
            if c.get("typeface"):
                out[name] = c.get("typeface")
        elif name == "highlight":
            out["highlight"] = c
        elif name == "ln":
            out["ln"] = c
        elif name == "effectLst":
            out["effects"] = c
        elif name == "hlinkClick":
            out["hlink"] = c
    return out


def parse_ppr(el) -> dict:
    out: dict = {}
    if el is None:
        return out
    for key in _PARA_ATTRS:
        if el.get(key) is not None:
            out[key] = el.get(key)
    for c in el:
        name = local(c.tag)
        if name in ("lnSpc", "spcBef", "spcAft"):
            pct = c.find(q("a:spcPct"))
            pts = c.find(q("a:spcPts"))
            if pct is not None:
                out[name] = ("pct", (attr_int(pct, "val", 100000) or 0) / 100000.0)
            elif pts is not None:
                out[name] = ("pts", (attr_int(pts, "val", 0) or 0) / 100.0)
        elif name == "buNone":
            out["buType"] = "none"
        elif name == "buChar":
            out["buType"] = "char"
            out["buChar"] = c.get("char", "•")
        elif name == "buAutoNum":
            out["buType"] = "autonum"
            out["buAutoNum"] = c.get("type", "arabicPeriod")
            out["buStart"] = attr_int(c, "startAt", 1) or 1
        elif name == "buBlip":
            out["buType"] = "blip"
            out["buBlip"] = c
        elif name == "buClrTx":
            out["buClr"] = "tx"
        elif name == "buClr":
            out["buClr"] = c
        elif name == "buSzTx":
            out["buSz"] = ("pct", 1.0)
        elif name == "buSzPct":
            out["buSz"] = ("pct", (attr_int(c, "val", 100000) or 100000) / 100000.0)
        elif name == "buSzPts":
            out["buSz"] = ("pts", (attr_int(c, "val", 1200) or 1200) / 100.0)
        elif name == "buFontTx":
            out["buFont"] = "tx"
        elif name == "buFont":
            out["buFont"] = c.get("typeface") or "tx"
        elif name == "defRPr":
            out["rpr"] = parse_rpr(c)
        elif name == "tabLst":
            out["tabs"] = [emu(t.get("pos", 0)) for t in c]
    return out


def merge(base: dict, over: dict) -> dict:
    if not over:
        return base
    merged = dict(base)
    for key, value in over.items():
        if key == "rpr":
            merged["rpr"] = {**base.get("rpr", {}), **value}
        else:
            merged[key] = value
    return merged


def list_levels(lst) -> dict[int, dict]:
    """``a:lstStyle``/``p:titleStyle``... -> {level: paragraph props}; 0 = defPPr."""
    levels: dict[int, dict] = {}
    if lst is None:
        return levels
    for c in lst:
        name = local(c.tag)
        if name == "defPPr":
            levels[0] = parse_ppr(c)
        elif name.startswith("lvl") and name.endswith("pPr"):
            try:
                levels[int(name[3:-3])] = parse_ppr(c)
            except ValueError:
                continue
    return levels


class StyleChain:
    def __init__(self, sources: list, font_ref: dict | None = None) -> None:
        self.layers = [list_levels(s) for s in sources if s is not None]
        self.font_ref = font_ref or {}
        self._cache: dict[int, dict] = {}

    def level(self, lvl: int) -> dict:
        cached = self._cache.get(lvl)
        if cached is not None:
            return cached
        merged: dict = {}
        for i, layer in enumerate(self.layers):
            if i == len(self.layers) - 1 and self.font_ref:
                merged = merge(merged, {"rpr": self.font_ref})
            merged = merge(merged, layer.get(0, {}))
            merged = merge(merged, layer.get(lvl, {}))
        if self.font_ref and not self.layers:
            merged = merge(merged, {"rpr": self.font_ref})
        self._cache[lvl] = merged
        return merged

    def with_overlay(self, lst) -> StyleChain:
        chain = StyleChain([], self.font_ref)
        chain.layers = [*self.layers, list_levels(lst)]
        return chain


def merge_body_pr(*bodies) -> dict:
    out: dict = {}
    for body in bodies:
        if body is None:
            continue
        out.update(body.attrib)
        for c in body:
            name = local(c.tag)
            if name in ("normAutofit", "spAutoFit", "noAutofit"):
                out["autofit"] = name
                out["fontScale"] = c.get("fontScale")
                out["lnSpcReduction"] = c.get("lnSpcReduction")
    return out


# -- numbering ----------------------------------------------------------------------------


def _roman(n: int) -> str:
    if n <= 0:
        return str(n)
    vals = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _alpha(n: int) -> str:
    out = ""
    while n > 0:
        n -= 1
        out = chr(ord("A") + n % 26) + out
        n //= 26
    return out or "A"


def autonum_text(scheme: str, n: int) -> str:
    if scheme.startswith("circleNum"):
        return chr(0x2460 + n - 1) if 1 <= n <= 20 else str(n)
    if scheme.startswith("romanUc"):
        core = _roman(n)
    elif scheme.startswith("romanLc"):
        core = _roman(n).lower()
    elif scheme.startswith("alphaUc"):
        core = _alpha(n)
    elif scheme.startswith("alphaLc"):
        core = _alpha(n).lower()
    else:
        core = str(n)
    if scheme.endswith("ParenBoth"):
        return f"({core})"
    if scheme.endswith("ParenR"):
        return f"{core})"
    if scheme.endswith("Period"):
        return f"{core}."
    if scheme.endswith("Minus"):
        return f"-{core}-"
    return core


# -- rendering -----------------------------------------------------------------------------


@dataclass
class TextBox:
    """Text frame geometry in shape-local px."""

    x: float
    y: float
    w: float
    h: float


_ALIGN = {
    "l": "left",
    "ctr": "center",
    "r": "right",
    "just": "justify",
    "dist": "justify",
    "justLow": "justify",
    "thaiDist": "justify",
}
_ANCHOR = {
    "t": "flex-start",
    "ctr": "center",
    "b": "flex-end",
    "just": "flex-start",
    "dist": "space-between",
}
_SELF_ALIGN = {"l": "flex-start", "ctr": "center", "r": "flex-end"}


class TextRenderer:
    def __init__(self, ctx: RenderContext, chain: StyleChain) -> None:
        self.ctx = ctx
        self.chain = chain
        self.counters: dict[int, int] = {}

    # -- run properties -------------------------------------------------------
    def size_pt(self, rpr: dict) -> float:
        try:
            sz = int(rpr.get("sz", DEFAULT_SIZE))
        except ValueError:
            sz = DEFAULT_SIZE
        return max(1.0, sz / 100.0 * self.ctx.font_scale)

    def run_color(self, rpr: dict) -> Color:
        fill = rpr.get("fill")
        if fill is not None and local(fill.tag) == "solidFill":
            color = self.ctx.colors.resolve(fill)
            if color is not None:
                return color
        if fill is not None and local(fill.tag) == "gradFill":
            gs = fill.find(q("a:gsLst"))
            if gs is not None and len(gs):
                color = self.ctx.colors.resolve(gs[0])
                if color is not None:
                    return color
        return self.ctx.colors.scheme_color("tx1")

    def font_families(self, rpr: dict) -> tuple[str | None, str | None, str | None]:
        theme = self.ctx.theme
        latin = theme.resolve_typeface(rpr.get("latin"), "latin")
        ea = theme.resolve_typeface(rpr.get("ea"), "ea")
        cs = theme.resolve_typeface(rpr.get("cs"), "cs")
        if latin is None:
            latin = theme.fonts.minor.get("latin")
        return latin, ea, cs

    def run_css(self, rpr: dict, text: str) -> tuple[str, str]:
        """(css, transformed text) for a run."""
        size = self.size_pt(rpr)
        latin, ea, cs = self.font_families(rpr)
        if fonts.is_symbol_font(latin):
            text, _ = fonts.translate_symbol(text, latin)
            family = fonts.family_stack("Noto Sans Symbols 2", "Noto Sans Symbols", "DejaVu Sans")
        elif (
            rpr.get("sym")
            and fonts.is_symbol_font(rpr.get("sym"))
            and any(0xF000 <= ord(c) <= 0xF0FF for c in text)
        ):
            text, _ = fonts.translate_symbol(text, rpr.get("sym"))
            family = fonts.family_stack(latin, "DejaVu Sans")
        else:
            family = fonts.family_stack(
                latin, ea if ea and ea != latin else None, cs if cs and cs != latin else None
            )
        parts = [f"font-family:{family}", f"font-size:{px(size)}"]
        if rpr.get("b") in ("1", "true"):
            parts.append("font-weight:700")
        if rpr.get("i") in ("1", "true"):
            parts.append("font-style:italic")
        decorations = []
        underline = rpr.get("u", "none")
        if rpr.get("hlink") is not None and _is_link(rpr.get("hlink")) and "u" not in rpr:
            underline = "sng"
        if underline and underline != "none":
            decorations.append("underline")
        strike = rpr.get("strike", "noStrike")
        if strike in ("sngStrike", "dblStrike"):
            decorations.append("line-through")
        if decorations:
            parts.append(f"text-decoration-line:{' '.join(decorations)}")
            if underline in ("dbl",) or strike == "dblStrike":
                parts.append("text-decoration-style:double")
            elif underline in ("dotted", "dottedHeavy"):
                parts.append("text-decoration-style:dotted")
            elif underline and underline.startswith(("dash", "dotDash", "dotDotDash")):
                parts.append("text-decoration-style:dashed")
            elif underline and underline.startswith("wavy"):
                parts.append("text-decoration-style:wavy")
            if underline in ("heavy", "dottedHeavy", "dashHeavy", "dashLongHeavy", "wavyHeavy"):
                parts.append("text-decoration-thickness:0.1em")
        fill = rpr.get("fill")
        hlink = rpr.get("hlink")
        if hlink is not None and _is_link(hlink):
            color = self.ctx.colors.scheme_color("hlink")
            parts.append(f"color:{color.css()}")
        elif fill is not None and local(fill.tag) == "noFill":
            parts.append("color:transparent")
        elif fill is not None and local(fill.tag) == "gradFill":
            gradient = css_gradient(self.ctx.colors, fill)
            if gradient:
                parts += [
                    f"background-image:{gradient}",
                    "-webkit-background-clip:text",
                    "background-clip:text",
                    "color:transparent",
                ]
            else:
                parts.append(f"color:{self.run_color(rpr).css()}")
        else:
            parts.append(f"color:{self.run_color(rpr).css()}")
        highlight = rpr.get("highlight")
        if highlight is not None:
            hl = self.ctx.colors.resolve(highlight)
            if hl is not None:
                parts.append(f"background-color:{hl.css()}")
        spc = rpr.get("spc")
        if spc:
            try:
                parts.append(f"letter-spacing:{px(int(spc) / 100.0)}")
            except ValueError:
                pass
        cap = rpr.get("cap")
        if cap == "all":
            parts.append("text-transform:uppercase")
        elif cap == "small":
            parts.append("font-variant:small-caps")
        baseline = rpr.get("baseline")
        if baseline:
            try:
                shift = int(baseline) / 100000.0
            except ValueError:
                shift = 0.0
            if shift:
                parts.append("font-size:" + px(size * (0.66 if abs(shift) > 0.0 else 1)))
                parts.append(f"position:relative;top:{px(-shift * size)}")
        ln = rpr.get("ln")
        if ln is not None:
            stroke_fill = find_fill(ln)
            if stroke_fill is not None and local(stroke_fill.tag) == "solidFill":
                color = self.ctx.colors.resolve(stroke_fill)
                if color is not None:
                    width = max(0.25, emu(ln.get("w", 9525)))
                    parts.append(f"-webkit-text-stroke:{px(width)} {color.css()}")
        shadow = text_shadow_css(self.ctx, rpr.get("effects"))
        if shadow:
            parts.append(f"text-shadow:{shadow}")
        return ";".join(parts), text

    # -- paragraphs -------------------------------------------------------------
    def paragraph_props(self, p) -> tuple[dict, int]:
        ppr_el = p.find(q("a:pPr"))
        lvl = attr_int(ppr_el, "lvl", 0) or 0
        lvl = max(0, min(8, lvl))
        return merge(self.chain.level(lvl + 1), parse_ppr(ppr_el)), lvl

    def render_paragraph(self, p, slide_number: int) -> str:
        pp, lvl = self.paragraph_props(p)
        base_rpr = pp.get("rpr", {})
        runs: list[tuple[str, dict, str | None]] = []  # (kind, rpr, text)
        for c in p:
            name = local(c.tag)
            if name == "r":
                t = c.find(q("a:t"))
                runs.append(
                    (
                        "r",
                        {**base_rpr, **parse_rpr(c.find(q("a:rPr")))},
                        t.text if t is not None and t.text else "",
                    )
                )
            elif name == "br":
                runs.append(("br", {**base_rpr, **parse_rpr(c.find(q("a:rPr")))}, None))
            elif name == "fld":
                t = c.find(q("a:t"))
                text = t.text if t is not None and t.text else ""
                if (c.get("type") or "").lower() == "slidenum":
                    text = str(slide_number)
                runs.append(("r", {**base_rpr, **parse_rpr(c.find(q("a:rPr")))}, text))
        end_rpr = {**base_rpr, **parse_rpr(p.find(q("a:endParaRPr")))}
        has_text = any(kind == "r" and text for kind, _, text in runs)
        first_rpr = next((rpr for kind, rpr, text in runs if kind == "r" and text), None) or (
            runs[0][1] if runs else end_rpr
        )
        para_size = self.size_pt(first_rpr if has_text else end_rpr)
        sizes = [self.size_pt(rpr) for kind, rpr, text in runs if kind == "r" and text]

        css = [f"font-size:{px(para_size)}"]
        algn = pp.get("algn", "l")
        css.append(f"text-align:{_ALIGN.get(algn, 'left')}")
        if algn == "dist":
            css.append("text-align-last:justify")
        mar_l = emu(pp.get("marL", 0))
        indent = emu(pp.get("indent", 0))
        if mar_l:
            css.append(f"padding-left:{px(mar_l)}")
        if indent:
            css.append(f"text-indent:{px(indent)}")
        if pp.get("rtl") in ("1", "true"):
            css.append("direction:rtl")
        ref_size = max(sizes) if sizes else para_size
        line = pp.get("lnSpc", ("pct", 1.0))
        if line[0] == "pct":
            factor = max(0.1, line[1] - self.ctx.spacing_reduction)
            css.append(f"line-height:{num(factor * LINE_FACTOR, 3)}")
        else:
            css.append(f"line-height:{px(line[1])}")
        for key, prop in (("spcBef", "margin-top"), ("spcAft", "margin-bottom")):
            spacing = pp.get(key)
            if spacing:
                value = spacing[1] * ref_size * LINE_FACTOR if spacing[0] == "pct" else spacing[1]
                if value:
                    css.append(f"{prop}:{px(value)}")
        tab = emu(pp.get("defTabSz", 914400))
        if tab > 0:
            css.append(f"tab-size:{px(tab)}")

        bullet = self.bullet_markup(pp, lvl, first_rpr, indent, has_text)
        body = []
        for kind, rpr, text in runs:
            if kind == "br":
                body.append("<br>")
                continue
            if not text:
                continue
            run_css, out_text = self.run_css(rpr, text)
            span = f'<span style="{run_css}">{esc(out_text)}</span>'
            hlink = rpr.get("hlink")
            if hlink is not None:
                span = self.link(hlink, span)
            body.append(span)
        if not has_text or runs and runs[-1][0] == "br":
            body.append("<br>")
        return f'<p style="{";".join(css)}">{bullet}{"".join(body)}</p>'

    def link(self, hlink, inner: str) -> str:
        action = hlink.get("action", "")
        target = None
        rel = self.ctx.part.rels.get(rid(hlink, "id") or "")
        if action.startswith("ppaction://hlinkshowjump"):
            jump = action.partition("jump=")[2]
            return f'<span class="link" data-jump="{esc(jump)}">{inner}</span>'
        if rel is not None:
            if rel.external:
                target = rel.target
            elif rel.kind == "slide":
                for ref in self.ctx.deck.presentation.slides:
                    if ref.partname == rel.target:
                        return f'<span class="link" data-slide-jump="{ref.index}">{inner}</span>'
        if target and target.lower().startswith(("http://", "https://", "mailto:")):
            return f'<span class="link" data-href="{esc(target)}">{inner}</span>'
        return inner

    def bullet_markup(
        self, pp: dict, lvl: int, first_rpr: dict, indent: float, has_text: bool
    ) -> str:
        kind = pp.get("buType", "none")
        if kind == "autonum":
            # Numbering continues across consecutive paragraphs at the same
            # level; deeper levels restart after a shallower paragraph.
            start = pp.get("buStart", 1)
            current = self.counters.get(lvl)
            self.counters[lvl] = start if current is None else current + 1
            for shallower in [k for k in self.counters if k > lvl]:
                del self.counters[shallower]
        else:
            if has_text:
                self.counters.pop(lvl, None)
        if not has_text or kind in ("none", None):
            return ""
        size = self.size_pt(first_rpr)
        bu_size = pp.get("buSz", ("pct", 1.0))
        bsize = size * bu_size[1] if bu_size[0] == "pct" else bu_size[1] * self.ctx.font_scale
        bu_font = pp.get("buFont", "tx")
        latin = self.font_families(first_rpr)[0]
        family_name = latin if bu_font == "tx" else self.ctx.theme.resolve_typeface(bu_font)
        if kind == "char":
            text = pp.get("buChar", "•")
            if fonts.is_symbol_font(family_name):
                text, _ = fonts.translate_symbol(text, family_name, bullet=True)
                family = fonts.family_stack(
                    "DejaVu Sans", "Noto Sans Symbols 2", "Noto Sans Symbols"
                )
            else:
                family = fonts.family_stack(family_name)
        elif kind == "autonum":
            text = autonum_text(pp.get("buAutoNum", "arabicPeriod"), self.counters.get(lvl, 1))
            family = fonts.family_stack(latin if bu_font == "tx" else family_name)
        else:  # picture bullets are drawn as a plain bullet
            text = "•"
            family = fonts.family_stack(latin)
        clr = pp.get("buClr", "tx")
        color = (
            self.run_color(first_rpr)
            if clr == "tx"
            else (self.ctx.colors.resolve(clr) or self.run_color(first_rpr))
        )
        css = [
            f"font-family:{family}",
            f"font-size:{px(bsize)}",
            f"color:{color.css()}",
            "display:inline-block",
            "text-indent:0",
            "font-weight:normal",
            "font-style:normal",
            "text-decoration:none",
        ]
        if first_rpr.get("b") in ("1", "true") and kind == "autonum":
            css[5] = "font-weight:700"
        if indent < 0:
            css.append(f"min-width:{px(-indent)}")
        else:
            css.append("margin-right:0.4em")
        return f'<span class="bu" style="{";".join(css)}">{esc(text)}</span>'


def _is_link(hlink) -> bool:
    action = hlink.get("action", "")
    return bool(rid(hlink, "id")) and not action.startswith("ppaction://hlinkshowjump")


def render_text_frame(
    ctx: RenderContext, tx_body, chain: StyleChain, body: dict, box: TextBox, flip_v: bool = False
) -> str:
    """HTML for a text body positioned inside its shape (shape-local px)."""
    if tx_body is None:
        return ""
    paragraphs = tx_body.findall(q("a:p"))
    if not paragraphs or not any((t.text or "").strip() for t in tx_body.iter(q("a:t"))):
        return ""
    local_chain = chain.with_overlay(tx_body.find(q("a:lstStyle")))
    ctx = ctx.child()
    if body.get("autofit") == "normAutofit":
        try:
            ctx.font_scale = int(body.get("fontScale") or 100000) / 100000.0
        except ValueError:
            ctx.font_scale = 1.0
        try:
            ctx.spacing_reduction = int(body.get("lnSpcReduction") or 0) / 100000.0
        except ValueError:
            ctx.spacing_reduction = 0.0
    else:
        ctx.font_scale = 1.0
        ctx.spacing_reduction = 0.0
    renderer = TextRenderer(ctx, local_chain)
    html_paragraphs = "".join(renderer.render_paragraph(p, ctx.slide_number) for p in paragraphs)

    l_ins = emu(body.get("lIns", 91440))
    t_ins = emu(body.get("tIns", 45720))
    r_ins = emu(body.get("rIns", 91440))
    b_ins = emu(body.get("bIns", 45720))
    vert = body.get("vert", "horz")
    anchor = body.get("anchor", "t")
    wrap = body.get("wrap", "square") != "none"
    x, y, w, h = box.x, box.y, box.w, box.h
    rotation = (attr_int_from(body.get("rot")) or 0) / 60000.0
    vertical = vert in (
        "vert",
        "vert270",
        "eaVert",
        "mongolianVert",
        "wordArtVert",
        "wordArtVertRtl",
    )
    css = [f"left:{px(x)}", f"top:{px(y)}"]
    if vertical:
        # Lay the frame out rotated: swap width/height around the centre.
        cx, cy = x + w / 2, y + h / 2
        w, h = h, w
        css = [f"left:{px(cx - w / 2)}", f"top:{px(cy - h / 2)}"]
        if vert in ("vert", "eaVert"):
            rotation += 90
        elif vert == "vert270":
            rotation += 270
    css += [
        f"width:{px(max(w, 0))}",
        f"height:{px(max(h, 0))}",
        f"padding:{px(t_ins)} {px(r_ins)} {px(b_ins)} {px(l_ins)}",
        f"justify-content:{_ANCHOR.get(anchor, 'flex-start')}",
    ]
    if flip_v:
        rotation += 180
    if rotation % 360:
        css.append(f"transform:rotate({num(rotation % 360)}deg)")
    classes = "tx" if wrap else "tx nowrap"
    if body.get("anchorCtr") in ("1", "true"):
        classes += " anchor-ctr"
    inner_css = ""
    try:
        cols = int(body.get("numCol", "1"))
    except ValueError:
        cols = 1
    if cols > 1:
        gap = emu(body.get("spcCol", 0))
        inner_css = f' style="column-count:{cols};column-gap:{px(gap)};height:100%"'
    if vert in ("wordArtVert", "wordArtVertRtl"):
        inner_css = ' style="writing-mode:vertical-rl;text-orientation:upright"'
    return f'<div class="{classes}" style="{";".join(css)}"><div class="tx-in"{inner_css}>{html_paragraphs}</div></div>'


def attr_int_from(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
