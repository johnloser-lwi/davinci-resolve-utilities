import ctypes
import json
import os
import time
import tkinter as tk
from tkinter import ttk

# Clean Orphan PreComps - delete nested timelines in the PreComps bin that
# nothing uses any more.
#
# Testing and re-nesting leaves a trail of abandoned nest timelines. This finds
# the ones in the PreComps bin that appear on NO timeline anywhere in the
# project, lists them, and deletes the ones you tick.
#
# Deliberately narrow, because deleting timelines cannot be undone:
#   - only timelines inside the PreComps bin are ever candidates
#   - a timeline used by anything (including another nest) is never listed
#   - the timeline you currently have open is never deleted
#   - nothing happens until you press Delete
#
# Nesting is discovered the same way as the hierarchy tool: a nested timeline
# is a timeline item whose media pool item has Type == "Timeline".

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\clean_precomps_prefs.json")
PRECOMPS_BIN = "PreComps"

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
DANGER = "#c05252"
DANGER_HOVER = "#d06060"
GOLD = "#e0b040"

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]
SCALE = 1.0


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
    elif isinstance(setting, (int, float)):
        return max(1.0, min(float(setting), 3.0))
    return detect_scale(root)


def load_prefs():
    if os.path.exists(PREFS_FILE):
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_prefs(prefs):
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception as e:
        print("Could not save prefs: " + str(e))


