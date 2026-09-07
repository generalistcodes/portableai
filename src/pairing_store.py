"""
Local-network pairing for phone clients.

The desktop browser UI on the same machine is always trusted (checked via
remote_addr in server.py, not here). Anything reaching the server from
elsewhere on the LAN -- an iPhone on the same WiFi/hotspot -- needs a
device token, gotten once by entering a short-lived PIN shown on the
server machine.

Deliberately just JSON-on-disk: one laptop, one Flask process, no
concurrent-writer problem worth solving with a real database.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

PIN_TTL_SECONDS = 300  # 5 minutes
MAX_PIN_ATTEMPTS = 5


def _load(path: Path) -> dict:
    if not Path(path).exists():
        return {"current_pin": None, "pin_expires_at": 0, "failed_attempts": 0, "devices": {}}
    try:
        data = json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError):
        return {"current_pin": None, "pin_expires_at": 0, "failed_attempts": 0, "devices": {}}
    data.setdefault("devices", {})
    data.setdefault("failed_attempts", 0)
    return data


def _save(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2))


def generate_pin(path: Path, ttl_seconds: int = PIN_TTL_SECONDS) -> str:
    data = _load(path)
    pin = f"{secrets.randbelow(1_000_000):06d}"
    data["current_pin"] = pin
    data["pin_expires_at"] = time.time() + ttl_seconds
    data["failed_attempts"] = 0
    _save(path, data)
    return pin


def get_current_pin(path: Path) -> dict | None:
    data = _load(path)
    if not data.get("current_pin"):
        return None
    if time.time() > data.get("pin_expires_at", 0):
        return None
    return {"pin": data["current_pin"], "expires_at": data["pin_expires_at"]}


def claim_pin(path: Path, submitted_pin: str, device_name: str, max_attempts: int = MAX_PIN_ATTEMPTS) -> str | None:
    """Exchange a correct, unexpired PIN for a device token. The PIN is
    single-use either way it resolves: correct guesses consume it (so a
    captured PIN can't be replayed), and hitting max_attempts wrong
    guesses invalidates it too (so it can't be brute-forced within its
    5-minute window)."""
    data = _load(path)
    if not data.get("current_pin"):
        return None
    if time.time() > data.get("pin_expires_at", 0):
        return None

    if submitted_pin != data["current_pin"]:
        data["failed_attempts"] = data.get("failed_attempts", 0) + 1
        if data["failed_attempts"] >= max_attempts:
            data["current_pin"] = None
            data["pin_expires_at"] = 0
            data["failed_attempts"] = 0
        _save(path, data)
        return None

    token = secrets.token_hex(16)
    data["devices"][token] = {"name": device_name or "Unnamed device", "paired_at": time.time()}
    data["current_pin"] = None
    data["pin_expires_at"] = 0
    data["failed_attempts"] = 0
    _save(path, data)
    return token


def is_valid_token(path: Path, token: str) -> bool:
    if not token:
        return False
    data = _load(path)
    return token in data.get("devices", {})


def list_devices(path: Path) -> list[dict]:
    data = _load(path)
    return [{"token": t, **info} for t, info in data.get("devices", {}).items()]


def revoke_device(path: Path, token: str) -> bool:
    data = _load(path)
    if token in data.get("devices", {}):
        del data["devices"][token]
        _save(path, data)
        return True
    return False
