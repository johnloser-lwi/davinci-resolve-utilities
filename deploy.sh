#!/bin/bash
# Deploy the utilities on a Mac - the counterpart of deploy.ps1.
#
#   ./deploy.sh
#
# What goes where:
#   tool scripts (*/NN_*.py)  -> Resolve's Scripts/Utility/John   (the Workspace > Scripts menu)
#   _shared/*.py              -> ~/Library/Application Support/ResolveUtilities/lib
#   MotionLink/core/*.js      -> ~/Library/Application Support/ResolveUtilities/motion_link
#   other *.js                -> Cavalry's Scripts/John            (if Cavalry is installed)
#   *.jsx                     -> After Effects' Scripts folder     (see install_ae_scripts.sh)
#
# Not deployed on a Mac: Fusion/01_fusion_clipboard.py - Windows-only and no
# longer maintained.
set -u

SOURCE="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support"
DEST="$SUPPORT/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/John"
DATA="$SUPPORT/ResolveUtilities"
SKIP_ON_MAC=("Fusion/01_fusion_clipboard.py")

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

skipped() {
    local rel="$1" s
    for s in "${SKIP_ON_MAC[@]}"; do [ "$rel" = "$s" ] && return 0; done
    return 1
}

# --- Back up the keyboard mapping before touching any scripts -------------
# Resolve stores script shortcuts by FILE PATH, so renaming or removing a script
# orphans its shortcut. Keep a rolling backup so a mapping can be restored.
KB="$HOME/Library/Preferences/Blackmagic Design/DaVinci Resolve/keyboard.preset.xml"
if [ -f "$KB" ]; then
    mkdir -p "$SOURCE/.keyboard-backups"
    stamp="$(date +%Y%m%d_%H%M%S)"
    cp "$KB" "$SOURCE/.keyboard-backups/keyboard.preset.$stamp.xml"
    # keep the 10 most recent
    ls -1t "$SOURCE/.keyboard-backups"/keyboard.preset.*.xml 2>/dev/null | tail -n +11 |
        while IFS= read -r old; do rm -f "$old"; done
    echo "Backed up keyboard mapping -> .keyboard-backups/keyboard.preset.$stamp.xml"
fi

# --- Resolve scripts --------------------------------------------------------
if [ -d "$DEST" ]; then
    # Mirror the repo: remove previously deployed .py files so renamed/removed
    # scripts don't linger as duplicate menu entries.
    removed=$(find "$DEST" -name "*.py" -type f | wc -l | tr -d ' ')
    find "$DEST" -name "*.py" -type f -delete
    [ "$removed" -gt 0 ] && echo "Cleared $removed previously deployed .py file(s)."
else
    mkdir -p "$DEST"
    echo "Created destination folder: $DEST"
fi

