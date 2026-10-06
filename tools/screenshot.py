"""Developer tool: launch SlideLite, run optional JS, save a window capture.

    GDK_BACKEND=x11 python3 tools/screenshot.py out.png [deck.pptx] [--js CODE]
        [--delay MS] [--size WxH] [--present]

Uses Gdk.pixbuf_get_from_window, which works under X11/XWayland without the
gi-cairo bindings.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
os.environ.setdefault("GDK_BACKEND", "x11")
# XGetImage cannot read GL-composited content.
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

from slidelite.app.application import SlideLiteApplication  # noqa: E402  (pins gi versions)
from slidelite.cli import LaunchOptions  # noqa: E402

from gi.repository import Gdk, GLib  # noqa: E402  isort: skip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("file", nargs="?")
    parser.add_argument("--js", action="append", default=[])
    parser.add_argument("--delay", type=int, default=2500)
    parser.add_argument("--size", default="1280x800")
    parser.add_argument("--present", action="store_true")
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.split("x"))

    app = SlideLiteApplication()
    app.set_flags(app.get_flags() | 32)  # G_APPLICATION_NON_UNIQUE

    def capture(window) -> bool:
        gdk_window = window.get_window()
        alloc = window.get_allocation()
        pixbuf = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, alloc.width, alloc.height)
        if pixbuf is None:
            print("capture failed", file=sys.stderr)
        else:
            pixbuf.savev(args.out, "png", [], [])
            print("saved", args.out, alloc.width, alloc.height)
        app.quit()
        return False

    def run_js(window, scripts) -> bool:
        for code in scripts:
            window.webview.evaluate_javascript(code, -1, None, None, None, None, None)
        return False

    def activate(_app, *_rest) -> None:
        files = (os.path.abspath(args.file),) if args.file else ()
        app.launch(LaunchOptions(files, False, args.present))
        window = app.get_windows()[0]
        window.resize(width, height)
        GLib.timeout_add(args.delay // 2, run_js, window, args.js)
        GLib.timeout_add(args.delay, capture, window)

    app.connect("command-line", lambda a, c: (activate(a), 0)[1])
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
