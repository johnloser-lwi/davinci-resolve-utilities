import ctypes
import json
import os
import glob
import re
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import filedialog, ttk

# Motion Link - hand a timeline's timing over to Cavalry.
#
# Renders the current range - its audio, or video, whichever you choose - and
# writes a small job file describing the timeline. The companion Cavalry script
# ("Motion Link Build Comp") reads that job and builds a Composition with
# matching frame rate, length and resolution, with what you rendered in it as a
# reference.
#
# The rendering is FxLink's own code (see "export pipeline" below), so both
# tools pick presets the same way and share every render fix.
#
# Nothing comes back. This is deliberately one-way: Cavalry builds an element,
# and the element gets composited in Fusion later.
#
# WHY A JOB FILE RATHER THAN A LIVE LINK
#   Cavalry has no equivalent of "AfterFX.exe -r script.jsx", so Resolve cannot
#   push a command into a running Cavalry. Its CLI is Enterprise-only and was
#   removed in 2.7; its in-app web server has to be started by hand every
#   session, which is more of a chore than clicking a menu item. So the handoff
#   is a file, at ONE fixed path, because Cavalry's scripting API can read a
#   file but cannot list a directory to find the newest one.
#
# FRAME MAPPING
#   Cavalry frame 0 is the start of the exported range, not the start of the
#   timeline:  resolve_frame = mark_in + cavalry_frame

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\motion_link_prefs.json")
INBOX_DIR = os.path.expandvars(r"%APPDATA%\motion_link")
JOB_VERSION = 2     # 2: "media" (audio or video) replaces "audio"

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

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]
SCALE = 1.0

RANGE_MODES = ["In / Out", "Whole timeline"]


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
        print(f"Could not save prefs: {e}")


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
# Resolve bridge
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


def current_project():
    try:
        return projectManager.GetCurrentProject()
    except Exception:
        return None


def current_timeline():
    project = current_project()
    try:
        return project.GetCurrentTimeline() if project else None
    except Exception:
        return None


