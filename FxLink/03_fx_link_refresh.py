import os
import re

CLIP_COLOR = "Pink"

# Forces Resolve to re-read every clip in the FxLink bin after After Effects has
# overwritten its Output files. Run this if the timeline doesn't pick up the new
# render on its own.
#
# Two kinds of clip need two different refreshes:
#   - a movie is replaced with its own file path (ReplaceClip), which makes
#     Resolve re-read it
#   - an image sequence reports its path as "name_[00000-00100].png", which is
#     not a real file, so ReplaceClip can't be pointed at it. Instead it is
#     relinked to the folder its frames already sit in (RelinkClips). That can't
#     break the link - the frames are exactly where they were - and it makes
#     Resolve rescan them.

SEQUENCE_PATH = re.compile(r"\[\d+-\d+\]")

resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
media_pool = project.GetMediaPool()
root_folder = media_pool.GetRootFolder()

ae_bin = None
for folder in root_folder.GetSubFolderList():
    if folder.GetName() == "FxLink":
        ae_bin = folder
        break

if not ae_bin:
    print("No 'FxLink' bin found in the media pool. Nothing to refresh.")
else:
    clips = ae_bin.GetClipList() or []
    if not clips:
        print("The FxLink bin is empty. Nothing to refresh.")
    else:
        refreshed = 0
        refreshed_paths = set()
        for clip in clips:
            file_path = clip.GetClipProperty("File Path")
            name = clip.GetName()
            if not file_path:
                print(f"Skipped '{name}': no file path.")
                continue

            if SEQUENCE_PATH.search(os.path.basename(file_path)):
                folder = os.path.dirname(file_path)
                if not os.path.isdir(folder):
                    print(f"Skipped '{name}': sequence folder not found at {folder}")
                elif media_pool.RelinkClips([clip], folder):
                    print(f"Refreshed sequence: {name}")
                    refreshed_paths.add(file_path)
                    refreshed += 1
                else:
                    print(f"Failed to refresh sequence: {name}")
                continue

            if not os.path.exists(file_path):
                print(f"Skipped '{name}': file not found at {file_path}")
            elif clip.ReplaceClip(file_path):
                print(f"Refreshed: {name}")
                refreshed_paths.add(file_path)
                refreshed += 1
            else:
                print(f"Failed to refresh: {name}")

        # A refresh can reset the clip color on timeline items — re-apply Pink
        # on every timeline that uses a refreshed clip.
        recolored = 0
        for tl_idx in range(1, project.GetTimelineCount() + 1):
            tl = project.GetTimelineByIndex(tl_idx)
            if not tl:
                continue
            for track_idx in range(1, tl.GetTrackCount("video") + 1):
                for item in tl.GetItemListInTrack("video", track_idx) or []:
                    source = item.GetMediaPoolItem()
                    if source and source.GetClipProperty("File Path") in refreshed_paths:
                        item.SetClipColor(CLIP_COLOR)
                        recolored += 1

        print(f"\nDone. {refreshed} of {len(clips)} clip(s) refreshed, {recolored} timeline clip(s) recolored {CLIP_COLOR}.")
