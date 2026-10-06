"""Tables, charts, SmartArt and metafiles."""

import re
import struct
import xml.etree.ElementTree as ET

import pytest

from slidelite.presentation import document, metafile
from slidelite.presentation.parts import Deck
from slidelite.presentation.tablestyles import BUILTIN_IDS, builtin_style
from slidelite.render.chart import format_number, nice_scale
from slidelite.render.slide import render_slide
from slidelite.server.router import Router
from slidelite.server.session import DocumentSession


def render(fixtures_dir, name, index=0):
    pres = document.load(fixtures_dir / name, lenient=True)
    deck = Deck(pres)
    result = render_slide(deck, index, "doc/test/")
    warnings = list(deck.warnings)
    pres.close()
    return result.html, warnings


# -- tables ---------------------------------------------------------------------------


def test_all_builtin_table_styles_expand():
    assert len(BUILTIN_IDS) == 74
    for guid in BUILTIN_IDS:
        style = builtin_style(guid)
        assert style is not None and style.find("{*}wholeTbl") is not None


def test_default_table_style_is_applied(fixtures_dir):
    html, warnings = render(fixtures_dir, "tables.pptx")
    assert not warnings
    assert '<table class="tbl"' in html
    # Medium Style 2 - Accent 1: header row filled with accent 1, white bold text.
    assert "background:#4472c4" in html or "background:#4f81bd" in html
    assert html.count("<td") == 15  # 16 cells, two merged into one
    assert 'colspan="2"' in html
    assert "font-weight:700" in html


# -- charts ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code", "expected"),
    [
        (1234.5, "General", "1234.5"),
        (1234.5, "#,##0", "1,235"),
        (0.256, "0%", "26%"),
        (0.256, "0.0%", "25.6%"),
        (12.5, '"$"#,##0.00', "$12.50"),
        (-3, "0.0", "-3.0"),
        (7, "[Red]0;[Blue]-0", "7"),
    ],
)
def test_number_formats(value, code, expected):
    assert format_number(value, code) == expected


def test_nice_scale():
    assert nice_scale(0, 25.3) == (0.0, 30.0, 5.0)
    lo, hi, major = nice_scale(8.4, 57.9)
    assert lo == 0 and hi >= 57.9 and major in (10, 5)
    lo, hi, _ = nice_scale(-12, 30)
    assert lo <= -12 and hi >= 30


def test_charts_render(fixtures_dir):
    for i in range(7):
        html, warnings = render(fixtures_dir, "charts.pptx", i)
        assert 'class="chart-svg"' in html, i
        assert "could not be displayed" not in html, (i, warnings)
    column, _ = render(fixtures_dir, "charts.pptx", 0)
    assert column.count("<rect") >= 12 + 1  # 3 series x 4 categories + background
    assert ">North<" in column and ">Q4<" in column
    pie, _ = render(fixtures_dir, "charts.pptx", 3)
    assert re.search(r">\d+%<", pie)  # percentage labels


