import os
import re
from tkinter import Tk, messagebox

# Clears the preview cache across the WHOLE project, not just the open
# timeline.
#
# The order matters: a cache clip can be used on several timelines, so every
# usage is mapped first and removed before anything is deleted from the media
# pool or from disk. Deleting the file while another timeline still referenced
# it would leave that timeline with offline media.
#
# What counts as preview cache (deliberately narrow, so nothing else is ever
# touched):
#   - the media pool item lives in the PreviewCache bin, AND
#   - it is colour-tagged on a timeline, or its name matches the pattern the
#     render script produces ("<project>_PreviewCache_<date>_<time>")
#
# Anything else you happen to have dropped in the bin is left alone.

CACHE_COLORS = ("Green", "Chocolate")
CACHE_BIN = "PreviewCache"
CACHE_NAME = re.compile(r"_PreviewCache_\d{8}_\d{6}$")


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


def item_key(mpi):
    """Stable identity for a media pool item."""
    try:
        uid = mpi.GetUniqueId()
        if uid:
            return str(uid)
    except Exception:
        pass
    try:
        return mpi.GetClipProperty("File Path") or mpi.GetName()
    except Exception:
        return str(id(mpi))


def confirm(message):
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    answer = messagebox.askyesno("Clear Preview Cache", message, parent=root)
    root.destroy()
    return answer


resolve = bmd.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()

if not project:
    print("Error: No project is open.")
else:
    media_pool = project.GetMediaPool()
    original_timeline = project.GetCurrentTimeline()

    # --- 1. what is in the PreviewCache bin -------------------------------
    preview_bin = None
    for folder in media_pool.GetRootFolder().GetSubFolderList():
        if folder.GetName() == CACHE_BIN:
            preview_bin = folder
            break

    if not preview_bin:
        print(f"No '{CACHE_BIN}' bin found in the media pool. Nothing to clear.")
    else:
        bin_items = {}
        for clip in (preview_bin.GetClipList() or []):
            bin_items[item_key(clip)] = clip
        print(f"'{CACHE_BIN}' bin holds {len(bin_items)} item(s).")

        # --- 2. map every usage across every timeline ---------------------
        usages = {}          # media pool key -> [(timeline, item)]
        tagged = set()       # keys seen with a cache colour
        timelines = []
        for i in range(1, (project.GetTimelineCount() or 0) + 1):
            tl = project.GetTimelineByIndex(i)
            if tl:
                timelines.append(tl)

        scanned = 0
        for tl in timelines:
            for ti in range(1, (tl.GetTrackCount("video") or 0) + 1):
                for item in (tl.GetItemListInTrack("video", ti) or []):
                    scanned += 1
                    source = item.GetMediaPoolItem()
                    if not source:
                        continue
                    key = item_key(source)
                    if key not in bin_items:
                        continue          # not a PreviewCache item at all
                    usages.setdefault(key, []).append((tl, item))
                    if item.GetClipColor() in CACHE_COLORS:
                        tagged.add(key)

        # --- 3. decide what is genuinely preview cache --------------------
        doomed = {}
        for key, clip in bin_items.items():
            name = clip.GetName() or ""
            if key in tagged or CACHE_NAME.search(name):
                doomed[key] = clip

        skipped = len(bin_items) - len(doomed)
        used_elsewhere = sum(1 for k in doomed if len(usages.get(k, [])) > 0)
        total_usages = sum(len(usages.get(k, [])) for k in doomed)
        affected = sorted({tl.GetName() for k in doomed
                           for tl, _item in usages.get(k, [])})

        print(f"Scanned {scanned} item(s) across {len(timelines)} timeline(s).")
        print(f"{len(doomed)} cache item(s) to remove, used {total_usages} "
              f"time(s) on {len(affected)} timeline(s).")
        if skipped:
            print(f"Leaving {skipped} unrelated item(s) in the bin untouched.")

        if not doomed:
            print("Nothing to clear.")
        elif not confirm(
                f"Remove {len(doomed)} preview cache item(s)?\n\n"
                f"{total_usages} clip(s) will be deleted from "
                f"{len(affected)} timeline(s):\n"
                + ("\n".join("  " + n for n in affected[:8]) or "  (none)")
                + ("\n  ..." if len(affected) > 8 else "")
                + "\n\nThe media pool items and their files on disk will be "
                  "deleted too. This cannot be undone."):
            print("Cancelled.")
        else:
            # --- 4. remove every usage FIRST, timeline by timeline --------
            by_timeline = {}
            for key in doomed:
                for tl, item in usages.get(key, []):
                    by_timeline.setdefault(tl.GetName(), (tl, []))[1].append(item)

            removed = 0
            for name, (tl, items) in by_timeline.items():
                try:
                    project.SetCurrentTimeline(tl)
                    if tl.DeleteClips(items):
                        removed += len(items)
                        print(f"  removed {len(items)} clip(s) from '{name}'")
                    else:
                        print(f"  could not remove clips from '{name}'")
                except Exception as e:
                    print(f"  error clearing '{name}': {e}")

            if original_timeline:
                try:
                    project.SetCurrentTimeline(original_timeline)
                except Exception:
                    pass

            # --- 5. only now touch the media pool and the files -----------
            file_paths = []
            for clip in doomed.values():
                path = clip.GetClipProperty("File Path")
                if path:
                    file_paths.append(path)

            if media_pool.DeleteClips(list(doomed.values())):
                print(f"Removed {len(doomed)} item(s) from the {CACHE_BIN} bin.")
            else:
                print(f"Warning: could not remove the items from the {CACHE_BIN} bin.")

            deleted_files = 0
            for path in file_paths:
                count = delete_media_files(path)
                if count:
                    deleted_files += count
                else:
                    print(f"  file(s) not found (already deleted?): {path}")

            print(f"\nDone. {removed} timeline clip(s) removed, "
                  f"{len(doomed)} cache item(s) deleted, "
                  f"{deleted_files} file(s) removed from disk.")
