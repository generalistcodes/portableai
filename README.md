<div align="center">
  <img src="ui/static/logo.svg" width="72" height="72" alt="PortableAI logo">
  <h1>PortableAI</h1>
  <p><strong>Your own AI, fully local — chat from your browser or your phone, powered by models you control.</strong></p>
</div>

PortableAI is a self-hosted chat interface for Ollama. New chats start as
a plain Assistant — like ChatGPT or Claude — and optional named personas
(a terse mentor, a patient explainer, a translator) are one click away.
Chat from a dark-mode web UI or a companion iPhone app, and keep a full
searchable history — all running on your own machine, with nothing sent
to the cloud. Pull, browse, and update models from one place, and pair
your phone over your local network in seconds.

This started as a tutorial on GGUF/Ollama and model personas — see
**Example prompts to try** further down if you're here for that. The
project lives at **portableai.app**. Note there's an existing, unrelated
open-source project called
[Portable-AI-USB](https://github.com/techjarves/Portable-AI-USB) (2k+
stars) doing something similar (Ollama + offline USB drive) — worth a
glance before finalizing public-facing copy, purely to keep positioning
distinct from theirs, not because "portable AI" is anyone's trademark.

A persona in Ollama is just a base GGUF model plus a fixed system prompt
and a few parameters, packaged into a named model. This repo shows how to
build one, drive it from Python instead of the CLI, and — the part most
tutorials skip — how to actually test it.

## Prerequisites

- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- The base model pulled: `ollama pull llama3.2:3b`
- Python 3.10+

```bash
pip install -r requirements.txt
```

Or, for a setup that works the same way regardless of OS: `python run.py`
(see **Portability across OSes** below — it installs dependencies for you
and starts the chat UI in one step).

## What's in here

```
personas/
  assistant.Modelfile            # default: plain, no-personality Assistant
  no-nonsense-mentor.Modelfile   # terse, opinionated, always ends with a next step
  eli5-explainer.Modelfile       # warm, analogy-heavy, explains like you're 10
src/
  persona_loader.py              # parses a Modelfile into a structured Persona
  ollama_client.py               # thin wrapper over Ollama's REST API
ui/
  server.py                      # Flask backend: proxies to Ollama, logs, settings, chat history
  model_catalog.json             # curated list of downloadable models shown in Settings
  static/                        # dark-mode chat UI (HTML/CSS/vanilla JS)
tests/
  test_persona_loader.py         # unit tests, no network, no Ollama needed
  test_ollama_client.py          # unit tests, requests fully mocked
  test_conversation_store.py     # unit tests for chat history, pure sqlite
  test_server.py                 # unit tests for the UI backend, Ollama mocked
  test_integration_persona.py    # real Ollama + real model, skips if unavailable
demo.py                          # CLI: build a persona and chat with it
data/                            # created at runtime: logs.jsonl, settings.json, chats.db
```

## The two ways to build a persona

**The CLI way** (what most tutorials show you):

```bash
ollama create no-nonsense-mentor -f personas/no-nonsense-mentor.Modelfile
ollama run no-nonsense-mentor "Should I use REST or GraphQL for a small API?"
```

**The programmatic way** (what this repo actually tests):

```bash
python demo.py mentor "Should I use REST or GraphQL for a small API?"
```

Under the hood, `demo.py` parses the same `.Modelfile` text with
`persona_loader.parse_modelfile()`, converts it into the JSON body
`/api/create` expects, and POSTs it via `OllamaClient`. Same result, but
now it's code you can unit test.

## Running the tests

Fast unit tests (no Ollama required — this is what CI would run):

```bash
pytest
```

This runs 177 tests covering: Modelfile parsing edge cases (missing `FROM`,
unterminated triple-quoted `SYSTEM` blocks, malformed `PARAMETER` lines,
numeric casting), the Ollama client's request/response handling with
`requests` fully mocked, the Flask UI backend (pairing, auth, chat history,
logs, catalog), and SQLite conversation storage — no network, no GPU, no
waiting on inference.

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

## Example prompts to try

New chats land on **Assistant** with no special instructions. These are
the kind of ordinary questions people actually type first:

**assistant** (the default)
- "Summarize the difference between TCP and UDP in a few sentences."
- "Help me write a polite follow-up email after an interview."

The named personas are optional — switch in the sidebar when you want a
specific voice. These make their differences obvious in a single reply:

**no-nonsense-mentor**
- "Should I use REST or GraphQL for a small internal API?"
- "I think I broke my database migration, help."
- "Give me a code review checklist for a pull request."

