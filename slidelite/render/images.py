"""Image header inspection (dimensions and DPI) without decoding pixels."""

from __future__ import annotations

import struct


def image_size(data: bytes) -> tuple[int, int, float, float] | None:
    """(width_px, height_px, dpi_x, dpi_y) for PNG/JPEG/GIF/BMP, else None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            dpi_x = dpi_y = 96.0
            pos = 8
            while pos + 8 <= len(data) and pos < 1 << 16:
                length = struct.unpack(">I", data[pos : pos + 4])[0]
                kind = data[pos + 4 : pos + 8]
                if kind == b"pHYs" and length >= 9:
                    px, py, unit = struct.unpack(">IIB", data[pos + 8 : pos + 17])
                    if unit == 1 and px and py:
                        dpi_x, dpi_y = px * 0.0254, py * 0.0254
                    break
                if kind == b"IDAT":
                    break
                pos += 12 + length
            return w, h, dpi_x, dpi_y
        if data[:2] == b"\xff\xd8":
            pos = 2
            dpi_x = dpi_y = 96.0
            while pos + 4 < len(data):
                if data[pos] != 0xFF:
                    pos += 1
                    continue
                marker = data[pos + 1]
                length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
                if marker == 0xE0 and data[pos + 4 : pos + 9] == b"JFIF\0":
                    unit = data[pos + 11]
                    xd, yd = struct.unpack(">HH", data[pos + 12 : pos + 16])
                    if unit == 1 and xd and yd:
                        dpi_x, dpi_y = float(xd), float(yd)
                    elif unit == 2 and xd and yd:
                        dpi_x, dpi_y = xd * 2.54, yd * 2.54
                if marker in (
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                ):
                    h, w = struct.unpack(">HH", data[pos + 5 : pos + 9])
                    return w, h, dpi_x, dpi_y
                pos += 2 + length
            return None
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return w, h, 96.0, 96.0
        if data[:2] == b"BM":
            w, h = struct.unpack("<ii", data[18:26])
            return abs(w), abs(h), 96.0, 96.0
    except (struct.error, IndexError):
        return None
    return None
