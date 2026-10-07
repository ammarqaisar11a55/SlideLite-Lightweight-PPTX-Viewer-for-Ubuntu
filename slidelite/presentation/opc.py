"""Open Packaging Conventions: safe, read-only access to a .pptx zip.

* The source file is opened read-only and never modified or extracted.
* Zip bombs are rejected (entry count, total size and compression ratio).
* Part names are normalised; relationship targets that escape the package
  root are refused (path traversal).
"""

from __future__ import annotations

import io
import os
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote

from slidelite.presentation import xmlsafe
from slidelite.presentation.xmlsafe import NS

MAX_ENTRIES = 50_000
MAX_TOTAL_UNCOMPRESSED = 4 * 1024**3  # 4 GiB (videos can be large)
MAX_RATIO = 400  # uncompressed / compressed, for entries above RATIO_MIN_SIZE
RATIO_MIN_SIZE = 32 * 1024 * 1024

RT_OFFICE_DOCUMENT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
RT_STRICT_OFFICE_DOCUMENT = "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"
RT_CORE_PROPS = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class PackageError(Exception):
    """A presentation that cannot be opened; ``message`` is user-facing."""

    title = "Unable to open this presentation"

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class EncryptedPackageError(PackageError):
    title = "This presentation is password-protected"


class LegacyFormatError(PackageError):
    title = "Unsupported file format"


@dataclass(frozen=True)
class Relationship:
    id: str
    type: str
    target: str  # absolute part name, or the raw URL when external
    external: bool = False

    @property
    def kind(self) -> str:
        """Last path segment of the relationship type, e.g. 'slide', 'image'."""
        return self.type.rsplit("/", 1)[-1]


def _normalize(name: str) -> str:
    """Collapse '.'/'..' segments; refuse to climb above the package root.

    (posixpath.normpath silently clamps '/../x' to '/x', hiding traversal.)
    """
    parts: list[str] = []
    for segment in name.replace("\\", "/").split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                raise PackageError("Broken presentation package", f"path escapes package: {name}")
            parts.pop()
        else:
            parts.append(segment)
    return "/" + "/".join(parts)


def normalize_partname(name: str) -> str:
    return _normalize(unquote(name))


def resolve_target(source_part: str, target: str) -> str:
    """Resolve a relationship target relative to the source part."""
    target = unquote(target.replace("\\", "/"))
    if not target.startswith("/"):
        target = posixpath.dirname(source_part) + "/" + target
    return _normalize(target)


def rels_partname(source_part: str) -> str:
    directory, name = posixpath.split(source_part)
    return posixpath.join(directory, "_rels", f"{name}.rels")


def sniff_container(head: bytes) -> str:
    """'zip', 'ole-encrypted', 'ole-ppt', 'ole' or 'unknown'."""
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(_OLE_MAGIC):
        if (
            "EncryptedPackage".encode("utf-16-le") in head
            or "EncryptionInfo".encode("utf-16-le") in head
        ):
            return "ole-encrypted"
        if "PowerPoint Document".encode("utf-16-le") in head:
            return "ole-ppt"
        return "ole"
    return "unknown"


