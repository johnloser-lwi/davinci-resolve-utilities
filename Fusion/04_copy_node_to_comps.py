import ctypes
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk

# Copy Node To Comps — take one node from one clip's Fusion comp and push it
# into the other selected clips' comps.
#
# Built for the case of adding e.g. a Drop Shadow to one Text+ and wanting the
# same effect on the rest. The pasted node is inserted just before MediaOut, so
# whatever fed the output now feeds the new node instead. Comps that already
# have a node of that type are skipped.
#
# Every comp is modified inside its own undo block, so Ctrl+Z in that comp
# reverts it.

for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

PREFS_FILE = os.path.expandvars(r"%APPDATA%\copy_node_prefs.json")

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
    """Video items, ordered by timeline position. GetSelectedClips() can return
    a partial selection, so poll and union by unique id."""
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
    out.sort(key=lambda it: it.GetStart() if hasattr(it, "GetStart") else 0)
    return out


def reg_id(tool):
    try:
        return tool.GetAttrs()["TOOLS_RegID"] or "?"
    except Exception:
        return "?"


def comps_of(clip):
    out = []
    try:
        count = clip.GetFusionCompCount() or 0
    except Exception:
        count = 0
    for i in range(1, count + 1):
        try:
            comp = clip.GetFusionCompByIndex(i)
            if comp:
                out.append(comp)
        except Exception:
            continue
    return out


def tools_of(comp):
    try:
        return list((comp.GetToolList(False) or {}).values())
    except Exception:
        return []


def find_media_out(comp):
    for tool in tools_of(comp):
        if reg_id(tool) == "MediaOut":
            return tool
    return None


COPYABLE_TYPES = ("Number", "Point", "Text")


def read_settings(tool):
    """Snapshot a tool's editable input values, keyed by input id.

    Only value inputs are read — Image/Mask inputs are connections, not
    settings, and must not be copied.
    """
    values = {}
    try:
        inputs = tool.GetInputList() or {}
    except Exception:
        return values
    for inp in list(inputs.values())[:400]:
        try:
            attrs = inp.GetAttrs() or {}
        except Exception:
            continue
        input_id = attrs.get("INPS_ID")
        dtype = str(attrs.get("INPS_DataType") or "")
        if not input_id or dtype not in COPYABLE_TYPES:
            continue
        try:
            value = tool.GetInput(input_id)
        except Exception:
            continue
        if value is not None:
            values[input_id] = value
    return values


def clone_into(comp, reg, values):
    """Create the node directly in `comp` and apply the captured values.

    Deliberately avoids comp.Paste(): pasting acts on whichever comp Fusion
    considers current, so it only ever landed in one of the target comps.
    AddTool() is explicit about which comp it builds in.
    """
    try:
        tool = comp.AddTool(reg)
    except Exception as e:
        return None, 0, f"AddTool failed: {e}"
    if not tool:
        return None, 0, "AddTool returned nothing"

    applied = 0
    for input_id, value in values.items():
        try:
            tool.SetInput(input_id, value)
            applied += 1
        except Exception:
            pass
    return tool, applied, ""


def insert_before_media_out(comp, tool):
    """Route whatever fed MediaOut through `tool` instead."""
    media_out = find_media_out(comp)
    if not media_out:
        return False, "no MediaOut node"
    try:
        mo_input = media_out.FindMainInput(1)
        if not mo_input:
            return False, "MediaOut has no input"
        upstream = mo_input.GetConnectedOutput()
        tool_input = tool.FindMainInput(1)
        tool_output = tool.FindMainOutput(1)
        if not tool_output:
            return False, "pasted node has no output"
        if upstream and tool_input:
            tool_input.ConnectTo(upstream)
        mo_input.ConnectTo(tool_output)
        return True, ""
    except Exception as e:
        return False, str(e)


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------

