"""Slide masters, layouts and slides with their inheritance relationships.

``Deck`` wraps a loaded :class:`~slidelite.presentation.document.Presentation`
and lazily parses the parts a slide depends on (layout, master, theme).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from slidelite.presentation import xmlsafe
from slidelite.presentation.color import DEFAULT_CLR_MAP
from slidelite.presentation.document import Presentation
from slidelite.presentation.opc import PackageError
from slidelite.presentation.theme import Theme, parse_theme
from slidelite.presentation.xmlsafe import local, q

# Placeholder types a layout placeholder of a given type inherits from on the master.
MASTER_PH_ALIAS = {
    "ctrTitle": "title",
    "title": "title",
    "subTitle": "body",
    "body": "body",
    "obj": "body",
    "chart": "body",
    "tbl": "body",
    "clipArt": "body",
    "dgm": "body",
    "media": "body",
    "pic": "body",
    "dt": "dt",
    "ftr": "ftr",
    "sldNum": "sldNum",
    "hdr": "hdr",
}
TITLE_TYPES = {"title", "ctrTitle"}
OTHER_STYLE_TYPES = {"dt", "ftr", "sldNum", "hdr"}


def _prefer_choice(_branch, requires: str) -> bool:
    return False


@dataclass
class Placeholder:
    type: str
    idx: str | None
    element: object


def placeholder_info(sp) -> tuple[str, str | None] | None:
    """(type, idx) for a placeholder shape element, else None."""
    for nv in sp:
        if local(nv.tag).startswith("nv"):
            nvpr = nv.find(q("p:nvPr"))
            ph = nvpr.find(q("p:ph")) if nvpr is not None else None
            if ph is not None:
                return ph.get("type", "obj"), ph.get("idx")
            return None
    return None


class Part:
    """A slide, layout or master part."""

    kind = "part"

    def __init__(self, deck: Deck, partname: str, root) -> None:
        self.deck = deck
        self.partname = partname
        self.root = root
        self.rels = deck.package.rels(partname)
        self.parent: Part | None = None
        csld = root.find(q("p:cSld"))
        self.csld = csld
        self.sp_tree = csld.find(q("p:spTree")) if csld is not None else None
        self._placeholders: list[Placeholder] | None = None

    # -- theme & colours --------------------------------------------------------
    @property
    def master(self) -> Master:
        part: Part = self
        while part.parent is not None:
            part = part.parent
        return part  # type: ignore[return-value]

    @property
    def theme(self) -> Theme:
        return self.master.theme

    @property
    def clr_map(self) -> dict[str, str]:
        ovr = self.root.find(q("p:clrMapOvr"))
        if ovr is not None:
            override = ovr.find(q("a:overrideClrMapping"))
            if override is not None and override.attrib:
                return dict(override.attrib)
        if self.parent is not None:
            return self.parent.clr_map
        return dict(DEFAULT_CLR_MAP)

    @property
    def background(self):
        return self.csld.find(q("p:bg")) if self.csld is not None else None

    # -- placeholders ---------------------------------------------------------------
    @property
    def placeholders(self) -> list[Placeholder]:
        if self._placeholders is None:
            found: list[Placeholder] = []
            if self.sp_tree is not None:
                for el in self.sp_tree.iter():
                    if local(el.tag) in ("sp", "pic", "graphicFrame"):
                        info = placeholder_info(el)
                        if info is not None:
                            found.append(Placeholder(info[0], info[1], el))
            self._placeholders = found
        return self._placeholders

    def find_placeholder(self, ph_type: str, idx: str | None, by_type_only: bool = False):
        """Placeholder this part offers for a child's (type, idx)."""
        if not by_type_only and idx is not None:
            for ph in self.placeholders:
                if ph.idx == idx:
                    return ph.element
        want = MASTER_PH_ALIAS.get(ph_type, "body") if by_type_only else ph_type
        for ph in self.placeholders:
            have = MASTER_PH_ALIAS.get(ph.type, "body") if by_type_only else ph.type
            if have == want:
                return ph.element
        if ph_type in TITLE_TYPES:
            for ph in self.placeholders:
                if ph.type in TITLE_TYPES:
                    return ph.element
        return None

    def inherited_placeholder(self, sp):
        """The placeholder element ``sp`` (on this part) inherits from."""
        info = placeholder_info(sp)
        if info is None or self.parent is None:
            return None
        ph_type, idx = info
        if isinstance(self.parent, Master):
            return self.parent.find_placeholder(ph_type, idx, by_type_only=True)
        return self.parent.find_placeholder(ph_type, idx)


