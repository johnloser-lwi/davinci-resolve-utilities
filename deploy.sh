#!/bin/bash
SOURCE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/John"

if [ ! -d "$DEST" ]; then
    mkdir -p "$DEST"
    echo "Created destination folder: $DEST"
fi

copied=0
while IFS= read -r -d '' file; do
    relative="${file#$SOURCE/}"
    target="$DEST/$relative"
    mkdir -p "$(dirname "$target")"
    cp "$file" "$target"
    echo "Deployed: $relative"
    ((copied++))
done < <(find "$SOURCE" -name "*.py" -print0)

if [ "$copied" -eq 0 ]; then
    echo "No .py scripts found in $SOURCE"
    exit 1
fi

echo ""
echo "Done. $copied script(s) deployed to:"
echo "  $DEST"
