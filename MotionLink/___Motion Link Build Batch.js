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
// The build itself lives in motion_link_core.js, in %APPDATA%\motion_link,
// shared with the other Motion Link scripts.

(function () {

    function inbox() {
        try {
            var home = api.getHomeFolder();
            if (home) {
                return String(home).replace(/\\/g, "/") + "/AppData/Roaming/motion_link";
            }
        } catch (e) { /* fall through */ }
        return "C:/Users/john-/AppData/Roaming/motion_link";
    }

    var corePath = inbox() + "/motion_link_core.js";
    if (!api.filePathExists(corePath)) {
        console.log("Motion Link: the shared core is missing.");
        console.log("  Expected: " + corePath);
        console.log("  Run deploy.ps1 from the davinci-resolve-utilities repo.");
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
                    + "- it is an older copy. Re-run deploy.ps1.");
        return;
    }

    motionLinkBuildBatch();

})();
