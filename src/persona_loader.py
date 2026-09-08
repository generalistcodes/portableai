"""
Parse an Ollama Modelfile into a structured dict.

This is intentionally a small, dependency-free parser rather than a full
Modelfile grammar implementation. It supports the subset most tutorials
actually use: FROM, SYSTEM (single-line or triple-quoted block), and
PARAMETER lines. That's enough to build personas and to build the JSON
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


@dataclass
class Persona:
    base_model: str
    system: str | None = None
    parameters: dict = field(default_factory=dict)

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

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        i += 1

        if not line or line.startswith("#"):
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
    )


def load_persona_file(path: str | Path) -> Persona:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such Modelfile: {path}")
    return parse_modelfile(path.read_text())
