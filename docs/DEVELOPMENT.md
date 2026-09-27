# Development

How to run PortableAI from source, how the tests are split, what survives
a move to another machine, and how the Linux executable is built.

The short path is in the [README](../README.md). Feature behavior is in
[Features](FEATURES.md).

## Prerequisites

**Linux, compiled binary:** no Python. See **Linux executable** below.
Copy `portableai`, run it. First run still needs internet for the
vendored Ollama download unless `data/ollama-bin/` is already sitting
next to the binary.

**From source (any OS):**
- Python 3.10+
- **Linux / macOS:** nothing else for Ollama. `python run.py` downloads a
  pinned standalone Ollama into `data/ollama-bin/` on first run, starts it
  for you, and stores models in `data/ollama-models/`. First run needs
  internet for that download (~1.4GB on Linux amd64; ~190MB on macOS).
  After that it's local.
- **Windows:** Ollama is not bundled yet — install it from
  [ollama.com](https://ollama.com) and have `ollama serve` running.
- A base model once the server is up — download one from Settings, or
  (Linux/macOS) use the bundled `data/ollama-bin/ollama pull …` against
  PortableAI's copy. A system `ollama` CLI talks to the system install,
  not PortableAI's copy.

```bash
pip install -r requirements.txt
```

Or, for a setup that works the same way regardless of OS: `python run.py`
(see **Portability across OSes** below — it installs Python dependencies
for you, and on Linux also vendors Ollama, then starts the chat UI).

## What's in here

```
personas/
  assistant.Modelfile            # default: plain, no-personality Assistant
  no-nonsense-mentor.Modelfile   # terse, opinionated, always ends with a next step
  eli5-explainer.Modelfile       # warm, analogy-heavy, explains like you're 10
benchmarks/
  prompts.json                   # the only copy of the model-quality prompt set
  runner.py                      # collect latency + raw answers from Ollama
  report.py                      # markdown comparison + critical-refusal flags
  results/                       # one JSON file per model run
src/
  persona_loader.py              # parses a Modelfile into a structured Persona
  ollama_client.py               # thin wrapper over Ollama's REST API
  ollama_runtime.py              # Linux: download/pin/run a bundled Ollama subprocess
  app_paths.py                   # frozen vs source: resource root vs writable data/
ui/
  server.py                      # Flask backend: proxies to Ollama, logs, settings, chat history
  model_catalog.json             # curated list of downloadable models shown in Settings
  static/                        # dark-mode chat UI (HTML/CSS/vanilla JS)
tests/
  test_persona_loader.py         # unit tests, no network, no Ollama needed
  test_ollama_client.py          # unit tests, requests fully mocked
  test_ollama_runtime.py         # unit tests for vendored Ollama download/lifecycle, fully mocked
  test_conversation_store.py     # unit tests for chat history, pure sqlite
  test_server.py                 # unit tests for the UI backend, Ollama mocked
  test_app_paths.py              # frozen vs source: data/ next to the binary, not the extract dir
  test_integration_persona.py    # real Ollama + real model, skips if unavailable
  test_benchmark_*.py            # unit tests for the model benchmark (Ollama mocked)
portableai.spec                  # PyInstaller one-file build (Linux/macOS/Windows)
requirements-dev.txt             # PyInstaller; build-time only, not needed to run from source
docs/
  API_CONTRACT.md                # every /api/* route as implemented — iOS/client source of truth
  ROADMAP.md                     # current project status — single source of truth
  FEATURES.md                    # chat, streaming, history, models, pairing
  TUTORIAL.md                    # persona walkthrough
  DEVELOPMENT.md                 # this file
  MODEL_BENCHMARK.md             # generated from benchmarks/prompts.json
  BENCHMARK_RESULTS.md           # last real run, with a recommendation
demo.py                          # CLI: build a persona and chat with it
data/                            # created at runtime: logs, settings, chats.db, ollama-bin/, ollama-models/
# iOS app lives in sibling repo portableai-ios (not in this tree)
```

Commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`,
`docs:`) — see `CONTRIBUTING.md`. Releases are `make release VERSION=vX.Y.Z`.

## Running the server from source

```bash
python run.py              # from source (Linux: vendors + starts Ollama; replaces leftover UI on 5050)
python run.py --restart    # stop ours on 5050, then start
python run.py --stop       # stop ours on 5050 and exit
./portableai               # Linux one-file build — same flags, no Python needed
```

Do **not** run `python ui/server.py` — that is not a supported launch path.
It exits immediately and tells you to use `python run.py`, which is the
command that vendors and starts the bundled Ollama on Linux.

Then open **http://localhost:5050**. Other programs on port 5050 are never killed.

## Running the tests

Fast unit tests (no Ollama required — this is what CI would run):

```bash
pytest
```

This runs 225 tests covering: Modelfile parsing edge cases (missing `FROM`,
unterminated triple-quoted `SYSTEM` blocks, malformed `PARAMETER` lines,
numeric casting), the Ollama client's request/response handling with
`requests` fully mocked, Linux Ollama vendoring (download URL, version pin,
child `OLLAMA_MODELS`) with download/subprocess mocked, the Flask UI backend
(pairing, auth, chat history, logs, catalog), SQLite conversation
storage, the model-quality benchmark (prompt catalog, mocked collector,
refusal heuristic), and frozen vs source path helpers for the Linux
one-file build — no network, no GPU, no waiting on inference.

Integration tests (spins up the real personas against a real, running
Ollama):

```bash
pytest -m integration
```

These actually create `test-no-nonsense-mentor` and `test-eli5-explainer`
in your local Ollama, chat with them, and check for persona-shaped
behavior — e.g. the mentor never says "I'm sorry", the ELI5 explainer
reaches for an analogy. They clean up after themselves (`delete_model` in
a fixture teardown).

### Why the split matters

LLM output isn't fully deterministic even at `temperature=0` across
different hardware/backends, so testing "does the model produce this exact
string" is a losing game. The unit tests instead pin down everything that
*should* be deterministic — parsing, request shape, error handling — and
the integration tests only make loose, structural assertions ("no banned
phrase appears", "an analogy marker is present") on real generations. If
you're used to testing regular APIs: treat the parser and client like any
other code (exact tests), and treat the model's actual text like an
external, slightly fuzzy dependency (smoke tests, not correctness proofs).

## Portability across OSes

If you're moving this whole folder to a different machine (including a
different OS — the same reasoning USBMind/Portable Ark rely on), here's
what carries over cleanly and what doesn't:

**Carries over as-is, just by copying the files:**
- `personas/*.Modelfile` — plain text, no OS-specific content
- `data/chats.db` — SQLite's file format is identical across Windows/macOS/Linux; copy it to a new machine's `data/` folder and your chat history is back
- `data/settings.json`, `data/logs.jsonl` — plain JSON/JSON-lines
- `data/ollama-bin/` and `data/ollama-models/` — on **Linux and macOS**, the vendored Ollama install and the models it pulled. Copy the whole folder to another machine of the **same OS family** (and same CPU arch on Linux) and you do not need a separate Ollama install. A first run that is missing the pinned binary will download it again.
- All the Python source — no hardcoded path separators anywhere (everything uses `pathlib.Path`, which normalizes for the current OS automatically), no hardcoded absolute paths, no OS-specific assumptions

**Does NOT carry over — needs a fresh setup per machine:**
- `venv/` (or any virtualenv folder) — a Python virtual environment is tied to the exact OS and Python build it was created with; copying one from Linux and running it on Windows will not work. Don't zip/copy this folder.
- **Ollama on Windows** — not bundled yet. Separate install from [ollama.com/download](https://ollama.com/download). Windows still talks to Ollama over HTTP the old way.
- GPU / CUDA / ROCm — the Linux vendored binary is **CPU-only for now**. PortableAI does not run the official installer's GPU detection, and it does not download CUDA or ROCm extras. If you have a GPU and care about it, install Ollama yourself from ollama.com (which *does* wire up GPU support) rather than assuming this bundled copy will use it. That is a deliberate trade-off for "one folder, one command", not a silent performance regression we hope you won't notice. On macOS the vendored app can use Apple Silicon / Metal when the machine supports it; this was not the focus of the bundling work.
- `dist/portableai` — the compiled Linux executable will not run on macOS or Windows. Those builds are planned, not shipped.

**Easiest way to run it on a new machine:**

```bash
python run.py
```

`run.py` works the same way on Windows, macOS, and Linux for the Python side — it checks whether Flask/requests are installed, installs them via pip if not (needs internet for that one-time step), then starts the server on `http://localhost:5050`. On **Linux and macOS** it also downloads a pinned Ollama `v0.34.0` into `data/ollama-bin/` the first time, launches `ollama serve` as a subprocess with `OLLAMA_MODELS` pointed at `data/ollama-models/`, and stops that subprocess when you Ctrl+C. If a system-wide `ollama` is already on PATH, it prints a clear notice that PortableAI is using the bundled copy instead — it does not silently reuse the system install.

To point PortableAI at an Ollama you already run yourself, set this
**launch-time environment variable** (not a Settings toggle — a UI
control would make it too easy to attach to the wrong version, or one
that does not have PortableAI's models):

```bash
PORTABLEAI_EXTERNAL_OLLAMA_URL=http://127.0.0.1:11434 python run.py
```

When that variable is set, `run.py` skips downloading and launching the
vendored Ollama and talks only to the URL you gave. Unset, the default
is always the bundled instance. Settings shows a read-only line so you
can see which mode is active: `Ollama: bundled (port …)` or
`Ollama: external override (http://…)`.

**macOS bundling (tested on a real Mac, 2026-09-12):** downloads the pinned GitHub asset `Ollama-darwin.zip` (~190MB), extracts `Ollama.app`, runs the CLI at `Ollama.app/Contents/Resources/ollama`, and clears `com.apple.quarantine` with `xattr` before the first launch. In that live test **no Gatekeeper dialog appeared** — download → extract → quarantine strip → `ollama serve` completed with zero manual clicks. Second start correctly skipped the download via the `VERSION` pin. Chat round-trip against a local model returned a real reply (`PONG`). Model *pulls* from the Ollama registry are a separate network path (CDN timeouts can still fail on a flaky connection); that is not a Gatekeeper issue.
Use `python run.py --restart` to stop ours then start, or `python run.py --stop` to stop without starting. No shell scripts, no `venv\Scripts\activate` vs `source venv/bin/activate` differences to remember.

If you'd rather manage a virtualenv yourself: `python -m venv venv` on
all three OSes, then `venv\Scripts\activate` (Windows) or `source
venv/bin/activate` (macOS/Linux), then `pip install -r requirements.txt`.

None of the dependencies (`flask`, `requests`, `pytest`) require a C
compiler or platform-specific build step — they all ship pre-built wheels
for Windows/macOS/Linux on PyPI, so `pip install` behaves the same way
everywhere.

## Packaged downloads (GitHub Releases)

Pushing a version tag (`v1.0.0`) runs `.github/workflows/release.yml` and
attaches OS packages to a GitHub Release. Binaries are **not** committed
to this repo.

The one-line installer is in the [README](../README.md). Read `install.sh`
first — it is the only curl-pipe installer; the website links here rather
than hosting a copy. It downloads the Linux AppImage or macOS `.dmg` into
the **current directory** and stops. It does not launch the server.

- **Linux:** AppImage — complete packaged path (PyInstaller + appimagetool). If FUSE is unavailable, run `./PortableAI-*.AppImage --appimage-extract-and-run` instead of double-clicking. The unchanging latest link is `https://github.com/generalistcodes/portableai/releases/latest/download/PortableAI-linux-x86_64.AppImage`.
- **macOS:** unsigned, unnotarized `.dmg`. Gatekeeper will warn. Signing
  needs a paid Apple Developer account; this repo does not assume one.
  Latest: `https://github.com/generalistcodes/portableai/releases/latest/download/PortableAI-macos-arm64.dmg`.
- **Windows:** PyInstaller `.exe` only. Incomplete: no vendored Ollama and
  no LAN IP detection for pairing (see `packaging/windows/INCOMPLETE.txt`
  and `docs/ROADMAP.md`). Latest: `https://github.com/generalistcodes/portableai/releases/latest/download/PortableAI-windows-x86_64.exe`.

## Linux executable (no Python)

Local Linux build (same PyInstaller spec the release workflow uses):

```bash
pip install -r requirements-dev.txt
pyinstaller portableai.spec
```

That produces a single ELF file, `dist/portableai`, about **13 MB**. Copy
just that file — not this repo, not `venv/` — to another Linux machine of
the same architecture, `chmod +x` it, and run `./portableai`. Same flags
as `run.py` (`--port`, `--ollama-host`, `--restart`, `--stop`).

The 13 MB is the chat UI plus an embedded Python runtime. It is **not**
Ollama and it is **not** a model. Writable state lives in a `data/`
folder **next to the binary**, not in the temp directory PyInstaller
unpacks at runtime (that extract dir is deleted when the process exits).
First run still downloads the pinned Ollama (~1.4 GB) into
`data/ollama-bin/` unless that folder is already there; models you pull
go in `data/ollama-models/`. The CPU-only Ollama caveat above still
applies.

```bash
./portableai                 # UI on http://localhost:5050
./portableai --port 5051     # if 5050 is already taken
```
