import os
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ollama_runtime import (  # noqa: E402
    DARWIN_ARCHIVE,
    DEFAULT_HOST,
    EXTERNAL_OLLAMA_ENV,
    PINNED_VERSION,
    OllamaHandle,
    OllamaRuntimeError,
    archive_name,
    clear_macos_quarantine,
    download_start_message,
    download_url,
    ensure_and_start,
    ensure_binary,
    external_ollama_url,
    start_managed_ollama,
    start_serve,
    supported_on_this_os,
    tcp_port_free,
    warn_if_system_ollama,
    wait_until_ready,
)


def _fake_extract(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "ollama").write_bytes(b"fake-ollama")
    (dest_dir / "ollama").chmod(0o755)


def _fake_extract_darwin(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    cli = dest_dir / "Ollama.app" / "Contents" / "Resources" / "ollama"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_bytes(b"fake-darwin-ollama")
    cli.chmod(0o755)
    target = dest_dir / "ollama"
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(Path("Ollama.app") / "Contents" / "Resources" / "ollama")


def test_download_url_is_arch_specific():
    assert download_url("x86_64", "linux") == (
        f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}/"
        "ollama-linux-amd64.tar.zst"
    )
    assert download_url("amd64", "linux") == download_url("x86_64", "linux")
    assert download_url("aarch64", "linux") == (
        f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}/"
        "ollama-linux-arm64.tar.zst"
    )
    assert download_url("arm64", "linux") == download_url("aarch64", "linux")
    assert archive_name("x86_64", "linux") == "ollama-linux-amd64.tar.zst"


def test_download_url_darwin_uses_pinned_zip():
    assert archive_name(platform_name="darwin") == DARWIN_ARCHIVE
    assert download_url(platform_name="darwin") == (
        f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}/{DARWIN_ARCHIVE}"
    )


def test_download_url_windows_uses_official_zip_not_setup_exe():
    assert archive_name("AMD64", "win32") == "ollama-windows-amd64.zip"
    assert archive_name("x86_64", "win32") == "ollama-windows-amd64.zip"
    assert archive_name("ARM64", "win32") == "ollama-windows-arm64.zip"
    url = download_url("AMD64", "win32")
    assert url.endswith("/ollama-windows-amd64.zip")
    assert "OllamaSetup.exe" not in url
    assert ".tar.zst" not in url


def _fake_extract_windows(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "ollama.exe").write_bytes(b"fake-win-ollama")


def test_windows_missing_binary_downloads_official_zip(tmp_path):
    fetched = []

    def fake_fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(b"zip-bytes")

    path = ensure_binary(
        tmp_path,
        machine="AMD64",
        platform_name="win32",
        fetch=fake_fetch,
        extract=_fake_extract_windows,
    )
    assert fetched == [
        f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}/"
        "ollama-windows-amd64.zip"
    ]
    assert path == tmp_path / "ollama-bin" / "ollama.exe"
    assert path.is_file()
    assert (tmp_path / "ollama-bin" / "VERSION").read_text(encoding="utf-8").strip() == PINNED_VERSION


def test_extract_windows_zip_places_ollama_exe(tmp_path):
    import zipfile

    from ollama_runtime import _extract_archive

    archive = tmp_path / "ollama-windows-amd64.zip"
    dest = tmp_path / "ollama-bin"
    dest.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ollama.exe", b"mz-fake")
        zf.writestr("lib/ollama/llama-server.exe", b"runner")
    _extract_archive(archive, dest)
    assert (dest / "ollama.exe").read_bytes() == b"mz-fake"
    assert (dest / "lib" / "ollama" / "llama-server.exe").is_file()


def test_start_serve_windows_uses_exe_and_hidden_console(tmp_path, monkeypatch):
    import subprocess

    import ollama_runtime as runtime

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    dest = tmp_path / "ollama-bin"
    dest.mkdir()
    (dest / "ollama.exe").write_bytes(b"fake")
    (dest / "lib" / "ollama").mkdir(parents=True)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 99
        return proc

    runtime.start_serve(
        tmp_path,
        host="127.0.0.1:11435",
        popen=fake_popen,
        wait=lambda *a, **k: None,
    )
    assert captured["cmd"][0].endswith("ollama.exe")
    assert captured["cmd"][1] == "serve"
    flags = captured["kwargs"]["creationflags"]
    assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert "start_new_session" not in captured["kwargs"]
    assert "preexec_fn" not in captured["kwargs"]
    path_env = captured["kwargs"]["env"]["PATH"]
    assert str((tmp_path / "ollama-bin").resolve()) in path_env
    assert str((tmp_path / "ollama-bin" / "lib" / "ollama").resolve()) in path_env


