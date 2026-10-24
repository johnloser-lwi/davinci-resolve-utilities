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
from tkinter import messagebox, ttk

PREFS_FILE = os.path.expandvars(r"%APPDATA%\fxlink_prefs.json")
CLIP_COLOR = "Pink"
STAGING_DIR = os.path.join(tempfile.gettempdir(), "fxlink_staging")

# Image sequences. A sequence lives in its own folder - FxLink\<name>\ and
# Output\<name>\ - so hundreds of frames never litter the project folders.
#
# Frames are RENUMBERED FROM 0 when they move in. Resolve numbers a render from
# its timeline position (00086400...), but After Effects numbers its renders
# from 0, so without this an AE re-render would write different filenames and
# never overwrite the frames Resolve is linked to. Starting at 0 with AE's own
# 5-digit [#####] padding means both sides agree using AE's defaults, with no
# dependence on Output Module settings.
SEQUENCE_EXTS = (".png", ".exr", ".dpx", ".tif", ".tiff", ".jpg", ".jpeg",
                 ".bmp", ".tga")
SEQUENCE_PAD = 5


def load_prefs():
    if os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_prefs(prefs):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


PROBLEMS = []


def problem(message):
    """Print a problem AND keep it for the summary shown at the end.

    FxLink usually runs from Resolve's Scripts menu or a hotkey, where the
    Console isn't on screen - so a run that stopped early looked exactly like
    "nothing happened". Everything that would have gone only to the Console
    is now also shown in a window once the run ends.
    """
    print(message)
    PROBLEMS.append(message)


def show_problems():
    if not PROBLEMS:
        return
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showwarning(
            "FxLink",
            "\n\n".join(PROBLEMS)
            + "\n\nMore detail is in Resolve's Console (Workspace > Console).",
            parent=root)
        root.destroy()
    except Exception:
        pass


MODE_FOOTAGE = "footage"
MODE_AUDIO = "audio"


def default_audio_preset(preset_names, last_audio_preset=None):
    """The audio preset to pre-select: last time's, else anything called
    "audio" (Resolve ships one named Audio Only), else the first."""
    if last_audio_preset in preset_names:
        return last_audio_preset
    for name in preset_names:
        if "audio" in name.lower():
            return name
    return preset_names[0] if preset_names else None


def ask_options(preset_names, last_preset=None, last_mode=MODE_FOOTAGE,
                last_audio_preset=None, parent=None, title="FxLink"):
    """What to send to After Effects, and which presets to render it with.

    Returns (picture preset, mode, audio preset), or (None, None, None) if
    cancelled.

    With a parent (the FxLink Queue window) it opens as a child window of it.
    Opening a second Tk() inside a running Tk app instead would bind this
    dialog's radio buttons to the OTHER window's variables, and they'd silently
    stop working - so the variable is also given its own master explicitly.
    """
    selected = [None, None, None]

    root = tk.Toplevel(parent) if parent is not None else tk.Tk()
    root.title(title)
    root.resizable(False, False)

    mode_var = tk.StringVar(master=root,
                            value=last_mode if last_mode in (MODE_FOOTAGE, MODE_AUDIO)
                            else MODE_FOOTAGE)
    modes = tk.Frame(root, padx=12, pady=8)
    modes.pack(fill="x")
    tk.Label(modes, text="Send to After Effects:", anchor="w").pack(fill="x")
    tk.Radiobutton(modes, text="Footage  (image sequences also get their audio)",
                   variable=mode_var, value=MODE_FOOTAGE, anchor="w").pack(fill="x")
    tk.Radiobutton(modes, text="Audio only  - as a timing reference, no footage",
                   variable=mode_var, value=MODE_AUDIO, anchor="w").pack(fill="x")

    label = tk.Label(root, anchor="w", padx=12, justify="left", fg="#666666")
    label.pack(fill="x")

    picture_label = tk.Label(root, padx=12, pady=4, anchor="w")
    picture_label.pack(fill="x")
    combo = ttk.Combobox(root, values=preset_names, state="readonly", width=44)
    default_idx = preset_names.index(last_preset) if last_preset in preset_names else 0
    combo.current(default_idx)
    combo.pack(padx=12, pady=4)

    # A preset that renders audio only. Resolve's own Deliver page settings are
    # used as-is rather than rebuilt through the API: forcing audio settings
    # onto a TIFF/PNG preset left the format as an image sequence, which can't
    # hold audio, and the render just hung.
    tk.Label(root, text="Audio Preset  (one that exports audio only):",
             padx=12, pady=4, anchor="w").pack(fill="x")
    audio_combo = ttk.Combobox(root, values=preset_names, state="readonly", width=44)
    audio_default = default_audio_preset(preset_names, last_audio_preset)
    if audio_default in preset_names:
        audio_combo.current(preset_names.index(audio_default))
    audio_combo.pack(padx=12, pady=4)

    def describe(*_args):
        # The picture preset still matters in audio-only mode - it sets the
        # format of what AE renders back - so say what it does in each mode.
        if mode_var.get() == MODE_AUDIO:
            picture_label.config(text="Format After Effects renders back to:")
            label.config(text="Resolve gets an empty placeholder in that format until AE\n"
                              "renders - use a preset with alpha so it stays see-through.")
        else:
            picture_label.config(text="Render Preset:")
            label.config(text="The footage is rendered with this preset. Image sequences\n"
                              "can't carry sound, so they also get the audio preset's render.")
    mode_var.trace_add("write", describe)
    describe()

    def on_ok():
        selected[0] = combo.get()
        selected[1] = mode_var.get()
        selected[2] = audio_combo.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=4)
    root.bind("<Return>", lambda e: on_ok())
    root.bind("<Escape>", lambda e: on_cancel())

    root.lift()
    root.attributes("-topmost", True)
    if parent is not None:
        root.transient(parent)
        root.grab_set()
        parent.wait_window(root)        # modal, inside the parent's event loop
    else:
        root.mainloop()

    return selected[0], selected[1], selected[2]


