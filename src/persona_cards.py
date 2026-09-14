"""Quick-reference cards stored beside each persona Modelfile.

A persona may have an optional sibling `personas/<slug>.cards.json`: a JSON
array of card objects. Missing file means no cards. `verified` is never
inferred from answer text — it is a boolean that starts false and can only
be set true through the dedicated card-edit API (which also requires an
audit trail).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLACEHOLDER_ANSWER = (
    "PLACEHOLDER -- replace with vetted first-aid content from a real source "
    "before relying on this."
)

DISPLAY_NAME_MIN = 1
DISPLAY_NAME_MAX = 80
SYSTEM_PROMPT_MIN = 1
SYSTEM_PROMPT_MAX = 8000
CARD_TITLE_MIN = 1
CARD_TITLE_MAX = 120
CARD_ANSWER_MIN = 1
CARD_ANSWER_MAX = 8000
VERIFIED_BY_MIN = 1
VERIFIED_BY_MAX = 120
VERIFIED_SOURCE_MIN = 1
VERIFIED_SOURCE_MAX = 500
SLUG_MAX = 80
MAX_CARDS_PER_PERSONA = 50

CARD_PUBLIC_FIELDS = (
    "id",
    "title",
    "answer",
    "verified",
    "verified_by",
    "verified_source",
)


class PersonaValidationError(ValueError):
    """Raised for 400-able input problems. str(exc) is the API error message."""


def cards_path(personas_dir: str | Path, persona_id: str) -> Path:
    return Path(personas_dir) / f"{persona_id}.cards.json"


def slugify(text: str, *, max_len: int = SLUG_MAX) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug[:max_len]


def _require_str(value, field: str) -> str:
    if value is None or not isinstance(value, str):
        raise PersonaValidationError(f"{field} is required")
    return value


def _bounded_text(value, field: str, min_len: int, max_len: int) -> str:
    text = _require_str(value, field).strip()
    if len(text) < min_len:
        raise PersonaValidationError(f"{field} is required")
    if len(text) > max_len:
        raise PersonaValidationError(f"{field} must be at most {max_len} characters")
    return text


def validate_display_name(value) -> str:
    return _bounded_text(value, "display_name", DISPLAY_NAME_MIN, DISPLAY_NAME_MAX)


def validate_system_prompt(value) -> str:
    text = _bounded_text(value, "system_prompt", SYSTEM_PROMPT_MIN, SYSTEM_PROMPT_MAX)
    if '"""' in text:
        raise PersonaValidationError('system_prompt cannot contain """')
    return text


def validate_base_model_name(value) -> str:
    text = _require_str(value, "base_model").strip()
    if not text:
        raise PersonaValidationError("base_model is required")
    if len(text) > 120:
        raise PersonaValidationError("base_model must be at most 120 characters")
    return text


def model_is_installed(base_model: str, installed_names: list[str] | set[str]) -> bool:
    wanted = {base_model, base_model.removesuffix(":latest")}
    if not base_model.endswith(":latest"):
        wanted.add(f"{base_model}:latest")
    have: set[str] = set()
    for name in installed_names:
        if not name:
            continue
        have.add(name)
        have.add(name.removesuffix(":latest"))
    return bool(wanted & have)


def unique_slug(base: str, taken: set[str]) -> str:
    slug = slugify(base)
    if not slug:
        raise PersonaValidationError("could not generate an id from display_name")
    candidate = slug
    n = 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate = f"{slug[: SLUG_MAX - len(suffix)]}{suffix}"
        n += 1
    return candidate


def _public_card(card: dict) -> dict:
    return {
        "id": card["id"],
        "title": card["title"],
        "answer": card["answer"],
        "verified": bool(card.get("verified")),
        "verified_by": card.get("verified_by"),
        "verified_source": card.get("verified_source"),
    }


def normalize_card(
    raw,
    *,
    allow_verified_true: bool,
    taken_ids: set[str] | None = None,
) -> dict:
    """Normalize one card object. `verified: true` is rejected unless
    `allow_verified_true` (the dedicated card-edit endpoint)."""
    if not isinstance(raw, dict):
        raise PersonaValidationError("each card must be an object")

    if raw.get("verified") is True and not allow_verified_true:
        raise PersonaValidationError(
            "cards cannot be marked verified here; use "
            "PUT /api/personas/<id>/cards/<card_id> with verified_by and verified_source"
        )

    title = _bounded_text(raw.get("title"), "card title", CARD_TITLE_MIN, CARD_TITLE_MAX)
    answer = _bounded_text(raw.get("answer"), "card answer", CARD_ANSWER_MIN, CARD_ANSWER_MAX)

    card_id = raw.get("id")
    if card_id is None or (isinstance(card_id, str) and not card_id.strip()):
        card_id = unique_slug(title, taken_ids or set())
    elif not isinstance(card_id, str):
        raise PersonaValidationError("card id must be a string")
    else:
        card_id = slugify(card_id)
        if not card_id:
            raise PersonaValidationError("card id is invalid")
        if taken_ids is not None and card_id in taken_ids:
            raise PersonaValidationError(f"duplicate card id: {card_id}")

    verified = raw.get("verified") is True if allow_verified_true else False
    verified_by = None
    verified_source = None
    if verified:
        verified_by = _bounded_text(
            raw.get("verified_by"), "verified_by", VERIFIED_BY_MIN, VERIFIED_BY_MAX
        )
        verified_source = _bounded_text(
            raw.get("verified_source"),
            "verified_source",
            VERIFIED_SOURCE_MIN,
            VERIFIED_SOURCE_MAX,
        )

    return _public_card(
        {
            "id": card_id,
            "title": title,
            "answer": answer,
            "verified": verified,
            "verified_by": verified_by,
            "verified_source": verified_source,
        }
    )


def normalize_cards(raw, *, allow_verified_true: bool) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PersonaValidationError("cards must be an array")
    if len(raw) > MAX_CARDS_PER_PERSONA:
        raise PersonaValidationError(f"at most {MAX_CARDS_PER_PERSONA} cards per persona")
    cards: list[dict] = []
    taken: set[str] = set()
    for item in raw:
        card = normalize_card(item, allow_verified_true=allow_verified_true, taken_ids=taken)
        taken.add(card["id"])
        cards.append(card)
    return cards


def load_cards(personas_dir: str | Path, persona_id: str) -> list[dict]:
    path = cards_path(personas_dir, persona_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    cards = []
    for item in data:
        if not isinstance(item, dict):
            continue
        card_id = slugify(str(item.get("id") or item.get("title") or ""))
        title = str(item.get("title") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not card_id or not title or not answer:
            continue
        verified = item.get("verified") is True
        cards.append(
            _public_card(
                {
                    "id": card_id,
                    "title": title[:CARD_TITLE_MAX],
                    "answer": answer[:CARD_ANSWER_MAX],
                    "verified": verified,
                    "verified_by": item.get("verified_by") if verified else None,
                    "verified_source": item.get("verified_source") if verified else None,
                }
            )
        )
    return cards


def save_cards(personas_dir: str | Path, persona_id: str, cards: list[dict]) -> None:
    path = cards_path(personas_dir, persona_id)
    public = [_public_card(c) for c in cards]
    path.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")


def delete_cards_file(personas_dir: str | Path, persona_id: str) -> None:
    path = cards_path(personas_dir, persona_id)
    if path.exists():
        path.unlink()


def find_card(cards: list[dict], card_id: str) -> dict | None:
    for card in cards:
        if card["id"] == card_id:
            return card
    return None
