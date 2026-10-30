"""Builds genuine PowerPoint 97–2003 binary files for tests and fixtures.

Real decks can't be committed, so this writes the same structures PowerPoint
does: compound file, persist directory, user edits, records and OfficeArt.
"""

from __future__ import annotations

import struct

MASTER_ID = 0x80000000
FIRST_SLIDE_ID = 256
ONE = 0x10000  # 16.16 fixed point

# Boolean property groups: value bit plus its "is set" bit.
FILLED_ON = (0x01BF, 0x00100010)
FILLED_OFF = (0x01BF, 0x00100000)
LINE_ON = (0x01FF, 0x00080008)
LINE_OFF = (0x01FF, 0x00080000)
SHADOW_ON = (0x023F, 0x00020002)


def rec(rtype: int, payload: bytes = b"", instance: int = 0, version: int = 0) -> bytes:
    head = struct.pack("<HHI", (instance << 4) | version, rtype, len(payload))
    return head + payload


def box(rtype: int, *children: bytes, instance: int = 0) -> bytes:
    return rec(rtype, b"".join(children), instance, 0xF)


def compound_file(streams: dict[str, bytes]) -> bytes:
    """A version 3 compound file; streams under 4096 bytes go to the mini stream."""
    sector = 512
    mini = bytearray()
    mini_fat: list[int] = []
    entries: list[tuple[str, int, int]] = []
    regular: list[tuple[int, bytes]] = []
    for name, data in streams.items():
        if len(data) < 4096:
            first = len(mini) // 64
            count = (len(data) + 63) // 64
            mini_fat += [first + i + 1 for i in range(count - 1)] + ([0xFFFFFFFE] if count else [])
            mini += data.ljust(count * 64, b"\0")
            entries.append((name, first if count else 0xFFFFFFFE, len(data)))
        else:
            regular.append((len(entries), data))
            entries.append((name, 0, len(data)))

    def sectors_of(size: int) -> int:
        return (size + sector - 1) // sector

    directory_size = (len(entries) + 4) // 4 * 4 * 128  # with the root, in whole sectors
    mini_fat_raw = struct.pack(f"<{len(mini_fat)}I", *mini_fat)
    mini_fat_raw += b"\xff" * (-len(mini_fat_raw) % sector)
    plain = sectors_of(directory_size) + sectors_of(len(mini_fat_raw)) + sectors_of(len(mini))
    plain += sum(sectors_of(len(d)) for _, d in regular)
    fat_sectors = 1
    while fat_sectors * sector // 4 < plain + fat_sectors:
        fat_sectors += 1

    fat = [0xFFFFFFFD] * fat_sectors
    body = bytearray()

    def place(data: bytes) -> int:
        first = len(fat)
        count = sectors_of(len(data))
        fat.extend(first + i + 1 for i in range(count - 1))
        fat.append(0xFFFFFFFE)
        body.extend(data.ljust(count * sector, b"\0"))
        return first

    directory_at = len(fat)
    fat.extend(directory_at + i + 1 for i in range(sectors_of(directory_size) - 1))
    fat.append(0xFFFFFFFE)
    body.extend(b"\0" * (sectors_of(directory_size) * sector))
    mini_fat_at = place(mini_fat_raw) if mini_fat else 0xFFFFFFFE
    mini_at = place(bytes(mini)) if mini else 0xFFFFFFFE
    for index, data in regular:
        name, _, size = entries[index]
        entries[index] = (name, place(data), size)
    fat_raw = struct.pack(f"<{len(fat)}I", *fat)
    fat_raw += b"\xff" * (fat_sectors * sector - len(fat_raw))

    def entry(name: str, kind: int, start: int, size: int, right: int, child: int) -> bytes:
        raw = name.encode("utf-16-le") + b"\0\0"
        record = bytearray(128)
        record[: len(raw)] = raw
        struct.pack_into("<HBB", record, 64, len(raw), kind, 1)
        struct.pack_into("<III", record, 68, 0xFFFFFFFF, right, child)
        struct.pack_into("<IQ", record, 116, start, size)
        return bytes(record)

    table = entry("Root Entry", 5, mini_at, len(mini), 0xFFFFFFFF, 1 if entries else 0xFFFFFFFF)
    for i, (name, start, size) in enumerate(entries):
        right = i + 2 if i + 1 < len(entries) else 0xFFFFFFFF
        table += entry(name, 2, start, size, right, 0xFFFFFFFF)
    table = table.ljust(directory_size, b"\0")
    body[: len(table)] = table

    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 44, fat_sectors)
    struct.pack_into("<I", header, 48, directory_at)
    struct.pack_into(
        "<III", header, 56, 4096, mini_fat_at, sectors_of(len(mini_fat_raw)) if mini_fat else 0
    )
    struct.pack_into("<II", header, 68, 0xFFFFFFFE, 0)
    difat = list(range(fat_sectors)) + [0xFFFFFFFF] * (109 - fat_sectors)
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header) + fat_raw + bytes(body)


