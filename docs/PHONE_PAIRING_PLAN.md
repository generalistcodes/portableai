# PortableAI — phone pairing & connection experience

PIN, QR, and LAN IP detection are **built**. This doc is the pairing
spec plus remaining work. Live pairing on a real phone has **not** been
confirmed on hardware — that verification is still needed.

## What's already true (built and tested, 167 passing unit tests)

- Server binds to the LAN, not just localhost.
- A 6-digit PIN (5 min expiry, single-use, brute-force lockout) is the
  only way a device not on the server machine itself can get a token.
- Each paired device is its own data-isolation boundary — no accounts,
  no passwords. The desktop (wherever the server runs) is the one
  identity that sees every device's data.
- "Multiple user support" is effectively already solved by this model:
  each person's phone pairs independently and never sees another
  device's conversations. What's still missing is making that pairing
  *fast and obvious* — the six points below are all about that.

## 1. Connecting the phone — three tiers, easiest first

**Tier 1 — QR code (the target experience).** The laptop displays a QR
code alongside the PIN. Scanning it in the app auto-fills the server
address and PIN, no typing at all. This is the single highest-impact
change on this list.

**Tier 2 — manual entry (already works).** Type the LAN IP and PIN by
hand. Fine as a fallback, tedious as a default.

**Tier 3 — auto-discovery (stretch goal, do last).** The phone finds the
laptop on the network with no address entry at all — the "must be easy"
requirement taken to its conclusion. This needs mDNS/Bonjour: iOS
supports it natively, but Linux needs either Avahi or the Python
`zeroconf` library running alongside the server, and it can be blocked
by some routers' AP/client isolation settings. Worth having, but the
most fragile piece here — sequence it after the QR code, not before.

## 2. What the QR code / pairing info should actually encode

**Built.** The desktop Settings QR encodes a plain HTTP URL any phone
camera opens without an app:

```
http://192.168.1.134:5050/?pair_pin=815010&exp=1732650000
```

A `portableai://pair?...` deep link is still generated for a future
native-app handler (ip, port, pin, name, exp), but scanning that scheme
today with no app installed is unrecognized text — which is why the live
QR uses `http://...?pair_pin=` instead.

- `ip` + `port` — exact address, no typing, no guessing which of several
  IPs on the laptop is the right one
- `pin` — the current one-time code
- `name` — a friendly label for *this* laptop, useful once someone has
  more than one PortableAI server around (home vs. work machine)
- `exp` — PIN expiry as a timestamp, so the app can show a live
  countdown instead of the user finding out it expired only when the
  claim request fails

This only needs the server to draw a QR image from data it already
returns (`/api/pairing/pin`) — no new pairing logic, just a new way to
present the same information. Turning that URI into an actual QR image
needs a small dependency (e.g. the `qrcode` package's SVG output, which
needs no Pillow/image library) — worth flagging since everything else in
this project has deliberately stayed dependency-light.

## 3. Turning the laptop into its own WiFi hotspot

This is what makes "must be easy, just connect to the same WiFi" true
even with **no router and no internet at all** — directly serving the
offline-first goal already decided for this whole project.

**Ubuntu (now):** NetworkManager can do this in one command:
```
nmcli device wifi hotspot ifname wlan0 ssid PortableAI password <a-password-you-choose>
```
Worth knowing: when the laptop *is* the hotspot (rather than joining an
existing WiFi network), its LAN IP is usually a fixed, predictable
address — NetworkManager's default hotspot config typically hands out
`10.42.0.1` to itself — rather than something that changes each time
like a normal DHCP-assigned address would. That's worth calling out
explicitly in the startup banner once this exists, since a stable IP
makes the whole pairing story simpler (the QR code's IP stops changing
between sessions).

**macOS (later, once you're on the Mac):** System Settings → Sharing →
Internet Sharing does the equivalent. Exact steps to confirm once that
machine is actually in the picture — the mechanism differs from
NetworkManager but the end result (laptop becomes its own access point)
is the same idea.

Open question: should PortableAI itself offer a "Start hotspot" button
(shelling out to `nmcli`), or should this stay a documented manual step,
keeping the app itself free of network-configuration responsibility?
Leaning toward documented-manual-step for v1 — shelling out to system
networking commands from a Flask app is a bigger trust/permissions
surface than anything built so far.

## 4. Multi-user — confirming the model, one small refinement

The per-device isolation already built *is* the multi-user support.
Nothing new needed there. One low-cost refinement worth doing whenever
pairing UI gets touched next: nudge people to type an actual person's
name at pairing time (e.g. placeholder text "e.g. Sarah's iPhone")
rather than leaving it as a generic device label — makes the admin's
device list in Settings meaningfully more readable with zero schema
changes, since `device_name` is already free text today.

Deliberately not adding: roles or elevated permissions for a *device*.
The current model — "whoever is physically at the laptop is the admin,
no exceptions" — is the simplest security boundary available and
shouldn't get more complicated without a concrete reason to.

## Zero-config is the actual bar, not a nice-to-have

**Status: fixed.** `_get_lan_ip()` now enumerates real network interfaces
(`hostname -I`, falling back to `ip -4 -json addr show`) instead of
relying on a routing-table guess. It returns `{"ip": ..., "detected":
bool}` — when `detected` is `False`, the pairing UI shows a visible
warning instead of silently offering `127.0.0.1` as if it were a real
address. Prefers a NetworkManager hotspot's `10.42.x.x` subnet when
present, since that's almost always the network a phone is meant to
join.

**Also done:** `/api/pairing/qr.svg` renders a scannable QR (HTTP
`pair_pin` URL, localhost-only like the PIN itself). Uses the `qrcode`
package's SVG output (no Pillow, no compiled dependency). The
`portableai://pair?ip=...&port=...&pin=...&name=...&exp=...` schema is
still returned as `pairing_uri` for a future native handler.

**Still needed (not confirmed on hardware):** verify live phone pairing
— a fresh server start, checking the console warns correctly if no
interface is found, and an actual phone scanning the real QR code and
pairing successfully. That can only be tested for real, not simulated.

macOS will need its own interface-enumeration equivalent later (likely
`ifconfig` or `networksetup` output) — same idea, different command,
added once that machine is in the picture.

## Suggested build order, once this is ready to become code

1. ~~Fix IP detection first~~ — **done**: real interface enumeration
   replaces the routing-table guess, with a visible warning when nothing
   is found instead of a silent wrong answer.
2. ~~Finalize the pairing URI schema~~ — **done**, and QR-encoded.
3. ~~Add QR code generation to Settings~~ — **done**.
4. **Verify live phone pairing actually works** — still needed; not
   confirmed on hardware. The one remaining step that needs a real
   phone, not more code.
5. Documented hotspot setup — Ubuntu now, macOS once that machine exists
6. mDNS/Bonjour auto-discovery (most fragile, do last)

## Open questions to settle before coding

- QR code: server-drawn image in the desktop Settings UI, or is
  "read me these 6 digits" acceptable for a first version?
- Hotspot: app-triggered button, or documented manual command?
- Any interest in the "friendly name at pairing" nudge, or is the
  current free-text device name good enough as-is?
