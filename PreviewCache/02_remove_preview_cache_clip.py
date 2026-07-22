import os
import re

CACHE_COLORS = ("Green", "Chocolate")


def expand_media_paths(file_path):
    """A media pool item's 'File Path' for an image sequence uses bracket
    notation like 'name_[0100-0200].png' — expand it to the real per-frame
    files. Single-file paths are returned as-is."""
    m = re.match(r"^(.*)\[(\d+)-(\d+)\](\.[A-Za-z0-9]+)$", file_path)
    if not m:
        return [file_path]
    prefix, start, end, ext = m.groups()
    pad = len(start)
    return [f"{prefix}{str(i).zfill(pad)}{ext}" for i in range(int(start), int(end) + 1)]


def delete_media_files(file_path):
    """Delete the file(s) behind a media pool 'File Path'; returns the number
    deleted. Removes the containing folder too if it ends up empty."""
    deleted = 0
    for path in expand_media_paths(file_path):
        if os.path.exists(path):
            os.remove(path)
            deleted += 1
    parent = os.path.dirname(file_path)
    try:
        os.rmdir(parent)  # only succeeds if empty
    except OSError:
        pass
    return deleted

resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()
media_pool = project.GetMediaPool()


def tc_str_to_frame(tc_str, fps):
    drop_frame = ";" in tc_str
    h, m, s, f = map(int, tc_str.replace(";", ":").split(":"))
    fps_round = round(float(fps))
    total = (h * 3600 + m * 60 + s) * fps_round + f
    if drop_frame:
        # Subtract the frames that were dropped (2 per minute, except every 10th minute)
        drop = 4 if fps_round == 60 else 2
        total_minutes = 60 * h + m
        total -= drop * (total_minutes - total_minutes // 10)
    return total


if not timeline:
    print("Error: No timeline is currently open.")
else:
    fps = timeline.GetSetting("timelineFrameRate") or 24
    playhead = tc_str_to_frame(timeline.GetCurrentTimecode(), fps)
    track_count = timeline.GetTrackCount("video")

    # Find the highest-track preview cache clip that covers the playhead
    cache_clip = None
    for track_idx in range(track_count, 0, -1):
        items = timeline.GetItemListInTrack("video", track_idx) or []
        for item in items:
            if item.GetClipColor() in CACHE_COLORS and item.GetStart() <= playhead < item.GetEnd():
                cache_clip = item
                break
        if cache_clip:
            break

    if not cache_clip:
        print("No PreviewCache clip found at the playhead position.")
    else:
        source_item = cache_clip.GetMediaPoolItem()
        file_path = source_item.GetClipProperty("File Path") if source_item else ""

        timeline.DeleteClips([cache_clip])
        print("Removed clip from timeline.")

        if source_item:
            media_pool.DeleteClips([source_item])
            print("Removed clip from media pool.")

        if file_path:
            deleted = delete_media_files(file_path)
            if deleted:
                print(f"Deleted {deleted} source file(s): {file_path}")
            else:
                print(f"Source file(s) not found (already deleted?): {file_path}")

        print("Done.")
