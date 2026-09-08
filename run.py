"""
Cross-platform launcher for the persona chat UI.

Run this instead of `python ui/server.py` directly when you're not sure
the target machine already has dependencies installed -- it checks for
Flask/requests and installs them via pip if missing, then starts the
server. Works identically on Windows, macOS, and Linux: no shell scripts,
no OS-specific activation commands:

    python run.py              # start (replaces our leftover on 5050)
    python run.py --restart    # stop ours on 5050, then start
    python run.py --stop       # stop ours on 5050 and exit

(`python3 run.py` on systems where "python" still means Python 2.)

This does NOT touch Ollama itself -- that's a separate install per
machine (see https://ollama.com/download). This script only handles the
Python side of this repo.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _ensure_dependencies() -> None:
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
        "other programs on that port are left alone."
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
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print(
            f"Warning: this repo targets Python 3.10+; you're running {sys.version.split()[0]}. "
            "Some syntax (e.g. 'str | None' type hints) may fail on older versions."
        )

    _ensure_dependencies()

    # Load ui/server.py by path so a leftover server.py in the repo root
    # cannot shadow the real Flask app.
    import importlib.util

    server_path = ROOT / "ui" / "server.py"
    spec = importlib.util.spec_from_file_location("server", server_path)
    server = importlib.util.module_from_spec(spec)
    sys.modules["server"] = server
    spec.loader.exec_module(server)

    if args.stop:
        server.stop_our_server(5050)
        return

    server.ensure_port_available(5050)
    # All interfaces, not localhost — phones on the same LAN must be able
    # to connect. --stop/--restart still find this process via port 5050.
    server.run_app(5050)


if __name__ == "__main__":
    main()
