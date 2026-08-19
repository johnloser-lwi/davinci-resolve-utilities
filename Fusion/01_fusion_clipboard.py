import ctypes
import json
import os
import shutil
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

# Tell Windows we handle our own DPI, so a 4K/high-DPI screen reports real
# pixels instead of a blurry bitmap-scaled window. Must run before Tk starts.
for _mod, _fn, _arg in (("shcore", "SetProcessDpiAwareness", 2),
                        ("shcore", "SetProcessDpiAwareness", 1),
                        ("user32", "SetProcessDPIAware", None)):
    try:
        _f = getattr(getattr(ctypes.windll, _mod), _fn)
        _f(_arg) if _arg is not None else _f()
        break
    except Exception:
        continue

# Fusion Clipboard — a node preset manager.
#
# Save the currently selected Fusion nodes under a name, organize them into
# folders with notes and favorites, then paste them back into any comp.
# Presets are plain .setting files on disk (browsable in Explorer) with a
# small .json sidecar holding notes / tool types / favorite flag.

PREFS_FILE = os.path.expandvars(r"%APPDATA%\fusion_clipboard_prefs.json")
# Presets live next to the prefs files, same as the other scripts. Set
# "library_path" in the prefs JSON to point somewhere else.
DEFAULT_LIBRARY = os.path.expandvars(r"%APPDATA%\FusionClipboard")
RECENT_MAX = 15

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

FAV_NODE = "__favorites__"
RECENT_NODE = "__recent__"
ALL_NODE = "__all__"

SCALE_CHOICES = ["Auto", "100%", "125%", "150%", "175%", "200%", "250%"]

# Every pixel dimension and font size below is multiplied by SCALE, so the
# whole panel grows uniformly on high-DPI screens.
SCALE = 1.0


def S(n):
    """Scale a pixel dimension."""
    return max(1, int(round(n * SCALE)))


def FONT(size, weight=None):
    """Scale a font's point size."""
    pts = max(6, int(round(size * SCALE)))
    return ("Segoe UI", pts, weight) if weight else ("Segoe UI", pts)


def detect_scale(root):
    """Work out a sensible UI scale for the screen we're opening on."""
    try:
        dpi = root.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    scale = dpi / 96.0
    if scale < 1.05:
        # A 4K panel left at 100% Windows scaling still reports 96 DPI, so the
        # UI would be physically tiny — fall back to judging by resolution.
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


# --------------------------------------------------------------------------
# prefs / storage
# --------------------------------------------------------------------------

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


def sanitize(name):
    cleaned = "".join(c for c in name if c not in '<>:"/\\|?*').strip()
    return cleaned or "Untitled"


def meta_path(setting_path):
    return os.path.splitext(setting_path)[0] + ".json"


