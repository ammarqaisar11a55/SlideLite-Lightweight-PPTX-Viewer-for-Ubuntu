"""Font substitution and symbol-font translation.

Office fonts are usually missing on Linux.  Each requested family is kept
first in the CSS stack (it is used if installed) and followed by
metric-compatible or visually close free fonts, so line breaks stay close
to PowerPoint's.
"""

from __future__ import annotations

import re

SUBSTITUTES: dict[str, list[str]] = {
    "calibri": ["Carlito"],
    "calibri light": ["Carlito"],
    "cambria": ["Caladea"],
    "cambria math": ["Caladea", "STIX Two Math", "DejaVu Serif"],
    "arial": ["Liberation Sans", "Arimo"],
    "arial mt": ["Arial", "Liberation Sans", "Arimo"],
    "helvetica": ["Liberation Sans", "Arimo"],
    "helvetica neue": ["Liberation Sans", "Arimo"],
    "arial narrow": ["Liberation Sans Narrow"],
    "arial black": ["Archivo Black", "Liberation Sans"],
    "times new roman": ["Liberation Serif", "Tinos"],
    "times": ["Liberation Serif", "Tinos"],
    "courier new": ["Liberation Mono", "Cousine"],
    "courier": ["Liberation Mono", "Cousine"],
    "consolas": ["Inconsolata", "DejaVu Sans Mono", "Liberation Mono"],
    "lucida console": ["DejaVu Sans Mono", "Liberation Mono"],
    "verdana": ["DejaVu Sans"],
    "tahoma": ["DejaVu Sans"],
    "segoe ui": ["Selawik", "Ubuntu Sans", "Noto Sans", "Open Sans"],
    "segoe ui light": ["Selawik", "Ubuntu Sans", "Noto Sans"],
    "segoe ui semibold": ["Selawik", "Ubuntu Sans", "Noto Sans"],
    "trebuchet ms": ["Ubuntu Sans", "Fira Sans", "DejaVu Sans"],
    "century gothic": ["URW Gothic", "TeX Gyre Adventor", "Questrial"],
    "gill sans mt": ["Gillius ADF", "Cabin", "Liberation Sans"],
    "franklin gothic medium": ["Libre Franklin", "Liberation Sans"],
    "franklin gothic book": ["Libre Franklin", "Liberation Sans"],
    "georgia": ["Gelasio", "DejaVu Serif"],
    "garamond": ["EB Garamond", "URW Garamond", "Liberation Serif"],
    "book antiqua": ["TeX Gyre Pagella", "P052", "Palladio"],
    "palatino linotype": ["TeX Gyre Pagella", "P052", "Palladio"],
    "palatino": ["TeX Gyre Pagella", "P052"],
    "bookman old style": ["TeX Gyre Bonum", "URW Bookman"],
    "century schoolbook": ["TeX Gyre Schola", "C059", "Century Schoolbook L"],
    "century": ["TeX Gyre Schola", "C059"],
    "comic sans ms": ["Comic Neue", "Comic Relief"],
    "impact": ["Anton", "Oswald", "Liberation Sans Narrow"],
    "lucida sans unicode": ["DejaVu Sans"],
    "lucida sans": ["DejaVu Sans"],
    "constantia": ["Caladea", "Liberation Serif"],
    "corbel": ["Carlito", "Liberation Sans"],
    "candara": ["Carlito", "Liberation Sans"],
    "aptos": ["Carlito", "Liberation Sans"],
    "aptos display": ["Carlito", "Liberation Sans"],
    "ms pgothic": ["Noto Sans CJK JP"],
    "ms gothic": ["Noto Sans CJK JP"],
    "ｍｓ ｐゴシック": ["Noto Sans CJK JP"],
    "meiryo": ["Noto Sans CJK JP"],
    "yu gothic": ["Noto Sans CJK JP"],
    "游ゴシック": ["Noto Sans CJK JP"],
    "ms mincho": ["Noto Serif CJK JP"],
    "simsun": ["Noto Serif CJK SC"],
    "宋体": ["Noto Serif CJK SC"],
    "microsoft yahei": ["Noto Sans CJK SC"],
    "微软雅黑": ["Noto Sans CJK SC"],
    "dengxian": ["Noto Sans CJK SC"],
    "等线": ["Noto Sans CJK SC"],
    "pmingliu": ["Noto Serif CJK TC"],
    "malgun gothic": ["Noto Sans CJK KR"],
    "맑은 고딕": ["Noto Sans CJK KR"],
    "gulim": ["Noto Sans CJK KR"],
    "굴림": ["Noto Sans CJK KR"],
    "batang": ["Noto Serif CJK KR"],
    "mangal": ["Noto Sans Devanagari"],
    "nirmala ui": ["Noto Sans Devanagari", "Noto Sans"],
    "arabic typesetting": ["Noto Naskh Arabic"],
    "traditional arabic": ["Noto Naskh Arabic"],
    "simplified arabic": ["Noto Naskh Arabic"],
    "jameel noori nastaleeq": ["Noto Nastaliq Urdu"],
}

