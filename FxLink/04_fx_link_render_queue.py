import json
import os
import tkinter as tk
from tkinter import ttk

PREFS_FILE = os.path.expandvars(r"%APPDATA%\fxlink_prefs.json")


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


def ask_preset(preset_names, last_preset=None):
    selected = [None]

    root = tk.Tk()
    root.title("Select Render Preset")
    root.resizable(False, False)

    tk.Label(root, text="Render Preset:", padx=12, pady=8).pack()

    combo = ttk.Combobox(root, values=preset_names, state="readonly", width=40)
    default_idx = preset_names.index(last_preset) if last_preset in preset_names else 0
    combo.current(default_idx)
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
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    print("Waiting for user to select a render directory...")
    target_dir = fusion.RequestDir()

    if not target_dir:
        print("Cancelled: No render path selected.")
    else:
        target_dir = str(target_dir)

        presets_dict = project.GetRenderPresets()
        preset_names = [presets_dict[k] for k in sorted(presets_dict.keys())]

        if not preset_names:
            print("Error: No render presets found. Please create one in the Deliver page.")
        else:
            prefs = load_prefs()
            preset_name = ask_preset(preset_names, last_preset=prefs.get("last_preset"))

            if not preset_name:
                print("Cancelled: No preset selected.")
            elif not project.LoadRenderPreset(preset_name):
                print(f"Error: Could not load render preset '{preset_name}'.")
            else:
                save_prefs({**prefs, "last_preset": preset_name})

                markers = timeline.GetMarkers()

                fx_markers = []
                if markers:
                    for frame_id, marker_data in markers.items():
                        if marker_data.get('name') == 'Fx Link':
                            fx_markers.append({
                                'start': int(frame_id),
                                'duration': int(marker_data.get('duration', 1))
                            })

                fx_markers = sorted(fx_markers, key=lambda k: k['start'])

                if not fx_markers:
                    print("No markers named 'Fx Link' found on the timeline.")
                else:
                    print(f"Found {len(fx_markers)} 'Fx Link' markers. Adding to Render Queue...")

                    for index, marker in enumerate(fx_markers, start=1):
                        start_frame = marker['start']
                        end_frame = start_frame + marker['duration'] - 1
                        job_name = f"FxLink_{index:03d}"

                        timeline.ClearMarkInOut()
                        timeline.SetMarkInOut(start_frame, end_frame)

                        project.SetRenderSettings({
                            "SelectAllFrames": False,
                            "CustomName": job_name,
                            "TargetDir": target_dir,
                        })

                        project.AddRenderJob()
                        print(f"Added Job: {job_name} -> {target_dir} (In: {start_frame}, Out: {end_frame})")

                    print("All jobs added successfully!")
