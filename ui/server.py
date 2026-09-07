"""
Minimal backend for the persona chat UI.

- Serves the static frontend (ui/static/).
- Proxies chat requests to a local Ollama instance via OllamaClient.
- Lazily builds each persona model in Ollama the first time it's used,
  optionally on a different base model (model_override) than the one in
  its .Modelfile -- built as a separate named variant so the original
  persona definition is never mutated.
- Lists installed Ollama models and a best-effort hint at where they live
  on disk, for the model selector / settings panel.
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
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ollama_client import OllamaClient, OllamaError  # noqa: E402
from persona_loader import ModelfileParseError, load_persona_file  # noqa: E402
import conversation_store as store  # noqa: E402
import pairing_store  # noqa: E402

PERSONAS_DIR = ROOT / "personas"
DATA_DIR = ROOT / "data"
LOG_FILE = DATA_DIR / "logs.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "chats.db"
PAIRING_FILE = DATA_DIR / "pairing.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"
CATALOG_FILE = Path(__file__).resolve().parent / "model_catalog.json"

DEFAULT_SETTINGS = {
    "base_url": "http://localhost:11434",
    "auto_build_on_startup": True,
    "update_check_url": "",       # empty = feature disabled, no network calls made
    "auto_check_updates": False,  # opt-in; manual "Check now" always works regardless
}

# Bump these when you change this repo / the curated catalog, and reflect
# the same values in whatever JSON you publish at your update_check_url.
APP_NAME = "PortableAI"
APP_VERSION = "0.4.0"
CATALOG_VERSION = "2026-09-06"

PORT = 5050

app = Flask(__name__, static_folder=None)

# Tracks which persona/model-variant names we've already `create`d in this
# process, so we don't re-POST /api/create on every single chat message.
_built_personas: set[str] = set()

# Admin-only pairing endpoints: viewing/regenerating the PIN and managing
# paired devices must never be reachable from the LAN, only from the
# machine physically running the server (checked by remote_addr below).
_ADMIN_ONLY_PATHS_PREFIX = "/api/pairing/devices"
_ADMIN_ONLY_PATHS = {"/api/pairing/pin", "/api/pairing/pin/regenerate"}

# A LAN device without a token yet must still be able to reach these to
# pair at all, or to do a basic reachability check before pairing.
_LAN_OPEN_PATHS = {"/api/pairing/claim", "/api/health"}


@app.before_request
def _check_auth():
    path = request.path
    if not path.startswith("/api/"):
        return None  # index page + static assets (js/css/svg): always public

    is_local = request.remote_addr in ("127.0.0.1", "::1")

    if path in _ADMIN_ONLY_PATHS or path.startswith(_ADMIN_ONLY_PATHS_PREFIX):
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


def _get_lan_ip() -> str:
    """Best-effort LAN IP for display in the pairing UI/console. The
    UDP connect() below never actually sends a packet -- it's a kernel
    routing-table lookup to find which local interface would be used to
    reach that address, so this works with no active network connection
    and contacts nothing on 8.8.8.8."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "app": APP_NAME, "app_version": APP_VERSION})


@app.route("/api/pairing/pin", methods=["GET"])
def api_pairing_pin():
    current = pairing_store.get_current_pin(PAIRING_FILE)
    if not current:
        pairing_store.generate_pin(PAIRING_FILE)
        current = pairing_store.get_current_pin(PAIRING_FILE)
    current["lan_url"] = f"http://{_get_lan_ip()}:{PORT}"
    return jsonify(current)


@app.route("/api/pairing/pin/regenerate", methods=["POST"])
def api_pairing_pin_regenerate():
    pin = pairing_store.generate_pin(PAIRING_FILE)
    return jsonify({"pin": pin})


@app.route("/api/pairing/claim", methods=["POST"])
def api_pairing_claim():
    body = request.get_json(force=True) or {}
    pin = (body.get("pin") or "").strip()
    device_name = (body.get("device_name") or "").strip()
    if not pin:
        return jsonify({"error": "pin is required"}), 400
    token = pairing_store.claim_pin(PAIRING_FILE, pin, device_name)
    if not token:
        return jsonify({"error": "invalid or expired PIN"}), 401
    return jsonify({"device_token": token})


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
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def _client() -> OllamaClient:
    return OllamaClient(base_url=_load_settings()["base_url"])


