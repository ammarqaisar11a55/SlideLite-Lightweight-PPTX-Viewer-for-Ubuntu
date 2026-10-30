"""The binary presentation as a model: pages, masters, styles, fonts and links."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from slidelite.legacy import officeart, persist, pictures, text
from slidelite.legacy import records as rec
from slidelite.legacy.cfb import CompoundFile
from slidelite.legacy.officeart import Shape
from slidelite.legacy.records import Record

DEFAULT_SCHEME = ("FFFFFF", "000000", "808080", "000000", "BBE0E3", "333399", "009999", "99CC00")

_F_MASTER_OBJECTS, _F_MASTER_SCHEME, _F_MASTER_BACKGROUND = 1, 2, 4
_F_HIDDEN, _F_MANUAL, _F_AUTO = 0x4, 0x1, 0x400


@dataclass
class Transition:
    effect: int
    direction: int
    speed: int
    advance_ms: int | None
    on_click: bool


@dataclass
class Page:
    """A slide or a master."""

    record: Record
    slide_id: int = 0
    is_master: bool = False
    master_ref: int = 0
    show_master_shapes: bool = True
    use_master_scheme: bool = True
    use_master_background: bool = True
    hidden: bool = False
    scheme: tuple[str, ...] | None = None
    styles: dict[int, list] = field(default_factory=dict)
    root: Shape | None = None
    background: Shape | None = None
    transition: Transition | None = None
    outline: list[text.TextBlock] = field(default_factory=list)
    master: Page | None = None
    timeline: Record | None = None

    @property
    def shapes(self) -> list[Shape]:
        return self.root.children if self.root is not None else []


@dataclass
class Deck:
    width: int  # master units
    height: int
    first_slide_number: int
    slides: list[Page]
    masters: list[Page]
    fonts: dict[int, str]
    defaults: dict[int, list]  # text styles from the Environment
    links: dict[int, str]
    pictures: dict[int, Record]
    title: str | None
    issues: list[str]


def _u32(record: Record, offset: int = 0) -> int:
    return struct.unpack_from("<I", record.stream, record.start + offset)[0]


def _read_scheme(atom: Record) -> tuple[str, ...]:
    data = atom.data
    return tuple(data[i * 4 : i * 4 + 3].hex().upper() for i in range(8))


def _read_page(record: Record, slide_id: int, outline: list) -> Page:
    page = Page(record, slide_id, outline=outline)
    page.is_master = record.type == rec.MAIN_MASTER
    for child in record.children():
        size = child.end - child.start
        if child.type == rec.SLIDE_ATOM and size >= 20:
            page.master_ref = _u32(child, 12)
            flags = struct.unpack_from("<H", child.stream, child.start + 20)[0] if size >= 22 else 7
            page.show_master_shapes = bool(flags & _F_MASTER_OBJECTS)
            page.use_master_scheme = bool(flags & _F_MASTER_SCHEME)
            page.use_master_background = bool(flags & _F_MASTER_BACKGROUND)
        elif child.type == rec.COLOR_SCHEME_ATOM and child.instance == 1 and size >= 32:
            page.scheme = _read_scheme(child)
        elif child.type == rec.TX_MASTER_STYLE_ATOM:
            page.styles[child.instance] = text.parse_master_style(child)
        elif child.type == rec.SLIDE_SHOW_SLIDE_INFO_ATOM and size >= 16:
            _read_slide_info(page, child)
    page.root, page.background = officeart.read_drawing(record.first(rec.DRAWING))
    page.timeline = _read_timeline(record)
    return page


def _read_timeline(record: Record) -> Record | None:
    tags = record.first(rec.PROG_TAGS)
    for tag in tags.all(rec.PROG_BINARY_TAG) if tags is not None else []:
        data = tag.first(rec.BINARY_TAG_DATA)
        node = data.first(rec.TIME_NODE) if data is not None else None
        if node is not None:
            return node
    return None


def _read_slide_info(page: Page, atom: Record) -> None:
    advance, _sound, direction, effect, flags, speed = struct.unpack_from(
        "<iIBBHB", atom.stream, atom.start
    )
    page.hidden = bool(flags & _F_HIDDEN)
    auto = bool(flags & _F_AUTO)
    if effect or auto:
        page.transition = Transition(
            effect,
            direction,
            speed,
            max(advance, 0) if auto else None,
            bool(flags & _F_MANUAL) or not auto,
        )


def _outline_lists(document: Record) -> dict[int, list[tuple]]:
    """Per SlideListWithText instance: [(persist ref, slide id, text blocks)]."""
    lists: dict[int, list[tuple]] = {}
    for section in document.all(rec.SLIDE_LIST_WITH_TEXT):
        entries = lists.setdefault(section.instance, [])
        current: list[Record] | None = None
        for child in section.children():
            if child.type == rec.SLIDE_PERSIST_ATOM and child.end - child.start >= 16:
                current = []
                entries.append((_u32(child), _u32(child, 12), current))
            elif current is not None:
                current.append(child)
    return lists


def _read_fonts(document: Record) -> dict[int, str]:
    fonts: dict[int, str] = {}
    environment = document.first(rec.ENVIRONMENT)
    collection = environment.first(rec.FONT_COLLECTION) if environment is not None else None
    if collection is None:
        return fonts
    for position, atom in enumerate(collection.all(rec.FONT_ENTITY_ATOM)):
        name = atom.data[:64].decode("utf-16-le", "replace").split("\0", 1)[0].strip()
        if name:
            fonts[atom.instance or position] = name
    return fonts


def _read_links(document: Record) -> dict[int, str]:
    links: dict[int, str] = {}
    objects = document.first(rec.EX_OBJ_LIST)
    if objects is None:
        return links
    for entry in objects.all(rec.EX_HYPERLINK):
        atom = entry.first(rec.EX_HYPERLINK_ATOM)
        target = next((s for s in entry.all(rec.CSTRING) if s.instance == 1), None)
        if atom is not None and target is not None and atom.end - atom.start >= 4:
            url = target.data.decode("utf-16-le", "replace").strip("\0 ")
            if url:
                links[_u32(atom)] = url
    return links


def read_summary_title(blob: bytes) -> str | None:
    """PIDSI_TITLE from the SummaryInformation property set."""
    try:
        section = struct.unpack_from("<I", blob, 44)[0]
        count = struct.unpack_from("<I", blob, section + 4)[0]
        for i in range(min(count, 256)):
            pid, offset = struct.unpack_from("<II", blob, section + 8 + i * 8)
            if pid != 2:
                continue
            kind, size = struct.unpack_from("<II", blob, section + offset)
            raw = blob[section + offset + 8 : section + offset + 8 + size]
            if kind == 31:
                return raw.decode("utf-16-le", "replace").split("\0", 1)[0].strip() or None
            if kind == 30:
                return raw.decode("cp1252", "replace").split("\0", 1)[0].strip() or None
    except struct.error:
        pass
    return None


def read(data: bytes) -> Deck:
    compound = CompoundFile(data)
    if not compound.has("Current User"):
        raise persist.LegacyParseError("no Current User stream")
    stream = compound.read("PowerPoint Document")
    persist_map, document_ref = persist.read_persist_map(compound.read("Current User"), stream)
    document = persist.load_document(stream, persist_map, document_ref)

    atom = document.first(rec.DOCUMENT_ATOM)
    if atom is None or atom.end - atom.start < 36:
        raise persist.LegacyParseError("no document atom")
    width, height = struct.unpack_from("<ii", stream, atom.start)
    first_number = struct.unpack_from("<H", stream, atom.start + 32)[0]

    issues: list[str] = []
    outlines = _outline_lists(document)
    masters_by_id: dict[int, Page] = {}
    masters: list[Page] = []
    for ref, slide_id, atoms in outlines.get(1, []):
        record = persist.resolve(stream, persist_map, ref)
        if record is None or record.type not in (rec.MAIN_MASTER, rec.SLIDE):
            issues.append(f"master {ref} is missing")
            continue
        page = _read_page(record, slide_id, text.read_blocks(atoms))
        page.is_master = True
        masters.append(page)
        masters_by_id[slide_id] = page
    for page in masters:  # a title master sits on top of a main master
        if page.record.type == rec.SLIDE:
            page.master = masters_by_id.get(page.master_ref)

    slides: list[Page] = []
    for ref, slide_id, atoms in outlines.get(0, []):
        record = persist.resolve(stream, persist_map, ref)
        if record is None or record.type != rec.SLIDE:
            issues.append(f"slide {len(slides) + 1} is missing")
            continue
        try:
            page = _read_page(record, slide_id, text.read_blocks(atoms))
        except (struct.error, ValueError, IndexError) as exc:
            issues.append(f"slide {len(slides) + 1}: {exc}")
            page = Page(record, slide_id)
        page.master = masters_by_id.get(page.master_ref) or (masters[0] if masters else None)
        slides.append(page)

    environment = document.first(rec.ENVIRONMENT)
    defaults: dict[int, list] = {}
    if environment is not None:
        for style in environment.all(rec.TX_MASTER_STYLE_ATOM):
            defaults[style.instance] = text.parse_master_style(style)

    pictures_stream = compound.read("Pictures") if compound.has("Pictures") else b""
    summary = (
        compound.read("\x05SummaryInformation") if compound.has("\x05SummaryInformation") else b""
    )
    return Deck(
        width=width,
        height=height,
        first_slide_number=first_number,
        slides=slides,
        masters=masters,
        fonts=_read_fonts(document),
        defaults=defaults,
        links=_read_links(document),
        pictures=pictures.load_store(document, pictures_stream),
        title=read_summary_title(summary),
        issues=issues,
    )
