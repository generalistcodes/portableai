"""Replace the running packaged executable in place, with a data backup.

The new file is written beside the current executable and then renamed
onto that exact path. Nothing is downloaded into a second folder for the
user to choose between. Before the rename, the whole data directory is
copied to ``data_backup_<timestamp>/`` next to it. After the rename, the
new executable is launched with ``--update-health-check``. If it does not
start, or it does not see the same data directory (and the same chats and
models when those existed), the previous executable is put back.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_paths import data_dir, is_frozen


class UpdateError(RuntimeError):
    """The update did not finish. The previous executable is still in place."""


def current_executable() -> Path | None:
    """The file an in-place update is allowed to replace, or None from source."""
    appimage = (os.environ.get("APPIMAGE") or "").strip()
    if appimage:
        return Path(appimage).resolve()
    if is_frozen():
        return Path(sys.executable).resolve()
    return None


def collect_health(data: Path) -> dict[str, Any]:
    """What a just-started build reports about the data directory it found."""
    data = Path(data)
    count = 0
    db = data / "chats.db"
    if db.is_file() and db.stat().st_size > 0:
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()
            count = int(row[0]) if row else 0
        except sqlite3.Error:
            count = 0
        finally:
            conn.close()
    models = data / "ollama-models"
    models_present = False
    if models.is_dir():
        try:
            models_present = next(models.iterdir(), None) is not None
        except OSError:
            models_present = False
    return {
        "ok": True,
        "data_dir": str(data.resolve()),
        "conversation_count": count,
        "models_dir_present": models_present,
    }


def emit_health_check() -> int:
    """Print one JSON object for ``--update-health-check`` and return 0."""
    report = collect_health(data_dir())
    sys.stdout.write(json.dumps(report) + "\n")
    sys.stdout.flush()
    return 0


def backup_data_dir(data: Path, now: datetime | None = None) -> Path:
    """Copy ``data`` to a sibling ``data_backup_<timestamp>`` directory."""
    data = Path(data)
    if not data.is_dir():
        raise UpdateError(f"No data directory to back up at {data}")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    dest = data.parent / f"data_backup_{stamp}"
    if dest.exists():
        dest = data.parent / f"data_backup_{stamp}_{os.getpid()}"
    shutil.copytree(data, dest)
    return dest


def release_asset_name() -> str:
    """GitHub Releases filename for this operating system."""
    import platform

    machine = platform.machine().lower()
    if sys.platform.startswith("linux"):
        arch = "x86_64" if machine in {"x86_64", "amd64"} else machine
        return f"PortableAI-linux-{arch}.AppImage"
    if sys.platform == "darwin":
        return "PortableAI-macos-arm64.dmg"
    if sys.platform.startswith("win"):
        return "PortableAI-windows-x86_64.exe"
    raise UpdateError(f"No packaged update for {sys.platform}")


def download_release_asset(repo: str, dest: Path) -> None:
    """Stream the latest release asset into ``dest`` (the caller picks the path)."""
    import requests

    name = release_asset_name()
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    listing = requests.get(
        api,
        timeout=30,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "PortableAI"},
    )
    listing.raise_for_status()
    assets = listing.json().get("assets") or []
    url = next((a.get("browser_download_url") for a in assets if a.get("name") == name), None)
    if not url:
        raise UpdateError(f"Latest release has no {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120, headers={"User-Agent": "PortableAI"}) as blob:
        blob.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in blob.iter_content(chunk_size=256 * 1024):
                if chunk:
                    handle.write(chunk)


def launch_health_check(executable: Path, timeout: int = 60) -> dict[str, Any]:
    """Run ``executable --update-health-check`` and parse its JSON report."""
    try:
        proc = subprocess.run(
            [str(executable), "--update-health-check"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Could not start the updated executable: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise UpdateError(detail or f"Health check exited {proc.returncode}")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise UpdateError("Health check produced no report")
    try:
        report = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise UpdateError("Health check report was not JSON") from exc
    if not isinstance(report, dict):
        raise UpdateError("Health check report was not an object")
    return report


def _health_problem(before: dict[str, Any], after: dict[str, Any], expected: Path) -> str | None:
    if not after.get("ok"):
        return "The updated executable did not report a successful start."
    try:
        found = Path(str(after.get("data_dir") or "")).resolve()
    except OSError:
        found = Path(str(after.get("data_dir") or ""))
    if found != expected.resolve():
        return (
            f"The updated executable is looking in {found} "
            f"instead of the existing data directory {expected.resolve()}."
        )
    if before["conversation_count"] > 0 and int(after.get("conversation_count") or 0) <= 0:
        return "The updated executable started, but chat history looks empty."
    if before["models_dir_present"] and not after.get("models_dir_present"):
        return "The updated executable started, but installed models are missing."
    return None


def _restore_executable(previous: Path, executable: Path) -> None:
    os.replace(previous, executable)


def apply_update(
    *,
    executable: Path,
    data: Path,
    fetch_new: Callable[[Path], None],
    launch_health: Callable[[Path], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Download onto ``executable``'s path, after backing up ``data``.

    ``fetch_new`` must write the new build to the path it is given. That
    path is always ``<current name>.new`` in the same directory. The file
    is then renamed onto ``executable`` — the path the user is running.
    """
    executable = Path(executable).resolve()
    data = Path(data).resolve()
    if not executable.is_file():
        raise UpdateError(f"No executable to replace at {executable}")

    staging = executable.with_name(executable.name + ".new")
    previous = executable.with_name(executable.name + ".previous")
    if staging.exists():
        staging.unlink()
    try:
        fetch_new(staging)
    except UpdateError:
        if staging.exists():
            staging.unlink()
        raise
    except Exception as exc:
        if staging.exists():
            staging.unlink()
        raise UpdateError(f"Download failed: {exc}") from exc
    if staging.resolve().parent != executable.parent or not staging.is_file() or staging.stat().st_size == 0:
        if staging.exists():
            staging.unlink()
        raise UpdateError(
            "The update was not written next to the current executable, so it was not installed."
        )

    before = collect_health(data)
    try:
        backup = backup_data_dir(data, now=now)
    except Exception:
        staging.unlink(missing_ok=True)
        raise

    mode = executable.stat().st_mode
    shutil.copy2(executable, previous)
    try:
        os.replace(staging, executable)
        executable.chmod(mode | 0o111)
        report = (launch_health or launch_health_check)(executable)
        problem = _health_problem(before, report, data)
    except Exception as exc:
        if previous.exists():
            _restore_executable(previous, executable)
        if isinstance(exc, UpdateError):
            raise UpdateError(
                f"{exc} The previous version was restored. Your data was not changed. "
                f"A backup is at {backup}."
            ) from exc
        raise UpdateError(
            f"The update failed ({exc}). The previous version was restored. "
            f"Your data was not changed. A backup is at {backup}."
        ) from exc
    if problem:
        if previous.exists():
            _restore_executable(previous, executable)
        raise UpdateError(
            f"{problem} The previous version was restored. Your data was not changed. "
            f"A backup is at {backup}."
        )
    previous.unlink(missing_ok=True)
    return {
        "executable": str(executable),
        "data_dir": str(data),
        "backup": str(backup),
        "conversation_count": int(report.get("conversation_count") or 0),
        "models_dir_present": bool(report.get("models_dir_present")),
    }


def apply_installed_update(*, repo: str, data: Path | None = None) -> dict[str, Any]:
    """Update the packaged app this process was launched from."""
    executable = current_executable()
    if executable is None:
        raise UpdateError(
            "This copy was started from source. An update replaces the packaged "
            "executable at its current path, and there isn't one."
        )
    repo = (repo or "").strip()
    if not repo:
        raise UpdateError("Set a GitHub owner/repo in Update check URL before updating.")

    def fetch(dest: Path) -> None:
        download_release_asset(repo, dest)

    return apply_update(executable=executable, data=data or data_dir(), fetch_new=fetch)
