# PortableAI HTTP API contract

This is the **current** `/api/*` surface of `ui/server.py`. A separate
client (the iOS app, built elsewhere) should treat this file as the
source of truth for paths, auth, request bodies, and JSON field names.
Do not infer fields from older notes, UI copy, or planned work.

If you change a route in `ui/server.py` and do not update this file in
the same change, the PR is incomplete — same bar as skipping pytest.

Default listen: `http://<host>:5050` (`0.0.0.0`, all IPv4 interfaces).
If 5050 is already taken, the server tries 5051, 5052, … and prints that
it moved; pairing QR / `pairing_web_url` use the port that actually bound.
There is no `/v1` prefix. JSON responses use Flask `jsonify`
(`Content-Type: application/json`) unless noted. Request bodies are
parsed with `request.get_json(force=True)` (JSON is accepted even
without `Content-Type: application/json`). Bodies larger than **1 MB**
return **413** `{"error": "request too large"}`.

A wiped or unreadable `data/chats.db` makes conversation routes return
**500** `{"error": "chat history database appears corrupted"}`.

Malformed JSON / empty body on routes that call `get_json(force=True)`
may surface as Flask’s default **400**, not the `{"error": ...}` objects
below.

---

## Auth

Enforced by `_check_auth` on every path that starts with `/api/`.
Static files (`/`, `/app.js`, …) are not under `/api/` and are public.

**Who is “local”:** `request.remote_addr` is loopback only (`127.0.0.1`,
`127.*`, `::1`, IPv4-mapped `::ffff:127.0.0.1`). Connecting to the
machine’s LAN IP from the same host is **not** local — that is how
phones connect, and it requires pairing.

**Token:** header `Authorization: Bearer <device_token>` (literal
`Bearer ` prefix, then the hex token from `POST /api/pairing/claim`).
There is no other auth scheme.

| Class | Meaning |
| --- | --- |
| **LAN-open** | No token, no loopback. Any client that can reach the port. There is no extra “must be on the LAN subnet” check. |
| **admin-only** | Loopback only. A valid device token is **not** enough. **403** `{"error": "only available on the server machine itself"}`. |
| **requires-token** | Loopback **or** a valid Bearer token. Otherwise **401** `{"error": "pairing required"}`. |

There is no `/api/*` route that is “open” in a different sense than
LAN-open. Desktop (loopback) is treated as admin for data ownership:
`owner_id` `"local"`, `is_admin` true. A paired phone’s `owner_id` is
its device token; it only sees its own conversations.

---

## `GET /api/personas`

**Auth:** requires-token

**Request:** none

**Success (200):** a JSON **array** (not wrapped in an object). Each
element is one of the two shapes below. Field names are verbatim from
the handler.

Parsed persona (the usual case):

```json
{
  "id": "assistant",
  "display_name": "Assistant",
  "is_default": true,
  "icon": "message",
  "base_model": "llama3.2:3b",
  "system_prompt": "You are a helpful, general-purpose assistant. …",
  "system_preview": "You are a helpful, general-purpose assistant. Answer naturally and\ndirectly, with no particular persona or exaggerated personality --\njust be clear, accurate, and easy to talk to.",
  "parameters": { "temperature": 0.7 },
  "cards": [],
  "model_installed": true
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Filename stem of `personas/<id>.Modelfile`. This is the value to send as `persona` on chat/create. |
| `display_name` | string | From `# display_name:` in the Modelfile, else a title-cased `id`. **This field exists on the backend today.** |
| `is_default` | boolean | From `# default:` in the Modelfile, else `false`. **This field exists on the backend today.** |
| `icon` | string | One of `message`, `shield`, `person`, `lightbulb`. |
| `base_model` | string | Modelfile `FROM`. |
| `system_prompt` | string | Full `SYSTEM` text (may be `""`). |
| `system_preview` | string | First 160 characters of `SYSTEM` (may be `""`). |
| `parameters` | object | Modelfile `PARAMETER` map; keys vary. Numeric params are numbers. |
| `cards` | array | Quick-reference cards from `personas/<id>.cards.json`. Missing file → `[]`. Each card: `id`, `title`, `answer`, `verified` (boolean, never inferred from text), `verified_by`, `verified_source` (null when unverified). |
| `model_installed` | boolean | Whether `base_model` is present in the same inventory as `GET /api/models`. `false` when Ollama is down, the Modelfile failed to parse, or the tag is not installed. |

