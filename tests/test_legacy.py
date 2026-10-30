"""PowerPoint 97–2003 files: the compound file, the records and the conversion.

Decks are built byte by byte with tools/ppt_builder.py, in the structures
PowerPoint itself writes, and then opened through the normal loading path.
"""

import io
import random
import re
import struct
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import ppt_builder as B  # noqa: E402

from slidelite.legacy import cfb, persist, pictures, text  # noqa: E402
from slidelite.legacy import deck as legacy_deck  # noqa: E402
from slidelite.legacy.convert import convert, is_ppt  # noqa: E402
from slidelite.legacy.officeart import Props  # noqa: E402
from slidelite.legacy.records import iter_records, read_header  # noqa: E402
from slidelite.presentation import document, timing  # noqa: E402
from slidelite.presentation.fonts import translate_symbol  # noqa: E402
from slidelite.presentation.opc import (  # noqa: E402
    EncryptedPackageError,
    LegacyFormatError,
    PackageError,
)
from slidelite.presentation.parts import Deck  # noqa: E402
from slidelite.render.slide import render_slide  # noqa: E402

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082"
)


def base_deck(**kwargs) -> B.Deck:
    deck = B.Deck(**kwargs)
    deck.add_master(
        B.color_scheme(B.DEFAULT_SCHEME),
        B.master_style(0, [(B.pf(align=1), B.cf(font=0, size=44, color=B.scheme_color(3)))]),
        B.master_style(
            1,
            [
                (B.pf(bullet=True, bullet_char=0x2022, left=216, indent=0), B.cf(font=0, size=32)),
                (B.pf(left=504, indent=288), B.cf(size=28)),
            ],
        ),
        shapes=[
            B.shape(10, 1, (0, 0, 5760, 288), {0x181: 0x0000FF, 0x1BF: 0x00100010}),
            B.shape(11, 1, (288, 432, 5472, 1152), {0x87: 1}, placeholder=1),
            B.shape(12, 1, (288, 1296, 5472, 3600), placeholder=2),
        ],
        background=B.shape(9, 1, None, {0x180: 0, 0x181: 0x08000000}, flags=0xC00),
    )
    return deck


def slide_xml(deck: B.Deck, number: int = 1, **build) -> ET.Element:
    pptx, issues = convert(deck.build(**build))
    assert issues == []
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        return ET.fromstring(archive.read(f"ppt/slides/slide{number}.xml"))


def load(deck: B.Deck) -> document.Presentation:
    return document.load(io.BytesIO(deck.build()))


def only_shape(root: ET.Element) -> ET.Element:
    tree = root.find(f"{P}cSld/{P}spTree")
    shapes = [c for c in tree if c.tag.rsplit("}", 1)[1] in ("sp", "pic", "grpSp", "cxnSp")]
    assert len(shapes) == 1
    return shapes[0]


# -- compound file ------------------------------------------------------------------


def test_compound_file_reads_mini_and_regular_streams():
    big = bytes(range(256)) * 40
    data = B.compound_file({"Small": b"tiny", "Big": big, "Empty": b""})
    compound = cfb.CompoundFile(data)
    assert compound.read("Small") == b"tiny"
    assert compound.read("BIG") == big  # names are case-insensitive
    assert compound.read("Empty") == b""
    assert compound.has("small") and not compound.has("missing")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d[:100],  # truncated header
        lambda d: b"\0" * len(d),  # no signature
        lambda d: d[:30] + b"\xff\xff" + d[32:],  # impossible sector size
    ],
)
def test_compound_file_rejects_bad_headers(mutate):
    with pytest.raises(cfb.CompoundFileError):
        cfb.CompoundFile(mutate(B.compound_file({"A": b"x"})))


def test_compound_file_detects_sector_loops():
    data = bytearray(B.compound_file({"Stream": b"y" * 5000}))
    fat_at = 512
    # Point every FAT entry at sector 2: any chain through it can never end.
    for i in range(2, 40):
        struct.pack_into("<I", data, fat_at + i * 4, 2)
    with pytest.raises(cfb.CompoundFileError):
        cfb.CompoundFile(bytes(data)).read("Stream")


def test_directory_cycles_terminate():
    data = bytearray(B.compound_file({"A": b"1", "B": b"2"}))
    compound = cfb.CompoundFile(bytes(data))
    directory = (struct.unpack_from("<I", data, 48)[0] + 1) * 512
    struct.pack_into("<I", data, directory + 128 * 2 + 72, 1)  # B's sibling points back at A
    assert cfb.CompoundFile(bytes(data)).streams.keys() == compound.streams.keys()


# -- records and the persist directory ----------------------------------------------