def make_button(parent, text, command, primary=False, danger=False):
    base = DANGER if danger else (ACCENT if primary else BTN)
    hover = DANGER_HOVER if danger else (ACCENT_HOVER if primary else BTN_HOVER)
    btn = tk.Button(parent, text=text, command=command, bg=base,
                    fg="#ffffff" if (primary or danger) else FG, relief="flat",
                    bd=0, padx=S(12), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


# --------------------------------------------------------------------------
# Resolve bridge
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


def current_project():
    try:
        return projectManager.GetCurrentProject()
    except Exception:
        return None


def find_precomps_bin(media_pool):
    try:
        for folder in (media_pool.GetRootFolder().GetSubFolderList() or []):
            if folder.GetName() == PRECOMPS_BIN:
                return folder
    except Exception as e:
        print("Could not read the media pool: " + str(e))
    return None


def timelines_in_bin(folder):
    """Names of timelines filed in this bin."""
    names = set()
    try:
        for clip in (folder.GetClipList() or []):
            try:
                if clip.GetClipProperty("Type") == "Timeline":
                    names.add(clip.GetName())
            except Exception:
                continue
    except Exception:
        pass
    return names


def scan_usage(project):
    """{timeline name -> [names of timelines using it]} across the project."""
    timelines, order = {}, []
    for i in range(1, (project.GetTimelineCount() or 0) + 1):
        try:
            t = project.GetTimelineByIndex(i)
        except Exception:
            continue
        if t:
            name = t.GetName()
            timelines[name] = t
            order.append(name)

    names = set(timelines)
    used_by = {n: [] for n in order}
    scanned = 0
    for name in order:
        tl = timelines[name]
        try:
            track_count = tl.GetTrackCount("video") or 0
        except Exception:
            continue
        for ti in range(1, track_count + 1):
            try:
                items = tl.GetItemListInTrack("video", ti) or []
            except Exception:
                continue
            for item in items:
                scanned += 1
                try:
                    item_name = item.GetName()
                except Exception:
                    continue
                if item_name not in names or item_name == name:
                    continue
                try:
                    mpi = item.GetMediaPoolItem()
                    if not mpi or mpi.GetClipProperty("Type") != "Timeline":
                        continue
                except Exception:
                    continue
                used_by[item_name].append(name)
    return timelines, used_by, scanned


def describe(timeline):
    """Cheap summary of what a timeline holds, so you can judge before deleting."""
    items, frames = 0, 0
    try:
        for ti in range(1, (timeline.GetTrackCount("video") or 0) + 1):
            for item in (timeline.GetItemListInTrack("video", ti) or []):
                items += 1
                frames = max(frames, item.GetEnd() - (timeline.GetStartFrame() or 0))
    except Exception:
        pass
    if not items:
        return "empty"
    return f"{items} clip(s), {frames} frames"


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class CleanPanel:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.rows = {}          # iid -> {"name", "timeline", "checked"}

        root.title("Clean Orphan PreComps")
        root.configure(bg=BG)
        root.minsize(S(620), S(420))
        geo = self.prefs.get("geometry")
        if geo and abs(float(self.prefs.get("geometry_scale", 1.0)) - SCALE) < 0.01:
            try:
                root.geometry(geo)
            except Exception:
                pass

        self._style()
        self._build()
        self.rescan()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Escape>", lambda e: self._on_close())

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(23), font=FONT(9))
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
                  fieldbackground=[("readonly", PANEL), ("!disabled", PANEL)],
                  foreground=[("readonly", FG), ("!disabled", FG)],
                  selectbackground=[("readonly", PANEL), ("!disabled", PANEL)],
                  selectforeground=[("readonly", FG), ("!disabled", FG)],
                  background=[("active", BTN_HOVER), ("readonly", BTN)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _build(self):
        bottom = tk.Frame(self.root, bg=PANEL2)
        bottom.pack(fill="x", side="bottom")

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(6)))
        tk.Label(head, text="Unused timelines in the PreComps bin", bg=BG, fg=FG,
                 anchor="w", font=FONT(11, "bold")).pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.info = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.info.pack(side="left")
        make_button(row, "Rescan", self.rescan).pack(side="right")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=S(14))
        self.tree = ttk.Treeview(body, columns=("tick", "name", "detail"),
                                 show="headings")
        self.tree.heading("tick", text="")
        self.tree.heading("name", text="TIMELINE")
        self.tree.heading("detail", text="CONTAINS")
        self.tree.column("tick", width=S(34), anchor="center", stretch=False)
        self.tree.column("name", width=S(330), anchor="w")
        self.tree.column("detail", width=S(160), anchor="w")
        vs = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.tag_configure("open_now", foreground=GOLD)

        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=S(14), pady=(S(10), 0))
        self.delete_btn = make_button(actions, "Delete ticked timelines",
                                      self.do_delete, danger=True)
        self.delete_btn.pack(side="left")
        make_button(actions, "None", lambda: self.set_all(False)).pack(side="right")
        make_button(actions, "All", lambda: self.set_all(True)).pack(side="right",
                                                                     padx=(0, S(6)))

        tk.Label(self.root,
                 text="Only timelines filed in the PreComps bin and used by "
                      "nothing are listed. Deleting a timeline cannot be undone. "
                      "Re-run afterwards to catch nests that only existed inside "
                      "the ones you removed.",
                 bg=BG, fg=SUB, font=FONT(8), anchor="w", justify="left",
                 wraplength=S(560)).pack(fill="x", padx=S(14), pady=(S(8), S(12)))

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
        self.status.config(text=msg, fg="#e07070" if error else SUB)

    def on_scale_change(self, event=None):
        global SCALE
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()
            except Exception:
                pass
        self.prefs["ui_scale"] = self.scale_var.get()
        save_prefs(self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(620), S(420))
        self._style()
        self._build()
        self.rescan()

    # -- scanning ---------------------------------------------------------
    def rescan(self):
        project = current_project()
        if not project:
            self.say("No project open.", True)
            return
        t0 = time.time()
        media_pool = project.GetMediaPool()
        folder = find_precomps_bin(media_pool)
        self.tree.delete(*self.tree.get_children())
        self.rows = {}

        if not folder:
            self.info.config(text="")
            self.say(f"No '{PRECOMPS_BIN}' bin in this project - nothing to clean.",
                     True)
            return

        in_bin = timelines_in_bin(folder)
        timelines, used_by, scanned = scan_usage(project)
        try:
            current = project.GetCurrentTimeline()
            current_name = current.GetName() if current else ""
        except Exception:
            current_name = ""

        orphans = sorted(n for n in in_bin
                         if n in timelines and not used_by.get(n))
        kept = len(in_bin) - len(orphans)

        for i, name in enumerate(orphans):
            tl = timelines[name]
            is_open = (name == current_name)
            self.tree.insert("", "end", iid=f"r{i}",
                             values=("" if is_open else "x", name,
                                     "currently open - skipped" if is_open
                                     else describe(tl)),
                             tags=("open_now",) if is_open else ())
            self.rows[f"r{i}"] = {"name": name, "timeline": tl,
                                  "checked": not is_open, "locked": is_open}

        self.info.config(
            text=f"{len(in_bin)} in {PRECOMPS_BIN} · {len(orphans)} unused · "
                 f"{kept} still in use (scanned {scanned} item(s) in "
                 f"{time.time() - t0:.2f}s)")
        if orphans:
            self.say(f"{len(orphans)} unused timeline(s). Untick anything you "
                     f"want to keep, then Delete.")
        else:
            self.say(f"Nothing to clean - every timeline in {PRECOMPS_BIN} is in use.")

    def on_click(self, event):
        """Clicking the tick column toggles that row."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        row = self.rows.get(iid)
        if not row or row["locked"]:
            return
        row["checked"] = not row["checked"]
        values = list(self.tree.item(iid, "values"))
        values[0] = "x" if row["checked"] else ""
        self.tree.item(iid, values=values)
        self._update_button()

    def set_all(self, checked):
        for iid, row in self.rows.items():
            if row["locked"]:
                continue
            row["checked"] = checked
            values = list(self.tree.item(iid, "values"))
            values[0] = "x" if checked else ""
            self.tree.item(iid, values=values)
        self._update_button()

    def _update_button(self):
        n = sum(1 for r in self.rows.values() if r["checked"])
        self.delete_btn.config(text=f"Delete {n} timeline(s)" if n
                               else "Delete ticked timelines")

    # -- deletion ---------------------------------------------------------
    def do_delete(self):
        project = current_project()
        if not project:
            self.say("No project open.", True)
            return
        doomed = [r for r in self.rows.values() if r["checked"] and not r["locked"]]
        if not doomed:
            self.say("Nothing ticked.", True)
            return

        print("")
        print(f"Deleting {len(doomed)} unused timeline(s) from {PRECOMPS_BIN}:")
        for row in doomed:
            print("  " + row["name"])

        media_pool = project.GetMediaPool()
        try:
            ok = media_pool.DeleteTimelines([r["timeline"] for r in doomed])
        except Exception as e:
            print("DeleteTimelines failed: " + str(e))
            self.say(f"Delete failed: {e}", True)
            return

        if ok:
            print(f"  removed {len(doomed)}")
            self.say(f"Deleted {len(doomed)} timeline(s).")
        else:
            print("  DeleteTimelines returned False")
            self.say("Resolve refused the delete - is one of them open?", True)
        self.rescan()

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            save_prefs(self.prefs)
        except Exception:
            pass
        self.root.destroy()


prefs = load_prefs()
root = tk.Tk()
SCALE = resolve_scale(prefs, root)
app = CleanPanel(root)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
