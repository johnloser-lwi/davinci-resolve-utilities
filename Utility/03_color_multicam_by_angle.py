import json
import os
import tkinter as tk
from tkinter import ttk

# Colors every multicam clip on the current timeline according to which angle
# it is cut to. The selected angle is read from the timeline item's name
# ("<multicam name> - <angle name>"); the API cannot look inside a multicam
# clip, so the angle-to-color mapping is chosen in a small dialog and
# remembered per angle name in prefs.

PREFS_FILE = os.path.expandvars(r"%APPDATA%\multicam_color_prefs.json")
SKIP = "— leave as is —"

CLIP_COLORS = [
    "Orange", "Apricot", "Yellow", "Lime", "Olive", "Green", "Teal", "Navy",
    "Blue", "Purple", "Violet", "Pink", "Tan", "Beige", "Brown", "Chocolate",
]

# Defaults cycled through for angles seen for the first time
DEFAULT_CYCLE = ["Orange", "Blue", "Green", "Purple", "Yellow", "Teal", "Pink", "Brown"]


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


def ask_angle_colors(angle_names, prefs):
    """One color dropdown per angle. Returns {angle_name: color} for angles
    not set to skip, or None if cancelled."""
    result = [None]

    root = tk.Tk()
    root.title("Multicam Angle Colors")
    root.resizable(False, False)

    combos = {}
    options = [SKIP] + CLIP_COLORS
    for row, angle in enumerate(angle_names):
        tk.Label(root, text=angle, padx=12, pady=4, anchor="w", width=24).grid(row=row, column=0, sticky="w")
        combo = ttk.Combobox(root, values=options, state="readonly", width=18)
        default = prefs.get(angle, DEFAULT_CYCLE[row % len(DEFAULT_CYCLE)])
        combo.set(default if default in options else SKIP)
        combo.grid(row=row, column=1, padx=12, pady=4)
        combos[angle] = combo

    def on_ok():
        result[0] = {a: c.get() for a, c in combos.items() if c.get() != SKIP}
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.grid(row=len(angle_names), column=0, columnspan=2, pady=10)
    tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=4)

    root.lift()
    root.attributes("-topmost", True)
    root.mainloop()

    return result[0]


resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    # Collect multicam items grouped by their selected angle name
    items_by_angle = {}
    for track_idx in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", track_idx) or []:
            source = item.GetMediaPoolItem()
            if not source or source.GetClipProperty("Type") != "Multicam":
                continue
            name = item.GetName()
            if " - " not in name:
                continue
            angle = name.rsplit(" - ", 1)[1]
            items_by_angle.setdefault(angle, []).append(item)

    if not items_by_angle:
        print("No multicam clips found on the current timeline.")
    else:
        angle_names = sorted(items_by_angle.keys())
        counts = {a: len(items_by_angle[a]) for a in angle_names}
        print("Found multicam clips: " + ", ".join(f"{a} ({counts[a]})" for a in angle_names))

        prefs = load_prefs()
        mapping = ask_angle_colors(angle_names, prefs)

        if mapping is None:
            print("Cancelled.")
        elif not mapping:
            print("All angles set to leave as is — nothing to do.")
        else:
            save_prefs({**prefs, **mapping})

            recolored = 0
            for angle, color in mapping.items():
                for item in items_by_angle[angle]:
                    if item.SetClipColor(color):
                        recolored += 1
                print(f"{angle} -> {color} ({len(items_by_angle[angle])} clip(s))")

            print(f"Done. {recolored} clip(s) recolored.")
