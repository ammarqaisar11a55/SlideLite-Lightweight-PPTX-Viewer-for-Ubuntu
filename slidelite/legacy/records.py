"""PowerPoint binary records: the type/instance/length tree of [MS-PPT]."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

MAX_DEPTH = 48
HEADER = struct.Struct("<HHI")

# Record types of the document stream.
DOCUMENT = 0x03E8
DOCUMENT_ATOM = 0x03E9
SLIDE = 0x03EE
SLIDE_ATOM = 0x03EF
NOTES = 0x03F0
ENVIRONMENT = 0x03F2
SLIDE_PERSIST_ATOM = 0x03F3
MAIN_MASTER = 0x03F8
SLIDE_SHOW_SLIDE_INFO_ATOM = 0x03F9
DRAWING_GROUP = 0x040B
DRAWING = 0x040C
FONT_COLLECTION = 0x07D5
COLOR_SCHEME_ATOM = 0x07F0
OE_PLACEHOLDER_ATOM = 0x0BC3
OUTLINE_TEXT_REF_ATOM = 0x0F9E
TEXT_HEADER_ATOM = 0x0F9F
TEXT_CHARS_ATOM = 0x0FA0
STYLE_TEXT_PROP_ATOM = 0x0FA1
TX_MASTER_STYLE_ATOM = 0x0FA3
TEXT_RULER_ATOM = 0x0FA6
TEXT_BYTES_ATOM = 0x0FA8
STYLE_TEXT_PROP_9_ATOM = 0x0FAC
FONT_ENTITY_ATOM = 0x0FB7
CSTRING = 0x0FBA
EX_OBJ_LIST = 0x0409
EX_HYPERLINK_ATOM = 0x0FD3
EX_HYPERLINK = 0x0FD7
SLIDE_NUMBER_MC_ATOM = 0x0FD8
TEXT_INTERACTIVE_INFO_ATOM = 0x0FDF
SLIDE_LIST_WITH_TEXT = 0x0FF0
INTERACTIVE_INFO = 0x0FF2
INTERACTIVE_INFO_ATOM = 0x0FF3
USER_EDIT_ATOM = 0x0FF5
DATETIME_MC_ATOM = 0x0FF7
GENERIC_DATE_MC_ATOM = 0x0FF8
PERSIST_DIRECTORY_ATOM = 0x1772
ANIMATION_INFO = 0x1014
ANIMATION_INFO_ATOM = 0x0FF1
PROG_TAGS = 0x1388
PROG_BINARY_TAG = 0x138A
BINARY_TAG_DATA = 0x138B
TIME_NODE = 0xF144

# OfficeArt (drawing) records.
DGG_CONTAINER = 0xF000
BSTORE_CONTAINER = 0xF001
DG_CONTAINER = 0xF002
SPGR_CONTAINER = 0xF003
SP_CONTAINER = 0xF004
BSE = 0xF007
SPGR = 0xF009
SP = 0xF00A
FOPT = 0xF00B
CLIENT_TEXTBOX = 0xF00D
CHILD_ANCHOR = 0xF00F
CLIENT_ANCHOR = 0xF010
CLIENT_DATA = 0xF011
TERTIARY_FOPT = 0xF122


@dataclass(frozen=True, slots=True)
class Record:
    """One record; ``data`` covers its body within the shared stream."""

    type: int
    version: int
    instance: int
    stream: bytes
    start: int
    end: int

    @property
    def data(self) -> bytes:
        return self.stream[self.start : self.end]

    @property
    def is_container(self) -> bool:
        return self.version == 0xF

    def children(self) -> Iterator[Record]:
        return iter_records(self.stream, self.start, self.end)

    def first(self, record_type: int) -> Record | None:
        for child in self.children():
            if child.type == record_type:
                return child
        return None

    def all(self, record_type: int) -> list[Record]:
        return [c for c in self.children() if c.type == record_type]

    def walk(self, depth: int = 0) -> Iterator[Record]:
        """Every descendant, depth first."""
        if depth >= MAX_DEPTH:
            return
        for child in self.children():
            yield child
            if child.is_container:
                yield from child.walk(depth + 1)


def read_header(stream: bytes, offset: int, end: int | None = None) -> Record | None:
    """The record at ``offset``, or None if no complete header fits there."""
    limit = len(stream) if end is None else end
    if offset < 0 or offset + 8 > limit:
        return None
    ver_inst, rec_type, length = HEADER.unpack_from(stream, offset)
    body = offset + 8
    # A truncated last record keeps whatever bytes are really there.
    return Record(rec_type, ver_inst & 0xF, ver_inst >> 4, stream, body, min(body + length, limit))


def iter_records(stream: bytes, start: int, end: int) -> Iterator[Record]:
    offset = start
    while True:
        record = read_header(stream, offset, end)
        if record is None:
            return
        yield record
        offset = record.end
