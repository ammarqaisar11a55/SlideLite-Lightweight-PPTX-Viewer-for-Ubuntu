"""Slide transitions (and, later, animation timelines) as JSON for the UI."""

from __future__ import annotations

from slidelite.presentation.xmlsafe import NS, local, q

_SPEED_MS = {"slow": 1000, "med": 750, "fast": 500}
_P14 = NS["p14"]

# Office 2010+ effects mapped onto the closest effect the slide show implements.
_ALIASES = {
    "vortex": "fade",
    "ripple": "fade",
    "glitter": "dissolve",
    "honeycomb": "dissolve",
    "flash": "flash",
    "shred": "strips",
    "switch": "push",
    "flip": "push",
    "gallery": "push",
    "cube": "push",
    "doors": "split",
    "box": "push",
    "window": "split",
    "ferris": "push",
    "conveyor": "push",
    "pan": "push",
    "prism": "push",
    "reveal": "fade",
    "warp": "zoom",
    "flythrough": "zoom",
    "morph": "fade",
    "prstTrans": "fade",
    "newsflash": "newsflash",
    "checker": "checker",
    "comb": "comb",
    "blinds": "blinds",
    "strips": "strips",
    "randomBar": "randomBar",
    "dissolve": "dissolve",
    "wedge": "wedge",
    "wheel": "wheel",
    "wheelReverse": "wheel",
    "circle": "circle",
    "diamond": "diamond",
    "plus": "plus",
    "zoom": "zoom",
    "cut": "cut",
    "fade": "fade",
    "push": "push",
    "wipe": "wipe",
    "split": "split",
    "cover": "cover",
    "pull": "uncover",
    "random": "fade",
}


def _int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_transition(slide_root) -> dict | None:
    """``{"type", "dur", "dir", "orient", "thruBlk", "advClick", "advTm"}``."""
    trans = slide_root.find(q("p:transition"))
    if trans is None:
        return None
    dur = _int(trans.get(f"{{{_P14}}}dur"))
    if dur is None:
        dur = _SPEED_MS.get(trans.get("spd", "fast"), 500)
    out: dict = {
        "dur": max(0, min(dur, 60000)),
        "advClick": trans.get("advClick", "1") not in ("0", "false"),
    }
    adv = _int(trans.get("advTm"))
    if adv is not None:
        out["advTm"] = max(0, adv)
    effect = next((c for c in trans if local(c.tag) not in ("sndAc", "extLst")), None)
    if effect is None:
        out["type"] = "none"
        return out
    name = local(effect.tag)
    if name == "prstTrans":
        name = effect.get("prst", "fade")
    out["type"] = _ALIASES.get(name, "fade")
    for key in ("dir", "orient", "thruBlk", "pattern", "spokes"):
        if effect.get(key) is not None:
            out[key] = effect.get(key)
    if name == "doors":
        out.update({"orient": effect.get("dir", "vert"), "dir": "out"})
    if name in ("flash",):
        out["type"] = "fade"
        out["thruWht"] = True
    return out


def slide_timing(slide) -> dict:
    timing: dict = {}
    transition = parse_transition(slide.root)
    if transition:
        timing["transition"] = transition
    return timing
