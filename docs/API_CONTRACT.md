# PortableAI HTTP API contract

This is the **current** `/api/*` surface of `ui/server.py`. A separate
client (the iOS app, built elsewhere) should treat this file as the
source of truth for paths, auth, request bodies, and JSON field names.
Do not infer fields from older notes, UI copy, or planned work.

If you change a route in `ui/server.py` and do not update this file in
the same change, the PR is incomplete — same bar as skipping pytest.

Default listen: `http://<host>:5050` (`0.0.0.0`, all IPv4 interfaces).
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
  "system_preview": "You are a helpful, general-purpose assistant. Answer naturally and\ndirectly, with no particular persona or exaggerated personality --\njust be clear, accurate, and easy to talk to.",
  "parameters": { "temperature": 0.7 }
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Filename stem of `personas/<id>.Modelfile`. This is the value to send as `persona` on chat/create. |
| `display_name` | string | From `# display_name:` in the Modelfile, else a title-cased `id`. **This field exists on the backend today.** |
| `is_default` | boolean | From `# default:` in the Modelfile, else `false`. **This field exists on the backend today.** |
| `icon` | string | One of `message`, `shield`, `person`, `lightbulb`. |
| `base_model` | string | Modelfile `FROM`. |
| `system_preview` | string | First 160 characters of `SYSTEM` (may be `""`). |
| `parameters` | object | Modelfile `PARAMETER` map; keys vary. Numeric params are numbers. |

Unparseable Modelfile (same array; **omits** `base_model`,
`system_preview`, `parameters`):

```json
{
  "id": "broken-persona",
  "display_name": "Broken Persona",
  "is_default": false,
  "icon": "message",
  "error": "<parse error string>"
}
```

Clients should key off `id`, show `display_name`, treat `is_default` as
the sidebar default, and use `icon` for the glyph. Do not assume
`name`, `label`, `title`, or `default` — those are not in this payload.

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
{ "ollama_available": true, "base_url": "http://127.0.0.1:11434" }
```

`base_url` is the URL this process is actually using (managed bundled
Ollama if `run.py` set it, else `settings.json` `base_url`).

### `GET /api/theme`

**Auth:** requires-token (not admin-only — a paired phone may read/write
this. `/api/settings` stays admin-only.)

**Success (200):**

```json
{ "theme": "dark" }
```

`theme` is one of `dark`, `light`, `ube`. Missing `data/theme.json` →
`"dark"`.

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

**Request:**

```json
{ "pin": "123456", "device_name": "Jane’s iPhone" }
```

| Field | Required | Notes |
| --- | --- | --- |
| `pin` | yes | Non-empty after strip. Else **400** `{"error": "pin is required"}`. |
| `device_name` | no | Empty/omitted stores as `"Unnamed device"`. |

**Success (200):**

```json
{ "device_token": "<32-char hex>" }
```

**401** `{"error": "invalid or expired PIN"}` — wrong PIN, expired PIN,
already consumed PIN, or lockout after 5 failed attempts (that also
invalidates the PIN).

Store `device_token` and send it as `Authorization: Bearer …` on every
requires-token call. The PIN is single-use.

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

**Success (200):** `{"pulled": "llama3.2:3b"}`

**503** `{"error": "Ollama is not reachable. Run \`ollama serve\`."}`

**502** `{"error": "<message>"}`

Blocks until the pull finishes. No progress events.

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

If the remote JSON loads **(200):**

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
are whatever the remote JSON had (`null` if omitted). Update flags are
true only when the remote field is non-empty **and** differs from the
baked-in `APP_VERSION` / `CATALOG_VERSION`.

---

## Settings

Keys in `DEFAULT_SETTINGS` (GET returns all four; POST only writes keys
that exist here and are present in the body):

| Key | Type | Default |
| --- | --- | --- |
| `base_url` | string | `"http://localhost:11434"` |
| `auto_build_on_startup` | boolean | `true` |
| `update_check_url` | string | `""` |
| `auto_check_updates` | boolean | `false` |

On Linux, `run.py` can override the Ollama URL used by this process
without rewriting `settings.json`. GET still returns the file contents.

### `GET /api/settings`

**Auth:** admin-only

**Success (200):** the four-key object above.

### `POST /api/settings`

**Auth:** admin-only

**Request:** any subset of those keys. Unknown keys are ignored.

**Success (200):** the full saved settings object (same shape as GET).

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
      "created_at": 1732650000.5
    },
    {
      "role": "assistant",
      "content": "…",
      "latency_ms": 842,
      "created_at": 1732650001.2
    }
  ]
}
```

Message objects have **no** `id` field. `latency_ms` is set on assistant
rows, `null` on user rows. `owner_id` is `"local"` for the desktop, or
the device token for a phone.

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

**503** `{"error": "Ollama is not reachable. Run \`ollama serve\`."}`

**500** `{"error": "could not build persona '<id>': …"}` if the
Modelfile is missing or `create` fails with `OllamaError`.

**502** `{"error": "<message>"}` for connection/timeout/truncated JSON
while building or chatting (`"Lost connection to Ollama mid-request."`
or the incomplete-response string from `ollama_client`).

The call is **not** streaming. The UI waits for this full JSON object.

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
| GET | `/api/theme` | requires-token |
| POST | `/api/theme` | requires-token |
| GET | `/api/personas` | requires-token |
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
| GET | `/api/logs` | admin-only |
| DELETE | `/api/logs` | admin-only |

Non-API (not part of this contract, listed so clients do not look for
them under `/api`): `GET /` → `index.html`; `GET /<filename>` → files
in `ui/static/`.
