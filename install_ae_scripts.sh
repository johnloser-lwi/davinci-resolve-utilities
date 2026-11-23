#!/bin/bash
# Install the After Effects scripts (.jsx) into AE's own Scripts folder - the
# Mac counterpart of install_ae_scripts.ps1.
#
# That folder belongs to the system (/Applications/Adobe After Effects 20xx/
# Scripts), so writing to it needs your password once. deploy.sh tells you when
# this is needed - it compares the installed copy with the repo and only shouts
# when they actually differ.
set -u
REPO="$(cd "$(dirname "$0")" && pwd)"

AE_DIR=$(ls -d "/Applications/Adobe After Effects "[0-9]* 2>/dev/null | sort | tail -1)
if [ -z "$AE_DIR" ]; then
    echo "No After Effects installation found under /Applications."
    exit 1
fi
AE_SCRIPTS="$AE_DIR/Scripts"

echo "Installing into: $AE_SCRIPTS"
echo "(macOS will ask for your password - the folder belongs to the system.)"
sudo mkdir -p "$AE_SCRIPTS" || exit 1

installed=0
while IFS= read -r -d '' file; do
    sudo cp "$file" "$AE_SCRIPTS/" && {
        echo "Installed: $(basename "$file")"
        installed=$((installed + 1))
    }
done < <(find "$REPO" -name "*.jsx" -type f -not -path "$REPO/.*" -print0)

echo ""
echo "$installed script(s) installed to:"
echo "  $AE_SCRIPTS"
echo ""
echo "After Effects picks them up next time you open File > Scripts."
echo "FxLink also needs: After Effects > Settings > Scripting & Expressions >"
echo "  'Allow Scripts to Write Files and Access Network'."
