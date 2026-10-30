"""Writing the model as an OPC package the normal engine can open.

Binary PowerPoint resolves styles and colours through the page's master, so
values are flattened here: runs carry their full formatting and colours are
literal.  One master part is written per (master, colour scheme) pair, because
a master's artwork is recoloured by the scheme of the slide showing it.
"""

from __future__ import annotations

import datetime
import io
import math
import struct
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from slidelite.legacy import geometry, pictures, text, timeline
from slidelite.legacy.deck import DEFAULT_SCHEME, Deck, Page, Transition
from slidelite.legacy.officeart import (
    FILL_BOOLS,
    FILLED,
    FIT_SHAPE_TO_TEXT,
    LINE_BOOLS,
    LINE_ON,
    SHADOW_BOOLS,
    SHADOW_ON,
    SP_BACKGROUND,
    SP_DELETED,
    TEXT_BOOLS,
    Props,
    Shape,
    array_items,
)
from slidelite.legacy.text import TextBlock
from slidelite.presentation import fonts

EMU_PER_UNIT = 914400 / 576
FIXED = 65536.0

_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PML = "application/vnd.openxmlformats-officedocument.presentationml"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# Property ids.
ROTATION = 0x0004
TEXT_LEFT, TEXT_TOP, TEXT_RIGHT, TEXT_BOTTOM = 0x0081, 0x0082, 0x0083, 0x0084
TEXT_WRAP, TEXT_ANCHOR, TEXT_FLOW = 0x0085, 0x0087, 0x0088
GTEXT, GTEXT_FONT = 0x00C0, 0x00C5
CROP_TOP, CROP_BOTTOM, CROP_LEFT, CROP_RIGHT = 0x0100, 0x0101, 0x0102, 0x0103
PICTURE = 0x0104
GEO_LEFT, GEO_TOP, GEO_RIGHT, GEO_BOTTOM = 0x0140, 0x0141, 0x0142, 0x0143
SHAPE_PATH, VERTICES, SEGMENTS, ADJUST_1 = 0x0144, 0x0145, 0x0146, 0x0147
FILL_TYPE, FILL_COLOR, FILL_OPACITY, FILL_BACK, FILL_BACK_OPACITY = (
    0x0180,
    0x0181,
    0x0182,
    0x0183,
    0x0184,
)
FILL_BLIP, FILL_ANGLE, FILL_FOCUS, FILL_SHADES = 0x0186, 0x018B, 0x018C, 0x0197
FILL_TO_LEFT, FILL_TO_TOP, FILL_TO_RIGHT, FILL_TO_BOTTOM = 0x018D, 0x018E, 0x018F, 0x0190
LINE_COLOR, LINE_OPACITY, LINE_WIDTH, LINE_STYLE, LINE_DASH = (
    0x01C0,
    0x01C1,
    0x01CB,
    0x01CD,
    0x01CE,
)
LINE_START, LINE_END = 0x01D0, 0x01D1
LINE_START_W, LINE_START_L, LINE_END_W, LINE_END_L = 0x01D2, 0x01D3, 0x01D4, 0x01D5
SHADOW_COLOR, SHADOW_OPACITY, SHADOW_X, SHADOW_Y = 0x0201, 0x0204, 0x0205, 0x0206
NAME, DESCRIPTION = 0x0380, 0x0381

_ALIGN = {0: "l", 1: "ctr", 2: "r", 3: "just", 4: "dist", 5: "thaiDist", 6: "justLow"}
_ANCHOR = {0: "t", 1: "ctr", 2: "b", 3: "t", 4: "ctr", 5: "b", 6: "t", 7: "b", 8: "t", 9: "b"}
_FLOW = {1: "vert", 2: "vert270", 3: "vert", 5: "vert"}
_DASH = {
    1: "sysDash", 2: "sysDot", 3: "sysDashDot", 4: "sysDashDotDot", 5: "dot", 6: "dash",
    7: "lgDash", 8: "dashDot", 9: "lgDashDot", 10: "lgDashDotDot",
}  # fmt: skip
_COMPOUND = {1: "dbl", 2: "thickThin", 3: "thinThick", 4: "tri"}
_ARROW = {1: "triangle", 2: "stealth", 3: "diamond", 4: "oval", 5: "arrow"}
_ARROW_SIZE = {0: "sm", 1: "med", 2: "lg"}
_NUMBERING = {
    0: "alphaLcPeriod", 1: "alphaUcPeriod", 2: "arabicParenR", 3: "arabicPeriod",
    4: "romanLcParenBoth", 5: "romanLcParenR", 6: "romanLcPeriod", 7: "romanUcPeriod",
    8: "alphaLcParenBoth", 9: "alphaLcParenR", 10: "alphaUcParenBoth", 11: "alphaUcParenR",
    12: "arabicParenBoth", 13: "arabicPlain", 14: "romanUcParenBoth", 15: "romanUcParenR",
}  # fmt: skip
_DIRECTION = {0: "l", 1: "u", 2: "r", 3: "d", 4: "lu", 5: "ru", 6: "ld", 7: "rd"}
_PLACEHOLDER_TITLES = {13: "title", 15: "ctrTitle"}
_MASTER_PLACEHOLDER = {13: 1, 17: 1, 15: 1, 14: 2, 16: 2, 18: 2, 7: 7, 8: 8, 9: 9, 10: 10}


def _emu(value: float) -> int:
    return round(value * EMU_PER_UNIT)


def _clean(value: str) -> str:
    """XML-safe text: only tabs survive from the C0 range."""
    return "".join(
        c for c in value if c == "\t" or (" " <= c < "￾" and not "\ud800" <= c <= "\udfff")
    )


def _is_symbol(char: str) -> bool:
    """Symbol fonts keep their glyphs in this private-use range."""
    return "\uf000" <= char <= "\uf0ff"


def _clamp8(value: float) -> int:
    return max(0, min(255, round(value)))


