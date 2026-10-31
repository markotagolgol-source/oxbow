"""Draw a page from the template's own boxes, or overlay fields on a real PDF page.

XFA pages are white. Text, rules, and pictures come from the template blocks.
An ordinary PDF passes its Poppler render as `background`; only filled or
active fields are painted on top of that image.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .layout import PAGE_H, PAGE_W, Page

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# One render at this density is a letter page at 150 DPI. Print uses it.
# The window passes the canvas pixel size instead, so the page is not scaled again.
PRINT_PPM = 150 / 25.4

_FONT_CACHE: dict = {}
_IMAGE_CACHE: dict = {}
_FACE_FILES: dict | None = None

_ACTIVE = (196, 78, 22)
_MIN_WIDGET = 3.0


def ensure_logo(size: int = 64) -> Path:
    """Draw a stand-in mark. A real logo.png in assets/ replaces it."""
    ASSETS.mkdir(parents=True, exist_ok=True)
    target = ASSETS / "logo.png"
    if target.exists():
        return target
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, size - 2, size - 2), radius=12, fill=(20, 72, 94, 255))
    draw.arc((8, 18, size - 8, size - 10), 200, 340, fill=(236, 228, 206, 255), width=4)
    draw.arc((14, 26, size - 18, size - 6), 20, 160, fill=(126, 184, 168, 255), width=3)
    image.save(target)
    return target


def _face_files() -> dict:
    global _FACE_FILES
    if _FACE_FILES is not None:
        return _FACE_FILES
    roots = (
        "/usr/share/fonts/truetype/liberation2",
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/freefont",
    )
    found = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if name.lower().endswith((".ttf", ".otf")):
                found.setdefault(name, os.path.join(root, name))
    _FACE_FILES = found
    return found


def _pick_file(family: str, bold: bool, italic: bool) -> str | None:
    files = _face_files()
    mono = any(token in (family or "").lower() for token in ("courier", "mono", "consol"))
    serif = any(token in (family or "").lower() for token in ("times", "georgia", "serif"))
    if mono:
        table = {
            (False, False): ("LiberationMono-Regular.ttf", "DejaVuSansMono.ttf", "FreeMono.ttf"),
            (True, False): ("LiberationMono-Bold.ttf", "DejaVuSansMono-Bold.ttf", "FreeMonoBold.ttf"),
            (False, True): ("LiberationMono-Italic.ttf", "DejaVuSansMono-Oblique.ttf", "FreeMonoOblique.ttf"),
            (True, True): ("LiberationMono-BoldItalic.ttf", "DejaVuSansMono-BoldOblique.ttf", "FreeMonoBoldOblique.ttf"),
        }
    elif serif:
        table = {
            (False, False): ("LiberationSerif-Regular.ttf", "DejaVuSerif.ttf", "FreeSerif.ttf"),
            (True, False): ("LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf", "FreeSerifBold.ttf"),
            (False, True): ("LiberationSerif-Italic.ttf", "DejaVuSerif-Italic.ttf", "FreeSerifItalic.ttf"),
            (True, True): ("LiberationSerif-BoldItalic.ttf", "DejaVuSerif-BoldItalic.ttf", "FreeSerifBoldItalic.ttf"),
        }
    else:
        table = {
            (False, False): ("LiberationSans-Regular.ttf", "DejaVuSans.ttf", "FreeSans.ttf"),
            (True, False): ("LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf", "FreeSansBold.ttf"),
            (False, True): ("LiberationSans-Italic.ttf", "DejaVuSans-Oblique.ttf", "FreeSansOblique.ttf"),
            (True, True): ("LiberationSans-BoldItalic.ttf", "DejaVuSans-BoldOblique.ttf", "FreeSansBoldOblique.ttf"),
        }
    for name in table[(bool(bold), bool(italic))]:
        if name in files:
            return files[name]
    return None


def font(size: int, bold: bool = False, italic: bool = False, family: str = "Arial"):
    size = max(1, int(size))
    key = (family or "Arial", size, bool(bold), bool(italic))
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    path = _pick_file(family, bold, italic)
    face = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    _FONT_CACHE[key] = face
    return face


def input_box(block) -> tuple[float, float, float, float]:
    """Editable rectangle inside a field, in millimetres: x, y, w, h.

    The caption reserve sits inside the field. The widget is the remainder.
    """
    x, y, w, h = block.x, block.y, block.w, block.h
    place = (getattr(block, "cap_place", "") or "").lower()
    reserve = float(getattr(block, "cap_reserve", 0) or 0)
    if getattr(block, "kind", "") == "button" or not place or reserve <= 0:
        return x, y, w, h
    if place == "top":
        reserve = min(reserve, max(0.0, h - _MIN_WIDGET))
        return x, y + reserve, w, max(_MIN_WIDGET, h - reserve)
    if place == "bottom":
        reserve = min(reserve, max(0.0, h - _MIN_WIDGET))
        return x, y, w, max(_MIN_WIDGET, h - reserve)
    if place == "left":
        reserve = min(reserve, max(0.0, w - _MIN_WIDGET))
        return x + reserve, y, max(_MIN_WIDGET, w - reserve), h
    if place == "right":
        reserve = min(reserve, max(0.0, w - _MIN_WIDGET))
        return x, y, max(_MIN_WIDGET, w - reserve), h
    return x, y, w, h


def render_page(
    page: Page,
    values: dict[str, str],
    px_per_mm: float = PRINT_PPM,
    highlight: str = "",
    active_path: str = "",
    background: Image.Image | None = None,
    for_print: bool = False,
) -> Image.Image:
    """Draw the page once at px_per_mm. Callers display the image at 1:1."""
    page_w = page.page_w or PAGE_W
    page_h = page.page_h or PAGE_H
    if background is not None:
        image = background.convert("RGB")
        scale = image.width / max(page_w, 0.1)
    else:
        scale = max(0.4, float(px_per_mm))
        width = max(1, int(round(page_w * scale)))
        height = max(1, int(round(page_h * scale)))
        image = Image.new("RGB", (width, height), (255, 255, 255))
        ImageDraw.Draw(image).rectangle((0, 0, width - 1, height - 1), outline=(188, 188, 188))
    draw = ImageDraw.Draw(image)
    later = []
    for block in page.blocks:
        if block.screen_only and for_print:
            continue
        if block.print_only and not for_print:
            continue
        if background is not None and block.kind != "field":
            continue
        _draw_block(image, draw, block, values, scale, active_path, background is not None)
        if highlight and highlight.lower() in " ".join((block.text, values.get(block.path, ""), block.path)).lower():
            later.append((block, "find"))
        if active_path and block.path == active_path and block.kind == "field":
            later.append((block, "active"))
    for block, kind in later:
        rect = input_box(block) if kind == "active" else (block.x, block.y, block.w, block.h)
        x, y, w, h = rect
        color = _ACTIVE if kind == "active" else (170, 70, 30)
        width = max(2, int(round(scale * (0.45 if kind == "active" else 0.28))))
        draw.rectangle(
            (_px(x, scale), _px(y, scale), _px(x + w, scale), _px(y + h, scale)),
            outline=color,
            width=width,
        )
    return image


def _px(mm: float, scale: float) -> int:
    return int(round(mm * scale))


def _draw_block(image, draw, block, values, scale, active_path, overlay: bool):
    value = values.get(block.path, "") if block.path else ""
    active = bool(active_path) and block.path == active_path and block.kind == "field"
    if overlay:
        _draw_overlay_field(draw, block, value, active, scale)
        return
    x, y, w, h = block.x, block.y, block.w, block.h
    if w <= 0 or h <= 0:
        return
    px, py, pw, ph = _px(x, scale), _px(y, scale), _px(w, scale), _px(h, scale)
    if pw < 1 or ph < 1:
        return
    if block.fill and block.kind != "field":
        draw.rectangle((px, py, px + pw, py + ph), fill=block.fill)
    elif block.kind == "field" and block.ui not in ("checkButton", "radio"):
        draw.rectangle((px, py, px + pw, py + ph), fill=block.fill or (255, 255, 255))
    if block.kind == "image" or (block.image and block.kind != "field"):
        _paste_image(image, block, scale)
    _stroke(draw, px, py, pw, ph, block.edges, scale)
    if block.kind == "button":
        _text_in(
            draw, block.text, block.cap_family or block.font_family, block.cap_size or block.font_size,
            block.cap_bold, False, (0, 0, 0), (px, py, px + pw, py + ph),
            "center", "middle", (0.4, 0.3, 0.4, 0.3), scale,
        )
        return
    if block.kind == "field" and block.cap_reserve and block.cap_place and block.text:
        _draw_caption(draw, block, scale)
    if block.kind == "field" and block.ui in ("checkButton", "radio"):
        _draw_mark(draw, block, value, scale)
        return
    if block.kind == "field":
        if active or not value:
            if block.ui == "choiceList":
                _chevron(draw, block, scale)
            return
        shown = value
        if block.ui == "choiceList":
            for label, saved in block.options:
                if saved == value:
                    shown = label
                    break
            _chevron(draw, block, scale)
        box = input_box(block)
        _text_in(
            draw, shown, block.font_family, block.font_size, block.bold, block.italic,
            block.color or (0, 0, 0),
            (_px(box[0], scale), _px(box[1], scale), _px(box[0] + box[2], scale), _px(box[1] + box[3], scale)),
            block.align or "left", block.valign or "middle", block.insets, scale,
        )
        return
    if block.kind == "text" and block.text:
        _text_in(
            draw, block.text, block.font_family, block.font_size, block.bold, block.italic,
            block.color or (0, 0, 0), (px, py, px + pw, py + ph),
            block.align or "left", block.valign or "top", block.insets, scale,
        )
    elif block.kind == "image" and not block.image and block.image_href:
        draw.rectangle((px, py, px + pw, py + ph), outline=(210, 210, 210))


def _draw_overlay_field(draw, block, value, active, scale):
    """Paint a widget on a Poppler page only when the screen must show a change."""
    baseline = getattr(block, "baseline", "") or ""
    if block.ui in ("checkButton", "radio"):
        if not active and value == baseline:
            return
        if active or value:
            _draw_mark(draw, block, value, scale)
            if active:
                box = input_box(block)
                draw.rectangle(
                    (_px(box[0], scale), _px(box[1], scale), _px(box[0] + box[2], scale), _px(box[1] + box[3], scale)),
                    outline=_ACTIVE, width=max(2, int(round(scale * 0.4))),
                )
        return
    if not value and not active:
        return
    box = input_box(block)
    x0, y0 = _px(box[0], scale), _px(box[1], scale)
    x1, y1 = _px(box[0] + box[2], scale), _px(box[1] + box[3], scale)
    if active:
        draw.rectangle((x0, y0, x1, y1), outline=_ACTIVE, width=max(2, int(round(scale * 0.4))))
        return
    draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 255))
    shown = value
    if block.ui == "choiceList":
        for label, saved in block.options:
            if saved == value:
                shown = label
                break
    _text_in(
        draw, shown, block.font_family, block.font_size or 10, block.bold, block.italic,
        (0, 0, 0), (x0, y0, x1, y1), "left", "middle", (0.6, 0.4, 0.4, 0.4), scale,
    )


def _draw_caption(draw, block, scale):
    x, y, w, h = block.x, block.y, block.w, block.h
    reserve = min(block.cap_reserve, h if block.cap_place in ("top", "bottom") else w)
    place = block.cap_place
    if place == "top":
        box = (x, y, w, reserve)
    elif place == "bottom":
        box = (x, y + h - reserve, w, reserve)
    elif place == "left":
        box = (x, y, reserve, h)
    elif place == "right":
        box = (x + w - reserve, y, reserve, h)
    else:
        return
    _text_in(
        draw, block.text, block.cap_family or "Arial", block.cap_size or 8, block.cap_bold, False,
        (0, 0, 0),
        (_px(box[0], scale), _px(box[1], scale), _px(box[0] + box[2], scale), _px(box[1] + box[3], scale)),
        block.cap_align or "left", block.cap_valign or "middle", (0.4, 0.2, 0.4, 0.15), scale,
    )


def _draw_mark(draw, block, value, scale):
    """Square mark. These templates use checkButton, including inside exclGroups."""
    box = input_box(block)
    side = min(box[2], box[3])
    if side <= 0:
        return
    if box[2] > box[3] * 1.35:
        gx, gy = box[0], box[1] + (box[3] - side) / 2.0
    else:
        gx = box[0] + (box[2] - side) / 2.0
        gy = box[1] + (box[3] - side) / 2.0
    pad = min(0.35, side * 0.08)
    x0, y0 = _px(gx + pad, scale), _px(gy + pad, scale)
    x1, y1 = _px(gx + side - pad, scale), _px(gy + side - pad, scale)
    stroke = max(1, int(round(scale * 0.2)))
    color = (20, 20, 20)
    # exclGroup children are still checkButtons in these templates: a square, not a disc.
    draw.rectangle((x0, y0, x1, y1), outline=color, width=stroke, fill=(255, 255, 255))
    if value and value == block.on_value:
        mid_x = x0 + (x1 - x0) // 3
        draw.line((x0 + stroke + 1, y0 + (y1 - y0) // 2, mid_x, y1 - stroke - 1), fill=color, width=stroke + 1)
        draw.line((mid_x, y1 - stroke - 1, x1 - stroke - 1, y0 + stroke + 2), fill=color, width=stroke + 1)


def _chevron(draw, block, scale):
    box = input_box(block)
    side = min(3.2, max(1.6, box[3] * 0.45))
    cx = box[0] + box[2] - side - 0.6
    cy = box[1] + box[3] / 2.0
    pts = [
        (_px(cx, scale), _px(cy - side * 0.28, scale)),
        (_px(cx + side * 0.55, scale), _px(cy - side * 0.28, scale)),
        (_px(cx + side * 0.27, scale), _px(cy + side * 0.28, scale)),
    ]
    draw.polygon(pts, fill=(40, 40, 40))


def _stroke(draw, x, y, w, h, edges, scale):
    if not edges:
        return
    sides = list(edges[:4])
    while len(sides) < 4:
        sides.append(sides[-1] if sides else (False, 0.2, (0, 0, 0)))
    lines = (
        (sides[0], (x, y, x + w, y)),
        (sides[1], (x + w, y, x + w, y + h)),
        (sides[2], (x, y + h, x + w, y + h)),
        (sides[3], (x, y, x, y + h)),
    )
    for (visible, thick, color), coords in lines:
        if not visible:
            continue
        width = max(1, int(thick * scale))
        draw.line(coords, fill=color, width=width)


def _paste_image(image, block, scale):
    if not block.image:
        return
    left, top, right, bottom = block.insets or (0, 0, 0, 0)
    x = block.x + left
    y = block.y + top
    w = max(0.4, block.w - left - right)
    h = max(0.4, block.h - top - bottom)
    pw, ph = max(1, _px(w, scale)), max(1, _px(h, scale))
    key = (len(block.image), block.image[:24], block.image[-12:], pw, ph)
    cached = _IMAGE_CACHE.get(key)
    if cached is None:
        try:
            opened = Image.open(BytesIO(block.image)).convert("RGBA")
            cached = opened.resize((pw, ph), Image.Resampling.LANCZOS)
        except Exception:
            return
        if len(_IMAGE_CACHE) > 32:
            _IMAGE_CACHE.clear()
        _IMAGE_CACHE[key] = cached
    image.paste(cached, (_px(x, scale), _px(y, scale)), cached)


def _length(face, text: str) -> float:
    try:
        return float(face.getlength(text))
    except Exception:
        return float(len(text) * max(getattr(face, "size", 12), 8) * 0.5)


def _clip(text: str, face, max_px: int) -> str:
    if max_px <= 4 or not text:
        return ""
    shown = text
    if _length(face, shown) <= max_px:
        return shown
    while shown and _length(face, shown + "…") > max_px and len(shown) > 1:
        shown = shown[:-1]
    return shown


def _wrap(text: str, face, max_px: int) -> list[str]:
    lines = []
    for para in (text or "").split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            piece = _clip(word, face, max_px)
            trial = piece if not current else current + " " + piece
            if current and _length(face, trial) > max_px:
                lines.append(current)
                current = piece
            else:
                current = trial
        lines.append(current)
    return lines or [""]


def _text_in(draw, text, family, pt, bold, italic, color, box, align, valign, insets, scale):
    if not text:
        return
    x0, y0, x1, y1 = box
    left, top, right, bottom = insets or (0, 0, 0, 0)
    x0 += _px(left, scale)
    y0 += _px(top, scale)
    x1 -= _px(right, scale)
    y1 -= _px(bottom, scale)
    if x1 - x0 < 4 or y1 - y0 < 3:
        return
    size = max(1, int(round(float(pt or 10) * 25.4 / 72.0 * scale)))
    band = y1 - y0
    if size > band and band >= 6:
        size = max(6, band)
    face = font(size, bold, italic, family)
    lines = _wrap(text, face, x1 - x0)
    # A one-line banner that is a hair too wide stays one line. Wrapping the
    # last word ("B") under the classification line is not the template.
    if "\n" not in text and len(lines) > 1 and (y1 - y0) <= size * 2.45:
        trial = size
        while trial > 6 and len(lines) > 1:
            trial -= 1
            face = font(trial, bold, italic, family)
            lines = _wrap(text, face, x1 - x0)
        size = trial
    line_h = max(size + 1, int(round(size * 1.15)))
    block_h = line_h * len(lines)
    if valign == "middle":
        y = y0 + (band - block_h) / 2.0
    elif valign == "bottom":
        y = y1 - block_h
    else:
        y = y0
    align = (align or "left").lower()
    for index, line in enumerate(lines):
        if y > y1:
            break
        if align == "justify" and index < len(lines) - 1 and " " in line.strip():
            _justify(draw, line, face, color, x0, y, x1 - x0)
        else:
            shown = _clip(line, face, x1 - x0)
            wide = _length(face, shown)
            if align in ("center", "middle"):
                x = x0 + max(0, (x1 - x0 - wide) / 2.0)
            elif align == "right":
                x = x1 - wide
            else:
                x = x0
            draw.text((x, y), shown, font=face, fill=color)
        y += line_h


def _justify(draw, line, face, color, x, y, width):
    words = line.split()
    if len(words) < 2:
        draw.text((x, y), _clip(line, face, int(width)), font=face, fill=color)
        return
    widths = [_length(face, word) for word in words]
    gap = (width - sum(widths)) / (len(words) - 1)
    if gap < _length(face, " ") * 0.6 or gap > _length(face, " ") * 4:
        draw.text((x, y), _clip(line, face, int(width)), font=face, fill=color)
        return
    cursor = x
    space = gap
    for word, wide in zip(words, widths):
        draw.text((cursor, y), word, font=face, fill=color)
        cursor += wide + space


def hit_test(page: Page, x_mm: float, y_mm: float):
    fields = [block for block in page.blocks if block.kind == "field" and not block.read_only]
    for block in reversed(fields):
        x, y, w, h = input_box(block)
        if x <= x_mm <= x + w and y <= y_mm <= y + h:
            return block
    for block in reversed(fields):
        if block.x <= x_mm <= block.x + block.w and block.y <= y_mm <= block.y + block.h:
            return block
    return None
