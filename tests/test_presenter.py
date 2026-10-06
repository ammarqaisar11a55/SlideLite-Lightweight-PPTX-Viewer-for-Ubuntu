"""Slide show behaviour in the real WebKit view."""

import pytest

pytestmark = pytest.mark.gui

SHOWN = "document.querySelector('#pres-stage .pres-layer:last-child .slide')?.dataset.slide"


@pytest.fixture
def show(fixtures_dir):
    from tests.harness import Harness

    h = Harness(fixtures_dir / "transitions.pptx")
    h.wait_js("!!window.SlideLite.state.doc && !!document.querySelector('#slide-host .slide')")
    h.js(
        "document.dispatchEvent(new CustomEvent('slidelite:present', {detail: {from: 0}})); return true;"
    )
    h.wait_js(f"{SHOWN} === '1'")
    yield h
    h.close()


def key(h, name):
    h.js(
        f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: {name!r}, bubbles: true}})); return true;"
    )


def wait_slide(h, number, timeout=8):
    h.wait_js(f"{SHOWN} === '{number}' && !window.SlideLite.presenter.busy", timeout)


def test_start_goes_fullscreen_and_hides_ui(show):
    assert show.js("return window.SlideLite.presenter.active") is True
    assert show.js("return document.querySelector('#presenter').hidden") is False
    show.wait(lambda: show.window._presenting, "host presenting flag")


def test_keyboard_navigation_and_transitions(show):
    key(show, "ArrowRight")
    wait_slide(show, 2)  # push transition (1s) completes
    key(show, " ")
    wait_slide(show, 3)
    key(show, "ArrowLeft")
    wait_slide(show, 2)
    key(show, "End")
    wait_slide(show, 9)
    key(show, "Home")
    wait_slide(show, 1)


def test_hidden_slide_is_skipped_and_auto_advance(show):
    show.js("window.SlideLite.presenter.goToSlide(4); return true;")
    wait_slide(show, 4)
    key(show, "ArrowRight")
    wait_slide(show, 6)  # slide 5 is hidden
    # Slide 6 advances by itself after 1.5 s and ignores clicks (advClick=0).
    wait_slide(show, 7, timeout=6)


def test_mouse_click_and_right_click(show):
    show.js(
        "document.querySelector('#presenter').dispatchEvent(new MouseEvent('click', {bubbles: true, button: 0})); return true;"
    )
    wait_slide(show, 2)
    show.js(
        "document.querySelector('#presenter').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true})); return true;"
    )
    wait_slide(show, 1)


def test_typed_slide_number(show):
    for k in ("8", "Enter"):
        key(show, k)
    wait_slide(show, 8)


def test_black_screen_and_end_of_show(show):
    key(show, "b")
    assert show.js(
        "const b = document.querySelector('#pres-blank'); return !b.hidden && b.classList.contains('black')"
    )
    key(show, "b")
    assert show.js("return document.querySelector('#pres-blank').hidden")
    key(show, "End")
    wait_slide(show, 9)
    key(show, "ArrowRight")
    show.wait_js("document.querySelector('#pres-blank').classList.contains('end')")
    key(show, "ArrowRight")
    show.wait_js("window.SlideLite.presenter.active === false")
    # Back in the viewer on the last slide shown.
    show.wait_js("window.SlideLite.state.current === 8")


def test_escape_ends_show(show):
    key(show, "ArrowRight")
    wait_slide(show, 2)
    key(show, "Escape")
    show.wait_js("window.SlideLite.presenter.active === false")
    show.wait(lambda: not show.window._presenting, "host leaves presenting")
    assert show.js("return window.SlideLite.state.current") == 1
