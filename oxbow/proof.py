"""Headless proof: page count, three filled fields, original pages preserved."""
from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
from PIL import ImageChops, ImageDraw

from . import extras
from .fill import read_back, save_filled
from .layout import load_document
from .render import PRINT_PPM, input_box, render_page
from .xfa import read_packets


def first_text_fields(pages, count=3):
    found = []
    for page in pages:
        for block in page.blocks:
            if block.kind == "field" and block.ui in ("textEdit", "picture", "dateTimeEdit") and not block.read_only:
                found.append(block)
                if len(found) == count:
                    return found
    return found


def _xfa_blob(pdf) -> bytes:
    xfa = pdf.Root.AcroForm.XFA
    if isinstance(xfa, pikepdf.Stream):
        return xfa.read_bytes()
    if isinstance(xfa, pikepdf.Array):
        chunks = []
        for index in range(0, len(xfa), 2):
            chunks.append(xfa[index + 1].read_bytes())
        return b"".join(chunks)
    return b""


def _page_streams(path: Path) -> list[bytes]:
    pdf = pikepdf.open(str(path))
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


def _mask_widgets(image, page, blocks):
    painted = image.copy()
    draw = ImageDraw.Draw(painted)
    scale = image.width / max(page.page_w, 0.1)
    pad = scale * 1.0
    for block in blocks:
        if block.page != page.number - 1:
            continue
        x, y, w, h = input_box(block)
        draw.rectangle(
            (x * scale - pad, y * scale - pad, (x + w) * scale + pad, (y + h) * scale + pad),
            fill=(255, 255, 255),
        )
    return painted


def run(src: Path, out_dir: Path, label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pikepdf.open(str(src))
    template = read_packets(pdf)["template"]
    pdf.close()
    pdf = pikepdf.open(str(src))
    pages = load_document(pdf)
    pdf.close()
    if not pages:
        raise SystemExit(f"{label}: no XFA pages laid out")
    print(label, "PAGE COUNT", len(pages))
    for page in pages:
        bottoms = [block.y + block.h for block in page.blocks]
        sample = next((block.text.split("\n")[0][:48] for block in page.blocks if block.kind == "text" and block.text), "")
        print(
            f"  page {page.number} blocks {len(page.blocks)} "
            f"fields {sum(1 for block in page.blocks if block.kind == 'field')} "
            f"content {page.content_y:.1f}+{page.content_h:.1f} "
            f"lowest {max(bottoms) if bottoms else 0:.1f} {sample!r}"
        )
    fields = first_text_fields(pages, 3)
    if len(fields) < 3:
        raise SystemExit(f"{label}: only found {len(fields)} text fields")
    samples = ["OXBOW-ONE", "OXBOW-TWO", "2020-04"]
    values = {fields[i].path: samples[i] for i in range(3)}
    titles = [page.title for page in pages]
    print(" titles", titles)
    if titles != [f"Page {i + 1}" for i in range(len(titles))]:
        raise SystemExit(f"{label} page titles {titles}")
    for index, page in enumerate(pages):
        render_page(page, {}, PRINT_PPM).save(out_dir / f"{label}-p{index + 1}.png")
    before = render_page(pages[fields[0].page], {}, PRINT_PPM)
    before_path = out_dir / f"{label}-before.png"
    before.save(before_path)
    saved = out_dir / f"{label}-filled.pdf"
    save_filled(str(src), str(saved), values)
    original_streams = _page_streams(src)
    saved_streams = _page_streams(saved)
    if original_streams != saved_streams:
        raise SystemExit(f"{label}: PDF page content streams changed")
    saved_pdf = pikepdf.open(str(saved))
    blob = _xfa_blob(saved_pdf)
    saved_pdf.close()
    if template not in blob:
        raise SystemExit(f"{label}: saved XFA does not contain the original template bytes")
    back = read_back(str(saved))
    missing = {key: values[key] for key in values if back.get(key) != values[key]}
    pdf2 = pikepdf.open(str(saved))
    pages2 = load_document(pdf2)
    pdf2.close()
    if len(pages2) != len(pages):
        raise SystemExit(f"{label}: reopened page count {len(pages2)} != {len(pages)}")
    for index, (page, page2) in enumerate(zip(pages, pages2)):
        same_a = render_page(page, values, PRINT_PPM)
        same_b = render_page(page2, values, PRINT_PPM)
        if same_a.tobytes() != same_b.tobytes():
            raise SystemExit(f"{label}: layout of saved file differs on page {index + 1}")
        blank = render_page(page, {}, PRINT_PPM)
        filled = render_page(page2, back, PRINT_PPM)
        if blank.size != filled.size:
            raise SystemExit(f"{label}: render size changed on page {index + 1}")
        raw = ImageChops.difference(blank, filled)
        masked = ImageChops.difference(
            _mask_widgets(blank, page, fields),
            _mask_widgets(filled, page2, fields),
        )
        if index == fields[0].page and raw.getbbox() is None:
            raise SystemExit(f"{label}: filled values did not change the rendered page")
        if masked.getbbox() is not None:
            raise SystemExit(f"{label}: page {index + 1} differs outside the filled fields {masked.getbbox()}")
    after = render_page(pages2[fields[0].page], back, PRINT_PPM)
    after_path = out_dir / f"{label}-after.png"
    after.save(after_path)
    changed = before.tobytes() != after.tobytes()
    report = {
        "label": label,
        "pages": len(pages),
        "fields": sum(1 for p in pages for b in p.blocks if b.kind == "field"),
        "filled": values,
        "missing": missing,
        "pixels_changed": changed,
        "saved": str(saved),
        "before": str(before_path),
        "after": str(after_path),
        "reopened_pages": len(pages2),
    }
    print(label, "pages", report["pages"], "fields", report["fields"])
    for key, value in values.items():
        print(" ", value, "->", key, "read", back.get(key))
    print(" pixels_changed", changed, "reopened", len(pages2), "missing", missing)
    if missing or not changed or not pages2:
        raise SystemExit(f"{label} proof failed")
    pdf = pikepdf.open(str(saved), allow_overwriting_input=True)
    extras.save_notes(pdf, [{"page": 1, "text": "bring cups"}])
    pdf.save(str(saved))
    pdf.close()
    if _page_streams(saved) != original_streams:
        raise SystemExit(f"{label}: notes save changed page content streams")
    pdf = pikepdf.open(str(saved))
    notes = extras.load_notes(pdf)
    still = template in _xfa_blob(pdf)
    pdf.close()
    if notes != [{"page": 1, "text": "bring cups"}]:
        raise SystemExit(f"{label} notes did not round-trip: {notes}")
    if not still:
        raise SystemExit(f"{label}: template bytes missing after notes save")
    back2 = read_back(str(saved))
    missing2 = {key: values[key] for key in values if back2.get(key) != values[key]}
    if missing2:
        raise SystemExit(f"{label} values lost after notes save: {missing2}")
    sig = extras.signature_status(str(src))
    print(" signature", sig.splitlines()[0] if sig else "(empty)")
    return report


def main():
    example = Path(__file__).resolve().parents[1] / "examples" / "harbor-picnic.pdf"
    sources = [Path(item) for item in sys.argv[1:]] or [example]
    out = Path("/tmp/oxbow-proof")
    for src in sources:
        run(src, out, src.stem)
    print("PROOF OK")


if __name__ == "__main__":
    main()
