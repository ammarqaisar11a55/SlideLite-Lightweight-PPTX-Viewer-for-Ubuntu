"""Main viewer window: native header bar + the web-rendered viewer."""

from __future__ import annotations

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from slidelite import APP_NAME  # noqa: E402
from slidelite.app import gi_versions  # noqa: F401
from slidelite.app.webview import SlideWebView  # noqa: E402

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class ViewerWindow(Gtk.ApplicationWindow):
    def __init__(self, application, runtime) -> None:
        super().__init__(application=application, title=APP_NAME)
        self.set_default_size(1200, 760)
        self.set_icon_name("slidelite")
        self.runtime = runtime
        self.path: str | None = None
        self._is_fullscreen = False
        self._presenting = False

        self._build_header()
        self.webview = SlideWebView(runtime, self._on_page_message, self._on_external_link)
        self.add(self.webview)
        self._install_actions()
        self.connect("window-state-event", self._on_window_state)
        self.webview.show()

    # -- chrome -------------------------------------------------------------
    def _build_header(self) -> None:
        self.header = Gtk.HeaderBar(show_close_button=True, title=APP_NAME)
        open_button = Gtk.Button(label="Open", action_name="win.open")
        open_button.set_tooltip_text("Open a presentation (Ctrl+O)")
        self.header.pack_start(open_button)

        menu = Gio.Menu()
        theme = Gio.Menu()
        theme.append("Follow System", "app.theme::system")
        theme.append("Light", "app.theme::light")
        theme.append("Dark", "app.theme::dark")
        menu.append_section("Appearance", theme)
        misc = Gio.Menu()
        misc.append("Keyboard Shortcuts", "win.shortcuts")
        misc.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, misc)
        menu_button = Gtk.MenuButton(menu_model=menu)
        menu_button.set_image(
            Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        )
        menu_button.set_tooltip_text("Main menu")
        self.header.pack_end(menu_button)

        self.present_button = Gtk.Button(label="Present", action_name="win.present-start")
        self.present_button.get_style_context().add_class("suggested-action")
        self.present_button.set_tooltip_text("Start the slide show from the beginning (F5)")
        self.header.pack_end(self.present_button)
        self.set_titlebar(self.header)
        self.header.show_all()

    def _install_actions(self) -> None:
        for name, callback in (
            ("open", lambda *_: self.choose_file()),
            ("present-start", lambda *_: self.webview.send("present", {"from": "start"})),
            ("present-current", lambda *_: self.webview.send("present", {"from": "current"})),
            ("fullscreen", lambda *_: self.toggle_fullscreen()),
            ("shortcuts", lambda *_: self.webview.send("show-shortcuts")),
            ("close", lambda *_: self.close()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.lookup_action("present-start").set_enabled(False)
        self.lookup_action("present-current").set_enabled(False)

    # -- file handling ------------------------------------------------------
    def choose_file(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Open Presentation", self, Gtk.FileChooserAction.OPEN, "_Open", "_Cancel"
        )
        pptx = Gtk.FileFilter()
        pptx.set_name("PowerPoint presentations")
        pptx.add_mime_type(PPTX_MIME)
        pptx.add_pattern("*.pptx")
        pptx.add_pattern("*.PPTX")
        dialog.add_filter(pptx)
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        dialog.add_filter(everything)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = dialog.get_filename()
            if path:
                self.open_path(path)
        dialog.destroy()

    def open_path(self, path: str, start_presentation: bool = False) -> None:
        self.get_application().open_in_window(self, path, start_presentation)

    def set_document(self, path: str | None, title: str | None, slide_count: int) -> None:
        self.path = path
        self.header.set_title(title or APP_NAME)
        self.header.set_subtitle(
            f"{slide_count} slide{'s' if slide_count != 1 else ''}" if path else None
        )
        has_slides = slide_count > 0
        self.lookup_action("present-start").set_enabled(has_slides)
        self.lookup_action("present-current").set_enabled(has_slides)

    # -- fullscreen / presentation -----------------------------------------
    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()

    def set_presenting(self, presenting: bool) -> None:
        self._presenting = presenting
        if presenting:
            self.fullscreen()
        else:
            self.unfullscreen()

    def _on_window_state(self, _window, event) -> None:
        self._is_fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        self.webview.send("fullscreen-changed", {"fullscreen": self._is_fullscreen})
        if not self._is_fullscreen and self._presenting:
            # The window manager left fullscreen (e.g. Super key); end the show.
            self._presenting = False
            self.webview.send("present-stop")

    # -- page messages ------------------------------------------------------
    def _on_page_message(self, message: dict) -> None:
        self.get_application().handle_page_message(self, message)

    def _on_external_link(self, uri: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Open external link?",
        )
        dialog.format_secondary_text(
            f"This presentation links to:\n{uri}\n\nIt will open in your default application."
        )
        dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("_Open Link", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            try:
                Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
            except GLib.Error:
                pass
