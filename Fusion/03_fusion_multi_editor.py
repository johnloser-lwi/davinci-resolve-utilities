import ctypes
import json
import os
import time
import tkinter as tk
from tkinter import ttk

# Fusion Multi Editor — edit one parameter across many clips' Fusion comps at
# once, including spreading values evenly across them.
#
# Select several timeline clips that have Fusion compositions, pick a tool type
# (e.g. Transform x6) and a parameter (e.g. Center.X), then Set / Offset /
# Spread across all of them. Clips are ordered by their timeline position.
#
# Note: only Fusion parameters are reachable. Edit-page OFX/ResolveFX applied in
# the Inspector expose nothing to scripting — apply an effect inside a Fusion
# comp if you want to drive it from here.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\fusion_multi_editor_prefs.json")

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
SELECTION_PASSES = 3


def S(n):
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    pts = max(6, int(round(size * SCALE)))
    return ("Segoe UI", pts, weight) if weight else ("Segoe UI", pts)


def MONO(size):
    return ("Consolas", max(6, int(round(size * SCALE))))


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
                    padx=S(10), pady=S(6), font=FONT(9), cursor="hand2",
                    activebackground=hover, activeforeground=FG,
                    highlightthickness=0)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


# --------------------------------------------------------------------------
# Resolve / Fusion bridge
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


def current_timeline():
    try:
        return projectManager.GetCurrentProject().GetCurrentTimeline()
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


def selected_clips():
    """Video timeline items, ordered by timeline position.

    GetSelectedClips() intermittently returns only part of the selection, so
    poll it a few times and union by unique id.
    """
    timeline = current_timeline()
    if not timeline:
        return []
    found = {}
    for _ in range(SELECTION_PASSES):
        try:
            sel = timeline.GetSelectedClips()
        except Exception as e:
            print(f"GetSelectedClips failed: {e}")
            return []
        if not sel:
            continue
        items = list(sel.values()) if isinstance(sel, dict) else list(sel)
        for it in items:
            found.setdefault(item_uid(it), it)

    out = []
    for it in found.values():
        try:
            info = it.GetTrackTypeAndIndex()
            if isinstance(info, dict):
                info = [info.get(1), info.get(2)]
            if info and str(info[0]).lower() != "video":
                continue
        except Exception:
            pass
        out.append(it)

    def start_of(it):
        try:
            return it.GetStart()
        except Exception:
            return 0

    out.sort(key=start_of)
    return out


def point_xy(value):
    """Fusion point values arrive as a 1-indexed table or a sequence."""
    if isinstance(value, dict):
        return float(value.get(1, value.get(1.0, 0))), float(value.get(2, value.get(2.0, 0)))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    raise TypeError("not a point")


def classify(value):
    """'number', 'point', or None for things we can't edit numerically."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return "number"
    try:
        point_xy(value)
        return "point"
    except Exception:
        return None


def get_param(tool, input_id, axis):
    value = tool.GetInput(input_id)
    if axis is None:
        return float(value)
    return point_xy(value)[axis]


def set_param(tool, input_id, axis, new_value):
    """Write a parameter. Returns True if the readback matches."""
    try:
        if axis is None:
            tool.SetInput(input_id, float(new_value))
        else:
            x, y = point_xy(tool.GetInput(input_id))
            if axis == 0:
                x = float(new_value)
            else:
                y = float(new_value)
            try:
                tool.SetInput(input_id, {1: x, 2: y})
            except Exception:
                tool.SetInput(input_id, [x, y])
    except Exception as e:
        print(f"    SetInput({input_id}) failed: {e}")
        return False
    try:
        return abs(get_param(tool, input_id, axis) - float(new_value)) < 1e-4
    except Exception:
        return True  # can't verify, assume it landed


def scan(clips):
    """Build {tool_type: [entry, ...]} across the clips' Fusion comps."""
    groups = {}
    comps = 0
    for clip in clips:
        try:
            count = clip.GetFusionCompCount()
        except Exception:
            count = 0
        for ci in range(1, (count or 0) + 1):
            try:
                comp = clip.GetFusionCompByIndex(ci)
            except Exception:
                continue
            if not comp:
                continue
            comps += 1
            try:
                tools = comp.GetToolList(False) or {}
            except Exception:
                continue
            for tool in tools.values():
                try:
                    reg = tool.GetAttrs()["TOOLS_RegID"]
                except Exception:
                    continue
                try:
                    start = clip.GetStart()
                except Exception:
                    start = 0
                groups.setdefault(reg, []).append({
                    "clip": clip.GetName(),
                    "clip_start": start,
                    "comp": comp,
                    "tool": tool,
                    "tool_name": tool.Name,
                })
    return groups, comps


