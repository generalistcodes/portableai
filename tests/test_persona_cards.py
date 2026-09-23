import json
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from persona_cards import (
    PLACEHOLDER_ANSWER,
    PersonaValidationError,
    find_card,
    load_cards,
    model_is_installed,
    normalize_card,
    normalize_cards,
    save_cards,
    slugify,
    unique_slug,
    validate_display_name,
    validate_system_prompt,
)


def test_slugify_from_display_name():
    assert slugify("Field Medic") == "field-medic"
    assert slugify("  Water & Food  ") == "water-food"


def test_unique_slug_avoids_collisions():
    taken = {"field-medic"}
    assert unique_slug("Field Medic", taken) == "field-medic-2"


def test_validate_display_name_rejects_empty_and_too_long():
    with pytest.raises(PersonaValidationError, match="display_name is required"):
        validate_display_name("  ")
    with pytest.raises(PersonaValidationError, match="at most 80"):
        validate_display_name("x" * 81)


def test_validate_system_prompt_rejects_triple_quotes():
    with pytest.raises(PersonaValidationError, match="cannot contain"):
        validate_system_prompt('hello """ there')


def test_normalize_card_forces_unverified_and_rejects_true():
    with pytest.raises(PersonaValidationError, match="cannot be marked verified"):
        normalize_card(
            {
                "title": "Severe bleeding",
                "answer": PLACEHOLDER_ANSWER,
                "verified": True,
                "verified_by": "alice",
                "verified_source": "Red Cross",
            },
            allow_verified_true=False,
        )


def test_normalize_card_create_path_always_unverified():
    card = normalize_card(
        {"title": "Severe bleeding", "answer": PLACEHOLDER_ANSWER, "verified": False},
        allow_verified_true=False,
    )
    assert card["verified"] is False
    assert card["verified_by"] is None
    assert card["id"] == "severe-bleeding"


def test_normalize_card_verify_requires_audit_fields():
    with pytest.raises(PersonaValidationError, match="verified_by"):
        normalize_card(
            {
                "title": "Severe bleeding",
                "answer": PLACEHOLDER_ANSWER,
                "verified": True,
            },
            allow_verified_true=True,
        )
    with pytest.raises(PersonaValidationError, match="verified_source"):
        normalize_card(
            {
                "title": "Severe bleeding",
                "answer": PLACEHOLDER_ANSWER,
                "verified": True,
                "verified_by": "alice",
            },
            allow_verified_true=True,
        )


def test_normalize_card_verify_with_audit_trail():
    card = normalize_card(
        {
            "title": "Severe bleeding",
            "answer": PLACEHOLDER_ANSWER,
            "verified": True,
            "verified_by": "alice",
            "verified_source": "Red Cross first aid manual, 2024",
        },
        allow_verified_true=True,
    )
    assert card["verified"] is True
    assert card["verified_by"] == "alice"


def test_normalize_cards_rejects_verified_true_in_bulk():
    with pytest.raises(PersonaValidationError, match="cannot be marked verified"):
        normalize_cards(
            [
                {
                    "title": "A",
                    "answer": "alpha",
                    "verified": True,
                    "verified_by": "x",
                    "verified_source": "y",
                }
            ],
            allow_verified_true=False,
        )


def test_normalize_card_rejects_empty_title_and_answer():
    with pytest.raises(PersonaValidationError, match="card title"):
        normalize_card({"title": " ", "answer": "ok"}, allow_verified_true=False)
    with pytest.raises(PersonaValidationError, match="card answer"):
        normalize_card({"title": "ok", "answer": ""}, allow_verified_true=False)


def test_model_is_installed_matches_latest_suffix():
    assert model_is_installed("llama3.2:3b", ["llama3.2:3b:latest"])
    assert model_is_installed("llama3.2:3b:latest", ["llama3.2:3b"])
    assert not model_is_installed("mistral:7b", ["llama3.2:3b"])


def test_load_save_cards_roundtrip(tmp_path):
    cards = [
        {
            "id": "severe-bleeding",
            "title": "Severe bleeding",
            "answer": PLACEHOLDER_ANSWER,
            "verified": False,
            "verified_by": None,
            "verified_source": None,
        }
    ]
    save_cards(tmp_path, "survival-guide", cards)
    loaded = load_cards(tmp_path, "survival-guide")
    assert loaded == cards
    assert find_card(loaded, "severe-bleeding")["title"] == "Severe bleeding"


def test_load_cards_missing_file_is_empty(tmp_path):
    assert load_cards(tmp_path, "assistant") == []


def test_load_cards_ignores_text_placeholder_heuristic(tmp_path):
    """verified is the flag, not a scan of the answer text."""
    payload = [
        {
            "id": "looks-real",
            "title": "Looks real",
            "answer": "Apply direct pressure. This is not a PLACEHOLDER.",
            "verified": False,
            "verified_by": None,
            "verified_source": None,
        }
    ]
    (tmp_path / "guide.cards.json").write_text(json.dumps(payload))
    loaded = load_cards(tmp_path, "guide")
    assert loaded[0]["verified"] is False
