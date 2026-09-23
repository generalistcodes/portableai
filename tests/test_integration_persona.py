"""
Integration test: creates the real personas in a real, locally running
Ollama and checks that persona-shaped behavior actually shows up.

This is intentionally separate from the mocked unit tests. LLM output is
not perfectly deterministic even at temperature 0, so these assertions are
loose/structural (banned phrases, presence of a trait) rather than exact
string matches. Treat this file as a smoke test, not a correctness proof --
its job is to catch "the persona build silently broke" or "the API contract
changed", not to grade the model's writing.

Run with: pytest -m integration
Skips automatically if Ollama isn't reachable on localhost:11434, or if
the base model (llama3.2:3b) hasn't been pulled yet.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ollama_client import OllamaClient
from persona_loader import load_persona_file

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"
BASE_MODEL = "llama3.2:3b"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    c = OllamaClient()
    if not c.is_available():
        pytest.skip("Ollama is not running on localhost:11434 -- start it with `ollama serve`")
    model_names = {m["name"] for m in c.list_models()}
    if BASE_MODEL not in model_names:
        pytest.skip(f"base model {BASE_MODEL} not pulled -- run `ollama pull {BASE_MODEL}`")
    return c


@pytest.fixture(scope="module")
def mentor_model(client):
    name = "test-no-nonsense-mentor"
    persona = load_persona_file(PERSONAS_DIR / "no-nonsense-mentor.Modelfile")
    client.create_model(persona.to_create_payload(name))
    yield name
    client.delete_model(name)


@pytest.fixture(scope="module")
def eli5_model(client):
    name = "test-eli5-explainer"
    persona = load_persona_file(PERSONAS_DIR / "eli5-explainer.Modelfile")
    client.create_model(persona.to_create_payload(name))
    yield name
    client.delete_model(name)


def test_mentor_never_apologizes(client, mentor_model):
    reply = client.chat(
        mentor_model,
        [{"role": "user", "content": "I think I broke my database migration, help."}],
        options={"temperature": 0},
    )
    assert reply.strip()
    lowered = reply.lower()
    assert "i'm sorry" not in lowered
    assert "as an ai" not in lowered


def test_mentor_gives_a_next_action(client, mentor_model):
    reply = client.chat(
        mentor_model,
        [{"role": "user", "content": "Should I use REST or GraphQL for a small internal API?"}],
        options={"temperature": 0},
    )
    assert reply.strip()
    # Structural check: the persona is instructed to end with a concrete
    # next step, so we expect an imperative-ish closing line rather than
    # an open-ended "it depends" with no answer.
    assert "it depends" not in reply.lower().split(".")[0]


def test_eli5_uses_an_analogy(client, eli5_model):
    reply = client.chat(
        eli5_model,
        [{"role": "user", "content": "What is an API?"}],
        options={"temperature": 0.6},
    )
    assert reply.strip()
    analogy_markers = [
        "like a",
        "imagine",
        "think of",
        "kitchen",
        "playground",
        "waiter",
        "menu",
    ]
    lowered = reply.lower()
    assert any(marker in lowered for marker in analogy_markers), reply


def test_eli5_never_mentions_being_an_ai(client, eli5_model):
    reply = client.chat(
        eli5_model,
        [{"role": "user", "content": "Are you a robot?"}],
        options={"temperature": 0.6},
    )
    assert "as an ai" not in reply.lower()
