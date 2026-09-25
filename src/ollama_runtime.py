"""
Bootstrap for a PortableAI-vendored Ollama binary (Linux, macOS, Windows).

On first run this downloads a pinned release into data/ollama-bin/,
points OLLAMA_MODELS at data/ollama-models/, and runs `ollama serve` as
a child of PortableAI.

Linux: GitHub `.tar.zst` archives (amd64/arm64).
macOS: pinned `Ollama-darwin.zip` (contains Ollama.app; CLI at
Ollama.app/Contents/Resources/ollama). After extract we clear
com.apple.quarantine so Gatekeeper does not block the first launch.
Windows: pinned `ollama-windows-amd64.zip` / `ollama-windows-arm64.zip`
(standalone `ollama.exe` at the zip root plus `lib/ollama/` DLLs —
not OllamaSetup.exe, which is the GUI installer).

We pin an exact GitHub tag in PINNED_VERSION / data/ollama-bin/VERSION
so later starts do not silently drift to a newer download.
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PINNED_VERSION = "v0.34.0"
RELEASE_BASE = f"https://github.com/ollama/ollama/releases/download/{PINNED_VERSION}"
DARWIN_ARCHIVE = "Ollama-darwin.zip"
# Verified against the v0.34.0 Ollama-darwin.zip contents on a real Mac:
# the CLI lives next to the GUI dylibs inside the app bundle.
DARWIN_CLI_RELATIVE = Path("Ollama.app") / "Contents" / "Resources" / "ollama"
# GitHub release zips (v0.34.0): ollama.exe at the archive root, CUDA/CPU
# libs under lib/ollama/. ollama.com/download's OllamaSetup.exe is a
# different artifact (Inno Setup installer) and is not used here.
DEFAULT_HOST = "127.0.0.1:11434"
OLLAMA_HOST_TRIES = 10
EXTERNAL_OLLAMA_ENV = "PORTABLEAI_EXTERNAL_OLLAMA_URL"
READY_TIMEOUT_SECONDS = 60.0

# Same idea as ollama_client model-pull retries: a flaky CDN edge often
# recovers after a short pause (and a fresh DNS lookup).
FETCH_MAX_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = (2, 5, 10)

_ARCHIVE_BY_MACHINE_LINUX = {
    "x86_64": "ollama-linux-amd64.tar.zst",
    "amd64": "ollama-linux-amd64.tar.zst",
    "aarch64": "ollama-linux-arm64.tar.zst",
    "arm64": "ollama-linux-arm64.tar.zst",
}

_ARCHIVE_BY_MACHINE_WINDOWS = {
    "amd64": "ollama-windows-amd64.zip",
    "x86_64": "ollama-windows-amd64.zip",
    "arm64": "ollama-windows-arm64.zip",
    "aarch64": "ollama-windows-arm64.zip",
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
            if sys.platform == "win32" and proc.pid:
                # terminate() is TerminateProcess and does not walk the
                # llama-server children Ollama spawns; /T does.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=5,
                )
                proc.wait(timeout=3)
                return
            proc.kill()
            proc.wait(timeout=3)


def _is_windows(platform_name: str | None = None) -> bool:
    name = (platform_name or sys.platform).lower()
    return name.startswith("win")


def supported_on_this_os(platform_name: str | None = None) -> bool:
    name = platform_name or sys.platform
    return name.startswith("linux") or name == "darwin" or _is_windows(name)


def external_ollama_url(env=None) -> str | None:
    """Explicit opt-in URL. Empty/unset means use the bundled Ollama."""
    raw = ((env or os.environ).get(EXTERNAL_OLLAMA_ENV) or "").strip()
    return raw or None


def start_managed_ollama(
    data_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    env=None,
    **kwargs,
):
    """Return (mode, url, handle).

    mode is ``external`` (skip vendor), ``bundled`` (we launched ollama),
    or ``none`` (this OS has no vendor path).
    """
    override = external_ollama_url(env)
    if override:
        return "external", override.rstrip("/"), None
    if not supported_on_this_os(kwargs.get("platform_name")):
        return "none", None, None
    handle = ensure_and_start(data_dir, host=host, **kwargs)
    return "bundled", handle.base_url, handle


def bin_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "ollama-bin"


def models_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "ollama-models"


def binary_path(data_dir: Path, platform_name: str | None = None) -> Path:
    name = "ollama.exe" if _is_windows(platform_name) else "ollama"
    return bin_dir(data_dir) / name


def version_path(data_dir: Path) -> Path:
    return bin_dir(data_dir) / "VERSION"


def darwin_app_path(data_dir: Path) -> Path:
    return bin_dir(data_dir) / "Ollama.app"


def archive_name(machine: str | None = None, platform_name: str | None = None) -> str:
    plat = platform_name or sys.platform
    if plat == "darwin":
        return DARWIN_ARCHIVE
    raw = (machine or platform.machine() or "").strip()
    key = raw.lower()
    if _is_windows(plat):
        name = _ARCHIVE_BY_MACHINE_WINDOWS.get(key)
        if name is None:
            raise OllamaRuntimeError(
                f"PortableAI's bundled Ollama supports Windows amd64/arm64; "
                f"this machine reports platform={plat!r} machine={raw!r}."
            )
        return name
    name = _ARCHIVE_BY_MACHINE_LINUX.get(key)
    if name is None:
        raise OllamaRuntimeError(
            f"PortableAI's bundled Ollama supports linux amd64/arm64, macOS, and Windows; "
            f"this machine reports platform={plat!r} machine={raw!r}."
        )
    return name


def download_url(machine: str | None = None, platform_name: str | None = None) -> str:
    return f"{RELEASE_BASE}/{archive_name(machine, platform_name)}"


def download_start_message(archive_filename: str) -> str:
    """User-facing banner printed *before* the HTTP download begins."""
    if archive_filename == DARWIN_ARCHIVE:
        return (
            f"Downloading Ollama {PINNED_VERSION} for macOS (~190MB)... this happens once "
            "and may take a few minutes."
        )
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


def is_pinned_install(data_dir: Path, platform_name: str | None = None) -> bool:
    binary = binary_path(data_dir, platform_name)
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
    platform_name: str | None = None,
    fetch=None,
    extract=None,
    clear_quarantine=None,
) -> Path:
    """Return data/ollama-bin/ollama, downloading the pinned release if needed."""
    dest = bin_dir(data_dir)
    dest.mkdir(parents=True, exist_ok=True)
    _bind_setup_log(Path(data_dir))
    plat = platform_name or sys.platform
    if is_pinned_install(data_dir, platform_name=plat):
        return binary_path(data_dir, plat)

    url = download_url(machine, plat)
    archive = dest / archive_name(machine, plat)
    print(download_start_message(archive.name), flush=True)
    _setup_log(f"engine download start  {download_start_message(archive.name)}")
    _setup_log(f"engine download url  {url}")
    _report_phase("downloading_ollama", message="Downloading Ollama")
    try:
        (fetch or _fetch)(url, archive)
        _report_phase("extracting", message="Extracting Ollama")
        _setup_log(f"extract start  archive={archive.name}")
        (extract or _extract_archive)(archive, dest)
        _setup_log("extract complete")
    except Exception as exc:
        _setup_log(f"engine setup failed  error={exc}")
        raise
    finally:
        archive.unlink(missing_ok=True)

    installed = binary_path(data_dir, plat)
    if not installed.is_file():
        raise OllamaRuntimeError(
            f"extracted Ollama {PINNED_VERSION} but {installed} is missing"
        )

    if plat == "darwin":
        clearer = clear_quarantine or clear_macos_quarantine
        clearer(dest)

    version_path(data_dir).write_text(PINNED_VERSION + "\n", encoding="utf-8")
    print(f"Pinned bundled Ollama {PINNED_VERSION} at {installed}")
    return installed


def clear_macos_quarantine(install_dir: Path, *, run=subprocess.run) -> None:
    """Strip com.apple.quarantine from the extracted app / CLI.

    Downloads from the browser (and urllib) get this attribute; without
    clearing it, the first `ollama serve` can hit a Gatekeeper block.
    We clear the whole install dir recursively so bundled dylibs are
    covered too. Failures are logged loudly — we do not pretend launch
    will succeed if xattr refused to run.
    """
    targets = [install_dir]
    app = install_dir / "Ollama.app"
    if app.is_dir():
        targets.append(app)
    cli = install_dir / DARWIN_CLI_RELATIVE
    if cli.exists():
        targets.append(cli)

    # Prefer recursive clear on the install tree; also try -d on known paths.
    result = run(
        ["xattr", "-cr", str(install_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        print(
            f"WARNING: could not recursively clear quarantine on {install_dir}: "
            f"{detail or f'exit {result.returncode}'}",
            flush=True,
        )

    for path in targets:
        if not path.exists():
            continue
        one = run(
            ["xattr", "-d", "com.apple.quarantine", str(path)],
            capture_output=True,
            text=True,
        )
        # exit 1 often means the attribute was already absent — not fatal.
        if one.returncode not in (0, 1):
            detail = (one.stderr or one.stdout or "").strip()
            print(
                f"WARNING: xattr -d com.apple.quarantine failed on {path}: "
                f"{detail or f'exit {one.returncode}'}",
                flush=True,
            )


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
    }
    if sys.platform == "win32":
        # POSIX start_new_session is a no-op here. CREATE_NEW_PROCESS_GROUP
        # lets us signal the tree; CREATE_NO_WINDOW hides the extra console
        # a double-clicked .exe would otherwise flash.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
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


def split_host_port(host: str) -> tuple[str, int]:
    """Parse `127.0.0.1:11434` into (hostname, port)."""
    raw = (host or DEFAULT_HOST).strip()
    if ":" not in raw:
        return raw, 11434
    name, port_s = raw.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError as exc:
        raise OllamaRuntimeError(f"invalid Ollama host {host!r}") from exc
    return (name or "127.0.0.1"), port


def tcp_port_free(hostname: str, port: int) -> bool:
    """True if we can bind this host:port (SO_REUSEADDR, same as Flask)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((hostname, int(port)))
        except OSError:
            return False
    return True


