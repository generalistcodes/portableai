"""
Parse an Ollama Modelfile into a structured dict.

This is intentionally a small, dependency-free parser rather than a full
Modelfile grammar implementation. It supports the subset most tutorials
actually use: FROM, SYSTEM (single-line or triple-quoted block), and
PARAMETER lines, plus optional `# display_name` / `# default` / `# icon`
comments for the UI. That's enough to build personas and to build the JSON
payload Ollama's /api/create endpoint expects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class ModelfileParseError(ValueError):
    """Raised when a Modelfile is missing something required or malformed."""


# Ollama parameters that take a numeric value vs. a string value.
# Anything not listed here is treated as a string.
_NUMERIC_PARAMETERS = {
    "temperature": float,
    "top_p": float,
    "top_k": int,
    "num_ctx": int,
    "num_predict": int,
    "repeat_penalty": float,
    "repeat_last_n": int,
    "seed": int,
    "mirostat": int,
    "mirostat_eta": float,
    "mirostat_tau": float,
}

# UI icon slugs accepted from `# icon: ...` comments. Unknown values are ignored
# so a typo can't leak an unexpected class name into the frontend.
KNOWN_ICONS = frozenset({"message", "shield", "person", "lightbulb"})
_ICON_ALIASES = {
    "chat": "message",
    "chat-bubble": "message",
    "message-circle": "message",
    "bubble": "message",
    "user": "person",
    "bulb": "lightbulb",
}
DEFAULT_ICON = "message"


def normalize_icon(value: str | None) -> str | None:
    """Map a Modelfile icon token to a known slug, or None if unset/unknown."""
    if not value:
        return None
    slug = value.strip().lower().replace("_", "-")
    slug = _ICON_ALIASES.get(slug, slug)
    if slug in KNOWN_ICONS:
        return slug
    return None


def humanize_persona_id(persona_id: str) -> str:
    """Turn a filename slug like 'no-nonsense-mentor' into a UI label."""
    return persona_id.replace("-", " ").replace("_", " ").strip().title()


@dataclass
class Persona:
    base_model: str
    system: str | None = None
    parameters: dict = field(default_factory=dict)
    display_name: str | None = None
    is_default: bool = False
    icon: str | None = None

    def resolved_display_name(self, persona_id: str) -> str:
        if self.display_name:
            return self.display_name
        return humanize_persona_id(persona_id)

    def resolved_icon(self) -> str:
        return self.icon or DEFAULT_ICON

    def to_create_payload(self, name: str) -> dict:
        """Build the JSON body for POST /api/create (structured form)."""
        if not name:
            raise ModelfileParseError("model name is required to create a persona")
        payload = {"model": name, "from": self.base_model}
        if self.system:
            payload["system"] = self.system
        if self.parameters:
            payload["parameters"] = self.parameters
        return payload


def parse_modelfile(text: str) -> Persona:
    """Parse Modelfile text into a Persona. Raises ModelfileParseError on
    malformed input (e.g. missing FROM, unterminated triple-quoted SYSTEM)."""
    lines = text.splitlines()
    base_model: str | None = None
    system_parts: list[str] = []
    parameters: dict = {}
    display_name: str | None = None
    is_default = False
    icon: str | None = None

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        i += 1

        if not line:
            continue

        if line.startswith("#"):
            meta = _metadata_from_comment(line)
            if "display_name" in meta:
                display_name = meta["display_name"]
            if "is_default" in meta:
                is_default = meta["is_default"]
            if "icon" in meta:
                icon = meta["icon"]
            continue

        if line.upper().startswith("FROM "):
            base_model = line[5:].strip()
            continue

        if line.upper().startswith("SYSTEM "):
            rest = line[7:].strip()
            if rest.startswith('"""'):
                # Triple-quoted block: may close on this line or a later one.
                block_lines = []
                remainder = rest[3:]
                if remainder.endswith('"""') and len(remainder) >= 3:
                    block_lines.append(remainder[:-3])
                else:
                    block_lines.append(remainder)
                    closed = False
                    while i < len(lines):
                        block_line = lines[i]
                        i += 1
                        if block_line.strip().endswith('"""'):
                            block_lines.append(block_line[: block_line.rstrip().rfind('"""')])
                            closed = True
                            break
                        block_lines.append(block_line)
                    if not closed:
                        raise ModelfileParseError("unterminated triple-quoted SYSTEM block")
                system_parts.append("\n".join(block_lines).strip())
            elif rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
                system_parts.append(rest[1:-1])
            else:
                system_parts.append(rest)
            continue

        if line.upper().startswith("PARAMETER "):
            body = line[10:].strip()
            parts = body.split(None, 1)
            if len(parts) != 2:
                raise ModelfileParseError(f"malformed PARAMETER line: {raw_line!r}")
            key, value = parts[0], parts[1].strip()
            caster = _NUMERIC_PARAMETERS.get(key)
            if caster is not None:
                try:
                    value = caster(value)
                except ValueError as exc:
                    raise ModelfileParseError(
                        f"PARAMETER {key} expects a {caster.__name__}, got {value!r}"
                    ) from exc
            parameters[key] = value
            continue

        # TEMPLATE / ADAPTER / LICENSE / MESSAGE are valid Ollama directives
        # but out of scope for this tutorial's parser; ignore silently.

    if base_model is None:
        raise ModelfileParseError("Modelfile is missing a FROM line")

    return Persona(
        base_model=base_model,
        system="\n".join(system_parts).strip() or None,
        parameters=parameters,
        display_name=display_name,
        is_default=is_default,
        icon=icon,
    )


def _metadata_from_comment(line: str) -> dict:
    """Read optional `# display_name:` / `# default:` / `# icon:` comments.

    Comments are ignored by Ollama, so this is safe UI metadata. Unknown
    comments and unknown icon slugs are ignored.
    """
    body = line[1:].strip()
    if ":" not in body:
        return {}
    key, _, value = body.partition(":")
    key = key.strip().lower()
    value = value.strip()
    if key == "display_name" and value:
        return {"display_name": value}
    if key == "default":
        return {"is_default": value.lower() in ("true", "yes", "1")}
    if key == "icon":
        icon = normalize_icon(value)
        if icon:
            return {"icon": icon}
    return {}


def load_persona_file(path: str | Path) -> Persona:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such Modelfile: {path}")
    return parse_modelfile(path.read_text())
