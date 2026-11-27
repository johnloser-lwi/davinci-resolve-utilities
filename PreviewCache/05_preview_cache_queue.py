import os
import sys

# Shared helpers (resolve_utils.py) live outside Resolve's Scripts menu - deploy puts them there.
_LIB = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~/Library/Application Support"),
                    "ResolveUtilities", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
sys.modules.pop("resolve_utils", None)      # Resolve keeps modules loaded between runs
import resolve_utils as ru
ru.bootstrap(globals(), "PreviewCache/05_preview_cache_queue.py")     # Mac: opens the window in its own Python

import datetime
import traceback
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Preview Cache Queue - render a long timeline in sections, safely.
#
#   1. Set In and Out on the timeline, click "Add In/Out". Repeat - e.g. split a
#      whole video into four sections.
#      Or let it split for you: "Split into N sections" queues the whole
#      timeline (or the current In/Out) as N equal, back-to-back sections.
#      Or queue the markers: "Queue Uncached Markers" queues every range marker
#      (optionally of one colour) that no preview cache clip covers yet.
#   2. Click "Render Queue". The sections render ONE AT A TIME: each is rendered,
#      imported and placed on the timeline (green, above the range) before the
#      next one starts.
#
# The point is crash safety. If Resolve crashes during section two, section
# one is already rendered and on the timeline, and the rest are still queued.
#
# The queue is saved per project after every change, and a finished section
# comes off it straight away. Reopen this window after a crash or a restart and
# whatever didn't render is still there; a section that was mid-render shows
# as "interrupted" and simply renders again.
#
# This has no render code of its own: it loads Render Preview Cache (01) and
# calls its render_range(), so both always render the same way.

QUEUE_FILE = ru.data_path("preview_cache_queue.json")
WINDOW_PREFS_FILE = ru.data_path("preview_cache_queue_prefs.json")
RENDER_SCRIPT = "01_render_preview_cache.py"

BG = "#1e1e1e"
PANEL = "#252525"
PANEL2 = "#2d2d2d"
FG = "#e0e0e0"
SUB = "#9a9a9a"
BORDER = "#3a3a3a"
BTN = "#383838"
BTN_HOVER = "#454545"
ACCENT = "#3d7fd6"
ACCENT_HOVER = "#4a90e8"
BAD = "#e07070"

CACHE_COLORS = ("Green", "Chocolate")     # cache clips: shown / hidden by Toggle (04)
ALL_COLOURS = "All colours"

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]
SCALE = 1.0


def S(n):
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    return ru.font(size, weight, SCALE)


def detect_scale(root):
    return ru.detect_scale(root)


def resolve_scale(prefs, root):
    setting = prefs.get("ui_scale", "Auto")
    if isinstance(setting, str) and setting.endswith("%"):
        try:
            return max(1.0, min(int(setting[:-1]) / 100.0, 3.0))
        except ValueError:
            pass
    return detect_scale(root)


def read_json(path, default):
    return ru.read_json(path, default)


def write_json(path, data):
    ru.write_json(path, data)