def ensure_and_start(
    data_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    machine: str | None = None,
    platform_name: str | None = None,
    fetch=None,
    extract=None,
    clear_quarantine=None,
    popen=subprocess.Popen,
    wait=None,
    which=shutil.which,
    port_tries: int = OLLAMA_HOST_TRIES,
    start=None,
) -> OllamaHandle:
    if not supported_on_this_os(platform_name):
        raise OllamaRuntimeError("bundled Ollama is only supported on Linux, macOS, and Windows")
    warn_if_system_ollama(which=which)
    ensure_binary(
        data_dir,
        machine=machine,
        platform_name=platform_name,
        fetch=fetch,
        extract=extract,
        clear_quarantine=clear_quarantine,
    )
    _report_phase("starting_ollama", message="Starting Ollama")
    hostname, preferred = split_host_port(host)
    starter = start or start_serve
    last_err: OllamaRuntimeError | None = None
    for offset in range(max(1, int(port_tries))):
        port = preferred + offset
        candidate = f"{hostname}:{port}"
        if not tcp_port_free(hostname, port):
            continue
        try:
            handle = starter(data_dir, host=candidate, popen=popen, wait=wait)
        except OllamaRuntimeError as exc:
            last_err = exc
            continue
        if port != preferred:
            print(
                f"Port {preferred} was already in use -- bundled Ollama is running on {port} instead."
            )
        return handle
    if last_err is not None:
        raise last_err
    raise OllamaRuntimeError(
        f"bundled Ollama could not bind any port in {preferred}–{preferred + max(1, int(port_tries)) - 1} "
        f"(preferred {host} is in use)."
    )