def read_meta(setting_path):
    path = meta_path(setting_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_meta(setting_path, meta):
    try:
        with open(meta_path(setting_path), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"Could not write sidecar: {e}")


# --------------------------------------------------------------------------
# Fusion bridge — every call re-fetches the comp so we follow the active one
# --------------------------------------------------------------------------

resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion()


def current_comp():
    try:
        return fusion.GetCurrentComp()
    except Exception:
        return None


def selected_tools(comp):
    try:
        return comp.GetToolList(True) or {}
    except Exception:
        return {}


def tool_types(tools):
    types = []
    for tool in tools.values():
        try:
            types.append(tool.GetAttrs()["TOOLS_RegID"])
        except Exception:
            types.append("?")
    return types


def copy_settings(comp):
    """Serialize the current selection. Returns (settings, error)."""
    try:
        settings = comp.CopySettings()
        if settings:
            return settings, None
    except Exception as e:
        first_err = str(e)
    else:
        first_err = "CopySettings() returned nothing"

    # Some builds want the tool passed explicitly
    sel = selected_tools(comp)
    if len(sel) == 1:
        try:
            settings = comp.CopySettings(list(sel.values())[0])
            if settings:
                return settings, None
        except Exception as e:
            return None, f"{first_err}; per-tool: {e}"
    return None, first_err


def paste_settings(comp, settings):
    """Paste a settings table into the comp. Returns (ok, how, errors)."""
    errors = []
    try:
        comp.Lock()
        comp.StartUndo("Paste Fusion Preset")
    except Exception:
        pass

    ok, how = False, ""
    try:
        ok = bool(comp.Paste(settings))
        how = "Paste(table)"
    except Exception as e:
        errors.append(f"Paste(table): {e}")

    if not ok:
        # Fall back to routing it through Fusion's own clipboard
        try:
            bmd.setclipboard(settings)
            ok = bool(comp.Paste())
            how = "clipboard + Paste()"
        except Exception as e:
            errors.append(f"clipboard: {e}")

    try:
        comp.EndUndo(True)
        comp.Unlock()
    except Exception:
        pass
    return ok, how, errors


def read_setting_file(path):
    try:
        return bmd.readfile(path), None
    except Exception as e:
        return None, str(e)


def write_setting_file(path, settings):
    try:
        return bool(bmd.writefile(path, settings)), None
    except Exception as e:
        return False, str(e)


# --------------------------------------------------------------------------
# small themed widgets
# --------------------------------------------------------------------------

def make_button(parent, text, command, primary=False, width=None):
    base = ACCENT if primary else BTN
    hover = ACCENT_HOVER if primary else BTN_HOVER
    btn = tk.Button(
        parent, text=text, command=command, bg=base, fg="#ffffff" if primary else FG,
        relief="flat", bd=0, padx=S(12), pady=S(6), font=FONT(9),
        activebackground=hover, activeforeground=FG, cursor="hand2",
        highlightthickness=0,
    )
    if width:
        btn.config(width=width)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=base))
    return btn


class PromptDialog(tk.Toplevel):
    """Dark modal prompt. fields = [(key, label, kind, initial)] where kind is
    'entry', 'combo' (needs values) or 'text'."""

    def __init__(self, parent, title, fields, combo_values=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.result = None
        self._widgets = {}
        combo_values = combo_values or {}

        body = tk.Frame(self, bg=BG)
        body.pack(padx=S(16), pady=(S(14), S(8)), fill="both", expand=True)

        for row, (key, label, kind, initial) in enumerate(fields):
            tk.Label(body, text=label, bg=BG, fg=SUB, font=FONT(9),
                     anchor="w").grid(row=row * 2, column=0, sticky="w", pady=(S(6), S(2)))
            if kind == "combo":
                w = ttk.Combobox(body, values=combo_values.get(key, []), width=38,
                                 font=FONT(9))
                w.set(initial or "")
            elif kind == "text":
                w = tk.Text(body, height=3, width=40, bg=PANEL, fg=FG, relief="flat",
                            insertbackground=FG, font=FONT(9), padx=S(6), pady=S(4),
                            highlightthickness=1, highlightbackground=BORDER)
                w.insert("1.0", initial or "")
            else:
                w = tk.Entry(body, width=40, bg=PANEL, fg=FG, relief="flat",
                             insertbackground=FG, font=FONT(9),
                             highlightthickness=1, highlightbackground=BORDER)
                w.insert(0, initial or "")
            w.grid(row=row * 2 + 1, column=0, sticky="ew", ipady=S(3))
            self._widgets[key] = (w, kind)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=S(16), pady=(S(4), S(14)))
        make_button(btns, "Cancel", self.destroy).pack(side="right")
        make_button(btns, "OK", self._ok, primary=True).pack(side="right", padx=(0, S(8)))

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        first = next(iter(self._widgets.values()))[0]
        first.focus_set()

        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + S(120)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        parent.wait_window(self)

    def _ok(self):
        out = {}
        for key, (w, kind) in self._widgets.items():
            out[key] = w.get("1.0", "end").strip() if kind == "text" else w.get().strip()
        self.result = out
        self.destroy()


# --------------------------------------------------------------------------
# main panel
# --------------------------------------------------------------------------