def test_stop_windows_taskkills_process_tree(monkeypatch):
    import subprocess

    import ollama_runtime as runtime

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="ollama", timeout=0.01), None]
    seen = []

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    runtime.OllamaHandle(proc, DEFAULT_HOST, Path("/tmp"), Path("/tmp/ollama.exe")).stop(
        timeout=0.01
    )
    assert seen[0][:4] == ["taskkill", "/F", "/T", "/PID"]
    assert seen[0][4] == "4242"


def test_start_managed_ollama_vendors_on_windows(tmp_path, monkeypatch):
    fake = OllamaHandle(MagicMock(), "127.0.0.1:11436", tmp_path, tmp_path / "ollama.exe")

    def fake_ensure(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("ollama_runtime.ensure_and_start", fake_ensure)
    mode, url, handle = start_managed_ollama(tmp_path, env={}, platform_name="win32")
    assert mode == "bundled"
    assert handle is fake


def test_supported_on_linux_darwin_and_windows():
    assert supported_on_this_os("linux") is True
    assert supported_on_this_os("darwin") is True
    assert supported_on_this_os("win32") is True
    assert supported_on_this_os("freebsd") is False


def test_unsupported_architecture_raises():
    with pytest.raises(OllamaRuntimeError, match="linux amd64/arm64, macOS, and Windows"):
        download_url("ppc64le", "linux")
    with pytest.raises(OllamaRuntimeError, match="Windows amd64/arm64"):
        download_url("ppc64le", "win32")


@pytest.mark.parametrize(
    ("machine", "expected_archive"),
    [
        ("x86_64", "ollama-linux-amd64.tar.zst"),
        ("aarch64", "ollama-linux-arm64.tar.zst"),
    ],
)
def test_missing_binary_downloads_arch_specific_url(tmp_path, machine, expected_archive):
    fetched = []

    def fake_fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(b"archive")

    path = ensure_binary(
        tmp_path,
        machine=machine,
        platform_name="linux",
        fetch=fake_fetch,
        extract=_fake_extract,
    )

    assert fetched == [
        f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}/{expected_archive}"
    ]
    assert path == tmp_path / "ollama-bin" / "ollama"
    assert path.is_file()
    assert (tmp_path / "ollama-bin" / "VERSION").read_text(encoding="utf-8").strip() == PINNED_VERSION


def test_darwin_missing_binary_downloads_zip_and_clears_quarantine(tmp_path):
    fetched = []
    cleared = []

    def fake_fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(b"zip-bytes")

    path = ensure_binary(
        tmp_path,
        platform_name="darwin",
        fetch=fake_fetch,
        extract=_fake_extract_darwin,
        clear_quarantine=lambda install_dir: cleared.append(install_dir),
    )

    assert fetched == [
        f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}/{DARWIN_ARCHIVE}"
    ]
    assert path == tmp_path / "ollama-bin" / "ollama"
    assert path.is_symlink() or path.is_file()
    assert cleared == [tmp_path / "ollama-bin"]
    assert (tmp_path / "ollama-bin" / "VERSION").read_text(encoding="utf-8").strip() == PINNED_VERSION


def test_clear_macos_quarantine_runs_xattr(tmp_path):
    install = tmp_path / "ollama-bin"
    app = install / "Ollama.app" / "Contents" / "Resources"
    app.mkdir(parents=True)
    cli = app / "ollama"
    cli.write_bytes(b"x")
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    clear_macos_quarantine(install, run=fake_run)
    assert ["xattr", "-cr", str(install)] in seen
    assert any(cmd[:3] == ["xattr", "-d", "com.apple.quarantine"] for cmd in seen)


