"""First-run helpers: interactive model prompt and waiting for Flask.

The terminal Y/n prompt is a fast path for people who launched
PortableAI from a real terminal. It is skipped whenever stdin or stdout
is not a TTY (double-click, piped, redirected) so we never block on a
stdin that will not get a keystroke. The web onboarding screen is the
fallback; both paths pull the same model into the same Ollama store and
agree afterward via GET /api/models.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

DEFAULT_MODEL_NAME = "llama3.2:3b"
DEFAULT_MODEL_SIZE = "~2 GB"
DEFAULT_MODEL_BLURB = "good general-purpose default"
PREFETCH_ONLY_ENV = "PORTABLEAI_PREFETCH_ONLY"
DEFAULT_MODEL_ENV = "PORTABLEAI_DEFAULT_MODEL"

PROMPT_HEADER = "No models installed yet."
PROMPT_RECOMMEND = (
    f"Recommended: {DEFAULT_MODEL_NAME} ({DEFAULT_MODEL_SIZE}) -- {DEFAULT_MODEL_BLURB}"
)
PROMPT_QUESTION = "Download it now? [Y/n]: "


def prefetch_requested(flag: bool = False, env=None) -> bool:
    if flag:
        return True
    raw = ((env or os.environ).get(PREFETCH_ONLY_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def catalog_path() -> Path:
    try:
        from app_paths import resource_root
    except ImportError:
        return Path(__file__).resolve().parent.parent / "ui" / "model_catalog.json"
    return resource_root() / "ui" / "model_catalog.json"


def load_catalog(path: Path | None = None) -> list[dict]:
    target = path or catalog_path()
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def recommended_model_from_catalog(catalog: list[dict] | None = None) -> str | None:
    """First catalog entry with recommended: true (llama3.2:3b in the shipped file)."""
    for entry in catalog if catalog is not None else load_catalog():
        if not isinstance(entry, dict) or not entry.get("recommended"):
            continue
        name = (entry.get("name") or "").strip()
        if name:
            return name
    return None


def default_model_name(*, env=None, catalog: list[dict] | None = None) -> str:
    override = ((env or os.environ).get(DEFAULT_MODEL_ENV) or "").strip()
    if override:
        return override
    return recommended_model_from_catalog(catalog) or DEFAULT_MODEL_NAME


def interactive_terminal(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> bool:
    """True only when both stdin and stdout are attached TTYs."""
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    in_tty = bool(getattr(in_stream, "isatty", lambda: False)())
    out_tty = bool(getattr(out_stream, "isatty", lambda: False)())
    return in_tty and out_tty


def wait_for_tcp_port(port: int, timeout: float = 20.0, host: str = "127.0.0.1") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _installed_names(client: Any) -> list[str]:
    if not getattr(client, "is_available", lambda: False)():
        return []
    try:
        raw = client.list_models()
    except Exception:
        return []
    names = []
    for item in raw or []:
        name = (item or {}).get("name") or (item or {}).get("model")
        if name:
            names.append(name)
    return names


def _print_pull_progress(
    completed: int,
    total: int,
    stdout: TextIO,
    state: dict[str, int],
) -> None:
    if total <= 0:
        return
    pct = min(100, int(100 * completed / total))
    # Match the vendored-Ollama fetch ticks: sparse percent lines.
    if pct < 100 and pct < state.get("last", -5) + 5:
        return
    state["last"] = pct
    done_gb = completed / (1024 * 1024 * 1024)
    total_gb = total / (1024 * 1024 * 1024)
    print(
        f"  ... {done_gb:.1f} / {total_gb:.1f} GB ({pct}%)",
        file=stdout,
        flush=True,
    )


def maybe_prompt_default_model(
    client: Any,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> str:
    """Offer llama3.2:3b when GET /api/models would be empty.

    Returns one of: ``skipped`` (no TTY / EOF / ollama down),
    ``has_models``, ``declined``, ``pulled``, ``error``.
    """
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    if not interactive_terminal(in_stream, out_stream):
        return "skipped"

    names = _installed_names(client)
    if names:
        return "has_models"

    model = default_model_name()
    print(PROMPT_HEADER, file=out_stream, flush=True)
    print(
        f"Recommended: {model} ({DEFAULT_MODEL_SIZE}) -- {DEFAULT_MODEL_BLURB}",
        file=out_stream,
        flush=True,
    )
    read_line = input_fn if input_fn is not None else input
    try:
        answer = read_line(PROMPT_QUESTION)
    except EOFError:
        print(file=out_stream)
        return "skipped"

    if answer.strip().lower() in ("n", "no"):
        return "declined"

    return ensure_default_model(client, model, stdout=out_stream)


def ensure_default_model(
    client: Any,
    name: str | None = None,
    *,
    stdout: TextIO | None = None,
) -> str:
    """Pull the starter model via OllamaClient.pull_model if it is missing."""
    out_stream = stdout if stdout is not None else sys.stdout
    model = name or default_model_name()
    names = _installed_names(client)
    if names and _model_already_installed(model, names):
        return "has_models"

    print(
        f"Downloading {model} ({DEFAULT_MODEL_SIZE})...",
        file=out_stream,
        flush=True,
    )
    progress_state = {"last": -5}
    _setup_log(f"model pull start  name={model}")

    def on_progress(completed: int, total: int) -> None:
        _print_pull_progress(completed, total, out_stream, progress_state)
        _report_model_progress(completed, total)

    def on_status(message: str, **kwargs: Any) -> None:
        print(f"  {message}", file=out_stream, flush=True)
        if kwargs.get("status") == "retrying" or "retrying" in (message or "").lower():
            _setup_log(f"model pull retry  {message}")
        else:
            _setup_log(f"model pull  {message}")

    try:
        client.pull_model(
            model,
            on_status=on_status,
            on_progress=on_progress,
        )
    except Exception as exc:
        print(f"Download failed: {exc}", file=out_stream, flush=True)
        _setup_log(f"model pull failed  name={model}  error={exc}")
        return "error"
    print("Done.", file=out_stream, flush=True)
    _setup_log(f"model pull complete  name={model}")
    return "pulled"


def _setup_log(text: str) -> None:
    try:
        from setup_progress import write_setup_log
    except ImportError:
        return
    write_setup_log(text)


def _report_model_progress(completed: int, total: int) -> None:
    """Reuse engine-download log ticks (5% / 5s) without flipping the setup overlay."""
    try:
        from setup_progress import LOG_INTERVAL_SECONDS, LOG_PERCENT_STEP, write_setup_log
    except ImportError:
        return
    state = _report_model_progress.state  # type: ignore[attr-defined]
    now = time.monotonic()
    pct = min(100, int(100 * completed / total)) if total else None
    last_at = state.get("last_at")
    last_pct = state.get("last_pct", -LOG_PERCENT_STEP)
    last_bytes = int(state.get("last_bytes") or 0)
    started = state.get("started") or now
    state["started"] = started
    should = last_at is None
    if pct is not None and pct >= last_pct + LOG_PERCENT_STEP:
        should = True
    if pct is not None and pct >= 100:
        should = True
    if last_at is not None and (now - last_at) >= LOG_INTERVAL_SECONDS:
        should = True
    if not should:
        return
    dt = (now - last_at) if last_at is not None else (now - started)
    delta = max(0, completed - last_bytes)
    speed = (delta / dt / (1024 * 1024)) if dt > 0 else 0.0
    elapsed = max(0.0, now - started)
    if total:
        line = (
            f"model progress  {completed / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB "
            f"({pct}%)  elapsed={elapsed:.1f}s  speed={speed:.2f} MB/s"
        )
    else:
        line = (
            f"model progress  {completed / (1024 * 1024):.1f} MB  "
            f"elapsed={elapsed:.1f}s  speed={speed:.2f} MB/s"
        )
    write_setup_log(line)
    state["last_at"] = now
    state["last_pct"] = pct if pct is not None else last_pct
    state["last_bytes"] = completed


_report_model_progress.state = {"last_at": None, "last_pct": -5, "last_bytes": 0, "started": None}


def _model_already_installed(name: str, installed: list[str]) -> bool:
    try:
        from persona_cards import model_is_installed
    except ImportError:
        return name in installed
    return model_is_installed(name, installed)


def run_prefetch(
    *,
    data_dir: Path,
    host: str = "127.0.0.1:11434",
    stdout: TextIO | None = None,
    start=None,
    client_factory=None,
) -> int:
    """Vendored Ollama + starter model, then stop. No Flask, no browser.

    Uses ``start_managed_ollama`` and ``OllamaClient.pull_model`` as-is.
    """
    from ollama_client import OllamaClient
    from ollama_runtime import OllamaRuntimeError, start_managed_ollama

    try:
        from setup_progress import bind_setup_log_from_data_dir

        bind_setup_log_from_data_dir(data_dir)
    except ImportError:
        pass

    out = stdout if stdout is not None else sys.stdout
    starter = start or start_managed_ollama
    make_client = client_factory or (lambda url: OllamaClient(base_url=url))
    handle = None
    try:
        mode, url, handle = starter(data_dir, host=host)
    except OllamaRuntimeError as exc:
        print(f"Failed to start bundled Ollama: {exc}", file=sys.stderr)
        return 1
    try:
        if mode == "none" or not url:
            print(
                "Note: PortableAI does not bundle Ollama on this OS yet (Linux/macOS only). "
                "Install Ollama separately: https://ollama.com/download",
                file=out,
            )
            return 1
        if mode == "external":
            print(
                f"PORTABLEAI_EXTERNAL_OLLAMA_URL is set — skipping bundled Ollama, using {url}",
                file=out,
            )
        client = make_client(url)
        model = default_model_name()
        result = ensure_default_model(client, model, stdout=out)
        if result == "error":
            return 1
        print("Prefetch complete.", file=out, flush=True)
        return 0
    finally:
        if handle is not None:
            handle.stop()
