import os

CLIP_COLOR = "Pink"

# Forces Resolve to re-read every clip in the FxLink bin by replacing each
# clip with its own file path. Run this after rendering from After Effects
# if the timeline doesn't pick up the overwritten Output file on its own.

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
            if not file_path:
                print(f"Skipped '{clip.GetName()}': no file path.")
            elif not os.path.exists(file_path):
                print(f"Skipped '{clip.GetName()}': file not found at {file_path}")
            elif clip.ReplaceClip(file_path):
                print(f"Refreshed: {clip.GetName()}")
                refreshed_paths.add(file_path)
                refreshed += 1
            else:
                print(f"Failed to refresh: {clip.GetName()}")

        # ReplaceClip resets the clip color on timeline items — re-apply Pink
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
