#!/usr/bin/env bash
# Wrap dist/portableai in an unsigned .app and a .dmg (dmgbuild).
# This is NOT notarized. Gatekeeper will warn until the user allows it.
# Signing would need an Apple Developer account ($99/year) and GitHub
# secrets for the certificate — this script does not assume those exist.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${VERSION:-dev}"
ARCH="$(uname -m)"
BINARY="${BINARY:-"$ROOT/dist/portableai"}"
OUT_DIR="${OUT_DIR:-"$ROOT/dist"}"
APP="${OUT_DIR}/PortableAI.app"
OUT="${OUT_DIR}/PortableAI-${VERSION}-macos-${ARCH}.dmg"

if [[ ! -f "$BINARY" ]]; then
  echo "missing PyInstaller binary: $BINARY" >&2
  exit 1
fi

rm -rf "$APP" "$OUT"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BINARY" "$APP/Contents/MacOS/portableai"
chmod +x "$APP/Contents/MacOS/portableai"
ICNS="$ROOT/branding/generated/macos/portableai.icns"
if [[ ! -f "$ICNS" ]]; then
  python3 "$ROOT/branding/generate_icons.py"
fi
if [[ ! -f "$ICNS" ]]; then
  echo "missing macOS icon: $ICNS (run python branding/generate_icons.py)" >&2
  exit 1
fi
cp "$ICNS" "$APP/Contents/Resources/portableai.icns"

cat > "$APP/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>PortableAI</string>
  <key>CFBundleDisplayName</key>
  <string>PortableAI</string>
  <key>CFBundleIdentifier</key>
  <string>app.portableai.server</string>
  <key>CFBundleVersion</key>
  <string>${VERSION}</string>
  <key>CFBundleShortVersionString</key>
  <string>${VERSION}</string>
  <key>CFBundleExecutable</key>
  <string>portableai</string>
  <key>CFBundleIconFile</key>
  <string>portableai</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

export PORTABLEAI_APP="$APP"
dmgbuild -s "$ROOT/packaging/macos/dmg_settings.py" "PortableAI ${VERSION}" "$OUT"
echo "wrote unsigned, unnotarized $OUT"
