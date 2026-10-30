"""OfficeArt shape types → DrawingML presets, with their adjust values."""

from __future__ import annotations

# Type numbers are positions in MSOSPT; absent types are drawn as a rectangle.
PRESETS: dict[int, str] = {
    1: "rect", 2: "roundRect", 3: "ellipse", 4: "diamond", 5: "triangle", 6: "rtTriangle",
    7: "parallelogram", 9: "hexagon", 10: "octagon", 11: "plus", 12: "star5", 13: "rightArrow",
    14: "rightArrow", 15: "homePlate", 16: "cube", 17: "wedgeRoundRectCallout", 18: "star16",
    19: "arc", 20: "line", 21: "plaque", 22: "can", 23: "donut", 24: "rect",
    32: "straightConnector1", 33: "bentConnector2", 34: "bentConnector3", 35: "bentConnector4",
    36: "bentConnector5", 37: "curvedConnector2", 38: "curvedConnector3", 39: "curvedConnector4",
    40: "curvedConnector5", 41: "callout1", 42: "callout2", 43: "callout3",
    44: "accentCallout1", 45: "accentCallout2", 46: "accentCallout3",
    47: "borderCallout1", 48: "borderCallout2", 49: "borderCallout3",
    50: "accentBorderCallout1", 51: "accentBorderCallout2", 52: "accentBorderCallout3",
    53: "ribbon", 54: "ribbon2", 55: "chevron", 56: "pentagon", 57: "noSmoking", 58: "star8",
    59: "star16", 60: "star32", 61: "wedgeRectCallout", 62: "wedgeRoundRectCallout",
    63: "wedgeEllipseCallout", 64: "wave", 65: "foldedCorner", 66: "leftArrow", 67: "downArrow",
    68: "upArrow", 69: "leftRightArrow", 70: "upDownArrow", 71: "irregularSeal1",
    72: "irregularSeal2", 73: "lightningBolt", 74: "heart", 75: "rect", 76: "quadArrow",
    77: "leftArrowCallout", 78: "rightArrowCallout", 79: "upArrowCallout",
    80: "downArrowCallout", 81: "leftRightArrowCallout", 82: "upDownArrowCallout",
    83: "quadArrowCallout", 84: "bevel", 85: "leftBracket", 86: "rightBracket",
    87: "leftBrace", 88: "rightBrace", 89: "leftUpArrow", 90: "bentUpArrow", 91: "bentArrow",
    92: "star24", 93: "stripedRightArrow", 94: "notchedRightArrow", 95: "blockArc",
    96: "smileyFace", 97: "verticalScroll", 98: "horizontalScroll", 99: "circularArrow",
    100: "circularArrow", 101: "uturnArrow", 102: "curvedRightArrow", 103: "curvedLeftArrow",
    104: "curvedUpArrow", 105: "curvedDownArrow", 106: "cloudCallout", 107: "ellipseRibbon",
    108: "ellipseRibbon2", 109: "flowChartProcess", 110: "flowChartDecision",
    111: "flowChartInputOutput", 112: "flowChartPredefinedProcess",
    113: "flowChartInternalStorage", 114: "flowChartDocument", 115: "flowChartMultidocument",
    116: "flowChartTerminator", 117: "flowChartPreparation", 118: "flowChartManualInput",
    119: "flowChartManualOperation", 120: "flowChartConnector", 121: "flowChartPunchedCard",
    122: "flowChartPunchedTape", 123: "flowChartSummingJunction", 124: "flowChartOr",
    125: "flowChartCollate", 126: "flowChartSort", 127: "flowChartExtract",
    128: "flowChartMerge", 129: "flowChartOfflineStorage", 130: "flowChartOnlineStorage",
    131: "flowChartMagneticTape", 132: "flowChartMagneticDisk", 133: "flowChartMagneticDrum",
    134: "flowChartDisplay", 135: "flowChartDelay", 176: "flowChartAlternateProcess",
    177: "flowChartOffpageConnector", 178: "callout1", 179: "accentCallout1",
    180: "borderCallout1", 181: "accentBorderCallout1", 182: "leftRightUpArrow", 183: "sun",
    184: "moon", 185: "bracketPair", 186: "bracePair", 187: "star4", 188: "doubleWave",
    189: "actionButtonBlank", 190: "actionButtonHome", 191: "actionButtonHelp",
    192: "actionButtonInformation", 193: "actionButtonForwardNext",
    194: "actionButtonBackPrevious", 195: "actionButtonEnd", 196: "actionButtonBeginning",
    197: "actionButtonReturn", 198: "actionButtonDocument", 199: "actionButtonSound",
    200: "actionButtonMovie", 202: "rect",
}  # fmt: skip

