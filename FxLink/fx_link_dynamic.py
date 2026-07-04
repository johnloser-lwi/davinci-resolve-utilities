import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk

PREFS_FILE = os.path.expandvars(r"%APPDATA%\fxlink_prefs.json")
CLIP_COLOR = "Pink"
STAGING_DIR = os.path.join(tempfile.gettempdir(), "fxlink_staging")


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


def ask_preset(preset_names, last_preset=None):
    selected = [None]

    root = tk.Tk()
    root.title("Select Render Preset")
    root.resizable(False, False)

    tk.Label(root, text="Render Preset:", padx=12, pady=8).pack()

    combo = ttk.Combobox(root, values=preset_names, state="readonly", width=40)
    default_idx = preset_names.index(last_preset) if last_preset in preset_names else 0
    combo.current(default_idx)
    combo.pack(padx=12, pady=4)

    def on_ok():
        selected[0] = combo.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=4)

    root.lift()
    root.attributes("-topmost", True)
    root.mainloop()

    return selected[0]


def find_afterfx(prefs):
    cached = prefs.get("afterfx_path")
    if cached and os.path.exists(cached):
        return cached
    candidates = glob.glob(r"C:\Program Files\Adobe\Adobe After Effects *\Support Files\AfterFX.exe")
    if not candidates:
        return None
    # Newest version sorts last (e.g. "... 2024" < "... 2025")
    path = sorted(candidates)[-1]
    save_prefs({**prefs, "afterfx_path": path})
    return path


def afterfx_running():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return "AfterFX.exe" in out


def run_extendscript(afterfx_exe, jsx_code, timeout=20):
    """Run ExtendScript in the running AE instance; returns contents of the
    result file the script writes, or None on timeout."""
    result_path = os.path.join(tempfile.gettempdir(), f"fxlink_ae_result_{os.getpid()}.txt")
    if os.path.exists(result_path):
        os.remove(result_path)

    # JSX paths must use forward slashes
    jsx = jsx_code.replace("__RESULT_FILE__", result_path.replace("\\", "/"))

    jsx_path = os.path.join(tempfile.gettempdir(), f"fxlink_ae_script_{os.getpid()}.jsx")
    with open(jsx_path, "w", encoding="utf-8") as f:
        f.write(jsx)

    # -r forwards the script to the already-running AE instance
    subprocess.Popen([afterfx_exe, "-r", jsx_path])

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(result_path):
            time.sleep(0.2)  # let AE finish writing
            with open(result_path, "r", encoding="utf-8") as f:
                result = f.read().strip()
            os.remove(result_path)
            os.remove(jsx_path)
            return result
        time.sleep(0.25)
    return None


GET_PROJECT_JSX = """
var f = new File("__RESULT_FILE__");
f.encoding = "UTF-8";
f.open("w");
if (app.project && app.project.file) {
    f.write(app.project.file.fsName);
} else {
    f.write("__UNSAVED__");
}
f.close();
"""

SETUP_PROJECT_JSX = """
var f = new File("__RESULT_FILE__");
f.encoding = "UTF-8";
f.open("w");
try {
    var proj = app.project;

    function findOrAddFolder(name) {
        for (var i = 1; i <= proj.numItems; i++) {
            var it = proj.item(i);
            if (it instanceof FolderItem && it.name === name) { return it; }
        }
        return proj.items.addFolder(name);
    }

    var fxlinkFolder = findOrAddFolder("FxLink");
    var outputFolder = findOrAddFolder("Output");

    var footage = proj.importFile(new ImportOptions(File("__SRC_FILE__")));
    footage.parentFolder = fxlinkFolder;

    var compName = footage.name.replace(/\\.[^\\.]+$/, "");
    var comp = proj.items.addComp(compName, footage.width, footage.height,
                                  footage.pixelAspect, footage.duration, footage.frameRate);
    comp.parentFolder = outputFolder;
    comp.layers.add(footage);
    comp.openInViewer();

    var rqNote = "";
    try {
        var rqItem = proj.renderQueue.items.add(comp);
        rqItem.outputModule(1).file = new File("__OUT_FILE__");
    } catch (e) {
        rqNote = " (render queue setup failed: " + e.toString() + ")";
    }

    f.write("OK" + rqNote);
} catch (e) {
    f.write("ERROR: " + e.toString());
}
f.close();
"""


