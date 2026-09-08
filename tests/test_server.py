import json
import sys
import time
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
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["ollama_available"] is False
    assert data["base_url"] == "http://localhost:11434"


@patch("server.OllamaClient")
def test_api_status_reports_available(mock_cls, client):
    mock_cls.return_value.is_available.return_value = True
    mock_cls.return_value.base_url = "http://localhost:11434"
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.get_json()["ollama_available"] is True


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


def test_ipv4_mapped_loopback_bypasses_auth(client):
    resp = client.get("/api/personas", environ_overrides={"REMOTE_ADDR": "::ffff:127.0.0.1"})
    assert resp.status_code == 200


@patch("server._enumerate_lan_ips", return_value=["192.168.1.134"])
def test_lan_ip_including_servers_own_requires_pairing(mock_enum, client):
    """Opening the UI at http://192.168.1.134:5050 is a remote client,
    even when that address belongs to this machine. Pairing is required."""
    env = {"REMOTE_ADDR": "192.168.1.134"}
    for path in ("/api/personas", "/api/models", "/api/status", "/api/settings"):
        resp = client.get(path, environ_overrides=env)
        assert resp.status_code == 401, path
        assert resp.get_json()["error"] == "pairing required"


@patch("server._enumerate_lan_ips", return_value=["192.168.1.134"])
def test_lan_ip_cannot_read_admin_pairing_pin(mock_enum, client):
    resp = client.get("/api/pairing/pin", environ_overrides={"REMOTE_ADDR": "192.168.1.134"})
    assert resp.status_code == 403


def test_claim_pin_then_token_unlocks_personas(client, app):
    pin = client.get("/api/pairing/pin").get_json()["pin"]
    claim = client.post(
        "/api/pairing/claim",
        data=json.dumps({"pin": pin, "device_name": "LAN browser"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
    )
    assert claim.status_code == 200
    token = claim.get_json()["device_token"]
    resp = client.get(
        "/api/personas",
        environ_overrides=LAN_ENV,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.get_json()}
    assert "eli5-explainer" in ids


def test_index_includes_pairing_gate_and_sidebar_toggle(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="pairingGate"' in html
    assert 'id="sidebarToggle"' in html
    assert 'id="menuBtn"' in html


def test_lan_request_without_token_is_rejected(client):
    resp = client.get("/api/personas", environ_overrides=LAN_ENV)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "pairing required"


@patch("server._enumerate_lan_ips", return_value=["192.168.1.134"])
def test_foreign_lan_ip_still_requires_pairing_for_status_and_models(mock_enum, client):
    for path in ("/api/status", "/api/models", "/api/personas", "/api/settings"):
        resp = client.get(path, environ_overrides=LAN_ENV)
        assert resp.status_code == 401, path
        assert resp.get_json()["error"] == "pairing required"


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


def test_pairing_regenerate_rejects_lan(client):
    resp = client.post("/api/pairing/pin/regenerate", environ_overrides=LAN_ENV)
    assert resp.status_code == 403


def test_pairing_pin_endpoint_works_from_localhost(client):
    resp = client.get("/api/pairing/pin")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "pin" in data
    assert len(data["pin"]) == 6 and data["pin"].isdigit()
    assert isinstance(data["expires_at"], (int, float))
    assert data["expires_at"] > time.time()


def test_pairing_pin_regenerate_issues_new_pin_with_expiry(client):
    first = client.get("/api/pairing/pin").get_json()
    resp = client.post("/api/pairing/pin/regenerate")
    assert resp.status_code == 200
    second = resp.get_json()
    assert second["pin"] != first["pin"]
    assert isinstance(second["expires_at"], (int, float))
    assert second["expires_at"] > time.time()
    # GET reflects the regenerated PIN
    again = client.get("/api/pairing/pin").get_json()
    assert again["pin"] == second["pin"]


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


# ---------- Per-device data isolation ----------


def _pair_device(app, name):
    pin = pairing_store.generate_pin(app.PAIRING_FILE)
    return pairing_store.claim_pin(app.PAIRING_FILE, pin, name)


def _lan_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_two_devices_do_not_see_each_others_conversations(client, app):
    token_a = _pair_device(app, "Phone A")
    token_b = _pair_device(app, "Phone B")

    conv_a = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_a),
    ).get_json()["id"]

    conv_b = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_b),
    ).get_json()["id"]

    list_a = client.get(
        "/api/conversations", environ_overrides=LAN_ENV, headers=_lan_headers(token_a)
    ).get_json()
    list_b = client.get(
        "/api/conversations", environ_overrides=LAN_ENV, headers=_lan_headers(token_b)
    ).get_json()

    assert {c["id"] for c in list_a} == {conv_a}
    assert {c["id"] for c in list_b} == {conv_b}


