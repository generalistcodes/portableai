import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ollama_runtime import (  # noqa: E402
    DARWIN_ARCHIVE,
    DEFAULT_HOST,
    PINNED_VERSION,
    OllamaHandle,
    OllamaRuntimeError,
    archive_name,
    clear_macos_quarantine,
    download_start_message,
    download_url,
    ensure_binary,
    start_serve,
    supported_on_this_os,
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


def test_supported_on_linux_and_darwin_only():
    assert supported_on_this_os("linux") is True
    assert supported_on_this_os("darwin") is True
    assert supported_on_this_os("win32") is False


def test_unsupported_architecture_raises():
    with pytest.raises(OllamaRuntimeError, match="linux amd64/arm64 and macOS"):
        download_url("ppc64le", "linux")


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
