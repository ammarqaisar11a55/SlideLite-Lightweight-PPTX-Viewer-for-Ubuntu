"""A loaded presentation exposed to the UI through the router.

Endpoints, relative to ``/doc/<id>/``:

* ``info.json``          slide count, size, title, per-slide flags
* ``slide/<n>.html``     rendered slide markup (1-based)
* ``part/<partname>``    media referenced by slides (images, video, audio)
"""

from __future__ import annotations

import json
import secrets
import threading
from collections import OrderedDict

from slidelite.presentation import document, metafile
from slidelite.presentation.parts import Deck
from slidelite.render.images import image_size
from slidelite.render.slide import render_slide
from slidelite.server.router import Response, Router, guess_mime


class DocumentSession:
    def __init__(self, presentation: document.Presentation, path: str | None = None) -> None:
        self.presentation = presentation
        self.path = path
        self.id = secrets.token_hex(8)
        self.lock = threading.RLock()
        self.deck = Deck(presentation)
        self.cache_size = 48
        self._cache: OrderedDict[int, str] = OrderedDict()
        self._converted: dict[str, Response] = {}
        self._thumbs: OrderedDict[str, Response] = OrderedDict()

    @property
    def prefix(self) -> str:
        return f"/doc/{self.id}"

    def info(self) -> dict:
        pres = self.presentation
        return {
            "id": self.id,
            "base": f"doc/{self.id}/",
            "path": self.path,
            "filename": pres.filename,
            "title": pres.title or (pres.filename or "Presentation").rsplit(".", 1)[0],
            "kind": pres.kind,
            "width": round(pres.width_pt, 3),
            "height": round(pres.height_pt, 3),
            "slideCount": len(pres.slides),
            "slides": [{"index": s.index, "hidden": s.hidden} for s in pres.slides],
            "issues": pres.issues,
            "hasMacros": pres.has_macros,
        }

    # -- routing --------------------------------------------------------------
    def mount(self, router: Router) -> None:
        router.mount(self.prefix, self.handle)

    def unmount(self, router: Router) -> None:
        router.unmount(self.prefix)

    def handle(self, sub: str, query: dict[str, str]) -> Response:
        if sub == "info.json":
            return Response(json.dumps(self.info()).encode(), "application/json")
        if sub.startswith("slide/") and sub.endswith(".html"):
            try:
                number = int(sub[len("slide/") : -len(".html")])
            except ValueError:
                return Response.not_found(sub)
            if not 1 <= number <= len(self.presentation.slides):
                return Response.not_found(sub)
            with self.lock:
                return Response(self.render_slide(number - 1).encode(), "text/html")
        if sub.startswith("part/"):
            return self.part(sub[len("part/") :])
        if sub.startswith("image/"):
            return self.converted_image(sub[len("image/") :])
        if sub.startswith("thumb/"):
            return self.thumbnail_image(sub[len("thumb/") :])
        return Response.not_found(sub)

    def render_slide(self, index: int) -> str:
        cached = self._cache.get(index)
        if cached is not None:
            self._cache.move_to_end(index)
            return cached
        result = render_slide(self.deck, index, base_url=f"doc/{self.id}/")
        self._cache[index] = result.html
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result.html

    def part(self, partname: str) -> Response:
        partname = "/" + partname.lstrip("/")
        pkg = self.presentation.package
        if not pkg.has(partname) or not partname.lower().startswith(
            ("/ppt/media/", "/ppt/embeddings/")
        ):
            return Response.not_found(partname)
        with self.lock:
            data = pkg.read(partname)
        mime = pkg.content_type(partname) or guess_mime(partname)
        return Response(data, mime, headers={"Cache-Control": "max-age=3600"})

    def converted_image(self, partname: str) -> Response:
        """Images WebKit cannot decode (EMF/WMF/TIFF), converted on demand."""
        partname = "/" + partname.lstrip("/")
        pkg = self.presentation.package
        if not pkg.has(partname) or not partname.lower().startswith(
            ("/ppt/media/", "/ppt/embeddings/")
        ):
            return Response.not_found(partname)
        cached = self._converted.get(partname)
        if cached is not None:
            return cached
        with self.lock:
            data = pkg.read(partname)
        ext = partname.rsplit(".", 1)[-1].lower()
        ctype = pkg.content_type(partname).lower()
        resp = None
        if ext in ("emf", "wmf", "emz", "wmz") or "emf" in ctype or "wmf" in ctype:
            if ext in ("emz", "wmz") or data[:2] == b"\x1f\x8b":
                import gzip

                try:
                    data = gzip.decompress(data)[: 64 * 1024 * 1024]
                except OSError:
                    data = b""
            try:
                svg = metafile.to_svg(data)
                resp = Response(svg.encode("utf-8"), "image/svg+xml")
            except metafile.MetafileError:
                resp = None
        if resp is None:
            png = _pixbuf_png(data)
            resp = (
                Response(png, "image/png") if png else Response(_UNSUPPORTED_SVG, "image/svg+xml")
            )
        resp.headers["Cache-Control"] = "max-age=3600"
        self._converted[partname] = resp
        return resp

    def thumbnail_image(self, partname: str) -> Response:
        """Raster media scaled down for thumbnails (bounded memory for large
        photos); falls back to the original when scaling is unavailable."""
        partname = "/" + partname.lstrip("/")
        cached = self._thumbs.get(partname)
        if cached is not None:
            self._thumbs.move_to_end(partname)
            return cached
        original = self.part(partname)
        if original.status != 200 or not original.mime.startswith(
            ("image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp")
        ):
            return original
        size = image_size(original.body)
        # Decide on pixel dimensions: decoded size, not file size, costs memory.
        if size is not None and max(size[0], size[1]) <= THUMB_MAX_PX * 1.5:
            resp = original
        else:
            scaled = _pixbuf_scaled(original.body, THUMB_MAX_PX)
            resp = (
                Response(scaled[0], scaled[1], headers={"Cache-Control": "max-age=3600"})
                if scaled
                else original
            )
        self._thumbs[partname] = resp
        while len(self._thumbs) > 64:
            self._thumbs.popitem(last=False)
        return resp

    def close(self) -> None:
        self.presentation.close()