# -- text --------------------------------------------------------------------------
def pf(**fields) -> bytes:
    """TextPFException: mask, then the fields present in their fixed order."""
    mask, out = 0, b""
    flags = 0
    for bit, key in (
        (1, "bullet"),
        (2, "bullet_has_font"),
        (4, "bullet_has_color"),
        (8, "bullet_has_size"),
    ):
        if key in fields:
            mask |= bit
            flags |= bit if fields[key] else 0
    if mask:
        out += struct.pack("<H", flags)
    for key, bit, fmt in (
        ("bullet_char", 0x80, "<H"), ("bullet_font", 0x10, "<H"), ("bullet_size", 0x40, "<h"),
        ("bullet_color", 0x20, "<I"), ("align", 0x800, "<H"), ("line", 0x1000, "<h"),
        ("before", 0x2000, "<h"), ("after", 0x4000, "<h"), ("left", 0x100, "<h"),
        ("indent", 0x400, "<h"),
    ):  # fmt: skip
        if key in fields:
            mask |= bit
            out += struct.pack(fmt, fields[key])
    return struct.pack("<I", mask) + out


def cf(**fields) -> bytes:
    """TextCFException: as above, for character formatting."""
    mask, style = 0, 0
    for bit, key in ((1, "bold"), (2, "italic"), (4, "underline")):
        if key in fields:
            mask |= bit
            style |= bit if fields[key] else 0
    out = struct.pack("<H", style) if mask else b""
    for key, bit, fmt in (
        ("font", 0x10000, "<H"), ("symbol_font", 0x800000, "<H"), ("size", 0x20000, "<H"),
        ("color", 0x40000, "<I"), ("position", 0x80000, "<h"),
    ):  # fmt: skip
        if key in fields:
            mask |= bit
            out += struct.pack(fmt, fields[key])
    return struct.pack("<I", mask) + out


def rgb(red: int, green: int, blue: int) -> int:
    """A ColorIndexStruct holding a literal colour."""
    return red | green << 8 | blue << 16 | 0xFE << 24


def scheme_color(index: int) -> int:
    return index << 24


def style_runs(paragraphs: list[tuple[int, int, bytes]], chars: list[tuple[int, bytes]]) -> bytes:
    out = b""
    for length, level, fields in paragraphs:
        out += struct.pack("<IH", length, level) + fields
    for length, fields in chars:
        out += struct.pack("<I", length) + fields
    return out


def text_atoms(
    text: str,
    text_type: int = 4,
    paragraphs: list | None = None,
    chars: list | None = None,
    extra: bytes = b"",
) -> bytes:
    """Header, characters and style atoms for one text body."""
    wide = any(ord(c) > 0xFF for c in text)
    atom = rec(0x0FA0, text.encode("utf-16-le")) if wide else rec(0x0FA8, text.encode("latin-1"))
    out = rec(0x0F9F, struct.pack("<I", text_type)) + atom
    if paragraphs is not None or chars is not None:
        total = len(text) + 1
        out += rec(
            0x0FA1,
            style_runs(paragraphs or [(total, 0, pf())], chars or [(total, cf())]),
        )
    return out + extra


def master_style(instance: int, levels: list[tuple[bytes, bytes]]) -> bytes:
    body = struct.pack("<H", len(levels))
    for number, (para, char) in enumerate(levels):
        if instance >= 5:
            body += struct.pack("<H", number)
        body += para + char
    return rec(0x0FA3, body, instance)


