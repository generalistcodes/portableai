"""
Minimal backend for the persona chat UI.

- Serves the static frontend (ui/static/).
- Proxies chat requests to a local Ollama instance via OllamaClient.
- Lazily builds each persona model in Ollama the first time it's used,
  optionally on a different base model (model_override) than the one in
  its .Modelfile -- built as a separate named variant so the original
  persona definition is never mutated.
- Lists installed Ollama models and where they live on disk (on Linux,
  PortableAI's own data/ollama-models/; elsewhere a best-effort OS default).
- Logs every request/reply to data/logs.jsonl (append-only, human-readable).
- Persists a couple of settings (Ollama base URL, whether to auto-build
  personas on startup) to data/settings.json.

Intentionally not a "real" web framework app: no auth, no DB, single
process. This is a teaching/demo server for the blog post and for local
use, same spirit as USBMind's minimal chat UI -- talk to Ollama directly,
no heavy third-party dependency.
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

try:
    from app_paths import data_dir, is_frozen, resource_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from app_paths import data_dir, is_frozen, resource_root

ROOT = resource_root()
sys.path.insert(0, str(ROOT / "src"))

from ollama_client import (  # noqa: E402
    INCOMPLETE_RESPONSE_MESSAGE,
    OllamaClient,
    OllamaError,
)
from ollama_runtime import models_dir  # noqa: E402
from persona_loader import (  # noqa: E402
    ModelfileParseError,
    Persona,
    humanize_persona_id,
    load_persona_file,
    write_default_flag,
    write_modelfile,
)
import conversation_store as store  # noqa: E402
import pairing_store  # noqa: E402
import persona_cards  # noqa: E402
import setup_progress  # noqa: E402

PERSONAS_DIR = ROOT / "personas"
DATA_DIR = data_dir()
LOG_FILE = DATA_DIR / "logs.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"
THEME_FILE = DATA_DIR / "theme.json"
DB_FILE = DATA_DIR / "chats.db"
PAIRING_FILE = DATA_DIR / "pairing.json"
STATIC_DIR = ROOT / "ui" / "static"
CATALOG_FILE = ROOT / "ui" / "model_catalog.json"

DEFAULT_SETTINGS = {
    "base_url": "http://localhost:11434",
    "auto_build_on_startup": True,
    "update_check_url": "",       # empty = feature disabled, no network calls made
    "auto_check_updates": False,  # opt-in; manual "Check now" always works regardless
}

ALLOWED_THEMES = ("dark", "light", "ube")
DEFAULT_THEME = "dark"

# Bump these when you change this repo / the curated catalog, and reflect
# the same values in whatever JSON you publish at your update_check_url.
APP_NAME = "PortableAI"
APP_VERSION = "0.7.3"
CATALOG_VERSION = "2026-09-06"

PORT = 5050
BIND_HOST = "0.0.0.0"  # all IPv4 interfaces; phones on the LAN need this
LISTEN_PORT = PORT  # actual bound port; may differ from PORT after fallback
LISTEN_PORT_TRIES = 10

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1MB; Flask returns 413 above this

# Ollama mid-request failures that should never become Werkzeug HTML 500s.
_OLLAMA_CALL_ERRORS = (OllamaError, requests.ConnectionError, requests.Timeout, json.JSONDecodeError)


def _ollama_error_message(exc: BaseException) -> str:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return "Lost connection to Ollama mid-request."
    if isinstance(exc, json.JSONDecodeError):
        return INCOMPLETE_RESPONSE_MESSAGE
    return str(exc)

# Tracks which persona/model-variant names we've already `create`d in this
# process, so we don't re-POST /api/create on every single chat message.
_built_personas: set[str] = set()

# Set by run.py when it launches the bundled Linux Ollama subprocess, so
# this process talks to that instance without rewriting settings.json.
# mode is "bundled", "external", or "none" (this OS has no vendor path).
_managed_ollama_base_url: str | None = None
_managed_ollama_mode: str | None = None

OLLAMA_REASON_UNREACHABLE = "Cannot reach Ollama -- is it running?"
OLLAMA_REASON_NOT_BUNDLED = (
    "PortableAI doesn't bundle Ollama on this OS yet -- "
    "install it separately from https://ollama.com/download"
)
OLLAMA_INSTALL_URL = "https://ollama.com/download"


def set_managed_ollama_base_url(url: str | None, *, mode: str | None = None) -> None:
    global _managed_ollama_base_url, _managed_ollama_mode
    _managed_ollama_base_url = url.rstrip("/") if url else None
    if _managed_ollama_base_url:
        _managed_ollama_mode = mode or "bundled"
    elif mode == "none":
        _managed_ollama_mode = "none"
    else:
        _managed_ollama_mode = None


def _ollama_url_port(base_url: str) -> int | None:
    try:
        parsed = urllib.parse.urlparse(base_url)
    except ValueError:
        return None
    if parsed.port:
        return int(parsed.port)
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def _ollama_unsupported_os() -> bool:
    if _managed_ollama_mode == "none":
        return True
    try:
        from ollama_runtime import supported_on_this_os
    except ImportError:
        return False
    return not supported_on_this_os()


def ollama_unavailable_reason() -> str:
    """Same sentence for GET /api/status and 503s when Ollama is down."""
    if _ollama_unsupported_os():
        return OLLAMA_REASON_NOT_BUNDLED
    return OLLAMA_REASON_UNREACHABLE


def _ollama_unreachable_response():
    return jsonify({"error": ollama_unavailable_reason()}), 503


def _ollama_mode_fields(base_url: str, *, available: bool) -> dict:
    """Read-only Settings label plus reachability: bundled / external / unavailable."""
    if not available:
        unsupported = _ollama_unsupported_os()
        fields = {
            "ollama_mode": "unavailable",
            "ollama_mode_label": "Ollama: unavailable",
            "ollama_reason": (
                OLLAMA_REASON_NOT_BUNDLED if unsupported else OLLAMA_REASON_UNREACHABLE
            ),
        }
        if unsupported:
            fields["ollama_install_url"] = OLLAMA_INSTALL_URL
        return fields
    if _managed_ollama_mode == "external":
        return {
            "ollama_mode": "external",
            "ollama_mode_label": f"Ollama: external override ({base_url})",
            "ollama_reason": "",
        }
    if _managed_ollama_mode == "bundled":
        port = _ollama_url_port(base_url)
        port_s = str(port) if port is not None else "unknown"
        return {
            "ollama_mode": "bundled",
            "ollama_mode_label": f"Ollama: bundled (port {port_s})",
            "ollama_reason": "",
        }
    return {
        "ollama_mode": "default",
        "ollama_mode_label": f"Ollama: not bundled ({base_url})",
        "ollama_reason": "",
    }


def set_listen_port(port: int) -> None:
    """Record the port Flask actually bound, so QR/LAN URLs match."""
    global LISTEN_PORT
    LISTEN_PORT = int(port)


def listen_port() -> int:
    return int(LISTEN_PORT)

# Admin-only: these must never be reachable from the LAN, even with a
# valid device token — only from the machine physically running the
# server (checked by remote_addr below). Pairing PIN/QR/devices, logs,
# settings, and model pull are the sensitive set.
#
# Catalog / updates decision: also admin-only. /api/models/catalog is a
# curated public list, but the response marks which models are installed
# here (local inventory) and the download UI is pull-adjacent.
# /api/updates/check is read-only version JSON with no user data, but it
# hits a URL taken from settings (outbound request a paired phone should
# not trigger). /api/models/check-update actually re-pulls. If catalog
# were a static file with no installed flags and no secrets, LAN-with-
# token would be acceptable; that is not the current shape.
#
# /api/theme is deliberately NOT admin-only: appearance has no security
# implications (unlike base_url, logs, or model pull). Paired devices
# share one server-side theme via requires-token, same as /api/personas.
#
# Persona writes (POST/PUT/DELETE /api/personas...) are admin-only: a
# Modelfile is shared by every device. GET /api/personas stays
# requires-token so phones can list personas and their cards.
_ADMIN_ONLY_PATHS_PREFIX = "/api/pairing/devices"
_ADMIN_ONLY_PATHS = {
    "/api/pairing/pin",
    "/api/pairing/pin/regenerate",
    "/api/pairing/qr.svg",
    "/api/logs",
    "/api/settings",
    "/api/models/pull",
    "/api/models/catalog",
    "/api/models/check-update",
    "/api/updates/check",
}

# A LAN device without a token yet must still be able to reach these to
# pair at all, or to do a basic reachability check before pairing.
_LAN_OPEN_PATHS = {"/api/pairing/claim", "/api/health"}


def _normalize_ip(addr: str | None) -> str:
    """Strip IPv4-mapped IPv6, brackets, and zone ids so comparisons are
    against a plain IPv4 or IPv6 address."""
    if not addr:
        return ""
    ip = addr.strip().lower()
    if ip.startswith("[") and ip.endswith("]"):
        ip = ip[1:-1]
    if "%" in ip:
        ip = ip.split("%", 1)[0]
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip


def _is_loopback_ip(addr: str | None) -> bool:
    ip = _normalize_ip(addr)
    if not ip:
        return False
    if ip in ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1"):
        return True
    return ip.startswith("127.")


def _is_local_client(addr: str | None = None) -> bool:
    """True only for loopback. The desktop owner at http://127.0.0.1:5050
    (or localhost) is trusted without a PIN. Anyone else — a phone, or a
    browser opened at the LAN IP — must pair. Do not treat the server's
    own LAN address as trusted: that is exactly how phones connect."""
    ip = _normalize_ip(addr if addr is not None else request.remote_addr)
    return _is_loopback_ip(ip)


def _is_persona_write(path: str, method: str) -> bool:
    """True for persona/card mutations. GET /api/personas stays public-to-token."""
    if method == "GET":
        return False
    return path == "/api/personas" or path.startswith("/api/personas/")


@app.before_request
def _check_auth():
    path = request.path
    if not path.startswith("/api/"):
        return None  # index page + static assets (js/css/svg): always public

    is_local = _is_local_client()

    if (
        path in _ADMIN_ONLY_PATHS
        or path.startswith(_ADMIN_ONLY_PATHS_PREFIX)
        or _is_persona_write(path, request.method)
    ):
        if is_local:
            return None
        return jsonify({"error": "only available on the server machine itself"}), 403

    if path in _LAN_OPEN_PATHS:
        return None

    if is_local:
        return None  # desktop browser UI on the same machine: always trusted

    auth_header = request.headers.get("Authorization", "")
    token = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
    if pairing_store.is_valid_token(PAIRING_FILE, token):
        return None

    return jsonify({"error": "pairing required"}), 401


def _caller_identity() -> dict:
    """Who is making this request, for data-ownership purposes. Only
    call this from inside a route handler that _check_auth has already
    let through -- by that point, either the request is local (trusted)
    or its Authorization header carries a token pairing_store already
    validated, so no re-validation happens here.

    is_admin=True (the desktop browser on the server machine) can see
    every device's conversations; everyone else only sees their own,
    identified by their device token as owner_id."""
    if _is_local_client():
        return {"owner_id": store.LOCAL_OWNER_ID, "is_admin": True}
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
    return {"owner_id": token, "is_admin": False}


def _looks_like_ipv4(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _usable_lan_ip(ip: str) -> bool:
    return _looks_like_ipv4(ip) and not ip.startswith("127.") and not ip.startswith("169.254.")


def _skip_iface(name: str) -> bool:
    """Drop loopback and container/VM bridges so pairing URLs use WiFi/LAN."""
    n = (name or "").lower()
    if n == "lo":
        return True
    return any(
        n.startswith(p)
        for p in ("docker", "br-", "veth", "virbr", "cni-", "flannel", "lxcbr", "tun", "tap")
    )


def _skip_darwin_iface(name: str) -> bool:
    """macOS virtual / point-to-point interfaces a phone cannot reach."""
    n = (name or "").lower()
    if n == "lo0" or n.startswith("lo"):
        return True
    return any(
        n.startswith(p)
        for p in (
            "awdl",
            "llw",
            "utun",
            "gif",
            "stf",
            "anpi",
            "bridge",
            "ap",
            "vmnet",
            "veth",
            "docker",
        )
    )


def _enumerate_lan_ips_darwin() -> list[str]:
    """macOS: list interfaces with `ifconfig -l`, then resolve each with
    `ipconfig getifaddr <iface>` (both ship with macOS)."""
    ips: list[str] = []
    try:
        listed = subprocess.run(
            ["ifconfig", "-l"], capture_output=True, text=True, timeout=2
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if listed.returncode != 0:
        return []

    for iface in listed.stdout.split():
        if _skip_darwin_iface(iface):
            continue
        try:
            result = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        ip = (result.stdout or "").strip()
        if ip and _usable_lan_ip(ip):
            ips.append(ip)
    return ips


def _enumerate_lan_ips_linux() -> list[str]:
    """Linux path (unchanged): `ip -4 -json` then `hostname -I`."""
    ips: list[str] = []

    # Primary: `ip -4 -json` includes interface names, so we can skip
    # docker/bridge addresses a phone on WiFi cannot reach.
    try:
        result = subprocess.run(
            ["ip", "-4", "-json", "addr", "show"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            for iface in json.loads(result.stdout):
                if _skip_iface(iface.get("ifname") or ""):
                    continue
                for addr_info in iface.get("addr_info", []):
                    ip = addr_info.get("local")
                    if ip and _usable_lan_ip(ip):
                        ips.append(ip)
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    if ips:
        return ips

    # Fallback: `hostname -I` lists every IPv4/IPv6 address, no route
    # required, but without interface names.
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            for token in result.stdout.split():
                if _usable_lan_ip(token):
                    ips.append(token)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return ips


def _skip_windows_adapter(name: str) -> bool:
    """Hyper-V/WSL/VM nics a phone on Wi-Fi cannot reach."""
    n = (name or "").lower()
    return any(
        needle in n
        for needle in (
            "loopback",
            "wsl",
            "hyper-v",
            "vethernet",
            "virtualbox",
            "vmware",
            "docker",
            "bluetooth",
            "pseudo",
        )
    )


def _parse_windows_ipconfig(text: str) -> list[str]:
    """Pull IPv4 addresses out of English `ipconfig` (or `ipconfig /all`)."""
    ips: list[str] = []
    skip = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.endswith(":") and "adapter" in stripped.lower() and not line.startswith((" ", "\t")):
            skip = _skip_windows_adapter(stripped[:-1])
            continue
        if skip:
            continue
        label = stripped.lower()
        if "ipv4 address" not in label and not label.startswith("ip address"):
            continue
        if ":" not in stripped:
            continue
        ip = stripped.rsplit(":", 1)[-1].strip().split("(", 1)[0].strip()
        if ip and _usable_lan_ip(ip):
            ips.append(ip)
    return ips


def _enumerate_lan_ips_windows() -> list[str]:
    """Windows: parse `ipconfig` (ships with every supported SKU)."""
    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return _parse_windows_ipconfig(result.stdout or "")


def _enumerate_lan_ips() -> list[str]:
    """Every non-loopback IPv4 address currently assigned to this
    machine, across all interfaces. Deliberately does NOT rely on
    routing-table tricks (connect-to-8.8.8.8 and see what source IP
    comes back) -- that approach silently returns 127.0.0.1 on a
    machine with no default route at all, which is exactly what an
    isolated WiFi hotspot with no internet uplink looks like. Real
    interface enumeration works regardless of whether there's a route
    to the outside world."""
    if sys.platform == "darwin":
        return _enumerate_lan_ips_darwin()
    if sys.platform.startswith("win"):
        return _enumerate_lan_ips_windows()
    return _enumerate_lan_ips_linux()


def _pick_lan_ip(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    for ip in candidates:
        if ip.startswith("10.42."):
            return ip
    for ip in candidates:
        if ip.startswith("192.168."):
            return ip
    return candidates[0]


def _get_lan_ip() -> dict:
    """Returns {"ip": str, "detected": bool}. detected=False means no
    real interface could be found and "ip" is the unreliable localhost
    fallback -- callers should surface that plainly (e.g. a warning in
    the pairing UI) rather than quietly showing 127.0.0.1 as if it were
    a real LAN address a phone could use."""
    picked = _pick_lan_ip(_enumerate_lan_ips())
    if not picked:
        return {"ip": "127.0.0.1", "detected": False}
    return {"ip": picked, "detected": True}


def _build_pairing_uri(ip: str, port: int, pin: str, expires_at: float) -> str:
    """A portableai:// deep link -- reserved for the native app to
    register and handle once it exists, letting a scan skip straight to
    a pre-filled pairing screen. Confirmed on real hardware (2026-09):
    scanning this today with no app installed shows unrecognized text,
    not an error -- expected, since nothing claims this scheme yet.
    Not used for the live QR code; see _build_pairing_web_url for that."""
    params = urllib.parse.urlencode(
        {"ip": ip, "port": port, "pin": pin, "name": socket.gethostname(), "exp": int(expires_at)}
    )
    return f"portableai://pair?{params}"


def _build_pairing_web_url(ip: str, port: int, pin: str, expires_at: float) -> str:
    """What the QR code actually encodes: a plain http:// URL any
    phone's camera recognizes and opens directly, with the PIN carried
    as a query param so the web UI itself can attempt pairing
    automatically on load -- no app required, no typing, genuinely one
    scan. See docs/PHONE_PAIRING_PLAN.md for why this replaced the
    portableai:// scheme as the default."""
    params = urllib.parse.urlencode({"pair_pin": pin, "exp": int(expires_at)})
    return f"http://{ip}:{port}/?{params}"


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "app": APP_NAME, "app_version": APP_VERSION})


