import ctypes
import json
import os
import time
import traceback
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
RECENT_LIMIT = 8        # parameters remembered per tool type


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
                # 'key' identifies the node across rescans so a row selection
                # survives a refresh. It must key off the CLIP's unique id,
                # not its start frame: stacked titles on V1..V4 share a start
                # frame and often share node names too, and two rows with the
                # same key collide as Treeview ids and blank the table.
                groups.setdefault(reg, []).append({
                    "clip": clip.GetName(),
                    "clip_start": start,
                    "comp": comp,
                    "tool": tool,
                    "tool_name": tool.Name,
                    "key": f"{item_uid(clip)}.{ci}.{tool.Name}",
                })
    return groups, comps


MAX_INPUTS = 400        # hard cap on inputs examined per tool
FALLBACK_PROBES = 20    # GetInput() calls allowed when metadata is inconclusive


STAR = "★  "


def base_of(label):
    """A displayed label with any recently-used star removed."""
    text = str(label or "")
    return text[len(STAR):] if text.startswith(STAR) else text


def make_param(label, input_id, axis, kind):
    """A parameter row. 'base' is the label without any decoration, so the
    recently-used list stays stable when a star is prefixed for display."""
    return {"label": label, "base": label, "id": input_id,
            "axis": axis, "kind": kind}


def input_object(tool, input_id):
    """The Input object behind an input id — needed for expressions, which
    live on the Input, not on the tool.

    This walks GetInputList() and is only ever called on demand (when you set
    or clear an expression), never during a refresh: a heavy plugin can have
    hundreds of inputs and doing this per node per redraw is exactly the kind
    of thing that stalls Resolve.
    """
    try:
        inputs = tool.GetInputList() or {}
    except Exception:
        return None
    for inp in list(inputs.values())[:MAX_INPUTS]:
        try:
            if (inp.GetAttrs() or {}).get("INPS_ID") == input_id:
                return inp
        except Exception:
            continue
    return None


def read_value(tool, param):
    """Current value of a parameter — float for numbers, str for text."""
    if param["kind"] == "text":
        value = tool.GetInput(param["id"])
        return "" if value is None else str(value)
    return get_param(tool, param["id"], param["axis"])


def set_text(tool, input_id, text):
    try:
        tool.SetInput(input_id, str(text))
    except Exception as e:
        print(f"    SetInput({input_id}) failed: {e}")
        return False
    try:
        return str(tool.GetInput(input_id) or "") == str(text)
    except Exception:
        return True


def expand_tokens(template, index, entry):
    """{n} 1-based row number, {i} 0-based, {clip}, {node}."""
    return (str(template)
            .replace("{n}", str(index + 1))
            .replace("{i}", str(index))
            .replace("{clip}", str(entry["clip"]))
            .replace("{node}", str(entry["tool_name"])))