def test_records_tree_and_truncation():
    stream = B.box(0x03E8, B.rec(0x03E9, b"abcd", instance=3))
    root = read_header(stream, 0)
    atom = root.first(0x03E9)
    assert root.is_container and not atom.is_container
    assert (atom.data, atom.instance) == (b"abcd", 3)
    # A length running past the end keeps only the bytes that exist.
    cut = list(iter_records(stream[:-2], 8, len(stream) - 2))
    assert cut[0].data == b"ab"


def test_newest_fast_save_wins():
    deck = base_deck()
    deck.add_slide(B.shape(20, 202, (0, 0, 2000, 500), text=B.text_atoms("old text")))
    edited = B.box(
        0x03EE,
        B.slide_atom(),
        B.drawing([B.shape(20, 202, (0, 0, 2000, 500), text=B.text_atoms("new text"))]),
    )
    model = legacy_deck.read(deck.build(edit=(1, edited)))
    assert model.slides[0].shapes[0].text.text == "new text"


def test_user_edit_loops_are_survived():
    deck = base_deck()
    deck.add_slide()
    data = deck.build()
    compound = cfb.CompoundFile(data)
    stream = bytearray(compound.read("PowerPoint Document"))
    user = compound.read("Current User")
    offset = struct.unpack_from("<I", user, 16)[0]
    struct.pack_into("<I", stream, offset + 8 + 8, offset)  # the edit precedes itself
    table, ref = persist.read_persist_map(user, bytes(stream))
    assert ref == 1 and table


# -- deck-level structure -----------------------------------------------------------


def test_slides_masters_size_and_title():
    deck = base_deck(title="Quarterly Review")
    deck.add_slide()
    deck.add_slide()
    pres = load(deck)
    assert len(pres.slides) == 2
    assert (pres.width_pt, pres.height_pt) == (720, 540)
    assert pres.title == "Quarterly Review"
    pres.close()


def test_path_and_stream_sources_both_convert(tmp_path):
    deck = base_deck()
    deck.add_slide()
    path = tmp_path / "talk.ppt"
    path.write_bytes(deck.build())
    pres = document.load(path)
    assert pres.filename == "talk.ppt" and len(pres.slides) == 1
    pres.close()


def test_extension_does_not_matter(tmp_path):
    deck = base_deck()
    deck.add_slide()
    path = tmp_path / "actually-binary.pptx"
    path.write_bytes(deck.build())
    pres = document.load(path)
    assert len(pres.slides) == 1
    pres.close()


def test_encrypted_files_explain_themselves():
    deck = base_deck()
    deck.add_slide()
    with pytest.raises(EncryptedPackageError):
        document.load(io.BytesIO(deck.build(encrypted=True)))


def test_other_office_documents_are_not_presentations():
    word = B.compound_file({"WordDocument": b"x" * 100})
    assert not is_ppt(word)
    with pytest.raises(LegacyFormatError) as exc:
        document.load(io.BytesIO(word))
    assert "not a PowerPoint" in exc.value.message


def test_missing_core_streams_are_reported_as_damage():
    broken = B.compound_file({"PowerPoint Document": b"\0" * 64})
    with pytest.raises(LegacyFormatError) as exc:
        document.load(io.BytesIO(broken))
    assert "damaged" in exc.value.message


def test_hidden_slides_and_transitions():
    deck = base_deck()
    deck.add_slide(extra=[B.slide_info(flags=0x5)])
    deck.add_slide(extra=[B.slide_info(effect=10, direction=3, flags=0x400, advance=3000)])
    pres = load(deck)
    assert pres.slides[0].hidden and not pres.slides[1].hidden
    second = Deck(pres).slide(1)
    transition = timing.parse_transition(second.root)
    assert transition["type"] == "wipe" and transition["dir"] == "d"
    assert transition["advTm"] == 3000 and transition["advClick"] is False
    pres.close()


def test_master_artwork_and_own_background():
    deck = base_deck()
    deck.add_slide()
    deck.add_slide(atom=B.slide_atom(flags=2))  # hides master shapes, own background
    pptx, _ = convert(deck.build())
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        master = archive.read("ppt/slideMasters/slideMaster1.xml").decode()
        hidden = archive.read("ppt/slides/slide2.xml").decode()
    assert 'val="FF0000"' in master  # the band; its placeholders are prompts, not artwork
    assert master.count("<p:sp>") == 1
    assert 'showMasterSp="0"' in hidden


def test_each_colour_scheme_gets_a_recoloured_master():
    deck = base_deck()
    deck.add_slide()
    other = [0x00FF00] * 8
    deck.add_slide(atom=B.slide_atom(flags=5), scheme=other)
    pptx, _ = convert(deck.build())
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        masters = [n for n in archive.namelist() if re.fullmatch(r".*slideMaster\d+\.xml", n)]
        rels = archive.read("ppt/slides/_rels/slide2.xml.rels").decode()
    assert len(masters) == 2
    assert "slideLayout2.xml" in rels


# -- text ---------------------------------------------------------------------------


