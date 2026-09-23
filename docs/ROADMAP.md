# PortableAI — roadmap

Consolidated from many rounds of decisions. Update this as things move,
so "what's the current state" has one real answer instead of living
only in chat history.

## Positioning (the actual differentiation, stated plainly)

Well-funded competitors (Perplexity's "Portable Computer" w/ NVIDIA,
launched Aug 2026) are pushing "local AI" at the expensive end —
$4,700 dedicated hardware, still requires a paid subscription, and by
their own admission isn't fully offline (escalates to cloud with
permission). Other offline/survival apps found in competitive research
(HAVEN, PrepGPT, Private LLM) are all single-device phone apps.

PortableAI's actual, defensible position: runs on any laptop including
old/scavenged hardware, completely free, genuinely zero internet
required ever, open source, and the only one found anywhere in this
research that does one server + multiple paired devices with isolated
per-device history. This needs to be *said*, not just be true —
see "Next up" below.

## Done and confirmed working

- Core chat, personas, SQLite history (search/archive/rename/export),
  model catalog + downloads + update checks — Linux, tested (241
  passing tests)
- LAN pairing: PIN + QR (plain URL with embedded PIN, not a custom
  scheme — confirmed on real hardware after the portableai:// scheme
  failed), per-device token auth
- Per-device data isolation — genuinely tested (not just assumed):
  cross-device reads/writes/exports all correctly blocked
- Security hardening: admin-only authz on logs/settings/model-pull
  (was previously open to any paired device), PIN-claim race condition
  fixed with file locking + atomic writes
- Reliability hardening: JSON error responses (not raw HTML 500s) for
  Ollama disconnects, corrupted-DB detection, request size limits,
  retry/backoff on model downloads (confirmed live against a real
  Cloudflare timeout on Mac)
- Vendored Ollama: Linux (CPU-only, confirmed working end-to-end) and
  macOS (Gatekeeper/quarantine handling confirmed working)
- macOS LAN IP detection (own implementation, separate from Linux's)
- Single-file compiled executable — Linux AppImage via GitHub Releases
  (confirmed: `v0.1.0-test` download runs `--help` / `--stop`). macOS
  ships an unsigned/unnotarized `.dmg` (Gatekeeper will warn; no Apple
  Developer cert). Windows ships a PyInstaller `.exe` that is **not** a
  complete product (see gaps below).
- Theme sync (dark/light/ube) shared between web UI and server, iOS
  alignment prompted
- iOS: pairing (QR scan + manual fallback), chat, sidebar/settings
  redesign (chat-first, matching ChatGPT/Claude navigation), Persona
  struct aligned to the real API contract (was previously mismatched
  and silently broken)
- docs/API_CONTRACT.md — single source of truth so the iOS repo
  doesn't have to guess the server's real API shape
- Benchmark tooling + real results: the custom `survival-guide` model
  beats stock models on both speed and directness for the actual use
  case, confirmed across 4 models and multiple runs

## Known, real gaps — not yet built

- **Persona-optional default + custom persona creation UI** — planned
  in detail, never built. This is the highest-value remaining gap:
  right now personas are still hand-edited .Modelfile files, and
  there's no neutral default someone lands on with zero picker.
- **`survival-guide` persona over-generalizes** — confirmed via live
  testing that it hijacks unrelated topics ("rainy afternoon" became
  emergency-prep advice) into its urgent all-caps format. Needs a
  scoping fix to the system prompt (restrict the format to genuine
  safety topics only).
- **iOS: conversation history view, pinned-chats browsing, model
  selector** — prompted, not yet confirmed built/tested.
- **iOS camera QR bug** — confirmed real, unresolved Apple bug
  (AVCaptureSession interruption reason 4). Manual PIN entry is the
  reliable path; not something PortableAI's own code can fix.
- **Windows server support** — nothing built (LAN detection, vendored
  Ollama, hotspot instructions all Linux/Mac only so far). The tagged
  Windows `.exe` is a UI wrapper only; pairing QR will not get a LAN
  address, and Ollama is not bundled.
- **macOS code signing / notarization** — the release `.dmg` is unsigned.
  Signing needs a paid Apple Developer account ($99/year) and GitHub
  secrets; those were not assumed. Gatekeeper will block until the user
  allows the app.
- **Open-source repo prep** — LICENSE file, dependency audit, final
  cleanup pass discussed in detail, not confirmed executed.
- **Outdoor field test** — planned; hotspot IP behavior
  (`10.42.0.1` assumption) and real battery/sunlight-readability
  behavior were never confirmed live before this test.

## Next up (in rough priority order)

1. Delete the stale, unused `site/` folder from this repo — confirmed
   not deployed, diverges from the real live portableai.app content
2. Positioning/messaging update (this session) — README.md and
   BRANDING.md should state the Perplexity/competitor contrast
   directly, not leave it implied. Scoped to THIS repo's own files only
   — the actual portableai.app marketing site lives in a separate Astro
   repo, not yet located/audited. Website work is explicitly deferred;
   current focus stays on the Ubuntu/macOS server + README.
3. Persona-optional default + creation UI — highest-value remaining
   feature gap
4. Fix `survival-guide`'s over-generalization (cheap, high-value fix)
5. Confirm iOS history/pinned/model-selector work actually landed and
   works live
6. Open-source repo prep and actual public push
7. Windows support (only once Linux + Mac are both genuinely solid) —
   still blocked on vendored Ollama + LAN IP detection, not on "can we
   produce an .exe"
8. Website (separate Astro repo) — audit its actual current content
   first before touching anything; it already has more developed
   positioning/pricing content ("Your AI, With No Signal Required," a
   $0/pre-loaded-drive/custom-build tier structure) than anything
   discussed in this repo's own docs. Reconcile against the "not a
   monetization-first project" decision below before assuming those
   pricing tiers are still the current plan.

## Deliberately NOT doing (decided, with reasons — don't re-litigate without new evidence)

- **Multi-device swarm/cluster inference** — directly conflicts with
  "portable and alone"; betting on models continuing to shrink instead
- **Forking/refactoring Ollama itself** — every customization need so
  far has been solved through Ollama's existing extension points
  (Modelfiles, API, env vars); forking means permanent maintenance debt
  for a problem that hasn't actually appeared
- **Docker as the primary/default path** — real RAM cost (2-4GB+ for
  Docker Desktop's VM on Mac/Windows) directly conflicts with the
  low-RAM, scavenged-hardware premise; `python run.py` stays the one
  recommended path
- **LangChain / Weights & Biases / Prompt Flow** — solve problems
  (multi-step agent chains, ML training tracking, team prompt
  collaboration) this project doesn't have; would add real dependency
  weight to wrap code that already works and is understood
- **AGPL or other copyleft licensing** — conflicts with eventual App
  Store distribution of the iOS app (confirmed real historical
  conflict, e.g. Apple pulling GNU Go in 2010); MIT recommended across
  the whole project instead
- **Treating this as a monetization-first project** — by the project's
  own established standard (durable income needs a real forcing
  function, not dependent on ongoing user/client motivation),
  PortableAI doesn't clear that bar the way other side-income ideas do.
  Primary value right now: open-source credibility + job-search
  portfolio piece. Revisit only if real, organic demand shows up
  (someone asking to pay for something specific), not speculatively.
