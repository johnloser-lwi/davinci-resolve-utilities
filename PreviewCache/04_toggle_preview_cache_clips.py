import os
import sys

# Shared helpers (resolve_utils.py) live outside Resolve's Scripts menu - deploy puts them there.
_LIB = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~/Library/Application Support"),
                    "ResolveUtilities", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
sys.modules.pop("resolve_utils", None)      # Resolve keeps modules loaded between runs
import resolve_utils as ru


PREFS_FILE = ru.data_path("preview_cache_prefs.json")
COLOR_VISIBLE = "Green"
COLOR_HIDDEN = "Chocolate"


def load_prefs():
    return ru.read_json(PREFS_FILE, {})


def save_prefs(prefs):
    ru.write_json(PREFS_FILE, prefs)


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
