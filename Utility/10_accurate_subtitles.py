import os
import sys

# Shared helpers (resolve_utils.py) live outside Resolve's Scripts menu - deploy puts them there.
_LIB = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~/Library/Application Support"),
                    "ResolveUtilities", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
sys.modules.pop("resolve_utils", None)      # Resolve keeps modules loaded between runs
import resolve_utils as ru
ru.bootstrap(globals(), "Utility/10_accurate_subtitles.py")     # Mac: opens the window in its own Python

import bisect
import copy
import hashlib
import math
import json
import queue
import re
import subprocess
import threading
import time
import traceback
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

# Accurate Subtitles - frame-accurate subtitles for one or many timelines.
#
# Tick the timelines, press Start. For each one it:
#   1. renders the timeline's audio exactly as it plays (one render job each)
#   2. converts it to 16 kHz mono WAV (afconvert)
#   3. transcribes it with Whisper large-v3 for the TEXT (+ a turbo pass for
#      punctuation), and cleans up the usual ASR mistakes
#   4. optionally stops so you can correct the transcript
#   5. force-aligns every word against the audio (torchaudio MMS_FA) and snaps
#      starts onto speech onsets - Whisper's own word times run early
#   6. builds the cues by John's rules: one line, 14 characters or fewer, no
#      punctuation, no gaps, cues sit 4 frames ahead of the word
#   7. imports the SRT onto the timeline's subtitle track
#   8. checks every cue landed on its exact frame
#
# Files go to <project>/_Documents/transcripts (found from the media's
# location) unless you pick another folder; a transcript is reused when the
# audio hasn't changed, so a re-run after editing only re-aligns.
#
# Mac only: the transcription uses mlx-whisper (Apple silicon). The machine
# learning packages live in their own venv, ~/.cache/claude-clipcheck/venv;
# the window offers to set it up when it's missing.

PREFS_FILE = ru.data_path("accurate_subtitles_prefs.json")
VENV = os.path.expanduser("~/.cache/claude-clipcheck/venv")
VENV_PY = os.path.join(VENV, "bin", "python")
VENV_PACKAGES = ["mlx-whisper", "torch", "torchaudio", "num2words"]
WORKER_NAME = "subtitle_worker.py"
SUBTITLE_BIN = "Subtitles"
RESTORE_PRESET = "_AccurateSubtitles_restore"
NO_PRESET = "(no preset)"
TREE_TEXT_PAD = 4                         # Treeview cell text inset (clam theme), points
AUDIO_PRESETS = ("Audio Only",)          # Resolve's built-in audio-only render preset
RENDER_START_SECONDS = 30                # not rendering this long after Start = never started
RENDER_FAILED = ("Failed", "Cancelled", "Background Render Cancelled", "Remote Render Cancelled")

DEFAULT_JARGON = ("COMLEX, USMLE, Anki, OMM, OSCE, PBL, EM, ortho, LECOM, TrueLearn, "
                  "Dirty Med, Sketchy, Pharm")
DEFAULT_SAY_AS_WORD = "COMLEX, COVID, OSCE, NASA, FAFSA"
LANGUAGES = [("English", "en"), ("Auto-detect", ""), ("Spanish", "es"), ("French", "fr"),
             ("German", "de"), ("Portuguese", "pt"), ("Italian", "it"), ("Chinese", "zh"),
             ("Japanese", "ja"), ("Korean", "ko")]

STEPS = ["Render audio", "Convert", "Transcribe", "Review", "Align", "Import", "Verify"]
# Share of one timeline's work each step stands for, for the overall bar.
WEIGHTS = {"Render audio": 0.12, "Convert": 0.03, "Transcribe": 0.5, "Review": 0.0,
           "Align": 0.25, "Import": 0.07, "Verify": 0.03}

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
GOOD = "#6cc070"
WARN = "#e0b050"
BAD = "#e07070"

SCALE = 1.0


def S(n):
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    return ru.font(size, weight, SCALE)


def MONO(size):
    return ru.font(size, None, SCALE, mono=True)


def make_button(parent, text, command, primary=False):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = ru.Button(parent, text=text, command=command, bg=base,
                    fg="#ffffff" if primary else FG, relief="flat", bd=0,
                    padx=S(12), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover) if str(btn.cget("state")) != "disabled" else None)
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


def entry(parent, var=None, width=None, font=None):
    kw = {"width": width} if width else {}
    return tk.Entry(parent, textvariable=var, bg=PANEL, fg=FG, relief="flat",
                    insertbackground=FG, font=font or FONT(9), highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT, **kw)


def text_box(parent, height, font=None):
    return tk.Text(parent, height=height, bg=PANEL, fg=FG, relief="flat", wrap="word",
                   insertbackground=FG, font=font or FONT(9), padx=S(6), pady=S(4),
                   highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                   undo=True)


def check(parent, text, var):
    return tk.Checkbutton(parent, text=text, variable=var, bg=BG, fg=FG, selectcolor=PANEL,
                          activebackground=BG, activeforeground=FG, font=FONT(9),
                          highlightthickness=0, anchor="w", justify="left",
                          wraplength=S(320))


def sanitize(name):
    return re.sub(r"[^\w\-. ]+", "_", name).strip(" .") or "timeline"


