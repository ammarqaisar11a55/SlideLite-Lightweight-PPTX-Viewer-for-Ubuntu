"""Hardened XML parsing and DrawingML/PresentationML namespace helpers.

Presentations are untrusted.  Office documents never need DTDs, so any
document type declaration or entity declaration is rejected outright, which
rules out XXE and entity-expansion ("billion laughs") attacks.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from functools import lru_cache

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "dsp": "http://schemas.microsoft.com/office/drawing/2008/diagram",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "p15": "http://schemas.microsoft.com/office/powerpoint/2012/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "a16": "http://schemas.microsoft.com/office/drawing/2014/main",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "v": "urn:schemas-microsoft-com:vml",
}

# ISO/IEC 29500 "Strict" documents use different namespace URIs for the same
# vocabulary; map them onto the transitional ones at parse time.
_STRICT = {
    "http://purl.oclc.org/ooxml/drawingml/main": NS["a"],
    "http://purl.oclc.org/ooxml/presentationml/main": NS["p"],
    "http://purl.oclc.org/ooxml/officeDocument/relationships": NS["r"],
    "http://purl.oclc.org/ooxml/drawingml/chart": NS["c"],
    "http://purl.oclc.org/ooxml/drawingml/diagram": NS["dgm"],
    "http://purl.oclc.org/ooxml/drawingml/picture": NS["pic"],
}

MAX_XML_BYTES = 64 * 1024 * 1024


class XMLSecurityError(ValueError):
    """Raised for XML using DTDs/entities (never present in real OOXML)."""


@lru_cache(maxsize=4096)
def q(name: str) -> str:
    """'a:rPr' -> '{http://...}rPr' (Clark notation)."""
    prefix, _, local = name.partition(":")
    return f"{{{NS[prefix]}}}{local}"


def parse(data: bytes, max_bytes: int = MAX_XML_BYTES) -> ET.Element:
    if len(data) > max_bytes:
        raise XMLSecurityError(f"XML part too large ({len(data)} bytes)")
    head = data[:4096]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        # UTF-16 documents (rare): normalise so the checks below see the text.
        text = data.decode("utf-16")
        data = (
            text.replace('encoding="UTF-16"', 'encoding="utf-8"', 1)
            .replace('encoding="utf-16"', 'encoding="utf-8"', 1)
            .encode("utf-8")
        )
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise XMLSecurityError("DTDs and entity declarations are not allowed")
    # A DTD can only appear literally as "<!DOCTYPE" (expat only accepts
    # UTF-8/UTF-16/Latin-1/ASCII, all covered above), and entity
    # declarations can only live inside one, so the check above suffices.
    root = ET.fromstring(data)
    if root.tag.startswith("{http://purl.oclc.org/ooxml/"):
        _unstrict(root)
    return root


def _unstrict(root: ET.Element) -> None:
    for el in root.iter():
        if el.tag[0] == "{":
            uri, _, local = el.tag[1:].partition("}")
            if uri in _STRICT:
                el.tag = f"{{{_STRICT[uri]}}}{local}"
        for key in [k for k in el.attrib if k.startswith("{http://purl.oclc.org/ooxml/")]:
            uri, _, local = key[1:].partition("}")
            if uri in _STRICT:
                el.attrib[f"{{{_STRICT[uri]}}}{local}"] = el.attrib.pop(key)


# -- markup compatibility ------------------------------------------------------


def resolve_alternate_content(
    root: ET.Element, prefer_choice: Callable[[ET.Element, str], bool] | None = None
) -> ET.Element:
    """Replace every ``mc:AlternateContent`` with the branch we can render.

    By default the ``mc:Fallback`` branch is used, because it is written for
    consumers that do not understand the newer markup (it typically holds a
    picture of ink, equations or new chart types).  ``prefer_choice`` may opt
    into a ``mc:Choice`` (given the element and its ``Requires`` value).
    """
    ac_tag = q("mc:AlternateContent")
    choice_tag = q("mc:Choice")
    fallback_tag = q("mc:Fallback")
    stack = [root]
    while stack:
        parent = stack.pop()
        children = list(parent)
        if any(child.tag == ac_tag for child in children):
            new_children: list[ET.Element] = []
            for child in children:
                if child.tag != ac_tag:
                    new_children.append(child)
                    continue
                chosen = None
                for branch in child:
                    if (
                        branch.tag == choice_tag
                        and prefer_choice
                        and prefer_choice(branch, branch.get("Requires", ""))
                    ):
                        chosen = branch
                        break
                if chosen is None:
                    chosen = child.find(fallback_tag)
                if chosen is None:
                    chosen = child.find(choice_tag)
                if chosen is not None:
                    new_children.extend(list(chosen))
            parent[:] = new_children
            children = new_children
        stack.extend(children)
    return root


# -- small helpers -----------------------------------------------------------------


def child(el: ET.Element | None, name: str) -> ET.Element | None:
    return None if el is None else el.find(q(name))


def children(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [] if el is None else el.findall(q(name))


def path(el: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        if el is None:
            return None
        el = el.find(q(name))
    return el


def local(tag: str) -> str:
    return tag.rpartition("}")[2]


def iter_local(el: ET.Element, name: str) -> Iterator[ET.Element]:
    for node in el.iter():
        if local(node.tag) == name:
            yield node


def attr_int(el: ET.Element | None, name: str, default: int | None = None) -> int | None:
    if el is None:
        return default
    value = el.get(name)
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def attr_bool(el: ET.Element | None, name: str, default: bool = False) -> bool:
    if el is None:
        return default
    value = el.get(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "on")


def rid(el: ET.Element | None, name: str = "embed") -> str | None:
    if el is None:
        return None
    return el.get(f"{{{NS['r']}}}{name}")