class Palette:
    """Turns stored colours into hex for one page's scheme."""

    def __init__(self, scheme: tuple[str, ...], props: Props | None = None) -> None:
        self.scheme = scheme
        self.props = props

    def text(self, value: int) -> str:
        """A ColorIndexStruct: red, green, blue, then a scheme index or 0xFE for RGB."""
        index = value >> 24
        if index < 8:
            return self.scheme[index]
        return f"{value & 0xFF:02X}{(value >> 8) & 0xFF:02X}{(value >> 16) & 0xFF:02X}"

    def art(self, value: int, fallback: str = "000000") -> str:
        """An OfficeArtCOLORREF, including colours derived from the shape's others."""
        flags = value >> 24
        red = value & 0xFF
        if flags & 0x08 and red < 8:
            return self.scheme[red]
        if flags & 0x10:
            return self._derived(value, fallback)
        return f"{red:02X}{(value >> 8) & 0xFF:02X}{(value >> 16) & 0xFF:02X}"

    def _derived(self, value: int, fallback: str) -> str:
        index = value & 0xFF
        mode = (value >> 8) & 0xF
        amount = (value >> 16) & 0xFF
        source = {0xF0: FILL_COLOR, 0xF1: LINE_COLOR, 0xF2: LINE_COLOR, 0xF3: SHADOW_COLOR}.get(
            index, FILL_BACK if index == 0xF5 else None
        )
        base = fallback
        if source is not None and self.props is not None:
            stored = self.props.get(source)
            if stored is not None and not (stored >> 24) & 0x10:
                base = self.art(stored, fallback)
        channels = [int(base[i : i + 2], 16) for i in (0, 2, 4)]
        if mode == 1:
            channels = [_clamp8(c * amount / 255) for c in channels]
        elif mode == 2:
            channels = [_clamp8(255 - (255 - c) * amount / 255) for c in channels]
        return "".join(f"{c:02X}" for c in channels)


def _solid(color: str, opacity: int | None = None) -> str:
    alpha = ""
    if opacity is not None and opacity < 0x10000:
        alpha = f'<a:alpha val="{max(0, round(opacity / FIXED * 100000))}"/>'
    if alpha:
        return f'<a:solidFill><a:srgbClr val="{color}">{alpha}</a:srgbClr></a:solidFill>'
    return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'


@dataclass
class Part:
    """An XML part being written, with its outgoing relationships."""

    name: str
    rels: list[tuple[str, str, bool]] = field(default_factory=list)  # (type, target, external)

    def relate(self, kind: str, target: str, external: bool = False) -> str:
        entry = (kind, target, external)
        if entry not in self.rels:
            self.rels.append(entry)
        return f"rId{self.rels.index(entry) + 1}"

    def rels_xml(self) -> str:
        items = "".join(
            f'<Relationship Id="rId{i}" Type="{_RT}/{kind}" Target={quoteattr(target)}'
            + (' TargetMode="External"' if external else "")
            + "/>"
            for i, (kind, target, external) in enumerate(self.rels, 1)
        )
        return (
            f"{_XML}<Relationships xmlns="
            f'"http://schemas.openxmlformats.org/package/2006/relationships">{items}</Relationships>'
        )