@app.route("/api/pairing/pin", methods=["GET"])
def api_pairing_pin():
    current = pairing_store.get_current_pin(PAIRING_FILE)
    if not current:
        pairing_store.generate_pin(PAIRING_FILE)
        current = pairing_store.get_current_pin(PAIRING_FILE)
    lan = _get_lan_ip()
    current["lan_ip"] = lan["ip"]
    current["lan_ip_detected"] = lan["detected"]
    current["lan_url"] = f"http://{lan['ip']}:{listen_port()}"
    current["pairing_uri"] = _build_pairing_uri(lan["ip"], listen_port(), current["pin"], current["expires_at"])
    current["pairing_web_url"] = _build_pairing_web_url(lan["ip"], listen_port(), current["pin"], current["expires_at"])
    return jsonify(current)


@app.route("/api/pairing/qr.svg", methods=["GET"])
def api_pairing_qr():
    current = pairing_store.get_current_pin(PAIRING_FILE)
    if not current:
        pairing_store.generate_pin(PAIRING_FILE)
        current = pairing_store.get_current_pin(PAIRING_FILE)
    lan = _get_lan_ip()
    # Encodes the plain-URL form, not the portableai:// scheme -- this
    # is what actually opens on a phone with no app installed.
    web_url = _build_pairing_web_url(lan["ip"], listen_port(), current["pin"], current["expires_at"])

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(web_url, image_factory=qrcode.image.svg.SvgPathImage)
    import io

    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), mimetype="image/svg+xml")