def fps_fraction(timeline):
    """The timeline rate as an exact (numerator, denominator)."""
    try:
        rate = float(str(timeline.GetSetting("timelineFrameRate")).split()[0])
    except Exception:
        rate = 24.0
    for nominal in (24, 30, 48, 60, 120):
        if abs(rate - nominal * 1000 / 1001) < 0.01:
            return nominal * 1000, 1001
    if abs(rate - round(rate)) < 0.001:
        return int(round(rate)), 1
    return int(round(rate * 1000)), 1000


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def wav_seconds(path):
    import wave
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


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
    return "%02d:%02d:%02d%s%02d" % ((frame // (rate * 3600)) % 24, (frame // (rate * 60)) % 60,
                                    (frame // rate) % 60, sep, frame % rate)


def srt_time(frame, num, den):
    # Rounded UP to the millisecond, so Resolve reads it back as the same frame
    # whether it rounds or truncates.
    ms = max(0, math.ceil(frame * den * 1000.0 / num - 1e-6))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def is_drop_frame(timeline):
    return str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"


class Stopped(Exception):
    pass


class StepError(Exception):
    pass


# --------------------------------------------------------------------------
# Resolve side
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


def subtitle_items(tl):
    items = []
    for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
        items += tl.GetItemListInTrack("subtitle", i) or []
    return items


def content_end(tl):
    """Last frame anything plays (exclusive) - GetEndFrame() can be stale."""
    end = None
    for kind in ("video", "audio"):
        for i in range(1, (tl.GetTrackCount(kind) or 0) + 1):
            for item in tl.GetItemListInTrack(kind, i) or []:
                e = item.GetEnd()
                end = e if end is None else max(end, e)
    return int(end) if end is not None else int(tl.GetEndFrame())


def timelines_with_bins(project):
    """[(timeline, bin path)] for every timeline, in media pool order."""
    by_name = {}
    for i in range(1, (project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            by_name.setdefault(tl.GetName(), []).append(tl)
    out, seen = [], set()

    def walk(folder, path):
        for clip in folder.GetClipList() or []:
            try:
                if clip.GetClipProperty("Type") != "Timeline":
                    continue
            except Exception:
                continue
            for tl in by_name.get(clip.GetName(), []):
                uid = tl.GetUniqueId()
                if uid not in seen:
                    seen.add(uid)
                    out.append((tl, path))
                    break
        for sub in folder.GetSubFolderList() or []:
            walk(sub, f"{path}/{sub.GetName()}" if path else sub.GetName())

    root = project.GetMediaPool().GetRootFolder()
    walk(root, "")
    for tls in by_name.values():                     # anything the walk missed
        for tl in tls:
            if tl.GetUniqueId() not in seen:
                seen.add(tl.GetUniqueId())
                out.append((tl, "?"))
    return out


def guess_work_dir(project, timelines):
    """<project folder>/_Documents/transcripts, found from where the media
    lives; else a folder in the prefs folder."""
    checked = 0
    for tl in timelines:
        for kind in ("audio", "video"):
            for i in range(1, (tl.GetTrackCount(kind) or 0) + 1):
                for item in (tl.GetItemListInTrack(kind, i) or [])[:15]:
                    try:
                        mpi = item.GetMediaPoolItem()
                        path = mpi.GetClipProperty("File Path") if mpi else ""
                    except Exception:
                        path = ""
                    if not path:
                        continue
                    checked += 1
                    d = os.path.dirname(path)
                    for _ in range(8):
                        if os.path.isdir(os.path.join(d, "_Documents")):
                            return os.path.join(d, "_Documents", "transcripts")
                        parent = os.path.dirname(d)
                        if parent == d:
                            break
                        d = parent
                    if checked > 40:
                        break
    return ru.data_path("subtitles", sanitize(project.GetName()))


def subtitle_bin(media_pool):
    root = media_pool.GetRootFolder()
    for folder in root.GetSubFolderList() or []:
        if folder.GetName() == SUBTITLE_BIN:
            return folder
    return media_pool.AddSubFolder(root, SUBTITLE_BIN)


# --------------------------------------------------------------------------
# window
# --------------------------------------------------------------------------

class AccurateSubtitles:
    def __init__(self, root):
        self.root = root
        self.prefs = ru.read_json(PREFS_FILE, {})
        self.rows = {}               # tree iid -> row dict
        self.running = False
        self.stop_requested = False
        self.proc = None
        self.work_dir = None

        root.title("Accurate Subtitles")
        root.configure(bg=BG)
        self._style()
        self._build()
        root.update_idletasks()
        need = root.winfo_reqheight()
        root.minsize(S(900), need)
        geo = self.prefs.get("geometry")
        if geo and abs(float(self.prefs.get("geometry_scale", 1.0)) - SCALE) < 0.01:
            try:
                root.geometry(geo)
            except Exception:
                pass
        else:
            root.geometry(f"{S(1060)}x{max(need, S(820))}")
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- project ----------------------------------------------------------
    def project(self):
        try:
            return projectManager.GetCurrentProject()
        except Exception:
            return None

    def project_prefs(self):
        project = self.project()
        key = project.GetName() if project else ""
        return self.prefs.setdefault("projects", {}).setdefault(key, {})

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
        style.map("Treeview", background=[("selected", PANEL2)],
                  foreground=[("selected", FG)])
        style.configure("Edit.Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(24), font=FONT(9))
        style.configure("Edit.Treeview.Heading", background=PANEL2, foreground=SUB,
                        relief="flat", font=FONT(8, "bold"))
        style.map("Edit.Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground=PANEL, background=BTN,
                        foreground=FG, arrowcolor=SUB, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL, selectbackground=PANEL,
                        selectforeground=FG, arrowsize=S(14), padding=S(3))
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", FG)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", FG)],
                  background=[("active", BTN_HOVER), ("readonly", BTN)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        for name, colour in (("Step", ACCENT), ("Overall", GOOD)):
            style.configure(f"{name}.Horizontal.TProgressbar", troughcolor=PANEL2,
                            background=colour, bordercolor=BORDER, lightcolor=colour,
                            darkcolor=colour, thickness=S(10))
        style.configure("Dark.TNotebook", background=BG, borderwidth=0, tabmargins=0)
        style.configure("Dark.TNotebook.Tab", background=PANEL2, foreground=SUB,
                        padding=(S(10), S(5)), font=FONT(9, "bold"), borderwidth=0)
        style.map("Dark.TNotebook.Tab", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=BTN, troughcolor=PANEL,
                        bordercolor=PANEL, arrowcolor=SUB, lightcolor=BTN, darkcolor=BTN)

    def _label(self, parent, text, sub=False, size=9, weight=None, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", BG), fg=SUB if sub else FG,
                        font=FONT(size, weight), anchor="w", justify="left", **kw)

    def _build(self):
        p = self.project_prefs()
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=S(14), pady=S(12))

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        self._label(head, "Accurate Subtitles", size=13, weight="bold").pack(side="left")
        self.project_label = self._label(head, "", sub=True)
        self.project_label.pack(side="left", padx=S(10))

        # Two tabs: Transcribe (make subtitles) and Subtitle Edit (fix them).
        self.nb = ttk.Notebook(outer, style="Dark.TNotebook")
        self.nb.pack(fill="both", expand=True, pady=(S(8), 0))
        tab = tk.Frame(self.nb, bg=BG)
        edit_tab = tk.Frame(self.nb, bg=BG)
        self.nb.add(tab, text="  Transcribe  ")
        self.nb.add(edit_tab, text="  Subtitle Edit  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.ed_tab_shown())

        body = tk.Frame(tab, bg=BG)

        # timelines ---------------------------------------------------------
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self._label(left, "Timelines - click to tick the ones to subtitle", sub=True).pack(fill="x")
        tree_wrap = tk.Frame(left, bg=BORDER, padx=1, pady=1)
        tree_wrap.pack(fill="both", expand=True, pady=(S(4), S(6)))
        cols = ("tick", "name", "bin", "subs", "status")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="none")
        for col, title, width, stretch in (("tick", "", 34, False), ("name", "Timeline", 220, True),
                                           ("bin", "Bin", 130, True), ("subs", "Subs", 50, False),
                                           ("status", "Status", 220, True)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=S(width), stretch=stretch,
                             anchor="center" if col in ("tick", "subs") else "w")
        self.tree.tag_configure("ticked", background="#2b3a4f")
        self.tree.tag_configure("done", foreground=GOOD)
        self.tree.tag_configure("failed", foreground=BAD)
        self.tree.tag_configure("warn", foreground=WARN)
        self.tree.tag_configure("active", foreground="#ffffff")
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)

        pick = tk.Frame(left, bg=BG)
        pick.pack(fill="x")
        for text, cmd in (("All", lambda: self.tick_all(True)), ("None", lambda: self.tick_all(False)),
                          ("Current", self.tick_current), ("Selected bin", self.tick_bin),
                          ("Refresh", self.refresh)):
            make_button(pick, text, cmd).pack(side="left", padx=(0, S(6)))
        self.count_label = self._label(pick, "", sub=True)
        self.count_label.pack(side="right")

        # settings ----------------------------------------------------------
        right = tk.Frame(body, bg=BG)
        right.pack(side="right", fill="y", padx=(S(14), 0))
        tk.Frame(right, bg=BG, width=S(340), height=1).pack()      # the column's width

        def row_label(text, top=S(8)):
            self._label(right, text, sub=True, wraplength=S(340)).pack(fill="x", pady=(top, S(2)))

        row_label("Client preset", 0)
        pr = tk.Frame(right, bg=BG)
        pr.pack(fill="x")
        self.preset_var = tk.StringVar(value=p.get("preset", NO_PRESET))
        self.preset_combo = ttk.Combobox(pr, textvariable=self.preset_var, state="readonly",
                                         font=FONT(9))
        self.preset_combo.pack(side="left", fill="x", expand=True)
        self.preset_combo.bind("<<ComboboxSelected>>", lambda e: self.load_preset())
        make_button(pr, "Save", self.save_preset).pack(side="left", padx=(S(6), 0))
        make_button(pr, "Delete", self.delete_preset).pack(side="left", padx=(S(4), 0))
        self._fill_presets()

        row_label("Language")
        self.lang_var = tk.StringVar(value=self.prefs.get("language", "English"))
        ttk.Combobox(right, textvariable=self.lang_var, state="readonly",
                     values=[n for n, _ in LANGUAGES], font=FONT(9)).pack(fill="x")

        nums = tk.Frame(right, bg=BG)
        nums.pack(fill="x", pady=(S(8), 0))
        self.max_var = tk.StringVar(value=str(self.prefs.get("max_chars", 14)))
        self.lead_var = tk.StringVar(value=f"{float(self.prefs.get('lead_frames', 4)):g}")
        self._label(nums, "Max characters per line", sub=True).pack(side="left")
        entry(nums, self.max_var, 3).pack(side="left", padx=(S(6), S(12)), ipady=S(2))
        self._label(nums, "Lead frames", sub=True).pack(side="left")
        entry(nums, self.lead_var, 3).pack(side="left", padx=(S(6), 0), ipady=S(2))

        row_label("Names and jargon (helps Whisper spell them)")
        self.jargon = text_box(right, 2)
        self.jargon.insert("1.0", p.get("jargon", DEFAULT_JARGON))
        self.jargon.pack(fill="x")

        row_label("Corrections, one per line:  wrong => right")
        self.repl = text_box(right, 3, MONO(9))
        self.repl.insert("1.0", p.get("replacements", ""))
        self.repl.pack(fill="x")

        row_label("Acronyms said as words (others are spelled out)")
        self.say_var = tk.StringVar(value=self.prefs.get("say_as_word", DEFAULT_SAY_AS_WORD))
        entry(right, self.say_var).pack(fill="x", ipady=S(3))

        opts = tk.Frame(right, bg=BG)
        opts.pack(fill="x", pady=(S(8), 0))
        self.turbo_var = tk.BooleanVar(value=self.prefs.get("turbo", True))
        self.review_var = tk.BooleanVar(value=self.prefs.get("review", False))
        self.back_var = tk.BooleanVar(value=self.prefs.get("drop_backchannels", True))
        self.replace_var = tk.BooleanVar(value=self.prefs.get("replace_existing", False))
        self.punct_var = tk.BooleanVar(value=self.prefs.get("keep_punctuation", False))
        for text, var in (("Keep punctuation in the subtitles", self.punct_var),
                          ("Punctuation pass (Whisper turbo)", self.turbo_var),
                          ("Stop to review each transcript before aligning", self.review_var),
                          ("Drop backchannels said over the speaker", self.back_var),
                          ("Replace existing subtitles (else skip those timelines)", self.replace_var)):
            check(opts, text, var).pack(fill="x", anchor="w")

        row_label("Work folder (blank = <project>/_Documents/transcripts)")
        wd = tk.Frame(right, bg=BG)
        wd.pack(fill="x")
        self.dir_var = tk.StringVar(value=p.get("work_dir", ""))
        entry(wd, self.dir_var).pack(side="left", fill="x", expand=True, ipady=S(3))
        make_button(wd, "...", self.pick_dir).pack(side="left", padx=(S(6), 0))

        # progress ----------------------------------------------------------
        prog = tk.Frame(tab, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        inner = tk.Frame(prog, bg=PANEL)
        inner.pack(fill="x", padx=S(10), pady=S(8))

        strip = tk.Frame(inner, bg=PANEL)
        strip.pack(fill="x")
        self.step_labels = {}
        for i, step in enumerate(STEPS):
            lbl = tk.Label(strip, text=f"{i + 1}  {step}", bg=PANEL2, fg=SUB, font=FONT(8),
                           padx=S(8), pady=S(3))
            lbl.pack(side="left", padx=(0, S(4)))
            self.step_labels[step] = lbl

        self.step_text = self._label(inner, "Ready.", size=10, weight="bold", bg=PANEL)
        self.step_text.pack(fill="x", pady=(S(8), S(3)))
        self.step_bar = ttk.Progressbar(inner, style="Step.Horizontal.TProgressbar",
                                        maximum=1000, mode="determinate")
        self.step_bar.pack(fill="x")
        self.overall_text = self._label(inner, "", sub=True, bg=PANEL)
        self.overall_text.pack(fill="x", pady=(S(6), S(3)))
        self.overall_bar = ttk.Progressbar(inner, style="Overall.Horizontal.TProgressbar",
                                           maximum=1000, mode="determinate")
        self.overall_bar.pack(fill="x")

        logwrap = tk.Frame(tab, bg=BORDER, padx=1, pady=1)
        self.log_box = tk.Text(logwrap, height=7, bg=PANEL, fg=FG, relief="flat", wrap="word",
                               font=MONO(8), padx=S(6), pady=S(4), highlightthickness=0,
                               state="disabled")
        lsb = ttk.Scrollbar(logwrap, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=lsb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.log_box.tag_configure("head", foreground="#ffffff", font=MONO(8))
        self.log_box.tag_configure("good", foreground=GOOD)
        self.log_box.tag_configure("warn", foreground=WARN)
        self.log_box.tag_configure("bad", foreground=BAD)
        self.log_box.tag_configure("sub", foreground=SUB)

        foot = tk.Frame(tab, bg=BG)
        self.start_btn = make_button(foot, "Start", self.start, primary=True)
        self.start_btn.pack(side="left")
        self.stop_btn = make_button(foot, "Stop", self.stop)
        self.stop_btn.pack(side="left", padx=(S(6), 0))
        make_button(foot, "Open Work Folder", self.open_folder).pack(side="left", padx=(S(6), 0))
        make_button(foot, "Close", self._on_close).pack(side="right")

        # Packed bottom-up so a small window squeezes the timeline list, never
        # the progress area or the buttons.
        foot.pack(side="bottom", fill="x", pady=(S(10), 0))
        logwrap.pack(side="bottom", fill="x", pady=(S(8), 0))
        prog.pack(side="bottom", fill="x", pady=(S(12), 0))
        body.pack(fill="both", expand=True, pady=(S(10), 0))

        self._build_editor(edit_tab)

    # -- client presets ---------------------------------------------------
    # A preset is everything about how a client's subtitles look and read:
    # names and jargon, corrections, acronyms, language, line length, lead,
    # punctuation. Run options (review, replace, work folder) aren't in it.
    def presets(self):
        return self.prefs.setdefault("presets", {})

    def _fill_presets(self):
        self.preset_combo["values"] = [NO_PRESET] + sorted(self.presets(), key=str.lower)
        if self.preset_var.get() not in self.presets():
            self.preset_var.set(NO_PRESET)

    def _preset_values(self):
        return {"jargon": self.jargon.get("1.0", "end").strip(),
                "replacements": self.repl.get("1.0", "end").strip(),
                "say_as_word": self.say_var.get().strip(),
                "language": self.lang_var.get(),
                "max_chars": self.max_var.get().strip(),
                "lead_frames": self.lead_var.get().strip(),
                "keep_punctuation": self.punct_var.get(),
                "turbo": self.turbo_var.get(),
                "drop_backchannels": self.back_var.get()}

    def load_preset(self):
        values = self.presets().get(self.preset_var.get())
        if not values:
            return
        for box, key in ((self.jargon, "jargon"), (self.repl, "replacements")):
            if key in values:
                box.delete("1.0", "end")
                box.insert("1.0", values[key])
        for var, key in ((self.say_var, "say_as_word"), (self.lang_var, "language"),
                         (self.max_var, "max_chars"), (self.lead_var, "lead_frames"),
                         (self.punct_var, "keep_punctuation"), (self.turbo_var, "turbo"),
                         (self.back_var, "drop_backchannels")):
            if key in values:
                var.set(values[key])
        self.project_prefs()["preset"] = self.preset_var.get()
        ru.write_json(PREFS_FILE, self.prefs)

    def save_preset(self):
        current = self.preset_var.get()
        name = self.ask_name("Save client preset",
                             "Preset name (the same name updates it):",
                             "" if current == NO_PRESET else current)
        if not name:
            return
        if name in self.presets() and name != current and not messagebox.askyesno(
                "Accurate Subtitles", f"Replace the '{name}' preset?", **ru.dialog_parent(self.root)):
            return
        self.presets()[name] = self._preset_values()
        self.preset_var.set(name)
        self.project_prefs()["preset"] = name
        self._fill_presets()
        ru.write_json(PREFS_FILE, self.prefs)

    def delete_preset(self):
        name = self.preset_var.get()
        if name not in self.presets():
            return
        if not messagebox.askyesno("Accurate Subtitles", f"Delete the '{name}' preset?\n\n"
                                   "The settings on screen stay as they are.",
                                   **ru.dialog_parent(self.root)):
            return
        del self.presets()[name]
        self.preset_var.set(NO_PRESET)
        self.project_prefs()["preset"] = NO_PRESET
        self._fill_presets()
        ru.write_json(PREFS_FILE, self.prefs)

    def ask_name(self, title, prompt, initial=""):
        """A small dark name prompt. Returns the name, or None."""
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        self._label(top, prompt).pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        var = tk.StringVar(value=initial)
        box = entry(top, var, 34)
        box.pack(fill="x", padx=S(14), ipady=S(3))
        result = {"name": None}

        def done(ok):
            name = " ".join(var.get().split())
            if ok and (not name or name == NO_PRESET):
                return
            result["name"] = name if ok else None
            top.destroy()

        foot = tk.Frame(top, bg=BG)
        foot.pack(fill="x", padx=S(14), pady=S(12))
        make_button(foot, "Save", lambda: done(True), primary=True).pack(side="right")
        make_button(foot, "Cancel", lambda: done(False)).pack(side="right", padx=(0, S(6)))
        top.bind("<Return>", lambda e: done(True))
        top.bind("<Escape>", lambda e: done(False))
        top.protocol("WM_DELETE_WINDOW", lambda: done(False))
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + S(120)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        box.focus_set()
        box.select_range(0, "end")
        top.grab_set()
        self.root.wait_window(top)
        return result["name"]

    # -- timeline list ----------------------------------------------------
    def refresh(self):
        if self.running:
            return
        project = self.project()
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        if not project:
            self.project_label.config(text="No project open")
            return
        self.project_label.config(text=project.GetName())
        ticked = set(self.project_prefs().get("ticked", []))
        current = project.GetCurrentTimeline()
        current_id = current.GetUniqueId() if current else None
        for tl, bin_path in timelines_with_bins(project):
            uid = tl.GetUniqueId()
            subs = len(subtitle_items(tl))
            row = {"tl": tl, "uid": uid, "name": tl.GetName(), "bin": bin_path or "Master",
                   "subs": subs, "ticked": uid in ticked if ticked else uid == current_id,
                   "status": ""}
            iid = self.tree.insert("", "end")
            self.rows[iid] = row
            self._draw_row(iid)
        self._update_count()

    def _draw_row(self, iid, tag=None):
        row = self.rows[iid]
        tags = (["ticked"] if row["ticked"] else []) + ([tag] if tag else [])
        self.tree.item(iid, values=("☑" if row["ticked"] else "☐", row["name"],
                                    row["bin"], row["subs"] or "", row["status"]),
                       tags=tags)

    def _on_click(self, event):
        if self.running:
            return "break"
        iid = self.tree.identify_row(event.y)
        if iid in self.rows:
            self.rows[iid]["ticked"] = not self.rows[iid]["ticked"]
            self._draw_row(iid)
            self._update_count()
        return "break"

    def tick_all(self, on):
        for iid, row in self.rows.items():
            row["ticked"] = on
            self._draw_row(iid)
        self._update_count()

    def tick_current(self):
        project = self.project()
        current = project.GetCurrentTimeline() if project else None
        uid = current.GetUniqueId() if current else None
        for iid, row in self.rows.items():
            row["ticked"] = row["uid"] == uid
            self._draw_row(iid)
        self._update_count()

    def tick_bin(self):
        """Tick the timelines in the bin selected in the media pool."""
        project = self.project()
        folder = project.GetMediaPool().GetCurrentFolder() if project else None
        names = set()
        for clip in (folder.GetClipList() if folder else None) or []:
            try:
                if clip.GetClipProperty("Type") == "Timeline":
                    names.add(clip.GetName())
            except Exception:
                pass
        for iid, row in self.rows.items():
            row["ticked"] = row["name"] in names
            self._draw_row(iid)
        self._update_count()

    def _update_count(self):
        n = sum(r["ticked"] for r in self.rows.values())
        self.count_label.config(text=f"{n} of {len(self.rows)} ticked")

    def pick_dir(self):
        path = ru.ask_directory(None, self.dir_var.get() or None, "Work folder for subtitles")
        if path:
            self.dir_var.set(str(path))

    def open_folder(self):
        path = self.work_dir or self.dir_var.get()
        if path and os.path.isdir(path):
            ru.open_path(path)

    # -- progress display -------------------------------------------------
    def log(self, text, tag=None):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n", tag or ())
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def pump(self):
        try:
            self.root.update()
        except tk.TclError:
            pass

    def set_step(self, step):
        found = False
        for name, lbl in self.step_labels.items():
            if name == step:
                lbl.config(bg=ACCENT, fg="#ffffff")
                found = True
            elif not found and step is not None:
                lbl.config(bg=PANEL2, fg=GOOD)
            else:
                lbl.config(bg=PANEL2, fg=SUB)

    def show(self, job, step, fraction=0.0, detail=""):
        """Current step + its progress, the row's status, the overall bar."""
        if job not in getattr(self, "jobs", []):            # an edit being applied
            self.ed_say(detail or step)
            self.pump()
            return
        self.current = (job, step)
        job["step"], job["frac"] = step, fraction
        self.set_step(step)
        n = self.jobs.index(job) + 1
        pct = f" {int(fraction * 100)}%" if fraction > 0 else ""
        self.step_text.config(text=f"Step {STEPS.index(step) + 1} of {len(STEPS)} · "
                                   f"{detail or step}{pct}   —   {job['name']}  ({n} of {len(self.jobs)})")
        self.step_bar["value"] = int(fraction * 1000)
        job["row"]["status"] = f"{detail or step}{pct}"
        self._draw_row(job["iid"], "active")
        self._update_overall()
        self.pump()

    def _update_overall(self):
        total = 0.0
        for job in self.jobs:
            if job.get("finished"):
                total += 1.0
                continue
            step = job.get("step")
            if step:
                i = STEPS.index(step)
                total += sum(WEIGHTS[s] for s in STEPS[:i]) + WEIGHTS[step] * job.get("frac", 0)
        frac = total / max(1, len(self.jobs))
        self.overall_bar["value"] = int(frac * 1000)
        done = sum(1 for j in self.jobs if j.get("finished"))
        elapsed = time.time() - self.started
        eta = ""
        if frac > 0.03:
            remaining = elapsed * (1 - frac) / frac
            eta = f"  ·  about {int(remaining // 60)}m {int(remaining % 60):02d}s left"
        self.overall_text.config(text=f"Overall {int(frac * 100)}%  ·  {done} of "
                                      f"{len(self.jobs)} timelines finished  ·  "
                                      f"{int(elapsed // 60)}m {int(elapsed % 60):02d}s{eta}")

    def finish_job(self, job, status, tag):
        job["finished"] = True
        job["row"]["status"] = status
        self._draw_row(job["iid"], tag)
        self._update_overall()

    def check_stop(self):
        self.pump()
        if self.stop_requested:
            raise Stopped()

    # -- settings ---------------------------------------------------------
    def read_settings(self):
        try:
            lead = float(self.lead_var.get())
            max_chars = int(self.max_var.get())
        except ValueError:
            raise StepError("Lead and Max characters must be numbers.")
        repl = []
        for line in self.repl.get("1.0", "end").splitlines():
            if "=>" in line:
                wrong, right = line.split("=>", 1)
                if wrong.strip():
                    repl.append([wrong.strip(), right.strip()])
        jargon = " ".join(self.jargon.get("1.0", "end").split())
        lang = dict(LANGUAGES).get(self.lang_var.get(), "en")
        settings = {
            "language": lang, "prompt": jargon, "replacements": repl,
            "turbo": self.turbo_var.get(), "drop_backchannels": self.back_var.get(),
            "say_as_word": [w.strip() for w in re.split(r"[,\s]+", self.say_var.get()) if w.strip()],
            "lead_frames": lead, "max_chars": max_chars,
            "keep_punctuation": self.punct_var.get(),
        }
        # save them
        p = self.project_prefs()
        p.update({"jargon": self.jargon.get("1.0", "end").strip(),
                  "replacements": self.repl.get("1.0", "end").strip(),
                  "work_dir": self.dir_var.get().strip(),
                  "preset": self.preset_var.get(),
                  "ticked": [r["uid"] for r in self.rows.values() if r["ticked"]]})
        self.prefs.update({"language": self.lang_var.get(), "say_as_word": self.say_var.get(),
                           "lead_frames": lead, "max_chars": max_chars,
                           "turbo": self.turbo_var.get(), "review": self.review_var.get(),
                           "drop_backchannels": self.back_var.get(),
                           "replace_existing": self.replace_var.get(),
                           "keep_punctuation": self.punct_var.get()})
        ru.write_json(PREFS_FILE, self.prefs)
        return settings

    def worker_path(self):
        here = os.path.dirname(os.path.abspath(globals().get("__file__") or "."))
        for path in (os.path.join(ru.LIB_DIR, WORKER_NAME),
                     os.path.join(here, "..", "_shared", WORKER_NAME)):
            if os.path.isfile(path):
                return os.path.abspath(path)
        raise StepError(f"{WORKER_NAME} is missing from {ru.LIB_DIR} - run deploy.sh.")

    # -- the venv ---------------------------------------------------------
    def venv_ok(self):
        if not os.path.isfile(VENV_PY):
            return False
        r = subprocess.run([VENV_PY, "-c", "import mlx_whisper, torchaudio, num2words"],
                           capture_output=True, env=ru._clean_env())
        return r.returncode == 0

    def setup_venv(self):
        if not messagebox.askyesno(
                "Accurate Subtitles",
                "The transcription packages aren't installed yet.\n\n"
                f"Set them up now in\n{VENV}?\n\n"
                "This downloads mlx-whisper, PyTorch and torchaudio (about 1 GB), and the "
                "first run downloads the Whisper and alignment models (about 5 GB).",
                **ru.dialog_parent(self.root)):
            return False
        self.log("Setting up the transcription venv...", "head")
        self.step_text.config(text="Setting up the transcription packages...")
        cmds = []
        if not os.path.isfile(VENV_PY):
            os.makedirs(os.path.dirname(VENV), exist_ok=True)
            cmds.append([sys.executable, "-m", "venv", VENV])
        cmds.append([VENV_PY, "-m", "pip", "install", "--upgrade"] + VENV_PACKAGES)
        for cmd in cmds:
            code = self._stream(cmd, lambda line: self.log(line, "sub"))
            if code != 0:
                self.log("Setup failed - see above.", "bad")
                return False
        self.log("Setup done.", "good")
        return True

    def _stream(self, cmd, on_line, cwd=None):
        """Run a command, feeding its output lines to on_line; returns the
        exit code. Keeps the window alive and honours Stop."""
        env = ru._clean_env()
        env.update({"PYTORCH_ENABLE_MPS_FALLBACK": "1", "TOKENIZERS_PARALLELISM": "false",
                    "HF_HUB_DISABLE_PROGRESS_BARS": "1", "PYTHONUNBUFFERED": "1"})
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     cwd=cwd, env=env, encoding="utf-8", errors="replace",
                                     start_new_session=True)
        lines = queue.Queue()

        def reader(stream):
            buf = ""
            while True:
                ch = stream.read(1)
                if not ch:
                    break
                if ch in "\r\n":
                    if buf.strip():
                        lines.put(buf)
                    buf = ""
                else:
                    buf += ch
            if buf.strip():
                lines.put(buf)

        thread = threading.Thread(target=reader, args=(self.proc.stdout,), daemon=True)
        thread.start()
        try:
            while True:
                try:
                    while True:
                        on_line(lines.get_nowait())
                except queue.Empty:
                    pass
                if self.proc.poll() is not None and not thread.is_alive() and lines.empty():
                    break
                if self.stop_requested:
                    self.proc.terminate()
                    try:
                        self.proc.wait(5)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                    raise Stopped()
                self.pump()
                time.sleep(0.05)
            return self.proc.returncode
        finally:
            self.proc = None

    def run_worker(self, job, command, payload, step, label):
        path = os.path.join(job["dirs"]["words"], f"{job['id']}_{command}_job.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        result, tail = {}, []

        def on_line(line):
            nonlocal result
            if line.startswith("@@P "):
                parts = line.split(" ", 2)
                try:
                    frac = float(parts[1])
                except ValueError:
                    frac = 0.0
                self.show(job, step, frac, parts[2] if len(parts) > 2 else label)
            elif line.startswith("@@R "):
                result = json.loads(line[4:])
            elif not line.startswith("Fetching ") and "it/s]" not in line:
                tail.append(line)
                del tail[:-15]
                self.log("    " + line, "sub")

        self.show(job, step, 0.0, label)
        code = self._stream([VENV_PY, "-W", "ignore", self.worker_path(), command, path],
                            on_line, cwd=job["dirs"]["words"])
        if code != 0:
            error = next((l for l in reversed(tail) if l.strip() and not l.startswith(" ")), "")
            raise StepError(f"{label} failed: {error or 'exit code %s' % code}")
        return result

    # -- run --------------------------------------------------------------
    def start(self):
        if self.running:
            return
        if not ru.IS_MAC:
            messagebox.showerror("Accurate Subtitles", "This tool needs a Mac: the transcription "
                                 "runs on mlx-whisper (Apple silicon).", **ru.dialog_parent(self.root))
            return
        project = self.project()
        if not project:
            return
        picked = [(iid, row) for iid, row in self.rows.items() if row["ticked"]]
        if not picked:
            messagebox.showinfo("Accurate Subtitles", "Tick at least one timeline.",
                                **ru.dialog_parent(self.root))
            return
        try:
            settings = self.read_settings()
            self.worker_path()
        except StepError as e:
            messagebox.showerror("Accurate Subtitles", str(e), **ru.dialog_parent(self.root))
            return

        self.running, self.stop_requested = True, False
        self.jobs = []
        self.start_btn.config(state="disabled")
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        try:
            if not self.venv_ok() and not self.setup_venv():
                return
            self._run(project, picked, settings)
        except Stopped:
            self.log("Stopped.", "warn")
            self.step_text.config(text="Stopped.")
        except Exception as e:
            self.log(traceback.format_exc(), "bad")
            self.step_text.config(text=f"Error: {e}")
        finally:
            self.running = False
            self.start_btn.config(state="normal")
            self.set_step(None)
            for job in getattr(self, "jobs", []):
                if not job.get("finished"):
                    self.finish_job(job, "Stopped" if self.stop_requested else
                                    job["row"]["status"] or "Not done", "warn")

    def _run(self, project, picked, settings):
        self.started = time.time()
        tls = [row["tl"] for _, row in picked]
        work = self.dir_var.get().strip() or guess_work_dir(project, tls)
        self.work_dir = work
        dirs = {k: os.path.join(work, k) for k in ("renders", "wav", "words", "subtitles")}
        for d in dirs.values():
            os.makedirs(d, exist_ok=True)
        self.log(f"Work folder: {work}", "sub")
        print(f"Accurate Subtitles: {len(picked)} timeline(s) -> {work}")

        self.jobs, used = [], set()
        for iid, row in picked:
            base = sanitize(row["name"])
            jid, n = base, 2
            while jid.lower() in used:
                jid, n = f"{base}_{n}", n + 1
            used.add(jid.lower())
            self.jobs.append({"iid": iid, "row": row, "tl": row["tl"], "name": row["name"],
                              "id": jid, "dirs": dirs})
            row["status"] = "Queued"
            self._draw_row(iid)

        todo = []
        for job in self.jobs:
            existing = len(subtitle_items(job["tl"]))
            if existing and not self.replace_var.get():
                self.finish_job(job, f"Skipped - already has {existing} subtitles", "warn")
                self.log(f"{job['name']}: skipped, it already has subtitles "
                         f"(tick 'Replace existing subtitles' to redo it).", "warn")
            else:
                todo.append(job)
        if not todo:
            self.step_text.config(text="Nothing to do.")
            return

        self.render_audio(project, todo)
        for job in todo:
            if job.get("finished"):
                continue
            self.log(f"\n▶ {job['name']}", "head")
            try:
                self.process(project, job, settings)
            except StepError as e:
                self.log(f"  ✗ {e}", "bad")
                self.finish_job(job, f"Failed: {e}", "failed")
                print(f"  {job['name']}: FAILED - {e}")

        good = [j for j in self.jobs if j.get("cues")]
        total = sum(j["cues"] for j in good)
        self.set_step(None)
        self.step_bar["value"] = 1000
        self.step_text.config(text=f"Finished: {len(good)} of {len(self.jobs)} timelines "
                                   f"subtitled, {total} cues.")
        self._update_overall()
        self.log(f"\nFinished: {len(good)} of {len(self.jobs)} timelines, {total} cues. "
                 f"Reports are in {dirs['subtitles']}", "head")
        print(f"Accurate Subtitles: finished, {len(good)} timeline(s), {total} cues.")
        self.refresh_counts()

    def refresh_counts(self):
        for iid, row in self.rows.items():
            try:
                row["subs"] = len(subtitle_items(row["tl"]))
            except Exception:
                pass
            tag = "done" if row["status"].startswith("Done") else (
                "failed" if row["status"].startswith("Failed") else None)
            self._draw_row(iid, tag)

    # -- step 1: render -----------------------------------------------------
    # Every render starts by LOADING A PRESET - Resolve's own "Audio Only" -
    # and only then sets where it goes and which frames. Switching the format
    # to QuickTime and turning video off on top of whatever the Deliver page
    # had loaded made the first render pass fail now and then (FxLink and
    # Motion Link went through the same thing). A job that still fails gets
    # one more pass before it counts as failed.
    def render_audio(self, project, jobs):
        """One audio-only render job per timeline, all rendered together."""
        original = project.GetCurrentTimeline()
        try:
            mode = project.GetCurrentRenderMode()
        except Exception:
            mode = None
        try:
            names = list(project.GetRenderPresetList() or [])
        except Exception:
            presets = project.GetRenderPresets() or {}
            names = list(presets.values()) if isinstance(presets, dict) else list(presets)
        if RESTORE_PRESET in names:
            project.DeleteRenderPreset(RESTORE_PRESET)
        saved = project.SaveAsNewRenderPreset(RESTORE_PRESET)
        preset = next((n for want in AUDIO_PRESETS for n in names
                       if n.lower() == want.lower()), None)
        if preset:
            self.log(f"Rendering the audio with the '{preset}' preset", "sub")
        else:
            self.log("No 'Audio Only' render preset in this project - setting the format by hand",
                     "warn")
        try:
            try:
                project.SetCurrentRenderMode(1)            # single clip
            except Exception:
                pass
            pending = list(jobs)
            for attempt in (1, 2):
                failed = self._render_pass(project, pending, preset)
                if not failed:
                    break
                for job, reason in failed:
                    if attempt == 1:
                        self.log(f"{job['name']}: the audio render failed ({reason}) - "
                                 f"trying once more", "warn")
                    else:
                        self.finish_job(job, f"Failed: render {reason}", "failed")
                        self.log(f"{job['name']}: the audio render failed again ({reason}).", "bad")
                pending = [job for job, _ in failed]
        finally:
            if saved:
                project.LoadRenderPreset(RESTORE_PRESET)
                project.DeleteRenderPreset(RESTORE_PRESET)
            if mode is not None:
                try:
                    project.SetCurrentRenderMode(mode)
                except Exception:
                    pass
            if original:
                project.SetCurrentTimeline(original)

    @staticmethod
    def _rendered_file(folder, stem):
        """What the render wrote for this name, whatever its extension."""
        try:
            hits = [n for n in os.listdir(folder) if os.path.splitext(n)[0] == stem]
            if not hits:
                hits = [n for n in os.listdir(folder) if n.startswith(stem)]
        except OSError:
            return None
        hits = [os.path.join(folder, n) for n in hits]
        return max(hits, key=os.path.getmtime) if hits else None

    def _render_pass(self, project, jobs, preset):
        """Queue and render these jobs. Returns [(job, reason)] that failed."""
        job_ids, failed = {}, []
        try:
            for job in jobs:
                self.show(job, "Render audio", 0.0, "Queuing the audio render")
                tl = job["tl"]
                if not project.SetCurrentTimeline(tl):
                    failed.append((job, "couldn't open the timeline"))
                    continue
                start, end = int(tl.GetStartFrame()), content_end(tl)
                job["start"], job["fps"] = start, fps_fraction(tl)
                job["expected_sec"] = (end - start) * job["fps"][1] / job["fps"][0]
                folder = job["dirs"]["renders"]
                for name in os.listdir(folder):
                    if os.path.splitext(name)[0] == job["id"]:
                        os.remove(os.path.join(folder, name))
                settings = {"SelectAllFrames": False, "MarkIn": start, "MarkOut": end - 1,
                            "TargetDir": folder, "CustomName": job["id"], "ExportAudio": True}
                if preset:
                    if not project.LoadRenderPreset(preset):
                        failed.append((job, f"couldn't load the '{preset}' preset"))
                        continue
                else:
                    project.SetCurrentRenderFormatAndCodec("mov", "H264")
                    settings.update({"ExportVideo": False, "ExportSubtitle": False,
                                     "AudioSampleRate": 48000, "AudioBitDepth": 16})
                project.SetRenderSettings(settings)
                jid = project.AddRenderJob()
                if not jid:
                    failed.append((job, "couldn't add the render job"))
                    continue
                job_ids[jid] = job
            if not job_ids:
                return failed
            ids = list(job_ids)
            if project.StartRendering(ids) is False and project.StartRendering(*ids) is False:
                return failed + [(job, "Resolve wouldn't start it") for job in job_ids.values()]

            started = time.time()
            states = {}
            while True:
                if self.stop_requested:
                    project.StopRendering()
                    raise Stopped()
                for jid, job in job_ids.items():
                    st = project.GetRenderJobStatus(jid) or {}
                    states[jid] = st
                    state = st.get("JobStatus", "")
                    pct = 1.0 if state == "Complete" else (st.get("CompletionPercentage") or 0) / 100.0
                    label = "Rendering audio" if state == "Rendering" else \
                        ("Rendered" if state == "Complete" else f"Render: {state or 'waiting'}")
                    self.show(job, "Render audio", pct, label)
                if all(s.get("JobStatus") in ("Complete",) + RENDER_FAILED for s in states.values()):
                    break
                if time.time() - started > RENDER_START_SECONDS and not project.IsRenderingInProgress() \
                        and not any(s.get("JobStatus") == "Rendering" for s in states.values()):
                    project.StopRendering()
                    break                       # never started - the unfinished ones fail below
                for _ in range(10):
                    self.check_stop()
                    time.sleep(0.05)
            for jid, job in job_ids.items():
                st = states.get(jid) or {}
                path = self._rendered_file(job["dirs"]["renders"], job["id"])
                if st.get("JobStatus") == "Complete" and path:
                    job["audio"] = path
                else:
                    state = st.get("JobStatus") or "unknown"
                    reason = st.get("Error") or ("no file written" if state == "Complete" else
                                                 f"status '{state}'")
                    failed.append((job, reason))
            return failed
        finally:
            for jid in job_ids:
                try:
                    project.DeleteRenderJob(jid)
                except Exception:
                    pass

    # -- steps 2-7 per timeline ---------------------------------------------
    def process(self, project, job, settings):
        d, jid = job["dirs"], job["id"]

        # 2 convert
        self.show(job, "Convert", 0.2, "Converting to 16 kHz WAV")
        wav = os.path.join(d["wav"], jid + ".wav")
        r = subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                            job["audio"], wav], capture_output=True)
        if r.returncode != 0 or not os.path.isfile(wav):
            raise StepError("afconvert failed: " + r.stderr.decode("utf-8", "replace").strip())
        secs = wav_seconds(wav)
        if abs(secs - job["expected_sec"]) > 0.25:
            self.log(f"  ! The WAV is {secs:.2f}s but the timeline is {job['expected_sec']:.2f}s", "warn")
        else:
            self.log(f"  Audio rendered: {secs:.1f}s, matches the timeline", "sub")
        self.check_stop()

        # 3 transcribe (reused when the audio and settings haven't changed)
        words_json = os.path.join(d["words"], jid + "_words.json")
        transcript = os.path.join(d["words"], jid + "_transcript.txt")
        key = {k: settings[k] for k in ("language", "prompt", "replacements", "turbo",
                                        "drop_backchannels")}
        key["wav"] = file_sha1(wav)
        cached = ru.read_json(words_json, {}).get("settings")
        if cached == key and os.path.isfile(transcript):
            self.show(job, "Transcribe", 1.0, "Transcript reused (same audio)")
            self.log("  Transcript reused - the audio and settings haven't changed", "sub")
        else:
            res = self.run_worker(job, "transcribe", {
                "wav": wav, "words_json": words_json, "transcript": transcript,
                "language": settings["language"], "prompt": settings["prompt"],
                "turbo": settings["turbo"], "replacements": settings["replacements"],
                "drop_backchannels": settings["drop_backchannels"], "cache_key": key,
            }, "Transcribe", "Transcribing")
            self.log(f"  Transcribed: {res.get('words', 0)} words in {res.get('segments', 0)} "
                     f"segments", "sub")
        self.check_stop()

        # 4 review
        if self.review_var.get():
            self.show(job, "Review", 0.0, "Waiting for your review")
            answer = self.review(job, words_json, transcript)
            if answer == "skip":
                raise StepError("skipped at review")
        self.check_stop()

        # 5 align + cues
        srt = os.path.join(d["subtitles"], jid + "_final.srt")
        cues_json = os.path.join(d["words"], jid + "_cues.json")
        report = os.path.join(d["subtitles"], jid + "_report.txt")
        res = self.run_worker(job, "align", {
            "wav": wav, "words_json": words_json, "transcript": transcript, "srt": srt,
            "cues_json": cues_json, "report": report, "fps": list(job["fps"]),
            "lead_frames": settings["lead_frames"], "max_chars": settings["max_chars"],
            "say_as_word": settings["say_as_word"],
            "keep_punctuation": settings["keep_punctuation"],
        }, "Align", "Aligning")
        cues = ru.read_json(cues_json, {}).get("cues") or []
        if not cues:
            raise StepError("no cues were built")
        try:
            with open(report, encoding="utf-8") as f:
                for line in f.read().splitlines():
                    self.log("  " + line, "warn" if line.startswith("  ") else "sub")
        except OSError:
            pass
        self.check_stop()

        # 6 import, 7 verify
        self.import_srt(project, job, srt, cues)
        problems = self.verify(job, cues)
        projectManager.SaveProject()
        flagged = res.get("long", 0) + res.get("lowconf", 0) + res.get("restored", 0) + res.get("pushed", 0)
        if problems:
            for p in problems[:12]:
                self.log("  ✗ " + p, "bad")
            self.finish_job(job, f"Check: {len(problems)} placement problem(s)", "failed")
        else:
            note = f", {flagged} to check by ear" if flagged else ""
            self.log(f"  ✓ {len(cues)} cues placed on their exact frames, 0 gaps{note}", "good")
            self.finish_job(job, f"Done · {len(cues)} cues{note}", "done")
        job["cues"] = len(cues)
        print(f"  {job['name']}: {len(cues)} cues" + (" - CHECK PLACEMENT" if problems else ""))

    def import_srt(self, project, job, srt, cues):
        self.show(job, "Import", 0.1, "Importing onto the timeline")
        tl = job["tl"]
        mp = project.GetMediaPool()
        project.SetCurrentTimeline(tl)
        old = subtitle_items(tl)
        if old:
            tl.DeleteClips(old)
        if (tl.GetTrackCount("subtitle") or 0) == 0:
            tl.AddTrack("subtitle")
        folder = subtitle_bin(mp)
        name = os.path.basename(srt)
        # This timeline's earlier SRTs (first run, edits, retries) come out of
        # the bin, so it only holds the ones in use.
        own = re.compile(r"^%s_(final|edited)(_\d{6})?\.srt$" % re.escape(job["id"]), re.I)
        stale = [c for c in folder.GetClipList() or [] if c.GetName() == name or own.match(c.GetName())]
        if stale:
            mp.DeleteClips(stale)
        mp.SetCurrentFolder(folder)
        items = mp.ImportMedia([srt]) or []
        if not items:
            # Resolve won't import a path it already has somewhere else.
            alt = srt[:-4] + time.strftime("_%H%M%S") + ".srt"
            with open(srt, "rb") as src, open(alt, "wb") as dst:
                dst.write(src.read())
            items = mp.ImportMedia([alt]) or []
        if not items:
            raise StepError("Resolve wouldn't import the SRT")
        self.show(job, "Import", 0.5, "Placing the subtitles")
        target = job["start"] + cues[0]["f"]
        mp.AppendToTimeline([{"mediaPoolItem": items[0], "recordFrame": target}])
        placed = subtitle_items(tl)
        if not placed:
            raise StepError("the subtitles didn't land on the timeline")
        # AppendToTimeline ignores recordFrame for subtitles and adds them at
        # the END of the timeline - move them back to the first cue's frame.
        first = min(int(round(i.GetStart())) for i in placed)
        if first != target and not tl.MoveItems(placed, {"startFrame": target}):
            raise StepError("couldn't move the subtitles into place")
        self.show(job, "Import", 1.0, "Imported")

    def verify(self, job, cues):
        self.show(job, "Verify", 0.3, "Checking every cue's frame")
        items = sorted(subtitle_items(job["tl"]), key=lambda i: i.GetStart())
        problems = []
        if len(items) != len(cues):
            problems.append(f"{len(items)} subtitles on the timeline but {len(cues)} cues in the "
                            f"SRT - Resolve dropped some")
        start = job["start"]
        for k, (item, cue) in enumerate(zip(items, cues)):
            s, e = int(round(item.GetStart())), int(round(item.GetEnd()))
            if s != start + cue["f"]:
                problems.append(f"cue {k + 1} '{cue['text']}' starts {s - start - cue['f']:+d} frames off")
            if k + 1 < len(items) and e != int(round(items[k + 1].GetStart())):
                problems.append(f"gap after cue {k + 1} '{cue['text']}'")
            try:
                name = (item.GetName() or "").strip()
            except Exception:
                name = cue["text"]
            if name and " ".join(name.split()) != cue["text"]:
                problems.append(f"cue {k + 1} reads '{name}', expected '{cue['text']}'")
        self.show(job, "Verify", 1.0, "Verified")
        return problems

    # -- review window ------------------------------------------------------
    def review(self, job, words_json, transcript):
        """Edit the transcript before aligning. One line per Whisper segment;
        unsure words are highlighted. Returns 'continue' or 'skip'."""
        wj = ru.read_json(words_json, {})
        segs = wj.get("segments") or []
        with open(transcript, encoding="utf-8") as f:
            original = f.read().rstrip("\n").split("\n")
        top = tk.Toplevel(self.root)
        top.title(f"Review transcript - {job['name']}")
        top.configure(bg=BG)
        top.geometry(f"{S(820)}x{S(600)}")
        top.transient(self.root)
        self._label(top, f"{job['name']}: fix any wrong words, then Continue.", size=10,
                    weight="bold").pack(fill="x", padx=S(12), pady=(S(10), 0))
        self._label(top, "Keep one line per segment (empty a line to drop it). Highlighted words "
                         "are ones Whisper was unsure of (blue: restored from the turbo pass). "
                         "Punctuation decides where cues break.",
                    sub=True, wraplength=S(790)).pack(fill="x", padx=S(12), pady=(S(2), S(6)))
        wrap = tk.Frame(top, bg=BORDER, padx=1, pady=1)
        wrap.pack(fill="both", expand=True, padx=S(12))
        box = text_box(wrap, 20, MONO(10))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=sb.set)
        box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        box.insert("1.0", "\n".join(original))
        box.tag_configure("unsure", background="#5a4a1a")
        box.tag_configure("restored", background="#1a4a5a")
        box.edit_reset()

        def line_of(t):
            for i, seg in enumerate(segs):
                if seg["words"] and seg["words"][0]["start"] - 0.05 <= t <= seg["words"][-1]["end"] + 0.05:
                    return i
            return None

        for kind, items in (("unsure", wj.get("lowconf") or []), ("restored", wj.get("restored") or [])):
            for x in items:
                li = line_of(x["time"])
                word = (x.get("word") or x.get("text") or "").strip(" ,.?!")
                if li is None or not word:
                    continue
                pos = box.search(word, f"{li + 1}.0", f"{li + 1}.end", nocase=True)
                if pos:
                    box.tag_add(kind, pos, f"{pos}+{len(word)}c")

        answer = {"value": None}
        status = self._label(top, "", sub=True)

        def finish(value):
            if value == "continue":
                lines = box.get("1.0", "end").rstrip("\n").split("\n")
                if len(lines) != len(original):
                    status.config(text=f"There are {len(lines)} lines - it must stay {len(original)} "
                                       f"(one per segment). Empty a line instead of deleting it.",
                                  fg=BAD)
                    return
                with open(transcript, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            answer["value"] = value
            top.destroy()

        foot = tk.Frame(top, bg=BG)
        foot.pack(fill="x", padx=S(12), pady=S(10))
        make_button(foot, "Continue", lambda: finish("continue"), primary=True).pack(side="left")
        make_button(foot, "Skip This Timeline", lambda: finish("skip")).pack(side="left", padx=(S(6), 0))
        status.pack(in_=foot, side="left", padx=S(10))
        top.protocol("WM_DELETE_WINDOW", lambda: finish("skip"))
        top.lift()
        box.focus_set()
        while answer["value"] is None:
            if self.stop_requested:
                try:
                    top.destroy()
                except tk.TclError:
                    pass
                raise Stopped()
            self.pump()
            time.sleep(0.03)
        return answer["value"]

    # ======================================================================
    # Subtitle Edit tab
    # ======================================================================
    # Edits happen here, not in Resolve - and playback is the timeline's own
    # exported audio, so nothing touches Resolve until "Apply to Resolve": split a cue at a word, merge cues,
    # fix text, delete, nudge a start - then "Apply to Resolve" writes an
    # edited SRT, swaps it in for the old subtitles and checks every frame.
    # There are never gaps: the cue before always ends where the next starts.
    # A cue split at a word starts on that word's aligned onset (minus the
    # lead), using the word times the Transcribe run saved.

    def _build_editor(self, tab):
        self.ed = {"cues": [], "job": None, "undo": [], "redo": [], "dirty": False}
        self.ed_timeline_map = {}
        self._ed_follow_guard = False
        self._ed_last_name = ""

        top = tk.Frame(tab, bg=BG)
        top.pack(fill="x", pady=(S(10), 0))
        self._label(top, "Timeline", sub=True).pack(side="left")
        self.ed_tl_var = tk.StringVar()
        self.ed_combo = ttk.Combobox(top, textvariable=self.ed_tl_var, state="readonly",
                                     width=36, font=FONT(9))
        self.ed_combo.pack(side="left", padx=(S(6), S(6)))
        self.ed_combo.bind("<<ComboboxSelected>>", lambda e: self.ed_load())
        make_button(top, "Reload", self.ed_load).pack(side="left")
        self.ed_source = self._label(top, "", sub=True)
        self.ed_source.pack(side="left", padx=S(10))
        self.ed_audio, self.ed_proc, self.ed_play_state = None, None, None
        self.ed_autoplay_var = tk.BooleanVar(value=self.prefs.get("edit_autoplay", True))

        wrap = tk.Frame(tab, bg=BORDER, padx=1, pady=1)
        wrap.pack(fill="both", expand=True, pady=(S(8), S(8)))
        cols = ("n", "start", "end", "dur", "text", "len")
        self.ed_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended",
                                    style="Edit.Treeview")
        for col, title, width, stretch, anchor in (
                ("n", "#", 46, False, "e"), ("start", "Start", 110, False, "w"),
                ("end", "End", 110, False, "w"), ("dur", "Frames", 60, False, "e"),
                ("text", "Text", 420, True, "w"), ("len", "Chars", 52, False, "e")):
            self.ed_tree.heading(col, text=title)
            self.ed_tree.column(col, width=S(width), stretch=stretch, anchor=anchor)
        self.ed_tree.tag_configure("long", foreground=WARN)
        self.ed_tree.tag_configure("short", foreground=BAD)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.ed_tree.yview)
        self.ed_tree.configure(yscrollcommand=sb.set)
        self.ed_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.ed_tree.bind("<<TreeviewSelect>>", lambda e: self.ed_on_select())
        mod = "Command" if ru.IS_MAC else "Control"
        for seq, fn in ((f"<{mod}-z>", self.ed_undo), (f"<{mod}-Z>", self.ed_redo),
                        (f"<{mod}-Shift-z>", self.ed_redo), ("e", self.ed_merge), ("E", self.ed_merge),
                        ("bracketleft", lambda: self.ed_nudge(-1)),
                        ("bracketright", lambda: self.ed_nudge(1)),
                        ("space", self.ed_toggle_play), ("p", self.ed_play_selected)):
            self.ed_tree.bind(seq if seq.startswith("<") or len(seq) == 1 else f"<{seq}>",
                              lambda e, fn=fn: (fn(), "break")[1])
        ru.bind_delete(self.ed_tree, lambda e=None: self.ed_delete())
        # Right-click a word in the Text column: split the cue just before it.
        for seq in ("<Button-2>", "<Button-3>") + (("<Control-Button-1>",) if ru.IS_MAC else ()):
            self.ed_tree.bind(seq, self.ed_right_click)

        panel = tk.Frame(tab, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        panel.pack(fill="x")
        inner = tk.Frame(panel, bg=PANEL)
        inner.pack(fill="x", padx=S(10), pady=S(8))

        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x")
        self._label(row, "Text", sub=True, bg=PANEL).pack(side="left")
        self.ed_text_var = tk.StringVar()
        self.ed_entry = entry(row, self.ed_text_var, font=FONT(11))
        self.ed_entry.pack(side="left", fill="x", expand=True, padx=(S(8), S(6)), ipady=S(4))
        self.ed_entry.bind("<Return>", lambda e: self.ed_apply_text())
        self.ed_entry.bind(f"<{mod}-Return>", lambda e: (self.ed_split_at_cursor(), "break")[1])
        make_button(row, "Apply Text", self.ed_apply_text).pack(side="left")
        make_button(row, "Split at Cursor", self.ed_split_at_cursor).pack(side="left", padx=(S(6), 0))

        self._label(inner, "Click a word to split the cue just before it (or right-click the word "
                           "in the list above):", sub=True,
                    bg=PANEL).pack(fill="x", pady=(S(8), S(3)))
        self.ed_words_frame = tk.Frame(inner, bg=PANEL, height=S(30))
        self.ed_words_frame.pack(fill="x")

        play = tk.Frame(inner, bg=PANEL)
        play.pack(fill="x", pady=(S(10), 0))
        self.ed_play_btn = make_button(play, "\u25b6 Play From Cue (Space)", self.ed_toggle_play)
        self.ed_play_btn.pack(side="left")
        make_button(play, "Play Cue (P)", self.ed_play_selected).pack(side="left", padx=(S(6), 0))
        make_button(play, "\u25a0 Stop", self.ed_stop).pack(side="left", padx=(S(6), 0))
        tk.Checkbutton(play, text="Play a cue when it's selected", variable=self.ed_autoplay_var,
                       bg=PANEL, fg=FG, selectcolor=PANEL2, activebackground=PANEL,
                       activeforeground=FG, font=FONT(9), highlightthickness=0
                       ).pack(side="left", padx=(S(12), 0))

        btns = tk.Frame(inner, bg=PANEL)
        btns.pack(fill="x", pady=(S(8), 0))
        for text, fn in (("Merge (E)", self.ed_merge), ("Delete", self.ed_delete),
                         ("◀ Start −1f  [", lambda: self.ed_nudge(-1)),
                         ("Start +1f ▶  ]", lambda: self.ed_nudge(1)),
                         ("Undo", self.ed_undo), ("Redo", self.ed_redo)):
            make_button(btns, text, fn).pack(side="left", padx=(0, S(6)))
        self.ed_apply_btn = make_button(btns, "Apply to Resolve", self.ed_apply, primary=True)
        self.ed_apply_btn.pack(side="right")
        self.ed_status = self._label(inner, "Pick a timeline to edit its subtitles.", sub=True, bg=PANEL)
        self.ed_status.pack(fill="x", pady=(S(8), 0))

        self.root.after(400, self.ed_poll)

    def ed_say(self, text, colour=None):
        self.ed_status.config(text=text, fg=colour or SUB)

    # -- loading -----------------------------------------------------------
    def ed_tab_shown(self):
        try:
            if self.nb.index("current") != 1:
                return
        except tk.TclError:
            return
        project = self.project()
        if not project:
            return
        self.ed_timeline_map = {}
        for tl, _bin in timelines_with_bins(project):
            name, n = tl.GetName(), 2
            while name in self.ed_timeline_map:
                name, n = f"{tl.GetName()} ({n})", n + 1
            self.ed_timeline_map[name] = tl
        self.ed_combo["values"] = list(self.ed_timeline_map)
        if not self.ed["job"]:
            current = project.GetCurrentTimeline()
            cur_id = current.GetUniqueId() if current else None
            for name, tl in self.ed_timeline_map.items():
                if tl.GetUniqueId() == cur_id:
                    self.ed_tl_var.set(name)
                    self.ed_load()
                    break

    def ed_load(self):
        name = self.ed_tl_var.get()
        tl = self.ed_timeline_map.get(name)
        if not tl:
            return
        if self.ed["dirty"] and not messagebox.askyesno(
                "Subtitle Edit", "Your edits haven't been applied to Resolve. Discard them?",
                **ru.dialog_parent(self.root)):
            self.ed_tl_var.set(self._ed_last_name)
            return
        project = self.project()
        start = int(tl.GetStartFrame())
        jid = sanitize(tl.GetName())
        work = self.dir_var.get().strip() or guess_work_dir(project, [tl])
        dirs = {k: os.path.join(work, k) for k in ("renders", "wav", "words", "subtitles")}
        cues_path = os.path.join(dirs["words"], jid + "_cues.json")
        data = ru.read_json(cues_path, {}) or {}
        saved = [dict(c) for c in data.get("cues") or []]
        on_tl = [{"f": int(round(i.GetStart())) - start, "e": int(round(i.GetEnd())) - start,
                  "text": " ".join((i.GetName() or "").split())}
                 for i in sorted(subtitle_items(tl), key=lambda i: i.GetStart())]

        def same(a, b):
            return len(a) == len(b) and all(x["f"] == y["f"] and x["text"] == y["text"]
                                             for x, y in zip(a, b))

        if saved and (not on_tl or same(saved, on_tl)):
            cues, source = saved, "word timing available"
        elif on_tl:
            # Edited in Resolve since: the timeline wins, word times are kept
            # for the cues that still match.
            times = {(c["f"], c["text"]): c.get("w") for c in saved}
            cues = [dict(c, w=times.get((c["f"], c["text"]))) for c in on_tl]
            source = "read from the timeline" + (" (changed in Resolve since the run)" if saved else "")
        else:
            cues, source = [], ""
        num, den = fps_fraction(tl)
        self.ed.update(cues=cues, undo=[], redo=[], dirty=False, job={
            "tl": tl, "name": tl.GetName(), "id": jid, "start": start, "fps": (num, den),
            "drop": is_drop_frame(tl), "dirs": dirs, "cues_path": cues_path,
            "lead": float(data.get("lead_frames", self.lead_var.get() or 4))})
        self._ed_last_name = name
        if cues and not self.ed_load_audio(self.ed["job"]):
            source += " \u00b7 no exported audio to play"
        self.ed_source.config(text=source)
        self.ed_draw()
        if cues:
            self.ed_say(f"{len(cues)} cues. Right-click a word to split before it; select cues and "
                        f"press E to merge; [ ] move a start a frame; Space plays on from the cue.")
        else:
            self.ed_say("No subtitles on this timeline yet - make them on the Transcribe tab.", WARN)

    # -- drawing -----------------------------------------------------------
    def ed_selected(self):
        return sorted(int(i) for i in self.ed_tree.selection())

    def ed_draw(self, select=None):
        job, cues = self.ed["job"], self.ed["cues"]
        keep = select if select is not None else self.ed_selected()
        self.ed_tree.delete(*self.ed_tree.get_children())
        if not job:
            return
        try:
            limit = int(self.max_var.get())
        except ValueError:
            limit = 14
        fps = job["fps"][0] / job["fps"][1]
        for i, c in enumerate(cues):
            n = len(c["text"])
            tag = "short" if c["e"] - c["f"] < 2 else ("long" if n > limit else "")
            self.ed_tree.insert("", "end", iid=str(i), tags=(tag,) if tag else (), values=(
                i + 1, frame_to_tc(job["start"] + c["f"], fps, job["drop"]),
                frame_to_tc(job["start"] + c["e"], fps, job["drop"]), c["e"] - c["f"], c["text"], n))
        keep = [i for i in (keep if isinstance(keep, list) else [keep]) if 0 <= i < len(cues)]
        if keep:
            self._ed_follow_guard = True          # a redraw is not a click
            self.ed_tree.selection_set([str(i) for i in keep])
            self.ed_tree.see(str(keep[0]))
        self.ed_fill_editor()
        state = " • edited - not applied yet" if self.ed["dirty"] else ""
        self.ed_apply_btn.config(text="Apply to Resolve" + (" ●" if self.ed["dirty"] else ""))
        if state:
            self.ed_source.config(text=self.ed_source.cget("text").split(" • ")[0] + state)

    def ed_fill_editor(self):
        sel = self.ed_selected()
        for w in self.ed_words_frame.winfo_children():
            w.destroy()
        if len(sel) != 1:
            self.ed_text_var.set("")
            return
        i = sel[0]
        cue = self.ed["cues"][i]
        self.ed_text_var.set(cue["text"])
        words, _real = self.ed_words(cue)
        for k, (word, _f) in enumerate(words):
            lbl = tk.Label(self.ed_words_frame, text=word, bg=PANEL2, fg=FG if k else SUB,
                           font=FONT(10), padx=S(7), pady=S(3), cursor="hand2" if k else "")
            lbl.pack(side="left", padx=(0, S(4)))
            if k:
                lbl.bind("<Enter>", lambda e, l=lbl: l.config(bg=ACCENT, fg="#ffffff"))
                lbl.bind("<Leave>", lambda e, l=lbl: l.config(bg=PANEL2, fg=FG))
                lbl.bind("<Button-1>", lambda e, i=i, k=k: self.ed_split(i, k))

    def ed_right_click(self, event):
        """Split the cue before the word under the mouse (on the first word:
        after it)."""
        iid = self.ed_tree.identify_row(event.y)
        if not iid:
            return "break"
        i = int(iid)
        if self.ed_tree.identify_column(event.x) != "#5":          # not the Text column
            self.ed_tree.selection_set(iid)
            return "break"
        box = self.ed_tree.bbox(iid, "text")
        words, _real = self.ed_words(self.ed["cues"][i])
        if not box or len(words) < 2:
            self.ed_tree.selection_set(iid)
            self.ed_say("A single word can't be split.", BAD)
            return "break"
        measure = tkfont.Font(font=FONT(9)).measure
        x = event.x - box[0] - S(TREE_TEXT_PAD)
        space, pos, k = measure(" "), 0, len(words) - 1
        for idx, (word, _f) in enumerate(words):
            width = measure(word)
            if x < pos + width + space / 2:
                k = idx
                break
            pos += width + space
        self.ed_split(i, max(1, k))
        return "break"

    def ed_words(self, cue):
        """[(word, frame)] for a cue, and whether the frames are real aligned
        word starts (else spread over the cue by character position)."""
        toks = cue["text"].split()
        w = cue.get("w")
        if w and [x[0] for x in w] == toks:
            return [(x[0], int(x[1])) for x in w], True
        out, pos, total = [], 0, max(1, len(cue["text"]))
        for t in toks:
            at = cue["text"].index(t, pos)
            out.append((t, cue["f"] + int(round((cue["e"] - cue["f"]) * at / total))))
            pos = at + len(t)
        return out, False

    # -- playback (the timeline's own exported audio, no Resolve needed) -----
    def ed_load_audio(self, job):
        import wave
        self.ed_stop()
        self.ed_audio = None
        path = os.path.join(job["dirs"]["wav"], job["id"] + ".wav")
        if not os.path.isfile(path):
            return False
        try:
            with wave.open(path, "rb") as w:
                self.ed_audio = {"params": w.getparams(), "rate": w.getframerate(),
                                 "width": w.getsampwidth() * w.getnchannels(),
                                 "data": w.readframes(w.getnframes())}
        except Exception:
            self.ed_audio = None
        return self.ed_audio is not None

    def ed_on_select(self):
        self.ed_fill_editor()
        if self._ed_follow_guard:
            self._ed_follow_guard = False
            return
        sel = self.ed_selected()
        if len(sel) == 1 and self.ed_autoplay_var.get():
            self.ed_play(sel[0], sel[0])

    def ed_play_selected(self):
        sel = self.ed_selected()
        if sel:
            self.ed_play(sel[0], sel[-1])

    def ed_toggle_play(self):
        if self.ed_play_state and self.ed_play_state.get("follow"):
            self.ed_stop()
            return
        sel = self.ed_selected()
        if self.ed["cues"]:
            self.ed_play(sel[0] if sel else 0, None)

    def ed_play(self, first, last=None):
        """Play from cue `first` to the end of cue `last` (None: to the end,
        with the list following along)."""
        import wave
        self.ed_stop()
        job, cues, a = self.ed["job"], self.ed["cues"], self.ed_audio
        if not a or not cues:
            if job and not a:
                self.ed_say("No exported audio for this timeline - it's made by a Transcribe run.", WARN)
            return
        fps = job["fps"][0] / job["fps"][1]
        t0 = cues[first]["f"] / fps
        b0 = int(t0 * a["rate"]) * a["width"]
        b1 = int(cues[last]["e"] / fps * a["rate"]) * a["width"] if last is not None else len(a["data"])
        if b1 <= b0:
            return
        path = ru.data_path("subtitle_edit_preview.wav")
        try:
            with wave.open(path, "wb") as w:
                w.setparams(a["params"])
                w.writeframes(a["data"][b0:b1])
            self.ed_proc = subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
        except Exception as e:
            self.ed_say(f"Couldn't play the audio: {e}", BAD)
            return
        self.ed_play_state = {"t0": time.time(), "offset": t0, "follow": last is None}
        if last is None:
            self.ed_play_btn.config(text="\u25a0 Stop (Space)")

    def ed_stop(self):
        proc, self.ed_proc, self.ed_play_state = self.ed_proc, None, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            self.ed_play_btn.config(text="\u25b6 Play From Cue (Space)")
        except Exception:
            pass

    def ed_poll(self):
        """While playing on, keep the cue being heard selected."""
        try:
            state, job, cues = self.ed_play_state, self.ed["job"], self.ed["cues"]
            if state:
                if self.ed_proc is None or self.ed_proc.poll() is not None:
                    self.ed_stop()
                elif state["follow"] and job and cues:
                    fps = job["fps"][0] / job["fps"][1]
                    f = (state["offset"] + time.time() - state["t0"]) * fps
                    i = bisect.bisect_right([c["f"] for c in cues], f) - 1
                    if 0 <= i < len(cues) and self.ed_selected() != [i]:
                        self._ed_follow_guard = True
                        self.ed_tree.selection_set(str(i))
                        self.ed_tree.see(str(i))
        except Exception:
            pass
        finally:
            try:
                self.root.after(60, self.ed_poll)
            except tk.TclError:
                pass

    # -- edits ---------------------------------------------------------------
    def ed_snapshot(self):
        self.ed["undo"].append(copy.deepcopy(self.ed["cues"]))
        del self.ed["undo"][:-200]
        self.ed["redo"] = []
        self.ed["dirty"] = True

    def ed_undo(self):
        if self.ed["undo"]:
            self.ed["redo"].append(copy.deepcopy(self.ed["cues"]))
            self.ed["cues"] = self.ed["undo"].pop()
            self.ed["dirty"] = True
            self.ed_draw()
            self.ed_say("Undone.")

    def ed_redo(self):
        if self.ed["redo"]:
            self.ed["undo"].append(copy.deepcopy(self.ed["cues"]))
            self.ed["cues"] = self.ed["redo"].pop()
            self.ed["dirty"] = True
            self.ed_draw()
            self.ed_say("Redone.")

    def ed_split(self, i, k):
        cues = self.ed["cues"]
        cue = cues[i]
        words, real = self.ed_words(cue)
        if not 0 < k < len(words):
            return
        if cue["e"] - cue["f"] < 4:
            self.ed_say("That cue is too short to split (each half needs 2 frames).", BAD)
            return
        f = words[k][1] - (int(round(self.ed["job"]["lead"])) if real else 0)
        f = max(cue["f"] + 2, min(cue["e"] - 2, f))
        self.ed_snapshot()
        first = {"f": cue["f"], "e": f, "text": " ".join(w for w, _ in words[:k]),
                 "w": [list(x) for x in words[:k]] if real else None}
        second = {"f": f, "e": cue["e"], "text": " ".join(w for w, _ in words[k:]),
                  "w": [list(x) for x in words[k:]] if real else None}
        cues[i:i + 1] = [first, second]
        self.ed_draw(select=[i + 1])
        how = "on the word's onset" if real else "estimated from the text (no word timing)"
        self.ed_say(f"Split cue {i + 1} before '{words[k][0]}' - {how}.")

    def ed_split_at_cursor(self):
        sel = self.ed_selected()
        if len(sel) != 1:
            self.ed_say("Select one cue to split.", BAD)
            return
        text, cursor = self.ed_text_var.get(), self.ed_entry.index("insert")
        if " ".join(text.split()) != self.ed["cues"][sel[0]]["text"]:
            self.ed_apply_text()
        # The cursor anywhere in a word splits just before that word.
        before = text[:cursor]
        k = len(before.split()) if (not before or before.endswith(" ")) else len(before.split()) - 1
        if cursor >= len(text.rstrip()):
            k = len(text.split())
        self.ed_split(sel[0], k)

    def ed_merge(self):
        sel = self.ed_selected()
        cues = self.ed["cues"]
        if len(sel) == 1 and sel[0] + 1 < len(cues):
            sel = [sel[0], sel[0] + 1]
        if len(sel) < 2:
            self.ed_say("Select two or more cues next to each other to merge.", BAD)
            return
        if sel != list(range(sel[0], sel[-1] + 1)):
            self.ed_say("Only cues next to each other can be merged.", BAD)
            return
        self.ed_snapshot()
        part = cues[sel[0]:sel[-1] + 1]
        words = [self.ed_words(c) for c in part]
        merged = {"f": part[0]["f"], "e": part[-1]["e"],
                  "text": " ".join(c["text"] for c in part),
                  "w": [list(x) for w, real in words for x in w] if all(r for _, r in words) else None}
        cues[sel[0]:sel[-1] + 1] = [merged]
        self.ed_draw(select=[sel[0]])
        self.ed_say(f"Merged {len(part)} cues into cue {sel[0] + 1}.")

    def ed_delete(self):
        sel = self.ed_selected()
        cues = self.ed["cues"]
        if not sel:
            return
        self.ed_snapshot()
        for i in reversed(sel):
            if i > 0 and cues[i - 1]["e"] == cues[i]["f"]:
                cues[i - 1]["e"] = cues[i]["e"]          # no gap: the cue before stays up
            del cues[i]
        self.ed_draw(select=[max(0, sel[0] - 1)] if cues else [])
        self.ed_say(f"Deleted {len(sel)} cue(s); the cue before each one now stays up in its place.")

    def ed_nudge(self, delta):
        sel = self.ed_selected()
        if len(sel) != 1:
            self.ed_say("Select one cue to move its start.", BAD)
            return
        i = sel[0]
        cues = self.ed["cues"]
        lo = cues[i - 1]["f"] + 2 if i > 0 else 0
        new = max(lo, min(cues[i]["e"] - 2, cues[i]["f"] + delta))
        if new == cues[i]["f"]:
            self.ed_say("It can't move further (cues need at least 2 frames).", BAD)
            return
        self.ed_snapshot()
        if i > 0 and cues[i - 1]["e"] == cues[i]["f"]:
            cues[i - 1]["e"] = new
        cues[i]["f"] = new
        self.ed_draw(select=[i])
        if self.ed_autoplay_var.get():
            self.ed_play(i, i)
        self.ed_say(f"Cue {i + 1} starts {'earlier' if delta < 0 else 'later'} by 1 frame.")

    def ed_apply_text(self):
        sel = self.ed_selected()
        if len(sel) != 1:
            return
        cue = self.ed["cues"][sel[0]]
        text = " ".join(self.ed_text_var.get().split())
        if not text or text == cue["text"]:
            return
        self.ed_snapshot()
        words, real = self.ed_words(cue)
        new = text.split()
        cue["w"] = [[t, f] for t, (_, f) in zip(new, words)] if real and len(new) == len(words) else None
        cue["text"] = text
        self.ed_draw(select=[sel[0]])
        self.ed_say(f"Cue {sel[0] + 1} text changed.")

    # -- apply -----------------------------------------------------------------
    def ed_apply(self):
        job, cues = self.ed["job"], self.ed["cues"]
        if not job or not cues:
            return
        if self.running:
            self.ed_say("Wait for the Transcribe run to finish first.", BAD)
            return
        for i, c in enumerate(cues):
            if c["e"] - c["f"] < 2 or not c["text"].strip() or (i and c["f"] < cues[i - 1]["f"] + 2):
                self.ed_select_and_say(i, f"Cue {i + 1} is shorter than 2 frames or empty - "
                                          f"Resolve would drop it. Fix it first.")
                return
        num, den = job["fps"]
        os.makedirs(job["dirs"]["subtitles"], exist_ok=True)
        os.makedirs(job["dirs"]["words"], exist_ok=True)
        srt = os.path.join(job["dirs"]["subtitles"], job["id"] + "_edited.srt")
        with open(srt, "w", encoding="utf-8") as f:
            for n, c in enumerate(cues, 1):
                f.write(f"{n}\n{srt_time(c['f'], num, den)} --> {srt_time(c['e'], num, den)}\n"
                        f"{c['text']}\n\n")
        if os.path.isfile(job["cues_path"]):
            orig = job["cues_path"].replace("_cues.json", "_cues_orig.json")
            if not os.path.exists(orig):
                os.replace(job["cues_path"], orig)          # the run's own cues, kept once
        ru.write_json(job["cues_path"], {"fps": [num, den], "lead_frames": job["lead"],
                                         "cues": cues, "edited": True})
        self.ed_apply_btn.config(state="disabled")
        try:
            project = self.project()
            self.import_srt(project, job, srt, cues)
            problems = self.verify(job, cues)
            projectManager.SaveProject()
        except StepError as e:
            self.ed_say(f"Couldn't apply: {e}", BAD)
            return
        except Exception as e:
            self.ed_say(f"Couldn't apply: {e}", BAD)
            self.log(traceback.format_exc(), "bad")
            return
        finally:
            self.ed_apply_btn.config(state="normal")
        self.ed["dirty"] = False
        self.ed_draw()
        self.ed_source.config(text="word timing available")
        if problems:
            self.ed_say("Applied, but: " + "; ".join(problems[:3]), BAD)
        else:
            self.ed_say(f"Applied: {len(cues)} cues on the timeline, every one on its exact frame, "
                        f"no gaps.", GOOD)

    def ed_select_and_say(self, i, text):
        self.ed_tree.selection_set(str(i))
        self.ed_tree.see(str(i))
        self.ed_say(text, BAD)

    # -- stop / close -------------------------------------------------------
    def stop(self):
        if self.running:
            self.stop_requested = True
            self.step_text.config(text="Stopping...")

    def _on_close(self):
        if getattr(self, "ed", {}).get("dirty") and not messagebox.askyesno(
                "Accurate Subtitles", "The subtitle edits haven't been applied to Resolve. "
                "Close anyway and lose them?", **ru.dialog_parent(self.root)):
            return
        if self.running:
            if not messagebox.askyesno("Accurate Subtitles", "Subtitles are being made. Stop and close?",
                                       **ru.dialog_parent(self.root)):
                return
            self.stop_requested = True
            if self.proc:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
        try:
            self.ed_stop()
            self.prefs["edit_autoplay"] = self.ed_autoplay_var.get()
        except Exception:
            pass
        try:
            self.read_settings()
        except Exception:
            pass
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            ru.write_json(PREFS_FILE, self.prefs)
        except Exception:
            pass
        self.root.destroy()


def start():
    global SCALE
    root = tk.Tk()
    SCALE = ru.detect_scale(root)
    app = AccurateSubtitles(root)
    root.bind("<Escape>", lambda e: None if app.running else app._on_close())
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


start()
