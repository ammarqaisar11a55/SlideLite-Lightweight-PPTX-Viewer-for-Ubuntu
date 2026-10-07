"""Generate docs/showcase.pptx, a synthetic demo deck used for screenshots.

    python3 tools/make_showcase.py [output.pptx]

Requires python-pptx (dev only).  All content is invented sample data.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixtures import deterministic  # noqa: E402

OUT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parent.parent / "docs" / "showcase.pptx"
)

INK = RGBColor(0x1B, 0x1F, 0x3B)
INDIGO = RGBColor(0x3F, 0x51, 0xC7)
SKY = RGBColor(0x4C, 0xC9, 0xF0)
CORAL = RGBColor(0xFF, 0x7A, 0x59)
MINT = RGBColor(0x2E, 0xC4, 0x9A)
SUN = RGBColor(0xFF, 0xC1, 0x3D)
PAPER = RGBColor(0xF7, 0xF8, 0xFC)
GRAY = RGBColor(0x5B, 0x60, 0x7A)
FONT = "Liberation Sans"


def text(shape, value, size, color=INK, bold=False, align=PP_ALIGN.LEFT):
    tf = shape.text_frame
    tf.word_wrap = True
    tf.text = value
    for p in tf.paragraphs:
        p.alignment = align
        for run in p.runs:
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = color
            run.font.name = FONT
    return tf


def box(slide, x, y, w, h, value="", size=18, color=INK, bold=False, align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    text(shape, value, size, color, bold, align)
    return shape


def background(slide, top, bottom=None):
    fill = slide.background.fill
    if bottom is None:
        fill.solid()
        fill.fore_color.rgb = top
        return
    fill.gradient()
    fill.gradient_angle = 90
    stops = fill.gradient_stops
    stops[0].color.rgb = top
    stops[1].color.rgb = bottom


def heading(slide, title, kicker):
    box(slide, 0.7, 0.45, 9, 0.4, kicker.upper(), 13, INDIGO, True)
    box(slide, 0.7, 0.8, 11.5, 0.9, title, 34, INK, True)
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0.7), Inches(1.72), Inches(0.9), Emu(57150)
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = CORAL
    bar.line.fill.background()


def title_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, RGBColor(0x14, 0x17, 0x33), RGBColor(0x30, 0x3C, 0x9E))
    for x, y, d, color in ((9.6, -1.2, 5.2, INDIGO), (11.2, 4.2, 3.4, SKY), (8.4, 4.9, 1.6, CORAL)):
        circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
        circle.fill.solid()
        circle.fill.fore_color.rgb = color
        circle.line.fill.background()
    box(slide, 0.9, 2.1, 8.5, 0.5, "QUARTERLY PRODUCT REVIEW", 16, SKY, True)
    box(
        slide,
        0.9,
        2.6,
        8.5,
        1.8,
        "Northwind Learning Platform",
        48,
        RGBColor(0xFF, 0xFF, 0xFF),
        True,
    )
    box(
        slide,
        0.9,
        4.45,
        8,
        0.6,
        "Q3 results, highlights and the road ahead",
        22,
        RGBColor(0xD8, 0xDC, 0xF5),
    )
    box(slide, 0.9, 6.4, 6, 0.4, "Product Team  ·  Sample data", 14, RGBColor(0xA9, 0xB0, 0xE0))


def highlights(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, PAPER)
    heading(slide, "Quarter highlights", "Overview")
    items = [
        ("38%", "growth in weekly active learners", INDIGO),
        ("4.7★", "average course rating", CORAL),
        ("12", "new courses launched", MINT),
        ("99.95%", "service availability", SUN),
    ]
    for i, (big, label, color) in enumerate(items):
        x = 0.7 + i * 3.05
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(2.3), Inches(2.8), Inches(2.4)
        )
        card.adjustments[0] = 0.08
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        card.line.color.rgb = RGBColor(0xE2, 0xE5, 0xF0)
        card.shadow.inherit = False
        accent = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(x), Inches(2.3), Inches(2.8), Emu(76200)
        )
        accent.fill.solid()
        accent.fill.fore_color.rgb = color
        accent.line.fill.background()
        box(slide, x + 0.25, 2.65, 2.4, 1.0, big, 40, color, True)
        box(slide, x + 0.25, 3.65, 2.35, 0.9, label, 16, GRAY)
    notes = box(slide, 0.7, 5.15, 12, 1.8, "", 18)
    tf = notes.text_frame
    tf.text = "Mobile app usage overtook desktop for the first time"
    for line in (
        "Average session length up from 18 to 24 minutes",
        "Support tickets down 21% after the new help centre",
    ):
        tf.add_paragraph().text = line
    for p in tf.paragraphs:
        p.font.size = Pt(18)
        p.font.color.rgb = INK
        p.font.name = FONT
        ppr = p._p.get_or_add_pPr()
        ppr.set("marL", "285750")
        ppr.set("indent", "-285750")
        from pptx.oxml import parse_xml

        ppr.append(
            parse_xml(
                '<a:buClr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:srgbClr val="FF7A59"/></a:buClr>'
            )
        )
        ppr.append(
            parse_xml(
                '<a:buChar xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" char="&#9679;"/>'
            )
        )


def revenue_chart(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, PAPER)
    heading(slide, "Revenue by region", "Finance")
    data = CategoryChartData()
    data.categories = ["Q4 '25", "Q1 '26", "Q2 '26", "Q3 '26"]
    data.add_series("Europe", (4.2, 4.8, 5.1, 5.9))
    data.add_series("Americas", (3.1, 3.4, 4.0, 4.6))
    data.add_series("Asia Pacific", (1.8, 2.3, 2.9, 3.8))
    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.7), Inches(2.0), Inches(12), Inches(5.0), data
    )
    chart = frame.chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(14)
    chart.value_axis.has_major_gridlines = True
    chart.value_axis.tick_labels.font.size = Pt(13)
    chart.category_axis.tick_labels.font.size = Pt(14)
    chart.value_axis.tick_labels.number_format = '"$"0.0"M"'
    chart.value_axis.tick_labels.number_format_is_linked = False
    for series, color in zip(chart.plots[0].series, (INDIGO, SKY, CORAL), strict=True):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = color
    chart.plots[0].gap_width = 80


def kpi_table(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, PAPER)
    heading(slide, "Key metrics", "Performance")
    rows = [
        ("Metric", "Q2 2026", "Q3 2026", "Change"),
        ("Weekly active learners", "182,400", "251,700", "+38%"),
        ("Course completions", "41,250", "52,900", "+28%"),
        ("Average rating", "4.5", "4.7", "+0.2"),
        ("Support tickets", "6,820", "5,390", "−21%"),
        ("Revenue", "$11.9M", "$14.3M", "+20%"),
    ]
    shape = slide.shapes.add_table(len(rows), 4, Inches(0.7), Inches(2.1), Inches(12), Inches(4.2))
    table = shape.table
    for c, width in enumerate((5.0, 2.3, 2.3, 2.4)):
        table.columns[c].width = Inches(width)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.text = value
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.RIGHT
            p.runs[0].font.size = Pt(18 if r else 17)
            p.runs[0].font.name = FONT
            if r and c == 3:
                p.runs[0].font.bold = True
                p.runs[0].font.color.rgb = MINT if not value.startswith("−") else MINT


def process(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, PAPER)
    heading(slide, "How a course goes live", "Process")
    steps = [
        ("Plan", "Outline & goals", INDIGO),
        ("Create", "Lessons & media", SKY),
        ("Review", "Experts & pilots", CORAL),
        ("Launch", "Publish & promote", MINT),
    ]
    for i, (name, sub, color) in enumerate(steps):
        x = 0.7 + i * 3.05
        shape = slide.shapes.add_shape(
            MSO_SHAPE.CHEVRON if i else MSO_SHAPE.PENTAGON,
            Inches(x),
            Inches(2.7),
            Inches(3.25),
            Inches(1.5),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = color
        shape.line.fill.background()
        text(shape, name, 26, RGBColor(0xFF, 0xFF, 0xFF), True, PP_ALIGN.CENTER)
        shape.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        box(slide, x + 0.3, 4.45, 2.7, 0.5, sub, 17, GRAY, False, PP_ALIGN.CENTER)
        dot = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(x + 1.4), Inches(5.15), Inches(0.42), Inches(0.42)
        )
        dot.fill.solid()
        dot.fill.fore_color.rgb = color
        dot.line.fill.background()
        text(dot, str(i + 1), 13, RGBColor(0xFF, 0xFF, 0xFF), True, PP_ALIGN.CENTER)
    line = slide.shapes.add_connector(1, Inches(1.2), Inches(5.36), Inches(12.2), Inches(5.36))
    line.line.color.rgb = RGBColor(0xC9, 0xCE, 0xE3)
    line.line.width = Pt(2)
    spTree = slide.shapes._spTree
    spTree.remove(line._element)
    spTree.insert(2, line._element)
    box(
        slide,
        0.7,
        6.1,
        12,
        0.6,
        "Average time from plan to launch: 5 weeks (down from 8)",
        18,
        INK,
        False,
        PP_ALIGN.CENTER,
    )


def share_chart(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, PAPER)
    heading(slide, "Where learners come from", "Audience")
    data = CategoryChartData()
    data.categories = ["Mobile app", "Web", "Partner portals", "Classroom kits"]
    data.add_series("Share", (0.46, 0.31, 0.15, 0.08))
    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.DOUGHNUT, Inches(0.7), Inches(2.0), Inches(6.5), Inches(5.0), data
    )
    chart = frame.chart
    chart.has_legend = False
    chart.has_title = False
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.show_percentage = True
    plot.data_labels.show_value = False
    plot.data_labels.font.size = Pt(14)
    plot.data_labels.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for point, color in zip(plot.series[0].points, (INDIGO, SKY, CORAL, SUN), strict=True):
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = color
    for i, (name, value, color) in enumerate(
        (
            ("Mobile app", "46%", INDIGO),
            ("Web", "31%", SKY),
            ("Partner portals", "15%", CORAL),
            ("Classroom kits", "8%", SUN),
        )
    ):
        y = 2.6 + i * 1.0
        swatch = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(7.8), Inches(y), Inches(0.35), Inches(0.35)
        )
        swatch.fill.solid()
        swatch.fill.fore_color.rgb = color
        swatch.line.fill.background()
        box(slide, 8.35, y - 0.1, 3.2, 0.5, name, 20, INK)
        box(slide, 11.2, y - 0.1, 1.4, 0.5, value, 20, INK, True, PP_ALIGN.RIGHT)


def closing(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background(slide, RGBColor(0x14, 0x17, 0x33), RGBColor(0x30, 0x3C, 0x9E))
    box(
        slide,
        0.9,
        2.5,
        11.5,
        1.2,
        "Thank you",
        54,
        RGBColor(0xFF, 0xFF, 0xFF),
        True,
        PP_ALIGN.CENTER,
    )
    box(
        slide,
        0.9,
        3.8,
        11.5,
        0.8,
        "Questions and discussion",
        24,
        RGBColor(0xD8, 0xDC, 0xF5),
        False,
        PP_ALIGN.CENTER,
    )


TRANSITIONS = [
    b'<p:transition spd="med"><p:fade/></p:transition>',
    b'<p:transition spd="slow"><p:push dir="u"/></p:transition>',
    b'<p:transition><p:wipe dir="l"/></p:transition>',
    b'<p:transition><p:cover dir="l"/></p:transition>',
    b'<p:transition><p:split orient="vert" dir="out"/></p:transition>',
    b"<p:transition><p:circle/></p:transition>",
    b'<p:transition spd="slow"><p:fade thruBlk="1"/></p:transition>',
]


def main() -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for build in (title_slide, highlights, revenue_chart, kpi_table, process, share_chart, closing):
        build(prs)
    prs.core_properties.title = "Northwind Learning Platform - Quarterly Review (sample)"
    buf = io.BytesIO()
    prs.save(buf)
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zin,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            data = zin.read(info)
            name = info.filename
            if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                n = int(name[len("ppt/slides/slide") : -4])
                data = data.replace(
                    b"</p:clrMapOvr>", b"</p:clrMapOvr>" + TRANSITIONS[(n - 1) % len(TRANSITIONS)]
                )
            zout.writestr(info, data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(deterministic(out.getvalue()))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