# -- drawings -----------------------------------------------------------------------
def properties(items: dict | list) -> bytes:
    """An FOPT record; bytes values become complex properties."""
    pairs = list(items.items()) if isinstance(items, dict) else list(items)
    table, tail = b"", b""
    for pid, value in pairs:
        if isinstance(value, bytes):
            table += struct.pack("<HI", pid | 0x8000, len(value))
            tail += value
        else:
            table += struct.pack("<HI", pid, value & 0xFFFFFFFF)
    return rec(0xF00B, table + tail, len(pairs), 3)


def array(items: list[bytes], element: int) -> bytes:
    return struct.pack("<HHH", len(items), len(items), element) + b"".join(items)


def shape(
    spid: int,
    kind: int = 1,
    anchor: tuple[int, int, int, int] | None = None,
    props: dict | list | None = None,
    text: bytes | None = None,
    placeholder: int = 0,
    flags: int = 0xA00,
    child_anchor: bool = False,
    client: bytes = b"",
) -> bytes:
    """An SpContainer. ``anchor`` is (left, top, right, bottom) in master units."""
    parts = [rec(0xF00A, struct.pack("<II", spid, flags), kind, 2)]
    if props:
        parts.append(properties(props))
    if anchor is not None:
        left, top, right, bottom = anchor
        if child_anchor:
            parts.append(rec(0xF00F, struct.pack("<4i", left, top, right, bottom)))
        else:
            parts.append(rec(0xF010, struct.pack("<4h", top, left, right, bottom)))
    data = client
    if placeholder:
        data += rec(0x0BC3, struct.pack("<iBBH", 0, placeholder, 0, 0))
    if data:
        parts.append(box(0xF011, data))
    if text is not None:
        parts.append(box(0xF00D, text))
    return box(0xF004, *parts)


def group(spid: int, anchor, rect, *children: bytes, child_anchor: bool = False) -> bytes:
    head = rec(0xF009, struct.pack("<4i", *rect))
    sp = rec(0xF00A, struct.pack("<II", spid, 0x201 | (0x2 if child_anchor else 0)), 0, 2)
    left, top, right, bottom = anchor
    if child_anchor:
        place = rec(0xF00F, struct.pack("<4i", left, top, right, bottom))
    else:
        place = rec(0xF010, struct.pack("<4h", top, left, right, bottom))
    return box(0xF003, box(0xF004, head, sp, place), *children)


def drawing(shapes: list[bytes], background: bytes | None = None) -> bytes:
    patriarch = box(
        0xF004,
        rec(0xF009, struct.pack("<4i", 0, 0, 5760, 4320)),
        rec(0xF00A, struct.pack("<II", 1024, 0x005), 0, 2),
    )
    parts = [
        rec(0xF008, struct.pack("<II", len(shapes) + 1, 2048)),
        box(0xF003, patriarch, *shapes),
    ]
    if background is not None:
        parts.append(background)
    return box(0x040C, box(0xF002, *parts))


def color_scheme(colors: list[int]) -> bytes:
    return rec(0x07F0, b"".join(struct.pack("<I", c) for c in colors), 1)


DEFAULT_SCHEME = [0xFFFFFF, 0x000000, 0x808080, 0x000000, 0xE3E0BB, 0x993333, 0x999900, 0x00CC99]


def slide_atom(master: int = MASTER_ID, flags: int = 7) -> bytes:
    return rec(0x03EF, struct.pack("<i8BIIHH", 0, *[0] * 8, master, 0, flags, 0), version=2)


def slide_info(effect=0, direction=0, flags=0x1, speed=1, advance=0) -> bytes:
    payload = struct.pack("<iIBBHB3x", advance, 0, direction, effect, flags, speed)
    return rec(0x03F9, payload)


def timeline_tag(root: bytes) -> bytes:
    name = rec(0x0FBA, "___PPT10".encode("utf-16-le"))
    return box(0x1388, box(0x138A, name, box(0x138B, rec(0x2B00, b"\0" * 4), root)))


