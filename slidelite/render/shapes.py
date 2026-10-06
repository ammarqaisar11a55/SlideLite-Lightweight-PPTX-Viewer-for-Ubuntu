"""Shape tree rendering: sp, cxnSp, pic, grpSp, graphicFrame."""

from __future__ import annotations

import traceback
from dataclasses import dataclass

from slidelite.presentation import geometry
from slidelite.presentation.parts import Part, placeholder_info
from slidelite.presentation.xmlsafe import attr_bool, attr_int, local, q, rid
from slidelite.render import paint
from slidelite.render.context import EMU_PER_PX, RenderContext
from slidelite.render.markup import esc, num, px
from slidelite.render.text import StyleChain, TextBox, merge_body_pr, parse_rpr, render_text_frame

A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


@dataclass
class Xfrm:
    x: float = 0.0
    y: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    rot: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    chx: float = 0.0
    chy: float = 0.0
    chcx: float = 0.0
    chcy: float = 0.0


def read_xfrm(el) -> Xfrm | None:
    if el is None:
        return None
    off = el.find(q("a:off"))
    ext = el.find(q("a:ext"))
    if off is None and ext is None:
        return None
    xf = Xfrm(
        x=float(attr_int(off, "x", 0) or 0),
        y=float(attr_int(off, "y", 0) or 0),
        cx=float(attr_int(ext, "cx", 0) or 0),
        cy=float(attr_int(ext, "cy", 0) or 0),
        rot=(attr_int(el, "rot", 0) or 0) / 60000.0,
        flip_h=attr_bool(el, "flipH"),
        flip_v=attr_bool(el, "flipV"),
    )
    ch_off = el.find(q("a:chOff"))
    ch_ext = el.find(q("a:chExt"))
    if ch_off is not None or ch_ext is not None:
        xf.chx = float(attr_int(ch_off, "x", 0) or 0)
        xf.chy = float(attr_int(ch_off, "y", 0) or 0)
        xf.chcx = float(attr_int(ch_ext, "cx", 0) or 0)
        xf.chcy = float(attr_int(ch_ext, "cy", 0) or 0)
    return xf


@dataclass
class Frame:
    """Maps child EMU coordinates into a container's local px space."""

    chx: float = 0.0
    chy: float = 0.0
    sx: float = 1 / EMU_PER_PX
    sy: float = 1 / EMU_PER_PX
    w: float = 0.0
    h: float = 0.0
    flip_h: bool = False
    flip_v: bool = False

    def place(self, xf: Xfrm) -> tuple[float, float, float, float, float, bool, bool]:
        x = (xf.x - self.chx) * self.sx
        y = (xf.y - self.chy) * self.sy
        w = xf.cx * self.sx
        h = xf.cy * self.sy
        rot, fh, fv = xf.rot, xf.flip_h, xf.flip_v
        if self.flip_h:
            x = self.w - x - w
            fh = not fh
            rot = -rot
        if self.flip_v:
            y = self.h - y - h
            fv = not fv
            rot = -rot
        return x, y, w, h, rot, fh, fv


def _find(el, *names):
    for name in names:
        el = el.find(q(name)) if el is not None else None
    return el


