"""Development server: serves the viewer UI on 127.0.0.1 for browser testing.

    python -m slidelite.devserver [deck.pptx] [--port 8765]

It shares the router used inside the app, so a regular browser can be used to
inspect rendering and the UI.  It binds to the loopback interface only.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from slidelite.server.router import Router


class DevHost:
    """Minimal stand-in for the GTK host used by the bridge."""

    def __init__(self, router: Router) -> None:
        self.router = router
        self.events: list[tuple[str, object]] = []
        self.lock = threading.Lock()
        self.on_message = None

    def emit(self, event: str, payload: object = None) -> None:
        with self.lock:
            self.events.append((event, payload))

    def drain(self) -> list[tuple[str, object]]:
        with self.lock:
            events, self.events = self.events, []
        return events


def make_handler(host: DevHost):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _reply(self, status: int, body: bytes, mime: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path == "/__events":
                self._reply(200, json.dumps(host.drain()).encode(), "application/json")
                return
            resp = host.router.handle(parts.path, dict(parse_qsl(parts.query)))
            self._reply(resp.status, resp.body, resp.mime, resp.headers)

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/__bridge":
                self._reply(404, b"", "text/plain")
                return
            length = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            try:
                message = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                message = {}
            if host.on_message and isinstance(message, dict):
                host.on_message(message)
            self._reply(200, json.dumps(host.drain()).encode(), "application/json")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    router = Router(cache_static=False)
    host = DevHost(router)
    try:
        from slidelite.server.session import attach_dev_session
    except ImportError:
        attach_dev_session = None
    if attach_dev_session is not None:
        attach_dev_session(host, args.file)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(host))
    print(f"SlideLite dev server on http://127.0.0.1:{args.port}/index.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
