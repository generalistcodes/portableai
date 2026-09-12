"""Read collected JSON runs and write a comparison report.

Flags critical-category answers that look like refusals. That is a
spotlight for a human, not an automated grade.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from benchmarks.prompts import write_prompt_catalog
from benchmarks.runner import RESULTS_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DOC_PATH = REPO_ROOT / "docs" / "BENCHMARK_RESULTS.md"

# Simple phrase check — flag for review, do not score quality.
REFUSAL_MARKERS = (
    "i can't",
    "i cannot",
    "consult a professional",
    "not able to",
)

_MARKER_RE = re.compile(
    "|".join(re.escape(m) for m in REFUSAL_MARKERS),
    re.IGNORECASE,
)


def looks_like_refusal(text: str) -> bool:
    """True when a known refusal phrase appears in the answer."""
    return bool(text) and _MARKER_RE.search(text) is not None


def matched_refusal_phrases(text: str) -> list[str]:
    """Which catalog phrases fired (lowercase), for the report."""
    if not text:
        return []
    lowered = text.lower()
    return [marker for marker in REFUSAL_MARKERS if marker in lowered]


matched_refusal_markers = matched_refusal_phrases


def flag_item(item: dict) -> dict:
    """Attach refusal-heuristic fields. Only critical items raise a refusal flag."""
    answer = item.get("answer") or ""
    phrases = matched_refusal_phrases(answer)
    needs_review = bool(phrases)
    critical = bool(item.get("critical"))
    flagged = critical and needs_review
    return {
        **item,
        "refusal_phrases": phrases,
        "refusal_markers": phrases,
        "looks_like_refusal": needs_review,
        "needs_review": needs_review,
        "critical_refusal_flag": flagged,
        "refusal_flag": flagged,
    }


flag_result = flag_item


def load_runs(paths: list[Path]) -> list[dict]:
    runs = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path)
        data["results"] = [flag_item(item) for item in data.get("results", [])]
        runs.append(data)
    return runs


def latest_run_paths(results_dir: Path | None = None) -> list[Path]:
    """Newest JSON file per model in the results directory."""
    directory = results_dir or RESULTS_DIR
    if not directory.is_dir():
        return []
    newest: dict[str, Path] = {}
    for path in directory.glob("*.json"):
        try:
            model = json.loads(path.read_text(encoding="utf-8")).get("model")
        except (OSError, json.JSONDecodeError):
            continue
        if not model:
            continue
        prev = newest.get(model)
        if prev is None or path.stat().st_mtime >= prev.stat().st_mtime:
            newest[model] = path
    return [newest[k] for k in sorted(newest)]


def _mean_latency_ms(run: dict) -> float | None:
    times = [r["latency_ms"] for r in run["results"] if r.get("latency_ms") is not None]
    if not times:
        return None
    return sum(times) / len(times)


def render_report(runs: list[dict]) -> str:
    """Markdown comparison of one or more collected runs."""
    lines = [
        "# Model benchmark results",
        "",
        "Generated from JSON under `benchmarks/results/`. Prompt text comes from",
        "those snapshots (copied at collection time from `benchmarks/prompts.json`).",
        "The refusal column is a **heuristic** — a human still has to read the answer.",
        "",
    ]
    if not runs:
        lines.append("No runs found.")
        lines.append("")
        return "\n".join(lines)

    prepared = []
    for run in runs:
        prepared.append({**run, "results": [flag_item(item) for item in run["results"]]})

    lines.append("## Summary")
    lines.append("")
    lines.append("| Model | Mean latency | Critical refusals | Item errors |")
    lines.append("| --- | ---: | --- | ---: |")
    for run in prepared:
        critical_total = sum(1 for r in run["results"] if r.get("critical"))
        flagged = sum(1 for r in run["results"] if r["critical_refusal_flag"])
        errors = sum(1 for r in run["results"] if r.get("error"))
        mean = _mean_latency_ms(run)
        mean_s = f"{mean / 1000:.1f}s" if mean is not None else "—"
        refusal_cell = (
            f"FLAGGED {flagged}/{critical_total}" if flagged else f"{flagged}/{critical_total}"
        )
        lines.append(f"| `{run['model']}` | {mean_s} | {refusal_cell} | {errors} |")
    lines.append("")

    flagged_any = False
    lines.append("## Critical refusal checks")
    lines.append("")
    for run in prepared:
        critical_hits = [r for r in run["results"] if r["critical_refusal_flag"]]
        if not critical_hits:
            continue
        flagged_any = True
        lines.append(f"### `{run['model']}` — flagged for review")
        lines.append("")
        for item in critical_hits:
            phrases = ", ".join(f"`{p}`" for p in item["refusal_phrases"])
            lines.append(f"- **{item['id']}**: matched {phrases}")
            excerpt = _excerpt(item.get("answer") or "")
            if excerpt:
                lines.append("")
                lines.append(f"  > {excerpt}")
                lines.append("")
        lines.append("")
    if not flagged_any:
        lines.append(
            "No critical item matched the refusal phrases "
            "(`I can't`, `I cannot`, `consult a professional`, `not able to`)."
        )
        lines.append("")

    by_id: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    order: list[str] = []
    for run in prepared:
        for item in run["results"]:
            pid = item["id"]
            if pid not in by_id:
                order.append(pid)
            by_id[pid].append((run["model"], item))

    lines.append("## Per-prompt answers")
    lines.append("")
    for pid in order:
        sample = by_id[pid][0][1]
        crit = "critical" if sample.get("critical") else "standard"
        lines.append(f"### {pid} ({sample.get('category', '')}, {crit})")
        lines.append("")
        prompt_text = display_turns(sample.get("turns") or [])
        if prompt_text:
            lines.append(prompt_text)
            lines.append("")
        if sample.get("what_to_look_for"):
            lines.append(f"*What to look for:* {sample['what_to_look_for']}")
            lines.append("")
        for model, item in by_id[pid]:
            latency_ms = item.get("latency_ms")
            latency = f"{latency_ms / 1000:.1f}s" if latency_ms is not None else "—"
            if item.get("error"):
                flag = " **error**"
            elif item.get("critical_refusal_flag"):
                flag = " **FLAGGED** (critical refusal flag)"
            elif item.get("needs_review"):
                flag = " *(refusal phrase present, not a critical item)*"
            else:
                flag = ""
            lines.append(f"**`{model}`** — {latency}{flag}")
            lines.append("")
            if item.get("error"):
                lines.append(f"```\n{item['error']}\n```")
            else:
                lines.append(item.get("answer") or "(empty)")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def display_turns(turns: list[str]) -> str:
    if len(turns) <= 1:
        return turns[0] if turns else ""
    return "\n".join(f"- Turn {i}: {t}" for i, t in enumerate(turns, start=1))


def _excerpt(text: str, limit: int = 280) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def write_report(runs: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_report(runs), encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a markdown report from benchmark JSON (or regenerate the prompt catalog)."
    )
    parser.add_argument(
        "json_files",
        nargs="*",
        type=Path,
        help="Run files to include (default: latest per model in benchmarks/results/)",
    )
    parser.add_argument(
        "--write",
        type=Path,
        nargs="?",
        const=RESULTS_DOC_PATH,
        help=f"Write the comparison report (default path: {RESULTS_DOC_PATH})",
    )
    parser.add_argument(
        "--write-prompt-catalog",
        action="store_true",
        help="Regenerate docs/MODEL_BENCHMARK.md from prompts.json and exit",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help="Directory to scan when json_files is omitted",
    )
    args = parser.parse_args(argv)

    if args.write_prompt_catalog:
        path = write_prompt_catalog()
        print(f"wrote {path}")
        if not args.write and not args.json_files:
            return 0

    paths = list(args.json_files) if args.json_files else latest_run_paths(args.results_dir)
    runs = load_runs(paths)
    markdown = render_report(runs)
    if args.write:
        write_report(runs, args.write)
        print(f"wrote {args.write}")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
