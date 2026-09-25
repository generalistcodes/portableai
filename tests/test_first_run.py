"""First-run TTY prompt, setup-progress snapshot, and launcher wiring."""
from __future__ import annotations

import io
import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from first_run import (  # noqa: E402
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_ENV,
    PREFETCH_ONLY_ENV,
    PROMPT_HEADER,
    PROMPT_QUESTION,
    default_model_name,
    ensure_default_model,
    interactive_terminal,
    maybe_prompt_default_model,
    prefetch_requested,
    recommended_model_from_catalog,
    run_prefetch,
    wait_for_tcp_port,
)
from setup_progress import (  # noqa: E402
    mark_ready,
    reset,
    set_phase,
    set_setup_log_path,
    snapshot,
    update_download,
)


@pytest.fixture(autouse=True)
def _reset_setup_progress():
    reset()
    set_setup_log_path(None)
    yield
    reset()
    set_setup_log_path(None)


class _FakeStd:
    def __init__(self, tty: bool) -> None:
        self._tty = tty
        self.buffer = io.StringIO()

    def isatty(self) -> bool:
        return self._tty

    def write(self, data: str) -> int:
        return self.buffer.write(data)

    def flush(self) -> None:
        return None


def test_interactive_terminal_requires_both_ttys():
    assert interactive_terminal(_FakeStd(True), _FakeStd(True)) is True
    assert interactive_terminal(_FakeStd(False), _FakeStd(True)) is False
    assert interactive_terminal(_FakeStd(True), _FakeStd(False)) is False
    assert interactive_terminal(_FakeStd(False), _FakeStd(False)) is False


def test_prompt_skipped_when_stdin_is_not_a_tty():
    client = MagicMock()
    called = []

    def boom(_prompt: str) -> str:
        called.append(True)
        raise AssertionError("input() must not be called without a TTY")

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(False),
        stdout=_FakeStd(True),
        input_fn=boom,
    )
    assert result == "skipped"
    client.list_models.assert_not_called()
    client.pull_model.assert_not_called()
    assert called == []


def test_prompt_skipped_when_stdout_is_not_a_tty():
    client = MagicMock()

    def boom(_prompt: str) -> str:
        raise AssertionError("input() must not be called without a TTY")

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=_FakeStd(False),
        input_fn=boom,
    )
    assert result == "skipped"
    client.pull_model.assert_not_called()


def test_prompt_yes_pulls_recommended_model():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []
    stdout = _FakeStd(True)

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=stdout,
        input_fn=lambda _p: "Y",
    )
    assert result == "pulled"
    client.pull_model.assert_called_once()
    args, kwargs = client.pull_model.call_args
    assert args[0] == DEFAULT_MODEL_NAME
    assert "on_progress" in kwargs
    out = stdout.buffer.getvalue()
    assert PROMPT_HEADER in out
    assert DEFAULT_MODEL_NAME in out


def test_prompt_enter_is_yes():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=_FakeStd(True),
        input_fn=lambda _p: "",
    )
    assert result == "pulled"
    client.pull_model.assert_called_once()


def test_prompt_no_skips_download():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=_FakeStd(True),
        input_fn=lambda _p: "n",
    )
    assert result == "declined"
    client.pull_model.assert_not_called()
    assert PROMPT_QUESTION.strip()


def test_prompt_eof_is_skipped_not_hung():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []

    def eof(_prompt: str) -> str:
        raise EOFError

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=_FakeStd(True),
        input_fn=eof,
    )
    assert result == "skipped"
    client.pull_model.assert_not_called()


def test_prompt_skipped_when_models_already_installed():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = [{"name": "qwen2.5:0.5b"}]

    def boom(_prompt: str) -> str:
        raise AssertionError("must not prompt when GET /api/models would be non-empty")

    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=_FakeStd(True),
        input_fn=boom,
    )
    assert result == "has_models"
    client.pull_model.assert_not_called()