def parameters_of(tool):
    """Editable parameters, as dicts with label / id / axis / kind.

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
        elif dtype == "Text":
            kind = "text"
        elif dtype:
            kind = None          # Image, Mask, Gradient, FontStyle...
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
            params.append(make_param(f"{name}{suffix}", input_id, None, "number"))
        elif kind == "text":
            params.append(make_param(f"{name}{suffix}", input_id, None, "text"))
        elif kind == "point":
            params.append(make_param(f"{name}.X{suffix}", input_id, 0, "number"))
            params.append(make_param(f"{name}.Y{suffix}", input_id, 1, "number"))
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
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._paint_scope())

        scope = tk.Frame(content, bg=BG)
        scope.pack(fill="x", padx=S(14), pady=(S(6), 0))
        self.sel_only_var = tk.BooleanVar(
            value=bool(self.prefs.get("selected_rows_only", True)))
        tk.Checkbutton(scope, text="Only rows selected above", variable=self.sel_only_var,
                       command=self._on_scope_change, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, font=FONT(9),
                       highlightthickness=0).pack(side="left")
        self.scope_label = tk.Label(scope, text="", bg=BG, fg=SUB, font=FONT(8))
        self.scope_label.pack(side="left", padx=(S(8), 0))
        make_button(scope, "Select all",
                    self.select_all_rows).pack(side="right")
        make_button(scope, "Clear selection",
                    lambda: self.tree.selection_remove(self.tree.selection())
                    ).pack(side="right", padx=(0, S(6)))

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

        r3 = tk.Frame(ops, bg=BG)
        r3.pack(fill="x", pady=(S(6), 0))
        tk.Label(r3, text="Text", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        self.text_var = tk.StringVar()
        tk.Entry(r3, textvariable=self.text_var, bg=PANEL, fg=FG, relief="flat",
                 insertbackground=FG, font=FONT(9), highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", fill="x", expand=True, ipady=S(3))
        make_button(r3, "Apply", self.do_text).pack(side="left", padx=(S(8), 0))

        r4 = tk.Frame(ops, bg=BG)
        r4.pack(fill="x", pady=(S(6), 0))
        tk.Label(r4, text="Expression", bg=BG, fg=SUB, font=FONT(9), width=9,
                 anchor="w").pack(side="left")
        self.expr_var = tk.StringVar()
        tk.Entry(r4, textvariable=self.expr_var, bg=PANEL, fg=FG, relief="flat",
                 insertbackground=FG, font=MONO(9), highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", fill="x", expand=True, ipady=S(3))
        make_button(r4, "Set", self.do_expression).pack(side="left", padx=(S(8), 0))
        make_button(r4, "Clear", self.clear_expression).pack(side="left", padx=(S(6), 0))

        tk.Label(content, text="The numbered table is the exact operation order. "
                               "Ordering by Value means Spread runs from the lowest "
                               "current value to the highest, so selection order never matters. "
                               "Recently used parameters are starred and float to the top of "
                               "the list, per tool type.\n"
                               "Text and Expression accept  {n}  (row number),  {i}  "
                               "(from 0),  {clip}  and  {node}  — so each node can get a "
                               "different value. An expression is Fusion's own syntax, e.g. "
                               "time/24  or  Transform1.Angle*2.",
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
        """Never let a scan fail silently - a half-updated panel looks exactly
        like 'it stopped seeing my clips', which is impossible to diagnose."""
        try:
            self._rescan()
        except Exception as e:
            traceback.print_exc()
            self.say(f"Scan failed: {e}", True)

    def _rescan(self):
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

    # -- recently used ----------------------------------------------------
    def recent_for(self, reg):
        """Most-recently-used parameter names for this tool type."""
        store = self.prefs.get("recent_params") or {}
        got = store.get(reg or "", [])
        return got if isinstance(got, list) else []

    def remember_param(self, param):
        """Called when a parameter is actually EDITED, not merely looked at —
        'recently used' is only useful if it tracks real work."""
        reg = self.current_group()
        if not reg or not param:
            return
        store = self.prefs.setdefault("recent_params", {})
        recent = [b for b in self.recent_for(reg) if b != param["base"]]
        store[reg] = ([param["base"]] + recent)[:RECENT_LIMIT]
        save_prefs(self.prefs)
        self._reorder_params()

    def order_params(self, params, reg):
        """Recently used first, starred; everything else in the tool's own order."""
        rank = {base: i for i, base in enumerate(self.recent_for(reg))}
        for p in params:
            p["label"] = p["base"]      # clear stars from a previous ordering
        hot = sorted((p for p in params if p["base"] in rank),
                     key=lambda p: rank[p["base"]])
        rest = [p for p in params if p["base"] not in rank]
        for p in hot:
            p["label"] = STAR + p["base"]
        return hot + rest

    def _reorder_params(self):
        """Re-apply the recent-first ordering in place, keeping the current
        selection selected under its new (possibly starred) label."""
        current = self.current_param()
        self.params = self.order_params(self.params, self.current_group())
        if current:
            self.param_var.set(current["label"])
        self.apply_param_filter()

    def on_group_change(self):
        reg = self.current_group()
        self.entries = self.groups.get(reg, [])
        if reg:
            self.prefs["last_group"] = reg
            save_prefs(self.prefs)
        params = parameters_of(self.entries[0]["tool"]) if self.entries else []
        self.params = self.order_params(params, reg)
        self.param_var.set(self._param_to_keep(reg))
        self.apply_param_filter()
        self.refresh_values()

    def _param_to_keep(self, reg):
        """Which parameter should be selected after a (re)scan.

        Whatever is on screen wins. Auto-rescan fires every time you click
        back from Resolve, so resetting to the top of the list here means the
        parameter you just picked is taken away from under you.

        Matching is on the base name, because starring a parameter changes its
        displayed label.
        """
        by_base = {}
        for p in self.params:
            by_base.setdefault(p["base"], p["label"])
        if not by_base:
            return ""
        for candidate in (base_of(self.param_var.get()),
                          (self.prefs.get("last_param_by_group") or {}).get(reg or "")):
            if candidate in by_base:
                return by_base[candidate]
        return self.params[0]["label"]

    def apply_param_filter(self):
        """Narrow the parameter dropdown. Matches name, input id and axis, so
        typing 'center' or 'blur' cuts a long list down fast."""
        query = self.filter_var.get().strip().lower() if hasattr(self, "filter_var") else ""
        labels = [p["label"] for p in self.params]
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
            if p["label"] == label:
                return p
        return None

    def on_order_change(self, _event=None):
        self.prefs["order_by"] = self.order_var.get()
        self.prefs["reverse"] = bool(self.reverse_var.get())
        save_prefs(self.prefs)
        self.refresh_values()

    def refresh_values(self):
        try:
            self._refresh_values()
        except Exception as e:
            traceback.print_exc()
            self.say(f"Could not read values: {e}", True)

    def _refresh_values(self):
        """Read every value and build self.rows in the exact order operations
        will use — the table shows that order, numbered, so nothing is guesswork."""
        keep = set(self.tree.selection()) if self.tree.get_children() else set()
        self.tree.delete(*self.tree.get_children())
        self.rows = []
        param = self.current_param()
        if not param:
            self._paint_scope()
            return
        self.prefs.setdefault("last_param_by_group", {})[
            self.current_group() or ""] = param["base"]
        save_prefs(self.prefs)

        rows = []
        for entry in self.entries:
            try:
                value = read_value(entry["tool"], param)
            except Exception:
                value = None
            rows.append({"entry": entry, "value": value})

        # Unreadable values sort last and are never used as spread endpoints
        is_text = param["kind"] == "text"
        blank = "" if is_text else 0.0
        order = self.order_var.get()
        if order == "Timeline position":
            rows.sort(key=lambda r: (r["value"] is None, r["entry"]["clip_start"]))
        elif order == "Node name":
            rows.sort(key=lambda r: (r["value"] is None, r["entry"]["tool_name"].lower()))
        elif is_text:
            rows.sort(key=lambda r: (r["value"] is None,
                                     str(r["value"] if r["value"] is not None else "").lower()))
        else:  # Value
            rows.sort(key=lambda r: (r["value"] is None,
                                     r["value"] if r["value"] is not None else blank))
        if self.reverse_var.get():
            rows = list(reversed(rows))

        self.rows = rows
        seen = set()
        for i, row in enumerate(rows, start=1):
            value = row["value"]
            # Stable per-node iid, so the row selection survives a refresh
            # rather than silently widening the next operation.
            # Belt and braces: a duplicate id would make insert() throw and
            # leave the table half-drawn, so never let one through.
            iid = f"n{row['entry']['key']}"
            while iid in seen:
                iid += "_"
            seen.add(iid)
            row["iid"] = iid
            if value is None:
                shown = "—"
            elif is_text:
                shown = str(value).replace("\n", " ⏎ ")[:60] or "(empty)"
            else:
                shown = f"{value:.4g}"
            self.tree.insert("", "end", iid=row["iid"], values=(
                i, row["entry"]["clip"], row["entry"]["tool_name"], shown))

        restore = [r["iid"] for r in rows if r["iid"] in keep]
        if restore:
            self.tree.selection_set(restore)

        if is_text:
            self.say(f"{len(rows)} node(s) — {param['base']}   (text — use the Text row)")
        else:
            usable = [r for r in rows if r["value"] is not None]
            if usable:
                lo, hi = usable[0]["value"], usable[-1]["value"]
                self.say(f"{len(rows)} node(s) — {param['base']}   "
                         f"ends: {lo:.4g} → {hi:.4g}")
            else:
                self.say(f"{len(rows)} node(s) — {param['base']}")
        self._paint_scope()

    # -- which rows an operation touches ----------------------------------
    def _on_scope_change(self):
        self.prefs["selected_rows_only"] = bool(self.sel_only_var.get())
        save_prefs(self.prefs)
        self._paint_scope()

    def select_all_rows(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids)

    def _paint_scope(self):
        total = len(getattr(self, "rows", []))
        n = len(self._scoped(getattr(self, "rows", [])))
        if not total:
            text = ""
        elif n == total:
            text = f"acting on all {total} node(s)"
        else:
            text = f"acting on {n} of {total} node(s)"
        try:
            self.scope_label.config(text=text)
        except Exception:
            pass

    def _scoped(self, rows):
        """Narrow rows to the table selection.

        A selection of nothing means 'everything' — otherwise the panel would
        sit there refusing to do anything until you clicked a row.
        """
        if not rows or not self.sel_only_var.get():
            return rows
        chosen = set(self.tree.selection())
        if not chosen:
            return rows
        return [r for r in rows if r.get("iid") in chosen]

    # -- operations -------------------------------------------------------
    def _prepared(self, kind="number"):
        """The parameter and the rows an operation should touch.

        Rows are narrowed to the table selection here, so every operation
        respects it without having to remember to ask.
        """
        param = self.current_param()
        if not param:
            self.say("Pick a tool and parameter first.", True)
            return None, None
        if kind == "number" and param["kind"] == "text":
            self.say(f"{param['base']} is a text parameter — use the Text row "
                     "or an expression.", True)
            return None, None
        if kind == "text" and param["kind"] != "text":
            self.say(f"{param['base']} is not a text parameter.", True)
            return None, None
        rows = [r for r in self._scoped(getattr(self, "rows", []))
                if r["value"] is not None]
        if not rows:
            self.say("Nothing to edit — rescan with clips selected.", True)
            return None, None
        return param, rows

    def _header(self, label, rows):
        scope = "selected " if len(rows) != len(getattr(self, "rows", [])) else ""
        print(f"\n{label} — on {len(rows)} {scope}node(s), "
              f"ordered by {self.order_var.get().lower()}"
              f"{' (reversed)' if self.reverse_var.get() else ''}:")

    def _apply(self, rows, values, label):
        """values: list matching the given rows, which are in display order."""
        param = self.current_param()
        if not param:
            return
        input_id, axis = param["id"], param["axis"]
        self._header(f"{label} — {param['base']}", rows)
        ok_count = 0
        for i, (row, new_value) in enumerate(zip(rows, values), start=1):
            entry = row["entry"]
            before = row["value"]
            good = set_param(entry["tool"], input_id, axis, new_value)
            ok_count += 1 if good else 0
            print(f"  {i:>2}. {entry['clip'][:26]:28} {entry['tool_name'][:16]:18} "
                  f"{before:>10.4g} -> {new_value:<10.4g} {'' if good else '(FAILED)'}")
        self.remember_param(param)
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

    def do_text(self):
        param, rows = self._prepared("text")
        if not param:
            return
        template = self.text_var.get()
        self._header(f"Set text — {param['base']}", rows)
        ok_count = 0
        for i, row in enumerate(rows):
            entry = row["entry"]
            new_text = expand_tokens(template, i, entry)
            good = set_text(entry["tool"], param["id"], new_text)
            ok_count += 1 if good else 0
            print(f"  {i + 1:>2}. {entry['clip'][:26]:28} "
                  f"{entry['tool_name'][:16]:18} -> {new_text[:40]!r} "
                  f"{'' if good else '(FAILED)'}")
        self.remember_param(param)
        nudge_playhead()
        self.refresh_values()
        self.say(f"Set text: {ok_count} of {len(rows)} node(s) updated.")

    def _write_expression(self, expr, label):
        """Expressions live on the Input object, not the tool.

        For a point parameter the expression drives the WHOLE point, not the
        .X / .Y row you happen to have selected — Fusion has no per-axis
        expression — so it has to be written as Point(x, y).
        """
        param = self.current_param()
        if not param:
            self.say("Pick a tool and parameter first.", True)
            return
        rows = self._scoped(getattr(self, "rows", []))
        if not rows:
            self.say("Nothing to edit — rescan with clips selected.", True)
            return

        self._header(f"{label} — {param['base']}", rows)
        ok_count = 0
        for i, row in enumerate(rows):
            entry = row["entry"]
            inp = entry.get("input_" + str(param["id"]))
            if inp is None:
                inp = input_object(entry["tool"], param["id"])
                entry["input_" + str(param["id"])] = inp
            if inp is None:
                print(f"  {i + 1:>2}. {entry['tool_name'][:16]:18} input not found")
                continue
            text = expand_tokens(expr, i, entry) if expr else None
            try:
                inp.SetExpression(text)
                ok_count += 1
            except Exception as e:
                print(f"  {i + 1:>2}. {entry['tool_name'][:16]:18} failed: {e}")
                continue
            print(f"  {i + 1:>2}. {entry['clip'][:26]:28} "
                  f"{entry['tool_name'][:16]:18} = {text if text else '(cleared)'}")
        self.remember_param(param)
        nudge_playhead()
        self.refresh_values()
        self.say(f"{label}: {ok_count} of {len(rows)} node(s) updated."
                 + ("  Expression drives the whole point, not just this axis."
                    if param["axis"] is not None and expr else ""))

    def do_expression(self):
        expr = self.expr_var.get().strip()
        if not expr:
            self.say("Type an expression first, or press Clear to remove one.", True)
            return
        self._write_expression(expr, "Set expression")

    def clear_expression(self):
        self._write_expression(None, "Clear expression")

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
