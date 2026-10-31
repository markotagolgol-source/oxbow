#!/bin/bash
# Add Oxbow to this user's application menu. No administrator rights.
# Does not change which program opens PDF files by default.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/applications"
ICON_ROOT="$DATA/icons/hicolor"
LOGO="$ROOT/assets/logo.png"
if [ ! -f "$LOGO" ]; then
    echo "Missing logo: $LOGO" >&2
    exit 1
fi
if [ ! -x "$ROOT/run.sh" ]; then
    echo "Missing start script: $ROOT/run.sh" >&2
    exit 1
fi
mkdir -p "$APP_DIR"
python3 - "$LOGO" "$ICON_ROOT" <<'PY'
import sys
from pathlib import Path
from PIL import Image

source = Path(sys.argv[1])
root = Path(sys.argv[2])
image = Image.open(source).convert("RGBA")
for size in (48, 64, 128, 256, 512):
    dest = root / f"{size}x{size}" / "apps"
    dest.mkdir(parents=True, exist_ok=True)
    image.resize((size, size), Image.Resampling.LANCZOS).save(dest / "oxbow.png")
PY
ICON="$ICON_ROOT/256x256/apps/oxbow.png"
DESKTOP="$APP_DIR/oxbow.desktop"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Oxbow
GenericName=PDF form reader
Comment=Read PDFs and fill dynamic XFA forms
Exec=$ROOT/run.sh %f
Icon=$ICON
Terminal=false
StartupNotify=true
Categories=Office;Viewer;
MimeType=application/pdf;
StartupWMClass=Oxbow
EOF
chmod 644 "$DESKTOP"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR"
fi
echo "Installed $DESKTOP"
echo "Oxbow is in Open With for PDF files. The default PDF viewer was not changed."