**eli5-explainer**
- "What is an API?"
- "How does a blockchain work?"
- "Why does my laptop get hot when I play games?"

Worth trying the same question against Mentor and Explainer back-to-back
(switch in the sidebar, keep the question identical) — that side-by-side
contrast is usually the most convincing part of a persona demo.

## Chat UI

A small self-hosted, dark-mode chat UI sits on top of the same
`persona_loader` / `ollama_client` code — no separate persona logic, it
just proxies to Ollama and adds logging + settings.

```bash
python run.py              # start (replaces a leftover instance of this app on 5050)
python run.py --restart    # stop ours on 5050, then start
python run.py --stop       # stop ours on 5050 and exit
python ui/server.py        # same server, without the run.py launcher
```

Then open **http://localhost:5050**. Other programs on port 5050 are never killed. You get:

- **Sidebar persona picker** — new chats start on Assistant. Other
  `.Modelfile` personas in `personas/` show up by display name (Mentor,
  Explainer, …) and are opt-in. Drop a new one in, reload the page, it
  shows up.
- **Settings** (gear icon) — change the Ollama base URL, and toggle
  whether personas get built automatically on server startup vs. lazily
  on first message.
- **Logs** (icon) — every chat request/reply, with latency, stored in
  `data/logs.jsonl` (one JSON object per line, easy to `tail -f` or
  parse). Clear them from the same panel.
- **Status pill** — live check of whether Ollama is actually reachable,
  polled every 15s.
- **Model selector** (top of chat) — a dropdown of every model installed
  in Ollama (`/api/models`, backed by `ollama list`/`/api/tags`). Leaving
  it on "Persona default" runs the persona's own `FROM` model; picking a
  different one builds a same-persona variant on that base instead
  (named `<persona>--<model>`, e.g. `no-nonsense-mentor--qwen2.5-0.5b`)
  without touching the original `.Modelfile`. Useful for comparing how a
  persona holds up on a smaller/larger base model.
- **Installed models + storage path** (in Settings) — lists every pulled
  model with its parameter size, quantization, and disk size, plus a
  best-effort path to where Ollama stores them (Ollama doesn't expose
  this over its API, so it's read from `$OLLAMA_MODELS` if set, otherwise
  the documented per-OS default).

Assistant replies are rendered as markdown (headers, code blocks, lists,
bold/italic, links) using a small hand-rolled renderer in `app.js` rather
than pulling `marked.js`/`DOMPurify` from a CDN — kept dependency-free on
purpose so this stays consistent with the offline-first USBMind/Portable
Ark philosophy: nothing here should require internet access beyond
talking to your own local Ollama.

It's a single Flask process (`ui/server.py`) serving static HTML/CSS/JS
with no build step — same philosophy as USBMind's minimal chat UI:
talk to Ollama's local API directly, no heavy third-party dependency in
the loop. `tests/test_server.py` covers the API routes with Ollama fully
mocked, same pattern as `test_ollama_client.py`.

## Chat history — save, search, archive, delete

Chats persist to `data/chats.db` (SQLite, part of Python's standard
library — no extra service to run):

- **Sidebar chat list** — every conversation you start shows up, titled
  automatically from your first message, sorted by most recently active.
- **Search** — the search box filters by title *and* message content
  (so "what did I ask about GraphQL" finds it even if you never typed
  "GraphQL" into the title).
- **Archive** — hides a chat from the main list without deleting it;
  toggle "Archived" in the sidebar to see archived chats and restore one.
- **Rename / Delete** — the ✎ and 🗑 icons on each chat row.
- Clicking a chat reloads its full message history from the server (not
  from browser memory), so closing the tab and coming back doesn't lose
  anything.

Implementation-wise: `src/conversation_store.py` is a small, dependency-free
wrapper around `sqlite3` (two tables: `conversations`, `messages`), fully
unit tested in `tests/test_conversation_store.py` with no Flask or Ollama
involved. `/api/chat` now takes an optional `conversation_id` — omit it to
start a new chat, pass it back to continue one. The server loads prior
messages from SQLite before each call to Ollama, so message history lives
in one place instead of being duplicated in the browser's memory.

No new framework needed for any of this — SQLite covers persistence and
search, Flask already covers routing, and the frontend is still plain
HTML/CSS/JS. The only point where you'd actually reach for something
heavier is if this needed multiple concurrent users writing to the same
chat at once, which a single-laptop tool doesn't.

## Portability across OSes

