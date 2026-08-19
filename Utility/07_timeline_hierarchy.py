import ctypes
import json
import os
import time
import tkinter as tk
from tkinter import ttk

# Timeline Hierarchy - the nesting chain around the timeline you have open.
#
# One tree, read top to bottom: the timelines that contain the current one are
# above it, the ones nested inside it are below, and the current timeline is
# marked in the middle. Unrelated timelines are never shown.
#
# A nested timeline appears as an ordinary timeline item whose media pool item
# has Type == "Timeline", which is how the graph is discovered.
#
# Double-click any row to travel there, keeping the same frame - the playhead
# is translated through every nest along the way, however deep.
#
# Read-only apart from switching timeline and moving the playhead.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\timeline_hierarchy_prefs.json")

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


def center_on_pointer(root):
    """Drop the window with its bottom edge at the mouse, across any monitor.

    Positioned while withdrawn and re-shown afterwards: setting geometry on an
    unmapped window can be overridden by the window manager when it first maps,
    which is why an earlier attempt appeared to do nothing. A full WxH+X+Y
    string is used rather than +X+Y alone for the same reason.
    """
    try:
        root.withdraw()
        root.update_idletasks()

        w = root.winfo_width()
        h = root.winfo_height()
        if w <= 1 or h <= 1:
            w, h = root.winfo_reqwidth(), root.winfo_reqheight()

        px, py = root.winfo_pointerx(), root.winfo_pointery()
        # Bottom edge at the pointer, horizontally centred: that lands the
        # action row (Up to parent) right under the cursor.
        x, y = px - w // 2, py - h

        try:
            gm = ctypes.windll.user32.GetSystemMetrics
            vx, vy, vw, vh = gm(76), gm(77), gm(78), gm(79)
        except Exception:
            vx, vy = 0, 0
            vw, vh = root.winfo_screenwidth(), root.winfo_screenheight()

        x = max(vx, min(x, vx + vw - w))
        y = max(vy, min(y, vy + vh - h))

        root.geometry(f"{int(w)}x{int(h)}+{int(x)}+{int(y)}")
        root.deiconify()
        root.update_idletasks()
        print(f"window {w}x{h} placed at ({int(x)},{int(y)}) "
              f"bottom-at-pointer ({px},{py}); actual {root.winfo_geometry()}")
    except Exception as e:
        try:
            root.deiconify()
        except Exception:
            pass
        print("Could not position the window: " + str(e))


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
# Resolve bridge
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()


def current_project():
    try:
        return projectManager.GetCurrentProject()
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