def _db() -> "sqlite3.Connection":
    DATA_DIR.mkdir(exist_ok=True)
    return store.connect(DB_FILE)


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
    """Ollama doesn't expose its storage path over the API, so this is a
    best-effort hint: the OLLAMA_MODELS env var if set, otherwise the
    documented per-OS default location."""
    import os

    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return env
    if sys.platform == "darwin":
        return "~/.ollama/models"
    if sys.platform.startswith("linux"):
        return "/usr/share/ollama/.ollama/models (or ~/.ollama/models for non-service installs)"
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


@app.route("/api/personas")
def api_personas():
    personas = []
    for path in _persona_files():
        name = _persona_name_from_path(path)
        try:
            persona = load_persona_file(path)
            personas.append(
                {
                    "id": name,
                    "base_model": persona.base_model,
                    "system_preview": (persona.system or "")[:160],
                    "parameters": persona.parameters,
                }
            )
        except ModelfileParseError as e:
            personas.append({"id": name, "error": str(e)})
    return jsonify(personas)


@app.route("/api/models")
def api_models():
    client = _client()
    if not client.is_available():
        return jsonify({"models": [], "models_path_hint": _models_path_hint(), "error": "Ollama not reachable"}), 200
    try:
        raw_models = client.list_models()
    except OllamaError as e:
        return jsonify({"error": str(e)}), 502

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
    return jsonify({"ollama_available": available, "base_url": client.base_url})


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
        except OllamaError:
            pass
    for entry in catalog:
        entry["installed"] = entry["name"] in installed_names
    return jsonify(catalog)


@app.route("/api/models/pull", methods=["POST"])
def api_models_pull():
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    client = _client()
    if not client.is_available():
        return jsonify({"error": "Ollama is not reachable. Run `ollama serve`."}), 503

    try:
        client.pull_model(name)
    except OllamaError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"pulled": name})


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
        return jsonify({"error": "Ollama is not reachable. Run `ollama serve`."}), 503

    try:
        before = {m.get("name"): m.get("digest") for m in client.list_models()}
    except OllamaError as e:
        return jsonify({"error": str(e)}), 502

    if name not in before:
        return jsonify({"error": f"'{name}' is not installed"}), 404
    digest_before = before[name]

    try:
        client.pull_model(name)
        after = {m.get("name"): m.get("digest") for m in client.list_models()}
    except OllamaError as e:
        return jsonify({"error": str(e)}), 502

    digest_after = after.get(name)
    return jsonify(
        {
            "name": name,
            "updated": digest_before != digest_after,
            "digest_before": digest_before,
            "digest_after": digest_after,
        }
    )


@app.route("/api/updates/check")
def api_updates_check():
    """Optional, centralized check for app/catalog updates -- entirely
    off by default. Only makes a network call if the person has filled in
    update_check_url in Settings, whether checking manually ("Check now")
    or via the separate auto_check_updates toggle. Never runs otherwise.

    Points at a JSON file the *user* chooses to publish/host, e.g. a raw
    GitHub URL for their own fork of this repo -- there's no Anthropic-run
    or otherwise pre-existing "central" update service baked in here.
    Expected shape: {"app_version": "...", "catalog_version": "...",
    "message": "...", "notes_url": "..."}
    """
    settings = _load_settings()
    url = (settings.get("update_check_url") or "").strip()
    if not url:
        return jsonify({"enabled": False})

    try:
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
            "app_update_available": bool(remote_app_version) and remote_app_version != APP_VERSION,
            "catalog_update_available": bool(remote_catalog_version) and remote_catalog_version != CATALOG_VERSION,
            "message": remote.get("message"),
            "notes_url": remote.get("notes_url"),
        }
    )


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(_load_settings())


@app.route("/api/settings", methods=["POST"])
def post_settings():
    body = request.get_json(force=True) or {}
    settings = _load_settings()
    for key in DEFAULT_SETTINGS:
        if key in body:
            settings[key] = body[key]
    _save_settings(settings)
    return jsonify(settings)


@app.route("/api/conversations", methods=["GET"])
def api_list_conversations():
    archived = request.args.get("archived") == "1"
    query = request.args.get("q") or None
    conn = _db()
    try:
        convs = store.list_conversations(conn, archived=archived, query=query)
    finally:
        conn.close()
    return jsonify(convs)


