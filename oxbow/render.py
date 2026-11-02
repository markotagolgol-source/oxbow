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


def scaled_logo(size: int) -> Image.Image:
    """Copy of assets/logo.png fitted into a size×size slot. The file is not written."""
    with Image.open(ensure_logo()) as source:
        return fit_image(source, size)


def fit_image(image: Image.Image, size: int) -> Image.Image:
    """Smooth-scale `image` so it sits inside a square slot. Aspect stays locked."""
    size = max(1, int(size))
    fitted = image.convert("RGBA")
    fitted.thumbnail((size, size), Image.Resampling.LANCZOS)
    if fitted.size == (size, size):
        return fitted
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2), fitted)
    return canvas


def fit_ppm(
    canvas_w: float,
    canvas_h: float,
    page_w: float,
    page_h: float,
    zoom: float = 1.0,
) -> float | None:
    """Pixels per millimetre so zoom 1 shows the whole page inside the canvas.

    A larger zoom may scroll. None means the canvas is not on screen yet.
    """
    if canvas_w < 40 or canvas_h < 40 or page_w <= 0 or page_h <= 0:
        return None
    pad = 28
    fit = min((canvas_w - pad) / page_w, (canvas_h - pad) / page_h)
    # Fit shows the whole page. The old fit-width fallback made a short wide
    # window (the giant logo) render a page wider than the visible area.
    ppm = fit * zoom
    cap = 7000 / max(page_w, page_h)
    ppm = min(max(ppm, 0.8), cap)
    if zoom <= 1.0:
        ppm = min(ppm, fit)
    return ppm


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


def _is_blue(color) -> bool:
    """True for a guide blue. Gray, black, and the orange focus stay."""
    if not color or len(color) < 3:
        return False
    red, green, blue = int(color[0]), int(color[1]), int(color[2])
    return blue > red + 15 and blue > green + 10 and blue > 140


def _paint_color(color):
    if not color or _is_blue(color):
        return (0, 0, 0)
    return color


def _edge_on(edges, index: int) -> bool:
    return bool(edges) and len(edges) > index and bool(edges[index][0])


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


def _ruled_caption(block) -> bool:
    """A caption that sits on a ruled line and must keep the template gap."""
    valign = (getattr(block, "cap_valign", "") or "").lower()
    return valign in ("top", "bottom") and bool(getattr(block, "cap_place", ""))


def rule_box(block) -> tuple[float, float, float, float]:
    """Where the widget underline is drawn.

    A page-1 line that is also the field's own bottom edge stays on that
    edge. A ruled caption whose reserve cannot hold the margin starts the
    blank after the margin, so the line begins after the words.
    """
    x, y, w, h = input_box(block)
    if getattr(block, "ui", "") in ("checkButton", "radio"):
        return x, y, w, h
    if _edge_on(getattr(block, "edges", ()), 2):
        return x, y, w, h
    if not _ruled_caption(block):
        return x, y, w, h
    left = (block.insets or (0.0, 0.0, 0.0, 0.0))[0]
    place = (block.cap_place or "").lower()
    if place in ("left", "right") and left > 0.05 and w - left >= _MIN_WIDGET:
        return x + left, y, w - left, h
    return x, y, w, h


def caption_box(block):
    """Reserve rectangle for the caption, in millimetres.

    Margin is applied before a left caption when the caption is ruled.
    The box stays inside the field and wide enough that the words are not cut.
    """
    x, y, w, h = block.x, block.y, block.w, block.h
    place = (block.cap_place or "").lower()
    reserve = float(block.cap_reserve or 0)
    if reserve <= 0 or not place:
        return None
    if place == "top":
        reserve = min(reserve, h)
        return (x, y, w, reserve)
    if place == "bottom":
        reserve = min(reserve, h)
        return (x, y + h - reserve, w, reserve)
    if place == "left":
        reserve = min(reserve, w)
        left = (block.insets or (0.0, 0.0, 0.0, 0.0))[0]
        if _ruled_caption(block) and 0.05 < left < w - 1:
            return (x + left, y, max(reserve, w - left), h)
        return (x, y, reserve, h)
    if place == "right":
        reserve = min(reserve, w)
        return (x + w - reserve, y, reserve, h)
    return None


