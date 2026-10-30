"""Text in binary PowerPoint: strings, style runs and master text styles."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from slidelite.legacy import records as rec
from slidelite.legacy.records import Record

MAX_LEVELS = 5

TITLE, BODY, NOTES, OTHER, CENTER_BODY, CENTER_TITLE, HALF_BODY, QUARTER_BODY = (
    0,
    1,
    2,
    4,
    5,
    6,
    7,
    8,
)


class _Reader:
    """Little-endian fields from a bounded range; reads past the end raise."""

    def __init__(self, data: bytes, at: int = 0) -> None:
        self.data, self.at = data, at

    def take(self, fmt: str):
        value = struct.unpack_from(fmt, self.data, self.at)[0]
        self.at += struct.calcsize(fmt)
        return value

    @property
    def left(self) -> int:
        return len(self.data) - self.at


def _read_pf(r: _Reader) -> dict:
    """TextPFException: the fields present follow the mask, in a fixed order."""
    mask = r.take("<I")
    pf: dict = {}
    if mask & 0xF:
        flags = r.take("<H")
        if mask & 1:
            pf["bullet"] = bool(flags & 1)
        if mask & 2:
            pf["bullet_has_font"] = bool(flags & 2)
        if mask & 4:
            pf["bullet_has_color"] = bool(flags & 4)
        if mask & 8:
            pf["bullet_has_size"] = bool(flags & 8)
    if mask & 0x80:
        pf["bullet_char"] = r.take("<H")
    if mask & 0x10:
        pf["bullet_font"] = r.take("<H")
    if mask & 0x40:
        pf["bullet_size"] = r.take("<h")
    if mask & 0x20:
        pf["bullet_color"] = r.take("<I")
    if mask & 0x800:
        pf["align"] = r.take("<H")
    if mask & 0x1000:
        pf["line"] = r.take("<h")
    if mask & 0x2000:
        pf["before"] = r.take("<h")
    if mask & 0x4000:
        pf["after"] = r.take("<h")
    if mask & 0x100:
        pf["left"] = r.take("<h")
    if mask & 0x400:
        pf["indent"] = r.take("<h")
    if mask & 0x8000:
        pf["tab_size"] = r.take("<H")
    if mask & 0x100000:
        stops = []
        for _ in range(r.take("<H")):
            position = r.take("<H")
            stops.append((position, r.take("<H")))
        pf["tabs"] = stops
    if mask & 0x10000:
        pf["font_align"] = r.take("<H")
    if mask & 0xE0000:
        pf["wrap_flags"] = r.take("<H")
    if mask & 0x200000:
        pf["rtl"] = bool(r.take("<H"))
    return pf


def _read_cf(r: _Reader) -> dict:
    """TextCFException: as above, for character formatting."""
    mask = r.take("<I")
    cf: dict = {}
    if mask & 0xFFFF:
        style = r.take("<H")
        for bit, key in ((1, "bold"), (2, "italic"), (4, "underline"), (0x10, "shadow")):
            if mask & bit:
                cf[key] = bool(style & bit)
    if mask & 0x10000:
        cf["font"] = r.take("<H")
    if mask & 0x200000:
        cf["ea_font"] = r.take("<H")
    if mask & 0x400000:
        cf["ansi_font"] = r.take("<H")
    if mask & 0x800000:
        cf["symbol_font"] = r.take("<H")
    if mask & 0x20000:
        cf["size"] = r.take("<H")
    if mask & 0x40000:
        cf["color"] = r.take("<I")
    if mask & 0x80000:
        cf["position"] = r.take("<h")
    return cf


def parse_style_runs(data: bytes, text_length: int) -> tuple[list, list]:
    """StyleTextPropAtom → ([(length, level, pf)], [(length, cf)]).

    Runs cover the text plus one character for the final paragraph end.  A
    damaged atom keeps the runs read so far; the rest falls back to the master.
    """
    r = _Reader(data)
    paragraphs: list = []
    chars: list = []
    total = text_length + 1
    try:
        covered = 0
        while covered < total and r.left >= 6:
            length = r.take("<I")
            level = r.take("<H")
            paragraphs.append((length, min(level, MAX_LEVELS - 1), _read_pf(r)))
            covered += max(length, 1)
        covered = 0
        while covered < total and r.left >= 4:
            length = r.take("<I")
            chars.append((length, _read_cf(r)))
            covered += max(length, 1)
    except struct.error:
        pass
    return paragraphs, chars


def parse_master_style(record: Record) -> list[tuple[dict, dict]]:
    """TxMasterStyleAtom → per-level (pf, cf), each level cumulative over the last."""
    r = _Reader(record.data)
    levels: list[tuple[dict, dict]] = []
    try:
        count = r.take("<H")
        for i in range(min(count, MAX_LEVELS)):
            if record.instance >= CENTER_BODY:
                r.take("<H")  # explicit level number, only on these text types
            pf = _read_pf(r)
            cf = _read_cf(r)
            if i:
                pf = {**levels[-1][0], **pf}
                cf = {**levels[-1][1], **cf}
            levels.append((pf, cf))
    except struct.error:
        pass
    return levels


def parse_ruler(data: bytes) -> dict[int, dict]:
    """TextRulerAtom → {level: {"left": …, "indent": …}} (older files store margins here)."""
    r = _Reader(data)
    levels: dict[int, dict] = {}
    try:
        mask = r.take("<I")
        if mask & 2:
            r.take("<h")  # level count
        if mask & 1:
            r.take("<H")  # default tab size
        if mask & 4:
            r.at += r.take("<H") * 4
        for level in range(MAX_LEVELS):
            if mask & (1 << (3 + level)):
                levels.setdefault(level, {})["indent"] = r.take("<h")
            if mask & (1 << (8 + level)):
                levels.setdefault(level, {})["left"] = r.take("<h")
    except struct.error:
        pass
    return levels


def parse_numbering(data: bytes) -> list[tuple[int, int] | None]:
    """StyleTextProp9Atom → per paragraph run, its (auto-number scheme, start) or None."""
    r = _Reader(data)
    runs: list[tuple[int, int] | None] = []
    try:
        while r.left >= 4:
            mask = r.take("<I")
            numbered = False
            number = (3, 1)  # 1., 2., 3. unless a scheme says otherwise
            if mask & 0x800000:
                r.take("<h")  # picture bullet
            if mask & 0x2000000:
                numbered = bool(r.take("<H"))
            if mask & 0x1000000:
                number = (r.take("<H"), r.take("<h"))
            runs.append(number if numbered else None)
            if r.take("<I") or r.take("<I"):
                break  # character extensions follow: nothing later is needed
    except struct.error:
        pass
    return runs


@dataclass
class TextBlock:
    text_type: int
    text: str = ""
    paragraphs: list = field(default_factory=list)
    chars: list = field(default_factory=list)
    ruler: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)  # position → "slidenum" | "date"
    links: list = field(default_factory=list)  # (start, end, exHyperlink id)
    outline_ref: int | None = None


def decode_text(atom: Record) -> str:
    if atom.type == rec.TEXT_CHARS_ATOM:
        return atom.data.decode("utf-16-le", "replace")
    return atom.data.decode("latin-1")


def read_blocks(records) -> list[TextBlock]:
    """Group a flat atom sequence into text blocks, one per TextHeaderAtom."""
    blocks: list[TextBlock] = []
    block: TextBlock | None = None
    style: Record | None = None
    pending_link: int | None = None

    def finish() -> None:
        if block is not None and style is not None:
            block.paragraphs, block.chars = parse_style_runs(style.data, len(block.text))

    for atom in records:
        if atom.type == rec.TEXT_HEADER_ATOM:
            finish()
            style = None
            pending_link = None
            kind = struct.unpack("<I", atom.data[:4])[0] if atom.end - atom.start >= 4 else OTHER
            block = TextBlock(kind)
            blocks.append(block)
        elif block is None:
            continue
        elif atom.type in (rec.TEXT_CHARS_ATOM, rec.TEXT_BYTES_ATOM):
            block.text = decode_text(atom)
        elif atom.type == rec.OUTLINE_TEXT_REF_ATOM and atom.end - atom.start >= 4:
            block.outline_ref = struct.unpack("<I", atom.data[:4])[0]
        elif atom.type == rec.STYLE_TEXT_PROP_ATOM:
            style = atom
        elif atom.type == rec.TEXT_RULER_ATOM:
            block.ruler = parse_ruler(atom.data)
        elif atom.type == rec.SLIDE_NUMBER_MC_ATOM and atom.end - atom.start >= 4:
            block.fields[struct.unpack("<I", atom.data[:4])[0]] = "slidenum"
        elif atom.type in (rec.DATETIME_MC_ATOM, rec.GENERIC_DATE_MC_ATOM):
            if atom.end - atom.start >= 4:
                block.fields[struct.unpack("<I", atom.data[:4])[0]] = "date"
        elif atom.type == rec.INTERACTIVE_INFO:
            pending_link = hyperlink_ref(atom)
        elif atom.type == rec.TEXT_INTERACTIVE_INFO_ATOM and atom.end - atom.start >= 8:
            if pending_link is not None:
                begin, end = struct.unpack("<II", atom.data[:8])
                block.links.append((begin, end, pending_link))
            pending_link = None
    finish()
    return blocks


def hyperlink_ref(container: Record) -> int | None:
    """The exHyperlink id of an InteractiveInfo container, if it has one."""
    atom = container.first(rec.INTERACTIVE_INFO_ATOM)
    if atom is None or atom.end - atom.start < 8:
        return None
    return struct.unpack_from("<I", atom.stream, atom.start + 4)[0] or None
