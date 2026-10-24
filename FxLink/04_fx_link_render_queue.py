import ctypes
import datetime
import glob
import json
import os
import traceback
import uuid
import tkinter as tk
from tkinter import messagebox, ttk

# FxLink Queue - line up several ranges, then send them all to After Effects.
#
#   1. Set In and Out on the timeline, click "Add In/Out". The FxLink settings
#      window opens for that range (footage or audio only, presets) and the
#      range is queued with those settings.
#   2. Repeat as often as you like - different ranges, different timelines.
#   3. Click "Render FxLink". Every queued range goes through the full dynamic
#      link, one after another: render, AE comp, Pink clip on the timeline.
#
# The queue is saved per project, so it survives closing this window or
# Resolve. A range that goes through is taken off the queue; one that fails
# stays on it with the reason, ready to try again.
#
# This does not have its own copy of the FxLink pipeline. It loads the
# dynamic-link script itself and runs its main() once per range, so a fix there
# is a fix here - two copies would drift apart.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

QUEUE_FILE = os.path.expandvars(r"%APPDATA%\fxlink_queue.json")
PREFS_FILE = os.path.expandvars(r"%APPDATA%\fxlink_queue_prefs.json")
DYNAMIC_SCRIPT = "02_fx_link_dynamic.py"
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
# the dynamic-link script, loaded as a library
# --------------------------------------------------------------------------

def find_dynamic_script():
    """The deployed FxLink dynamic-link script, wherever it is."""
    candidates = []
    try:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), DYNAMIC_SCRIPT))
    except NameError:
        pass                                    # some embedded runners have no __file__
    candidates.append(os.path.join(RESOLVE_SCRIPTS, "John", "FxLink", DYNAMIC_SCRIPT))
    candidates += glob.glob(os.path.join(RESOLVE_SCRIPTS, "**", "*fx_link_dynamic.py"),
                            recursive=True)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_fxlink():
    """The dynamic-link script's functions, without running it."""
    path = find_dynamic_script()
    if not path:
        raise RuntimeError(f"Couldn't find {DYNAMIC_SCRIPT}. Run deploy.ps1.")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    ns = {"bmd": bmd, "__name__": "fxlink_dynamic", "__file__": path,
          "FXLINK_AS_LIBRARY": True}
    exec(compile(source, path, "exec"), ns)
    if "main" not in ns:
        raise RuntimeError(f"{os.path.basename(path)} is an older version without main() - "
                           f"run deploy.ps1 so both scripts match.")
    return ns


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