def caption_insets(block, box) -> tuple[float, float, float, float]:
    """Template insets that keep caption ink off the rules around it.

    Vertical insets apply only when the caption is top- or bottom-aligned
    and the remaining band still holds the text. The left inset is already
    part of caption_box. The far-side inset is not applied.
    """
    if box is None:
        return (0.0, 0.0, 0.0, 0.0)
    left, top, right, bottom = block.insets or (0.0, 0.0, 0.0, 0.0)
    if not _ruled_caption(block):
        return (0.0, 0.0, 0.0, 0.0)
    use_top = use_bottom = use_right = 0.0
    font_mm = float(block.cap_size or 8) * 25.4 / 72.0
    lines = max(1, (block.text or "").count("\n") + 1)
    need = font_mm * lines * 0.9
    if box[3] - top - bottom >= need:
        use_top, use_bottom = top, bottom
    place = (block.cap_place or "").lower()
    if place == "right" and right > 0.05 and box[2] - right >= max(need, 4.0):
        use_right = right
    return (0.0, use_top, use_right, use_bottom)


def _value_insets(block):
    """Content insets for typed text. A shifted rule already used the left margin."""
    left, top, right, bottom = block.insets or (0.0, 0.0, 0.0, 0.0)
    widget = input_box(block)
    ruled = rule_box(block)
    if ruled[0] > widget[0] + 0.05:
        left = 0.0
    return (left, top, right, bottom)


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
    page.layout_issues = check_page(page, values, scale, overlay=background is not None)
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
    # Corners come from the millimetre ends, not from a rounded width. Two
    # boxes that share an edge then share the pixel, instead of landing a
    # pixel apart.
    x0, y0 = _px(x, scale), _px(y, scale)
    x1, y1 = _px(x + w, scale), _px(y + h, scale)
    if x1 <= x0 or y1 <= y0:
        return
    # Fill only when the template asks for one. A blank text field is
    # transparent; painting it white covers the underline of the line above
    # when the two boxes share an edge.
    if block.fill and block.ui not in ("checkButton", "radio") and not _is_blue(block.fill):
        draw.rectangle((x0, y0, x1, y1), fill=block.fill)
    if block.kind == "image" or (block.image and block.kind != "field"):
        _paste_image(image, block, scale)
    _stroke(draw, x0, y0, x1, y1, block.edges, scale)
    # The ui border is the widget's own box: the underline of a fill-in blank,
    # drawn on the content rectangle after the caption reserve. A checkButton
    # border is the graphic, drawn with the mark, not around the whole field.
    if block.kind == "field" and block.ui not in ("checkButton", "radio") and block.ui_edges:
        box = rule_box(block)
        _stroke(
            draw,
            _px(box[0], scale), _px(box[1], scale),
            _px(box[0] + box[2], scale), _px(box[1] + box[3], scale),
            block.ui_edges, scale,
        )
    if block.kind == "button":
        _text_in(
            draw, block.text, block.cap_family or block.font_family, block.cap_size or block.font_size,
            block.cap_bold, False, (0, 0, 0), (x0, y0, x1, y1),
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
        box = rule_box(block)
        _text_in(
            draw, shown, block.font_family, block.font_size, block.bold, block.italic,
            _paint_color(block.color),
            (_px(box[0], scale), _px(box[1], scale), _px(box[0] + box[2], scale), _px(box[1] + box[3], scale)),
            block.align or "left", block.valign or "middle", _value_insets(block), scale,
        )
        return
    if block.kind == "text" and block.text:
        _text_in(
            draw, block.text, block.font_family, block.font_size, block.bold, block.italic,
            _paint_color(block.color), (x0, y0, x1, y1),
            block.align or "left", block.valign or "top", block.insets, scale,
        )
    elif block.kind == "image" and not block.image and block.image_href:
        draw.rectangle((x0, y0, x1, y1), outline=(210, 210, 210))


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
    box = caption_box(block)
    if box is None:
        return
    insets = caption_insets(block, box)
    _text_in(
        draw, block.text, block.cap_family or "Arial", block.cap_size or 8, block.cap_bold, False,
        (0, 0, 0),
        (_px(box[0], scale), _px(box[1], scale), _px(box[0] + box[2], scale), _px(box[1] + box[3], scale)),
        block.cap_align or "left", block.cap_valign or "middle", insets, scale,
    )


def _mark_box(block):
    """Square checkbox graphic, in millimetres, inside the field.

    XFA places a checkButton of the size attribute (default 10pt) in the
    content region, which is the field minus the caption reserve and the
    margins, and aligns it with the field's hAlign and vAlign. An ordinary
    PDF widget has no size attribute; its rectangle is the graphic.
    """
    x, y, w, h = input_box(block)
    left, top, right, bottom = block.insets or (0.0, 0.0, 0.0, 0.0)
    cx = x + left
    cy = y + top
    cw = max(0.0, w - left - right)
    ch = max(0.0, h - top - bottom)
    if cw <= 0.05 or ch <= 0.05:
        return None
    if block.mark_size > 0:
        side = min(block.mark_size, cw, ch)
        align = (block.align or "left").lower()
        valign = (block.valign or "top").lower()
        if align in ("center", "middle"):
            mx = cx + (cw - side) / 2.0
        elif align == "right":
            mx = cx + cw - side
        else:
            mx = cx
        if valign == "bottom":
            my = cy + ch - side
        elif valign == "middle":
            my = cy + (ch - side) / 2.0
        else:
            my = cy
        return (mx, my, side, side)
    side = min(cw, ch)
    if cw > ch * 1.35:
        mx, my = cx, cy + (ch - side) / 2.0
    else:
        mx = cx + (cw - side) / 2.0
        my = cy + (ch - side) / 2.0
    return (mx, my, side, side)


def _draw_mark(draw, block, value, scale):
    """Square mark. These templates use checkButton, including inside exclGroups."""
    box = _mark_box(block)
    if box is None:
        return
    gx, gy, side, _side = box
    # Keep the stroke inside the graphic so it cannot leave the field.
    pad = min(0.2, side * 0.08)
    x0, y0 = _px(gx + pad, scale), _px(gy + pad, scale)
    x1, y1 = _px(gx + side - pad, scale), _px(gy + side - pad, scale)
    if x1 <= x0 or y1 <= y0:
        return
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


def _stroke(draw, x0, y0, x1, y1, edges, scale):
    """Stroke each edge as a filled bar that ends on the line it meets.

    x0, y0, x1, y1 are the corner pixels. A corner is the overlap of the two
    bars. A missing edge adds no extra reach, and a 1px hairline has no reach
    at all, so stacked rails stay continuous and do not stick past the
    horizontal they join.
    """
    if not edges or x1 <= x0 or y1 <= y0:
        return
    sides = list(edges[:4])
    while len(sides) < 4:
        sides.append(sides[-1] if sides else (False, 0.2, (0, 0, 0)))
    spans = (
        (sides[0], x0, y0, x1, y0),
        (sides[1], x1, y0, x1, y1),
        (sides[2], x0, y1, x1, y1),
        (sides[3], x0, y0, x0, y1),
    )
    for (visible, thick, color), x0, y0, x1, y1 in spans:
        if not visible or _is_blue(color):
            continue
        t = max(1, int((thick or 0.2) * scale))
        half = (t - 1) // 2
        if y0 == y1:
            bar = (x0 - half, y0 - half, x1 + half, y0 - half + t - 1)
        else:
            bar = (x0 - half, y0 - half, x0 - half + t - 1, y1 + half)
        draw.rectangle(bar, fill=color)


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
    while shown and _length(face, shown) > max_px:
        shown = shown[:-1]
    return shown


def _wrap(text: str, face, max_px: int) -> tuple[list[str], bool]:
    """Wrap on spaces. A word wider than the line is clipped, and reported."""
    lines = []
    wide = False
    limit = max(1, int(max_px))
    for para in (text or "").split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            if _length(face, word) > limit + 0.5:
                wide = True
            piece = _clip(word, face, limit)
            trial = piece if not current else current + " " + piece
            if current and _length(face, trial) > limit:
                lines.append(current)
                current = piece
            else:
                current = trial
        lines.append(current)
    return (lines or [""]), wide


def _line_h(face, size: int) -> int:
    """Line height from the face. XFA's default is the font size, with no extra leading."""
    try:
        ascent, descent = face.getmetrics()
        natural = int(ascent) + int(descent)
    except Exception:
        natural = size
    return max(size, natural, 1)


def _compose(text, family, pt, bold, italic, box, align, valign, insets, scale):
    """Place each line inside the box. Nothing is returned outside the box.

    Returns (placed, wide). Each placed item is
    (x, y, w, h, shown, justify) in pixels. `wide` is true when a word had to
    be clipped to the content width.
    """
    if not text:
        return [], False
    x0, y0, x1, y1 = box
    left, top, right, bottom = insets or (0, 0, 0, 0)
    x0 += _px(left, scale)
    y0 += _px(top, scale)
    x1 -= _px(right, scale)
    y1 -= _px(bottom, scale)
    if x1 - x0 < 4 or y1 - y0 < 3:
        return [], False
    size = max(1, int(round(float(pt or 10) * 25.4 / 72.0 * scale)))
    band = y1 - y0
    if size > band and band >= 6:
        size = max(6, int(band))
    face = font(size, bold, italic, family)
    width = x1 - x0
    lines, wide = _wrap(text, face, width)
    line_h = _line_h(face, size)
    # A box with room for one line keeps a slightly wide banner on that line.
    # A taller box wraps. The font is never shrunk when the template has room.
    if "\n" not in text and len(lines) > 1 and band < line_h * 1.85:
        trial = size
        face2, lines2, wide2 = face, lines, wide
        while trial > 6 and len(lines2) > 1:
            trial -= 1
            face2 = font(trial, bold, italic, family)
            lines2, wide2 = _wrap(text, face2, width)
        if len(lines2) == 1 and not wide2:
            size, face, lines, wide = trial, face2, lines2, wide2
            line_h = _line_h(face, size)
    block_h = line_h * max(1, len(lines))
    if valign == "middle":
        y = y0 + (band - block_h) / 2.0
    elif valign == "bottom":
        y = y1 - block_h
    else:
        y = float(y0)
    align = (align or "left").lower()
    placed = []
    for index, line in enumerate(lines):
        if y >= y1 - 0.5:
            break
        line_top = y
        line_bot = y + line_h
        y += line_h
        if line_bot <= y0 + 0.5:
            continue
        shown = _clip(line, face, width)
        if line and not shown:
            continue
        ink = _length(face, shown) if shown else 0.0
        justify = align == "justify" and index < len(lines) - 1 and " " in (shown or "")
        if justify:
            ix, iw = float(x0), float(width)
        elif align in ("center", "middle"):
            ix = x0 + max(0.0, (width - ink) / 2.0)
            iw = ink
        elif align == "right":
            ix = x1 - ink
            iw = ink
        else:
            ix, iw = float(x0), ink
        if ix < x0:
            iw -= x0 - ix
            ix = float(x0)
        if ix + iw > x1:
            iw = x1 - ix
        top_px = max(float(y0), line_top)
        bot_px = min(float(y1), line_bot)
        if iw <= 0.5 or bot_px - top_px <= 0.5:
            continue
        placed.append((ix, top_px, iw, bot_px - top_px, shown, justify, face))
    return placed, wide


def _text_in(draw, text, family, pt, bold, italic, color, box, align, valign, insets, scale):
    placed, _wide = _compose(text, family, pt, bold, italic, box, align, valign, insets, scale)
    if not placed:
        return placed
    left, top, right, bottom = insets or (0, 0, 0, 0)
    x0, y0, x1, y1 = box
    cx0 = x0 + _px(left, scale)
    cy0 = y0 + _px(top, scale)
    cx1 = x1 - _px(right, scale)
    cy1 = y1 - _px(bottom, scale)
    tw, th = cx1 - cx0, cy1 - cy0
    image = getattr(draw, "_image", None)
    if image is None or tw < 2 or th < 2:
        for x, y, w, _h, shown, justify, face in placed:
            if not shown:
                continue
            if justify:
                _justify(draw, shown, face, color, x, y, w)
            else:
                draw.text((x, y), shown, font=face, fill=color)
        return placed
    # Clip to the content box. Glyphs must not paint into the next element.
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    for x, y, w, _h, shown, justify, face in placed:
        if not shown:
            continue
        if justify:
            _justify(layer_draw, shown, face, color, x - cx0, y - cy0, w)
        else:
            layer_draw.text((x - cx0, y - cy0), shown, font=face, fill=color)
    image.paste(layer, (cx0, cy0), layer)
    return placed


def _justify(draw, line, face, color, x, y, width):
    words = [word for word in line.split() if word]
    if len(words) < 2:
        draw.text((x, y), _clip(line, face, int(width)), font=face, fill=color)
        return
    widths = [_length(face, word) for word in words]
    gaps = len(words) - 1
    room = width - sum(widths)
    if room < 0:
        draw.text((x, y), _clip(line, face, int(width)), font=face, fill=color)
        return
    gap = room / gaps
    space = _length(face, " ")
    if space > 0 and (gap < space * 0.6 or gap > space * 4):
        draw.text((x, y), _clip(line, face, int(width)), font=face, fill=color)
        return
    cursor = x
    limit = x + width
    for word, wide in zip(words, widths):
        if cursor >= limit - 0.5:
            break
        if cursor + wide > limit + 0.5:
            draw.text((cursor, y), _clip(word, face, int(limit - cursor)), font=face, fill=color)
            break
        draw.text((cursor, y), word, font=face, fill=color)
        cursor += wide + gap


def _px_box(x, y, w, h, scale):
    return (_px(x, scale), _px(y, scale), _px(x + w, scale), _px(y + h, scale))


def _rects_mm(placed, scale):
    found = []
    for x, y, w, h, _shown, _justify, _face in placed:
        if w <= 0 or h <= 0:
            continue
        found.append((x / scale, y / scale, w / scale, h / scale))
    return found


def _text_rects(text, family, pt, bold, italic, box_mm, align, valign, insets, scale):
    x, y, w, h = box_mm
    placed, wide = _compose(
        text, family, pt, bold, italic, _px_box(x, y, w, h, scale),
        align, valign, insets, scale,
    )
    return _rects_mm(placed, scale), wide


def _block_text_rects(block, values, scale, overlay):
    """Rectangles of the text this render actually paints, plus a clip flag."""
    value = values.get(block.path, "") if block.path else ""
    rects = []
    wide = False
    if overlay:
        if block.kind != "field" or block.ui in ("checkButton", "radio") or not value:
            return rects, wide
        shown = value
        if block.ui == "choiceList":
            for label, saved in block.options:
                if saved == value:
                    shown = label
                    break
        found, wide = _text_rects(
            shown, block.font_family, block.font_size or 10, block.bold, block.italic,
            input_box(block), "left", "middle", (0.6, 0.4, 0.4, 0.4), scale,
        )
        return found, wide
    if block.kind == "button" and block.text:
        found, wide = _text_rects(
            block.text, block.cap_family or block.font_family, block.cap_size or block.font_size,
            block.cap_bold, False, (block.x, block.y, block.w, block.h),
            "center", "middle", (0.4, 0.3, 0.4, 0.3), scale,
        )
        return found, wide
    if block.kind == "field" and block.cap_reserve and block.cap_place and block.text:
        cap = caption_box(block)
        if cap is not None:
            found, flag = _text_rects(
                block.text, block.cap_family or "Arial", block.cap_size or 8, block.cap_bold, False,
                cap, block.cap_align or "left", block.cap_valign or "middle",
                caption_insets(block, cap), scale,
            )
            rects.extend(found)
            wide = wide or flag
    if block.kind == "field" and block.ui in ("checkButton", "radio"):
        return rects, wide
    if block.kind == "field":
        if not value:
            return rects, wide
        shown = value
        if block.ui == "choiceList":
            for label, saved in block.options:
                if saved == value:
                    shown = label
                    break
        found, flag = _text_rects(
            shown, block.font_family, block.font_size, block.bold, block.italic,
            rule_box(block), block.align or "left", block.valign or "middle",
            _value_insets(block), scale,
        )
        rects.extend(found)
        return rects, wide or flag
    if block.kind == "text" and block.text:
        found, flag = _text_rects(
            block.text, block.font_family, block.font_size, block.bold, block.italic,
            (block.x, block.y, block.w, block.h), block.align or "left", block.valign or "top",
            block.insets, scale,
        )
        rects.extend(found)
        wide = wide or flag
    return rects, wide


def _outside(inner, outer, tol):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return (
        x < ox - tol or y < oy - tol
        or x + w > ox + ow + tol or y + h > oy + oh + tol
    )


def _overlaps(a, b, tol):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    iw = min(ax + aw, bx + bw) - max(ax, bx)
    ih = min(ay + ah, by + bh) - max(ay, by)
    return iw > tol and ih > tol


def _border_lines(page):
    """Merged template rules, as (x, y0, y1) verticals and (y, x0, x1) horizontals.

    Colinear pieces that touch become one segment. The shared coordinate is
    kept from the first piece, so a merge cannot shift a line sideways.
    """
    verts = []
    horzs = []
    for block in page.blocks:
        edges = getattr(block, "edges", ()) or ()
        if not edges or block.w <= 0 or block.h <= 0:
            continue
        sides = list(edges[:4])
        while len(sides) < 4:
            sides.append(sides[-1] if sides else (False, 0.2, (0, 0, 0)))
        x, y, w, h = block.x, block.y, block.w, block.h
        specs = (
            (sides[0], "h", y, x, x + w),
            (sides[1], "v", x + w, y, y + h),
            (sides[2], "h", y + h, x, x + w),
            (sides[3], "v", x, y, y + h),
        )
        for (visible, _thick, color), kind, pos, a0, a1 in specs:
            if not visible or _is_blue(color):
                continue
            if a1 < a0:
                a0, a1 = a1, a0
            if a1 - a0 < 0.3:
                continue
            if kind == "v":
                verts.append((pos, a0, a1))
            else:
                horzs.append((pos, a0, a1))
    return _merge_lines(verts), _merge_lines(horzs)


def _merge_lines(items, tol=0.06):
    changed = True
    pending = list(items)
    while changed:
        changed = False
        nxt = []
        for pos, a0, a1 in pending:
            placed = False
            for index, (kept, b0, b1) in enumerate(nxt):
                if abs(kept - pos) > tol:
                    continue
                if a1 < b0 - tol or a0 > b1 + tol:
                    continue
                nxt[index] = (kept, min(b0, a0), max(b1, a1))
                placed = True
                changed = True
                break
            if not placed:
                nxt.append((pos, a0, a1))
        pending = nxt
    return pending


def _on_span(lines, at, pos, tol) -> bool:
    for kept, a0, a1 in lines:
        if abs(kept - at) > tol:
            continue
        if a0 - tol <= pos <= a1 + tol:
            return True
    return False


def border_overshoots(page) -> list[str]:
    """Vertical rules that do not end on a horizontal, and rails shifted sideways.

    A section is closed when every vertical, after colinear pieces are joined,
    starts and ends on a horizontal that spans that x. A second vertical a
    fraction of a millimetre away is a sideways shift. The cell grid's margin
    is wider than that and is not a shift.
    """
    verts, horzs = _border_lines(page)
    issues = []
    tol = 0.08
    for x, y0, y1 in verts:
        if y1 - y0 < 0.3:
            continue
        if _on_span(horzs, y0, x, tol) and _on_span(horzs, y1, x, tol):
            continue
        issues.append(f"overshoot: vertical at {x:.2f} from {y0:.2f} to {y1:.2f}")
        if len(issues) >= 12:
            return issues
    for index, (x0, y0, y1) in enumerate(verts):
        for x1, z0, z1 in verts[index + 1:]:
            dx = abs(x0 - x1)
            if dx <= 0.05 or dx >= 0.6:
                continue
            if min(y1, z1) - max(y0, z0) <= 0.5:
                continue
            issues.append(f"overshoot: shifted verticals at {x0:.2f} and {x1:.2f}")
            if len(issues) >= 12:
                return issues
    return issues


def check_page(page, values=None, scale=None, overlay=False) -> list[str]:
    """Layout gate. Empty means this render stayed inside the template.

    Flags a text line that intersects another text line, a text line drawn
    past the page, a text line or check graphic drawn outside the box the
    template gave that element, and a vertical rule that does not end on the
    horizontal it meets. The same measurements paint uses.
    """
    values = values or {}
    scale = PRINT_PPM if not scale else float(scale)
    tol = 0.35
    lines = []
    issues = border_overshoots(page)
    page_box = (0.0, 0.0, page.page_w or PAGE_W, page.page_h or PAGE_H)
    for block in page.blocks:
        if block.w <= 0 or block.h <= 0:
            continue
        own = (block.x, block.y, block.w, block.h)
        rects, wide = _block_text_rects(block, values, scale, overlay)
        if wide:
            label = (block.text or block.path or "text").split("\n", 1)[0][:48]
            issues.append(f"past margin: {label}")
        for rect in rects:
            if rect[2] <= 0.05 or rect[3] <= 0.05:
                continue
            if _outside(rect, page_box, tol):
                label = (block.text or block.path or "text").split("\n", 1)[0][:48]
                issues.append(f"past margin: {label}")
            elif _outside(rect, own, tol):
                label = (block.text or block.path or "text").split("\n", 1)[0][:48]
                issues.append(f"outside template: {label}")
            lines.append(rect)
        if block.kind == "field" and block.ui in ("checkButton", "radio"):
            if overlay and not values.get(block.path, ""):
                continue
            mark = _mark_box(block)
            if mark is not None and _outside(mark, own, tol):
                label = block.path or block.text or "check"
                issues.append(f"outside template: {label}")
    reported = 0
    for i, left in enumerate(lines):
        for right in lines[i + 1:]:
            if _overlaps(left, right, tol):
                issues.append(
                    "overlap: text lines "
                    f"at {left[0]:.1f},{left[1]:.1f} and {right[0]:.1f},{right[1]:.1f}"
                )
                reported += 1
                if reported >= 8:
                    return _unique(issues)
    return _unique(issues)


def _unique(issues: list[str]) -> list[str]:
    found = []
    for item in issues:
        if item not in found:
            found.append(item)
    return found


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
