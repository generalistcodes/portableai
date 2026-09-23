"""HTTP tests for persona CRUD, cards, verification gate, and chat reference."""
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

import pairing_store  # noqa: E402

LAN_ENV = {"REMOTE_ADDR": "192.168.1.50"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    import server as server_module

    personas_copy = tmp_path / "personas"
    shutil.copytree(ROOT / "personas", personas_copy)
    monkeypatch.setattr(server_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server_module, "LOG_FILE", tmp_path / "logs.jsonl")
    monkeypatch.setattr(server_module, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server_module, "THEME_FILE", tmp_path / "theme.json")
    monkeypatch.setattr(server_module, "DB_FILE", tmp_path / "chats.db")
    monkeypatch.setattr(server_module, "PAIRING_FILE", tmp_path / "pairing.json")
    monkeypatch.setattr(server_module, "PERSONAS_DIR", personas_copy)
    server_module._built_personas.clear()
    server_module.set_managed_ollama_base_url(None)
    server_module.app.config.update(TESTING=True)
    return server_module


@pytest.fixture
def client(app):
    return app.app.test_client()


def _lan_auth_headers(client, app, addr="192.168.1.77"):
    pin = pairing_store.generate_pin(app.PAIRING_FILE)
    token = pairing_store.claim_pin(app.PAIRING_FILE, pin, "LAN phone")
    return {"REMOTE_ADDR": addr}, {"Authorization": f"Bearer {token}"}

CREATE_BODY = {
    "display_name": "Field Notes",
    "base_model": "llama3.2:3b",
    "system_prompt": "You take terse field notes.",
    "is_default": False,
}


def _install(mock_cls, names=("llama3.2:3b",)):
    instance = mock_cls.return_value
    instance.is_available.return_value = True
    instance.list_models.return_value = [{"name": n} for n in names]
    return instance


@patch("server.OllamaClient")
def test_create_persona_writes_modelfile(mock_cls, client, app):
    _install(mock_cls)
    resp = client.post("/api/personas", data=json.dumps(CREATE_BODY), content_type="application/json")
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"] == "field-notes"
    assert data["display_name"] == "Field Notes"
    assert data["is_default"] is False
    assert data["cards"] == []
    assert (app.PERSONAS_DIR / "field-notes.Modelfile").exists()
    listed = {p["id"] for p in client.get("/api/personas").get_json()}
    assert "field-notes" in listed


@patch("server.OllamaClient")
def test_create_persona_rejects_uninstalled_base_model(mock_cls, client):
    _install(mock_cls, names=("llama3.2:3b",))
    body = {**CREATE_BODY, "base_model": "mistral:7b"}
    resp = client.post("/api/personas", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 400
    assert "not installed" in resp.get_json()["error"]


@patch("server.OllamaClient")
def test_create_persona_validation_failures(mock_cls, client):
    _install(mock_cls)
    cases = [
        ({**CREATE_BODY, "display_name": ""}, "display_name"),
        ({**CREATE_BODY, "system_prompt": ""}, "system_prompt"),
        ({**CREATE_BODY, "base_model": ""}, "base_model"),
        ({**CREATE_BODY, "display_name": "x" * 81}, "display_name"),
        ({**CREATE_BODY, "system_prompt": 'bad """ prompt'}, "system_prompt"),
    ]
    for body, needle in cases:
        resp = client.post("/api/personas", data=json.dumps(body), content_type="application/json")
        assert resp.status_code == 400, body
        assert needle in resp.get_json()["error"], resp.get_json()


@patch("server.OllamaClient")
def test_create_persona_rejects_verified_true_on_cards(mock_cls, client, app):
    """The bypass: verified:true on create must 400, not silently coerce."""
    _install(mock_cls)
    body = {
        **CREATE_BODY,
        "cards": [
            {
                "title": "Severe bleeding",
                "answer": "Apply pressure.",
                "verified": True,
                "verified_by": "sneaky",
                "verified_source": "none",
            }
        ],
    }
    resp = client.post("/api/personas", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 400
    assert "verified" in resp.get_json()["error"]
    assert not (app.PERSONAS_DIR / "field-notes.Modelfile").exists()
    assert not (app.PERSONAS_DIR / "field-notes.cards.json").exists()


@patch("server.OllamaClient")
def test_create_persona_cards_are_forced_unverified_when_omitted_flag(mock_cls, client):
    _install(mock_cls)
    body = {
        **CREATE_BODY,
        "cards": [{"title": "Severe bleeding", "answer": "PLACEHOLDER text."}],
    }
    resp = client.post("/api/personas", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 201
    cards = resp.get_json()["cards"]
    assert len(cards) == 1
    assert cards[0]["verified"] is False
    assert cards[0]["verified_by"] is None


@patch("server.OllamaClient")
def test_put_persona_does_not_apply_sneaked_cards(mock_cls, client, app):
    _install(mock_cls)
    created = client.post("/api/personas", data=json.dumps(CREATE_BODY), content_type="application/json").get_json()
    pid = created["id"]
    resp = client.put(
        f"/api/personas/{pid}",
        data=json.dumps(
            {
                "system_prompt": "Updated prompt.",
                "cards": [
                    {
                        "title": "Sneak",
                        "answer": "Verified-looking",
                        "verified": True,
                        "verified_by": "nope",
                        "verified_source": "nope",
                    }
                ],
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["system_prompt"] == "Updated prompt."
    assert resp.get_json()["cards"] == []
    listed = {p["id"]: p for p in client.get("/api/personas").get_json()}
    assert listed[pid]["cards"] == []


@patch("server.OllamaClient")
def test_put_persona_rejects_uninstalled_base_model(mock_cls, client):
    _install(mock_cls, names=("llama3.2:3b",))
    pid = client.post("/api/personas", data=json.dumps(CREATE_BODY), content_type="application/json").get_json()["id"]
    resp = client.put(
        f"/api/personas/{pid}",
        data=json.dumps({"base_model": "missing:7b"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "not installed" in resp.get_json()["error"]


def test_put_delete_unknown_persona_is_404(client):
    resp = client.put("/api/personas/does-not-exist", data=json.dumps({"display_name": "X"}), content_type="application/json")
    assert resp.status_code == 404
    resp = client.delete("/api/personas/does-not-exist")
    assert resp.status_code == 404


@patch("server.OllamaClient")
def test_cannot_delete_only_remaining_persona(mock_cls, client, app):
    _install(mock_cls)
    for path in app.PERSONAS_DIR.glob("*.Modelfile"):
        if path.stem != "assistant":
            path.unlink()
            cards = app.PERSONAS_DIR / f"{path.stem}.cards.json"
            if cards.exists():
                cards.unlink()
    resp = client.delete("/api/personas/assistant")
    assert resp.status_code == 400
    assert "only remaining" in resp.get_json()["error"]
    assert (app.PERSONAS_DIR / "assistant.Modelfile").exists()


@patch("server.OllamaClient")
def test_delete_default_promotes_another(mock_cls, client, app):
    _install(mock_cls)
    assert (app.PERSONAS_DIR / "assistant.Modelfile").exists()
    resp = client.delete("/api/personas/assistant")
    assert resp.status_code == 200
    listed = client.get("/api/personas").get_json()
    ids = {p["id"] for p in listed}
    assert "assistant" not in ids
    defaults = [p for p in listed if p["is_default"]]
    assert len(defaults) == 1


@patch("server.OllamaClient")
def test_card_verify_rejects_missing_audit_fields(mock_cls, client):
    _install(mock_cls)
    created = client.post(
        "/api/personas",
        data=json.dumps(
            {
                **CREATE_BODY,
                "cards": [{"title": "Severe bleeding", "answer": "PLACEHOLDER"}],
            }
        ),
        content_type="application/json",
    ).get_json()
    pid = created["id"]
    card_id = created["cards"][0]["id"]
    resp = client.put(
        f"/api/personas/{pid}/cards/{card_id}",
        data=json.dumps({"verified": True}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "verified_by" in resp.get_json()["error"]
    still = {p["id"]: p for p in client.get("/api/personas").get_json()}[pid]
    assert still["cards"][0]["verified"] is False


@patch("server.OllamaClient")
def test_card_verify_requires_source_too(mock_cls, client):
    _install(mock_cls)
    created = client.post(
        "/api/personas",
        data=json.dumps({**CREATE_BODY, "cards": [{"title": "Severe bleeding", "answer": "PLACEHOLDER"}]}),
        content_type="application/json",
    ).get_json()
    pid, card_id = created["id"], created["cards"][0]["id"]
    resp = client.put(
        f"/api/personas/{pid}/cards/{card_id}",
        data=json.dumps({"verified": True, "verified_by": "alice"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "verified_source" in resp.get_json()["error"]


@patch("server.OllamaClient")
def test_card_verify_succeeds_with_audit_trail(mock_cls, client):
    _install(mock_cls)
    created = client.post(
        "/api/personas",
        data=json.dumps({**CREATE_BODY, "cards": [{"title": "Severe bleeding", "answer": "PLACEHOLDER"}]}),
        content_type="application/json",
    ).get_json()
    pid, card_id = created["id"], created["cards"][0]["id"]
    resp = client.put(
        f"/api/personas/{pid}/cards/{card_id}",
        data=json.dumps(
            {
                "verified": True,
                "verified_by": "alice",
                "verified_source": "Red Cross first aid, 2024",
                "answer": "Apply firm direct pressure.",
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["verified"] is True
    assert data["verified_by"] == "alice"
    assert data["verified_source"] == "Red Cross first aid, 2024"
    listed = {p["id"]: p for p in client.get("/api/personas").get_json()}[pid]
    assert listed["cards"][0]["verified"] is True


@patch("server.OllamaClient")
def test_add_card_rejects_verified_true(mock_cls, client):
    _install(mock_cls)
    pid = client.post("/api/personas", data=json.dumps(CREATE_BODY), content_type="application/json").get_json()["id"]
    resp = client.post(
        f"/api/personas/{pid}/cards",
        data=json.dumps(
            {
                "title": "Sneak",
                "answer": "nope",
                "verified": True,
                "verified_by": "x",
                "verified_source": "y",
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "verified" in resp.get_json()["error"]


@patch("server.OllamaClient")
def test_add_and_delete_card(mock_cls, client):
    _install(mock_cls)
    pid = client.post("/api/personas", data=json.dumps(CREATE_BODY), content_type="application/json").get_json()["id"]
    added = client.post(
        f"/api/personas/{pid}/cards",
        data=json.dumps({"title": "Lost after dark", "answer": "PLACEHOLDER"}),
        content_type="application/json",
    )
    assert added.status_code == 201
    card_id = added.get_json()["id"]
    assert added.get_json()["verified"] is False
    gone = client.delete(f"/api/personas/{pid}/cards/{card_id}")
    assert gone.status_code == 200
    listed = {p["id"]: p for p in client.get("/api/personas").get_json()}[pid]
    assert listed["cards"] == []


def test_card_routes_404_when_persona_or_card_missing(client):
    resp = client.put(
        "/api/personas/nope/cards/x",
        data=json.dumps({"answer": "a"}),
        content_type="application/json",
    )
    assert resp.status_code == 404
    resp = client.put(
        "/api/personas/assistant/cards/not-a-card",
        data=json.dumps({"answer": "a"}),
        content_type="application/json",
    )
    assert resp.status_code == 404


def test_persona_writes_reject_lan_with_valid_token(client, app):
    env, headers = _lan_auth_headers(client, app)
    cases = [
        ("POST", "/api/personas", CREATE_BODY),
        ("PUT", "/api/personas/assistant", {"display_name": "Hacked"}),
        ("DELETE", "/api/personas/eli5-explainer", None),
        ("POST", "/api/personas/assistant/cards", {"title": "X", "answer": "Y"}),
        ("PUT", "/api/personas/survival-guide/cards/severe-bleeding", {"verified": True, "verified_by": "x", "verified_source": "y"}),
        ("DELETE", "/api/personas/survival-guide/cards/severe-bleeding", None),
    ]
    for method, path, body in cases:
        kwargs = {"environ_overrides": env, "headers": headers}
        if body is not None:
            kwargs["data"] = json.dumps(body)
            kwargs["content_type"] = "application/json"
        resp = client.open(path, method=method, **kwargs)
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code} {resp.get_json()}"
        assert resp.get_json()["error"] == "only available on the server machine itself"


def test_get_personas_still_works_from_lan_with_token(client, app):
    env, headers = _lan_auth_headers(client, app)
    resp = client.get("/api/personas", environ_overrides=env, headers=headers)
    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.get_json()}
    assert by_id["survival-guide"]["cards"][0]["verified"] is False


def test_chat_reference_inserts_card_without_ollama(client, app):
    resp = client.post(
        "/api/chat/reference",
        data=json.dumps({"persona": "survival-guide", "card_id": "severe-bleeding"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["source"] == "card"
    assert data["verified"] is False
    assert data["user_message"] == "Severe bleeding"
    assert "PLACEHOLDER" in data["reply"]
    assert data["latency_ms"] == 0
    conv = client.get(f"/api/conversations/{data['conversation_id']}").get_json()
    assert conv["messages"][0]["source"] == "card"
    assert conv["messages"][1]["source"] == "card"
    assert conv["messages"][1]["source_meta"]["verified"] is False
    exported = client.get(f"/api/conversations/{data['conversation_id']}/export").get_json()
    assert exported["messages"][1]["source"] == "card"


def test_chat_reference_unknown_card_is_404(client):
    resp = client.post(
        "/api/chat/reference",
        data=json.dumps({"persona": "survival-guide", "card_id": "nope"}),
        content_type="application/json",
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "card not found"


def test_chat_reference_works_from_lan_with_token(client, app):
    env, headers = _lan_auth_headers(client, app)
    resp = client.post(
        "/api/chat/reference",
        data=json.dumps({"persona": "survival-guide", "card_id": "water-purification"}),
        content_type="application/json",
        environ_overrides=env,
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.get_json()["source"] == "card"
