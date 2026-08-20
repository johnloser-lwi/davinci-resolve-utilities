import ctypes
import json
import os
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

# Nest Selected Clips - two-step, so a huge source timeline costs nothing.
#
# Unlike a compound clip, a nested timeline is a real project timeline, so the
# scripting API can see inside it: GetSelectedClips, transforms and the align
# tools all work within it. Compound clips are a black box to scripting.
#
# The flow:
#   Step 1  copy the clips in Resolve (Ctrl+C), then click "Create nest
#           timeline". An empty timeline is made with the source timeline's
#           settings copied across, filed into the PreComps bin, and opened
#           with the playhead on the first frame.
#   Paste   you paste (Ctrl+V). Resolve's own paste carries everything -
#           grades, Fusion comps, Text+ generators - which no API rebuild can.
#   Step 2  click "Pasted, place it". The nest is measured, the originals are
#           disabled or deleted, and it is placed back over the same span
#           without breaking layer order.
#
# Nothing duplicates the source timeline, so this stays fast no matter how
# long the edit gets.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\nest_clips_prefs.json")

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

CLIP_COLOURS = ["Orange", "Apricot", "Yellow", "Lime", "Olive", "Green",
                "Teal", "Navy", "Blue", "Purple", "Violet", "Pink", "Tan",
                "Beige", "Brown", "Chocolate"]
COLOUR_CHOICES = ["From first clip"] + CLIP_COLOURS + ["None"]

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]
SCALE = 1.0
SELECTION_PASSES = 3


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


def style_button(btn, primary=False):
    """Re-colour a button after creation.

    The hover handlers close over the original colours, so they have to be
    rebound too - otherwise moving the mouse away repaints the old style.
    """
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn.config(bg=base, fg="#ffffff" if primary else FG,
               activebackground=hover)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))


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


def item_uid(item):
    try:
        uid = item.GetUniqueId()
        if uid:
            return str(uid)
    except Exception:
        pass
    try:
        return f"{item.GetName()}@{item.GetStart()}"
    except Exception:
        return str(id(item))


def track_info(item):
    try:
        info = item.GetTrackTypeAndIndex()
        if isinstance(info, dict):
            info = [info.get(1), info.get(2)]
        if isinstance(info, (list, tuple)) and len(info) >= 2:
            return str(info[0]).lower(), int(info[1])
    except Exception:
        pass
    return None, None


def selected_clips(include_audio=False):
    """Selected items as cached records, one API round-trip per field.

    Every attribute (track, start, end, name) is read ONCE here and reused by
    the caller. Re-reading them downstream was costing ~20 API calls per clip,
    which is what made large selections crawl.

    GetSelectedClips() can return a partial selection, so it is polled more
    than once — but identity is only resolved (GetUniqueId, an extra call per
    item) when two polls actually disagree, which is the rare case.
    """
    timeline = current_timeline()
    if not timeline:
        return []

    polls = []
    for _ in range(SELECTION_PASSES):
        try:
            sel = timeline.GetSelectedClips()
        except Exception as e:
            print(f"GetSelectedClips failed: {e}")
            return []
        items = list(sel.values()) if isinstance(sel, dict) else list(sel or [])
        polls.append(items)
        if len(polls) >= 2 and len(polls[-1]) == len(polls[-2]):
            break                      # stable, no need to keep polling

    if not polls:
        return []
    if all(len(p) == len(polls[0]) for p in polls):
        items = polls[0]               # agreed: skip identity work entirely
    else:
        merged = {}
        for poll in polls:
            for it in poll:
                merged.setdefault(item_uid(it), it)
        items = list(merged.values())

    records = []
    for it in items:
        kind, idx = track_info(it)
        kind = kind or "video"
        if kind == "audio" and not include_audio:
            continue
        if kind not in ("video", "audio"):
            continue
        try:
            record = {
                "item": it,
                "kind": kind,
                "idx": idx,
                "start": it.GetStart(),
                "end": it.GetEnd(),
                "name": it.GetName(),
            }
        except Exception:
            continue
        records.append(record)
    records.sort(key=lambda r: r["start"])
    return records


