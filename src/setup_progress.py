"""In-process first-run status for GET /api/setup-status.

The Flask thread reads this while the main thread downloads or starts
the bundled Ollama. Both the setup overlay and console logs reflect the
same values.
"""
from __future__ import annotations

import threading

BUSY_PHASES = frozenset({"downloading_ollama", "extracting", "starting_ollama"})

_lock = threading.Lock()
_state: dict = {
    "phase": "ready",
    "percent": None,
    "bytes_downloaded": None,
    "bytes_total": None,
    "message": "",
}


def snapshot() -> dict:
    with _lock:
        payload = dict(_state)
    payload["busy"] = payload["phase"] in BUSY_PHASES
    return payload


def set_phase(
    phase: str,
    *,
    message: str = "",
    percent: float | int | None = None,
    bytes_downloaded: int | None = None,
    bytes_total: int | None = None,
) -> None:
    with _lock:
        _state["phase"] = phase
        _state["message"] = message
        _state["percent"] = None if percent is None else int(percent)
        _state["bytes_downloaded"] = bytes_downloaded
        _state["bytes_total"] = bytes_total


def update_download(bytes_downloaded: int, bytes_total: int | None) -> None:
    percent = None
    if bytes_total and bytes_total > 0:
        percent = min(100, int(100 * bytes_downloaded / bytes_total))
    set_phase(
        "downloading_ollama",
        message="Downloading Ollama",
        percent=percent,
        bytes_downloaded=bytes_downloaded,
        bytes_total=bytes_total,
    )


def mark_ready(message: str = "") -> None:
    set_phase("ready", message=message)


def mark_error(message: str) -> None:
    set_phase("error", message=message)


def reset() -> None:
    """Tests only: restore the idle default."""
    set_phase("ready", message="")