def test_style_runs_cover_text_and_the_final_paragraph_mark():
    data = B.style_runs([(6, 1, B.pf(align=2))], [(2, B.cf(bold=True)), (4, B.cf(size=20))])
    paragraphs, chars = text.parse_style_runs(data, 5)
    assert paragraphs == [(6, 1, {"align": 2})]
    assert chars == [(2, {"bold": True}), (4, {"size": 20})]


def test_damaged_style_runs_keep_what_was_read():
    data = B.style_runs([(6, 0, B.pf(align=2))], [])
    assert text.parse_style_runs(data[:-1], 5)[0] == []


def test_runs_inherit_from_the_master_and_override_it():
    deck = base_deck()
    atoms = B.text_atoms(
        "Plain bold",
        1,
        [(11, 0, B.pf())],
        [(6, B.cf()), (5, B.cf(bold=True, size=20, color=B.rgb(0x11, 0x22, 0x33), font=1))],
    )
    deck.add_slide(B.shape(20, 1, (288, 1296, 5472, 3600), text=atoms, placeholder=14))
    runs = list(only_shape(slide_xml(deck)).iter(f"{A}r"))
    first, second = runs[0].find(f"{A}rPr"), runs[1].find(f"{A}rPr")
    assert first.get("sz") == "3200" and first.get("b") == "0"
    assert first.find(f"{A}latin").get("typeface") == "Arial"
    assert second.get("sz") == "2000" and second.get("b") == "1"
    assert second.find(f"{A}latin").get("typeface") == "Times New Roman"
    assert second.find(f"{A}solidFill/{A}srgbClr").get("val") == "112233"


def test_paragraphs_breaks_levels_and_margins():
    deck = base_deck()
    atoms = B.text_atoms(
        "One\rTwo\x0bbreak",
        1,
        [(4, 0, B.pf()), (10, 1, B.pf())],
        [(14, B.cf())],
    )
    deck.add_slide(B.shape(20, 1, (288, 1296, 5472, 3600), text=atoms, placeholder=14))
    paragraphs = list(only_shape(slide_xml(deck)).iter(f"{A}p"))
    assert len(paragraphs) == 2
    assert paragraphs[1].find(f"{A}br") is not None
    first, second = paragraphs[0].find(f"{A}pPr"), paragraphs[1].find(f"{A}pPr")
    assert first.get("marL") == str(round(216 * 1587.5)) and first.get("indent") == "-342900"
    assert second.get("marL") == "800100" and second.get("indent") == "-342900"
    assert first.find(f"{A}buChar").get("char") == "•"


def test_spacing_and_alignment():
    deck = base_deck()
    atoms = B.text_atoms(
        "Text", 4, [(5, 0, B.pf(align=1, line=90, before=40, after=-32))], [(5, B.cf())]
    )
    deck.add_slide(B.shape(20, 202, (0, 0, 2000, 800), text=atoms))
    para = only_shape(slide_xml(deck)).find(f".//{A}pPr")
    assert para.get("algn") == "ctr"
    assert para.find(f"{A}lnSpc/{A}spcPct").get("val") == "90000"
    assert para.find(f"{A}spcBef/{A}spcPct").get("val") == "40000"
    assert para.find(f"{A}spcAft/{A}spcPts").get("val") == "400"


def test_older_rulers_set_margins_per_level():
    ruler = struct.pack("<I", 0x008 | 0x100) + struct.pack("<hh", 72, 360)
    assert text.parse_ruler(ruler) == {0: {"indent": 72, "left": 360}}


def test_stored_bullet_font_wins_over_its_flag():
    deck = base_deck()
    atoms = B.text_atoms(
        "Item",
        1,
        [(5, 0, B.pf(bullet=True, bullet_has_font=False, bullet_char=0x75, bullet_font=2))],
        [(5, B.cf())],
    )
    deck.add_slide(B.shape(20, 1, (288, 1296, 5472, 3600), text=atoms, placeholder=14))
    para = only_shape(slide_xml(deck)).find(f".//{A}pPr")
    assert para.find(f"{A}buFont").get("typeface") == "Wingdings"


def test_symbol_characters_use_the_symbol_font():
    deck = base_deck()
    atoms = B.text_atoms("ab", 4, [(4, 0, B.pf())], [(4, B.cf(symbol_font=2))])
    deck.add_slide(B.shape(20, 202, (0, 0, 2000, 500), text=atoms))
    run = list(only_shape(slide_xml(deck)).iter(f"{A}r"))[1]
    assert run.find(f"{A}t").text == ""
    assert run.find(f"{A}rPr/{A}latin").get("typeface") == "Wingdings"


def test_private_use_bullet_without_a_symbol_font_stays_a_bullet():
    deck = base_deck()
    atoms = B.text_atoms(
        "Item", 1, [(5, 0, B.pf(bullet=True, bullet_char=0xF097, bullet_font=0))], [(5, B.cf())]
    )
    deck.add_slide(B.shape(20, 1, (288, 1296, 5472, 3600), text=atoms, placeholder=14))
    para = only_shape(slide_xml(deck)).find(f".//{A}pPr")
    assert para.find(f"{A}buChar").get("char") == "•"


