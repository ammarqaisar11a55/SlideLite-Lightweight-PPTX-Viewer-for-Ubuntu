"""SmartArt diagrams.

PowerPoint stores a pre-rendered "drawing" part next to the diagram data
(``dsp:drawing``); it holds ordinary shapes with absolute geometry, so the
diagram is reproduced exactly as PowerPoint laid it out.  When the drawing is
missing (files from some other generators) the diagram text is shown as a
simple list instead.
"""

from __future__ import annotations

import copy

from slidelite.presentation.xmlsafe import NS, local, q, rid
from slidelite.render.markup import esc, num

DSP = NS["dsp"]
DGM = NS["dgm"]
P = NS["p"]
RT_DRAWING = "http://schemas.microsoft.com/office/2007/relationships/diagramDrawing"


class PartView:
    """A part (chart, diagram drawing) that is not a slide/layout/master but
    needs its own relationships and inherits theme/master from ``owner``."""

    kind = "view"

    def __init__(self, deck, partname: str, root, owner) -> None:
        self.deck = deck
        self.partname = partname
        self.root = root
        self.rels = deck.package.rels(partname)
        self.owner = owner
        self.parent = getattr(owner, "parent", None)

    @property
    def master(self):
        return self.owner.master

    @property
    def theme(self):
        return self.owner.theme

    @property
    def clr_map(self):
        return self.owner.clr_map

    def inherited_placeholder(self, _sp):
        return None


def _to_presentation_ns(el):
    """Copy a dsp:* subtree, renaming the dsp namespace to p (same schema)."""
    clone = copy.deepcopy(el)
    for node in clone.iter():
        if node.tag.startswith(f"{{{DSP}}}"):
            node.tag = f"{{{P}}}" + node.tag[len(DSP) + 2 :]
    return clone


def _drawing_part(ctx, graphic_data) -> str | None:
    rel_ids = graphic_data.find(f"{{{DGM}}}relIds")
    data_part = ctx.related_part(rid(rel_ids, "dm")) if rel_ids is not None else None
    # Preferred: the data model names the drawing relationship explicitly.
    if data_part is not None:
        try:
            data = ctx.deck.package.xml(data_part)
        except Exception:
            data = None
        if data is not None:
            for ext in data.iter():
                if local(ext.tag) == "dataModelExt" and ext.get("relId"):
                    target = ctx.related_part(ext.get("relId"))
                    if target:
                        return target
    for rel in ctx.part.rels.values():
        if rel.type == RT_DRAWING and not rel.external and ctx.deck.package.has(rel.target):
            return rel.target
    return None


def render_diagram(renderer, ctx, graphic_data, w: float, h: float) -> str:
    from slidelite.render.shapes import Frame

    drawing = _drawing_part(ctx, graphic_data)
    if drawing is None:
        return _text_fallback(ctx, graphic_data, w, h)
    try:
        root = ctx.deck.parse(drawing)
    except Exception as exc:
        ctx.warn(f"diagram drawing {drawing}: {exc}")
        return _text_fallback(ctx, graphic_data, w, h)
    tree = root.find(f"{{{DSP}}}spTree")
    if tree is None:
        return _text_fallback(ctx, graphic_data, w, h)
    view_ctx = ctx.for_part(PartView(ctx.deck, drawing, root, ctx.part))
    converted = _to_presentation_ns(tree)
    _split_text_frames(converted)
    frame = Frame(w=w, h=h)
    inner = renderer.render_tree(converted, view_ctx, frame)
    return f'<div class="dgm-tree" style="width:{num(w)}px;height:{num(h)}px">{inner}</div>'


def _split_text_frames(tree) -> None:
    """dsp shapes may carry a separate text transform (dsp:txXfrm): move the
    text into its own text-only shape at that position."""
    import xml.etree.ElementTree as ET

    targets = [(parent, sp) for parent in tree.iter() for sp in parent if local(sp.tag) == "sp"]
    for parent, sp in targets:
        if True:  # keeps the block below at a stable indentation
            tx_xfrm = sp.find(q("p:txXfrm"))
            tx_body = sp.find(q("p:txBody"))
            if tx_xfrm is None or tx_body is None:
                continue
            sp.remove(tx_xfrm)
            sp.remove(tx_body)
            text_sp = copy.deepcopy(sp)
            for child in list(text_sp):
                text_sp.remove(child)
            nv = sp.find(q("p:nvSpPr"))
            if nv is not None:
                text_sp.append(copy.deepcopy(nv))
            sp_pr = sp.find(q("p:spPr"))
            new_pr = copy.deepcopy(sp_pr) if sp_pr is not None else None
            if new_pr is not None:
                for child in list(new_pr):
                    new_pr.remove(child)
            else:
                new_pr = ET.Element(q("p:spPr"))
            xfrm = copy.deepcopy(tx_xfrm)
            xfrm.tag = q("a:xfrm")
            new_pr.append(xfrm)
            geom = ET.SubElement(new_pr, q("a:prstGeom"), {"prst": "rect"})
            ET.SubElement(geom, q("a:avLst"))
            ET.SubElement(new_pr, q("a:noFill"))
            # The text overlay must not repeat the shape's outline or effects.
            ET.SubElement(ET.SubElement(new_pr, q("a:ln")), q("a:noFill"))
            ET.SubElement(new_pr, q("a:effectLst"))
            text_sp.append(new_pr)
            style = sp.find(q("p:style"))
            if style is not None:
                text_sp.append(copy.deepcopy(style))
            text_sp.append(tx_body)
            parent.insert(list(parent).index(sp) + 1, text_sp)


def _text_fallback(ctx, graphic_data, w, h) -> str:
    rel_ids = graphic_data.find(f"{{{DGM}}}relIds")
    data_part = ctx.related_part(rid(rel_ids, "dm")) if rel_ids is not None else None
    texts: list[str] = []
    if data_part is not None:
        try:
            data = ctx.deck.package.xml(data_part)
            for pt in data.iter(f"{{{DGM}}}pt"):
                if pt.get("type", "node") in ("node", None):
                    text = "".join(t.text or "" for t in pt.iter(q("a:t"))).strip()
                    if text:
                        texts.append(text)
        except Exception:
            texts = []
    if not texts:
        return ""
    size = max(8.0, min(20.0, h / (len(texts) * 1.6 + 1)))
    items = "".join(f"<li>{esc(t)}</li>" for t in texts[:40])
    return (
        f'<div class="dgm-fallback" style="width:{num(w)}px;height:{num(h)}px;font-size:{num(size)}px">'
        f"<ul>{items}</ul></div>"
    )
