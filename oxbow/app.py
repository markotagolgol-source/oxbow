"""Oxbow reader window: fit-to-window pages and in-place XFA editing."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
import tkinter as tk
from io import BytesIO
from tkinter import filedialog, messagebox, ttk

import pikepdf
from PIL import Image

from . import extras
from .fill import read_acro_values, read_back, save_acro_values, save_filled
from .layout import PAGE_H, PAGE_W, load_document, load_static_pages
from .render import PRINT_PPM, fit_ppm, hit_test, input_box, render_page, scaled_logo


class Reader(tk.Tk):
    def __init__(self, path: str | None = None):
        super().__init__(className="Oxbow")
        self._apply_screen_dpi()
        self.title("Oxbow")
        try:
            self._window_icon = self._logo_photo(self._icon_slot())
            self.iconphoto(True, self._window_icon)
        except tk.TclError:
            self._window_icon = None
        self.geometry("1180x860+40+40")
        self.minsize(900, 600)
        self.pages = []
        self.xfa = False
        self.values: dict[str, str] = {}
        self.notes: list[dict] = []
        self.index = 0
        # 1.0 means the page is fitted to the window. Zoom steps multiply that.
        self.user_zoom = 1.0
        self._ppm = PRINT_PPM
        self._origin_x = 16
        self._origin_y = 16
        self.path: str | None = None
        self.highlight = ""
        self._active_path = ""
        self._photo = None
        self._static_count = 0
        self._static_dir = ""
        self._static_cache: dict = {}
        self.sig_text = ""
        self._editing = None
        self._widget = None
        self._win = None
        self._focused = None
        self._edit_gen = 0
        self._outline_lock = False
        self._field_lock = False
        self._canvas_size = (0, 0)
        self._resize_job = None
        self._last_resize_paint = 0.0
        self._paint_tries = 0
        self._screen_dpi = 96.0

        self._build()
        if path:
            self.open_path(path)
        self.after(120, self._place_large)

    def _apply_screen_dpi(self):
        """Follow the desktop DPI when Tk is still on its 96 DPI default."""
        try:
            current = float(self.tk.call("tk", "scaling"))
        except tk.TclError:
            return
        dpi = _xft_dpi()
        self._screen_dpi = dpi or (current * 72.0)
        if dpi and dpi >= 144:
            target = dpi / 72.0
            if current < target * 0.85:
                self.tk.call("tk", "scaling", target)

    def _primary_monitor(self):
        try:
            out = subprocess.check_output(["xrandr", "--listmonitors"], text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return None
        found = []
        for line in out.splitlines():
            match = re.search(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", line)
            if not match:
                continue
            w, h, x, y = (int(match.group(i)) for i in range(1, 5))
            found.append((w, h, x, y, "*" in line))
        if not found:
            return None
        primary = [item for item in found if item[4]]
        return primary[0] if primary else max(found, key=lambda item: item[0] * item[1])

    def _place_large(self):
        """Open large on the primary monitor so fit-to-window starts readable."""
        mon = self._primary_monitor()
        if mon:
            width, height, x, y, _primary = mon
            self.geometry(f"900x700+{x + 80}+{y + 60}")
            self.update_idletasks()
            try:
                self.state("zoomed")
            except tk.TclError:
                self.geometry(f"{max(900, width - 40)}x{max(600, height - 80)}+{x + 20}+{y + 20}")
            self.update_idletasks()
            self.update()
            if self.winfo_width() > width + 80:
                try:
                    self.state("normal")
                except tk.TclError:
                    pass
                self.geometry(f"{width - 16}x{height - 48}+{x}+{y}")
                self.update_idletasks()
        try:
            self._paned.sashpos(0, 300)
        except tk.TclError:
            pass
        self._canvas_size = (0, 0)
        self._blit(self._active_path)

    def _logo_slot(self) -> int:
        """Toolbar mark in pixels. 32px at 96 DPI, tracking Tk's font scale."""
        try:
            scaling = float(self.tk.call("tk", "scaling"))
        except tk.TclError:
            scaling = 96 / 72
        slot = int(round(32 * scaling / (96 / 72)))
        return max(24, min(64, slot))

    def _icon_slot(self) -> int:
        """Title-bar mark. The window manager draws it; the file stays 1024."""
        try:
            scaling = float(self.tk.call("tk", "scaling"))
        except tk.TclError:
            scaling = 96 / 72
        slot = int(round(48 * scaling / (96 / 72)))
        return max(32, min(96, slot))

    def _logo_photo(self, size: int) -> tk.PhotoImage:
        image = scaled_logo(size)
        buf = BytesIO()
        image.save(buf, "PNG")
        try:
            return tk.PhotoImage(data=buf.getvalue())
        except tk.TclError:
            path = os.path.join(tempfile.gettempdir(), f"oxbow-logo-{size}.png")
            image.save(path, "PNG")
            return tk.PhotoImage(file=path)

    def _build(self):
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        logo = self._logo_photo(self._logo_slot())
        self._logo = logo
        ttk.Label(bar, image=logo).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Open", command=self.open_dialog).pack(side="left")
        ttk.Button(bar, text="Previous", command=self.prev_page).pack(side="left", padx=2)
        ttk.Button(bar, text="Next", command=self.next_page).pack(side="left")
        self.page_label = ttk.Label(bar, text="No file")
        self.page_label.pack(side="left", padx=8)
        ttk.Button(bar, text="Zoom −", command=lambda: self.set_zoom(self.user_zoom / 1.2)).pack(side="left")
        ttk.Button(bar, text="Zoom +", command=lambda: self.set_zoom(self.user_zoom * 1.2)).pack(side="left", padx=2)
        ttk.Button(bar, text="Fit", command=lambda: self.set_zoom(1.0)).pack(side="left", padx=2)
        ttk.Label(bar, text="Find").pack(side="left", padx=(10, 2))
        self.find_var = tk.StringVar()
        find = ttk.Entry(bar, textvariable=self.find_var, width=22)
        find.pack(side="left")
        find.bind("<Return>", lambda _e: self.find_next())
        ttk.Button(bar, text="Find", command=self.find_next).pack(side="left", padx=2)
        ttk.Button(bar, text="Save as", command=self.save_as).pack(side="right")
        ttk.Button(bar, text="Print", command=self.print_doc).pack(side="right", padx=4)
        ttk.Button(bar, text="Signature", command=self.show_signature).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        self._paned = body

        side = ttk.Frame(body, padding=6)
        view = ttk.Frame(body)
        body.add(side, weight=1)
        body.add(view, weight=5)

        ttk.Label(side, text="Pages").pack(anchor="w")
        self.outline = tk.Listbox(side, height=8, exportselection=False, width=32)
        self.outline.pack(fill="x", pady=4)
        self.outline.bind("<<ListboxSelect>>", self._on_outline)

        ttk.Label(side, text="Fields on this page").pack(anchor="w", pady=(8, 0))
        self.field_caption = ttk.Label(side, text="Click a box on the page to type", wraplength=280)
        self.field_caption.pack(anchor="w")
        self.field_list = tk.Listbox(side, height=12, exportselection=False, width=32)
        self.field_list.pack(fill="both", expand=True, pady=4)
        self.field_list.bind("<<ListboxSelect>>", self._on_field_list)

        ttk.Label(side, text="Notes on this document").pack(anchor="w", pady=(8, 0))
        self.note_list = tk.Listbox(side, height=5, exportselection=False)
        self.note_list.pack(fill="x")
        self.note_entry = ttk.Entry(side)
        self.note_entry.pack(fill="x", pady=2)
        ttk.Button(side, text="Add note", command=self.add_note).pack(anchor="w")

        self.canvas = tk.Canvas(view, background="#cfcabe", highlightthickness=0, takefocus=True)
        ybar = ttk.Scrollbar(view, orient="vertical", command=self.canvas.yview)
        xbar = ttk.Scrollbar(view, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Tab>", self._on_canvas_tab)
        self.canvas.bind("<Shift-Tab>", self._on_canvas_tab)
        self.canvas.bind("<Return>", self._on_canvas_return)
        self.canvas.bind("<space>", self._on_canvas_space)
        self.canvas.bind("<Configure>", self._on_configure)
        self.bind("<Prior>", lambda _e: self.prev_page())
        self.bind("<Next>", lambda _e: self.next_page())

        self.status = ttk.Label(self, text="Open a PDF", anchor="w")
        self.status.pack(fill="x")

    def open_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf"), ("All", "*")])
        if path:
            self.open_path(path)

    def open_path(self, path: str):
        self._commit_live()
        self._destroy_editor()
        self._focused = None
        self._active_path = ""
        pdf = pikepdf.open(path)
        self.path = path
        self.pages = load_document(pdf)
        self.xfa = bool(self.pages)
        if not self.xfa:
            self.pages = load_static_pages(pdf)
        titles = extras.outline_titles(pdf)
        if self.pages and len(titles) == len(self.pages) and len(set(titles)) == len(titles):
            for page, title in zip(self.pages, titles):
                page.title = title
        if self.xfa:
            self.values = read_back(path)
        else:
            self.values = {}
            for page in self.pages:
                for block in page.blocks:
                    if block.baseline:
                        self.values[block.path] = block.baseline
        self.notes = extras.load_notes(pdf)
        self.sig_text = ""
        self._static_count = 0 if self.xfa else len(self.pages)
        self._static_cache = {}
        self._static_dir = "" if self.xfa else tempfile.mkdtemp(prefix="oxbow-pages-")
        pdf.close()
        self.index = 0
        self.highlight = ""
        self.user_zoom = 1.0
        self._fill_outline()
        self._fill_notes()
        self.title(f"Oxbow — {os.path.basename(path)}")
        self.update_idletasks()
        self._blit()
        kind = "XFA form" if self.xfa else "PDF pages"
        count = len(self.pages) or self._static_count
        self.status.config(text=f"{path}  ·  {kind}  ·  {count} pages")

    def _fill_outline(self):
        self._outline_lock = True
        self.outline.delete(0, "end")
        if self.pages:
            for page in self.pages:
                self.outline.insert("end", page.title)
        else:
            for i in range(self._static_count):
                self.outline.insert("end", f"Page {i + 1}")
        self._outline_lock = False
        self._sync_outline()

    def _fill_notes(self):
        self.note_list.delete(0, "end")
        for note in self.notes:
            self.note_list.insert("end", f"p{note.get('page', 1)}: {note.get('text', '')[:60]}")

    def _page_fields(self):
        if not self.pages or not (0 <= self.index < len(self.pages)):
            return []
        return [block for block in self.pages[self.index].blocks if block.kind == "field" and not block.read_only]

    def _editable_fields(self):
        found = []
        for page in self.pages:
            for block in page.blocks:
                if block.kind == "field" and not block.read_only:
                    found.append(block)
        return found

    def _fill_fields(self):
        self._field_lock = True
        self.field_list.delete(0, "end")
        for block in self._page_fields():
            label = (block.text or block.path.rsplit(".", 1)[-1]).strip() or block.path
            value = self.values.get(block.path, "")
            if block.ui == "choiceList" and value:
                for choice, saved in block.options:
                    if saved == value:
                        value = choice
                        break
            if block.ui in ("checkButton", "radio"):
                value = "yes" if value == block.on_value and value else ""
            shown = value if value else "·"
            self.field_list.insert("end", f"{label[:42]}   {shown[:28]}")
        self._field_lock = False

    def _sync_outline(self):
        if self.outline.size() <= 0:
            return
        if not (0 <= self.index < self.outline.size()):
            return
        self._outline_lock = True
        self.outline.selection_clear(0, "end")
        self.outline.selection_set(self.index)
        self.outline.activate(self.index)
        self.outline.see(self.index)
        self._outline_lock = False

    def _select_field_row(self, block):
        fields = self._page_fields()
        self._field_lock = True
        self.field_list.selection_clear(0, "end")
        for i, item in enumerate(fields):
            if item.path == block.path:
                self.field_list.selection_set(i)
                self.field_list.see(i)
                break
        self._field_lock = False
        self.field_caption.config(text=block.text or block.path)

    def _on_outline(self, _event):
        if self._outline_lock:
            return
        sel = self.outline.curselection()
        if not sel or sel[0] == self.index:
            return
        self._commit_live()
        self._focused = None
        self._active_path = ""
        self.index = sel[0]
        self._blit()

    def _on_field_list(self, _event):
        if self._field_lock:
            return
        sel = self.field_list.curselection()
        fields = self._page_fields()
        if not sel or sel[0] >= len(fields):
            return
        block = fields[sel[0]]
        if self._editing is not None and self._editing.path == block.path:
            return
        if self._widget is None and self._focused is not None and self._focused.path == block.path:
            return
        self._activate(block)

    def set_zoom(self, zoom: float):
        self.user_zoom = min(2.5, max(0.6, zoom))
        self.redraw(keep_edit=self._editing is not None)

    def prev_page(self):
        self._commit_live()
        self._focused = None
        self._active_path = ""
        if self.index > 0:
            self.index -= 1
            self._blit()

    def next_page(self):
        self._commit_live()
        self._focused = None
        self._active_path = ""
        last = (len(self.pages) or self._static_count) - 1
        if self.index < last:
            self.index += 1
            self._blit()

    def _display_ppm(self) -> float | None:
        """Pixels per millimetre for one render of the whole page in this window."""
        self.update_idletasks()
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        page = self.pages[self.index] if self.pages and 0 <= self.index < len(self.pages) else None
        page_w = getattr(page, "page_w", 0) or PAGE_W
        page_h = getattr(page, "page_h", 0) or PAGE_H
        return fit_ppm(width, height, page_w, page_h, self.user_zoom)

    def _report_layout(self, page) -> None:
        issues = getattr(page, "layout_issues", None) or []
        if issues:
            self.status.config(text="Layout check: " + "; ".join(issues[:3]))

    def _zoom_label(self) -> str:
        if abs(self.user_zoom - 1.0) < 0.03:
            return "Fit"
        return f"Zoom {int(round(self.user_zoom * 100))}%"

    def _blit(self, active: str = "") -> bool:
        self._active_path = active
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 40 or ch < 40:
            self._paint_tries += 1
            if self._paint_tries < 50:
                self.after(40, lambda a=active: self._blit(a))
            return False
        self._paint_tries = 0
        if self._editing is not None and self._widget is not None:
            self._store(self._editing, self._editor_value())
        self._destroy_editor()
        count = len(self.pages) or self._static_count
        if count:
            self.index = min(self.index, count - 1)
        if self.pages and self.xfa:
            ppm = self._display_ppm()
            if ppm is None:
                self._paint_tries += 1
                if self._paint_tries < 50:
                    self.after(40, lambda a=active: self._blit(a))
                return False
            self._ppm = ppm
            image = render_page(self.pages[self.index], self.values, ppm, self.highlight, active)
            self.page_label.config(text=f"Page {self.index + 1} of {len(self.pages)}    {self._zoom_label()}")
            self._report_layout(self.pages[self.index])
        elif self.pages and self.path:
            ppm = self._display_ppm()
            if ppm is None:
                self._paint_tries += 1
                if self._paint_tries < 50:
                    self.after(40, lambda a=active: self._blit(a))
                return False
            raster = self._raster_page(self.index, ppm)
            if raster is None:
                self.page_label.config(text="No pages")
                self.status.config(text="pdftoppm produced no pages for this PDF.")
                return False
            page = self.pages[self.index]
            self._ppm = raster.width / max(page.page_w, 0.1)
            image = render_page(
                page, self.values, self._ppm, self.highlight, active, background=raster,
            )
            self.page_label.config(text=f"Page {self.index + 1} of {len(self.pages)}    {self._zoom_label()}")
            self._report_layout(page)
        else:
            self.page_label.config(text="No pages")
            return False
        page_w, page_h = image.size
        # Fit reads the canvas after idle resize. Place the page in that size.
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        self._origin_x = max(16, (cw - page_w) // 2) if page_w < cw else 16
        self._origin_y = max(16, (ch - page_h) // 2) if page_h < ch else 16
        frame_path = os.path.join(tempfile.gettempdir(), "oxbow-view.ppm")
        try:
            image.save(frame_path, "PPM")
            self._photo = tk.PhotoImage(file=frame_path)
        except tk.TclError:
            frame_path = os.path.join(tempfile.gettempdir(), "oxbow-view.png")
            image.save(frame_path, "PNG")
            self._photo = tk.PhotoImage(file=frame_path)
        self.canvas.delete("all")
        self._win = None
        self.canvas.create_image(self._origin_x, self._origin_y, image=self._photo, anchor="nw")
        self.canvas.configure(scrollregion=(
            0, 0,
            max(cw, page_w + self._origin_x + 16),
            max(ch, page_h + self._origin_y + 16),
        ))
        if page_w <= cw and page_h <= ch:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        self._fill_fields()
        self._sync_outline()
        if active:
            for block in self._page_fields():
                if block.path == active:
                    self._select_field_row(block)
                    break
        return True

    def _raster_page(self, index: int, ppm: float):
        dpi = max(72, int(round(ppm * 25.4)))
        key = (index, dpi)
        cached = self._static_cache.get(key)
        if cached and os.path.exists(cached):
            return Image.open(cached).convert("RGB")
        os.makedirs(self._static_dir, exist_ok=True)
        stem = os.path.join(self._static_dir, f"p{index + 1}-{dpi}")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-f", str(index + 1), "-l", str(index + 1),
             self.path, stem],
            check=False, capture_output=True,
        )
        made = ""
        for candidate in (
            f"{stem}-{index + 1}.png",
            f"{stem}-{index + 1:02d}.png",
            f"{stem}-1.png",
        ):
            if os.path.exists(candidate):
                made = candidate
                break
        if not made:
            subprocess.run(
                ["pdftocairo", "-png", "-singlefile", "-f", str(index + 1), "-l", str(index + 1),
                 "-r", str(dpi), self.path, stem],
                check=False, capture_output=True,
            )
            made = stem + ".png"
        if not os.path.exists(made):
            subprocess.run(
                ["pdftocairo", "-png", "-f", str(index + 1), "-l", str(index + 1),
                 "-r", str(dpi), self.path, stem],
                check=False, capture_output=True,
            )
            for candidate in (stem + ".png", stem + "-1.png", f"{stem}-{index + 1}.png"):
                if os.path.exists(candidate):
                    made = candidate
                    break
            else:
                return None
        self._static_cache[key] = made
        return Image.open(made).convert("RGB")

    def _event_mm(self, event):
        x = self.canvas.canvasx(event.x) - self._origin_x
        y = self.canvas.canvasy(event.y) - self._origin_y
        ppm = self._ppm or PRINT_PPM
        return x / ppm, y / ppm

    def _commit_live(self):
        if self._editing is None or self._widget is None:
            return
        self._store(self._editing, self._editor_value())
        self._destroy_editor()

    def _destroy_editor(self):
        if self._win is not None:
            self.canvas.delete(self._win)
            self._win = None
        if self._widget is not None:
            try:
                self._widget.destroy()
            except tk.TclError:
                pass
            self._widget = None
        self._editing = None

    def _editor_value(self) -> str:
        if self._widget is None or self._editing is None:
            return ""
        if self._editing.ui == "choiceList":
            label = self._widget.get()
            return dict(self._editing.options).get(label, label)
        return self._widget.get()

    def _store(self, block, value: str):
        if block.ui not in ("checkButton", "radio"):
            value = value.strip()
        if value:
            self.values[block.path] = value
        else:
            self.values.pop(block.path, None)

    def _reveal(self, y_px: float, h_px: float):
        total = max(1, self.canvas.bbox("all")[3] if self.canvas.bbox("all") else 1)
        view_h = max(1, self.canvas.winfo_height())
        top = self.canvas.canvasy(0)
        if y_px < top + 8 or y_px + h_px > top + view_h - 8:
            self.canvas.yview_moveto(max(0.0, (y_px - 40) / total))

    def _place_widget(self, block):
        self._destroy_editor()
        box = input_box(block)
        x, y, w, h = box
        ppm = self._ppm or PRINT_PPM
        ix = self._origin_x + x * ppm
        iy = self._origin_y + y * ppm
        iw = max(28, w * ppm)
        ih = max(20, h * ppm)
        self._reveal(iy, ih)
        inset = 3
        font_px = max(13, int(ih * 0.62))
        value = self.values.get(block.path, "")
        if block.ui == "choiceList":
            widget = ttk.Combobox(self.canvas, state="readonly", font=("DejaVu Sans", -font_px))
            labels = [label for label, _saved in block.options]
            widget.configure(values=labels)
            shown = value
            for label, saved in block.options:
                if saved == value:
                    shown = label
                    break
            if shown:
                widget.set(shown)
            widget.bind("<<ComboboxSelected>>", lambda _e: self._queue_advance(1))
        else:
            widget = tk.Entry(
                self.canvas,
                font=("DejaVu Sans", -font_px),
                bg="#fff4dc",
                fg="#102033",
                insertbackground="#102033",
                insertwidth=2,
                relief="flat",
                highlightthickness=0,
            )
            if value:
                widget.insert(0, value)
                widget.icursor("end")
        widget.bind("<Return>", lambda _e: self._queue_advance(1))
        widget.bind("<KP_Enter>", lambda _e: self._queue_advance(1))
        widget.bind("<Tab>", lambda _e: self._queue_advance(1))
        widget.bind("<Shift-Tab>", lambda _e: self._queue_advance(-1))
        widget.bind("<Escape>", lambda _e: self._queue_close())
        self._widget = widget
        self._editing = block
        self._focused = block
        self._win = self.canvas.create_window(
            ix + inset, iy + inset,
            window=widget, anchor="nw",
            width=max(12, iw - 2 * inset),
            height=max(12, ih - 2 * inset),
        )
        widget.focus_set()
        name = block.text or block.path
        self.status.config(text=f"Editing {name}  ·  Enter or Tab moves to the next field")
        self.field_caption.config(text=name)

    def _activate(self, block):
        self._commit_live()
        if block.page != self.index:
            self.index = block.page
        self._focused = block
        self._active_path = block.path
        self._blit(active=block.path)
        self._select_field_row(block)
        if block.ui in ("checkButton", "radio"):
            self.canvas.focus_set()
            self.status.config(text=f"{block.text or block.path}  ·  click toggles  ·  Tab moves on")
            return
        self._place_widget(block)

    def _queue_advance(self, step: int):
        gen = self._edit_gen
        block = self._editing or self._focused
        value = self._editor_value() if self._widget is not None else None
        self.after_idle(lambda: self._advance_now(gen, step, block, value))
        return "break"

    def _queue_close(self):
        gen = self._edit_gen
        block = self._editing
        value = self._editor_value() if self._widget is not None else None
        self.after_idle(lambda: self._close_now(gen, block, value))
        return "break"

    def _close_now(self, gen, block, value):
        if gen != self._edit_gen:
            return
        self._edit_gen += 1
        if block is not None and value is not None:
            self._store(block, value)
        self._destroy_editor()
        self._focused = block
        self._active_path = ""
        self._blit()
        self._fill_fields()

    def _advance_now(self, gen, step, block, value):
        if gen != self._edit_gen:
            return
        self._edit_gen += 1
        if block is not None and value is not None:
            self._store(block, value)
        self._destroy_editor()
        fields = self._editable_fields()
        if not fields:
            self._blit()
            return
        if block is None:
            nxt = fields[0]
        else:
            paths = [item.path for item in fields]
            try:
                index = paths.index(block.path)
            except ValueError:
                index = -1
            nxt = fields[(index + step) % len(fields)]
        self._focused = None
        self._activate(nxt)

    def _toggle_check(self, block):
        if self.values.get(block.path) == block.on_value:
            self.values.pop(block.path, None)
            state = "cleared"
        else:
            self.values[block.path] = block.on_value
            state = "checked"
        self._focused = block
        self.index = block.page
        self._active_path = block.path
        self._blit(active=block.path)
        self._select_field_row(block)
        self.canvas.focus_set()
        self.status.config(text=f"{block.text or block.path} {state}")

    def _select_radio(self, block):
        for page in self.pages:
            for other in page.blocks:
                if other.kind == "field" and other.group and other.group == block.group and other.path != block.path:
                    self.values.pop(other.path, None)
        self.values[block.path] = block.on_value
        self._focused = block
        self.index = block.page
        self._active_path = block.path
        self._blit(active=block.path)
        self._select_field_row(block)
        self.canvas.focus_set()
        self.status.config(text=f"Selected {block.text or block.path}")

    def on_click(self, event):
        if not self.pages:
            return
        x_mm, y_mm = self._event_mm(event)
        block = hit_test(self.pages[self.index], x_mm, y_mm)
        if block is None:
            self._commit_live()
            self._focused = None
            self._active_path = ""
            self._blit()
            return
        if block.ui == "checkButton":
            self._commit_live()
            self._toggle_check(block)
            return
        if block.ui == "radio":
            self._commit_live()
            self._select_radio(block)
            return
        self._activate(block)

    def _on_canvas_tab(self, event):
        if self._editing is not None or self._focused is None:
            return
        step = -1 if event.state & 0x1 else 1
        return self._queue_advance(step)

    def _on_canvas_return(self, _event):
        if self._editing is not None or self._focused is None:
            return
        return self._queue_advance(1)

    def _on_canvas_space(self, _event):
        block = self._focused
        if block is None or self._editing is not None:
            return
        if block.ui == "checkButton":
            self._toggle_check(block)
            return "break"
        if block.ui == "radio":
            self._select_radio(block)
            return "break"

    def _on_configure(self, event):
        if event.widget is not self.canvas:
            return
        size = (event.width, event.height)
        if size[0] < 40 or size == self._canvas_size:
            return
        self._canvas_size = size
        now = time.monotonic()
        wait = 0.05 - (now - self._last_resize_paint)
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
            self._resize_job = None
        if wait <= 0:
            self._resized()
        else:
            self._resize_job = self.after(int(wait * 1000) + 20, self._resized)

    def _resized(self):
        self._resize_job = None
        self._last_resize_paint = time.monotonic()
        self.redraw(keep_edit=self._editing is not None)

    def redraw(self, keep_edit: bool = False):
        block = self._editing if keep_edit else None
        self._commit_live()
        if not keep_edit:
            self._focused = None
            self._active_path = ""
        active = block.path if block is not None else ""
        self._blit(active)
        if block is not None and block.ui not in ("checkButton", "radio"):
            self._place_widget(block)

    def find_next(self):
        query = self.find_var.get().strip().lower()
        if not query or not self.pages:
            return
        self._commit_live()
        start = self.index
        for offset in range(len(self.pages)):
            page_index = (start + offset) % len(self.pages)
            for block in self.pages[page_index].blocks:
                hay = " ".join((block.text, self.values.get(block.path, ""), block.path)).lower()
                if query in hay:
                    self.index = page_index
                    self.highlight = query
                    self._active_path = ""
                    self._blit()
                    self.status.config(text=f"Found on page {page_index + 1}")
                    return
        self.status.config(text=f"No match for {query!r}")

    def add_note(self):
        text = self.note_entry.get().strip()
        if not text:
            return
        self.notes.append({"page": self.index + 1, "text": text})
        self.note_entry.delete(0, "end")
        self._fill_notes()

    def save_as(self):
        if not self.path:
            return
        self._commit_live()
        if not self._has_fields():
            messagebox.showinfo("Oxbow", "This file has no fields to save. Notes are kept with the file when you save a form.")
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile="filled.pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not dest:
            return
        missing = self.save_to(dest, dialogs=True)
        if missing is None:
            return
        self._blit(self._active_path)

    def _has_fields(self) -> bool:
        return any(
            block.kind == "field" and not block.read_only
            for page in self.pages
            for block in page.blocks
        )

    def save_to(self, dest: str, dialogs: bool = False):
        """Write the current values. Returns the paths that did not read back."""
        self._commit_live()
        if not self.path or not self.pages:
            return []
        try:
            if self.xfa:
                save_filled(self.path, dest, self.values)
            else:
                save_acro_values(self.path, dest, self.values)
            pdf = pikepdf.open(dest, allow_overwriting_input=True)
            extras.save_notes(pdf, self.notes)
            pdf.save(dest)
            pdf.close()
        except Exception as exc:
            if dialogs:
                messagebox.showerror("Oxbow", f"Save failed:\n{exc}")
            else:
                self.status.config(text=f"Save failed: {exc}")
            return None
        back = read_back(dest) if self.xfa else read_acro_values(dest)
        missing = [key for key, value in self.values.items() if back.get(key) != value]
        if missing:
            if dialogs:
                messagebox.showerror("Oxbow", "Saved, but these values did not read back:\n" + "\n".join(missing[:6]))
            self.status.config(text=f"Saved, but {len(missing)} values did not read back")
            return missing
        self.status.config(text=f"Saved {len(self.values)} values to {dest}")
        if dialogs:
            messagebox.showinfo("Oxbow", f"Saved {len(self.values)} values.\n{dest}")
        return []

    def print_doc(self):
        self._commit_live()
        if not self.pages and not self._static_count:
            return
        images = []
        for index, page in enumerate(self.pages):
            if self.xfa:
                images.append(render_page(page, self.values, PRINT_PPM, "", "", for_print=True))
            else:
                raster = self._raster_page(index, PRINT_PPM)
                if raster is None:
                    continue
                images.append(render_page(
                    page, self.values, raster.width / max(page.page_w, 0.1), "", "",
                    background=raster, for_print=True,
                ))
        if not images:
            return
        dest = os.path.join(tempfile.gettempdir(), "oxbow-print.pdf")
        first, rest = images[0], images[1:]
        first.save(dest, save_all=True, append_images=rest, resolution=150)
        result = subprocess.run(["lp", dest], capture_output=True, text=True)
        if result.returncode == 0:
            self.status.config(text=f"Sent to the printer. Copy: {dest}")
        else:
            self.status.config(text=f"No printer accepted the job. Printable PDF: {dest}")
            messagebox.showinfo("Oxbow", f"A printable PDF is here:\n{dest}\n\nThe print command was not available or no printer is set up.")

    def show_signature(self):
        if not self.path:
            return
        messagebox.showinfo("Signature", extras.signature_status(self.path))


def _xft_dpi() -> float | None:
    try:
        raw = subprocess.check_output(["xrdb", "-query"], text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in raw.splitlines():
        if line.lower().startswith("xft.dpi:"):
            try:
                return float(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def main(argv: list[str] | None = None):
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else None
    if path is not None and not str(path).strip():
        path = None
    app = Reader(path)
    app.mainloop()
