"""Presentation loading: presentation.xml, slide discovery and slide size."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from slidelite.presentation import xmlsafe
from slidelite.presentation.opc import Package, PackageError
from slidelite.presentation.xmlsafe import q

EMU_PER_PT = 12700
DEFAULT_SLIDE_SIZE = (9144000, 6858000)  # 10in x 7.5in, the schema default

_PML = "application/vnd.openxmlformats-officedocument.presentationml"
_PML_MACRO = "application/vnd.ms-powerpoint"
MAIN_CONTENT_TYPES = {
    f"{_PML}.presentation.main+xml": "presentation",
    f"{_PML}.slideshow.main+xml": "slideshow",
    f"{_PML}.template.main+xml": "template",
    f"{_PML_MACRO}.presentation.macroEnabled.main+xml": "presentation",
    f"{_PML_MACRO}.slideshow.macroEnabled.main+xml": "slideshow",
    f"{_PML_MACRO}.template.macroEnabled.main+xml": "template",
}

_HIDDEN_RE = re.compile(rb"<p:sld\b[^>]*\bshow=\"(0|false)\"")


class RecoverablePackageError(PackageError):
    """Parts of the deck are broken; it can still be opened "anyway"."""

    title = "Some content in this presentation could not be read"

    def __init__(self, issues: list[str]) -> None:
        super().__init__(
            "Some parts of this presentation are damaged or use features SlideLite does not "
            "support. You can open it anyway; affected slides may look incomplete.",
            "\n".join(issues[:20]),
        )
        self.issues = issues


@dataclass
class SlideRef:
    index: int
    partname: str
    hidden: bool = False


@dataclass
class Presentation:
    package: Package
    main_part: str
    width_emu: int
    height_emu: int
    slides: list[SlideRef]
    kind: str = "presentation"
    title: str | None = None
    filename: str | None = None
    issues: list[str] = field(default_factory=list)
    root: object = None

    @property
    def width_pt(self) -> float:
        return self.width_emu / EMU_PER_PT

    @property
    def height_pt(self) -> float:
        return self.height_emu / EMU_PER_PT

    @property
    def has_macros(self) -> bool:
        return any(p.lower().endswith("vbaproject.bin") for p in self.package.partnames())

    def close(self) -> None:
        self.package.close()


def load(source: str | Path | BinaryIO, lenient: bool = False) -> Presentation:
    """Open a presentation.  Raises :class:`PackageError` subclasses on failure.

    With ``lenient=False`` structural damage raises
    :class:`RecoverablePackageError`; call again with ``lenient=True`` to open
    whatever is readable ("Open Anyway").
    """
    package = Package(source)
    try:
        return _load(package, source, lenient)
    except Exception:
        package.close()
        raise


def _load(package: Package, source, lenient: bool) -> Presentation:
    main = package.main_part()
    issues: list[str] = []
    kind = MAIN_CONTENT_TYPES.get(package.content_type(main), "presentation")
    try:
        root = package.xml(main)
    except PackageError:
        raise
    except Exception as exc:
        raise PackageError(
            "The presentation is damaged and cannot be read.", f"{main}: {exc}"
        ) from None
    if xmlsafe.local(root.tag) != "presentation":
        raise PackageError("The file is not a PowerPoint presentation.", f"root element {root.tag}")

    size = root.find(q("p:sldSz"))
    width = xmlsafe.attr_int(size, "cx", DEFAULT_SLIDE_SIZE[0]) or DEFAULT_SLIDE_SIZE[0]
    height = xmlsafe.attr_int(size, "cy", DEFAULT_SLIDE_SIZE[1]) or DEFAULT_SLIDE_SIZE[1]
    if not (EMU_PER_PT <= width <= 51206400 * 2 and EMU_PER_PT <= height <= 51206400 * 2):
        issues.append(f"invalid slide size {width}x{height}")
        width, height = DEFAULT_SLIDE_SIZE

    rels = package.rels(main)
    slides: list[SlideRef] = []
    seen: set[str] = set()
    for sld_id in xmlsafe.children(root.find(q("p:sldIdLst")), "p:sldId"):
        rel_id = xmlsafe.rid(sld_id, "id")
        rel = rels.get(rel_id or "")
        if rel is None or rel.external:
            issues.append(f"slide relationship {rel_id} is missing")
            continue
        if not package.has(rel.target):
            issues.append(f"slide part {rel.target} is missing")
            continue
        if rel.target in seen:
            continue
        seen.add(rel.target)
        slides.append(SlideRef(len(slides), rel.target, _is_hidden(package, rel.target)))

    if not slides and not issues:
        # Tolerate packages with an empty or absent slide list but real slides.
        for rel in rels.values():
            if rel.kind == "slide" and package.has(rel.target) and rel.target not in seen:
                seen.add(rel.target)
                slides.append(SlideRef(len(slides), rel.target, _is_hidden(package, rel.target)))
        slides.sort(key=lambda s: _natural_key(s.partname))
        for i, ref in enumerate(slides):
            ref.index = i

    if issues and not lenient:
        raise RecoverablePackageError(issues)

    filename = None
    if isinstance(source, (str, Path)):
        filename = os.path.basename(str(source))
    return Presentation(
        package=package,
        main_part=main,
        width_emu=width,
        height_emu=height,
        slides=slides,
        kind=kind,
        title=package.core_title(),
        filename=filename,
        issues=issues,
        root=root,
    )


def _is_hidden(package: Package, partname: str) -> bool:
    try:
        with package.open(partname) as fh:
            head = fh.read(4096)
    except Exception:
        return False
    return bool(_HIDDEN_RE.search(head))


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]