Unparseable Modelfile (same array; **omits** `base_model`,
`system_prompt`, `system_preview`, `parameters`):

```json
{
  "id": "broken-persona",
  "display_name": "Broken Persona",
  "is_default": false,
  "icon": "message",
  "error": "<parse error string>",
  "cards": [],
  "model_installed": false
}
```

Clients should key off `id`, show `display_name`, treat `is_default` as
the sidebar default, and use `icon` for the glyph. Do not assume
`name`, `label`, `title`, or `default` — those are not in this payload.

### `POST /api/personas`

**Auth:** admin-only

**Request:** `display_name`, `base_model` (must be an installed Ollama
model), `system_prompt`, optional `is_default`, optional `cards`.

`verified: true` on any card is **400** — the create path cannot mark
cards verified. Use `PUT /api/personas/<id>/cards/<card_id>` for that.

**Success (201):** the same persona object as GET (including `cards`).

**400** specific `{"error": "…"}` for empty/too-long fields, uninstalled
`base_model`, or a verified card on create.

**503** if Ollama is down (cannot validate `base_model`).

### `PUT /api/personas/<id>`

**Auth:** admin-only

Edits `display_name`, `system_prompt`, `is_default`, `base_model`.
**Does not touch cards.** A `cards` key in the body is ignored.

Cannot unset the only default. **404** if the persona does not exist.

### `DELETE /api/personas/<id>`

**Auth:** admin-only

Removes the `.Modelfile` and sibling `.cards.json`. **400** if this is
the only remaining persona. Deleting the default when others exist
promotes another persona to default so there is always exactly one.

**404** `{"error": "persona not found"}`.

### `POST /api/personas/<id>/cards`

**Auth:** admin-only

**Request:** `title`, `answer`. `verified: true` is **400**.

**Success (201):** the new card object (`verified` is always `false`).

### `PUT /api/personas/<id>/cards/<card_id>`

**Auth:** admin-only

**This is the only way to set `verified` to true.** Request may include
`title`, `answer`, `verified`, `verified_by`, `verified_source`.

Setting `verified: true` without both `verified_by` and
`verified_source` is **400**. Editing title/answer without sending
`verified: true` clears the verified flag.

**404** if the persona or card does not exist.

### `DELETE /api/personas/<id>/cards/<card_id>`

**Auth:** admin-only

**Success (200):** `{"deleted": true, "id": "<card_id>"}`.

---

## Health and status

### `GET /api/health`

**Auth:** LAN-open

**Success (200):**

```json
{ "ok": true, "app": "PortableAI", "app_version": "0.4.0" }
```

`app` / `app_version` are `APP_NAME` / `APP_VERSION` in `server.py`.

### `GET /api/status`

**Auth:** requires-token

**Success (200):**

```json
{
  "ollama_available": true,
  "base_url": "http://127.0.0.1:11434",
  "ollama_mode": "bundled",
  "ollama_mode_label": "Ollama: bundled (port 11434)",
  "ollama_reason": ""
}
```

`base_url` is the URL this process is actually using (managed bundled
Ollama if `run.py` set it, else `settings.json` `base_url`).

`ollama_mode` / `ollama_mode_label` are read-only. `bundled` means
`run.py` launched PortableAI's own Ollama; `external` means
`PORTABLEAI_EXTERNAL_OLLAMA_URL` was set at launch (skip vendoring).
`default` means no managed URL was set (for example tests that import
the server module without `run.py`). `unavailable` means Ollama is not
reachable right now — either this OS has no bundled binary yet
or a configured instance refused the connection.
`ollama_reason` is the human-readable sentence the UI banner and
model-pull 503 share; empty when Ollama is up. On the unsupported-OS
case the payload also includes
`"ollama_install_url": "https://ollama.com/download"`. The Settings UI
shows `ollama_mode_label` as-is.

### `GET /api/theme`

**Auth:** requires-token (not admin-only — a paired phone may read/write
this. `/api/settings` stays admin-only.)

**Success (200):**

```json
{ "theme": "dark" }
```