MAX_INPUTS = 400        # hard cap on inputs examined per tool
FALLBACK_PROBES = 20    # GetInput() calls allowed when metadata is inconclusive


def parameters_of(tool):
    """Editable numeric/point parameters, as (label, input_id, axis).

    Classification comes from each input's METADATA, not by reading its value.
    Calling GetInput() on every input of a heavy OFX plugin is slow enough to
    hang Resolve, and on some plugins it also fails outright — which made the
    parameter list come back empty. GetAttrs() is cheap and doesn't evaluate
    the parameter, so it is used first and value reads are strictly budgeted.

    Display names repeat within a tool (several inputs can both be called
    "Size"), so the unique input ID is shown alongside.
    """
    params = []
    probes_left = FALLBACK_PROBES
    try:
        inputs = tool.GetInputList() or {}
    except Exception:
        return params

    for inp in list(inputs.values())[:MAX_INPUTS]:
        try:
            attrs = inp.GetAttrs() or {}
        except Exception:
            continue
        input_id = attrs.get("INPS_ID")
        if not input_id:
            continue
        name = attrs.get("INPS_Name") or input_id
        dtype = str(attrs.get("INPS_DataType") or "")
        control = str(attrs.get("INPID_InputControl") or "")

        if dtype == "Point" or control == "PointControl":
            kind = "point"
        elif dtype == "Number":
            kind = "number"
        elif dtype:
            kind = None          # Text, Image, Mask, Gradient, FontStyle...
        elif probes_left > 0:
            # No usable metadata — fall back to reading the value, but only
            # for a bounded number of inputs so this can never run away.
            probes_left -= 1
            try:
                kind = classify(tool.GetInput(input_id))
            except Exception:
                kind = None
        else:
            kind = None

        suffix = "" if str(name) == str(input_id) else f"   ·   {input_id}"
        if kind == "number":
            params.append((f"{name}{suffix}", input_id, None))
        elif kind == "point":
            params.append((f"{name}.X{suffix}", input_id, 0))
            params.append((f"{name}.Y{suffix}", input_id, 1))
    return params


