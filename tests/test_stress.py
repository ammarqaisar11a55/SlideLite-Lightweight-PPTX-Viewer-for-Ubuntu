"""Large presentations stay fast and memory-bounded."""

import re
import struct
import time
import zipfile
import zlib

import pytest

from slidelite.presentation import document
from slidelite.server.router import Router
from slidelite.server.session import DocumentSession

pytestmark = pytest.mark.slow

RT_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"


def build_large_deck(src, dst, count: int) -> None:
    """Clone slide 1 of ``src`` ``count`` times using plain zip edits."""
    with zipfile.ZipFile(src) as zin:
        files = {i.filename: zin.read(i) for i in zin.infolist()}
    slide = files["ppt/slides/slide1.xml"]
    slide_rels = files["ppt/slides/_rels/slide1.xml.rels"]
    for name in [n for n in files if re.match(r"ppt/slides/(_rels/)?slide\d+\.xml(\.rels)?$", n)]:
        del files[name]
    pres = files["ppt/presentation.xml"].decode()
    rels = files["ppt/_rels/presentation.xml.rels"].decode()
    rels = re.sub(rf'<Relationship [^>]*Type="{RT_SLIDE}"[^>]*/>', "", rels)
    ids, rel_entries, overrides = [], [], []
    for i in range(1, count + 1):
        text = f"Stress slide {i}".encode()
        files[f"ppt/slides/slide{i}.xml"] = re.sub(rb"Slide \d+", text, slide, count=1)
        files[f"ppt/slides/_rels/slide{i}.xml.rels"] = slide_rels
        ids.append(f'<p:sldId id="{255 + i}" r:id="rIdS{i}"/>')
        rel_entries.append(
            f'<Relationship Id="rIdS{i}" Type="{RT_SLIDE}" Target="slides/slide{i}.xml"/>'
        )
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
    pres = re.sub(
        r"<p:sldIdLst>.*?</p:sldIdLst>",
        f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst>",
        pres,
        flags=re.S,
    )
    files["ppt/presentation.xml"] = pres.encode()
    files["ppt/_rels/presentation.xml.rels"] = rels.replace(
        "</Relationships>", "".join(rel_entries) + "</Relationships>"
    ).encode()
    ct = files["[Content_Types].xml"].decode()
    ct = re.sub(r'<Override PartName="/ppt/slides/slide\d+\.xml"[^>]*/>', "", ct)
    files["[Content_Types].xml"] = ct.replace("</Types>", "".join(overrides) + "</Types>").encode()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)


def big_png(width: int, height: int) -> bytes:
    row = b"\x00" + bytes((x * 7 + 13) % 256 for x in range(width * 3))
    raw = zlib.compress(row * height, 6)

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


@pytest.fixture(scope="module")
def large_deck(tmp_path_factory, fixtures_dir_module):
    path = tmp_path_factory.mktemp("stress") / "300-slides.pptx"
    build_large_deck(fixtures_dir_module / "multi-slide.pptx", path, 300)
    return path


@pytest.fixture(scope="module")
def fixtures_dir_module():
    from tests.conftest import FIXTURES

    return FIXTURES


def test_open_300_slides_quickly(large_deck):
    start = time.perf_counter()
    pres = document.load(large_deck)
    elapsed = time.perf_counter() - start
    assert len(pres.slides) == 300
    assert elapsed < 2.0, f"loading took {elapsed:.2f}s"
    pres.close()


def test_render_every_slide_and_cache_is_bounded(large_deck):
    session = DocumentSession(document.load(large_deck), str(large_deck))
    worst = 0.0
    start = time.perf_counter()
    for i in range(300):
        t = time.perf_counter()
        html = session.render_slide(i)
        worst = max(worst, time.perf_counter() - t)
        assert f"Stress slide {i + 1}" in html
    total = time.perf_counter() - start
    assert total < 20.0 and worst < 0.5, (total, worst)
    assert len(session._cache) <= session.cache_size
    session.close()


def test_large_images_are_downscaled_for_thumbnails(fixtures_dir_module, tmp_path):
    pytest.importorskip("gi")
    src = fixtures_dir_module / "images.pptx"
    dst = tmp_path / "big-image.pptx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "ppt/media/image1.png":
                data = big_png(3000, 2000)
            zout.writestr(info, data)
    router = Router()
    session = DocumentSession(document.load(dst), str(dst))
    session.mount(router)
    full = router.handle(f"/doc/{session.id}/part/ppt/media/image1.png")
    thumb = router.handle(f"/doc/{session.id}/thumb/ppt/media/image1.png")
    assert full.status == thumb.status == 200
    from slidelite.render.images import image_size

    w, h, *_ = image_size(thumb.body)
    assert max(w, h) <= 480
    assert image_size(full.body)[:2] == (3000, 2000)
    session.close()


@pytest.mark.gui
def test_300_slide_deck_in_the_viewer(large_deck):
    from tests.harness import Harness

    h = Harness(large_deck)
    try:
        h.wait_js("document.querySelectorAll('#thumbs .thumb').length === 300", 30)
        h.wait_js("document.querySelectorAll('#thumbs .thumb[data-mounted]').length > 0")
        assert h.js("return document.querySelectorAll('#thumbs .thumb[data-mounted]').length") <= 40
        start = time.perf_counter()
        h.js("return window.SlideLite.goTo(299)")
        h.wait_js("document.querySelector('#slide-host .slide')?.dataset.slide === '300'")
        assert time.perf_counter() - start < 2.0
        assert h.js("return document.querySelectorAll('#slide-host .slide').length") == 1
    finally:
        h.close()
