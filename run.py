"""
Cross-platform launcher for the persona chat UI.

This is the only supported way to start PortableAI. `python ui/server.py`
refuses to start: that would skip the bundled Ollama on Linux/macOS. This
launcher checks for Flask/requests and installs them via pip if missing,
then starts the server. Works identically on Windows, macOS, and Linux:
no shell scripts, no OS-specific activation commands:

    python run.py              # start (replaces our leftover on 5050)
    python run.py --restart    # stop ours on 5050, then start
    python run.py --stop       # stop ours on 5050 and exit

(`python3 run.py` on systems where "python" still means Python 2.)

On Linux, macOS, and Windows this also vendors a pinned standalone Ollama into
data/ollama-bin/ (downloaded on first run) and launches `ollama serve`
as a subprocess with models stored in data/ollama-models/. Set
PORTABLEAI_EXTERNAL_OLLAMA_URL to skip that and use an Ollama you
already run.
"""
from __future__ import annotations

import argparse
import atexit
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "ui"))


def _ensure_dependencies() -> None:
    if getattr(sys, "frozen", False):
        return

    missing = []
    for module_name, package_name in (
        ("flask", "flask"),
        ("requests", "requests"),
        ("zeroconf", "zeroconf"),
    ):
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)

    if not missing:
        return

    print(f"Installing missing dependencies: {', '.join(missing)}")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start, restart, or stop the PortableAI chat UI on port 5050. "
        "Only a leftover instance of this app (python run.py / ui/server.py) is ever stopped; "
        "Only a leftover instance of this app (python run.py / ui/server.py) is ever stopped; "
        "other programs on that port are left alone. On Linux/macOS/Windows, also starts a bundled Ollama."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--restart",
        action="store_true",
        help="Stop a leftover instance of this app on port 5050, then start.",
    )
    mode.add_argument(
        "--stop",
        action="store_true",
        help="Stop a leftover instance of this app on port 5050 and exit (do not start).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5050,
        help="Port for the chat UI (default 5050).",
    )
    parser.add_argument(
        "--ollama-host",
        default="127.0.0.1:11434",
        help="Host:port for bundled Ollama (default 127.0.0.1:11434).",
    )
    parser.add_argument(
        "--prefetch-only",
        action="store_true",
        help="Download the bundled Ollama engine and a starter model, then exit "
        "(no UI, no browser). Also set by PORTABLEAI_PREFETCH_ONLY=1.",
    )
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print(
            f"Warning: this repo targets Python 3.10+; you're running {sys.version.split()[0]}. "
            "Some syntax (e.g. 'str | None' type hints) may fail on older versions."
        )

    _ensure_dependencies()

    if getattr(sys, "frozen", False):
        import server
    else:
        # Load ui/server.py by path so a leftover server.py in the repo root
        # cannot shadow the real Flask app.
        import importlib.util

        server_path = ROOT / "ui" / "server.py"
        spec = importlib.util.spec_from_file_location("server", server_path)
        server = importlib.util.module_from_spec(spec)
        sys.modules["server"] = server
        spec.loader.exec_module(server)

    if args.stop:
        server.stop_our_server(args.port)
        return

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(ROOT / "src"))
    from first_run import (  # noqa: E402
        maybe_prompt_default_model,
        prefetch_requested,
        run_prefetch,
        wait_for_tcp_port,
    )
    from ollama_runtime import (  # noqa: E402
        OllamaRuntimeError,
        external_ollama_url,
        is_pinned_install,
        start_managed_ollama,
        supported_on_this_os,
    )
    from setup_progress import (  # noqa: E402
        bind_setup_log_from_data_dir,
        mark_error,
        mark_ready,
        set_phase,
    )

    bind_setup_log_from_data_dir(server.DATA_DIR)

    if prefetch_requested(args.prefetch_only, os.environ):
        raise SystemExit(
            run_prefetch(data_dir=server.DATA_DIR, host=args.ollama_host)
        )

    port = server.pick_listen_port(args.port)

    from mdns_broadcast import MdnsAdvertiser  # noqa: E402

    runtime = None
    advertiser = MdnsAdvertiser()

    def _stop_runtime() -> None:
        nonlocal runtime
        if runtime is None:
            return
        runtime.stop()
        runtime = None
        server.set_managed_ollama_base_url(None)

    def _cleanup() -> None:
        # Drop the mDNS advertisement before stopping Ollama. A stale
        # broadcast is worse than none: a phone would think the server
        # is still reachable.
        advertiser.stop()
        _stop_runtime()

    atexit.register(_cleanup)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        _cleanup()
        if callable(previous_sigterm):
            previous_sigterm(signum, frame)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    # First-run overlay: mark vendor work before Flask accepts connections
    # so the browser never sees a false "ready" snapshot.
    using_external = bool(external_ollama_url())
    if not using_external and supported_on_this_os():
        if is_pinned_install(server.DATA_DIR):
            set_phase("starting_ollama", message="Starting Ollama")
        else:
            set_phase("downloading_ollama", message="Downloading Ollama")
        # Do not talk to a leftover system Ollama on 11434 while ours starts.
        server.set_managed_ollama_base_url("http://127.0.0.1:1", mode="bundled")

    ui_thread = threading.Thread(
        target=server.run_app, args=(port,), name="portableai-ui", daemon=True
    )
    ui_thread.start()
    if not wait_for_tcp_port(port):
        print(f"UI did not bind port {port}", file=sys.stderr)
        sys.exit(1)
    advertiser.start(port, version=server.APP_VERSION)
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass

    try:
        try:
            mode, ollama_url, runtime = start_managed_ollama(
                server.DATA_DIR, host=args.ollama_host
            )
        except OllamaRuntimeError as exc:
            mark_error(str(exc))
            print(f"Failed to start bundled Ollama: {exc}", file=sys.stderr)
            sys.exit(1)
        if mode == "external":
            print(
                f"PORTABLEAI_EXTERNAL_OLLAMA_URL is set — skipping bundled Ollama, using {ollama_url}"
            )
            server.set_managed_ollama_base_url(ollama_url, mode="external")
        elif mode == "bundled":
            server.set_managed_ollama_base_url(ollama_url, mode="bundled")
        else:
            print(
                "Note: PortableAI does not bundle Ollama on this OS yet. "
                "Install Ollama separately: https://ollama.com/download"
            )
            server.set_managed_ollama_base_url(None, mode="none")
        mark_ready()
        maybe_prompt_default_model(server._client())
        ui_thread.join()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