def test_wait_for_tcp_port_detects_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert wait_for_tcp_port(port, timeout=2.0) is True
    finally:
        sock.close()


def test_wait_for_tcp_port_times_out_on_closed_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert wait_for_tcp_port(port, timeout=0.2) is False


def test_setup_progress_snapshot_marks_busy_during_download():
    assert snapshot()["busy"] is False
    assert snapshot()["phase"] == "ready"
    assert snapshot()["stalled"] is False
    update_download(50, 100)
    snap = snapshot()
    assert snap["busy"] is True
    assert snap["phase"] == "downloading_ollama"
    assert snap["percent"] == 50
    assert snap["stalled"] is False
    assert snap["setup_log"].endswith("setup.log")
    set_phase("extracting", message="Extracting Ollama")
    assert snapshot()["busy"] is True
    assert snapshot()["stalled"] is False
    mark_ready()
    assert snapshot()["busy"] is False


def test_setup_log_path_matches_data_dir(tmp_path):
    from setup_progress import bind_setup_log_from_data_dir, setup_log_path

    bind_setup_log_from_data_dir(tmp_path)
    assert setup_log_path() == tmp_path / "setup.log"
    assert snapshot()["setup_log"] == str((tmp_path / "setup.log").resolve())


def test_snapshot_marks_stalled_after_sixty_seconds_without_bytes(monkeypatch, tmp_path):
    from setup_progress import STALL_SECONDS, bind_setup_log_from_data_dir

    bind_setup_log_from_data_dir(tmp_path)
    clock = {"now": 1000.0}
    monkeypatch.setattr("setup_progress.time.monotonic", lambda: clock["now"])
    update_download(10 * 1024 * 1024, 100 * 1024 * 1024)
    assert snapshot()["stalled"] is False
    clock["now"] += STALL_SECONDS - 1
    assert snapshot()["stalled"] is False
    clock["now"] += 1
    snap = snapshot()
    assert snap["stalled"] is True
    assert snap["busy"] is True
    assert snap["message"] == "Download appears stalled -- check your connection"
    log = (tmp_path / "setup.log").read_text(encoding="utf-8")
    assert "stalled  no new bytes" in log
    assert "downloaded=" in log
    clock["now"] += 1
    update_download(20 * 1024 * 1024, 100 * 1024 * 1024)
    assert snapshot()["stalled"] is False
    assert snapshot()["message"] == "Downloading Ollama"


def test_stalled_after_real_pause_with_short_threshold(monkeypatch, tmp_path):
    """Live clock: freeze bytes, wait past the threshold, overlay must flip."""
    import setup_progress as sp

    sp.bind_setup_log_from_data_dir(tmp_path)
    monkeypatch.setattr(sp, "STALL_SECONDS", 0.4)
    update_download(1024, 10 * 1024 * 1024)
    assert snapshot()["stalled"] is False
    time.sleep(0.55)
    snap = snapshot()
    assert snap["stalled"] is True
    assert "stalled" in snap["message"].lower()


def test_run_py_starts_ui_before_ollama_and_gates_tty_prompt():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "wait_for_tcp_port" in text
    assert "webbrowser.open" in text
    assert text.index("ui_thread.start()") < text.index("start_managed_ollama(")
    assert "maybe_prompt_default_model" in text
    assert 'mode="none"' in text
    assert "interactive_terminal" in (ROOT / "src" / "first_run.py").read_text(encoding="utf-8")
    assert "sys.stdin.isatty" in (ROOT / "src" / "first_run.py").read_text(
        encoding="utf-8"
    ) or "isatty" in (ROOT / "src" / "first_run.py").read_text(encoding="utf-8")


