"""Package / unpackage the preferences of the Resolve utilities, to move them to
another machine - including from Windows to a Mac.

    python migrate_prefs.py pack                     # -> ResolveUtilities_prefs_<pc>_<date>.zip
    python migrate_prefs.py show  BUNDLE.zip         # what's inside, and every path in it
    python migrate_prefs.py unpack BUNDLE.zip --map "D:\\Work=/Volumes/Work" --dry-run
    python migrate_prefs.py unpack BUNDLE.zip --map "D:\\Work=/Volumes/Work"

Run it with any Python 3 - it is not a Resolve script and needs nothing but the
standard library.

WHAT TRAVELS
  Every tool's prefs file, the Fusion Clipboard library (your saved presets) and
  its Learn Node captures. Not moved, on purpose: queues and Motion Link job
  files (temporary), and Spline Keyframe Sync baselines and backups (tied to
  specific comps on the old machine).

WHAT CHANGES ON THE WAY IN
  - Paths are rewritten with your --map rules, longest match first, e.g.
    D:\\Work\\Client\\x -> /Volumes/Work/Client/x. Going to another OS, a
    Windows path that no rule covers can't be valid there, so it is dropped
    (each tool falls back to its default) and listed in the report.
  - Window positions and UI scale are dropped when the OS changes - they belong
    to the old screen. --keep-layout keeps them.
  - Fusion Clipboard presets are copied byte for byte. Paths inside them (e.g. a
    Loader's file) are listed, not rewritten - edit those in Fusion.
  - Nothing is silently overwritten: a file that's already there and differs is
    kept as <name>.before-import-<time> first.

WHERE THINGS GO
  Windows: %APPDATA%, where the tools keep them today.
  Mac:     ~/Library/Application Support/ResolveUtilities - the folder the Mac
           versions of the tools will read. (They still need porting to it; see
           the migration report.)
"""
import argparse
import datetime
import io
import json
import os
import platform
import re
import shutil
import sys
import zipfile

BUNDLE_VERSION = 1

# Every prefs file a tool in this repo writes. Kept as an explicit list so a
# bundle never sweeps up other applications' files from %APPDATA%.
PREFS_FILES = [
    "align_clips_prefs.json",
    "batch_export_prefs.json",
    "clean_precomps_prefs.json",
    "copy_node_prefs.json",
    "fusion_clipboard_prefs.json",
    "fusion_clipboard_rules.json",
    "fusion_multi_editor_prefs.json",
    "fxlink_prefs.json",
    "fxlink_queue_prefs.json",
    "marker_progress_prefs.json",
    "motion_link_prefs.json",
    "motion_link_queue_prefs.json",
    "multicam_color_prefs.json",
    "nest_clips_prefs.json",
    "preview_cache_prefs.json",
    "spline_keyframe_sync_prefs.json",
    "timeline_hierarchy_prefs.json",
]
FOLDERS = ["FusionClipboard"]
NOT_MOVED = {
    "fxlink_queue.json": "queue - temporary",
    "motion_link_queue.json": "queue - temporary",
    "motion_link": "Motion Link job files - temporary",
    "spline_keyframe_sync_baselines.json": "tied to comps on this machine",
    "spline_keyframe_sync_backups": "tied to comps on this machine",
}
LAYOUT_KEYS = ("geometry", "geometry_scale", "ui_scale")

WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)")
# Not preceded by a letter, so the "s:" in "https://" isn't taken for a drive.
WINDOWS_PATH_IN_TEXT = re.compile(r"(?<![A-Za-z])[A-Za-z]:(?:\\\\|\\|/)[^\"'\r\n]*")


def this_os():
    return {"Windows": "windows", "Darwin": "mac"}.get(platform.system(), "linux")


def data_dir(os_name):
    if os_name == "windows":
        return os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    if os_name == "mac":
        return os.path.expanduser("~/Library/Application Support/ResolveUtilities")
    return os.path.expanduser("~/.config/ResolveUtilities")


# ---------------------------------------------------------------- pack

