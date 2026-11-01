"""Lay out an XFA template the way the template describes it.

Lengths are millimetres. A parent's layout attribute places its children:
position (the default when the attribute is omitted) uses x and y; tb stacks
down; lr-tb packs across and wraps; table rows use columnWidths. x and y on a
child are ignored unless that parent is positioned. breakBefore startNew
starts a new page. The first page uses the first pageArea; later pages use
the overflow pageArea, including its own content box and its own drawings.
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field, replace

import pikepdf
from lxml import etree

from .xfa import read_packets

PAGE_W = 215.9
PAGE_H = 279.4
_HAIRLINE = 0.5 * 25.4 / 72.0

_VISUAL = {"subform", "field", "draw", "exclGroup", "area", "subformSet"}


def local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1]


def parse_len(value, default=0.0) -> float:
    if value is None or value == "":
        return default
    text = str(value).strip()
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*([a-zA-Z%]*)$", text)
    if not match:
        return default
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("", "mm"):
        return number
    if unit == "cm":
        return number * 10
    if unit == "in":
        return number * 25.4
    if unit == "pt":
        return number * 25.4 / 72
    if unit == "px":
        return number * 25.4 / 96
    return number


def _pt(value, default=10.0) -> float:
    if value is None or value == "":
        return default
    return parse_len(value, default * 25.4 / 72.0) * 72.0 / 25.4


def _color(value, default=(0, 0, 0)):
    if not value:
        return default
    parts = str(value).replace(" ", "").split(",")
    if len(parts) < 3:
        return default
    try:
        return tuple(max(0, min(255, int(float(p)))) for p in parts[:3])
    except ValueError:
        return default


def _direct(el, name):
    for child in el:
        if local(child.tag) == name:
            return child
    return None


def _under(node, stop, banned) -> bool:
    parent = node.getparent()
    while parent is not None and parent is not stop:
        if local(parent.tag) in banned:
            return True
        parent = parent.getparent()
    return False


def _break_text(text: str) -> str:
    """Keep paragraph and line breaks. Collapse ordinary spaces on each line.

    U+2028 and U+2029 are the line and paragraph separators XFA puts in a
    text run. A newline in a text node is the same kind of break.
    """
    if not text:
        return ""
    text = text.replace("\u2029", "\n").replace("\u2028", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(" ".join(line.split()) for line in text.split("\n"))


def _rich_text(node) -> str:
    parts = []
    if node.text:
        parts.append(node.text)
    for child in node:
        if not isinstance(child.tag, str):
            if child.tail:
                parts.append(child.tail)
            continue
        if local(child.tag) == "br":
            parts.append("\n")
        else:
            parts.append(_rich_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def plain_text(el) -> str:
    """Visible words of a caption or value. Skips scripts and choice items."""
    if el is None:
        return ""
    banned = {"script", "event", "items", "calculate", "extras"}
    paragraphs = []
    for node in el.iter():
        if local(node.tag) != "p" or _under(node, el, banned):
            continue
        words = _break_text(_rich_text(node)).strip("\n")
        if words.strip():
            paragraphs.append(words)
    if paragraphs:
        return "\n".join(paragraphs)
    chunks = []
    for node in el.iter():
        if local(node.tag) != "text" or not (node.text and node.text.strip()):
            continue
        if _under(node, el, banned):
            continue
        words = _break_text(node.text).strip("\n")
        if words.strip():
            chunks.append(words)
    if chunks:
        return chunks[0] if len(chunks) == 1 else "\n".join(chunks)
    for node in el.iter():
        if local(node.tag) != "exData" or not (node.text and "<" in node.text):
            continue
        raw = re.sub(r"(?i)<br\s*/?>", "\n", node.text)
        raw = re.sub(r"(?i)</p\s*>", "\n", raw)
        raw = re.sub(r"<[^>]+>", " ", raw)
        words = _break_text(raw).strip("\n")
        if words.strip():
            return words
    return ""


def _font(el, default_pt=10.0):
    node = _direct(el, "font") if el is not None else None
    if node is None:
        return "Arial", default_pt, False, False, (0, 0, 0)
    color = (0, 0, 0)
    for child in node:
        if local(child.tag) == "color":
            color = _color(child.get("value"), color)
    return (
        node.get("typeface") or "Arial",
        _pt(node.get("size"), default_pt),
        (node.get("weight") or "").lower() == "bold",
        (node.get("posture") or "").lower() == "italic",
        color,
    )


def _para(el, default_h="left", default_v="top"):
    node = _direct(el, "para") if el is not None else None
    if node is None:
        return default_h, default_v
    align = (node.get("hAlign") or default_h).lower()
    valign = (node.get("vAlign") or default_v).lower()
    if align not in ("left", "center", "right", "justify"):
        align = default_h
    if valign not in ("top", "middle", "bottom"):
        valign = default_v
    return align, valign


def _insets(el):
    node = _direct(el, "margin") if el is not None else None
    if node is None:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        parse_len(node.get("leftInset")),
        parse_len(node.get("topInset")),
        parse_len(node.get("rightInset")),
        parse_len(node.get("bottomInset")),
    )


def _margins(el):
    return _insets(el)


def _border(el):
    node = _direct(el, "border")
    if node is None:
        return (), None
    edges = []
    fill = None
    for child in node:
        if local(child.tag) == "edge":
            presence = child.get("presence") or "visible"
            color = (0, 0, 0)
            for grand in child:
                if local(grand.tag) == "color":
                    color = _color(grand.get("value"), color)
            thick = parse_len(child.get("thickness"), _HAIRLINE) if child.get("thickness") else _HAIRLINE
            edges.append((presence not in ("hidden", "invisible", "inactive"), thick, color))
        elif local(child.tag) == "fill":
            for grand in child:
                if local(grand.tag) == "color":
                    fill = _color(grand.get("value"), (255, 255, 255))
    if len(edges) == 1:
        edges = edges * 4
    elif 1 < len(edges) < 4:
        while len(edges) < 4:
            edges.append(edges[-1])
    return tuple(edges[:4]), fill


def _image(el):
    for node in el.iter():
        if local(node.tag) != "image":
            continue
        href = node.get("href") or ""
        compact = "".join((node.text or "").split())
        if len(compact) > 40:
            try:
                return base64.b64decode(compact), href
            except Exception:
                return b"", href
        return b"", href
    return None, ""


def _ui_widget(el):
    node = _direct(el, "ui")
    if node is None:
        return None
    for child in node:
        if isinstance(child.tag, str):
            return child
    return None


def _ui(el) -> str:
    widget = _ui_widget(el)
    return local(widget.tag) if widget is not None else ""


def _relevant_flags(el):
    rel = (el.get("relevant") or "").strip()
    return rel in ("-print", "screen") or rel.startswith("-print"), rel == "print"


def choice_options(el) -> list:
    lists = [child for child in el if local(child.tag) == "items"]
    display, saved = [], []
    for items in lists:
        texts = []
        for value in items:
            if value.text and value.text.strip():
                texts.append(value.text.strip())
        if items.get("presence") == "hidden" or items.get("save") == "1":
            saved = texts
        elif not display:
            display = texts
    if not display:
        display = saved
    if not saved:
        saved = display
    count = max(len(display), len(saved))
    options = []
    for i in range(count):
        label = display[i] if i < len(display) else saved[i]
        value = saved[i] if i < len(saved) else display[i]
        options.append((label, value))
    return options


def check_values(el) -> tuple[str, str]:
    for items in el:
        if local(items.tag) == "items":
            values = [(child.text or "").strip() for child in items if (child.text or "").strip()]
            if len(values) >= 2:
                return values[0], values[1]
            if len(values) == 1:
                return values[0], ""
    return "1", "0"


def repeat_count(el) -> int:
    for child in el:
        if local(child.tag) == "occur":
            raw = child.get("initial")
            if raw not in (None, ""):
                try:
                    return max(0, int(float(raw)))
                except ValueError:
                    return 1
            return 1
    return 1


def _break_before(el) -> bool:
    for child in el:
        if local(child.tag) == "breakBefore" and str(child.get("startNew") or "") in ("1", "true"):
            return True
    return False


def _keep(el) -> bool:
    for child in el:
        if local(child.tag) == "keep" and (child.get("intact") or "") in ("contentArea", "pageArea"):
            return True
    return False


def _leader(el) -> str:
    for child in el:
        if local(child.tag) == "overflow" and child.get("leader"):
            return child.get("leader")
    return ""


def _columns(text, inner):
    if not text:
        return []
    widths = []
    for part in str(text).split():
        widths.append(None if part == "-1" else parse_len(part, 0))
    fixed = sum(w for w in widths if w)
    flex = sum(1 for w in widths if not w)
    share = max(0.0, inner - fixed) / flex if flex else 0.0
    return [w if w else share for w in widths]


def visual_children(el):
    found = []
    for child in el:
        if local(child.tag) not in _VISUAL:
            continue
        if (child.get("presence") or "") in ("hidden", "inactive"):
            continue
        found.append(child)
    return found


def _extend(parts, el, index):
    name = el.get("name") or ""
    if name and local(el.tag) in ("subform", "field", "exclGroup"):
        bit = name if index == 0 else f"{name}[{index}]"
        return parts + [bit]
    return list(parts)


def _dbg(message):
    if os.environ.get("OXBOW_LAYOUT"):
        print(message, flush=True)


@dataclass
class Block:
    kind: str
    text: str = ""
    path: str = ""
    ui: str = ""
    options: list = field(default_factory=list)
    on_value: str = "1"
    off_value: str = "0"
    read_only: bool = False
    group: str = ""
    x: float = 0
    y: float = 0
    w: float = 40
    h: float = 8
    page: int = 0
    font_family: str = "Arial"
    font_size: float = 9
    bold: bool = False
    italic: bool = False
    align: str = "left"
    valign: str = "top"
    color: tuple = (0, 0, 0)
    fill: tuple | None = None
    edges: tuple = ()
    insets: tuple = (0, 0, 0, 0)
    cap_place: str = ""
    cap_reserve: float = 0
    cap_family: str = "Arial"
    cap_size: float = 8
    cap_bold: bool = False
    cap_align: str = "left"
    cap_valign: str = "middle"
    image: bytes | None = None
    image_href: str = ""
    screen_only: bool = False
    print_only: bool = False
    page_label: bool = False
    baseline: str = ""
    # Border of the ui widget (the underline of a text edit, the box of a check).
    # Edge order is top, right, bottom, left, the same as the field border.
    ui_edges: tuple = ()
    # checkButton size in millimetres. 0 means this is not an XFA check graphic.
    # The specification default, when the attribute is omitted, is 10pt.
    mark_size: float = 0.0


@dataclass
class Page:
    number: int
    title: str
    blocks: list = field(default_factory=list)
    content_x: float = 7.62
    content_y: float = 20.0
    content_w: float = 200.66
    content_h: float = 236.0
    page_w: float = PAGE_W
    page_h: float = PAGE_H
    layout_issues: list = field(default_factory=list)


@dataclass
class Frag:
    w: float = 0
    h: float = 0
    x: float = 0
    y: float = 0
    blocks: list = field(default_factory=list)
    children: list = field(default_factory=list)
    splittable: bool = False
    break_before: bool = False
    keep: bool = False
    is_leader: bool = False
    any_break: bool = False
    name: str = ""


def _auto_height(el, text, font_pt, insets, reserve, place, width):
    em = max(2.2, font_pt) * 25.4 / 72.0
    _left, top, _right, bottom = insets
    line = em * 1.35 + top + bottom
    extra = reserve if place in ("top", "bottom") else 0.0
    height = max(4.4, line + extra)
    if text and width > 2:
        chars = max(8, int(width / max(em * 0.48, 0.8)))
        rows = max(1, (len(text) + chars - 1) // chars)
        height = max(height, rows * em * 1.25 + top + bottom + extra)
    if _ui(el) in ("checkButton",) or (el.get("h") is None and _ui(el) == "checkButton"):
        height = max(height, 5.5)
    return height


def _instance_index(parts, name):
    for part in parts:
        if part == name:
            return 0
        prefix = name + "["
        if part.startswith(prefix) and part.endswith("]"):
            try:
                return int(part[len(prefix):-1])
            except ValueError:
                return 0
    return None


def _embedded_letter(el, parts) -> str:
    """Section letters such as c) are a hidden field plus a one-line script.

    The script is String.fromCharCode(99 + instanceIndex). 99 is 'c'.
    The draw embeds that field in front of the heading.
    """
    parent = el.getparent()
    if parent is None:
        return ""
    for sib in parent:
        if local(sib.tag) != "field":
            continue
        raw = etree.tostring(sib, encoding="unicode")
        if "fromCharCode(99" not in raw:
            continue
        index = _instance_index(parts, "Item2X")
        return chr(99 + (0 if index is None else index))
    return ""


def _leaf(el, avail, parts, group, index, force_w, natural) -> Frag:
    screen_only, print_only = _relevant_flags(el)
    ui = _ui(el)
    kind_tag = local(el.tag)
    if force_w:
        width = force_w
    elif el.get("w"):
        width = min(parse_len(el.get("w")), avail)
    elif natural:
        width = min(avail, 40.0)
    else:
        width = avail
    width = max(0.5, width)
    edges, fill = _border(el)
    insets = _insets(el)
    face, size, bold, italic, color = _font(el, 9 if kind_tag == "field" else 10)
    align, valign = _para(el)
    image, href = _image(el)
    cap_text, cap_place, cap_reserve = "", "", 0.0
    cap_face, cap_size, cap_bold = "Arial", 8.0, False
    cap_align, cap_valign = "left", "middle"
    widget = _ui_widget(el)
    ui_edges = ()
    mark_size = 0.0
    if widget is not None:
        ui_edges, _ui_fill = _border(widget)
        if local(widget.tag) == "checkButton":
            mark_size = parse_len(widget.get("size"), 10.0 * 25.4 / 72.0)
    cap = _direct(el, "caption")
    if cap is not None:
        cap_text = plain_text(cap).strip()
        cap_place = (cap.get("placement") or "").lower()
        cap_reserve = parse_len(cap.get("reserve"))
        if cap_text and cap_reserve and not cap_place:
            cap_place = "left"
        cap_face, cap_size, cap_bold, _cap_italic, _cap_color = _font(cap, 8)
        if _direct(cap, "para") is None:
            cap_align, cap_valign = "left", "middle"
        else:
            cap_align, cap_valign = _para(cap, "left", "middle")
    value_el = _direct(el, "value")
    value_text = plain_text(value_el).strip() if value_el is not None else ""
    if ui == "button":
        kind = "button"
        shown = cap_text or value_text
    elif kind_tag == "field":
        kind = "field"
        shown = cap_text
        if group and ui == "checkButton":
            ui = "radio"
    elif image is not None:
        kind = "image"
        shown = ""
    else:
        kind = "text"
        letter = _embedded_letter(el, parts)
        shown = (letter + value_text) if letter and value_text.startswith(")") else value_text
    height = parse_len(el.get("h")) if el.get("h") else 0.0
    if height <= 0:
        height = _auto_height(el, shown if kind != "field" else cap_text, size, insets, cap_reserve, cap_place, width)
    path = ""
    options = []
    on_value, off_value = "1", "0"
    read_only = el.get("access") == "readOnly"
    if kind == "field":
        path = ".".join(_extend(parts, el, 0))
        if ui == "choiceList":
            options = choice_options(el)
        if ui in ("checkButton", "radio"):
            on_value, off_value = check_values(el)
    visible = bool(shown) or image is not None or any(edge[0] for edge in edges) or fill or kind in ("field", "button")
    frag = Frag(w=width, h=height, name=el.get("name") or kind_tag)
    if not visible:
        return frag
    frag.blocks.append(Block(
        kind=kind,
        text=shown,
        path=path,
        ui=ui if kind == "field" else "",
        options=options,
        on_value=on_value,
        off_value=off_value,
        read_only=read_only,
        group=group if kind == "field" else "",
        w=width,
        h=height,
        font_family=face,
        font_size=size,
        bold=bold,
        italic=italic,
        align=align,
        valign=valign,
        color=color,
        fill=fill if kind == "button" or fill else (fill if kind != "field" else fill),
        edges=edges,
        insets=insets,
        cap_place=cap_place if kind == "field" else "",
        cap_reserve=cap_reserve if kind == "field" else 0,
        cap_family=cap_face,
        cap_size=cap_size,
        cap_bold=cap_bold,
        cap_align=cap_align,
        cap_valign=cap_valign,
        image=image if image else None,
        image_href=href or "",
        screen_only=screen_only,
        print_only=print_only,
        page_label=(kind == "text" and shown.strip() in ("Page", "Page of")),
        ui_edges=ui_edges if kind == "field" else (),
        mark_size=mark_size if kind == "field" else 0.0,
    ))
    if kind == "button" and fill is None and any(edge[0] for edge in edges):
        frag.blocks[0].fill = (212, 208, 200)
    if kind == "image" and not image:
        frag.blocks[0].kind = "image"
    return frag


def _resize_leaf(frag, width):
    frag.w = width
    if frag.blocks and not frag.children:
        frag.blocks[0].w = width


def _stretch_leaf(frag, height):
    if frag.children or not frag.blocks or frag.h >= height - 0.05:
        return
    block = frag.blocks[0]
    if block.ui in ("checkButton", "radio") or block.kind in ("button", "image"):
        return
    if block.kind == "text" and not any(edge[0] for edge in block.edges) and not block.fill:
        return
    block.h = height
    frag.h = height


def _center_check(frag, line_h):
    if not frag.blocks:
        return
    if frag.blocks[0].ui in ("checkButton", "radio") and frag.h < line_h - 0.2:
        frag.y = (line_h - frag.h) / 2.0


def _extend_line_rails(line):
    """Continue a section's outer side rail down a line that is taller than the rail.

    A checkbox keeps its own box, so its right edge can stop short of a wrapped
    question. The missing span is the same edge, from the template line height,
    and the checkbox itself does not move.
    """
    found = []

    def walk(frag, ox, oy):
        for block in frag.blocks:
            edges = block.edges or ()
            if len(edges) < 4:
                continue
            bx = ox + block.x
            by = oy + block.y
            if edges[1][0]:
                found.append((bx + block.w, by, by + block.h, edges[1], "r"))
            if edges[3][0]:
                found.append((bx, by, by + block.h, edges[3], "l"))
        for child in frag.children:
            walk(child, ox + child.x, oy + child.y)

    walk(line, 0.0, 0.0)
    if not found or line.h <= 0:
        return
    left_x = min(item[0] for item in found)
    right_x = max(item[0] for item in found)
    for x, y0, y1, edge, side in found:
        if side == "r" and abs(x - right_x) > 0.2:
            continue
        if side == "l" and abs(x - left_x) > 0.2:
            continue
        spans = []
        if y0 > 0.05:
            spans.append((0.0, y0))
        if line.h - y1 > 0.05:
            spans.append((y1, line.h))
        _visible, thick, color = edge
        hidden = (False, thick, color)
        shown = (True, thick, color)
        for top, bottom in spans:
            if side == "r":
                edges = (hidden, shown, hidden, hidden)
                bx = x - 1.0
            else:
                edges = (hidden, hidden, hidden, shown)
                bx = x
            line.blocks.append(Block(
                kind="text", x=bx, y=top, w=1.0, h=bottom - top, edges=edges,
            ))


def _mark(frag) -> bool:
    inner = False
    for child in frag.children:
        if _mark(child):
            inner = True
    frag.any_break = bool(frag.break_before or inner)
    return frag.any_break


def measure(el, avail, parts, group, index=0, force_w=None, natural=False) -> Frag:
    if (el.get("presence") or "") in ("hidden", "inactive"):
        return Frag(name=el.get("name") or "")
    kind = local(el.tag)
    if kind in ("field", "draw"):
        return _leaf(el, avail, parts, group, index, force_w, natural)
    mode = el.get("layout") or ("tb" if kind == "subformSet" else "position")
    if mode == "row":
        mode = "lr-tb"
    own = _extend(parts, el, index)
    if kind == "exclGroup":
        group = ".".join(own)
    if mode == "table":
        return _table(el, avail, own, group, force_w, natural)
    if mode == "position":
        return _position(el, avail, own, group, force_w, natural)
    return _flow(el, mode, avail, own, group, force_w, natural)


def _outer_width(el, avail, force_w, natural, left, right):
    if force_w:
        return force_w
    if el.get("w"):
        return min(parse_len(el.get("w")), avail)
    if natural:
        return None
    return avail


def _child_frag(child, index, inner, parts, group, natural_child):
    if child.get("w") and not natural_child:
        width = min(parse_len(child.get("w")), inner)
        return measure(child, width, parts, group, index, natural=False)
    if natural_child:
        return measure(child, inner, parts, group, index, natural=True)
    return measure(child, inner, parts, group, index, natural=False)


def _flow(el, mode, avail, parts, group, force_w, natural) -> Frag:
    left, top, right, bottom = _margins(el)
    outer = _outer_width(el, avail, force_w, natural, left, right)
    inner = max(4.0, (outer if outer else avail) - left - right)
    frags = []
    for child in visual_children(el):
        count = repeat_count(child) if local(child.tag) == "subform" and child.get("name") else 1
        for index in range(count):
            natural_child = mode in ("lr-tb", "rl-tb") and not child.get("w")
            frag = _child_frag(child, index, inner, parts, group, natural_child)
            if frag.w > inner + 0.3 and not frag.children:
                _resize_leaf(frag, inner)
            frags.append(frag)
    if mode in ("lr-tb", "rl-tb"):
        lines = _pack(frags, inner, rtl=(mode == "rl-tb"))
        content_h = sum(line.h for line in lines)
        used_w = max((line.used for line in lines), default=0)
        if outer is None:
            outer = min(avail, used_w + left + right) if used_w else inner
        height = top + content_h + bottom
        if el.get("h"):
            height = max(parse_len(el.get("h")), height)
        flow = Frag(w=outer, h=height, splittable=True, name=el.get("name") or "lr")
        y = top
        for line in lines:
            line.x = left
            line.y = y
            line.w = max(line.w, inner) if outer else line.used
            y += line.h
            flow.children.append(line)
        if bottom:
            flow.children.append(Frag(w=inner, h=bottom, x=left, y=y))
    else:
        y = top
        flow = Frag(splittable=True, name=el.get("name") or "tb")
        for frag in frags:
            frag.x = left
            frag.y = y
            y += frag.h
            flow.children.append(frag)
        y += bottom
        if el.get("h"):
            y = max(parse_len(el.get("h")), y)
        if outer is None:
            outer = max((frag.x + frag.w for frag in frags), default=inner)
            outer = min(avail, outer + right)
        flow.w = outer
        flow.h = y
    edges, fill = _border(el)
    if any(edge[0] for edge in edges) or fill:
        flow.blocks.append(Block(kind="text", w=flow.w, h=flow.h, edges=edges, fill=fill))
    flow.break_before = _break_before(el)
    flow.keep = _keep(el)
    return flow


def _pack(frags, inner, rtl=False):
    lines = []
    current = []
    used = 0.0

    def close():
        nonlocal current, used
        if not current:
            return
        height = max(item.h for item in current)
        for item in current:
            _stretch_leaf(item, height)
            _center_check(item, height)
        line = Frag(w=inner, h=height, children=list(current), splittable=False)
        line.used = used
        if rtl:
            shift = max(0.0, inner - used)
            for item in line.children:
                item.x += shift
        _extend_line_rails(line)
        lines.append(line)
        current = []
        used = 0.0

    for frag in frags:
        if current and used + frag.w > inner + 0.45:
            close()
        frag.x = used
        frag.y = 0
        current.append(frag)
        used += frag.w
    close()
    return lines


def _position(el, avail, parts, group, force_w, natural) -> Frag:
    left, top, right, bottom = _margins(el)
    outer = _outer_width(el, avail, force_w, natural, left, right)
    inner = max(4.0, (outer if outer else avail) - left - right)
    children = []
    for child in visual_children(el):
        count = repeat_count(child) if local(child.tag) == "subform" and child.get("name") else 1
        for index in range(count):
            if child.get("w"):
                frag = measure(child, min(parse_len(child.get("w")), inner), parts, group, index)
            else:
                frag = measure(child, inner, parts, group, index, natural=True)
            frag.x = parse_len(child.get("x")) + left
            frag.y = parse_len(child.get("y")) + top
            children.append(frag)
    extent_r = max((child.x + child.w for child in children), default=left)
    extent_b = max((child.y + child.h for child in children), default=top)
    if outer is None:
        outer = max(extent_r + right, 1)
    height = extent_b + bottom
    if el.get("h"):
        height = max(parse_len(el.get("h")), height)
    frag = Frag(w=outer, h=height, children=children, splittable=False, name=el.get("name") or "pos")
    edges, fill = _border(el)
    if any(edge[0] for edge in edges) or fill:
        frag.blocks.append(Block(kind="text", w=outer, h=height, edges=edges, fill=fill))
    frag.break_before = _break_before(el)
    frag.keep = _keep(el)
    return frag


def _table(el, avail, parts, group, force_w, natural) -> Frag:
    left, top, right, bottom = _margins(el)
    outer = _outer_width(el, avail, force_w, False if el.get("w") or force_w else natural, left, right)
    if outer is None:
        outer = avail
    inner = max(4.0, outer - left - right)
    cols = _columns(el.get("columnWidths") or "", inner)
    leader = _leader(el)
    rows = []
    if top:
        rows.append(Frag(w=inner, h=top, x=left, name="pad"))
    for child in visual_children(el):
        count = repeat_count(child) if local(child.tag) == "subform" and child.get("name") else 1
        for index in range(count):
            row = _row(child, cols, inner, parts, group, index)
            row.x = left
            row.is_leader = bool(leader) and (child.get("name") or "") == leader
            rows.append(row)
    if bottom:
        rows.append(Frag(w=inner, h=bottom, x=left, name="pad"))
    y = 0.0
    for row in rows:
        row.y = y
        y += row.h
    frag = Frag(w=outer, h=y, children=rows, splittable=True, name=el.get("name") or "table")
    # The cell grid is already inside the margin. The subform border is the
    # section rectangle around that grid: one box, hidden edges omitted.
    # Rows keep the column lines, so a side rail meets the header above and
    # the bottom edge below instead of ending in the margin.
    edges, fill = _border(el)
    if any(edge[0] for edge in edges) or fill:
        frag.blocks.append(Block(kind="text", w=frag.w, h=frag.h, edges=edges, fill=fill))
    frag.break_before = _break_before(el)
    frag.keep = _keep(el)
    return frag


def _row(el, cols, inner, parts, group, index) -> Frag:
    own = _extend(parts, el, index)
    cells = []
    cursor = 0
    x = 0.0
    kids = []
    for child in visual_children(el):
        count = repeat_count(child) if local(child.tag) == "subform" and child.get("name") else 1
        for kid_index in range(count):
            kids.append((child, kid_index))
    for child, kid_index in kids:
        span = child.get("colSpan")
        if cols and cursor < len(cols):
            if span == "-1":
                take = cols[cursor:]
                cursor = len(cols)
            else:
                count = int(span) if span and span.lstrip("-").isdigit() else 1
                take = cols[cursor:cursor + max(1, count)]
                cursor += max(1, count)
            width = sum(take) if take else (parse_len(child.get("w"), 20) if child.get("w") else 20)
        elif child.get("w"):
            width = min(parse_len(child.get("w")), inner)
        else:
            width = inner / max(1, len(kids))
        frag = measure(child, width, own, group, kid_index, force_w=width)
        frag.x = x
        x += width
        cells.append(frag)
    height = max((cell.h for cell in cells), default=6.0)
    for cell in cells:
        _stretch_leaf(cell, height)
        _center_check(cell, height)
    row = Frag(w=max(inner, x), h=height, children=cells, splittable=False, name=el.get("name") or "row")
    _extend_line_rails(row)
    edges, fill = _border(el)
    if any(edge[0] for edge in edges) or fill:
        row.blocks.append(Block(kind="text", w=row.w, h=height, edges=edges, fill=fill))
    return row


def _flatten(frag, ox, oy):
    found = []
    for block in frag.blocks:
        found.append(replace(block, x=block.x + ox, y=block.y + oy))
    for child in frag.children:
        found.extend(_flatten(child, ox + child.x, oy + child.y))
    return found


@dataclass
class _Area:
    page_w: float
    page_h: float
    content: tuple
    chrome: list
    max_occur: int | None


def _page_areas(form):
    areas = []
    for node in form:
        if local(node.tag) != "pageSet":
            continue
        for area in node:
            if local(area.tag) != "pageArea":
                continue
            short, long = PAGE_W, PAGE_H
            content = (7.62, 20.0, 200.66, 236.0)
            max_occur = None
            for child in area:
                if local(child.tag) == "medium":
                    short = parse_len(child.get("short"), PAGE_W)
                    long = parse_len(child.get("long"), PAGE_H)
                elif local(child.tag) == "contentArea":
                    content = (
                        parse_len(child.get("x"), 0),
                        parse_len(child.get("y"), 0),
                        parse_len(child.get("w"), 200),
                        parse_len(child.get("h"), 236),
                    )
                elif local(child.tag) == "occur" and child.get("max") not in (None, "", "-1"):
                    try:
                        max_occur = int(child.get("max"))
                    except ValueError:
                        max_occur = None
            chrome = []
            for child in visual_children(area):
                frag = measure(child, short, [], "", 0)
                chrome.extend(_flatten(frag, parse_len(child.get("x")), parse_len(child.get("y"))))
            areas.append(_Area(short, long, content, chrome, max_occur))
    if not areas:
        areas.append(_Area(PAGE_W, PAGE_H, (7.62, 20.0, 200.66, 236.0), [], 1))
    return areas


class _Pager:
    def __init__(self, areas):
        self.areas = areas
        self.pages = []
        self.page = None
        self.cursor = 0.0
        self.top = 0.0
        self.bottom = 0.0
        self.left = 0.0
        self.body = False
        self._used = [0 for _ in areas]

    def new_page(self):
        if not self.pages:
            index = 0
        else:
            index = 1 if self.areas[0].max_occur == 1 and len(self.areas) > 1 else 0
            while index < len(self.areas) - 1:
                limit = self.areas[index].max_occur
                if limit and self._used[index] >= limit:
                    index += 1
                    continue
                break
            index = min(index, len(self.areas) - 1)
        area = self.areas[index]
        self._used[index] += 1
        x, y, w, h = area.content
        page = Page(
            number=len(self.pages) + 1,
            title=f"Page {len(self.pages) + 1}",
            content_x=x,
            content_y=y,
            content_w=w,
            content_h=h,
            page_w=area.page_w,
            page_h=area.page_h,
        )
        for block in area.chrome:
            page.blocks.append(replace(block, page=page.number - 1))
        self.pages.append(page)
        self.page = page
        self.left = x
        self.top = y
        self.bottom = y + h
        self.cursor = y
        self.body = False
        _dbg(f"page {page.number} content y {y:.2f} h {h:.2f}")

    def emit(self, frag, left):
        self._paint(frag, left, self.cursor)
        self.cursor += frag.h
        if frag.h > 0.15 or frag.blocks or frag.children:
            self.body = True

    def _paint(self, frag, x, y):
        index = self.page.number - 1
        for block in frag.blocks:
            self.page.blocks.append(replace(block, x=block.x + x, y=block.y + y, page=index))
        for child in frag.children:
            self._paint(child, x + child.x, y + child.y)

    def place(self, frag, left):
        if frag is None:
            return
        if frag.h <= 0.05 and not frag.children and not frag.blocks and not frag.break_before:
            return
        if self.page is None:
            self.new_page()
        if frag.break_before and self.body:
            _dbg(f"break before {frag.name} h={frag.h:.1f}")
            self.new_page()
        room = self.bottom - self.cursor
        content_h = self.bottom - self.top
        if frag.keep and not frag.any_break and frag.h <= content_h + 0.7:
            if frag.h > room + 0.7 and self.body:
                self.new_page()
            self.emit(frag, left)
            return
        if frag.splittable and frag.children and not frag.any_break and frag.h <= room + 0.7:
            self.emit(frag, left)
            return
        if frag.splittable and frag.children and not frag.any_break and not self.body and frag.h <= content_h + 0.7:
            self.emit(frag, left)
            return
        if frag.splittable and frag.children and (frag.any_break or frag.h > room + 0.7):
            leaders = [child for child in frag.children if child.is_leader]
            if leaders and frag.h > room + 0.7 and not frag.any_break:
                self._place_split(frag, left, leaders)
                return
            for child in frag.children:
                self.place(child, left + child.x)
            return
        if frag.h > room + 0.7 and self.body and frag.h <= content_h + 12:
            self.new_page()
        self.emit(frag, left)

    def _place_split(self, frag, left, leaders):
        started = False
        for child in frag.children:
            if child.is_leader:
                continue
            spacer = not child.blocks and not child.children
            lead_h = 0.0 if started or spacer else sum(item.h for item in leaders)
            room = self.bottom - self.cursor
            if self.body and child.h + lead_h > room + 0.7:
                self.new_page()
                started = False
                lead_h = 0.0 if spacer else sum(item.h for item in leaders)
            if not spacer and leaders and not started:
                for leader in leaders:
                    self.emit(leader, left + leader.x)
                started = True
            if spacer:
                self.cursor += child.h
                if child.h > 0.15:
                    self.body = True
            else:
                self.emit(child, left + child.x)
                started = True


def layout_template(template_xml: bytes) -> list[Page]:
    root = etree.fromstring(template_xml)
    form = None
    for child in root:
        if local(child.tag) == "subform":
            form = child
            break
    if form is None:
        return []
    areas = _page_areas(form)
    parts = [form.get("name")] if form.get("name") else []
    children = []
    content_w = areas[0].content[2]
    for child in visual_children(form):
        count = repeat_count(child) if local(child.tag) == "subform" and child.get("name") else 1
        for index in range(count):
            children.append(measure(child, content_w, parts, "", index))
    root_frag = Frag(splittable=True, name=form.get("name") or "form", w=content_w)
    y = 0.0
    for child in children:
        child.y = y
        y += child.h
        root_frag.children.append(child)
    root_frag.h = y
    _mark(root_frag)
    pager = _Pager(areas)
    pager.place(root_frag, areas[0].content[0])
    if not pager.pages:
        return []
    total = len(pager.pages)
    for page in pager.pages:
        for block in page.blocks:
            if block.page_label:
                block.text = f"Page {page.number} of {total}"
    if os.environ.get("OXBOW_LAYOUT"):
        for page in pager.pages:
            texts = [block.text.split("\n")[0][:42] for block in page.blocks if block.text][:6]
            fields = sum(1 for block in page.blocks if block.kind == "field")
            print(f"page {page.number} blocks {len(page.blocks)} fields {fields} {texts}", flush=True)
    return [page for page in pager.pages if page.blocks]


def load_document(pdf) -> list[Page]:
    packets = read_packets(pdf)
    if "template" not in packets:
        return []
    return layout_template(packets["template"])


def _ann_name(obj) -> str:
    name = obj.get("/T")
    if name:
        return str(name)
    parent = obj.get("/Parent")
    if parent is not None and parent.get("/T"):
        return str(parent.get("/T"))
    return "field"


def _ann_type(obj):
    ft = obj.get("/FT")
    flags = obj.get("/Ff")
    parent = obj.get("/Parent")
    if ft is None and parent is not None:
        ft = parent.get("/FT")
    if flags is None and parent is not None:
        flags = parent.get("/Ff")
    try:
        flags = int(flags or 0)
    except (TypeError, ValueError):
        flags = 0
    return str(ft or ""), flags


def _ann_value(obj) -> str:
    value = obj.get("/V")
    if value is None:
        parent = obj.get("/Parent")
        if parent is not None:
            value = parent.get("/V")
    if value is None:
        return ""
    return str(value)


def iter_widgets(pdf):
    """Yield (page_index, annot, key) for fillable AcroForm widgets."""
    found = []
    for page_index, page in enumerate(pdf.pages):
        annots = page.get("/Annots") or []
        counts = {}
        for annot in annots:
            obj = annot
            if str(obj.get("/Subtype") or "") != "/Widget":
                continue
            ft, flags = _ann_type(obj)
            if ft == "/Btn" and flags & (1 << 16):
                continue
            if not ft and not obj.get("/Parent"):
                continue
            name = _ann_name(obj)
            nth = counts.get(name, 0)
            counts[name] = nth + 1
            key = f"p{page_index + 1}.{name}" if nth == 0 else f"p{page_index + 1}.{name}.{nth}"
            found.append((page_index, obj, key))
    return found


def load_static_pages(pdf) -> list[Page]:
    """One page per PDF page, with AcroForm widgets over a Poppler render."""
    widgets = {}
    for page_index, obj, key in iter_widgets(pdf):
        widgets.setdefault(page_index, []).append((obj, key))
    pages = []
    for page_index, page in enumerate(pdf.pages):
        box = page.MediaBox
        x0, y0, x1, y1 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        width = abs(x1 - x0) * 25.4 / 72.0
        height = abs(y1 - y0) * 25.4 / 72.0
        made = Page(
            number=page_index + 1,
            title=f"Page {page_index + 1}",
            page_w=width,
            page_h=height,
            content_x=0,
            content_y=0,
            content_w=width,
            content_h=height,
        )
        rotate = page.get("/Rotate")
        try:
            rotate = int(rotate or 0) % 360
        except (TypeError, ValueError):
            rotate = 0
        if rotate:
            # Poppler paints the rotated page. Widget rectangles stay in
            # unrotated user space, so they are left off until that is mapped.
            pages.append(made)
            continue
        for obj, key in widgets.get(page_index, []):
            rect = obj.get("/Rect")
            if rect is None or len(rect) < 4:
                continue
            rx0, ry0, rx1, ry1 = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            ft, flags = _ann_type(obj)
            ui = "textEdit"
            group = ""
            on_value, off_value = "Yes", "Off"
            options = []
            if ft == "/Btn":
                ui = "radio" if flags & (1 << 15) else "checkButton"
                normal = None
                ap = obj.get("/AP")
                if ap is not None and ap.get("/N") is not None and isinstance(ap.get("/N"), pikepdf.Dictionary):
                    normal = ap.get("/N")
                if normal is not None:
                    for token in normal.keys():
                        label = str(token).lstrip("/")
                        if label != "Off":
                            on_value = label
                            break
                parent = obj.get("/Parent")
                if ui == "radio" and parent is not None and parent.get("/T"):
                    group = f"p{page_index + 1}.{parent.get('/T')}"
            elif ft == "/Ch":
                ui = "choiceList"
                opt = obj.get("/Opt")
                parent = obj.get("/Parent")
                if opt is None and parent is not None:
                    opt = parent.get("/Opt")
                if opt is not None:
                    for item in opt:
                        if isinstance(item, pikepdf.Array) and len(item) >= 2:
                            options.append((str(item[1]), str(item[0])))
                        else:
                            options.append((str(item), str(item)))
            made.blocks.append(Block(
                kind="field",
                text=key.rsplit(".", 1)[-1],
                path=key,
                ui=ui,
                options=options,
                on_value=on_value,
                off_value=off_value,
                read_only=bool(flags & 1),
                group=group,
                x=(min(rx0, rx1) - x0) * 25.4 / 72.0,
                y=(y1 - max(ry0, ry1)) * 25.4 / 72.0,
                w=abs(rx1 - rx0) * 25.4 / 72.0,
                h=abs(ry1 - ry0) * 25.4 / 72.0,
                page=page_index,
                font_family="Arial",
                font_size=10,
                baseline=_ann_value(obj),
            ))
        pages.append(made)
    return pages