def test_device_cannot_read_another_devices_conversation(client, app):
    token_a = _pair_device(app, "Phone A")
    token_b = _pair_device(app, "Phone B")

    conv_a = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_a),
    ).get_json()["id"]

    resp = client.get(
        f"/api/conversations/{conv_a}", environ_overrides=LAN_ENV, headers=_lan_headers(token_b)
    )
    assert resp.status_code == 404  # not 403 -- doesn't confirm the ID even exists


def test_device_cannot_rename_archive_or_delete_anothers_conversation(client, app):
    token_a = _pair_device(app, "Phone A")
    token_b = _pair_device(app, "Phone B")

    conv_a = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_a),
    ).get_json()["id"]

    rename_resp = client.patch(
        f"/api/conversations/{conv_a}",
        data=json.dumps({"title": "hijacked"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_b),
    )
    assert rename_resp.status_code == 404

    archive_resp = client.post(
        f"/api/conversations/{conv_a}/archive",
        data=json.dumps({"archived": True}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_b),
    )
    assert archive_resp.status_code == 404

    delete_resp = client.delete(
        f"/api/conversations/{conv_a}", environ_overrides=LAN_ENV, headers=_lan_headers(token_b)
    )
    assert delete_resp.status_code == 404

    # Confirm it's genuinely untouched -- owner can still read it normally.
    still_there = client.get(
        f"/api/conversations/{conv_a}", environ_overrides=LAN_ENV, headers=_lan_headers(token_a)
    )
    assert still_there.status_code == 200
    assert still_there.get_json()["title"] is None


def test_admin_localhost_sees_every_devices_conversations(client, app):
    token_a = _pair_device(app, "Phone A")
    token_b = _pair_device(app, "Phone B")

    conv_a = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_a),
    ).get_json()["id"]
    conv_b = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "eli5-explainer"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_b),
    ).get_json()["id"]

    # No environ_overrides here -- default test client is localhost (admin).
    admin_list = client.get("/api/conversations").get_json()
    assert {conv_a, conv_b}.issubset({c["id"] for c in admin_list})

    # Admin can also read either device's conversation directly.
    assert client.get(f"/api/conversations/{conv_a}").status_code == 200
    assert client.get(f"/api/conversations/{conv_b}").status_code == 200


def test_admin_created_conversation_has_local_owner_id(client):
    conv_id = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
    ).get_json()["id"]
    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    assert conv["owner_id"] == "local"


@patch("server.OllamaClient")
def test_chat_from_lan_device_creates_conversation_owned_by_that_device(mock_cls, client, app):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.chat.return_value = "here's my answer"
    token = _pair_device(app, "Phone A")

    resp = client.post(
        "/api/chat",
        data=json.dumps({"persona": "no-nonsense-mentor", "message": "hi"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token),
    )
    conv_id = resp.get_json()["conversation_id"]

    # The owning device can keep chatting in it.
    resp2 = client.post(
        "/api/chat",
        data=json.dumps(
            {"persona": "no-nonsense-mentor", "message": "follow-up", "conversation_id": conv_id}
        ),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token),
    )
    assert resp2.status_code == 200

    # A different device cannot piggyback on someone else's conversation_id.
    other_token = _pair_device(app, "Phone B")
    resp3 = client.post(
        "/api/chat",
        data=json.dumps(
            {"persona": "no-nonsense-mentor", "message": "sneaky", "conversation_id": conv_id}
        ),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(other_token),
    )
    assert resp3.status_code == 404