`theme` is one of `dark`, `light`, `ube`. Missing `data/theme.json` →
`"dark"`.

### `GET /api/setup-status`

**Auth:** requires-token

**Success (200):** first-run vendor progress. The setup overlay polls this
while the bundled Ollama archive is downloaded. `busy` is true only for
`downloading_ollama`, `extracting`, and `starting_ollama`.

```json
{
  "phase": "ready",
  "percent": null,
  "bytes_downloaded": null,
  "bytes_total": null,
  "message": "",
  "busy": false,
  "stalled": false,
  "setup_log": "/absolute/path/to/data/setup.log"
}
```

`phase` is one of `ready`, `downloading_ollama`, `extracting`,
`starting_ollama`, `error`. `percent` is `0`–`100` during the download,
else JSON `null`. `setup_log` is the OS-specific path of the persistent
engine/model download log (`<data_dir>/setup.log`). `stalled` is true
when `downloading_ollama` has received no new bytes for 60 seconds;
`message` is then `"Download appears stalled -- check your connection"`.

### `POST /api/theme`

**Auth:** requires-token

**Request:** `{"theme": "light"}` — must be exactly `dark`, `light`, or
`ube`.

**Success (200):** `{"theme": "light"}` (echoes the saved value)

**400** `{"error": "theme must be one of: dark, light, ube"}`

---

## Pairing

### `GET /api/pairing/pin`

**Auth:** admin-only

**Success (200):**

```json
{
  "pin": "123456",
  "expires_at": 1732650300.0,
  "lan_ip": "192.168.1.134",
  "lan_ip_detected": true,
  "lan_url": "http://192.168.1.134:5050",
  "pairing_uri": "portableai://pair?ip=192.168.1.134&port=5050&pin=123456&name=hostname&exp=1732650300",
  "pairing_web_url": "http://192.168.1.134:5050/?pair_pin=123456&exp=1732650300"
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `pin` | string | 6-digit, zero-padded. |
| `expires_at` | number | Unix time (seconds, float). PIN TTL is 300 seconds. |
| `lan_ip` | string | Chosen IPv4, or `"127.0.0.1"` if none found. |
| `lan_ip_detected` | boolean | `false` means `lan_ip` is the localhost fallback and a phone cannot use it. |
| `lan_url` | string | `http://{lan_ip}:{PORT}` with module constant `PORT` (**5050**), not necessarily the `--port` the process was started with. |
| `pairing_uri` | string | `portableai://pair?...` query keys: `ip`, `port`, `pin`, `name` (hostname), `exp` (int). Reserved for a native handler; the QR does **not** encode this. |
| `pairing_web_url` | string | What the QR encodes. Query keys: `pair_pin`, `exp`. |

If no PIN is stored or it has expired, the server generates one first.

### `GET /api/pairing/qr.svg`

**Auth:** admin-only

**Success (200):** raw SVG (`Content-Type: image/svg+xml`), not JSON.
Encodes `pairing_web_url`, not `pairing_uri`.

### `POST /api/pairing/pin/regenerate`

**Auth:** admin-only

**Request:** no body required.

**Success (200):** PIN fields only — **not** the LAN/QR extras from GET:

```json
{ "pin": "654321", "expires_at": 1732650600.0 }
```

### `POST /api/pairing/claim`

**Auth:** LAN-open (this is how a phone pairs)

**Request** — send **either** a PIN **or** a family password, not both
required:

```json
{ "pin": "123456", "device_name": "Jane’s iPhone" }
```

```json
{ "family_password": "house-key", "device_name": "Jane’s iPhone" }
```

| Field | Required | Notes |
| --- | --- | --- |
| `pin` | one of `pin` / `family_password` | Non-empty after strip uses the PIN path (existing behavior). |
| `family_password` | one of `pin` / `family_password` | Used only when `pin` is omitted/empty. Does not expire, not single-use. |
| `device_name` | no | Empty/omitted stores as `"Unnamed device"`. |

If both `pin` and `family_password` are sent, **PIN wins**.

**Success (200):**

```json
{ "device_token": "<32-char hex>" }
```

A password-issued token is indistinguishable from a PIN-issued one:
same `Authorization: Bearer …` header, same requires-token access, same
admin-only 403s, same per-device conversation isolation.

