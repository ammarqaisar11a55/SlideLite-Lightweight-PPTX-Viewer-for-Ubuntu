"""Smoke test: the GTK shell builds a window and the page reports ready."""

import pytest

pytestmark = pytest.mark.gui


def test_window_loads_ui():
    from slidelite.app.window import ViewerWindow
    from tests.harness import Harness

    # The harness shares one registered Gtk.Application per test process:
    # creating and running a second one in the same process is unsupported.
    h = Harness()
    try:
        assert isinstance(h.window, ViewerWindow)
        assert {"cmd": "ready"} in h.messages
        assert h.js("return document.querySelector('#app').dataset.state") == "welcome"
        assert h.window.lookup_action("present-start").get_enabled() is False
    finally:
        h.close()