def test_numbered_lists_come_from_the_ppt9_tag():
    numbering = struct.pack("<IhHHhII", 0x03800000, -1, 1, 7, 3, 0, 0)
    deck = base_deck()
    atoms = B.text_atoms("One\rTwo", 1, [(8, 0, B.pf(bullet=True))], [(8, B.cf())])
    deck.add_slide(
        B.shape(
            20, 1, (288, 1296, 5472, 3600), text=atoms, placeholder=14,
            client=B.numbering_tag(numbering),
        )
    )  # fmt: skip
    number = only_shape(slide_xml(deck)).find(f".//{A}buAutoNum")
    assert number.get("type") == "romanUcPeriod" and number.get("startAt") == "3"


def test_slide_number_fields_render_the_real_number():
    deck = base_deck()
    field = B.rec(0x0FD8, struct.pack("<I", 0))
    deck.add_slide()
    deck.add_slide(
        B.shape(20, 202, (0, 0, 800, 300), text=B.text_atoms("*", 4, extra=field), placeholder=8)
    )
    pres = load(deck)
    html = render_slide(Deck(pres), 1, "doc/t/").html
    pres.close()
    assert re.search(r">2<", html)


def test_control_characters_never_reach_the_xml():
    deck = base_deck()
    deck.add_slide(B.shape(20, 202, (0, 0, 2000, 500), text=B.text_atoms("a\x01b<&>\x1fc")))
    assert "".join(t.text for t in slide_xml(deck).iter(f"{A}t")) == "ab<&>c"


def test_empty_placeholders_show_nothing():
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (288, 432, 5472, 1152), text=B.text_atoms("", 0), placeholder=13))
    tree = slide_xml(deck).find(f"{P}cSld/{P}spTree")
    assert tree.find(f"{P}sp") is None


def test_placeholders_inherit_geometry_and_text_props_from_the_master():
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, None, text=B.text_atoms("Title", 0), placeholder=13))
    shape = only_shape(slide_xml(deck))
    assert shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph").get("type") == "title"
    assert shape.find(f"{P}spPr/{A}xfrm/{A}off").get("y") == str(round(432 * 1587.5))
    assert shape.find(f"{P}txBody/{A}bodyPr").get("anchor") == "ctr"


def test_hyperlinks_on_shapes_and_text():
    deck = base_deck()
    deck.links.append((1, "https://example.com/"))
    link = B.hyperlink_data(1) + B.rec(0x0FDF, struct.pack("<II", 0, 4))
    atoms = B.text_atoms("Link here", extra=link)
    deck.add_slide(
        B.shape(20, 202, (0, 0, 2000, 500), text=atoms, client=B.hyperlink_data(1)),
    )
    pptx, _ = convert(deck.build())
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
        rels = archive.read("ppt/slides/_rels/slide1.xml.rels").decode()
    shape = only_shape(root)
    assert shape.find(f"{P}nvSpPr/{P}cNvPr/{A}hlinkClick") is not None
    assert next(shape.iter(f"{A}r")).find(f"{A}rPr/{A}hlinkClick") is not None
    assert 'Target="https://example.com/"' in rels and 'TargetMode="External"' in rels


# -- shapes -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "preset"),
    [
        (3, "ellipse"),
        (5, "triangle"),
        (13, "rightArrow"),
        (61, "wedgeRectCallout"),
        (116, "flowChartTerminator"),
    ],
)
def test_shape_types_map_to_presets(kind, preset):
    deck = base_deck()
    deck.add_slide(B.shape(20, kind, (0, 0, 1000, 1000)))
    assert only_shape(slide_xml(deck)).find(f"{P}spPr/{A}prstGeom").get("prst") == preset


def test_adjust_values_are_rescaled():
    deck = base_deck()
    deck.add_slide(B.shape(20, 2, (0, 0, 1000, 1000), {0x147: 5400}))
    guide = only_shape(slide_xml(deck)).find(f".//{A}gd")
    assert guide.get("fmla") == "val 25000"


def test_group_keeps_child_coordinates():
    deck = base_deck()
    child = B.shape(21, 1, (100, 100, 300, 300), child_anchor=True, flags=0xA02)
    deck.add_slide(B.group(20, (576, 576, 1152, 1152), (0, 0, 1000, 1000), child))
    grp = only_shape(slide_xml(deck))
    xfrm = grp.find(f"{P}grpSpPr/{A}xfrm")
    assert xfrm.find(f"{A}ext").get("cx") == "914400"
    assert xfrm.find(f"{A}chExt").get("cx") == "1000"
    assert grp.find(f"{P}sp/{P}spPr/{A}xfrm/{A}ext").get("cx") == "200"


