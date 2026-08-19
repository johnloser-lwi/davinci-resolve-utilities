import ctypes
import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

# Nest Selected Clips — build a nested timeline from the selected clips,
# non-destructively.
#
# Unlike a compound clip, a nested timeline is a real project timeline, so the
# scripting API can see inside it: GetSelectedClips, transforms and the align
# tools all work within it. Compound clips are a black box to scripting.
#
# How it works, and why:
#   1. The source timeline is DUPLICATED. A duplicate keeps every clip's
#      transform, grade and Fusion comp — rebuilding from media pool items
#      would throw all of that away.
#   2. Everything except the selected clips is removed from the duplicate.
#   3. The originals are DISABLED, never deleted, so nothing is lost.
#   4. The nest is placed on a new track above, covering the same span.
#
# The duplicate also inherits the source timeline's resolution and frame rate,
# which avoids the usual nested-timeline pitfall of unwanted rescaling.

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

        self.colour_var = tk.BooleanVar(value=bool(self.prefs.get("match_colour", True)))
        tk.Checkbutton(opts, text="Give the nest the first clip's colour",
                       variable=self.colour_var, command=self._save_opts,
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, font=FONT(9), highlightthickness=0,
                       anchor="w").pack(fill="x")

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
        make_button(actions, "Create Nested Timeline", self.do_nest,
                    primary=True).pack(side="left")

        tk.Label(content,
                 text="The nest is a duplicate of this timeline with everything "
                      "but the selection removed, so transforms, grades and "
                      "Fusion comps survive. It inherits the parent's resolution "
                      "and frame rate. Originals are disabled, not deleted, "
                      "and the nest is filed into the PreComps bin.",
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
        self.prefs["match_colour"] = bool(self.colour_var.get())
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
            self.say("Ready.")
        else:
            self.info.config(text="0 clips selected")
            self.say("Select clips on the timeline. Clips inside a compound clip "
                     "can't be read by the API.", True)

    def do_nest(self):
        project = current_project()
        source = current_timeline()
        if not project or not source:
            self.say("No timeline open.", True)
            return
        include_audio = bool(self.audio_var.get())
        # Reuse the records the table is already showing — what you see listed
        # is exactly what gets nested, and it avoids re-querying every clip.
        clips = self.clips or selected_clips(include_audio)
        if not clips:
            self.say("Nothing selected.", True)
            return

        min_start = min(c["start"] for c in clips)
        max_end = max(c["end"] for c in clips)
        keep = set()
        lowest_track = None
        highest_track = 0
        has_audio = False
        for c in clips:
            kind, idx = c["kind"], c["idx"]
            keep.add((kind, idx, c["start"]))
            if kind == "audio":
                has_audio = True
            else:
                if idx:
                    highest_track = max(highest_track, idx)
                    if lowest_track is None or idx < lowest_track:
                        lowest_track = idx
        lowest_track = lowest_track or 1
        highest_track = highest_track or lowest_track

        # Colour of the earliest clip, applied to the nest afterwards
        source_colour = ""
        if self.colour_var.get():
            try:
                source_colour = clips[0]["item"].GetClipColor() or ""
            except Exception:
                source_colour = ""

        name = unique_timeline_name(project, self.name_var.get().strip() or "Nest")

        print(f"\nNesting {len(clips)} clip(s) into '{name}':")

        # 1. Duplicate — this is what preserves per-clip settings
        try:
            nest = source.DuplicateTimeline(name)
        except Exception as e:
            self.say(f"DuplicateTimeline failed: {e}", True)
            return
        if not nest:
            self.say("DuplicateTimeline returned nothing.", True)
            return
        print(f"  duplicated timeline -> {nest.GetName()}")

        # 2. Strip the duplicate down to just the selection
        removed = 0
        try:
            project.SetCurrentTimeline(nest)

            # Only tracks that actually hold a kept clip need item-by-item
            # inspection. Everything else is removed a whole track at a time,
            # so the cost scales with the number of TRACKS rather than with
            # every item on a long timeline.
            kept_tracks = {(k, i) for (k, i, _s) in keep}

            doomed = []
            for kind, ti in sorted(kept_tracks):
                for item in (nest.GetItemListInTrack(kind, ti) or []):
                    try:
                        if (kind, ti, item.GetStart()) in keep:
                            continue
                    except Exception:
                        pass
                    doomed.append(item)

            if doomed:
                if nest.DeleteClips(doomed):
                    removed = len(doomed)
                else:
                    for i in range(0, len(doomed), 200):
                        chunk = doomed[i:i + 200]
                        if nest.DeleteClips(chunk):
                            removed += len(chunk)

            # Drop whole tracks that hold nothing we keep. Descending order
            # keeps the remaining indices valid as we go.
            dropped = 0
            for kind in ("video", "audio"):
                total = nest.GetTrackCount(kind) or 0
                remaining = total
                for ti in range(total, 0, -1):
                    if (kind, ti) in kept_tracks:
                        continue
                    if kind == "video" and remaining <= 1:
                        break
                    try:
                        if nest.DeleteTrack(kind, ti):
                            dropped += 1
                            remaining -= 1
                    except Exception:
                        pass
            print(f"  stripped the nest: {removed} clip(s), {dropped} track(s) removed")
        except Exception as e:
            print(f"  WARNING: could not fully strip the nest: {e}")
        finally:
            try:
                project.SetCurrentTimeline(source)
            except Exception:
                pass

        # 3. Handle the originals — only now that the nest exists
        mode = self.originals_var.get()
        handled, action = 0, "left alone"
        if mode == "Disable":
            for c in clips:
                try:
                    if c["item"].SetClipEnabled(False):
                        handled += 1
                except Exception as e:
                    print(f"  could not disable {c['name']}: {e}")
            action = "disabled"
        elif mode == "Delete":
            try:
                if source.DeleteClips([c["item"] for c in clips]):
                    handled, action = len(clips), "deleted"
                else:
                    action = "left alone (delete refused)"
            except Exception as e:
                print(f"  DeleteClips failed: {e}")
                action = "left alone (delete failed)"
        print(f"  {handled} original clip(s) {action}")

        # 4. Place the nest above, covering the same span
        media_pool = project.GetMediaPool()
        mpi = find_timeline_media_item(media_pool, name)
        if not mpi:
            self.say(f"Nest '{name}' created, but it wasn't found in the media "
                     f"pool to place. Drag it in manually.", True)
            return

        try:
            mpi_start = int(float(mpi.GetClipProperty("Start") or 0))
        except Exception:
            mpi_start = 0
        offset = min_start - (source.GetStartFrame() or 0)
        start_frame = mpi_start + offset
        # Place the nest where the selection sat, without breaking layer order.
        # Search upward from the lowest selected track for a track that is
        # actually clear across the span. Anything the originals still occupy
        # (Disable / Leave as is) counts as blocked, since they are physically
        # still there. Staying within the selection's own track range means the
        # nest can never end up above clips that were above the selection.
        target_track = None
        for candidate in range(lowest_track, highest_track + 1):
            if track_is_free(source, candidate, min_start, max_end):
                target_track = candidate
                break

        if target_track is None:
            # Every track the selection spanned is blocked, so make room
            # directly above it — still below whatever sat above the selection.
            insert_at = highest_track + 1
            try:
                source.AddTrack("video", {"index": insert_at})
            except Exception:
                source.AddTrack("video")
            target_track = insert_at
            print(f"  no free track in V{lowest_track}-V{highest_track}; "
                  f"inserted a new V{insert_at}")
        else:
            print(f"  placing on existing V{target_track}")

        clip_info = {
            "mediaPoolItem": mpi,
            "startFrame": start_frame,
            "endFrame": start_frame + (max_end - min_start),
            "trackIndex": target_track,
            "recordFrame": min_start,
        }
        if not (include_audio and has_audio):
            clip_info["mediaType"] = 1      # video only; omitting it brings audio too
        elif (source.GetTrackCount("audio") or 0) < 1:
            source.AddTrack("audio")

        placed = media_pool.AppendToTimeline([clip_info])

        # Carry the first clip's colour onto the nest
        if placed and source_colour:
            try:
                if placed[0].SetClipColor(source_colour):
                    print(f"  coloured the nest {source_colour}")
            except Exception as e:
                print(f"  could not set clip colour: {e}")

        # File the nest alongside the other pre-comps
        filed = ""
        precomps, created_bin = find_or_create_precomps(media_pool)
        if precomps:
            try:
                if media_pool.MoveClips([mpi], precomps):
                    filed = " Filed into " + PRECOMPS_BIN + "."
                    print("  moved into the " + PRECOMPS_BIN + " bin"
                          + (" (bin created)" if created_bin else ""))
                else:
                    print("  could not move the nest into " + PRECOMPS_BIN)
            except Exception as e:
                print("  MoveClips failed: " + str(e))

        if placed:
            print(f"  placed nest on V{target_track} at frame {min_start}")
            self.say(f"Created '{name}' — {len(clips)} clip(s) nested, "
                     f"{handled} original(s) {action}, placed on V{target_track}."
                     + filed)
        else:
            print("  FAILED to place the nest on the timeline")
            self.say(f"Created '{name}' but could not place it — "
                     f"drag it from the media pool onto V{target_track}." + filed, True)
        self.rescan()

    def _on_close(self):
        try:
            self.prefs["geometry"] = self.root.geometry()
            self.prefs["geometry_scale"] = round(SCALE, 3)
            self.prefs["originals"] = self.originals_var.get()
            self.prefs["include_audio"] = bool(self.audio_var.get())
            self.prefs["match_colour"] = bool(self.colour_var.get())
            save_prefs(self.prefs)
        except Exception:
            pass
        self.root.destroy()


prefs = load_prefs()
root = tk.Tk()
SCALE = resolve_scale(prefs, root)
app = NestPanel(root)
root.lift()
root.attributes("-topmost", True)
if not prefs.get("always_on_top", True):
    root.after(300, lambda: root.attributes("-topmost", False))
root.mainloop()
