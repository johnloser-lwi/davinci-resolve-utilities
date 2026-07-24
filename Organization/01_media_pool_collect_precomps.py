resolve = bmd.scriptapp("Resolve")

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
mediaPool = project.GetMediaPool()

root_folder = mediaPool.GetRootFolder()

# Find or create PreComps bin at root level
precomps_bin = None
for folder in root_folder.GetSubFolderList():
    if folder.GetName() == "PreComps":
        precomps_bin = folder
        break

if not precomps_bin:
    precomps_bin = mediaPool.AddSubFolder(root_folder, "PreComps")
    print("Created 'PreComps' bin.")
else:
    print("Found existing 'PreComps' bin.")

# Recursively collect compound clips, skipping PreComps itself
compound_clips = []

def collect_compound_clips(folder):
    if folder == precomps_bin:
        return
    for clip in folder.GetClipList():
        if clip.GetClipProperty("Type") == "Compound":
            compound_clips.append(clip)
    for sub in folder.GetSubFolderList():
        collect_compound_clips(sub)

collect_compound_clips(root_folder)

if not compound_clips:
    print("No compound clips found outside of 'PreComps'.")
else:
    print(f"Found {len(compound_clips)} compound clip(s). Moving to 'PreComps'...")
    result = mediaPool.MoveClips(compound_clips, precomps_bin)
    if result:
        print(f"Done. Moved {len(compound_clips)} compound clip(s) to 'PreComps'.")
    else:
        print("Error: MoveClips failed.")
