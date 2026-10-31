import ctypes
import datetime
import glob
import json
import os
import traceback
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# Motion Link Queue - line up several ranges, then export them all for Cavalry.
#
#   1. Set In and Out on the timeline, click "Add In/Out". The render settings
#      window opens for that range (Video or Audio only, and the presets - the
#      same window FxLink uses) and the range is queued with those settings.
#      Repeat for as many ranges as you like, on any timeline in the project.
#   2. Click "Export All". The ranges render one after another, each exactly as
#      a single Motion Link Export would: its file and its job file.
#   3. In Cavalry: Window > Scripts > John > Motion Link Build Batch builds a
#      Composition for every range of that batch.
#
# The queue is saved per project, so it survives closing this window or
# Resolve. A range that goes through is taken off the queue; one that fails
# stays on it with the reason, ready to try again.
#
# This has no export code of its own. It loads the Motion Link Export script
# and uses its pipeline, so a fix there is a fix here.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

QUEUE_FILE = os.path.expandvars(r"%APPDATA%\motion_link_queue.json")
PREFS_FILE = os.path.expandvars(r"%APPDATA%\motion_link_queue_prefs.json")
EXPORT_SCRIPT = "01_motion_link_export.py"
RESOLVE_SCRIPTS = os.path.expandvars(
    r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility")

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

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]
SCALE = 1.0
AUTO_NAME = "(same as the .wav)"


def S(n):
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    pts = max(6, int(round(size * SCALE)))
    return ("Segoe UI", pts, weight) if weight else ("Segoe UI", pts)


def detect_scale(root):
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    scale = dpi / 96.0
    if scale < 1.05:
        try:
            width = root.winfo_screenwidth()
        except Exception:
            width = 1920
        if width >= 3400:
            scale = 1.5
        elif width >= 2800:
            scale = 1.25
    return max(1.0, min(scale, 3.0))


def resolve_scale(prefs, root):
    setting = prefs.get("ui_scale", "Auto")
    if isinstance(setting, str) and setting.endswith("%"):
        try:
            return max(1.0, min(int(setting[:-1]) / 100.0, 3.0))
        except ValueError:
            pass
    return detect_scale(root)


def read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Could not save {os.path.basename(path)}: {e}")


def make_button(parent, text, command, primary=False):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = tk.Button(parent, text=text, command=command, bg=base,
                    fg="#ffffff" if primary else FG, relief="flat", bd=0,
                    padx=S(12), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


# --------------------------------------------------------------------------
# the export script, loaded as a library
# --------------------------------------------------------------------------

def find_export_script():
    """The deployed Motion Link Export script, wherever it is."""
    candidates = []
    try:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), EXPORT_SCRIPT))
    except NameError:
        pass                                    # some embedded runners have no __file__
    candidates.append(os.path.join(RESOLVE_SCRIPTS, "John", "MotionLink", EXPORT_SCRIPT))
    candidates += glob.glob(os.path.join(RESOLVE_SCRIPTS, "**", "*motion_link_export.py"),
                            recursive=True)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_motion_link():
    """The export script's functions, without opening its panel."""
    path = find_export_script()
    if not path:
        raise RuntimeError(f"Couldn't find {EXPORT_SCRIPT}. Run deploy.ps1.")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    ns = {"bmd": bmd, "__name__": "motion_link_export", "__file__": path,
          "MOTION_LINK_AS_LIBRARY": True}
    exec(compile(source, path, "exec"), ns)
    if "export_range" not in ns:
        raise RuntimeError(f"{os.path.basename(path)} is an older version without the "
                           f"export pipeline - run deploy.ps1 so both scripts match.")
    return ns


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


