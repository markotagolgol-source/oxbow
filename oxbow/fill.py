"""Oxbow — XFA datasets fill and save.

Writes typed values into the XFA <datasets> packet so Adobe Reader (and any
XFA-aware viewer) shows them, then saves the PDF with every other structure
untouched.

Clean-room: datasets layout follows the public XFA spec and the structure
Adobe itself writes into empty IRCC forms (observed from the forms' own
shipped empty state, not from Adobe's program code).
"""
from __future__ import annotations

import copy
import posixpath

from lxml import etree
import pikepdf

from . import xfa as xfa_mod
from .layout import iter_widgets

DATA_NS = "http://www.xfa.org/schema/xfa-data/1.0/"
NSMAP = {"xfa": DATA_NS}


def _local(tag):
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1]


def _path_segments(som: str):
    """'a.b[2].c' -> ['a', 'b', 2, 'c']"""
    segs = []
    for part in som.split("."):
        if "[" in part:
            name, idx = part[:-1].split("[")
            segs.append(name)
            segs.append(int(idx))
        else:
            segs.append(part)
    return segs


def _get_or_create_group(parent, name, index=0):
    """Return the index-th child element named `name`, creating empty ones."""
    matches = [c for c in parent if _local(c.tag) == name or c.tag == name]
    if index < len(matches):
        return matches[index]
    # create placeholders for any missing earlier instances too
    while len(matches) <= index:
        el = etree.SubElement(parent, name)
        el.set("{%s}dataNode" % DATA_NS, "dataGroup")
        matches.append(el)
    return matches[index]


def set_data_value(data_root, som: str, value: str):
    """Set a leaf value in the datasets tree, creating groups as needed."""
    segs = _path_segments(som)
    node = data_root
    i = 0
    while i < len(segs):
        seg = segs[i]
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if isinstance(nxt, int):
            node = _get_or_create_group(node, seg, nxt)
            i += 2
        else:
            children = [c for c in node if _local(c.tag) == seg or c.tag == seg]
            if children:
                node = children[0]
            else:
                node = etree.SubElement(node, seg)
            i += 1
    # set text content
    node.text = value
    # clear dataNode marker when it now holds a value
    dn = "{%s}dataNode" % DATA_NS
    if node.get(dn):
        del node.attrib[dn]


def build_datasets(template_xml: bytes, values: dict[str, str]) -> bytes:
    """Build a full <xfa:datasets> packet from template skeleton + values."""
    root_name = xfa_mod.root_subform_name(template_xml)
    ds = etree.Element("{%s}datasets" % DATA_NS, nsmap=NSMAP)
    data = etree.SubElement(ds, "{%s}data" % DATA_NS)
    form_root = etree.SubElement(data, root_name)
    for som, v in values.items():
        if v is None or v == "":
            continue
        parts = som.split(".")
        if parts and parts[0] == root_name:
            som = ".".join(parts[1:])
        set_data_value(form_root, som, v)
    return etree.tostring(ds, xml_declaration=False)


def merge_datasets(existing_xml: bytes, values: dict[str, str]) -> bytes:
    """Fill values into an existing datasets packet, preserving its structure."""
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.fromstring(existing_xml, parser)
    data = tree.find("{%s}data" % DATA_NS)
    if data is None:
        data = tree  # tolerate packets without the xfa: prefix
    for som, v in values.items():
        set_data_value(data, som, v)
    return etree.tostring(tree, xml_declaration=False)


XFA_ORDER = ["xdp:xdp", "config", "template", "localeSet", "xmpmeta",
             "PDFSecurity", "form", "datasets", "/xdp:xdp"]