def make_button(parent, text, command, primary=False):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = ru.Button(parent, text=text, command=command, bg=base,
                    fg="#ffffff" if primary else FG, relief="flat", bd=0,
                    padx=S(12), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


def frame_to_tc(frame, fps, drop_frame):
    rate = max(1, round(float(fps)))
    sep = ":"
    if drop_frame:
        drop = 4 if rate == 60 else 2
        per_min = rate * 60 - drop
        per_10 = rate * 600 - drop * 9
        d, m = divmod(frame, per_10)
        frame += drop * 9 * d
        if m >= drop:
            frame += drop * ((m - drop) // per_min)
        sep = ";"
    return "%02d:%02d:%02d%s%02d" % ((frame // (rate * 3600)) % 24,
                                    (frame // (rate * 60)) % 60,
                                    (frame // rate) % 60, sep, frame % rate)


# --------------------------------------------------------------------------
# the render script, loaded as a library
# --------------------------------------------------------------------------

def load_preview_cache():
    """Render Preview Cache's functions, without running it."""
    ns = ru.load_library(globals(), RENDER_SCRIPT, "PreviewCache",
                         "PREVIEW_CACHE_AS_LIBRARY", "render_preview_cache")
    if "render_range" not in ns:
        raise RuntimeError(f"{os.path.basename(ns['__file__'])} is an older version without "
                           f"render_range() - redeploy so both scripts match.")
    return ns


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


class PreviewCacheQueue:
    def __init__(self, root, pc):
        self.root = root
        self.pc = pc
        self.prefs = read_json(WINDOW_PREFS_FILE, {})
        self.queue_store = read_json(QUEUE_FILE, {})
        self.running = False
        self.stop_requested = False
        self._dest_project = None

        root.title("Preview Cache Queue")
        root.configure(bg=BG)
        root.minsize(S(720), S(400))
        geo = self.prefs.get("geometry")
        if geo and abs(float(self.prefs.get("geometry_scale", 1.0)) - SCALE) < 0.01:
            try:
                root.geometry(geo)
            except Exception:
                pass

        self._recover_interrupted()
        self._style()
        self._build()
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<FocusIn>", lambda e: self.show_in_out() if e.widget is self.root else None)

    # -- data -------------------------------------------------------------
    def project(self):
        try:
            return projectManager.GetCurrentProject()
        except Exception:
            return None

    def project_key(self):
        project = self.project()
        return project.GetName() if project else ""

    @property
    def entries(self):
        return self.queue_store.setdefault(self.project_key(), [])

    def save_queue(self):
        write_json(QUEUE_FILE, self.queue_store)

    def _recover_interrupted(self):
        """A section still marked "rendering" means Resolve (or this window)
        went down mid-render. It goes back in line to render again."""
        changed = False
        for entries in self.queue_store.values():
            for entry in entries:
                if entry.get("status", "").startswith("rendering"):
                    entry["status"] = "interrupted - will render again"
                    changed = True
        if changed:
            self.save_queue()

    def timeline_by_id(self, entry):
        project = self.project()
        if not project:
            return None
        fallback = None
        for i in range(1, (project.GetTimelineCount() or 0) + 1):
            tl = project.GetTimelineByIndex(i)
            if not tl:
                continue
            try:
                if tl.GetUniqueId() == entry.get("timeline_id"):
                    return tl
            except Exception:
                pass
            if tl.GetName() == entry.get("timeline"):
                fallback = tl
        return fallback

    @staticmethod
    def read_in_out(timeline):
        """(in, out) relative to the timeline start, or None if not both set."""
        try:
            marks = (timeline.GetMarkInOut() or {}).get("video") or {}
        except Exception:
            return None
        if "in" in marks and "out" in marks:
            return int(marks["in"]), int(marks["out"])
        return None

    def describe_range(self, timeline, mark_in, mark_out):
        fps = timeline.GetSetting("timelineFrameRate") or 24
        drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"
        start = timeline.GetStartFrame()
        return frame_to_tc(start + mark_in, fps, drop), frame_to_tc(start + mark_out, fps, drop)

    # -- ui ---------------------------------------------------------------
    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(24), font=FONT(9))
        style.configure("Treeview.Heading", background=PANEL2, foreground=SUB,
                        relief="flat", font=FONT(8, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground=PANEL, background=BTN,
                        foreground=FG, arrowcolor=SUB, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=FG,
                        arrowsize=S(14), padding=S(3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", FG)],
                  background=[("active", BTN_HOVER), ("readonly", BTN)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    def _build(self):
        bottom = tk.Frame(self.root, bg=PANEL2)
        bottom.pack(fill="x", side="bottom")

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        self.title_label = tk.Label(head, text="-", bg=BG, fg=FG, anchor="w",
                                    font=FONT(11, "bold"))
        self.title_label.pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.inout_label = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.inout_label.pack(side="left")
        self.top_btn = ru.Button(row, text="On Top", relief="flat", bd=0, font=FONT(9),
                                 cursor="hand2", padx=S(10), pady=S(5),
                                 highlightthickness=0, command=self.toggle_topmost)
        self.top_btn.pack(side="right")
        self._paint_topmost()

        form = tk.Frame(self.root, bg=BG)
        form.pack(fill="x", padx=S(14), pady=(S(10), 0))
        form.columnconfigure(1, weight=1)
        tk.Label(form, text="Render to", bg=BG, fg=SUB, font=FONT(9), width=11,
                 anchor="w").grid(row=0, column=0, sticky="w", pady=(0, S(6)))
        dest_row = tk.Frame(form, bg=BG)
        dest_row.grid(row=0, column=1, sticky="ew", pady=(0, S(6)))
        self.dest_var = tk.StringVar()
        tk.Entry(dest_row, textvariable=self.dest_var, bg=PANEL, fg=FG, relief="flat",
                 insertbackground=FG, font=FONT(9), highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", fill="x", expand=True, ipady=S(3))
        make_button(dest_row, "Browse", self.browse).pack(side="left", padx=(S(8), 0))

        tk.Label(form, text="Preset", bg=BG, fg=SUB, font=FONT(9), width=11,
                 anchor="w").grid(row=1, column=0, sticky="w")
        preset_row = tk.Frame(form, bg=BG)
        preset_row.grid(row=1, column=1, sticky="ew")
        self.preset_var = tk.StringVar(value=self.pc["load_prefs"]().get("last_preset", ""))
        self.preset_box = ttk.Combobox(preset_row, textvariable=self.preset_var, values=[],
                                       state="readonly", width=34, font=FONT(9))
        self.preset_box.pack(side="left")
        self.preset_box.bind("<<ComboboxSelected>>", self.on_preset_change)
        tk.Label(preset_row, text="used for ranges you add next", bg=BG, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(S(8), 0))

        tk.Label(form, text="Split into", bg=BG, fg=SUB, font=FONT(9), width=11,
                 anchor="w").grid(row=2, column=0, sticky="w", pady=(S(6), 0))
        split_row = tk.Frame(form, bg=BG)
        split_row.grid(row=2, column=1, sticky="ew", pady=(S(6), 0))
        self.split_var = tk.StringVar(value=str(self.prefs.get("split_count", 4)))
        tk.Spinbox(split_row, from_=2, to=200, textvariable=self.split_var, width=5,
                   bg=PANEL, fg=FG, buttonbackground=BTN, relief="flat", font=FONT(9),
                   insertbackground=FG, highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT).pack(side="left", ipady=S(2))
        tk.Label(split_row, text="sections:", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(S(6), S(8)))
        self.split_whole_btn = make_button(split_row, "Whole Timeline",
                                           lambda: self.add_sections(whole=True))
        self.split_whole_btn.pack(side="left")
        self.split_range_btn = make_button(split_row, "Current In/Out",
                                           lambda: self.add_sections(whole=False))
        self.split_range_btn.pack(side="left", padx=(S(6), 0))

        tk.Label(form, text="Markers", bg=BG, fg=SUB, font=FONT(9), width=11,
                 anchor="w").grid(row=3, column=0, sticky="w", pady=(S(6), 0))
        marker_row = tk.Frame(form, bg=BG)
        marker_row.grid(row=3, column=1, sticky="ew", pady=(S(6), 0))
        self.marker_color_var = tk.StringVar(value=self.prefs.get("marker_color", ALL_COLOURS))
        self.marker_box = ttk.Combobox(marker_row, textvariable=self.marker_color_var,
                                       values=[ALL_COLOURS], state="readonly", width=14,
                                       font=FONT(9))
        self.marker_box.pack(side="left")
        self.marker_box.bind("<<ComboboxSelected>>", lambda e: self.show_in_out())
        self.markers_btn = make_button(marker_row, "Queue Uncached Markers", self.add_markers)
        self.markers_btn.pack(side="left", padx=(S(8), 0))
        self.marker_info = tk.Label(marker_row, text="", bg=BG, fg=SUB, font=FONT(8))
        self.marker_info.pack(side="left", padx=(S(8), 0))

        table = tk.Frame(self.root, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14), pady=(S(10), 0))
        cols = ("n", "timeline", "range", "frames", "preset", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=8)
        for col, label, width, stretch in (
                ("n", "#", 34, False), ("timeline", "TIMELINE", 150, True),
                ("range", "IN - OUT", 190, False), ("frames", "FRAMES", 70, False),
                ("preset", "PRESET", 170, True), ("status", "STATUS", 190, True)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=S(width), anchor="w", stretch=stretch)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.apply_preset_to_selected())
        ru.bind_delete(self.tree, lambda e: self.remove_selected())
        self.tree.tag_configure("failed", foreground=BAD)
        self.tree.tag_configure("active", foreground=ACCENT_HOVER)

        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=S(14), pady=(S(10), S(10)))
        self.add_btn = make_button(actions, "+  Add In/Out", self.add, primary=True)
        self.add_btn.pack(side="left")
        make_button(actions, "Use Preset on Selected",
                    self.apply_preset_to_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Remove", self.remove_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Clear", self.clear).pack(side="left", padx=(S(6), 0))
        self.go_btn = make_button(actions, "Render Queue  >", self.go, primary=True)
        self.go_btn.pack(side="right")
        self.stop_btn = make_button(actions, "Stop After This One", self.request_stop)
        self.stop_btn.pack(side="right", padx=(0, S(6)))
        self.stop_btn.config(state="disabled")

        self.status = tk.Label(bottom, text="", bg=PANEL2, fg=SUB, anchor="w",
                               font=FONT(8), padx=S(12), pady=S(5))
        self.status.pack(side="left", fill="x", expand=True)
        tk.Label(bottom, text="UI scale", bg=PANEL2, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(0, S(6)))
        self.scale_var = tk.StringVar(value=self.prefs.get("ui_scale", "Auto"))
        box = ttk.Combobox(bottom, textvariable=self.scale_var, values=SCALE_CHOICES,
                           state="readonly", width=6, font=FONT(8))
        box.pack(side="left", padx=(0, S(10)), pady=S(3))
        box.bind("<<ComboboxSelected>>", self.on_scale_change)

    def say(self, msg, error=False):
        self.status.config(text=msg, fg=BAD if error else SUB)
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    def _paint_topmost(self):
        on = bool(self.prefs.get("always_on_top", False))
        self.top_btn.config(bg=ACCENT if on else BTN, fg="#ffffff" if on else FG,
                            activebackground=ACCENT_HOVER if on else BTN_HOVER)

    def toggle_topmost(self):
        on = not bool(self.prefs.get("always_on_top", False))
        self.prefs["always_on_top"] = on
        write_json(WINDOW_PREFS_FILE, self.prefs)
        try:
            self.root.attributes("-topmost", on)
        except Exception:
            pass
        self._paint_topmost()

    def on_scale_change(self, event=None):
        global SCALE
        if self.running:
            return
        self.prefs["ui_scale"] = self.scale_var.get()
        write_json(WINDOW_PREFS_FILE, self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        self._remember_dest()
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(720), S(400))
        self._style()
        self._build()
        self._dest_project = None
        self.refresh()

    def on_preset_change(self, event=None):
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()
            except Exception:
                pass
        # Shared with Render Preview Cache, so both start on the same preset.
        self.pc["save_prefs"]({**self.pc["load_prefs"](), "last_preset": self.preset_var.get()})

    def browse(self):
        start = self.dest_var.get().strip()
        chosen = filedialog.askdirectory(
            **ru.dialog_parent(self.root), title="Where should the preview cache renders go?",
            initialdir=start if os.path.isdir(start) else None)
        if chosen:
            self.dest_var.set(os.path.normpath(chosen))

    def _remember_dest(self):
        dest = self.dest_var.get().strip()
        if dest and self._dest_project is not None:
            self.prefs.setdefault("dest_by_project", {})[self._dest_project] = dest
            write_json(WINDOW_PREFS_FILE, self.prefs)

    def show_in_out(self):
        project = self.project()
        timeline = project.GetCurrentTimeline() if project else None
        if not timeline:
            self.inout_label.config(text="No timeline open")
            return
        self.show_markers(timeline)
        marks = self.read_in_out(timeline)
        if marks:
            a, b = self.describe_range(timeline, *marks)
            self.inout_label.config(text=f"{timeline.GetName()}   ·   In {a}   Out {b}   "
                                         f"({marks[1] - marks[0] + 1} frames)")
        else:
            self.inout_label.config(text=f"{timeline.GetName()}   ·   no In/Out set")

    def refresh(self):
        project = self.project()
        name = project.GetName() if project else ""
        self.title_label.config(text=f"Preview Cache Queue  -  {name or 'no project'}")
        if name != self._dest_project:
            # The render folder follows the project: remembered, else
            # <Project Media folder>\PreviewCache.
            self._dest_project = name
            remembered = (self.prefs.get("dest_by_project") or {}).get(name)
            if not (remembered and os.path.isdir(remembered)):
                remembered = (self.pc["default_render_dir"](project) if project else None) or ""
            self.dest_var.set(remembered)
            names = self.pc["preset_names"](project) if project else []
            self.preset_box.config(values=names)
            if names and self.preset_var.get() not in names:
                self.preset_var.set(names[0])
        self.show_in_out()
        self.tree.delete(*self.tree.get_children())
        for n, entry in enumerate(self.entries, start=1):
            status = entry.get("status", "queued")
            tag = ("active",) if status.startswith("rendering") else \
                  ("failed",) if status not in ("queued",) else ()
            self.tree.insert("", "end", iid=entry["id"], tags=tag,
                             values=(n, entry.get("timeline", "") + (f"  \u00b7  {entry['marker']}"
                                                                     if entry.get("marker") else ""),
                                     f"{entry.get('in_tc')}  -  {entry.get('out_tc')}",
                                     entry["out"] - entry["in"] + 1,
                                     entry.get("preset", ""), status))
        if not self.running:
            count = len(self.entries)
            self.say(f"{count} range(s) queued." if count else
                     "Set In/Out on the timeline, then Add In/Out.")

    # -- queue editing ----------------------------------------------------
    def add(self):
        project = self.project()
        timeline = project.GetCurrentTimeline() if project else None
        if not timeline:
            self.say("Open a timeline first.", True)
            return
        marks = self.read_in_out(timeline)
        if not marks:
            self.say("Set both an In and an Out point on the timeline first.", True)
            return
        preset = self.preset_var.get()
        if not preset:
            self.say("Choose a render preset first.", True)
            return
        in_tc, out_tc = self._queue_range(timeline, marks[0], marks[1], preset)
        self.save_queue()
        self.refresh()
        self.say(f"Queued {in_tc} - {out_tc} on '{timeline.GetName()}'.")

    # -- markers ----------------------------------------------------------
    @staticmethod
    def range_markers(timeline):
        """[(in, out, colour, name)] for the timeline's range markers (longer
        than one frame), relative to the timeline start like In/Out."""
        out = []
        for frame, m in (timeline.GetMarkers() or {}).items():
            duration = int(float(m.get("duration") or 1))
            if duration > 1:
                first = int(float(frame))
                out.append((first, first + duration - 1, m.get("color") or "", m.get("name") or ""))
        return sorted(out)

    @staticmethod
    def cache_spans(timeline):
        """Merged [(first, last)] frames covered by preview cache clips."""
        start = timeline.GetStartFrame()
        spans = []
        for track in range(1, (timeline.GetTrackCount("video") or 0) + 1):
            for item in timeline.GetItemListInTrack("video", track) or []:
                try:
                    if item.GetClipColor() in CACHE_COLORS:
                        spans.append((int(item.GetStart()) - start, int(item.GetEnd()) - start - 1))
                except Exception:
                    pass
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged

    @staticmethod
    def covered(first, last, spans):
        """Whether cache clips cover every frame from first to last."""
        for a, b in spans:
            if a <= first <= b:
                if b >= last:
                    return True
                first = b + 1
        return False

    def show_markers(self, timeline):
        try:
            markers = self.range_markers(timeline)
            spans = self.cache_spans(timeline)
        except Exception:
            return
        colours = sorted({c for _, _, c, _ in markers if c})
        self.marker_box.config(values=[ALL_COLOURS] + colours)
        wanted = self.marker_color_var.get()
        if wanted != ALL_COLOURS and wanted not in colours and colours:
            self.marker_color_var.set(ALL_COLOURS)
            wanted = ALL_COLOURS
        picked = [m for m in markers if wanted == ALL_COLOURS or m[2] == wanted]
        todo = [(a, b) for a, b, _, _ in picked if not self.covered(a, b, spans)]
        try:
            key = timeline.GetUniqueId() or timeline.GetName()
        except Exception:
            key = timeline.GetName()
        queued = {(e.get("timeline_id") or e.get("timeline"), e["in"], e["out"]) for e in self.entries}
        in_queue = sum(1 for a, b in todo if (key, a, b) in queued)
        text = f"{len(todo)} of {len(picked)} range marker(s) uncached" + (
            f", {in_queue} queued" if in_queue else "")
        self.marker_info.config(text=text if picked else "no range markers on this timeline")

    def add_markers(self):
        """Queue every range marker (of the chosen colour) that preview cache
        clips don't already cover. Point markers have no range and are left out."""
        project = self.project()
        timeline = project.GetCurrentTimeline() if project else None
        if not timeline:
            self.say("Open a timeline first.", True)
            return
        preset = self.preset_var.get()
        if not preset:
            self.say("Choose a render preset first.", True)
            return
        colour = self.marker_color_var.get()
        self.prefs["marker_color"] = colour
        write_json(WINDOW_PREFS_FILE, self.prefs)
        try:
            timeline_id = timeline.GetUniqueId()
        except Exception:
            timeline_id = ""
        queued = {(e.get("timeline_id") or e.get("timeline"), e["in"], e["out"]) for e in self.entries}
        spans = self.cache_spans(timeline)
        added = cached = already = 0
        for first, last, c, name in self.range_markers(timeline):
            if colour != ALL_COLOURS and c != colour:
                continue
            if self.covered(first, last, spans):
                cached += 1
            elif (timeline_id or timeline.GetName(), first, last) in queued:
                already += 1
            else:
                self._queue_range(timeline, first, last, preset)
                self.entries[-1]["marker"] = name or f"{c} marker"
                added += 1
        self.save_queue()
        self.refresh()
        which = "" if colour == ALL_COLOURS else f"{colour} "
        extra = ", ".join(x for x in (f"{cached} already cached" if cached else "",
                                      f"{already} already queued" if already else "") if x)
        if added:
            self.say(f"Queued {added} {which}marker range(s) from '{timeline.GetName()}'"
                     + (f" ({extra})." if extra else "."))
        else:
            self.say(f"No {which}range markers need a cache" + (f" ({extra})." if extra else "."),
                     not extra)

    def _queue_range(self, timeline, mark_in, mark_out, preset):
        in_tc, out_tc = self.describe_range(timeline, mark_in, mark_out)
        try:
            timeline_id = timeline.GetUniqueId()
        except Exception:
            timeline_id = ""
        self.entries.append({
            "id": uuid.uuid4().hex, "timeline": timeline.GetName(),
            "timeline_id": timeline_id, "in": mark_in, "out": mark_out,
            "in_tc": in_tc, "out_tc": out_tc, "preset": preset, "status": "queued",
            "added": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        return in_tc, out_tc

    @staticmethod
    def split_frames(first, last, count):
        """[(in, out), ...]: first..last cut into `count` equal sections, back to
        back with no gap or overlap. Lengths differ by at most one frame."""
        length = last - first + 1
        bounds = [first + round(i * length / count) for i in range(count + 1)]
        return [(bounds[i], bounds[i + 1] - 1) for i in range(count)]

    def add_sections(self, whole):
        """Split the whole timeline (or the current In/Out) into N sections and
        queue each one."""
        project = self.project()
        timeline = project.GetCurrentTimeline() if project else None
        if not timeline:
            self.say("Open a timeline first.", True)
            return
        try:
            count = int(self.split_var.get())
        except ValueError:
            count = 0
        if count < 2:
            self.say("Enter how many sections to split into (2 or more).", True)
            return
        preset = self.preset_var.get()
        if not preset:
            self.say("Choose a render preset first.", True)
            return

        if whole:
            # In/Out are relative to the timeline start; GetEndFrame is one
            # past the last frame.
            first, last = 0, timeline.GetEndFrame() - timeline.GetStartFrame() - 1
            what = "the whole timeline"
        else:
            marks = self.read_in_out(timeline)
            if not marks:
                self.say("Set both an In and an Out point first, or use Whole Timeline.", True)
                return
            first, last = marks
            what = "the In/Out range"
        if last - first + 1 < count:
            self.say(f"{what.capitalize()} is only {last - first + 1} frames - too short for "
                     f"{count} sections.", True)
            return

        self.prefs["split_count"] = count
        write_json(WINDOW_PREFS_FILE, self.prefs)
        for mark_in, mark_out in self.split_frames(first, last, count):
            self._queue_range(timeline, mark_in, mark_out, preset)
        self.save_queue()
        self.refresh()
        each = (last - first + 1) // count
        self.say(f"Queued {count} sections of ~{each} frames covering {what} of "
                 f"'{timeline.GetName()}'.")

    def selected_entries(self):
        chosen = set(self.tree.selection())
        return [e for e in self.entries if e["id"] in chosen and not
                e.get("status", "").startswith("rendering")]

    def apply_preset_to_selected(self):
        picked = self.selected_entries()
        if not picked:
            self.say("Select the range(s) to give the current preset.", True)
            return
        for entry in picked:
            entry.update(preset=self.preset_var.get(), status="queued")
        self.save_queue()
        self.refresh()

    def remove_selected(self):
        chosen = {e["id"] for e in self.selected_entries()}
        if not chosen:
            self.say("Select the range(s) to remove.", True)
            return
        self.entries[:] = [e for e in self.entries if e["id"] not in chosen]
        self.save_queue()
        self.refresh()

    def clear(self):
        if self.running:
            return
        if self.entries and messagebox.askyesno(
                "Preview Cache Queue", f"Remove all {len(self.entries)} queued range(s)?",
                **ru.dialog_parent(self.root)):
            self.entries.clear()
            self.save_queue()
            self.refresh()

    # -- running ----------------------------------------------------------
    def request_stop(self):
        self.stop_requested = True
        self.stop_btn.config(state="disabled")
        self.say("Stopping after the current range finishes...")

    def _tick(self, label, index, total):
        def tick(percent):
            self.say(f"Rendering {index} of {total}:  {label}  -  {percent}%")
            try:
                self.root.update()          # keeps the window and Stop button alive
            except Exception:
                pass
        return tick

    def go(self):
        if self.running:
            return
        todo = list(self.entries)
        if not todo:
            self.say("Nothing queued.", True)
            return
        dest = self.dest_var.get().strip()
        if not dest:
            self.say("Pick a render folder first.", True)
            return
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            self.say(f"Cannot use that folder: {e}", True)
            return
        self._remember_dest()

        project = self.project()
        original_timeline = project.GetCurrentTimeline()
        original_marks = {}                     # timeline key -> (timeline, In/Out, playhead)
        done, failed = 0, []
        self.running, self.stop_requested = True, False
        for btn in (self.go_btn, self.add_btn, self.split_whole_btn, self.split_range_btn):
            btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        try:
            for index, entry in enumerate(todo, start=1):
                if self.stop_requested:
                    break
                if entry not in self.entries:   # removed while an earlier range rendered
                    continue
                label =f"{entry['timeline']} {entry['in_tc']}-{entry['out_tc']}"
                timeline = self.timeline_by_id(entry)
                if not timeline:
                    entry["status"] = "failed: timeline not found"
                    failed.append(f"{label}: timeline '{entry['timeline']}' no longer exists")
                    self.save_queue()
                    continue
                key = entry.get("timeline_id") or entry["timeline"]
                if key not in original_marks:
                    original_marks[key] = (timeline, self.read_in_out(timeline),
                                           timeline.GetCurrentTimecode())

                project.SetCurrentTimeline(timeline)
                timeline.ClearMarkInOut()
                timeline.SetMarkInOut(entry["in"], entry["out"])

                # Saved BEFORE rendering: if Resolve dies now, the next launch
                # finds this range marked as interrupted, not lost.
                entry["status"] = "rendering..."
                self.save_queue()
                self.refresh()
                self.tree.see(entry["id"])

                try:
                    where = self.pc["render_range"](project, timeline, dest, entry["preset"],
                                                    tick=self._tick(label, index, len(todo)))
                except self.pc["PreviewCacheError"] as e:
                    entry["status"] = "failed: " + str(e)[:70]
                    failed.append(f"{label}: {e}")
                except Exception as e:
                    traceback.print_exc()
                    entry["status"] = "failed: " + str(e)[:70]
                    failed.append(f"{label}: {e}")
                else:
                    # On the timeline - off the queue, saved right away.
                    print(f"Queue: {label} done ({where}).")
                    self.entries[:] = [e for e in self.entries if e["id"] != entry["id"]]
                    done += 1
                self.save_queue()
                self.refresh()
        finally:
            # Put every timeline's In/Out and playhead back, and you back where
            # you were.
            for timeline, marks, playhead in original_marks.values():
                try:
                    timeline.ClearMarkInOut()
                    if marks:
                        timeline.SetMarkInOut(*marks)
                    project.SetCurrentTimeline(timeline)
                    timeline.SetCurrentTimecode(playhead)
                except Exception:
                    pass
            if original_timeline:
                try:
                    project.SetCurrentTimeline(original_timeline)
                except Exception:
                    pass
            for entry in self.entries:          # nothing is rendering any more
                if entry.get("status", "").startswith("rendering"):
                    entry["status"] = "queued"
            self.save_queue()
            self.running = False
            for btn in (self.go_btn, self.add_btn, self.split_whole_btn, self.split_range_btn):
                btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.refresh()

        left = len(self.entries)
        summary = f"{done} of {len(todo)} range(s) rendered and placed on the timeline."
        if self.stop_requested and left:
            summary += f"  Stopped - {left} still queued."
        self.say(summary, bool(failed))
        if failed:
            messagebox.showwarning("Preview Cache Queue", summary + "\n\n" + "\n\n".join(failed),
                                   **ru.dialog_parent(self.root))

    def _on_close(self):
        if self.running:
            # Closing mid-render would leave Resolve rendering with nobody to
            # place the result on the timeline.
            messagebox.showinfo(
                "Preview Cache Queue",
                "A range is rendering. Click 'Stop After This One', and close once it "
                "has been placed on the timeline.\n\nWhatever hasn't rendered stays queued "
                "for next time.", **ru.dialog_parent(self.root))
            return
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            write_json(WINDOW_PREFS_FILE, self.prefs)
            self._remember_dest()
        except Exception:
            pass
        self.root.destroy()


def start():
    global SCALE
    prefs = read_json(WINDOW_PREFS_FILE, {})
    root = tk.Tk()
    try:
        pc = load_preview_cache()
    except Exception as e:
        root.withdraw()
        messagebox.showerror("Preview Cache Queue", str(e), **ru.dialog_parent(root))
        root.destroy()
        return
    SCALE = resolve_scale(prefs, root)
    app = PreviewCacheQueue(root, pc)
    root.bind("<Escape>", lambda e: app._on_close())
    root.lift()
    root.attributes("-topmost", True)
    if not prefs.get("always_on_top", False):
        root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


start()