def test_quarter_turned_frames_swap_their_sides():
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 500), {0x0004: 90 * B.ONE}))
    xfrm = only_shape(slide_xml(deck)).find(f"{P}spPr/{A}xfrm")
    assert xfrm.get("rot") == "5400000"
    assert xfrm.find(f"{A}ext").get("cx") == str(round(500 * 1587.5))
    assert xfrm.find(f"{A}ext").get("cy") == str(round(1000 * 1587.5))


def test_flips_lines_arrows_and_dashes():
    deck = base_deck()
    props = {0x1CB: 25400, 0x1CE: 6, 0x1D1: 1, 0x1C0: 0x0000FF}
    deck.add_slide(B.shape(20, 20, (0, 0, 1000, 500), props, flags=0xA40))
    shape = only_shape(slide_xml(deck))
    assert shape.find(f"{P}spPr/{A}xfrm").get("flipH") == "1"
    line = shape.find(f"{P}spPr/{A}ln")
    assert line.get("w") == "25400"
    assert line.find(f"{A}prstDash").get("val") == "dash"
    assert line.find(f"{A}tailEnd").get("type") == "triangle"
    assert line.find(f"{A}solidFill/{A}srgbClr").get("val") == "FF0000"


def test_connectors_become_connector_shapes():
    deck = base_deck()
    deck.add_slide(B.shape(20, 34, (0, 0, 1000, 500), {0x147: 5400}))
    shape = only_shape(slide_xml(deck))
    assert shape.tag == f"{P}cxnSp"
    assert shape.find(f".//{A}gd").get("fmla") == "val 25000"


def test_free_forms_keep_their_path():
    vertices = [struct.pack("<hh", x, y) for x, y in ((0, 0), (100, 0), (100, 100))]
    segments = [struct.pack("<H", s) for s in (0x4000, 0x0002, 0x6001, 0x8000)]
    props = {
        0x142: 100,
        0x143: 100,
        0x145: B.array(vertices, 4),
        0x146: B.array(segments, 2),
    }
    deck = base_deck()
    deck.add_slide(B.shape(20, 0, (0, 0, 1000, 1000), props))
    path = only_shape(slide_xml(deck)).find(f".//{A}path")
    assert (path.get("w"), path.get("h")) == ("100", "100")
    assert [c.tag.rsplit("}", 1)[1] for c in path] == ["moveTo", "lnTo", "lnTo", "close"]


def test_fills_solid_scheme_and_opacity():
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000), {0x181: 0x08000005, 0x182: B.ONE // 2}))
    fill = only_shape(slide_xml(deck)).find(f"{P}spPr/{A}solidFill/{A}srgbClr")
    assert fill.get("val") == "333399"
    assert fill.find(f"{A}alpha").get("val") == "50000"


def test_gradient_direction_and_focus():
    props = {0x180: 7, 0x181: 0x0000FF, 0x183: 0xFFFFFF, 0x18B: -90 * B.ONE, 0x18C: 100}
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000), props))
    gradient = only_shape(slide_xml(deck)).find(f"{P}spPr/{A}gradFill")
    stops = [s.find(f"{A}srgbClr").get("val") for s in gradient.iter(f"{A}gs")]
    assert stops == ["FFFFFF", "FF0000"]
    assert gradient.find(f"{A}lin").get("ang") == str(180 * 60000)


def test_mirrored_gradients_have_three_stops():
    props = {0x180: 7, 0x181: 0x0000FF, 0x183: 0xFFFFFF, 0x18C: 50}
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000), props))
    stops = list(only_shape(slide_xml(deck)).iter(f"{A}gs"))
    assert [s.get("pos") for s in stops] == ["0", "50000", "100000"]


def test_derived_colours_darken_the_fill():
    props = {0x181: 0x000000FF, 0x1C0: 0x10000000 | 0x7F << 16 | 0x0100 | 0xF0}
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000), props))
    color = only_shape(slide_xml(deck)).find(f"{P}spPr/{A}ln/{A}solidFill/{A}srgbClr")
    assert color.get("val") == "7F0000"


def test_shadows():
    props = dict([B.SHADOW_ON, (0x205, 25400), (0x206, 25400), (0x201, 0x808080)])
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000), props))
    shadow = only_shape(slide_xml(deck)).find(f"{P}spPr/{A}effectLst/{A}outerShdw")
    assert shadow.get("dir") == str(45 * 60000)


def test_text_boxes_have_no_paint_by_default():
    deck = base_deck()
    deck.add_slide(B.shape(20, 202, (0, 0, 1000, 500), text=B.text_atoms("x")))
    sp_pr = only_shape(slide_xml(deck)).find(f"{P}spPr")
    assert sp_pr.find(f"{A}noFill") is not None
    assert sp_pr.find(f"{A}ln/{A}noFill") is not None


