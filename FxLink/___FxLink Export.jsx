// FxLink Export — batch-export selected comps to the project's Output folder.
//
// Select one or more comps in the project panel, then run this script
// (File > Scripts > FxLink Export.jsx). Each comp renders to
// <project folder>/Output/<comp name>.<ext>, overwriting the file that
// DaVinci Resolve links to. Applies the Output Module template named
// "FxLink" if it exists.
//
// One-time setup for correct export settings: render any comp once with
// your desired QuickTime codec, then in the Render Queue's Output Module
// dropdown choose "Make Template..." and name it exactly: FxLink
//
// IMAGE SEQUENCES: when Resolve linked an image sequence, the comp has a
// folder at Output/<comp name>/ instead of a single file. The render then goes
// to Output/<comp name>/<comp name>_[#####].<ext>, overwriting the same
// frames Resolve is linked to. Resolve's FxLink renumbers those frames from 0
// with 5-digit padding precisely so this default numbering lines up. Make a
// second template for it, named exactly: FxLink Sequence  (same image format
// as the sequence - PNG, EXR, etc). A movie template cannot write frames.

(function () {
    var TEMPLATE_NAME = "FxLink";
    var SEQUENCE_TEMPLATE_NAME = "FxLink Sequence";
    var proj = app.project;

    if (!proj || !proj.file) {
        alert("FxLink Export: Save the project first so it has a folder.");
        return;
    }

    var comps = [];
    var sel = proj.selection;
    for (var i = 0; i < sel.length; i++) {
        if (sel[i] instanceof CompItem) comps.push(sel[i]);
    }

    if (comps.length === 0) {
        alert("FxLink Export: No comps selected.\n\nSelect the comp(s) to export in the project panel, then run this script again.");
        return;
    }

    var outputDir = new Folder(proj.file.parent.fsName + "/Output");
    if (!outputDir.exists) outputDir.create();

    function findExistingExtension(compName) {
        // Reuse the extension of the file Resolve linked to, so we overwrite
        // exactly that file. Default to .mov for new comps.
        var files = outputDir.getFiles();
        for (var i = 0; i < files.length; i++) {
            var n = files[i].displayName;
            var dot = n.lastIndexOf(".");
            if (dot > 0 && n.substring(0, dot) === compName) {
                return n.substring(dot);
            }
        }
        return ".mov";
    }

    function sequenceFolderFor(compName) {
        // Resolve linked a sequence if Output/<comp name>/ is a folder.
        var dir = new Folder(outputDir.fsName + "/" + compName);
        return dir.exists ? dir : null;
    }

    // AE's own names for the image-sequence formats, keyed by file extension.
    var SEQUENCE_FORMATS = {
        ".png": "PNG Sequence",
        ".tif": "TIFF Sequence", ".tiff": "TIFF Sequence",
        ".exr": "OpenEXR Sequence",
        ".jpg": "JPEG Sequence", ".jpeg": "JPEG Sequence",
        ".dpx": "DPX/Cineon Sequence",
        ".tga": "Targa Sequence"
    };

    function currentFormat(om) {
        try {
            return String(om.getSettings(GetSettingsFormat.STRING)["Format"]);
        } catch (e) {
            return null;
        }
    }

    function ensureSequenceFormat(om, ext) {
        // Returns null if the output module will write frames in the right
        // format, otherwise the reason it won't.
        //
        // Without this, a missing template leaves AE's DEFAULT output module in
        // place - usually a movie - and AE quietly renders a .mov that Resolve
        // never sees. Everything is read back after setting it, so a format
        // name AE doesn't accept fails loudly instead of rendering the wrong
        // thing.
        var want = SEQUENCE_FORMATS[ext.toLowerCase()];
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

    function sequenceExtension(dir) {
        // Match the format of the frames already there, so we overwrite them.
        var files = dir.getFiles();
        for (var i = 0; i < files.length; i++) {
            if (files[i] instanceof File) {
                var n = files[i].displayName;
                var dot = n.lastIndexOf(".");
                if (dot > 0) return n.substring(dot);
            }
        }
        return ".png";
    }

    function removeQueuedItemsFor(comp) {
        var rq = proj.renderQueue;
        for (var i = rq.numItems; i >= 1; i--) {
            var item = rq.item(i);
            if (item.comp === comp && item.status !== RQItemStatus.RENDERING && item.status !== RQItemStatus.DONE) {
                item.remove();
            }
        }
    }

    app.beginUndoGroup("FxLink Export");

    var templateMissing = false;
    var sequenceTemplateMissing = [];
    var refused = [];
    var queued = [];

    for (var c = 0; c < comps.length; c++) {
        var comp = comps[c];
        removeQueuedItemsFor(comp);

        var rqItem = proj.renderQueue.items.add(comp);
        var om = rqItem.outputModule(1);
        var seqDir = sequenceFolderFor(comp.name);

        if (seqDir) {
            // The movie template can't produce frames, so it is NOT a fallback
            // here - a sequence without its own template is flagged instead.
            try {
                om.applyTemplate(SEQUENCE_TEMPLATE_NAME);
            } catch (e) {
                sequenceTemplateMissing.push(comp.name);
            }
            var ext = sequenceExtension(seqDir);
            var problem = ensureSequenceFormat(om, ext);
            if (problem) {
                // Rendering anyway would write a file Resolve ignores, so take
                // it back out of the queue rather than leave a trap in it.
                rqItem.remove();
                refused.push(comp.name + " - " + problem);
                continue;
            }
            // Set the file AFTER the format: changing format can rewrite it.
            om.file = new File(seqDir.fsName + "/" + comp.name + "_[#####]" + ext);
        } else {
            try {
                om.applyTemplate(TEMPLATE_NAME);
            } catch (e) {
                templateMissing = true;
            }
            var outPath = outputDir.fsName + "/" + comp.name + findExistingExtension(comp.name);
            om.file = new File(outPath);
        }
        queued.push(comp.name);
    }

    app.endUndoGroup();

    if (refused.length) {
        alert("FxLink Export: NOT queued - these are linked to an image sequence in Resolve, " +
              "but AE would not render one:\n\n  " + refused.join("\n  ") + "\n\n" +
              "Make an Output Module template in the matching image format and name it exactly '" +
              SEQUENCE_TEMPLATE_NAME + "', then run this again.");
    } else if (sequenceTemplateMissing.length) {
        alert("FxLink Export: no Output Module template named '" + SEQUENCE_TEMPLATE_NAME +
              "', so these use AE's default settings with the format set to match:\n\n  " +
              sequenceTemplateMissing.join("\n  ") +
              "\n\nCheck Channels before pressing Render - set it to RGB + Alpha if the graphic " +
              "needs transparency. Make the '" + SEQUENCE_TEMPLATE_NAME + "' template once and " +
              "this message goes away.");
    }

    if (templateMissing) {
        alert("FxLink Export: Output Module template '" + TEMPLATE_NAME + "' not found — using default settings.\n\n" +
              "To fix: set up an Output Module once with your codec, then in the Render Queue's " +
              "Output Module dropdown choose 'Make Template...' and name it exactly '" + TEMPLATE_NAME + "'.");
    }

    // Rendering from a script blocks AE's UI (frozen progress bar), so just
    // queue everything and let the user press Render for live progress.
    // No success popup — the opened Render Queue panel is the feedback.
    proj.renderQueue.showWindow(true);
})();
