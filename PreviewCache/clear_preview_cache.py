import os

resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()
media_pool = project.GetMediaPool()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    # --- Remove green clips from the timeline ---
    removed_from_timeline = 0
    track_count = timeline.GetTrackCount("video")
    green_clips = []

    for track_idx in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", track_idx)
        if not items:
            continue
        for item in items:
            if item.GetClipColor() == "Green":
                green_clips.append(item)

    if green_clips:
        timeline.DeleteClips(green_clips)
        removed_from_timeline = len(green_clips)
        print(f"Removed {removed_from_timeline} green clip(s) from timeline.")
    else:
        print("No green clips found on timeline.")

    # --- Delete items from the PreviewCache bin and source files from disk ---
    root_folder = media_pool.GetRootFolder()
    preview_bin = None
    for folder in root_folder.GetSubFolderList():
        if folder.GetName() == "PreviewCache":
            preview_bin = folder
            break

    if not preview_bin:
        print("No PreviewCache bin found in media pool.")
    else:
        items = preview_bin.GetClipList()
        if not items:
            print("PreviewCache bin is already empty.")
        else:
            file_paths = []
            for item in items:
                path = item.GetClipProperty("File Path")
                if path:
                    file_paths.append(path)

            media_pool.DeleteClips(items)
            print(f"Removed {len(items)} item(s) from PreviewCache bin.")

            deleted_files = 0
            for path in file_paths:
                if os.path.exists(path):
                    os.remove(path)
                    deleted_files += 1
                    print(f"Deleted: {path}")
                else:
                    print(f"File not found (already deleted?): {path}")

            print(f"Deleted {deleted_files} source file(s) from disk.")

    print("Preview cache cleared.")
