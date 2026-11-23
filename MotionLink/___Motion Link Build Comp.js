// Motion Link - build a Composition from the NEWEST Resolve export.
//
// One click, no questions: reads the job that "Motion Link Export" left behind
// in Resolve and builds a matching Composition with the audio in it.
//
// To pick an older export instead, use "Motion Link Build From File".
//
// The build itself lives in motion_link_core.js, in the motion_link data folder (see inbox() below) -
// outside Cavalry's Scripts folder so it doesn't appear as its own menu entry.
// Both menu scripts share it, so a fix only ever has to be made once.

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

    if (typeof motionLinkBuild !== "function") {
        console.log("Motion Link: the core loaded but motionLinkBuild is missing "
                    + "- the file is probably truncated. Re-run the deploy script.");
        return;
    }

    motionLinkBuild(null);          // null = whatever the newest export was

})();
