"""Gtk.Application: single instance, opens one window per presentation."""

from __future__ import annotations

import os
import sys
import threading

from gi.repository import Gio, GLib, Gtk

from slidelite import APP_ID, APP_NAME, REPO_URL, __version__
from slidelite.app.webview import WebRuntime
from slidelite.app.window import ViewerWindow
from slidelite.cli import LaunchOptions, build_parser
from slidelite.recent import RecentFiles
from slidelite.server.router import Router
from slidelite.server.session import DocumentSession, error_payload, open_session
from slidelite.settings import Settings


class SlideLiteApplication(Gtk.Application):
    def __init__(self, application_id: str | None = APP_ID) -> None:
        # application_id=None gives an isolated, non-D-Bus instance (tests, tools).
        super().__init__(
            application_id=application_id, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        GLib.set_application_name(APP_NAME)
        GLib.set_prgname("slidelite")
        self.debug = bool(os.environ.get("SLIDELITE_DEBUG"))
        self.router = Router()
        self.runtime: WebRuntime | None = None
        self.settings = Settings()
        self.recent = RecentFiles()
        self.system_dark = False
        self._portal = None

    # -- lifecycle ----------------------------------------------------------
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.runtime = WebRuntime(self.router, debug=self.debug)

        theme = Gio.SimpleAction.new_stateful(
            "theme", GLib.VariantType.new("s"), GLib.Variant("s", self.settings.get("theme"))
        )
        theme.connect("activate", self._on_theme)
        self.add_action(theme)
        for key, name in (("useTimings", "use-timings"), ("loop", "loop")):
            toggle = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant("b", self.settings.get(key))
            )
            toggle.connect("activate", self._on_toggle, key)
            self.add_action(toggle)
        clear = Gio.SimpleAction.new("clear-recent", None)
        clear.connect("activate", lambda *_: self.clear_recent())
        self.add_action(clear)
        self._watch_system_theme()
        self._apply_gtk_theme()
        for name, callback in (
            ("about", self._on_about),
            ("source", lambda *_: self._open_repo()),
            ("quit", lambda *_: self.quit()),
        ):
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
        self,
        window: ViewerWindow,
        path: str,
        start_presentation: bool = False,
        lenient: bool = False,
    ) -> None:
        """Load ``path`` off the main thread, then hand it to ``window``."""
        window.webview.send("open-requested", {"path": path, "present": start_presentation})
        window.loading_token = token = object()

        def work() -> None:
            try:
                session = open_session(path, lenient=lenient)
            except Exception as exc:  # every failure becomes a friendly error page
                GLib.idle_add(self._load_failed, window, token, path, exc)
                return
            GLib.idle_add(self._load_done, window, token, session, start_presentation)

        threading.Thread(target=work, name="slidelite-load", daemon=True).start()

    def _load_done(
        self, window: ViewerWindow, token, session: DocumentSession, present: bool
    ) -> bool:
        if window.loading_token is not token or window.get_window() is None:
            session.close()
            return False
        window.attach_session(session, self.router)
        if session.path:
            self.recent.add(session.path)
            self.broadcast_recent()
        payload = session.info()
        payload["present"] = present
        window.webview.send("document", payload)
        return False

    def _load_failed(self, window: ViewerWindow, token, path: str, exc: Exception) -> bool:
        if window.loading_token is not token:
            return False
        if self.debug:
            import traceback

            traceback.print_exception(exc)
        window.webview.send("load-error", error_payload(exc, path))
        return False

    def handle_page_message(self, window: ViewerWindow, message: dict) -> None:
        cmd = message.get("cmd")
        if self.debug:
            print("page:", message, file=sys.stderr)
        if cmd == "ready":
            self.send_preferences(window)
        elif cmd == "open-dialog":
            window.choose_file()
        elif cmd == "open-uri":
            gfile = Gio.File.new_for_uri(str(message.get("uri", "")))
            if gfile.get_path():
                window.open_path(gfile.get_path())
        elif cmd == "toggle-fullscreen":
            window.toggle_fullscreen()
        elif cmd == "open-anyway" and message.get("path"):
            self.open_in_window(window, str(message["path"]), lenient=True)
        elif cmd == "close-document":
            window.detach_session(self.router)
        elif cmd == "open-link" and isinstance(message.get("url"), str):
            # Links from slides are untrusted: the window asks before opening.
            window.confirm_external_link(message["url"])
        elif cmd == "present-start":
            window.set_presenting(True)
        elif cmd == "present-stop":
            window.set_presenting(False)
        elif cmd == "setting" and isinstance(message.get("key"), str):
            if self.settings.set(message["key"], message.get("value")):
                self.broadcast("settings", self.settings.as_dict(), exclude=window)
        elif cmd == "open-recent" and isinstance(message.get("path"), str):
            window.open_path(message["path"])
        elif cmd == "remove-recent" and isinstance(message.get("path"), str):
            self.recent.remove(message["path"])
            self.broadcast_recent()
        elif cmd == "clear-recent":
            self.clear_recent()
        elif cmd == "open-repo":
            window.open_trusted_uri(REPO_URL)

    # -- preferences ------------------------------------------------------------
    def viewer_windows(self) -> list[ViewerWindow]:
        return [w for w in self.get_windows() if isinstance(w, ViewerWindow)]

    def broadcast(self, event: str, payload, exclude=None) -> None:
        for window in self.viewer_windows():
            if window is not exclude:
                window.webview.send(event, payload)

    def theme_payload(self) -> dict:
        return {"theme": self.settings.get("theme"), "systemDark": self.system_dark}

    def send_preferences(self, window: ViewerWindow) -> None:
        window.webview.send("settings", self.settings.as_dict())
        window.webview.send("theme", self.theme_payload())
        window.webview.send("recent", self.recent.listing())
        window.update_recent_menu(self.recent.listing())

    def broadcast_recent(self) -> None:
        listing = self.recent.listing()
        for window in self.viewer_windows():
            window.webview.send("recent", listing)
            window.update_recent_menu(listing)

    def clear_recent(self) -> None:
        self.recent.clear()
        self.broadcast_recent()

    def _apply_gtk_theme(self) -> None:
        theme = self.settings.get("theme")
        dark = theme == "dark" or (theme == "system" and self.system_dark)
        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings is not None:
            gtk_settings.props.gtk_application_prefer_dark_theme = dark

    def _watch_system_theme(self) -> None:
        """Follow the desktop light/dark preference (XDG desktop portal)."""
        try:
            self._portal = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Settings",
                None,
            )
            value = self._portal.call_sync(
                "ReadOne",
                GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            ).unpack()[0]
            self.system_dark = int(value) == 1
            self._portal.connect("g-signal", self._on_portal_signal)
        except (GLib.Error, TypeError, ValueError):
            gtk_settings = Gtk.Settings.get_default()
            name = gtk_settings.props.gtk_theme_name if gtk_settings is not None else ""
            self.system_dark = bool(name) and name.lower().endswith("-dark")

    def _on_portal_signal(self, _proxy, _sender, signal: str, params: GLib.Variant) -> None:
        if signal != "SettingChanged":
            return
        namespace, key, value = params.unpack()
        if namespace == "org.freedesktop.appearance" and key == "color-scheme":
            self.system_dark = int(value) == 1
            self._apply_gtk_theme()
            self.broadcast("theme", self.theme_payload())

    # -- actions ------------------------------------------------------------
    def _on_theme(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.settings.set("theme", value.get_string())
        self._apply_gtk_theme()
        self.broadcast("theme", self.theme_payload())

    def _on_toggle(self, action: Gio.SimpleAction, _param, key: str) -> None:
        state = not action.get_state().get_boolean()
        action.set_state(GLib.Variant("b", state))
        self.settings.set(key, state)
        self.broadcast("settings", self.settings.as_dict())

    def _open_repo(self) -> None:
        window = self.get_active_window()
        if isinstance(window, ViewerWindow):
            window.open_trusted_uri(REPO_URL)

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
            logo_icon_name=APP_ID,
            website=REPO_URL,
            website_label="SlideLite on GitHub",
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