class Writer:
    def __init__(self, deck: Deck) -> None:
        self.deck = deck
        self.issues = list(deck.issues)
        self.files: dict[str, bytes] = {}
        self.media: dict[int, str | None] = {}
        self.overrides: dict[str, str] = {}
        self.master_parts: dict[tuple[int, tuple[str, ...]], Part] = {}
        self.slide_size = (
            round(deck.width * EMU_PER_UNIT) or 9144000,
            round(deck.height * EMU_PER_UNIT) or 6858000,
        )
        self._style_cache: dict[tuple, tuple[dict, dict]] = {}

    # -- package --------------------------------------------------------------
    def write(self) -> bytes:
        presentation = Part("ppt/presentation.xml")
        slide_ids = []
        for number, page in enumerate(self.deck.slides, 1):
            part = self._slide(page, number)
            rel = presentation.relate("slide", f"slides/slide{number}.xml")
            slide_ids.append(f'<p:sldId id="{255 + number}" r:id="{rel}"/>')
        master_ids = []
        for i, part in enumerate(self.master_parts.values()):
            rel = presentation.relate("slideMaster", part.name.removeprefix("ppt/"))
            master_ids.append(f'<p:sldMasterId id="{2147483648 + i * 16}" r:id="{rel}"/>')
        width, height = self.slide_size
        self._add(
            presentation,
            f"{_XML}<p:presentation {_NS}><p:sldMasterIdLst>{''.join(master_ids)}"
            f"</p:sldMasterIdLst><p:sldIdLst>{''.join(slide_ids)}</p:sldIdLst>"
            f'<p:sldSz cx="{width}" cy="{height}"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>",
            f"{_PML}.presentation.main+xml",
        )
        return self._zip()

    def _add(self, part: Part, xml: str, content_type: str) -> None:
        self.files[part.name] = xml.encode("utf-8")
        self.overrides[part.name] = content_type
        if part.rels:
            directory, name = part.name.rsplit("/", 1)
            self.files[f"{directory}/_rels/{name}.rels"] = part.rels_xml().encode("utf-8")

    def _zip(self) -> bytes:
        core = ""
        if self.deck.title:
            core = (
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
                'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            )
            self.files["docProps/core.xml"] = (
                f'{_XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/'
                f'2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f"<dc:title>{escape(_clean(self.deck.title))}</dc:title></cp:coreProperties>"
            ).encode()
            self.overrides["docProps/core.xml"] = (
                "application/vnd.openxmlformats-package.core-properties+xml"
            )
        package_rels = (
            f"{_XML}<Relationships xmlns="
            '"http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_RT}/officeDocument" Target="ppt/presentation.xml"/>'
            f"{core}</Relationships>"
        )
        overrides = "".join(
            f'<Override PartName="/{name}" ContentType="{kind}"/>'
            for name, kind in self.overrides.items()
        )
        types = (
            f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType='
            '"application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Default Extension="bmp" ContentType="image/bmp"/>'
            '<Default Extension="tiff" ContentType="image/tiff"/>'
            '<Default Extension="emf" ContentType="image/x-emf"/>'
            '<Default Extension="wmf" ContentType="image/x-wmf"/>'
            f"{overrides}</Types>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("[Content_Types].xml", types)
            archive.writestr("_rels/.rels", package_rels)
            for name, data in self.files.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    # -- masters --------------------------------------------------------------
    def _scheme_of(self, page: Page) -> tuple[str, ...]:
        master = page.master
        if page.scheme is not None and (not page.use_master_scheme or master is None):
            return page.scheme
        if master is not None and master.scheme is not None:
            return master.scheme
        return page.scheme or DEFAULT_SCHEME

    def _layout_for(self, master: Page | None, scheme: tuple[str, ...]) -> str:
        """The layout part name for a master drawn in a scheme, writing it when new."""
        key = (id(master), scheme)
        known = self.master_parts.get(key)
        index = (
            len(self.master_parts) + 1 if known is None else list(self.master_parts).index(key) + 1
        )
        layout_name = f"ppt/slideLayouts/slideLayout{index}.xml"
        if known is not None:
            return layout_name

        part = Part(f"ppt/slideMasters/slideMaster{index}.xml")
        self.master_parts[key] = part
        layout = Part(layout_name)
        layout.relate("slideMaster", f"../slideMasters/slideMaster{index}.xml")
        self._add(
            layout,
            f'{_XML}<p:sldLayout {_NS} type="blank"><p:cSld name="Blank"><p:spTree>{_EMPTY_TREE}'
            "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>",
            f"{_PML}.slideLayout+xml",
        )
        rel_layout = part.relate("slideLayout", f"../slideLayouts/slideLayout{index}.xml")
        part.relate("theme", f"../theme/theme{index}.xml")
        self._theme(index, scheme)

        shapes, background = "", ""
        if master is not None:
            pages = [master]
            if master.master is not None and master.show_master_shapes:
                pages.insert(0, master.master)
            ids = _Ids()
            for page in pages:
                shapes += self._shapes(page, page.shapes, part, scheme, EMU_PER_UNIT, ids, True)
            source = (
                master if master.background is not None or master.master is None else master.master
            )
            background = self._background(source, part, scheme)
        if not background:
            background = f"<p:bg><p:bgPr>{_solid(scheme[0])}<a:effectLst/></p:bgPr></p:bg>"
        self._add(
            part,
            f"{_XML}<p:sldMaster {_NS}><p:cSld>{background}<p:spTree>{_EMPTY_TREE}{shapes}"
            '</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
            'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
            'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            f'<p:sldLayoutIdLst><p:sldLayoutId id="{2147483649 + (index - 1) * 16}" '
            f'r:id="{rel_layout}"/></p:sldLayoutIdLst></p:sldMaster>',
            f"{_PML}.slideMaster+xml",
        )
        return layout_name

    def _theme(self, index: int, scheme: tuple[str, ...]) -> None:
        names = (
            ("dk1", 1), ("lt1", 0), ("dk2", 3), ("lt2", 2), ("accent1", 4), ("accent2", 5),
            ("accent3", 2), ("accent4", 3), ("accent5", 6), ("accent6", 7), ("hlink", 6),
            ("folHlink", 7),
        )  # fmt: skip
        colors = "".join(f'<a:{n}><a:srgbClr val="{scheme[i]}"/></a:{n}>' for n, i in names)
        font = escape(self.deck.fonts.get(0, "Arial"))
        self.files[f"ppt/theme/theme{index}.xml"] = (
            f'{_XML}<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            f'name="Design"><a:themeElements><a:clrScheme name="Scheme">{colors}</a:clrScheme>'
            f'<a:fontScheme name="Fonts"><a:majorFont><a:latin typeface="{font}"/><a:ea '
            f'typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface='
            f'"{font}"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
            "</a:themeElements></a:theme>"
        ).encode()
        self.overrides[f"ppt/theme/theme{index}.xml"] = (
            "application/vnd.openxmlformats-officedocument.theme+xml"
        )

    def _background(self, page: Page, part: Part, scheme: tuple[str, ...]) -> str:
        shape = page.background
        if shape is None:
            return ""
        palette = Palette(scheme, shape.props)
        fill = self._fill(shape.props, part, palette, default="solid", scheme=scheme)
        if not fill or fill == "<a:noFill/>":
            return ""
        return f"<p:bg><p:bgPr>{fill}<a:effectLst/></p:bgPr></p:bg>"

    # -- slides ---------------------------------------------------------------
    def _slide(self, page: Page, number: int) -> Part:
        part = Part(f"ppt/slides/slide{number}.xml")
        scheme = self._scheme_of(page)
        layout = self._layout_for(page.master, scheme)
        part.relate("slideLayout", "../" + layout.removeprefix("ppt/"))
        try:
            shapes = self._shapes(page, page.shapes, part, scheme, EMU_PER_UNIT, _Ids(), False)
            background = "" if page.use_master_background else self._background(page, part, scheme)
        except Exception as exc:  # noqa: BLE001 - a broken slide stays in place, blank
            self.issues.append(f"slide {number}: {exc}")
            shapes, background = "", ""
        attrs = ""
        if not page.show_master_shapes:
            attrs += ' showMasterSp="0"'
        if page.hidden:
            attrs += ' show="0"'
        self._add(
            part,
            f"{_XML}<p:sld {_NS}{attrs}><p:cSld>{background}<p:spTree>{_EMPTY_TREE}{shapes}"
            "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
            f"{_transition(page.transition)}{self._timing(page)}</p:sld>",
            f"{_PML}.slide+xml",
        )
        return part

    # -- animations -----------------------------------------------------------
    def _timing(self, page: Page) -> str:
        try:
            shapes = {s.spid: s for s in page.root.walk()} if page.root is not None else {}

            def paragraphs(spid: int, begin: int, end: int) -> tuple[int, int] | None:
                block = shapes[spid].text if spid in shapes else None
                if block is None:
                    return None
                # Targets are character offsets; p:timing counts paragraphs.
                first = block.text.count("\r", 0, max(begin, 0))
                return first, block.text.count("\r", 0, max(end, begin, 0))

            if page.timeline is not None:  # the older records are only a stale copy of it
                return timeline.from_timeline(page.timeline, paragraphs)
            return timeline.from_builds(self._builds(shapes.values()))
        except (struct.error, ValueError, IndexError, KeyError, RecursionError) as exc:
            self.issues.append(f"animation skipped: {exc}")
            return ""

    def _builds(self, shapes) -> list[timeline.Build]:
        builds: list[timeline.Build] = []
        for shape in shapes:
            if shape.animation is None:
                continue
            order, flags, build_type, effect, direction = shape.animation
            automatic = bool(flags & 0x4)
            block = shape.text
            if build_type == 1 or (build_type > 1 and (block is None or not block.text)):
                builds.append(timeline.Build(order, shape.spid, effect, direction, automatic))
            elif build_type > 1:
                # Built by paragraph: each one down to this level gets its own click.
                for step, span in enumerate(_paragraph_groups(block, build_type - 2)):
                    key = order + step / 1000
                    builds.append(
                        timeline.Build(key, shape.spid, effect, direction, automatic, span)
                    )
        return builds

    # -- shapes ---------------------------------------------------------------
    def _shapes(self, page, shapes, part, scheme, scale, ids, on_master) -> str:
        out = []
        for shape in shapes:
            if shape.flags & (SP_DELETED | SP_BACKGROUND):
                continue
            if on_master and shape.placeholder:
                continue  # prompts on a master are never shown on a slide
            try:
                out.append(self._shape(page, shape, part, scheme, scale, ids, on_master))
            except (struct.error, ValueError, IndexError, KeyError, ZeroDivisionError) as exc:
                self.issues.append(f"shape skipped: {exc}")
        return "".join(out)

    def _master_shape(self, page: Page, shape: Shape) -> Shape | None:
        wanted = _MASTER_PLACEHOLDER.get(shape.placeholder, 2 if shape.placeholder >= 19 else 0)
        master = page.master
        while wanted and master is not None:
            for candidate in master.shapes:
                if candidate.placeholder == wanted:
                    return candidate
            master = master.master
        return None

    def _shape(self, page, shape, part, scheme, scale, ids, on_master) -> str:
        if shape.placeholder and not on_master:
            base = self._master_shape(page, shape)
            if base is not None:
                shape.props = shape.props.merged_over(base.props)
                shape.anchor = shape.anchor or base.anchor
        if shape.anchor is None:
            return ""
        props = shape.props
        palette = Palette(scheme, props)
        left, top, right, bottom = shape.anchor
        offset_x, offset_y = left * scale, top * scale
        width, height = (right - left) * scale, (bottom - top) * scale

        angle = props.signed(ROTATION) / FIXED % 360
        if 45 <= angle < 135 or 225 <= angle < 315:
            # OfficeArt stores these frames as if turned a further quarter.
            center_x, center_y = offset_x + width / 2, offset_y + height / 2
            width, height = height, width
            offset_x, offset_y = center_x - width / 2, center_y - height / 2
        xfrm = _xfrm(offset_x, offset_y, width, height, angle, shape, "a")

        if shape.is_group:
            return self._group(page, shape, part, scheme, ids, on_master, xfrm, width, height)

        nv_id = ids.take(shape.spid)
        name = _prop_text(props, NAME) or f"Shape {nv_id}"
        c_nv = _c_nv_pr(nv_id, name, _prop_text(props, DESCRIPTION), self._link(shape, part))

        picture = props.get(PICTURE)
        if picture or shape.kind == geometry.PICTURE_FRAME:
            return self._picture(shape, part, palette, xfrm, c_nv)

        kind = shape.kind
        block = self._text_of(page, shape)
        has_text = block is not None and bool(block.text.strip("\r\x0b "))
        wordart = props.complex.get(GTEXT)
        is_placeholder = bool(shape.placeholder)
        decorated = props.flag(FILL_BOOLS, FILLED, False) or props.flag(LINE_BOOLS, LINE_ON, False)
        if is_placeholder and not has_text and not decorated:
            return ""  # an unfilled placeholder shows nothing in a slide show

        emu_w, emu_h = round(width), round(height)
        geom = self._geometry(shape, emu_w, emu_h)
        plain = kind == geometry.TEXT_BOX or is_placeholder or kind in geometry.WORDART
        if wordart:  # its fill and outline belong to the letters, not to a box
            fill, line, effects = "", "<a:ln><a:noFill/></a:ln>", ""
        else:
            fill = self._fill(
                props,
                part,
                palette,
                default="" if kind in geometry.LINES or plain else "solid",
                scheme=scheme,
            )
            line = self._line(props, palette, not plain and kind != geometry.PICTURE_FRAME)
            effects = self._shadow(props, palette)
        sp_pr = f"<p:spPr>{xfrm}{geom}{fill or '<a:noFill/>'}{line}{effects}</p:spPr>"

        if kind in geometry.CONNECTORS:
            return (
                f"<p:cxnSp><p:nvCxnSpPr>{c_nv}<p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>{sp_pr}"
                "</p:cxnSp>"
            )
        placeholder = _PLACEHOLDER_TITLES.get(shape.placeholder)
        nv_pr = f'<p:nvPr><p:ph type="{placeholder}"/></p:nvPr>' if placeholder else "<p:nvPr/>"
        tx_box = ' txBox="1"' if kind == geometry.TEXT_BOX else ""
        if wordart:
            body = self._wordart(props, palette, wordart, emu_w, emu_h)
        elif block is not None:
            body = self._body(page, shape, block, part, scheme)
        else:
            body = ""
        return f"<p:sp><p:nvSpPr>{c_nv}<p:cNvSpPr{tx_box}/>{nv_pr}</p:nvSpPr>{sp_pr}{body}</p:sp>"

    def _group(self, page, shape, part, scheme, ids, on_master, xfrm, width, height) -> str:
        rect = shape.group_rect or shape.anchor
        left, top, right, bottom = rect
        child_w, child_h = (right - left) or round(width) or 1, (bottom - top) or round(height) or 1
        xfrm = xfrm.replace(
            "</a:xfrm>",
            f'<a:chOff x="{left}" y="{top}"/><a:chExt cx="{child_w}" cy="{child_h}"/></a:xfrm>',
        )
        nv_id = ids.take(shape.spid)
        inner = self._shapes(page, shape.children, part, scheme, 1, ids, on_master)
        return (
            f'<p:grpSp><p:nvGrpSpPr><p:cNvPr id="{nv_id}" name="Group {nv_id}"/><p:cNvGrpSpPr/>'
            f"<p:nvPr/></p:nvGrpSpPr><p:grpSpPr>{xfrm}</p:grpSpPr>{inner}</p:grpSp>"
        )

    def _link(self, shape: Shape, part: Part) -> str:
        url = self.deck.links.get(shape.link or 0)
        if not url:
            return ""
        return f'<a:hlinkClick r:id="{part.relate("hyperlink", url, True)}"/>'

    def _text_of(self, page: Page, shape: Shape) -> TextBlock | None:
        block = shape.text
        if block is None or block.outline_ref is None:
            return block
        if block.outline_ref < len(page.outline):
            outlined = page.outline[block.outline_ref]
            outlined.ruler = block.ruler or outlined.ruler
            outlined.fields = block.fields or outlined.fields
            return outlined
        return block

    def _image(self, index: int | None, part: Part) -> str | None:
        """A relationship id for picture ``index``, adding the media part on first use."""
        if not index:
            return None
        if index not in self.media:
            blip = self.deck.pictures.get(index)
            image = pictures.decode_blip(blip) if blip is not None else None
            if image is None:
                self.media[index] = None
            else:
                name = f"image{index}.{image.extension}"
                self.files[f"ppt/media/{name}"] = image.data
                self.media[index] = name
        name = self.media[index]
        return part.relate("image", f"../media/{name}") if name else None

    def _picture(self, shape, part, palette, xfrm, c_nv) -> str:
        props = shape.props
        rel = self._image(props.get(PICTURE), part)
        if rel is None:
            return ""
        crop = ""
        edges = (("t", CROP_TOP), ("b", CROP_BOTTOM), ("l", CROP_LEFT), ("r", CROP_RIGHT))
        values = {k: round(props.signed(p) / FIXED * 100000) for k, p in edges}
        if any(values.values()):
            crop = "<a:srcRect " + " ".join(f'{k}="{v}"' for k, v in values.items() if v) + "/>"
        line = self._line(props, palette, False)
        return (
            f'<p:pic><p:nvPicPr>{c_nv}<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>'
            f'<p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="{rel}"/>{crop}<a:stretch>'
            f'<a:fillRect/></a:stretch></p:blipFill><p:spPr>{xfrm}<a:prstGeom prst="rect">'
            f"<a:avLst/></a:prstGeom>{line}</p:spPr></p:pic>"
        )

    # -- geometry and paint ---------------------------------------------------
    def _geometry(self, shape: Shape, width: int, height: int) -> str:
        props = shape.props
        if shape.kind == 0 and VERTICES in props.complex:
            custom = _custom_geometry(props)
            if custom:
                return custom
        if shape.kind == geometry.TRAPEZOID:
            return _trapezoid(props)
        preset = geometry.PRESETS.get(shape.kind, "rect")
        adjust = [props.get(ADJUST_1 + i) for i in range(8)]
        guides = geometry.adjustments(shape.kind, adjust, width, height)
        names = "".join(f'<a:gd name="{k}" fmla="val {v}"/>' for k, v in guides.items())
        return f'<a:prstGeom prst="{preset}"><a:avLst>{names}</a:avLst></a:prstGeom>'

    def _fill(self, props: Props, part: Part, palette: Palette, default: str, scheme) -> str:
        """DrawingML fill, or "" when the shape has none set and none by default."""
        if not props.flag(FILL_BOOLS, FILLED, bool(default)):
            return "<a:noFill/>"
        kind = props.get(FILL_TYPE, 0)
        color = palette.art(props.get(FILL_COLOR, 0xFFFFFF), "FFFFFF")
        if props.get(FILL_COLOR) is None and props.simple.get(FILL_BOOLS) is None and not default:
            return ""
        opacity = props.get(FILL_OPACITY)
        if kind in (2, 3):
            rel = self._image(props.get(FILL_BLIP), part)
            if rel is not None:
                mode = "<a:tile/>" if kind == 2 else "<a:stretch><a:fillRect/></a:stretch>"
                return f'<a:blipFill><a:blip r:embed="{rel}"/>{mode}</a:blipFill>'
        if kind == 1:
            back = palette.art(props.get(FILL_BACK, 0xFFFFFF), "FFFFFF")
            return (
                f'<a:pattFill prst="pct50"><a:fgClr><a:srgbClr val="{color}"/></a:fgClr>'
                f'<a:bgClr><a:srgbClr val="{back}"/></a:bgClr></a:pattFill>'
            )
        if 4 <= kind <= 8:
            return _gradient(props, palette, kind, color)
        if kind == 9:
            return _solid(scheme[0], opacity)
        return _solid(color, opacity)

    def _line(self, props: Props, palette: Palette, default: bool) -> str:
        if not props.flag(LINE_BOOLS, LINE_ON, default):
            return "<a:ln><a:noFill/></a:ln>"
        width = props.get(LINE_WIDTH, 9525)
        color = palette.art(props.get(LINE_COLOR, 0), "000000")
        attrs = f' w="{width}"'
        compound = _COMPOUND.get(props.get(LINE_STYLE, 0))
        if compound:
            attrs += f' cmpd="{compound}"'
        inner = _solid(color, props.get(LINE_OPACITY))
        dash = _DASH.get(props.get(LINE_DASH, 0))
        if dash:
            inner += f'<a:prstDash val="{dash}"/>'
        inner += _arrow("headEnd", props, LINE_START, LINE_START_W, LINE_START_L)
        inner += _arrow("tailEnd", props, LINE_END, LINE_END_W, LINE_END_L)
        return f"<a:ln{attrs}>{inner}</a:ln>"

    def _shadow(self, props: Props, palette: Palette) -> str:
        if not props.flag(SHADOW_BOOLS, SHADOW_ON, False):
            return ""
        dx, dy = props.signed(SHADOW_X, 25400), props.signed(SHADOW_Y, 25400)
        distance = round(math.hypot(dx, dy))
        direction = round(math.degrees(math.atan2(dy, dx)) * 60000) % 21600000
        color = palette.art(props.get(SHADOW_COLOR, 0x808080), "808080")
        opacity = props.get(SHADOW_OPACITY, 0x10000)
        alpha = f'<a:alpha val="{round(opacity / FIXED * 100000)}"/>' if opacity < 0x10000 else ""
        return (
            f'<a:effectLst><a:outerShdw dist="{distance}" dir="{direction}" rotWithShape="0">'
            f'<a:srgbClr val="{color}">{alpha}</a:srgbClr></a:outerShdw></a:effectLst>'
        )

    # -- text -----------------------------------------------------------------
    def _style(self, page: Page, text_type: int, level: int) -> tuple[dict, dict]:
        """Master formatting for a text type and level, least specific first."""
        key = (id(page.master), text_type, level)
        cached = self._style_cache.get(key)
        if cached is not None:
            return cached
        master = page if page.is_master and page.styles else page.master
        while master is not None and not master.styles and master.master is not None:
            master = master.master
        base_type = {
            text.CENTER_TITLE: text.TITLE,
            text.CENTER_BODY: text.BODY,
            text.HALF_BODY: text.BODY,
            text.QUARTER_BODY: text.BODY,
        }.get(text_type, text_type)
        layers = [self.deck.defaults.get(text.OTHER)]
        if master is not None:
            layers.append(master.styles.get(base_type))
            if text_type != base_type:
                layers.append(master.styles.get(text_type))
        pf: dict = {}
        cf: dict = {}
        for levels in layers:
            if levels:
                entry = levels[min(level, len(levels) - 1)]
                pf = {**pf, **entry[0]}
                cf = {**cf, **entry[1]}
        self._style_cache[key] = (pf, cf)
        return pf, cf

    def _body(self, page, shape, block, part, scheme) -> str:
        props = shape.props
        palette = Palette(scheme, props)
        attrs = (
            f'lIns="{props.get(TEXT_LEFT, 91440)}" tIns="{props.get(TEXT_TOP, 45720)}" '
            f'rIns="{props.get(TEXT_RIGHT, 91440)}" bIns="{props.get(TEXT_BOTTOM, 45720)}"'
        )
        anchor = props.get(TEXT_ANCHOR, 0)
        attrs += f' anchor="{_ANCHOR.get(anchor, "t")}"'
        if anchor in (3, 4, 5):
            attrs += ' anchorCtr="1"'
        if props.get(TEXT_WRAP, 0) == 2:
            attrs += ' wrap="none"'
        else:
            attrs += ' wrap="square"'
        flow = _FLOW.get(props.get(TEXT_FLOW, 0))
        if flow:
            attrs += f' vert="{flow}"'
        fit = props.flag(TEXT_BOOLS, FIT_SHAPE_TO_TEXT, False)
        autofit = "<a:spAutoFit/>" if fit else "<a:noAutofit/>"
        paragraphs = self._paragraphs(page, block, part, palette)
        return (
            f"<p:txBody><a:bodyPr {attrs}>{autofit}</a:bodyPr><a:lstStyle/>{paragraphs}</p:txBody>"
        )

    def _paragraphs(self, page, block, part, palette) -> str:
        source = block.text
        out = []
        start = 0
        for piece in source.split("\r"):
            end = start + len(piece)
            level, run_pf = _run_at(block.paragraphs, start)
            master_pf, master_cf = self._style(page, block.text_type, level)
            pf = {**master_pf, **block.ruler.get(level, {}), **run_pf}
            if "bullet_font" in run_pf:  # even with its flag off: nobody means a letter "u"
                pf["bullet_has_font"] = True
            for key in ("color", "size"):  # a stored value applies when the flag is inherited
                if f"bullet_{key}" in run_pf:
                    pf[f"bullet_has_{key}"] = run_pf.get(f"bullet_has_{key}", True)
            runs = self._runs(block, start, end, master_cf, part, palette)
            out.append(f"<a:p>{self._paragraph_props(pf, palette)}{runs}</a:p>")
            start = end + 1
        return "".join(out)

    def _paragraph_props(self, pf: dict, palette: Palette) -> str:
        left = pf.get("left", 0)
        attrs = f' marL="{_emu(left)}" indent="{_emu(pf.get("indent", 0) - left)}"'
        align = _ALIGN.get(pf.get("align", 0))
        if align and align != "l":
            attrs += f' algn="{align}"'
        if pf.get("rtl"):
            attrs += ' rtl="1"'
        inner = ""
        line = pf.get("line", 100)
        if line != 100:
            inner += f"<a:lnSpc>{_spacing(line)}</a:lnSpc>"
        inner += f"<a:spcBef>{_spacing(pf.get('before', 0))}</a:spcBef>"
        inner += f"<a:spcAft>{_spacing(pf.get('after', 0))}</a:spcAft>"
        if pf.get("bullet"):
            if pf.get("bullet_has_color") and "bullet_color" in pf:
                color = palette.text(pf["bullet_color"])
                inner += f'<a:buClr><a:srgbClr val="{color}"/></a:buClr>'
            size = pf.get("bullet_size", 100)
            if pf.get("bullet_has_size") and size > 0:
                inner += f'<a:buSzPct val="{size * 1000}"/>'
            elif pf.get("bullet_has_size") and size < 0:
                inner += f'<a:buSzPts val="{-size * 100}"/>'
            number = pf.get("number")
            font = self.deck.fonts.get(pf.get("bullet_font", -1))
            if number is not None:
                scheme, first = number
                kind = _NUMBERING.get(scheme, "arabicPeriod")
                inner += f'<a:buAutoNum type="{kind}" startAt="{max(first, 1)}"/>'
            else:
                if pf.get("bullet_has_font") and font:
                    inner += f"<a:buFont typeface={quoteattr(font)}/>"
                char = chr(pf.get("bullet_char") or 0x2022)
                if _is_symbol(char) and not fonts.is_symbol_font(font):
                    char = "•"  # a symbol code point with no symbol font to draw it
                inner += f"<a:buChar char={quoteattr(_clean(char) or '•')}/>"
        else:
            inner += "<a:buNone/>"
        tabs = pf.get("tabs")
        if tabs:
            kinds = {0: "l", 1: "ctr", 2: "r", 3: "dec"}
            items = "".join(
                f'<a:tab pos="{_emu(position)}" algn="{kinds.get(kind, "l")}"/>'
                for position, kind in tabs
            )
            inner += f"<a:tabLst>{items}</a:tabLst>"
        return f"<a:pPr{attrs}>{inner}</a:pPr>"

    def _runs(self, block, start, end, master_cf, part, palette) -> str:
        text_value = block.text
        cuts = {start, end}
        position = 0
        for length, _cf in block.chars:
            position += length
            if start < position < end:
                cuts.add(position)
        for i in range(start, end):
            if text_value[i] == "\x0b" or i in block.fields or _is_symbol(text_value[i]):
                cuts.update((i, i + 1))
        for begin, stop, _ref in block.links:
            cuts.update(p for p in (begin, stop) if start < p < end)
        points = sorted(cuts)
        out = []
        cf = master_cf
        for first, last in zip(points, points[1:], strict=False):
            cf = {**master_cf, **_char_run_at(block.chars, first)}
            segment = text_value[first:last]
            if _is_symbol(segment[0]):
                cf["typeface"] = self.deck.fonts.get(cf.get("symbol_font", -1), "Symbol")
            if segment == "\x0b":
                out.append(f"<a:br>{self._run_props('a:rPr', cf, palette, '')}</a:br>")
                continue
            link = ""
            for begin, stop, ref in block.links:
                url = self.deck.links.get(ref)
                if url and begin <= first < stop:
                    link = f'<a:hlinkClick r:id="{part.relate("hyperlink", url, True)}"/>'
            props = self._run_props("a:rPr", cf, palette, link)
            field_kind = block.fields.get(first)
            if field_kind == "slidenum":
                out.append(
                    '<a:fld id="{00000000-0000-0000-0000-000000000001}" type="slidenum">'
                    f"{props}<a:t>‹#›</a:t></a:fld>"
                )
                continue
            if field_kind == "date":
                segment = datetime.date.today().strftime("%x")
            cleaned = _clean(segment)
            if cleaned:
                out.append(f"<a:r>{props}<a:t>{escape(cleaned)}</a:t></a:r>")
        if start == end:
            cf = {**master_cf, **_char_run_at(block.chars, start)}
        out.append(self._run_props("a:endParaRPr", cf, palette, ""))
        return "".join(out)

    def _run_props(self, tag: str, cf: dict, palette: Palette, link: str) -> str:
        attrs = f' lang="en-US" sz="{cf.get("size", 18) * 100}"'
        attrs += f' b="{int(bool(cf.get("bold")))}" i="{int(bool(cf.get("italic")))}"'
        if cf.get("underline"):
            attrs += ' u="sng"'
        position = cf.get("position", 0)
        if position:
            attrs += f' baseline="{position * 1000}"'
        color = palette.scheme[1]
        if "color" in cf:
            color = palette.text(cf["color"])
        inner = _solid(color)
        if cf.get("shadow"):
            inner += (
                '<a:effectLst><a:outerShdw blurRad="12700" dist="19050" dir="2700000">'
                '<a:srgbClr val="000000"><a:alpha val="40000"/></a:srgbClr></a:outerShdw>'
                "</a:effectLst>"
            )
        font = cf.get("typeface") or self.deck.fonts.get(cf.get("font", 0))
        if font:
            inner += f"<a:latin typeface={quoteattr(font)}/><a:cs typeface={quoteattr(font)}/>"
        return f"<{tag}{attrs}>{inner}{link}</{tag}>"

    def _wordart(self, props, palette, blob, width, height) -> str:
        """WordArt keeps its words and colour; warps are not reproduced."""
        words = blob.decode("utf-16-le", "replace").split("\0", 1)[0]
        font = _prop_text(props, GTEXT_FONT) or "Arial"
        color = palette.art(props.get(FILL_COLOR, 0xFFFFFF), "FFFFFF")
        # WordArt stretches to fill its box; pick the largest size that fits instead.
        fitted = min(height * 0.8, width / max(len(words), 1) / 0.55) / 12700
        size = max(100, round(fitted) * 100)
        run = (
            f'<a:rPr lang="en-US" sz="{min(size, 40000)}" b="1">{_solid(color)}'
            f"<a:latin typeface={quoteattr(font)}/></a:rPr>"
        )
        return (
            '<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr" '
            f'anchorCtr="1"><a:noAutofit/></a:bodyPr><a:lstStyle/><a:p><a:pPr algn="ctr"/><a:r>'
            f"{run}<a:t>{escape(_clean(words))}</a:t></a:r></a:p></p:txBody>"
        )


class _Ids:
    """Shape ids stay the file's own, which animation targets refer to."""

    def __init__(self) -> None:
        self.used = {1}
        self.value = 1

    def take(self, wanted: int) -> int:
        while not wanted or wanted in self.used:
            self.value += 1
            wanted = self.value
        self.used.add(wanted)
        return wanted


_EMPTY_TREE = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
)


def _c_nv_pr(nv_id: int, name: str, description: str, link: str) -> str:
    descr = f" descr={quoteattr(description)}" if description else ""
    return f'<p:cNvPr id="{nv_id}" name={quoteattr(name)}{descr}>{link}</p:cNvPr>'


def _prop_text(props: Props, pid: int) -> str:
    blob = props.complex.get(pid)
    if not blob:
        return ""
    return _clean(blob.decode("utf-16-le", "replace").split("\0", 1)[0]).strip()


def _xfrm(x, y, width, height, angle, shape: Shape, ns: str) -> str:
    attrs = ""
    if angle:
        attrs += f' rot="{round(angle * 60000) % 21600000}"'
    if shape.flip_h:
        attrs += ' flipH="1"'
    if shape.flip_v:
        attrs += ' flipV="1"'
    return (
        f'<{ns}:xfrm{attrs}><a:off x="{round(x)}" y="{round(y)}"/>'
        f'<a:ext cx="{max(round(width), 0)}" cy="{max(round(height), 0)}"/></{ns}:xfrm>'
    )


def _spacing(value: int) -> str:
    """Positive values are percentages; negative ones are master units."""
    if value >= 0:
        return f'<a:spcPct val="{value * 1000}"/>'
    return f'<a:spcPts val="{round(-value / 8 * 100)}"/>'


def _arrow(tag: str, props: Props, kind: int, width: int, length: int) -> str:
    name = _ARROW.get(props.get(kind, 0))
    if not name:
        return ""
    w = _ARROW_SIZE.get(props.get(width, 1), "med")
    size = _ARROW_SIZE.get(props.get(length, 1), "med")
    return f'<a:{tag} type="{name}" w="{w}" len="{size}"/>'


def _run_at(runs: list, position: int) -> tuple[int, dict]:
    end = 0
    for length, level, pf in runs:
        end += length
        if position < end:
            return level, pf
    return (runs[-1][1], runs[-1][2]) if runs else (0, {})


def _char_run_at(runs: list, position: int) -> dict:
    end = 0
    for length, cf in runs:
        end += length
        if position < end:
            return cf
    return runs[-1][1] if runs else {}


def _paragraph_groups(block: TextBlock, deepest: int):
    """The (first, last) paragraphs of each step of a paragraph build."""
    start = 0
    groups: list[list[int]] = []
    for index, piece in enumerate(block.text.split("\r")):
        level = _run_at(block.paragraphs, start)[0]
        if piece.strip() and (level <= deepest or not groups):
            groups.append([index, index])
        elif groups:
            groups[-1][1] = index
        start += len(piece) + 1
    return [(first, last) for first, last in groups]


def _gradient(props: Props, palette: Palette, kind: int, color: str) -> str:
    back = palette.art(props.get(FILL_BACK, 0xFFFFFF), color)
    shades = array_items(props.complex.get(FILL_SHADES))
    if shades:
        stops = [
            (
                struct.unpack("<i", item[4:8])[0] / FIXED,
                palette.art(struct.unpack("<I", item[:4])[0]),
            )
            for item in shades
            if len(item) >= 8
        ]
    else:
        stops = [(0.0, back), (1.0, color)]

    # The focus is where the fill colour sits along the gradient; negative
    # values swap the colours, and the middle mirrors the gradient.
    focus = props.signed(FILL_FOCUS)
    if focus < 0:
        focus = -focus
        stops = [(1 - p, c) for p, c in reversed(stops)]
    if focus == 0:
        stops = [(1 - p, c) for p, c in reversed(stops)]
    elif focus < 100:
        at = focus / 100
        stops = [(p * at, c) for p, c in stops] + [
            (at + (1 - at) * (1 - p), c) for p, c in reversed(stops[:-1])
        ]
    entries = "".join(
        f'<a:gs pos="{round(max(0.0, min(1.0, p)) * 100000)}"><a:srgbClr val="{c}"/></a:gs>'
        for p, c in stops
    )
    if kind in (5, 6):
        edges = (
            ("l", FILL_TO_LEFT),
            ("t", FILL_TO_TOP),
            ("r", FILL_TO_RIGHT),
            ("b", FILL_TO_BOTTOM),
        )
        rect = " ".join(
            f'{k}="{round(props.signed(p) / FIXED * 100000)}"'
            if k in "lt"
            else f'{k}="{100000 - round(props.signed(p) / FIXED * 100000)}"'
            for k, p in edges
        )
        path = "shape" if kind == 6 else "rect"
        return (
            f'<a:gradFill rotWithShape="1"><a:gsLst>{entries}</a:gsLst><a:path path="{path}">'
            f"<a:fillToRect {rect}/></a:path></a:gradFill>"
        )
    # Angle 0 runs bottom to top and grows clockwise; DrawingML's 0 runs left to right.
    angle = round((270 + props.signed(FILL_ANGLE) / FIXED) % 360 * 60000)
    return (
        f'<a:gradFill rotWithShape="1"><a:gsLst>{entries}</a:gsLst>'
        f'<a:lin ang="{angle}" scaled="0"/></a:gradFill>'
    )


def _trapezoid(props: Props) -> str:
    """Drawn as a path: the legacy shape is wide at the top, the preset at the bottom."""
    inset = props.get(ADJUST_1, 5400)
    return (
        '<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="0" t="0" r="r" b="b"/>'
        '<a:pathLst><a:path w="21600" h="21600"><a:moveTo><a:pt x="0" y="0"/></a:moveTo>'
        '<a:lnTo><a:pt x="21600" y="0"/></a:lnTo>'
        f'<a:lnTo><a:pt x="{21600 - inset}" y="21600"/></a:lnTo>'
        f'<a:lnTo><a:pt x="{inset}" y="21600"/></a:lnTo><a:close/></a:path></a:pathLst>'
        "</a:custGeom>"
    )


def _custom_geometry(props: Props) -> str:
    items = array_items(props.complex.get(VERTICES))
    points: list[tuple[int, int]] = []
    for item in items:
        if len(item) == 4:
            points.append(struct.unpack("<hh", item))
        elif len(item) >= 8:
            points.append(struct.unpack("<ii", item[:8]))
    if len(points) < 2:
        return ""
    left, top = props.signed(GEO_LEFT, 0), props.signed(GEO_TOP, 0)
    right = props.signed(GEO_RIGHT, 21600)
    bottom = props.signed(GEO_BOTTOM, 21600)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return ""

    def pt(index: int) -> str:
        x, y = points[index]
        return f'<a:pt x="{x - left}" y="{y - top}"/>'

    commands: list[str] = []
    segments = [struct.unpack("<H", s[:2])[0] for s in array_items(props.complex.get(SEGMENTS))]
    at = 0
    total = len(points)
    if not segments:
        commands.append(f"<a:moveTo>{pt(0)}</a:moveTo>")
        commands += [f"<a:lnTo>{pt(i)}</a:lnTo>" for i in range(1, total)]
        if props.get(SHAPE_PATH, 0) in (1, 3):
            commands.append("<a:close/>")
    for segment in segments:
        code, count = segment >> 13, segment & 0x1FFF
        if code == 0:
            for _ in range(max(count, 1)):
                if at < total:
                    commands.append(f"<a:lnTo>{pt(at)}</a:lnTo>")
                    at += 1
        elif code == 1:
            for _ in range(max(count, 1)):
                if at + 3 <= total:
                    commands.append(
                        f"<a:cubicBezTo>{pt(at)}{pt(at + 1)}{pt(at + 2)}</a:cubicBezTo>"
                    )
                    at += 3
        elif code == 2:
            if at < total:
                commands.append(f"<a:moveTo>{pt(at)}</a:moveTo>")
                at += 1
        elif code == 3:
            commands.append("<a:close/>")
        elif code == 4:
            break
        elif code == 5:
            at += segment & 0xFF
    if not commands:
        return ""
    return (
        '<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="0" t="0" r="r" b="b"/>'
        f'<a:pathLst><a:path w="{width}" h="{height}">{"".join(commands)}</a:path></a:pathLst>'
        "</a:custGeom>"
    )


def _transition(transition: Transition | None) -> str:
    if transition is None:
        return ""
    speed = {0: "slow", 1: "med", 2: "fast"}.get(transition.speed, "fast")
    attrs = f' spd="{speed}"'
    if not transition.on_click:
        attrs += ' advClick="0"'
    if transition.advance_ms is not None:
        attrs += f' advTm="{transition.advance_ms}"'
    return f"<p:transition{attrs}>{_transition_effect(transition)}</p:transition>"


def _transition_effect(t: Transition) -> str:
    direction = t.direction
    corner = _DIRECTION.get(direction, "l")
    side = _DIRECTION.get(direction if direction < 4 else 0)
    axis = "vert" if direction else "horz"
    match t.effect:
        case 1:
            return "<p:random/>"
        case 2:
            return f'<p:blinds dir="{"horz" if direction else "vert"}"/>'
        case 3:
            return f'<p:checker dir="{axis}"/>'
        case 4:
            return f'<p:cover dir="{corner}"/>'
        case 5:
            return "<p:dissolve/>"
        case 6:
            return '<p:fade thruBlk="1"/>'
        case 7:
            return f'<p:pull dir="{corner}"/>'
        case 8:
            return f'<p:randomBar dir="{axis}"/>'
        case 9:
            return f'<p:strips dir="{corner if direction >= 4 else "ld"}"/>'
        case 10:
            return f'<p:wipe dir="{side}"/>'
        case 11:
            return f'<p:zoom dir="{"in" if direction else "out"}"/>'
        case 13:
            orient = "vert" if direction >= 2 else "horz"
            way = "in" if direction % 2 else "out"
            return f'<p:split orient="{orient}" dir="{way}"/>'
        case 17:
            return "<p:diamond/>"
        case 18:
            return "<p:plus/>"
        case 19:
            return "<p:wedge/>"
        case 20:
            return f'<p:push dir="{side}"/>'
        case 21:
            return f'<p:comb dir="{axis}"/>'
        case 22:
            return "<p:newsflash/>"
        case 23:
            return "<p:fade/>"
        case 26:
            return f'<p:wheel spokes="{direction or 4}"/>'
        case 27:
            return "<p:circle/>"
    return ""
