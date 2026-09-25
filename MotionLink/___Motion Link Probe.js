// Motion Link Probe - a read-only diagnostic. It changes nothing.
//
// Cavalry does not publicly document the attribute IDs for a Composition's
// frame rate, duration and size, nor the layer type string for the Sound
// behaviour. This dumps them so the CAV block in "___Motion Link Build Comp.js"
// can be corrected from fact instead of guesswork.
//
// HOW TO RUN
//   1. Make a Composition. Add a Sound behaviour to it by hand (drag a .wav
//      from the Assets window into the Scene window) and select it.
//   2. Window > Scripts > John > ___Motion Link Probe
//   3. Read the Console. A copy is written to
//      <home>/AppData/Roaming/motion_link/probe_dump.json
//
// IF THE DUMP IS EMPTY: every introspection function name below is a guess, so
// they may all be wrong. Fall back to reading the IDs straight out of Cavalry's
// Attribute Editor, or from JavaScript Editor autocomplete on a layer id. The
// value shown next to each candidate is the giveaway - a 1920 is the width.

(function () {
    var out = { probedAt: new Date().toISOString(), api: {}, comp: {}, selection: {} };

    function log(line) { console.log(line); }

    function attempt(label, fn) {
        // Every call here is a guess at a function that may not exist, so each
        // one gets its own try/catch and the failures are reported rather than
        // swallowed - knowing which name works is half the point of the probe.
        try {
            var value = fn();
            if (value === undefined || value === null) {
                return { ok: false, why: "returned nothing" };
            }
            return { ok: true, value: value };
        } catch (e) {
            return { ok: false, why: String(e) };
        }
    }

    function firstWorking(label, candidates) {
        for (var i = 0; i < candidates.length; i++) {
            var got = attempt(label, candidates[i].fn);
            if (got.ok) {
                log("  " + label + ": api." + candidates[i].name + "() worked");
                return { name: candidates[i].name, value: got.value };
            }
            log("  " + label + ": api." + candidates[i].name + "() -> " + got.why);
        }
        return null;
    }

    log("\n===== MOTION LINK PROBE =====");

    // --- 1. What does the api object actually expose? ----------------------
    // This single listing is worth more than every guess below it.
    log("\n--- api functions ---");
    try {
        var names = Object.getOwnPropertyNames(api);
        if (!names || !names.length) { names = Object.keys(api); }
        names = names.sort();
        out.api.functions = names;
        log("  " + names.length + " member(s):");
        log("  " + names.join(", "));
    } catch (e) {
        out.api.error = String(e);
        log("  could not enumerate the api object: " + e);
    }

    // --- 2. The active composition ----------------------------------------
    log("\n--- active composition ---");
    var compId = null;
    try {
        compId = api.getActiveComp();
        out.comp.id = compId;
        log("  getActiveComp() -> " + compId);
    } catch (e) {
        log("  getActiveComp() failed: " + e);
    }

    function dumpAttributes(owner, bucket, label) {
        if (!owner) {
            log("  no " + label + " to inspect");
            return;
        }
        var found = firstWorking(label + " attribute ids", [
            { name: "getAttrIds", fn: function () { return api.getAttrIds(owner); } },
            { name: "getAttributeIds", fn: function () { return api.getAttributeIds(owner); } },
            { name: "getAttributes", fn: function () { return api.getAttributes(owner); } },
            { name: "getAttrs", fn: function () { return api.getAttrs(owner); } },
            { name: "getLayerAttributes", fn: function () { return api.getLayerAttributes(owner); } }
        ]);
        if (!found) {
            bucket.attributesError = "no introspection function responded";
            log("  none of the attribute-listing names worked for the " + label);
            return;
        }
        bucket.attributeSource = found.name;

        var ids = found.value;
        if (!(ids instanceof Array)) {
            // Some builds may hand back an object keyed by id.
            try { ids = Object.keys(ids); } catch (e2) { ids = []; }
        }
        bucket.attributes = {};
        log("  " + ids.length + " attribute(s). id -> current value:");
        for (var i = 0; i < ids.length; i++) {
            var id = ids[i];
            var value;
            try {
                value = api.get(owner, id);
            } catch (e3) {
                value = "<unreadable: " + e3 + ">";
            }
            bucket.attributes[id] = value;
            log("    " + id + " = " + JSON.stringify(value));
        }
    }

    dumpAttributes(compId, out.comp, "composition");

    // --- 3. The selected layer (hopefully the Sound behaviour) -------------
    log("\n--- selection ---");
    var selected = firstWorking("selection", [
        { name: "getSelection", fn: function () { return api.getSelection(); } },
        { name: "getSelectedLayers", fn: function () { return api.getSelectedLayers(); } },
        { name: "getSelected", fn: function () { return api.getSelected(); } }
    ]);

    var soundId = null;
    if (selected) {
        out.selection.source = selected.name;
        out.selection.ids = selected.value;
        log("  selected: " + JSON.stringify(selected.value));
        soundId = (selected.value instanceof Array) ? selected.value[0] : selected.value;
    } else {
        log("  nothing selected, or no selection function responded.");
        log("  Select the Sound behaviour and run this again.");
    }

    if (soundId) {
        var type = firstWorking("layer type", [
            { name: "getType", fn: function () { return api.getType(soundId); } },
            { name: "get(id,'type')", fn: function () { return api.get(soundId, "type"); } },
            { name: "getLayerType", fn: function () { return api.getLayerType(soundId); } }
        ]);
        if (type) {
            out.selection.type = type.value;
            log("  >>> LAYER TYPE STRING: " + JSON.stringify(type.value));
            log("  >>> this is what goes in CAV.soundLayerType");
        }
        dumpAttributes(soundId, out.selection, "selected layer");
    }

    // --- 4. Where are we allowed to write? --------------------------------
    log("\n--- paths ---");
    var paths = {};
    var pathFns = ["getHomeFolder", "getScenesPath", "getAssetPath", "getPreferencesPath",
                   "getRenderPath", "getProjectPath", "getPresetsPath", "getDesktopFolder"];
    for (var p = 0; p < pathFns.length; p++) {
        var got = attempt(pathFns[p], (function (fname) {
            return function () { return api[fname](); };
        })(pathFns[p]));
        paths[pathFns[p]] = got.ok ? got.value : ("<" + got.why + ">");
        log("  " + pathFns[p] + "() -> " + paths[pathFns[p]]);
    }
    out.paths = paths;

    try {
        out.sceneFile = api.getSceneFilePath();
        log("  getSceneFilePath() -> " + out.sceneFile);
    } catch (e) {
        log("  getSceneFilePath() failed: " + e);
    }

    // --- 5. Write the dump somewhere readable -----------------------------
    var home = paths.getHomeFolder;
    var dumpPath = null;
    if (home && String(home).charAt(0) !== "<") {
        dumpPath = String(home).replace(/\\/g, "/") + "/AppData/Roaming/motion_link/probe_dump.json";
        try {
            // writeToFile will not create the folder, so this only lands once
            // the Resolve side has run at least one export. Not a problem - the
            // Console output above is the primary result.
            var wrote = api.writeToFile(dumpPath, JSON.stringify(out, null, 2), true);
            log("\nDump written to: " + dumpPath + "  (" + wrote + ")");
        } catch (e) {
            log("\nCould not write the dump (" + e + ").");
            log("The Console output above is the real result - copy it from here.");
        }
    }

    log("\n===== END PROBE =====\n");
})();
