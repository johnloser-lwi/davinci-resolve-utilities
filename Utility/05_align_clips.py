import ctypes
import json
import os
import tkinter as tk
from tkinter import ttk

# Align Clips — align and distribute the selected timeline clips using the
# Edit page Transform (Pan / Tilt), which every clip shares regardless of what
# plugin or effect is on it.
#
# Two alignment modes:
#   Edges   — each clip's on-screen rectangle is computed from its source
#             resolution, crop and zoom, so differently sized elements line up
#             by their real edges.
#   Centres — every clip simply gets the same Pan (or Tilt). Use this when the
#             artwork sits inside transparent padding, since the API exposes no
#             alpha bounding box and computed edges would be misleading.
#
# Read-only on everything except Pan / Tilt. Previous values are printed to the
# console before each change, because Resolve offers no undo grouping here.

# Tell Windows we handle our own DPI, so the panel isn't tiny on a 4K screen.
for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\align_clips_prefs.json")

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
        # A 4K panel left at 100% Windows scaling still reports 96 DPI
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


def make_button(parent, text, command, primary=False, width=None):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = tk.Button(parent, text=text, command=command, bg=base,
                    fg="#ffffff" if primary else FG, relief="flat", bd=0,
                    padx=S(10), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    if width:
        btn.config(width=width)
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
    if not project:
        return None
    try:
        return project.GetCurrentTimeline()
    except Exception:
        return None


def track_type_of(item):
    """'video', 'audio', 'subtitle', or None when it can't be determined."""
    try:
        info = item.GetTrackTypeAndIndex()
    except Exception:
        return None
    if isinstance(info, dict):          # 1-indexed table on some versions
        info = [info.get(1), info.get(2)]
    if isinstance(info, (list, tuple)) and info:
        return str(info[0]).lower()
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


SELECTION_PASSES = 3

COMPOUND_HINT = (
    'Nothing selected. Note: clips inside a COMPOUND CLIP are invisible to the API - Resolve reports the parent timeline instead. Use a nested timeline or Fusion clip if you need to align inside a container.'
)


def selected_clips():
    """Selected timeline items on VIDEO tracks only.

    GetSelectedClips() is unreliable: it intermittently returns only PART of
    the selection (measured returning 1 of 3 selected clips, depending on what
    API calls preceded it). A missed clip doesn't just fail to move — it is
    also left out of the min/max the alignment target is built from, which
    throws every other clip off too. So poll it several times and union the
    results by unique id.

    Linked audio items are filtered out as well; they have no usable Pan/Tilt.
    Returns (video_items, skipped_non_video).
    """
    timeline = current_timeline()
    if not timeline:
        return [], 0

    found = {}
    for _ in range(SELECTION_PASSES):
        try:
            sel = timeline.GetSelectedClips()
        except Exception as e:
            print(f"GetSelectedClips failed — is this Resolve version new enough? ({e})")
            return [], 0
        if not sel:
            continue
        # Comes back as a list or a 1-indexed dict depending on version
        items = list(sel.values()) if isinstance(sel, dict) else list(sel)
        for item in items:
            found.setdefault(item_uid(item), item)

    video, skipped = [], 0
    for item in found.values():
        kind = track_type_of(item)
        if kind is not None and kind != "video":
            skipped += 1
            continue
        video.append(item)
    return video, skipped


def timeline_resolution(project):
    try:
        w = int(float(project.GetSetting("timelineResolutionWidth")))
        h = int(float(project.GetSetting("timelineResolutionHeight")))
        return w, h
    except Exception:
        return 1920, 1080


def fit_scale(source_w, source_h, tl_w, tl_h, behavior):
    """How Resolve scales the source to the timeline before Zoom applies."""
    if not source_w or not source_h:
        return 1.0, 1.0
    sx, sy = tl_w / float(source_w), tl_h / float(source_h)
    mode = (behavior or "").lower()
    if "stretch" in mode:
        return sx, sy
    if "crop" in mode or "fill" in mode:
        s = max(sx, sy)
        return s, s
    if "fit" in mode:
        s = min(sx, sy)
        return s, s
    # "center" / "none" — placed at original pixel size
    return 1.0, 1.0


def clip_rect(item, project, tl_w, tl_h, behavior):
    """On-screen rectangle for a timeline item.

    Returns a dict with the visible centre and size in timeline pixels, where
    (0, 0) is the frame centre. Rotation is deliberately ignored — the
    unrotated box is used for alignment.
    """
    # Retry the read — an empty or partial dict here would silently corrupt the
    # computed width and send the clip to the wrong place.
    props = {}
    for _ in range(3):
        try:
            props = item.GetProperty() or {}
        except Exception:
            props = {}
        if "Pan" in props or "ZoomX" in props:
            break

    def num(key, default=0.0):
        try:
            return float(props.get(key, default))
        except (TypeError, ValueError):
            return default

    pan, tilt = num("Pan"), num("Tilt")
    zoom_x, zoom_y = num("ZoomX", 1.0) or 1.0, num("ZoomY", 1.0) or 1.0
    crop_l, crop_r = num("CropLeft"), num("CropRight")
    crop_t, crop_b = num("CropTop"), num("CropBottom")

    # Source size — titles, generators and Fusion clips have no media pool
    # item, so treat them as filling the frame.
    source_w, source_h = tl_w, tl_h
    try:
        mpi = item.GetMediaPoolItem()
        if mpi:
            res = mpi.GetClipProperty("Resolution")
            if res and "x" in str(res):
                parts = str(res).lower().split("x")
                source_w, source_h = int(parts[0]), int(parts[1])
    except Exception:
        pass

    fx, fy = fit_scale(source_w, source_h, tl_w, tl_h, behavior)

    # Crop is in source pixels and removes from each side, so it both shrinks
    # the image and shifts what remains off-centre.
    vis_w = max(source_w - crop_l - crop_r, 1.0) * fx * zoom_x
    vis_h = max(source_h - crop_t - crop_b, 1.0) * fy * zoom_y
    shift_x = ((crop_l - crop_r) / 2.0) * fx * zoom_x
    shift_y = ((crop_t - crop_b) / 2.0) * fy * zoom_y

    # Tilt is positive-up, so a positive crop from the top moves the visible
    # centre down. Verify this sign if Align Top ever moves clips the wrong way.
    cx = pan + shift_x
    cy = tilt - shift_y

    return {
        "item": item,
        "pan": pan,
        "tilt": tilt,
        "cx": cx,
        "cy": cy,
        "w": vis_w,
        "h": vis_h,
        "left": cx - vis_w / 2.0,
        "right": cx + vis_w / 2.0,
        "top": cy + vis_h / 2.0,       # +Y is up
        "bottom": cy - vis_h / 2.0,
        "name": item.GetName() if hasattr(item, "GetName") else "?",
    }


WRITE_TOLERANCE = 0.5   # Resolve rounds Pan/Tilt slightly
WRITE_RETRIES = 2


def set_verified(rect, key, value):
    """Write a transform property and confirm it actually stuck.

    Resolve sometimes ignores the first write to a timeline item — the clip
    doesn't move and the call still reports success, so pressing the button a
    second time appears to 'fix' it. Rather than trusting the return value we
    write, read back, and retry until the value matches.

    Returns (ok, attempts_used).
    """
    item = rect["item"]
    target = float(value)
    actual = None

    for attempt in range(1, WRITE_RETRIES + 2):
        try:
            item.SetProperty(key, target)
        except Exception as e:
            if attempt > WRITE_RETRIES:
                print(f"  {rect['name']}: {key} failed — {e}")
                return False, attempt
            continue

        try:
            actual = float(item.GetProperty(key))
        except Exception:
            return True, attempt  # can't read back; assume it landed

        if abs(actual - target) <= WRITE_TOLERANCE:
            if attempt > 1:
                print(f"  {rect['name']}: {key} needed {attempt} attempts")
            return True, attempt

    print(f"  {rect['name']}: {key} would not stick "
          f"(wanted {target:.1f}, still {actual if actual is None else round(actual, 1)})")
    return False, WRITE_RETRIES + 1


def apply_pan(rect, new_cx):
    """Set Pan so the visible centre lands on new_cx."""
    return set_verified(rect, "Pan", rect["pan"] + (new_cx - rect["cx"]))


def apply_tilt(rect, new_cy):
    return set_verified(rect, "Tilt", rect["tilt"] + (new_cy - rect["cy"]))


def set_raw(rect, key, value):
    """Write a property value straight through, matching the Inspector."""
    return set_verified(rect, key, value)


def tc_to_frame(tc_str, fps):
    drop_frame = ";" in tc_str
    h, m, s, f = map(int, tc_str.replace(";", ":").split(":"))
    fps_round = round(float(fps))
    total = (h * 3600 + m * 60 + s) * fps_round + f
    if drop_frame:
        drop = 4 if fps_round == 60 else 2
        total_minutes = 60 * h + m
        total -= drop * (total_minutes - total_minutes // 10)
    return total


def frame_to_tc(frame, fps, drop_frame):
    fps_round = round(float(fps))
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
    return f"{h:02d}:{mnt:02d}:{s:02d}{sep}{f:02d}"


def refresh_viewer(timeline):
    """Force Resolve to re-render after transform changes.

    Resolve caches the composited frame and doesn't always redraw when a
    clip's Pan/Tilt is changed via the API — the values are correct but the
    viewer still shows the old position, which looks like the clip was
    skipped. Stepping the playhead one frame and back invalidates that cache.
    """
    try:
        original = timeline.GetCurrentTimecode()
    except Exception:
        return False
    if not original:
        return False
    try:
        fps = timeline.GetSetting("timelineFrameRate") or 24
        drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"
        frame = tc_to_frame(original, fps)
    except Exception:
        return False

    for delta in (1, -1):   # -1 covers sitting on the very last frame
        try:
            probe = frame_to_tc(frame + delta, fps, drop)
            if timeline.SetCurrentTimecode(probe):
                timeline.SetCurrentTimecode(original)
                return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class AlignPanel:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()

        root.title("Align Clips")
        root.configure(bg=BG)
        root.minsize(S(430), S(260))
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
        # Readonly comboboxes render white-on-white without explicit state maps
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
        # Status bar is pinned outside the scroll area so it's always visible
        bottom = tk.Frame(self.root, bg=PANEL2)
        bottom.pack(fill="x", side="bottom")

        # Scrollable body, so shrinking the window doesn't clip the controls
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        self.vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll(_event=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            needed = content.winfo_reqheight() > self.canvas.winfo_height()
            if needed and not self.vsb.winfo_ismapped():
                self.vsb.pack(side="right", fill="y")
            elif not needed and self.vsb.winfo_ismapped():
                self.vsb.pack_forget()

        def fit_width(event):
            self.canvas.itemconfigure(window, width=event.width)
            sync_scroll()

        content.bind("<Configure>", sync_scroll)
        self.canvas.bind("<Configure>", fit_width)
        self.root.bind_all(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        head = tk.Frame(content, bg=BG)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        self.tl_label = tk.Label(head, text="—", bg=BG, fg=FG, anchor="w",
                                 font=FONT(11, "bold"))
        self.tl_label.pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.sel_label = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w",
                                  font=FONT(9))
        self.sel_label.pack(side="left")
        make_button(row, "Refresh", self.refresh).pack(side="right")
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0,
                                 font=FONT(9), cursor="hand2", padx=S(10),
                                 pady=S(5), highlightthickness=0,
                                 command=self.toggle_topmost)
        self.top_btn.pack(side="right", padx=(0, S(6)))
        self._paint_topmost()

        # toggles
        toggles = tk.Frame(content, bg=BG)
        toggles.pack(fill="x", padx=S(14), pady=(S(12), S(2)))

        self.mode = tk.StringVar(value=self.prefs.get("mode", "Edges"))
        self.mode_buttons = self._toggle_row(
            toggles, "Align by", ("Edges", "Centres"), self.set_mode,
            "edges use each clip's real size")
        self._paint_toggles()

        grid = tk.Frame(content, bg=BG)
        grid.pack(fill="x", padx=S(14), pady=(S(10), 0))
        tk.Label(grid, text="HORIZONTAL", bg=BG, fg=SUB,
                 font=FONT(8, "bold"), anchor="w").grid(row=0, column=0,
                                                        columnspan=3, sticky="w",
                                                        pady=(0, S(4)))
        make_button(grid, "Left", lambda: self.align("left")).grid(row=1, column=0, sticky="ew", padx=(0, S(4)))
        make_button(grid, "Centre", lambda: self.align("centreh")).grid(row=1, column=1, sticky="ew", padx=S(4))
        make_button(grid, "Right", lambda: self.align("right")).grid(row=1, column=2, sticky="ew", padx=(S(4), 0))

        tk.Label(grid, text="VERTICAL", bg=BG, fg=SUB,
                 font=FONT(8, "bold"), anchor="w").grid(row=2, column=0,
                                                        columnspan=3, sticky="w",
                                                        pady=(S(12), S(4)))
        make_button(grid, "Top", lambda: self.align("top")).grid(row=3, column=0, sticky="ew", padx=(0, S(4)))
        make_button(grid, "Middle", lambda: self.align("middle")).grid(row=3, column=1, sticky="ew", padx=S(4))
        make_button(grid, "Bottom", lambda: self.align("bottom")).grid(row=3, column=2, sticky="ew", padx=(S(4), 0))

        tk.Label(grid, text="DISTRIBUTE  (3 or more clips)", bg=BG, fg=SUB,
                 font=FONT(8, "bold"), anchor="w").grid(row=4, column=0,
                                                        columnspan=3, sticky="w",
                                                        pady=(S(12), S(4)))
        make_button(grid, "Horizontally", lambda: self.distribute("h"),
                    primary=True).grid(row=5, column=0, columnspan=2, sticky="ew", padx=(0, S(4)))
        make_button(grid, "Vertically", lambda: self.distribute("v"),
                    primary=True).grid(row=5, column=2, sticky="ew", padx=(S(4), 0))
        for col in range(3):
            grid.columnconfigure(col, weight=1)

        # --- move everything together -----------------------------------
        move = tk.Frame(content, bg=BG)
        move.pack(fill="x", padx=S(14), pady=(S(14), 0))
        tk.Label(move, text="MOVE TOGETHER", bg=BG, fg=SUB,
                 font=FONT(8, "bold"), anchor="w").pack(fill="x", pady=(0, S(4)))

        nudge_row = tk.Frame(move, bg=BG)
        nudge_row.pack(fill="x")
        tk.Label(nudge_row, text="Step", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(0, S(5)))
        self.step_var = tk.StringVar(value=str(self.prefs.get("step", 10)))
        tk.Entry(nudge_row, textvariable=self.step_var, width=5, bg=PANEL, fg=FG,
                 relief="flat", insertbackground=FG, font=FONT(9),
                 justify="center", highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", ipady=S(3))
        tk.Label(nudge_row, text="px", bg=BG, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(S(4), S(12)))
        for text, dx, dy in (("←", -1, 0), ("→", 1, 0), ("↑", 0, 1), ("↓", 0, -1)):
            make_button(nudge_row, text,
                        lambda x=dx, y=dy: self.nudge(x, y)).pack(side="left", padx=(0, S(4)))

        set_row = tk.Frame(move, bg=BG)
        set_row.pack(fill="x", pady=(S(8), 0))
        tk.Label(set_row, text="Set  X", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(0, S(5)))
        self.x_var = tk.StringVar()
        tk.Entry(set_row, textvariable=self.x_var, width=7, bg=PANEL, fg=FG,
                 relief="flat", insertbackground=FG, font=FONT(9),
                 justify="center", highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", ipady=S(3))
        tk.Label(set_row, text="Y", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(S(8), S(5)))
        self.y_var = tk.StringVar()
        tk.Entry(set_row, textvariable=self.y_var, width=7, bg=PANEL, fg=FG,
                 relief="flat", insertbackground=FG, font=FONT(9),
                 justify="center", highlightthickness=1,
                 highlightbackground=BORDER).pack(side="left", ipady=S(3))
        make_button(set_row, "Apply", self.set_absolute,
                    primary=True).pack(side="left", padx=(S(10), 0))
        make_button(set_row, "Read", self.read_position).pack(side="left", padx=(S(4), 0))
        tk.Label(move, text="Blank = leave that axis alone. Values match the Inspector.",
                 bg=BG, fg=SUB, font=FONT(8), anchor="w").pack(fill="x", pady=(S(4), S(14)))

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

    def _toggle_row(self, parent, label, options, on_click, hint=None):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, S(5)))
        tk.Label(row, text=label, bg=BG, fg=SUB, font=FONT(9), width=8,
                 anchor="w").pack(side="left", padx=(0, S(6)))
        buttons = {}
        for opt in options:
            b = tk.Button(row, text=opt, relief="flat", bd=0, font=FONT(9),
                          cursor="hand2", padx=S(12), pady=S(5),
                          highlightthickness=0,
                          command=lambda o=opt: on_click(o))
            b.pack(side="left", padx=(0, S(4)))
            buttons[opt] = b
        if hint:
            tk.Label(row, text=hint, bg=BG, fg=SUB,
                     font=FONT(8)).pack(side="left", padx=(S(8), 0))
        return buttons

    def _paint_toggles(self):
        for label, btn in self.mode_buttons.items():
            active = (label == self.mode.get())
            btn.config(bg=ACCENT if active else BTN,
                       fg="#ffffff" if active else FG,
                       activebackground=ACCENT_HOVER if active else BTN_HOVER)

    def set_mode(self, label):
        self.mode.set(label)
        self.prefs["mode"] = label
        save_prefs(self.prefs)
        self._paint_toggles()
        self.say(f"Aligning by {label.lower()}.")

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
        self.say("Window stays on top." if on else "Window no longer on top.")

    def nudge(self, dx, dy):
        """Move every selected clip by the same delta."""
        try:
            step = float(self.step_var.get())
        except ValueError:
            self.say("Step must be a number.", True)
            return
        self.prefs["step"] = self.step_var.get()
        save_prefs(self.prefs)

        rects = self.gather(1)
        if not rects:
            return

        print(f"\nNudge by ({dx * step:+.1f}, {dy * step:+.1f}):")
        changed, retries = 0, 0
        for r in rects:
            print(f"  before  {r['name']}: Pan={r['pan']:.1f} Tilt={r['tilt']:.1f}")
            ok = True
            if dx:
                good, tries = apply_pan(r, r["cx"] + dx * step)
                ok &= good
                retries += tries - 1
            if dy:
                good, tries = apply_tilt(r, r["cy"] + dy * step)
                ok &= good
                retries += tries - 1
            changed += 1 if ok else 0
        redrawn = refresh_viewer(current_timeline())
        self.say(f"Moved {changed} of {len(rects)} clip(s) by "
                 f"{dx * step:+.0f}, {dy * step:+.0f}."
                 + (f"  ({retries} retry needed)" if retries else "")
                 + ("" if redrawn else "  [viewer refresh failed]"))

    def set_absolute(self):
        """Set Pan and/or Tilt outright on every selected clip."""
        xs, ys = self.x_var.get().strip(), self.y_var.get().strip()
        if not xs and not ys:
            self.say("Enter an X and/or Y value first.", True)
            return
        for label, raw in (("X", xs), ("Y", ys)):
            if raw:
                try:
                    float(raw)
                except ValueError:
                    self.say(f"{label} must be a number.", True)
                    return

        rects = self.gather(1)
        if not rects:
            return

        print(f"\nSet position X={xs or '—'} Y={ys or '—'}:")
        changed, retries = 0, 0
        for r in rects:
            print(f"  before  {r['name']}: Pan={r['pan']:.1f} Tilt={r['tilt']:.1f}")
            ok = True
            if xs:
                good, tries = set_raw(r, "Pan", xs)
                ok &= good
                retries += tries - 1
            if ys:
                good, tries = set_raw(r, "Tilt", ys)
                ok &= good
                retries += tries - 1
            changed += 1 if ok else 0
        redrawn = refresh_viewer(current_timeline())
        self.say(f"Set position on {changed} of {len(rects)} clip(s)."
                 + (f"  ({retries} retry needed)" if retries else "")
                 + ("" if redrawn else "  [viewer refresh failed]"))

    def read_position(self):
        """Fill the X/Y fields from the first selected clip."""
        rects = self.gather(1)
        if not rects:
            return
        r = rects[0]
        self.x_var.set(f"{r['pan']:.1f}")
        self.y_var.set(f"{r['tilt']:.1f}")
        self.say(f"Read position from '{r['name']}'.")

    def rebuild(self):
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(430), S(260))
        self._style()
        self._build()
        self.refresh()
        try:
            self.root.attributes("-topmost", bool(self.prefs.get("always_on_top", False)))
        except Exception:
            pass

    def on_scale_change(self, event=None):
        global SCALE
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()
            except Exception:
                pass
        choice = self.scale_var.get()
        self.prefs["ui_scale"] = choice
        save_prefs(self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        self.rebuild()

    def say(self, msg, error=False):
        self.status.config(text=msg, fg="#e07070" if error else SUB)

    def refresh(self):
        timeline = current_timeline()
        if not timeline:
            self.tl_label.config(text="No timeline open")
            self.sel_label.config(text="")
            self.say("Open a timeline to begin.", True)
            return
        self.tl_label.config(text=timeline.GetName())
        clips, skipped = selected_clips()
        self._set_count(len(clips), skipped)
        self.say("Ready." if clips else COMPOUND_HINT, error=not clips)

    def _set_count(self, count, skipped):
        text = f"{count} video clip(s) selected"
        if skipped:
            text += f"   ·   ignoring {skipped} audio/other"
        self.sel_label.config(text=text)

    def gather(self, minimum):
        """Read the live selection and build rectangles for each clip."""
        project = current_project()
        timeline = current_timeline()
        if not project or not timeline:
            self.say("No timeline open.", True)
            return None
        clips, skipped = selected_clips()
        self._set_count(len(clips), skipped)
        if len(clips) < minimum:
            if not clips:
                self.say(COMPOUND_HINT, True)
            else:
                self.say(f"Select at least {minimum} video clip(s) (found {len(clips)}).", True)
            return None
        tl_w, tl_h = timeline_resolution(project)
        self.tl_w, self.tl_h = tl_w, tl_h
        behavior = project.GetSetting("timelineInputResMismatchBehavior")
        return [clip_rect(c, project, tl_w, tl_h, behavior) for c in clips]

    def align(self, how):
        edges = self.mode.get() == "Edges"
        rects = self.gather(2)
        if not rects:
            return
        print(f"\nAlign {how} ({'edges' if edges else 'centres'}, to selection):")
        for r in rects:
            print(f"  before  {r['name']}: Pan={r['pan']:.1f} Tilt={r['tilt']:.1f} "
                  f"size={r['w']:.0f}x{r['h']:.0f}")

        changed, retries = 0, 0

        def do(fn, rect, value):
            ok, tries = fn(rect, value)
            return (1 if ok else 0), (tries - 1)

        # Axis-specific accessors so both directions share one code path.
        if how in ("left", "right", "centreh"):
            size = lambda r: r["w"]
            near = lambda r: r["left"]          # more negative side
            far = lambda r: r["right"]
            centre = lambda r: r["cx"]
            mover = apply_pan
            low_side = (how == "left")
            is_centre = (how == "centreh")
        else:
            size = lambda r: r["h"]
            near = lambda r: r["bottom"]        # +Y is up, so bottom is -ve
            far = lambda r: r["top"]
            centre = lambda r: r["cy"]
            mover = apply_tilt
            low_side = (how == "bottom")
            is_centre = (how == "middle")

        if is_centre:
            # Centre of the selection's own bounds, not the frame's
            if edges:
                mid = (min(near(r) for r in rects) + max(far(r) for r in rects)) / 2.0
            else:
                mid = (min(centre(r) for r in rects) + max(centre(r) for r in rects)) / 2.0
            target_for = lambda r: mid
        elif edges:
            edge = min(near(r) for r in rects) if low_side else max(far(r) for r in rects)
            target_for = (lambda r: edge + size(r) / 2.0) if low_side                 else (lambda r: edge - size(r) / 2.0)
        else:
            pos = min(centre(r) for r in rects) if low_side else max(centre(r) for r in rects)
            target_for = lambda r: pos

        for r in rects:
            c, t = do(mover, r, target_for(r))
            changed += c
            retries += t

        redrawn = refresh_viewer(current_timeline())
        self.say(f"Aligned {changed} of {len(rects)} clip(s) — {how}."
                 + (f"  ({retries} retry needed)" if retries else "")
                 + ("" if redrawn else "  [viewer refresh failed]"))

    def distribute(self, axis):
        rects = self.gather(3)
        if not rects:
            return
        key = "cx" if axis == "h" else "cy"
        ordered = sorted(rects, key=lambda r: r[key])
        mover = apply_pan if axis == "h" else apply_tilt

        print(f"\nDistribute {'horizontally' if axis == 'h' else 'vertically'}:")
        for r in ordered:
            print(f"  before  {r['name']}: Pan={r['pan']:.1f} Tilt={r['tilt']:.1f}")

        # Outermost two stay put; everything between them is evenly spaced
        changed, retries = 0, 0
        first = ordered[0]
        step = (ordered[-1][key] - first[key]) / float(len(ordered) - 1)
        for i, r in enumerate(ordered[1:-1], start=1):
            ok, tries = mover(r, first[key] + step * i)
            changed += 1 if ok else 0
            retries += tries - 1

        redrawn = refresh_viewer(current_timeline())
        self.say(f"Distributed {len(ordered)} clip(s) — moved {changed} inner clip(s)."
                 + (f"  ({retries} retry needed)" if retries else "")
                 + ("" if redrawn else "  [viewer refresh failed]"))

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
app = AlignPanel(root)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
if not prefs.get("always_on_top", False):
    # Surface it once, then let it behave normally unless pinned
    root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
