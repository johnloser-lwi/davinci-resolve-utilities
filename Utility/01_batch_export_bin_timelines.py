import json
import os
import tkinter as tk
from tkinter import ttk

# Batch-queues every timeline in the CURRENTLY SELECTED media pool bin for
# export with a chosen render preset — one output file per timeline named
# after the timeline, written flat into a chosen destination folder. Jobs are
# ADDED to the render queue (not started); review them in the Deliver page and
# press Render.
#
# Select the bin in the media pool before running.

PREFS_FILE = os.path.expandvars(r"%APPDATA%\batch_export_prefs.json")


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


def sanitize(name):
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "timeline"


def ask_preset(preset_names, last_preset=None):
    selected = [None]

    root = tk.Tk()
    root.title("Select Render Preset")
    root.resizable(False, False)

    tk.Label(root, text="Render Preset:", padx=12, pady=8).pack()

    combo = ttk.Combobox(root, values=preset_names, state="readonly", width=44)
    combo.current(preset_names.index(last_preset) if last_preset in preset_names else 0)
    combo.pack(padx=12, pady=4)

    def on_ok():
        selected[0] = combo.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=4)

    root.lift()
    root.attributes("-topmost", True)
    root.mainloop()

    return selected[0]


resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion()

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
media_pool = project.GetMediaPool()

if not project:
    print("Error: No project is open.")
else:
    chosen_folder = media_pool.GetCurrentFolder()
    if not chosen_folder:
        print("Error: No bin is selected in the media pool.")
    else:
        bin_name = chosen_folder.GetName()

        # Timelines stored in a bin appear as clips with Type == "Timeline"
        tl_names = [
            clip.GetName()
            for clip in chosen_folder.GetClipList() or []
            if clip.GetClipProperty("Type") == "Timeline"
        ]

        if not tl_names:
            print(f"No timelines found in the selected bin '{bin_name}'.")
        else:
            presets_dict = project.GetRenderPresets()
            preset_names = [presets_dict[k] for k in sorted(presets_dict.keys())]

            if not preset_names:
                print("Error: No render presets found. Please create one in the Deliver page.")
            else:
                prefs = load_prefs()
                print(f"Selected bin '{bin_name}' has {len(tl_names)} timeline(s).")
                preset_name = ask_preset(preset_names, last_preset=prefs.get("last_preset"))

                if not preset_name:
                    print("Cancelled: No preset selected.")
                else:
                    print("Select the destination folder...")
                    target_dir = fusion.RequestDir()

                    if not target_dir:
                        print("Cancelled: No destination selected.")
                    elif not project.LoadRenderPreset(preset_name):
                        print(f"Error: Could not load render preset '{preset_name}'.")
                    else:
                        target_dir = str(target_dir)
                        save_prefs({**prefs, "last_preset": preset_name})

                        original_timeline = project.GetCurrentTimeline()

                        # Map timeline names to Timeline objects (needed to render them)
                        name_to_tl = {}
                        for i in range(1, project.GetTimelineCount() + 1):
                            tl = project.GetTimelineByIndex(i)
                            name_to_tl.setdefault(tl.GetName(), []).append(tl)

                        print(f"Queuing {len(tl_names)} timeline(s) from '{bin_name}' -> {target_dir}")

                        queued = 0
                        for name in tl_names:
                            matches = name_to_tl.get(name, [])
                            if not matches:
                                print(f"  Skipped '{name}': timeline object not found.")
                                continue
                            if len(matches) > 1:
                                print(f"  Note: multiple timelines named '{name}' — using the first.")

                            tl = matches[0]
                            project.SetCurrentTimeline(tl)
                            # SelectAllFrames: True renders the entire timeline
                            project.SetRenderSettings({
                                "SelectAllFrames": True,
                                "TargetDir": target_dir,
                                "CustomName": sanitize(name),
                            })
                            if project.AddRenderJob():
                                queued += 1
                                print(f"  Queued: {name}")
                            else:
                                print(f"  FAILED to queue: {name}")

                        if original_timeline:
                            project.SetCurrentTimeline(original_timeline)

                        print(f"\nDone. {queued} of {len(tl_names)} timeline(s) added to the render queue.")
                        print("Open the Deliver page and press Render to start.")
