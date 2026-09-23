#!/bin/sh
# PortableAI installer — the only "curl | sh" install path.
#
# Read this file first (it is short on purpose), then:
#   curl -fsSL https://raw.githubusercontent.com/generalistcodes/portableai/HEAD/install.sh | sh
# or, from a clone:  sh install.sh
#
# Downloads into the current working directory. Does not start PortableAI:
# the app is a local network server, and an unattended pipe-to-sh launch
# would be a surprise, not a convenience.

set -eu

# Stable, version-free names from GitHub Releases (see .github/workflows/release.yml).
DOWNLOAD_BASE="https://github.com/generalistcodes/portableai/releases/latest/download"

die() {
  printf '%s\n' "$@" >&2
  exit 1
}

unsupported() {
  os="$1"
  arch="$2"
  die \
    "PortableAI has no packaged build for ${os}/${arch} yet." \
    "" \
    "Supported today:" \
    "  - Linux x86_64  →  AppImage" \
    "  - macOS         →  Apple Silicon .dmg (unsigned)" \
    "" \
    "Windows is not installed by this script (Git Bash / MSYS / Cygwin included)." \
    "Download PortableAI-windows-x86_64.exe from GitHub Releases instead —" \
    "that build is still incomplete (no bundled Ollama)." \
    "Linux arm64 is not packaged yet."
}

os=$(uname -s)
arch=$(uname -m)

case "$os" in
  *MINGW*|*MSYS*|*CYGWIN*)
    unsupported "$os" "$arch"
    ;;
  Linux)
    case "$arch" in
      x86_64|amd64)
        url="${DOWNLOAD_BASE}/PortableAI-linux-x86_64.AppImage"
        dest="./PortableAI.AppImage"
        ;;
      *)
        unsupported "$os" "$arch"
        ;;
    esac
    ;;
  Darwin)
    # One macOS artifact today: the Apple Silicon .dmg, for any Darwin arch.
    url="${DOWNLOAD_BASE}/PortableAI-macos-arm64.dmg"
    dest="./PortableAI.dmg"
    ;;
  *)
    unsupported "$os" "$arch"
    ;;
esac

echo "Downloading $url"
echo "         -> $dest  (current directory)"
command -v curl >/dev/null 2>&1 || die "curl is required."
curl -fL --progress-bar -o "$dest" "$url" || die "Download failed. Check $url"

# AppImage needs +x. The .dmg is just a file to open in Finder.
case "$dest" in
  *.AppImage) chmod +x "$dest" ;;
esac

echo
echo "✓ Downloaded $dest"
case "$dest" in
  *.AppImage)
    echo "Run it with: $dest"
    echo "If FUSE is unavailable: $dest --appimage-extract-and-run"
    ;;
  *.dmg)
    echo "Open the .dmg, then right-click the app and choose \"Open\""
    echo "the first time — unsigned build for now."
    ;;
esac
echo "(This script does not start PortableAI for you.)"
