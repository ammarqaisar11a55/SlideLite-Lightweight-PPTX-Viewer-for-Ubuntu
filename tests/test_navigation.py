"""Viewer navigation, zoom and keyboard shortcuts in the real WebKit view."""

import pytest

pytestmark = pytest.mark.gui


@pytest.fixture
def h(fixtures_dir):
    from tests.harness import Harness

    harness = Harness(fixtures_dir / "multi-slide.pptx")
    harness.wait_js(
        "!!window.SlideLite.state.doc && !!document.querySelector('#slide-host .slide')"
    )
    yield harness
    harness.close()


def current(h):
    h.pump(0.05)
    return h.wait_js("document.querySelector('#slide-host .slide')?.dataset.slide") and h.js(
        "return window.SlideLite.state.current"
    )


def press(h, key, **mods):
    h.js(
        "document.querySelector('#stage').focus();"
        f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: {key!r}, bubbles: true, "
        f"ctrlKey: {str(mods.get('ctrl', False)).lower()}}})); return true;"
    )
    h.pump(0.1)


def test_buttons(h):
    assert h.js("return document.querySelector('#btn-prev').disabled") is True
    h.js("document.querySelector('#btn-next').click(); return true;")
    h.wait_js("window.SlideLite.state.current === 1")
    h.js("document.querySelector('#btn-prev').click(); return true;")
    h.wait_js("window.SlideLite.state.current === 0")


@pytest.mark.parametrize("key", ["ArrowRight", "ArrowDown", " ", "PageDown"])
def test_next_keys(h, key):
    press(h, key)
    h.wait_js("window.SlideLite.state.current === 1")


@pytest.mark.parametrize("key", ["ArrowLeft", "ArrowUp", "PageUp"])
def test_previous_keys(h, key):
    h.js("return window.SlideLite.goTo(5)")
    press(h, key)
    h.wait_js("window.SlideLite.state.current === 4")


def test_home_end(h):
    press(h, "End")
    h.wait_js("window.SlideLite.state.current === 11")
    assert h.js("return document.querySelector('#btn-next').disabled") is True
    press(h, "Home")
    h.wait_js("window.SlideLite.state.current === 0")


def test_slide_number_input(h):
    h.js(
        "const i = document.querySelector('#slide-input'); i.focus(); i.value = '7';"
        "i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true;"
    )
    h.wait_js("window.SlideLite.state.current === 6")
    h.wait_js("document.querySelector('#slide-host .slide').dataset.slide === '7'")
    # Out-of-range numbers clamp to the deck.
    h.js(
        "const i = document.querySelector('#slide-input'); i.value = '999';"
        "i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true;"
    )
    h.wait_js("window.SlideLite.state.current === 11")


def test_zoom_controls(h):
    assert h.js("return document.querySelector('#zoom-select').value") == "fit"
    h.js(
        "document.querySelector('#zoom-select').value = '2';"
        "document.querySelector('#zoom-select').dispatchEvent(new Event('change')); return true;"
    )
    assert abs(h.js("return window.SlideLite.viewer.zoom") - 2) < 1e-6
    # A 960pt-wide slide at 200% is far wider than the window: it scrolls.
    assert h.js(
        "const s = document.querySelector('#stage-scroll'); return s.scrollWidth > s.clientWidth"
    )
    press(h, "-", ctrl=True)
    assert abs(h.js("return window.SlideLite.viewer.zoom") - 1.5) < 1e-6
    press(h, "+", ctrl=True)
    assert abs(h.js("return window.SlideLite.viewer.zoom") - 2) < 1e-6
    press(h, "0", ctrl=True)
    assert h.js("return document.querySelector('#zoom-select').value") == "fit"
    h.js("window.SlideLite.viewer.setZoom(0.9); return true;")
    assert h.js("return document.querySelector('#zoom-select').value") == "custom"
    assert "90%" in h.js(
        "return document.querySelector('#zoom-select option[value=custom]').textContent"
    )


def test_fit_keeps_whole_slide_visible(h):
    rect = h.js(
        "const r = document.querySelector('#slide-host').getBoundingClientRect();"
        "const s = document.querySelector('#stage').getBoundingClientRect();"
        "return [r.left >= s.left, r.right <= s.right + 1, r.top >= s.top, r.bottom <= s.bottom + 1];"
    )
    assert all(rect)


def test_sidebar_toggle(h):
    press(h, "b", ctrl=True)
    assert h.js("return document.querySelector('#viewer').classList.contains('no-sidebar')")
    press(h, "b", ctrl=True)
    assert not h.js("return document.querySelector('#viewer').classList.contains('no-sidebar')")


def test_repo_link_uses_host_command(h):
    h.messages.clear()
    h.window.open_trusted_uri = lambda uri: h.messages.append({"opened": uri})
    h.js("document.querySelector('#toolbar .repo-link').click(); return true;")
    h.wait(lambda: any("opened" in m for m in h.messages), "repo link")
    from slidelite import REPO_URL

    assert {"opened": REPO_URL} in h.messages