def pack(args):
    source = data_dir(this_os())
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    out = args.out or f"ResolveUtilities_prefs_{platform.node() or 'pc'}_{stamp}.zip"
    packed = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in PREFS_FILES:
            path = os.path.join(source, name)
            if os.path.isfile(path):
                z.write(path, "prefs/" + name)
                packed.append(name)
        for folder in FOLDERS:
            root = os.path.join(source, folder)
            count = 0
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, source).replace("\\", "/")
                    z.write(full, rel)
                    count += 1
            if count:
                packed.append(f"{folder}/ ({count} files)")
        z.writestr("manifest.json", json.dumps({
            "bundle_version": BUNDLE_VERSION,
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "source_os": this_os(),
            "source_host": platform.node(),
            "source_dir": source,
            "items": packed,
            "not_moved": NOT_MOVED,
        }, indent=2))
    print(f"Packed {len(packed)} item(s) from {source}")
    for item in packed:
        print(f"  {item}")
    print(f"\nNot moved on purpose: {', '.join(NOT_MOVED)}")
    print(f"\nBundle: {os.path.abspath(out)}")
    print("Next: on the new machine, `show` it to see its paths, then `unpack` with --map.")


# ---------------------------------------------------------------- path remapping

def parse_maps(raw):
    """["D:\\Work=/Volumes/Work", ...] -> [(old prefix, new prefix)], longest first."""
    maps = []
    for item in raw or []:
        if "=" not in item:
            sys.exit(f"--map needs OLD=NEW, got: {item}")
        old, new = item.split("=", 1)
        maps.append((old.strip().rstrip("\\/"), new.strip().rstrip("\\/")))
    return sorted(maps, key=lambda m: len(m[0]), reverse=True)


def remap(value, maps, target_os):
    """The path rewritten for the target, or None if it can't be valid there."""
    norm = value.replace("/", "\\")
    for old, new in maps:
        old_norm = old.replace("/", "\\")
        if norm.lower() == old_norm.lower() or norm.lower().startswith(old_norm.lower() + "\\"):
            rest = norm[len(old_norm):].lstrip("\\")
            sep = "\\" if target_os == "windows" else "/"
            joined = new + (sep + rest.replace("\\", sep) if rest else "")
            return joined
    return value if target_os == "windows" else None


class Report:
    def __init__(self):
        self.remapped, self.dropped, self.layout, self.backups, self.written = [], [], [], [], []
        self.preset_paths = {}


