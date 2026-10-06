"""Slide transitions and animation timelines as JSON for the slide show.

The PowerPoint timing tree (``p:timing``) is a SMIL-like structure::

    tmRoot / mainSeq / click group (par) / offset group (par) / effect (par)

SlideLite flattens the main sequence into *build steps*: each step is one
click group (or one that starts automatically) holding effects with a start
offset and duration.  Effects are identified by preset class/ID/subtype and
carry the behaviour parameters the slide show needs (motion paths, scale,
rotation).  Interactive (trigger) sequences are ignored.
"""

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


MAX_EFFECTS = 2000


def _delay(ctn) -> tuple[int | None, bool]:
    """(delay in ms or None for "indefinite", starts on slide begin)."""
    cond_lst = ctn.find(q("p:stCondLst")) if ctn is not None else None
    delay: int | None = 0
    on_begin = False
    for cond in cond_lst if cond_lst is not None else []:
        d = cond.get("delay")
        if d == "indefinite":
            delay = None
        else:
            value = _int(d, 0) or 0
            if cond.get("evt") in ("onBegin", "begin"):
                on_begin = True
                delay = value
            elif delay is not None:
                delay = value
    return delay, on_begin


def _ms(value, default: int = 0) -> int:
    if value in (None, "indefinite"):
        return default
    return max(0, min(_int(value, default) or 0, 600000))


def _targets(ctn) -> list[tuple[str, list[int] | None]]:
    out = []
    for tgt in ctn.iter(q("p:spTgt")):
        spid = tgt.get("spid")
        if not spid:
            continue
        paras = None
        rng = tgt.find(f".//{q('p:pRg')}")
        if rng is not None:
            st = _int(rng.get("st"), 0) or 0
            end = _int(rng.get("end"), st) or st
            paras = list(range(st, min(end, st + 500) + 1))
        out.append((spid, paras))
    return out


def _behaviours(ctn) -> dict:
    """Pull the parameters the UI needs out of an effect's behaviours."""
    info: dict = {}
    duration = 0
    children = ctn.find(q("p:childTnLst"))
    for b in children if children is not None else []:
        name = local(b.tag)
        bctn = b.find(f"{q('p:cBhvr')}/{q('p:cTn')}")
        if bctn is not None:
            d = _ms(bctn.get("dur"), 0)
            start, _ = _delay(bctn)
            repeat = bctn.get("repeatCount")
            reps = (_int(repeat, 1000) or 1000) / 1000 if repeat not in (None, "indefinite") else 1
            total = (start or 0) + d * max(1.0, reps) * (
                2 if bctn.get("autoRev") in ("1", "true") else 1
            )
            duration = max(duration, int(total))
            if d > 1 and "dur" not in info:
                info["dur"] = d
            if bctn.get("autoRev") in ("1", "true"):
                info["autoRev"] = True
            if repeat not in (None, "1000"):
                info["repeat"] = reps if repeat != "indefinite" else 1
            for key in ("accel", "decel"):
                if bctn.get(key):
                    info[key] = (_int(bctn.get(key), 0) or 0) / 100000
        if name == "animMotion":
            if b.get("path"):
                info["path"] = b.get("path")[:4000]
        elif name == "animScale":
            by = b.find(q("p:by"))
            if by is not None:
                info["scale"] = [
                    (_int(by.get("x"), 100000) or 0) / 100000,
                    (_int(by.get("y"), 100000) or 0) / 100000,
                ]
        elif name == "animRot":
            if b.get("by"):
                info["rot"] = (_int(b.get("by"), 0) or 0) / 60000
        elif name == "animEffect":
            if b.get("filter"):
                info["filter"] = b.get("filter")[:60]
            info["transition"] = b.get("transition", "in")
        elif name == "anim":
            attr = b.find(f".//{q('p:attrName')}")
            if attr is not None and attr.text == "style.opacity":
                val = b.find(f".//{q('p:fltVal')}")
                if val is not None:
                    try:
                        info["opacity"] = float(val.get("val"))
                    except (TypeError, ValueError):
                        pass
    info["total"] = duration
    return info


def parse_animations(slide_root) -> dict | None:
    timing = slide_root.find(q("p:timing"))
    if timing is None:
        return None
    main = None
    for ctn in timing.iter(q("p:cTn")):
        if ctn.get("nodeType") == "mainSeq":
            main = ctn
            break
    if main is None:
        return None
    steps: list[dict] = []
    first_class: dict[tuple, str] = {}
    count = 0
    groups = main.find(q("p:childTnLst"))
    for click_par in groups if groups is not None else []:
        click_ctn = click_par.find(q("p:cTn"))
        if click_ctn is None:
            continue
        delay, on_begin = _delay(click_ctn)
        auto = on_begin or (delay is not None and not steps)
        effects: list[dict] = []
        inner = click_ctn.find(q("p:childTnLst"))
        for offset_par in inner if inner is not None else []:
            offset_ctn = offset_par.find(q("p:cTn"))
            if offset_ctn is None:
                continue
            offset, _ = _delay(offset_ctn)
            leaf = offset_ctn.find(q("p:childTnLst"))
            for effect_par in leaf if leaf is not None else []:
                ectn = effect_par.find(q("p:cTn"))
                if ectn is None or count >= MAX_EFFECTS:
                    continue
                cls = ectn.get("presetClass")
                if cls in (None, "mediacall", "verb"):
                    continue
                edelay, _ = _delay(ectn)
                behaviours = _behaviours(ectn)
                for spid, paras in _targets(ectn)[:1]:
                    count += 1
                    effect = {
                        "spid": spid,
                        "cls": cls,
                        "preset": _int(ectn.get("presetID"), 0) or 0,
                        "sub": _int(ectn.get("presetSubtype"), 0) or 0,
                        "start": (offset or 0) + (edelay or 0),
                        "dur": behaviours.get("dur", _ms(ectn.get("dur"), 500) or 500),
                        "total": max(behaviours.pop("total", 0), 1),
                    }
                    if paras is not None:
                        effect["para"] = paras
                    effect.update({k: v for k, v in behaviours.items() if k != "dur"})
                    effects.append(effect)
                    key = (spid, tuple(paras) if paras else None)
                    first_class.setdefault(key, cls)
        if effects:
            steps.append({"auto": auto, "effects": effects})
    if not steps:
        return None
    hidden = [
        {"spid": k[0], **({"para": list(k[1])} if k[1] else {})}
        for k, cls in first_class.items()
        if cls == "entr"
    ]
    return {"steps": steps, "hidden": hidden}


def slide_timing(slide) -> dict:
    timing: dict = {}
    transition = parse_transition(slide.root)
    if transition:
        timing["transition"] = transition
    animations = parse_animations(slide.root)
    if animations:
        timing["build"] = animations
    return timing
