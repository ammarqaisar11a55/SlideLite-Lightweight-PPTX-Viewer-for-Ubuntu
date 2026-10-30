"""The Pictures stream: BLIP records turned into ordinary image files."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from slidelite.legacy import records as rec
from slidelite.legacy.records import Record, read_header

MAX_INFLATED = 256 * 1024 * 1024
_METAFILE_HEADER = 34

BLIP_EMF, BLIP_WMF, BLIP_PICT, BLIP_JPEG, BLIP_PNG, BLIP_DIB = (
    0xF01A,
    0xF01B,
    0xF01C,
    0xF01D,
    0xF01E,
    0xF01F,
)
BLIP_TIFF, BLIP_CMYK_JPEG = 0xF029, 0xF02A

# The instance that means one UID; any other value means two.
_ONE_UID = {
    BLIP_EMF: 0x3D4,
    BLIP_WMF: 0x216,
    BLIP_PICT: 0x542,
    BLIP_JPEG: 0x46A,
    BLIP_PNG: 0x6E0,
    BLIP_DIB: 0x7A8,
    BLIP_TIFF: 0x6E4,
    BLIP_CMYK_JPEG: 0x6E2,
}
_EXTENSIONS = {
    BLIP_JPEG: "jpeg",
    BLIP_CMYK_JPEG: "jpeg",
    BLIP_PNG: "png",
    BLIP_DIB: "bmp",
    BLIP_TIFF: "tiff",
    BLIP_EMF: "emf",
    BLIP_WMF: "wmf",
}


@dataclass(frozen=True)
class Image:
    extension: str
    data: bytes


def _inflate(data: bytes, expected: int) -> bytes:
    inflater = zlib.decompressobj()
    return inflater.decompress(data, min(max(expected, 1), MAX_INFLATED))


def _wmf_with_placeable(data: bytes, bounds: tuple[int, int, int, int]) -> bytes:
    """Blips drop the Aldus header that gives a WMF its picture frame; put it back."""
    if data[:4] == b"\xd7\xcd\xc6\x9a":
        return data
    left, top, right, bottom = bounds
    fields = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, left, top, right, bottom, 1440, 0)
    checksum = 0
    for (word,) in struct.iter_unpack("<H", fields):
        checksum ^= word
    return fields + struct.pack("<H", checksum) + data


def _bitmap_file(dib: bytes) -> bytes:
    """A BMP file header for a bare DIB, with the offset of its pixel data."""
    if len(dib) < 40:
        return dib
    header, _w, _h, _planes, bits, _comp, _size, _x, _y, used = struct.unpack_from(
        "<IiiHHIIiiI", dib
    )
    palette = (used or (1 << bits if bits <= 8 else 0)) * 4
    offset = 14 + header + palette
    return struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, offset) + dib


def decode_blip(record: Record) -> Image | None:
    """An image from a BLIP record, or None for formats that can't be shown (Mac PICT)."""
    extension = _EXTENSIONS.get(record.type)
    if extension is None:
        return None
    uid_size = 16 if record.instance == _ONE_UID.get(record.type) else 32
    body = record.data[uid_size:]
    if record.type in (BLIP_EMF, BLIP_WMF):
        if len(body) < _METAFILE_HEADER:
            return None
        size, left, top, right, bottom = struct.unpack_from("<I4i", body)
        compression = body[32]
        payload = body[_METAFILE_HEADER:]
        data = _inflate(payload, size) if compression == 0 else payload[:size]
        if record.type == BLIP_WMF:
            data = _wmf_with_placeable(data, _clamp16(left, top, right, bottom))
        return Image(extension, data)
    pixels = body[1:]  # one "tag" byte precedes bitmap data
    if record.type == BLIP_DIB:
        pixels = _bitmap_file(pixels)
    return Image(extension, pixels)


def _clamp16(*values: int) -> tuple[int, int, int, int]:
    return tuple(max(-32768, min(32767, v)) for v in values)  # type: ignore[return-value]


def load_store(document: Record, pictures: bytes) -> dict[int, Record]:
    """1-based picture index → BLIP record, as shapes refer to pictures."""
    store: dict[int, Record] = {}
    group = document.first(rec.DRAWING_GROUP)
    container = group.first(rec.DGG_CONTAINER) if group is not None else None
    bstore = container.first(rec.BSTORE_CONTAINER) if container is not None else None
    if bstore is None:
        return store
    for index, entry in enumerate(bstore.children(), start=1):
        if entry.type != rec.BSE or entry.end - entry.start < 36:
            continue
        name_size = entry.stream[entry.start + 33]
        delay = struct.unpack_from("<I", entry.stream, entry.start + 28)[0]
        size = struct.unpack_from("<I", entry.stream, entry.start + 20)[0]
        blip = None
        inline_at = entry.start + 36 + name_size
        if inline_at + 8 <= entry.end:
            blip = read_header(entry.stream, inline_at, entry.end)
        elif size:
            blip = read_header(pictures, delay)
        if blip is not None:
            store[index] = blip
    return store
