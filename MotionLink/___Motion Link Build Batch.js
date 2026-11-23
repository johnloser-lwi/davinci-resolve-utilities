// Motion Link - build every Composition from the last batch export.
//
// Pairs with "Motion Link Queue" in DaVinci Resolve: after Export All there,
// this builds one Composition per queued range - each with matching frame
// rate, length and resolution, and its audio in it - exactly as
// "Motion Link Build Comp" builds a single one.
//
// To redo just one range of the batch, use "Motion Link Build From File" and
// pick its <name>_MotionLink_NNN.json.
//
// The build itself lives in motion_link_core.js, in the motion_link data folder (see inbox() below),
// shared with the other Motion Link scripts.

(function () {

    function inbox() {
        // The folder deploy puts motion_link_core.js in - the same folder
        // Resolve's Motion Link Export writes its jobs to:
        //   Mac      ~/Library/Application Support/ResolveUtilities/motion_link
        //   Windows  %APPDATA%\motion_link
        var home = "";
        try { home = String(api.getHomeFolder() || "").replace(/\\/g, "/"); } catch (e) { /* none */ }
        var places = [home + "/Library/Application Support/ResolveUtilities/motion_link",
                      home + "/AppData/Roaming/motion_link"];
        for (var i = 0; i < places.length; i++) {
            if (api.filePathExists(places[i] + "/motion_link_core.js")) { return places[i]; }
        }
        return places[0];
    }

    var corePath = inbox() + "/motion_link_core.js";
    if (!api.filePathExists(corePath)) {
        console.log("Motion Link: the shared core is missing.");
        console.log("  Expected: " + corePath);
        console.log("  Run deploy.sh (Mac) / deploy.ps1 (Windows) from the davinci-resolve-utilities repo.");
        return;
    }

    try {
        eval(api.readFromFile(corePath));
    } catch (e) {
        console.log("Motion Link: the shared core would not load: " + e);
        return;
    }

    if (typeof motionLinkBuildBatch !== "function") {
        console.log("Motion Link: the core loaded but motionLinkBuildBatch is missing "
                    + "- it is an older copy. Re-run the deploy script.");
        return;
    }

    motionLinkBuildBatch();

})();
