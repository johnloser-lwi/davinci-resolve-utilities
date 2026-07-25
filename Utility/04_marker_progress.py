import json
import os
import tkinter as tk
from tkinter import messagebox

# Reports how many of the current timeline's markers already have supporting
# work built for them. Video track 1 (or whichever track(s) you tick as the
# base/raw-footage track) is ignored; a marker counts as "done" if ANY clip on
# any other video track overlaps its range, even partially. Audio is ignored.
#
# Only the marker colors actually present on the timeline are offered as
# filters, so you can report on just the colors you care about.
#
# Strictly read-only — nothing on the timeline is added, changed, or deleted.

PREFS_FILE = os.path.expandvars(r"%APPDATA%\marker_progress_prefs.json")

# Dark palette so the report doesn't glare next to Resolve's UI
BG = "#1e1e1e"
PANEL = "#252525"
FG = "#e0e0e0"
SUB = "#9a9a9a"
TRACK_BG = "#3a3a3a"
BAR_H = 16

# Approximations of Resolve's marker palette, for the swatches
MARKER_COLORS = {
    "Blue": "#3a6fd8", "Cyan": "#4ec3e0", "Green": "#4caf50", "Yellow": "#e0c020",
    "Red": "#d94c4c", "Pink": "#e08cc0", "Purple": "#8c5ad9", "Fuchsia": "#d94ca8",
    "Rose": "#e0a0a8", "Lavender": "#a89ce0", "Sky": "#8cc8e8", "Mint": "#a0e0b8",
    "Lemon": "#e8e070", "Sand": "#d9c08c", "Cocoa": "#a08060", "Cream": "#f0e8d0",
}


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


def frame_to_tc(frame, fps, drop_frame):
    """Absolute frame number -> timecode string, drop-frame aware."""
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


