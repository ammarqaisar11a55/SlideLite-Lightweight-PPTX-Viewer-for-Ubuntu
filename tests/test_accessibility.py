"""Keyboard access, accessible names and screen-reader semantics."""

import re

import pytest

from slidelite.presentation import document
from slidelite.presentation.parts import Deck
from slidelite.render.slide import render_slide


def test_slide_markup_semantics(fixtures_dir):
    pres = document.load(fixtures_dir / "shapes.pptx")
    html = render_slide(Deck(pres), 0, "doc/x/").html
    pres.close()
    assert 'role="group" aria-roledescription="slide"' in html
    assert html.count('class="geom" aria-hidden="true"') == html.count('class="geom"')
    assert 'class="bg" aria-hidden="true"' in html


def test_described_pictures_are_images(fixtures_dir, tmp_path):
    import zipfile

    dst = tmp_path / "alt.pptx"
    with zipfile.ZipFile(fixtures_dir / "images.pptx") as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "ppt/slides/slide1.xml":
                data = re.sub(rb'descr="[^"]*"', b'descr="A red gradient"', data, count=1)
            zout.writestr(info, data)
    pres = document.load(dst)
    html = render_slide(Deck(pres), 0, "doc/x/").html
    pres.close()
    assert 'aria-label="A red gradient" role="img"' in html


@pytest.fixture
def h(fixtures_dir):
    from tests.harness import Harness

    harness = Harness(fixtures_dir / "multi-slide.pptx")
    harness.wait_js(
        "!!window.SlideLite.state.doc && !!document.querySelector('#slide-host .slide')"
    )
    yield harness
    harness.close()


@pytest.mark.gui
def test_every_control_has_an_accessible_name(h):
    unnamed = h.js(
        """
        const controls = [...document.querySelectorAll('button, input, select, [role=button], [role=listbox]')];
        return controls.filter((el) => {
          if (el.closest('[hidden]') || el.closest('dialog:not([open])')) return false;
          const label = el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent.trim()
            || (el.id && document.querySelector(`label[for="${el.id}"]`)) || el.closest('label');
          return !label;
        }).map((el) => el.outerHTML.slice(0, 80));
        """
    )
    assert unnamed == []


@pytest.mark.gui
def test_slide_changes_are_announced(h):
    h.js("return window.SlideLite.goTo(3)")
    h.wait_js("document.querySelector('#sr-status').textContent === 'Slide 4 of 12'")


@pytest.mark.gui
def test_f6_cycles_between_regions(h):
    h.js("document.querySelector('#stage').focus(); return true;")
    seen = []
    for _ in range(3):
        h.js(
            "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'F6', bubbles: true})); return true;"
        )
        seen.append(h.js("return document.activeElement.id"))
    assert seen == ["btn-next", "thumbs", "stage"]


@pytest.mark.gui
def test_toolbar_is_reachable_by_tab_order(h):
    order = h.js(
        "return [...document.querySelectorAll('#viewer [tabindex], #viewer button, #viewer input, #viewer select, #viewer a')]"
        ".filter((el) => el.tabIndex >= 0 && !el.disabled).map((el) => el.id || el.className)"
    )
    for control in (
        "thumbs",
        "btn-sidebar",
        "slide-input",
        "btn-next",
        "zoom-select",
        "btn-present",
    ):
        assert control in order