CONNECTORS = frozenset(range(32, 41))
TRAPEZOID = 8
PICTURE_FRAME = 75
TEXT_BOX = 202
WORDART = range(136, 176)
LINES = frozenset({19, 20, 85, 86, 87, 88, 185, 186}) | CONNECTORS

_ANGLE = 65536  # fixed-point degrees
_UNIT = 21600  # adjust values live in a 21600 × 21600 box


def _pct(value: float) -> int:
    return round(value * 100000 / _UNIT)


def _along(value: float, length: float, short: float) -> int:
    """Distance along ``length``, as DrawingML's share of the shorter side."""
    return round(value / _UNIT * length / max(short, 1) * 100000)


def _star(a: int) -> dict[str, int]:
    return {"adj": round((10800 - a) / 10800 * 50000)}


def adjustments(kind: int, adj: list[int | None], width: int, height: int) -> dict[str, int]:
    """DrawingML guide values for a shape, given its legacy adjust values."""
    if not adj or adj[0] is None:
        return {}
    a = adj[0]
    b = adj[1] if len(adj) > 1 and adj[1] is not None else None
    short = min(width, height)
    match kind:
        case 2 | 21 | 22 | 16 | 84 | 23 | 10 | 5:
            return {"adj": _pct(a)}
        case 7 | 9:
            return {"adj": _along(a, width, short)}
        case 15 | 55:
            return {"adj": _along(_UNIT - a, width, short)}
        case 13 | 14:
            b = 5400 if b is None else b
            return {"adj1": _pct(_UNIT - 2 * b), "adj2": _along(_UNIT - a, width, short)}
        case 66:
            b = 5400 if b is None else b
            return {"adj1": _pct(_UNIT - 2 * b), "adj2": _along(a, width, short)}
        case 67:
            b = 5400 if b is None else b
            return {"adj1": _pct(_UNIT - 2 * b), "adj2": _along(_UNIT - a, height, short)}
        case 68:
            b = 5400 if b is None else b
            return {"adj1": _pct(_UNIT - 2 * b), "adj2": _along(a, height, short)}
        case 69:
            b = 5400 if b is None else b
            return {"adj1": _pct(_UNIT - 2 * b), "adj2": _along(a, width, short)}
        case 70:
            b = 5400 if b is None else b
            return {"adj1": _pct(_UNIT - 2 * b), "adj2": _along(a, height, short)}
        case 34 | 38:
            return {"adj1": _pct(a)}
        case 61 | 62 | 63:
            b = 10800 if b is None else b
            return {"adj1": _pct(a - 10800), "adj2": _pct(b - 10800)}
        case 65:
            return {"adj": _pct(_UNIT - a)}
        case 85 | 86:
            return {"adj": _along(a, height, short)}
        case 87 | 88:
            guides = {"adj1": _along(a, height, short)}
            if b is not None:
                guides["adj2"] = _pct(b)
            return guides
        case 58 | 59 | 60 | 92 | 187:
            return _star(a)
        case 19:
            b = 0 if b is None else b
            return {"adj1": _arc_angle(a), "adj2": _arc_angle(b)}
    return {}


def _arc_angle(value: int) -> int:
    signed = value - (1 << 32) if value & 0x80000000 else value
    return round(signed / _ANGLE * 60000) % 21600000