def ask_options(track_labels, color_counts, default_ignored, default_excluded):
    """Tracks to ignore + marker colors to include. Colors listed are only
    those actually present on the timeline. Returns (ignored, included)
    or (None, None) if cancelled."""
    result = [None, None]

    root = tk.Tk()
    root.title("Marker Progress")
    root.configure(bg=BG)
    root.resizable(False, False)

    cols = tk.Frame(root, bg=BG)
    cols.pack(padx=14, pady=(12, 4))

    # --- Tracks ---
    left = tk.Frame(cols, bg=BG)
    left.grid(row=0, column=0, sticky="nw", padx=(0, 24))
    tk.Label(left, text="Ignore these tracks", bg=BG, fg=FG,
             font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(0, 4))
    tk.Label(left, text="(your base / raw-footage track)", bg=BG, fg=SUB,
             font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 6))

    track_vars = {}
    for idx, label in track_labels:
        var = tk.BooleanVar(value=(idx in default_ignored))
        tk.Checkbutton(left, text=label, variable=var, anchor="w", bg=BG, fg=FG,
                       selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                       ).pack(fill="x")
        track_vars[idx] = var

    # --- Marker colors (only those in use) ---
    right = tk.Frame(cols, bg=BG)
    right.grid(row=0, column=1, sticky="nw")
    tk.Label(right, text="Include these markers", bg=BG, fg=FG,
             font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(0, 4))
    tk.Label(right, text=f"({len(color_counts)} color(s) on this timeline)", bg=BG, fg=SUB,
             font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 6))

    color_vars = {}
    for color, count in color_counts:
        var = tk.BooleanVar(value=(color not in default_excluded))
        row = tk.Frame(right, bg=BG)
        row.pack(fill="x")
        sw = tk.Canvas(row, width=11, height=11, highlightthickness=1,
                       highlightbackground="#555", bg=MARKER_COLORS.get(color, "#888888"))
        sw.pack(side="left", padx=(2, 6))
        tk.Checkbutton(row, text=f"{color} ({count})", variable=var, anchor="w",
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG).pack(side="left")
        color_vars[color] = var

    def set_all_colors(value):
        for var in color_vars.values():
            var.set(value)

    toggles = tk.Frame(right, bg=BG)
    toggles.pack(fill="x", pady=(6, 0))
    tk.Button(toggles, text="All", width=5, command=lambda: set_all_colors(True)).pack(side="left")
    tk.Button(toggles, text="None", width=5, command=lambda: set_all_colors(False)).pack(side="left", padx=4)

    def on_ok():
        result[0] = [idx for idx, var in track_vars.items() if var.get()]
        result[1] = [c for c, var in color_vars.items() if var.get()]
        root.destroy()

    btns = tk.Frame(root, bg=BG)
    btns.pack(pady=(4, 12))
    tk.Button(btns, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", width=10, command=root.destroy).pack(side="left", padx=4)

    root.lift()
    root.attributes("-topmost", True)
    root.mainloop()

    return result[0], result[1]


def show_report(timeline_name, subtitle, total, completed, lines):
    """Report window: headline stats, progress bar, and the untouched list."""
    remaining = total - completed
    pct = (completed / total * 100) if total else 0.0
    bar_color = "#e05252" if pct < 34 else ("#e0a52e" if pct < 67 else "#4caf72")

    root = tk.Tk()
    root.title("Marker Progress")
    root.configure(bg=BG)
    root.minsize(580, 400)

    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=16, pady=(14, 0))
    tk.Label(head, text=timeline_name, bg=BG, fg=FG,
             font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
    tk.Label(head, text=subtitle, bg=BG, fg=SUB,
             font=("Segoe UI", 9), anchor="w", justify="left").pack(fill="x")

    stats = tk.Frame(root, bg=BG)
    stats.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(stats, text=f"{pct:.1f}%", bg=BG, fg=bar_color,
             font=("Segoe UI", 22, "bold")).pack(side="left")
    tk.Label(stats, text=f"  {completed} of {total} done   ·   {remaining} remaining",
             bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side="left", anchor="s", pady=(0, 6))

    canvas = tk.Canvas(root, height=BAR_H, bg=TRACK_BG, highlightthickness=0)
    canvas.pack(fill="x", padx=16, pady=(0, 14))

    def draw_bar(event=None):
        canvas.delete("all")
        w = canvas.winfo_width()
        canvas.create_rectangle(0, 0, w, BAR_H, fill=TRACK_BG, width=0)
        if pct > 0:
            canvas.create_rectangle(0, 0, w * pct / 100, BAR_H, fill=bar_color, width=0)

    canvas.bind("<Configure>", draw_bar)

    if lines:
        tk.Label(root, text=f"Untouched markers ({len(lines)})", bg=BG, fg=FG,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=16)
    else:
        tk.Label(root, text="Everything is covered — nothing left to build.",
                 bg=BG, fg="#4caf72", font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(fill="x", padx=16)

    body = "\n".join(lines) if lines else ""

    text_frame = tk.Frame(root, bg=BG)
    text_frame.pack(fill="both", expand=True, padx=16, pady=(6, 0))
    scroll = tk.Scrollbar(text_frame)
    scroll.pack(side="right", fill="y")
    text = tk.Text(text_frame, wrap="word", bg=PANEL, fg=FG, relief="flat",
                   font=("Consolas", 9), yscrollcommand=scroll.set,
                   padx=8, pady=8, height=12)
    text.pack(side="left", fill="both", expand=True)
    scroll.config(command=text.yview)
    text.insert("1.0", body)
    text.config(state="disabled")  # read-only, still selectable/copyable

    def copy_all():
        root.clipboard_clear()
        root.clipboard_append(body)

    btns = tk.Frame(root, bg=BG)
    btns.pack(fill="x", padx=16, pady=12)
    tk.Button(btns, text="Close", width=10, command=root.destroy).pack(side="right", padx=(6, 0))
    if lines:
        tk.Button(btns, text="Copy list", width=10, command=copy_all).pack(side="right")

    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.mainloop()


resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline() if project else None

if not timeline:
    messagebox.showwarning("Marker Progress", "No timeline is currently open.")
else:
    markers = timeline.GetMarkers() or {}

    if not markers:
        messagebox.showinfo("Marker Progress", f"Timeline '{timeline.GetName()}' has no markers.")
    else:
        # Only offer colors that actually exist on this timeline, most used first
        counts = {}
        for data in markers.values():
            color = data.get("color") or "(no color)"
            counts[color] = counts.get(color, 0) + 1
        color_counts = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

        track_count = timeline.GetTrackCount("video")
        track_labels = []
        for idx in range(1, track_count + 1):
            name = timeline.GetTrackName("video", idx)
            label = f"V{idx}"
            if name and name != f"Video {idx}":
                label += f"   ({name})"
            track_labels.append((idx, label))

        prefs = load_prefs()
        ignored, included = ask_options(
            track_labels, color_counts,
            prefs.get("ignore_tracks", [1]),
            prefs.get("excluded_colors", []),
        )

        if ignored is None:
            print("Cancelled.")
        elif not included:
            messagebox.showinfo("Marker Progress", "No marker colors selected — nothing to report.")
        else:
            # Remember exclusions (not inclusions) so unseen colors default to on
            newly_excluded = [c for c, _ in color_counts if c not in included]
            remembered = [c for c in prefs.get("excluded_colors", []) if c not in included]
            save_prefs({
                **prefs,
                "ignore_tracks": ignored,
                "excluded_colors": sorted(set(newly_excluded) | set(remembered)),
            })

            checked_tracks = [i for i in range(1, track_count + 1) if i not in ignored]

            # Collect clip ranges from the tracks that count as "work done"
            clip_ranges = []
            for idx in checked_tracks:
                for item in timeline.GetItemListInTrack("video", idx) or []:
                    clip_ranges.append((item.GetStart(), item.GetEnd()))

            def has_coverage(m_start, m_end):
                for c_start, c_end in clip_ranges:
                    if not (c_end <= m_start or c_start >= m_end):
                        return True
                return False

            fps = timeline.GetSetting("timelineFrameRate") or 24
            drop_frame = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") == "1"
            start_frame = timeline.GetStartFrame()

            total = 0
            completed = 0
            not_done = []
            for rel_frame, data in markers.items():
                color = data.get("color") or "(no color)"
                if color not in included:
                    continue
                total += 1
                # Marker keys are relative to the timeline start; clip
                # GetStart()/GetEnd() are absolute — line them up before comparing.
                abs_start = start_frame + int(rel_frame)
                abs_end = abs_start + max(int(data.get("duration") or 1), 1)
                if has_coverage(abs_start, abs_end):
                    completed += 1
                else:
                    not_done.append({
                        "abs_start": abs_start,
                        "name": (data.get("name") or "").strip(),
                        "color": color,
                        "note": (data.get("note") or "").replace("\n", " ").strip(),
                    })

            lines = []
            for m in sorted(not_done, key=lambda x: x["abs_start"]):
                tc = frame_to_tc(m["abs_start"], fps, drop_frame)
                label = m["name"] or "(unnamed)"
                color = f"[{m['color']}]".ljust(11)
                line = f"{tc}  {color} {label}"
                if m["note"]:
                    line += f" :: {m['note']}"
                lines.append(line)

            subtitle = "Ignoring " + (", ".join(f"V{i}" for i in sorted(ignored)) or "nothing")
            if len(included) < len(color_counts):
                subtitle += "   ·   " + ", ".join(sorted(included)) + " markers only"
            if not checked_tracks:
                subtitle += "   ·   every track ignored, nothing counts as progress"

            show_report(timeline.GetName(), subtitle, total, completed, lines)