def numbering_tag(atom: bytes) -> bytes:
    name = rec(0x0FBA, "___PPT9".encode("utf-16-le"))
    return box(0x1388, box(0x138A, name, box(0x138B, rec(0x0FAC, atom))))


def animation_info(
    order: int, effect: int, direction: int = 0, build: int = 1, flags: int = 0x400
) -> bytes:
    payload = struct.pack(
        "<IIIiHHBBBBBB2x", 0, flags, 0, 0, order, 1, build, effect, direction, 0, 0, 0
    )
    return box(0x1014, rec(0x0FF1, payload, version=1))


def hyperlink_data(link_id: int) -> bytes:
    atom = struct.pack("<IIBBBBBBBB", 0, link_id, 4, 0, 0, 0, 0, 8, 0, 0)
    return box(0x0FF2, rec(0x0FF3, atom))


def blip_png(data: bytes) -> bytes:
    return rec(0xF01E, b"\0" * 16 + b"\xff" + data, 0x6E0)


def blip_wmf(data: bytes, bounds=(0, 0, 1000, 1000)) -> bytes:
    import zlib

    packed = zlib.compress(data)
    header = struct.pack("<I4i2iIBB", len(data), *bounds, 914400, 914400, len(packed), 0, 0xFE)
    return rec(0xF01B, b"\0" * 16 + header + packed, 0x216)


def bse(blip_type: int, size: int, offset: int) -> bytes:
    payload = struct.pack(
        "<BB16sHIIIBBBB", blip_type, blip_type, b"\0" * 16, 0xFF, size, 1, offset, 0, 0, 0, 0
    )
    return rec(0xF007, payload, blip_type, 2)


def sample_deck() -> bytes:
    """A small deck for smoke tests: a master, a title, bullets and a coloured shape."""
    deck = Deck(title="Binary Sample")
    deck.add_master(
        color_scheme(DEFAULT_SCHEME),
        master_style(0, [(pf(align=1), cf(font=0, size=44))]),
        master_style(1, [(pf(bullet=True, bullet_char=0x2022, left=216), cf(font=0, size=28))]),
        shapes=[
            shape(10, 1, (0, 4176, 5760, 4320), {0x181: 0x993333}),
            shape(11, 1, (288, 432, 5472, 1152), {0x87: 1}, placeholder=1),
            shape(12, 1, (288, 1296, 5472, 3600), placeholder=2),
        ],
    )
    deck.add_slide(
        shape(20, 1, (288, 432, 5472, 1152), text=text_atoms("PowerPoint 97-2003", 0), placeholder=13),
        shape(
            21, 1, (288, 1296, 5472, 3600), placeholder=14,
            text=text_atoms("Opened natively\rNo conversion tools", 1),
        ),
        shape(22, 3, (3888, 2880, 5184, 3744), {0x181: 0x0099FF}),
    )  # fmt: skip
    return deck.build()


