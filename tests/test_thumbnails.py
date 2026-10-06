"""Thumbnail sidebar behaviour in the real WebKit view."""

import pytest

pytestmark = pytest.mark.gui


@pytest.fixture
def viewer(fixtures_dir):
    from tests.harness import Harness

    def make(name):
        harness = Harness(fixtures_dir / name)
        harness.wait_js(
            "!!window.SlideLite.state.doc && document.querySelectorAll('#thumbs .thumb').length > 0"
        )
        made.append(harness)
        return harness

    made = []
    yield make
    for harness in made:
        harness.close()


def test_one_thumbnail_per_slide(viewer):
    h = viewer("multi-slide.pptx")
    assert h.js("return document.querySelectorAll('#thumbs .thumb').length") == 12
    h.wait_js("document.querySelector('#thumb-0 .slide').textContent.includes('Slide 1')")
    assert h.js("return document.querySelector('#thumb-0').getAttribute('aria-selected')") == "true"


def test_clicking_a_thumbnail_navigates(viewer):
    h = viewer("multi-slide.pptx")
    h.js("document.querySelector('#thumb-4').click(); return true;")
    h.wait_js("document.querySelector('#slide-host .slide')?.dataset.slide === '5'")
    assert h.js("return window.SlideLite.state.current") == 4
    assert h.js("return document.querySelector('#thumb-4').getAttribute('aria-selected')") == "true"
    assert (
        h.js("return document.querySelector('#thumb-0').getAttribute('aria-selected')") == "false"
    )


def test_thumbnails_are_lazy_for_large_decks(viewer):
    h = viewer("stress-150-slides.pptx")
    assert h.js("return document.querySelectorAll('#thumbs .thumb').length") == 150
    h.wait_js("document.querySelectorAll('#thumbs .thumb[data-mounted]').length > 0")
    mounted = h.js("return document.querySelectorAll('#thumbs .thumb[data-mounted]').length")
    assert mounted < 40
    # Scrolling to the end mounts the last thumbnails and evicts early ones.
    h.js("const t = document.querySelector('#thumbs'); t.scrollTop = t.scrollHeight; return true;")
    h.wait_js("document.querySelector('#thumb-149').dataset.mounted === '1'")
    h.pump(0.3)
    assert h.js("return document.querySelectorAll('#thumbs .thumb[data-mounted]').length") <= 40


def test_thumbnail_ids_do_not_collide(viewer):
    h = viewer("shapes.pptx")
    h.wait_js(
        "!!(document.querySelector('#thumb-0 .slide') && document.querySelector('#slide-host .slide'))"
    )
    duplicates = h.js(
        "const ids = [...document.querySelectorAll('[id]')].map(e => e.id);"
        "return ids.length - new Set(ids).size;"
    )
    assert duplicates == 0
