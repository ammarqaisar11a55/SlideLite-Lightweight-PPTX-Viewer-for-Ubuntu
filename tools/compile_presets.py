"""Compile ECMA-376 presetShapeDefinitions.xml into slidelite/data/presets.json.

    python3 tools/compile_presets.py presetShapeDefinitions.xml

The definitions (Ecma International, ECMA-376 Part 1, Annex D) describe every
PowerPoint auto-shape with the DrawingML shape-guide language; SlideLite
evaluates them in slidelite/presentation/geometry.py.
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def local(tag: str) -> str:
    return tag.rpartition("}")[2]


def guides(el):
    return [[gd.get("name"), gd.get("fmla")] for gd in (el if el is not None else [])]


def point(pt):
    return [pt.get("x"), pt.get("y")]


def compile_path(path):
    cmds = []
    for cmd in path:
        name = local(cmd.tag)
        if name in ("moveTo", "lnTo"):
            cmds.append([name[0].upper() if name == "moveTo" else "L", *point(cmd[0])])
        elif name == "arcTo":
            cmds.append(["A", cmd.get("wR"), cmd.get("hR"), cmd.get("stAng"), cmd.get("swAng")])
        elif name == "quadBezTo":
            cmds.append(["Q", *point(cmd[0]), *point(cmd[1])])
        elif name == "cubicBezTo":
            cmds.append(["C", *point(cmd[0]), *point(cmd[1]), *point(cmd[2])])
        elif name == "close":
            cmds.append(["Z"])
    out = {"c": cmds}
    for key in ("w", "h"):
        if path.get(key):
            out[key] = int(path.get(key))
    if path.get("fill") and path.get("fill") != "norm":
        out["fill"] = path.get("fill")
    if path.get("stroke") in ("0", "false"):
        out["stroke"] = False
    return out


def path_list(shape):
    paths = shape.find(f"{A}pathLst")
    return list(paths) if paths is not None else []


def main() -> None:
    src = Path(sys.argv[1])
    root = ET.parse(src).getroot()  # trusted build-time input
    presets = {}
    for shape in root:
        name = local(shape.tag)
        entry = {
            "av": guides(shape.find(f"{A}avLst")),
            "gd": guides(shape.find(f"{A}gdLst")),
            "paths": [compile_path(p) for p in path_list(shape)],
        }
        rect = shape.find(f"{A}rect")
        if rect is not None:
            entry["rect"] = [rect.get("l"), rect.get("t"), rect.get("r"), rect.get("b")]
        presets[name] = entry
    out = Path(__file__).resolve().parent.parent / "slidelite" / "data" / "presets.json"
    out.write_text(json.dumps(presets, separators=(",", ":"), sort_keys=True))
    print(f"{len(presets)} presets -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
