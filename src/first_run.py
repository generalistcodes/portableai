"""First-run helpers: interactive model prompt and waiting for Flask.

The terminal Y/n prompt is a fast path for people who launched
PortableAI from a real terminal. It is skipped whenever stdin or stdout
is not a TTY (double-click, piped, redirected) so we never block on a
stdin that will not get a keystroke. The web onboarding screen is the
fallback; both paths pull the same model into the same Ollama store and
agree afterward via GET /api/models.
"""
from __future__ import annotations

import socket
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

DEFAULT_MODEL_NAME = "llama3.2:3b"
DEFAULT_MODEL_SIZE = "~2 GB"
DEFAULT_MODEL_BLURB = "good general-purpose default"

PROMPT_HEADER = "No models installed yet."
PROMPT_RECOMMEND = (
    f"Recommended: {DEFAULT_MODEL_NAME} ({DEFAULT_MODEL_SIZE}) -- {DEFAULT_MODEL_BLURB}"
)
PROMPT_QUESTION = "Download it now? [Y/n]: "


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

    print(PROMPT_HEADER, file=out_stream, flush=True)
    print(PROMPT_RECOMMEND, file=out_stream, flush=True)
    read_line = input_fn if input_fn is not None else input
    try:
        answer = read_line(PROMPT_QUESTION)
    except EOFError:
        print(file=out_stream)
        return "skipped"

    if answer.strip().lower() in ("n", "no"):
        return "declined"

    print(
        f"Downloading {DEFAULT_MODEL_NAME} ({DEFAULT_MODEL_SIZE})...",
        file=out_stream,
        flush=True,
    )
    progress_state = {"last": -5}

    def on_progress(completed: int, total: int) -> None:
        _print_pull_progress(completed, total, out_stream, progress_state)

    def on_status(message: str, **_kwargs: Any) -> None:
        print(f"  {message}", file=out_stream, flush=True)

    try:
        client.pull_model(
            DEFAULT_MODEL_NAME,
            on_status=on_status,
            on_progress=on_progress,
        )
    except Exception as exc:
        print(f"Download failed: {exc}", file=out_stream, flush=True)
        return "error"
    print("Done.", file=out_stream, flush=True)
    return "pulled"