def frame_to_tc(frame, fps, drop_frame):
    fps_round = max(1, round(float(fps)))
    sep = ":"
    if drop_frame:
        drop = 4 if fps_round == 60 else 2
        frames_per_min = fps_round * 60 - drop
        frames_per_10min = fps_round * 600 - drop * 9
        d, m = divmod(frame, frames_per_10min)
        frame += drop * 9 * d
        if m >= drop:
            frame += drop * ((m - drop) // frames_per_min)
        sep = ";"
    f = frame % fps_round
    s = (frame // fps_round) % 60
    mnt = (frame // (fps_round * 60)) % 60
    h = (frame // (fps_round * 3600)) % 24
    return "%02d:%02d:%02d%s%02d" % (h, mnt, s, sep, f)


def timeline_facts(timeline):
    """Everything the Cavalry side needs to size a Composition."""
    def setting(name, default=None):
        try:
            value = timeline.GetSetting(name)
            return value if value not in (None, "") else default
        except Exception:
            return default

    fps_string = str(setting("timelineFrameRate", "24"))
    try:
        fps = float(fps_string)
    except ValueError:
        fps = 24.0
    try:
        width = int(float(setting("timelineResolutionWidth", 1920)))
        height = int(float(setting("timelineResolutionHeight", 1080)))
    except (TypeError, ValueError):
        width, height = 1920, 1080

    return {
        "name": timeline.GetName(),
        "fps": fps,
        "fps_string": fps_string,
        "width": width,
        "height": height,
        "drop_frame": str(setting("timelineDropFrameTimecode", "0")) == "1",
        "start_frame": timeline.GetStartFrame(),
        "end_frame": timeline.GetEndFrame(),
    }


def next_numbered_name(folder, base, taken=()):
    """<base>001, incrementing past whatever is already in the folder.

    taken: stems already claimed by earlier jobs in the same batch. Their files
    don't exist until the batch renders, so without this every range in a
    batch would be given the same number.
    """
    try:
        names = [os.path.splitext(entry)[0] for entry in os.listdir(folder)]
    except OSError:
        names = []
    highest = 0
    for stem in names + list(taken):
        match = re.fullmatch(re.escape(base) + r"(\d+)", stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{base}{highest + 1:03d}"


def find_output(folder, stem):
    """The rendered file, whatever extension Resolve chose to give it."""
    try:
        for entry in os.listdir(folder):
            name, ext = os.path.splitext(entry)
            if name == stem and ext.lower() != ".json":      # not our own job file
                return os.path.join(folder, entry)
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------
# export pipeline - shared by this panel and the Motion Link Queue
# --------------------------------------------------------------------------
#
# The render is FxLink's: its dynamic-link script is loaded as a library and its
# settings window, preset handling and render monitoring are used as they are.
# Motion Link only adds the job file that Cavalry reads.
#
# Every render loads a preset first. An earlier version instead switched audio
# settings on whatever the Deliver page had loaded - after an FxLink run that
# was a TIFF sequence, which can't produce audio, so the render sometimes never
# started. FxLink had already been through exactly that.

MODE_VIDEO = "footage"      # FxLink's mode names, so its settings window is shared
MODE_AUDIO = "audio"
FXLINK_SCRIPT = "02_fx_link_dynamic.py"
RESOLVE_SCRIPTS = os.path.expandvars(
    r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility")
_FX = None


class ExportError(Exception):
    """A problem worded for the user."""


def find_fxlink_script():
    candidates = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(os.path.dirname(here), "FxLink", FXLINK_SCRIPT))
    except NameError:
        pass                                    # some embedded runners have no __file__
    candidates.append(os.path.join(RESOLVE_SCRIPTS, "John", "FxLink", FXLINK_SCRIPT))
    candidates += glob.glob(os.path.join(RESOLVE_SCRIPTS, "**", "*fx_link_dynamic.py"),
                            recursive=True)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def fxlink():
    """FxLink's functions, loaded once, without running FxLink."""
    global _FX
    if _FX is None:
        path = find_fxlink_script()
        if not path:
            raise ExportError(f"Motion Link renders with FxLink's code and couldn't find "
                              f"{FXLINK_SCRIPT}. Run deploy.ps1.")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        ns = {"bmd": bmd, "__name__": "fxlink_dynamic", "__file__": path,
              "FXLINK_AS_LIBRARY": True}
        exec(compile(source, path, "exec"), ns)
        if "run_render" not in ns or "target" not in ns["ask_options"].__code__.co_varnames:
            raise ExportError(f"{os.path.basename(path)} is an older version - run deploy.ps1 "
                              f"so FxLink and Motion Link match.")
        _FX = ns
    return _FX


def preset_names(project):
    presets = project.GetRenderPresets() or {}
    if isinstance(presets, dict):
        return [presets[k] for k in sorted(presets.keys())]
    return list(presets)


def ask_settings(project, last=None, parent=None, title="Motion Link"):
    """FxLink's settings window, worded for Cavalry.

    Returns {"mode", "preset", "audio_preset"}, or None if cancelled.
    """
    names = preset_names(project)
    if not names:
        raise ExportError("No render presets in this project - make one on the Deliver page.")
    last = last or {}
    preset, mode, audio_preset = fxlink()["ask_options"](
        names, last_preset=last.get("preset"), last_mode=last.get("mode", MODE_AUDIO),
        last_audio_preset=last.get("audio_preset"), parent=parent, title=title,
        target="Cavalry")
    if not mode:
        return None
    return {"mode": mode, "preset": preset, "audio_preset": audio_preset}


def describe_settings(settings):
    if not settings:
        return "not chosen yet"
    if settings.get("mode") == MODE_AUDIO:
        return f"Audio only  ·  {settings.get('audio_preset')}"
    return f"Video  ·  {settings.get('preset')}"


def export_range(project, timeline, dest, settings, whole, comp_name=None):
    """Render the timeline's In/Out (or all of it) and write the job for Cavalry.

    settings comes from ask_settings. Returns (sidecar path, job dict); raises
    ExportError with a message for the user.
    """
    fx = fxlink()
    facts = timeline_facts(timeline)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        raise ExportError(f"Cannot use that destination: {e}")

    audio = (settings or {}).get("mode") == MODE_AUDIO
    preset = (settings or {}).get("audio_preset" if audio else "preset")
    if not preset:
        raise ExportError("No render preset chosen - set the render settings first.")
    if not project.LoadRenderPreset(preset):
        raise ExportError(f"Couldn't load the '{preset}' render preset.")
    if not audio and fx["loaded_preset_is_sequence"](project):
        raise ExportError(f"'{preset}' renders an image sequence. Cavalry gets one file - "
                          f"pick a movie preset (ProRes, H.264...).")

    stem = next_numbered_name(dest, f"{sanitise(facts['name'])}_MotionLink_")
    print(f"Rendering {'audio' if audio else 'video'} with '{preset}': {stem} -> {dest}")
    fx["PROBLEMS"].clear()
    try:
        _job_id, mark_in, mark_out = fx["run_render"](
            project, {"SelectAllFrames": whole, "TargetDir": dest, "CustomName": stem},
            label="audio" if audio else "video")
    except RuntimeError as e:
        raise ExportError(str(e))
    for note in fx["PROBLEMS"]:
        print(note)

    media_path = find_output(dest, stem)
    if not media_path:
        raise ExportError(f"The render finished but no file named '{stem}' turned up in {dest}.")
    # Check the preset made what was asked for, rather than handing Cavalry a
    # video file as the "audio" or the other way round.
    is_audio_file = os.path.splitext(media_path)[1].lower() in fx["AUDIO_EXTS"]
    if audio and not is_audio_file:
        raise ExportError(f"The '{preset}' preset rendered '{os.path.basename(media_path)}', "
                          f"which isn't an audio file. {fx['AUDIO_PRESET_HOWTO']}")
    if not audio and is_audio_file:
        raise ExportError(f"The '{preset}' preset only rendered audio. Choose Audio only, "
                          f"or a preset that exports video.")

    if mark_in is None or mark_out is None:
        mark_in = facts["start_frame"]
        mark_out = facts["end_frame"] - 1
        print("Render job did not report MarkIn/MarkOut; "
              f"falling back to the whole timeline ({mark_in}-{mark_out}).")
    mark_in, mark_out = int(mark_in), int(mark_out)
    duration = mark_out - mark_in + 1

    job = {
        "motion_link_version": JOB_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "resolve": {
            "project": project.GetName(),
            "timeline": facts["name"],
            "timeline_start_frame": facts["start_frame"],
            "timeline_start_timecode": frame_to_tc(facts["start_frame"],
                                                   facts["fps"], facts["drop_frame"]),
            "drop_frame": facts["drop_frame"],
        },
        "comp": {
            # An untouched Comp name tracks the export name, so the Composition
            # in Cavalry and the file on disk are called the same thing.
            "name": sanitise(comp_name) if comp_name and sanitise(comp_name) else stem,
            "fps": facts["fps"],
            # Kept alongside the float in case Cavalry's frame rate turns
            # out to be an enum of named rates rather than a number.
            "fps_string": facts["fps_string"],
            "width": facts["width"],
            "height": facts["height"],
            "duration_frames": duration,
            "start_frame": 0,
        },
        "range": {
            "mode": "whole" if whole else "in_out",
            "mark_in": mark_in,
            "mark_out": mark_out,
            "in_timecode": frame_to_tc(mark_in, facts["fps"], facts["drop_frame"]),
            "out_timecode": frame_to_tc(mark_out, facts["fps"], facts["drop_frame"]),
        },
        "media": {
            # Forward slashes throughout so the Cavalry side never has to
            # deal with backslash escapes.
            "path": media_path.replace("\\", "/"),
            "kind": "audio" if audio else "video",
            "preset": preset,
            "offset_frames": 0,
        },
    }

    sidecar = os.path.join(dest, stem + ".json")
    for path in (sidecar, os.path.join(INBOX_DIR, "latest_job.json")):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(job, f, indent=2)
        except OSError as e:
            print(f"Could not write {path}: {e}")
            # The fixed-path copy is the one Cavalry reads; without it the
            # handoff is broken even though the render went fine.
            raise ExportError("Rendered, but the job file for Cavalry could not be "
                              "written - see the console.")

    print(f"\nMedia : {media_path}")
    print(f"Comp  : {job['comp']['name']}  {facts['width']}x{facts['height']}  "
          f"{facts['fps_string']} fps  {duration} frames")
    print(f"Timing: Cavalry frame 0 = Resolve frame {mark_in} "
          f"({job['range']['in_timecode']})")
    return sidecar, job


def write_batch(project_name, sidecars):
    """%APPDATA%\\motion_link\\latest_batch.json - what "Build Batch" reads."""
    path = os.path.join(INBOX_DIR, "latest_batch.json")
    os.makedirs(INBOX_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "motion_link_batch_version": 1,
            "created": datetime.now().isoformat(timespec="seconds"),
            "project": project_name,
            "jobs": [p.replace("\\", "/") for p in sidecars],
        }, f, indent=2)
    return path


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class MotionLink:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.facts = None
        self.busy = False
        self._dest_project = None

        root.title("Motion Link - Export for Cavalry")
        root.configure(bg=BG)
        root.minsize(S(600), S(400))
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

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
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

        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True)

        head = tk.Frame(content, bg=BG)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        self.tl_label = tk.Label(head, text="-", bg=BG, fg=FG, anchor="w",
                                 font=FONT(11, "bold"))
        self.tl_label.pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.info = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.info.pack(side="left")
        make_button(row, "Refresh", self.refresh).pack(side="right")
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0, font=FONT(9),
                                 cursor="hand2", padx=S(10), pady=S(5),
                                 highlightthickness=0, command=self.toggle_topmost)
        self.top_btn.pack(side="right", padx=(0, S(6)))
        self._paint_topmost()

        form = tk.Frame(content, bg=BG)
        form.pack(fill="x", padx=S(14), pady=(S(14), 0))
        form.columnconfigure(1, weight=1)

        def label(text, r):
            tk.Label(form, text=text, bg=BG, fg=SUB, font=FONT(9), width=11,
                     anchor="w").grid(row=r, column=0, sticky="w", pady=(0, S(8)))

        label("Range", 0)
        range_row = tk.Frame(form, bg=BG)
        range_row.grid(row=0, column=1, sticky="ew", pady=(0, S(8)))
        self.range_var = tk.StringVar(value=self.prefs.get("range_mode", RANGE_MODES[0]))
        for mode in RANGE_MODES:
            tk.Radiobutton(range_row, text=mode, variable=self.range_var, value=mode,
                           command=self._save_range, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG, font=FONT(9),
                           highlightthickness=0).pack(side="left", padx=(0, S(14)))

        label("Destination", 1)
        dest_row = tk.Frame(form, bg=BG)
        dest_row.grid(row=1, column=1, sticky="ew", pady=(0, S(8)))
        self.dest_var = tk.StringVar()
        tk.Entry(dest_row, textvariable=self.dest_var, bg=PANEL, fg=FG, relief="flat",
                 insertbackground=FG, font=FONT(9), highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", fill="x", expand=True, ipady=S(3))
        make_button(dest_row, "Browse", self.browse).pack(side="left", padx=(S(8), 0))
        self.dest_var.trace_add("write", lambda *a: self._sync_comp_name())

        label("Comp name", 2)
        self.comp_var = tk.StringVar()
        # Typing here marks the name as yours, so Refresh stops overwriting it.
        self.comp_var.trace_add("write", lambda *a: setattr(self, "_name_edited", True))
        tk.Entry(form, textvariable=self.comp_var, bg=PANEL, fg=FG, relief="flat",
                 insertbackground=FG, font=FONT(9), highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).grid(row=2, column=1, sticky="ew", ipady=S(3), pady=(0, S(8)))
        self._name_edited = False

        label("Render", 3)
        render_row = tk.Frame(form, bg=BG)
        render_row.grid(row=3, column=1, sticky="ew", pady=(0, S(8)))
        self.render_label = tk.Label(render_row, bg=BG, fg=FG, anchor="w", font=FONT(9),
                                     text=describe_settings(self.prefs.get("render_settings")))
        self.render_label.pack(side="left")
        make_button(render_row, "Settings", self.choose_settings).pack(side="right")

        action = tk.Frame(content, bg=BG)
        action.pack(fill="x", padx=S(14), pady=(S(6), 0))
        self.export_btn = make_button(action, "Export for Cavalry", self.do_export,
                                      primary=True)
        self.export_btn.pack(side="left")
        self.result = tk.Label(action, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.result.pack(side="left", padx=(S(12), 0))

        tk.Label(content, text="Renders the range - audio or video, as set in "
                               "Settings - and writes a job file next to it. In Cavalry, run Window > Scripts > John "
                               "> Motion Link Build Comp to build a matching "
                               "Composition with that render in it.\n"
                               "Nothing comes back from Cavalry - render your element "
                               "there and bring it into Fusion yourself.",
                 bg=BG, fg=SUB, font=FONT(8), anchor="w", justify="left",
                 wraplength=S(560)).pack(fill="x", padx=S(14), pady=(S(14), S(14)))

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
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    def _paint_topmost(self):
        on = bool(self.prefs.get("always_on_top", False))
        self.top_btn.config(bg=ACCENT if on else BTN,
                            fg="#ffffff" if on else FG,
                            activebackground=ACCENT_HOVER if on else BTN_HOVER)

    def toggle_topmost(self):
        on = not bool(self.prefs.get("always_on_top", False))
        self.prefs["always_on_top"] = on
        save_prefs(self.prefs)
        try:
            self.root.attributes("-topmost", on)
        except Exception:
            pass
        self._paint_topmost()

    def _save_range(self):
        self.prefs["range_mode"] = self.range_var.get()
        save_prefs(self.prefs)

    def choose_settings(self):
        """FxLink's settings window: Video or Audio only, and the presets."""
        project = current_project()
        if not project:
            return None
        try:
            chosen = ask_settings(project, self.prefs.get("render_settings"),
                                  parent=self.root, title="Motion Link - render settings")
        except ExportError as e:
            self.say(str(e), True)
            return None
        if chosen:
            self.prefs["render_settings"] = chosen
            save_prefs(self.prefs)
            self.render_label.config(text=describe_settings(chosen))
        return chosen

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
        self.root.minsize(S(600), S(400))
        self._style()
        self._build()
        self.refresh()
        try:
            self.root.attributes("-topmost", bool(self.prefs.get("always_on_top", False)))
        except Exception:
            pass

    def browse(self):
        # tkinter's own picker rather than fusion.RequestDir: this runs inside a
        # Tk mainloop, and Fusion's modal does not always give focus back.
        start = self.dest_var.get().strip() or self._remembered_dest(self._dest_project or "")
        chosen = filedialog.askdirectory(
            parent=self.root, title="Where should the audio and job file go?",
            initialdir=start if os.path.isdir(start) else None)
        if chosen:
            self.dest_var.set(os.path.normpath(chosen))

    # -- scanning ---------------------------------------------------------
    def refresh(self):
        try:
            self._refresh()
        except Exception as e:
            traceback.print_exc()
            self.say(f"Could not read the timeline: {e}", True)

    def _refresh(self):
        timeline = current_timeline()
        if not timeline:
            self.facts = None
            self.tl_label.config(text="No timeline open")
            self.info.config(text="")
            self.say("Open a timeline to begin.", True)
            return

        self.facts = timeline_facts(timeline)
        self.tl_label.config(text=self.facts["name"])
        length = self.facts["end_frame"] - self.facts["start_frame"]
        self.info.config(text=f"{self.facts['width']} x {self.facts['height']} · "
                              f"{self.facts['fps_string']} fps · {length} frames")


        # The destination follows the project, so opening a different project
        # lands in its own media folder instead of the last one's.
        project = current_project()
        project_name = project.GetName() if project else ""
        if project_name != self._dest_project or not self.dest_var.get().strip():
            self._dest_project = project_name
            self.dest_var.set(self._remembered_dest(project_name))

        self._sync_comp_name()
        self.say("Ready.")

    def export_stem(self):
        """The filename this export will use: <Timeline>_MotionLink_001.

        Recomputed from whatever is already in the destination folder, so the
        Comp name shown in the panel is the name Cavalry will actually get.
        """
        if not self.facts:
            return ""
        base = f"{sanitise(self.facts['name'])}_MotionLink_"
        dest = self.dest_var.get().strip()
        return next_numbered_name(dest, base) if dest else f"{base}001"

    def _sync_comp_name(self):
        """Default the comp name to the export name, until you type your own."""
        if self._name_edited:
            return
        self.comp_var.set(self.export_stem())
        self._name_edited = False       # our own write tripped the trace

    def _remembered_dest(self, project_name):
        """Where this project exported to last, else <media location>\\MotionLink."""
        by_project = self.prefs.get("dest_by_project") or {}
        remembered = by_project.get(project_name)
        if remembered and os.path.isdir(remembered):
            return remembered
        return default_destination()

    def _remember_dest(self):
        dest = self.dest_var.get().strip()
        if not dest:
            return
        by_project = self.prefs.setdefault("dest_by_project", {})
        by_project[self._dest_project or ""] = dest
        save_prefs(self.prefs)

    # -- export -----------------------------------------------------------
    def do_export(self):
        if self.busy:
            return
        self.busy = True
        self.export_btn.config(state="disabled")
        try:
            self._export()
        except Exception as e:
            traceback.print_exc()
            self.say(f"Export failed: {e}", True)
        finally:
            self.busy = False
            self.export_btn.config(state="normal")

    def _export(self):
        project = current_project()
        timeline = current_timeline()
        if not project or not timeline:
            self.say("No timeline open.", True)
            return

        dest = self.dest_var.get().strip()
        if not dest:
            self.say("Pick a destination folder first.", True)
            return
        settings = self.prefs.get("render_settings") or self.choose_settings()
        if not settings:
            self.say("Choose the render settings first.", True)
            return

        self.say("Rendering...")
        try:
            sidecar, job = export_range(
                project, timeline, dest, settings,
                whole=self.range_var.get() == RANGE_MODES[1],
                comp_name=self.comp_var.get() if self._name_edited else None)
        except ExportError as e:
            self.say(str(e), True)
            return

        self.facts = timeline_facts(timeline)
        self._remember_dest()
        self._sync_comp_name()
        self.result.config(text=f"{os.path.basename(job['media']['path'])}  ·  "
                                f"{job['comp']['duration_frames']} frames")
        self.say("Done. In Cavalry: Window > Scripts > John > Motion Link Build Comp.")

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            save_prefs(self.prefs)
            self._remember_dest()
        except Exception:
            pass
        self.root.destroy()


def sanitise(name):
    """A filename- and comp-name-safe version of a timeline name."""
    kept = "".join(c for c in str(name or "") if c.isalnum() or c in " _-")
    return kept.strip().replace(" ", "_")


def default_destination():
    """<project media location>\\MotionLink, same convention as PreviewCache.

    The folder is created if it doesn't exist. projectMediaLocation can be
    unset or point somewhere unwritable on this machine, so a failure here
    falls back to the home folder rather than raising.
    """
    project = current_project()
    try:
        media_location = project.GetSetting("projectMediaLocation") if project else None
    except Exception:
        media_location = None

    if media_location:
        target = os.path.join(media_location, "MotionLink")
        try:
            os.makedirs(target, exist_ok=True)
            return target
        except OSError as e:
            print(f"Note: could not use '{target}' ({e}) - pick a folder instead.")

    home = os.path.expanduser("~")
    return home if os.path.isdir(home) else ""


# Run from the Scripts menu it opens the panel. The Motion Link Queue loads
# this file with MOTION_LINK_AS_LIBRARY set, to use the export pipeline above.
if not globals().get("MOTION_LINK_AS_LIBRARY"):
    prefs = load_prefs()
    root = tk.Tk()
    SCALE = resolve_scale(prefs, root)
    app = MotionLink(root)
    root.bind("<Escape>", lambda e: app._on_close())
    root.lift()
    root.attributes("-topmost", True)
    if not prefs.get("always_on_top", False):
        root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()