_SERIF_HINTS = re.compile(
    r"times|serif|georgia|cambria|garamond|book|century|palatino|constantia|baskerville|"
    r"bodoni|caslon|didot|minion|mincho|batang|songti|simsun|宋|schoolbook|antiqua|roman|rockwell|slab",
    re.IGNORECASE,
)
_NARROW_HINTS = re.compile(r"condensed|narrow|compressed|\bcond\b|tw cen", re.IGNORECASE)
_SLAB_HINTS = re.compile(r"rockwell|slab|memphis|clarendon|courier", re.IGNORECASE)
_MONO_HINTS = re.compile(r"mono|courier|consolas|console|code", re.IGNORECASE)

_UNSAFE = re.compile(r"[\"'<>&;{}()\\\x00-\x1f]")

SYMBOL_FONTS = {"wingdings", "wingdings 2", "wingdings 3", "symbol", "webdings", "marlett"}


def family_stack(*families: str | None) -> str:
    """CSS font-family value for the requested families, with substitutes."""
    seen: list[str] = []

    def add(name: str) -> None:
        # Family names come from untrusted files and end up inside a style
        # attribute: drop anything that could escape the CSS string.
        name = _UNSAFE.sub("", name).strip()
        if name and name.lower() not in (s.lower() for s in seen):
            seen.append(name)

    primary = next((f for f in families if f), None)
    for family in families:
        if not family:
            continue
        add(family)
        for sub in SUBSTITUTES.get(family.lower(), []):
            add(sub)
        if _NARROW_HINTS.search(family):
            for sub in ("Liberation Sans Narrow", "DejaVu Sans Condensed", "Ubuntu Condensed"):
                add(sub)
        if _SLAB_HINTS.search(family):
            for sub in ("Roboto Slab", "Zilla Slab", "DejaVu Serif"):
                add(sub)
    if primary and _MONO_HINTS.search(primary):
        generic = "monospace"
        tail = ["Liberation Mono", "DejaVu Sans Mono"]
    elif primary and _SERIF_HINTS.search(primary):
        generic = "serif"
        tail = ["Liberation Serif", "DejaVu Serif"]
    else:
        generic = "sans-serif"
        tail = ["Carlito", "Liberation Sans", "DejaVu Sans"]
    for name in tail:
        add(name)
    quoted = ", ".join(f"'{name}'" for name in seen)
    return f"{quoted}, {generic}" if quoted else generic


# -- symbol fonts -------------------------------------------------------------------