class FxLinkQueue:
    def __init__(self, root, fx):
        self.root = root
        self.fx = fx
        self.prefs = read_json(PREFS_FILE, {})
        self.queue_store = read_json(QUEUE_FILE, {})

        root.title("FxLink Queue")
        root.configure(bg=BG)
        root.minsize(S(720), S(360))
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
            marks = (timeline.GetMarkInOut() or {}).get("video") or {}
        except Exception:
            return None
        if "in" in marks and "out" in marks:
            return int(marks["in"]), int(marks["out"])
        return None

    @staticmethod
    def clock(timeline):
        fps = timeline.GetSetting("timelineFrameRate") or 24
        drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"
        return fps, drop

    def describe_range(self, timeline, mark_in, mark_out):
        fps, drop = self.clock(timeline)
        start = timeline.GetStartFrame()
        return (frame_to_tc(start + mark_in, fps, drop),
                frame_to_tc(start + mark_out, fps, drop))

    def preset_names(self):
        presets = self.project().GetRenderPresets() or {}
        if isinstance(presets, dict):
            return [presets[k] for k in sorted(presets.keys())]
        return list(presets)

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

        table = tk.Frame(self.root, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14), pady=(S(10), 0))
        cols = ("n", "timeline", "range", "frames", "mode", "presets", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=8)
        for col, label, width, stretch in (
                ("n", "#", 34, False), ("timeline", "TIMELINE", 150, True),
                ("range", "IN - OUT", 190, False), ("frames", "FRAMES", 70, False),
                ("mode", "SEND", 80, False), ("presets", "PRESETS", 220, True),
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
        make_button(actions, "Remove", self.remove_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Clear", self.clear).pack(side="left", padx=(S(6), 0))
        self.go_btn = make_button(actions, "Render FxLink  >", self.go, primary=True)
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
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(720), S(360))
        self._style()
        self._build()
        self.refresh()

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
        self.title_label.config(text=f"FxLink Queue  -  {project.GetName() if project else 'no project'}")
        self.show_in_out()
        self.tree.delete(*self.tree.get_children())
        for n, entry in enumerate(self.entries, start=1):
            if entry.get("mode") == self.fx["MODE_AUDIO"]:
                mode, presets = "Audio", f"{entry.get('preset')}  +  {entry.get('audio_preset')}"
            else:
                mode, presets = "Footage", entry.get("preset", "")
            status = entry.get("status", "queued")
            self.tree.insert("", "end", iid=entry["id"], tags=("failed",) if status != "queued" else (),
                             values=(n, entry.get("timeline", ""),
                                     f"{entry.get('in_tc')}  -  {entry.get('out_tc')}",
                                     entry["out"] - entry["in"] + 1, mode, presets, status))
        count = len(self.entries)
        self.say(f"{count} range(s) queued." if count else
                 "Set In/Out on the timeline, then Add In/Out.")

    # -- queue editing ----------------------------------------------------
    def ask_settings(self, preset_names, entry=None):
        """The dynamic link's own settings window, pre-filled."""
        last = self.fx["load_prefs"]()
        source = entry or {}
        return self.fx["ask_options"](
            preset_names,
            last_preset=source.get("preset", last.get("last_preset")),
            last_mode=source.get("mode", last.get("last_mode", self.fx["MODE_FOOTAGE"])),
            last_audio_preset=source.get("audio_preset", last.get("last_audio_preset")),
            parent=self.root, title="FxLink Queue - settings for this range")

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
        names = self.preset_names()
        if not names:
            self.say("No render presets in this project.", True)
            return

        preset, mode, audio_preset = self.ask_settings(names)
        if not preset:
            self.say("Not added.")
            return
        # The next range pre-fills with these, like the normal FxLink run.
        self.fx["save_prefs"]({**self.fx["load_prefs"](), "last_preset": preset,
                               "last_mode": mode, "last_audio_preset": audio_preset})

        in_tc, out_tc = self.describe_range(timeline, *marks)
        try:
            timeline_id = timeline.GetUniqueId()
        except Exception:
            timeline_id = ""
        self.entries.append({
            "id": uuid.uuid4().hex, "timeline": timeline.GetName(),
            "timeline_id": timeline_id, "in": marks[0], "out": marks[1],
            "in_tc": in_tc, "out_tc": out_tc, "preset": preset, "mode": mode,
            "audio_preset": audio_preset, "status": "queued",
            "added": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self.save_queue()
        self.refresh()
        self.say(f"Queued {in_tc} - {out_tc} on '{timeline.GetName()}'.")

    def selected_entries(self):
        chosen = set(self.tree.selection())
        return [e for e in self.entries if e["id"] in chosen]

    def edit_selected(self):
        picked = self.selected_entries()
        if len(picked) != 1:
            self.say("Select one range to edit its settings.", True)
            return
        entry = picked[0]
        preset, mode, audio_preset = self.ask_settings(self.preset_names(), entry)
        if preset:
            entry.update(preset=preset, mode=mode, audio_preset=audio_preset, status="queued")
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
                "FxLink Queue", f"Remove all {len(self.entries)} queued range(s)?", parent=self.root):
            self.entries.clear()
            self.save_queue()
            self.refresh()

    # -- running ----------------------------------------------------------
    def preflight(self):
        """AE must be up with a saved project BEFORE anything renders - otherwise
        every range renders and then fails at the AE step."""
        fx = self.fx
        exe = fx["find_afterfx"](fx["load_prefs"]())
        if not exe:
            return "After Effects isn't installed where FxLink looks (C:\\Program Files\\Adobe)."
        if not fx["afterfx_running"]():
            return "After Effects isn't running. Open your AE project, then Render FxLink."
        answer = fx["run_extendscript"](exe, fx["GET_PROJECT_JSX"], timeout=15)
        if answer is None:
            return "After Effects didn't respond - is a dialog open in it?"
        if answer == "__UNSAVED__":
            return "The open AE project has never been saved. Save it first so it has a folder."
        return None

    def go(self):
        todo = list(self.entries)
        if not todo:
            self.say("Nothing queued.", True)
            return
        problem = self.preflight()
        if problem:
            self.say(problem, True)
            messagebox.showwarning("FxLink Queue", problem, parent=self.root)
            return

        project = self.project()
        original_timeline = project.GetCurrentTimeline()
        original_marks = {}                     # timeline uid -> its In/Out before we touched it
        done, failed = 0, []
        self.go_btn.config(state="disabled")

        try:
            for index, entry in enumerate(todo, start=1):
                label = f"{entry['timeline']} {entry['in_tc']}-{entry['out_tc']}"
                self.say(f"Rendering {index} of {len(todo)}:  {label}  ...")
                self.tree.selection_set(entry["id"])
                self.root.update()

                timeline = self.timeline_by_id(entry)
                if not timeline:
                    entry["status"] = "failed: timeline not found"
                    failed.append(f"{label}: timeline '{entry['timeline']}' no longer exists")
                    continue
                key = entry.get("timeline_id") or entry["timeline"]
                if key not in original_marks:
                    original_marks[key] = (timeline, self.read_in_out(timeline))

                project.SetCurrentTimeline(timeline)
                timeline.ClearMarkInOut()
                timeline.SetMarkInOut(entry["in"], entry["out"])

                self.fx["PROBLEMS"].clear()
                try:
                    self.fx["main"](job=entry)
                except Exception as e:
                    traceback.print_exc()
                    self.fx["PROBLEMS"].append(f"Error: {e}")

                errors = [p for p in self.fx["PROBLEMS"] if p.startswith("Error")]
                if errors:
                    entry["status"] = "failed: " + errors[0].replace("Error: ", "")[:60]
                    failed.append(f"{label}: {errors[0]}")
                else:
                    entry["status"] = "done"
                    done += 1
                    for warning in self.fx["PROBLEMS"]:
                        failed.append(f"{label}: {warning}")
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

        summary = f"{done} of {len(todo)} range(s) sent to After Effects."
        self.say(summary, bool(failed))
        if failed:
            messagebox.showwarning("FxLink Queue", summary + "\n\n" + "\n\n".join(failed),
                                   parent=self.root)

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            write_json(PREFS_FILE, self.prefs)
        except Exception:
            pass
        self.root.destroy()


def start():
    global SCALE
    prefs = read_json(PREFS_FILE, {})
    root = tk.Tk()
    try:
        fx = load_fxlink()
    except Exception as e:
        root.withdraw()
        messagebox.showerror("FxLink Queue", str(e), parent=root)
        root.destroy()
        return
    SCALE = resolve_scale(prefs, root)
    app = FxLinkQueue(root, fx)
    root.bind("<Escape>", lambda e: app._on_close())
    root.lift()
    root.attributes("-topmost", True)
    if not prefs.get("always_on_top", False):
        root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


start()