class MotionLinkQueue:
    def __init__(self, root, ml):
        self.root = root
        self.ml = ml
        self.prefs = read_json(PREFS_FILE, {})
        # The destination is shared with the single export panel, so both
        # remember the same folder per project.
        self.ml_prefs = ml["load_prefs"]()
        self.queue_store = read_json(QUEUE_FILE, {})
        self._dest_project = None

        root.title("Motion Link Queue")
        root.configure(bg=BG)
        root.minsize(S(700), S(400))
        geo = self.prefs.get("geometry")
        if geo and abs(float(self.prefs.get("geometry_scale", 1.0)) - SCALE) < 0.01:
            try:
                root.geometry(geo)
            except Exception:
                pass

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
            both = timeline.GetMarkInOut() or {}
            marks = both.get("audio") or both.get("video") or {}
        except Exception:
            return None
        if "in" in marks and "out" in marks:
            return int(marks["in"]), int(marks["out"])
        return None

    def describe_range(self, timeline, mark_in, mark_out):
        fps = timeline.GetSetting("timelineFrameRate") or 24
        drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"
        start = timeline.GetStartFrame()
        tc = self.ml["frame_to_tc"]
        return tc(start + mark_in, fps, drop), tc(start + mark_out, fps, drop)

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
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0, font=FONT(9),
                                 cursor="hand2", padx=S(10), pady=S(5),
                                 highlightthickness=0, command=self.toggle_topmost)
        self.top_btn.pack(side="right")
        self._paint_topmost()

        form = tk.Frame(self.root, bg=BG)
        form.pack(fill="x", padx=S(14), pady=(S(10), 0))
        form.columnconfigure(1, weight=1)
        tk.Label(form, text="Destination", bg=BG, fg=SUB, font=FONT(9), width=11,
                 anchor="w").grid(row=0, column=0, sticky="w", pady=(0, S(6)))
        dest_row = tk.Frame(form, bg=BG)
        dest_row.grid(row=0, column=1, sticky="ew", pady=(0, S(6)))
        self.dest_var = tk.StringVar()
        tk.Entry(dest_row, textvariable=self.dest_var, bg=PANEL, fg=FG, relief="flat",
                 insertbackground=FG, font=FONT(9), highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", fill="x", expand=True, ipady=S(3))
        make_button(dest_row, "Browse", self.browse).pack(side="left", padx=(S(8), 0))

        table = tk.Frame(self.root, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14), pady=(S(10), 0))
        cols = ("n", "timeline", "range", "frames", "send", "name", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=8)
        for col, label, width, stretch in (
                ("n", "#", 34, False), ("timeline", "TIMELINE", 150, True),
                ("range", "IN - OUT", 190, False), ("frames", "FRAMES", 70, False),
                ("send", "RENDER", 200, True), ("name", "COMP NAME", 160, True),
                ("status", "STATUS", 150, True)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=S(width), anchor="w", stretch=stretch)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.tag_configure("failed", foreground=BAD)

        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=S(14), pady=(S(10), S(10)))
        make_button(actions, "+  Add In/Out", self.add, primary=True).pack(side="left")
        make_button(actions, "Edit Settings", self.edit_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Rename", self.rename_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Remove", self.remove_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Clear", self.clear).pack(side="left", padx=(S(6), 0))
        self.go_btn = make_button(actions, "Export All  >", self.go, primary=True)
        self.go_btn.pack(side="right")

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
        write_json(PREFS_FILE, self.prefs)
        try:
            self.root.attributes("-topmost", on)
        except Exception:
            pass
        self._paint_topmost()

    def on_scale_change(self, event=None):
        global SCALE
        self.prefs["ui_scale"] = self.scale_var.get()
        write_json(PREFS_FILE, self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        self._remember_dest()
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(700), S(400))
        self._style()
        self._build()
        self._dest_project = None
        self.refresh()

    def browse(self):
        start = self.dest_var.get().strip()
        chosen = filedialog.askdirectory(
            parent=self.root, title="Where should the audio and job files go?",
            initialdir=start if os.path.isdir(start) else None)
        if chosen:
            self.dest_var.set(os.path.normpath(chosen))

    def _remembered_dest(self, project_name):
        remembered = (self.ml_prefs.get("dest_by_project") or {}).get(project_name)
        if remembered and os.path.isdir(remembered):
            return remembered
        return self.ml["default_destination"]()

    def _remember_dest(self):
        dest = self.dest_var.get().strip()
        if not dest or self._dest_project is None:
            return
        self.ml_prefs = self.ml["load_prefs"]()
        self.ml_prefs.setdefault("dest_by_project", {})[self._dest_project] = dest
        self.ml["save_prefs"](self.ml_prefs)

    def show_in_out(self):
        project = self.project()
        timeline = project.GetCurrentTimeline() if project else None
        if not timeline:
            self.inout_label.config(text="No timeline open")
            return
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
        self.title_label.config(text=f"Motion Link Queue  -  {name or 'no project'}")
        # The destination follows the project, like the single export panel.
        if name != self._dest_project:
            self._dest_project = name
            self.dest_var.set(self._remembered_dest(name))
        self.show_in_out()
        self.tree.delete(*self.tree.get_children())
        for n, entry in enumerate(self.entries, start=1):
            status = entry.get("status", "queued")
            self.tree.insert("", "end", iid=entry["id"],
                             tags=("failed",) if status != "queued" else (),
                             values=(n, entry.get("timeline", ""),
                                     f"{entry.get('in_tc')}  -  {entry.get('out_tc')}",
                                     entry["out"] - entry["in"] + 1,
                                     self.ml["describe_settings"](entry.get("settings")),
                                     entry.get("name") or AUTO_NAME, status))
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
        settings = self.ask_settings()
        if not settings:
            self.say("Not added.")
            return
        in_tc, out_tc = self.describe_range(timeline, *marks)
        try:
            timeline_id = timeline.GetUniqueId()
        except Exception:
            timeline_id = ""
        self.entries.append({
            "id": uuid.uuid4().hex, "timeline": timeline.GetName(),
            "timeline_id": timeline_id, "in": marks[0], "out": marks[1],
            "in_tc": in_tc, "out_tc": out_tc, "name": "", "settings": settings,
            "status": "queued",
            "added": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self.save_queue()
        self.refresh()
        self.say(f"Queued {in_tc} - {out_tc} on '{timeline.GetName()}'.")

    def ask_settings(self, entry=None):
        """The render settings window, pre-filled with the range's own settings
        or else the last ones used. The last ones are shared with the single
        export panel."""
        self.ml_prefs = self.ml["load_prefs"]()
        last = (entry or {}).get("settings") or self.ml_prefs.get("render_settings")
        try:
            chosen = self.ml["ask_settings"](self.project(), last, parent=self.root,
                                             title="Motion Link Queue - settings for this range")
        except self.ml["ExportError"] as e:
            self.say(str(e), True)
            return None
        if chosen:
            self.ml_prefs["render_settings"] = chosen
            self.ml["save_prefs"](self.ml_prefs)
        return chosen

    def edit_selected(self):
        picked = self.selected_entries()
        if len(picked) != 1:
            self.say("Select one range to edit its settings.", True)
            return
        settings = self.ask_settings(picked[0])
        if settings:
            picked[0].update(settings=settings, status="queued")
            self.save_queue()
            self.refresh()

    def selected_entries(self):
        chosen = set(self.tree.selection())
        return [e for e in self.entries if e["id"] in chosen]

    def rename_selected(self):
        picked = self.selected_entries()
        if len(picked) != 1:
            self.say("Select one range to name its Composition.", True)
            return
        entry = picked[0]
        name = simpledialog.askstring(
            "Motion Link Queue",
            "Composition name for this range.\nLeave empty to use the .wav name "
            "(<Timeline>_MotionLink_NNN).",
            initialvalue=entry.get("name", ""), parent=self.root)
        if name is None:
            return
        entry["name"] = self.ml["sanitise"](name)
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
        if self.entries and messagebox.askyesno(
                "Motion Link Queue", f"Remove all {len(self.entries)} queued range(s)?",
                parent=self.root):
            self.entries.clear()
            self.save_queue()
            self.refresh()

    # -- running ----------------------------------------------------------
    def go(self):
        todo = list(self.entries)
        if not todo:
            self.say("Nothing queued.", True)
            return
        dest = self.dest_var.get().strip()
        if not dest:
            self.say("Pick a destination folder first.", True)
            return
        self._remember_dest()

        ml = self.ml
        project = self.project()
        original_timeline = project.GetCurrentTimeline()
        original_marks = {}                     # timeline key -> its In/Out before we touched it
        failed, sidecars = [], []
        self.go_btn.config(state="disabled")

        def fail(entry, label, reason):
            entry["status"] = "failed: " + reason[:60]
            failed.append(f"{label}: {reason}")

        try:
            for index, entry in enumerate(todo, start=1):
                label = f"{entry['timeline']} {entry['in_tc']}-{entry['out_tc']}"
                self.say(f"Rendering {index} of {len(todo)}:  {label}  ...")
                self.tree.selection_set(entry["id"])
                self.root.update()

                timeline = self.timeline_by_id(entry)
                if not timeline:
                    fail(entry, label, f"timeline '{entry['timeline']}' no longer exists")
                    continue
                key = entry.get("timeline_id") or entry["timeline"]
                if key not in original_marks:
                    original_marks[key] = (timeline, self.read_in_out(timeline))

                project.SetCurrentTimeline(timeline)
                timeline.ClearMarkInOut()
                timeline.SetMarkInOut(entry["in"], entry["out"])
                try:
                    sidecar, _job = ml["export_range"](
                        project, timeline, dest, entry.get("settings"), whole=False,
                        comp_name=entry.get("name") or None)
                except ml["ExportError"] as e:
                    fail(entry, label, str(e))
                    continue
                except Exception as e:
                    traceback.print_exc()
                    fail(entry, label, f"{e}")
                    continue
                sidecars.append(sidecar)
                entry["status"] = "done"
                self.save_queue()
        finally:
            # Put every timeline's In/Out back, and you back where you were.
            for timeline, marks in original_marks.values():
                try:
                    timeline.ClearMarkInOut()
                    if marks:
                        timeline.SetMarkInOut(*marks)
                except Exception:
                    pass
            if original_timeline:
                try:
                    project.SetCurrentTimeline(original_timeline)
                except Exception:
                    pass
            # Finished ranges come off the queue; failures stay, with the reason.
            self.entries[:] = [e for e in self.entries if e.get("status") != "done"]
            self.save_queue()
            self.go_btn.config(state="normal")
            self.refresh()

        if sidecars:
            try:
                ml["write_batch"](project.GetName(), sidecars)
            except OSError as e:
                failed.append(f"The batch list for Cavalry could not be written: {e}")

        summary = f"{len(sidecars)} of {len(todo)} range(s) exported."
        if sidecars:
            summary += "  In Cavalry: Window > Scripts > John > Motion Link Build Batch."
        self.say(summary, bool(failed))
        if failed:
            messagebox.showwarning("Motion Link Queue", summary + "\n\n" + "\n\n".join(failed),
                                   parent=self.root)

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            write_json(PREFS_FILE, self.prefs)
            self._remember_dest()
        except Exception:
            pass
        self.root.destroy()


def start():
    global SCALE
    prefs = read_json(PREFS_FILE, {})
    root = tk.Tk()
    try:
        ml = load_motion_link()
    except Exception as e:
        root.withdraw()
        messagebox.showerror("Motion Link Queue", str(e), parent=root)
        root.destroy()
        return
    SCALE = resolve_scale(prefs, root)
    app = MotionLinkQueue(root, ml)
    root.bind("<Escape>", lambda e: app._on_close())
    root.lift()
    root.attributes("-topmost", True)
    if not prefs.get("always_on_top", False):
        root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


start()