WINGDINGS = {
    0x21: "✏",
    0x22: "✂",
    0x23: "✁",
    0x24: "👓",
    0x25: "🔔",
    0x26: "📖",
    0x28: "☎",
    0x29: "✆",
    0x2A: "✉",
    0x30: "📁",
    0x31: "📂",
    0x32: "📄",
    0x36: "⌛",
    0x37: "⌨",
    0x38: "🖱",
    0x3A: "🖥",
    0x3E: "✇",
    0x3F: "✍",
    0x41: "✌",
    0x42: "👌",
    0x43: "👍",
    0x44: "👎",
    0x45: "☜",
    0x46: "☞",
    0x47: "☝",
    0x48: "☟",
    0x49: "✋",
    0x4A: "☺",
    0x4B: "😐",
    0x4C: "☹",
    0x4D: "💣",
    0x4E: "☠",
    0x4F: "⚐",
    0x50: "⚑",
    0x51: "✈",
    0x52: "☼",
    0x53: "💧",
    0x54: "❄",
    0x55: "✝",
    0x56: "✞",
    0x58: "✠",
    0x59: "✡",
    0x5A: "☪",
    0x5B: "☯",
    0x5C: "ॐ",
    0x5D: "☸",
    0x5E: "♈",
    0x5F: "♉",
    0x60: "♊",
    0x61: "♋",
    0x62: "♌",
    0x63: "♍",
    0x64: "♎",
    0x65: "♏",
    0x66: "♐",
    0x67: "♑",
    0x68: "♒",
    0x69: "♓",
    0x6A: "&",
    0x6B: "&",
    0x6C: "●",
    0x6D: "❍",
    0x6E: "■",
    0x6F: "□",
    0x70: "◻",
    0x71: "❑",
    0x72: "❒",
    0x73: "⬧",
    0x74: "⧫",
    0x75: "◆",
    0x76: "❖",
    0x77: "⬥",
    0x78: "⌧",
    0x79: "⍓",
    0x7A: "⌘",
    0x7B: "❀",
    0x7C: "✿",
    0x7D: "❝",
    0x7E: "❞",
    0x80: "⓪",
    0x81: "①",
    0x82: "②",
    0x83: "③",
    0x84: "④",
    0x85: "⑤",
    0x86: "⑥",
    0x87: "⑦",
    0x88: "⑧",
    0x89: "⑨",
    0x8A: "⑩",
    0x8B: "⓿",
    0x8C: "❶",
    0x8D: "❷",
    0x8E: "❸",
    0x8F: "❹",
    0x90: "❺",
    0x91: "❻",
    0x92: "❼",
    0x93: "❽",
    0x94: "❾",
    0x95: "❿",
    0x9E: "·",
    0x9F: "•",
    0xA0: "▪",
    0xA1: "○",
    0xA2: "⭕",
    0xA3: "⭘",
    0xA4: "◉",
    0xA5: "◎",
    0xA6: "◌",
    0xA7: "▪",
    0xA8: "◻",
    0xA9: "◇",
    0xAA: "✦",
    0xAB: "★",
    0xAC: "✶",
    0xAD: "✴",
    0xAE: "✹",
    0xAF: "✵",
    0xB0: "⯐",
    0xB1: "⌖",
    0xB2: "⟡",
    0xB3: "⌑",
    0xB4: "⍰",
    0xB5: "✪",
    0xB6: "✰",
    0xD5: "⌫",
    0xD6: "⌦",
    0xD8: "➢",
    0xDF: "←",
    0xE0: "→",
    0xE1: "↑",
    0xE2: "↓",
    0xE3: "↖",
    0xE4: "↗",
    0xE5: "↙",
    0xE6: "↘",
    0xE7: "⬅",
    0xE8: "➔",
    0xE9: "⬆",
    0xEA: "⬇",
    0xEF: "⇦",
    0xF0: "⇨",
    0xF1: "⇧",
    0xF2: "⇩",
    0xF3: "⬄",
    0xF4: "⇳",
    0xF5: "⬀",
    0xF6: "⬁",
    0xF7: "⬃",
    0xF8: "⬂",
    0xFB: "✗",
    0xFC: "✔",
    0xFD: "☒",
    0xFE: "☑",
}
WINGDINGS_2 = {
    0x4F: "✗",
    0x50: "✔",
    0x51: "☒",
    0x52: "☑",
    0x97: "◆",
    0x98: "●",
    0x9E: "■",
    0xA2: "▪",
}
WINGDINGS_3 = {
    0x5B: "◀",
    0x5C: "▶",
    0x5D: "▲",
    0x5E: "▼",
    0x70: "▲",
    0x71: "▼",
    0x74: "◄",
    0x75: "►",
    0x7D: "▶",
    0x7E: "◀",
    0x80: "▲",
    0x84: "►",
    0x85: "◄",
    0x86: "▲",
    0x87: "▼",
    0xD8: "➢",
    0xE0: "←",
    0xE1: "→",
}
_SYMBOL_GREEK = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖαβχδεφγηιϕκλμνοπθρστυϖωξψζ",
        strict=True,
    )
)
SYMBOL = {ord(k): v for k, v in _SYMBOL_GREEK.items()}
SYMBOL.update(
    {
        0x22: "∀",
        0x24: "∃",
        0x27: "∋",
        0x2D: "−",
        0x40: "≅",
        0x5C: "∴",
        0x5E: "⊥",
        0xA0: "€",
        0xA1: "ϒ",
        0xA2: "′",
        0xA3: "≤",
        0xA4: "⁄",
        0xA5: "∞",
        0xA6: "ƒ",
        0xA7: "♣",
        0xA8: "♦",
        0xA9: "♥",
        0xAA: "♠",
        0xAB: "↔",
        0xAC: "←",
        0xAD: "↑",
        0xAE: "→",
        0xAF: "↓",
        0xB0: "°",
        0xB1: "±",
        0xB2: "″",
        0xB3: "≥",
        0xB4: "×",
        0xB5: "∝",
        0xB6: "∂",
        0xB7: "•",
        0xB8: "÷",
        0xB9: "≠",
        0xBA: "≡",
        0xBB: "≈",
        0xBC: "…",
        0xC0: "ℵ",
        0xC1: "ℑ",
        0xC2: "ℜ",
        0xC3: "℘",
        0xC4: "⊗",
        0xC5: "⊕",
        0xC6: "∅",
        0xC7: "∩",
        0xC8: "∪",
        0xC9: "⊃",
        0xCA: "⊇",
        0xCB: "⊄",
        0xCC: "⊂",
        0xCD: "⊆",
        0xCE: "∈",
        0xCF: "∉",
        0xD0: "∠",
        0xD1: "∇",
        0xD2: "®",
        0xD3: "©",
        0xD4: "™",
        0xD5: "∏",
        0xD6: "√",
        0xD7: "⋅",
        0xD8: "¬",
        0xD9: "∧",
        0xDA: "∨",
        0xDB: "⇔",
        0xDC: "⇐",
        0xDD: "⇑",
        0xDE: "⇒",
        0xDF: "⇓",
        0xE0: "◊",
        0xE1: "〈",
        0xE5: "∑",
        0xF1: "〉",
        0xF2: "∫",
    }
)
_SYMBOL_TABLES = {
    "wingdings": WINGDINGS,
    "wingdings 2": WINGDINGS_2,
    "wingdings 3": WINGDINGS_3,
    "symbol": SYMBOL,
}


def is_symbol_font(family: str | None) -> bool:
    return bool(family) and family.lower() in SYMBOL_FONTS


def translate_symbol(text: str, family: str | None, bullet: bool = False) -> tuple[str, bool]:
    """Map symbol-font characters to Unicode.

    Returns (text, translated).  For bullets, unknown symbol characters become
    a plain bullet instead of a random Latin letter.
    """
    table = _SYMBOL_TABLES.get((family or "").lower())
    if table is None:
        return text, False
    out = []
    for ch in text:
        code = ord(ch)
        if 0xF000 <= code <= 0xF0FF:  # private-use encoding of symbol fonts
            code -= 0xF000
        mapped = table.get(code)
        if mapped is None:
            mapped = "•" if bullet else (chr(code) if code < 0x80 and code >= 0x20 else ch)
        out.append(mapped)
    return "".join(out), True