**400** `{"error": "pin or family_password is required"}` — both empty.

**401** `{"error": "invalid or expired PIN"}` — wrong / expired /
already consumed PIN, or PIN lockout after 5 failed attempts (that also
invalidates the PIN).

**401** `{"error": "invalid family password"}` — no password configured,
or a wrong password that has not yet hit the per-source limit.

**429** `{"error": "too many failed password attempts"}` — 5 wrong
password guesses from the same client IP. Other IPs are unaffected.
This counter is **not** shared with the PIN lockout.

Store `device_token` and send it as `Authorization: Bearer …` on every
requires-token call. The PIN is single-use. The family password is not.

### `GET /api/pairing/devices`

**Auth:** admin-only (prefix `/api/pairing/devices`)

**Success (200):** JSON array:

```json
[
  { "token": "<device_token>", "name": "Jane’s iPhone", "paired_at": 1732650000.0 }
]
```

### `DELETE /api/pairing/devices/<token>`

**Auth:** admin-only (same prefix)

**Success (200):** `{"revoked": true}` if that token existed,
`{"revoked": false}` if it did not. Always 200.

---

## Models

### `GET /api/models`

**Auth:** requires-token

**Success (200)** when Ollama is up:

```json
{
  "models": [
    {
      "name": "llama3.2:3b",
      "digest": "sha256:…",
      "size_bytes": 2019393184,
      "size_human": "1.9 GB",
      "quantization": "Q4_K_M",
      "parameter_size": "3.2B",
      "modified_at": "2026-09-12T12:50:00.000000Z"
    }
  ],
  "models_path_hint": "/absolute/or/~/.ollama/models"
}
```

`name` / `digest` / `size_bytes` / `modified_at` can be JSON `null` if
Ollama omitted them. `size_human` is `null` when size is missing or 0.
`quantization` and `parameter_size` come from Ollama `details` and may
be `null`.

**200** when Ollama is not reachable (not 503):

```json
{ "models": [], "models_path_hint": "…", "error": "Ollama not reachable" }
```

**502** `{"error": "<message>"}` if listing throws after a reachability
check passed. Connection/timeout → `"Lost connection to Ollama mid-request."`

### `GET /api/models/catalog`

**Auth:** admin-only

**Success (200):** JSON **array** of catalog objects from
`ui/model_catalog.json`, each with `installed` added:

```json
{
  "name": "llama3.2:3b",
  "family": "Llama 3.2",
  "parameter_size": "3B",
  "approx_size": "2.0GB",
  "description": "…",
  "recommended": true,
  "recommended_reason": "…",
  "installed": true
}
```

| Field | Always present? |
| --- | --- |
| `name`, `family`, `parameter_size`, `approx_size`, `description` | yes, in the current catalog file |
| `recommended`, `recommended_reason` | only on some entries |
| `installed` | always added by the server (`true`/`false`) |

If Ollama is down, `installed` is `false` for every row. Missing or
invalid catalog file → `[]`.

### `POST /api/models/pull`

**Auth:** admin-only

**Request:** `{"name": "llama3.2:3b"}` — `name` required after strip,
else **400** `{"error": "name is required"}`.

**503** `{"error": "<ollama_reason>"}` — same sentence as
`GET /api/status` `ollama_reason` when Ollama is down (unsupported OS:
not bundled yet; otherwise `"Cannot reach Ollama -- is it running?"`).
Plain JSON; pull does not start.

Once the pull starts, **200** with `Content-Type: application/x-ndjson`:
newline-delimited JSON events. Transient connection failures are retried
a few times (3 attempts, backoff 2s/5s/10s); each retry emits a status
line before sleeping.

| Event | Shape |
| --- | --- |
| retry (optional, 0+) | `{"status":"retrying","message":"Connection issue, retrying (2/3)...","attempt":2,"max_attempts":3}` |
| success (final) | `{"pulled":"llama3.2:3b"}` |
| failure (final) | `{"error":"<message>"}` |

Clients should read the stream to the end and treat the last object as
the result (check `pulled` vs `error`). There is no byte-level download
progress — only retry status between full pull attempts.

### `POST /api/models/check-update`

**Auth:** admin-only

**Request:** `{"name": "llama3.2:3b"}` — same required `name` as pull.

