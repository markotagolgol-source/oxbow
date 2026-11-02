"""Behavior of the synthetic sample PDFs. No personal forms."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import pikepdf
from PIL import Image

from oxbow.fill import read_back, save_filled
from oxbow.layout import PAGE_H, PAGE_W, Block, Page, layout_template, load_document, load_static_pages
from oxbow.render import (
    border_overshoots,
    caption_box,
    caption_insets,
    check_page,
    fit_image,
    fit_ppm,
    input_box,
    render_page,
    rule_box,
    scaled_logo,
)
from oxbow.xfa import read_packets

ROOT = Path(__file__).resolve().parents[1]
HARBOR = ROOT / "examples" / "harbor-picnic.pdf"
CLUB = ROOT / "examples" / "club-note.pdf"

FILLED = {
    "PicnicSignup.Sheet.GuestName": "River Quinn",
    "PicnicSignup.Sheet.Dish": "Salad",
    "PicnicSignup.NotesPage.Note": "I will bring cups.",
}

INLINE = """<?xml version="1.0" encoding="UTF-8"?>
<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/">
  <subform name="Sheet" layout="tb" w="180mm">
    <pageSet>
      <pageArea>
        <medium short="215.9mm" long="279.4mm"/>
        <contentArea x="18mm" y="18mm" w="180mm" h="240mm"/>
      </pageArea>
    </pageSet>
    <subform name="Body" layout="tb" w="180mm">
      <subform layout="lr-tb" w="180mm">
        <field name="Who" w="100mm" h="8mm">
          <ui><textEdit>
            <border>
              <edge presence="hidden"/>
              <edge presence="hidden"/>
              <edge/>
              <edge presence="hidden"/>
            </border>
          </textEdit></ui>
          <font typeface="Courier New" size="10pt"/>
          <para vAlign="bottom"/>
          <caption reserve="8mm">
            <font typeface="Arial" size="10pt"/>
            <para vAlign="bottom"/>
            <value><text>I,</text></value>
          </caption>
        </field>
        <draw name="Rest" w="80mm" h="8mm">
          <font typeface="Arial" size="10pt"/>
          <para vAlign="bottom"/>
          <value><text>, do declare the rest of the sentence here.</text></value>
        </draw>
      </subform>
      <subform layout="lr-tb" w="180mm">
        <draw name="Question" w="150mm" h="8mm">
          <font typeface="Arial" size="9pt"/>
          <para vAlign="middle"/>
          <margin leftInset="2mm" rightInset="2mm"/>
          <value><text>Question that must stay left of the boxes</text></value>
        </draw>
        <exclGroup name="Answer" layout="lr-tb">
          <field name="Yes" w="8mm" h="8mm">
            <ui><checkButton mark="check"><border><edge/></border></checkButton></ui>
            <para vAlign="middle"/>
            <margin leftInset="1mm" rightInset="1mm"/>
          </field>
          <field name="No" w="22mm" h="8mm">
            <ui><checkButton mark="check"><border><edge/></border></checkButton></ui>
            <para vAlign="middle"/>
            <margin leftInset="1mm" rightInset="12mm"/>
          </field>
        </exclGroup>
      </subform>
      <draw name="Head" w="180mm" h="10mm">
        <font typeface="Arial" size="10pt" weight="bold"/>
        <value><text>Heading line that stays in its own box</text></value>
      </draw>
      <draw name="BodyText" w="180mm" h="10mm">
        <font typeface="Arial" size="10pt"/>
        <value><text>Body line that must not collide with the heading above it.</text></value>
      </draw>
    </subform>
  </subform>