def test_broken_chart_shows_placeholder(fixtures_dir, tmp_path):
    import zipfile

    dst = tmp_path / "broken-chart.pptx"
    with zipfile.ZipFile(fixtures_dir / "charts.pptx") as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename.startswith("ppt/charts/chart") and info.filename.endswith(".xml"):
                data = data[: len(data) // 3]
            zout.writestr(info, data)
    html, warnings = render(tmp_path, "broken-chart.pptx", 0)
    assert "could not be displayed" in html
    assert warnings


# -- SmartArt --------------------------------------------------------------------------------


def test_smartart_uses_drawing_part(fixtures_dir):
    html, warnings = render(fixtures_dir, "smartart.pptx")
    assert not warnings
    assert 'class="dgm-tree"' in html
    for label in ("Plan", "Build", "Ship"):
        assert f">{label}<" in html
    # Text moved to its own frame: shape outline is not drawn twice.
    assert html.count(">Plan<") == 1


# -- metafiles ---------------------------------------------------------------------------------


def _emf(records: list[bytes], frame=(0, 0, 10000, 5000)) -> bytes:
    header = bytearray(108)
    struct.pack_into("<II", header, 0, 1, 108)
    struct.pack_into("<4i", header, 8, 0, 0, 377, 188)
    struct.pack_into("<4i", header, 24, *frame)
    header[40:44] = b" EMF"
    struct.pack_into("<2i", header, 72, 1920, 1080)
    struct.pack_into("<2i", header, 80, 508, 286)
    body = b"".join(records)
    eof = struct.pack("<IIIII", 14, 20, 0, 16, 20)
    return bytes(header) + body + eof


def _rec(kind: int, payload: bytes) -> bytes:
    return struct.pack("<II", kind, 8 + len(payload)) + payload


def test_emf_shapes_and_text():
    brush = _rec(39, struct.pack("<IIII", 1, 0, 0x0000FF, 0))  # red solid brush, handle 1
    select = _rec(37, struct.pack("<I", 1))
    rect = _rec(43, struct.pack("<4i", 10, 10, 200, 100))
    text = "Hi"
    text_payload = bytearray(76 + 4)
    struct.pack_into("<iiIII", text_payload, 28, 20, 40, len(text), 88, 0)
    text_rec = _rec(84, bytes(text_payload) + text.encode("utf-16-le"))
    svg = metafile.to_svg(_emf([brush, select, rect, text_rec]))
    ET.fromstring(svg)  # well-formed
    assert 'fill="#ff0000"' in svg
    assert ">Hi</text>" in svg


def test_emf_nop_blit_does_not_paint():
    blit = bytearray(100 - 8)
    struct.pack_into("<4i", blit, 16, 0, 0, 500, 500)
    struct.pack_into("<I", blit, 32, 0x00AA0029)  # NOP raster operation, no bitmap
    svg = metafile.to_svg(_emf([_rec(76, bytes(blit))]))
    assert "<path" not in svg


def test_wmf_placeable_polygon():
    placeable = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, 0, 0, 1000, 1000, 1440, 0) + b"\0\0"
    header = struct.pack("<HHHIHIH", 1, 9, 0x300, 0, 1, 0, 0)
    polygon = struct.pack("<IHh", 3 + 1 + 6, 0x0324, 3) + struct.pack("<6h", 0, 0, 500, 0, 250, 500)
    data = placeable + header + polygon + struct.pack("<IH", 3, 0)
    svg = metafile.to_svg(data)
    ET.fromstring(svg)
    assert 'viewBox="0 0 1000 1000"' in svg and "<path" in svg


@pytest.mark.parametrize("blob", [b"", b"\x01\0\0\0" * 3, b"\0" * 200, bytes(range(256)) * 4])
def test_hostile_metafiles_never_crash(blob):
    try:
        svg = metafile.to_svg(blob)
    except metafile.MetafileError:
        return
    ET.fromstring(svg)


def test_truncated_emf_keeps_partial_output():
    rect = _rec(43, struct.pack("<4i", 10, 10, 200, 100))
    data = _emf([rect, rect])
    svg = metafile.to_svg(data[: len(data) - 30])
    ET.fromstring(svg)


def test_image_route_converts_metafiles(fixtures_dir, tmp_path):
    import shutil
    import zipfile

    rect = _rec(43, struct.pack("<4i", 10, 10, 200, 100))
    src = fixtures_dir / "images.pptx"
    dst = tmp_path / "emf.pptx"
    shutil.copy(src, dst)
    with zipfile.ZipFile(dst, "a") as z:
        z.writestr("ppt/media/drawing.emf", _emf([rect]))
    router = Router()
    session = DocumentSession(document.load(dst), str(dst))
    session.mount(router)
    resp = router.handle(f"/doc/{session.id}/image/ppt/media/drawing.emf")
    assert resp.status == 200 and resp.mime == "image/svg+xml"
    assert b"<path" in resp.body
    assert router.handle(f"/doc/{session.id}/image/ppt/presentation.xml").status == 404
    assert router.handle(f"/doc/{session.id}/image/docProps/core.xml").status == 404
    session.close()
