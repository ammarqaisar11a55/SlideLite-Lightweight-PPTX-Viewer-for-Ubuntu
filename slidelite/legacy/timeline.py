"""Animations: binary timelines and older build records as DrawingML p:timing.

PowerPoint 2002+ stores a tree of time nodes that mirrors p:timing node for node.
Earlier versions (and decks they last touched) only record, per shape, an effect
and its place in the build order; those become an equivalent simple timeline.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from slidelite.legacy import records as rec
from slidelite.legacy.records import MAX_DEPTH, Record

_NODE_ATOM, _CONDITION, _CONDITION_ATOM = 0xF127, 0xF125, 0xF128
_PROPERTIES, _VARIANT_LIST, _VALUE_LIST, _VALUE_ATOM, _VARIANT = (
    0xF13D,
    0xF13E,
    0xF13F,
    0xF143,
    0xF142,
)
_ELEMENT, _VISUAL_SHAPE = 0xF13C, 0x2AFB
_BEHAVIOR = 0xF12A
_EFFECT_ATOM, _ROTATION_ATOM, _SCALE_ATOM = 0xF136, 0xF138, 0xF139
_BEGIN = 1  # instance of the condition list a node starts on
_TARGET_PARAGRAPHS = 2

_PROP_PRESET, _PROP_SUBTYPE, _PROP_CLASS, _PROP_GROUP, _PROP_NODE = 9, 10, 11, 19, 20
_CLASSES = {1: "entr", 2: "exit", 3: "emph", 4: "path", 5: "verb", 6: "mediacall"}
_NODES = {
    1: "clickEffect",
    2: "withEffect",
    3: "afterEffect",
    4: "mainSeq",
    5: "interactiveSeq",
    6: "clickPar",
    7: "withGroup",
    8: "afterGroup",
    9: "tmRoot",
}
_FILL = {0: "remove", 1: "freeze", 2: "hold", 3: "hold"}
_SEQUENCE, _BEHAVIOR_NODE = 1, 3


def _variant(record: Record):
    data = record.data
    if len(data) < 2:
        return None
    kind, body = data[0], data[1:]
    if kind == 0:
        return bool(body[0])
    if kind == 1 and len(body) >= 4:
        return struct.unpack("<i", body[:4])[0]
    if kind == 2 and len(body) >= 4:
        return struct.unpack("<f", body[:4])[0]
    if kind == 3:
        return body.decode("utf-16-le", "replace").split("\0", 1)[0]
    return None


def _variants(container: Record | None) -> dict[int, object]:
    if container is None:
        return {}
    return {v.instance: _variant(v) for v in container.all(_VARIANT)}


class _Timeline:
    def __init__(self, paragraphs: Callable[[int, int, int], tuple[int, int] | None]) -> None:
        self.paragraphs = paragraphs
        self.next_id = 0
        self.effects = 0

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def _conditions(self, node: Record) -> str:
        items = []
        for condition in node.all(_CONDITION):
            atom = condition.first(_CONDITION_ATOM)
            if condition.instance != _BEGIN or atom is None or atom.end - atom.start < 16:
                continue
            _object, event, _id, delay = struct.unpack_from("<IIIi", atom.stream, atom.start)
            when = "indefinite" if delay < 0 else str(delay)
            trigger = ' evt="onBegin"' if event == 1 else ""
            items.append(f'<p:cond{trigger} delay="{when}"/>')
        return f"<p:stCondLst>{''.join(items)}</p:stCondLst>" if items else ""

    def _ctn(self, node: Record, children: str = "") -> str:
        atom = node.first(_NODE_ATOM)
        fill = duration = 0
        if atom is not None and atom.end - atom.start >= 32:
            fill = struct.unpack_from("<i", atom.stream, atom.start + 12)[0]
            duration = struct.unpack_from("<i", atom.stream, atom.start + 24)[0]
        props = _variants(node.first(_PROPERTIES))
        attrs = f'id="{self._id()}"'
        preset_class = _CLASSES.get(props.get(_PROP_CLASS))
        if preset_class:
            self.effects += 1
            attrs += f' presetID="{props.get(_PROP_PRESET, 1)}" presetClass="{preset_class}"'
            attrs += f' presetSubtype="{props.get(_PROP_SUBTYPE, 0)}"'
        attrs += f' dur="{"indefinite" if duration < 0 else duration}"'
        attrs += f' fill="{_FILL.get(fill, "hold")}"'
        if isinstance(props.get(_PROP_GROUP), int):
            attrs += f' grpId="{props[_PROP_GROUP]}"'
        node_type = _NODES.get(props.get(_PROP_NODE))
        if node_type:
            attrs += f' nodeType="{node_type}"'
        inner = self._conditions(node)
        if children:
            inner += f"<p:childTnLst>{children}</p:childTnLst>"
        return f"<p:cTn {attrs}>{inner}</p:cTn>" if inner else f"<p:cTn {attrs}/>"

    def _target(self, behavior: Record | None) -> str:
        element = behavior.first(_ELEMENT) if behavior is not None else None
        shape = element.first(_VISUAL_SHAPE) if element is not None else None
        if shape is None or shape.end - shape.start < 20:
            return ""
        mode, _ref, spid, begin, end = struct.unpack_from("<IIIii", shape.stream, shape.start)
        inner = ""
        if mode == _TARGET_PARAGRAPHS and begin >= 0:
            span = self.paragraphs(spid, begin, end)
            if span is not None:
                inner = f'<p:txEl><p:pRg st="{span[0]}" end="{span[1]}"/></p:txEl>'
        return f'<p:tgtEl><p:spTgt spid="{spid}">{inner}</p:spTgt></p:tgtEl>'

    def _c_behavior(self, node: Record, container: Record) -> str:
        behavior = container.first(_BEHAVIOR)
        target = self._target(behavior)
        names = behavior.first(_VARIANT_LIST) if behavior is not None else None
        attrs = ""
        if names is not None:
            items = "".join(
                f"<p:attrName>{escape(str(_variant(v)))}</p:attrName>" for v in names.all(_VARIANT)
            )
            attrs = f"<p:attrNameLst>{items}</p:attrNameLst>"
        return f"<p:cBhvr>{self._ctn(node)}{target}{attrs}</p:cBhvr>"

    def _values(self, container: Record) -> str:
        values = container.first(_VALUE_LIST)
        if values is None:
            return ""
        items = []
        moment: int | None = None
        for child in values.children():
            if child.type == _VALUE_ATOM and child.end - child.start >= 4:
                moment = struct.unpack_from("<i", child.stream, child.start)[0]
            elif child.type == _VARIANT and child.instance == 0 and moment is not None:
                value = _variant(child)
                if isinstance(value, float):
                    body = f'<p:fltVal val="{value}"/>'
                else:
                    body = f"<p:strVal val={quoteattr(str(value))}/>"
                items.append(f'<p:tav tm="{moment * 100}"><p:val>{body}</p:val></p:tav>')
                moment = None
        return f"<p:tavLst>{''.join(items)}</p:tavLst>" if items else ""

    def _behavior(self, node: Record) -> str:
        for child in node.children():
            match child.type:
                case 0xF131:  # set
                    value = _variants(child).get(1)
                    to = f"<p:to><p:strVal val={quoteattr(str(value))}/></p:to>"
                    return f"<p:set>{self._c_behavior(node, child)}{to}</p:set>"
                case 0xF12D:  # effect
                    atom = child.first(_EFFECT_ATOM)
                    way = 0
                    if atom is not None and atom.end - atom.start >= 8:
                        way = struct.unpack_from("<I", atom.stream, atom.start + 4)[0]
                    filter_name = quoteattr(str(_variants(child).get(1) or "fade"))
                    return (
                        f'<p:animEffect transition="{"out" if way else "in"}" filter={filter_name}>'
                        f"{self._c_behavior(node, child)}</p:animEffect>"
                    )
                case 0xF12E:  # motion
                    path = quoteattr(str(_variants(child).get(1) or ""))
                    return (
                        f"<p:animMotion path={path}>{self._c_behavior(node, child)}</p:animMotion>"
                    )
                case 0xF12F:  # rotation
                    atom = child.first(_ROTATION_ATOM)
                    by = ""
                    if atom is not None and atom.end - atom.start >= 8:
                        degrees = struct.unpack_from("<f", atom.stream, atom.start + 4)[0]
                        by = f' by="{round(degrees * 60000)}"'
                    return f"<p:animRot{by}>{self._c_behavior(node, child)}</p:animRot>"
                case 0xF130:  # scale
                    atom = child.first(_SCALE_ATOM)
                    by = ""
                    if atom is not None and atom.end - atom.start >= 12:
                        x, y = struct.unpack_from("<ff", atom.stream, atom.start + 4)
                        by = f'<p:by x="{round(x * 1000)}" y="{round(y * 1000)}"/>'
                    return f"<p:animScale>{self._c_behavior(node, child)}{by}</p:animScale>"
                case 0xF12B | 0xF12C:  # property or colour animation
                    values = self._values(child)
                    return f"<p:anim>{self._c_behavior(node, child)}{values}</p:anim>"
                case 0xF132:  # commands drive embedded media, which is not played
                    return ""
        return ""

    def node(self, record: Record, depth: int = 0) -> str:
        if depth >= MAX_DEPTH:
            return ""
        atom = record.first(_NODE_ATOM)
        if atom is None or atom.end - atom.start < 12:
            return ""
        kind = struct.unpack_from("<i", atom.stream, atom.start + 8)[0]
        if kind == _BEHAVIOR_NODE:
            return self._behavior(record)
        children = "".join(self.node(c, depth + 1) for c in record.all(rec.TIME_NODE))
        ctn = self._ctn(record, children)
        if kind == _SEQUENCE:
            return (
                f'<p:seq concurrent="1" nextAc="seek">{ctn}<p:prevCondLst/><p:nextCondLst/></p:seq>'
            )
        return f"<p:par>{ctn}</p:par>"


def from_timeline(root: Record, paragraphs) -> str:
    """p:timing from a time node tree, or "" when it animates nothing."""
    timeline = _Timeline(paragraphs)
    body = timeline.node(root)
    if not timeline.effects:
        return ""
    return f"<p:timing><p:tnLst>{body}</p:tnLst></p:timing>"


# -- pre-2002 builds ---------------------------------------------------------------
@dataclass(frozen=True)
class Build:
    order: float
    spid: int
    effect: int
    direction: int
    automatic: bool
    paragraphs: tuple[int, int] | None = None


_FLY_FROM = {0: 8, 1: 1, 2: 2, 3: 4, 4: 9, 5: 3, 6: 12, 7: 6}
_SIDES = {0: "left", 1: "up", 2: "right", 3: "down"}
_SIDE_BITS = {0: 8, 1: 1, 2: 2, 3: 4}


def _preset(effect: int, direction: int) -> tuple[int, int, str | None]:
    """(presetID, presetSubtype, filter) of a legacy effect, entrance flavoured."""
    axis = "horizontal" if direction else "vertical"
    match effect:
        case 2:
            return 3, 10 if direction else 5, f"blinds({axis})"
        case 3:
            return 5, 10 if direction else 5, "checkerboard(across)"
        case 5:
            return 9, 0, "dissolve"
        case 6:
            return 10, 0, "fade"
        case 8:
            return 14, 10 if direction else 5, f"randombar({axis})"
        case 10:
            return 22, _SIDE_BITS.get(direction, 8), f"wipe({_SIDES.get(direction, 'left')})"
        case 11:
            return 4, 16 if direction else 32, f"box({'in' if direction else 'out'})"
        case 12:
            return 2, _FLY_FROM.get(direction, 8), None
        case 13:
            return 16, 42, "barn(inVertical)"
        case 17:
            return 8, 32, "diamond(out)"
        case 18:
            return 13, 32, "plus(out)"
        case 19:
            return 20, 0, "wedge"
        case 27:
            return 6, 32, "circle(out)"
    return 1, 0, None


def _build_effect(build: Build, ids: list[int], node: str) -> str:
    def next_id() -> int:
        ids[0] += 1
        return ids[0]

    preset, subtype, filter_name = _preset(build.effect, build.direction)
    inner = ""
    if build.paragraphs is not None:
        st, end = build.paragraphs
        inner = f'<p:txEl><p:pRg st="{st}" end="{end}"/></p:txEl>'
    target = f'<p:tgtEl><p:spTgt spid="{build.spid}">{inner}</p:spTgt></p:tgtEl>'
    behaviors = (
        f'<p:set><p:cBhvr><p:cTn id="{next_id()}" dur="1" fill="hold"><p:stCondLst><p:cond '
        f'delay="0"/></p:stCondLst></p:cTn>{target}<p:attrNameLst><p:attrName>style.visibility'
        '</p:attrName></p:attrNameLst></p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>'
    )
    if filter_name:
        behaviors += (
            f'<p:animEffect transition="in" filter="{filter_name}"><p:cBhvr><p:cTn '
            f'id="{next_id()}" dur="500"/>{target}</p:cBhvr></p:animEffect>'
        )
    return (
        f'<p:par><p:cTn id="{next_id()}" presetID="{preset}" presetClass="entr" '
        f'presetSubtype="{subtype}" fill="hold" nodeType="{node}"><p:stCondLst><p:cond '
        f'delay="0"/></p:stCondLst><p:childTnLst>{behaviors}</p:childTnLst></p:cTn></p:par>'
    )


def from_builds(builds: list[Build]) -> str:
    """p:timing for per-shape builds: each is a click, unless marked automatic."""
    if not builds:
        return ""
    ids = [2]

    def next_id() -> int:
        ids[0] += 1
        return ids[0]

    steps: list[list[Build]] = []
    for build in sorted(builds, key=lambda b: b.order):
        if build.automatic and steps:
            steps[-1].append(build)
        else:
            steps.append([build])
    clicks = ""
    for step in steps:
        offset = 0
        groups = ""
        for i, build in enumerate(step):
            node = "afterEffect" if i else "clickEffect"
            groups += (
                f'<p:par><p:cTn id="{next_id()}" fill="hold"><p:stCondLst><p:cond '
                f'delay="{offset}"/></p:stCondLst><p:childTnLst>'
                f"{_build_effect(build, ids, node)}</p:childTnLst></p:cTn></p:par>"
            )
            offset += 500
        begin = "0" if step[0].automatic else "indefinite"
        clicks += (
            f'<p:par><p:cTn id="{next_id()}" fill="hold"><p:stCondLst><p:cond delay="{begin}"/>'
            f"</p:stCondLst><p:childTnLst>{groups}</p:childTnLst></p:cTn></p:par>"
        )
    return (
        '<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" '
        'nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" nextAc="seek"><p:cTn id="2" '
        f'dur="indefinite" nodeType="mainSeq"><p:childTnLst>{clicks}</p:childTnLst></p:cTn>'
        "<p:prevCondLst/><p:nextCondLst/></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst>"
        "</p:timing>"
    )