def tc_str_to_frame(tc_str, fps):
    drop_frame = ";" in tc_str
    h, m, s, f = map(int, tc_str.replace(";", ":").split(":"))
    fps_round = round(float(fps))
    total = (h * 3600 + m * 60 + s) * fps_round + f
    if drop_frame:
        drop = 4 if fps_round == 60 else 2
        total_minutes = 60 * h + m
        total -= drop * (total_minutes - total_minutes // 10)
    return total


def find_linked_clip_path_under_playhead(timeline):
    """If a Pink FxLink clip sits under the playhead, return its linked file
    path, else None."""
    fps = timeline.GetSetting("timelineFrameRate") or 24
    playhead = tc_str_to_frame(timeline.GetCurrentTimecode(), fps)
    for track_idx in range(timeline.GetTrackCount("video"), 0, -1):
        for item in timeline.GetItemListInTrack("video", track_idx) or []:
            if item.GetClipColor() == CLIP_COLOR and item.GetStart() <= playhead < item.GetEnd():
                source = item.GetMediaPoolItem()
                file_path = source.GetClipProperty("File Path") if source else ""
                if file_path:
                    return file_path
    return None


def find_any_pink_clip_path(timeline):
    """Return the linked file path of the first Pink FxLink clip found
    anywhere on the timeline, else None. Used to map to the AE project."""
    for track_idx in range(timeline.GetTrackCount("video"), 0, -1):
        for item in timeline.GetItemListInTrack("video", track_idx) or []:
            if item.GetClipColor() == CLIP_COLOR:
                source = item.GetMediaPoolItem()
                file_path = source.GetClipProperty("File Path") if source else ""
                if file_path:
                    return file_path
    return None


def link_identity(file_path):
    """(aep folder, comp name) for a linked Output file, or (None, None).

    A movie links as      <aep folder>\\Output\\<comp>.mov
    a sequence links as   <aep folder>\\Output\\<comp>\\<comp>_[00000-00100].png
    (Resolve reports a sequence's File Path in that bracket form). Walking up to
    the Output folder handles both, where "the parent's parent" only fits the
    movie case.
    """
    parts = os.path.normpath(file_path).split(os.sep)
    lowered = [p.lower() for p in parts]
    if "output" not in lowered:
        return None, None
    at = len(lowered) - 1 - lowered[::-1].index("output")
    rest = parts[at + 1:]
    if not rest:
        return None, None
    aep_dir = os.sep.join(parts[:at])
    if len(rest) >= 2:
        return aep_dir, rest[0]                     # sequence: its folder name
    return aep_dir, os.path.splitext(rest[0])[0]    # movie: its file stem


def find_aep_for_output(file_path):
    """The newest .aep beside the Output folder the linked file lives in."""
    aep_dir, _comp = link_identity(file_path)
    if not aep_dir:
        return None
    aeps = glob.glob(os.path.join(aep_dir, "*.aep"))
    if not aeps:
        return None
    return max(aeps, key=os.path.getmtime)


def collect_render(render_dir, custom_name):
    """What the render produced: ("movie", path), ("sequence", [paths in frame
    order]) or (None, None).

    The frame number is read from what FOLLOWS the known custom name, not by
    pattern-matching the whole filename: the custom name ends in a timestamp,
    and a regex for "trailing digits" would happily glue that timestamp onto the
    frame number.
    """
    try:
        names = [n for n in os.listdir(render_dir) if n.startswith(custom_name)]
    except OSError:
        return None, None

    for name in names:
        if os.path.splitext(name)[0] == custom_name:
            return "movie", os.path.join(render_dir, name)

    frames = []
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext.lower() not in SEQUENCE_EXTS:
            continue
        match = re.search(r"(\d+)$", stem[len(custom_name):])
        if match:
            frames.append((int(match.group(1)), os.path.join(render_dir, name)))
    if frames:
        frames.sort()
        return "sequence", [path for _n, path in frames]
    return None, None


# The audio is rendered with an audio-only preset you pick in the FxLink menu,
# rather than by switching settings on whatever preset is loaded. With a TIFF or
# PNG sequence preset loaded the render FORMAT stays an image sequence, which
# can't carry audio - turning video off and naming WAV through the API didn't
# change that, so the job had nothing it could produce and hung. A preset is set
# up exactly the way the Deliver page sets it up.
AUDIO_PRESET_HOWTO = (
    "Resolve's built-in 'Audio Only' preset works, or make one on the Deliver "
    "page: Video tab - untick Export Video; Audio tab - Format Wave; then Save "
    "As New Preset.")
AUDIO_EXTS = (".wav", ".wave", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac")
IMAGE_SEQUENCE_FORMATS = ("png", "tif", "tiff", "exr", "dpx", "jpg", "jpeg", "bmp", "tga")


def loaded_preset_is_sequence(project):
    """Whether the loaded preset renders an image sequence - which can't hold audio."""
    try:
        fmt = (project.GetCurrentRenderFormatAndCodec() or {}).get("format", "")
    except Exception:
        return False
    return str(fmt).lower() in IMAGE_SEQUENCE_FORMATS


RENDER_START_SECONDS = 30     # still not rendering this long after Start = it never started
RENDER_STALL_SECONDS = 90     # no progress for this long while rendering = stuck
FAILED_STATES = ("Failed", "Cancelled", "Background Render Cancelled",
                 "Remote Render Cancelled")


def run_render(project, settings, label="render"):
    """Render one job with these settings. Returns (job_id, mark_in, mark_out),
    or raises RuntimeError with a message for the user.

    This used to wait on IsRenderingInProgress() with no limit, so a job that
    never started - or started and stopped making progress - hung FxLink
    forever with nothing on screen. It now follows the job's own status, stops
    a render that has stalled, and reports what Resolve says went wrong.
    """
    # Only an explicit False counts as a refusal. A None from some builds is
    # not an answer either way, and treating it as failure would break renders
    # that work today; the job-status monitoring below catches a real one.
    if project.SetRenderSettings(settings) is False:
        problem(f"Warning: Resolve didn't accept all of the {label} settings: "
              + ", ".join(f"{k}={v}" for k, v in settings.items()))

    job_id = project.AddRenderJob()
    if not job_id:
        raise RuntimeError("Failed to add render job. Make sure an In/Out range is set on the timeline.")
    jobs = project.GetRenderJobList() or []
    details = next((j for j in jobs if j.get("JobId") == job_id), {})

    # The documented form takes a LIST of job ids; FxLink used to pass the bare
    # id, which also worked. Try the documented one, fall back to the old one.
    if project.StartRendering([job_id]) is False:
        if project.StartRendering(job_id) is False:
            raise RuntimeError(f"Resolve wouldn't start the {label} render.")

    started = time.time()
    last_percent, last_change = None, time.time()
    while True:
        status = project.GetRenderJobStatus(job_id) or {}
        state = status.get("JobStatus", "Unknown")
        percent = status.get("CompletionPercentage", 0)
        reason = status.get("Error") or ""

        if state == "Complete":
            break
        if state in FAILED_STATES:
            raise RuntimeError(f"The {label} render ended '{state}'"
                               + (f": {reason}" if reason else "."))

        now = time.time()
        if percent != last_percent:
            if last_percent is None or percent // 25 != last_percent // 25:
                print(f"  {label}: {state} {percent}%")
            last_percent, last_change = percent, now

        if state != "Rendering" and not project.IsRenderingInProgress() \
                and now - started > RENDER_START_SECONDS:
            raise RuntimeError(f"The {label} render never started (job status '{state}'"
                               + (f", error: {reason}" if reason else "") + ").")
        if now - last_change > RENDER_STALL_SECONDS:
            project.StopRendering()
            raise RuntimeError(f"The {label} render stalled at {percent}% for "
                               f"{RENDER_STALL_SECONDS}s (status '{state}'"
                               + (f", error: {reason}" if reason else "")
                               + ") and was stopped.")
        time.sleep(1)

    return job_id, details.get("MarkIn"), details.get("MarkOut")


def render_audio(project, audio_preset, out_dir, name):
    """Render the In/Out range's audio with the audio preset. Returns its path.

    Only where it goes and what it's called are set here - the preset decides
    everything about the audio itself. It uses the same timeline In/Out as the
    picture render, so the range is identical by construction.
    """
    if not project.LoadRenderPreset(audio_preset):
        raise RuntimeError(f"Couldn't load the '{audio_preset}' render preset.")
    os.makedirs(out_dir, exist_ok=True)
    run_render(project, {
        "SelectAllFrames": False,
        "TargetDir": out_dir,
        "CustomName": name,
    }, label="audio")

    for entry in os.listdir(out_dir):
        stem, ext = os.path.splitext(entry)
        if stem == name:
            if ext.lower() not in AUDIO_EXTS:
                # The preset renders picture, not audio - don't hand AE a
                # video file and call it the audio.
                raise RuntimeError(f"The '{audio_preset}' preset rendered '{entry}', which isn't "
                                   f"an audio file. {AUDIO_PRESET_HOWTO}")
            return os.path.join(out_dir, entry)
    raise RuntimeError(f"The audio render finished but '{name}' isn't in '{out_dir}'.")


def render_placeholder(project, timeline, settings):
    """Render the In/Out range with every video track switched off.

    Audio-only mode still needs something for Resolve to link to - that's how
    After Effects' render comes back - so this makes it: the preset's own format
    and length, with nothing in it. It's quick because there's nothing to
    decode or composite. Your tracks are switched back on afterwards, even if
    the render fails, and anything that didn't come back is reported.
    """
    switched_off = []
    try:
        for index in range(1, (timeline.GetTrackCount("video") or 0) + 1):
            if timeline.GetIsTrackEnabled("video", index):
                if timeline.SetTrackEnable("video", index, False):
                    switched_off.append(index)
        return run_render(project, settings, label="placeholder")
    finally:
        stuck = []
        for index in switched_off:
            timeline.SetTrackEnable("video", index, True)
            if not timeline.GetIsTrackEnabled("video", index):
                stuck.append(index)
        if stuck:
            problem("WARNING: these video tracks were switched off for the placeholder render "
                  "and did NOT come back on: " + ", ".join(f"V{i}" for i in stuck)
                  + ". Re-enable them by hand.")


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
    result file the script writes, or None on timeout.

    The JSX writes to a .part file and renames it when done, so the result
    file only ever appears complete. We also wait for the AfterFX launcher
    process to exit before returning, so back-to-back calls can't collide
    and trigger AE's "only one script can be run at a time" warning."""
    result_path = os.path.join(tempfile.gettempdir(), f"fxlink_ae_result_{os.getpid()}.txt")
    for stale in (result_path, result_path + ".part"):
        if os.path.exists(stale):
            os.remove(stale)

    # JSX paths must use forward slashes
    jsx = jsx_code.replace("__RESULT_FILE__", result_path.replace("\\", "/"))

    jsx_path = os.path.join(tempfile.gettempdir(), f"fxlink_ae_script_{os.getpid()}.jsx")
    with open(jsx_path, "w", encoding="utf-8") as f:
        f.write(jsx)

    # -r forwards the script to the already-running AE instance
    launcher = subprocess.Popen([afterfx_exe, "-r", jsx_path])

    result = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as f:
                result = f.read().strip()
            os.remove(result_path)
            os.remove(jsx_path)
            break
        time.sleep(0.25)

    # Don't return until the launcher process is gone, otherwise the next
    # AfterFX.exe -r call can race against this one inside AE.
    try:
        launcher.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass

    return result


GET_PROJECT_JSX = """
var f = new File("__RESULT_FILE__" + ".part");
f.encoding = "UTF-8";
f.open("w");
if (app.project && app.project.file) {
    f.write(app.project.file.fsName);
} else {
    f.write("__UNSAVED__");
}
f.close();
f.rename(File("__RESULT_FILE__").name);
"""

OPEN_PROJECT_JSX = """
var f = new File("__RESULT_FILE__" + ".part");
f.encoding = "UTF-8";
f.open("w");
try {
    app.open(new File("__AEP_FILE__"));
    f.write("OK");
} catch (e) {
    f.write("ERROR: " + e.toString());
}
f.close();
f.rename(File("__RESULT_FILE__").name);
"""

OPEN_COMP_JSX = """
var f = new File("__RESULT_FILE__" + ".part");
f.encoding = "UTF-8";
f.open("w");
try {
    var proj = app.project;
    var comp = null;
    for (var i = 1; i <= proj.numItems; i++) {
        var it = proj.item(i);
        if (it instanceof CompItem && it.name === "__COMP_NAME__") { comp = it; break; }
    }
    if (comp) {
        comp.openInViewer();
        app.activate();
        f.write("OK");
    } else {
        f.write("NOT_FOUND");
    }
} catch (e) {
    f.write("ERROR: " + e.toString());
}
f.close();
f.rename(File("__RESULT_FILE__").name);
"""

SETUP_PROJECT_JSX = """
var f = new File("__RESULT_FILE__" + ".part");
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

    // Any of these can be empty: audio-only has no footage, and a movie
    // carries its own audio so has no separate file.
    var sourcePath = "__SRC_FILE__";
    var audioPath = "__AUDIO_FILE__";
    var isSequence = __IS_SEQUENCE__;

    var footage = null;
    if (sourcePath) {
        var opts = new ImportOptions(File(sourcePath));
        if (isSequence) {
            // Without this AE imports the first frame as a single still.
            opts.sequence = true;
        }
        footage = proj.importFile(opts);
        footage.parentFolder = fxlinkFolder;
        if (isSequence) {
            // A sequence has no frame rate of its own - AE would use its default.
            footage.mainSource.conformFrameRate = __FPS__;
        }
    }

    var audio = null;
    if (audioPath) {
        audio = proj.importFile(new ImportOptions(File(audioPath)));
        audio.parentFolder = fxlinkFolder;
    }

    // Named explicitly: it must equal the Output name, because that is how
    // Resolve's link finds its comp again. Taken from the footage instead, a
    // sequence would give "name_[00000-00100]".
    var compName = "__COMP_NAME__";
    var comp;
    if (footage) {
        comp = proj.items.addComp(compName, footage.width, footage.height,
                                  footage.pixelAspect, footage.duration, footage.frameRate);
    } else {
        // Audio only: no footage to take the size from, so the comp is built
        // from the Resolve timeline's own settings.
        comp = proj.items.addComp(compName, __WIDTH__, __HEIGHT__, 1.0,
                                  __DURATION__, __FPS__);
    }
    comp.parentFolder = outputFolder;
    // Layers stack newest-on-top, so audio goes in first and sits underneath.
    if (audio) comp.layers.add(audio);
    if (footage) comp.layers.add(footage);
    comp.openInViewer();

    f.write("OK");
} catch (e) {
    f.write("ERROR: " + e.toString());
}
f.close();
f.rename(File("__RESULT_FILE__").name);
"""


def ensure_ae_project(afterfx_exe, aep_path, timeout=90):
    """Make sure AE is running with aep_path open — launching AE or switching
    projects as needed. Returns True once AE reports the expected project."""
    def norm(p):
        return os.path.normcase(os.path.normpath(p))

    if not afterfx_running():
        print(f"Launching After Effects with {os.path.basename(aep_path)} ...")
        subprocess.Popen([afterfx_exe, aep_path])
    else:
        current = run_extendscript(afterfx_exe, GET_PROJECT_JSX)
        if current and current != "__UNSAVED__" and norm(current) == norm(aep_path):
            return True
        print(f"Switching After Effects to {os.path.basename(aep_path)} ...")
        open_jsx = OPEN_PROJECT_JSX.replace("__AEP_FILE__", aep_path.replace("\\", "/"))
        result = run_extendscript(afterfx_exe, open_jsx, timeout=60)
        if result != "OK":
            problem(f"Error: Could not open the project in After Effects: {result}")
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        current = run_extendscript(afterfx_exe, GET_PROJECT_JSX, timeout=10)
        if current and current != "__UNSAVED__" and norm(current) == norm(aep_path):
            return True
        time.sleep(3)

    problem("Error: After Effects did not load the expected project in time.")
    return False


def main(job=None):
    """Run FxLink once.

    job is None for the normal interactive run (the menu asks for settings).
    The FxLink Queue passes a dict with "preset", "mode" and "audio_preset"
    instead: the menu is skipped, and so is "reopen the comp under the
    playhead" - a queued range is always a new render, wherever the playhead
    happens to be.
    """
    resolve = bmd.scriptapp("Resolve")
    fusion = resolve.Fusion()

    projectManager = resolve.GetProjectManager()
    project = projectManager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()

    if not timeline:
        problem("Error: No timeline is currently open.")
    else:
        playhead_tc = timeline.GetCurrentTimecode()
        prefs = load_prefs()

        afterfx_exe = find_afterfx(prefs)
        linked_path = None if job else find_linked_clip_path_under_playhead(timeline)

        ae_running = afterfx_running()
        aep_hint = None
        if not ae_running and not linked_path:
            pink_path = find_any_pink_clip_path(timeline)
            if pink_path:
                aep_hint = find_aep_for_output(pink_path)

        if not afterfx_exe:
            problem("Error: Could not find AfterFX.exe under C:\\Program Files\\Adobe.")
        elif linked_path:
            # Connect flow: the Pink clip's path maps to the AE project — launch
            # AE with it or switch the running AE to it as needed.
            linked_dir, linked_comp = link_identity(linked_path)
            aep_path = find_aep_for_output(linked_path)
            if not aep_path:
                problem(f"Error: No .aep project found beside the Output folder for '{linked_path}'.")
                if linked_dir:
                    print(f"  (looked in {linked_dir})")
            elif ensure_ae_project(afterfx_exe, aep_path):
                print(f"Opening comp '{linked_comp}' in After Effects...")
                result = run_extendscript(afterfx_exe, OPEN_COMP_JSX.replace("__COMP_NAME__", linked_comp))
                if result == "OK":
                    print("Comp opened in After Effects.")
                elif result == "NOT_FOUND":
                    print(f"Comp '{linked_comp}' was not found in project '{os.path.basename(aep_path)}'.")
                elif result is None:
                    problem("Error: After Effects did not respond. Is it busy with a modal dialog?")
                else:
                    problem(f"Error from AE: {result}")
        elif not ae_running and not aep_hint:
            problem("Error: After Effects is not running, and no FxLink clip on this timeline maps to a project.")
            print("Open your AE project first (or place the playhead over an existing FxLink clip).")
        else:
            if not ae_running:
                # Launch AE with the mapped project now so it boots while Resolve
                # renders; the post-render project query retries until it's up.
                print(f"Launching After Effects with {os.path.basename(aep_hint)} — it will load during the render...")
                subprocess.Popen([afterfx_exe, aep_hint])

            presets_dict = project.GetRenderPresets()
            preset_names = [presets_dict[k] for k in sorted(presets_dict.keys())]

            if not preset_names:
                problem("Error: No render presets found. Please create one in the Deliver page.")
            else:
                prefs = load_prefs()  # find_afterfx may have updated it
                if job:
                    preset_name = job.get("preset")
                    mode = job.get("mode", MODE_FOOTAGE)
                    audio_preset = job.get("audio_preset")
                else:
                    preset_name, mode, audio_preset = ask_options(
                        preset_names,
                        last_preset=prefs.get("last_preset"),
                        last_mode=prefs.get("last_mode", MODE_FOOTAGE),
                        last_audio_preset=prefs.get("last_audio_preset"))
                audio_only = mode == MODE_AUDIO

                if not preset_name:
                    print("Cancelled: No preset selected.")
                elif not project.LoadRenderPreset(preset_name):
                    problem(f"Error: Could not load render preset '{preset_name}'.")
                elif ((audio_only or loaded_preset_is_sequence(project))
                      and audio_preset == preset_name):
                    # Checked before anything renders, so it costs nothing - the
                    # alternative is rendering the picture twice and failing anyway.
                    problem(f"Error: '{preset_name}' is selected for both the picture and the audio. "
                            f"The audio preset needs to be one that exports audio only. "
                            f"{AUDIO_PRESET_HOWTO}")
                else:
                    save_prefs({**load_prefs(), "last_preset": preset_name, "last_mode": mode,
                                "last_audio_preset": audio_preset})

                    # Render into a local staging folder first — After Effects is not
                    # contacted at all until the render is finished and placed in Resolve.
                    timeline_name = "".join(
                        c for c in timeline.GetName() if c.isalnum() or c in " _-"
                    ).strip().replace(" ", "_")
                    # Render under a unique temp name; the final incremented name is
                    # applied when the file moves into the AE project's FxLink folder
                    # (we can't count existing files there until AE tells us the path).
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    custom_name = f"{timeline_name}_FxLink_tmp_{timestamp}"

                    # Every render gets staging folders of its own, so an image
                    # sequence's frames can never mix with leftovers from an
                    # earlier attempt, nor with the audio.
                    render_dir = os.path.join(STAGING_DIR, custom_name)
                    audio_dir = render_dir + "_audio"
                    os.makedirs(render_dir, exist_ok=True)

                    picture_settings = {
                        "SelectAllFrames": False,
                        "TargetDir": render_dir,
                        "CustomName": custom_name,
                    }

                    kind = staged = audio_staged = None
                    mark_in = mark_out = None
                    failure = None
                    audio_attempted = False
                    try:
                        if audio_only:
                            # An empty placeholder in the preset's format - its own
                            # audio isn't wanted, the WAV below is the reference.
                            print("Rendering an empty placeholder (video tracks switched off for a moment)...")
                            _job, mark_in, mark_out = render_placeholder(
                                project, timeline, dict(picture_settings, ExportAudio=False))
                        else:
                            # No ExportAudio override — the selected preset decides.
                            print(f"Rendering {custom_name} ...")
                            _job, mark_in, mark_out = run_render(project, picture_settings, label="picture")

                        kind, staged = collect_render(render_dir, custom_name)
                        if not kind:
                            raise RuntimeError(f"The render finished but nothing named '{custom_name}' "
                                               f"turned up in '{render_dir}'.")
                        if kind == "sequence":
                            print(f"Rendered an image sequence of {len(staged)} frame(s).")

                        # An image sequence can't carry sound, so its audio is
                        # rendered on its own - as is the whole point of audio-only.
                        # A movie keeps the audio its preset gives it.
                        if audio_preset and (audio_only or kind == "sequence"):
                            print(f"Rendering the audio with '{audio_preset}'...")
                            audio_attempted = True
                            audio_staged = render_audio(project, audio_preset, audio_dir,
                                                        custom_name + "_audio")
                    except RuntimeError as e:
                        failure = str(e)
                        problem(f"Error: {failure}")
                        if os.listdir(render_dir):
                            print(f"Anything already rendered is safe at: {render_dir}")
                    finally:
                        # The audio render loaded its own preset; put yours back so
                        # the Deliver page isn't left on audio-only.
                        if audio_attempted:
                            project.LoadRenderPreset(preset_name)

                    if failure is None and kind:
                        safe_at = render_dir if kind == "sequence" else staged

                        print("Render complete. Asking After Effects for the open project path...")
                        aep_path = run_extendscript(afterfx_exe, GET_PROJECT_JSX)

                        # AE may still be starting up if we just launched it — retry
                        retry_deadline = time.time() + 90
                        while aep_path is None and time.time() < retry_deadline:
                            print("Waiting for After Effects to finish starting up...")
                            time.sleep(5)
                            aep_path = run_extendscript(afterfx_exe, GET_PROJECT_JSX, timeout=10)

                        if aep_path is None:
                            problem("Error: After Effects did not respond. Is it busy with a modal dialog?")
                            print(f"The render is safe at: {safe_at}")
                        elif aep_path == "__UNSAVED__":
                            problem("Error: The open AE project has never been saved. Save it first so it has a folder.")
                            print(f"The render is safe at: {safe_at}")
                        elif not os.path.exists(aep_path):
                            problem(f"Error: AE reported project path '{aep_path}' but it does not exist.")
                            print(f"The render is safe at: {safe_at}")
                        else:
                            aep_dir = os.path.dirname(aep_path)
                            fxlink_dir = os.path.join(aep_dir, "FxLink")
                            output_dir = os.path.join(aep_dir, "Output")
                            os.makedirs(fxlink_dir, exist_ok=True)
                            os.makedirs(output_dir, exist_ok=True)
                            print(f"AE project: {aep_path}")

                            # Final name: [Timeline Name]_FxLink_[001], incremented
                            # from what is already in the FxLink and Output folders.
                            # A movie is a file with that stem, a sequence a folder
                            # with that name, a WAV a file - the stem test fits all.
                            # Output is checked too: an audio-only link puts nothing
                            # picture-wise in FxLink.
                            base = f"{timeline_name}_FxLink_"
                            highest = 0
                            for folder in (fxlink_dir, output_dir):
                                for existing in os.listdir(folder):
                                    stem = os.path.splitext(existing)[0]
                                    m = re.fullmatch(re.escape(base) + r"(\d+)", stem)
                                    if m:
                                        highest = max(highest, int(m.group(1)))
                            final_name = f"{base}{highest + 1:03d}"

                            # In audio-only mode the picture is only a placeholder:
                            # it goes straight to Output for Resolve to link to, and
                            # AE never imports it, so no source copy in FxLink.
                            if kind == "movie":
                                ext = os.path.splitext(staged)[1]
                                output_path = os.path.join(output_dir, final_name + ext)
                                if audio_only:
                                    shutil.move(staged, output_path)
                                    render_path = None
                                else:
                                    render_path = os.path.join(fxlink_dir, final_name + ext)
                                    shutil.move(staged, render_path)
                                    shutil.copy2(render_path, output_path)
                                import_info = output_path
                            else:
                                # Own folder each side, frames renumbered from 0
                                # (see SEQUENCE_PAD for why).
                                ext = os.path.splitext(staged[0])[1]
                                out_seq = os.path.join(output_dir, final_name)
                                fx_seq = os.path.join(fxlink_dir, final_name)
                                os.makedirs(out_seq, exist_ok=True)
                                if not audio_only:
                                    os.makedirs(fx_seq, exist_ok=True)
                                for index, source in enumerate(staged):
                                    frame_name = f"{final_name}_{index:0{SEQUENCE_PAD}d}{ext}"
                                    if audio_only:
                                        shutil.move(source, os.path.join(out_seq, frame_name))
                                    else:
                                        moved = os.path.join(fx_seq, frame_name)
                                        shutil.move(source, moved)
                                        shutil.copy2(moved, os.path.join(out_seq, frame_name))
                                render_path = (None if audio_only else
                                               os.path.join(fx_seq, f"{final_name}_{0:0{SEQUENCE_PAD}d}{ext}"))
                                output_path = out_seq
                                import_info = {
                                    "FilePath": os.path.join(out_seq, f"{final_name}_%0{SEQUENCE_PAD}d{ext}"),
                                    "StartIndex": 0,
                                    "EndIndex": len(staged) - 1,
                                }

                            audio_path = None
                            if audio_staged:
                                audio_path = os.path.join(fxlink_dir, final_name + os.path.splitext(audio_staged)[1])
                                shutil.move(audio_staged, audio_path)

                            # The staging folders are empty now; tidy them away.
                            shutil.rmtree(render_dir, ignore_errors=True)
                            shutil.rmtree(audio_dir, ignore_errors=True)
                            if render_path:
                                print(f"Source: {render_path}")
                            if audio_path:
                                print(f"Audio:  {audio_path}")
                            print(f"Output: {output_path}")

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

                            fps = timeline.GetSetting("timelineFrameRate") or 24
                            frame_total = None
                            imported = media_pool.ImportMedia([import_info])
                            if not imported:
                                problem(f"Warning: Could not import '{output_path}' into media pool.")
                            elif mark_in is None:
                                problem("Warning: Could not read MarkIn from render job. Skipping timeline placement.")
                            else:
                                clip = imported[0]
                                clip_start_frame = 0
                                if kind == "sequence":
                                    # A sequence comes in at Resolve's default
                                    # still-sequence rate, not the timeline's.
                                    if not clip.SetClipProperty("FPS", str(fps)):
                                        problem(f"Warning: could not set the sequence's frame rate to {fps}.")
                                    # Sequence clips index from their file numbers.
                                    try:
                                        clip_start_frame = int(float(clip.GetClipProperty("Start") or 0))
                                    except (TypeError, ValueError):
                                        clip_start_frame = 0

                                clip_total_frames = int(clip.GetClipProperty("Frames"))
                                frame_total = clip_total_frames
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
                                    "startFrame": clip_start_frame,
                                    "endFrame": clip_start_frame + clip_total_frames,
                                    "mediaType": 1,
                                    "trackIndex": target_track,
                                    "recordFrame": record_frame,
                                }])

                                timeline_item = placed[0] if placed else None
                                if not timeline_item:
                                    problem("Warning: Could not place clip onto timeline (note: placement inside compound clips is not supported by the API).")
                                    print("The clip is in the FxLink bin — you can drag it manually.")
                                else:
                                    timeline_item.SetClipColor(CLIP_COLOR)
                                    print(f"Placed on video track {target_track} at frame {record_frame} ({CLIP_COLOR}).")

                                timeline.SetCurrentTimecode(playhead_tc)

                            # --- Everything in Resolve is done; now set up the AE project ---
                            # The comp's size and length come from the footage when
                            # there is some; in audio-only mode, from the timeline.
                            if frame_total is None and mark_in is not None and mark_out is not None:
                                frame_total = mark_out - mark_in + 1
                            try:
                                width = int(float(timeline.GetSetting("timelineResolutionWidth") or 1920))
                                height = int(float(timeline.GetSetting("timelineResolutionHeight") or 1080))
                            except (TypeError, ValueError):
                                width, height = 1920, 1080
                            duration = (frame_total or 1) / float(fps)

                            print("Setting up After Effects project...")
                            setup_jsx = (SETUP_PROJECT_JSX
                                         .replace("__SRC_FILE__", (render_path or "").replace("\\", "/"))
                                         .replace("__AUDIO_FILE__", (audio_path or "").replace("\\", "/"))
                                         .replace("__COMP_NAME__", final_name)
                                         .replace("__IS_SEQUENCE__", "true" if (kind == "sequence" and render_path) else "false")
                                         .replace("__FPS__", repr(float(fps)))
                                         .replace("__WIDTH__", str(width))
                                         .replace("__HEIGHT__", str(height))
                                         .replace("__DURATION__", repr(duration)))
                            ae_result = run_extendscript(afterfx_exe, setup_jsx, timeout=30)

                            if ae_result is None:
                                problem("Warning: AE did not confirm project setup (timeout). Check AE manually.")
                            elif ae_result.startswith("OK"):
                                if audio_only:
                                    print("AE ready: comp created with the audio in it, no footage.")
                                else:
                                    print("AE ready: comp created and opened.")
                                print("Work in AE, then select the comp and run FxLink Export to overwrite the Output file — Resolve will pick it up.")
                                if kind == "sequence":
                                    print("For a sequence, give FxLink Export an Output Module template named 'FxLink Sequence' that renders the same image format.")
                            else:
                                problem(f"Warning: AE setup failed: {ae_result}")

# Run directly (Resolve's Scripts menu) it behaves exactly as it always has.
# The FxLink Queue loads this file with FXLINK_AS_LIBRARY set, to call main()
# once per queued range itself and collect the problems into one summary.
if not globals().get("FXLINK_AS_LIBRARY"):
    main()
    show_problems()
