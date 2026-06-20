import time
import json
import os
from datetime import datetime
import tkinter as tk
from tkinter import ttk

PREFS_FILE = os.path.expandvars(r"%APPDATA%\preview_cache_prefs.json")


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


def tc_str_to_frame(tc_str, fps):
    h, m, s, f = map(int, tc_str.split(":"))
    return ((h * 3600 + m * 60 + s) * round(float(fps))) + f


def consolidate_green_clips(timeline, media_pool):
    top_track = timeline.GetTrackCount("video")

    to_move = []
    for track_idx in range(1, top_track):
        items = timeline.GetItemListInTrack("video", track_idx)
        if not items:
            continue
        for item in items:
            if item.GetClipColor() == "Green":
                to_move.append(item)

    for item in to_move:
        record_frame = item.GetStart()
        source_item = item.GetMediaPoolItem()
        total_frames = int(source_item.GetClipProperty("Frames"))

        timeline.DeleteClips([item])

        clip_info = {
            "mediaPoolItem": source_item,
            "startFrame": 0,
            "endFrame": total_frames,
            "mediaType": 1,
            "trackIndex": top_track,
            "recordFrame": record_frame,
        }
        placed = media_pool.AppendToTimeline([clip_info])
        if placed and placed[0]:
            placed[0].SetClipColor("Green")
            print(f"Moved green clip to track {top_track} at frame {record_frame}.")
        else:
            print(f"Warning: Could not move green clip at frame {record_frame} to top track.")


resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion()

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    project_name = project.GetName()
    playhead_tc = timeline.GetCurrentTimecode()

    print("Please select the render output folder...")
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

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                custom_name = f"{project_name}_PreviewCache_{timestamp}"

                # SelectAllFrames: False tells Resolve to use the In/Out range set on the timeline
                project.SetRenderSettings({
                    "SelectAllFrames": False,
                    "TargetDir": target_dir,
                    "ExportAudio": False,
                    "CustomName": custom_name,
                })

                job_id = project.AddRenderJob()

                if not job_id:
                    print("Error: Failed to add render job. Make sure an In/Out range is set on the timeline.")
                else:
                    print(f"Render job added: {custom_name} -> {target_dir}")
                    print("Starting render...")

                    project.StartRendering(job_id)

                    while project.IsRenderingInProgress():
                        time.sleep(1)

                    status = project.GetRenderJobStatus(job_id)
                    job_status = status.get("JobStatus", "Unknown")

                    if job_status != "Complete":
                        print(f"Render did not complete successfully. Status: {job_status}")
                    else:
                        print("Render complete. Importing into media pool...")

                        media_pool = project.GetMediaPool()
                        root_folder = media_pool.GetRootFolder()

                        preview_bin = None
                        for folder in root_folder.GetSubFolderList():
                            if folder.GetName() == "PreviewCache":
                                preview_bin = folder
                                break

                        if not preview_bin:
                            preview_bin = media_pool.AddSubFolder(root_folder, "PreviewCache")
                            print("Created 'PreviewCache' bin.")

                        media_pool.SetCurrentFolder(preview_bin)

                        render_path = target_dir.rstrip("/\\") + "/" + custom_name + ".mov"
                        imported = media_pool.ImportMedia([render_path])

                        if not imported:
                            print(f"Warning: Could not import '{render_path}' into media pool.")
                        else:
                            print(f"Imported '{render_path}' into PreviewCache bin.")

                            clip = imported[0]
                            fps = timeline.GetSetting("timelineFrameRate") or 24
                            clip_total_frames = int(clip.GetClipProperty("Frames"))

                            # The clip's Start TC matches the timeline in-point since that's where rendering began
                            start_tc_str = clip.GetClipProperty("Start TC")
                            record_frame = tc_str_to_frame(start_tc_str, fps)

                            # Add a new video track on top then place the clip onto it
                            timeline.AddTrack("video")
                            top_track = timeline.GetTrackCount("video")

                            clip_info = {
                                "mediaPoolItem": clip,
                                "startFrame": 0,
                                "endFrame": clip_total_frames,
                                "mediaType": 1,
                                "trackIndex": top_track,
                                "recordFrame": record_frame,
                            }

                            placed = media_pool.AppendToTimeline([clip_info])

                            timeline_item = placed[0] if placed else None
                            if not timeline_item:
                                print("Warning: Could not place clip onto timeline.")
                            else:
                                timeline_item.SetClipColor("Green")
                                print(f"Placed on video track {top_track} at frame {record_frame} and colored green.")

                            print("Consolidating green clips to top track...")
                            consolidate_green_clips(timeline, media_pool)

                            timeline.SetCurrentTimecode(playhead_tc)
                            print(f"Playhead restored to {playhead_tc}.")
