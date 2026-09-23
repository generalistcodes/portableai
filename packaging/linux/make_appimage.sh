#!/usr/bin/env bash
# Wrap dist/portableai in an AppImage via appimagetool.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${VERSION:-dev}"
ARCH="$(uname -m)"
BINARY="${BINARY:-"$ROOT/dist/portableai"}"
OUT_DIR="${OUT_DIR:-"$ROOT/dist"}"
APPDIR="${OUT_DIR}/PortableAI.AppDir"
OUT="${OUT_DIR}/PortableAI-${VERSION}-linux-${ARCH}.AppImage"

if [[ ! -f "$BINARY" ]]; then
  echo "missing PyInstaller binary: $BINARY" >&2
  exit 1
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp "$BINARY" "$APPDIR/usr/bin/portableai"
chmod +x "$APPDIR/usr/bin/portableai"
cp "$ROOT/packaging/linux/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp "$ROOT/packaging/linux/portableai.desktop" "$APPDIR/portableai.desktop"
python3 "$ROOT/branding/generate_icons.py" --appdir "$APPDIR"

TOOL="${OUT_DIR}/appimagetool-${ARCH}.AppImage"
if [[ ! -f "$TOOL" ]]; then
  url="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  echo "downloading $url"
  curl -fsSL -o "$TOOL" "$url"
  chmod +x "$TOOL"
fi

# GitHub-hosted runners usually have no FUSE; extract-and-run avoids it.
export APPIMAGE_EXTRACT_AND_RUN=1
"$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "wrote $OUT"
