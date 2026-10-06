import re
import xml.etree.ElementTree as ET

import pytest

from slidelite.presentation import document, geometry
from slidelite.presentation.color import ColorResolver
from slidelite.presentation.fonts import family_stack, translate_symbol
from slidelite.presentation.parts import Deck
from slidelite.render.slide import render_slide
from slidelite.render.text import autonum_text

A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def render(fixtures_dir, name, index=0):
    pres = document.load(fixtures_dir / name, lenient=True)
    deck = Deck(pres)
    result = render_slide(deck, index, "doc/test/")
    pres.close()
    return result


# -- geometry -----------------------------------------------------------------------


def test_guide_formulas():
    g = geometry.Guides(1000, 500)
    assert g.evaluate("*/ w 1 2") == 500
    assert g.evaluate("+- w h 100") == 1400
    assert g.evaluate("pin 0 600 500") == 500
    assert g.evaluate("?: -1 10 20") == 20
    assert g.evaluate("max w h") == 1000
    assert round(g.evaluate("cos 100 5400000")) == 0
    assert round(g.evaluate("sin 100 5400000")) == 100
    assert round(g.evaluate("at2 1 1")) == 2700000
    assert g.val("ss") == 500 and g.val("hc") == 500 and g.val("wd4") == 250


def test_preset_rect_and_ellipse():
    rect = geometry.preset("rect", 914400, 457200, scale=1 / 12700)
    assert rect.paths[0].d == "M0 0L72 0L72 36L0 36Z"
    ellipse = geometry.preset("ellipse", 914400, 914400, scale=1 / 12700)
    assert ellipse.paths[0].d.count("A") == 4
    assert ellipse.text_rect is not None and 0.1 < ellipse.text_rect[0] < 0.2


def test_every_preset_evaluates():
    for name in geometry.presets():
        geom = geometry.preset(name, 1219200, 685800, scale=1 / 12700)
        for path in geom.paths:
            assert "nan" not in path.d and "inf" not in path.d, name


def test_adjustments_change_geometry():
    default = geometry.preset("roundRect", 1000000, 1000000, scale=1 / 12700).paths[0].d
    sharper = (
        geometry.preset("roundRect", 1000000, 1000000, {"adj": "val 0"}, scale=1 / 12700).paths[0].d
    )
    assert default != sharper


def test_custom_geometry():
    cust = ET.fromstring(
        f'<a:custGeom xmlns:a="{A}"><a:pathLst><a:path w="100" h="100">'
        '<a:moveTo><a:pt x="0" y="0"/></a:moveTo><a:lnTo><a:pt x="100" y="100"/></a:lnTo>'
        "<a:close/></a:path></a:pathLst></a:custGeom>"
    )
    geom = geometry.custom(cust, 254000, 254000, scale=1 / 12700)
    assert geom.paths[0].d == "M0 0L20 20Z"


# -- colours / fonts --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        ('<a:schemeClr val="accent1"><a:lumMod val="75000"/></a:schemeClr>', "#2f5597"),
        ('<a:schemeClr val="bg1"><a:lumMod val="85000"/></a:schemeClr>', "#d9d9d9"),
        (
            '<a:schemeClr val="accent1"><a:lumMod val="60000"/><a:lumOff val="40000"/></a:schemeClr>',
            "#8faadc",
        ),
        ('<a:srgbClr val="FF0000"><a:alpha val="50000"/></a:srgbClr>', "rgba(255,0,0,0.500)"),
        ('<a:prstClr val="black"/>', "#000000"),
        ('<a:sysClr val="window" lastClr="FFFFFF"/>', "#ffffff"),
    ],
)
def test_color_transforms(xml, expected):
    resolver = ColorResolver({"dk1": "000000", "lt1": "FFFFFF", "accent1": "4472C4"})
    tag_end = xml.index(" ")
    el = ET.fromstring(f'{xml[:tag_end]} xmlns:a="{A}"{xml[tag_end:]}')
    assert resolver.resolve(el).css() == expected


def test_color_map_is_used():
    resolver = ColorResolver({"dk1": "111111", "lt1": "EEEEEE"}, {"tx1": "lt1", "bg1": "dk1"})
    assert resolver.scheme_color("tx1").css() == "#eeeeee"


def test_font_substitution_and_sanitizing():
    assert family_stack("Calibri").startswith("'Calibri', 'Carlito'")
    assert "'Liberation Serif'" in family_stack("Times New Roman")
    assert family_stack("Courier New").endswith("monospace")
    hostile = family_stack('Evil"; x:url(y)</style><script>')
    assert '"' not in hostile and "<" not in hostile and ";" not in hostile


