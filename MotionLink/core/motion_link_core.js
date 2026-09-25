// Motion Link core - shared by the menu scripts, and NOT a menu entry itself:
// it lives in %APPDATA%\motion_link, outside Cavalry's Scripts folder, so it
// never shows up under Window > Scripts.
//
// Builds a Composition from a job file written by "Motion Link Export" in
// DaVinci Resolve: frame rate, length and resolution matched to the timeline,
// with the exported audio in it as a timing guide. Nothing goes back to Resolve.
//
// WHY A FILE AND NOT A LIVE CONNECTION
//   Cavalry has no equivalent of After Effects' "AfterFX.exe -r script.jsx", so
//   Resolve cannot push a command into a running Cavalry. cavalry-cli is
//   Enterprise-only and was removed in 2.7 anyway. The alternative, api.WebServer,
//   has to be started by hand from a UI script every session - a bigger chore
//   than clicking a menu item. So Resolve writes a job file and this reads it.
//
// FRAME MAPPING
//   Cavalry frame 0 is the start of the exported range, not the start of the
//   Resolve timeline:  resolve_frame = job.range.mark_in + cavalry_frame

function motionLinkBuild(jobPathOverride) {


// ======================= ATTRIBUTE CANDIDATES =======================
// Cavalry does not document the attribute IDs for a Composition's settings, and
// a wrong ID is a SILENT no-op - which is exactly how the first version of this
// script produced default comps while claiming success.
//
// So rather than commit to one guess, each setting lists candidates best-first
// and the script finds the one that actually works: it reads the attribute,
// writes it, then reads it back, and only accepts an ID whose value really
// changed. The winning IDs are logged and saved to last_result.json.
//
// "resolution.y" is confirmed from Cavalry's own docs
// (api.get(api.getActiveComp(), "resolution.y")), so resolution is a vector,
// not a pair of scalars. Composition length is a FRAME RANGE - a start and an
// end frame - not a duration, which is why the range is computed from the
// comp's existing start rather than assumed to begin at 0.
// First entry in each list is CONFIRMED on Cavalry 2.x (probe_dump.json, Sept
// 2026: a Composition exposes fps, startFrame, endFrame and resolution{x,y}).
// The rest are kept as fallbacks in case a future version renames one - the
// search costs nothing when the first candidate works.
var CANDIDATES = {
    frameRate:  ["fps", "frameRate", "frameRate.value"],
    rangeStart: ["startFrame", "frameRange.x", "range.x"],
    rangeEnd:   ["endFrame", "frameRange.y", "range.y"],
    width:      ["resolution.x", "width", "size.x", "dimensions.x"],
    height:     ["resolution.y", "height", "size.y", "dimensions.y"]
};

// Audio needs no attribute guesses at all: addAssetToComp builds and wires the
// Sound behaviour itself.
var CAV = {
    compNameAttr: "niceName"
};
// ====================================================================

var JOB_VERSION = 1;
var FALLBACK_INBOX = "C:/Users/john-/AppData/Roaming/motion_link";

function log(line) { console.log(line); }

function inboxDir() {
    // Matches %APPDATA%\motion_link on the Resolve side.
    try {
        var home = api.getHomeFolder();
        if (home) {
            return String(home).replace(/\\/g, "/") + "/AppData/Roaming/motion_link";
        }
    } catch (e) { /* fall through */ }
    return FALLBACK_INBOX;
}

function readJob(explicitPath) {
    var path = explicitPath || (inboxDir() + "/latest_job.json");
    if (!api.filePathExists(path)) {
        log("No Motion Link job found.");
        log("  Expected: " + path);
        log("  Run 'Motion Link Export' in DaVinci Resolve first.");
        return null;
    }
    var raw;
    try {
        raw = api.readFromFile(path);
    } catch (e) {
        log("Could not read the job file: " + e);
        return null;
    }
    var job;
    try {
        job = JSON.parse(raw);
    } catch (e) {
        log("The job file is not valid JSON (" + e + ").");
        log("  " + path);
        return null;
    }
    if (job.motion_link_version !== JOB_VERSION) {
        // Refuse rather than half-build against a schema this script predates.
        log("This job was written by a different version of Motion Link "
            + "(job says " + job.motion_link_version + ", this script expects "
            + JOB_VERSION + "). Redeploy the scripts so both sides match.");
        return null;
    }
    if (!job.audio || !job.audio.path) {
        log("The job has no audio path in it.");
        return null;
    }
    if (!api.filePathExists(job.audio.path)) {
        log("The audio file named in the job is missing:");
        log("  " + job.audio.path);
        log("  It may have been moved or renamed since the export. Re-export "
            + "from Resolve.");
        return null;
    }

    // Resolve overwrites this file on every SUCCESSFUL export, so it is always
    // the newest one. But an export that fails partway leaves the previous job
    // in place, and rebuilding that silently would be indistinguishable from
    // success - so say out loud which export this is and how old it is.
    log("Job written : " + (job.created || "unknown time") + describeAge(job.created));
    log("Audio file  : " + String(job.audio.path).split("/").pop());

    return job;
}

function describeAge(created) {
    if (!created) { return ""; }
    var then = new Date(created);          // local time, matching the writer
    if (isNaN(then.getTime())) { return ""; }
    var minutes = Math.round((new Date().getTime() - then.getTime()) / 60000);
    if (minutes < 0) { return ""; }
    if (minutes < 1) { return "   (just now)"; }
    if (minutes < 60) { return "   (" + minutes + " min ago)"; }
    var hours = Math.round(minutes / 60);
    if (hours < 24) {
        return "   (" + hours + "h ago - check this is the export you meant)";
    }
    return "   (" + Math.round(hours / 24) + " day(s) ago - check this is the "
        + "export you meant)";
}

var ASSET_FOLDER = "MotionLink";     // where imported audio lands
var COMP_FOLDER = "_Outputs";        // where the Composition lands

function folderIndexPath() {
    return inboxDir() + "/folder_index.json";
}

function findOrCreateFolder(name) {
    // Cavalry can create an Assets Window group but offers no way to search for
    // one by name, so the IDs are remembered between runs. An ID is only reused
    // if it still reads back - a folder you deleted, or one belonging to another
    // scene, is detected and replaced rather than parented into silently.
    var scene = "";
    try { scene = api.getSceneFilePath() || ""; } catch (e) { /* unsaved scene */ }
    var key = scene + "|" + name;

    var index = {};
    try {
        if (api.filePathExists(folderIndexPath())) {
            index = JSON.parse(api.readFromFile(folderIndexPath())) || {};
        }
    } catch (e) { index = {}; }

    var known = index[key];
    if (known) {
        var alive = false;
        try {
            var label = api.get(known, CAV.compNameAttr);
            alive = (label !== undefined && label !== null && String(label) !== "");
        } catch (e) { alive = false; }
        if (alive) { return known; }
        log("    (the remembered '" + name + "' folder is gone - making a new one)");
    }

    var folderId = null;
    try {
        folderId = api.createAssetGroup(name);
    } catch (e) {
        log("  ! could not create the '" + name + "' folder: " + e);
        return null;
    }
    if (!folderId) { return null; }

    index[key] = folderId;
    try {
        api.writeToFile(folderIndexPath(), JSON.stringify(index, null, 2), true);
    } catch (e) { /* losing the index only costs a duplicate folder next run */ }
    return folderId;
}

function fileInto(layerId, folderName) {
    if (!layerId) { return false; }
    var folderId = findOrCreateFolder(folderName);
    if (!folderId) { return false; }
    try {
        api.parent(layerId, folderId);
        log("    filed into " + folderName);
        return true;
    } catch (e) {
        log("  ! could not file into " + folderName + ": " + e);
        return false;
    }
}

function close(a, b) {
    if (typeof a === "number" && typeof b === "number") {
        return Math.abs(a - b) < 0.01;
    }
    return String(a) === String(b);
}

function readAttr(targetId, ids) {
    // The first candidate that reads back a usable value. Reading first means a
    // wrong-but-existing attribute never gets written to.
    for (var i = 0; i < ids.length; i++) {
        var value;
        try {
            value = api.get(targetId, ids[i]);
        } catch (e) {
            continue;
        }
        if (value !== undefined && value !== null && value !== "") {
            return { id: ids[i], value: value };
        }
    }
    return null;
}

function writeAttr(targetId, ids, wanted, label) {
    // Try each candidate ID until one demonstrably takes the value. "Set it and
    // hope" is what produced default comps last time, so nothing is reported as
    // applied unless the read-back proves it.
    for (var i = 0; i < ids.length; i++) {
        var id = ids[i];
        var before;
        try {
            before = api.get(targetId, id);
        } catch (e) {
            continue;                       // attribute does not exist
        }
        if (before === undefined || before === null) { continue; }

        var payload = {};
        payload[id] = wanted;
        try {
            api.set(targetId, payload);
        } catch (e) {
            continue;
        }

        var after;
        try {
            after = api.get(targetId, id);
        } catch (e) {
            continue;
        }
        if (close(after, wanted)) {
            log("    " + label + ": " + id + " = " + JSON.stringify(after));
            return { id: id, value: after };
        }
        if (close(after, before)) { continue; }   // no-op, wrong attribute

        // It moved but not to what we asked for - an enum, or a clamp.
        log("  ! " + label + ": " + id + " accepted " + JSON.stringify(wanted)
            + " but reads back " + JSON.stringify(after));
        return { id: id, value: after, approximate: true };
    }
    log("  ! " + label + ": none of [" + ids.join(", ") + "] would take "
        + JSON.stringify(wanted));
    return null;
}

function attachAudio(job) {
    // Returns "wired" | "imported" | "failed".
    var assetId;
    try {
        assetId = api.loadAsset(job.audio.path, false);
    } catch (e) {
        log("  could not load the audio asset: " + e);
        return "failed";
    }
    if (!assetId) {
        log("  loadAsset returned nothing for " + job.audio.path);
        return "failed";
    }
    log("  audio asset loaded: " + assetId);
    fileInto(assetId, ASSET_FOLDER);

    // addAssetToComp does the whole job for audio: it creates the Footage Shape
    // AND its own Sound behaviour, already wired and already playing. An earlier
    // version then added a second Sound behaviour of its own ("Motion Link
    // Audio"), which was pure duplication - it sat on top doing nothing, because
    // the one Cavalry made was already driving the audio.
    try {
        api.addAssetToComp(assetId);
    } catch (e) {
        log("  ! addAssetToComp failed: " + e);
        log("  The asset is in the Assets window but not in the Composition - "
            + "drag it in by hand.");
        return "imported";
    }
    return "wired";
}

// ---------------------------------------------------------------- main

log("\n===== MOTION LINK =====");

var job = readJob(jobPathOverride);
if (!job) {
    log("===== nothing built =====\n");
    return;
}

log("Job from Resolve timeline: " + job.resolve.timeline
    + "  (project: " + job.resolve.project + ")");

var compId;
try {
    // Always a new Composition. Re-exporting gives you _002 beside _001, which
    // is predictable and has nothing to go stale; Cavalry offers no verified
    // "find comp by name", so reusing one would mean keeping an id cache that
    // breaks the moment a comp is deleted.
    compId = api.createComp(job.comp.name);
} catch (e) {
    log("Could not create the Composition: " + e);
    log("===== nothing built =====\n");
    return;
}
if (!compId) {
    log("createComp returned nothing - cannot continue.");
    log("===== nothing built =====\n");
    return;
}
log("Created Composition: " + job.comp.name + "  [" + compId + "]");
fileInto(compId, COMP_FOLDER);

try {
    api.setActiveComp(compId);
} catch (e) {
    log("  could not make it the active Composition: " + e);
}

log("\nApplying Composition settings:");
var applied = {};
var bad = 0;

function apply(key, wanted, label) {
    var got = writeAttr(compId, CANDIDATES[key], wanted, label);
    if (!got) { bad++; return null; }
    applied[key] = got.id;
    if (got.approximate) { bad++; }
    return got;
}

apply("width", job.comp.width, "width");
apply("height", job.comp.height, "height");

// Frame rate may be an enum of named rates rather than a free number, which is
// why the job carries fps_string as well.
var rate = apply("frameRate", job.comp.fps, "frame rate");
if (!rate) {
    log("    (frame rate may be a preset list rather than a number - set it by "
        + "hand to " + job.comp.fps_string + ")");
}

// Length is a frame RANGE, not a duration. Keep whatever start frame the comp
// already uses rather than assuming it begins at 0, and move only the end.
var startNow = readAttr(compId, CANDIDATES.rangeStart);
var start = startNow ? Number(startNow.value) : 0;
if (!isFinite(start)) { start = 0; }
var end = start + job.comp.duration_frames - 1;
log("    length: " + job.comp.duration_frames + " frames -> range "
    + start + " to " + end);
apply("rangeEnd", end, "range end");

log("\nAttaching audio:");
var audioState = attachAudio(job);

// A receipt, so the Resolve panel can report what Cavalry last did without any
// live connection between the two.
try {
    api.writeToFile(inboxDir() + "/last_result.json", JSON.stringify({
        builtAt: new Date().toISOString(),
        comp: job.comp.name,
        compId: compId,
        timeline: job.resolve.timeline,
        audio: audioState,
        attributeMismatches: bad,
        // Which candidate IDs actually worked on this Cavalry version - so the
        // guessing only ever has to happen once.
        resolvedAttributeIds: applied
    }, null, 2), true);
} catch (e) { /* a missing receipt is not worth reporting */ }

log("\n----- done -----");
log("Composition : " + job.comp.name);
log("Size        : " + job.comp.width + " x " + job.comp.height);
log("Frame rate  : " + job.comp.fps_string);
log("Duration    : " + job.comp.duration_frames + " frames");
log("Audio       : " + job.audio.path.split("/").pop() + "  (" + audioState + ")");
log("Timing      : Cavalry frame 0 = Resolve frame " + job.range.mark_in
    + "  (" + job.range.in_timecode + ")");
if (bad > 0) {
    log("");
    log(bad + " Composition setting(s) did not take. Run ___Motion Link Probe.js "
        + "- it lists every attribute ID next to its current value - and add the "
        + "right one to the CANDIDATES block at the top of this script.");
} else {
    log("Attribute IDs used: " + JSON.stringify(applied));
}
log("");
log("Two things this script cannot do for you:");
log("  - turn on Audio Playback in the Viewport settings (bottom of the viewport)");
log("  - tick Play Audio on the Sound behaviour");
log("");
log("The scene has NOT been saved - that is deliberate, in case you were mid-edit.");
log("===== END =====\n");
}

