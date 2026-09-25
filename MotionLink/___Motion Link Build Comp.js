// Motion Link - build a Composition from the NEWEST Resolve export.
//
// One click, no questions: reads the job that "Motion Link Export" left behind
// in Resolve and builds a matching Composition with the audio in it.
//
// To pick an older export instead, use "Motion Link Build From File".
//
// The build itself lives in motion_link_core.js, in %APPDATA%\motion_link -
// outside Cavalry's Scripts folder so it doesn't appear as its own menu entry.
// Both menu scripts share it, so a fix only ever has to be made once.

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

    if (typeof motionLinkBuild !== "function") {
        console.log("Motion Link: the core loaded but motionLinkBuild is missing "
                    + "- the file is probably truncated. Re-run deploy.ps1.");
        return;
    }

    motionLinkBuild(null);          // null = whatever the newest export was

})();
