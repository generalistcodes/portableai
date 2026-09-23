"""
Minimal client for the local Ollama REST API (default: http://localhost:11434).

Deliberately thin: no chat streaming, no auth. Model pulls retry a few
times on transient connection/timeout failures (CDN anycast flakes are
common against ollama.com registry blob URLs). This is a teaching client
for the blog post, not a production SDK -- Ollama already ships an
official Python library if you want that.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 120  # seconds; local inference on CPU can be slow

INCOMPLETE_RESPONSE_MESSAGE = "Ollama returned an incomplete or invalid response."

# Transient registry/CDN failures (e.g. a bad Cloudflare anycast edge)
# often clear after a short pause and a fresh DNS lookup.
PULL_MAX_ATTEMPTS = 3
PULL_BACKOFF_SECONDS = (2, 5, 10)


class OllamaError(RuntimeError):
    """Raised when Ollama returns a non-2xx response."""


def _response_json(resp: requests.Response):
    """Parse JSON, wrapping a truncated/malformed body as OllamaError.

    Killing Ollama mid-response often yields a partial body and
    json.JSONDecodeError (or requests' subclass) instead of ConnectionError.
    """
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise OllamaError(INCOMPLETE_RESPONSE_MESSAGE) from exc


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def is_available(self) -> bool:
        """Quick health check -- used by tests to skip integration tests
        gracefully when Ollama isn't running locally."""
        try:
            resp = requests.get(self._url("/api/tags"), timeout=3)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list[dict]:
        resp = requests.get(self._url("/api/tags"), timeout=self.timeout)
        if resp.status_code != 200:
            raise OllamaError(f"GET /api/tags failed: {resp.status_code} {resp.text}")
        return _response_json(resp).get("models", [])

    def create_model(self, payload: dict) -> None:
        """payload is the structured dict from Persona.to_create_payload()."""
        body = dict(payload)
        body.setdefault("stream", False)
        resp = requests.post(self._url("/api/create"), json=body, timeout=self.timeout)
        if resp.status_code != 200:
            raise OllamaError(f"POST /api/create failed: {resp.status_code} {resp.text}")

    def chat(self, model: str, messages: list[dict], options: dict | None = None) -> str:
        """Send a chat request and return the assistant's reply text."""
        body = {"model": model, "messages": messages, "stream": False}
        if options:
            body["options"] = options
        resp = requests.post(self._url("/api/chat"), json=body, timeout=self.timeout)
        if resp.status_code != 200:
            raise OllamaError(f"POST /api/chat failed: {resp.status_code} {resp.text}")
        data = _response_json(resp)
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaError(f"unexpected /api/chat response shape: {data!r}") from exc

    def delete_model(self, name: str) -> None:
        resp = requests.delete(self._url("/api/delete"), json={"model": name}, timeout=self.timeout)
        if resp.status_code not in (200, 404):
            raise OllamaError(f"DELETE /api/delete failed: {resp.status_code} {resp.text}")

    def _pull_model_once(
        self,
        name: str,
        timeout: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Single POST /api/pull. Stream NDJSON when ``on_progress`` is set."""
        stream = on_progress is not None
        try:
            resp = requests.post(
                self._url("/api/pull"),
                json={"name": name, "stream": stream},
                stream=stream,
                timeout=timeout or 1800,  # 30 minutes; large models are large
            )
        except requests.Timeout as exc:
            raise OllamaError(f"POST /api/pull timed out: {exc}") from exc
        except requests.ConnectionError as exc:
            raise OllamaError(f"POST /api/pull connection failed: {exc}") from exc
        except requests.RequestException as exc:
            raise OllamaError(f"POST /api/pull request failed: {exc}") from exc
        if resp.status_code != 200:
            raise OllamaError(f"POST /api/pull failed: {resp.status_code} {resp.text}")
        if not stream:
            data = _response_json(resp)
            status = str(data.get("status", ""))
            if "error" in status.lower():
                raise OllamaError(f"pull failed: {status}")
            return
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OllamaError(INCOMPLETE_RESPONSE_MESSAGE) from exc
            err = data.get("error")
            if err:
                raise OllamaError(f"pull failed: {err}")
            status = str(data.get("status", ""))
            if "error" in status.lower():
                raise OllamaError(f"pull failed: {status}")
            completed = data.get("completed")
            total = data.get("total")
            if on_progress is not None and completed is not None and total:
                on_progress(int(completed), int(total))

    def iter_pull_model(
        self,
        name: str,
        timeout: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Pull a model, yielding status dicts then a final ``{"pulled": name}``.

        Retries transient connection/timeout failures up to
        ``PULL_MAX_ATTEMPTS`` with backoff ``PULL_BACKOFF_SECONDS``.
        Non-transient errors raise ``OllamaError`` immediately.
        """
        last_error: OllamaError | None = None
        for attempt in range(1, PULL_MAX_ATTEMPTS + 1):
            try:
                self._pull_model_once(name, timeout=timeout, on_progress=on_progress)
                yield {"pulled": name}
                return
            except OllamaError as exc:
                if not _is_transient_pull_error(exc) or attempt >= PULL_MAX_ATTEMPTS:
                    raise
                last_error = exc
            next_attempt = attempt + 1
            message = (
                f"Connection issue, retrying ({next_attempt}/{PULL_MAX_ATTEMPTS})..."
            )
            yield {
                "status": "retrying",
                "message": message,
                "attempt": next_attempt,
                "max_attempts": PULL_MAX_ATTEMPTS,
            }
            time.sleep(PULL_BACKOFF_SECONDS[attempt - 1])
        # Unreachable: loop either returns or raises. Keep mypy/readers happy.
        raise last_error or OllamaError(f"pull failed after {PULL_MAX_ATTEMPTS} attempts")

    def pull_model(
        self,
        name: str,
        timeout: int | None = None,
        *,
        on_status: Callable[..., None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Download a model from the Ollama library (ollama.com/library).

        Blocks until the download finishes -- for multi-GB models that can
        take many minutes, so this uses a much longer default timeout than
        chat/create. Transient connection failures are retried a few times
        (see ``PULL_MAX_ATTEMPTS``); ``on_status`` receives user-facing
        retry messages when a retry is about to happen.

        ``on_progress(completed, total)`` is called for Ollama NDJSON
        byte ticks when a progress callback is provided (stream=True).
        """
        for event in self.iter_pull_model(
            name, timeout=timeout, on_progress=on_progress
        ):
            if event.get("status") == "retrying" and on_status is not None:
                on_status(
                    event["message"],
                    attempt=event.get("attempt"),
                    max_attempts=event.get("max_attempts"),
                )


def _is_transient_pull_error(exc: BaseException) -> bool:
    """True for connection/timeout flakes worth retrying on a fresh attempt."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    text = str(exc).lower()
    needles = (
        "connection",
        "timeout",
        "timed out",
        "temporarily",
        "reset by peer",
        "broken pipe",
        "i/o timeout",
        "eof",
        "unavailable",
        "connecterror",
    )
    return any(n in text for n in needles)