# Only scripts inside a tool folder are menu entries: root-level .py
# (migrate_prefs.py) are command-line tools, and _shared is a library.
copied=0
while IFS= read -r -d '' file; do
    rel="${file#"$SOURCE"/}"
    case "$rel" in
        */*) ;;                      # inside a tool folder
        *) continue ;;
    esac
    if skipped "$rel"; then
        echo "Skipped (Windows only): $rel"
        continue
    fi
    mkdir -p "$(dirname "$DEST/$rel")"
    cp "$file" "$DEST/$rel"
    echo "Deployed: $rel"
    copied=$((copied + 1))
done < <(find "$SOURCE" -name "*.py" -type f \
            -not -path "$SOURCE/.*" -not -path "$SOURCE/_shared/*" \
            -not -path "$SOURCE/build/*" -not -path "$SOURCE/dist/*" \
            -not -path "*/__pycache__/*" -print0 | sort -z)

if [ "$copied" -eq 0 ]; then
    red "No .py scripts found in $SOURCE"
    exit 1
fi
echo ""
echo "Done. $copied script(s) deployed to:"
echo "  $DEST"

# Data files that sit beside a script (never dot-folders or build output).
while IFS= read -r -d '' file; do
    rel="${file#"$SOURCE"/}"
    mkdir -p "$(dirname "$DEST/$rel")"
    cp "$file" "$DEST/$rel"
    echo "Deployed data: $rel"
done < <(find "$SOURCE" -mindepth 2 -name "*.json" -type f \
            -not -path "$SOURCE/.*" -not -path "$SOURCE/build/*" -not -path "$SOURCE/dist/*" -print0)

# --- Shared library ---------------------------------------------------------
mkdir -p "$DATA/lib"
for file in "$SOURCE"/_shared/*.py; do
    [ -f "$file" ] || continue
    cp "$file" "$DATA/lib/"
    echo "Deployed shared library: $(basename "$file") -> $DATA/lib"
done
# Forget the cached Python choice, in case Pythons were installed or removed.
rm -f "$DATA/python_for_tk.json"

# The Tk tools need a Python with Tk on a Mac (Resolve's own has none).
tk_python=$(cd /tmp && python3 "$DATA/lib/resolve_utils.py" which-python 2>/dev/null)
if [ -z "$tk_python" ] || [ "$tk_python" = "none found" ]; then
    echo ""
    red "!! No Python with Tk found - the tools with a window can't open yet."
    red "   Install one:  brew install python-tk@3.15   (or python.org/downloads)"
else
    echo "Python for the windowed tools: $tk_python"
fi

# --- After Effects (.jsx) ---------------------------------------------------
AE_DIR=$(ls -d "/Applications/Adobe After Effects "[0-9]* 2>/dev/null | sort | tail -1)
if [ -z "$AE_DIR" ]; then
    echo ""
    echo "No After Effects installation found - skipped .jsx deployment."
else
    AE_SCRIPTS="$AE_DIR/Scripts"
    stale=()
    jsx_copied=0
    while IFS= read -r -d '' file; do
        name="$(basename "$file")"
        target="$AE_SCRIPTS/$name"
        # Only act when the installed copy actually differs, so a genuinely
        # outdated script stands out instead of being routine noise.
        if [ -f "$target" ] && cmp -s "$file" "$target"; then
            echo "AE script up to date: $name"
            continue
        fi
        if cp "$file" "$target" 2>/dev/null; then
            echo "Deployed to AE: $name"
            jsx_copied=$((jsx_copied + 1))
        elif [ -f "$target" ]; then
            stale+=("$name  (outdated)")
        else
            stale+=("$name  (not installed yet)")
        fi
    done < <(find "$SOURCE" -name "*.jsx" -type f -not -path "$SOURCE/.*" -print0)
    [ "$jsx_copied" -gt 0 ] && echo "$jsx_copied .jsx script(s) deployed to: $AE_SCRIPTS"
    if [ "${#stale[@]}" -gt 0 ]; then
        echo ""
        red "!! After Effects needs these scripts updated:"
        for name in "${stale[@]}"; do red "     $name"; done
        red "   Its Scripts folder belongs to the system. Run this once (asks for your password):"
        red "     ./install_ae_scripts.sh"
    fi
fi

# --- Cavalry (.js) ----------------------------------------------------------
# Anything under a "core" folder is shared library code, not a menu entry: it
# goes to the Motion Link data folder, because every .js in Cavalry's Scripts
# folder shows up under Window > Scripts.
mkdir -p "$DATA/motion_link"
while IFS= read -r -d '' file; do
    cp "$file" "$DATA/motion_link/"
    echo "Deployed shared core: $(basename "$file") -> $DATA/motion_link"
done < <(find "$SOURCE" -path "*/core/*.js" -type f -not -path "$SOURCE/.*" -print0)

CAV_ROOT=""
for candidate in "$SUPPORT/Cavalry" "$HOME/Library/Preferences/Cavalry" "$HOME/Documents/Cavalry"; do
    if [ -d "$candidate" ]; then CAV_ROOT="$candidate"; break; fi
done
if [ -z "$CAV_ROOT" ]; then
    echo ""
    echo "No Cavalry data folder found - skipped .js deployment."
    echo "(Cavalry's Help > Show Scripts Folder shows where it is, if it's installed.)"
else
    CAV_SCRIPTS="$CAV_ROOT/Scripts/John"
    mkdir -p "$CAV_SCRIPTS"
    find "$CAV_SCRIPTS" -name "*.js" -type f -delete        # mirror the repo
    js_copied=0
    while IFS= read -r -d '' file; do
        cp "$file" "$CAV_SCRIPTS/"
        echo "Deployed to Cavalry: $(basename "$file")"
        js_copied=$((js_copied + 1))
    done < <(find "$SOURCE" -name "*.js" -type f -not -path "*/core/*" -not -path "$SOURCE/.*" -print0)
    echo "$js_copied .js script(s) deployed to: $CAV_SCRIPTS"
fi

echo ""
green "All done."