function motionLinkInbox() {
    try {
        var home = api.getHomeFolder();
        if (home) {
            return String(home).replace(/\\/g, "/") + "/AppData/Roaming/motion_link";
        }
    } catch (e) { /* fall through */ }
    return "C:/Users/john-/AppData/Roaming/motion_link";
}

function motionLinkNewestFolder() {
    // The folder the newest export was written to.
    try {
        var latest = motionLinkInbox() + "/latest_job.json";
        if (api.filePathExists(latest)) {
            var job = JSON.parse(api.readFromFile(latest));
            if (job && job.audio && job.audio.path) {
                var p = String(job.audio.path);
                return p.substring(0, p.lastIndexOf("/"));
            }
        }
    } catch (e) { /* no newest export yet */ }
    try { return api.getHomeFolder() || ""; } catch (e) { return ""; }
}

function motionLinkPickJob() {
    // Opens where the newest export landed, so the ones you might want to
    // re-import are already in front of you.
    var start = motionLinkNewestFolder();
    var jobs = motionLinkListJobs(start);
    if (jobs.length) {
        console.log(jobs.length + " job(s) in " + start + ":");
        for (var i = 0; i < jobs.length; i++) {
            console.log("  " + jobs[i].split("/").pop());
        }
    }

    var chosen = "";
    try {
        chosen = api.presentOpenFile(start, "Choose a Motion Link export to build",
                                     "Motion Link job (*.json)");
    } catch (e) {
        console.log("Could not open the file dialog: " + e);
        return null;
    }
    if (!chosen) {
        console.log("Cancelled - nothing built.");
        return null;
    }
    return String(chosen).replace(/\\/g, "/");
}

function motionLinkListJobs(folder) {
    // Cavalry does expose directory listing (listDirectory / listDirectoryPaths),
    // contrary to what the first version of these scripts assumed.
    var out = [];
    if (!folder) { return out; }
    try {
        var entries = api.listDirectory(folder) || [];
        for (var i = 0; i < entries.length; i++) {
            if (/_MotionLink_[0-9]+\.json$/i.test(entries[i])) {
                out.push(String(entries[i]).replace(/\\/g, "/"));
            }
        }
        out.sort();
    } catch (e) { /* unreadable folder - the dialog still opens */ }
    return out;
}
