"""Build animations in the slide show (real WebKit view)."""

import json

import pytest

from slidelite.presentation import document
from slidelite.presentation.parts import Deck
from slidelite.presentation.timing import slide_timing


def test_timeline_is_flattened_into_steps(fixtures_dir):
    pres = document.load(fixtures_dir / "animations.pptx")
    timing = slide_timing(Deck(pres).slide(0))
    pres.close()
    steps = timing["build"]["steps"]
    assert [s["auto"] for s in steps] == [True, False, False, False]
    assert [e["cls"] for e in steps[3]["effects"]] == ["exit", "emph", "path"]
    list_effects = [e for e in steps[2]["effects"] if e.get("para") is not None]
    assert [e["para"] for e in list_effects] == [[0], [1]]
    assert all(e["start"] == 500 for e in list_effects)  # "after previous"
    assert steps[3]["effects"][1]["rot"] == 360
    assert steps[3]["effects"][2]["path"].startswith("M 0 0")
    hidden = {json.dumps(h, sort_keys=True) for h in timing["build"]["hidden"]}
    assert '{"para": [0], "spid": "6"}' in hidden


def test_slides_without_timing_have_no_build(fixtures_dir):
    pres = document.load(fixtures_dir / "shapes.pptx")
    assert "build" not in slide_timing(Deck(pres).slide(0))
    pres.close()


@pytest.fixture
def show(fixtures_dir):
    from tests.harness import Harness

    h = Harness(fixtures_dir / "animations.pptx")
    h.wait_js("!!window.SlideLite.state.doc && !!document.querySelector('#slide-host .slide')")
    h.js(
        "document.dispatchEvent(new CustomEvent('slidelite:present', {detail: {from: 0}})); return true;"
    )
    h.wait_js("!!document.querySelector('#pres-stage .slide')")
    yield h
    h.close()


def visible(h, spid, para=None):
    sel = f"#pres-stage [data-spid='{spid}']" + (
        f" p[data-para='{para}']" if para is not None else ""
    )
    return h.js(
        f'const el = document.querySelector("{sel}"); if (!el) return null;'
        "return getComputedStyle(el).visibility !== 'hidden';"
    )


def click(h):
    h.js(
        "document.querySelector('#presenter').dispatchEvent(new MouseEvent('click', {bubbles: true, button: 0})); return true;"
    )


def settle(h):
    h.wait_js("!window.SlideLite.presenter.slideBuilds.running.length", 10)


@pytest.mark.gui
def test_builds_play_in_order(show):
    settle(show)
    assert visible(show, 2) is True  # automatic zoom on slide start
    assert visible(show, 3) is False
    assert visible(show, 4) is False
    click(show)
    settle(show)
    assert visible(show, 3) is True
    assert visible(show, 6, 0) is False
    click(show)
    settle(show)
    assert visible(show, 4) and visible(show, 5)
    assert visible(show, 6, 0) and visible(show, 6, 1)
    click(show)
    settle(show)
    assert visible(show, 3) is False  # exit effect
    assert visible(show, 5) is True  # emphasis keeps it visible
    assert show.js("return window.SlideLite.presenter.slideBuilds.hasNext()") is False
    click(show)  # no more builds and last slide: end screen
    show.wait_js("document.querySelector('#pres-blank').classList.contains('end')")


@pytest.mark.gui
def test_click_during_animation_completes_it(show):
    settle(show)
    click(show)
    click(show)  # immediately: completes the fade
    settle(show)
    assert visible(show, 3) is True
    assert show.js("return window.SlideLite.presenter.slideBuilds.index") == 2


@pytest.mark.gui
def test_previous_slide_is_shown_fully_built(show):
    settle(show)
    # Jump to the end of the show and come back: everything is built.
    show.js("window.SlideLite.presenter.showEnd(); return true;")
    show.js(
        "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true})); return true;"
    )
    show.js("window.SlideLite.presenter.show(0, {transition: false, finished: true}); return true;")
    show.wait_js("!!document.querySelector('#pres-stage .slide')")
    show.pump(0.2)
    assert visible(show, 4) is True and visible(show, 6, 1) is True
    assert visible(show, 3) is False  # its last effect is an exit
