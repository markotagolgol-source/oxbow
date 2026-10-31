"""Drive the real window: in-place typing, save, reopen, screenshot.

Run on the desktop session, not a headless shell:

    DISPLAY=:0 python3 -m oxbow.proof_ui
"""
from __future__ import annotations

import os
import statistics
import subprocess
import time
from pathlib import Path

from PIL import Image


OUT = Path(__file__).resolve().parents[1] / "proof"
SAMPLES = ["OXBOW-ONE", "OXBOW-TWO", "2020-04"]


def ensure_display():
    os.environ.setdefault("DISPLAY", ":0")
    if os.environ.get("XAUTHORITY"):
        return
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        names = os.listdir(runtime)
    except OSError:
        return
    for name in names:
        if name.startswith(".mutter-Xwaylandauth") or name.startswith(".Xauthority"):
            os.environ["XAUTHORITY"] = os.path.join(runtime, name)
            return


def text_fields(app, count=3):
    found = []
    for page in app.pages:
        for block in page.blocks:
            if block.kind == "field" and block.ui in ("textEdit", "picture", "dateTimeEdit") and not block.read_only:
                found.append(block)
                if len(found) == count:
                    return found
    return found


def wait_ready(app, timeout=8):
    start = time.time()
    while time.time() - start < timeout:
        app.update()
        photo = app._photo
        if photo is not None and app.canvas.winfo_width() > 200 and app._ppm > 2:
            return True
        time.sleep(0.05)
    return False


def force_large(app):
    mon = app._primary_monitor()
    if not mon:
        return
    width, height, x, y, _primary = mon
    too_wide = app.winfo_width() > width + 80
    too_small = app.winfo_width() < width * 0.75 or app.winfo_height() < height * 0.7
    if too_wide or too_small:
        try:
            app.state("normal")
        except Exception:
            pass
        app.geometry(f"{width - 8}x{height - 40}+{x}+{y}")
        app.update_idletasks()
        app.update()
    try:
        app._paned.sashpos(0, 300)
    except Exception:
        pass
    app._blit(app._active_path)
    app.update()


def type_into(app, block, text, commit: bool):
    app._activate(block)
    app.update_idletasks()
    app.update()
    widget = app._widget
    if widget is None:
        raise SystemExit(f"No in-place editor for {block.path} ({block.ui})")
    widget.focus_set()
    widget.delete(0, "end")
    widget.insert(0, text)
    widget.icursor("end")
    widget.focus_set()
    app.update_idletasks()
    app.update()
    if widget.get() != text:
        raise SystemExit(f"Editor did not show {text!r} for {block.path}: {widget.get()!r}")
    if commit:
        widget.event_generate("<Return>")
        app.update_idletasks()
        app.update()
        if app.values.get(block.path) != text:
            raise SystemExit(f"Enter did not commit {text!r} into {block.path}: {app.values.get(block.path)!r}")
        nxt = app._editing or app._focused
        print(" next-field", nxt.path if nxt is not None else None, getattr(nxt, "ui", ""))


def click_block(app, block):
    from .render import input_box
    x, y, w, h = input_box(block)
    px = app._origin_x + (x + min(2.2, w / 2)) * app._ppm
    py = app._origin_y + (y + min(2.2, h / 2)) * app._ppm
    cx = int(round(px - app.canvas.canvasx(0)))
    cy = int(round(py - app.canvas.canvasy(0)))
    app.canvas.event_generate("<Button-1>", x=cx, y=cy)
    app.update_idletasks()
    app.update()


def editor_box(app):
    widget = app._widget
    if widget is None:
        return None
    dx = widget.winfo_rootx() - app.winfo_rootx()
    dy = widget.winfo_rooty() - app.winfo_rooty()
    return dx, dy, max(1, widget.winfo_width()), max(1, widget.winfo_height())


def variance(image: Image.Image, box=None) -> float:
    crop = image
    if box is not None:
        width, height = image.size
        x0, y0, x1, y1 = (int(v) for v in box)
        x0 = max(0, min(width - 1, x0))
        y0 = max(0, min(height - 1, y0))
        x1 = max(x0 + 1, min(width, x1))
        y1 = max(y0 + 1, min(height, y1))
        crop = image.crop((x0, y0, x1, y1))
    sample = crop.resize((48, 48)).convert("L")
    pixels = sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata()
    return statistics.pstdev(list(pixels))


