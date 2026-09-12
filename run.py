"""
Cross-platform launcher for the persona chat UI.

This is the only supported way to start PortableAI. `python ui/server.py`
refuses to start: that would skip the bundled Ollama on Linux. This
launcher checks for Flask/requests and installs them via pip if missing,
then starts the server. Works identically on Windows, macOS, and Linux:
no shell scripts, no OS-specific activation commands:

    python run.py              # start (replaces our leftover on 5050)
    python run.py --restart    # stop ours on 5050, then start
    python run.py --stop       # stop ours on 5050 and exit

(`python3 run.py` on systems where "python" still means Python 2.)

On Linux this also vendors a pinned standalone Ollama into data/ollama-bin/
(downloaded on first run) and launches `ollama serve` as a subprocess with
models stored in data/ollama-models/. macOS and Windows still need a
separate Ollama install (see https://ollama.com/download).
"""
from __future__ import annotations

import argparse
import atexit
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "ui"))


def _ensure_dependencies() -> None:
    if getattr(sys, "frozen", False):
        return

    missing = []
    for module_name, package_name in (("flask", "flask"), ("requests", "requests")):
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
        "other programs on that port are left alone. On Linux, also starts a bundled Ollama."
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
        help="Host:port for bundled Ollama on Linux (default 127.0.0.1:11434).",
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
    from ollama_runtime import (  # noqa: E402
        OllamaRuntimeError,
        ensure_and_start,
        supported_on_this_os,
    )

    server.ensure_port_available(args.port)

    runtime = None

    def _stop_runtime() -> None:
        nonlocal runtime
        if runtime is None:
            return
        runtime.stop()
        runtime = None
        server.set_managed_ollama_base_url(None)

    atexit.register(_stop_runtime)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        _stop_runtime()
        if callable(previous_sigterm):
            previous_sigterm(signum, frame)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        if supported_on_this_os():
            try:
                runtime = ensure_and_start(server.DATA_DIR, host=args.ollama_host)
            except OllamaRuntimeError as exc:
                print(f"Failed to start bundled Ollama: {exc}", file=sys.stderr)
                sys.exit(1)
            server.set_managed_ollama_base_url(runtime.base_url)
        else:
            print(
                "Note: PortableAI does not bundle Ollama on this OS yet (Linux only). "
                "Install Ollama separately: https://ollama.com/download"
            )
        server.run_app(args.port)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        _stop_runtime()


if __name__ == "__main__":
    main()
