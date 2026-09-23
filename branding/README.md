This is the one source of truth for PortableAI's logo. Never hand-edit a
copy in ui/static/, packaging/, or elsewhere -- edit logo-source.svg here,
then re-run generate_icons.py to update every platform's copy at once.

```bash
python branding/generate_icons.py
```

`logo-source.svg` is the only file that should be hand-edited. It is the
live purple circular badge (copied from `ui/static/logo.svg`); do not
replace it with a redraw.

`generate_icons.py` reads that SVG and writes every platform copy:

| Target | Output |
| --- | --- |
| Web (favicon + sidebar) | `ui/static/logo.svg` (byte-identical to the source) and `ui/static/logo.png` |
| Linux AppImage | `packaging/linux/icons/` — `portableai.png` (256×256), `.desktop`/hicolor/pixmap sizes, and SVG. `make_appimage.sh` installs these into the AppDir (`portableai.png` + a real `.DirIcon`, not a symlink) |
| macOS `.app` | `branding/generated/macos/portableai.icns` — `make_dmg.sh` copies this into `Contents/Resources/` |
| iOS AppIcon | `branding/generated/ios/AppIcon.appiconset/` — 1024×1024 App Store icon plus the sizes Xcode's catalog expects. Copy that folder into the separate `portableai-ios` repo; do not re-export |

Linux AppImage wrap also runs this script (`--appdir`) so the icon cannot
silently drift from a one-off copy the way the generic-gear bug did.