def test_existing_pinned_version_is_not_redownloaded(tmp_path):
    dest = tmp_path / "ollama-bin"
    dest.mkdir()
    (dest / "ollama").write_bytes(b"already-there")
    (dest / "VERSION").write_text(PINNED_VERSION + "\n", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise AssertionError("should not download or extract when the pin matches")

    path = ensure_binary(tmp_path, fetch=boom, extract=boom)
    assert path == dest / "ollama"
    assert (dest / "ollama").read_bytes() == b"already-there"


def test_mismatched_version_redownloads(tmp_path):
    dest = tmp_path / "ollama-bin"
    dest.mkdir()
    (dest / "ollama").write_bytes(b"old")
    (dest / "VERSION").write_text("v0.0.0\n", encoding="utf-8")
    fetched = []

    def fake_fetch(url, dest_path):
        fetched.append(url)
        dest_path.write_bytes(b"archive")

    ensure_binary(
        tmp_path,
        machine="x86_64",
        platform_name="linux",
        fetch=fake_fetch,
        extract=_fake_extract,
    )
    assert fetched
    assert (dest / "VERSION").read_text(encoding="utf-8").strip() == PINNED_VERSION


def test_start_serve_sets_ollama_models_on_child_only(tmp_path, monkeypatch):
    dest = tmp_path / "ollama-bin"
    dest.mkdir()
    (dest / "ollama").write_bytes(b"fake")
    (dest / "VERSION").write_text(PINNED_VERSION + "\n", encoding="utf-8")

    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 4242
        return proc

    handle = start_serve(
        tmp_path,
        host="127.0.0.1:11435",
        popen=fake_popen,
        wait=lambda *a, **k: None,
    )

    expected_models = str((tmp_path / "ollama-models").resolve())
    assert captured["cmd"][0] == str(dest / "ollama")
    assert captured["cmd"][1] == "serve"
    assert captured["env"]["OLLAMA_MODELS"] == expected_models
    assert captured["env"]["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert "OLLAMA_MODELS" not in os.environ
    assert handle.base_url == "http://127.0.0.1:11435"
    assert handle.models_path == (tmp_path / "ollama-models").resolve()


def test_stop_terminates_subprocess():
    proc = MagicMock()
    proc.poll.return_value = None
    handle = OllamaHandle(proc, DEFAULT_HOST, Path("/tmp"), Path("/tmp/ollama"))
    handle.stop()
    proc.terminate.assert_called_once()
    proc.wait.assert_called()


def test_wait_until_ready_errors_if_process_exits():
    proc = MagicMock()
    proc.poll.return_value = 1
    proc.returncode = 1
    with pytest.raises(OllamaRuntimeError, match="exited before becoming ready"):
        wait_until_ready("http://127.0.0.1:11434", proc, timeout=1)


def test_warns_when_system_ollama_is_on_path(capsys):
    found = warn_if_system_ollama(which=lambda _name: "/usr/local/bin/ollama")
    assert found == "/usr/local/bin/ollama"
    out = capsys.readouterr().out
    assert "system-wide `ollama` is on PATH" in out
    assert "bundled copy" in out
    assert warn_if_system_ollama(which=lambda _name: None) is None


def test_download_start_message_states_amd64_size_and_once():
    msg = download_start_message("ollama-linux-amd64.tar.zst")
    assert PINNED_VERSION in msg
    assert "~1.4GB" in msg
    assert "this happens once" in msg
    assert "CUDA" in msg
    arm = download_start_message("ollama-linux-arm64.tar.zst")
    assert "this happens once" in arm
    assert "~1.4GB" not in arm
    mac = download_start_message(DARWIN_ARCHIVE)
    assert "macOS" in mac
    assert "~190MB" in mac
    assert "this happens once" in mac


def test_download_message_prints_before_fetch_starts(tmp_path, capsys):
    seen = {}

    def fake_fetch(_url, dest):
        seen["before_fetch"] = capsys.readouterr().out
        dest.write_bytes(b"archive")

    ensure_binary(
        tmp_path,
        machine="x86_64",
        platform_name="linux",
        fetch=fake_fetch,
        extract=_fake_extract,
    )

    banner = seen["before_fetch"]
    assert "Downloading Ollama" in banner
    assert PINNED_VERSION in banner
    assert "~1.4GB" in banner
    assert "this happens once" in banner


def test_existing_pinned_version_does_not_print_download_banner(tmp_path, capsys):
    dest = tmp_path / "ollama-bin"
    dest.mkdir()
    (dest / "ollama").write_bytes(b"already-there")
    (dest / "VERSION").write_text(PINNED_VERSION + "\n", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise AssertionError("should not download or extract when the pin matches")

    ensure_binary(tmp_path, fetch=boom, extract=boom)
    out = capsys.readouterr().out
    assert "Downloading" not in out


def test_fetch_retries_urlerror_then_succeeds(tmp_path, capsys):
    import io
    import urllib.error
    from unittest.mock import MagicMock, patch

    from ollama_runtime import _fetch

    dest = tmp_path / "archive.tar.zst"
    body = b"ollama-bytes"
    ok_resp = MagicMock()
    ok_resp.headers = {"Content-Length": str(len(body))}
    ok_resp.read.side_effect = [body, b""]
    ok_resp.__enter__ = lambda self: self
    ok_resp.__exit__ = lambda *args: False

    with (
        patch("ollama_runtime.time.sleep") as mock_sleep,
        patch(
            "ollama_runtime.urllib.request.urlopen",
            side_effect=[urllib.error.URLError("timed out"), ok_resp],
        ) as mock_open,
    ):
        _fetch("https://example.test/ollama.tar.zst", dest)

    assert dest.read_bytes() == body
    assert mock_open.call_count == 2
    mock_sleep.assert_called_once_with(2)
    assert "Connection issue, retrying (2/3)..." in capsys.readouterr().out
    log = (tmp_path / "setup.log").read_text(encoding="utf-8")
    assert "retry  attempt 2/3" in log
    assert "URLError" in log
    assert "progress" in log
    assert "download complete" in log


def test_ensure_and_start_falls_back_when_preferred_port_is_taken(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("ollama_runtime.ensure_binary", lambda *a, **k: None)
    monkeypatch.setattr("ollama_runtime.warn_if_system_ollama", lambda **k: None)

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    preferred = None
    for candidate in range(18134, 18234):
        try:
            holder.bind(("127.0.0.1", candidate))
            holder.listen(1)
            preferred = candidate
            break
        except OSError:
            continue
    assert preferred is not None
    captured = []

    def fake_start(data_dir, *, host, **kwargs):
        captured.append(host)
        proc = MagicMock()
        proc.poll.return_value = None
        return OllamaHandle(proc, host, Path("/tmp"), Path("/tmp/ollama"))

    try:
        assert tcp_port_free("127.0.0.1", preferred) is False
        handle = ensure_and_start(
            tmp_path,
            host=f"127.0.0.1:{preferred}",
            start=fake_start,
            which=lambda _name: None,
        )
        assert captured == [f"127.0.0.1:{preferred + 1}"]
        assert handle.host == f"127.0.0.1:{preferred + 1}"
        assert handle.base_url == f"http://127.0.0.1:{preferred + 1}"
        out = capsys.readouterr().out
        assert f"Port {preferred} was already in use" in out
        assert f"bundled Ollama is running on {preferred + 1} instead" in out
    finally:
        holder.close()


def test_external_ollama_url_empty_is_none():
    assert external_ollama_url({}) is None
    assert external_ollama_url({EXTERNAL_OLLAMA_ENV: ""}) is None
    assert external_ollama_url({EXTERNAL_OLLAMA_ENV: "   "}) is None
    assert (
        external_ollama_url({EXTERNAL_OLLAMA_ENV: "http://127.0.0.1:19999"})
        == "http://127.0.0.1:19999"
    )


def test_start_managed_ollama_skips_vendor_when_override_set(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("ensure_and_start should not run for an explicit override")

    monkeypatch.setattr("ollama_runtime.ensure_and_start", boom)
    mode, url, handle = start_managed_ollama(
        tmp_path,
        env={EXTERNAL_OLLAMA_ENV: "http://127.0.0.1:19999/"},
    )
    assert mode == "external"
    assert url == "http://127.0.0.1:19999"
    assert handle is None


def test_start_managed_ollama_vendors_when_override_unset(tmp_path, monkeypatch):
    fake = OllamaHandle(MagicMock(), "127.0.0.1:11436", tmp_path, tmp_path / "ollama")

    def fake_ensure(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("ollama_runtime.ensure_and_start", fake_ensure)
    mode, url, handle = start_managed_ollama(tmp_path, env={})
    assert mode == "bundled"
    assert url == "http://127.0.0.1:11436"
    assert handle is fake


def test_fetch_writes_timestamped_progress_to_setup_log(tmp_path):
    """Real HTTP download: setup.log must gain timestamped speed ticks as bytes arrive."""
    import http.server
    import re
    import threading
    import time

    from setup_progress import reset, set_setup_log_path

    from ollama_runtime import _fetch

    reset()
    payload = os.urandom(3 * 1024 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            step = 1024 * 1024
            for offset in range(0, len(payload), step):
                self.wfile.write(payload[offset : offset + step])
                self.wfile.flush()
                time.sleep(0.05)

        def log_message(self, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    dest = tmp_path / "ollama-bin" / "archive.bin"
    dest.parent.mkdir()
    try:
        _fetch(f"http://127.0.0.1:{server.server_address[1]}/archive.bin", dest)
    finally:
        server.shutdown()
    assert dest.read_bytes() == payload
    log_path = tmp_path / "setup.log"
    text = log_path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines, "setup.log stayed empty"
    stamp = re.compile(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC  "
    )
    assert all(stamp.match(line) for line in lines)
    progress_lines = [line for line in lines if "  progress  " in line]
    assert len(progress_lines) >= 2
    assert any("speed=" in line and "MB/s" in line for line in progress_lines)
    assert any("elapsed=" in line for line in progress_lines)
    assert "download complete" in text
    set_setup_log_path(None)