class Package:
    """Read-only view of an OPC package."""

    def __init__(self, source: str | Path | BinaryIO) -> None:
        if isinstance(source, (str, Path)):
            self.path: str | None = str(source)
            try:
                self._fh: BinaryIO = open(source, "rb")  # noqa: SIM115 - held for the document lifetime
            except FileNotFoundError:
                raise PackageError("The file could not be found.", str(source)) from None
            except IsADirectoryError:
                raise PackageError("This is a folder, not a presentation.", str(source)) from None
            except PermissionError:
                raise PackageError(
                    "You don't have permission to read this file.", str(source)
                ) from None
        else:
            self.path = None
            self._fh = source
        self._check_container()
        try:
            self.zip = zipfile.ZipFile(self._fh)
        except Exception as exc:  # noqa: BLE001 - any archive failure means a damaged file
            self.close()
            raise PackageError(
                "The file is damaged or is not a PowerPoint presentation.", str(exc)
            ) from None
        self._names: dict[str, zipfile.ZipInfo] = {}
        self._check_limits()
        self._rels_cache: dict[str, dict[str, Relationship]] = {}
        self._defaults: dict[str, str] = {}
        self._overrides: dict[str, str] = {}
        self._load_content_types()

    # -- validation -----------------------------------------------------------
    def _check_container(self) -> None:
        self._fh.seek(0)
        head = self._fh.read(1 << 16)
        if head.startswith(_OLE_MAGIC) and len(head) >= 0x40:
            # Also read the compound file's directory sector, which may sit
            # anywhere in the file (often at the end for encrypted packages).
            shift = int.from_bytes(head[0x1E:0x20], "little")
            first_dir = int.from_bytes(head[0x30:0x34], "little")
            if 7 <= shift <= 16 and first_dir < 0xFFFFFFFA:
                self._fh.seek((first_dir + 1) << shift)
                head += self._fh.read(1 << 16)
        self._fh.seek(0)
        kind = sniff_container(head)
        if kind == "zip":
            return
        self.close()
        if kind == "ole-encrypted":
            raise EncryptedPackageError(
                "SlideLite can't open encrypted presentations. Remove the password in "
                "PowerPoint (File → Info → Protect Presentation) and try again."
            )
        if kind in ("ole-ppt", "ole"):
            raise LegacyFormatError(
                "This looks like a PowerPoint 97–2003 (.ppt) file. SlideLite opens .pptx "
                "presentations only. Save it as .pptx and try again."
            )
        if not head:
            raise PackageError("The file is empty.")
        if self.path and os.path.basename(self.path).startswith("~$"):
            raise PackageError(
                "This is a temporary lock file that PowerPoint creates while a presentation "
                "is open, not a presentation itself."
            )
        raise PackageError("The file is damaged or is not a PowerPoint presentation.")

    def _check_limits(self) -> None:
        infos = self.zip.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackageError("The presentation package is too large to open safely.")
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            total += info.file_size
            if info.file_size > RATIO_MIN_SIZE and info.file_size > MAX_RATIO * max(
                info.compress_size, 1
            ):
                raise PackageError(
                    "The presentation package looks malicious (compression bomb).", info.filename
                )
            try:
                key = normalize_partname(info.filename).lower()
            except PackageError:
                continue  # entries with traversal names are simply unreachable
            self._names.setdefault(key, info)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise PackageError("The presentation package is too large to open safely.")

    def _load_content_types(self) -> None:
        if not self.has("/[Content_Types].xml"):
            return  # tolerated: content types are guessed from extensions
        try:
            root = xmlsafe.parse(self.read("/[Content_Types].xml"))
        except Exception:
            return
        for el in root:
            name = xmlsafe.local(el.tag)
            if name == "Default":
                self._defaults[el.get("Extension", "").lower()] = el.get("ContentType", "")
            elif name == "Override":
                try:
                    part = normalize_partname(el.get("PartName", ""))
                except PackageError:
                    continue
                self._overrides[part.lower()] = el.get("ContentType", "")

    # -- access -----------------------------------------------------------------
    def close(self) -> None:
        try:
            if getattr(self, "zip", None) is not None:
                self.zip.close()
        finally:
            if self.path is not None:
                self._fh.close()

    def __enter__(self) -> Package:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _info(self, partname: str) -> zipfile.ZipInfo | None:
        try:
            return self._names.get(normalize_partname(partname).lower())
        except PackageError:
            return None

    def has(self, partname: str) -> bool:
        return self._info(partname) is not None

    def size(self, partname: str) -> int:
        info = self._info(partname)
        return info.file_size if info else 0

    def partnames(self) -> list[str]:
        return [normalize_partname(i.filename) for i in self._names.values()]

    def read(self, partname: str, max_bytes: int | None = None) -> bytes:
        info = self._info(partname)
        if info is None:
            raise KeyError(partname)
        if max_bytes is not None and info.file_size > max_bytes:
            raise PackageError("A part of this presentation is too large.", partname)
        try:
            return self.zip.read(info)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
            raise PackageError(
                "Part of the presentation is damaged.", f"{partname}: {exc}"
            ) from None
        except NotImplementedError as exc:  # unsupported compression method
            raise PackageError(
                "Part of the presentation uses unsupported compression.", str(exc)
            ) from None

    def open(self, partname: str) -> io.BufferedIOBase:
        info = self._info(partname)
        if info is None:
            raise KeyError(partname)
        return self.zip.open(info)

    def xml(self, partname: str):
        return xmlsafe.parse(self.read(partname))

    def content_type(self, partname: str) -> str:
        part = normalize_partname(partname)
        ct = self._overrides.get(part.lower())
        if ct:
            return ct
        ext = posixpath.splitext(part)[1].lstrip(".").lower()
        return self._defaults.get(ext, "")

    # -- relationships --------------------------------------------------------------
    def rels(self, source_part: str) -> dict[str, Relationship]:
        source_part = normalize_partname(source_part)
        cached = self._rels_cache.get(source_part)
        if cached is not None:
            return cached
        result: dict[str, Relationship] = {}
        rels_name = rels_partname(source_part) if source_part != "/" else "/_rels/.rels"
        if self.has(rels_name):
            try:
                root = xmlsafe.parse(self.read(rels_name))
            except Exception:
                root = None
            for el in root if root is not None else ():
                if xmlsafe.local(el.tag) != "Relationship":
                    continue
                rel_id = el.get("Id")
                target = el.get("Target", "")
                if not rel_id:
                    continue
                external = el.get("TargetMode", "").lower() == "external"
                if not external:
                    try:
                        target = resolve_target(source_part, target)
                    except PackageError:
                        continue
                result[rel_id] = Relationship(rel_id, el.get("Type", ""), target, external)
        self._rels_cache[source_part] = result
        return result

    def related(self, source_part: str, kind: str) -> list[Relationship]:
        return [r for r in self.rels(source_part).values() if r.kind == kind]

    def main_part(self) -> str:
        for rel in self.rels("/").values():
            is_main = rel.type in (RT_OFFICE_DOCUMENT, RT_STRICT_OFFICE_DOCUMENT)
            if is_main and not rel.external and self.has(rel.target):
                return rel.target
        # Some generators omit the package relationships; fall back to convention.
        if self.has("/ppt/presentation.xml"):
            return "/ppt/presentation.xml"
        if any(name.startswith("/word/") for name in self.partnames()):
            raise LegacyFormatError("This is a Word document, not a presentation.")
        if any(name.startswith("/xl/") for name in self.partnames()):
            raise LegacyFormatError("This is an Excel workbook, not a presentation.")
        raise PackageError(
            "The file is damaged or is not a PowerPoint presentation.", "no presentation part"
        )

    def core_title(self) -> str | None:
        for rel in self.rels("/").values():
            if rel.type == RT_CORE_PROPS and self.has(rel.target):
                try:
                    root = xmlsafe.parse(self.read(rel.target))
                except Exception:
                    return None
                title = root.find(f"{{{NS['dc']}}}title")
                if title is not None and title.text and title.text.strip():
                    return title.text.strip()
        return None