def save_filled(src_path: str, dst_path: str, values: dict[str, str]):
    """Open src, write values into XFA datasets, save to dst.

    The stale <form> render-cache packet is removed so viewers regenerate
    layout from the updated data (dynamic XFA is rebuilt on open anyway).
    """
    pdf = pikepdf.open(src_path)
    packets = xfa_mod.read_packets(pdf)
    if "template" not in packets:
        raise ValueError("not a dynamic XFA form: no template packet")

    if "datasets" in packets:
        new_datasets = merge_datasets(packets["datasets"], values)
    else:
        new_datasets = build_datasets(packets["template"], values)

    # Single-stream XFA (ISO 32000-1 allows stream OR array): concatenate the
    # XDP packets in spec order into one stream.  Every reader handles this
    # form, and it sidesteps packet-name lookup quirks in open-source viewers.
    acro = pdf.Root.get("/AcroForm")
    old_xfa = acro.get("/XFA")
    pairs = []
    if isinstance(old_xfa, pikepdf.Array):
        for i in range(0, len(old_xfa), 2):
            pairs.append((str(old_xfa[i]).lstrip("/"), old_xfa[i + 1]))
    if not pairs:
        raise ValueError("empty XFA array")

    def norm(name):
        return name.lstrip("/")

    def stream_text(obj):
        return obj.read_bytes()

    parts = {}
    for n, s in pairs:
        parts[norm(n)] = s

    order = ["xdp:xdp", "preamble", "config", "template", "localeSet",
             "xmpmeta", "PDFSecurity", "form", "datasets", "postamble",
             "</xdp:xdp>"]
    chunks = []
    for key in order:
        if key == "form":
            continue  # stale render cache — rebuilt by viewers on open
        if key == "datasets":
            chunks.append(new_datasets)
            continue
        if key in parts:
            chunks.append(stream_text(parts[key]))
    # keep any unexpected packets so nothing is silently lost
    for n, s in pairs:
        if norm(n) not in order and norm(n):
            chunks.append(stream_text(s))
    acro.XFA = pdf.make_stream(b"".join(chunks))
    pdf.save(dst_path)
    pdf.close()


def read_back(path: str) -> dict[str, str]:
    """Read every leaf value out of the saved file's datasets packet."""
    pdf = pikepdf.open(path)
    packets = xfa_mod.read_packets(pdf)
    pdf.close()
    if "datasets" not in packets:
        return {}
    root = etree.fromstring(packets["datasets"])
    out = {}

    def walk(el, path):
        kids = list(el)
        text = (el.text or "").strip()
        if not kids and text:
            out[path] = text
        elif kids:
            names = [(_local(c.tag) or str(c.tag)) for c in kids]
            seen = {}
            for c, nm in zip(kids, names):
                idx = seen.get(nm, 0)
                seen[nm] = idx + 1
                dup_total = names.count(nm)
                # first instance keeps the bare name; later ones get [n]
                suffix = f"[{idx}]" if dup_total > 1 and idx > 0 else ""
                walk(c, f"{path}.{nm}{suffix}")

    data = root.find("{%s}data" % DATA_NS)
    base = data if data is not None else root
    for top in base:
        walk(top, _local(top.tag) or top.tag)
    return out


def _widget_text(obj) -> str:
    value = obj.get("/V")
    if value is None:
        parent = obj.get("/Parent")
        if parent is not None:
            value = parent.get("/V")
    if value is None:
        return ""
    return str(value).lstrip("/")


def read_acro_values(path: str) -> dict[str, str]:
    """Read AcroForm widget values. Ordinary PDFs only; XFA uses read_back."""
    pdf = pikepdf.open(path)
    found = {}
    for _page_index, obj, key in iter_widgets(pdf):
        text = _widget_text(obj)
        if text and text != "Off":
            found[key] = text
    pdf.close()
    return found


def save_acro_values(src_path: str, dst_path: str, values: dict[str, str]):
    """Write AcroForm /V entries. Does not touch the XFA datasets save."""
    pdf = pikepdf.open(src_path)
    for _page_index, obj, key in iter_widgets(pdf):
        if key not in values:
            continue
        raw = values.get(key) or ""
        parent = obj.get("/Parent")
        ft = obj.get("/FT")
        flags = obj.get("/Ff")
        if ft is None and parent is not None:
            ft = parent.get("/FT")
        if flags is None and parent is not None:
            flags = parent.get("/Ff")
        try:
            flags = int(flags or 0)
        except (TypeError, ValueError):
            flags = 0
        if str(ft or "") == "/Btn":
            name = pikepdf.Name(f"/{raw}") if raw else pikepdf.Name("/Off")
            obj["/AS"] = name
            if parent is not None and flags & (1 << 15):
                parent["/V"] = name
            else:
                obj["/V"] = name
        else:
            obj["/V"] = pikepdf.String(raw)
    acro = pdf.Root.get("/AcroForm")
    if acro is not None:
        acro["/NeedAppearances"] = True
    pdf.save(dst_path)
    pdf.close()