class Deck:
    """Accumulates pages, then lays out the document stream the way PowerPoint does."""

    def __init__(self, title: str = "Binary Deck", fonts=("Arial", "Times New Roman", "Wingdings")):
        self.title = title
        self.fonts = list(fonts)
        self.masters: list[bytes] = []
        self.slides: list[bytes] = []
        self.pictures: list[tuple[int, bytes]] = []
        self.links: list[tuple[int, str]] = []

    def add_picture(self, blip: bytes, blip_type: int) -> int:
        self.pictures.append((blip_type, blip))
        return len(self.pictures)

    def add_master(self, *children: bytes, shapes=(), background=None) -> None:
        self.masters.append(
            box(0x03F8, slide_atom(0, 0), *children, drawing(list(shapes), background))
        )

    def add_slide(self, *shapes: bytes, atom=None, extra=(), background=None, scheme=None) -> None:
        parts = [atom or slide_atom()]
        parts += list(extra)
        parts.append(drawing(list(shapes), background))
        if scheme is not None:
            parts.append(color_scheme(scheme))
        self.slides.append(box(0x03EE, *parts))

    def _environment(self) -> bytes:
        entries = []
        for index, name in enumerate(self.fonts):
            raw = name.encode("utf-16-le").ljust(64, b"\0")[:64]
            entries.append(rec(0x0FB7, raw + b"\0\0\0\0", index))
        return box(0x03F2, box(0x07D5, *entries), master_style(4, [(pf(), cf(font=0, size=18))]))

    def _pictures(self) -> tuple[bytes, bytes]:
        stream = b""
        entries = []
        for blip_type, blip in self.pictures:
            entries.append(bse(blip_type, len(blip), len(stream)))
            stream += blip
        store = box(0xF001, *entries, instance=len(entries))
        return box(0x040B, box(0xF000, store)), stream

    def _links(self) -> bytes:
        if not self.links:
            return b""
        items = []
        for link_id, url in self.links:
            items.append(
                box(
                    0x0FD7,
                    rec(0x0FD3, struct.pack("<I", link_id)),
                    rec(0x0FBA, url.encode("utf-16-le"), 1),
                )
            )
        return box(0x0409, *items)

    def build(self, encrypted: bool = False, edit: tuple[int, bytes] | None = None) -> bytes:
        """``edit`` appends a fast save replacing slide N (1-based) with a new container."""
        stream = bytearray()
        persist: dict[int, int] = {}
        drawing_group, picture_stream = self._pictures()

        master_refs = list(range(2, 2 + len(self.masters)))
        first_slide_ref = 2 + len(self.masters)
        slide_refs = list(range(first_slide_ref, first_slide_ref + len(self.slides)))

        def persist_list(instance, refs, ids) -> bytes:
            atoms = [
                rec(0x03F3, struct.pack("<IIiII", ref, 0, 0, slide_id, 0))
                for ref, slide_id in zip(refs, ids, strict=True)
            ]
            return box(0x0FF0, *atoms, instance=instance)

        atom = struct.pack("<ii", 5760, 4320) + struct.pack("<ii", 3240, 4320)
        atom += struct.pack("<iiII", 1, 2, 0, 0) + struct.pack("<HH", 1, 0) + b"\0" * 4
        document = box(
            0x03E8,
            rec(0x03E9, atom, version=1),
            self._environment(),
            drawing_group,
            self._links(),
            persist_list(1, master_refs, [MASTER_ID + i for i in range(len(self.masters))]),
            persist_list(0, slide_refs, [FIRST_SLIDE_ID + i for i in range(len(self.slides))]),
            rec(0x03EA),
        )
        persist[1] = len(stream)
        stream += document
        for ref, page in zip(master_refs + slide_refs, self.masters + self.slides, strict=True):
            persist[ref] = len(stream)
            stream += page

        def write_edit(entries: dict[int, int], previous: int) -> int:
            directory_at = len(stream)
            body = b""
            for ref, offset in sorted(entries.items()):
                body += struct.pack("<II", ref | 1 << 20, offset)
            stream.extend(rec(0x1772, body))
            edit_at = len(stream)
            stream.extend(
                rec(
                    0x0FF5,
                    struct.pack(
                        "<IHBBIIIIHH", 0, 0, 0, 3, previous, directory_at, 1,
                        first_slide_ref + len(self.slides), 1, 0,
                    ),
                )
            )  # fmt: skip
            return edit_at

        last = write_edit(persist, 0)
        if edit is not None:
            number, container = edit
            offset = len(stream)
            stream += container
            last = write_edit({first_slide_ref + number - 1: offset}, last)

        token = 0xF3D1C4DF if encrypted else 0xE391C05F
        user = rec(0x0FF6, struct.pack("<IIIIHHHH", 20, token, last, 0, 8, 0, 0x3F4, 3))
        title = self.title.encode("cp1252")
        section = struct.pack("<IIII", 16 + 8 + len(title) + 4, 1, 2, 16)
        section += struct.pack("<II", 30, len(title) + 1) + title + b"\0"
        summary = struct.pack("<HHI16sI", 0xFFFE, 0, 0, b"\0" * 16, 1)
        summary += b"\0" * 16 + struct.pack("<I", 48) + section

        streams = {
            "Current User": user,
            "PowerPoint Document": bytes(stream),
            "\x05SummaryInformation": summary,
        }
        if picture_stream:
            streams["Pictures"] = picture_stream
        return compound_file(streams)