def tc_to_frame(tc_str, fps):
    drop_frame = ";" in tc_str
    h, m, s, f = map(int, tc_str.replace(";", ":").split(":"))
    fps_round = max(1, round(float(fps)))
    total = (h * 3600 + m * 60 + s) * fps_round + f
    if drop_frame:
        drop = 4 if fps_round == 60 else 2
        total_minutes = 60 * h + m
        total -= drop * (total_minutes - total_minutes // 10)
    return total


def timeline_clock(tl):
    """(fps, is_drop_frame) for a timeline."""
    try:
        fps = tl.GetSetting("timelineFrameRate") or 24
        drop = str(tl.GetSetting("timelineDropFrameTimecode") or "0") == "1"
        return fps, drop
    except Exception:
        return 24, False


def playhead_frame(tl):
    fps, drop = timeline_clock(tl)
    try:
        return tc_to_frame(tl.GetCurrentTimecode(), fps)
    except Exception:
        return None


def seek_to(project, timeline, frame):
    """Move the playhead and confirm it actually landed.

    SetCurrentTimecode returns a value that can be True while nothing moves,
    and right after switching timelines Resolve may ignore the first attempt
    entirely - so write, read back, retry. It also only works on the Cut, Edit,
    Color, Fairlight and Deliver pages; on the Fusion page it silently fails.
    """
    fps, drop = timeline_clock(timeline)
    want = frame_to_tc(frame, fps, drop)
    for attempt in range(4):
        try:
            live = project.GetCurrentTimeline() or timeline
            live.SetCurrentTimecode(want)
            got = live.GetCurrentTimecode()
            if got == want:
                if attempt:
                    print(f"  playhead set on attempt {attempt + 1}")
                return True, want, got
        except Exception as e:
            print("  SetCurrentTimecode error: " + str(e))
        time.sleep(0.15)
    try:
        got = (project.GetCurrentTimeline() or timeline).GetCurrentTimecode()
    except Exception:
        got = "?"
    return False, want, got


def scan_hierarchy(project):
    """Build {parent: [usages]} and {child: [usages]} across the project.

    Cheap by construction: a nested timeline's item carries the timeline's
    name, so GetName() (one call) filters candidates, and the far more
    expensive GetMediaPoolItem/GetClipProperty pair only runs for names that
    actually match a timeline.
    """
    timelines = {}
    order = []
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
    children = {n: [] for n in order}
    parents = {n: [] for n in order}
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
                # Confirm it really is a nested timeline, not a clip that
                # happens to share the name
                try:
                    mpi = item.GetMediaPoolItem()
                    if not mpi or mpi.GetClipProperty("Type") != "Timeline":
                        continue
                    start = item.GetStart()
                    # Where inside the nested timeline this clip begins, so a
                    # playhead position can be translated between the two.
                    try:
                        source_start = item.GetSourceStartFrame()
                    except Exception:
                        source_start = 0
                    duration = item.GetDuration()
                except Exception:
                    continue
                usage = {"parent": name, "child": item_name,
                         "start": start, "track": ti,
                         "source_start": source_start, "duration": duration}
                children[name].append(usage)
                parents[item_name].append(usage)

    return timelines, order, children, parents, scanned


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class HierarchyPanel:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.timelines = {}
        self.order = []
        self.children = {}
        self.parents = {}
        self.nodes = {}          # tree iid -> {"name", "usage"}

        root.title("Timeline Hierarchy")
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
        tk.Label(head, text="Timeline Hierarchy", bg=BG, fg=FG, anchor="w",
                 font=FONT(11, "bold")).pack(fill="x")
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=(S(2), 0))
        self.info = tk.Label(row, text="", bg=BG, fg=SUB, anchor="w", font=FONT(9))
        self.info.pack(side="left")
        make_button(row, "Rescan", self.rescan).pack(side="right")
        self.show_all_var = tk.BooleanVar(value=bool(self.prefs.get("show_all", False)))
        tk.Checkbutton(row, text="All timelines", variable=self.show_all_var,
                       command=self._toggle_all, bg=BG, fg=SUB, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, font=FONT(8),
                       highlightthickness=0).pack(side="right", padx=(0, S(8)))
        self.top_btn = tk.Button(row, text="On Top", relief="flat", bd=0,
                                 font=FONT(9), cursor="hand2", padx=S(10),
                                 pady=S(5), highlightthickness=0,
                                 command=self.toggle_topmost)
        self.top_btn.pack(side="right", padx=(0, S(6)))
        self._paint_topmost()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=S(14), pady=(S(6), 0))
        self.tree = self._make_tree(body)

        self.detail = tk.Label(self.root, text="", bg=BG, fg=SUB, anchor="w",
                               justify="left", font=FONT(8))
        self.detail.pack(fill="x", padx=S(14), pady=(S(8), 0))

        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=S(14), pady=(S(10), S(12)))
        make_button(actions, "Up to parent", self.go_up,
                    primary=True).pack(side="left")
        make_button(actions, "Into nest",
                    self.go_down).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Open selected",
                    self.open_selected).pack(side="left", padx=(S(6), 0))
        make_button(actions, "Expand all",
                    lambda: self._expand_all(True)).pack(side="right")
        make_button(actions, "Collapse",
                    lambda: self._expand_all(False)).pack(side="right", padx=(0, S(6)))

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

    def _make_tree(self, parent):
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="both", expand=True)
        tree = ttk.Treeview(holder, columns=("where",), show="tree headings")
        tree.heading("#0", text="TIMELINE")
        tree.heading("where", text="NESTED AT")
        tree.column("#0", width=S(360), anchor="w")
        tree.column("where", width=S(190), anchor="w")
        vs = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        tree.tag_configure("current", foreground=GOLD)
        tree.bind("<Double-1>", lambda e: self.travel_selected())
        tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())
        return tree

    def say(self, msg, error=False):
        self.status.config(text=msg, fg="#e07070" if error else SUB)

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

    def _toggle_all(self):
        self.prefs["show_all"] = bool(self.show_all_var.get())
        save_prefs(self.prefs)
        self.rescan()

    def _expand_all(self, opened):
        self._expand_tree(self.tree, opened)

    # -- scanning ---------------------------------------------------------
    def rescan(self):
        project = current_project()
        if not project:
            self.say("No project open.", True)
            return
        t0 = time.time()
        (self.timelines, self.order, self.children,
         self.parents, scanned) = scan_hierarchy(project)

        try:
            current = project.GetCurrentTimeline()
            self.current_name = current.GetName() if current else ""
        except Exception:
            self.current_name = ""

        self.nodes = {}
        self._node_seq = 0
        self.tree.delete(*self.tree.get_children())

        if not self.current_name:
            self.say("Open a timeline to see its nesting chain.", True)
            return

        if self.show_all_var.get():
            drawn = self._render_all()
            self._expand_tree(self.tree, True)
            self.info.config(
                text=f"whole project - {len(self.order)} timeline(s), "
                     f"{drawn} row(s) (scanned {scanned} item(s) in "
                     f"{time.time() - t0:.2f}s)")
            self.say("Showing every timeline. Double-click to travel; the frame "
                     "is carried when there's a path from the current one.")
            return

        above = below = 0
        for path in self._paths_to_current(self.current_name, {self.current_name}):
            a, b = self._render_path(path)
            above += a
            below += b

        self._expand_tree(self.tree, True)
        self.info.config(
            text=f"'{self.current_name}' - {above} above, {below} below "
                 f"(scanned {len(self.order)} timeline(s), {scanned} item(s) "
                 f"in {time.time() - t0:.2f}s)")
        if not above and not below:
            self.say(f"'{self.current_name}' is not nested and contains no nests. "
                     f"Tick 'All timelines' to see the whole project.")
        else:
            self.say("Double-click any row to travel there, keeping the frame.")

    def _render_all(self):
        """Whole-project view: every root, then anything unreachable from one."""
        drawn = 0
        for name in [n for n in self.order if not self.parents.get(n)]:
            drawn += self._add_any("", name, set(), None)
        shown = {n["name"] for n in self.nodes.values()}
        for name in self.order:
            if name not in shown:
                drawn += self._add_any("", name, set(), None)
        return drawn

    def _add_any(self, parent_iid, name, seen, usage):
        """Draw a timeline and everything nested inside it.

        Travel chains are left unresolved here and worked out on demand, since
        most rows in this view have no path to the current timeline at all.
        """
        self._node_seq += 1
        is_current = (name == self.current_name)
        iid = self.tree.insert(
            parent_iid, "end", iid=f"n{self._node_seq}",
            text=("> " + name) if is_current else name,
            values=(self._where(usage) if usage else "",),
            tags=("current",) if is_current else (), open=True)
        self.nodes[iid] = {"name": name, "usage": usage,
                           "chain": None, "direction": None}
        drawn = 1
        if name in seen:
            self.tree.item(iid, text=name + "   (already shown)")
            return drawn
        for child_usage in self.children.get(name, []):
            drawn += self._add_any(iid, child_usage["child"],
                                   seen | {name}, child_usage)
        return drawn

    def _search_down(self, name, target, chain, seen):
        for usage in self.children.get(name, []):
            child = usage["child"]
            if child == target:
                return chain + [usage]
            if child in seen:
                continue
            found = self._search_down(child, target, chain + [usage],
                                      seen | {child})
            if found:
                return found
        return None

    def _search_up(self, name, target, chain, seen):
        for usage in self.parents.get(name, []):
            owner = usage["parent"]
            if owner == target:
                return chain + [usage]
            if owner in seen:
                continue
            found = self._search_up(owner, target, chain + [usage],
                                    seen | {owner})
            if found:
                return found
        return None

    def _chain_between(self, target):
        """Find a route from the current timeline to `target`, either way."""
        if target == self.current_name:
            return [], None
        down = self._search_down(self.current_name, target, [],
                                 {self.current_name})
        if down:
            return down, "down"
        up = self._search_up(self.current_name, target, [], {self.current_name})
        if up:
            return up, "up"
        return [], None

    def _paths_to_current(self, name, seen):
        """Every chain of nests leading down to `name`, root first.

        A timeline used in more than one place has more than one path, so each
        is drawn as its own branch instead of guessing which one you meant.
        """
        ups = [u for u in self.parents.get(name, []) if u["parent"] not in seen]
        if not ups:
            return [[]]
        paths = []
        for usage in ups:
            for head in self._paths_to_current(usage["parent"],
                                               seen | {usage["parent"]}):
                paths.append(head + [usage])
        return paths or [[]]

    def _render_path(self, path):
        """Draw one ancestor chain, then hang the descendants off the current
        timeline at the end of it."""
        names = ([path[0]["parent"]] + [u["child"] for u in path]) if path \
            else [self.current_name]
        parent_iid = ""
        above = 0
        for i, name in enumerate(names):
            is_current = (i == len(names) - 1)
            usage = path[i - 1] if i > 0 else None
            # Travelling up from the current timeline to this row means
            # applying the remaining nests in reverse order.
            chain = list(reversed(path[i:])) if i < len(path) else []
            self._node_seq += 1
            iid = self.tree.insert(
                parent_iid, "end", iid=f"n{self._node_seq}",
                text=("> " + name) if is_current else name,
                values=(self._where(usage) if usage else "",),
                tags=("current",) if is_current else (), open=True)
            self.nodes[iid] = {"name": name, "usage": usage,
                               "chain": chain, "direction": "up"}
            parent_iid = iid
            if not is_current:
                above += 1
        below = self._add_below(parent_iid, self.current_name, [],
                                {self.current_name})
        return above, below

    def _add_below(self, parent_iid, name, chain, seen):
        added = 0
        for usage in self.children.get(name, []):
            child = usage["child"]
            new_chain = chain + [usage]
            self._node_seq += 1
            iid = self.tree.insert(parent_iid, "end", iid=f"n{self._node_seq}",
                                   text=child, values=(self._where(usage),),
                                   open=True)
            self.nodes[iid] = {"name": child, "usage": usage,
                               "chain": new_chain, "direction": "down"}
            added += 1
            if child in seen:
                self.tree.item(iid, text=child + "   (already shown)")
                continue
            added += self._add_below(iid, child, new_chain, seen | {child})
        return added

    def _where(self, usage):
        """Track and timecode of a nest inside its parent."""
        tl = self.timelines.get(usage["parent"])
        try:
            fps = tl.GetSetting("timelineFrameRate") or 24
            drop = str(tl.GetSetting("timelineDropFrameTimecode") or "0") == "1"
            return f"V{usage['track']}  {frame_to_tc(usage['start'], fps, drop)}"
        except Exception:
            return f"V{usage['track']}  frame {usage['start']}"

    def _expand_tree(self, tree, opened):
        def walk(node):
            for kid in tree.get_children(node):
                tree.item(kid, open=opened)
                walk(kid)
        walk("")

    def on_select(self, tree=None):
        node = self._selected_node()
        if not node:
            self.detail.config(text="")
            return
        name = node["name"]
        used_by = sorted({u["parent"] for u in self.parents.get(name, [])})
        contains = sorted({c["child"] for c in self.children.get(name, [])})
        bits = ["'" + name + "'"]
        bits.append("used by: " + (", ".join(used_by) if used_by else "nothing"))
        if contains:
            bits.append("contains: " + ", ".join(contains))
        if name == self.current_name:
            bits.append("(currently open)")
        self.detail.config(text="     ".join(bits))

    # -- navigation -------------------------------------------------------
    def _selected_node(self):
        sel = self.tree.selection()
        return self.nodes.get(sel[0]) if sel else None

    def open_selected(self):
        node = self._selected_node()
        if not node:
            self.say("Pick a timeline first.", True)
            return
        project = current_project()
        tl = self.timelines.get(node["name"])
        if not project or not tl:
            self.say("That timeline is no longer available - rescan.", True)
            return
        if not project.SetCurrentTimeline(tl):
            self.say(f"Could not open '{node['name']}'.", True)
            return
        print(f"Opened '{node['name']}'")
        self._on_close()

    def _hop_up(self, frame, from_tl, usage):
        """Frame inside `from_tl` -> the matching frame in usage['parent']."""
        if frame is None:
            return usage["start"]
        rel = frame - (from_tl.GetStartFrame() or 0) if from_tl else frame
        offset = rel - usage.get("source_start", 0)
        span = usage.get("duration") or 0
        if span:
            offset = max(0, min(offset, span - 1))
        return usage["start"] + offset

    def _hop_down(self, frame, usage):
        """Frame in the parent -> the matching frame inside usage['child']."""
        child = self.timelines.get(usage["child"])
        base = (child.GetStartFrame() or 0) if child else 0
        if frame is None:
            return base + usage.get("source_start", 0)
        offset = frame - usage["start"]
        span = usage.get("duration") or 0
        if span:
            offset = max(0, min(offset, span - 1))
        return base + usage.get("source_start", 0) + offset

    def _travel(self, chain, direction):
        """Walk a chain of nests, carrying the playhead frame along."""
        project = current_project()
        if not project or not chain:
            self.say("Nothing to travel to.", True)
            return
        tl = self.timelines.get(self.current_name)
        frame = playhead_frame(tl) if tl else None
        start_name, start_frame = self.current_name, frame

        for usage in chain:
            if direction == "up":
                frame = self._hop_up(frame, tl, usage)
                tl = self.timelines.get(usage["parent"])
            else:
                frame = self._hop_down(frame, usage)
                tl = self.timelines.get(usage["child"])
            if not tl:
                self.say("A timeline in that chain is unavailable - rescan.", True)
                return

        dest = tl.GetName()
        if not project.SetCurrentTimeline(tl):
            self.say(f"Could not open '{dest}'.", True)
            return

        print("")
        print(f"{direction.title()}: '{start_name}' frame {start_frame} -> "
              f"'{dest}' frame {frame}  ({len(chain)} hop(s))")
        ok, want, got = seek_to(project, tl, frame)

        if ok:
            # One-shot tool: the jump is done, so get out of the way.
            print(f"  arrived at {want}")
            self._on_close()
            return
        self.rescan()
        self.say(f"Opened '{dest}' but the playhead stayed at {got} "
                 f"(wanted {want}).", True)

    def travel_selected(self):
        """Double-click: go to that timeline, carrying the frame through every
        nest between here and there."""
        node = self._selected_node()
        if not node:
            self.say("Pick a timeline first.", True)
            return
        if node["name"] == self.current_name and not node["chain"]:
            self.say(f"Already in '{node['name']}'.")
            return
        chain = node.get("chain")
        direction = node.get("direction")
        if chain is None:               # whole-project view: work it out now
            chain, direction = self._chain_between(node["name"])
        if not chain:
            # No nesting path from here, so just open it - there is no
            # meaningful frame to carry across.
            self.open_selected()
            return
        self._travel(chain, direction or "up")

    def _usage_for_current(self):
        """Which nest to travel through when going up one level.

        Honours a selected row if it refers to the current timeline, so with a
        timeline nested in several places you can pick the branch; otherwise
        the first parent is used.
        """
        usages = self.parents.get(self.current_name, [])
        if not usages:
            return None
        node = self._selected_node()
        if node and (node.get("usage") or {}).get("child") == self.current_name:
            return node["usage"]
        return usages[0]

    def go_up(self):
        """Straight up one level from the current timeline, keeping the frame."""
        if not self.current_name:
            self.say("No timeline open.", True)
            return
        usage = self._usage_for_current()
        if not usage:
            self.say(f"Nothing nests '{self.current_name}' - already at the top.",
                     True)
            return
        self._travel([usage], "up")

    def go_down(self):
        """Drop one level into a nested timeline, keeping the frame."""
        node = self._selected_node()
        kids = self.children.get(self.current_name, [])
        if node and node.get("usage", {}).get("parent") == self.current_name:
            usage = node["usage"]
        elif len(kids) == 1:
            usage = kids[0]
        elif kids:
            self.say("Several nests inside - pick one in CONTAINS first.", True)
            return
        else:
            self.say("No nested timelines inside this one.", True)
            return
        self._travel([usage], "down")

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
app = HierarchyPanel(root)
center_on_pointer(root)
root.lift()
root.focus_force()          # so Escape and the tree respond immediately
root.attributes("-topmost", True)
if not prefs.get("always_on_top", True):
    root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
