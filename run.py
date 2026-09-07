"""
Cross-platform launcher for the persona chat UI.

Run this instead of `python ui/server.py` directly when you're not sure
the target machine already has dependencies installed -- it checks for
Flask/requests and installs them via pip if missing, then starts the
server. Works identically on Windows, macOS, and Linux: no shell scripts,
no OS-specific activation commands, just `python run.py` (or `python3
run.py` on systems where "python" still means Python 2).

This does NOT touch Ollama itself -- that's a separate install per
machine (see https://ollama.com/download). This script only handles the
Python side of this repo.
"""
from __future__ import annotations

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
    if sys.version_info < (3, 10):
        print(
            f"Warning: this repo targets Python 3.10+; you're running {sys.version.split()[0]}. "
            "Some syntax (e.g. 'str | None' type hints) may fail on older versions."
        )

    _ensure_dependencies()

    sys.path.insert(0, str(ROOT / "ui"))
    import server  # noqa: E402  (import after path/dep setup on purpose)

    # Bind all interfaces, matching ui/server.py. A 127.0.0.1 bind here made
    # `python run.py` unreachable from phones on the LAN while `python ui/server.py`
    # (host="0.0.0.0") worked — keep these two in sync.
    server.app.run(host="0.0.0.0", port=server.PORT, debug=False)


if __name__ == "__main__":
    main()
