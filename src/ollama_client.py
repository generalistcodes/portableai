"""
Minimal client for the local Ollama REST API (default: http://localhost:11434).

Deliberately thin: no retries, no streaming support, no auth. This is a
teaching client for the blog post, not a production SDK -- Ollama already
ships an official Python library if you want that.
"""
from __future__ import annotations

import requests

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 120  # seconds; local inference on CPU can be slow


class OllamaError(RuntimeError):
    """Raised when Ollama returns a non-2xx response."""


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
        return resp.json().get("models", [])

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
        data = resp.json()
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaError(f"unexpected /api/chat response shape: {data!r}") from exc

    def delete_model(self, name: str) -> None:
        resp = requests.delete(self._url("/api/delete"), json={"model": name}, timeout=self.timeout)
        if resp.status_code not in (200, 404):
            raise OllamaError(f"DELETE /api/delete failed: {resp.status_code} {resp.text}")

    def pull_model(self, name: str, timeout: int | None = None) -> None:
        """Download a model from the Ollama library (ollama.com/library).

        Blocks until the download finishes -- for multi-GB models that can
        take many minutes, so this uses a much longer default timeout than
        chat/create. There's no progress reporting here: Ollama's /api/pull
        supports a streaming mode (stream=True, newline-delimited JSON
        progress events) but this client uses stream=False for simplicity.
        A progress bar would need to consume that stream instead.
        """
        resp = requests.post(
            self._url("/api/pull"),
            json={"name": name, "stream": False},
            timeout=timeout or 1800,  # 30 minutes; large models are large
        )
        if resp.status_code != 200:
            raise OllamaError(f"POST /api/pull failed: {resp.status_code} {resp.text}")
        data = resp.json()
        status = str(data.get("status", ""))
        if "error" in status.lower():
            raise OllamaError(f"pull failed: {status}")
