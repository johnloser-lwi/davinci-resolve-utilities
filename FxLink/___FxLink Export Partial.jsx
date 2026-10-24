// FxLink Export (Partial) - re-render only the frames you changed.
//
// For a comp that Resolve links to as an IMAGE SEQUENCE. Instead of rendering
// the whole comp again, this renders just a range and writes each frame to its
// own number, overwriting those frames in Output/<comp name>/ and leaving every
// other frame exactly as it was. Resolve needs nothing - it reads the frames
// from disk (run FxLink Refresh in Resolve if it shows stale frames).
//
// WHICH FRAMES
//   - If the comp has markers WITH A DURATION, each one is a range to render.
//     Good for several scattered fixes in one go.
//   - Otherwise the WORK AREA is the range (set it with B / N).
//   You see the exact ranges before anything is queued, and can add a few
//   frames of handle either side - worth it if the change involves motion
//   blur, echo, trails or frame blending, which reach into neighbouring frames.
//
// It is a separate script on purpose. Making the normal export go partial
// whenever the work area is trimmed would silently skip frames, because work
// areas get left trimmed by accident all the time.
//
// THE ONE THING THAT MUST BE RIGHT
//   Each frame has to be written to ITS OWN frame number. If AE numbered the
//   files from 0 instead, rendering frames 120-188 would overwrite frames 0-68:
//   the wrong frames, no error, and it would look like it worked. So the
//   numbering is set explicitly and read back, and if it can't be confirmed the
//   range is NOT queued.
//
// The format helpers below mirror the ones in "___FxLink Export.jsx". They are
// copied rather than shared so the proven full export didn't have to change to
// make room for this one.