def test_export_conversation_respects_ownership(client, app):
    token_a = _pair_device(app, "Phone A")
    token_b = _pair_device(app, "Phone B")
    conv_a = client.post(
        "/api/conversations",
        data=json.dumps({"persona": "no-nonsense-mentor"}),
        content_type="application/json",
        environ_overrides=LAN_ENV,
        headers=_lan_headers(token_a),
    ).get_json()["id"]

    own_export = client.get(
        f"/api/conversations/{conv_a}/export", environ_overrides=LAN_ENV, headers=_lan_headers(token_a)
    )
    assert own_export.status_code == 200
    assert own_export.get_json()["id"] == conv_a

    other_export = client.get(
        f"/api/conversations/{conv_a}/export", environ_overrides=LAN_ENV, headers=_lan_headers(token_b)
    )
    assert other_export.status_code == 404


# ---------- LAN IP detection (the fixed version) ----------


@patch("server.subprocess.run")
def test_enumerate_lan_ips_parses_hostname_dash_i(mock_run, app):
    mock_run.return_value = MagicMock(returncode=0, stdout="192.168.1.134 172.17.0.1\n")
    ips = app._enumerate_lan_ips()
    assert ips == ["192.168.1.134", "172.17.0.1"]


@patch("server.subprocess.run")
def test_enumerate_lan_ips_filters_loopback(mock_run, app):
    mock_run.return_value = MagicMock(returncode=0, stdout="127.0.0.1 192.168.1.134\n")
    ips = app._enumerate_lan_ips()
    assert "127.0.0.1" not in ips
    assert "192.168.1.134" in ips


@patch("server.subprocess.run")
def test_enumerate_lan_ips_falls_back_to_ip_command(mock_run, app):
    def side_effect(cmd, **kwargs):
        if cmd[0] == "hostname":
            return MagicMock(returncode=0, stdout="")  # nothing found
        if cmd[0] == "ip":
            return MagicMock(
                returncode=0,
                stdout=json.dumps(
                    [{"addr_info": [{"local": "10.42.0.1"}, {"local": "127.0.0.1"}]}]
                ),
            )
        raise FileNotFoundError

    mock_run.side_effect = side_effect
    ips = app._enumerate_lan_ips()
    assert ips == ["10.42.0.1"]


@patch("server.subprocess.run")
def test_enumerate_lan_ips_empty_when_both_commands_fail(mock_run, app):
    mock_run.side_effect = FileNotFoundError
    assert app._enumerate_lan_ips() == []


@patch("server._enumerate_lan_ips")
def test_get_lan_ip_prefers_hotspot_subnet(mock_enum, app):
    mock_enum.return_value = ["192.168.1.134", "10.42.0.1"]
    result = app._get_lan_ip()
    assert result == {"ip": "10.42.0.1", "detected": True}


@patch("server._enumerate_lan_ips")
def test_get_lan_ip_uses_first_candidate_when_no_hotspot_match(mock_enum, app):
    mock_enum.return_value = ["192.168.1.134"]
    result = app._get_lan_ip()
    assert result == {"ip": "192.168.1.134", "detected": True}


@patch("server._enumerate_lan_ips")
def test_get_lan_ip_reports_undetected_when_nothing_found(mock_enum, app):
    mock_enum.return_value = []
    result = app._get_lan_ip()
    assert result == {"ip": "127.0.0.1", "detected": False}


def test_build_pairing_uri_shape(app):
    uri = app._build_pairing_uri("192.168.1.134", 5050, "815010", 1732650000)
    assert uri.startswith("portableai://pair?")
    assert "ip=192.168.1.134" in uri
    assert "port=5050" in uri
    assert "pin=815010" in uri
    assert "exp=1732650000" in uri


def test_build_pairing_web_url_shape(app):
    url = app._build_pairing_web_url("192.168.1.134", 5050, "815010", 1732650000)
    assert url.startswith("http://192.168.1.134:5050/?")
    assert "pair_pin=815010" in url
    assert "exp=1732650000" in url


