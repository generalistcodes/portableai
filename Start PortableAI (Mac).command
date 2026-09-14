#!/bin/bash
# Double-click in Finder, or run from Terminal:
#   "./Start PortableAI (Mac).command"
set -e
cd "$(dirname "$0")"

PY=""
for candidate in \
  /opt/homebrew/bin/python3.12 \
  /opt/homebrew/bin/python3.11 \
  /usr/local/bin/python3.12 \
  /usr/local/bin/python3.11 \
  "$(command -v python3.12 2>/dev/null || true)" \
  "$(command -v python3.11 2>/dev/null || true)"
do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "Need Python 3.11+ (system python3 on macOS is often 3.9 and is too old)."
  echo "Install with:  brew install python@3.11"
  read -r -p "Press Return to close…"
  exit 1
fi

OLLAMA_HOST="127.0.0.1:11434"
if lsof -nP -iTCP:11434 -sTCP:LISTEN >/dev/null 2>&1; then
  OLLAMA_HOST="127.0.0.1:11435"
  echo "Something is already on port 11434 — using bundled Ollama at $OLLAMA_HOST"
fi

echo "Using $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
echo "Open http://localhost:5050 when it says PortableAI is running."
echo "Ctrl+C stops the server."
echo

exec "$PY" run.py --restart --ollama-host "$OLLAMA_HOST"