**Success (200):**

```json
{
  "name": "llama3.2:3b",
  "updated": false,
  "digest_before": "sha256:…",
  "digest_after": "sha256:…"
}
```

`updated` is `true` when the digest changed. **404**
`{"error": "'<name>' is not installed"}`. Same 503/502 as pull.

---

## Updates (optional, off by default)

### `GET /api/updates/check`

**Auth:** admin-only

No request body. Uses `update_check_url` from settings.

If that URL is empty **(200):**

```json
{ "enabled": false }
```

If set but the fetch fails **(200):**

```json
{ "enabled": true, "reachable": false }
```

If `update_check_url` is `owner/repo` or a `https://github.com/owner/repo…`
URL, the server calls GitHub Releases
`GET https://api.github.com/repos/<owner>/<repo>/releases/latest` and
compares `tag_name` to `APP_VERSION` with numeric semver (so `0.10.0`
is newer than `0.9.0`; a leading `v` is ignored). There
is no auto-download. `notes_url` is the Release `html_url`. Catalog
fields stay unset.

```json
{
  "enabled": true,
  "reachable": true,
  "current_app_version": "0.4.0",
  "current_catalog_version": "2026-09-06",
  "remote_app_version": "v0.5.0",
  "remote_catalog_version": null,
  "app_update_available": true,
  "catalog_update_available": false,
  "message": "A new version is available.",
  "notes_url": "https://github.com/owner/repo/releases/tag/v0.5.0"
}
```

Any other http(s) URL still loads the original JSON manifest
`{"app_version", "catalog_version", "message", "notes_url"}` **(200):**

```json
{
  "enabled": true,
  "reachable": true,
  "current_app_version": "0.4.0",
  "current_catalog_version": "2026-09-06",
  "remote_app_version": "0.5.0",
  "remote_catalog_version": "2026-10-01",
  "app_update_available": true,
  "catalog_update_available": true,
  "message": "…",
  "notes_url": "https://…"
}
```

`remote_app_version`, `remote_catalog_version`, `message`, `notes_url`
are whatever the remote JSON had (`null` if omitted). `app_update_available`
is true only when the remote app version is a **higher** semver than
`APP_VERSION` (numeric segments, not string compare). Catalog flags stay
true when the remote catalog field is non-empty **and** differs from
`CATALOG_VERSION`. GitHub mode only compares the app version (the Release
tag). The check stays opt-in: an empty
URL never hits the network.

---

## Settings

Keys in `DEFAULT_SETTINGS` (GET returns these four plus
`family_password_set`; POST only writes the four keys below that are
present in the body, plus the write-only `family_password` field):

| Key | Type | Default |
| --- | --- | --- |
| `base_url` | string | `"http://localhost:11434"` |
| `auto_build_on_startup` | boolean | `true` |
| `update_check_url` | string | `""` |
| `auto_check_updates` | boolean | `false` |

`family_password` is **not** stored in `settings.json`. It lives in
`pairing.json` as a hash. GET never echoes the secret.

| Extra GET field | Type | Default |
| --- | --- | --- |
| `family_password_set` | boolean | `false` |

On Linux, `run.py` can override the Ollama URL used by this process
without rewriting `settings.json`. GET still returns the file contents
for the four keys above.

### `GET /api/settings`

**Auth:** admin-only

**Success (200):** the four-key object plus `family_password_set`.

### `POST /api/settings`

**Auth:** admin-only (localhost only — a valid device token is **403**)

**Request:** any subset of the four `DEFAULT_SETTINGS` keys, and/or
write-only `family_password` (string; empty or `null` clears it).
Unknown keys are ignored. Non-string `family_password` is **400**
`{"error": "family_password must be a string"}`.

**Success (200):** the full GET payload (`family_password_set` reflects
the new value; the password itself is never returned).

---

## Conversations

Ownership: a paired client only sees rows whose `owner_id` equals its
device token. Loopback sees every device (`owner_id` filter omitted).
Someone else’s id is **404** `{"error": "conversation not found"}`, never 403.

SQLite integers are serialized as JSON **numbers**. `archived` in list/get
payloads is `0` or `1`, not `true`/`false`. `title` may be JSON `null`
until the first user message (then it is the first line of that message,
max 60 characters). Timestamps are Unix seconds (float).