class ShapeRenderer:
    def __init__(self, ctx: RenderContext) -> None:
        self.ctx = ctx

    # -- tree ----------------------------------------------------------------------
    def render_tree(
        self, tree, ctx: RenderContext, frame: Frame, skip_placeholders: bool = False
    ) -> str:
        if tree is None:
            return ""
        out = []
        for el in tree:
            name = local(el.tag)
            if name not in ("sp", "cxnSp", "pic", "grpSp", "graphicFrame"):
                continue
            if skip_placeholders and placeholder_info(el) is not None:
                continue
            try:
                out.append(self.render_element(el, ctx, frame))
            except Exception as exc:  # one broken shape never breaks the slide
                ctx.warn(f"shape skipped on {ctx.part.partname}: {type(exc).__name__}: {exc}")
                if __debug__ and ctx.deck.package.path is None:
                    traceback.print_exc()
        return "".join(out)

    def render_element(self, el, ctx: RenderContext, frame: Frame) -> str:
        name = local(el.tag)
        if name in ("sp", "cxnSp"):
            return self.render_shape(el, ctx, frame)
        if name == "pic":
            return self.render_picture(el, ctx, frame)
        if name == "grpSp":
            return self.render_group(el, ctx, frame)
        if name == "graphicFrame":
            return self.render_graphic_frame(el, ctx, frame)
        return ""

    # -- helpers ---------------------------------------------------------------------
    @staticmethod
    def nv_props(el):
        for c in el:
            if local(c.tag).startswith("nv"):
                return c.find(q("p:cNvPr")), c.find(q("p:nvPr"))
        return None, None

    def placeholder_chain(self, el, part: Part) -> list:
        """Inherited placeholder elements, lowest priority (master) first."""
        chain = []
        current_el, current_part = el, part
        while current_part is not None and placeholder_info(current_el) is not None:
            parent_el = current_part.inherited_placeholder(current_el)
            if parent_el is None:
                break
            chain.insert(0, (parent_el, current_part.parent))
            current_el, current_part = parent_el, current_part.parent
        return chain

    @staticmethod
    def sp_pr(el):
        for c in el:
            if local(c.tag) in ("spPr", "grpSpPr"):
                return c
        return None

    def box_css(self, x, y, w, h, rot, extra: dict[str, str] | None = None) -> str:
        css = [f"left:{px(x)}", f"top:{px(y)}", f"width:{px(w)}", f"height:{px(h)}"]
        if rot % 360:
            css.append(f"transform:rotate({num(rot % 360, 3)}deg)")
        for k, v in (extra or {}).items():
            css.append(f"{k}:{v}")
        return ";".join(css)

    def link_attrs(self, ctx: RenderContext, c_nv_pr) -> str:
        if c_nv_pr is None:
            return ""
        attrs = ""
        spid = c_nv_pr.get("id")
        # Shape ids repeat across slide/layout/master parts; only the slide's
        # own shapes are animation targets.
        if spid and ctx.part is ctx.slide:
            attrs += f' data-spid="{esc(spid)}"'
        hlink = c_nv_pr.find(q("a:hlinkClick"))
        if hlink is not None:
            action = hlink.get("action", "")
            rel = ctx.part.rels.get(rid(hlink, "id") or "")
            if action.startswith("ppaction://hlinkshowjump"):
                attrs += f' data-jump="{esc(action.partition("jump=")[2])}"'
            elif (
                rel is not None
                and rel.external
                and rel.target.lower().startswith(("http://", "https://", "mailto:"))
            ):
                attrs += f' data-href="{esc(rel.target)}"'
            elif rel is not None and rel.kind == "slide":
                for ref in ctx.deck.presentation.slides:
                    if ref.partname == rel.target:
                        attrs += f' data-slide-jump="{ref.index}"'
        descr = c_nv_pr.get("descr") or ""
        if descr:
            attrs += f' aria-label="{esc(descr[:300])}"'
        return attrs

    def geometry_for(self, sp_pr, chain_sp_prs, w: float, h: float) -> geometry.Geometry:
        for candidate in [sp_pr, *reversed(chain_sp_prs)]:
            if candidate is None:
                continue
            prst = candidate.find(q("a:prstGeom"))
            if prst is not None:
                return geometry.preset(
                    prst.get("prst", "rect"),
                    w * EMU_PER_PX,
                    h * EMU_PER_PX,
                    geometry.adjustments_from(prst.find(q("a:avLst"))),
                    1 / EMU_PER_PX,
                )
            cust = candidate.find(q("a:custGeom"))
            if cust is not None:
                return geometry.custom(cust, w * EMU_PER_PX, h * EMU_PER_PX, 1 / EMU_PER_PX)
        return geometry.preset("rect", w * EMU_PER_PX, h * EMU_PER_PX, None, 1 / EMU_PER_PX)

    def resolve_paints(self, ctx, el, sp_pr, chain_sp_prs, defs, w, h, is_line_shape=False):
        style = el.find(q("p:style"))
        # fill
        fill_el = None
        placeholder_color = None
        for candidate in [sp_pr, *reversed(chain_sp_prs)]:
            fill_el = paint.find_fill(candidate)
            if fill_el is not None:
                break
        if fill_el is None and style is not None and not is_line_shape:
            fill_el, placeholder_color = paint.theme_fill(ctx, style.find(q("a:fillRef")))
        fill = paint.resolve_fill(ctx, fill_el, defs, w, h, placeholder_color)
        # line
        ln_theme, ln_color = (None, None)
        if style is not None:
            ln_theme, ln_color = paint.theme_line(ctx, style.find(q("a:lnRef")))
        lns = [ln_theme] + [c.find(q("a:ln")) for c in chain_sp_prs if c is not None]
        lns.append(sp_pr.find(q("a:ln")) if sp_pr is not None else None)
        merged = paint.merge_lines(*lns)
        line = paint.resolve_line(ctx, merged, defs, w, h, ln_color)
        # effects
        effect_lst = None
        effect_color = None
        found_effects = False
        for candidate in [sp_pr, *reversed(chain_sp_prs)]:
            if candidate is not None:
                eff = candidate.find(q("a:effectLst"))
                if eff is not None:
                    effect_lst, found_effects = eff, True
                    break
        if not found_effects and style is not None:
            effect_lst, effect_color = paint.theme_effect(ctx, style.find(q("a:effectRef")))
        effects = paint.effect_css(ctx, effect_lst, effect_color)
        return fill, line, effects, placeholder_color

    def geometry_svg(
        self, geom, fill, line, defs, w, h, flip_h, flip_v, ctx, extra_inner=""
    ) -> str:
        markers = (
            paint.marker_defs(ctx, line, defs) if (line.head or line.tail) and line.visible else ""
        )
        body = [extra_inner] if extra_inner else []
        for path in geom.paths:
            d = path.d
            if path.fill != "none" and not fill.is_none:
                body.append(f'<path d="{d}" {fill.attrs()} stroke="none"/>')
                overlay = {
                    "darken": ("#000", 0.4),
                    "darkenLess": ("#000", 0.2),
                    "lighten": ("#fff", 0.4),
                    "lightenLess": ("#fff", 0.2),
                }.get(path.fill)
                if overlay:
                    body.append(
                        f'<path d="{d}" fill="{overlay[0]}" fill-opacity="{overlay[1]}" stroke="none"/>'
                    )
            if path.stroke and line.visible:
                body.append(f'<path d="{d}" fill="none" {paint.line_attrs(line)}{markers}/>')
        if not body:
            return ""
        transform = ""
        if flip_h or flip_v:
            transform = f' style="transform:scale({-1 if flip_h else 1},{-1 if flip_v else 1})"'
        defs_markup = f"<defs>{''.join(defs)}</defs>" if defs else ""
        return (
            f'<svg class="geom" width="{num(max(w, 0.01))}" height="{num(max(h, 0.01))}"{transform}>'
            f"{defs_markup}{''.join(body)}</svg>"
        )

    # -- shapes --------------------------------------------------------------------------
    def render_shape(self, el, ctx: RenderContext, frame: Frame) -> str:
        c_nv_pr, nv_pr = self.nv_props(el)
        if c_nv_pr is not None and attr_bool(c_nv_pr, "hidden"):
            return ""
        sp_pr = self.sp_pr(el)
        chain = self.placeholder_chain(el, ctx.part)
        chain_sp_prs = [self.sp_pr(ph_el) for ph_el, _ in chain]
        xf = read_xfrm(sp_pr.find(q("a:xfrm")) if sp_pr is not None else None)
        for ph_sp_pr in reversed(chain_sp_prs):
            if xf is not None:
                break
            xf = read_xfrm(ph_sp_pr.find(q("a:xfrm")) if ph_sp_pr is not None else None)
        if xf is None:
            return ""
        x, y, w, h, rot, flip_h, flip_v = frame.place(xf)
        geom = self.geometry_for(sp_pr, chain_sp_prs, w, h)
        defs: list[str] = []
        is_line = local(el.tag) == "cxnSp"
        fill, line, effects, _ = self.resolve_paints(
            ctx, el, sp_pr, chain_sp_prs, defs, w, h, is_line
        )
        svg = self.geometry_svg(geom, fill, line, defs, w, h, flip_h, flip_v, ctx)
        text = self.shape_text(el, ctx, chain, geom, w, h, flip_v)
        if not svg and not text:
            return ""
        attrs = self.link_attrs(ctx, c_nv_pr)
        info = placeholder_info(el)
        if info is not None:
            attrs += f' data-ph="{esc(info[0])}"'
        return f'<div class="sp"{attrs} style="{self.box_css(x, y, w, h, rot, effects)}">{svg}{text}</div>'

    def shape_text(self, el, ctx: RenderContext, chain, geom, w, h, flip_v) -> str:
        tx_body = el.find(q("p:txBody"))
        if tx_body is None:
            return ""
        info = placeholder_info(el)
        master = ctx.part.master
        sources = [ctx.deck.default_text_style]
        if master is not None:
            sources.append(master.text_style_for(info[0] if info else None))
        bodies = []
        for ph_el, _part in chain:
            ph_tx = ph_el.find(q("p:txBody"))
            if ph_tx is not None:
                sources.append(ph_tx.find(q("a:lstStyle")))
                bodies.append(ph_tx.find(q("a:bodyPr")))
        bodies.append(tx_body.find(q("a:bodyPr")))
        font_ref = {}
        style = el.find(q("p:style"))
        if style is not None:
            ref = style.find(q("a:fontRef"))
            if ref is not None:
                idx = ref.get("idx", "minor")
                if idx in ("major", "minor"):
                    prefix = "+mj" if idx == "major" else "+mn"
                    font_ref["latin"] = f"{prefix}-lt"
                    font_ref["ea"] = f"{prefix}-ea"
                    font_ref["cs"] = f"{prefix}-cs"
                color_el = next((c for c in ref), None)
                if color_el is not None:
                    import xml.etree.ElementTree as ET

                    solid = ET.Element(q("a:solidFill"))
                    solid.append(color_el)
                    font_ref["fill"] = solid
        chain_styles = StyleChain(sources, font_ref)
        body = merge_body_pr(*bodies)
        rect = geom.text_rect or (0.0, 0.0, 1.0, 1.0)
        box = TextBox(rect[0] * w, rect[1] * h, (rect[2] - rect[0]) * w, (rect[3] - rect[1]) * h)
        return render_text_frame(ctx, tx_body, chain_styles, body, box, flip_v)

    # -- pictures ------------------------------------------------------------------------
    def render_picture(
        self, el, ctx: RenderContext, frame: Frame, xf_override: Xfrm | None = None
    ) -> str:
        c_nv_pr, nv_pr = self.nv_props(el)
        if c_nv_pr is not None and attr_bool(c_nv_pr, "hidden"):
            return ""
        sp_pr = self.sp_pr(el)
        chain = self.placeholder_chain(el, ctx.part)
        chain_sp_prs = [self.sp_pr(ph_el) for ph_el, _ in chain]
        xf = xf_override or read_xfrm(sp_pr.find(q("a:xfrm")) if sp_pr is not None else None)
        for ph_sp_pr in reversed(chain_sp_prs):
            if xf is not None:
                break
            xf = read_xfrm(ph_sp_pr.find(q("a:xfrm")) if ph_sp_pr is not None else None)
        if xf is None:
            return ""
        x, y, w, h, rot, flip_h, flip_v = frame.place(xf)
        geom = self.geometry_for(sp_pr, chain_sp_prs, w, h)
        defs: list[str] = []
        _fill, line, effects, _ = self.resolve_paints(
            ctx, el, sp_pr, chain_sp_prs, defs, w, h, True
        )
        blip_fill = el.find(q("p:blipFill"))
        if blip_fill is None:
            blip_fill = el.find(q("a:blipFill"))
        image = ""
        if blip_fill is not None:
            image = self.image_markup(ctx, blip_fill, geom, defs, w, h)
        svg = self.geometry_svg(geom, paint.NONE, line, defs, w, h, flip_h, flip_v, ctx, image)
        attrs = self.link_attrs(ctx, c_nv_pr)
        media = self.media_markup(ctx, nv_pr, w, h, blip_fill)
        if media:
            attrs += ' data-media="1"'
        return f'<div class="sp pic"{attrs} style="{self.box_css(x, y, w, h, rot, effects)}">{svg}{media}</div>'

    def image_markup(self, ctx, blip_fill, geom, defs, w, h) -> str:
        info = paint.blip_info(ctx, blip_fill)
        if not info.url:
            return (
                f'<rect width="{num(w)}" height="{num(h)}" fill="#e6e6e6"/>'
                f'<path d="M0 0L{num(w)} {num(h)}M{num(w)} 0L0 {num(h)}" stroke="#bdbdbd" stroke-width="1"/>'
            )
        clip_id = ctx.ids("c")
        clip_paths = "".join(f'<path d="{p.d}"/>' for p in geom.paths if p.fill != "none") or (
            f'<rect width="{num(w)}" height="{num(h)}"/>'
        )
        defs.append(f'<clipPath id="{clip_id}">{clip_paths}</clipPath>')
        filt = paint.image_filter(ctx, blip_fill, defs)
        fattr = f' filter="url(#{filt})"' if filt else ""
        opacity = f' opacity="{num(info.opacity, 3)}"' if info.opacity < 0.999 else ""
        if info.tile is not None:
            pattern = paint.resolve_fill(ctx, blip_fill, defs, w, h)
            return f'<rect width="{num(w)}" height="{num(h)}" fill="{pattern.value}" clip-path="url(#{clip_id})"{opacity}/>'
        l, t, r, b = info.crop
        # a:stretch/a:fillRect insets the image inside the frame.
        fill_rect = _find(blip_fill, "a:stretch", "a:fillRect")
        fl = ft = fr = fb = 0.0
        if fill_rect is not None:
            fl, ft, fr, fb = (
                (attr_int(fill_rect, k, 0) or 0) / 100000.0 for k in ("l", "t", "r", "b")
            )
        fx, fy = fl * w, ft * h
        fw, fh = w * (1 - fl - fr), h * (1 - ft - fb)
        iw = fw / max(0.0001, 1 - l - r)
        ih = fh / max(0.0001, 1 - t - b)
        return (
            f'<g clip-path="url(#{clip_id})"{opacity}><image href="{esc(info.url)}" '
            f'x="{num(fx - l * iw)}" y="{num(fy - t * ih)}" width="{num(iw)}" height="{num(ih)}" '
            f'preserveAspectRatio="none"{fattr}/></g>'
        )

    def media_markup(self, ctx, nv_pr, w, h, blip_fill) -> str:
        """Hook for audio/video (see slidelite.render.media)."""
        if nv_pr is None:
            return ""
        from slidelite.render.media import media_markup

        return media_markup(ctx, nv_pr, w, h, blip_fill)

    # -- groups ----------------------------------------------------------------------------
    def render_group(self, el, ctx: RenderContext, frame: Frame) -> str:
        c_nv_pr, _ = self.nv_props(el)
        if c_nv_pr is not None and attr_bool(c_nv_pr, "hidden"):
            return ""
        grp_pr = el.find(q("p:grpSpPr"))
        xf = read_xfrm(grp_pr.find(q("a:xfrm")) if grp_pr is not None else None)
        if xf is None:
            xf = Xfrm()
        x, y, w, h, rot, flip_h, flip_v = frame.place(xf)
        chcx = xf.chcx or xf.cx
        chcy = xf.chcy or xf.cy
        inner = Frame(
            chx=xf.chx if (xf.chcx or xf.chcy) else xf.x,
            chy=xf.chy if (xf.chcx or xf.chcy) else xf.y,
            sx=(w / chcx) if chcx else 1 / EMU_PER_PX,
            sy=(h / chcy) if chcy else 1 / EMU_PER_PX,
            w=w,
            h=h,
            flip_h=flip_h,
            flip_v=flip_v,
        )
        child_ctx = ctx.child()
        group_fill = paint.find_fill(grp_pr)
        if group_fill is not None and local(group_fill.tag) != "grpFill":
            child_ctx.group_fill = group_fill
        effects = paint.effect_css(
            ctx, grp_pr.find(q("a:effectLst")) if grp_pr is not None else None
        )
        content = self.render_tree(el, child_ctx, inner)
        if not content:
            return ""
        attrs = self.link_attrs(ctx, c_nv_pr)
        return f'<div class="grp"{attrs} style="{self.box_css(x, y, w, h, rot, effects)}">{content}</div>'

    # -- graphic frames -------------------------------------------------------------------------
    def render_graphic_frame(self, el, ctx: RenderContext, frame: Frame) -> str:
        c_nv_pr, nv_pr = self.nv_props(el)
        if c_nv_pr is not None and attr_bool(c_nv_pr, "hidden"):
            return ""
        xf = read_xfrm(el.find(q("p:xfrm")))
        if xf is None:
            chain = self.placeholder_chain(el, ctx.part)
            for ph_el, _ in reversed(chain):
                xf = read_xfrm(ph_el.find(q("p:xfrm"))) or read_xfrm(
                    _find(self.sp_pr(ph_el), "a:xfrm")
                )
                if xf is not None:
                    break
        if xf is None:
            return ""
        data = _find(el, "a:graphic", "a:graphicData")
        if data is None:
            return ""
        uri = data.get("uri", "")
        x, y, w, h, rot, flip_h, flip_v = frame.place(xf)
        attrs = self.link_attrs(ctx, c_nv_pr)
        if uri.endswith("/table"):
            from slidelite.render.table import render_table

            inner = render_table(ctx, data.find(q("a:tbl")), w, h)
            kind = "tbl"
        elif uri.endswith("/chart"):
            from slidelite.render.chart import render_chart_frame

            inner = render_chart_frame(ctx, data, w, h)
            kind = "chart"
        elif uri.endswith("/diagram"):
            from slidelite.render.diagram import render_diagram

            inner = render_diagram(self, ctx, data, w, h)
            kind = "dgm"
        else:
            # OLE objects and other embedded content: draw their preview picture.
            pic = next((p for p in data.iter(q("p:pic"))), None)
            if pic is None:
                return ""
            ole_xf = Xfrm(
                x=xf.x, y=xf.y, cx=xf.cx, cy=xf.cy, rot=xf.rot, flip_h=xf.flip_h, flip_v=xf.flip_v
            )
            return self.render_picture(pic, ctx, frame, ole_xf)
        if not inner:
            return ""
        return f'<div class="sp gf {kind}"{attrs} style="{self.box_css(x, y, w, h, rot)}">{inner}</div>'


def run_props(el) -> dict:
    return parse_rpr(el)
