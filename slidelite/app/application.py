"""Gtk.Application: single instance, opens one window per presentation."""

from __future__ import annotations

import os
import sys

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from slidelite import APP_ID, APP_NAME, __version__  # noqa: E402
from slidelite.app import gi_versions  # noqa: F401
from slidelite.app.webview import WebRuntime  # noqa: E402
from slidelite.app.window import ViewerWindow  # noqa: E402
from slidelite.cli import LaunchOptions, build_parser  # noqa: E402
from slidelite.server.router import Router  # noqa: E402


class SlideLiteApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        GLib.set_application_name(APP_NAME)
        GLib.set_prgname("slidelite")
        self.debug = bool(os.environ.get("SLIDELITE_DEBUG"))
        self.router = Router()
        self.runtime: WebRuntime | None = None

    # -- lifecycle ----------------------------------------------------------
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.runtime = WebRuntime(self.router, debug=self.debug)

        theme = Gio.SimpleAction.new_stateful(
            "theme", GLib.VariantType.new("s"), GLib.Variant("s", "system")
        )
        theme.connect("activate", self._on_theme)
        self.add_action(theme)
        for name, callback in (("about", self._on_about), ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        for action, accels in (
            ("win.open", ["<Control>o"]),
            ("win.present-start", ["F5"]),
            ("win.present-current", ["<Shift>F5"]),
            ("win.fullscreen", ["F11"]),
            ("win.shortcuts", ["<Control>question", "F1"]),
            ("win.close", ["<Control>w"]),
            ("app.quit", ["<Control>q"]),
        ):
            self.set_accels_for_action(action, accels)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        argv = command_line.get_arguments()[1:]
        try:
            ns = build_parser().parse_args(argv)
        except SystemExit as exc:  # argparse already printed usage/version
            return int(exc.code or 0)
        cwd = command_line.get_cwd() or os.getcwd()
        files = []
        for arg in ns.files:
            gfile = Gio.File.new_for_commandline_arg_and_cwd(arg, cwd)
            files.append(gfile.get_path() or arg)
        self.launch(LaunchOptions(tuple(files), ns.fullscreen, ns.presentation))
        return 0

    def launch(self, options: LaunchOptions) -> None:
        if not options.files:
            window = self._new_window()
            if options.fullscreen:
                window.fullscreen()
            window.present()
            return
        for path in options.files:
            window = self._reusable_window() or self._new_window()
            if options.fullscreen:
                window.fullscreen()
            window.present()
            self.open_in_window(window, path, options.presentation)

    def _new_window(self) -> ViewerWindow:
        return ViewerWindow(self, self.runtime)

    def _reusable_window(self) -> ViewerWindow | None:
        for window in self.get_windows():
            if isinstance(window, ViewerWindow) and window.path is None:
                return window
        return None

    # -- documents ----------------------------------------------------------
    def open_in_window(
        self, window: ViewerWindow, path: str, start_presentation: bool = False
    ) -> None:
        # Module 2 replaces this with the real loader.
        window.webview.send("open-requested", {"path": path, "present": start_presentation})

    def handle_page_message(self, window: ViewerWindow, message: dict) -> None:
        cmd = message.get("cmd")
        if self.debug:
            print("page:", message, file=sys.stderr)
        if cmd == "open-dialog":
            window.choose_file()
        elif cmd == "open-uri":
            gfile = Gio.File.new_for_uri(str(message.get("uri", "")))
            if gfile.get_path():
                window.open_path(gfile.get_path())
        elif cmd == "toggle-fullscreen":
            window.toggle_fullscreen()

    # -- actions ------------------------------------------------------------
    def _on_theme(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        for window in self.get_windows():
            if isinstance(window, ViewerWindow):
                window.webview.send("theme", {"theme": value.get_string()})

    def _on_about(self, *_args) -> None:
        dialog = Gtk.AboutDialog(
            transient_for=self.get_active_window(),
            modal=True,
            program_name=APP_NAME,
            version=__version__,
            comments=(
                "A lightweight, offline PowerPoint (.pptx) viewer for Ubuntu.\n"
                "Open → View → Present."
            ),
            license_type=Gtk.License.MIT_X11,
            logo_icon_name="slidelite",
            website="https://github.com/ammarqaisar11a55/SlideLite-Lightweight-PPTX-Viewer-for-Ubuntu",
        )
        dialog.run()
        dialog.destroy()


def run(options: LaunchOptions) -> int:
    app = SlideLiteApplication()
    return app.run([sys.argv[0], *_argv_from_options(options)])


def _argv_from_options(options: LaunchOptions) -> list[str]:
    argv: list[str] = []
    if options.fullscreen:
        argv.append("--fullscreen")
    if options.presentation:
        argv.append("--presentation")
    argv.extend(options.files)
    return argv
