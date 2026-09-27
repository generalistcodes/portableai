# Features

What the running app actually does. Install and first-run steps are in
the [README](../README.md). Building personas is in
[Tutorial](TUTORIAL.md). Route shapes are in
[API_CONTRACT.md](API_CONTRACT.md).

## Chat UI

A small self-hosted, dark-mode chat UI sits on top of the same
`persona_loader` / `ollama_client` code — no separate persona logic, it
just proxies to Ollama and adds logging + settings.

```bash
python run.py              # from source (Linux: vendors + starts Ollama; replaces leftover UI on 5050)
python run.py --restart    # stop ours on 5050, then start
python run.py --stop       # stop ours on 5050 and exit
./portableai               # Linux one-file build — same flags, no Python needed
```

Do **not** run `python ui/server.py` — that is not a supported launch path.
It exits immediately and tells you to use `python run.py`, which is the
command that vendors and starts the bundled Ollama on Linux.

Then open **http://localhost:5050**. Other programs on port 5050 are never killed. You get:

- **Sidebar persona picker** — new chats start on Assistant. Other
  `.Modelfile` personas in `personas/` show up by display name (Mentor,
  Explainer, …) and are opt-in. Drop a new one in, reload the page, it
  shows up.
- **Settings** (gear icon) — change the Ollama base URL, and toggle
  whether personas get built automatically on server startup vs. lazily
  on first message. A read-only line shows whether this process is using
  the bundled Ollama or `PORTABLEAI_EXTERNAL_OLLAMA_URL`.
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
  model with its parameter size, quantization, and disk size, plus the
  storage path. On Linux and macOS this is PortableAI's own
  `data/ollama-models/` (the bundled Ollama is launched with
  `OLLAMA_MODELS` set there). On Windows it is still a best-effort guess
  (`$OLLAMA_MODELS` or the documented default) until Windows is bundled
  too.

Assistant replies are rendered as markdown (headers, code blocks, lists,
bold/italic, links) using a small hand-rolled renderer in `app.js` rather
than pulling `marked.js`/`DOMPurify` from a CDN — kept dependency-free on
purpose so this stays consistent with the offline-first USBMind/Portable
Ark philosophy: once Ollama is present (bundled on Linux after the first
download, or installed separately on other OSes), chatting does not
require internet. Tokens are drawn as they arrive; see **Streaming
replies** below.

It's a single Flask process (`ui/server.py`) serving static HTML/CSS/JS
with no build step — same philosophy as USBMind's minimal chat UI:
talk to Ollama's local API directly, no heavy third-party dependency in
the loop. `tests/test_server.py` covers the API routes with Ollama fully
mocked, same pattern as `test_ollama_client.py`.

## Streaming replies

Shipped in v0.7.5. A chat reply is not held back until the model
finishes. `POST /api/chat` streams newline-delimited JSON, and the page
appends each token as it arrives. The wire format is in
[API_CONTRACT.md](API_CONTRACT.md).

- **Token by token, not the full response.** Each content delta is its
  own `{"token":"…"}` line. The bubble grows while the model is still
  generating. The UI does not wait for the completed reply and then
  paint it in one shot.
- **A blinking caret** sits at the end of the assistant text for as long
  as tokens are arriving. It goes away when the reply finishes.
- **Failures before the first token** stay ordinary JSON errors — the
  same 400, 404, 500, 502, and 503 bodies as before (missing fields,
  unknown conversation, persona build failure, Ollama down, connection
  lost, truncated response). Nothing has been stored yet.
- **A drop after tokens have started** keeps HTTP 200 and ends the
  stream with an `{"error":"…"}` line instead of a `done` object. The
  partial text is taken off the screen and is not written to
  `chats.db`. The user message is not saved either, so sending again
  retries a clean turn.

The user and assistant messages are stored only when the stream ends
with `done`.

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

Clicking "Download" calls `POST /api/models/pull`, which streams NDJSON
until Ollama finishes (large models can take several minutes). The button
shows "Downloading…", and if a transient connection failure hits (common
against flaky CDN edges when fetching model blobs), PortableAI retries a
few times and updates the button to messages like
"Connection issue, retrying (2/3)…". There's still no byte-level progress
bar — Ollama's `/api/pull` supports streaming progress events; this repo
keeps `stream=False` per attempt and only surfaces retry status between
attempts (see `test_pull_model_*` in `tests/test_server.py` /
`tests/test_ollama_client.py`, all mocked — no multi-GB downloads in CI).
A progress bar is still listed under "What's still missing" below.

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
network calls unless you configure it**. Paste a GitHub `owner/repo`
(for example `generalistcodes/portableai`) to compare this app against
that repo’s latest GitHub Release — the Settings dot links to the Release
page; nothing is auto-downloaded. You can still host a small JSON file
instead — e.g. `update-manifest.json` — shaped like the example below.
`1.0.0` is a placeholder: set `app_version` to the version you are
actually releasing, not a number copied from this repo.

```json
{
  "app_version": "1.0.0",
  "catalog_version": "2026-10-01",
  "message": "Added 3 new persona examples and a bugfix for archive search",
  "notes_url": "https://your-blog.example.com/ollama-persona-tutorial-changelog"
}
```

The same shape is checked in as `ui/static/update-manifest.example.json`.

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

1. **Download progress bar** — model pulls report retry status, not
   byte-level progress. See the model-download section above.
2. **Persona editor in the UI** — personas are still hand-edited
   `.Modelfile` text files; there's no "create a persona" form.
3. **Copy button on code blocks** — the markdown renderer produces
   `<pre><code>`, but there's no one-click copy affordance yet.
4. **Stop-generation / regenerate** — no way to cancel a slow reply or
   ask the persona to try again without retyping the question.
5. **Windows as a complete product** — the tagged release ships a
   PyInstaller `.exe`, but vendored Ollama and LAN IP detection are still
   unbuilt. macOS ships an unsigned `.dmg` (Gatekeeper will warn). Linux
   AppImage is the complete packaged path. See `docs/ROADMAP.md`.
