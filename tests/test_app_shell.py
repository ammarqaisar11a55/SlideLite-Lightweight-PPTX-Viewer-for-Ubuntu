"""Smoke test: the GTK shell builds a window and the page reports ready."""

import pytest

pytestmark = pytest.mark.gui


def test_window_loads_ui():
    from gi.repository import GLib

    from slidelite.app import gi_versions  # noqa: F401
    from slidelite.app.application import SlideLiteApplication
    from slidelite.app.window import ViewerWindow

    app = SlideLiteApplication()
    app.set_flags(app.get_flags() | 32)  # NON_UNIQUE so tests never talk to a running instance
    seen = []

    def on_cmdline(application, _cmd):
        application.launch(__import__("slidelite.cli").cli.LaunchOptions())
        window = application.get_windows()[0]
        assert isinstance(window, ViewerWindow)
        original = application.handle_page_message

        def spy(win, message):
            seen.append(message)
            original(win, message)
            if message.get("cmd") == "ready":
                application.quit()

        application.handle_page_message = spy
        GLib.timeout_add_seconds(20, application.quit)
        return 0

    app.connect("command-line", on_cmdline)
    app.run(["slidelite"])
    assert {"cmd": "ready"} in seen