</template>
"""


def _page_streams(path: Path) -> list[bytes]:
    pdf = pikepdf.open(path)
    found = []
    for page in pdf.pages:
        contents = page.get("/Contents")
        if contents is None:
            found.append(b"")
        elif isinstance(contents, pikepdf.Array):
            found.append(b"".join(item.read_bytes() for item in contents))
        else:
            found.append(contents.read_bytes())
    pdf.close()
    return found


class SampleTests(unittest.TestCase):
    def test_harbor_lays_out_two_pages(self):
        pdf = pikepdf.open(HARBOR)
        pages = load_document(pdf)
        pdf.close()
        self.assertEqual(len(pages), 2)
        paths = [block.path for page in pages for block in page.blocks if block.path]
        self.assertIn("PicnicSignup.Sheet.GuestName", paths)
        self.assertIn("PicnicSignup.Sheet.Dish", paths)
        self.assertIn("PicnicSignup.NotesPage.Note", paths)
        labels = [block.text for page in pages for block in page.blocks]
        self.assertIn("Page 1 of 2", labels)
        self.assertIn("Page 2 of 2", labels)
        self.assertTrue(any("Harbor Picnic Signup" in text for text in labels))

    def test_harbor_save_keeps_pages_and_template(self):
        before = _page_streams(HARBOR)
        pdf = pikepdf.open(HARBOR)
        template = read_packets(pdf)["template"]
        pdf.close()
        self.assertIn(b"PicnicSignup", template)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "filled.pdf"
            save_filled(str(HARBOR), str(dest), FILLED)
            self.assertEqual(_page_streams(dest), before)
            saved = pikepdf.open(dest)
            blob = saved.Root.AcroForm.XFA.read_bytes()
            saved.close()
            self.assertIn(template, blob)
            values = read_back(str(dest))
        self.assertEqual(values.get("PicnicSignup.Sheet.GuestName"), "River Quinn")
        self.assertEqual(values.get("PicnicSignup.Sheet.Dish"), "Salad")
        self.assertEqual(values.get("PicnicSignup.NotesPage.Note"), "I will bring cups.")

    def test_layout_check_accepts_the_harbor_form(self):
        pdf = pikepdf.open(HARBOR)
        pages = load_document(pdf)
        pdf.close()
        for page in pages:
            self.assertEqual(check_page(page, FILLED, scale=8), [])
            self.assertEqual(check_page(page, {}, scale=6), [])

    def test_layout_check_flags_overlap_and_margin(self):
        page = Page(number=1, title="P", page_w=80, page_h=80, content_w=80, content_h=80)
        page.blocks = [
            Block(kind="text", text="Alpha overlaps Beta", x=10, y=10, w=50, h=8, font_size=11),
            Block(kind="text", text="Alpha overlaps Beta", x=12, y=11, w=50, h=8, font_size=11),
        ]
        issues = check_page(page, {}, scale=8)
        self.assertTrue(any(item.startswith("overlap:") for item in issues))
        wide = Page(number=1, title="P", page_w=40, page_h=40, content_w=40, content_h=40)
        wide.blocks = [
            Block(
                kind="text",
                text="Supercalifragilisticexpialidocious",
                x=5, y=5, w=12, h=8, font_size=11,
            ),
        ]
        issues = check_page(wide, {}, scale=8)
        self.assertTrue(any(item.startswith("past margin:") for item in issues))

    def test_inline_blank_and_check_use_template_boxes(self):
        pages = layout_template(INLINE.encode("utf-8"))
        self.assertEqual(len(pages), 1)
        page = pages[0]
        who = next(block for block in page.blocks if block.path.endswith("Who"))
        rest = next(block for block in page.blocks if block.text.startswith(", do declare"))
        yes = next(block for block in page.blocks if block.path.endswith("Yes"))
        no = next(block for block in page.blocks if block.path.endswith("No"))
        self.assertEqual(who.cap_place, "left")
        self.assertAlmostEqual(who.cap_reserve, 8.0, places=2)
        widget = input_box(who)
        self.assertAlmostEqual(widget[0], who.x + 8.0, places=2)
        self.assertAlmostEqual(widget[2], who.w - 8.0, places=2)
        self.assertTrue(who.ui_edges[2][0])
        self.assertLess(abs((who.x + who.w) - rest.x), 0.4)
        self.assertLess(abs(who.y - rest.y), 0.4)
        from oxbow.render import _mark_box
        yes_mark = _mark_box(yes)
        no_mark = _mark_box(no)
        self.assertIsNotNone(yes_mark)
        self.assertIsNotNone(no_mark)
        self.assertAlmostEqual(yes_mark[2], no_mark[2], places=2)
        self.assertAlmostEqual(yes.mark_size, 10 * 25.4 / 72.0, places=2)
        self.assertGreaterEqual(yes_mark[0], yes.x - 0.01)
        self.assertLessEqual(yes_mark[0] + yes_mark[2], yes.x + yes.w + 0.01)
        self.assertGreaterEqual(no_mark[0], no.x - 0.01)
        self.assertLessEqual(no_mark[0] + no_mark[2], no.x + no.w + 0.01)
        self.assertEqual(check_page(page, {}, scale=8), [])

    def test_club_note_is_an_ordinary_pdf(self):
        pdf = pikepdf.open(CLUB)
        self.assertEqual(load_document(pdf), [])
        pages = load_static_pages(pdf)
        pdf.close()
        self.assertEqual(len(pages), 1)
        paths = [block.path for block in pages[0].blocks]
        self.assertEqual(paths, ["p1.Message"])
        self.assertEqual(pages[0].blocks[0].ui, "textEdit")


SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/">
  <subform name="Sheet" layout="tb" w="180mm">
    <pageSet>
      <pageArea>
        <medium short="215.9mm" long="279.4mm"/>
        <contentArea x="18mm" y="18mm" w="180mm" h="240mm"/>
      </pageArea>
    </pageSet>
    <subform name="Body" layout="tb" w="180mm">
      <draw name="Head" w="180mm" h="6mm">
        <border>
          <edge/>
          <edge/>
          <edge presence="hidden"/>
          <edge/>
        </border>
        <value><text>7. Education</text></value>
      </draw>
      <subform name="Grid" layout="table" columnWidths="40mm 40mm">
        <margin leftInset="6mm" rightInset="2mm" bottomInset="2mm"/>
        <border>
          <edge presence="hidden"/>
          <edge/>
          <edge/>
          <edge/>
        </border>
        <subform name="Header" layout="row">
          <draw name="From" w="40mm" h="8mm">
            <border><edge/></border>
            <value><text>From</text></value>
          </draw>
          <draw name="To" w="40mm" h="8mm">
            <border><edge/></border>
            <value><text>To</text></value>
          </draw>
        </subform>
      </subform>
      <field name="Who" w="120mm" h="8mm">
        <ui><textEdit>
          <border>
            <edge presence="hidden"/>
            <edge presence="hidden"/>
            <edge/>
            <edge presence="hidden"/>
          </border>
        </textEdit></ui>
        <font typeface="Arial" size="8pt"/>
        <para vAlign="bottom"/>
        <margin leftInset="6mm" topInset="0.8mm" rightInset="1mm" bottomInset="1.5mm"/>
        <caption placement="left" reserve="3mm">
          <font typeface="Arial" size="8pt"/>
          <para vAlign="bottom"/>
          <value><text>I,</text></value>
        </caption>
      </field>
      <field name="Family" w="80mm" h="10mm">
        <ui><textEdit>
          <border>
            <edge presence="hidden"/>
            <edge presence="hidden"/>
            <edge/>
            <edge presence="hidden"/>
          </border>
        </textEdit></ui>
        <para vAlign="middle"/>
        <margin leftInset="1.27mm" topInset="0.8mm" rightInset="1.27mm" bottomInset="1.27mm"/>
        <caption placement="top" reserve="3mm">
          <para vAlign="middle"/>
          <value><text>Family name</text></value>
        </caption>
      </field>
    </subform>
  </subform>
</template>
"""


