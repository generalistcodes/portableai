"""Collect raw model answers from Ollama. Makes no quality judgment."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from benchmarks.prompts import load_prompts, user_turns

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 180
RESULTS_DIR = Path(__file__).resolve().parent / "results"


class BenchmarkError(RuntimeError):
    """Raised when Ollama returns a non-2xx status or an unusable body."""


def list_installed_models(base_url: str = DEFAULT_BASE_URL, timeout: int = 10) -> list[str]:
    resp = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
    if resp.status_code != 200:
        raise BenchmarkError(f"GET /api/tags failed: {resp.status_code} {resp.text}")
    names = []
    for entry in resp.json().get("models") or []:
        name = entry.get("name")
        if name:
            names.append(name)
    return names


def chat(
    model: str,
    messages: list[dict],
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """POST /api/chat and return assistant text. Records nothing."""
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise BenchmarkError(f"POST /api/chat failed: {resp.status_code} {resp.text}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise BenchmarkError("Ollama returned an incomplete or invalid response.") from exc
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise BenchmarkError(f"unexpected /api/chat response shape: {data!r}") from exc


def run_item(
    model: str,
    item: dict,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Send one catalog item (single prompt or multi-turn) and record the reply."""
    turns = user_turns(item)
    messages: list[dict] = []
    turn_details: list[dict] = []
    total_ms = 0
    last_answer = ""
    error = None

    for text in turns:
        messages.append({"role": "user", "content": text})
        started = time.perf_counter()
        try:
            answer = chat(model, messages, base_url=base_url, timeout=timeout)
        except (BenchmarkError, requests.RequestException) as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            total_ms += elapsed_ms
            error = str(exc)
            turn_details.append({"latency_ms": elapsed_ms, "answer": "", "error": error})
            break
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        total_ms += elapsed_ms
        last_answer = answer
        messages.append({"role": "assistant", "content": answer})
        turn_details.append({"latency_ms": elapsed_ms, "answer": answer, "error": None})

    return {
        "id": item["id"],
        "category": item["category"],
        "critical": bool(item.get("critical")),
        "turns": turns,
        "what_to_look_for": item["what_to_look_for"],
        "answer": last_answer,
        "latency_ms": total_ms,
        "error": error,
        "turn_details": turn_details,
    }


def run_model(
    model: str,
    prompts: list[dict] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run every prompt against one model. Continues after per-item errors."""
    items = prompts if prompts is not None else load_prompts()
    started_at = datetime.now(timezone.utc).isoformat()
    results = []
    for item in items:
        results.append(run_item(model, item, base_url=base_url, timeout=timeout))
    return {
        "model": model,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url.rstrip("/"),
        "results": results,
    }


def save_run(run: dict, dest_dir: Path | None = None, results_dir: Path | None = None) -> Path:
    """Write one JSON file for this model run. Returns the path."""
    directory = dest_dir or results_dir or RESULTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = run["model"].replace(":", "_").replace("/", "_")
    path = directory / f"{stamp}_{slug}.json"
    path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect PortableAI model-benchmark answers from a local Ollama."
    )
    parser.add_argument(
        "models",
        nargs="*",
        help="Ollama model tags, e.g. llama3.2:3b qwen2.5:3b",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every model currently installed (GET /api/tags).",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Ollama API base URL (default {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=RESULTS_DIR,
        help="Directory for per-model JSON files",
    )
    args = parser.parse_args(argv)

    models = list(args.models)
    if args.all:
        models = list_installed_models(base_url=args.base_url)
        if not models:
            print("No models installed at", args.base_url, file=sys.stderr)
            return 1
    if not models:
        parser.print_help()
        print("\nPass model names, or --all to use every installed model.", file=sys.stderr)
        return 2

    prompts = load_prompts()
    for model in models:
        print(f"Collecting {model} ({len(prompts)} prompts) ...", flush=True)
        run = run_model(model, prompts=prompts, base_url=args.base_url, timeout=args.timeout)
        path = save_run(run, dest_dir=args.out_dir)
        errors = sum(1 for r in run["results"] if r["error"])
        print(f"  wrote {path} ({errors} item error(s))", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