def test_text_frame_properties():
    props = {0x81: 0, 0x82: 12700, 0x85: 2, 0x87: 4, 0x88: 2, 0xBF: 0x00020002}
    deck = base_deck()
    deck.add_slide(B.shape(20, 202, (0, 0, 1000, 500), props, text=B.text_atoms("x")))
    body = only_shape(slide_xml(deck)).find(f"{P}txBody/{A}bodyPr")
    assert body.get("wrap") == "none" and body.get("anchor") == "ctr"
    assert body.get("anchorCtr") == "1" and body.get("vert") == "vert270"
    assert (body.get("lIns"), body.get("tIns")) == ("0", "12700")
    assert body.find(f"{A}spAutoFit") is not None


def test_wordart_keeps_words_and_colour_without_a_box():
    props = {
        0xC0: "Wow".encode("utf-16-le") + b"\0\0",
        0xC5: "Arial Black".encode("utf-16-le") + b"\0\0",
        0x181: 0x0000FF,
        0x1BF: 0x00100010,
        0x1FF: 0x00080008,
    }
    deck = base_deck()
    deck.add_slide(B.shape(20, 136, (0, 0, 2000, 500), props))
    shape = only_shape(slide_xml(deck))
    assert next(shape.iter(f"{A}t")).text == "Wow"
    assert shape.find(f"{P}spPr/{A}noFill") is not None
    run = next(shape.iter(f"{A}rPr"))
    assert run.find(f"{A}latin").get("typeface") == "Arial Black"
    assert run.find(f"{A}solidFill/{A}srgbClr").get("val") == "FF0000"


# -- pictures -----------------------------------------------------------------------


def test_png_pictures_with_cropping():
    deck = base_deck()
    index = deck.add_picture(B.blip_png(PNG), 6)
    deck.add_slide(B.shape(20, 75, (0, 0, 1000, 1000), {0x104: index, 0x100: B.ONE // 4}))
    pptx, _ = convert(deck.build())
    with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
        assert archive.read("ppt/media/image1.png") == PNG
    picture = only_shape(root)
    assert picture.tag == f"{P}pic"
    assert picture.find(f"{P}blipFill/{A}srcRect").get("t") == "25000"


def test_inline_blips_in_the_store_are_found():
    entry = B.bse(6, 0, 0) + b""
    blip = B.blip_png(PNG)
    payload = read_header(entry, 0).data + blip
    store = B.box(0xF001, B.rec(0xF007, payload, 6, 2))
    document_box = B.box(0x03E8, B.box(0x040B, B.box(0xF000, store)))
    found = pictures.load_store(read_header(document_box, 0), b"")
    assert pictures.decode_blip(found[1]).data == PNG


def test_compressed_metafiles_get_their_frame_back():
    image = pictures.decode_blip(read_header(B.blip_wmf(b"\x01\x00\x09\x00" + b"\0" * 14), 0))
    assert image.extension == "wmf"
    assert image.data.startswith(b"\xd7\xcd\xc6\x9a")
    assert len(image.data) == 22 + 18


def test_bitmaps_get_a_file_header():
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0) + b"\xff\0\0\0"
    blip = B.rec(0xF01F, b"\0" * 16 + b"\xff" + dib, 0x7A8)
    image = pictures.decode_blip(read_header(blip, 0))
    assert image.data.startswith(b"BM") and image.extension == "bmp"
    assert struct.unpack_from("<I", image.data, 10)[0] == 54


def test_mac_pict_pictures_are_skipped_gracefully():
    deck = base_deck()
    index = deck.add_picture(B.rec(0xF01C, b"\0" * 100, 0x542), 4)
    deck.add_slide(B.shape(20, 75, (0, 0, 1000, 1000), {0x104: index}))
    assert slide_xml(deck).find(f".//{P}pic") is None


def test_inflate_is_bounded():
    assert len(pictures._inflate(__import__("zlib").compress(b"\0" * 100000), 10)) == 10


def test_picture_fills():
    deck = base_deck()
    index = deck.add_picture(B.blip_png(PNG), 6)
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000), {0x180: 3, 0x186: index}))
    fill = only_shape(slide_xml(deck)).find(f"{P}spPr/{A}blipFill")
    assert fill.find(f"{A}stretch") is not None


# -- animations ---------------------------------------------------------------------


def variant_int(instance: int, value: int) -> bytes:
    return B.rec(0xF142, b"\x01" + struct.pack("<i", value), instance)


def variant_text(instance: int, value: str) -> bytes:
    return B.rec(0xF142, b"\x03" + value.encode("utf-16-le") + b"\0\0", instance)