### Conversation object (GET one / export)

```json
{
  "id": "uuid",
  "owner_id": "local",
  "persona": "assistant",
  "model_used": "assistant",
  "title": "Should I deploy on Friday?",
  "created_at": 1732650000.0,
  "updated_at": 1732650001.0,
  "archived": 0,
  "messages": [
    {
      "role": "user",
      "content": "Should I deploy on Friday?",
      "latency_ms": null,
      "created_at": 1732650000.5,
      "source": "chat",
      "source_id": null,
      "source_meta": null
    },
    {
      "role": "assistant",
      "content": "…",
      "latency_ms": 842,
      "created_at": 1732650001.2,
      "source": "chat",
      "source_id": null,
      "source_meta": null
    }
  ]
}
```

Message objects have **no** `id` field. `latency_ms` is set on assistant
rows, `null` on user rows. `owner_id` is `"local"` for the desktop, or
the device token for a phone. `source` is `"chat"` for model replies and
`"card"` for quick-reference inserts. Card rows include `source_id` (the
card id) and `source_meta` `{card_id, verified, verified_by, verified_source}`
snapshotted at insert time.

### List row (GET collection)

Same conversation keys as above **except there is no `messages` array**
(`SELECT` of the `conversations` row only).

### `GET /api/conversations`

**Auth:** requires-token

**Query:**

| Param | Meaning |
| --- | --- |
| `archived` | Only `"1"` lists archived. Missing or any other value lists active. |
| `q` | Optional substring search on title **or** message content. |

**Success (200):** JSON array of list rows, `updated_at` descending.

### `POST /api/conversations`

**Auth:** requires-token

**Request:**

```json
{ "persona": "assistant", "model_override": "qwen2.5:7b" }
```

| Field | Required | Notes |
| --- | --- | --- |
| `persona` | yes | Must match a `personas/<persona>.Modelfile` stem. Else **400** `{"error": "persona is required"}` or `{"error": "no such persona: …"}`. |
| `model_override` | no | Stored as `model_used` if set; otherwise the persona’s `FROM`. |

**Success (200):** `{"id": "<uuid>"}` only.

### `GET /api/conversations/<conv_id>`

**Auth:** requires-token

**Success (200):** conversation object including `messages`.

**404** `{"error": "conversation not found"}`

### `GET /api/conversations/<conv_id>/export`

**Auth:** requires-token

Same ownership and **200** body as GET one conversation. Still
`application/json` (no `Content-Disposition` filename).

### `PATCH /api/conversations/<conv_id>`

**Auth:** requires-token

**Request:** `{"title": "My renamed chat"}` — non-empty after strip,
else **400** `{"error": "title is required"}`.

**Success (200):** `{"id": "<conv_id>", "title": "My renamed chat"}`

**404** as above.

### `POST /api/conversations/<conv_id>/archive`

**Auth:** requires-token

**Request:** `{"archived": true}` — missing `archived` defaults to
`true`. Value is `bool(...)` of the JSON value.

**Success (200):** `{"id": "<conv_id>", "archived": true}` — here
`archived` **is** a JSON boolean (the request value), not `0`/`1`.

**404** as above.

### `DELETE /api/conversations/<conv_id>`

**Auth:** requires-token

**Success (200):** `{"deleted": true}`

**404** as above.

---

## Chat

### `POST /api/chat`

**Auth:** requires-token

**Request:**