def clone_timeline_settings(source, target):
    """Copy every timeline setting across, custom ones included.

    GetSetting() with no argument returns the whole dict on builds that
    support it, which matches settings exactly rather than guessing at a
    hand-written list. Falls back to the essentials if that isn't available.
    """
    copied = 0
    settings = None
    try:
        settings = source.GetSetting()
    except Exception:
        settings = None

    if isinstance(settings, dict) and settings:
        for key, value in settings.items():
            try:
                if target.SetSetting(key, str(value)):
                    copied += 1
            except Exception:
                continue
        return copied, len(settings)

    essentials = ["timelineResolutionWidth", "timelineResolutionHeight",
                  "timelineFrameRate", "timelinePlaybackFrameRate",
                  "timelinePixelAspectRatio", "timelineDropFrameTimecode",
                  "timelineOutputResolutionWidth", "timelineOutputResolutionHeight",
                  "timelineInterlaceProcessing"]
    for key in essentials:
        try:
            value = source.GetSetting(key)
            if value not in (None, "") and target.SetSetting(key, str(value)):
                copied += 1
        except Exception:
            continue
    return copied, len(essentials)


def track_is_free(timeline, track_index, start, end):
    """True when nothing on this video track overlaps [start, end)."""
    try:
        items = timeline.GetItemListInTrack("video", track_index) or []
    except Exception:
        return False
    for item in items:
        try:
            if item.GetStart() < end and item.GetEnd() > start:
                return False
        except Exception:
            return False
    return True


def find_timeline_media_item(media_pool, name):
    """Timelines appear in the media pool as clips with Type == 'Timeline'."""
    def walk(folder):
        try:
            clips = folder.GetClipList() or []
        except Exception:
            clips = []
        for clip in clips:
            try:
                if clip.GetName() == name and clip.GetClipProperty("Type") == "Timeline":
                    return clip
            except Exception:
                continue
        try:
            subs = folder.GetSubFolderList() or []
        except Exception:
            subs = []
        for sub in subs:
            hit = walk(sub)
            if hit:
                return hit
        return None

    try:
        return walk(media_pool.GetRootFolder())
    except Exception:
        return None


PRECOMPS_BIN = "PreComps"


def default_nest_name(timeline):
    """<Timeline>_Nest_<timestamp> so a name is always ready without typing."""
    try:
        raw = timeline.GetName()
    except Exception:
        raw = "Timeline"
    clean = "".join(c for c in raw if c.isalnum() or c in " _-").strip()
    clean = clean.replace(" ", "_") or "Timeline"
    return clean + "_Nest_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def find_or_create_precomps(media_pool):
    """Same PreComps bin the media-pool organisation script collects into."""
    try:
        root = media_pool.GetRootFolder()
        for folder in (root.GetSubFolderList() or []):
            if folder.GetName() == PRECOMPS_BIN:
                return folder, False
        return media_pool.AddSubFolder(root, PRECOMPS_BIN), True
    except Exception as e:
        print("  could not access the PreComps bin: " + str(e))
        return None, False


