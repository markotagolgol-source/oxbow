"""Behavior of the synthetic sample PDFs. No personal forms."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from oxbow.fill import read_back, save_filled
from oxbow.layout import load_document, load_static_pages
from oxbow.xfa import read_packets

ROOT = Path(__file__).resolve().parents[1]
HARBOR = ROOT / "examples" / "harbor-picnic.pdf"
CLUB = ROOT / "examples" / "club-note.pdf"

FILLED = {
    "PicnicSignup.Sheet.GuestName": "River Quinn",
    "PicnicSignup.Sheet.Dish": "Salad",
    "PicnicSignup.NotesPage.Note": "I will bring cups.",
}


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

    def test_club_note_is_an_ordinary_pdf(self):
        pdf = pikepdf.open(CLUB)
        self.assertEqual(load_document(pdf), [])
        pages = load_static_pages(pdf)
        pdf.close()
        self.assertEqual(len(pages), 1)
        paths = [block.path for block in pages[0].blocks]
        self.assertEqual(paths, ["p1.Message"])
        self.assertEqual(pages[0].blocks[0].ui, "textEdit")


if __name__ == "__main__":
    unittest.main()