def test_symbol_fonts():
    assert translate_symbol("§", "Wingdings", bullet=True)[0] == "▪"
    assert translate_symbol("", "Wingdings", bullet=True)[0] == "➢"
    assert translate_symbol("a", "Symbol")[0] == "α"
    assert translate_symbol("x", "Arial") == ("x", False)


def test_autonumbering():
    assert autonum_text("arabicPeriod", 3) == "3."
    assert autonum_text("romanUcPeriod", 4) == "IV."
    assert autonum_text("alphaLcParenR", 28) == "ab)"
    assert autonum_text("arabicParenBoth", 2) == "(2)"


# -- slides --------------------------------------------------------------------------------


def test_placeholder_inheritance(fixtures_dir):
    html = render(fixtures_dir, "simple-text.pptx", 0).html
    # Title inherits 44pt from the master title style; subtitle is centred via layout.
    assert "font-size:44px" in html
    assert "SlideLite Test Deck" in html
    assert 'data-ph="ctrTitle"' in html


def test_bullets_and_levels(fixtures_dir):
    html = render(fixtures_dir, "simple-text.pptx", 1).html
    assert html.count('class="bu"') == 5
    assert "font-weight:700" in html and "font-style:italic" in html
    assert "color:#c02020" in html


def test_hyperlink_markup(fixtures_dir):
    html = render(fixtures_dir, "simple-text.pptx", 2).html
    assert 'data-href="https://example.com/"' in html


def test_text_formatting(fixtures_dir):
    html = render(fixtures_dir, "text-formatting.pptx").html
    assert "line-through" in html
    assert "background-color:#ffff00" in html
    assert "letter-spacing:6px" in html
    assert "text-transform:uppercase" in html
    assert ">1.<" in html and ">3.<" in html
    assert "transform:rotate(90deg)" in html  # vertical text
    assert ">▪<" in html  # Wingdings bullet translated
    assert "'Liberation Serif'" in html


def test_shapes_render_as_svg(fixtures_dir):
    html = render(fixtures_dir, "shapes.pptx").html
    assert html.count('class="geom"') == 13
    assert "transform:rotate(20deg)" in html
    assert "url(#@@" in html  # theme gradient fills use per-mount ids


def test_fills_lines_effects(fixtures_dir):
    html = render(fixtures_dir, "fills.pptx").html
    assert "<linearGradient" in html
    assert "<pattern" in html
    assert 'fill-opacity="0.5"' in html
    assert "stroke-dasharray" in html
    assert "drop-shadow(" in html
    assert "<marker" in html


def test_pictures_and_cropping(fixtures_dir):
    html = render(fixtures_dir, "images.pptx").html
    hrefs = re.findall(r'<image href="([^"]+)"', html)
    assert len(hrefs) == 3 and all(h.startswith("doc/test/part/ppt/media/") for h in hrefs)
    # 25% cropped from each side of a 216px frame: the image is 432px wide, shifted left.
    assert re.search(r'x="-108" y="0" width="432" height="216"', html)


def test_groups_and_flips(fixtures_dir):
    html = render(fixtures_dir, "groups.pptx").html
    assert html.count('class="grp"') == 2
    assert "transform:scale(-1,1)" in html


def test_text_is_escaped(fixtures_dir, tmp_path):
    import shutil
    import zipfile

    src = fixtures_dir / "simple-text.pptx"
    dst = tmp_path / "evil.pptx"
    shutil.copy(src, dst)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "ppt/slides/slide1.xml":
                data = data.replace(
                    b"SlideLite Test Deck", b"&lt;img src=x onerror=alert(1)&gt; @@x"
                )
            zout.writestr(info, data)
    html = render(tmp_path, "evil.pptx", 0).html
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&#64;&#64;x" in html


def test_broken_slide_is_isolated(fixtures_dir):
    pres = document.load(fixtures_dir / "corrupt-bad-xml.pptx", lenient=True)
    deck = Deck(pres)
    broken = render_slide(deck, 2, "doc/x/")
    assert broken.failed and "could not be displayed" in broken.html
    ok = render_slide(deck, 3, "doc/x/")
    assert not ok.failed and "Slide 4" in ok.html
    pres.close()


def test_every_fixture_renders(fixtures_dir):
    for path in sorted(fixtures_dir.glob("*.pptx")):
        try:
            pres = document.load(path, lenient=True)
        except document.PackageError:
            continue
        deck = Deck(pres)
        for i in range(len(pres.slides)):
            result = render_slide(deck, i, "doc/x/")
            assert result.html.startswith('<div class="slide')
        pres.close()