@app.route("/api/pairing/pin/regenerate", methods=["POST"])
def api_pairing_pin_regenerate():
    pairing_store.generate_pin(PAIRING_FILE)
    current = pairing_store.get_current_pin(PAIRING_FILE)
    return jsonify(current)


@app.route("/api/pairing/claim", methods=["POST"])
def api_pairing_claim():
    body = request.get_json(force=True) or {}
    pin = (body.get("pin") or "").strip()
    family_password = (body.get("family_password") or "")
    if not isinstance(family_password, str):
        family_password = str(family_password)
    family_password = family_password.strip()
    device_name = (body.get("device_name") or "").strip()
    if pin:
        token = pairing_store.claim_pin(PAIRING_FILE, pin, device_name)
        if not token:
            return jsonify({"error": "invalid or expired PIN"}), 401
        return jsonify({"device_token": token})
    if family_password:
        result = pairing_store.claim_family_password(
            PAIRING_FILE,
            family_password,
            device_name,
            source=_normalize_ip(request.remote_addr),
        )
        if result["status"] == "locked":
            return jsonify({"error": "too many failed password attempts"}), 429
        if result["status"] != "ok":
            return jsonify({"error": "invalid family password"}), 401
        return jsonify({"device_token": result["token"]})
    return jsonify({"error": "pin or family_password is required"}), 400


@app.route("/api/pairing/devices", methods=["GET"])
def api_pairing_devices():
    return jsonify(pairing_store.list_devices(PAIRING_FILE))


@app.route("/api/pairing/devices/<token>", methods=["DELETE"])
def api_pairing_revoke(token):
    revoked = pairing_store.revoke_device(PAIRING_FILE, token)
    return jsonify({"revoked": revoked})


def _load_settings() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, indent=2))
        return dict(DEFAULT_SETTINGS)
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def _save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    to_write = {key: settings[key] for key in DEFAULT_SETTINGS}
    SETTINGS_FILE.write_text(json.dumps(to_write, indent=2))


def _settings_payload() -> dict:
    payload = _load_settings()
    payload["family_password_set"] = pairing_store.family_password_is_set(PAIRING_FILE)
    return payload


