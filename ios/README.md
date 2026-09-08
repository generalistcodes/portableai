# PortableAI iOS app

A minimal SwiftUI client for the PortableAI server: pair once with a PIN,
pick a persona, chat. This is source files only -- there's no `.xcodeproj`
here (hand-building one reliably without Xcode itself is error-prone), so
set it up as a fresh Xcode project and drop these files in.

**I couldn't compile or run any of this** -- there's no Swift toolchain in
the sandbox that wrote it. Treat it as a solid first draft, not verified
working code. Xcode's own compiler is the first real test it needs to pass.

## Setup

1. In Xcode: **File → New → Project → iOS → App**. Name it `PortableAI`,
   interface: SwiftUI, language: Swift. Minimum deployment target:
   **iOS 17** (this uses `ContentUnavailableView` and the two-parameter
   `onChange` modifier, both iOS 17+).
2. Delete the default `ContentView.swift` and the generated
   `PortableAIApp.swift` Xcode creates.
3. Drag this folder's `Models/` and `Views/` folders, plus
   `PortableAIApp.swift`, into the Xcode project navigator (check "Copy
   items if needed").
4. Build (⌘B) and fix whatever the compiler flags -- expect at least
   minor issues on a first pass from code that's never been compiled.

## Local network permission

iOS requires an `NSLocalNetworkUsageDescription` entry in Info.plist for
any app that talks to devices on the local network (which this does --
your laptop, over WiFi). In the target's **Info** tab, add:

- Key: `Privacy - Local Network Usage Description`
- Value: something like "PortableAI connects to your own PortableAI
  server on your local network."

Without this, network requests to the server will silently fail or the
system will prompt unexpectedly.

## Testing against the real server

1. On your laptop: `python run.py` (from the repo root), then open
   Settings → "Phone pairing" to see the LAN address and PIN.
2. Make sure your iPhone is on the **same WiFi network** as the laptop
   (or connected to its hotspot, per the original Portable Ark plan).
3. Run the app on a real device (not the Simulator -- the Simulator
   shares the Mac's network stack in confusing ways for LAN testing;
   a real iPhone is the honest test here) via Xcode, enter the server
   address and PIN, pair.
4. Confirm the persona list loads, and that chatting actually round-trips
   through Ollama on the laptop.

## Pinning conversations for offline access

The tap-a-pin-icon button in ChatView calls the server's
`/api/conversations/<id>/export` endpoint and saves the raw JSON to the
app's local Documents directory via `PinnedChatsStore`. This is the
actual reason a native app matters here, not just a nicer web view: once
pinned, that conversation is readable with **zero network connection** --
away from the laptop, off the WiFi, doesn't matter.

Important limitation to know about: **pinning is a snapshot, not a live
sync.** If new messages get added to that conversation later (from the
web UI, or the same phone continuing the chat), the pinned copy does NOT
update automatically -- you'd need to pin it again to refresh. This is a
deliberate simplification for now; building real sync would mean
reconciling a local copy against a server that might not even be
reachable when you're looking at the pinned copy, which is a much bigger
problem to solve correctly.

## What's not built yet

- No chat history / conversation **list** on the phone beyond pinned
  ones (the server already supports it via `/api/conversations`; the app
  only ever starts fresh chats or shows a pinned snapshot for now)
- No screen to browse/manage pinned conversations yet -- `PinnedChatsStore.listPinnedIds()`
  exists but nothing calls it in a view
- No model selector or Settings screen on the phone side
- No QR-code pairing (manual IP + PIN entry only)
- No handling for the laptop's IP changing (e.g. switching WiFi networks
  mid-session) -- re-pairing would currently be required
- Not using Bonjour/mDNS for auto-discovery, so the address has to be
  typed in manually each first pairing