class CopyNodePanel:
    def __init__(self, root):
        self.root = root
        self.prefs = load_prefs()
        self.sources = []       # [{label, clip, comp, tool, reg}]
        self.targets = []       # [{clip, comp}]

        root.title("Copy Node To Comps")
        root.configure(bg=BG)
        root.minsize(S(560), S(320))
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

        pick = tk.Frame(content, bg=BG)
        pick.pack(fill="x", padx=S(14), pady=(S(10), 0))
        tk.Label(pick, text="Copy this node", bg=BG, fg=SUB, font=FONT(9),
                 anchor="w").pack(fill="x", pady=(0, S(4)))
        self.source_var = tk.StringVar()
        self.source_box = ttk.Combobox(pick, textvariable=self.source_var,
                                       state="readonly", font=FONT(9))
        self.source_box.pack(fill="x")
        self.source_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_targets())

        tk.Label(content, text="Into these comps", bg=BG, fg=SUB, font=FONT(9),
                 anchor="w").pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        table = tk.Frame(content, bg=BG)
        table.pack(fill="both", expand=True, padx=S(14))
        self.tree = ttk.Treeview(table, columns=("clip", "state"),
                                 show="headings", height=6)
        self.tree.heading("clip", text="CLIP")
        self.tree.heading("state", text="STATUS")
        self.tree.column("clip", width=S(300), anchor="w")
        self.tree.column("state", width=S(180), anchor="w")
        self.tree.pack(fill="both", expand=True)

        actions = tk.Frame(content, bg=BG)
        actions.pack(fill="x", padx=S(14), pady=(S(12), 0))
        make_button(actions, "Copy to all listed comps", self.do_copy,
                    primary=True).pack(side="left")
        tk.Label(actions, text="inserted just before MediaOut · one undo step per comp",
                 bg=BG, fg=SUB, font=FONT(8)).pack(side="left", padx=(S(10), 0))

        tk.Label(content, text="Comps that already contain a node of this type are "
                               "skipped, so re-running won't stack duplicates.",
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
        self.root.minsize(S(560), S(320))
        self._style()
        self._build()
        self.rescan()

    # -- scanning ---------------------------------------------------------
    def rescan(self):
        timeline = current_timeline()
        if not timeline:
            self.tl_label.config(text="No timeline open")
            self.say("Open a timeline to begin.", True)
            return
        self.tl_label.config(text=timeline.GetName())

        clips = selected_clips()
        self.sources = []
        self.comp_rows = []
        for clip in clips:
            name = clip.GetName()
            for comp in comps_of(clip):
                self.comp_rows.append({"clip": name, "comp": comp})
                for tool in tools_of(comp):
                    reg = reg_id(tool)
                    if reg in ("MediaOut", "MediaIn"):
                        continue          # structural, never worth copying
                    self.sources.append({
                        "label": f"{name}  ›  {tool.Name}   ({reg})",
                        "clip": name, "comp": comp, "tool": tool, "reg": reg,
                    })

        self.info.config(text=f"{len(clips)} clip(s) · {len(self.comp_rows)} comp(s) · "
                              f"{len(self.sources)} copyable node(s)")
        labels = [s["label"] for s in self.sources]
        self.source_box["values"] = labels
        if labels:
            self.source_var.set(labels[0])
            self.say("Pick the node to copy.")
        else:
            self.source_var.set("")
            self.say("Select clips that have Fusion comps." if not clips
                     else "No copyable nodes found in the selected clips.", True)
        self.refresh_targets()

    def current_source(self):
        label = self.source_var.get()
        for s in self.sources:
            if s["label"] == label:
                return s
        return None

    def refresh_targets(self):
        self.tree.delete(*self.tree.get_children())
        self.targets = []
        source = self.current_source()
        if not source:
            return
        for row in self.comp_rows:
            comp = row["comp"]
            if comp == source["comp"]:
                self.tree.insert("", "end", values=(row["clip"], "source — skipped"))
                continue
            has = any(reg_id(t) == source["reg"] for t in tools_of(comp))
            if has:
                self.tree.insert("", "end",
                                 values=(row["clip"], f"already has {source['reg']}"))
                continue
            self.targets.append(row)
            self.tree.insert("", "end", values=(row["clip"], "will receive"))
        self.say(f"{len(self.targets)} comp(s) will receive '{source['tool'].Name}'.")

    # -- the copy ---------------------------------------------------------
    def do_copy(self):
        source = self.current_source()
        if not source:
            self.say("Pick a node to copy first.", True)
            return
        if not self.targets:
            self.say("Nothing to copy into — every comp already has it, or none selected.", True)
            return
        if not messagebox.askyesno(
                "Copy Node To Comps",
                f"Copy '{source['tool'].Name}' ({source['reg']}) into "
                f"{len(self.targets)} comp(s)?\n\n"
                "The node is rebuilt in each comp with the same settings and "
                "inserted just before MediaOut.",
                parent=self.root):
            return

        values = read_settings(source["tool"])
        if not values:
            print(f"Note: no readable settings on '{source['tool'].Name}' — "
                  f"the node will be created with its defaults.")

        print(f"\nCopying '{source['tool'].Name}' ({source['reg']}) "
              f"into {len(self.targets)} comp(s) — {len(values)} setting(s) captured:")
        done, failed = 0, 0
        for row in self.targets:
            comp = row["comp"]
            # No comp.Lock() here — locking has been observed to interfere with
            # building nodes; the undo block alone is enough.
            try:
                comp.StartUndo("Copy Node Into Comp")
            except Exception:
                pass

            tool, applied, err = clone_into(comp, source["reg"], values)
            if not tool:
                print(f"  {row['clip'][:34]:36} FAILED — {err}")
                failed += 1
            else:
                wired, why = insert_before_media_out(comp, tool)
                state = "wired in" if wired else f"NOT wired ({why})"
                print(f"  {row['clip'][:34]:36} created '{tool.Name}' "
                      f"({applied}/{len(values)} settings) — {state}")
                done += 1

            try:
                comp.EndUndo(True)
            except Exception:
                pass

        self.refresh_targets()
        self.say(f"Copied into {done} comp(s)."
                 + (f"  {failed} failed — see console." if failed else ""))

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
app = CopyNodePanel(root)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
