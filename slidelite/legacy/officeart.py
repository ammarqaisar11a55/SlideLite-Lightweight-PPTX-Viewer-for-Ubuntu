"""OfficeArt drawings: the shape tree and its property tables."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from slidelite.legacy import records as rec
from slidelite.legacy.records import MAX_DEPTH, Record
from slidelite.legacy.text import TextBlock, hyperlink_ref, parse_numbering, read_blocks

MAX_SHAPES = 100_000

SP_GROUP, SP_DELETED, SP_FLIP_H, SP_FLIP_V, SP_BACKGROUND = 0x1, 0x8, 0x40, 0x80, 0x400

# Boolean property groups: value bits in the low word, "is set" bits in the high word.
TEXT_BOOLS = 0x00BF
FILL_BOOLS = 0x01BF
LINE_BOOLS = 0x01FF
SHADOW_BOOLS = 0x023F
FIT_SHAPE_TO_TEXT, FILLED, LINE_ON, SHADOW_ON = 1, 4, 3, 1


@dataclass
class Props:
    simple: dict[int, int] = field(default_factory=dict)
    complex: dict[int, bytes] = field(default_factory=dict)

    def get(self, pid: int, default: int | None = None) -> int | None:
        return self.simple.get(pid, default)

    def signed(self, pid: int, default: int = 0) -> int:
        value = self.simple.get(pid)
        if value is None:
            return default
        return value - (1 << 32) if value & 0x80000000 else value

    def flag(self, group: int, bit: int, default: bool) -> bool:
        value = self.simple.get(group)
        if value is None:
            return default
        if value & (1 << (bit + 16)):
            return bool(value & (1 << bit))
        if value >> 16:
            return default  # other flags are declared, this one is not
        return bool(value & (1 << bit)) or default  # writers that omit the "is set" bits

    def merged_over(self, base: Props) -> Props:
        return Props({**base.simple, **self.simple}, {**base.complex, **self.complex})


def read_properties(record: Record, props: Props) -> None:
    data = record.data
    count = min(record.instance, len(data) // 6)
    tail = count * 6
    for i in range(count):
        word, value = struct.unpack_from("<HI", data, i * 6)
        pid = word & 0x3FFF
        if word & 0x8000:
            props.complex[pid] = data[tail : tail + value]
            tail += value
        else:
            props.simple[pid] = value


def array_items(blob: bytes | None) -> list[bytes]:
    """An IMsoArray: count, reserved count and element size, then the elements."""
    if not blob or len(blob) < 6:
        return []
    count, _alloc, size = struct.unpack_from("<HHH", blob, 0)
    if size == 0xFFF0:  # shorthand for 4-byte elements stored as 2-byte pairs
        size = 4
    if size <= 0:
        return []
    count = min(count, (len(blob) - 6) // size)
    return [blob[6 + i * size : 6 + (i + 1) * size] for i in range(count)]


@dataclass
class Shape:
    kind: int = 0  # MSOSPT shape type
    spid: int = 0
    flags: int = 0
    props: Props = field(default_factory=Props)
    anchor: tuple[int, int, int, int] | None = None  # left, top, right, bottom
    is_child_anchor: bool = False
    placeholder: int = 0  # OEPlaceholderAtom placement id
    placeholder_pos: int = -1
    blocks: list[TextBlock] = field(default_factory=list)
    link: int | None = None  # exHyperlink id
    animation: tuple | None = None  # (order, flags, build, effect, direction)
    # Groups only:
    children: list[Shape] = field(default_factory=list)
    group_rect: tuple[int, int, int, int] | None = None

    @property
    def text(self) -> TextBlock | None:
        return self.blocks[0] if self.blocks else None

    @property
    def flip_h(self) -> bool:
        return bool(self.flags & SP_FLIP_H)

    @property
    def flip_v(self) -> bool:
        return bool(self.flags & SP_FLIP_V)

    @property
    def is_group(self) -> bool:
        return bool(self.flags & SP_GROUP)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def _read_anchor(record: Record) -> tuple[int, int, int, int] | None:
    data = record.data
    if len(data) >= 16:
        top, left, right, bottom = struct.unpack_from("<4i", data)
    elif len(data) >= 8:
        top, left, right, bottom = struct.unpack_from("<4h", data)
    else:
        return None
    return left, top, right, bottom


def _read_rect(record: Record) -> tuple[int, int, int, int] | None:
    data = record.data
    return struct.unpack_from("<4i", data) if len(data) >= 16 else None


def _read_animation(container: Record) -> tuple | None:
    atom = container.first(rec.ANIMATION_INFO_ATOM)
    if atom is None or atom.end - atom.start < 28:
        return None
    flags = struct.unpack_from("<I", atom.stream, atom.start + 4)[0]
    order = struct.unpack_from("<H", atom.stream, atom.start + 16)[0]
    build, effect, direction = struct.unpack_from("<BBB", atom.stream, atom.start + 20)
    return order, flags, build, effect, direction


def _apply_numbering(shape: Shape, tags: Record) -> None:
    """Numbered lists live in a tag beside the text, one entry per paragraph run."""
    for tag in tags.all(rec.PROG_BINARY_TAG):
        data = tag.first(rec.BINARY_TAG_DATA)
        atom = data.first(rec.STYLE_TEXT_PROP_9_ATOM) if data is not None else None
        if atom is None or shape.text is None:
            continue
        for number, (_length, _level, pf) in zip(
            parse_numbering(atom.data), shape.text.paragraphs, strict=False
        ):
            if number is not None:
                pf["number"] = number


def read_shape(container: Record) -> Shape:
    shape = Shape()
    tags: Record | None = None
    for child in container.children():
        if child.type == rec.SP:
            data = child.data
            if len(data) >= 8:
                shape.kind = child.instance
                shape.spid, shape.flags = struct.unpack_from("<II", data)
        elif child.type in (rec.FOPT, rec.TERTIARY_FOPT):
            read_properties(child, shape.props)
        elif child.type == rec.CLIENT_ANCHOR:
            shape.anchor = shape.anchor or _read_anchor(child)
        elif child.type == rec.CHILD_ANCHOR:
            shape.anchor = _read_rect(child) or shape.anchor
            shape.is_child_anchor = True
        elif child.type == rec.SPGR:
            shape.group_rect = _read_rect(child)
        elif child.type == rec.CLIENT_DATA:
            for item in child.children():
                if item.type == rec.OE_PLACEHOLDER_ATOM and item.end - item.start >= 5:
                    shape.placeholder_pos = struct.unpack_from("<i", item.stream, item.start)[0]
                    shape.placeholder = item.stream[item.start + 4]
                elif item.type == rec.INTERACTIVE_INFO:
                    shape.link = hyperlink_ref(item)
                elif item.type == rec.ANIMATION_INFO:
                    shape.animation = _read_animation(item)
                elif item.type == rec.PROG_TAGS:
                    tags = item
        elif child.type == rec.CLIENT_TEXTBOX:
            shape.blocks = read_blocks(child.children())
    if tags is not None:
        _apply_numbering(shape, tags)
    return shape


def _read_group(container: Record, depth: int, budget: list[int]) -> Shape | None:
    group: Shape | None = None
    for child in container.children():
        if budget[0] <= 0 or depth >= MAX_DEPTH:
            break
        budget[0] -= 1
        if child.type == rec.SPGR_CONTAINER:
            nested = _read_group(child, depth + 1, budget)
            if nested is not None and group is not None:
                group.children.append(nested)
        elif child.type == rec.SP_CONTAINER:
            shape = read_shape(child)
            if group is None:
                shape.flags |= SP_GROUP
                group = shape  # the first shape describes the group itself
            elif not shape.flags & SP_DELETED:
                group.children.append(shape)
    return group


def read_drawing(drawing: Record | None) -> tuple[Shape | None, Shape | None]:
    """(root group, background shape) of a Drawing container."""
    if drawing is None:
        return None, None
    container = drawing.first(rec.DG_CONTAINER)
    if container is None:
        return None, None
    root: Shape | None = None
    background: Shape | None = None
    budget = [MAX_SHAPES]
    for child in container.children():
        if child.type == rec.SPGR_CONTAINER and root is None:
            root = _read_group(child, 0, budget)
        elif child.type == rec.SP_CONTAINER:
            shape = read_shape(child)
            if shape.flags & SP_BACKGROUND:
                background = shape
    return root, background