def node(
    kind: int, dur: int, *children: bytes, props: dict | None = None, delay: int | None = None
) -> bytes:
    atom = B.rec(0xF127, struct.pack("<8i", 0, 0, kind, 3, 0, 0, dur, 0))
    props = b"".join(variant_int(k, v) for k, v in (props or {}).items())
    parts = [atom, B.box(0xF13D, props)]
    if delay is not None:
        parts.append(B.box(0xF125, B.rec(0xF128, struct.pack("<IIIi", 0, 0, 0, delay)), instance=1))
    return B.box(0xF144, *parts, *children, instance=1)


def target(spid: int, mode: int = 0, begin: int = -1, end: int = -1) -> bytes:
    return B.box(0xF13C, B.rec(0x2AFB, struct.pack("<IIIii", mode, 1, spid, begin, end)))


def visibility(spid: int, **where) -> bytes:
    behaviour = B.box(
        0xF12A,
        B.rec(0xF133, b"\0" * 16),
        B.box(0xF13E, variant_text(0, "style.visibility"), instance=1),
        target(spid, **where),
    )
    return node(
        3, 1, B.box(0xF131, B.rec(0xF13A, b"\0" * 8), variant_text(1, "visible"), behaviour)
    )


def effect_filter(spid: int, name: str) -> bytes:
    behaviour = B.box(0xF12A, B.rec(0xF133, b"\0" * 16), target(spid))
    return node(3, 500, B.box(0xF12D, B.rec(0xF136, b"\0" * 8), variant_text(1, name), behaviour))


def timeline(*effects: bytes) -> bytes:
    clicks = [
        node(
            0, 0,
            node(0, 0, effect, props={20: 7}, delay=0),
            props={20: 6}, delay=-1,
        )
        for effect in effects
    ]  # fmt: skip
    main = node(1, -1, *clicks, props={20: 4})
    return node(0, -1, main, props={20: 9})


def animated(deck: B.Deck) -> dict:
    pres = load(deck)
    root = Deck(pres).slide(0).root
    pres.close()
    return timing.parse_animations(root)


def test_timelines_become_click_steps():
    effect = node(
        0, 0, visibility(20), effect_filter(20, "blinds(horizontal)"),
        props={20: 1, 9: 3, 10: 10, 11: 1}, delay=0,
    )  # fmt: skip
    deck = base_deck()
    deck.add_slide(B.shape(20, 1, (0, 0, 1000, 1000)), extra=[B.timeline_tag(timeline(effect))])
    build = animated(deck)
    assert len(build["steps"]) == 1
    only = build["steps"][0]["effects"][0]
    assert (only["spid"], only["cls"], only["preset"], only["sub"]) == ("20", "entr", 3, 10)
    assert only["filter"] == "blinds(horizontal)"
    assert build["hidden"] == [{"spid": "20"}]


def test_paragraph_targets_are_character_offsets():
    effect = node(0, 0, visibility(20, mode=2, begin=4, end=7), props={20: 1, 9: 1, 11: 1}, delay=0)
    deck = base_deck()
    deck.add_slide(
        B.shape(20, 202, (0, 0, 2000, 1000), text=B.text_atoms("One\rTwo\rSix")),
        extra=[B.timeline_tag(timeline(effect))],
    )
    assert animated(deck)["steps"][0]["effects"][0]["para"] == [1]


def test_empty_timelines_produce_no_animation():
    deck = base_deck()
    deck.add_slide(
        B.shape(20, 1, (0, 0, 1000, 1000), client=B.animation_info(1, 12)),
        extra=[B.timeline_tag(node(0, -1, props={20: 9}))],
    )
    assert animated(deck) is None


def test_older_builds_become_steps_in_order():
    deck = base_deck()
    deck.add_slide(
        B.shape(21, 1, (0, 0, 1000, 1000), client=B.animation_info(2, 12, 3)),
        B.shape(20, 1, (0, 0, 1000, 1000), client=B.animation_info(1, 2, 1)),
        B.shape(22, 1, (0, 0, 1000, 1000), client=B.animation_info(3, 0, build=0)),
    )
    steps = animated(deck)["steps"]
    assert [s["effects"][0]["spid"] for s in steps] == ["20", "21"]
    assert steps[0]["effects"][0]["filter"] == "blinds(horizontal)"
    assert (steps[1]["effects"][0]["preset"], steps[1]["effects"][0]["sub"]) == (2, 4)


def test_older_builds_by_paragraph():
    atoms = B.text_atoms(
        "One\rSub\rTwo", 1, [(4, 0, B.pf()), (4, 1, B.pf()), (4, 0, B.pf())], [(12, B.cf())]
    )
    deck = base_deck()
    deck.add_slide(
        B.shape(20, 202, (0, 0, 2000, 1000), text=atoms, client=B.animation_info(1, 0, build=2))
    )
    steps = animated(deck)["steps"]
    assert [s["effects"][0]["para"] for s in steps] == [[0, 1], [2]]


# -- robustness ---------------------------------------------------------------------


