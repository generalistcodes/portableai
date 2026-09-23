"""Where PortableAI keeps bundled assets vs writable runtime data.

A PyInstaller --onefile binary unpacks to a temp directory (sys._MEIPASS)
that is deleted when the process exits. Personas and the web UI belong
there. chats.db, pairing state, logs, and vendored Ollama do not.

AppImage mounts the payload read-only; `APPIMAGE` is the path of the
`.AppImage` file the user actually launched, so writable `data/` lives
next to that file. A macOS `.app` keeps `data/` next to the bundle, not
inside `Contents/MacOS`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_root() -> Path:
    """Read-only files shipped with the app (personas, UI, catalog)."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _macos_app_bundle_parent(exe: Path) -> Path | None:
    """Return the directory that contains Foo.app, or None if not in a bundle."""
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        bundle = exe.parent.parent.parent
        if bundle.suffix == ".app":
            return bundle.parent
    return None


def install_root() -> Path:
    """Directory of the launched program — writable `data/` lives here.

    Frozen: the real binary (sys.executable), not the temp extract dir.
    AppImage: the folder that contains the `.AppImage` file.
    macOS .app: the folder that contains the bundle.
    Source: sys.argv[0], so `python run.py` keeps data/ next to the repo.
    """
    appimage = (os.environ.get("APPIMAGE") or "").strip()
    if appimage:
        return Path(appimage).resolve().parent
    if is_frozen():
        exe = Path(sys.executable).resolve()
        bundle_parent = _macos_app_bundle_parent(exe)
        if bundle_parent is not None:
            return bundle_parent
        return exe.parent
    return Path(sys.argv[0]).resolve().parent


def data_dir() -> Path:
    return install_root() / "data"
