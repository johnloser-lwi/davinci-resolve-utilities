# Get the Resolve app and Fusion instances
resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion() # Added to enable UI pop-ups

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    # Pop-up window to ask for the render folder
    print("Waiting for user to select a render directory...")
    target_dir = fusion.RequestDir()
    
    # Check if the user clicked 'Cancel' or closed the window
    if not target_dir:
        print("Cancelled: No render path selected.")
    else:
        # Get all timeline markers
        markers = timeline.GetMarkers()

        # Filter for "Fx Link" markers
        fx_markers = []
        if markers: # Safety check in case timeline has zero markers
            for frame_id, marker_data in markers.items():
                if marker_data.get('name') == 'Fx Link':
                    fx_markers.append({
                        'start': int(frame_id),
                        'duration': int(marker_data.get('duration', 1))
                    })

        # Sort markers chronologically by their start frame
        fx_markers = sorted(fx_markers, key=lambda k: k['start'])

        if not fx_markers:
            print("No markers named 'Fx Link' found on the timeline.")
        else:
            # Load the user's FxLink preset
            if not project.LoadRenderPreset("FxLink"):
                print("Error: Could not load render preset 'FxLink'.")
                print("Please create and save a render preset named exactly 'FxLink' in the Deliver page.")
            else:
                print(f"Found {len(fx_markers)} 'Fx Link' markers. Adding to Render Queue...")

                # Loop through and create render jobs
                for index, marker in enumerate(fx_markers, start=1):
                    start_frame = marker['start']
                    end_frame = start_frame + marker['duration'] - 1 
                    job_name = f"FxLink_{index:03d}"
                    
                    timeline.ClearMarkInOut()
                    timeline.SetMarkInOut(start_frame, end_frame)
                    
                    # Set the Render Settings
                    render_settings = {
                        "SelectAllFrames": False,
                        "CustomName": job_name,
                        "TargetDir": str(target_dir) # Applies the chosen directory
                        #"MarkIn": start_frame,
                        #"MarkOut": end_frame
                    }
                    
                    project.SetRenderSettings(render_settings)
                    
                    # Add to queue
                    project.AddRenderJob()
                    
                    print(f"Added Job: {job_name} -> {target_dir} (In: {start_frame}, Out: {end_frame})")

                print("All jobs added successfully!")