def convert(node, maps, target_os, where, report):
    """Walk a JSON value, rewriting Windows paths. Returns (new value, keep?)."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            new, keep = convert(value, maps, target_os, f"{where}/{key}", report)
            if keep:
                out[key] = new
        return out, True
    if isinstance(node, list):
        out = []
        for i, value in enumerate(node):
            new, keep = convert(value, maps, target_os, f"{where}[{i}]", report)
            if keep:
                out.append(new)
        return out, True
    if isinstance(node, str) and WINDOWS_PATH.match(node):
        new = remap(node, maps, target_os)
        if new is None:
            report.dropped.append(f"{where}: {node}")
            return None, False
        if new != node:
            report.remapped.append(f"{where}: {node}  ->  {new}")
        return new, True
    return node, True


# ---------------------------------------------------------------- unpack

def write_file(path, data, report, dry_run):
    """Write bytes, keeping a differing existing file as .before-import-<time>."""
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return
        backup = f"{path}.before-import-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        report.backups.append(backup)
        if not dry_run:
            shutil.copy2(path, backup)
    report.written.append(path)
    if not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def read_bundle(path):
    z = zipfile.ZipFile(path)
    try:
        manifest = json.loads(z.read("manifest.json"))
    except KeyError:
        sys.exit(f"{path} isn't a prefs bundle (no manifest.json).")
    if manifest.get("bundle_version") != BUNDLE_VERSION:
        sys.exit(f"Bundle version {manifest.get('bundle_version')} - this script reads "
                 f"version {BUNDLE_VERSION}. Use the migrate_prefs.py that made it.")
    return z, manifest


def unpack(args):
    z, manifest = read_bundle(args.bundle)
    target_os = args.target_os or this_os()
    target = args.to or data_dir(target_os)
    maps = parse_maps(args.map)
    cross_os = manifest["source_os"] != target_os
    drop_layout = cross_os and not args.keep_layout
    report = Report()

    for name in z.namelist():
        if name == "manifest.json" or name.endswith("/"):
            continue
        data = z.read(name)
        if name.startswith("prefs/"):
            fname = name[len("prefs/"):]
            try:
                prefs = json.loads(data.decode("utf-8"))
            except ValueError:
                print(f"  ! {fname} isn't valid JSON - copied as it is")
                write_file(os.path.join(target, fname), data, report, args.dry_run)
                continue
            if drop_layout and isinstance(prefs, dict):
                for key in LAYOUT_KEYS:
                    if prefs.pop(key, None) is not None:
                        report.layout.append(f"{fname}/{key}")
            prefs, _ = convert(prefs, maps, target_os, fname, report)
            out = json.dumps(prefs, indent=2).encode("utf-8")
            write_file(os.path.join(target, fname), out, report, args.dry_run)
        else:
            if name.lower().endswith(".setting"):
                found = sorted(set(WINDOWS_PATH_IN_TEXT.findall(data.decode("utf-8", "replace"))))
                if found and cross_os:
                    report.preset_paths[name] = found
            write_file(os.path.join(target, *name.split("/")), data, report, args.dry_run)

    print(("DRY RUN - nothing written.\n" if args.dry_run else "")
          + f"Bundle from {manifest['source_host']} ({manifest['source_os']}, "
          f"{manifest['created']}) -> {target}  ({target_os})\n")
    print(f"Files {'to write' if args.dry_run else 'written'}: {len(report.written)}"
          f"  (unchanged ones skipped)")
    section("Paths rewritten", report.remapped)
    section("Paths dropped - no --map rule covers them, so they can't be valid here; "
            "the tool falls back to its default", report.dropped)
    if report.layout:
        print(f"\nWindow positions / UI scale dropped from {len(report.layout)} place(s) "
              f"(new screen - use --keep-layout to keep them).")
    section("Existing files kept as backups", report.backups)
    if report.preset_paths:
        print(f"\nFusion Clipboard presets with Windows paths inside ({len(report.preset_paths)}) - "
              f"copied unchanged; fix them in Fusion if they matter:")
        for name, found in report.preset_paths.items():
            print(f"  {name}")
            for p in found[:3]:
                print(f"      {p}")
            if len(found) > 3:
                print(f"      ... and {len(found) - 3} more")


def section(title, lines):
    if lines:
        print(f"\n{title} ({len(lines)}):")
        for line in lines:
            print(f"  {line}")


def show(args):
    z, manifest = read_bundle(args.bundle)
    print(f"Bundle from {manifest['source_host']} ({manifest['source_os']}), "
          f"{manifest['created']}, source {manifest['source_dir']}")
    for item in manifest["items"]:
        print(f"  {item}")
    prefixes = {}
    report = Report()
    for name in z.namelist():
        if name.startswith("prefs/"):
            try:
                convert(json.loads(z.read(name).decode("utf-8")), [], "mac", name[6:], report)
            except ValueError:
                pass
    for line in report.dropped:
        path = line.split(": ", 1)[1]
        parts = re.split(r"[\\/]", path)
        key = "\\".join(parts[:2])                 # drive + first folder, e.g. D:\Work
        prefixes.setdefault(key, []).append(line)
    print(f"\nPaths in the prefs ({len(report.dropped)}), grouped by where they start - "
          f"each group needs a --map rule to survive a move to another OS:")
    for key, lines in sorted(prefixes.items()):
        print(f"\n  {key}   ({len(lines)})")
        for line in lines[:4]:
            print(f"    {line}")
        if len(lines) > 4:
            print(f"    ... and {len(lines) - 4} more")


def main():
    parser = argparse.ArgumentParser(description="Package / unpackage the Resolve utilities' preferences.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="bundle this machine's prefs into a .zip")
    p.add_argument("--out", help="bundle file name")
    s = sub.add_parser("show", help="list a bundle's contents and the paths in it")
    s.add_argument("bundle")
    u = sub.add_parser("unpack", help="install a bundle on this machine")
    u.add_argument("bundle")
    u.add_argument("--map", action="append", metavar="OLD=NEW",
                   help='path rule, repeatable, e.g. --map "D:\\Work=/Volumes/Work"')
    u.add_argument("--to", help="install somewhere else (default: this OS's prefs folder)")
    u.add_argument("--target-os", choices=["windows", "mac", "linux"],
                   help="convert for this OS (default: the one you're on) - "
                        "lets you preview a Mac unpack from Windows")
    u.add_argument("--keep-layout", action="store_true",
                   help="keep window positions / UI scale across OSes")
    u.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()
    {"pack": pack, "show": show, "unpack": unpack}[args.cmd](args)


if __name__ == "__main__":
    main()