def test_run_py_prefetch_exits_before_flask_and_browser():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "--prefetch-only" in text
    assert "PORTABLEAI_PREFETCH_ONLY" in text
    assert text.index("run_prefetch") < text.index("ui_thread.start()")
    assert text.index("prefetch_requested") < text.index("webbrowser.open")
    src = (ROOT / "src" / "first_run.py").read_text(encoding="utf-8")
    assert "start_managed_ollama" in src
    assert "pull_model" in src
    assert "webbrowser" not in src


def test_catalog_first_recommended_is_llama32_3b():
    catalog = [
        {"name": "llama3.2:1b"},
        {"name": "llama3.2:3b", "recommended": True},
        {"name": "qwen2.5:0.5b", "recommended": True},
    ]
    assert recommended_model_from_catalog(catalog) == "llama3.2:3b"
    assert default_model_name(env={}, catalog=catalog) == "llama3.2:3b"
    assert default_model_name(env={DEFAULT_MODEL_ENV: "qwen2.5:0.5b"}, catalog=catalog) == "qwen2.5:0.5b"


def test_prefetch_requested_flag_and_env():
    assert prefetch_requested(True, {}) is True
    assert prefetch_requested(False, {}) is False
    assert prefetch_requested(False, {PREFETCH_ONLY_ENV: "1"}) is True
    assert prefetch_requested(False, {PREFETCH_ONLY_ENV: "true"}) is True
    assert prefetch_requested(False, {PREFETCH_ONLY_ENV: "0"}) is False


def test_ensure_default_model_skips_when_installed():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = [{"name": DEFAULT_MODEL_NAME}]
    assert ensure_default_model(client, DEFAULT_MODEL_NAME, stdout=_FakeStd(True)) == "has_models"
    client.pull_model.assert_not_called()


def test_ensure_default_model_reuses_client_pull_model():
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []
    stdout = _FakeStd(True)
    assert ensure_default_model(client, DEFAULT_MODEL_NAME, stdout=stdout) == "pulled"
    client.pull_model.assert_called_once()
    args, kwargs = client.pull_model.call_args
    assert args[0] == DEFAULT_MODEL_NAME
    assert "on_progress" in kwargs
    assert DEFAULT_MODEL_NAME in stdout.buffer.getvalue()


def test_run_prefetch_uses_existing_runtime_and_pull_then_stops():
    handle = MagicMock()
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []
    stdout = _FakeStd(True)

    def fake_start(data_dir, host="127.0.0.1:11434"):
        return "bundled", "http://127.0.0.1:11435", handle

    code = run_prefetch(
        data_dir=Path("/tmp"),
        host="127.0.0.1:11434",
        stdout=stdout,
        start=fake_start,
        client_factory=lambda _url: client,
    )
    assert code == 0
    client.pull_model.assert_called_once()
    handle.stop.assert_called_once()
    assert "Prefetch complete." in stdout.buffer.getvalue()


def test_noninteractive_redirect_never_blocks_on_input():
    """stdin from a pipe is not a TTY; the prompt must return immediately."""
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.return_value = []
    started = time.monotonic()
    result = maybe_prompt_default_model(
        client,
        stdin=_FakeStd(False),
        stdout=_FakeStd(False),
        input_fn=lambda _p: time.sleep(30) or "Y",
    )
    elapsed = time.monotonic() - started
    assert result == "skipped"
    assert elapsed < 1.0
    client.pull_model.assert_not_called()


def test_prompt_and_web_share_list_models_inventory():
    """Terminal yes and web onboarding both key off the same list_models list."""
    client = MagicMock()
    client.is_available.return_value = True
    client.list_models.side_effect = [
        [],
        [],
        [{"name": DEFAULT_MODEL_NAME}],
    ]
    maybe_prompt_default_model(
        client,
        stdin=_FakeStd(True),
        stdout=_FakeStd(True),
        input_fn=lambda _p: "Y",
    )
    client.pull_model.assert_called_once()
    names = [m["name"] for m in client.list_models()]
    assert DEFAULT_MODEL_NAME in names