def unique_timeline_name(project, base):
    existing = set()
    try:
        for i in range(1, (project.GetTimelineCount() or 0) + 1):
            t = project.GetTimelineByIndex(i)
            if t:
                existing.add(t.GetName())
    except Exception:
        pass
    if base not in existing:
        return base
    n = 2
    while f"{base} {n}" in existing:
        n += 1
    return f"{base} {n}"


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class NestPanel:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.clips = []
        self._last_auto_name = ""

        root.title("Nest Selected Clips")
        root.configure(bg=BG)
        root.minsize(S(520), S(300))
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

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(22), font=FONT(9))
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

        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True)

        head = tk.Frame(content, bg=BG)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        self.tl_label = tk.Label(head, text="—", bg=BG, fg=FG, anchor="w",
                                 font=FONT(11, "bold"))
        self.tl_label.pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.info = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.info.pack(side="left")
        make_button(row, "Rescan", self.rescan).pack(side="right")
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0,
                                 font=FONT(9), cursor="hand2", padx=S(10),
                                 pady=S(5), highlightthickness=0,
                                 command=self.toggle_topmost)
        self.top_btn.pack(side="right", padx=(0, S(6)))
        self._paint_topmost()

        name_row = tk.Frame(content, bg=BG)
        name_row.pack(fill="x", padx=S(14), pady=(S(12), 0))
        tk.Label(name_row, text="Nest name", bg=BG, fg=SUB, font=FONT(9),
                 width=10, anchor="w").pack(side="left")
        self.name_var = tk.StringVar()
        tk.Entry(name_row, textvariable=self.name_var, bg=PANEL, fg=FG,
                 relief="flat", insertbackground=FG, font=FONT(9),
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", fill="x", expand=True, ipady=S(4))

        tk.Label(content, text="SETTINGS", bg=BG, fg=SUB, font=FONT(8, "bold"),
                 anchor="w").pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        opts = tk.Frame(content, bg=BG)
        opts.pack(fill="x", padx=S(14))

        orig_row = tk.Frame(opts, bg=BG)
        orig_row.pack(fill="x", pady=(0, S(6)))
        tk.Label(orig_row, text="Originals", bg=BG, fg=SUB, font=FONT(9),
                 width=10, anchor="w").pack(side="left")
        self.originals_var = tk.StringVar(
            value=self.prefs.get("originals", "Disable"))
        orig_box = ttk.Combobox(orig_row, textvariable=self.originals_var,
                                values=["Disable", "Delete", "Leave as is"],
                                state="readonly", width=14, font=FONT(9))
        orig_box.pack(side="left")
        orig_box.bind("<<ComboboxSelected>>", lambda e: self._save_opts())
        tk.Label(orig_row, text="the nest is built and verified before this happens",
                 bg=BG, fg=SUB, font=FONT(8)).pack(side="left", padx=(S(10), 0))

        self.audio_var = tk.BooleanVar(value=bool(self.prefs.get("include_audio", True)))
        tk.Checkbutton(opts, text="Include selected audio clips",
                       variable=self.audio_var, command=self._on_audio_toggle,
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, font=FONT(9), highlightthickness=0,
                       anchor="w").pack(fill="x")

        colour_row = tk.Frame(opts, bg=BG)
        colour_row.pack(fill="x", pady=(S(4), 0))
        tk.Label(colour_row, text="Nest colour", bg=BG, fg=SUB, font=FONT(9),
                 width=10, anchor="w").pack(side="left")
        self.colour_var = tk.StringVar(
            value=self.prefs.get("nest_colour", "From first clip"))
        colour_box = ttk.Combobox(colour_row, textvariable=self.colour_var,
                                  values=COLOUR_CHOICES, state="readonly",
                                  width=16, font=FONT(9))
        colour_box.pack(side="left")
        colour_box.bind("<<ComboboxSelected>>", lambda e: self._save_opts())
        tk.Label(colour_row,
                 text="pick a colour if your clips have none set",
                 bg=BG, fg=SUB, font=FONT(8)).pack(side="left", padx=(S(10), 0))

        table = tk.Frame(content, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14), pady=(S(10), 0))
        self.tree = ttk.Treeview(table, columns=("clip", "track", "span"),
                                 show="headings", height=6)
        for col, label, width in (("clip", "CLIP", 240), ("track", "TRACK", 70),
                                  ("span", "FRAMES", 130)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=S(width), anchor="w")
        self.tree.pack(fill="both", expand=True)

        actions = tk.Frame(content, bg=BG)
        actions.pack(fill="x", padx=S(14), pady=(S(12), 0))
        self.step1_btn = make_button(actions, "1 - Create nest timeline",
                                     self.do_create, primary=True)
        self.step1_btn.pack(side="left")
        self.step2_btn = make_button(actions, "2 - Pasted, place it",
                                     self.do_place)
        self.step2_btn.pack(side="left", padx=(S(8), 0))
        self.step2_btn.config(state="disabled")

        tk.Label(content,
                 text="Copy the clips in Resolve first, then step 1. Paste into "
                      "the new timeline at the first frame, then step 2. Resolve's "
                      "own paste keeps grades, Fusion comps and Text+ intact.",
                 bg=BG, fg=SUB, font=FONT(8), anchor="w", justify="left",
                 wraplength=S(480)).pack(fill="x", padx=S(14), pady=(S(8), S(14)))

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

    def _paint_topmost(self):
        on = bool(self.prefs.get("always_on_top", True))
        self.top_btn.config(bg=ACCENT if on else BTN,
                            fg="#ffffff" if on else FG,
                            activebackground=ACCENT_HOVER if on else BTN_HOVER)

    def toggle_topmost(self):
        on = not bool(self.prefs.get("always_on_top", True))
        self.prefs["always_on_top"] = on
        save_prefs(self.prefs)
        try:
            self.root.attributes("-topmost", on)
        except Exception:
            pass
        self._paint_topmost()
        self.say("Window stays on top." if on else "Window no longer on top.")

    def _save_opts(self):
        self.prefs["originals"] = self.originals_var.get()
        self.prefs["include_audio"] = bool(self.audio_var.get())
        self.prefs["nest_colour"] = self.colour_var.get()
        save_prefs(self.prefs)

    def _on_audio_toggle(self):
        self._save_opts()
        self.rescan()

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
        self.root.minsize(S(520), S(300))
        self._style()
        self._build()
        self.rescan()

    def rescan(self):
        timeline = current_timeline()
        if not timeline:
            self.tl_label.config(text="No timeline open")
            self.say("Open a timeline to begin.", True)
            return
        self.tl_label.config(text=timeline.GetName())

        current = self.name_var.get().strip()
        if not current or current == self._last_auto_name:
            self._last_auto_name = default_nest_name(timeline)
            self.name_var.set(self._last_auto_name)

        self.clips = selected_clips(self.audio_var.get())
        self.tree.delete(*self.tree.get_children())
        videos = audios = 0
        for rec in self.clips:
            prefix = "A" if rec["kind"] == "audio" else "V"
            if rec["kind"] == "audio":
                audios += 1
            else:
                videos += 1
            self.tree.insert("", "end", values=(
                rec["name"], f"{prefix}{rec['idx']}" if rec["idx"] else "?",
                f"{rec['start']} - {rec['end']}"))
        if self.clips:
            span = max(c["end"] for c in self.clips) - min(c["start"] for c in self.clips)
            detail = f"{videos} video"
            if self.audio_var.get():
                detail += f" · {audios} audio"
            self.info.config(text=f"{detail} · {span} frames")
            self.say("Step 1 creates the nest and opens it - then just paste.")
        else:
            self.info.config(text="0 clips selected")
            self.say("Select clips on the timeline. Clips inside a compound clip "
                     "can't be read by the API.", True)

    # -- step 1: make the empty nest ------------------------------------
    def do_create(self):
        # One nest per window. A second run would strand the first timeline and
        # leave step 2 pointing at the wrong one.
        if getattr(self, "pending", None):
            self.say("A nest is already waiting - paste into it, then click "
                     "step 2. Reopen this window to start another.", True)
            return
        project = current_project()
        source = current_timeline()
        if not project or not source:
            self.say("No timeline open.", True)
            return
        clips = self.clips or selected_clips(bool(self.audio_var.get()))
        if not clips:
            self.say("Nothing selected.", True)
            return

        lowest = highest = None
        for c in clips:
            if c["kind"] == "video" and c["idx"]:
                lowest = c["idx"] if lowest is None else min(lowest, c["idx"])
                highest = c["idx"] if highest is None else max(highest, c["idx"])
        lowest = lowest or 1
        highest = highest or lowest

        name = unique_timeline_name(project, self.name_var.get().strip() or "Nest")
        t0 = time.time()

        media_pool = project.GetMediaPool()
        nest = media_pool.CreateEmptyTimeline(name)
        if not nest:
            self.say("CreateEmptyTimeline failed.", True)
            return
        done, total = clone_timeline_settings(source, nest)
        print("\nCreated " + name + " - " + str(done) + "/" + str(total)
              + " setting(s) matched  [" + format(time.time() - t0, ".2f") + "s]")

        precomps, created_bin = find_or_create_precomps(media_pool)
        mpi = find_timeline_media_item(media_pool, name)
        if precomps and mpi:
            try:
                media_pool.MoveClips([mpi], precomps)
                print("  filed into " + PRECOMPS_BIN
                      + (" (bin created)" if created_bin else ""))
            except Exception as e:
                print("  could not file into " + PRECOMPS_BIN + ": " + str(e))

        # Everything step 2 needs, captured before we switch away
        self.pending = {
            "name": name, "source": source, "clips": clips,
            "min_start": min(c["start"] for c in clips),
            "max_end": max(c["end"] for c in clips),
            "lowest": lowest, "highest": highest,
        }

        project.SetCurrentTimeline(nest)
        try:
            nest.SetCurrentTimecode(nest.GetStartTimecode())
        except Exception:
            pass

        # Hand the highlight over: step 1 is done, step 2 is what's next
        self.step1_btn.config(state="disabled", text="1 - Nest created")
        style_button(self.step1_btn, primary=False)
        self.step2_btn.config(state="normal")
        style_button(self.step2_btn, primary=True)
        self.say(name + " is open with the playhead on frame 1. Paste now, "
                 "then click step 2.")

    # -- step 2: put the nest back on the source timeline ----------------
    def do_place(self):
        if not getattr(self, "pending", None):
            self.say("Run step 1 first.", True)
            return
        project = current_project()
        info = self.pending
        source, name = info["source"], info["name"]
        media_pool = project.GetMediaPool()

        nest = None
        for i in range(1, (project.GetTimelineCount() or 0) + 1):
            t = project.GetTimelineByIndex(i)
            if t and t.GetName() == name:
                nest = t
                break
        if not nest:
            self.say("Could not find " + name + " any more.", True)
            return

        # Measure what was actually pasted, so it works wherever it landed.
        # The colour is taken from the earliest VIDEO clip in the nest rather
        # than from the original selection - the pasted content is the real
        # source of truth, and the originals often carry no colour at all.
        starts, ends = [], []
        first_item, first_start = None, None
        for kind in ("video", "audio"):
            for ti in range(1, (nest.GetTrackCount(kind) or 0) + 1):
                for item in (nest.GetItemListInTrack(kind, ti) or []):
                    begin = item.GetStart()
                    starts.append(begin)
                    ends.append(item.GetEnd())
                    if kind == "video" and (first_start is None or begin < first_start):
                        first_item, first_start = item, begin
        if not starts:
            self.say(name + " is still empty - paste into it first, then "
                     "click step 2.", True)
            return

        content_start, content_end = min(starts), max(ends)
        duration = content_end - content_start
        print("\nPlacing " + name + ": " + str(len(starts))
              + " pasted item(s), " + str(duration) + " frames")

        choice = self.colour_var.get()
        colour = ""
        if choice == "From first clip":
            if first_item is not None:
                try:
                    colour = first_item.GetClipColor() or ""
                except Exception as e:
                    print("  could not read the first clip colour: " + str(e))
            if colour:
                print("  colour from '" + first_item.GetName() + "': " + colour)
            else:
                print("  the first pasted clip has no colour set - pick an "
                      "explicit Nest colour if you want one applied")
        elif choice and choice != "None":
            colour = choice
            print("  using the chosen nest colour: " + colour)

        project.SetCurrentTimeline(source)

        clips = info["clips"]
        mode = self.originals_var.get()
        handled, action = 0, "left alone"
        if mode == "Disable":
            for c in clips:
                try:
                    if c["item"].SetClipEnabled(False):
                        handled += 1
                except Exception:
                    pass
            action = "disabled"
        elif mode == "Delete":
            try:
                if source.DeleteClips([c["item"] for c in clips]):
                    handled, action = len(clips), "deleted"
                else:
                    action = "left alone (delete refused)"
            except Exception as e:
                print("  DeleteClips failed: " + str(e))
                action = "left alone (delete failed)"
        print("  " + str(handled) + " original clip(s) " + action)

        mpi = find_timeline_media_item(media_pool, name)
        if not mpi:
            self.say(name + " is not in the media pool - drag it in manually.", True)
            return

        min_start = info["min_start"]
        lowest, highest = info["lowest"], info["highest"]
        target = None
        for candidate in range(lowest, highest + 1):
            if track_is_free(source, candidate, min_start, min_start + duration):
                target = candidate
                break
        if target is None:
            target = highest + 1
            try:
                source.AddTrack("video", {"index": target})
            except Exception:
                source.AddTrack("video")
            print("  no free track in V" + str(lowest) + "-V" + str(highest)
                  + "; inserted V" + str(target))
        else:
            print("  placing on existing V" + str(target))

        try:
            mpi_start = int(float(mpi.GetClipProperty("Start") or 0))
        except Exception:
            mpi_start = 0
        offset = content_start - (nest.GetStartFrame() or 0)
        start_frame = mpi_start + offset

        placed = media_pool.AppendToTimeline([{
            "mediaPoolItem": mpi,
            "startFrame": start_frame,
            "endFrame": start_frame + duration,
            "trackIndex": target,
            "recordFrame": min_start,
        }])

        if colour:
            if placed:
                try:
                    if placed[0].SetClipColor(colour):
                        print("  coloured the timeline clip " + colour)
                    else:
                        print("  SetClipColor(" + colour + ") was refused")
                except Exception as e:
                    print("  SetClipColor failed: " + str(e))
            # Colour the media pool entry too, so the nest is easy to spot in
            # the PreComps bin as well as on the timeline.
            try:
                if mpi.SetClipColor(colour):
                    print("  coloured the media pool item " + colour)
            except Exception as e:
                print("  media pool SetClipColor failed: " + str(e))

        self.pending = None

        if placed:
            print("  placed on V" + str(target) + " at frame " + str(min_start)
                  + ". " + str(handled) + " original(s) " + action
                  + ((", coloured " + colour) if colour else ""))
            # Job done - close rather than leaving another panel lying around.
            self._on_close()
            return

        self.say("Could not place " + name + " - drag it onto V"
                 + str(target) + " from " + PRECOMPS_BIN + ".", True)
        self.step2_btn.config(state="disabled")
        self.rescan()

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            self.prefs["originals"] = self.originals_var.get()
            self.prefs["include_audio"] = bool(self.audio_var.get())
            self.prefs["nest_colour"] = self.colour_var.get()
            save_prefs(self.prefs)
        except Exception:
            pass
        self.root.destroy()


prefs = load_prefs()
root = tk.Tk()
SCALE = resolve_scale(prefs, root)
app = NestPanel(root)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
if not prefs.get("always_on_top", True):
    root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
