import os

resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion()

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
mediaPool = project.GetMediaPool()
mediaStorage = resolve.GetMediaStorage()
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline open.")
else:
    # --- UI: SELECT IMPORT DIRECTORY ---
    print("Waiting for user to select the import directory...")
    target_dir = fusion.RequestDir()
    
    if not target_dir:
        print("Cancelled: No import path selected.")
    else:
        # --- 1. GET TIMELINE TIMECODE OFFSET ---
        start_frame = timeline.GetStartFrame()
            
        # --- 2. GET MARKERS ---
        markers = timeline.GetMarkers()
        fx_markers = []
        if markers:
            for frame_id, data in markers.items():
                if data.get('name') == 'Fx Link':
                    fx_markers.append({
                        'start': int(frame_id),
                        'duration': int(data.get('duration', 1))
                    })
        
        fx_markers = sorted(fx_markers, key=lambda k: k['start'])

        if not fx_markers:
            print("No markers named 'Fx Link' found on the timeline.")
        else:
            # --- 3. SCAN FOLDER FOR FILES ---
            valid_exts = ['.mov', '.mp4', '.mxf', '.avi', '.exr', '.dpx', '.png', '.tif', '.tiff']
            files_to_import = []
            
            for f in os.listdir(target_dir):
                if any(f.lower().endswith(ext) for ext in valid_exts):
                    files_to_import.append(os.path.join(target_dir, f))
            
            files_to_import.sort()
            
            if not files_to_import:
                print(f"Error: No valid media files found in {target_dir}")
            else:
                # --- 4. SETUP BIN & IMPORT ---
                root_folder = mediaPool.GetRootFolder()
                sub_folders = root_folder.GetSubFolderList()
                fx_bin = None
                
                for folder in sub_folders:
                    if folder.GetName() == "FxLink":
                        fx_bin = folder
                        break
                
                if not fx_bin:
                    fx_bin = mediaPool.AddSubFolder(root_folder, "FxLink")
                
                mediaPool.SetCurrentFolder(fx_bin)
                
                print(f"Importing {len(files_to_import)} files...")
                imported_items = mediaStorage.AddItemListToMediaPool(files_to_import)
                
                if not imported_items:
                    print("Error: Failed to import files into the Media Pool.")
                else:
                    # Sort imported clips by name
                    imported_items.sort(key=lambda c: c.GetName())
                    
                    # --- 5. CREATE NEW TRACK ---
                    timeline.AddTrack("video")
                    target_track = timeline.GetTrackCount("video")
                    print(f"Added new Video Track (V{target_track})...")
                    
                    success_count = 0
                    
                    # --- 6. APPEND WITH RECORD FRAME OFFSET ---
                    for i, marker in enumerate(fx_markers):
                        if i < len(imported_items):
                            clip = imported_items[i]
                            clip_name = clip.GetName()
                            
                            # Offset the marker's relative frame by the timeline's starting frame
                            final_record_frame = marker['start'] + start_frame
                            
                            clip_info = [{
                                "mediaPoolItem": clip,
                                "recordFrame": final_record_frame, 
                                "trackIndex": target_track
                            }]
                            
                            result = mediaPool.AppendToTimeline(clip_info)
                            
                            if result:
                                print(f"Success: Added '{clip_name}' exactly at frame {final_record_frame}")
                                success_count += 1
                            else:
                                print(f"FAILED: Resolve rejected '{clip_name}'")
                    
                    print(f"\nFinished! Added {success_count} clips successfully to V{target_track}.")