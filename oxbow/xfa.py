"""Oxbow — XFA form field discovery.

Parses the XFA template packet of a dynamic XFA PDF and produces the fillable
field list: SOM-style path, UI type, bound-data flag, read-only flag, and the
choice/check items where the template defines them.

Clean-room implementation based on the public XFA Specification (Adobe XFA
Spec 3.3) and the ISO 32000-1 description of the AcroForm /XFA entry.  No
Adobe code is used or derived from.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from lxml import etree
import pikepdf

XFA_NS = "http://www.xfa.org/schema/xfa-template/"
DATA_NS = "http://www.xfa.org/schema/xfa-data/1.0/"
FORM_NS = "http://www.xfa.org/schema/xfa-form/2.8/"

UI_TYPES = ("textEdit", "checkButton", "choiceList", "dateTimeEdit", "picture", "barcode", "imageEdit")


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1]


@dataclass
class XfaField:
    path: str            # SOM-style dotted path, duplicates indexed [n]
    ui: str              # textEdit / checkButton / choiceList / dateTimeEdit / button / none
    name: str
    read_only: bool = False
    bind_match: str | None = None   # None means default (data); 'none' = not in datasets
    items: list[str] = dc_field(default_factory=list)  # choice/check legal values
    calculate: bool = False


def read_packets(pdf) -> dict:
    """Return {packet_name: bytes} from /Root/AcroForm/XFA."""
    xfa = pdf.Root.get("/AcroForm").get("/XFA")
    packets = {}
    if xfa is None:
        return packets
    if isinstance(xfa, pikepdf.Array):
        for i in range(0, len(xfa), 2):
            name = str(xfa[i]).lstrip("/")
            if name in ("xdp:xdp", "preamble", "postamble") or name.startswith("<"):
                continue  # XDP wrapper opener/closer markers, not packets
            packets[name] = xfa[i + 1].read_bytes()
    else:
        # single-stream XFA: one stream holds the whole XDP document
        root = etree.fromstring(xfa.read_bytes())
        for child in root:
            name = _local(child.tag)
            if not name or name in ("xdp:xdp", "preamble", "postamble"):
                continue
            packets[name] = etree.tostring(child)
    return packets


def _som_path(el) -> str:
    """Build the dotted SOM path with [n] indexes on repeated sibling names."""
    chain = []  # (name, index_among_same_name) for container elements
    cur = el
    while cur is not None:
        ln = _local(cur.tag)
        if ln in ("subform", "area", "field", "exclGroup") and cur.get("name"):
            chain.append(cur.get("name"))
        cur = cur.getparent()
    chain.reverse()
    # count duplicates among non-field containers to know row indexes
    out = []
    for i, name in enumerate(chain):
        if i == 0:
            out.append(name)
            continue
        # how many earlier elements in chain share the name?
        dup = chain[:i].count(name)
        out.append(f"{name}[{dup}]" if chain.count(name) > 1 else name)
    return ".".join(out)


def parse_template(template_xml: bytes) -> list[XfaField]:
    root = etree.fromstring(template_xml)
    fields = []
    for f in [e for e in root.iter() if _local(e.tag) == "field"]:
        ui_el = [c for c in f if _local(c.tag) == "ui"]
        ui = _local(list(ui_el[0])[0].tag) if ui_el and len(list(ui_el[0])) else "none"
        bind_el = [c for c in f if _local(c.tag) == "bind"]
        bind_match = bind_el[0].get("match") if bind_el else None
        items = []
        for it in [c for c in f if _local(c.tag) == "items"]:
            if it.get("presence", "visible") == "hidden":
                continue
            for v in it:
                if _local(v.tag) == "text" and v.text:
                    items.append(v.text)
        fields.append(XfaField(
            path=_som_path(f),
            ui=ui,
            name=f.get("name") or "",
            read_only=(f.get("access") == "readOnly"),
            bind_match=bind_match,
            items=items,
            calculate=any(_local(c.tag) == "calculate" for c in f),
        ))
    return fields


def fillable(fields: list[XfaField]) -> list[XfaField]:
    """Fields a human can type into: data-bound, not read-only, not calculated."""
    out = []
    for f in fields:
        if f.ui not in UI_TYPES:
            continue
        if f.read_only or f.calculate or f.bind_match == "none":
            continue
        out.append(f)
    return out


def root_subform_name(template_xml: bytes) -> str:
    root = etree.fromstring(template_xml)
    for e in root:
        if _local(e.tag) == "subform":
            return e.get("name") or "form1"
    return "form1"