class Master(Part):
    kind = "master"

    def __init__(self, deck: Deck, partname: str, root) -> None:
        super().__init__(deck, partname, root)
        self._theme: Theme | None = None
        styles = root.find(q("p:txStyles"))
        self.title_style = styles.find(q("p:titleStyle")) if styles is not None else None
        self.body_style = styles.find(q("p:bodyStyle")) if styles is not None else None
        self.other_style = styles.find(q("p:otherStyle")) if styles is not None else None

    @property
    def theme(self) -> Theme:
        if self._theme is None:
            self._theme = Theme()
            for rel in self.rels.values():
                if rel.kind == "theme" and self.deck.package.has(rel.target):
                    try:
                        self._theme = parse_theme(self.deck.package.xml(rel.target))
                    except Exception:
                        self.deck.warn(f"theme {rel.target} unreadable")
                    break
        return self._theme

    @property
    def clr_map(self) -> dict[str, str]:
        el = self.root.find(q("p:clrMap"))
        if el is not None and el.attrib:
            merged = dict(DEFAULT_CLR_MAP)
            merged.update(el.attrib)
            return merged
        return dict(DEFAULT_CLR_MAP)

    def text_style_for(self, ph_type: str | None):
        if ph_type is None:
            return self.other_style
        if ph_type in TITLE_TYPES:
            return self.title_style
        if ph_type in OTHER_STYLE_TYPES:
            return self.other_style
        return self.body_style


class Layout(Part):
    kind = "layout"

    @property
    def show_master_shapes(self) -> bool:
        return xmlsafe.attr_bool(self.root, "showMasterSp", True)


class Slide(Part):
    kind = "slide"

    def __init__(self, deck: Deck, partname: str, root, index: int) -> None:
        super().__init__(deck, partname, root)
        self.index = index

    @property
    def layout(self) -> Layout | None:
        return self.parent if isinstance(self.parent, Layout) else None

    @property
    def show_master_shapes(self) -> bool:
        return xmlsafe.attr_bool(self.root, "showMasterSp", True)

    @property
    def hidden(self) -> bool:
        return not xmlsafe.attr_bool(self.root, "show", True)


class Deck:
    """Lazily parsed presentation parts with caching (thread-safe)."""

    def __init__(self, presentation: Presentation) -> None:
        self.presentation = presentation
        self.package = presentation.package
        self.warnings: list[str] = []
        self._lock = threading.RLock()
        self._parts: dict[str, Part] = {}
        root = presentation.root
        self.default_text_style = root.find(q("p:defaultTextStyle")) if root is not None else None
        self._table_styles = None

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def parse(self, partname: str):
        root = self.package.xml(partname)
        return xmlsafe.resolve_alternate_content(root, _prefer_choice)

    def _related(self, part: Part, kind: str) -> str | None:
        for rel in part.rels.values():
            if rel.kind == kind and not rel.external and self.package.has(rel.target):
                return rel.target
        return None

    def _master(self, partname: str) -> Master:
        part = self._parts.get(partname)
        if part is None:
            part = Master(self, partname, self.parse(partname))
            self._parts[partname] = part
        return part  # type: ignore[return-value]

    def _layout(self, partname: str) -> Layout:
        part = self._parts.get(partname)
        if part is None:
            part = Layout(self, partname, self.parse(partname))
            master = self._related(part, "slideMaster")
            if master:
                try:
                    part.parent = self._master(master)
                except (PackageError, ValueError, SyntaxError) as exc:
                    self.warn(f"slide master {master} unreadable: {exc}")
            self._parts[partname] = part
        return part  # type: ignore[return-value]

    def slide(self, index: int) -> Slide:
        """Parse slide ``index`` (0-based).  Raises on unreadable slide XML."""
        ref = self.presentation.slides[index]
        with self._lock:
            root = self.parse(ref.partname)
            slide = Slide(self, ref.partname, root, index)
            layout = self._related(slide, "slideLayout")
            if layout:
                try:
                    slide.parent = self._layout(layout)
                except (PackageError, ValueError, SyntaxError) as exc:
                    self.warn(f"slide layout {layout} unreadable: {exc}")
            if slide.parent is None:
                slide.parent = self.fallback_master()
            return slide

    def fallback_master(self) -> Master | None:
        main = self.presentation.main_part
        for rel in self.package.rels(main).values():
            if rel.kind == "slideMaster" and self.package.has(rel.target):
                try:
                    return self._master(rel.target)
                except Exception as exc:
                    self.warn(f"slide master {rel.target} unreadable: {exc}")
        return None

    @property
    def table_styles(self):
        if self._table_styles is None:
            self._table_styles = {}
            target = self._related_main("tableStyles")
            if target:
                try:
                    root = self.package.xml(target)
                    for style in root.findall(q("a:tblStyle")):
                        self._table_styles[style.get("styleId", "")] = style
                except Exception:
                    self.warn("table styles unreadable")
        return self._table_styles

    def _related_main(self, kind: str) -> str | None:
        for rel in self.package.rels(self.presentation.main_part).values():
            if rel.kind == kind and self.package.has(rel.target):
                return rel.target
        return None
