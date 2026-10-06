"""Compose a slide: background, master and layout graphics, slide shapes."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field

from slidelite.presentation.parts import Deck, Layout, Master
from slidelite.presentation.timing import slide_timing
from slidelite.presentation.xmlsafe import q
from slidelite.render import paint
from slidelite.render.context import RenderContext
from slidelite.render.markup import IdGen, esc, num, px
from slidelite.render.shapes import Frame, ShapeRenderer


@dataclass
class RenderedSlide:
    html: str
    index: int
    warnings: list[str] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)
    failed: bool = False


def render_background(ctx: RenderContext, w: float, h: float) -> str:
    slide = ctx.slide
    part = slide
    bg = None
    while part is not None:
        bg = part.background
        if bg is not None:
            break
        part = part.parent
    defs: list[str] = []
    fill = paint.Paint("#ffffff", 1.0)
    effects = ""
    if bg is not None:
        bg_ctx = ctx.for_part(part)
        bg_pr = bg.find(q("p:bgPr"))
        bg_ref = bg.find(q("p:bgRef"))
        if bg_pr is not None:
            fill_el = paint.find_fill(bg_pr)
            if fill_el is not None:
                fill = paint.resolve_fill(bg_ctx, fill_el, defs, w, h)
        elif bg_ref is not None:
            fill_el, color = paint.theme_fill(bg_ctx, bg_ref)
            if fill_el is not None:
                fill = paint.resolve_fill(bg_ctx, fill_el, defs, w, h, color)
            elif color is not None:
                fill = paint.Paint(color.rgb_hex(), color.a, color)
    defs_markup = f"<defs>{''.join(defs)}</defs>" if defs else ""
    base = (
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        if fill.opacity < 0.999 or fill.is_none
        else ""
    )
    return (
        f'<svg class="bg" width="{num(w)}" height="{num(h)}"{effects}>{defs_markup}{base}'
        f'<rect width="{num(w)}" height="{num(h)}" {fill.attrs()}/></svg>'
    )


def render_slide(
    deck: Deck, index: int, base_url: str = "", thumbnail: bool = False
) -> RenderedSlide:
    pres = deck.presentation
    w, h = pres.width_pt, pres.height_pt
    try:
        slide = deck.slide(index)
    except Exception as exc:
        return RenderedSlide(_error_slide(index, w, h, exc), index, [str(exc)], failed=True)
    ctx = RenderContext(deck, slide, base_url, IdGen(f"s{index}-"), thumbnail)
    renderer = ShapeRenderer(ctx)
    root_frame = Frame(w=w, h=h)
    layers = []
    try:
        layers.append(render_background(ctx, w, h))
    except Exception as exc:
        deck.warn(f"background of slide {index + 1}: {exc}")
    layout = slide.layout
    master = slide.master
    show = slide.show_master_shapes
    if (
        show
        and isinstance(master, Master)
        and master is not slide
        and (layout is None or layout.show_master_shapes)
    ):
        layers.append(
            renderer.render_tree(
                master.sp_tree, ctx.for_part(master), root_frame, skip_placeholders=True
            )
        )
    if show and isinstance(layout, Layout):
        layers.append(
            renderer.render_tree(
                layout.sp_tree, ctx.for_part(layout), root_frame, skip_placeholders=True
            )
        )
    try:
        layers.append(renderer.render_tree(slide.sp_tree, ctx, root_frame))
    except Exception as exc:  # pragma: no cover - render_tree already isolates shapes
        traceback.print_exc()
        deck.warn(f"slide {index + 1}: {exc}")
    try:
        timing = slide_timing(slide)
    except Exception as exc:  # timing problems must never hide the slide
        deck.warn(f"timing of slide {index + 1}: {exc}")
        timing = {}
    timing_attr = (
        f' data-timing="{esc(json.dumps(timing, separators=(",", ":")))}"' if timing else ""
    )
    hidden = ' data-hidden="1"' if slide.hidden else ""
    html = (
        f'<div class="slide" data-slide="{index + 1}"{hidden}{timing_attr} '
        f'style="width:{px(w)};height:{px(h)}">'
        f"{''.join(layers)}</div>"
    )
    return RenderedSlide(html, index, list(deck.warnings), ctx.media)


def _error_slide(index: int, w: float, h: float, exc: Exception) -> str:
    return (
        f'<div class="slide slide-error" data-slide="{index + 1}" style="width:{px(w)};height:{px(h)}">'
        f'<div class="slide-error-msg"><strong>This slide could not be displayed</strong>'
        f"<span>{esc(type(exc).__name__)}: {esc(str(exc)[:200])}</span></div></div>"
    )
