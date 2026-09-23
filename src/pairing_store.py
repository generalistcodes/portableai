"""
Local-network pairing for phone clients.

The desktop browser UI on the same machine is always trusted (checked via
remote_addr in server.py, not here). Anything reaching the server from
elsewhere on the LAN -- an iPhone on the same WiFi/hotspot -- needs a
device token, gotten once by entering a short-lived PIN shown on the
server machine.

JSON-on-disk with a process-wide lock around PIN generate/claim so
concurrent claims cannot tear pairing.json or skip the lockout counter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

PIN_TTL_SECONDS = 300  # 5 minutes
MAX_PIN_ATTEMPTS = 5
MAX_PASSWORD_ATTEMPTS = 5
PASSWORD_LOCKOUT_SECONDS = 300

_lock = threading.Lock()


def _empty_state() -> dict:
    return {
        "current_pin": None,
        "pin_expires_at": 0,
        "failed_attempts": 0,
        "devices": {},
        "family_password_hash": None,
        "password_failures": {},
    }


def _load(path: Path) -> dict:
    if not Path(path).exists():
        return _empty_state()
    try:
        data = json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    data.setdefault("devices", {})
    data.setdefault("failed_attempts", 0)
    data.setdefault("family_password_hash", None)
    data.setdefault("password_failures", {})
    return data


def _save(path: Path, data: dict) -> None:
    """Write pairing.json atomically: temp file in the same directory, then
    os.replace() over the real path so readers never see a torn write."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def generate_pin(path: Path, ttl_seconds: int = PIN_TTL_SECONDS) -> str:
    with _lock:
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
    5-minute window). The full read-modify-write is under _lock so
    concurrent claims cannot skip failed_attempts or orphan a token."""
    with _lock:
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

        token = _issue_device_token(data, device_name)
        data["current_pin"] = None
        data["pin_expires_at"] = 0
        data["failed_attempts"] = 0
        _save(path, data)
        return token


def _issue_device_token(data: dict, device_name: str) -> str:
    token = secrets.token_hex(16)
    data["devices"][token] = {"name": device_name or "Unnamed device", "paired_at": time.time()}
    return token


def _hash_family_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def family_password_is_set(path: Path) -> bool:
    hash_value = _load(path).get("family_password_hash")
    return bool(hash_value)


def set_family_password(path: Path, password: str | None) -> None:
    """Store or clear the persistent family password. Empty/None clears it.

    Clearing or changing also drops per-source password lockouts so a new
    secret is not blocked by failures against the old one.
    """
    with _lock:
        data = _load(path)
        cleaned = (password or "").strip()
        if cleaned:
            data["family_password_hash"] = _hash_family_password(cleaned)
        else:
            data["family_password_hash"] = None
        data["password_failures"] = {}
        _save(path, data)


def claim_family_password(
    path: Path,
    submitted_password: str,
    device_name: str,
    source: str,
    max_attempts: int = MAX_PASSWORD_ATTEMPTS,
    lockout_seconds: int = PASSWORD_LOCKOUT_SECONDS,
    now: float | None = None,
) -> dict:
    """Exchange a matching family password for a device token.

    The password is not consumed and does not expire. Each successful
    claim mints a distinct token. Wrong attempts are counted per
    ``source`` (typically a remote IP) and never share the PIN lockout
    counter. Returns ``{"status": "ok", "token": ...}`` or
    ``{"status": "invalid"|"locked", "token": None}``.
    """
    clock = time.time() if now is None else now
    source_key = (source or "unknown").strip() or "unknown"
    submitted = (submitted_password or "").strip()
    with _lock:
        data = _load(path)
        failures = data.setdefault("password_failures", {})
        entry = failures.get(source_key) or {"count": 0, "locked_until": 0}
        if float(entry.get("locked_until") or 0) > clock:
            return {"status": "locked", "token": None}

        stored = data.get("family_password_hash")
        if not stored or not submitted:
            return {"status": "invalid", "token": None}

        candidate = _hash_family_password(submitted)
        if len(stored) != len(candidate) or not hmac.compare_digest(stored, candidate):
            entry["count"] = int(entry.get("count") or 0) + 1
            if entry["count"] >= max_attempts:
                entry["locked_until"] = clock + lockout_seconds
                entry["count"] = 0
                failures[source_key] = entry
                _save(path, data)
                return {"status": "locked", "token": None}
            failures[source_key] = entry
            _save(path, data)
            return {"status": "invalid", "token": None}

        failures.pop(source_key, None)
        token = _issue_device_token(data, device_name)
        _save(path, data)
        return {"status": "ok", "token": token}


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