def rich_deck() -> bytes:
    deck = base_deck()
    index = deck.add_picture(B.blip_png(PNG), 6)
    effect = node(0, 0, visibility(20), props={20: 1, 9: 1, 11: 1}, delay=0)
    atoms = B.text_atoms(
        "Hello\rWorld", 1, [(6, 0, B.pf()), (6, 1, B.pf())], [(12, B.cf(bold=True))]
    )
    deck.add_slide(
        B.shape(20, 1, (288, 432, 5472, 1152), text=atoms, placeholder=14),
        B.shape(21, 75, (0, 0, 1000, 1000), {0x104: index}),
        B.group(
            22, (0, 0, 500, 500), (0, 0, 10, 10), B.shape(23, 3, (0, 0, 5, 5), child_anchor=True)
        ),
        extra=[B.slide_info(effect=2), B.timeline_tag(timeline(effect))],
    )
    return deck.build()


def outcome(data: bytes) -> None:
    """Every mutation either opens and renders or raises a friendly error."""
    try:
        pres = document.load(io.BytesIO(data), lenient=True)
    except PackageError as exc:
        assert exc.message
        return
    deck = Deck(pres)
    for ref in pres.slides:
        render_slide(deck, ref.index, "doc/t/")
    pres.close()


@pytest.mark.parametrize("seed", range(40))
def test_random_byte_flips_never_crash(seed):
    data = bytearray(rich_deck())
    rng = random.Random(seed)
    for _ in range(rng.randint(1, 60)):
        data[rng.randrange(len(data))] = rng.randrange(256)
    outcome(bytes(data))


@pytest.mark.parametrize("seed", range(40))
def test_document_stream_damage_never_crashes(seed):
    """Flips inside the record stream itself, past the container checks."""
    original = rich_deck()
    compound = cfb.CompoundFile(original)
    stream = bytearray(compound.read("PowerPoint Document"))
    rng = random.Random(seed)
    for _ in range(rng.randint(1, 30)):
        stream[rng.randrange(len(stream))] = rng.randrange(256)
    streams = {
        "Current User": compound.read("Current User"),
        "PowerPoint Document": bytes(stream),
    }
    outcome(B.compound_file(streams))


@pytest.mark.parametrize("fraction", [0.1, 0.3, 0.5, 0.7, 0.9, 0.99])
def test_truncated_files_never_crash(fraction):
    data = rich_deck()
    outcome(data[: int(len(data) * fraction)])


def test_deep_nesting_is_bounded():
    payload = B.box(0xF003, B.box(0xF004, B.rec(0xF00A, struct.pack("<II", 1, 1), 0, 2)))
    for _ in range(3000):
        payload = B.box(0xF003, payload)
    deck = base_deck()
    deck.add_slide()
    deck.slides[0] = B.box(0x03EE, B.slide_atom(), B.box(0x040C, B.box(0xF002, payload)))
    outcome(deck.build())


def test_property_table_lengths_are_bounded():
    props = Props()
    from slidelite.legacy.officeart import read_properties

    bogus = B.rec(0xF00B, struct.pack("<HI", 0x8145, 0xFFFFFFF0), 2000, 3)
    read_properties(read_header(bogus, 0), props)
    assert props.complex[0x145] == b""


# -- symbol fonts and shared rendering fixes ----------------------------------------


def test_dingbat_fonts_map_to_unicode():
    assert translate_symbol("n", "Monotype Sorts")[0] == "■"
    assert translate_symbol("u", "ZapfDingbats")[0] == "◆"
    assert translate_symbol("!", "Monotype Sorts")[0] == "✁"


def test_a_big_first_character_does_not_stretch_the_paragraph():
    deck = base_deck()
    atoms = B.text_atoms(
        "  Small text", 4, [(13, 0, B.pf())], [(2, B.cf(size=40)), (11, B.cf(size=20))]
    )
    deck.add_slide(B.shape(20, 202, (0, 0, 3000, 500), text=atoms))
    pres = load(deck)
    html = render_slide(Deck(pres), 0, "doc/t/").html
    pres.close()
    assert re.search(r"<p [^>]*font-size:20px", html)


# -- the real window ----------------------------------------------------------------


@pytest.mark.gui
def test_ppt_file_opens_in_the_viewer(fixtures_dir):
    from tests.harness import Harness

    harness = Harness(fixtures_dir / "binary-deck.ppt")
    harness.wait_js(
        "!!window.SlideLite.state.doc && !!document.querySelector('#slide-host .slide')"
    )
    harness.pump(0.1)
    text_shown = harness.js("return document.querySelector('#slide-host .slide').textContent")
    harness.close()
    assert "PowerPoint 97-2003" in text_shown and "Opened natively" in text_shown


def test_pps_files_start_as_a_slide_show(tmp_path):
    deck = base_deck()
    deck.add_slide()
    path = tmp_path / "show.pps"
    path.write_bytes(deck.build())
    pres = document.load(path)
    assert pres.kind == "slideshow"
    pres.close()