resolve = bmd.scriptapp("Resolve")
fusion = resolve.Fusion()

projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()

if not timeline:
    print("Error: No timeline is currently open.")
else:
    playhead_tc = timeline.GetCurrentTimecode()
    prefs = load_prefs()

    afterfx_exe = find_afterfx(prefs)
    if not afterfx_exe:
        print("Error: Could not find AfterFX.exe under C:\\Program Files\\Adobe.")
    elif not afterfx_running():
        print("Error: After Effects is not running. Open your AE project first.")
    else:
        presets_dict = project.GetRenderPresets()
        preset_names = [presets_dict[k] for k in sorted(presets_dict.keys())]

        if not preset_names:
            print("Error: No render presets found. Please create one in the Deliver page.")
        else:
            prefs = load_prefs()  # find_afterfx may have updated it
            preset_name = ask_preset(preset_names, last_preset=prefs.get("last_preset"))

            if not preset_name:
                print("Cancelled: No preset selected.")
            elif not project.LoadRenderPreset(preset_name):
                print(f"Error: Could not load render preset '{preset_name}'.")
            else:
                save_prefs({**load_prefs(), "last_preset": preset_name})

                # Render into a local staging folder first — After Effects is not
                # contacted at all until the render is finished and placed in Resolve.
                os.makedirs(STAGING_DIR, exist_ok=True)

                timeline_name = "".join(
                    c for c in timeline.GetName() if c.isalnum() or c in " _-"
                ).strip().replace(" ", "_")
                # Render under a unique temp name; the final incremented name is
                # applied when the file moves into the AE project's FxLink folder
                # (we can't count existing files there until AE tells us the path).
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                custom_name = f"{timeline_name}_FxLink_tmp_{timestamp}"

                project.SetRenderSettings({
                    "SelectAllFrames": False,
                    "TargetDir": STAGING_DIR,
                    "ExportAudio": False,
                    "CustomName": custom_name,
                })

                job_id = project.AddRenderJob()

                if not job_id:
                    print("Error: Failed to add render job. Make sure an In/Out range is set on the timeline.")
                else:
                    all_jobs = project.GetRenderJobList() or []
                    job_details = next((j for j in all_jobs if j.get("JobId") == job_id), {})
                    mark_in = job_details.get("MarkIn")
                    mark_out = job_details.get("MarkOut")

                    print(f"Rendering {custom_name} ...")
                    project.StartRendering(job_id)

                    while project.IsRenderingInProgress():
                        time.sleep(1)

                    status = project.GetRenderJobStatus(job_id)
                    if status.get("JobStatus") != "Complete":
                        print(f"Render did not complete successfully. Status: {status.get('JobStatus', 'Unknown')}")
                    else:
                        # Find the rendered file by name regardless of extension
                        staged_path = None
                        for fname in os.listdir(STAGING_DIR):
                            if os.path.splitext(fname)[0] == custom_name:
                                staged_path = os.path.join(STAGING_DIR, fname)
                                break

                        if not staged_path:
                            print(f"Error: Could not find rendered file '{custom_name}' in '{STAGING_DIR}'.")
                        else:
                            print("Render complete. Asking After Effects for the open project path...")
                            aep_path = run_extendscript(afterfx_exe, GET_PROJECT_JSX)

                            if aep_path is None:
                                print("Error: After Effects did not respond. Is it busy with a modal dialog?")
                                print(f"The render is safe at: {staged_path}")
                            elif aep_path == "__UNSAVED__":
                                print("Error: The open AE project has never been saved. Save it first so it has a folder.")
                                print(f"The render is safe at: {staged_path}")
                            elif not os.path.exists(aep_path):
                                print(f"Error: AE reported project path '{aep_path}' but it does not exist.")
                                print(f"The render is safe at: {staged_path}")
                            else:
                                aep_dir = os.path.dirname(aep_path)
                                fxlink_dir = os.path.join(aep_dir, "FxLink")
                                output_dir = os.path.join(aep_dir, "Output")
                                os.makedirs(fxlink_dir, exist_ok=True)
                                os.makedirs(output_dir, exist_ok=True)
                                print(f"AE project: {aep_path}")

                                # Final name: [Timeline Name]_FxLink_[001], incremented
                                # from the existing files in the FxLink folder.
                                base = f"{timeline_name}_FxLink_"
                                highest = 0
                                for existing in os.listdir(fxlink_dir):
                                    stem = os.path.splitext(existing)[0]
                                    m = re.fullmatch(re.escape(base) + r"(\d+)", stem)
                                    if m:
                                        highest = max(highest, int(m.group(1)))

                                ext = os.path.splitext(staged_path)[1]
                                final_name = f"{base}{highest + 1:03d}{ext}"
                                render_path = os.path.join(fxlink_dir, final_name)
                                shutil.move(staged_path, render_path)
                                output_path = os.path.join(output_dir, os.path.basename(render_path))
                                shutil.copy2(render_path, output_path)
                                print(f"Source: {render_path}")
                                print(f"Output copy: {output_path}")

                                # --- Place the Output copy on the Resolve timeline ---
                                media_pool = project.GetMediaPool()
                                root_folder = media_pool.GetRootFolder()

                                ae_bin = None
                                for folder in root_folder.GetSubFolderList():
                                    if folder.GetName() == "FxLink":
                                        ae_bin = folder
                                        break
                                if not ae_bin:
                                    ae_bin = media_pool.AddSubFolder(root_folder, "FxLink")
                                media_pool.SetCurrentFolder(ae_bin)

                                imported = media_pool.ImportMedia([output_path])
                                if not imported:
                                    print(f"Warning: Could not import '{output_path}' into media pool.")
                                elif mark_in is None:
                                    print("Warning: Could not read MarkIn from render job. Skipping timeline placement.")
                                else:
                                    clip = imported[0]
                                    clip_total_frames = int(clip.GetClipProperty("Frames"))
                                    record_frame = mark_in
                                    clip_end = mark_out + 1 if mark_out is not None else mark_in + clip_total_frames

                                    total_tracks = timeline.GetTrackCount("video")
                                    highest_occupied = 0
                                    for track_idx in range(1, total_tracks + 1):
                                        items = timeline.GetItemListInTrack("video", track_idx) or []
                                        if any(item.GetStart() < clip_end and item.GetEnd() > record_frame for item in items):
                                            highest_occupied = track_idx

                                    target_track = highest_occupied + 1
                                    if target_track > total_tracks:
                                        timeline.AddTrack("video")

                                    placed = media_pool.AppendToTimeline([{
                                        "mediaPoolItem": clip,
                                        "startFrame": 0,
                                        "endFrame": clip_total_frames,
                                        "mediaType": 1,
                                        "trackIndex": target_track,
                                        "recordFrame": record_frame,
                                    }])

                                    timeline_item = placed[0] if placed else None
                                    if not timeline_item:
                                        print("Warning: Could not place clip onto timeline (note: placement inside compound clips is not supported by the API).")
                                        print("The clip is in the FxLink bin — you can drag it manually.")
                                    else:
                                        timeline_item.SetClipColor(CLIP_COLOR)
                                        print(f"Placed on video track {target_track} at frame {record_frame} ({CLIP_COLOR}).")

                                    timeline.SetCurrentTimecode(playhead_tc)

                                # --- Everything in Resolve is done; now set up the AE project ---
                                print("Setting up After Effects project...")
                                setup_jsx = (
                                    SETUP_PROJECT_JSX
                                    .replace("__SRC_FILE__", render_path.replace("\\", "/"))
                                    .replace("__OUT_FILE__", output_path.replace("\\", "/"))
                                )
                                ae_result = run_extendscript(afterfx_exe, setup_jsx, timeout=30)

                                if ae_result is None:
                                    print("Warning: AE did not confirm project setup (timeout). Check AE manually.")
                                elif ae_result.startswith("OK"):
                                    print(f"AE ready: comp created and render queued to overwrite the Output file.{ae_result[2:]}")
                                    print("Work in AE, then just hit Render — Resolve links to the Output file and will pick up the overwrite.")
                                else:
                                    print(f"Warning: AE setup failed: {ae_result}")