def grab_ximage(window_id: int):
    """Read one X11 window's pixels. Returns None when the grab fails."""
    import ctypes

    x11 = ctypes.CDLL("libX11.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XGetGeometry.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XGetGeometry.restype = ctypes.c_int
    x11.XGetImage.restype = ctypes.c_void_p
    x11.XGetImage.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
    ]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

    class XImage(ctypes.Structure):
        _fields_ = [
            ("width", ctypes.c_int),
            ("height", ctypes.c_int),
            ("xoffset", ctypes.c_int),
            ("format", ctypes.c_int),
            ("data", ctypes.c_void_p),
            ("byte_order", ctypes.c_int),
            ("bitmap_unit", ctypes.c_int),
            ("bitmap_bit_order", ctypes.c_int),
            ("bitmap_pad", ctypes.c_int),
            ("depth", ctypes.c_int),
            ("bytes_per_line", ctypes.c_int),
            ("bits_per_pixel", ctypes.c_int),
            ("_pad", ctypes.c_int),
            ("red_mask", ctypes.c_ulong),
            ("green_mask", ctypes.c_ulong),
            ("blue_mask", ctypes.c_ulong),
        ]

    display = x11.XOpenDisplay(None)
    if not display:
        return None
    try:
        root = ctypes.c_ulong()
        x = ctypes.c_int()
        y = ctypes.c_int()
        w = ctypes.c_uint()
        h = ctypes.c_uint()
        bw = ctypes.c_uint()
        depth = ctypes.c_uint()
        ok = x11.XGetGeometry(
            display, ctypes.c_ulong(window_id), ctypes.byref(root),
            ctypes.byref(x), ctypes.byref(y), ctypes.byref(w), ctypes.byref(h),
            ctypes.byref(bw), ctypes.byref(depth),
        )
        if not ok or w.value < 2 or h.value < 2:
            return None
        x11.XSync(display, 0)
        raw = x11.XGetImage(display, ctypes.c_ulong(window_id), 0, 0, w.value, h.value, ctypes.c_ulong(0xFFFFFFFF), 2)
        if not raw:
            return None
        info = ctypes.cast(raw, ctypes.POINTER(XImage)).contents
        if info.width != w.value or info.height != h.value or not info.data or info.bits_per_pixel not in (24, 32):
            return None
        size = info.bytes_per_line * info.height
        blob = ctypes.string_at(info.data, size)
        mode = "BGRX" if info.bits_per_pixel == 32 else "BGR"
        if info.red_mask == 0xFF and info.blue_mask in (0xFF0000, 0xFF000000):
            mode = "RGBX" if info.bits_per_pixel == 32 else "RGB"
        image = Image.frombytes("RGB", (info.width, info.height), blob, "raw", mode, info.bytes_per_line, 1 if info.byte_order == 1 else 0)
        return image
    except Exception as exc:
        print(" ximage", exc)
        return None
    finally:
        x11.XCloseDisplay(display)


def gnome_area(path: Path, x, y, w, h) -> bool:
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell.Screenshot",
                "--object-path", "/org/gnome/Shell/Screenshot",
                "--method", "org.gnome.Shell.Screenshot.ScreenshotArea",
                str(int(x)), str(int(y)), str(int(w)), str(int(h)),
                "false", str(path),
            ],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(" gnome area", exc)
        return False
    print(" gnome", result.returncode, (result.stdout or result.stderr).strip()[:200])
    return result.returncode == 0 and path.exists() and path.stat().st_size > 1000


