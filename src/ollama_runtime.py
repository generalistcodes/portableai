"""
Linux-only bootstrap for a PortableAI-vendored Ollama binary.

On first run this downloads a pinned standalone release from GitHub into
data/ollama-bin/, points OLLAMA_MODELS at data/ollama-models/, and runs
`ollama serve` as a child of PortableAI. macOS and Windows are
intentionally not handled here.

The official Linux archives are currently `.tar.zst` (older releases used
`.tgz`). We pin an exact GitHub tag in PINNED_VERSION / data/ollama-bin/VERSION
so later starts do not silently drift to a newer download.
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PINNED_VERSION = "v0.34.0"
RELEASE_BASE = f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}"
DEFAULT_HOST = "127.0.0.1:11434"
READY_TIMEOUT_SECONDS = 60.0

_ARCHIVE_BY_MACHINE = {
    "x86_64": "ollama-linux-amd64.tar.zst",
    "amd64": "ollama-linux-amd64.tar.zst",
    "aarch64": "ollama-linux-arm64.tar.zst",
    "arm64": "ollama-linux-arm64.tar.zst",
}

SYSTEM_OLLAMA_NOTICE = (
    "A system-wide `ollama` is on PATH, but PortableAI is using its own "
    "bundled copy in data/ollama-bin/ instead — isolated from the system install."
)


class OllamaRuntimeError(RuntimeError):
    """Raised when the bundled Ollama binary cannot be downloaded, started, or stopped."""


class OllamaHandle:
    """A running `ollama serve` child started by PortableAI."""

    def __init__(self, process: subprocess.Popen, host: str, models_path: Path, binary: Path):
        self.process = process
        self.host = host
        self.models_path = models_path
        self.binary = binary

    @property
    def base_url(self) -> str:
        return f"http://{self.host}"

    def stop(self, timeout: float = 8.0) -> None:
        proc = self.process
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def supported_on_this_os(platform_name: str | None = None) -> bool:
    return (platform_name or sys.platform).startswith("linux")


def bin_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "ollama-bin"


def models_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "ollama-models"


def binary_path(data_dir: Path) -> Path:
    return bin_dir(data_dir) / "ollama"


def version_path(data_dir: Path) -> Path:
    return bin_dir(data_dir) / "VERSION"


def archive_name(machine: str | None = None) -> str:
    raw = (machine or platform.machine() or "").strip()
    key = raw.lower()
    name = _ARCHIVE_BY_MACHINE.get(key)
    if name is None:
        raise OllamaRuntimeError(
            f"PortableAI's bundled Ollama supports linux amd64 and arm64; "
            f"this machine reports {raw!r}."
        )
    return name


def download_url(machine: str | None = None) -> str:
    return f"{RELEASE_BASE}/{archive_name(machine)}"


def download_start_message(archive_filename: str) -> str:
    """User-facing banner printed *before* the HTTP download begins."""
    if "amd64" in archive_filename:
        return (
            f"Downloading Ollama {PINNED_VERSION} (~1.4GB)... this happens once "
            "and may take a few minutes.\n"
            "The official amd64 archive includes CUDA libraries even though "
            "PortableAI does not wire GPU."
        )
    return (
        f"Downloading Ollama {PINNED_VERSION} ({archive_filename})... this happens once "
        "and may take a few minutes."
    )


def is_pinned_install(data_dir: Path) -> bool:
    binary = binary_path(data_dir)
    pinned = version_path(data_dir)
    if not binary.is_file() or not pinned.is_file():
        return False
    return pinned.read_text(encoding="utf-8").strip() == PINNED_VERSION


def warn_if_system_ollama(*, which=shutil.which) -> str | None:
    """Print a notice when a system `ollama` exists — we never silently ignore it."""
    found = which("ollama")
    if not found:
        return None
    print(SYSTEM_OLLAMA_NOTICE)
    print(f"  system binary: {found}")
    return found


def ensure_binary(
    data_dir: Path,
    *,
    machine: str | None = None,
    fetch=None,
    extract=None,
) -> Path:
    """Return data/ollama-bin/ollama, downloading the pinned release if needed."""
    dest = bin_dir(data_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if is_pinned_install(data_dir):
        return binary_path(data_dir)

    url = download_url(machine)
    archive = dest / archive_name(machine)
    print(download_start_message(archive.name), flush=True)
    try:
        (fetch or _fetch)(url, archive)
        (extract or _extract_archive)(archive, dest)
    finally:
        archive.unlink(missing_ok=True)

    installed = binary_path(data_dir)
    if not installed.is_file():
        raise OllamaRuntimeError(
            f"extracted Ollama {PINNED_VERSION} but {installed} is missing"
        )
    version_path(data_dir).write_text(PINNED_VERSION + "\n", encoding="utf-8")
    print(f"Pinned bundled Ollama {PINNED_VERSION} at {installed}")
    return installed


def start_serve(
    data_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    popen=subprocess.Popen,
    wait=None,
    ready_timeout: float = READY_TIMEOUT_SECONDS,
) -> OllamaHandle:
    """Launch bundled `ollama serve` with OLLAMA_MODELS set on the child only."""
    binary = binary_path(data_dir)
    if not binary.is_file():
        raise OllamaRuntimeError(f"bundled Ollama binary not found at {binary}")

    models = models_dir(data_dir)
    models.mkdir(parents=True, exist_ok=True)
    models_abs = str(models.resolve())

    child_env = os.environ.copy()
    child_env["OLLAMA_MODELS"] = models_abs
    child_env["OLLAMA_HOST"] = host
    _prepend_library_path(child_env, bin_dir(data_dir))

    log_path = bin_dir(data_dir) / "ollama.log"
    log_file = open(log_path, "ab")
    kwargs: dict = {
        "env": child_env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "start_new_session": True,
    }
    if sys.platform.startswith("linux"):
        kwargs["preexec_fn"] = _linux_pdeathsig

    print(f"Starting bundled Ollama {PINNED_VERSION} at {host} (models: {models_abs})")
    try:
        proc = popen([str(binary), "serve"], **kwargs)
    finally:
        log_file.close()

    handle = OllamaHandle(proc, host, models.resolve(), binary)
    try:
        (wait or wait_until_ready)(handle.base_url, proc, timeout=ready_timeout)
    except BaseException:
        handle.stop()
        raise
    return handle


def wait_until_ready(base_url: str, proc: subprocess.Popen, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    url = base_url.rstrip("/") + "/api/tags"
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise OllamaRuntimeError(
                f"bundled Ollama exited before becoming ready (exit {proc.returncode}). "
                f"Check whether {base_url} is already in use, and see data/ollama-bin/ollama.log."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if getattr(resp, "status", 200) == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
        time.sleep(0.2)
    detail = f" Last error: {last_err}" if last_err else ""
    raise OllamaRuntimeError(
        f"bundled Ollama did not become ready at {url} within {int(timeout)}s.{detail}"
    )


def ensure_and_start(
    data_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    machine: str | None = None,
    fetch=None,
    extract=None,
    popen=subprocess.Popen,
    wait=None,
    which=shutil.which,
) -> OllamaHandle:
    if not supported_on_this_os():
        raise OllamaRuntimeError("bundled Ollama is only supported on Linux")
    warn_if_system_ollama(which=which)
    ensure_binary(data_dir, machine=machine, fetch=fetch, extract=extract)
    return start_serve(data_dir, host=host, popen=popen, wait=wait)


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
            total_header = resp.headers.get("Content-Length")
            total_n = int(total_header) if total_header and total_header.isdigit() else None
            if total_n:
                print(f"  {total_n / (1024 * 1024):.0f} MB from {url}", flush=True)
            read = 0
            last_print = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if total_n and read - last_print >= 64 * 1024 * 1024:
                    last_print = read
                    pct = 100 * read / total_n
                    print(
                        f"  ... {read / (1024 * 1024):.0f} / {total_n / (1024 * 1024):.0f} MB ({pct:.0f}%)",
                        flush=True,
                    )
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _extract_archive(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive.name
    if name.endswith(".tar.zst"):
        result = subprocess.run(
            ["tar", "--zstd", "-xf", str(archive), "-C", str(dest_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OllamaRuntimeError(
                f"failed to extract {name}: {result.stderr.strip() or result.stdout.strip()}"
            )
    elif name.endswith(".tgz") or name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            if sys.version_info >= (3, 12):
                tf.extractall(dest_dir, filter="data")
            else:
                tf.extractall(dest_dir)
    else:
        raise OllamaRuntimeError(f"unsupported Ollama archive format: {name}")
    _place_binary(dest_dir)


def _place_binary(dest_dir: Path) -> None:
    """Make sure data/ollama-bin/ollama exists (tarball layout is usually bin/ollama)."""
    target = dest_dir / "ollama"
    if target.is_file() and not target.is_symlink():
        target.chmod(0o755)
        return
    nested = dest_dir / "bin" / "ollama"
    if not nested.is_file():
        raise OllamaRuntimeError(f"extracted archive did not contain an ollama binary under {dest_dir}")
    nested.chmod(0o755)
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(Path("bin") / "ollama")


def _prepend_library_path(env: dict[str, str], install_dir: Path) -> None:
    extras = []
    for candidate in (install_dir / "lib" / "ollama", install_dir / "lib"):
        if candidate.is_dir():
            extras.append(str(candidate.resolve()))
    if not extras:
        return
    existing = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(extras + ([existing] if existing else []))


def _linux_pdeathsig() -> None:
    """SIGTERM this process when the parent dies, so `ollama serve` is not left orphaned."""
    import ctypes

    pr_set_pdeathsig = 1
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(pr_set_pdeathsig, signal.SIGTERM)
    except OSError:
        pass
