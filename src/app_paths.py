"""Where PortableAI keeps bundled assets vs writable runtime data.

A PyInstaller --onefile binary unpacks to a temp directory (sys._MEIPASS)
that is deleted when the process exits. Personas and the web UI belong
there. chats.db, pairing state, logs, and vendored Ollama do not.
"""
from __future__ import annotations

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


def install_root() -> Path:
    """Directory of the launched program — writable `data/` lives here.

    Frozen: the real binary (sys.executable), not the temp extract dir.
    Source: sys.argv[0], so `python run.py` keeps data/ next to the repo.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def data_dir() -> Path:
    return install_root() / "data"
