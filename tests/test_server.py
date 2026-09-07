import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

import conversation_store  # noqa: E402
import pairing_store  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Import server fresh with DATA_DIR/LOG_FILE/SETTINGS_FILE/DB_FILE
    redirected to a temp dir, so tests never touch the real data/ folder."""
    import server as server_module

    monkeypatch.setattr(server_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server_module, "LOG_FILE", tmp_path / "logs.jsonl")
    monkeypatch.setattr(server_module, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server_module, "DB_FILE", tmp_path / "chats.db")
    monkeypatch.setattr(server_module, "PAIRING_FILE", tmp_path / "pairing.json")
    server_module._built_personas.clear()
    server_module.app.config.update(TESTING=True)
    return server_module


@pytest.fixture
def client(app):
    return app.app.test_client()


def test_index_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"PortableAI" in resp.data


def test_logo_svg_is_served(client):
    resp = client.get("/logo.svg")
    assert resp.status_code == 200
    assert b"<svg" in resp.data


def test_api_personas_lists_bundled_personas(client):
    resp = client.get("/api/personas")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.get_json()}
    assert {"no-nonsense-mentor", "eli5-explainer"}.issubset(ids)


def test_settings_roundtrip(client):
    resp = client.post(
        "/api/settings",
        data=json.dumps({"base_url": "http://example.com:11434"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["base_url"] == "http://example.com:11434"

    resp = client.get("/api/settings")
    assert resp.get_json()["base_url"] == "http://example.com:11434"


def test_settings_ignores_unknown_keys(client):
    resp = client.post(
        "/api/settings",
        data=json.dumps({"not_a_real_setting": "x"}),
        content_type="application/json",
    )
    assert "not_a_real_setting" not in resp.get_json()


@patch("server.OllamaClient")
def test_api_status_reports_unavailable(mock_cls, client):
    mock_cls.return_value.is_available.return_value = False
    mock_cls.return_value.base_url = "http://localhost:11434"
    resp = client.get("/api/status")
    assert resp.get_json()["ollama_available"] is False


@patch("server.OllamaClient")
def test_chat_requires_persona_and_message(mock_cls, client):
    resp = client.post("/api/chat", data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 400


@patch("server.OllamaClient")
def test_chat_503_when_ollama_down(mock_cls, client):
    mock_cls.return_value.is_available.return_value = False
    resp = client.post(
        "/api/chat",
        data=json.dumps({"persona": "no-nonsense-mentor", "message": "hi"}),
        content_type="application/json",
    )
    assert resp.status_code == 503


@patch("server.OllamaClient")
def test_chat_happy_path_builds_persona_once_and_logs(mock_cls, client, app):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.chat.return_value = "Ship it. Next: write the migration test."

    body = json.dumps({"persona": "no-nonsense-mentor", "message": "Should I deploy on Friday?"})
    resp = client.post("/api/chat", data=body, content_type="application/json")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reply"] == "Ship it. Next: write the migration test."
    conversation_id = data["conversation_id"]
    assert conversation_id
    instance.create_model.assert_called_once()  # persona built exactly once

    # A second message in the SAME conversation should not rebuild the persona.
    body2 = json.dumps(
        {"persona": "no-nonsense-mentor", "message": "And staging?", "conversation_id": conversation_id}
    )
    resp2 = client.post("/api/chat", data=body2, content_type="application/json")
    assert resp2.status_code == 200
    assert resp2.get_json()["conversation_id"] == conversation_id
    instance.create_model.assert_called_once()

    logs = app._read_logs()
    assert len(logs) == 2
    assert logs[0]["reply"] == "Ship it. Next: write the migration test."

    # Conversation should now hold 4 messages (2 user, 2 assistant).
    conn = app._db()
    conv = conversation_store.get_conversation(conn, conversation_id)
    conn.close()
    assert len(conv["messages"]) == 4
    assert conv["title"] == "Should I deploy on Friday?"


@patch("server.OllamaClient")
def test_chat_unknown_persona_returns_500(mock_cls, client):
    mock_cls.return_value.is_available.return_value = True
    resp = client.post(
        "/api/chat",
        data=json.dumps({"persona": "does-not-exist", "message": "hi"}),
        content_type="application/json",
    )
    assert resp.status_code == 500


@patch("server.OllamaClient")
def test_chat_with_model_override_builds_named_variant(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.chat.return_value = "ok"

    body = json.dumps(
        {"persona": "no-nonsense-mentor", "message": "hi", "model_override": "qwen2.5:0.5b"}
    )
    resp = client.post("/api/chat", data=body, content_type="application/json")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["model_used"] == "no-nonsense-mentor--qwen2.5-0.5b"

    payload = instance.create_model.call_args[0][0]
    assert payload["model"] == "no-nonsense-mentor--qwen2.5-0.5b"
    assert payload["from"] == "qwen2.5:0.5b"

    # chat() should be called against the variant name, not the persona name
    call_args = instance.chat.call_args[0]
    assert call_args[0] == "no-nonsense-mentor--qwen2.5-0.5b"


@patch("server.OllamaClient")
def test_api_models_formats_size_and_reports_path(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.list_models.return_value = [
        {
            "name": "llama3.2:3b",
            "size": 2147483648,
            "modified_at": "2026-01-01T00:00:00Z",
            "details": {"quantization_level": "Q4_K_M", "parameter_size": "3B"},
        }
    ]
    resp = client.get("/api/models")
    data = resp.get_json()
    assert data["models"][0]["name"] == "llama3.2:3b"
    assert data["models"][0]["size_human"] == "2.0 GB"
    assert data["models"][0]["quantization"] == "Q4_K_M"
    assert "models_path_hint" in data and data["models_path_hint"]


@patch("server.OllamaClient")
def test_api_models_returns_empty_when_ollama_down(mock_cls, client):
    mock_cls.return_value.is_available.return_value = False
    resp = client.get("/api/models")
    assert resp.status_code == 200
    assert resp.get_json()["models"] == []


def test_logs_empty_then_populated(client, app):
    resp = client.get("/api/logs")
    assert resp.get_json() == []

    app._log({"persona": "no-nonsense-mentor", "message": "hi", "reply": "yo"})
    resp = client.get("/api/logs")
    entries = resp.get_json()
    assert len(entries) == 1
    assert entries[0]["message"] == "hi"


def test_clear_logs(client, app):
    app._log({"persona": "no-nonsense-mentor", "message": "hi", "reply": "yo"})
    resp = client.delete("/api/logs")
    assert resp.get_json()["cleared"] is True
    assert client.get("/api/logs").get_json() == []


# ---------- Conversation history: create/list/search/archive/delete ----------


def test_create_conversation_uses_persona_base_model_when_no_override(client):
    resp = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    conv_id = resp.get_json()["id"]

    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    assert conv["model_used"] == "llama3.2:3b"
    assert conv["messages"] == []


def test_create_conversation_unknown_persona_400(client):
    resp = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "does-not-exist"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_get_conversation_404_when_missing(client):
    resp = client.get("/api/conversations/does-not-exist")
    assert resp.status_code == 404


@patch("server.OllamaClient")
def test_chat_without_conversation_id_creates_one_and_history_persists(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.chat.return_value = "Use REST unless you need federated queries."

    resp = client.post(
        "/api/chat",
        data=json.dumps({"persona": "no-nonsense-mentor", "message": "REST or GraphQL?"}),
        content_type="application/json",
    )
    conv_id = resp.get_json()["conversation_id"]

    listing = client.get("/api/conversations").get_json()
    assert any(c["id"] == conv_id for c in listing)
    matched = next(c for c in listing if c["id"] == conv_id)
    assert matched["title"] == "REST or GraphQL?"


def test_rename_conversation(client):
    conv_id = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
    ).get_json()["id"]

    resp = client.patch(
        f"/api/conversations/{conv_id}",
        data=json.dumps({"title": "My renamed chat"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    assert conv["title"] == "My renamed chat"


def test_rename_conversation_requires_nonempty_title(client):
    conv_id = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
    ).get_json()["id"]

    resp = client.patch(
        f"/api/conversations/{conv_id}",
        data=json.dumps({"title": "   "}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_archive_and_unarchive_conversation(client):
    conv_id = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
    ).get_json()["id"]

    client.post(f"/api/conversations/{conv_id}/archive", data=json.dumps({"archived": True}), content_type="application/json")
    active = client.get("/api/conversations?archived=0").get_json()
    archived = client.get("/api/conversations?archived=1").get_json()
    assert not any(c["id"] == conv_id for c in active)
    assert any(c["id"] == conv_id for c in archived)

    client.post(f"/api/conversations/{conv_id}/archive", data=json.dumps({"archived": False}), content_type="application/json")
    active = client.get("/api/conversations?archived=0").get_json()
    assert any(c["id"] == conv_id for c in active)


def test_delete_conversation(client):
    conv_id = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
    ).get_json()["id"]

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.get_json()["deleted"] is True
    assert client.get(f"/api/conversations/{conv_id}").status_code == 404


def test_search_conversations_by_query_param(client):
    conv_id = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "eli5-explainer"}),
        content_type="application/json",
    ).get_json()["id"]
    import server as server_module

    conn = server_module._db()
    conversation_store.add_message(conn, conv_id, "user", "What is a container?")
    conn.close()

    hits = client.get("/api/conversations?q=container").get_json()
    assert any(c["id"] == conv_id for c in hits)

    misses = client.get("/api/conversations?q=kubernetes").get_json()
    assert not any(c["id"] == conv_id for c in misses)


# ---------- LAN pairing / auth gate ----------

LAN_ENV = {"REMOTE_ADDR": "192.168.1.50"}


def test_health_endpoint_open_to_lan(client):
    resp = client.get("/api/health", environ_overrides=LAN_ENV)
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_localhost_bypasses_auth_entirely(client):
    # default test client REMOTE_ADDR is 127.0.0.1 -- no token needed
    resp = client.get("/api/personas")
    assert resp.status_code == 200


def test_lan_request_without_token_is_rejected(client):
    resp = client.get("/api/personas", environ_overrides=LAN_ENV)
    assert resp.status_code == 401


def test_lan_request_with_valid_token_is_allowed(client, app):
    pin = pairing_store.generate_pin(app.PAIRING_FILE)
    token = pairing_store.claim_pin(app.PAIRING_FILE, pin, "Test iPhone")

    resp = client.get(
        "/api/personas",
        environ_overrides=LAN_ENV,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_lan_request_with_bogus_token_is_rejected(client):
    resp = client.get(
        "/api/personas",
        environ_overrides=LAN_ENV,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_pairing_pin_endpoint_rejects_lan(client):
    resp = client.get("/api/pairing/pin", environ_overrides=LAN_ENV)
    assert resp.status_code == 403


def test_pairing_pin_endpoint_works_from_localhost(client):
    resp = client.get("/api/pairing/pin")
    assert resp.status_code == 200
    assert "pin" in resp.get_json()


def test_pairing_devices_endpoint_rejects_lan(client):
    resp = client.get("/api/pairing/devices", environ_overrides=LAN_ENV)
    assert resp.status_code == 403


def test_pairing_claim_works_from_lan(client):
    pin_resp = client.get("/api/pairing/pin")  # localhost: generates/returns current pin
    pin = pin_resp.get_json()["pin"]

    resp = client.post(
        "/api/pairing/claim",
        data=json.dumps({"pin": pin, "device_name": "Kim's iPhone"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
    )
    assert resp.status_code == 200
    assert "device_token" in resp.get_json()


def test_lan_web_client_loads_personas_and_models_after_claim(client):
    """Phone/LAN browser: HTML is public, APIs 401 until claim, then Bearer works."""
    assert client.get("/", environ_overrides=LAN_ENV).status_code == 200
    assert client.get("/api/personas", environ_overrides=LAN_ENV).status_code == 401
    assert client.get("/api/models", environ_overrides=LAN_ENV).status_code == 401

    pin = client.get("/api/pairing/pin").get_json()["pin"]
    claim = client.post(
        "/api/pairing/claim",
        data=json.dumps({"pin": pin, "device_name": "Phone browser"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
    )
    assert claim.status_code == 200
    token = claim.get_json()["device_token"]
    headers = {"Authorization": f"Bearer {token}"}

    personas = client.get("/api/personas", environ_overrides=LAN_ENV, headers=headers)
    models = client.get("/api/models", environ_overrides=LAN_ENV, headers=headers)
    assert personas.status_code == 200
    assert isinstance(personas.get_json(), list)
    assert len(personas.get_json()) >= 1
    assert models.status_code == 200
    assert "models" in models.get_json()


def test_pairing_claim_wrong_pin_from_lan_is_401(client):
    resp = client.post(
        "/api/pairing/claim",
        data=json.dumps({"pin": "000000", "device_name": "Attacker"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
    )
    assert resp.status_code == 401


def test_pairing_claim_requires_pin_field(client):
    resp = client.post(
        "/api/pairing/claim",
        data=json.dumps({"device_name": "No pin given"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
    )
    assert resp.status_code == 400


def test_paired_device_appears_in_devices_list_and_can_be_revoked(client, app):
    pin = pairing_store.generate_pin(app.PAIRING_FILE)
    token = pairing_store.claim_pin(app.PAIRING_FILE, pin, "Kim's iPhone")

    devices = client.get("/api/pairing/devices").get_json()
    assert any(d["token"] == token for d in devices)

    resp = client.delete(f"/api/pairing/devices/{token}")
    assert resp.get_json()["revoked"] is True

    # Now that same LAN request should be rejected again.
    lan_resp = client.get(
        "/api/personas",
        environ_overrides=LAN_ENV,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert lan_resp.status_code == 401


# ---------- Model catalog / download ----------


@patch("server.OllamaClient")
def test_catalog_marks_installed_models(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.list_models.return_value = [{"name": "llama3.2:3b"}]

    catalog = client.get("/api/models/catalog").get_json()
    assert len(catalog) > 0
    by_name = {m["name"]: m for m in catalog}
    assert by_name["llama3.2:3b"]["installed"] is True
    # something in the curated catalog that almost certainly isn't installed
    assert by_name["deepseek-r1:7b"]["installed"] is False


@patch("server.OllamaClient")
def test_catalog_still_returns_entries_when_ollama_down(mock_cls, client):
    mock_cls.return_value.is_available.return_value = False
    catalog = client.get("/api/models/catalog").get_json()
    assert len(catalog) > 0
    assert all(entry["installed"] is False for entry in catalog)


@patch("server.OllamaClient")
def test_pull_model_requires_name(mock_cls, client):
    resp = client.post("/api/models/pull", data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 400


@patch("server.OllamaClient")
def test_pull_model_503_when_ollama_down(mock_cls, client):
    mock_cls.return_value.is_available.return_value = False
    resp = client.post(
        "/api/models/pull",
        data=json.dumps({"name": "qwen2.5:0.5b"}),
        content_type="application/json",
    )
    assert resp.status_code == 503


@patch("server.OllamaClient")
def test_pull_model_happy_path(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.pull_model.return_value = None

    resp = client.post(
        "/api/models/pull",
        data=json.dumps({"name": "qwen2.5:0.5b"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["pulled"] == "qwen2.5:0.5b"
    instance.pull_model.assert_called_once_with("qwen2.5:0.5b")


@patch("server.OllamaClient")
def test_pull_model_propagates_ollama_error(mock_cls, client):
    from ollama_client import OllamaError

    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.pull_model.side_effect = OllamaError("model not found")

    resp = client.post(
        "/api/models/pull",
        data=json.dumps({"name": "not-a-real-model"}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert "not found" in resp.get_json()["error"]


# ---------- Per-model update check (digest-diff via re-pull) ----------


@patch("server.OllamaClient")
def test_check_update_requires_name(mock_cls, client):
    resp = client.post("/api/models/check-update", data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 400


@patch("server.OllamaClient")
def test_check_update_503_when_ollama_down(mock_cls, client):
    mock_cls.return_value.is_available.return_value = False
    resp = client.post(
        "/api/models/check-update",
        data=json.dumps({"name": "llama3.2:3b"}),
        content_type="application/json",
    )
    assert resp.status_code == 503


@patch("server.OllamaClient")
def test_check_update_404_when_not_installed(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.list_models.return_value = [{"name": "llama3.2:3b", "digest": "sha1"}]
    resp = client.post(
        "/api/models/check-update",
        data=json.dumps({"name": "qwen2.5:0.5b"}),
        content_type="application/json",
    )
    assert resp.status_code == 404


@patch("server.OllamaClient")
def test_check_update_detects_digest_change(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.list_models.side_effect = [
        [{"name": "llama3.2:3b", "digest": "sha-old"}],
        [{"name": "llama3.2:3b", "digest": "sha-new"}],
    ]
    resp = client.post(
        "/api/models/check-update",
        data=json.dumps({"name": "llama3.2:3b"}),
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["updated"] is True
    assert data["digest_before"] == "sha-old"
    assert data["digest_after"] == "sha-new"
    instance.pull_model.assert_called_once_with("llama3.2:3b")


@patch("server.OllamaClient")
def test_check_update_reports_no_change(mock_cls, client):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.list_models.side_effect = [
        [{"name": "llama3.2:3b", "digest": "sha-same"}],
        [{"name": "llama3.2:3b", "digest": "sha-same"}],
    ]
    resp = client.post(
        "/api/models/check-update",
        data=json.dumps({"name": "llama3.2:3b"}),
        content_type="application/json",
    )
    assert resp.get_json()["updated"] is False


# ---------- Optional centralized app/catalog update check ----------


def test_updates_check_disabled_by_default(client):
    resp = client.get("/api/updates/check")
    assert resp.get_json() == {"enabled": False}


@patch("server.requests.get")
def test_updates_check_reports_available_updates(mock_get, client):
    client.post(
        "/api/settings",
        data=json.dumps({"update_check_url": "https://example.com/update-manifest.json"}),
        content_type="application/json",
    )
    mock_get.return_value = MagicMock(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {
            "app_version": "99.0.0",
            "catalog_version": "9999-01-01",
            "message": "New personas added",
            "notes_url": "https://example.com/notes",
        },
    )
    resp = client.get("/api/updates/check")
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["reachable"] is True
    assert data["app_update_available"] is True
    assert data["catalog_update_available"] is True
    assert data["message"] == "New personas added"


@patch("server.requests.get")
def test_updates_check_matching_versions_reports_no_update(mock_get, client, app):
    client.post(
        "/api/settings",
        data=json.dumps({"update_check_url": "https://example.com/update-manifest.json"}),
        content_type="application/json",
    )
    mock_get.return_value = MagicMock(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {"app_version": app.APP_VERSION, "catalog_version": app.CATALOG_VERSION},
    )
    resp = client.get("/api/updates/check")
    data = resp.get_json()
    assert data["app_update_available"] is False
    assert data["catalog_update_available"] is False


@patch("server.requests.get")
def test_updates_check_handles_unreachable_url(mock_get, client):
    import requests as real_requests

    client.post(
        "/api/settings",
        data=json.dumps({"update_check_url": "https://example.com/update-manifest.json"}),
        content_type="application/json",
    )
    mock_get.side_effect = real_requests.RequestException("timed out")
    resp = client.get("/api/updates/check")
    data = resp.get_json()
    assert data == {"enabled": True, "reachable": False}
