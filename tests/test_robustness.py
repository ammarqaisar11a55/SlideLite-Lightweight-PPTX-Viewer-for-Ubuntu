"""Crash testing: corrupted presentations never break the viewer.

Deterministically mutates fixtures (byte flips, truncation, garbage inside
XML parts, hostile attribute values) and checks that every outcome is a
friendly PackageError or a rendered slide (possibly the error placeholder),
never an unhandled exception.
"""

import io
import os
import random
import zipfile

import pytest

from slidelite.presentation import document, metafile
from slidelite.presentation.parts import Deck
from slidelite.render.slide import render_slide

ITERATIONS = int(os.environ.get("SLIDELITE_FUZZ_ITERATIONS", "25"))
SOURCES = [
    "simple-text.pptx",
    "shapes.pptx",
    "tables.pptx",
    "charts.pptx",
    "smartart.pptx",
    "animations.pptx",
    "fills.pptx",
]


def exercise(data: bytes) -> str:
    """Load + render every slide; returns 'error' for friendly load errors."""
    try:
        pres = document.load(io.BytesIO(data), lenient=True)
    except document.PackageError as exc:
        assert exc.message
        return "error"
    try:
        deck = Deck(pres)
        for i in range(len(pres.slides)):
            html = render_slide(deck, i, "doc/x/").html
            assert html.startswith('<div class="slide')
    finally:
        pres.close()
    return "ok"


def mutate_xml(data: bytes, rng: random.Random) -> bytes:
    """Rewrite one XML part with a structural corruption."""
    src = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in src.namelist() if n.endswith(".xml") or n.endswith(".rels")]
    victim = rng.choice(names)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            content = src.read(info)
            if info.filename == victim:
                kind = rng.randrange(6)
                if kind == 0:
                    content = content[: rng.randrange(1, max(2, len(content)))]
                elif kind == 1:
                    pos = rng.randrange(len(content))
                    content = (
                        content[:pos] + bytes(rng.randrange(256) for _ in range(8)) + content[pos:]
                    )
                elif kind == 2:
                    content = content.replace(b'val="', b'val="-99999999999', 3)
                elif kind == 3:
                    content = content.replace(b'"', b'"NaN', 5)
                elif kind == 4:
                    content = content.replace(b'r:embed="', b'r:embed="missing', 2).replace(
                        b'r:id="', b'r:id="missing', 2
                    )
                else:
                    content = content.replace(b"<a:", b"<a:x", 1)
            dst.writestr(info, content)
    return out.getvalue()


@pytest.mark.parametrize("name", SOURCES)
def test_xml_corruption_never_crashes(fixtures_dir, name):
    rng = random.Random(f"xml-{name}")
    data = (fixtures_dir / name).read_bytes()
    outcomes = {exercise(mutate_xml(data, rng)) for _ in range(ITERATIONS)}
    assert outcomes <= {"ok", "error"}


@pytest.mark.parametrize("name", SOURCES[:4])
def test_byte_flips_and_truncation_never_crash(fixtures_dir, name):
    rng = random.Random(f"bytes-{name}")
    data = (fixtures_dir / name).read_bytes()
    for _ in range(ITERATIONS):
        mutated = bytearray(data)
        for _ in range(rng.randrange(1, 12)):
            mutated[rng.randrange(len(mutated))] = rng.randrange(256)
        if rng.random() < 0.3:
            mutated = mutated[: rng.randrange(len(mutated))]
        assert exercise(bytes(mutated)) in ("ok", "error")


def test_metafile_fuzzing_never_crashes():
    rng = random.Random("metafiles")
    seeds = [bytes(108), b"\x01\x00\x00\x00\x6c\x00\x00\x00" + bytes(32) + b" EMF" + bytes(64)]
    for _ in range(300):
        blob = bytearray(rng.choice(seeds))
        blob += bytes(rng.randrange(256) for _ in range(rng.randrange(0, 400)))
        try:
            metafile.to_svg(bytes(blob))
        except metafile.MetafileError:
            pass
