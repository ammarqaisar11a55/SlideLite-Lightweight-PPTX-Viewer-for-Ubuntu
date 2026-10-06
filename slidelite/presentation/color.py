"""DrawingML colour resolution: srgbClr, scrgbClr, hslClr, sysClr, prstClr,
schemeClr (through the colour map) and the colour transforms."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from slidelite.presentation.xmlsafe import local

COLOR_TAGS = {"srgbClr", "scrgbClr", "hslClr", "sysClr", "prstClr", "schemeClr"}


@dataclass(frozen=True)
class Color:
    r: float  # 0..1 sRGB
    g: float
    b: float
    a: float = 1.0

    @classmethod
    def hex(cls, value: str, alpha: float = 1.0) -> Color:
        value = (value or "000000").strip().lstrip("#")
        if len(value) != 6:
            value = (value + "000000")[:6]
        try:
            return cls(
                int(value[0:2], 16) / 255,
                int(value[2:4], 16) / 255,
                int(value[4:6], 16) / 255,
                alpha,
            )
        except ValueError:
            return cls(0, 0, 0, alpha)

    def css(self) -> str:
        r, g, b = (max(0, min(255, round(c * 255))) for c in (self.r, self.g, self.b))
        if self.a >= 0.999:
            return f"#{r:02x}{g:02x}{b:02x}"
        return f"rgba({r},{g},{b},{max(0.0, self.a):.3f})"

    def rgb_hex(self) -> str:
        r, g, b = (max(0, min(255, round(c * 255))) for c in (self.r, self.g, self.b))
        return f"#{r:02x}{g:02x}{b:02x}"

    def with_alpha(self, a: float) -> Color:
        return Color(self.r, self.g, self.b, max(0.0, min(1.0, a)))

    def scaled_lum(self, factor: float) -> Color:
        h, l, s = colorsys.rgb_to_hls(self.r, self.g, self.b)
        r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l * factor)), s)
        return Color(r, g, b, self.a)


BLACK = Color(0, 0, 0)
WHITE = Color(1, 1, 1)

PRESET_COLORS = {
    "aliceBlue": "F0F8FF",
    "antiqueWhite": "FAEBD7",
    "aqua": "00FFFF",
    "aquamarine": "7FFFD4",
    "azure": "F0FFFF",
    "beige": "F5F5DC",
    "bisque": "FFE4C4",
    "black": "000000",
    "blanchedAlmond": "FFEBCD",
    "blue": "0000FF",
    "blueViolet": "8A2BE2",
    "brown": "A52A2A",
    "burlyWood": "DEB887",
    "cadetBlue": "5F9EA0",
    "chartreuse": "7FFF00",
    "chocolate": "D2691E",
    "coral": "FF7F50",
    "cornflowerBlue": "6495ED",
    "cornsilk": "FFF8DC",
    "crimson": "DC143C",
    "cyan": "00FFFF",
    "darkBlue": "00008B",
    "darkCyan": "008B8B",
    "darkGoldenrod": "B8860B",
    "darkGray": "A9A9A9",
    "darkGrey": "A9A9A9",
    "darkGreen": "006400",
    "darkKhaki": "BDB76B",
    "darkMagenta": "8B008B",
    "darkOliveGreen": "556B2F",
    "darkOrange": "FF8C00",
    "darkOrchid": "9932CC",
    "darkRed": "8B0000",
    "darkSalmon": "E9967A",
    "darkSeaGreen": "8FBC8F",
    "darkSlateBlue": "483D8B",
    "darkSlateGray": "2F4F4F",
    "darkSlateGrey": "2F4F4F",
    "darkTurquoise": "00CED1",
    "darkViolet": "9400D3",
    "deepPink": "FF1493",
    "deepSkyBlue": "00BFFF",
    "dimGray": "696969",
    "dimGrey": "696969",
    "dodgerBlue": "1E90FF",
    "firebrick": "B22222",
    "floralWhite": "FFFAF0",
    "forestGreen": "228B22",
    "fuchsia": "FF00FF",
    "gainsboro": "DCDCDC",
    "ghostWhite": "F8F8FF",
    "gold": "FFD700",
    "goldenrod": "DAA520",
    "gray": "808080",
    "grey": "808080",
    "green": "008000",
    "greenYellow": "ADFF2F",
    "honeydew": "F0FFF0",
    "hotPink": "FF69B4",
    "indianRed": "CD5C5C",
    "indigo": "4B0082",
    "ivory": "FFFFF0",
    "khaki": "F0E68C",
    "lavender": "E6E6FA",
    "lavenderBlush": "FFF0F5",
    "lawnGreen": "7CFC00",
    "lemonChiffon": "FFFACD",
    "lightBlue": "ADD8E6",
    "lightCoral": "F08080",
    "lightCyan": "E0FFFF",
    "lightGoldenrodYellow": "FAFAD2",
    "lightGray": "D3D3D3",
    "lightGrey": "D3D3D3",
    "lightGreen": "90EE90",
    "lightPink": "FFB6C1",
    "lightSalmon": "FFA07A",
    "lightSeaGreen": "20B2AA",
    "lightSkyBlue": "87CEFA",
    "lightSlateGray": "778899",
    "lightSlateGrey": "778899",
    "lightSteelBlue": "B0C4DE",
    "lightYellow": "FFFFE0",
    "lime": "00FF00",
    "limeGreen": "32CD32",
    "linen": "FAF0E6",
    "magenta": "FF00FF",
    "maroon": "800000",
    "medAquamarine": "66CDAA",
    "medBlue": "0000CD",
    "medOrchid": "BA55D3",
    "medPurple": "9370DB",
    "medSeaGreen": "3CB371",
    "medSlateBlue": "7B68EE",
    "medSpringGreen": "00FA9A",
    "medTurquoise": "48D1CC",
    "medVioletRed": "C71585",
    "midnightBlue": "191970",
    "mintCream": "F5FFFA",
    "mistyRose": "FFE4E1",
    "moccasin": "FFE4B5",
    "navajoWhite": "FFDEAD",
    "navy": "000080",
    "oldLace": "FDF5E6",
    "olive": "808000",
    "oliveDrab": "6B8E23",
    "orange": "FFA500",
    "orangeRed": "FF4500",
    "orchid": "DA70D6",
    "paleGoldenrod": "EEE8AA",
    "paleGreen": "98FB98",
    "paleTurquoise": "AFEEEE",
    "paleVioletRed": "DB7093",
    "papayaWhip": "FFEFD5",
    "peachPuff": "FFDAB9",
    "peru": "CD853F",
    "pink": "FFC0CB",
    "plum": "DDA0DD",
    "powderBlue": "B0E0E6",
    "purple": "800080",
    "red": "FF0000",
    "rosyBrown": "BC8F8F",
    "royalBlue": "4169E1",
    "saddleBrown": "8B4513",
    "salmon": "FA8072",
    "sandyBrown": "F4A460",
    "seaGreen": "2E8B57",
    "seaShell": "FFF5EE",
    "sienna": "A0522D",
    "silver": "C0C0C0",
    "skyBlue": "87CEEB",
    "slateBlue": "6A5ACD",
    "slateGray": "708090",
    "slateGrey": "708090",
    "snow": "FFFAFA",
    "springGreen": "00FF7F",
    "steelBlue": "4682B4",
    "tan": "D2B48C",
    "teal": "008080",
    "thistle": "D8BFD8",
    "tomato": "FF6347",
    "turquoise": "40E0D0",
    "violet": "EE82EE",
    "wheat": "F5DEB3",
    "white": "FFFFFF",
    "whiteSmoke": "F5F5F5",
    "yellow": "FFFF00",
    "yellowGreen": "9ACD32",
}

SYSTEM_COLORS = {
    "windowText": "000000",
    "window": "FFFFFF",
    "btnFace": "F0F0F0",
    "btnText": "000000",
    "highlight": "0078D7",
    "highlightText": "FFFFFF",
    "grayText": "6D6D6D",
    "menu": "F0F0F0",
    "menuText": "000000",
    "captionText": "000000",
    "infoBk": "FFFFE1",
    "infoText": "000000",
    "3dDkShadow": "696969",
    "3dLight": "E3E3E3",
    "btnShadow": "A0A0A0",
    "btnHighlight": "FFFFFF",
    "background": "000000",
    "activeCaption": "99B4D1",
    "inactiveCaption": "BFCDDB",
    "windowFrame": "646464",
    "scrollBar": "C8C8C8",
    "appWorkspace": "ABABAB",
}

DEFAULT_CLR_MAP = {
    "bg1": "lt1",
    "tx1": "dk1",
    "bg2": "lt2",
    "tx2": "dk2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "folHlink",
}


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _pct(el, attr: str = "val") -> float:
    try:
        return int(el.get(attr, "0")) / 100000.0
    except ValueError:
        return 0.0


def apply_transforms(color: Color, el) -> Color:
    """Apply the child transform elements of colour element ``el``."""
    r, g, b, a = color.r, color.g, color.b, color.a
    for t in el:
        name = local(t.tag)
        v = _pct(t)
        if name == "alpha":
            a = v
        elif name == "alphaMod":
            a *= v
        elif name == "alphaOff":
            a += v
        elif name in (
            "lumMod",
            "lumOff",
            "lum",
            "satMod",
            "satOff",
            "sat",
            "hueMod",
            "hueOff",
            "hue",
        ):
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            if name == "lumMod":
                l *= v
            elif name == "lumOff":
                l += v
            elif name == "lum":
                l = v
            elif name == "satMod":
                s *= v
            elif name == "satOff":
                s += v
            elif name == "sat":
                s = v
            elif name == "hueMod":
                h = (h * v) % 1.0
            elif name == "hueOff":
                h = (h + int(t.get("val", "0")) / 21600000.0) % 1.0
            elif name == "hue":
                h = (int(t.get("val", "0")) / 21600000.0) % 1.0
            r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
        elif name == "tint":
            # Tint/shade are defined on linear RGB.
            r, g, b = (_srgb(_lin(c) * v + (1 - v)) for c in (r, g, b))
        elif name == "shade":
            r, g, b = (_srgb(_lin(c) * v) for c in (r, g, b))
        elif name == "comp":
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb((h + 0.5) % 1.0, l, s)
        elif name == "inv":
            r, g, b = 1 - r, 1 - g, 1 - b
        elif name == "gray":
            y = 0.3 * r + 0.59 * g + 0.11 * b
            r = g = b = y
        elif name == "red":
            r = v
        elif name == "redMod":
            r *= v
        elif name == "redOff":
            r += v
        elif name == "green":
            g = v
        elif name == "greenMod":
            g *= v
        elif name == "greenOff":
            g += v
        elif name == "blue":
            b = v
        elif name == "blueMod":
            b *= v
        elif name == "blueOff":
            b += v
        elif name == "gamma":
            r, g, b = (_srgb(c) for c in (r, g, b))
        elif name == "invGamma":
            r, g, b = (_lin(c) for c in (r, g, b))
    clamp = lambda c: max(0.0, min(1.0, c))  # noqa: E731
    return Color(clamp(r), clamp(g), clamp(b), clamp(a))


class ColorResolver:
    """Resolves colour elements for one rendering context.

    ``scheme`` maps theme colour names (dk1, lt1, accent1...) to hex; ``clr_map``
    maps logical names (tx1, bg1...) to theme names; ``placeholder`` is the
    style-matrix ``phClr`` substitute when resolving theme format styles.
    """

    def __init__(
        self,
        scheme: dict[str, str],
        clr_map: dict[str, str] | None = None,
        placeholder: Color | None = None,
    ) -> None:
        self.scheme = scheme
        self.clr_map = clr_map or DEFAULT_CLR_MAP
        self.placeholder = placeholder

    def with_placeholder(self, color: Color | None) -> ColorResolver:
        return ColorResolver(self.scheme, self.clr_map, color)

    def scheme_color(self, name: str) -> Color:
        if name == "phClr":
            return self.placeholder or BLACK
        mapped = self.clr_map.get(name, name)
        value = self.scheme.get(mapped) or self.scheme.get(name)
        if value is None:
            # Unmapped bg/tx names fall back to their usual theme slots.
            value = self.scheme.get(
                {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}.get(name, ""), "000000"
            )
        return Color.hex(value)

    def resolve(self, el) -> Color | None:
        """``el`` is a colour element (srgbClr...) or a container holding one."""
        if el is None:
            return None
        name = local(el.tag)
        if name not in COLOR_TAGS:
            for child in el:
                if local(child.tag) in COLOR_TAGS:
                    return self.resolve(child)
            return None
        if name == "srgbClr":
            base = Color.hex(el.get("val", "000000"))
        elif name == "schemeClr":
            base = self.scheme_color(el.get("val", "tx1"))
        elif name == "sysClr":
            base = Color.hex(el.get("lastClr") or SYSTEM_COLORS.get(el.get("val", ""), "000000"))
        elif name == "prstClr":
            base = Color.hex(PRESET_COLORS.get(el.get("val", "black"), "000000"))
        elif name == "scrgbClr":
            base = Color(*(_srgb(_pct(el, k)) for k in ("r", "g", "b")))
        else:  # hslClr
            h = int(el.get("hue", "0")) / 21600000.0
            base = Color(*colorsys.hls_to_rgb(h % 1.0, _pct(el, "lum"), _pct(el, "sat")))
        return apply_transforms(base, el)
