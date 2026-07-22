import os
import re


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

if not timeline:
    print("Error: No timeline is currently open.")
else:
    # --- Find color-labeled preview cache clips on the timeline ---
    # Only clips carrying the cache color label are treated as preview cache.
    # Their source file paths determine what may be deleted from the bin and
    # disk — anything else in the PreviewCache bin is never touched.
    track_count = timeline.GetTrackCount("video")
    cache_clips = []
    cache_paths = set()

    for track_idx in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", track_idx)
        if not items:
            continue
        for item in items:
            if item.GetClipColor() in ("Green", "Chocolate"):
                cache_clips.append(item)
                source = item.GetMediaPoolItem()
                path = source.GetClipProperty("File Path") if source else ""
                if path:
                    cache_paths.add(path)

    if cache_clips:
        timeline.DeleteClips(cache_clips)
        print(f"Removed {len(cache_clips)} PreviewCache clip(s) from timeline.")
    else:
        print("No PreviewCache clips found on timeline.")

    # --- Delete only the matching items from the PreviewCache bin and disk ---
    root_folder = media_pool.GetRootFolder()
    preview_bin = None
    for folder in root_folder.GetSubFolderList():
        if folder.GetName() == "PreviewCache":
            preview_bin = folder
            break

    if not preview_bin:
        print("No PreviewCache bin found in media pool.")
    elif not cache_paths:
        print("Nothing to delete from the PreviewCache bin.")
    else:
        bin_items = preview_bin.GetClipList() or []
        to_delete = []
        skipped = 0
        for item in bin_items:
            path = item.GetClipProperty("File Path")
            if path in cache_paths:
                to_delete.append(item)
            else:
                skipped += 1

        if skipped:
            print(f"Left {skipped} unrelated item(s) in the PreviewCache bin untouched.")

        if not to_delete:
            print("No matching preview cache items found in the bin.")
        else:
            file_paths = [item.GetClipProperty("File Path") for item in to_delete]
            media_pool.DeleteClips(to_delete)
            print(f"Removed {len(to_delete)} item(s) from PreviewCache bin.")

            deleted_files = 0
            for path in file_paths:
                deleted = delete_media_files(path)
                if deleted:
                    deleted_files += deleted
                    print(f"Deleted {deleted} file(s): {path}")
                else:
                    print(f"File(s) not found (already deleted?): {path}")

            print(f"Deleted {deleted_files} source file(s) from disk.")

    print("Preview cache cleared.")
