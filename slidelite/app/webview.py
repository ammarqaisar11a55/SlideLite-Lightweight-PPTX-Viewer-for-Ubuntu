"""Hardened WebKit view that renders the SlideLite UI.

Security model (presentations are untrusted input):

* the page is loaded from the private ``slidelite://`` scheme served by
  :class:`slidelite.server.router.Router` from memory;
* the web context is ephemeral (no cookies, caches or storage on disk);
* a content filter plus the page CSP block every non-``slidelite`` load, so a
  presentation can never make the viewer touch the network;
* navigation away from the app page, new windows, plugins and the default
  context menu are disabled; external hyperlinks are routed to the host,
  which asks the user before opening them.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from gi.repository import Gio, GLib, WebKit2

from slidelite.server.router import Response, Router

SCHEME = "slidelite"
APP_URL = f"{SCHEME}://app/index.html"
MESSAGE_HANDLER = "slidelite"

_BLOCK_RULES = json.dumps(
    [
        {"trigger": {"url-filter": ".*"}, "action": {"type": "block"}},
        {"trigger": {"url-filter": f"^{SCHEME}:"}, "action": {"type": "ignore-previous-rules"}},
        {"trigger": {"url-filter": "^data:"}, "action": {"type": "ignore-previous-rules"}},
        {"trigger": {"url-filter": "^blob:"}, "action": {"type": "ignore-previous-rules"}},
        {"trigger": {"url-filter": "^about:"}, "action": {"type": "ignore-previous-rules"}},
    ]
)


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "slidelite"


class WebRuntime:
    """One shared ephemeral web context serving every window."""

    def __init__(self, router: Router, debug: bool = False) -> None:
        self.router = router
        self.debug = debug
        self.context = WebKit2.WebContext.new_ephemeral()
        self.context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
        security = self.context.get_security_manager()
        security.register_uri_scheme_as_secure(SCHEME)
        security.register_uri_scheme_as_cors_enabled(SCHEME)
        self.context.register_uri_scheme(SCHEME, self._on_scheme_request)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="slidelite-render")
        self._pending: set = set()
        self._filter: WebKit2.UserContentFilter | None = None
        self._filter_waiters: list[WebKit2.UserContentManager] = []
        self._load_filter()

    # -- network lockdown ---------------------------------------------------
    def _load_filter(self) -> None:
        try:
            path = _cache_dir() / "content-filters"
            path.mkdir(parents=True, exist_ok=True)
            store = WebKit2.UserContentFilterStore.new(str(path))
            store.save(
                "block-remote", GLib.Bytes.new(_BLOCK_RULES.encode()), None, self._filter_saved
            )
        except Exception:  # CSP still blocks remote loads if this fails
            self._filter = None

    def _filter_saved(self, store, result) -> None:
        try:
            self._filter = store.save_finish(result)
        except GLib.Error:
            return
        for manager in self._filter_waiters:
            manager.add_filter(self._filter)
        self._filter_waiters.clear()

    def protect(self, manager: WebKit2.UserContentManager) -> None:
        if self._filter is not None:
            manager.add_filter(self._filter)
        else:
            self._filter_waiters.append(manager)

    # -- scheme -------------------------------------------------------------
    def _on_scheme_request(self, request: WebKit2.URISchemeRequest) -> None:
        parts = urlsplit(request.get_uri())
        if parts.netloc != "app":
            self._finish(request, Response.not_found(parts.netloc))
            return
        query = dict(parse_qsl(parts.query))
        if not parts.path.startswith("/doc/"):
            # Bundled UI assets are cached in memory: answer immediately.
            self._finish(request, self.router.handle(parts.path, query))
            return
        # Rendering and conversions run on a worker thread so the GTK main
        # loop stays responsive; WebKit requests must finish on the main loop.
        self._pending.add(request)
        future = self._pool.submit(self.router.handle, parts.path, query)
        future.add_done_callback(lambda f: GLib.idle_add(self._finish_future, request, f))

    def _finish_future(self, request: WebKit2.URISchemeRequest, future) -> bool:
        self._pending.discard(request)
        try:
            resp = future.result()
        except Exception as exc:  # pragma: no cover - router already contains errors
            resp = Response.error(500, str(exc))
        self._finish(request, resp)
        return False

    @staticmethod
    def _finish(request: WebKit2.URISchemeRequest, resp: Response) -> None:
        if resp.status >= 400:
            request.finish_error(
                GLib.Error.new_literal(Gio.io_error_quark(), resp.body.decode(errors="replace"), 1)
            )
            return
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(resp.body))
        request.finish(stream, len(resp.body), resp.mime)


class SlideWebView(WebKit2.WebView):
    """The single web view hosting the viewer UI for one window."""

    def __init__(
        self,
        runtime: WebRuntime,
        on_message: Callable[[dict], None],
        on_external_link: Callable[[str], None],
    ) -> None:
        manager = WebKit2.UserContentManager()
        super().__init__(web_context=runtime.context, user_content_manager=manager)
        self._on_message = on_message
        self._on_external_link = on_external_link
        self._ready = False
        self._pending: list[str] = []
        runtime.protect(manager)
        manager.register_script_message_handler(MESSAGE_HANDLER)
        manager.connect(f"script-message-received::{MESSAGE_HANDLER}", self._script_message)

        settings = self.get_settings()
        settings.set_enable_javascript(True)
        settings.set_javascript_can_open_windows_automatically(False)
        settings.set_allow_file_access_from_file_urls(False)
        settings.set_allow_universal_access_from_file_urls(False)
        settings.set_enable_page_cache(False)
        settings.set_enable_html5_local_storage(False)
        settings.set_enable_html5_database(False)
        settings.set_enable_back_forward_navigation_gestures(False)
        settings.set_media_playback_requires_user_gesture(False)
        settings.set_enable_developer_extras(runtime.debug)
        settings.set_enable_write_console_messages_to_stdout(runtime.debug)
        settings.set_enable_webgl(False)

        self.connect("decide-policy", self._decide_policy)
        self.connect("create", lambda *_: None)
        self.connect("context-menu", lambda *_: not runtime.debug)
        self.connect("load-changed", self._load_changed)
        self.load_uri(APP_URL)

    # -- host -> page -------------------------------------------------------
    def send(self, event: str, payload: object = None) -> None:
        args = f"{json.dumps(event)}, {json.dumps(payload)}"
        script = f"window.SlideLiteHost && window.SlideLiteHost.receive({args});"
        if not self._ready:
            self._pending.append(script)
            return
        self.evaluate_javascript(script, -1, None, None, None, None, None)

    def _load_changed(self, _view, event) -> None:
        # LOAD_FINISHED can fire before ES modules have executed, so the page
        # announces readiness itself with a {"cmd": "ready"} message.
        if event == WebKit2.LoadEvent.STARTED:
            self._ready = False

    def _flush(self) -> None:
        self._ready = True
        pending, self._pending = self._pending, []
        for script in pending:
            self.evaluate_javascript(script, -1, None, None, None, None, None)

    # -- page -> host -------------------------------------------------------
    def _script_message(self, _manager, value) -> None:
        try:
            js = value.get_js_value() if hasattr(value, "get_js_value") else value
            message = json.loads(js.to_string())
        except (ValueError, AttributeError):
            return
        if not isinstance(message, dict):
            return
        if message.get("cmd") == "ready":
            self._flush()
        self._on_message(message)

    def _decide_policy(self, _view, decision, kind) -> bool:
        if kind in (
            WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            uri = decision.get_navigation_action().get_request().get_uri()
            if (
                uri.startswith(f"{SCHEME}://app/index.html")
                and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION
            ):
                decision.use()
                return True
            decision.ignore()
            if uri.startswith(("http://", "https://", "mailto:")):
                self._on_external_link(uri)
            elif uri.startswith("file://"):
                # A .pptx dropped onto the view arrives as a navigation.
                self._on_message({"cmd": "open-uri", "uri": uri})
            return True
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            decision.use()
            return True
        return False
