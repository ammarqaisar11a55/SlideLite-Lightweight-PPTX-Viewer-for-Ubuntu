"""Maps request paths to in-memory responses.

The same router backs the ``slidelite://app/...`` URI scheme inside WebKit and
the optional 127.0.0.1 development server, so the UI behaves identically in
both.  Nothing here touches the network and nothing is written to disk.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

_MIME_OVERRIDES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".woff2": "font/woff2",
}


@dataclass
class Response:
    body: bytes
    mime: str
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def not_found(cls, what: str = "") -> Response:
        return cls(f"not found: {what}".encode(), "text/plain", 404)

    @classmethod
    def error(cls, status: int, message: str) -> Response:
        return cls(message.encode(), "text/plain", status)


def guess_mime(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class Router:
    """Resolves ``/path`` requests to :class:`Response` objects."""

    def __init__(self, web_root: Path = WEB_ROOT, cache_static: bool = True) -> None:
        self.web_root = web_root.resolve()
        self.cache_static = cache_static
        self._static_cache: dict[str, Response] = {}
        self._handlers: list[tuple[str, object]] = []

    def mount(self, prefix: str, handler) -> None:
        """Register ``handler(subpath, query) -> Response`` for ``prefix``."""
        self._handlers.append((prefix.rstrip("/") + "/", handler))

    def unmount(self, prefix: str) -> None:
        prefix = prefix.rstrip("/") + "/"
        self._handlers = [(p, h) for p, h in self._handlers if p != prefix]

    def handle(self, path: str, query: dict[str, str] | None = None) -> Response:
        path = unquote(path or "/")
        if not path.startswith("/"):
            path = "/" + path
        for prefix, handler in self._handlers:
            if path.startswith(prefix):
                try:
                    return handler(path[len(prefix) :], query or {})
                except Exception as exc:  # never let one bad request take the app down
                    return Response.error(500, f"{type(exc).__name__}: {exc}")
        return self._static(path)

    def _static(self, path: str) -> Response:
        if path in ("/", ""):
            path = "/index.html"
        cached = self._static_cache.get(path)
        if cached is not None:
            return cached
        target = (self.web_root / path.lstrip("/")).resolve()
        # Refuse anything that escapes the bundled web root (path traversal).
        if self.web_root not in target.parents or not target.is_file():
            return Response.not_found(path)
        resp = Response(target.read_bytes(), guess_mime(target.name))
        resp.headers["Cache-Control"] = "no-cache"
        if self.cache_static:
            self._static_cache[path] = resp
        return resp