def nudge_playhead():
    """Resolve caches the composited frame; step a frame to force a redraw."""
    timeline = current_timeline()
    if not timeline:
        return
    try:
        tc = timeline.GetCurrentTimecode()
        fps = round(float(timeline.GetSetting("timelineFrameRate") or 24))
        drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"
        sep = ";" if drop else ":"
        h, m, s, f = map(int, tc.replace(";", ":").split(":"))
        total = (h * 3600 + m * 60 + s) * fps + f
        if drop:
            d = 4 if fps == 60 else 2
            tm = 60 * h + m
            total -= d * (tm - tm // 10)
        nxt = total + 1
        if drop:
            d = 4 if fps == 60 else 2
            fpm, fp10 = fps * 60 - d, fps * 600 - d * 9
            q, rem = divmod(nxt, fp10)
            nxt += d * 9 * q + (d * ((rem - d) // fpm) if rem >= d else 0)
        probe = (f"{(nxt // (fps * 3600)) % 24:02d}:{(nxt // (fps * 60)) % 60:02d}:"
                 f"{(nxt // fps) % 60:02d}{sep}{nxt % fps:02d}")
        timeline.SetCurrentTimecode(probe)
        timeline.SetCurrentTimecode(tc)
    except Exception:
        pass


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class MultiEditor:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.groups = {}
        self.entries = []
        self.params = []

        root.title("Fusion Multi Editor")
        root.configure(bg=BG)
        root.minsize(S(560), S(300))
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

        # Rescan when the window is re-focused, so changing the timeline
        # selection and clicking back here picks it up automatically.
        self._has_focus = True
        self._last_auto_scan = 0.0
        root.bind("<FocusIn>", self._on_focus_in)
        root.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_out(self, _event=None):
        # FocusOut also fires moving between widgets inside the window, so
        # confirm focus really left the app before arming the next rescan.
        self.root.after(150, self._confirm_focus_lost)

    def _confirm_focus_lost(self):
        try:
            if not self.root.focus_displayof():
                self._has_focus = False
        except Exception:
            self._has_focus = False

    def _on_focus_in(self, _event=None):
        if self._has_focus:
            return                      # internal widget focus, not a return to the app
        self._has_focus = True
        if not getattr(self, "auto_var", None) or not self.auto_var.get():
            return
        now = time.time()
        if now - self._last_auto_scan < 0.4:   # throttle rapid focus flapping
            return
        self._last_auto_scan = now
        self.rescan()

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

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        self.vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window((0, 0), window=content, anchor="nw")

        def sync(_e=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            need = content.winfo_reqheight() > self.canvas.winfo_height()
            if need and not self.vsb.winfo_ismapped():
                self.vsb.pack(side="right", fill="y")
            elif not need and self.vsb.winfo_ismapped():
                self.vsb.pack_forget()

        content.bind("<Configure>", sync)
        self.canvas.bind("<Configure>",
                         lambda e: (self.canvas.itemconfigure(window, width=e.width), sync()))
        self.root.bind_all("<MouseWheel>",
                           lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

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
        self.auto_var = tk.BooleanVar(value=bool(self.prefs.get("auto_rescan", True)))
        tk.Checkbutton(row, text="Auto", variable=self.auto_var,
                       command=self._save_auto, bg=BG, fg=SUB, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, font=FONT(8),
                       highlightthickness=0).pack(side="right", padx=(0, S(4)))
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0, font=FONT(9),
                                 cursor="hand2", padx=S(10), pady=S(5),
                                 highlightthickness=0, command=self.toggle_topmost)
        self.top_btn.pack(side="right", padx=(0, S(6)))
        self._paint_topmost()

        pick = tk.Frame(content, bg=BG)
        pick.pack(fill="x", padx=S(14), pady=(S(10), 0))
        tk.Label(pick, text="Tool", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").grid(row=0, column=0, sticky="w", pady=(0, S(4)))
        self.group_var = tk.StringVar()
        self.group_box = ttk.Combobox(pick, textvariable=self.group_var,
                                      state="readonly", font=FONT(9))
        self.group_box.grid(row=0, column=1, sticky="ew", pady=(0, S(4)))
        self.group_box.bind("<<ComboboxSelected>>", lambda e: self.on_group_change())

        tk.Label(pick, text="Parameter", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").grid(row=1, column=0, sticky="w")
        self.param_var = tk.StringVar()
        self.param_box = ttk.Combobox(pick, textvariable=self.param_var,
                                      state="readonly", font=FONT(9))
        self.param_box.grid(row=1, column=1, sticky="ew")
        self.param_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_values())

        tk.Label(pick, text="Find", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").grid(row=2, column=0, sticky="w", pady=(S(4), 0))
        find_row = tk.Frame(pick, bg=BG)
        find_row.grid(row=2, column=1, sticky="ew", pady=(S(4), 0))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.apply_param_filter())
        tk.Entry(find_row, textvariable=self.filter_var, bg=PANEL, fg=FG,
                 relief="flat", insertbackground=FG, font=FONT(9),
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", fill="x", expand=True, ipady=S(3))
        self.filter_count = tk.Label(find_row, text="", bg=BG, fg=SUB, font=FONT(8))
        self.filter_count.pack(side="left", padx=(S(8), 0))
        make_button(find_row, "Clear", lambda: self.filter_var.set("")).pack(side="left", padx=(S(6), 0))

        tk.Label(pick, text="Order by", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").grid(row=3, column=0, sticky="w", pady=(S(4), 0))
        order_row = tk.Frame(pick, bg=BG)
        order_row.grid(row=3, column=1, sticky="ew", pady=(S(4), 0))
        self.order_var = tk.StringVar(value=self.prefs.get("order_by", "Value"))
        order_box = ttk.Combobox(order_row, textvariable=self.order_var,
                                 values=["Value", "Timeline position", "Node name"],
                                 state="readonly", width=18, font=FONT(9))
        order_box.pack(side="left")
        order_box.bind("<<ComboboxSelected>>", lambda e: self.on_order_change(e))
        self.reverse_var = tk.BooleanVar(value=bool(self.prefs.get("reverse", False)))
        tk.Checkbutton(order_row, text="Reverse", variable=self.reverse_var,
                       command=self.on_order_change, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, font=FONT(9),
                       highlightthickness=0).pack(side="left", padx=(S(10), 0))
        pick.columnconfigure(1, weight=1)

        table = tk.Frame(content, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14), pady=(S(10), 0))
        self.tree = ttk.Treeview(table, columns=("idx", "clip", "tool", "value"),
                                 show="headings", height=7)
        for col, label, width in (("idx", "#", 34), ("clip", "CLIP", 190),
                                  ("tool", "NODE", 130), ("value", "VALUE", 100)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=S(width), anchor="w")
        self.tree.pack(fill="both", expand=True)

        ops = tk.Frame(content, bg=BG)
        ops.pack(fill="x", padx=S(14), pady=(S(12), 0))

        r1 = tk.Frame(ops, bg=BG)
        r1.pack(fill="x", pady=(0, S(6)))
        tk.Label(r1, text="Set all", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        self.set_var = tk.StringVar()
        self._entry(r1, self.set_var, 9).pack(side="left")
        make_button(r1, "Apply", self.do_set).pack(side="left", padx=(S(8), 0))
        tk.Label(r1, text="Offset by", bg=BG, fg=SUB, font=FONT(9),
                 anchor="w").pack(side="left", padx=(S(16), S(6)))
        self.off_var = tk.StringVar()
        self._entry(r1, self.off_var, 9).pack(side="left")
        make_button(r1, "Apply", self.do_offset).pack(side="left", padx=(S(8), 0))

        r2 = tk.Frame(ops, bg=BG)
        r2.pack(fill="x")
        tk.Label(r2, text="Spread", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        make_button(r2, "Between current ends", lambda: self.do_spread(False),
                    primary=True).pack(side="left")
        tk.Label(r2, text="or  From", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(S(12), S(5)))
        self.from_var = tk.StringVar()
        self._entry(r2, self.from_var, 7).pack(side="left")
        tk.Label(r2, text="To", bg=BG, fg=SUB,
                 font=FONT(9)).pack(side="left", padx=(S(6), S(5)))
        self.to_var = tk.StringVar()
        self._entry(r2, self.to_var, 7).pack(side="left")
        make_button(r2, "Spread", lambda: self.do_spread(True)).pack(side="left", padx=(S(8), 0))

        tk.Label(content, text="The numbered table is the exact operation order. "
                               "Ordering by Value means Spread runs from the lowest "
                               "current value to the highest, so selection order never matters.",
                 bg=BG, fg=SUB, font=FONT(8), anchor="w", justify="left",
                 wraplength=S(520)).pack(fill="x", padx=S(14), pady=(S(8), S(14)))

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

    def _entry(self, parent, var, width):
        return tk.Entry(parent, textvariable=var, width=width, bg=PANEL, fg=FG,
                        relief="flat", insertbackground=FG, font=FONT(9),
                        justify="center", highlightthickness=1,
                        highlightbackground=BORDER)

    def say(self, msg, error=False):
        self.status.config(text=msg, fg="#e07070" if error else SUB)

    def _save_auto(self):
        self.prefs["auto_rescan"] = bool(self.auto_var.get())
        save_prefs(self.prefs)

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
        self.root.minsize(S(560), S(300))
        self._style()
        self._build()
        self.rescan()
        try:
            self.root.attributes("-topmost", bool(self.prefs.get("always_on_top", False)))
        except Exception:
            pass

    # -- scanning ---------------------------------------------------------
    def rescan(self):
        timeline = current_timeline()
        if not timeline:
            self.tl_label.config(text="No timeline open")
            self.say("Open a timeline to begin.", True)
            return
        self.tl_label.config(text=timeline.GetName())

        clips = selected_clips()
        self.groups, comps = scan(clips)
        total_tools = sum(len(v) for v in self.groups.values())
        self.info.config(text=f"{len(clips)} clip(s) · {comps} comp(s) · {total_tools} node(s)")

        if not self.groups:
            self.group_box["values"] = []
            self.group_var.set("")
            self.param_box["values"] = []
            self.param_var.set("")
            self.tree.delete(*self.tree.get_children())
            if not clips:
                self.say("Select timeline clips that have Fusion comps.", True)
            else:
                self.say("None of the selected clips have a Fusion composition.", True)
            return

        labels = [f"{reg}   ×{len(items)}" for reg, items in
                  sorted(self.groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
        self.group_box["values"] = labels
        remembered = self.prefs.get("last_group")
        match = next((l for l in labels if l.split()[0] == remembered), None)
        self.group_var.set(match or labels[0])
        self.on_group_change()

    def current_group(self):
        label = self.group_var.get()
        return label.split()[0] if label else None

    def on_group_change(self):
        reg = self.current_group()
        self.entries = self.groups.get(reg, [])
        if reg:
            self.prefs["last_group"] = reg
            save_prefs(self.prefs)
        self.params = parameters_of(self.entries[0]["tool"]) if self.entries else []
        labels = [p[0] for p in self.params]
        if labels:
            remembered = self.prefs.get("last_param")
            self.param_var.set(remembered if remembered in labels else labels[0])
        else:
            self.param_var.set("")
        self.apply_param_filter()
        self.refresh_values()

    def apply_param_filter(self):
        """Narrow the parameter dropdown. Matches name, input id and axis, so
        typing 'center' or 'blur' cuts a long list down fast."""
        query = self.filter_var.get().strip().lower() if hasattr(self, "filter_var") else ""
        labels = [p[0] for p in self.params]
        shown = [l for l in labels if query in l.lower()] if query else labels
        self.param_box["values"] = shown
        total = len(labels)
        if not total:
            self.filter_count.config(text="")
        elif query:
            self.filter_count.config(text=f"{len(shown)}/{total}")
        else:
            self.filter_count.config(text=f"{total}")

    def current_param(self):
        label = self.param_var.get()
        for p in self.params:
            if p[0] == label:
                return p
        return None

    def on_order_change(self, _event=None):
        self.prefs["order_by"] = self.order_var.get()
        self.prefs["reverse"] = bool(self.reverse_var.get())
        save_prefs(self.prefs)
        self.refresh_values()

    def refresh_values(self):
        """Read every value and build self.rows in the exact order operations
        will use — the table shows that order, numbered, so nothing is guesswork."""
        self.tree.delete(*self.tree.get_children())
        self.rows = []
        param = self.current_param()
        if not param:
            return
        self.prefs["last_param"] = param[0]
        save_prefs(self.prefs)
        _, input_id, axis = param

        rows = []
        for entry in self.entries:
            try:
                value = get_param(entry["tool"], input_id, axis)
            except Exception:
                value = None
            rows.append({"entry": entry, "value": value})

        # Unreadable values sort last and are never used as spread endpoints
        order = self.order_var.get()
        if order == "Timeline position":
            rows.sort(key=lambda r: (r["value"] is None, r["entry"]["clip_start"]))
        elif order == "Node name":
            rows.sort(key=lambda r: (r["value"] is None, r["entry"]["tool_name"].lower()))
        else:  # Value
            rows.sort(key=lambda r: (r["value"] is None,
                                     r["value"] if r["value"] is not None else 0.0))
        if self.reverse_var.get():
            rows = list(reversed(rows))

        self.rows = rows
        for i, row in enumerate(rows, start=1):
            value = row["value"]
            self.tree.insert("", "end", values=(
                i, row["entry"]["clip"], row["entry"]["tool_name"],
                "—" if value is None else f"{value:.4g}"))

        usable = [r for r in rows if r["value"] is not None]
        if usable:
            lo, hi = usable[0]["value"], usable[-1]["value"]
            self.say(f"{len(rows)} node(s) — {param[0]}   "
                     f"ends: {lo:.4g} → {hi:.4g}")
        else:
            self.say(f"{len(rows)} node(s) — {param[0]}")

    # -- operations -------------------------------------------------------
    def _prepared(self):
        param = self.current_param()
        if not param:
            self.say("Pick a tool and parameter first.", True)
            return None, None
        rows = [r for r in getattr(self, "rows", []) if r["value"] is not None]
        if not rows:
            self.say("Nothing to edit — rescan with clips selected.", True)
            return None, None
        return param, rows

    def _apply(self, rows, values, label):
        """values: list matching the given rows, which are in display order."""
        param = self.current_param()
        if not param:
            return
        _, input_id, axis = param
        print(f"\n{label} — {param[0]} on {len(rows)} node(s), "
              f"ordered by {self.order_var.get().lower()}"
              f"{' (reversed)' if self.reverse_var.get() else ''}:")
        ok_count = 0
        for i, (row, new_value) in enumerate(zip(rows, values), start=1):
            entry = row["entry"]
            before = row["value"]
            good = set_param(entry["tool"], input_id, axis, new_value)
            ok_count += 1 if good else 0
            print(f"  {i:>2}. {entry['clip'][:26]:28} {entry['tool_name'][:16]:18} "
                  f"{before:>10.4g} -> {new_value:<10.4g} {'' if good else '(FAILED)'}")
        nudge_playhead()
        self.refresh_values()
        self.say(f"{label}: {ok_count} of {len(rows)} node(s) updated.")

    def _number(self, var, name):
        try:
            return float(var.get().strip())
        except ValueError:
            self.say(f"{name} must be a number.", True)
            return None

    def do_set(self):
        value = self._number(self.set_var, "Set all")
        if value is None:
            return
        param, rows = self._prepared()
        if param:
            self._apply(rows, [value] * len(rows), "Set all")

    def do_offset(self):
        delta = self._number(self.off_var, "Offset")
        if delta is None:
            return
        param, rows = self._prepared()
        if not param:
            return
        self._apply(rows, [r["value"] + delta for r in rows], f"Offset by {delta:g}")

    def do_spread(self, typed):
        param, rows = self._prepared()
        if not param:
            return
        if len(rows) < 2:
            self.say("Spread needs at least 2 nodes with readable values.", True)
            return

        if typed:
            start = self._number(self.from_var, "From")
            end = self._number(self.to_var, "To")
            if start is None or end is None:
                return
            label = f"Spread {start:g} → {end:g}"
        else:
            # Rows are already in display order, so with "Order by: Value"
            # these ends are simply the lowest and highest current values.
            start, end = rows[0]["value"], rows[-1]["value"]
            if abs(end - start) < 1e-9:
                self.say("All values are identical — nothing to spread between. "
                         "Use From/To instead.", True)
                return
            label = f"Spread {start:.4g} → {end:.4g} (current ends)"

        step = (end - start) / float(len(rows) - 1)
        self._apply(rows, [start + step * i for i in range(len(rows))], label)

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
app = MultiEditor(root)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
if not prefs.get("always_on_top", False):
    root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
