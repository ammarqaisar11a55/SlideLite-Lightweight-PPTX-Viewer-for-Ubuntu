"""Drive a real SlideLite window (GTK + WebKit) from tests.

The application is registered without entering ``run()``; the harness
iterates the GLib main context itself while waiting for page conditions.
"""

from __future__ import annotations

import json
import time

from gi.repository import GLib

import slidelite.app  # noqa: F401  (pins GTK 3 / WebKit2 4.1)
from slidelite.app.application import SlideLiteApplication
from slidelite.cli import LaunchOptions

_APP: SlideLiteApplication | None = None


def shared_app() -> SlideLiteApplication:
    """One registered application per test process (GApplication exports a
    D-Bus object on registration, so instances cannot be recreated)."""
    global _APP
    if _APP is None:
        _APP = SlideLiteApplication(application_id=None)
        _APP.register(None)
        _APP.hold()
        _APP.listeners = []
        original = _APP.handle_page_message

        def spy(window, message):
            for listener in _APP.listeners:
                listener(window, message)
            original(window, message)

        _APP.handle_page_message = spy
    return _APP


class Harness:
    def __init__(self, path: str | None = None, present: bool = False) -> None:
        self.app = shared_app()
        self.messages: list[dict] = []
        before = set(self.app.get_windows())

        def listener(window, message):
            if window is getattr(self, "window", None) or window not in before:
                self.messages.append(message)

        self.listener = listener
        self.app.listeners.append(listener)
        files = (str(path),) if path else ()
        if files:
            # Always use a fresh window so tests are independent.
            self.window = self.app._new_window()
            self.window.present()
            self.app.open_in_window(self.window, files[0], present)
        else:
            self.app.launch(LaunchOptions(files, False, present))
            self.window = next(w for w in self.app.get_windows() if w not in before)
        self.window.resize(1200, 800)
        self.wait(lambda: any(m.get("cmd") == "ready" for m in self.messages), "page ready")

    @property
    def webview(self):
        return self.window.webview

    def pump(self, seconds: float = 0.05) -> None:
        ctx = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.002)

    def wait(self, condition, what: str = "condition", timeout: float = 20.0) -> None:
        ctx = GLib.MainContext.default()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while ctx.pending():
                ctx.iteration(False)
            if condition():
                return
            time.sleep(0.005)
        raise TimeoutError(f"timed out waiting for {what}")

    def js(self, body: str, timeout: float = 15.0):
        """Run an async JS function body in the page and return its JSON result."""
        result: dict = {}

        def done(view, res):
            try:
                value = view.call_async_javascript_function_finish(res)
                text = value.to_json(0) if value is not None else "null"
                result["value"] = json.loads(text) if text not in (None, "undefined") else None
            except GLib.Error as exc:
                result["error"] = exc.message

        wrapped = f"return JSON.parse(JSON.stringify(await (async () => {{ {body} }})() ?? null));"
        self.webview.call_async_javascript_function(wrapped, -1, None, None, None, None, done)
        self.wait(lambda: bool(result), "javascript result", timeout)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["value"]

    def wait_js(self, expression: str, timeout: float = 20.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                value = self.js(f"return ({expression});")
            except RuntimeError:  # e.g. an element that is not mounted yet
                value = None
            if value:
                return value
            self.pump(0.05)
        raise TimeoutError(f"timed out waiting for JS: {expression}")

    def key(self, key: str, **mods) -> None:
        init = ", ".join(
            f"{k}: {json.dumps(v)}" for k, v in {"key": key, "bubbles": True, **mods}.items()
        )
        self.js(
            f"document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {{{init}}})); return true;"
        )

    def close(self) -> None:
        self.app.listeners.remove(self.listener)
        self.window.destroy()
        self.pump(0.1)
