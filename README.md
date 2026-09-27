<p align="center">
  <img src="ui/static/logo.png" width="96" height="96" alt="Portable AI">
</p>

<h1 align="center">Portable AI</h1>

<p align="center"><strong>Your own AI, fully local — chat from your browser or your phone, powered by models you control.</strong></p>

PortableAI is a self-hosted chat interface for Ollama. New chats start as
a plain Assistant — like ChatGPT or Claude — and optional named personas
(a terse mentor, a patient explainer, a translator) are one click away.
Chat from a dark-mode web UI or a companion iPhone app, and keep a full
searchable history — all running on your own machine, with nothing sent
to the cloud. Pull, browse, and update models from one place, and pair
your phone over your local network in seconds.

Other 'local AI' products are chasing this from the expensive end --
Perplexity's Portable Computer (launched with NVIDIA, August 2026)
needs a $4,700 DGX Spark or a high-end GPU workstation, plus an active
Pro/Max subscription, and by its own admission isn't fully offline --
it calls out to the cloud for harder tasks with permission. PortableAI
runs on the laptop you already have, costs nothing, and needs zero
internet connection, ever -- that's not a limitation, it's the whole
point.

The project lives at **portableai.app**. The iPhone companion is a
separate repo (`portableai-ios`); this tree is the server and web UI.

## Quick start

**Packaged install** — downloads the Linux AppImage or macOS disk image
into the current directory and stops. It does not launch the server.
Read `install.sh` first; it is the only curl-pipe installer.

```bash
curl -fsSL https://raw.githubusercontent.com/generalistcodes/portableai/HEAD/install.sh | sh
```

That writes `PortableAI.AppImage` (Linux) or `PortableAI.dmg` (macOS) in
the current directory. Launch that file, then open the URL below.

**From a clone of this repo:**

```bash
python run.py
```

Then open **http://localhost:5050**. You get a chat box. Type a normal
question; the reply fills in as the model writes it. Download a model
from Settings if this machine does not have one yet. A phone on the same
Wi-Fi pairs from Settings with the QR code or the PIN.

Prerequisites, the Linux binary, and what does not copy between machines
are in [Development](docs/DEVELOPMENT.md). Do not start `ui/server.py`
directly — `python run.py` is the supported source command.

## What you get

- **Streaming replies.** Tokens appear as they are generated, with a
  blinking caret at the end of the bubble. A failure before the first
  token is a normal error. A drop mid-reply discards the partial text
  and does not save it. Details are in
  [Features](docs/FEATURES.md#streaming-replies).
- **Personas.** New chats start as a plain Assistant. Named voices — a
  terse mentor, a patient explainer, a translator — are one click away
  in the sidebar, and you can add your own. See the
  [Tutorial](docs/TUTORIAL.md).
- **Phone pairing.** The server listens on the local network. A phone
  joins with a QR code or a single-use PIN from Settings. The plan is
  in [Phone pairing](docs/PHONE_PAIRING_PLAN.md).
- **Multi-device isolation.** Each paired phone sees only its own
  chats. The desktop, which issued the PINs, can see every device.
  History is searchable, and a chat can be archived, renamed, or
  exported.
- **Cross-platform.** Linux ships a complete AppImage. macOS ships an
  unsigned disk image. Windows ships an executable that is not a
  finished product yet. The same tree also runs from source with
  `python run.py`. Status is in the [Roadmap](docs/ROADMAP.md).

## Read next

Everything past "try it" lives in `docs/`. Nothing below is required to
start the server.

- [Tutorial](docs/TUTORIAL.md) — the two ways to build a persona
  (Ollama CLI, and `python demo.py`), example prompts, and how to add
  another `.Modelfile`.
- [Features](docs/FEATURES.md) — the chat UI, streaming replies, saved
  history, model downloads, optional update checking, and phone pairing
  with per-device isolation.
- [Development](docs/DEVELOPMENT.md) — prerequisites, `pytest` versus
  `pytest -m integration`, what copies to another machine, packaged
  downloads, and building the Linux executable.
- [API contract](docs/API_CONTRACT.md) — every `/api/*` route as
  implemented. Clients, including the iOS app, should follow this
  instead of guessing.
- [Roadmap](docs/ROADMAP.md) — what is done and what is not.
- [Phone pairing plan](docs/PHONE_PAIRING_PLAN.md) — QR, PIN, and why
  the link is a normal `http://` URL.
- [Model benchmark](docs/MODEL_BENCHMARK.md) and
  [latest results](docs/BENCHMARK_RESULTS.md) — the prompt set and the
  last real comparison.

Commit messages follow Conventional Commits. See `CONTRIBUTING.md`.
Releases are `make release VERSION=vX.Y.Z`.