(function () {
    var SEQUENCE_TEMPLATE_NAME = "FxLink Sequence";
    var SETTINGS_SECTION = "FxLink";
    var SETTINGS_HANDLES = "partialHandles";
    var proj = app.project;

    if (!proj || !proj.file) {
        alert("FxLink Partial: Save the project first so it has a folder.");
        return;
    }

    // --- which comp --------------------------------------------------------
    // The open comp is the natural target - you're editing it. Otherwise
    // exactly one comp selected in the project panel.
    var comp = (proj.activeItem instanceof CompItem) ? proj.activeItem : null;
    if (!comp) {
        var picked = [];
        for (var s = 0; s < proj.selection.length; s++) {
            if (proj.selection[s] instanceof CompItem) picked.push(proj.selection[s]);
        }
        if (picked.length === 1) comp = picked[0];
    }
    if (!comp) {
        alert("FxLink Partial: Open the comp you changed (or select just that one comp " +
              "in the project panel), then run this again.");
        return;
    }

    // --- the sequence it overwrites ----------------------------------------
    var outputDir = new Folder(proj.file.parent.fsName + "/Output");
    var seqDir = new Folder(outputDir.fsName + "/" + comp.name);
    if (!seqDir.exists) {
        alert("FxLink Partial: '" + comp.name + "' isn't linked to an image sequence " +
              "(there's no Output/" + comp.name + "/ folder).\n\n" +
              "Partial renders only work for image sequences. For a movie, use the full " +
              "FxLink Export.");
        return;
    }

    function escapeRegExp(text) {
        return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }

    var frameName = new RegExp("^" + escapeRegExp(comp.name) + "_(\\d+)(\\.[A-Za-z0-9]+)$");
    var ext = null;
    var lastFrame = -1;
    var existing = seqDir.getFiles();
    for (var f = 0; f < existing.length; f++) {
        if (!(existing[f] instanceof File)) continue;
        var m = frameName.exec(existing[f].displayName);
        if (!m) continue;
        var n = parseInt(m[1], 10);
        if (n > lastFrame) lastFrame = n;
        if (!ext) ext = m[2];
    }
    if (lastFrame < 0) {
        alert("FxLink Partial: Output/" + comp.name + "/ has no frames in it yet.\n\n" +
              "Do one full FxLink Export first - a partial render only replaces frames " +
              "that already exist.");
        return;
    }

    var fps = comp.frameRate;
    var compFrames = Math.round(comp.duration * fps);

    // --- the ranges --------------------------------------------------------
    var raw = [];
    var source;
    var markers = comp.markerProperty;
    for (var k = 1; k <= markers.numKeys; k++) {
        var mv = markers.keyValue(k);
        if (mv.duration > 0) {
            var a = Math.round(markers.keyTime(k) * fps);
            var b = Math.round((markers.keyTime(k) + mv.duration) * fps) - 1;
            raw.push([a, b]);
        }
    }
    if (raw.length) {
        source = raw.length + " marker(s) with a duration";
    } else {
        var wa = Math.round(comp.workAreaStart * fps);
        var wb = Math.round((comp.workAreaStart + comp.workAreaDuration) * fps) - 1;
        raw.push([wa, wb]);
        source = (wa === 0 && wb >= compFrames - 1)
            ? "the work area - which covers the WHOLE comp"
            : "the work area";
    }

    // A range reaching past the linked sequence means the comp got longer than
    // what Resolve imported. A partial can't add frames Resolve doesn't know
    // about - that needs a full export and a re-import.
    for (var r = 0; r < raw.length; r++) {
        if (raw[r][1] > lastFrame) {
            alert("FxLink Partial: the range ending at frame " + raw[r][1] + " goes past the " +
                  "end of the linked sequence (last frame " + lastFrame + ").\n\n" +
                  "The comp is now longer than what Resolve imported. Do a full FxLink " +
                  "Export, then re-import the sequence in Resolve.");
            return;
        }
    }

    function padAndMerge(ranges, handles) {
        // Handles are a convenience, so they're clipped to the frames that exist
        // rather than refused. Overlaps are merged so no frame renders twice.
        var padded = [];
        for (var i = 0; i < ranges.length; i++) {
            padded.push([Math.max(0, ranges[i][0] - handles),
                         Math.min(lastFrame, ranges[i][1] + handles)]);
        }
        padded.sort(function (x, y) { return x[0] - y[0]; });
        var merged = [];
        for (var j = 0; j < padded.length; j++) {
            var top = merged.length ? merged[merged.length - 1] : null;
            if (top && padded[j][0] <= top[1] + 1) {
                if (padded[j][1] > top[1]) top[1] = padded[j][1];
            } else {
                merged.push([padded[j][0], padded[j][1]]);
            }
        }
        return merged;
    }

    // --- confirm -----------------------------------------------------------
    var savedHandles = 0;
    if (app.settings.haveSetting(SETTINGS_SECTION, SETTINGS_HANDLES)) {
        savedHandles = parseInt(app.settings.getSetting(SETTINGS_SECTION, SETTINGS_HANDLES), 10) || 0;
    }

    var dlg = new Window("dialog", "FxLink Export (Partial)");
    dlg.orientation = "column";
    dlg.alignChildren = ["fill", "top"];
    dlg.add("statictext", undefined, "Comp:  " + comp.name);
    dlg.add("statictext", undefined, "Ranges from:  " + source);
    var list = dlg.add("listbox", undefined, []);
    list.preferredSize = [380, 130];
    var handleRow = dlg.add("group");
    handleRow.add("statictext", undefined, "Handles (extra frames each side):");
    var handleField = handleRow.add("edittext", undefined, String(savedHandles));
    handleField.characters = 4;
    var summary = dlg.add("statictext", undefined, "", { multiline: true });
    summary.preferredSize = [380, 36];
    var buttons = dlg.add("group");
    buttons.alignment = "right";
    buttons.add("button", undefined, "Cancel", { name: "cancel" });
    buttons.add("button", undefined, "Queue", { name: "ok" });

    var ranges = [];
    var handles = savedHandles;

    function refresh() {
        handles = parseInt(handleField.text, 10);
        if (isNaN(handles) || handles < 0) handles = 0;
        ranges = padAndMerge(raw, handles);
        list.removeAll();
        var total = 0;
        for (var i = 0; i < ranges.length; i++) {
            var count = ranges[i][1] - ranges[i][0] + 1;
            total += count;
            list.add("item", "frames " + ranges[i][0] + " - " + ranges[i][1] +
                             "   (" + count + ")");
        }
        var sequenceFrames = lastFrame + 1;
        summary.text = "Re-render " + total + " of " + sequenceFrames + " frames. The other " +
                       (sequenceFrames - total) + " stay as they are.";
    }
    handleField.onChanging = refresh;
    refresh();

    if (dlg.show() !== 1) return;
    app.settings.saveSetting(SETTINGS_SECTION, SETTINGS_HANDLES, String(handles));

    // --- output module helpers (mirror ___FxLink Export.jsx) ---------------
    var SEQUENCE_FORMATS = {
        ".png": "PNG Sequence",
        ".tif": "TIFF Sequence", ".tiff": "TIFF Sequence",
        ".exr": "OpenEXR Sequence",
        ".jpg": "JPEG Sequence", ".jpeg": "JPEG Sequence",
        ".dpx": "DPX/Cineon Sequence",
        ".tga": "Targa Sequence"
    };

    function settingsOf(om) {
        try { return om.getSettings(GetSettingsFormat.STRING); } catch (e) { return null; }
    }

    function currentFormat(om) {
        var s = settingsOf(om);
        return s ? String(s["Format"]) : null;
    }

    function ensureSequenceFormat(om, extension) {
        var want = SEQUENCE_FORMATS[extension.toLowerCase()];
        var now = currentFormat(om);
        if (now === null) return "could not read the Output Module's format";
        if (want && now === want) return null;
        if (!want && now.indexOf("Sequence") !== -1) return null;
        if (want) {
            try { om.setSettings({ "Format": want }); } catch (e) { }
            now = currentFormat(om);
            if (now === want) return null;
        }
        return "it renders '" + now + "', not " + (want || "an image sequence");
    }

    function findSetting(node, key, path) {
        // Where a setting lives in getSettings()' (possibly nested) result, as
        // a list of keys - so setSettings can be built to the same shape.
        for (var name in node) {
            if (name === key) return path.concat([name]);
            if (node[name] && typeof node[name] === "object") {
                var found = findSetting(node[name], key, path.concat([name]));
                if (found) return found;
            }
        }
        return null;
    }

    function nest(path, value) {
        var out = {};
        var cursor = out;
        for (var i = 0; i < path.length - 1; i++) {
            cursor[path[i]] = {};
            cursor = cursor[path[i]];
        }
        cursor[path[path.length - 1]] = value;
        return out;
    }

    function valueAt(node, path) {
        for (var i = 0; i < path.length; i++) {
            if (!node) return null;
            node = node[path[i]];
        }
        return node;
    }

    function topLevelKeys(node) {
        var names = [];
        for (var name in node) names.push(name);
        return names.join(", ");
    }

    function setNumbering(om, startFrame) {
        // Make the first file of this range carry ITS frame number. "Starting #"
        // does that directly; "Use Comp Frame Number" would override it, so it
        // is switched off. Both are read back - a guess here is exactly the
        // silent wrong-frames failure this script exists to avoid.
        var before = settingsOf(om);
        if (!before) return "could not read the Output Module settings";

        var startPath = findSetting(before, "Starting #", []);
        if (!startPath) {
            return "AE didn't expose a 'Starting #' setting. Settings it did expose: " +
                   topLevelKeys(before);
        }
        var compNumberPath = findSetting(before, "Use Comp Frame Number", []);

        try {
            if (compNumberPath) om.setSettings(nest(compNumberPath, false));
        } catch (e) { }

        // getSettings reports values as strings; whether setSettings wants a
        // number or a string isn't documented, so try both and keep whichever
        // reads back.
        var tries = [startFrame, String(startFrame)];
        var lastError = "";
        for (var t = 0; t < tries.length; t++) {
            try {
                om.setSettings(nest(startPath, tries[t]));
            } catch (e) {
                lastError = e.toString();
                continue;
            }
            if (String(valueAt(settingsOf(om), startPath)) === String(startFrame)) break;
        }

        var after = settingsOf(om);
        if (lastError && String(valueAt(after, startPath)) !== String(startFrame)) {
            return "AE refused the frame numbering: " + lastError;
        }
        if (String(valueAt(after, startPath)) !== String(startFrame)) {
            return "frame numbering didn't take (asked for " + startFrame + ", AE has " +
                   valueAt(after, startPath) + ")";
        }
        if (compNumberPath && String(valueAt(after, compNumberPath)) === "true") {
            return "'Use Comp Frame Number' stayed on, which would override the numbering";
        }
        return null;
    }

    function removeQueuedItemsFor(target) {
        var rq = proj.renderQueue;
        for (var i = rq.numItems; i >= 1; i--) {
            var item = rq.item(i);
            if (item.comp === target && item.status !== RQItemStatus.RENDERING &&
                item.status !== RQItemStatus.DONE) {
                item.remove();
            }
        }
    }

    // --- queue -------------------------------------------------------------
    app.beginUndoGroup("FxLink Export (Partial)");
    removeQueuedItemsFor(comp);

    var templateMissing = false;
    var problems = [];
    var queued = 0;

    for (var q = 0; q < ranges.length; q++) {
        var first = ranges[q][0];
        var count = ranges[q][1] - first + 1;
        var label = "frames " + first + "-" + ranges[q][1];

        var item = proj.renderQueue.items.add(comp);

        // Set in exact frame durations, then read back as frames: a time that
        // rounds the wrong way at 23.976 would render a neighbouring frame.
        item.timeSpanStart = first * comp.frameDuration;
        item.timeSpanDuration = count * comp.frameDuration;
        if (Math.round(item.timeSpanStart / comp.frameDuration) !== first ||
            Math.round(item.timeSpanDuration / comp.frameDuration) !== count) {
            item.remove();
            problems.push(label + ": AE wouldn't take the exact time span");
            continue;
        }

        var om = item.outputModule(1);
        try { om.applyTemplate(SEQUENCE_TEMPLATE_NAME); } catch (e) { templateMissing = true; }

        var formatProblem = ensureSequenceFormat(om, ext);
        if (formatProblem) {
            item.remove();
            problems.push(label + ": " + formatProblem);
            continue;
        }

        // File before numbering, in case setting the file resets it.
        om.file = new File(seqDir.fsName + "/" + comp.name + "_[#####]" + ext);

        var numberingProblem = setNumbering(om, first);
        if (numberingProblem) {
            item.remove();
            problems.push(label + ": " + numberingProblem);
            continue;
        }
        queued++;
    }

    app.endUndoGroup();

    if (problems.length) {
        alert("FxLink Partial: " + problems.length + " range(s) were NOT queued, so nothing " +
              "can overwrite the wrong frames:\n\n  " + problems.join("\n  ") +
              (queued ? "\n\n" + queued + " other range(s) are queued." : ""));
    } else if (templateMissing) {
        alert("FxLink Partial: no Output Module template named '" + SEQUENCE_TEMPLATE_NAME +
              "', so AE's default settings are used with the format set to match.\n\n" +
              "Check Channels before pressing Render - RGB + Alpha if the graphic needs " +
              "transparency.");
    }

    // As with the full export: rendering from a script freezes AE's UI, so
    // queue and let you press Render.
    if (queued) proj.renderQueue.showWindow(true);
})();