@app.route("/api/conversations", methods=["POST"])
def api_create_conversation():
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
        conv_id = store.create_conversation(conn, persona_name, base_model)
    finally:
        conn.close()
    return jsonify({"id": conv_id})


@app.route("/api/conversations/<conv_id>", methods=["GET"])
def api_get_conversation(conv_id):
    conn = _db()
    try:
        conv = store.get_conversation(conn, conv_id)
    finally:
        conn.close()
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    return jsonify(conv)


@app.route("/api/conversations/<conv_id>", methods=["PATCH"])
def api_rename_conversation(conv_id):
    body = request.get_json(force=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    conn = _db()
    try:
        store.rename_conversation(conn, conv_id, title)
    finally:
        conn.close()
    return jsonify({"id": conv_id, "title": title})


@app.route("/api/conversations/<conv_id>/archive", methods=["POST"])
def api_archive_conversation(conv_id):
    body = request.get_json(force=True) or {}
    archived = bool(body.get("archived", True))
    conn = _db()
    try:
        store.set_archived(conn, conv_id, archived)
    finally:
        conn.close()
    return jsonify({"id": conv_id, "archived": archived})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def api_delete_conversation(conv_id):
    conn = _db()
    try:
        store.delete_conversation(conn, conv_id)
    finally:
        conn.close()
    return jsonify({"deleted": True})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(force=True) or {}
    persona_name = body.get("persona")
    message = body.get("message", "")
    model_override = body.get("model_override") or None
    conversation_id = body.get("conversation_id") or None

    if not persona_name or not message:
        return jsonify({"error": "persona and message are required"}), 400

    client = _client()
    if not client.is_available():
        return jsonify({"error": "Ollama is not reachable. Run `ollama serve`."}), 503

    try:
        actual_model = _ensure_persona_built(client, persona_name, model_override)
    except (FileNotFoundError, OllamaError) as e:
        return jsonify({"error": f"could not build persona '{persona_name}': {e}"}), 500

    conn = _db()
    try:
        if not conversation_id:
            conversation_id = store.create_conversation(conn, persona_name, actual_model)

        prior = store.get_messages(conn, conversation_id)
        messages = [{"role": m["role"], "content": m["content"]} for m in prior]
        messages.append({"role": "user", "content": message})

        started = time.time()
        try:
            reply = client.chat(actual_model, messages)
        except OllamaError as e:
            _log(
                {
                    "conversation_id": conversation_id,
                    "persona": persona_name,
                    "model_used": actual_model,
                    "message": message,
                    "error": str(e),
                }
            )
            return jsonify({"error": str(e)}), 502
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


@app.route("/api/logs", methods=["GET"])
def api_get_logs():
    return jsonify(_read_logs())


@app.route("/api/logs", methods=["DELETE"])
def api_clear_logs():
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    return jsonify({"cleared": True})


if __name__ == "__main__":
    settings = _load_settings()
    if settings.get("auto_build_on_startup"):
        client = _client()
        if client.is_available():
            for path in _persona_files():
                name = _persona_name_from_path(path)
                try:
                    _ensure_persona_built(client, name)
                    print(f"Built persona: {name}")
                except (OllamaError, ModelfileParseError) as e:
                    print(f"Skipped building {name}: {e}")
        else:
            print("Ollama not reachable at startup -- personas will build lazily on first chat.")

    PORT_TO_USE = PORT
    lan_ip = _get_lan_ip()
    pin = pairing_store.generate_pin(PAIRING_FILE)
    print(f"\n{APP_NAME} v{APP_VERSION} running.")
    print(f"  On this machine:  http://localhost:{PORT_TO_USE}")
    print(f"  On your phone:    http://{lan_ip}:{PORT_TO_USE}   (same WiFi/hotspot)")
    print(f"  Pairing PIN:      {pin}   (valid 5 minutes -- enter once in the app)")
    print(f"  Regenerate anytime from Settings on the desktop UI.\n")

    # Bind to all interfaces, not just localhost, so phones on the same
    # network can reach it -- the pairing gate above is what makes that
    # safe. First run on Windows/macOS may prompt a firewall permission
    # dialog the moment this binds to a non-loopback interface; that's
    # expected and needs to be allowed for phone pairing to work.
    app.run(host="0.0.0.0", port=PORT_TO_USE, debug=False)