If you're moving this whole folder to a different machine (including a
different OS — the same reasoning USBMind/Portable Ark rely on), here's
what carries over cleanly and what doesn't:

**Carries over as-is, just by copying the files:**
- `personas/*.Modelfile` — plain text, no OS-specific content
- `data/chats.db` — SQLite's file format is identical across Windows/macOS/Linux; copy it to a new machine's `data/` folder and your chat history is back
- `data/settings.json`, `data/logs.jsonl` — plain JSON/JSON-lines
- All the Python source — no hardcoded path separators anywhere (everything uses `pathlib.Path`, which normalizes for the current OS automatically), no hardcoded absolute paths, no OS-specific assumptions

**Does NOT carry over — needs a fresh setup per machine:**
- `venv/` (or any virtualenv folder) — a Python virtual environment is tied to the exact OS and Python build it was created with; copying one from Linux and running it on Windows will not work. Don't zip/copy this folder.
- **Ollama itself** — separate install per OS, from [ollama.com/download](https://ollama.com/download). This repo talks to it over HTTP; it doesn't bundle Ollama.
- Pulled models (`llama3.2:3b` etc.) — these live inside Ollama's own storage, not in this repo. Run `ollama pull llama3.2:3b` again on the new machine.

**Easiest way to run it on a new machine:**

```bash
python run.py
```

`run.py` works identically on Windows, macOS, and Linux — it checks
whether Flask/requests are installed, installs them via pip if not
(needs internet for that one-time step, same as installing Ollama
itself), then starts the server on `http://localhost:5050`. Use
`python run.py --restart` to stop ours then start, or `python run.py --stop`
to stop without starting. No shell scripts, no `venv\Scripts\activate`
vs `source venv/bin/activate` differences to remember.

If you'd rather manage a virtualenv yourself: `python -m venv venv` on
all three OSes, then `venv\Scripts\activate` (Windows) or `source
venv/bin/activate` (macOS/Linux), then `pip install -r requirements.txt`.

None of the dependencies (`flask`, `requests`, `pytest`) require a C
compiler or platform-specific build step — they all ship pre-built wheels
for Windows/macOS/Linux on PyPI, so `pip install` behaves the same way
everywhere.

## Downloading models from the UI

Settings now has a "Download models" section — a curated list (in
`ui/model_catalog.json`) of ~15 popular Ollama models across a range of
sizes, each marked "✓ Installed" or with a "Download" button.

This is a **curated snapshot, not a live scrape of the full Ollama
library** — Ollama doesn't expose a "list every downloadable model" API,
only the website at [ollama.com/library](https://ollama.com/library)
does, and that's not meant to be scraped programmatically. If you want a
model that isn't in the catalog, `ollama pull <name>` from the terminal
still works exactly as before; the UI's model selector picks it up
automatically once it's installed. To add more entries to the curated
list, just edit `ui/model_catalog.json` — it's a flat JSON array, no
build step.

Clicking "Download" calls `POST /api/models/pull`, which blocks until
Ollama finishes downloading (large models can take several minutes) and
shows "Downloading…" the whole time — there's no progress bar. Ollama's
`/api/pull` does support a streaming mode with progress events; this
repo intentionally uses the simpler non-streaming call to keep the
`/api/models/pull` contract easy to test (see `test_pull_model_*` in
`tests/test_server.py`, all mocked — no multi-GB downloads happen in CI).
A progress bar is exactly the kind of thing that belongs in the
streaming-replies work already listed under "What's still missing" below.

### Recommended models

A few catalog entries carry a "★ Recommended" badge (with a reason on
hover) and sort to the top of the list — `llama3.2:3b` as the best default
balance, `qwen2.5:0.5b` for low-RAM machines, `qwen2.5:7b` if you have
16GB+ to spare. Edit the `"recommended"` / `"recommended_reason"` fields
in `ui/model_catalog.json` to change these.

### Checking for model updates

Each row in the "Installed models" table has a ⟳ button. There's no
public Ollama API for "is a newer version of this tag available" without
reverse-engineering their registry auth flow, so this takes an honest
shortcut instead: it re-runs `ollama pull <model>` (which is already
idempotent — a no-op if nothing changed, and only downloads the diff if
something did) and compares the model's manifest digest before and after
to report whether anything actually changed. `POST /api/models/check-update`
is fully unit tested with mocked digests in `tests/test_server.py`.

### Checking for app/catalog updates (fully optional)

Settings has an "Updates" section that is **off by default and makes zero
network calls unless you configure it**. If you publish this repo
somewhere (a GitHub fork, your own blog's git host), you can host a small
JSON file — e.g. `update-manifest.json` — shaped like:

```json
{
  "app_version": "0.5.0",
  "catalog_version": "2026-10-01",
  "message": "Added 3 new persona examples and a bugfix for archive search",
  "notes_url": "https://your-blog.example.com/ollama-persona-tutorial-changelog"
}
```

Paste that file's raw URL into "Update check URL" in Settings. From there
you get two ways to check it:
- **"Check now"** — always available, one click, one request.
- **"Check automatically on startup"** — a checkbox, off by default. When
  on, the app makes one lightweight request to your URL each time it
  starts and shows a small dot on the Settings button if either
  `app_version` or `catalog_version` doesn't match what's baked into
  `ui/server.py` (`APP_VERSION` / `CATALOG_VERSION` constants — bump
  these when you actually change something).

There's no Anthropic-run or otherwise pre-existing "central" update
service here — "centralized" just means *your* published file is the one
source of truth this instance checks against, entirely your choice to
set up.

## Pairing a phone (or any other device) over the LAN

The server binds to your machine's actual network interfaces, not just
`localhost` — so a phone on the same WiFi (or connected to a hotspot
this machine is running) can reach it directly.

Anything that isn't this machine itself needs to pair first: open
Settings → "Phone pairing" on the desktop, and either scan the QR code
shown there or manually enter the LAN address + 6-digit PIN. The PIN is
single-use, expires after 5 minutes, and locks out after 5 wrong
guesses. The QR code encodes a plain
`http://<lan-ip>:<port>/?pair_pin=...` URL any phone camera can open
(PIN carried as a query param so the web UI can pair on load). A
`portableai://pair?...` deep link is still generated for a future native
app handler, but that is not what the QR encodes (see
`docs/PHONE_PAIRING_PLAN.md`).

If Settings shows a warning instead of an address, the server couldn't
find any active network interface — check that WiFi is actually
connected (or a hotspot is running) before trying again.

## Multi-device data separation

Every phone that pairs is its own identity boundary — no usernames or
passwords, just the same device token pairing already issues. Each
paired device only ever sees its own conversations; the desktop browser
(trusted via `remote_addr`, same as everywhere else in this app) is the
one exception and can see every device's history, since it's the person
who owns the pairing PINs in the first place — useful for later
benchmarking across devices/personas, or just keeping an eye on things.

Concretely: every conversation is tagged with an `owner_id` — a device
token, or the fixed value `"local"` for the desktop. `GET
/api/conversations` filters to just your own unless you're the admin
(no filter, sees everyone's). Every other conversation route (get,
rename, archive, delete, export, and continuing a chat via `/api/chat`)
checks ownership and returns a plain 404 — not 403 — for someone else's
conversation, so a guessed ID doesn't even confirm it exists.

Each conversation in the sidebar has an export button (⬇) that downloads
it as a `.json` file — the full message history plus metadata, for
whoever owns it. Handy for taking your own chat data with you, or for
the benchmarking use case above.

If you're upgrading from a `data/chats.db` created before this existed,
nothing breaks: `conversation_store.py` migrates the schema
automatically on first connect, and every pre-existing conversation
defaults to `owner_id: "local"` (visible in the admin/desktop view,
same as before).

## What's still missing

Roughly in order of what most affects the demo/blog experience:

1. **Streaming replies** — right now the UI waits for the full response
   before showing anything (aside from the typing dots). Ollama supports
   token streaming; this UI intentionally doesn't use it yet to keep the
   `/api/chat` contract simple to test. The same gap applies to model
   downloads (no progress bar, see above).
2. **Persona editor in the UI** — personas are still hand-edited
   `.Modelfile` text files; there's no "create a persona" form.
3. **Copy button on code blocks** — the markdown renderer produces
   `<pre><code>`, but there's no one-click copy affordance yet.
4. **Stop-generation / regenerate** — no way to cancel a slow reply or
   ask the persona to try again without retyping the question.

## Extending this

- Add a persona: drop a new `.Modelfile` in `personas/`, add a
  `test_bundled_personas_parse_cleanly` case, done — the loader and client
  don't change.
- Swap the base model: change `FROM llama3.2:3b` to any model you've
  pulled; nothing else in the repo cares what's underneath.
- LoRA fine-tunes: `ADAPTER` is a valid Modelfile directive not yet
  handled by `persona_loader.py` — that's the natural "part 2" of this
  tutorial.