```json
{
  "persona": "assistant",
  "message": "Summarize TCP vs UDP.",
  "model_override": null,
  "conversation_id": null
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `persona` | yes | Modelfile stem. |
| `message` | yes | Empty string is rejected. **400** `{"error": "persona and message are required"}`. |
| `model_override` | no | Falsy/omitted → persona’s own `FROM`. If set, Ollama model name is `{persona}--{slug(override)}` (e.g. `no-nonsense-mentor--qwen2.5-0.5b`). |
| `conversation_id` | no | Continue an existing chat. Omitted/null creates a new conversation owned by the caller. |

**Success (200):**

```json
{
  "reply": "…",
  "latency_ms": 842,
  "model_used": "assistant",
  "conversation_id": "uuid"
}
```

`model_used` is the Ollama model name actually chatted against (persona
id, or the `--` variant). `latency_ms` is an integer millisecond count.

**404** `{"error": "conversation not found"}` if `conversation_id` is
unknown or not owned by the caller.

**503** `{"error": "<ollama_reason>"}` — same sentence as
`GET /api/status` when Ollama is down.

**500** `{"error": "could not build persona '<id>': …"}` if the
Modelfile is missing or `create` fails with `OllamaError`.

**502** `{"error": "<message>"}` for connection/timeout/truncated JSON
while building or chatting (`"Lost connection to Ollama mid-request."`
or the incomplete-response string from `ollama_client`).

The call is **not** streaming. The UI waits for this full JSON object.

### `POST /api/chat/reference`

**Auth:** requires-token

Inserts a quick-reference card into conversation history **without**
calling Ollama.

**Request:** `{"persona": "survival-guide", "card_id": "severe-bleeding", "conversation_id": null}`

**Success (200):**

```json
{
  "reply": "PLACEHOLDER -- …",
  "user_message": "Severe bleeding",
  "latency_ms": 0,
  "model_used": "llama3.2:3b",
  "conversation_id": "uuid",
  "source": "card",
  "card_id": "severe-bleeding",
  "verified": false,
  "verified_by": null,
  "verified_source": null
}
```

**400** `{"error": "persona and card_id are required"}`

**404** `{"error": "persona not found"}` or `{"error": "card not found"}`.

---

## Logs (server machine)

### `GET /api/logs`

**Auth:** admin-only

**Success (200):** JSON array, newest first, last 200 lines of
`data/logs.jsonl`. Chat success entries:

```json
{
  "timestamp": 1732650001.2,
  "conversation_id": "uuid",
  "persona": "assistant",
  "model_used": "assistant",
  "message": "…",
  "reply": "…",
  "latency_ms": 842
}
```

Failed chat entries omit `reply` / `latency_ms` and include `"error"`.

Missing log file → `[]`.

### `DELETE /api/logs`

**Auth:** admin-only

**Success (200):** `{"cleared": true}`

---

## Route index

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/health` | LAN-open |
| GET | `/api/status` | requires-token |
| GET | `/api/setup-status` | requires-token |
| GET | `/api/theme` | requires-token |
| POST | `/api/theme` | requires-token |
| GET | `/api/personas` | requires-token |
| POST | `/api/personas` | admin-only |
| PUT | `/api/personas/<id>` | admin-only |
| DELETE | `/api/personas/<id>` | admin-only |
| POST | `/api/personas/<id>/cards` | admin-only |
| PUT | `/api/personas/<id>/cards/<card_id>` | admin-only |
| DELETE | `/api/personas/<id>/cards/<card_id>` | admin-only |
| GET | `/api/pairing/pin` | admin-only |
| GET | `/api/pairing/qr.svg` | admin-only |
| POST | `/api/pairing/pin/regenerate` | admin-only |
| POST | `/api/pairing/claim` | LAN-open |
| GET | `/api/pairing/devices` | admin-only |
| DELETE | `/api/pairing/devices/<token>` | admin-only |
| GET | `/api/models` | requires-token |
| GET | `/api/models/catalog` | admin-only |
| POST | `/api/models/pull` | admin-only |
| POST | `/api/models/check-update` | admin-only |
| GET | `/api/updates/check` | admin-only |
| GET | `/api/settings` | admin-only |
| POST | `/api/settings` | admin-only |
| GET | `/api/conversations` | requires-token |
| POST | `/api/conversations` | requires-token |
| GET | `/api/conversations/<conv_id>` | requires-token |
| GET | `/api/conversations/<conv_id>/export` | requires-token |
| PATCH | `/api/conversations/<conv_id>` | requires-token |
| POST | `/api/conversations/<conv_id>/archive` | requires-token |
| DELETE | `/api/conversations/<conv_id>` | requires-token |
| POST | `/api/chat` | requires-token |
| POST | `/api/chat/reference` | requires-token |
| GET | `/api/logs` | admin-only |
| DELETE | `/api/logs` | admin-only |

Non-API (not part of this contract, listed so clients do not look for
them under `/api`): `GET /` → `index.html`; `GET /<filename>` → files
in `ui/static/`.