def _fetch(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``, retrying transient connection failures."""
    _bind_setup_log_from_dest(dest)
    last_error: BaseException | None = None
    for attempt in range(1, FETCH_MAX_ATTEMPTS + 1):
        try:
            _fetch_once(url, dest)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= FETCH_MAX_ATTEMPTS:
                _setup_log(
                    f"download failed after {FETCH_MAX_ATTEMPTS} attempts  "
                    f"error={type(exc).__name__}: {exc}"
                )
                break
            print(
                f"Connection issue, retrying ({attempt + 1}/{FETCH_MAX_ATTEMPTS})...",
                flush=True,
            )
            _note_retry(attempt + 1, FETCH_MAX_ATTEMPTS, exc)
            time.sleep(FETCH_BACKOFF_SECONDS[attempt - 1])
    assert last_error is not None
    raise last_error


def _report_phase(phase: str, *, message: str = "") -> None:
    try:
        from setup_progress import set_phase
    except ImportError:
        return
    set_phase(phase, message=message)


def _bind_setup_log(data_dir: Path) -> None:
    try:
        from setup_progress import bind_setup_log_from_data_dir
    except ImportError:
        return
    bind_setup_log_from_data_dir(data_dir)


def _bind_setup_log_from_dest(dest: Path) -> None:
    try:
        from setup_progress import bind_setup_log_from_dest
    except ImportError:
        return
    bind_setup_log_from_dest(dest)


def _setup_log(text: str) -> None:
    try:
        from setup_progress import write_setup_log
    except ImportError:
        return
    write_setup_log(text)


def _note_retry(attempt: int, max_attempts: int, error: BaseException) -> None:
    try:
        from setup_progress import note_retry
    except ImportError:
        _setup_log(
            f"retry  attempt {attempt}/{max_attempts}  "
            f"error={type(error).__name__}: {error}"
        )
        return
    note_retry(attempt, max_attempts, error)


def _report_download(bytes_downloaded: int, bytes_total: int | None) -> None:
    try:
        from setup_progress import update_download
    except ImportError:
        return
    update_download(bytes_downloaded, bytes_total)


def _fetch_once(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
            total_header = resp.headers.get("Content-Length")
            total_n = int(total_header) if total_header and total_header.isdigit() else None
            if total_n:
                print(f"  {total_n / (1024 * 1024):.0f} MB from {url}", flush=True)
                _setup_log(f"download size  {total_n / (1024 * 1024):.1f} MB from {url}")
            read = 0
            last_print = 0
            _report_download(0, total_n)
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                _report_download(read, total_n)
                if total_n and read - last_print >= 64 * 1024 * 1024:
                    last_print = read
                    pct = 100 * read / total_n
                    print(
                        f"  ... {read / (1024 * 1024):.0f} / {total_n / (1024 * 1024):.0f} MB ({pct:.0f}%)",
                        flush=True,
                    )
        tmp.replace(dest)
        _setup_log(
            f"download complete  {read / (1024 * 1024):.1f} MB -> {dest}"
        )
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
        _place_binary_linux(dest_dir)
    elif name.endswith(".tgz") or name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            if sys.version_info >= (3, 12):
                tf.extractall(dest_dir, filter="data")
            else:
                tf.extractall(dest_dir)
        _place_binary_linux(dest_dir)
    elif name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest_dir)
        if "darwin" in name.lower() or (dest_dir / "Ollama.app").is_dir():
            _place_binary_darwin(dest_dir)
        else:
            _place_binary_windows(dest_dir)
    else:
        raise OllamaRuntimeError(f"unsupported Ollama archive format: {name}")


def _place_binary_windows(dest_dir: Path) -> None:
    """Make sure data/ollama-bin/ollama.exe exists (zip root is the usual layout)."""
    target = dest_dir / "ollama.exe"
    if target.is_file():
        return
    nested = dest_dir / "bin" / "ollama.exe"
    if nested.is_file():
        shutil.copy2(nested, target)
        return
    found = next((p for p in dest_dir.rglob("ollama.exe") if p.is_file()), None)
    if found is None:
        raise OllamaRuntimeError(
            f"extracted archive did not contain ollama.exe under {dest_dir}"
        )
    shutil.copy2(found, target)


def _place_binary_linux(dest_dir: Path) -> None:
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


def _place_binary_darwin(dest_dir: Path) -> None:
    """Locate the CLI inside Ollama.app and expose it as data/ollama-bin/ollama."""
    cli = dest_dir / DARWIN_CLI_RELATIVE
    if not cli.is_file():
        # Defensive discovery if Apple/Ollama moves the layout.
        found = None
        for candidate in dest_dir.rglob("ollama"):
            if candidate.is_file() and candidate.name == "ollama" and "Contents/Resources" in str(candidate):
                found = candidate
                break
        if found is None:
            raise OllamaRuntimeError(
                f"extracted {DARWIN_ARCHIVE} but could not find "
                f"Ollama.app/Contents/Resources/ollama under {dest_dir}"
            )
        cli = found

    resources = cli.parent
    # zipfile.extractall does not reliably preserve +x; llama-server must be
    # executable or generate/chat fails with "permission denied".
    for name in ("ollama", "llama-server", "llama-quantize"):
        helper = resources / name
        if helper.is_file():
            helper.chmod(0o755)
    gui = dest_dir / "Ollama.app" / "Contents" / "MacOS" / "Ollama"
    if gui.is_file():
        gui.chmod(0o755)

    target = dest_dir / "ollama"
    if target.exists() or target.is_symlink():
        target.unlink()
    # Prefer a relative symlink so the whole data/ollama-bin/ folder stays portable.
    try:
        rel = cli.relative_to(dest_dir)
        target.symlink_to(rel)
    except ValueError:
        target.symlink_to(cli)


def _prepend_library_path(env: dict[str, str], install_dir: Path) -> None:
    extras = []
    for candidate in (
        install_dir / "lib" / "ollama",
        install_dir / "lib",
        install_dir / "Ollama.app" / "Contents" / "Resources",
    ):
        if candidate.is_dir():
            extras.append(str(candidate.resolve()))
    if sys.platform == "win32":
        extras.append(str(install_dir.resolve()))
    if not extras:
        return
    if sys.platform == "win32":
        key = "PATH"
    elif sys.platform == "darwin":
        key = "DYLD_LIBRARY_PATH"
    else:
        key = "LD_LIBRARY_PATH"
    existing = env.get(key)
    env[key] = os.pathsep.join(extras + ([existing] if existing else []))


def _linux_pdeathsig() -> None:
    """SIGTERM this process when the parent dies, so `ollama serve` is not left orphaned."""
    import ctypes

    pr_set_pdeathsig = 1
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(pr_set_pdeathsig, signal.SIGTERM)
    except OSError:
        pass
