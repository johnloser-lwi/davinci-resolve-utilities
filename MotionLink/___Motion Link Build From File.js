// Motion Link - build a Composition from an export you choose.
//
// Same as "Motion Link Build Comp" except it asks which export to use, with the
// dialog already sitting in the folder the newest export went to. Use it when
// an earlier export didn't come in properly and you want to redo just that one.
//
// Every export leaves a <name>_MotionLink_NNN.json next to its .wav - that is
// what you pick. The job names found in the folder are also listed in the
// Console, so you can see what is there without scrolling the dialog.

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

    if (typeof motionLinkBuild !== "function" || typeof motionLinkPickJob !== "function") {
        console.log("Motion Link: the core loaded but its functions are missing "
                    + "- the file is probably truncated. Re-run deploy.ps1.");
        return;
    }

    var chosen = motionLinkPickJob();
    if (!chosen) { return; }        // cancelled, already reported

    motionLinkBuild(chosen);

})();
