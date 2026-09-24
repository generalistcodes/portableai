#!/bin/sh
# PortableAI installer — the only "curl | sh" install path.
#
# Read this file first (it is short on purpose), then:
#   curl -fsSL https://raw.githubusercontent.com/generalistcodes/portableai/HEAD/install.sh | sh
#   curl -fsSL …/install.sh | sh -s -- --prefetch
# or, from a clone:  sh install.sh [--prefetch]
#
# Downloads into the current working directory. Without --prefetch, does not start PortableAI:
# the app is a local network server, and an unattended pipe-to-sh launch would be a surprise,
# not a convenience. --prefetch (Linux AppImage only) then runs the app once with
# PORTABLEAI_PREFETCH_ONLY=1 so the engine + starter model download now.

set -eu

# Stable, version-free names from GitHub Releases (see .github/workflows/release.yml).
DOWNLOAD_BASE="https://github.com/generalistcodes/portableai/releases/latest/download"

die() {
  printf '%s\n' "$@" >&2
  exit 1
}

prefetch=0
for arg in "$@"; do
  case "$arg" in
    --prefetch)
      prefetch=1
      ;;
    -h|--help)
      echo "Usage: sh install.sh [--prefetch]"
      echo "  --prefetch  also download the AI engine and a starter model (Linux AppImage)"
      exit 0
      ;;
    *)
      die "Unknown option: $arg"
      ;;
  esac
done

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
if [ "$prefetch" -eq 1 ]; then
  case "$dest" in
    *.AppImage)
      echo "Prefetching the AI engine and a starter model..."
      if ! PORTABLEAI_PREFETCH_ONLY=1 "$dest" --prefetch-only; then
        PORTABLEAI_PREFETCH_ONLY=1 "$dest" --appimage-extract-and-run --prefetch-only \
          || die "Prefetch failed."
      fi
      echo
      echo "✓ Everything is ready. Run $dest to start chatting"
      echo "immediately -- no more downloads needed."
      ;;
    *)
      die "--prefetch is only supported for the Linux AppImage."
      ;;
  esac
else
  echo "✓ Downloaded $dest (app only, ~29 MB)"
  case "$dest" in
    *.AppImage)
      echo "Run it with: $dest"
      echo "The first launch downloads the AI engine (~1.4 GB) and a starter"
      echo "model -- this only happens once."
      echo "If FUSE is unavailable: $dest --appimage-extract-and-run"
      ;;
    *.dmg)
      echo "Open the .dmg, then right-click the app and choose \"Open\""
      echo "the first time — unsigned build for now."
      echo "The first launch downloads the AI engine (~1.4 GB) and a starter"
      echo "model -- this only happens once."
      ;;
  esac
fi