def _load_theme() -> str:
    """Shared UI theme. Missing or unreadable file → dark. Never raises."""
    if not THEME_FILE.exists():
        return DEFAULT_THEME
    try:
        data = json.loads(THEME_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return DEFAULT_THEME
    theme = data.get("theme") if isinstance(data, dict) else None
    if theme in ALLOWED_THEMES:
        return theme
    return DEFAULT_THEME


def _save_theme(theme: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    THEME_FILE.write_text(json.dumps({"theme": theme}, indent=2) + "\n")


def _client() -> OllamaClient:
    base_url = _managed_ollama_base_url or _load_settings()["base_url"]
    return OllamaClient(base_url=base_url)


def _db() -> "sqlite3.Connection":
    DATA_DIR.mkdir(exist_ok=True)
    return store.connect(DB_FILE)


@app.errorhandler(store.ChatDatabaseError)
@app.errorhandler(sqlite3.DatabaseError)
def _handle_corrupt_chat_db(_err):
    return jsonify({"error": "chat history database appears corrupted"}), 500


@app.errorhandler(413)
def _handle_request_too_large(_err):
    return jsonify({"error": "request too large"}), 413


def _startup_check_chat_db() -> None:
    """Log a loud warning for a wiped or malformed chats.db without
    crashing the process — conversation routes then return JSON 500."""
    DATA_DIR.mkdir(exist_ok=True)
    try:
        conn = store.connect(DB_FILE)
        conn.close()
    except store.ChatDatabaseError:
        print(
            f"WARNING: chat history database {DB_FILE} appears corrupted. "
            "Conversation routes will return an error until it is repaired "
            "or replaced. Silent history loss is worse than a visible error.",
            file=sys.stderr,
        )


def _log(entry: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    entry = {"timestamp": time.time(), **entry}
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _read_logs(limit: int = 200) -> list[dict]:
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text().splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    return list(reversed(entries[-limit:]))


def _persona_files() -> list[Path]:
    return sorted(PERSONAS_DIR.glob("*.Modelfile"))


def _persona_name_from_path(path: Path) -> str:
    return path.stem  # e.g. "no-nonsense-mentor"


def _persona_modelfile(persona_id: str) -> Path:
    return PERSONAS_DIR / f"{persona_id}.Modelfile"


def _persona_ids() -> list[str]:
    return [_persona_name_from_path(p) for p in _persona_files()]


def _invalidate_built_persona(persona_id: str) -> None:
    stale = {n for n in _built_personas if n == persona_id or n.startswith(f"{persona_id}--")}
    _built_personas.difference_update(stale)


def _installed_model_names() -> tuple[list[str] | None, tuple | None]:
    """Return (names, error_response). error_response is (jsonify, status) on failure."""
    client = _client()
    if not client.is_available():
        return None, _ollama_unreachable_response()
    try:
        raw = client.list_models()
    except _OLLAMA_CALL_ERRORS as e:
        return None, (jsonify({"error": _ollama_error_message(e)}), 502)
    names = []
    for m in raw:
        name = m.get("name") or m.get("model")
        if name:
            names.append(name)
    return names, None


def _installed_model_names_or_empty() -> list[str]:
    """Same inventory GET /api/models uses, but never fails the caller."""
    names, err = _installed_model_names()
    if err is not None:
        return []
    return names or []


def _with_model_installed(payload: dict, installed: list[str] | None = None) -> dict:
    names = installed if installed is not None else _installed_model_names_or_empty()
    base = payload.get("base_model")
    payload["model_installed"] = bool(base) and persona_cards.model_is_installed(base, names)
    return payload


def _require_installed_base_model(base_model: str):
    names, err = _installed_model_names()
    if err is not None:
        return err
    if not persona_cards.model_is_installed(base_model, names):
        return jsonify({"error": f"base_model is not installed: {base_model}"}), 400
    return None


def _set_only_default(target_id: str) -> None:
    for path in _persona_files():
        write_default_flag(path, _persona_name_from_path(path) == target_id)


def _promote_another_default(excluding_id: str) -> str | None:
    remaining = [pid for pid in _persona_ids() if pid != excluding_id]
    if not remaining:
        return None
    _set_only_default(remaining[0])
    return remaining[0]


def _persona_payload(path: Path) -> dict:
    name = _persona_name_from_path(path)
    cards = persona_cards.load_cards(PERSONAS_DIR, name)
    try:
        persona = load_persona_file(path)
        return {
            "id": name,
            "display_name": persona.resolved_display_name(name),
            "is_default": persona.is_default,
            "icon": persona.resolved_icon(),
            "base_model": persona.base_model,
            "system_prompt": persona.system or "",
            "system_preview": (persona.system or "")[:160],
            "parameters": persona.parameters,
            "cards": cards,
        }
    except ModelfileParseError as e:
        return {
            "id": name,
            "display_name": humanize_persona_id(name),
            "is_default": False,
            "icon": "message",
            "error": str(e),
            "cards": cards,
        }


def _slug(name: str) -> str:
    """Turn an Ollama model tag like 'qwen2.5:0.5b' into a safe suffix for
    a derived model name, e.g. 'qwen2.5-0.5b'."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", name)


def _human_size(num_bytes) -> str | None:
    if not num_bytes:
        return None
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _models_path_hint() -> str:
    """Where this PortableAI instance stores GGUF blobs.

    run.py launches a bundled Ollama with OLLAMA_MODELS pointed at
    data/ollama-models/, so the path is known and controlled. Unknown
    OSes still guess from $OLLAMA_MODELS or the documented default.
    """
    if sys.platform.startswith("linux") or sys.platform == "darwin" or sys.platform.startswith("win"):
        return str(models_dir(DATA_DIR).resolve())
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return env
    if sys.platform.startswith("win"):
        return r"%USERPROFILE%\.ollama\models"
    return "~/.ollama/models"


def _ensure_persona_built(client: OllamaClient, persona_name: str, base_model_override: str | None = None) -> str:
    """Builds the persona in Ollama if it hasn't been built yet in this
    process. If base_model_override is given, builds (and caches) a
    separate named variant on top of that base model instead of mutating
    the persona's own .Modelfile-declared base. Returns the actual model
    name to chat against."""
    variant_name = persona_name if not base_model_override else f"{persona_name}--{_slug(base_model_override)}"

    if variant_name in _built_personas:
        return variant_name

    path = PERSONAS_DIR / f"{persona_name}.Modelfile"
    if not path.exists():
        raise FileNotFoundError(f"no such persona: {persona_name}")

    persona = load_persona_file(path)
    if base_model_override:
        persona.base_model = base_model_override

    client.create_model(persona.to_create_payload(variant_name))
    _built_personas.add(variant_name)
    return variant_name


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/personas", methods=["GET"])
def api_personas():
    installed = _installed_model_names_or_empty()
    payloads = [_with_model_installed(_persona_payload(path), installed) for path in _persona_files()]
    return jsonify(payloads)


@app.route("/api/personas", methods=["POST"])
def api_create_persona():
    body = request.get_json(force=True) or {}
    try:
        display_name = persona_cards.validate_display_name(body.get("display_name"))
        system_prompt = persona_cards.validate_system_prompt(body.get("system_prompt"))
        base_model = persona_cards.validate_base_model_name(body.get("base_model"))
        cards = persona_cards.normalize_cards(body.get("cards"), allow_verified_true=False)
    except persona_cards.PersonaValidationError as e:
        return jsonify({"error": str(e)}), 400

    installed_err = _require_installed_base_model(base_model)
    if installed_err is not None:
        return installed_err

    persona_id = persona_cards.unique_slug(display_name, set(_persona_ids()))
    existing = _persona_files()
    want_default = bool(body.get("is_default")) or not existing

    persona = Persona(
        base_model=base_model,
        system=system_prompt,
        display_name=display_name,
        is_default=want_default,
        icon="message",
    )
    write_modelfile(_persona_modelfile(persona_id), persona)
    if want_default:
        _set_only_default(persona_id)
    if cards:
        persona_cards.save_cards(PERSONAS_DIR, persona_id, cards)
    return jsonify(_with_model_installed(_persona_payload(_persona_modelfile(persona_id)))), 201


@app.route("/api/personas/<persona_id>", methods=["PUT"])
def api_update_persona(persona_id):
    path = _persona_modelfile(persona_id)
    if not path.exists():
        return jsonify({"error": "persona not found"}), 404
    try:
        persona = load_persona_file(path)
    except ModelfileParseError as e:
        return jsonify({"error": str(e)}), 400

    body = request.get_json(force=True) or {}
    try:
        if "display_name" in body:
            persona.display_name = persona_cards.validate_display_name(body.get("display_name"))
        if "system_prompt" in body:
            persona.system = persona_cards.validate_system_prompt(body.get("system_prompt"))
        if "base_model" in body:
            persona.base_model = persona_cards.validate_base_model_name(body.get("base_model"))
            installed_err = _require_installed_base_model(persona.base_model)
            if installed_err is not None:
                return installed_err
    except persona_cards.PersonaValidationError as e:
        return jsonify({"error": str(e)}), 400

    becoming_default = bool(body["is_default"]) if "is_default" in body else persona.is_default
    if "is_default" in body and not becoming_default and persona.is_default:
        others = [pid for pid in _persona_ids() if pid != persona_id]
        if not others:
            return jsonify({"error": "cannot unset the only default persona"}), 400
        persona.is_default = False
        write_modelfile(path, persona)
        _set_only_default(others[0])
        _invalidate_built_persona(persona_id)
        return jsonify(_with_model_installed(_persona_payload(path)))

    persona.is_default = becoming_default
    write_modelfile(path, persona)
    if becoming_default:
        _set_only_default(persona_id)
    _invalidate_built_persona(persona_id)
    return jsonify(_with_model_installed(_persona_payload(path)))


@app.route("/api/personas/<persona_id>", methods=["DELETE"])
def api_delete_persona(persona_id):
    path = _persona_modelfile(persona_id)
    if not path.exists():
        return jsonify({"error": "persona not found"}), 404
    ids = _persona_ids()
    if len(ids) <= 1:
        return jsonify({"error": "cannot delete the only remaining persona"}), 400
    try:
        persona = load_persona_file(path)
        was_default = persona.is_default
    except ModelfileParseError:
        was_default = False
    if was_default:
        promoted = _promote_another_default(persona_id)
        if promoted is None:
            return jsonify({"error": "cannot delete the only default persona"}), 400
    path.unlink()
    persona_cards.delete_cards_file(PERSONAS_DIR, persona_id)
    _invalidate_built_persona(persona_id)
    return jsonify({"deleted": True, "id": persona_id})


@app.route("/api/personas/<persona_id>/cards", methods=["POST"])
def api_add_persona_card(persona_id):
    if not _persona_modelfile(persona_id).exists():
        return jsonify({"error": "persona not found"}), 404
    body = request.get_json(force=True) or {}
    cards = persona_cards.load_cards(PERSONAS_DIR, persona_id)
    taken = {c["id"] for c in cards}
    try:
        card = persona_cards.normalize_card(
            body, allow_verified_true=False, taken_ids=taken
        )
    except persona_cards.PersonaValidationError as e:
        return jsonify({"error": str(e)}), 400
    cards.append(card)
    persona_cards.save_cards(PERSONAS_DIR, persona_id, cards)
    return jsonify(card), 201


@app.route("/api/personas/<persona_id>/cards/<card_id>", methods=["PUT"])
def api_update_persona_card(persona_id, card_id):
    if not _persona_modelfile(persona_id).exists():
        return jsonify({"error": "persona not found"}), 404
    cards = persona_cards.load_cards(PERSONAS_DIR, persona_id)
    existing = persona_cards.find_card(cards, card_id)
    if existing is None:
        return jsonify({"error": "card not found"}), 404
    body = request.get_json(force=True) or {}
    merged = dict(existing)
    if "title" in body:
        merged["title"] = body.get("title")
    if "answer" in body:
        merged["answer"] = body.get("answer")
    content_changed = (
        ("title" in body and body.get("title") != existing["title"])
        or ("answer" in body and body.get("answer") != existing["answer"])
    )
    if "verified" in body:
        merged["verified"] = body.get("verified")
        merged["verified_by"] = body.get("verified_by")
        merged["verified_source"] = body.get("verified_source")
    elif content_changed:
        # Edited text is no longer the reviewed version.
        merged["verified"] = False
        merged["verified_by"] = None
        merged["verified_source"] = None
    taken = {c["id"] for c in cards if c["id"] != card_id}
    try:
        updated = persona_cards.normalize_card(
            merged, allow_verified_true=True, taken_ids=taken
        )
    except persona_cards.PersonaValidationError as e:
        return jsonify({"error": str(e)}), 400
    # Keep the original id unless the client sent a new one (we don't expose that).
    updated["id"] = card_id
    cards = [updated if c["id"] == card_id else c for c in cards]
    persona_cards.save_cards(PERSONAS_DIR, persona_id, cards)
    return jsonify(updated)


@app.route("/api/personas/<persona_id>/cards/<card_id>", methods=["DELETE"])
def api_delete_persona_card(persona_id, card_id):
    if not _persona_modelfile(persona_id).exists():
        return jsonify({"error": "persona not found"}), 404
    cards = persona_cards.load_cards(PERSONAS_DIR, persona_id)
    if persona_cards.find_card(cards, card_id) is None:
        return jsonify({"error": "card not found"}), 404
    cards = [c for c in cards if c["id"] != card_id]
    persona_cards.save_cards(PERSONAS_DIR, persona_id, cards)
    return jsonify({"deleted": True, "id": card_id})


@app.route("/api/models")
def api_models():
    client = _client()
    if not client.is_available():
        return jsonify({"models": [], "models_path_hint": _models_path_hint(), "error": "Ollama not reachable"}), 200
    try:
        raw_models = client.list_models()
    except _OLLAMA_CALL_ERRORS as e:
        return jsonify({"error": _ollama_error_message(e)}), 502

    models = []
    for m in raw_models:
        details = m.get("details") or {}
        models.append(
            {
                "name": m.get("name") or m.get("model"),
                "digest": m.get("digest"),
                "size_bytes": m.get("size"),
                "size_human": _human_size(m.get("size")),
                "quantization": details.get("quantization_level"),
                "parameter_size": details.get("parameter_size"),
                "modified_at": m.get("modified_at"),
            }
        )
    return jsonify({"models": models, "models_path_hint": _models_path_hint()})


@app.route("/api/status")
def api_status():
    client = _client()
    available = client.is_available()
    payload = {"ollama_available": available, "base_url": client.base_url}
    payload.update(_ollama_mode_fields(client.base_url, available=available))
    return jsonify(payload)


@app.route("/api/setup-status")
def api_setup_status():
    """First-run vendor progress. Same snapshot the setup overlay polls."""
    payload = setup_progress.snapshot()
    payload["setup_log"] = str((DATA_DIR / "setup.log").resolve())
    return jsonify(payload)


def _load_catalog() -> list[dict]:
    if not CATALOG_FILE.exists():
        return []
    try:
        return json.loads(CATALOG_FILE.read_text())
    except json.JSONDecodeError:
        return []


@app.route("/api/models/catalog")
def api_models_catalog():
    catalog = _load_catalog()
    client = _client()
    installed_names: set[str] = set()
    if client.is_available():
        try:
            installed_names = {m.get("name") for m in client.list_models()}
        except _OLLAMA_CALL_ERRORS:
            pass
    for entry in catalog:
        entry["installed"] = entry["name"] in installed_names
    return jsonify(catalog)


@app.route("/api/models/pull", methods=["POST"])
def api_models_pull():
    """Pull a model; stream NDJSON so the UI can show retry messages mid-download.

    Validation / reachability errors stay plain JSON (400/503). Once the
    pull starts, the response is ``application/x-ndjson``: zero or more
    ``{"status":"retrying","message":"..."}`` lines, then either
    ``{"pulled":"<name>"}`` or ``{"error":"..."}``.
    """
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    client = _client()
    if not client.is_available():
        return _ollama_unreachable_response()

    def generate():
        try:
            for event in client.iter_pull_model(name):
                yield json.dumps(event) + "\n"
        except _OLLAMA_CALL_ERRORS as e:
            yield json.dumps({"error": _ollama_error_message(e)}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@app.route("/api/models/check-update", methods=["POST"])
def api_models_check_update():
    """Ask Ollama itself whether an installed model has changed upstream.

    There's no public "is a newer version available" endpoint to query
    without reverse-engineering Ollama's registry auth flow, so instead
    this delegates to Ollama the same way `ollama pull <already-installed>`
    does: re-pulling is a safe, idempotent no-op if nothing changed, and
    only downloads the diff if something did. We just compare the
    manifest digest before and after to know which one happened.
    """
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    client = _client()
    if not client.is_available():
        return _ollama_unreachable_response()

    try:
        before = {m.get("name"): m.get("digest") for m in client.list_models()}
    except _OLLAMA_CALL_ERRORS as e:
        return jsonify({"error": _ollama_error_message(e)}), 502

    if name not in before:
        return jsonify({"error": f"'{name}' is not installed"}), 404
    digest_before = before[name]

    try:
        client.pull_model(name)
        after = {m.get("name"): m.get("digest") for m in client.list_models()}
    except _OLLAMA_CALL_ERRORS as e:
        return jsonify({"error": _ollama_error_message(e)}), 502

    digest_after = after.get(name)
    return jsonify(
        {
            "name": name,
            "updated": digest_before != digest_after,
            "digest_before": digest_before,
            "digest_after": digest_after,
        }
    )


_GITHUB_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def _github_repo_from_update_setting(value: str) -> str | None:
    """Treat `org/repo` or a github.com URL as GitHub Releases, not a JSON file."""
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return None
    if _GITHUB_REPO_RE.fullmatch(raw):
        return raw
    match = re.match(r"https?://github\.com/([\w.-]+/[\w.-]+)", raw)
    if match:
        return match.group(1)
    return None


def _normalize_version_tag(value: str) -> str:
    """Strip a leading `v` so `v0.4.0` and `0.4.0` compare equal."""
    text = (value or "").strip()
    if len(text) >= 2 and text[0] in "vV" and text[1].isdigit():
        return text[1:]
    return text


def _version_ints(value: str) -> tuple[int, ...]:
    """Numeric dotted segments only. `v0.10.0-test` → (0, 10, 0)."""
    core = re.split(r"[-+]", _normalize_version_tag(value), maxsplit=1)[0]
    parts: list[int] = []
    for segment in core.split("."):
        match = re.match(r"(\d+)", segment)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _is_newer_app_version(remote: str, current: str) -> bool:
    """True if remote is a higher semver than current.

    Tuple-of-ints, not string compare: `"0.10.0" > "0.9.0"` here, whereas
    `"0.10.0" < "0.9.0"` character-by-character.
    """
    if not (remote or "").strip():
        return False
    remote_parts = _version_ints(remote)
    current_parts = _version_ints(current)
    if not remote_parts:
        return False
    width = max(len(remote_parts), len(current_parts), 1)
    remote_parts = remote_parts + (0,) * (width - len(remote_parts))
    current_parts = current_parts + (0,) * (width - len(current_parts))
    return remote_parts > current_parts


def _updates_from_github_releases(repo: str) -> dict:
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    resp = requests.get(
        api,
        timeout=5,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PortableAI",
        },
    )
    resp.raise_for_status()
    remote = resp.json()
    if not isinstance(remote, dict):
        raise ValueError("GitHub Releases response was not an object")
    tag = (remote.get("tag_name") or "").strip()
    html_url = (remote.get("html_url") or "").strip()
    available = bool(tag) and _is_newer_app_version(tag, APP_VERSION)
    return {
        "enabled": True,
        "reachable": True,
        "current_app_version": APP_VERSION,
        "current_catalog_version": CATALOG_VERSION,
        "remote_app_version": tag or None,
        "remote_catalog_version": None,
        "app_update_available": available,
        "catalog_update_available": False,
        "message": "A new version is available." if available else None,
        "notes_url": html_url or None,
    }


@app.route("/api/updates/check")
def api_updates_check():
    """Optional check for app/catalog updates -- entirely off by default.

    Only makes a network call if the person has filled in update_check_url
    in Settings. That field can be:

    - empty: disabled, no network
    - `org/repo` or a github.com URL: GitHub Releases latest tag vs APP_VERSION
    - any other http(s) URL: the original JSON manifest
      {"app_version", "catalog_version", "message", "notes_url"}
    """
    settings = _load_settings()
    url = (settings.get("update_check_url") or "").strip()
    if not url:
        return jsonify({"enabled": False})

    github_repo = _github_repo_from_update_setting(url)
    try:
        if github_repo:
            return jsonify(_updates_from_github_releases(github_repo))
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        remote = resp.json()
    except (requests.RequestException, ValueError):
        return jsonify({"enabled": True, "reachable": False})

    remote_app_version = remote.get("app_version")
    remote_catalog_version = remote.get("catalog_version")
    return jsonify(
        {
            "enabled": True,
            "reachable": True,
            "current_app_version": APP_VERSION,
            "current_catalog_version": CATALOG_VERSION,
            "remote_app_version": remote_app_version,
            "remote_catalog_version": remote_catalog_version,
            "app_update_available": bool(remote_app_version) and _is_newer_app_version(str(remote_app_version), APP_VERSION),
            "catalog_update_available": bool(remote_catalog_version) and remote_catalog_version != CATALOG_VERSION,
            "message": remote.get("message"),
            "notes_url": remote.get("notes_url"),
        }
    )


@app.route("/api/theme", methods=["GET"])
def get_theme():
    return jsonify({"theme": _load_theme()})


@app.route("/api/theme", methods=["POST"])
def post_theme():
    body = request.get_json(force=True) or {}
    theme = body.get("theme")
    if theme not in ALLOWED_THEMES:
        return jsonify({"error": "theme must be one of: dark, light, ube"}), 400
    _save_theme(theme)
    return jsonify({"theme": theme})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(_settings_payload())


@app.route("/api/settings", methods=["POST"])
def post_settings():
    body = request.get_json(force=True) or {}
    password_to_set = None
    if "family_password" in body:
        value = body.get("family_password")
        if value is None:
            value = ""
        if not isinstance(value, str):
            return jsonify({"error": "family_password must be a string"}), 400
        password_to_set = value
    settings = _load_settings()
    for key in DEFAULT_SETTINGS:
        if key in body:
            settings[key] = body[key]
    _save_settings(settings)
    if password_to_set is not None:
        pairing_store.set_family_password(PAIRING_FILE, password_to_set)
    return jsonify(_settings_payload())


def _owns_conversation(conv: dict, identity: dict) -> bool:
    """Admins (the desktop) can touch any conversation; everyone else
    only their own."""
    return identity["is_admin"] or conv.get("owner_id") == identity["owner_id"]


@app.route("/api/conversations", methods=["GET"])
def api_list_conversations():
    identity = _caller_identity()
    archived = request.args.get("archived") == "1"
    query = request.args.get("q") or None
    # Admins see every device's conversations by default (owner_id=None);
    # everyone else only ever sees their own.
    owner_filter = None if identity["is_admin"] else identity["owner_id"]
    conn = _db()
    try:
        convs = store.list_conversations(conn, owner_id=owner_filter, archived=archived, query=query)
    finally:
        conn.close()
    return jsonify(convs)


@app.route("/api/conversations", methods=["POST"])
def api_create_conversation():
    identity = _caller_identity()
    body = request.get_json(force=True) or {}
    persona_name = body.get("persona")
    if not persona_name:
        return jsonify({"error": "persona is required"}), 400
    path = PERSONAS_DIR / f"{persona_name}.Modelfile"
    if not path.exists():
        return jsonify({"error": f"no such persona: {persona_name}"}), 400
    base_model = body.get("model_override") or load_persona_file(path).base_model
    conn = _db()
    try:
        conv_id = store.create_conversation(conn, persona_name, base_model, identity["owner_id"])
    finally:
        conn.close()
    return jsonify({"id": conv_id})


@app.route("/api/conversations/<conv_id>", methods=["GET"])
def api_get_conversation(conv_id):
    identity = _caller_identity()
    conn = _db()
    try:
        conv = store.get_conversation(conn, conv_id)
    finally:
        conn.close()
    # 404 rather than 403 for someone else's conversation -- confirming
    # that an ID exists at all is itself a small information leak.
    if conv is None or not _owns_conversation(conv, identity):
        return jsonify({"error": "conversation not found"}), 404
    return jsonify(conv)


@app.route("/api/conversations/<conv_id>/export", methods=["GET"])
def api_export_conversation(conv_id):
    """Same ownership rule as GET, just a distinct endpoint so intent is
    explicit and the frontend can trigger a file download from it."""
    identity = _caller_identity()
    conn = _db()
    try:
        conv = store.get_conversation(conn, conv_id)
    finally:
        conn.close()
    if conv is None or not _owns_conversation(conv, identity):
        return jsonify({"error": "conversation not found"}), 404
    return jsonify(conv)


@app.route("/api/conversations/<conv_id>", methods=["PATCH"])
def api_rename_conversation(conv_id):
    identity = _caller_identity()
    body = request.get_json(force=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    conn = _db()
    try:
        conv = store.get_conversation(conn, conv_id)
        if conv is None or not _owns_conversation(conv, identity):
            return jsonify({"error": "conversation not found"}), 404
        store.rename_conversation(conn, conv_id, title)
    finally:
        conn.close()
    return jsonify({"id": conv_id, "title": title})


@app.route("/api/conversations/<conv_id>/archive", methods=["POST"])
def api_archive_conversation(conv_id):
    identity = _caller_identity()
    body = request.get_json(force=True) or {}
    archived = bool(body.get("archived", True))
    conn = _db()
    try:
        conv = store.get_conversation(conn, conv_id)
        if conv is None or not _owns_conversation(conv, identity):
            return jsonify({"error": "conversation not found"}), 404
        store.set_archived(conn, conv_id, archived)
    finally:
        conn.close()
    return jsonify({"id": conv_id, "archived": archived})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def api_delete_conversation(conv_id):
    identity = _caller_identity()
    conn = _db()
    try:
        conv = store.get_conversation(conn, conv_id)
        if conv is None or not _owns_conversation(conv, identity):
            return jsonify({"error": "conversation not found"}), 404
        store.delete_conversation(conn, conv_id)
    finally:
        conn.close()
    return jsonify({"deleted": True})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    identity = _caller_identity()
    body = request.get_json(force=True) or {}
    persona_name = body.get("persona")
    message = body.get("message", "")
    model_override = body.get("model_override") or None
    conversation_id = body.get("conversation_id") or None

    if not persona_name or not message:
        return jsonify({"error": "persona and message are required"}), 400

    client = _client()
    if not client.is_available():
        return _ollama_unreachable_response()

    try:
        actual_model = _ensure_persona_built(client, persona_name, model_override)
    except FileNotFoundError as e:
        return jsonify({"error": f"could not build persona '{persona_name}': {e}"}), 500
    except OllamaError as e:
        return jsonify({"error": f"could not build persona '{persona_name}': {e}"}), 500
    except (requests.ConnectionError, requests.Timeout, json.JSONDecodeError) as e:
        return jsonify({"error": _ollama_error_message(e)}), 502

    conn = _db()
    try:
        if conversation_id:
            existing = store.get_conversation(conn, conversation_id)
            if existing is None or not _owns_conversation(existing, identity):
                return jsonify({"error": "conversation not found"}), 404
        else:
            conversation_id = store.create_conversation(conn, persona_name, actual_model, identity["owner_id"])

        prior = store.get_messages(conn, conversation_id)
        messages = [{"role": m["role"], "content": m["content"]} for m in prior]
        messages.append({"role": "user", "content": message})

        started = time.time()
        try:
            reply = client.chat(actual_model, messages)
        except _OLLAMA_CALL_ERRORS as e:
            err_msg = _ollama_error_message(e)
            _log(
                {
                    "conversation_id": conversation_id,
                    "persona": persona_name,
                    "model_used": actual_model,
                    "message": message,
                    "error": err_msg,
                }
            )
            return jsonify({"error": err_msg}), 502
        latency_ms = round((time.time() - started) * 1000)

        store.add_message(conn, conversation_id, "user", message)
        store.add_message(conn, conversation_id, "assistant", reply, latency_ms=latency_ms)
    finally:
        conn.close()

    _log(
        {
            "conversation_id": conversation_id,
            "persona": persona_name,
            "model_used": actual_model,
            "message": message,
            "reply": reply,
            "latency_ms": latency_ms,
        }
    )
    return jsonify(
        {
            "reply": reply,
            "latency_ms": latency_ms,
            "model_used": actual_model,
            "conversation_id": conversation_id,
        }
    )


@app.route("/api/chat/reference", methods=["POST"])
def api_chat_reference():
    """Insert a quick-reference card Q&A into history with no model call."""
    identity = _caller_identity()
    body = request.get_json(force=True) or {}
    persona_name = body.get("persona")
    card_id = body.get("card_id")
    conversation_id = body.get("conversation_id") or None
    if not persona_name or not card_id:
        return jsonify({"error": "persona and card_id are required"}), 400

    path = _persona_modelfile(persona_name)
    if not path.exists():
        return jsonify({"error": "persona not found"}), 404
    card = persona_cards.find_card(persona_cards.load_cards(PERSONAS_DIR, persona_name), card_id)
    if card is None:
        return jsonify({"error": "card not found"}), 404

    try:
        persona = load_persona_file(path)
        model_used = persona.base_model
    except ModelfileParseError:
        model_used = "card"

    user_message = card["title"]
    source_meta = {
        "card_id": card["id"],
        "verified": bool(card["verified"]),
        "verified_by": card.get("verified_by"),
        "verified_source": card.get("verified_source"),
    }

    conn = _db()
    try:
        if conversation_id:
            existing = store.get_conversation(conn, conversation_id)
            if existing is None or not _owns_conversation(existing, identity):
                return jsonify({"error": "conversation not found"}), 404
        else:
            conversation_id = store.create_conversation(
                conn, persona_name, model_used, identity["owner_id"]
            )
        store.add_message(
            conn,
            conversation_id,
            "user",
            user_message,
            source="card",
            source_id=card["id"],
            source_meta=source_meta,
        )
        store.add_message(
            conn,
            conversation_id,
            "assistant",
            card["answer"],
            latency_ms=0,
            source="card",
            source_id=card["id"],
            source_meta=source_meta,
        )
    finally:
        conn.close()

    return jsonify(
        {
            "reply": card["answer"],
            "user_message": user_message,
            "latency_ms": 0,
            "model_used": model_used,
            "conversation_id": conversation_id,
            "source": "card",
            "card_id": card["id"],
            "verified": bool(card["verified"]),
            "verified_by": card.get("verified_by"),
            "verified_source": card.get("verified_source"),
        }
    )


@app.route("/api/logs", methods=["GET"])
def api_get_logs():
    return jsonify(_read_logs())


@app.route("/api/logs", methods=["DELETE"])
def api_clear_logs():
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    return jsonify({"cleared": True})


def _bind_fails(port: int) -> bool:
    """True if Flask would not be able to bind this port (IPv4).

    Uses SO_REUSEADDR the same way Werkzeug/Flask does. A recently-killed
    listener can leave the port in TIME_WAIT; bind() without reuse then
    fails even though nothing is LISTENing, which used to look like a
    mystery occupant (`lsof` empty, `--restart` still refused).
    """
    for host in ("127.0.0.1", "0.0.0.0"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                return True
    return False


def _port_in_use(port: int) -> bool:
    if _pids_listening_on(port):
        return True
    return _bind_fails(port)


def _pids_listening_on(port: int) -> list[int]:
    """PIDs with a TCP LISTEN socket on `port`. Empty if none, or if we
    cannot see them (missing ss/lsof, permissions). Never includes us."""
    found: list[int] = []

    try:
        result = subprocess.run(
            ["ss", "-tlnp"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            port_re = re.compile(rf":{port}\b")
            pid_re = re.compile(r"pid=(\d+)")
            for line in result.stdout.splitlines():
                if not port_re.search(line):
                    continue
                found.extend(int(m) for m in pid_re.findall(line))
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    if not found:
        try:
            result = subprocess.run(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                found.extend(int(tok) for tok in result.stdout.split() if tok.isdigit())
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    me = os.getpid()
    # Preserve order while dropping duplicates and our own pid.
    return list(dict.fromkeys(pid for pid in found if pid != me))


def _process_cmdline(pid: int) -> str:
    proc_cmd = Path(f"/proc/{pid}/cmdline")
    if proc_cmd.exists():
        try:
            return proc_cmd.read_bytes().replace(b"\x00", b" ").decode(errors="replace").strip()
        except OSError:
            pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return ""


def _process_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _script_arg_in_cmdline(cmdline: str, script: str) -> bool:
    """True if `script` appears as its own argv token or path suffix
    (`python run.py`, `/path/run.py`), not as a substring of another name."""
    return re.search(rf"(?:^|[/\\ \t]){re.escape(script)}(?:$|[ \t])", cmdline) is not None


def _is_our_server_process(cmdline: str, cwd: str = "", root: str | None = None) -> bool:
    """A leftover of this app: `python run.py` / `python ui/server.py`,
    or `python server.py` launched from this project's ui/ directory."""
    if not cmdline:
        return False
    root = root if root is not None else str(ROOT)
    if is_frozen():
        exe_name = Path(sys.executable).name
        if exe_name and _script_arg_in_cmdline(cmdline, exe_name):
            return True
    if _script_arg_in_cmdline(cmdline, "run.py") or _script_arg_in_cmdline(cmdline, "ui/server.py"):
        return True
    if _script_arg_in_cmdline(cmdline, "ui\\server.py"):
        return True
    ui_dir = str(Path(root) / "ui")
    if _script_arg_in_cmdline(cmdline, "server.py") and cwd.rstrip("/\\") == ui_dir:
        return True
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not signalable as us


def _stop_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            print(
                f"Port {PORT} is in use by pid {pid}, but we don't have permission to stop it.",
                file=sys.stderr,
            )
            sys.exit(1)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not any(_pid_alive(pid) for pid in pids):
            break
        time.sleep(0.1)

    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    for pid in pids:
        if not _pid_alive(pid):
            continue
        try:
            os.kill(pid, sigkill)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if not any(_pid_alive(pid) for pid in pids):
            return
        time.sleep(0.05)


def _classify_listeners(port: int) -> tuple[list[int], list[tuple[int, str]]]:
    """Split listeners on `port` into (ours, foreign). Empty lists if
    nothing is visible on the port anymore."""
    pids = _pids_listening_on(port)
    ours: list[int] = []
    foreign: list[tuple[int, str]] = []
    for pid in pids:
        cmdline = _process_cmdline(pid) or f"pid {pid} (command unavailable)"
        cwd = _process_cwd(pid)
        if _is_our_server_process(cmdline, cwd):
            ours.append(pid)
        else:
            foreign.append((pid, cmdline))
    return ours, foreign


def _refuse_foreign(port: int, foreign: list[tuple[int, str]]) -> None:
    details = "; ".join(f"pid {pid}: {cmd}" for pid, cmd in foreign)
    print(
        f"Port {port} is in use by another program ({details}).\n"
        "Stop that program, or start the server on a different port.",
        file=sys.stderr,
    )
    sys.exit(1)


def stop_our_server(port: int = PORT, *, quiet_if_idle: bool = False, refuse_foreign: bool = True) -> bool:
    """Stop this app if it owns `port`. Returns True if a process was
    stopped. A different program on the port is reported and left running
    (non-zero exit) when refuse_foreign is True. Nothing listening: return False."""
    pids = _pids_listening_on(port)
    if not pids:
        if _bind_fails(port):
            if refuse_foreign:
                print(
                    f"Port {port} is in use, but the process could not be identified.\n"
                    "Stop the program using that port, or start the server on a different port.",
                    file=sys.stderr,
                )
                sys.exit(1)
            return False
        if not quiet_if_idle:
            print(f"No server listening on port {port}")
        return False

    ours, foreign = _classify_listeners(port)
    if foreign:
        if refuse_foreign:
            _refuse_foreign(port, foreign)
        return False
    if not ours:
        if not quiet_if_idle:
            print(f"No server listening on port {port}")
        return False

    _stop_pids(ours)
    deadline = time.time() + 3.0
    while _pids_listening_on(port) and time.time() < deadline:
        time.sleep(0.1)
    leftover = _pids_listening_on(port)
    if leftover:
        print(
            f"Stopped previous server (pid {', '.join(str(p) for p in ours)}) on port {port}, "
            f"but the port is still in use (pid {', '.join(str(p) for p in leftover)}).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Stopped previous server (pid {', '.join(str(p) for p in ours)}) on port {port}")
    return True


def ensure_port_available(port: int = PORT) -> None:
    """If this app already owns `port`, stop that process so we can bind
    again. A different program on the port is reported and left running."""
    stop_our_server(port, quiet_if_idle=True)


def pick_listen_port(preferred: int, *, tries: int = LISTEN_PORT_TRIES) -> int:
    """Bind probe from `preferred` upward. Stops a leftover PortableAI on
    each candidate; skips a foreign occupant instead of crashing."""
    preferred = int(preferred)
    last = preferred + max(1, int(tries))
    for port in range(preferred, last):
        stop_our_server(port, quiet_if_idle=True, refuse_foreign=False)
        if _bind_fails(port):
            continue
        if port != preferred:
            print(
                f"Port {preferred} was already in use -- PortableAI is running on {port} instead."
            )
        set_listen_port(port)
        return port
    print(
        f"Ports {preferred}–{last - 1} are all in use. Stop one of those programs, or pass --port.",
        file=sys.stderr,
    )
    sys.exit(1)


def print_listen_info(port: int = PORT) -> None:
    lan = _get_lan_ip()
    print(f"\n{APP_NAME} v{APP_VERSION} running.")
    print(f"  On this machine:  http://localhost:{port}")
    print(f"  Data:             {DATA_DIR}")
    if lan["detected"]:
        print(f"  On your phone:    http://{lan['ip']}:{port}   (same WiFi/hotspot)")
    else:
        print("  WARNING: could not detect a LAN IP address on this machine.")
        print("  Phones will not be able to reach this server until that's fixed --")
        print("  check that a network interface is actually up (WiFi connected, or")
        print("  hotspot started), then restart the server.")


def run_app(port: int = PORT) -> None:
    """Bind all interfaces so phones on the LAN can pair. --stop/--restart
    still identify this process by whatever is listening on `port`."""
    _startup_check_chat_db()
    print_listen_info(port)
    # First run on Windows/macOS may prompt a firewall permission dialog
    # the moment this binds to a non-loopback interface; that's expected
    # and needs to be allowed for phone pairing to work.
    app.run(host=BIND_HOST, port=port, debug=False, threaded=True)


DIRECT_LAUNCH_MESSAGE = (
    "Do not start PortableAI with `python ui/server.py`.\n"
    "\n"
    "That path starts Flask only and skips the bundled Ollama on Linux,\n"
    "so the app is not self-contained.\n"
    "\n"
    "Use this instead:\n"
    "\n"
    "    python run.py\n"
)


def refuse_direct_launch() -> None:
    """`python ui/server.py` is not a supported start path. Use `python run.py`."""
    print(DIRECT_LAUNCH_MESSAGE, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    refuse_direct_launch()
