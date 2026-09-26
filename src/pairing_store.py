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
        # Set only by the localhost admin before the next claim. Applied
        # once, onto the device created by that claim, then cleared.
        "pending_parent_device_id": None,
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
    data.setdefault("pending_parent_device_id", None)
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


def _ensure_device_shape(data: dict) -> bool:
    """Give every device a public id and an explicit parent field.

    ``id`` is not the auth token. Mirror URLs use it so a parent never
    receives the child's Bearer secret. ``parent_device_id`` is the
    parent's auth token, or None for an independent device. Missing
    keys on older pairing files are filled in once and persisted.
    """
    changed = False
    if "pending_parent_device_id" not in data:
        data["pending_parent_device_id"] = None
        changed = True
    for info in data.get("devices", {}).values():
        if not isinstance(info, dict):
            continue
        if "parent_device_id" not in info:
            info["parent_device_id"] = None
            changed = True
        if not info.get("id"):
            info["id"] = secrets.token_hex(16)
            changed = True
    return changed


def _consume_pending_parent(data: dict) -> str | None:
    """Parent token chosen by the admin for this claim, then forget it.

    A pending value that no longer names a paired device is dropped
    rather than stored, so a revoked parent cannot linger onto a
    later claim.
    """
    pending = data.get("pending_parent_device_id") or None
    data["pending_parent_device_id"] = None
    if pending and pending in data.get("devices", {}):
        return pending
    return None


def _issue_device_token(data: dict, device_name: str) -> str:
    token = secrets.token_hex(16)
    data["devices"][token] = {
        "id": secrets.token_hex(16),
        "name": device_name or "Unnamed device",
        "paired_at": time.time(),
        "parent_device_id": _consume_pending_parent(data),
    }
    return token


def set_pending_parent(path: Path, parent_token: str | None) -> None:
    """Remember which already-paired device will parent the next claim.

    ``None`` means the next device pairs as independent. This does not
    rewrite any device that already exists.
    """
    with _lock:
        data = _load(path)
        _ensure_device_shape(data)
        if parent_token:
            if parent_token not in data.get("devices", {}):
                raise ValueError("unknown parent device")
            data["pending_parent_device_id"] = parent_token
        else:
            data["pending_parent_device_id"] = None
        _save(path, data)


def get_pending_parent(path: Path) -> str | None:
    return _load(path).get("pending_parent_device_id") or None


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


# Skip rewriting pairing.json when a device is polling faster than this.
LAST_SEEN_MIN_INTERVAL = 60


def touch_last_seen(path: Path, token: str, now: float | None = None) -> None:
    """Record a successful authenticated request from this device.

    No-op for unknown tokens. Writes at most once per LAST_SEEN_MIN_INTERVAL
    so a polling client does not rewrite the file on every call.
    """
    if not token:
        return
    clock = time.time() if now is None else now
    with _lock:
        data = _load(path)
        info = data.get("devices", {}).get(token)
        if not isinstance(info, dict):
            return
        previous = float(info.get("last_seen") or 0)
        if previous and clock - previous < LAST_SEEN_MIN_INTERVAL:
            return
        info["last_seen"] = clock
        _save(path, data)


def list_devices(path: Path) -> list[dict]:
    with _lock:
        data = _load(path)
        if _ensure_device_shape(data):
            _save(path, data)
        rows = [{"token": t, **info} for t, info in data.get("devices", {}).items() if isinstance(info, dict)]
    rows.sort(key=lambda row: (float(row.get("last_seen") or 0), float(row.get("paired_at") or 0)), reverse=True)
    return rows


def list_children(path: Path, parent_token: str) -> list[dict]:
    """Public id and name of devices this token parents. No auth tokens."""
    if not parent_token:
        return []
    children = []
    for info in list_devices(path):
        if info.get("parent_device_id") == parent_token:
            children.append({
                "id": info["id"],
                "name": info.get("name") or "Unnamed device",
            })
    return children


def child_for_parent(path: Path, public_id: str, parent_token: str) -> dict | None:
    """The child record iff ``parent_token`` is the stored parent.

    Unknown ids and ids that belong to someone else both return None,
    so a caller cannot tell those cases apart.
    """
    if not public_id or not parent_token:
        return None
    for info in list_devices(path):
        if info.get("id") != public_id:
            continue
        if info.get("parent_device_id") != parent_token:
            return None
        return {
            "id": info["id"],
            "name": info.get("name") or "Unnamed device",
            "token": info["token"],
        }
    return None


def linked_child(path: Path, public_id: str) -> dict | None:
    """A device that already has a parent, by public id.

    Independent devices and unknown ids return None. Used by the desktop
    to open a child's mirror; it does not grant that access to other phones.
    """
    if not public_id:
        return None
    for info in list_devices(path):
        if info.get("id") != public_id:
            continue
        if not info.get("parent_device_id"):
            return None
        return {
            "id": info["id"],
            "name": info.get("name") or "Unnamed device",
            "token": info["token"],
        }
    return None


def revoke_device(path: Path, token: str) -> bool:
    data = _load(path)
    if token in data.get("devices", {}):
        del data["devices"][token]
        _save(path, data)
        return True
    return False
