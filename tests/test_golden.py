"""Rendering regression tests: rendered slide markup is compared with
reviewed golden snapshots in tests/golden/.

After an intentional rendering change, regenerate and review the diff:

    SLIDELITE_UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

import difflib
import os
import re
from pathlib import Path

import pytest

from slidelite.presentation import document
from slidelite.presentation.parts import Deck
from slidelite.render.slide import render_slide

GOLDEN = Path(__file__).resolve().parent / "golden"
CASES = [
    ("simple-text.pptx", 3),
    ("text-formatting.pptx", 1),
    ("shapes.pptx", 1),
    ("fills.pptx", 1),
    ("groups.pptx", 1),
    ("images.pptx", 1),
    ("tables.pptx", 1),
    ("charts.pptx", 7),
    ("smartart.pptx", 1),
    ("transitions.pptx", 2),
    ("animations.pptx", 1),
    ("aspect-4x3.pptx", 1),
]
UPDATE = bool(os.environ.get("SLIDELITE_UPDATE_GOLDEN"))


def pretty(html: str) -> str:
    """One element per line so diffs are reviewable."""
    return re.sub(r"><", ">\n<", html) + "\n"


@pytest.mark.parametrize(("name", "slides"), CASES)
def test_rendering_matches_golden(fixtures_dir, name, slides):
    pres = document.load(fixtures_dir / name)
    deck = Deck(pres)
    for index in range(slides):
        actual = pretty(render_slide(deck, index, "doc/golden/").html)
        path = GOLDEN / f"{Path(name).stem}-{index + 1}.html"
        if UPDATE or not path.exists():
            path.write_text(actual, encoding="utf-8")
            continue
        expected = path.read_text(encoding="utf-8")
        if actual != expected:
            diff = "".join(
                difflib.unified_diff(
                    expected.splitlines(True), actual.splitlines(True), str(path), "rendered", n=1
                )
            )
            pytest.fail(f"rendering of {name} slide {index + 1} changed:\n{diff[:4000]}")
    pres.close()