# ---------- Pairing PIN response includes QR-ready fields ----------


@patch("server._get_lan_ip")
def test_pairing_pin_response_includes_uri_and_detection_flag(mock_lan, client):
    mock_lan.return_value = {"ip": "192.168.1.134", "detected": True}
    resp = client.get("/api/pairing/pin")
    data = resp.get_json()
    assert data["lan_ip"] == "192.168.1.134"
    assert data["lan_ip_detected"] is True
    assert data["lan_url"] == "http://192.168.1.134:5050"
    assert "127.0.0.1" not in data["lan_url"]
    assert data["pairing_uri"].startswith("portableai://pair?")
    assert data["pairing_web_url"].startswith("http://192.168.1.134:5050/?pair_pin=")


def test_bind_host_is_all_interfaces(app):
    assert app.BIND_HOST == "0.0.0.0"


def test_normalize_and_loopback_helpers(app):
    assert app._normalize_ip("::ffff:127.0.0.1") == "127.0.0.1"
    assert app._normalize_ip("[::1]") == "::1"
    assert app._is_loopback_ip("127.0.0.1")
    assert app._is_loopback_ip("::1")
    assert app._is_loopback_ip("::ffff:127.0.0.1")
    assert not app._is_loopback_ip("192.168.1.50")


def test_is_local_client_only_loopback(app):
    assert app._is_local_client("127.0.0.1") is True
    assert app._is_local_client("::1") is True
    assert app._is_local_client("::ffff:127.0.0.1") is True
    assert app._is_local_client("192.168.1.134") is False
    assert app._is_local_client("192.168.1.50") is False


def test_is_our_server_process_matches_run_py(app):
    assert app._is_our_server_process("./venv/bin/python run.py --restart")
    assert app._is_our_server_process("python ui/server.py")
    assert not app._is_our_server_process("python other.py")
    assert not app._is_our_server_process("nginx")


def test_is_our_server_process_server_py_only_from_ui_dir(app):
    ui_dir = str(Path(app.ROOT) / "ui")
    assert app._is_our_server_process("python server.py", cwd=ui_dir)
    assert not app._is_our_server_process("python server.py", cwd="/tmp")


@patch("server._pids_listening_on", return_value=[])
@patch("server._bind_fails", return_value=False)
def test_stop_our_server_idle_returns_false(mock_bind, mock_pids, app):
    assert app.stop_our_server(5050, quiet_if_idle=True) is False


@patch("server._get_lan_ip")
def test_pairing_pin_flags_undetected_ip(mock_lan, client):
    mock_lan.return_value = {"ip": "127.0.0.1", "detected": False}
    resp = client.get("/api/pairing/pin")
    assert resp.get_json()["lan_ip_detected"] is False


def test_qr_endpoint_is_localhost_only(client):
    resp = client.get("/api/pairing/qr.svg", environ_overrides=LAN_ENV)
    assert resp.status_code == 403


def test_qr_endpoint_returns_svg_from_localhost(client):
    resp = client.get("/api/pairing/qr.svg")
    assert resp.status_code == 200
    assert resp.content_type.startswith("image/svg+xml")
    assert b"<svg" in resp.data or b"<?xml" in resp.data


@patch("qrcode.make")
def test_qr_endpoint_encodes_web_url_not_deep_link(mock_make, client):
    """Regression test for the real-hardware finding that portableai://
    shows 'no user data found' with no app installed to claim it -- the
    QR must encode the plain http:// pairing URL instead."""
    import qrcode.image.svg

    mock_make.return_value = qrcode.make("http://placeholder", image_factory=qrcode.image.svg.SvgPathImage)
    client.get("/api/pairing/qr.svg")
    encoded_data = mock_make.call_args[0][0]
    assert encoded_data.startswith("http://")
    assert "pair_pin=" in encoded_data
    assert not encoded_data.startswith("portableai://")# ---------- Model catalog / download ----------


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
