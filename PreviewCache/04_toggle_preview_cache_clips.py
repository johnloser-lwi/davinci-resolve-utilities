import json
import os

PREFS_FILE = os.path.expandvars(r"%APPDATA%\preview_cache_prefs.json")
COLOR_VISIBLE = "Green"
COLOR_HIDDEN = "Chocolate"


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    track_count = timeline.GetTrackCount("video")
    cache_clips = []

    for track_idx in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", track_idx) or []
        for item in items:
            if item.GetClipColor() in (COLOR_VISIBLE, COLOR_HIDDEN):
                cache_clips.append(item)

    if not cache_clips:
        print("No PreviewCache clips found on the timeline.")
    else:
        prefs = load_prefs()
        currently_visible = prefs.get("clips_enabled", True)

        if currently_visible:
            for clip in cache_clips:
                clip.SetProperty("Opacity", 0)
                clip.SetClipColor(COLOR_HIDDEN)
            save_prefs({**prefs, "clips_enabled": False})
            print(f"{len(cache_clips)} PreviewCache clip(s) hidden.")
        else:
            for clip in cache_clips:
                clip.SetProperty("Opacity", 100)
                clip.SetClipColor(COLOR_VISIBLE)
            save_prefs({**prefs, "clips_enabled": True})
            print(f"{len(cache_clips)} PreviewCache clip(s) visible.")