def open_session(path: str, lenient: bool = False) -> DocumentSession:
    return DocumentSession(document.load(path, lenient=lenient), path)


def attach_dev_session(host, path: str | None) -> None:
    """Used by the dev server: load ``path`` and announce it to the page."""
    state = {"session": None}

    def load(p: str, lenient: bool = False) -> None:
        try:
            session = open_session(p, lenient)
        except document.PackageError as exc:
            host.emit("load-error", error_payload(exc, p))
            return
        if state["session"]:
            state["session"].unmount(host.router)
            state["session"].close()
        state["session"] = session
        session.mount(host.router)
        host.emit("document", session.info())

    def on_message(message: dict) -> None:
        cmd = message.get("cmd")
        if cmd == "ready" and path:
            if state["session"]:
                host.emit("document", state["session"].info())
            else:
                load(path)
        elif cmd == "open-anyway" and message.get("path"):
            load(message["path"], lenient=True)

    host.on_message = on_message


def error_payload(exc: Exception, path: str) -> dict:
    from slidelite.presentation.document import RecoverablePackageError

    title = getattr(exc, "title", "Unable to open this presentation")
    return {
        "path": path,
        "title": title,
        "message": getattr(exc, "message", str(exc)),
        "detail": getattr(exc, "detail", ""),
        "recoverable": isinstance(exc, RecoverablePackageError),
    }


THUMB_MAX_PX = 480

_UNSUPPORTED_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120" viewBox="0 0 200 120">'
    b'<rect width="200" height="120" fill="#eeeeee"/><path d="M70 80l25-30 20 22 12-12 23 20z" fill="#c4c4c4"/>'
    b'<circle cx="80" cy="45" r="8" fill="#c4c4c4"/></svg>'
)


def _pixbuf_png(data: bytes) -> bytes | None:
    """Decode other formats (TIFF, ...) with GdkPixbuf when available."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        ok, buffer = pixbuf.save_to_bufferv("png", [], [])
        return bytes(buffer) if ok else None
    except (ImportError, ValueError, Exception):  # noqa: BLE001 - any decoder failure means "unsupported"
        return None


def _pixbuf_scaled(data: bytes, max_px: int) -> tuple[bytes, str] | None:
    """Downscale an image so its longest side is at most ``max_px``."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        w, h = pixbuf.get_width(), pixbuf.get_height()
        scale = max_px / max(w, h)
        if scale >= 1:
            return None
        small = pixbuf.scale_simple(
            max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR
        )
        if small.get_has_alpha():
            ok, buffer = small.save_to_bufferv("png", ["compression"], ["3"])
            mime = "image/png"
        else:
            ok, buffer = small.save_to_bufferv("jpeg", ["quality"], ["85"])
            mime = "image/jpeg"
        return (bytes(buffer), mime) if ok else None
    except Exception:  # noqa: BLE001 - thumbnails fall back to the original
        return None
