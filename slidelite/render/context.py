"""Per-slide rendering context."""

from __future__ import annotations

import copy

from slidelite.presentation.color import ColorResolver
from slidelite.presentation.parts import Deck, Part, Slide
from slidelite.presentation.xmlsafe import rid
from slidelite.render.markup import IdGen

EMU_PER_PX = 12700.0  # 1 CSS px == 1 pt

# Formats WebKitGTK decodes natively.
WEB_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/bmp",
    "image/x-bmp",
    "image/webp",
    "image/svg+xml",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/avif",
}
_EXT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "jpe": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "emf": "image/x-emf",
    "wmf": "image/x-wmf",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "wdp": "image/vnd.ms-photo",
    "jxr": "image/vnd.ms-photo",
    "ico": "image/x-icon",
}


def emu(value) -> float:
    try:
        return float(value) / EMU_PER_PX
    except (TypeError, ValueError):
        return 0.0


class RenderContext:
    def __init__(
        self,
        deck: Deck,
        slide: Slide,
        base_url: str = "",
        ids: IdGen | None = None,
        thumbnail: bool = False,
    ) -> None:
        self.deck = deck
        self.slide = slide
        self.part: Part = slide
        self.theme = slide.theme
        self.colors = ColorResolver(self.theme.colors, slide.clr_map)
        self.base_url = base_url
        self.ids = ids or IdGen()
        self.group_fill = None
        self.thumbnail = thumbnail
        self.media: list[dict] = []
        self.font_scale = 1.0
        self.spacing_reduction = 0.0

    @property
    def slide_number(self) -> int:
        return self.slide.index + 1 + self.first_slide_offset

    @property
    def first_slide_offset(self) -> int:
        root = self.deck.presentation.root
        try:
            return int(root.get("firstSlideNum", "1")) - 1 if root is not None else 0
        except ValueError:
            return 0

    def for_part(self, part: Part) -> RenderContext:
        ctx = copy.copy(self)
        ctx.part = part
        return ctx

    def child(self) -> RenderContext:
        return copy.copy(self)

    def warn(self, message: str) -> None:
        self.deck.warn(message)

    # -- media --------------------------------------------------------------------
    def related_part(self, rel_id: str | None) -> str | None:
        if not rel_id:
            return None
        rel = self.part.rels.get(rel_id)
        if rel is None or rel.external or not self.deck.package.has(rel.target):
            return None
        return rel.target

    def media_type(self, partname: str) -> str:
        ct = self.deck.package.content_type(partname).lower()
        if ct and ct != "application/octet-stream":
            return ct
        return _EXT_TYPES.get(partname.rsplit(".", 1)[-1].lower(), ct)

    def part_url(self, partname: str) -> str:
        return f"{self.base_url}part{partname}"

    def image_url(self, blip) -> str | None:
        """URL for an ``a:blip`` (preferring an SVG alternative), or None."""
        if blip is None:
            return None
        for ext in blip.iter():
            if ext.tag.endswith("}svgBlip"):
                svg = self.related_part(rid(ext, "embed"))
                if svg:
                    return self.part_url(svg)
        partname = self.related_part(rid(blip, "embed"))
        if partname is None:
            return None
        kind = self.media_type(partname)
        if kind in WEB_IMAGE_TYPES:
            return self.part_url(partname)
        # Formats WebKit cannot display (EMF/WMF/TIFF) go through the converter.
        return f"{self.base_url}image{partname}"
