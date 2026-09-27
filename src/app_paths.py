"""Where PortableAI keeps bundled assets vs writable runtime data.

A PyInstaller --onefile binary unpacks to a temp directory (sys._MEIPASS)
that is deleted when the process exits. Personas and the web UI belong
there. chats.db, pairing state, logs, and vendored Ollama do not.

A packaged Linux app (AppImage or frozen binary) keeps that data in
``$XDG_DATA_HOME/PortableAI`` (default ``~/.local/share/PortableAI``)
so a second download in another folder still sees the same chats and
models. Data stays next to the executable only when that file is on
removable media (a USB stick you carry with you).

Source checkouts (``python run.py``) keep ``data/`` next to the repo.
AppImage mounts the payload read-only; ``APPIMAGE`` is the path of the
``.AppImage`` file the user actually launched. A macOS ``.app`` keeps
``data/`` next to the bundle, not inside ``Contents/MacOS``.
"""
from __future__ import annotations

import os
import shutil
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


def _xdg_data_home() -> Path:
    raw = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def _packaged_linux() -> bool:
    """True for an AppImage or frozen binary on Linux, not a source checkout."""
    if not sys.platform.startswith("linux"):
        return False
    if (os.environ.get("APPIMAGE") or "").strip():
        return True
    return is_frozen()


def _device_is_removable(dev: int, sysfs: Path | None = None) -> bool:
    """True when ``dev`` (st_dev) is a partition of a removable disk.

    Partition nodes usually have no ``removable`` file; the flag lives on
    the parent disk (``sdb1`` → ``sdb``).
    """
    root = sysfs or Path("/sys")
    node = root / "dev" / "block" / f"{os.major(dev)}:{os.minor(dev)}"
    try:
        resolved = node.resolve()
    except OSError:
        return False
    for candidate in (resolved / "removable", resolved.parent / "removable"):
        try:
            if candidate.read_text(encoding="utf-8").strip() == "1":
                return True
        except OSError:
            continue
    return False


def _is_removable_mount(path: Path) -> bool:
    """True when ``path`` (or its nearest existing parent) is on removable media."""
    current = path
    while not current.exists():
        if current.parent == current:
            return False
        current = current.parent
    try:
        dev = current.stat().st_dev
    except OSError:
        return False
    return _device_is_removable(dev)


def _has_user_state(path: Path) -> bool:
    db = path / "chats.db"
    if db.is_file() and db.stat().st_size > 0:
        return True
    models = path / "ollama-models"
    if not models.is_dir():
        return False
    try:
        next(models.iterdir())
    except (StopIteration, OSError):
        return False
    return True


def _adopt_legacy_data(legacy: Path, target: Path) -> None:
    """Move an older next-to-executable data dir into XDG once.

    Only when the XDG directory does not exist yet, so a second copy of
    the app does not hide chats that already lived beside an older build.
    """
    if target.exists() or not _has_user_state(legacy):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(legacy, target)
    except OSError:
        shutil.copytree(legacy, target)


def data_dir() -> Path:
    root = install_root()
    if _packaged_linux() and not _is_removable_mount(root):
        target = _xdg_data_home() / "PortableAI"
        _adopt_legacy_data(root / "data", target)
        return target
    return root / "data"