class BorderAndCaptionTests(unittest.TestCase):
    def test_table_border_closes_on_the_section_rectangle(self):
        pages = layout_template(SECTION.encode("utf-8"))
        self.assertEqual(len(pages), 1)
        page = pages[0]
        head = next(block for block in page.blocks if block.text == "7. Education")
        grid = [
            block for block in page.blocks
            if block.edges and len(block.edges) >= 4 and not block.edges[0][0] and block.edges[2][0]
            and abs(block.w - 180) < 0.2 and block.kind == "text" and not block.text
        ]
        self.assertEqual(len(grid), 1)
        border = grid[0]
        self.assertAlmostEqual(border.x, head.x, places=2)
        self.assertAlmostEqual(border.w, head.w, places=2)
        self.assertAlmostEqual(border.y, head.y + head.h, places=2)
        cell = next(block for block in page.blocks if block.text == "From")
        self.assertAlmostEqual(cell.x, border.x + 6.0, places=2)
        self.assertGreater(cell.x, border.x + 1)
        self.assertLess(cell.x + cell.w, border.x + border.w)
        self.assertEqual(border_overshoots(page), [])
        self.assertEqual(check_page(page, {}, scale=8), [])

    def test_ruled_caption_keeps_the_template_gap(self):
        pages = layout_template(SECTION.encode("utf-8"))
        page = pages[0]
        who = next(block for block in page.blocks if block.path.endswith("Who"))
        family = next(block for block in page.blocks if block.path.endswith("Family"))
        widget = input_box(who)
        self.assertAlmostEqual(widget[0], who.x + 3.0, places=2)
        ruled = rule_box(who)
        self.assertAlmostEqual(ruled[0], who.x + 6.0 + 3.0, places=2)
        self.assertAlmostEqual(ruled[0] + ruled[2], widget[0] + widget[2], places=2)
        cap = caption_box(who)
        self.assertAlmostEqual(cap[0], who.x + 6.0, places=2)
        insets = caption_insets(who, cap)
        self.assertAlmostEqual(insets[1], 0.8, places=2)
        self.assertAlmostEqual(insets[3], 1.5, places=2)
        self.assertEqual(insets[0], 0.0)
        family_cap = caption_box(family)
        self.assertEqual(caption_insets(family, family_cap), (0.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(rule_box(family)[0], input_box(family)[0], places=2)
        self.assertEqual(check_page(page, {}, scale=8), [])

    def test_open_vertical_is_an_overshoot(self):
        page = Page(number=1, title="P", page_w=80, page_h=80)
        page.blocks = [
            Block(
                kind="text", x=10, y=10, w=40, h=12,
                edges=(
                    (True, 0.2, (0, 0, 0)),
                    (False, 0.2, (0, 0, 0)),
                    (False, 0.2, (0, 0, 0)),
                    (True, 0.2, (0, 0, 0)),
                ),
            ),
        ]
        issues = border_overshoots(page)
        self.assertTrue(any(item.startswith("overshoot:") for item in issues))

    def test_blue_fill_and_stroke_are_not_painted(self):
        page = Page(number=1, title="P", page_w=80, page_h=40)
        page.blocks = [
            Block(
                kind="text", text="Hello", x=10, y=8, w=40, h=12,
                fill=(170, 210, 255),
                edges=(
                    (True, 0.4, (0, 0, 255)),
                    (True, 0.4, (0, 0, 255)),
                    (True, 0.4, (0, 0, 255)),
                    (True, 0.4, (0, 0, 255)),
                ),
            ),
        ]
        image = render_page(page, {}, px_per_mm=4)
        raw = image.tobytes()
        blue = 0
        for index in range(0, len(raw), 3):
            red, green, band = raw[index], raw[index + 1], raw[index + 2]
            if band > red + 15 and band > green + 10 and band > 140:
                blue += 1
        self.assertEqual(blue, 0)
        self.assertEqual(border_overshoots(page), [])


class LogoAndFitTests(unittest.TestCase):
    def test_scaled_logo_fits_its_slot_and_leaves_the_file(self):
        path = ROOT / "assets" / "logo.png"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        raw = Image.open(path)
        self.assertGreater(max(raw.size), 64)
        raw.close()
        mark = scaled_logo(48)
        self.assertEqual(mark.size, (48, 48))
        small = scaled_logo(32)
        self.assertEqual(small.size, (32, 32))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        wide = fit_image(Image.new("RGBA", (100, 40), (20, 72, 94, 255)), 20)
        self.assertEqual(wide.size, (20, 20))
        self.assertEqual(wide.getpixel((10, 0))[3], 0)
        self.assertGreater(wide.getpixel((10, 10))[3], 0)

    def test_fit_zoom_keeps_the_page_inside_the_window(self):
        wide = (4700, 400)
        ppm = fit_ppm(wide[0], wide[1], PAGE_W, PAGE_H, 1.0)
        self.assertIsNotNone(ppm)
        self.assertLessEqual(ppm * PAGE_W, wide[0] - 28)
        self.assertLessEqual(ppm * PAGE_H, wide[1] - 28)
        small = fit_ppm(640, 480, PAGE_W, PAGE_H, 1.0)
        self.assertLessEqual(small * PAGE_W, 640 - 28)
        self.assertLessEqual(small * PAGE_H, 480 - 28)
        fitted = fit_ppm(1400, 1800, PAGE_W, PAGE_H, 1.0)
        zoomed = fit_ppm(1400, 1800, PAGE_W, PAGE_H, 1.2)
        self.assertGreater(zoomed, fitted)
        self.assertIsNone(fit_ppm(20, 800, PAGE_W, PAGE_H, 1.0))
        tiny = fit_ppm(80, 80, PAGE_W, PAGE_H, 1.0)
        self.assertLessEqual(tiny * PAGE_W, 80 - 28)
        self.assertLessEqual(tiny * PAGE_H, 80 - 28)


if __name__ == "__main__":
    unittest.main()
