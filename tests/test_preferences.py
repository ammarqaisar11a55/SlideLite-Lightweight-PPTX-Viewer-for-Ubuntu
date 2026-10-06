"""Theme, persisted preferences and recent files through the real app."""

import pytest

pytestmark = pytest.mark.gui


@pytest.fixture
def h(fixtures_dir):
    from tests.harness import Harness

    harness = Harness(fixtures_dir / "multi-slide.pptx")
    harness.wait_js("!!window.SlideLite.state.doc")
    yield harness
    harness.close()


def test_theme_switching_never_touches_slides(h):
    from gi.repository import GLib

    for theme, expected in (("dark", "dark"), ("light", "light")):
        h.app.activate_action("theme", GLib.Variant("s", theme))
        h.wait_js(f"document.documentElement.dataset.theme === '{expected}'")
        assert h.app.settings.get("theme") == theme
        # Slide content keeps its own colours whatever the UI theme is.
        bg = h.js("return getComputedStyle(document.querySelector('#slide-host')).backgroundColor")
        assert bg == "rgb(255, 255, 255)"
    h.app.activate_action("theme", GLib.Variant("s", "system"))


def test_opened_file_is_recorded_in_recent(h, fixtures_dir):
    path = str(fixtures_dir / "multi-slide.pptx")
    assert h.app.recent.entries[0]["path"] == path
    h.wait_js("document.querySelectorAll('#recent-list .recent-item').length > 0")
    names = h.js(
        "return [...document.querySelectorAll('#recent-list .recent-name')].map(e => e.textContent)"
    )
    assert names[0] == "multi-slide.pptx"
    # The native Recent menu lists it too.
    model = h.window.recent_menu
    section = model.get_item_link(0, "section")
    label = section.get_item_attribute_value(0, "label", None).get_string()
    assert label == "multi-slide.pptx"


def test_remove_and_clear_recent(h, fixtures_dir):
    h.app.recent.add(str(fixtures_dir / "shapes.pptx"))
    h.app.broadcast_recent()
    h.wait_js("document.querySelectorAll('#recent-list .recent-item').length >= 2")
    h.js("document.querySelector('#recent-list .recent-remove').click(); return true;")
    h.wait(
        lambda: all(not e["path"].endswith("shapes.pptx") for e in h.app.recent.entries), "removal"
    )
    h.js("document.querySelector('#recent-clear').click(); return true;")
    h.wait(lambda: h.app.recent.entries == [], "clear")
    h.wait_js("document.querySelector('#recent').hidden === true")


def test_sidebar_and_zoom_are_remembered(h):
    h.js("document.querySelector('#btn-sidebar').click(); return true;")
    h.wait(lambda: h.app.settings.get("showThumbnails") is False, "sidebar setting")
    h.js(
        "const s = document.querySelector('#zoom-select'); s.value = '1.5'; s.dispatchEvent(new Event('change')); return true;"
    )
    h.wait(lambda: h.app.settings.get("zoom") == 1.5, "zoom setting")
    h.app.settings.set("showThumbnails", True)
    h.app.settings.set("zoom", "fit")


def test_slide_show_options(h):
    h.app.activate_action("loop", None)
    h.wait_js("window.SlideLite.state.settings.loop === true")
    assert h.app.settings.get("loop") is True
    h.app.activate_action("loop", None)
    h.wait_js("window.SlideLite.state.settings.loop === false")
