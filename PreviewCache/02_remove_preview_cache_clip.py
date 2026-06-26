import os

CACHE_COLORS = ("Green", "Chocolate")

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

        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted source file: {file_path}")
        elif file_path:
            print(f"Source file not found (already deleted?): {file_path}")

        print("Done.")
