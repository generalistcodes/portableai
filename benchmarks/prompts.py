"""Load the prompt catalog — the only source of benchmark prompt text."""
from __future__ import annotations

import json
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent
PROMPTS_PATH = BENCHMARKS_DIR / "prompts.json"
REPO_ROOT = BENCHMARKS_DIR.parent
PROMPT_CATALOG_PATH = REPO_ROOT / "docs" / "MODEL_BENCHMARK.md"

EXPECTED_CATEGORIES = {
    "Refusal check",
    "Practical accuracy",
    "Instruction-following",
    "Conversation memory",
    "Honesty about limits",
}

GENERATED_BANNER = (
    "<!-- Generated from benchmarks/prompts.json. Do not edit by hand.\n"
    "     Regenerate: python -m benchmarks.report --write-prompt-catalog -->\n"
)


def load_prompts(path: Path | None = None) -> list[dict]:
    """Return the prompt list from prompts.json, validating required fields."""
    data = json.loads((path or PROMPTS_PATH).read_text(encoding="utf-8"))
    prompts = data["prompts"]
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts.json must contain a non-empty 'prompts' list")
    for item in prompts:
        _validate_item(item)
    return prompts


def user_turns(item: dict) -> list[str]:
    """User messages to send, in order (one turn, or a memory sequence)."""
    if "turns" in item:
        return list(item["turns"])
    return [item["prompt"]]


def display_prompt(item: dict) -> str:
    """Human-readable prompt text for docs and reports."""
    turns = user_turns(item)
    if len(turns) == 1:
        return turns[0]
    lines = []
    for i, text in enumerate(turns, start=1):
        lines.append(f"Turn {i}: {text}")
    return "\n".join(lines)


def render_prompt_catalog(prompts: list[dict] | None = None) -> str:
    """Markdown catalog generated from prompts.json — no hand-copied prompt text."""
    items = prompts if prompts is not None else load_prompts()
    lines = [
        GENERATED_BANNER,
        "",
        "# Model benchmark prompts",
        "",
        "This file is generated from `benchmarks/prompts.json`. That JSON file is",
        "the only place prompt text lives. The collector (`python -m benchmarks.runner`)",
        "and this document both read it, so they cannot drift apart.",
        "",
        "Run a collection (one JSON file per model, under `benchmarks/results/`):",
        "",
        "```bash",
        "python -m benchmarks.runner llama3.2:3b qwen2.5:3b qwen2.5:0.5b",
        "```",
        "",
        "Turn those files into a mechanical comparison of the latest JSON:",
        "",
        "```bash",
        "python -m benchmarks.report --write benchmarks/results/comparison.md",
        "```",
        "",
        "A human reading of a real run (pass/fail on critical checks, and which",
        "model PortableAI should default to) is `docs/BENCHMARK_RESULTS.md`.",
        "That file is written after reading the answers — do not overwrite it",
        "from the JSON dump.",
        "",
        "`critical: true` items are refusal checks. A refusal-shaped answer there",
        "is flagged prominently in the report for human review — the heuristic is",
        "a spotlight, not a grade.",
        "",
    ]
    current_category = None
    for item in items:
        if item["category"] != current_category:
            current_category = item["category"]
            critical_note = (
                " — **critical** (a refusal here is a real failure for PortableAI)"
                if any(
                    p["category"] == current_category and p.get("critical")
                    for p in items
                )
                else ""
            )
            lines.append(f"## {current_category}{critical_note}")
            lines.append("")
        crit = "critical" if item.get("critical") else "standard"
        lines.append(f"### `{item['id']}` ({crit})")
        lines.append("")
        lines.append(display_prompt(item))
        lines.append("")
        lines.append(f"*What to look for:* {item['what_to_look_for']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_prompt_catalog(dest: Path | None = None) -> Path:
    path = dest or PROMPT_CATALOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_prompt_catalog(), encoding="utf-8")
    return path


def _validate_item(item: dict) -> None:
    if not isinstance(item, dict):
        raise ValueError("each prompt must be an object")
    for key in ("id", "category", "what_to_look_for"):
        if not item.get(key):
            raise ValueError(f"prompt is missing '{key}': {item!r}")
    if "prompt" not in item and "turns" not in item:
        raise ValueError(f"prompt {item['id']!r} needs 'prompt' or 'turns'")
    if "turns" in item:
        turns = item["turns"]
        if not isinstance(turns, list) or not all(isinstance(t, str) and t for t in turns):
            raise ValueError(f"prompt {item['id']!r} has empty or invalid 'turns'")
    if "prompt" in item and not isinstance(item["prompt"], str):
        raise ValueError(f"prompt {item['id']!r} has a non-string 'prompt'")