def capture(app, dest: Path) -> str:
    """Save a full-window image. Returns a short note on how it was taken."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    app.update_idletasks()
    app.update()
    time.sleep(0.25)
    app.update()
    box = editor_box(app)
    rx, ry = app.winfo_rootx(), app.winfo_rooty()
    rw, rh = app.winfo_width(), app.winfo_height()
    gnome_path = dest.with_name(dest.stem + "-gnome.png")
    if gnome_area(gnome_path, rx, ry, rw, rh):
        image = Image.open(gnome_path).convert("RGB")
        if variance(image) > 4:
            region = None
            if box:
                region = (box[0], box[1], box[0] + box[2], box[1] + box[3])
            if region is None or variance(image, region) > 3:
                image.save(dest)
                return f"gnome area {image.size} editor-variance {variance(image, region) if region else 'n/a'}"
    top = grab_ximage(app.winfo_id())
    note = "ximage"
    if top is None or variance(top) < 2:
        return "FAILED window grab was empty or black"
    if box and app._widget is not None:
        child = grab_ximage(app._widget.winfo_id())
        if child is not None and variance(child) > 2:
            top.paste(child, (max(0, box[0]), max(0, box[1])))
            note = "ximage+editor"
        elif variance(top, (box[0], box[1], box[0] + box[2], box[1] + box[3])) < 2:
            note = "ximage editor region looks flat"
    top.save(dest)
    return f"{note} {top.size}"


def fill_three(app, src: Path, label: str, shoot_edit: bool):
    fields = text_fields(app, 3)
    if len(fields) < 3:
        raise SystemExit(f"{label}: only {len(fields)} text fields")
    app.index = 0
    app.highlight = ""
    app._active_path = ""
    app._blit()
    app.update()
    note = capture(app, OUT / f"{label}-layout.png")
    print(label, "layout-shot", note, "pages", len(app.pages))
    if note.startswith("FAILED"):
        raise SystemExit(note)
    if len(app.pages) > 1:
        app.index = 1
        app._blit()
        app.update()
        note = capture(app, OUT / f"{label}-page2.png")
        print(label, "page2-shot", note)
        if note.startswith("FAILED"):
            raise SystemExit(note)
        app.index = 0
        app._blit()
        app.update()
    print(label, "window", app.winfo_width(), app.winfo_height(), "canvas", app.canvas.winfo_width(), app.canvas.winfo_height(), "ppm", round(app._ppm, 2))
    print(label, "page", app._photo.width(), "x", app._photo.height(), "dpi", round(app._screen_dpi, 1), "scaling", app.tk.call("tk", "scaling"))
    labels = [app.outline.get(i) for i in range(app.outline.size())]
    print(label, "pages", labels)
    if labels != [f"Page {i + 1}" for i in range(len(labels))]:
        raise SystemExit(f"{label} page list {labels}")
    if len(labels) != len(set(labels)):
        raise SystemExit(f"{label} duplicate page labels")
    page_h = app._photo.height()
    if page_h < app.canvas.winfo_height() * 0.72 and app._photo.width() < app.canvas.winfo_width() * 0.72:
        raise SystemExit(f"{label} page is still small in the window: {app._photo.width()}x{page_h} canvas {app.canvas.winfo_width()}x{app.canvas.winfo_height()}")
    paths = []
    for index, (block, sample) in enumerate(zip(fields, SAMPLES)):
        type_into(app, block, sample, commit=False)
        if shoot_edit and index == 0:
            note = capture(app, OUT / f"{label}-editing.png")
            print(label, "edit-shot", note)
            if note.startswith("FAILED"):
                raise SystemExit(note)
        type_into(app, block, sample, commit=True)
        paths.append(block.path)
        print(" typed", sample, "->", block.path, "now", app.values.get(block.path))
    started = time.perf_counter()
    app.set_zoom(1.2)
    app.update()
    zoom_s = time.perf_counter() - started
    grown = app._photo.width()
    app.set_zoom(1.0)
    app.update()
    print(label, "zoom redraw", round(zoom_s, 3), "s, width", grown)
    app.find_var.set("name")
    app.find_next()
    app.update()
    print(label, "find", app.status.cget("text"))
    saved = OUT / f"{label}-filled.pdf"
    missing = app.save_to(str(saved), dialogs=False)
    if missing is None or missing:
        raise SystemExit(f"{label} save failed {missing}")
    app.open_path(str(saved))
    wait_ready(app)
    force_large(app)
    for path, sample in zip(paths, SAMPLES):
        if app.values.get(path) != sample:
            raise SystemExit(f"{label} reopen lost {path}: {app.values.get(path)!r}")
    # Leave the page painted with the values, no editor covering them.
    app._commit_live()
    app._active_path = ""
    app._blit()
    app.update()
    note = capture(app, OUT / f"{label}-reopened.png")
    print(label, "reopen-shot", note, "values", {path: app.values.get(path) for path in paths})
    if note.startswith("FAILED"):
        raise SystemExit(note)
    return paths


def exercise_clicks(app):
    check = next((block for block in app._page_fields() if block.ui == "checkButton"), None)
    if check is not None:
        before = app.values.get(check.path)
        click_block(app, check)
        after = app.values.get(check.path)
        print(" checkbox", check.path, before, "->", after)
        if after == before:
            raise SystemExit(f"checkbox click did not toggle {check.path}")
        click_block(app, check)
    radio = next((block for block in app._editable_fields() if block.ui == "radio"), None)
    if radio is not None:
        # Show that radio on screen if it is on another page.
        if radio.page != app.index:
            app.index = radio.page
            app._blit()
            app.update()
        click_block(app, radio)
        print(" radio", radio.path, app.values.get(radio.path), "on", radio.on_value)
        if app.values.get(radio.path) != radio.on_value:
            raise SystemExit(f"radio click did not select {radio.path}")
        siblings = [
            other.path for page in app.pages for other in page.blocks
            if other.group == radio.group and other.path != radio.path and app.values.get(other.path)
        ]
        if siblings:
            raise SystemExit(f"radio siblings still set {siblings}")


def run_one(src: Path, label: str, shoot_edit: bool):
    from .app import Reader
    app = Reader(str(src))
    try:
        if not wait_ready(app):
            raise SystemExit(f"{label} window never painted")
        force_large(app)
        if not wait_ready(app):
            raise SystemExit(f"{label} window never painted after place")
        fill_three(app, src, label, shoot_edit)
        exercise_clicks(app)
    finally:
        app.destroy()


def main():
    ensure_display()
    OUT.mkdir(parents=True, exist_ok=True)
    example = Path(__file__).resolve().parents[1] / "examples" / "harbor-picnic.pdf"
    sources = [Path(item) for item in sys.argv[1:]] or [example]
    for index, src in enumerate(sources):
        run_one(src, src.stem, index == 0)
    print("UI PROOF OK")
    for path in sorted(OUT.glob("*.png")):
        print(" ", path, path.stat().st_size)


if __name__ == "__main__":
    main()
