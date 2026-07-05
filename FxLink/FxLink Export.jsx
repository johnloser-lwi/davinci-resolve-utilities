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

(function () {
    var TEMPLATE_NAME = "FxLink";
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
    var queued = [];

    for (var c = 0; c < comps.length; c++) {
        var comp = comps[c];
        removeQueuedItemsFor(comp);

        var rqItem = proj.renderQueue.items.add(comp);
        var om = rqItem.outputModule(1);

        try {
            om.applyTemplate(TEMPLATE_NAME);
        } catch (e) {
            templateMissing = true;
        }

        var outPath = outputDir.fsName + "/" + comp.name + findExistingExtension(comp.name);
        om.file = new File(outPath);
        queued.push(comp.name);
    }

    app.endUndoGroup();

    if (templateMissing) {
        alert("FxLink Export: Output Module template '" + TEMPLATE_NAME + "' not found — using default settings.\n\n" +
              "To fix: set up an Output Module once with your codec, then in the Render Queue's " +
              "Output Module dropdown choose 'Make Template...' and name it exactly '" + TEMPLATE_NAME + "'.");
    }

    proj.renderQueue.render();

    alert("FxLink Export: Rendered " + queued.length + " comp(s) to:\n" + outputDir.fsName + "\n\n" +
          queued.join("\n") + "\n\n" +
          "If Resolve doesn't show the update, run fx_link_refresh there.");
})();
