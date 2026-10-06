"""Theme parts: colour scheme, font scheme and format scheme (style matrix)."""

from __future__ import annotations

from dataclasses import dataclass, field

from slidelite.presentation.xmlsafe import local, q

# Office 2013+ default theme, used when a deck has no (readable) theme.
DEFAULT_SCHEME = {
    "dk1": "000000",
    "lt1": "FFFFFF",
    "dk2": "44546A",
    "lt2": "E7E6E6",
    "accent1": "4472C4",
    "accent2": "ED7D31",
    "accent3": "A5A5A5",
    "accent4": "FFC000",
    "accent5": "5B9BD5",
    "accent6": "70AD47",
    "hlink": "0563C1",
    "folHlink": "954F72",
}


@dataclass
class FontScheme:
    major: dict[str, str] = field(default_factory=lambda: {"latin": "Calibri Light"})
    minor: dict[str, str] = field(default_factory=lambda: {"latin": "Calibri"})


@dataclass
class Theme:
    colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SCHEME))
    fonts: FontScheme = field(default_factory=FontScheme)
    fill_styles: list = field(default_factory=list)
    line_styles: list = field(default_factory=list)
    effect_styles: list = field(default_factory=list)
    bg_fill_styles: list = field(default_factory=list)
    name: str = ""

    def resolve_typeface(self, typeface: str | None, script: str = "latin") -> str | None:
        """'+mj-lt' / '+mn-ea' ... -> actual family name."""
        if not typeface:
            return None
        if not typeface.startswith("+"):
            return typeface
        group = self.fonts.major if typeface.startswith("+mj") else self.fonts.minor
        kind = {"lt": "latin", "ea": "ea", "cs": "cs"}.get(typeface[-2:], "latin")
        return group.get(kind) or group.get("latin")


def parse_theme(root) -> Theme:
    theme = Theme(name=root.get("name", ""))
    elements = root.find(q("a:themeElements"))
    if elements is None:
        return theme
    scheme = elements.find(q("a:clrScheme"))
    if scheme is not None:
        for slot in scheme:
            name = local(slot.tag)
            for colour in slot:
                kind = local(colour.tag)
                if kind == "srgbClr":
                    theme.colors[name] = colour.get("val", "000000")
                elif kind == "sysClr":
                    theme.colors[name] = colour.get("lastClr") or (
                        "000000" if colour.get("val") == "windowText" else "FFFFFF"
                    )
    fonts = elements.find(q("a:fontScheme"))
    if fonts is not None:
        for attr, tag in (("major", "a:majorFont"), ("minor", "a:minorFont")):
            group = fonts.find(q(tag))
            if group is None:
                continue
            values: dict[str, str] = {}
            for kind in ("latin", "ea", "cs"):
                el = group.find(q(f"a:{kind}"))
                if el is not None and el.get("typeface"):
                    values[kind] = el.get("typeface")
            for font in group.findall(q("a:font")):
                if font.get("script") and font.get("typeface"):
                    values[f"script:{font.get('script')}"] = font.get("typeface")
            if values.get("latin"):
                setattr(theme.fonts, attr, values)
    fmt = elements.find(q("a:fmtScheme"))
    if fmt is not None:
        theme.fill_styles = _kids(fmt.find(q("a:fillStyleLst")))
        theme.line_styles = _kids(fmt.find(q("a:lnStyleLst")))
        theme.effect_styles = _kids(fmt.find(q("a:effectStyleLst")))
        theme.bg_fill_styles = _kids(fmt.find(q("a:bgFillStyleLst")))
    return theme


def _kids(el) -> list:
    return list(el) if el is not None else []
