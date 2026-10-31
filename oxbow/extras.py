"""Bookmarks, notes, and signature status. No Adobe code."""
from __future__ import annotations

import hashlib
import json

import pikepdf


def outline_titles(pdf) -> list[str]:
    titles = []
    try:
        with pdf.open_outline() as outline:
            def walk(node):
                for item in node:
                    title = getattr(item, "title", None)
                    if title:
                        titles.append(str(title))
                    walk(item)
            walk(outline.root)
    except Exception:
        return titles
    return titles


def load_notes(pdf) -> list[dict]:
    try:
        raw = pdf.attachments["oxbow-notes.json"].get_file().read_bytes()
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_notes(pdf, notes: list[dict]):
    payload = json.dumps(notes, indent=2).encode("utf-8")
    try:
        pdf.attachments["oxbow-notes.json"] = payload
    except Exception:
        pdf.attachments.add("oxbow-notes.json", payload)


def signature_status(path: str) -> str:
    """Say whether a usage-rights or approval signature still matches its byte ranges.

    This checks the digest published in the PDF. It does not create a signature.
    """
    data = open(path, "rb").read()
    try:
        pdf = pikepdf.open(path)
    except Exception as exc:
        return f"Could not open the file: {exc}"
    try:
        root = pdf.Root
        perms = root.get("/Perms")
        sig = None
        if perms is not None:
            perms = perms.get_object() if hasattr(perms, "get_object") else perms
            if "/UR3" in perms:
                sig = perms["/UR3"]
        if sig is None:
            pdf.close()
            if b"/ByteRange" not in data:
                return "This file has no digital signature."
            return "A signature dictionary is present, but Oxbow could not read a /UR3 usage-rights signature."
        sig = sig.get_object() if hasattr(sig, "get_object") else sig
        byte_range = [int(n) for n in sig.get("/ByteRange", [])]
        name = str(sig.get("/Name", ""))
        when = str(sig.get("/M", ""))
        pdf.close()
        if len(byte_range) != 4:
            return "Signature has no usable byte range."
        start_a, len_a, start_b, len_b = byte_range
        end = start_b + len_b
        signed = data[start_a:start_a + len_a] + data[start_b:end]
        digest = hashlib.sha1(signed).hexdigest()
        covered = end == len(data)
        extra = len(data) - end
        lines = [
            f"Name: {name or '(none)'}",
            f"Date: {when or '(none)'}",
            f"SHA-1 of the signed byte ranges: {digest}",
        ]
        if covered:
            lines.append("The signature byte range covers the whole file.")
        else:
            lines.append(
                f"The signature does not cover the last {extra} bytes, so a reader that checks usage rights will treat the file as changed."
            )
        lines.append("Oxbow does not create new signatures. That needs your own certificate, which this build does not hold.")
        return "\n".join(lines)
    except Exception as exc:
        try:
            pdf.close()
        except Exception:
            pass
        return f"Could not read the signature: {exc}"
