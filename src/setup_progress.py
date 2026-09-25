"""In-process first-run status for GET /api/setup-status.

The Flask thread reads this while the main thread downloads or starts
the bundled Ollama. Both the setup overlay and console logs reflect the
same values.

Download ticks also append to ``data/setup.log`` (not ``logs.jsonl``,
which is the chat transcript). Double-clicked macOS/Windows launches
have no terminal; that file is how you tell slow from stuck.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

BUSY_PHASES = frozenset({"downloading_ollama", "extracting", "starting_ollama"})
STALL_SECONDS = 60.0
LOG_INTERVAL_SECONDS = 5.0
LOG_PERCENT_STEP = 5
SETUP_LOG_NAME = "setup.log"

_lock = threading.RLock()
_setup_log_path: Path | None = None
_state: dict = {
    "phase": "ready",
    "percent": None,
    "bytes_downloaded": None,
    "bytes_total": None,
    "message": "",
}
_clock: dict = {
    "last_progress_at": None,
    "started_at": None,
    "last_log_at": None,
    "last_log_pct": -LOG_PERCENT_STEP,
    "last_log_bytes": 0,
    "stall_logged": False,
}


def setup_log_path() -> Path:
    """Writable setup log: ``<data_dir>/setup.log`` on every OS."""
    if _setup_log_path is not None:
        return _setup_log_path
    try:
        from app_paths import data_dir
    except ImportError:
        return Path("data") / SETUP_LOG_NAME
    return data_dir() / SETUP_LOG_NAME


def set_setup_log_path(path: Path | None) -> None:
    """Bind the log file (tests, or engine download into a given data_dir)."""
    global _setup_log_path
    _setup_log_path = Path(path) if path is not None else None


def bind_setup_log_from_data_dir(data_dir: Path) -> Path:
    path = Path(data_dir) / SETUP_LOG_NAME
    set_setup_log_path(path)
    return path


def bind_setup_log_from_dest(dest: Path) -> Path:
    """``data/ollama-bin/<archive>`` → ``data/setup.log``; else sibling file."""
    parent = Path(dest).parent
    root = parent.parent if parent.name == "ollama-bin" else parent
    return bind_setup_log_from_data_dir(root)


def write_setup_log(text: str, *, path: Path | None = None) -> None:
    """Append one timestamped line. Never raises — logging must not abort a download.

    Writes only after ``bind_setup_log_from_data_dir`` / ``set_setup_log_path``
    so tests do not scatter files next to pytest. Production binds in
    ``run.py`` and ``ensure_binary``.
    """
    target = Path(path) if path is not None else _setup_log_path
    if target is None:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    line = f"{ts}  {text.rstrip()}\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
    except OSError:
        return


def snapshot() -> dict:
    now = time.monotonic()
    stall_line = None
    with _lock:
        payload = dict(_state)
        last_at = _clock["last_progress_at"]
        stalled = (
            payload["phase"] == "downloading_ollama"
            and last_at is not None
            and (now - last_at) >= STALL_SECONDS
        )
        if stalled:
            payload["message"] = "Download appears stalled -- check your connection"
            if not _clock["stall_logged"]:
                _clock["stall_logged"] = True
                stall_line = (
                    f"stalled  no new bytes for {now - last_at:.1f}s  "
                    f"downloaded={int(payload.get('bytes_downloaded') or 0)} bytes"
                )
        payload["stalled"] = stalled
        payload["busy"] = payload["phase"] in BUSY_PHASES
        payload["setup_log"] = str(setup_log_path().resolve())
    if stall_line:
        write_setup_log(stall_line)
    return payload


def set_phase(
    phase: str,
    *,
    message: str = "",
    percent: float | int | None = None,
    bytes_downloaded: int | None = None,
    bytes_total: int | None = None,
) -> None:
    now = time.monotonic()
    with _lock:
        entering_download = (
            phase == "downloading_ollama" and _state["phase"] != "downloading_ollama"
        )
        _state["phase"] = phase
        _state["message"] = message
        _state["percent"] = None if percent is None else int(percent)
        _state["bytes_downloaded"] = bytes_downloaded
        _state["bytes_total"] = bytes_total
        if entering_download:
            _clock["last_progress_at"] = now
            _clock["started_at"] = now
            _clock["last_log_at"] = None
            _clock["last_log_pct"] = -LOG_PERCENT_STEP
            _clock["last_log_bytes"] = int(bytes_downloaded or 0)
            _clock["stall_logged"] = False
        elif phase != "downloading_ollama":
            _clock["last_progress_at"] = None
            _clock["stall_logged"] = False


def update_download(bytes_downloaded: int, bytes_total: int | None) -> None:
    percent = None
    if bytes_total and bytes_total > 0:
        percent = min(100, int(100 * bytes_downloaded / bytes_total))
    now = time.monotonic()
    log_line = None
    with _lock:
        prev_bytes = _state.get("bytes_downloaded")
        if _state["phase"] != "downloading_ollama":
            _clock["started_at"] = now
            _clock["last_log_at"] = None
            _clock["last_log_pct"] = -LOG_PERCENT_STEP
            _clock["last_log_bytes"] = 0
            _clock["stall_logged"] = False
        if prev_bytes is None or bytes_downloaded > int(prev_bytes):
            _clock["last_progress_at"] = now
            _clock["stall_logged"] = False
        elif _clock["last_progress_at"] is None:
            _clock["last_progress_at"] = now
        started = _clock["started_at"] or now
        _clock["started_at"] = started
        elapsed = max(0.0, now - started)
        last_log_at = _clock["last_log_at"]
        last_log_pct = _clock["last_log_pct"]
        last_log_bytes = int(_clock["last_log_bytes"] or 0)
        should_log = last_log_at is None
        if percent is not None and percent >= last_log_pct + LOG_PERCENT_STEP:
            should_log = True
        if percent is not None and percent >= 100:
            should_log = True
        if last_log_at is not None and (now - last_log_at) >= LOG_INTERVAL_SECONDS:
            should_log = True
        if should_log:
            dt = (now - last_log_at) if last_log_at is not None else elapsed
            delta_bytes = max(0, int(bytes_downloaded) - last_log_bytes)
            speed = (delta_bytes / dt / (1024 * 1024)) if dt > 0 else 0.0
            log_line = _format_progress_line(
                int(bytes_downloaded),
                int(bytes_total) if bytes_total else None,
                elapsed,
                speed,
            )
            _clock["last_log_at"] = now
            _clock["last_log_pct"] = percent if percent is not None else last_log_pct
            _clock["last_log_bytes"] = int(bytes_downloaded)
        _state["phase"] = "downloading_ollama"
        _state["message"] = "Downloading Ollama"
        _state["percent"] = percent
        _state["bytes_downloaded"] = bytes_downloaded
        _state["bytes_total"] = bytes_total
    if log_line:
        write_setup_log(log_line)


def note_retry(attempt: int, max_attempts: int, error: BaseException | str) -> None:
    """Record a retry in setup.log and reset the stall / speed clocks."""
    write_setup_log(
        f"retry  attempt {attempt}/{max_attempts}  error={_short_error(error)}"
    )
    now = time.monotonic()
    with _lock:
        _clock["started_at"] = now
        _clock["last_progress_at"] = now
        _clock["last_log_at"] = None
        _clock["last_log_pct"] = -LOG_PERCENT_STEP
        _clock["last_log_bytes"] = 0
        _clock["stall_logged"] = False


def mark_ready(message: str = "") -> None:
    set_phase("ready", message=message)


def mark_error(message: str) -> None:
    set_phase("error", message=message)
    write_setup_log(f"error  {message}")


def reset() -> None:
    """Tests only: restore the idle default. Does not unbind the log path."""
    with _lock:
        _clock["last_progress_at"] = None
        _clock["started_at"] = None
        _clock["last_log_at"] = None
        _clock["last_log_pct"] = -LOG_PERCENT_STEP
        _clock["last_log_bytes"] = 0
        _clock["stall_logged"] = False
    set_phase("ready", message="")


def _mb(n: int | float) -> float:
    return float(n) / (1024 * 1024)


def _format_progress_line(
    bytes_downloaded: int,
    bytes_total: int | None,
    elapsed: float,
    speed_mb_s: float,
) -> str:
    if bytes_total and bytes_total > 0:
        pct = min(100, int(100 * bytes_downloaded / bytes_total))
        return (
            f"progress  {_mb(bytes_downloaded):.1f} / {_mb(bytes_total):.1f} MB "
            f"({pct}%)  elapsed={elapsed:.1f}s  speed={speed_mb_s:.2f} MB/s"
        )
    return (
        f"progress  {_mb(bytes_downloaded):.1f} MB  "
        f"elapsed={elapsed:.1f}s  speed={speed_mb_s:.2f} MB/s"
    )


def _short_error(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        detail = str(error).strip() or type(error).__name__
        return f"{type(error).__name__}: {detail}"
    return str(error).strip()
