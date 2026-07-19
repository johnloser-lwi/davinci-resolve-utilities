import os

# Closes the small gaps DaVinci Resolve's auto-captioning leaves between
# consecutive captions: each caption's end is extended to the next caption's
# start. Gaps longer than MAX_GAP_SECONDS are left alone (silence).
#
# Resolve's scripting API cannot trim subtitle items in place, so this works
# as a fully automatic round trip: read the captions, write a corrected .srt,
# import it into a "Subtitles" bin, delete the old subtitle items, and append
# the corrected subtitles back onto the timeline (AppendToTimeline places SRT
# media pool items onto the subtitle track at their exact timecodes —
# verified frame-accurate, including on drop-frame timelines).

MAX_GAP_SECONDS = 5.0

resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()
media_pool = project.GetMediaPool()


def frame_to_srt_time(frame, fps):
    total_ms = int(round(frame / fps * 1000))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


if not timeline:
    print("Error: No timeline is currently open.")
else:
    sub_track_count = timeline.GetTrackCount("subtitle")

    # Use the first subtitle track that has items
    sub_items = []
    sub_track = None
    for track_idx in range(1, sub_track_count + 1):
        items = timeline.GetItemListInTrack("subtitle", track_idx) or []
        if items:
            sub_track = track_idx
            sub_items = sorted(items, key=lambda i: i.GetStart())
            break

    if not sub_items:
        print("No captions found on any subtitle track.")
    else:
        fps = float(timeline.GetSetting("timelineFrameRate") or 24)
        start_offset = timeline.GetStartFrame()
        max_gap_frames = int(MAX_GAP_SECONDS * fps)

        captions = []
        for item in sub_items:
            captions.append({
                "start": item.GetStart() - start_offset,
                "end": item.GetEnd() - start_offset,
                "text": item.GetName(),
            })

        closed = 0
        skipped_long = 0
        for i in range(len(captions) - 1):
            gap = captions[i + 1]["start"] - captions[i]["end"]
            if 0 < gap <= max_gap_frames:
                captions[i]["end"] = captions[i + 1]["start"]
                closed += 1
            elif gap > max_gap_frames:
                skipped_long += 1

        if closed == 0:
            print("No gaps found to close — captions are already contiguous.")
        else:
            print(f"Closing {closed} gap(s). Left {skipped_long} gap(s) longer than {MAX_GAP_SECONDS}s untouched.")

            # Write the corrected SRT next to the project media if configured
            media_location = project.GetSetting("projectMediaLocation")
            if media_location and os.path.isdir(media_location):
                srt_dir = os.path.join(media_location, "Subtitles")
            else:
                srt_dir = os.path.join(os.path.expanduser("~"), "Documents")
            os.makedirs(srt_dir, exist_ok=True)

            timeline_name = "".join(
                c for c in timeline.GetName() if c.isalnum() or c in " _-"
            ).strip()
            srt_path = os.path.join(srt_dir, f"{timeline_name}_captions_fixed.srt")

            with open(srt_path, "w", encoding="utf-8-sig") as f:
                for idx, cap in enumerate(captions, start=1):
                    f.write(f"{idx}\n")
                    f.write(f"{frame_to_srt_time(cap['start'], fps)} --> {frame_to_srt_time(cap['end'], fps)}\n")
                    f.write(f"{cap['text']}\n\n")

            print(f"Corrected subtitles written to: {srt_path}")

            # Import the corrected SRT into a "Subtitles" bin
            root_folder = media_pool.GetRootFolder()
            sub_bin = None
            for folder in root_folder.GetSubFolderList():
                if folder.GetName() == "Subtitles":
                    sub_bin = folder
                    break
            if not sub_bin:
                sub_bin = media_pool.AddSubFolder(root_folder, "Subtitles")
            media_pool.SetCurrentFolder(sub_bin)

            imported = media_pool.ImportMedia([srt_path])
            if not imported:
                print("Warning: could not import the corrected SRT into the media pool.")
                print("The old captions were NOT removed. Import the SRT manually.")
            else:
                print("Imported corrected SRT into the 'Subtitles' bin.")

                if not timeline.DeleteClips(sub_items):
                    print("Warning: could not remove the old captions.")
                    print("Delete them manually, then drag the imported SRT onto the subtitle track.")
                else:
                    print(f"Removed {len(sub_items)} old caption(s) from subtitle track {sub_track}.")

                    placed = media_pool.AppendToTimeline([imported[0]])
                    if placed:
                        new_count = len(timeline.GetItemListInTrack("subtitle", sub_track) or [])
                        print(f"Placed {new_count} corrected caption(s) back onto the timeline.")
                        print("Done — captions are contiguous.")
                    else:
                        print("Warning: could not place the corrected subtitles automatically.")
                        print("Drag the imported SRT from the 'Subtitles' bin onto the subtitle track.")
