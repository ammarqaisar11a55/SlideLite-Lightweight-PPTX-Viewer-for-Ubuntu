"""PowerPoint's built-in table styles.

PowerPoint does not store the definition of a built-in table style in the
file when it is used, only its GUID.  The 74 built-in styles are 11 families
x (no accent | accent 1-6); each family is described declaratively below and
expanded into the same ``a:tblStyle`` XML that PowerPoint writes for
custom styles, so a single code path renders both.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache

A = "http://schemas.openxmlformats.org/drawingml/2006/main"

# GUID -> (family, accent).  GUIDs are documented by Microsoft for the
# Office 2010 table style gallery.
BUILTIN_IDS = {
    "{E8034E78-7F5D-4C2E-B375-FC64B27BC917}": ("Dark Style 1", ""),
    "{125E5076-3810-47DD-B79F-674D7AD40C01}": ("Dark Style 1", "accent1"),
    "{37CE84F3-28C3-443E-9E96-99CF82512B78}": ("Dark Style 1", "accent2"),
    "{D03447BB-5D67-496B-8E87-E561075AD55C}": ("Dark Style 1", "accent3"),
    "{E929F9F4-4A8F-4326-A1B4-22849713DDAB}": ("Dark Style 1", "accent4"),
    "{8FD4443E-F989-4FC4-A0C8-D5A2AF1F390B}": ("Dark Style 1", "accent5"),
    "{AF606853-7671-496A-8E4F-DF71F8EC918B}": ("Dark Style 1", "accent6"),
    "{5202B0CA-FC54-4496-8BCA-5EF66A818D29}": ("Dark Style 2", ""),
    "{0660B408-B3CF-4A94-85FC-2B1E0A45F4A2}": ("Dark Style 2", "accent1"),
    "{91EBBBCC-DAD2-459C-BE2E-F6DE35CF9A28}": ("Dark Style 2", "accent3"),
    "{46F890A9-2807-4EBB-B81D-B2AA78EC7F39}": ("Dark Style 2", "accent5"),
    "{9D7B26C5-4107-4FEC-AEDC-1716B250A1EF}": ("Light Style 1", ""),
    "{3B4B98B0-60AC-42C2-AFA5-B58CD77FA1E5}": ("Light Style 1", "accent1"),
    "{0E3FDE45-AF77-4B5C-9715-49D594BDF05E}": ("Light Style 1", "accent2"),
    "{C083E6E3-FA7D-4D7B-A595-EF9225AFEA82}": ("Light Style 1", "accent3"),
    "{D27102A9-8310-4765-A935-A1911B00CA55}": ("Light Style 1", "accent4"),
    "{5FD0F851-EC5A-4D38-B0AD-8093EC10F338}": ("Light Style 1", "accent5"),
    "{68D230F3-CF80-4859-8CE7-A43EE81993B5}": ("Light Style 1", "accent6"),
    "{7E9639D4-E3E2-4D34-9284-5A2195B3D0D7}": ("Light Style 2", ""),
    "{69012ECD-51FC-41F1-AA8D-1B2483CD663E}": ("Light Style 2", "accent1"),
    "{72833802-FEF1-4C79-8D5D-14CF1EAF98D9}": ("Light Style 2", "accent2"),
    "{F2DE63D5-997A-4646-A377-4702673A728D}": ("Light Style 2", "accent3"),
    "{17292A2E-F333-43FB-9621-5CBBE7FDCDCB}": ("Light Style 2", "accent4"),
    "{5A111915-BE36-4E01-A7E5-04B1672EAD32}": ("Light Style 2", "accent5"),
    "{912C8C85-51F0-491E-9774-3900AFEF0FD7}": ("Light Style 2", "accent6"),
    "{616DA210-FB5B-4158-B5E0-FEB733F419BA}": ("Light Style 3", ""),
    "{BC89EF96-8CEA-46FF-86C4-4CE0E7609802}": ("Light Style 3", "accent1"),
    "{5DA37D80-6434-44D0-A028-1B22A696006F}": ("Light Style 3", "accent2"),
    "{8799B23B-EC83-4686-B30A-512413B5E67A}": ("Light Style 3", "accent3"),
    "{ED083AE6-46FA-4A59-8FB0-9F97EB10719F}": ("Light Style 3", "accent4"),
    "{BDBED569-4797-4DF1-A0F4-6AAB3CD982D8}": ("Light Style 3", "accent5"),
    "{E8B1032C-EA38-4F05-BA0D-38AFFFC7BED3}": ("Light Style 3", "accent6"),
    "{793D81CF-94F2-401A-BA57-92F5A7B2D0C5}": ("Medium Style 1", ""),
    "{B301B821-A1FF-4177-AEE7-76D212191A09}": ("Medium Style 1", "accent1"),
    "{9DCAF9ED-07DC-4A11-8D7F-57B35C25682E}": ("Medium Style 1", "accent2"),
    "{1FECB4D8-DB02-4DC6-A0A2-4F2EBAE1DC90}": ("Medium Style 1", "accent3"),
    "{1E171933-4619-4E11-9A3F-F7608DF75F80}": ("Medium Style 1", "accent4"),
    "{FABFCF23-3B69-468F-B69F-88F6DE6A72F2}": ("Medium Style 1", "accent5"),
    "{10A1B5D5-9B99-4C35-A422-299274C87663}": ("Medium Style 1", "accent6"),
    "{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}": ("Medium Style 2", ""),
    "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}": ("Medium Style 2", "accent1"),
    "{21E4AEA4-8DFA-4A89-87EB-49C32662AFE0}": ("Medium Style 2", "accent2"),
    "{F5AB1C69-6EDB-4FF4-983F-18BD219EF322}": ("Medium Style 2", "accent3"),
    "{00A15C55-8517-42AA-B614-E9B94910E393}": ("Medium Style 2", "accent4"),
    "{7DF18680-E054-41AD-8BC1-D1AEF772440D}": ("Medium Style 2", "accent5"),
    "{93296810-A885-4BE3-A3E7-6D5BEEA58F35}": ("Medium Style 2", "accent6"),
    "{8EC20E35-A176-4012-BC5E-935CFFF8708E}": ("Medium Style 3", ""),
    "{6E25E649-3F16-4E02-A733-19D2CDBF48F0}": ("Medium Style 3", "accent1"),
    "{85BE263C-DBD7-4A20-BB59-AAB30ACAA65A}": ("Medium Style 3", "accent2"),
    "{EB344D84-9AFB-497E-A393-DC336BA19D2E}": ("Medium Style 3", "accent3"),
    "{EB9631B5-78F2-41C9-869B-9F39066F8104}": ("Medium Style 3", "accent4"),
    "{74C1A8A3-306A-4EB7-A6B1-4F7E0EB9C5D6}": ("Medium Style 3", "accent5"),
    "{2A488322-F2BA-4B5B-9748-0D474271808F}": ("Medium Style 3", "accent6"),
    "{D7AC3CCA-C797-4891-BE02-D94E43425B78}": ("Medium Style 4", ""),
    "{69CF1AB2-1976-4502-BF36-3FF5EA218861}": ("Medium Style 4", "accent1"),
    "{8A107856-5554-42FB-B03E-39F5DBC370BA}": ("Medium Style 4", "accent2"),
    "{0505E3EF-67EA-436B-97B2-0124C06EBD24}": ("Medium Style 4", "accent3"),
    "{C4B1156A-380E-4F78-BDF5-A606A8083BF9}": ("Medium Style 4", "accent4"),
    "{22838BEF-8BB2-4498-84A7-C5851F593DF1}": ("Medium Style 4", "accent5"),
    "{16D9F66E-5EB9-4882-86FB-DCBF35E3C3E4}": ("Medium Style 4", "accent6"),
    "{2D5ABB26-0587-4C30-8999-92F81FD0307C}": ("Themed Style 1", ""),
    "{3C2FFA5D-87B4-456A-9821-1D502468CF0F}": ("Themed Style 1", "accent1"),
    "{284E427A-3D55-4303-BF80-6455036E1DE7}": ("Themed Style 1", "accent2"),
    "{69C7853C-536D-4A76-A0AE-DD22124D55A5}": ("Themed Style 1", "accent3"),
    "{775DCB02-9BB8-47FD-8907-85C794F793BA}": ("Themed Style 1", "accent4"),
    "{35758FB7-9AC5-4552-8A53-C91805E547FA}": ("Themed Style 1", "accent5"),
    "{08FB837D-C827-4EFA-A057-4D05807E0F7C}": ("Themed Style 1", "accent6"),
    "{5940675A-B579-460E-94D1-54222C63F5DA}": ("Themed Style 2", ""),
    "{D113A9D2-9D6B-4929-AA2D-F23B5EE8CBE7}": ("Themed Style 2", "accent1"),
    "{18603FDC-E32A-4AB5-989C-0864C3EAD2B8}": ("Themed Style 2", "accent2"),
    "{306799F8-075E-4A3A-A7F6-7FBC6576F1A4}": ("Themed Style 2", "accent3"),
    "{E269D01E-BC32-4049-B463-5C60D7B0CCD2}": ("Themed Style 2", "accent4"),
    "{327F97BB-C833-4FB7-BDE5-3F7075034690}": ("Themed Style 2", "accent5"),
    "{638B1855-1B75-4FBE-930C-398BA8C253C6}": ("Themed Style 2", "accent6"),
}

# Colour specs: "A" is the style accent (or the family's no-accent default),
# optionally followed by transforms, e.g. ("A", "tint", 20000).
# Part spec keys: fill, text, bold, and border edges
# (left/right/top/bottom/insideH/insideV) as (colour spec, width EMU).
_THIN, _MID, _THICK = 12700, 25400, 38100


def _family(name: str, accent: str):
    has_accent = bool(accent)
    if name == "Themed Style 1":
        border = ("A",) if has_accent else None
        whole = {"text": ("dk1",) if has_accent else ("tx1",)}
        if has_accent:
            whole.update(
                {
                    e: (border, _THIN)
                    for e in ("left", "right", "top", "bottom", "insideH", "insideV")
                }
            )
        return {
            "wholeTbl": whole,
            "band1H": {"fill": ("A", "alpha", 40000)} if has_accent else {},
            "band1V": {"fill": ("A", "alpha", 40000)} if has_accent else {},
            "firstRow": {
                "fill": ("A",),
                "text": ("lt1",),
                "bold": True,
                "bottom": (("lt1",), _THIN),
            }
            if has_accent
            else {"bold": True},
            "lastRow": {"bold": True, "top": (("A",), _MID)} if has_accent else {"bold": True},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "dk1"
    if name == "Themed Style 2":
        if has_accent:
            edge = (("A", "tint", 50000), _THIN)
            return {
                "wholeTbl": {
                    "text": ("lt1",),
                    "fill": ("A",),
                    "left": edge,
                    "right": edge,
                    "top": edge,
                    "bottom": edge,
                },
                "band1H": {"fill": ("lt1", "alpha", 20000)},
                "band1V": {"fill": ("lt1", "alpha", 20000)},
                "firstRow": {"text": ("lt1",), "bold": True, "bottom": (("lt1",), _THICK)},
                "lastRow": {"bold": True, "top": (("lt1",), _THICK)},
                "firstCol": {"bold": True, "right": (("lt1",), _THIN)},
                "lastCol": {"bold": True, "left": (("lt1",), _THIN)},
            }, "dk1"
        edge = (("tx1",), _THIN)
        return {
            "wholeTbl": {
                "text": ("tx1",),
                **{e: edge for e in ("left", "right", "top", "bottom", "insideH", "insideV")},
            },
            "firstRow": {"bold": True},
            "lastRow": {"bold": True},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "tx1"
    if name == "Light Style 1":
        return {
            "wholeTbl": {"text": ("tx1",), "top": (("A",), _THIN), "bottom": (("A",), _THIN)},
            "band1H": {"fill": ("A", "alpha", 20000)},
            "band1V": {"fill": ("A", "alpha", 20000)},
            "firstRow": {"bold": True, "text": ("tx1",), "bottom": (("A",), _THIN)},
            "lastRow": {"bold": True, "top": (("A",), _THIN)},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "tx1"
    if name == "Light Style 2":
        edge = (("A",), _THIN)
        return {
            "wholeTbl": {
                "text": ("tx1",),
                "left": edge,
                "right": edge,
                "top": edge,
                "bottom": edge,
            },
            "band1H": {"top": edge, "bottom": edge},
            "band1V": {"left": edge, "right": edge},
            "band2V": {"left": edge, "right": edge},
            "firstRow": {"fill": ("A",), "text": ("bg1",), "bold": True},
            "lastRow": {"bold": True, "top": (("A",), _THICK)},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "tx1"
    if name == "Light Style 3":
        edge = (("A",), _THIN)
        return {
            "wholeTbl": {
                "text": ("tx1",),
                **{e: edge for e in ("left", "right", "top", "bottom", "insideH", "insideV")},
            },
            "band1H": {"fill": ("A", "alpha", 20000)},
            "band1V": {"fill": ("A", "alpha", 20000)},
            "firstRow": {"text": ("A",), "bold": True, "bottom": (("A",), _MID)},
            "lastRow": {"bold": True, "top": (("A",), _MID)},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "tx1"
    if name == "Medium Style 1":
        edge = (("A",), _THIN)
        return {
            "wholeTbl": {
                "text": ("dk1",),
                "fill": ("lt1",),
                "left": edge,
                "right": edge,
                "top": edge,
                "bottom": edge,
                "insideH": edge,
            },
            "band1H": {"fill": ("A", "tint", 20000)},
            "band1V": {"fill": ("A", "tint", 20000)},
            "firstRow": {"fill": ("A",), "text": ("lt1",), "bold": True},
            "lastRow": {"fill": ("lt1",), "bold": True, "top": (("A",), _THICK)},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "dk1"
    if name == "Medium Style 2":
        edge = (("lt1",), _THIN)
        return {
            "wholeTbl": {
                "text": ("dk1",),
                "fill": ("A", "tint", 20000),
                **{e: edge for e in ("left", "right", "top", "bottom", "insideH", "insideV")},
            },
            "band1H": {"fill": ("A", "tint", 40000)},
            "band1V": {"fill": ("A", "tint", 40000)},
            "firstRow": {
                "fill": ("A",),
                "text": ("lt1",),
                "bold": True,
                "bottom": (("lt1",), _THICK),
            },
            "lastRow": {"fill": ("A",), "text": ("lt1",), "bold": True, "top": (("lt1",), _THICK)},
            "firstCol": {"fill": ("A",), "text": ("lt1",), "bold": True},
            "lastCol": {"fill": ("A",), "text": ("lt1",), "bold": True},
        }, "dk1"
    if name == "Medium Style 3":
        edge = (("dk1",), _THICK)
        return {
            "wholeTbl": {"text": ("dk1",), "fill": ("lt1",), "top": edge, "bottom": edge},
            "band1H": {"fill": ("dk1", "tint", 20000)},
            "band1V": {"fill": ("dk1", "tint", 20000)},
            "firstRow": {
                "fill": ("A",),
                "text": ("lt1",),
                "bold": True,
                "bottom": (("dk1",), _THICK),
            },
            "lastRow": {"fill": ("lt1",), "bold": True, "top": (("dk1",), _THICK)},
            "firstCol": {"fill": ("A",), "text": ("lt1",), "bold": True},
            "lastCol": {"fill": ("A",), "text": ("lt1",), "bold": True},
        }, "dk1"
    if name == "Medium Style 4":
        edge = (("A",), _THIN)
        return {
            "wholeTbl": {
                "text": ("dk1",),
                "fill": ("A", "tint", 20000),
                **{e: edge for e in ("left", "right", "top", "bottom", "insideH", "insideV")},
            },
            "band1H": {"fill": ("A", "tint", 40000)},
            "band1V": {"fill": ("A", "tint", 40000)},
            "firstRow": {"fill": ("A", "tint", 20000), "text": ("A",), "bold": True},
            "lastRow": {"fill": ("dk1", "tint", 20000), "bold": True, "top": (("dk1",), _THICK)},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "dk1"
    if name == "Dark Style 1":
        t = "shade" if has_accent else "tint"
        return {
            "wholeTbl": {"text": ("lt1",) if has_accent else ("dk1",), "fill": ("A", t, 20000)},
            "band1H": {"fill": ("A", t, 40000)},
            "band1V": {"fill": ("A", t, 40000)},
            "firstRow": {
                "fill": ("dk1",),
                "text": ("lt1",),
                "bold": True,
                "bottom": (("lt1",), _THICK),
            },
            "lastRow": {"fill": ("A", t, 20000), "bold": True, "top": (("lt1",), _THICK)},
            "firstCol": {"fill": ("A", t, 60000), "bold": True, "right": (("lt1",), _THICK)},
            "lastCol": {"fill": ("A", t, 60000), "bold": True, "left": (("lt1",), _THICK)},
        }, "dk1"
    if name == "Dark Style 2":
        header = {"": "dk1", "accent1": "accent2", "accent3": "accent4", "accent5": "accent6"}.get(
            accent, "dk1"
        )
        return {
            "wholeTbl": {"text": ("dk1",), "fill": ("A", "tint", 20000)},
            "band1H": {"fill": ("A", "tint", 40000)},
            "band1V": {"fill": ("A", "tint", 40000)},
            "firstRow": {"fill": (header,), "text": ("lt1",), "bold": True},
            "lastRow": {"fill": ("A", "tint", 20000), "bold": True, "top": (("dk1",), _THICK)},
            "firstCol": {"bold": True},
            "lastCol": {"bold": True},
        }, "dk1"
    return None, "dk1"


def _color(parent, spec, accent: str) -> None:
    name, *transforms = spec
    if name == "A":
        name = accent
    el = ET.SubElement(parent, f"{{{A}}}schemeClr", {"val": name})
    for kind, value in zip(transforms[0::2], transforms[1::2], strict=False):
        ET.SubElement(el, f"{{{A}}}{kind}", {"val": str(value)})


_EDGE_TAGS = {
    "left": "left",
    "right": "right",
    "top": "top",
    "bottom": "bottom",
    "insideH": "insideH",
    "insideV": "insideV",
}


@lru_cache(maxsize=128)
def builtin_style(style_id: str):
    """a:tblStyle element for a built-in style GUID, or None."""
    entry = BUILTIN_IDS.get(style_id.upper()) if style_id else None
    if entry is None:
        return None
    family, accent = entry
    parts, default = _family(family, accent)
    if parts is None:
        return None
    accent_name = accent or default
    style = ET.Element(f"{{{A}}}tblStyle", {"styleId": style_id, "styleName": family})
    for part_name, spec in parts.items():
        part = ET.SubElement(style, f"{{{A}}}{part_name}")
        if "text" in spec or spec.get("bold"):
            tx = ET.SubElement(part, f"{{{A}}}tcTxStyle", {"b": "on"} if spec.get("bold") else {})
            if "text" in spec:
                _color(tx, spec["text"], accent_name)
        tc = ET.SubElement(part, f"{{{A}}}tcStyle")
        edges = [e for e in _EDGE_TAGS if e in spec]
        if edges:
            borders = ET.SubElement(tc, f"{{{A}}}tcBdr")
            for edge in edges:
                color_spec, width = spec[edge]
                holder = ET.SubElement(borders, f"{{{A}}}{edge}")
                ln = ET.SubElement(holder, f"{{{A}}}ln", {"w": str(width), "cmpd": "sng"})
                _color(ET.SubElement(ln, f"{{{A}}}solidFill"), color_spec, accent_name)
        if "fill" in spec:
            fill = ET.SubElement(tc, f"{{{A}}}fill")
            _color(ET.SubElement(fill, f"{{{A}}}solidFill"), spec["fill"], accent_name)
    return style
