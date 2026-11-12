import time
import json
import os
import re
from datetime import datetime
import tkinter as tk
from tkinter import ttk

if os.name == "nt":
    PREFS_FILE = os.path.join(os.environ["APPDATA"], "preview_cache_prefs.json")
else:
    PREFS_FILE = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                              "preview_cache_prefs.json")

SEQUENCE_FORMATS = ("png", "exr", "dpx", "tif", "tiff", "jpg", "jpeg", "bmp")
RENDER_START_SECONDS = 30     # nothing rendering this long after Start = it never started
RENDER_STALL_SECONDS = 120    # no progress for this long while rendering = stuck
FAILED_STATES = ("Failed", "Cancelled", "Background Render Cancelled",
                 "Remote Render Cancelled")


class PreviewCacheError(Exception):
    """A problem worded for the user."""


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
    drop_frame = ";" in tc_str
    h, m, s, f = map(int, tc_str.replace(";", ":").split(":"))
    fps_round = round(float(fps))
    total = (h * 3600 + m * 60 + s) * fps_round + f
    if drop_frame:
        drop = 4 if fps_round == 60 else 2
        total_minutes = 60 * h + m
        total -= drop * (total_minutes - total_minutes // 10)
    return total


def default_render_dir(project):
    """<Project Media folder>\\PreviewCache (created if missing), or None if the
    project media location is unset, missing on this machine, or not writable."""
    media_location = project.GetSetting("projectMediaLocation")
    if not media_location:
        return None
    default_dir = os.path.join(media_location, "PreviewCache")
    try:
        os.makedirs(default_dir, exist_ok=True)
    except OSError as e:
        print(f"Note: could not create '{default_dir}' ({e}) — using plain folder picker.")
        return None
    return default_dir


def preset_names(project):
    presets_dict = project.GetRenderPresets() or {}
    return [presets_dict[k] for k in sorted(presets_dict.keys())]


def wait_for_render(project, job_id, tick=None):
    """Wait for one render job. Returns its final status; raises if it never
    starts or stops making progress (it is stopped first) - a plain wait on
    IsRenderingInProgress() would hang forever on a job that never starts.
    tick() is called about once a second, so a window can stay responsive."""
    started = last_change = time.time()
    last_percent = None
    while True:
        status = project.GetRenderJobStatus(job_id) or {}
        state = status.get("JobStatus", "Unknown")
        if state == "Complete" or state in FAILED_STATES:
            return status
        percent = status.get("CompletionPercentage", 0)
        now = time.time()
        if percent != last_percent:
            last_percent, last_change = percent, now
        if state != "Rendering" and not project.IsRenderingInProgress() \
                and now - started > RENDER_START_SECONDS:
            raise PreviewCacheError(f"The render never started (job status '{state}').")
        if now - last_change > RENDER_STALL_SECONDS:
            project.StopRendering()
            raise PreviewCacheError(f"The render stalled at {percent}% for "
                                    f"{RENDER_STALL_SECONDS}s and was stopped.")
        if tick:
            tick(percent)
        time.sleep(1)


def render_range(project, timeline, target_dir, preset_name, tick=None):
    """Render the timeline's current In/Out with the preset, import the result
    into the PreviewCache bin and place it, green, on the first free video track
    above the range. Returns a one-line summary; raises PreviewCacheError.

    Shared by the menu script and the Preview Cache Queue, which calls it once
    per queued range and only starts the next range after this has returned -
    so a crash part-way through a long render loses one range, not all of them.
    """
    if not project.LoadRenderPreset(preset_name):
        raise PreviewCacheError(f"Could not load render preset '{preset_name}'.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    custom_name = f"{project.GetName()}_PreviewCache_{timestamp}"

    # Image-sequence presets write one file per frame — render those
    # into their own subfolder instead of littering the root folder.
    fmt_codec = project.GetCurrentRenderFormatAndCodec() or {}
    is_sequence = str(fmt_codec.get("format", "")).lower() in SEQUENCE_FORMATS

    render_dir = os.path.join(target_dir, custom_name) if is_sequence else target_dir
    os.makedirs(render_dir, exist_ok=True)

    # SelectAllFrames: False tells Resolve to use the In/Out range set on the timeline
    project.SetRenderSettings({
        "SelectAllFrames": False,
        "TargetDir": render_dir,
        "ExportAudio": False,
        "CustomName": custom_name,
    })

    job_id = project.AddRenderJob()
    if not job_id:
        raise PreviewCacheError("Failed to add render job. Make sure an In/Out range is "
                                "set on the timeline.")

    # MarkIn is in the same frame coordinate space as GetStart()/
    # GetEnd() on timeline items — the reliable recordFrame source.
    all_jobs = project.GetRenderJobList() or []
    job_details = next((j for j in all_jobs if j.get("JobId") == job_id), {})
    mark_in = job_details.get("MarkIn")

    print(f"Render job added: {custom_name} -> {target_dir}")
    print("Starting render...")
    if project.StartRendering([job_id]) is False and project.StartRendering(job_id) is False:
        raise PreviewCacheError("Resolve wouldn't start the render.")

    status = wait_for_render(project, job_id, tick)
    job_status = status.get("JobStatus", "Unknown")
    if job_status != "Complete":
        reason = status.get("Error") or ""
        raise PreviewCacheError(f"Render did not complete successfully. Status: {job_status}"
                                + (f" - {reason}" if reason else ""))

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

    if is_sequence:
        # Collect the frame-numbered files and import as one sequence
        frame_re = re.compile(r"^(.*?)(\d+)\.([A-Za-z0-9]+)$")
        frames = []
        for f in os.listdir(render_dir):
            m = frame_re.match(f)
            if m and f.startswith(custom_name):
                frames.append((int(m.group(2)), m.group(1), len(m.group(2)), m.group(3)))
        if not frames:
            raise PreviewCacheError(f"No rendered frames found in '{render_dir}'.")
        frames.sort()
        start_idx, prefix, pad, ext = frames[0]
        end_idx = frames[-1][0]
        render_path = os.path.join(render_dir, f"{prefix}%0{pad}d.{ext}")
        imported = media_pool.ImportMedia([{
            "FilePath": render_path,
            "StartIndex": start_idx,
            "EndIndex": end_idx,
        }])
    else:
        # Find the rendered file by name regardless of extension
        render_path = None
        for f in os.listdir(render_dir):
            if os.path.splitext(f)[0] == custom_name:
                render_path = os.path.join(render_dir, f)
                break
        if not render_path:
            raise PreviewCacheError(f"Could not find rendered file '{custom_name}' in "
                                    f"'{render_dir}'.")
        imported = media_pool.ImportMedia([render_path])

    if not imported:
        raise PreviewCacheError(f"Rendered, but could not import '{render_path}' into the "
                                f"media pool.")
    print(f"Imported '{render_path}' into PreviewCache bin.")

    clip = imported[0]
    fps = timeline.GetSetting("timelineFrameRate") or 24

    # Image sequences import at Resolve's default sequence
    # rate (usually 24) — force the timeline's frame rate.
    if is_sequence and not clip.SetClipProperty("FPS", str(fps)):
        print(f"Warning: could not set clip FPS to {fps}.")

    clip_total_frames = int(clip.GetClipProperty("Frames"))
    # Sequence clips index from their file numbers, not 0
    clip_start_frame = int(float(clip.GetClipProperty("Start") or 0))

    if mark_in is not None:
        record_frame = mark_in
    else:
        # Fallback: the clip's Start TC matches the timeline in-point
        record_frame = tc_str_to_frame(clip.GetClipProperty("Start TC"), fps)

    # Find the highest track with any clip overlapping the range
    clip_end = record_frame + clip_total_frames
    total_tracks = timeline.GetTrackCount("video")
    highest_occupied = 0
    for track_idx in range(1, total_tracks + 1):
        items = timeline.GetItemListInTrack("video", track_idx) or []
        if any(item.GetStart() < clip_end and item.GetEnd() > record_frame for item in items):
            highest_occupied = track_idx

    top_track = highest_occupied + 1
    if top_track > total_tracks:
        timeline.AddTrack("video")

    placed = media_pool.AppendToTimeline([{
        "mediaPoolItem": clip,
        "startFrame": clip_start_frame,
        "endFrame": clip_start_frame + clip_total_frames,
        "mediaType": 1,
        "trackIndex": top_track,
        "recordFrame": record_frame,
    }])
    timeline_item = placed[0] if placed else None
    if not timeline_item:
        raise PreviewCacheError(f"Rendered and imported '{os.path.basename(render_path)}', "
                                f"but could not place it on the timeline.")
    timeline_item.SetClipColor("Green")
    print(f"Placed on video track {top_track} at frame {record_frame} and colored green.")
    return f"V{top_track} at frame {record_frame}"


def main():
    resolve = bmd.scriptapp("Resolve")
    fusion = resolve.Fusion()
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("Error: No timeline is currently open.")
        return
    playhead_tc = timeline.GetCurrentTimecode()

    # Start the folder picker at <Project Media folder>\PreviewCache. Falls
    # back to a plain picker if that can't be used.
    default_dir = default_render_dir(project)
    print("Please select the render output folder...")
    target_dir = fusion.RequestDir(default_dir + os.sep) if default_dir else fusion.RequestDir()
    if not target_dir:
        print("Cancelled: No render path selected.")
        return

    names = preset_names(project)
    if not names:
        print("Error: No render presets found. Please create one in the Deliver page.")
        return
    prefs = load_prefs()
    preset_name = ask_preset(names, last_preset=prefs.get("last_preset"))
    if not preset_name:
        print("Cancelled: No preset selected.")
        return
    save_prefs({**prefs, "last_preset": preset_name})

    try:
        render_range(project, timeline, str(target_dir), preset_name)
    except PreviewCacheError as e:
        print(f"Error: {e}")
    finally:
        timeline.SetCurrentTimecode(playhead_tc)
        print(f"Playhead restored to {playhead_tc}.")


# Run from the Scripts menu it renders the current In/Out, as it always has.
# The Preview Cache Queue loads this file with PREVIEW_CACHE_AS_LIBRARY set, to
# call render_range() once per queued range.
if not globals().get("PREVIEW_CACHE_AS_LIBRARY"):
    main()