class FusionClipboard:
    def __init__(self, root, library):
        self.root = root
        self.library = library
        self.prefs = load_prefs()
        self.presets = []
        self.visible = []
        self.current = None
        self._notes_dirty = False

        root.title("Fusion Clipboard")
        root.configure(bg=BG)
        root.minsize(S(880), S(520))
        # Only reuse a saved size if it was saved at this same scale, otherwise
        # a window sized on a 2K screen comes back cramped on a 4K one.
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
        root.bind("<Control-f>", lambda e: self.search_entry.focus_set())

    def rebuild(self):
        """Tear down and rebuild the UI — used when the scale changes."""
        keep_search = self.search_var.get() if hasattr(self, "search_var") else ""
        for child in self.root.winfo_children():
            child.destroy()
        self.root.minsize(S(880), S(520))
        self._style()
        self._build()
        self.search_var.set(keep_search)
        self.refresh()

    def on_scale_change(self, event=None):
        global SCALE
        if event and getattr(event, "widget", None):
            try:
                event.widget.selection_clear()  # drop the highlight after picking
            except Exception:
                pass
        choice = self.scale_var.get()
        self.prefs["ui_scale"] = choice
        save_prefs(self.prefs)
        SCALE = resolve_scale(self.prefs, self.root)
        self.rebuild()
        self.say(f"UI scale set to {choice} ({SCALE:.2f}x).")

    # -- theming ----------------------------------------------------------
    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")  # required before Treeview colors take effect
        except Exception:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=S(23),
                        font=FONT(9))
        style.configure("Treeview.Heading", background=PANEL2, foreground=SUB,
                        relief="flat", font=FONT(8, "bold"), padding=S(4))
        style.map("Treeview.Heading", background=[("active", PANEL2)])
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=BTN, troughcolor=BG,
                        bordercolor=BG, arrowcolor=SUB, relief="flat",
                        arrowsize=S(14), width=S(14))

        # A readonly ttk Combobox draws its text as "selected", so without
        # explicit state maps it lands white-on-white. The dropdown popup is a
        # plain Tk listbox that ttk styling never reaches — themed separately.
        style.configure("TCombobox",
                        fieldbackground=PANEL, background=BTN, foreground=FG,
                        arrowcolor=SUB, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=FG,
                        arrowsize=S(14), padding=S(3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL), ("disabled", PANEL),
                                   ("focus", PANEL), ("!disabled", PANEL)],
                  foreground=[("readonly", FG), ("disabled", SUB),
                              ("focus", FG), ("!disabled", FG)],
                  selectbackground=[("readonly", PANEL), ("focus", PANEL),
                                    ("!disabled", PANEL)],
                  selectforeground=[("readonly", FG), ("focus", FG),
                                    ("!disabled", FG)],
                  background=[("active", BTN_HOVER), ("readonly", BTN)],
                  bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("active", FG), ("disabled", BORDER)])

        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", FONT(9))

    # -- layout -----------------------------------------------------------
    def _build(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=S(12), pady=(S(12), S(8)))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.apply_filter())
        search = tk.Entry(top, textvariable=self.search_var, bg=PANEL, fg=FG,
                          relief="flat", insertbackground=FG, font=FONT(10),
                          highlightthickness=1, highlightbackground=BORDER,
                          highlightcolor=ACCENT)
        search.pack(side="left", fill="x", expand=True, ipady=S(5), padx=(0, S(8)))
        self.search_entry = search
        tk.Label(top, text="Search  (Ctrl+F)", bg=BG, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(0, S(12)))
        make_button(top, "+  Save Selection", self.save_selection, primary=True).pack(side="right")

        middle = tk.Frame(self.root, bg=BG)
        middle.pack(fill="both", expand=True, padx=S(12))

        # folder tree
        left = tk.Frame(middle, bg=BG, width=S(190))
        left.pack(side="left", fill="y", padx=(0, S(10)))
        left.pack_propagate(False)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.apply_filter())

        # preset list
        centre = tk.Frame(middle, bg=BG)
        centre.pack(side="left", fill="both", expand=True)
        cols = ("name", "nodes", "folder")
        self.list = ttk.Treeview(centre, columns=cols, show="headings", selectmode="browse")
        self.list.heading("name", text="PRESET")
        self.list.heading("nodes", text="NODES")
        self.list.heading("folder", text="FOLDER")
        self.list.column("name", width=S(240), anchor="w")
        self.list.column("nodes", width=S(180), anchor="w")
        self.list.column("folder", width=S(120), anchor="w")
        vs = ttk.Scrollbar(centre, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self.list.bind("<<TreeviewSelect>>", self.on_select)
        self.list.bind("<Double-1>", lambda e: self.paste())
        self.list.bind("<Return>", lambda e: self.paste())
        self.list.bind("<Delete>", lambda e: self.delete())
        self.list.bind("<F2>", lambda e: self.rename())

        # details
        right = tk.Frame(middle, bg=BG, width=S(250))
        right.pack(side="left", fill="y", padx=(S(10), 0))
        right.pack_propagate(False)
        self.detail_name = tk.Label(right, text="—", bg=BG, fg=FG, anchor="w",
                                    font=FONT(11, "bold"), wraplength=S(240),
                                    justify="left")
        self.detail_name.pack(fill="x")
        self.detail_meta = tk.Label(right, text="", bg=BG, fg=SUB, anchor="w",
                                    font=FONT(8), justify="left",
                                    wraplength=S(240))
        self.detail_meta.pack(fill="x", pady=(S(2), S(8)))
        tk.Label(right, text="NOTES", bg=BG, fg=SUB,
                 font=FONT(8, "bold"), anchor="w").pack(fill="x")
        self.notes = tk.Text(right, height=8, bg=PANEL, fg=FG, relief="flat",
                             insertbackground=FG, font=FONT(9), padx=S(6), pady=S(5),
                             wrap="word", highlightthickness=1, highlightbackground=BORDER)
        self.notes.pack(fill="both", expand=True, pady=(S(4), S(6)))
        self.notes.bind("<KeyRelease>", lambda e: setattr(self, "_notes_dirty", True))
        make_button(right, "Save Notes", self.save_notes).pack(fill="x")

        # actions
        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=S(12), pady=(S(10), S(4)))
        make_button(actions, "Paste into Comp", self.paste, primary=True).pack(side="left")
        self.fav_btn = make_button(actions, "☆ Favorite", self.toggle_favorite)
        self.fav_btn.pack(side="left", padx=S(6))
        make_button(actions, "Rename", self.rename).pack(side="left", padx=(0, S(6)))
        make_button(actions, "Move", self.move).pack(side="left", padx=(0, S(6)))
        make_button(actions, "Delete", self.delete).pack(side="left")
        make_button(actions, "⟳", self.refresh).pack(side="right")
        make_button(actions, "Open Folder", self.open_folder).pack(side="right", padx=S(6))
        make_button(actions, "New Folder", self.new_folder).pack(side="right")

        bottom = tk.Frame(self.root, bg=PANEL2)
        bottom.pack(fill="x", side="bottom")
        self.status = tk.Label(bottom, text="", bg=PANEL2, fg=SUB, anchor="w",
                               font=FONT(8), padx=S(12), pady=S(5))
        self.status.pack(side="left", fill="x", expand=True)
        tk.Label(bottom, text="UI scale", bg=PANEL2, fg=SUB,
                 font=FONT(8)).pack(side="left", padx=(0, S(6)))
        self.scale_var = tk.StringVar(value=self.prefs.get("ui_scale", "Auto"))
        scale_box = ttk.Combobox(bottom, textvariable=self.scale_var, values=SCALE_CHOICES,
                                 state="readonly", width=6, font=FONT(8))
        scale_box.pack(side="left", padx=(0, S(10)), pady=S(3))
        scale_box.bind("<<ComboboxSelected>>", self.on_scale_change)

    def say(self, msg, error=False):
        self.status.config(text=msg, fg="#e07070" if error else SUB)

    # -- library scanning -------------------------------------------------
    def scan(self):
        presets = []
        for dirpath, _dirs, files in os.walk(self.library):
            for fn in files:
                if not fn.lower().endswith(".setting"):
                    continue
                full = os.path.join(dirpath, fn)
                rel_dir = os.path.relpath(dirpath, self.library)
                rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")
                name = os.path.splitext(fn)[0]
                presets.append({
                    "name": name,
                    "folder": rel_dir,
                    "rel": f"{rel_dir}/{name}" if rel_dir else name,
                    "path": full,
                    "meta": read_meta(full),
                })
        presets.sort(key=lambda p: (p["folder"].lower(), p["name"].lower()))
        return presets

    def folders(self):
        out = []
        for dirpath, _dirs, _files in os.walk(self.library):
            rel = os.path.relpath(dirpath, self.library)
            if rel != ".":
                out.append(rel.replace("\\", "/"))
        return sorted(out)

    def refresh(self):
        self.presets = self.scan()
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", ALL_NODE, text=f"  All Presets ({len(self.presets)})")
        favs = sum(1 for p in self.presets if p["meta"].get("favorite"))
        self.tree.insert("", "end", FAV_NODE, text=f"  ★ Favorites ({favs})")
        self.tree.insert("", "end", RECENT_NODE, text="  ↺ Recent")
        for folder in self.folders():
            parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
            label = folder.rsplit("/", 1)[-1]
            try:
                self.tree.insert(parent, "end", folder, text=f"  {label}", open=True)
            except Exception:
                self.tree.insert("", "end", folder, text=f"  {folder}", open=True)
        if not self.tree.selection():
            self.tree.selection_set(ALL_NODE)
        self.apply_filter()
        self.say(f"{len(self.presets)} preset(s) · {self.library}")

    # -- filtering / listing ---------------------------------------------
    def apply_filter(self):
        query = self.search_var.get().strip().lower()
        sel = self.tree.selection()
        node = sel[0] if sel else ALL_NODE

        if query:
            pool = self.presets  # search is always global
        elif node == FAV_NODE:
            pool = [p for p in self.presets if p["meta"].get("favorite")]
        elif node == RECENT_NODE:
            order = self.prefs.get("recent", [])
            by_rel = {p["rel"]: p for p in self.presets}
            pool = [by_rel[r] for r in order if r in by_rel]
        elif node == ALL_NODE:
            pool = self.presets
        else:
            pool = [p for p in self.presets
                    if p["folder"] == node or p["folder"].startswith(node + "/")]

        if query:
            def hit(p):
                hay = " ".join([
                    p["name"], p["folder"],
                    " ".join(p["meta"].get("tools", [])),
                    p["meta"].get("notes", ""),
                ]).lower()
                return query in hay
            pool = [p for p in pool if hit(p)]

        self.visible = pool
        self.list.delete(*self.list.get_children())
        for i, p in enumerate(pool):
            star = "★ " if p["meta"].get("favorite") else ""
            nodes = " + ".join(p["meta"].get("tools", [])) or "—"
            if len(nodes) > 34:
                nodes = nodes[:32] + "…"
            self.list.insert("", "end", iid=str(i),
                             values=(star + p["name"], nodes, p["folder"] or "—"))
        self.current = None
        self.show_details(None)

    def on_select(self, _event=None):
        if self._notes_dirty:
            self.save_notes(quiet=True)
        sel = self.list.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.visible):
            self.current = self.visible[idx]
            self.show_details(self.current)

    def show_details(self, preset):
        if not preset:
            self.detail_name.config(text="—")
            self.detail_meta.config(text="")
            self.notes.delete("1.0", "end")
            self.fav_btn.config(text="☆ Favorite")
            self._notes_dirty = False
            return
        meta = preset["meta"]
        self.detail_name.config(text=preset["name"])
        bits = []
        if meta.get("tools"):
            bits.append(f"{len(meta['tools'])} node(s):  " + " + ".join(meta["tools"]))
        if preset["folder"]:
            bits.append(f"Folder:  {preset['folder']}")
        if meta.get("created"):
            bits.append(f"Saved:  {meta['created']}")
        self.detail_meta.config(text="\n".join(bits))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", meta.get("notes", ""))
        self.fav_btn.config(text="★ Favorited" if meta.get("favorite") else "☆ Favorite")
        self._notes_dirty = False

    # -- actions ----------------------------------------------------------
    def save_selection(self):
        comp = current_comp()
        if not comp:
            self.say("No Fusion comp open — open a clip on the Fusion page first.", True)
            return
        sel = selected_tools(comp)
        if not sel:
            self.say("Nothing selected in the comp — select the node(s) to save.", True)
            return

        types = tool_types(sel)
        sel_node = self.tree.selection()
        default_folder = ""
        if sel_node and sel_node[0] not in (ALL_NODE, FAV_NODE, RECENT_NODE):
            default_folder = sel_node[0]

        dlg = PromptDialog(
            self.root, "Save Selection",
            [("name", f"Preset name   ({len(sel)} node(s): {' + '.join(types)})", "entry", ""),
             ("folder", "Folder   (type a new name to create one)", "combo", default_folder),
             ("notes", "Notes   (optional)", "text", "")],
            combo_values={"folder": [""] + self.folders()},
        )
        if not dlg.result or not dlg.result["name"]:
            return

        name = sanitize(dlg.result["name"])
        folder = dlg.result["folder"].strip().strip("/")
        target_dir = os.path.join(self.library, *folder.split("/")) if folder else self.library
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, name + ".setting")

        if os.path.exists(path) and not messagebox.askyesno(
                "Fusion Clipboard", f"'{name}' already exists in that folder.\n\nOverwrite it?",
                parent=self.root):
            return

        settings, err = copy_settings(comp)
        if not settings:
            self.say(f"Could not copy the selection: {err}", True)
            return

        ok, werr = write_setting_file(path, settings)
        if not ok:
            self.say(f"Could not write '{name}': {werr}", True)
            return

        write_meta(path, {
            "notes": dlg.result["notes"],
            "tools": types,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "favorite": False,
        })
        self.refresh()
        self.say(f"Saved '{name}' ({len(sel)} node(s)) to {folder or 'the library root'}.")

    def paste(self):
        if not self.current:
            self.say("Select a preset to paste.", True)
            return
        comp = current_comp()
        if not comp:
            self.say("No Fusion comp open — open a clip on the Fusion page first.", True)
            return

        settings, err = read_setting_file(self.current["path"])
        if settings is None:
            self.say(f"Could not read the preset file: {err}", True)
            return

        ok, how, errors = paste_settings(comp, settings)
        if ok:
            recent = [r for r in self.prefs.get("recent", []) if r != self.current["rel"]]
            recent.insert(0, self.current["rel"])
            self.prefs["recent"] = recent[:RECENT_MAX]
            save_prefs(self.prefs)
            self.say(f"Pasted '{self.current['name']}' via {how}.")
        else:
            self.say("Paste failed — " + " | ".join(errors or ["no method succeeded"]), True)

    def toggle_favorite(self):
        if not self.current:
            return
        meta = self.current["meta"]
        meta["favorite"] = not meta.get("favorite")
        write_meta(self.current["path"], meta)
        rel = self.current["rel"]
        self.refresh()
        for i, p in enumerate(self.visible):
            if p["rel"] == rel:
                self.list.selection_set(str(i))
                break
        self.say(("Added to" if meta["favorite"] else "Removed from") + " favorites.")

    def save_notes(self, quiet=False):
        if not self.current:
            return
        meta = self.current["meta"]
        meta["notes"] = self.notes.get("1.0", "end").strip()
        write_meta(self.current["path"], meta)
        self._notes_dirty = False
        if not quiet:
            self.say(f"Notes saved for '{self.current['name']}'.")

    def rename(self):
        if not self.current:
            return
        dlg = PromptDialog(self.root, "Rename Preset",
                           [("name", "New name", "entry", self.current["name"])])
        if not dlg.result or not dlg.result["name"]:
            return
        new = sanitize(dlg.result["name"])
        folder = os.path.dirname(self.current["path"])
        new_path = os.path.join(folder, new + ".setting")
        if os.path.exists(new_path):
            self.say(f"'{new}' already exists in that folder.", True)
            return
        try:
            os.rename(self.current["path"], new_path)
            if os.path.exists(meta_path(self.current["path"])):
                os.rename(meta_path(self.current["path"]), meta_path(new_path))
        except OSError as e:
            self.say(f"Rename failed: {e}", True)
            return
        self.refresh()
        self.say(f"Renamed to '{new}'.")

    def move(self):
        if not self.current:
            return
        dlg = PromptDialog(self.root, "Move Preset",
                           [("folder", "Destination folder   (blank = library root)",
                             "combo", self.current["folder"])],
                           combo_values={"folder": [""] + self.folders()})
        if dlg.result is None:
            return
        folder = dlg.result["folder"].strip().strip("/")
        target_dir = os.path.join(self.library, *folder.split("/")) if folder else self.library
        os.makedirs(target_dir, exist_ok=True)
        new_path = os.path.join(target_dir, self.current["name"] + ".setting")
        if os.path.normcase(new_path) == os.path.normcase(self.current["path"]):
            return
        if os.path.exists(new_path):
            self.say("A preset with that name already exists there.", True)
            return
        try:
            shutil.move(self.current["path"], new_path)
            old_meta = meta_path(self.current["path"])
            if os.path.exists(old_meta):
                shutil.move(old_meta, meta_path(new_path))
        except OSError as e:
            self.say(f"Move failed: {e}", True)
            return
        self.refresh()
        self.say(f"Moved to {folder or 'the library root'}.")

    def delete(self):
        if not self.current:
            return
        if not messagebox.askyesno(
                "Fusion Clipboard",
                f"Delete '{self.current['name']}' permanently?\n\n{self.current['path']}",
                parent=self.root):
            return
        try:
            os.remove(self.current["path"])
            mp = meta_path(self.current["path"])
            if os.path.exists(mp):
                os.remove(mp)
        except OSError as e:
            self.say(f"Delete failed: {e}", True)
            return
        name = self.current["name"]
        self.refresh()
        self.say(f"Deleted '{name}'.")

    def new_folder(self):
        sel = self.tree.selection()
        parent = ""
        if sel and sel[0] not in (ALL_NODE, FAV_NODE, RECENT_NODE):
            parent = sel[0]
        dlg = PromptDialog(self.root, "New Folder",
                           [("name", f"Folder name   (inside '{parent or 'library root'}')",
                             "entry", "")])
        if not dlg.result or not dlg.result["name"]:
            return
        rel = f"{parent}/{sanitize(dlg.result['name'])}" if parent else sanitize(dlg.result["name"])
        try:
            os.makedirs(os.path.join(self.library, *rel.split("/")), exist_ok=True)
        except OSError as e:
            self.say(f"Could not create folder: {e}", True)
            return
        self.refresh()
        self.say(f"Created folder '{rel}'.")

    def open_folder(self):
        target = os.path.dirname(self.current["path"]) if self.current else self.library
        try:
            os.startfile(target)
        except Exception as e:
            self.say(f"Could not open folder: {e}", True)

    def _on_close(self):
        if self._notes_dirty:
            self.save_notes(quiet=True)
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            save_prefs(self.prefs)
        except Exception:
            pass
        self.root.destroy()


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------

prefs = load_prefs()
library = prefs.get("library_path") or DEFAULT_LIBRARY
os.makedirs(library, exist_ok=True)

root = tk.Tk()
SCALE = resolve_scale(prefs, root)
app = FusionClipboard(root, library)
root.bind("<Escape>", lambda e: app._on_close())
root.lift()
root.attributes("-topmost", True)
root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
