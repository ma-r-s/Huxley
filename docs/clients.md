# Clients

Huxley is headless. The framework runs as a server and exposes a documented protocol; **clients** are separate codebases that speak that protocol. The framework doesn't ship a UI. Different clients serve different audiences.

This doc defines the client architecture, names the clients that exist or are planned, and documents the boundary between them and the framework.

## The Claude analogy

Anthropic ships **Claude** the model (an API), and several clients on top of it: **claude.ai** (the consumer web app), **Claude Code** (the CLI), **Claude apps** (mobile, desktop). Same brand, distinct codebases, distinct repos, distinct audiences, distinct release cycles. The model defines the protocol; each client speaks it.

Huxley follows the same shape:

- **Huxley** = the framework (this repo). Runtime, persona loader, skill registry, focus management, audio plumbing. Defines the protocol via [`docs/protocol.md`](./protocol.md).
- **`huxley-web`** (separate repo, planned) = the consumer-facing PWA. The **Huxley user** installs it on their phone / tablet / laptop to talk to their own Huxley instance, see transcripts, and admin the device. One user, one Huxley, one PWA install.
- **`web/` in this repo** = the developer workbench. Quick PTT button + status display, used during framework development. Not the production client; not end-user-facing.
- **`huxley-firmware`** (separate repo, future) = the ESP32 hardware client. A physical button + speaker + mic for users who want a tactile device in the room with them instead of (or alongside) the PWA.

The framework + the clients share branding ("Huxley") because that's the platform name. Context tells you which is meant: a developer reading `docs/` thinks "Huxley = the framework"; a user tapping the install icon thinks "Huxley = the app on my phone." Same name, different surfaces — exactly like Claude.

## Naming convention (canonical)

To keep "what is Huxley" unambiguous in commits, conversations, and design docs:

| Term                 | Meaning                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Huxley**           | The platform. Refers to either the framework or any of its clients depending on context. When a user installs the PWA, it's branded "Huxley." When a developer runs `uv run huxley`, that's also Huxley.                                                                                                                                                                      |
| **AbuelOS**          | One example **persona** that ships with Huxley. A YAML file under `personas/abuelos/` plus its data dir. Spanish, elderly, warm-toned, enables audiobooks + radio + news + timers skills. **Not a product name.** Other personas (BasicOS) ship in this repo as counter-examples; future personas may ship as separate packages. Any user of Huxley runs exactly one persona. |
| **`huxley`**         | The Python runtime package (PyPI). What `uv run huxley` invokes.                                                                                                                                                                                                                                                                                                              |
| **`huxley-sdk`**     | The skill-author surface (PyPI). What a skill package depends on.                                                                                                                                                                                                                                                                                                             |
| **`huxley-skill-*`** | Skill packages (PyPI). One per discrete capability; loaded via the `huxley.skills` entry point.                                                                                                                                                                                                                                                                               |
| **`huxley-web`**     | The PWA repo + npm package. The Huxley user's client for interacting with their own Huxley instance. **Lives in its own repo**, not under this one.                                                                                                                                                                                                                           |
| **`web/`**           | The developer workbench in this repo. Useful while building / debugging the framework.                                                                                                                                                                                                                                                                                        |
| **persona**          | A YAML config + assets that determines who Huxley is for a given user. Lives under `personas/<name>/`.                                                                                                                                                                                                                                                                        |
| **skill**            | A Python package providing one or more tools the LLM can call. Persona-agnostic; opts into persona behavioral constraints. Skills can bridge Huxley to third-party services (Telegram, Twilio, etc.) — that's how inter-user communication happens, not via Huxley clients.                                                                                                   |
| **user**             | The human interacting with a Huxley instance through a client. Always exactly one human per Huxley instance. If a user wants other people to reach them, that's handled by skills bridging to external comms apps (Telegram, WhatsApp, phone), not by Huxley-to-Huxley communication.                                                                                         |

When in doubt: **the framework is Huxley. The first persona is AbuelOS. The PWA is also Huxley.** Distinct things, common brand, no ambiguity for users (each surface is what it is).

## Why clients are separate repos

Different clients have different language ecosystems, different deployment cycles, and different audiences. Forcing them into one repo would conflate concerns and tempt internal-API shortcuts that violate the protocol contract.

| Concern         | Framework (`huxley`)                      | PWA (`huxley-web`)                   | Firmware (`huxley-firmware`) |
| --------------- | ----------------------------------------- | ------------------------------------ | ---------------------------- |
| Language        | Python 3.13                               | TypeScript / Svelte (Mario's choice) | C/Rust on ESP-IDF            |
| Build system    | `uv`                                      | `bun` / `vite`                       | `idf.py`                     |
| Dist channel    | PyPI (eventually)                         | npm + PWA install + maybe TestFlight | OTA + flashed binaries       |
| Audience        | Skill / persona authors, system operators | The Huxley user                      | The Huxley user (tactile)    |
| Release cadence | Semver, tied to skill API                 | Free to ship UX changes daily        | Ties to firmware OTA tooling |
| Test surface    | Python pytest                             | Component tests + browser smoke      | Hardware-in-the-loop         |

When all three are separate repos, the **only** API between them is the documented Huxley protocol. That's a feature.

## The protocol is the contract

[`docs/protocol.md`](./protocol.md) is the cross-repo spec. Anything a client speaks to the framework — and anything the framework sends back — is defined there. Both sides depend on it; neither depends on the other.

Today the protocol covers:

- **Primary WebSocket** at `ws://<host>:<port>/` — the user's conversation channel. The dev `web/` client speaks it today; `huxley-web` and `huxley-firmware` will speak the same contract.
  - Client → server: PTT events, audio frames (PCM16 mono @ 24 kHz), wake word, reset, `client_event` (pure telemetry).
  - Server → client: `hello` + protocol version, state machine transitions, status strings, transcript lines, audio chunks, `audio_clear`, `model_speaking` indicator, volume hints, `dev_event`.

Planned additions:

- **`ClientEvent`** / `server_event` wire protocol (T1.4 Stage 4) for typed bidirectional app↔skill messaging. Lets skills surface their own UI widgets in the PWA (see `huxley-web-brief.md` Tier 3) and receive structured input from the client beyond raw audio.
- **Capability handshake** so the framework can adapt behavior based on the client (a PWA can render full transcripts; an ESP32 can't).

**Inter-user communication is not a framework protocol concern.** If a Huxley user's voice agent needs to reach other people (call a family member, send a voice message, fire an emergency alert), that happens inside a skill that bridges to a third-party service (Telegram, Twilio, WhatsApp, etc.). The skill is responsible for its own transport; the framework just exposes `InputClaim` + `inject_turn` + `background_task` primitives that the skill uses. See `huxley-skill-telegram` for the first concrete example.

A client is in good standing if it speaks the documented protocol correctly. It doesn't need to know about Python, focus management, or skill internals.

## What today's `web/` is

The dev workbench. One PTT button, status display, persona switcher, transcript log. Useful while building the framework or debugging a skill. Today it doubles as the de facto user-facing client while `huxley-web` is under construction.

It will keep existing as long as it's useful for development. Once `huxley-web` can do everything `web/` does and an ESP32 firmware exists for users who want hardware, `web/` can be deprecated. Until then, all three surfaces have a job.

## What `huxley-web` is

The PWA the Huxley user installs on their phone / tablet / laptop to interact with their own Huxley instance. See [`docs/research/huxley-web-brief.md`](./research/huxley-web-brief.md) for the full project brief that should be fed into a design tool to start the work.

Quick framing:

- **Audience**: the Huxley user. Exactly one human per install; no multi-user, no sharing. If the user wants their friends or family to reach them, that happens through skills bridging to third-party comms apps (Telegram, Twilio) — not through this PWA.
- **Core loop**: hold-to-talk, hear response, see transcript, see device status. Claude.ai for a personal, local-first voice agent.
- **Secondary surfaces**: persona / skill configuration, device health, recent conversation history, and (Tier 3, when `ClientEvent` ships) skill-contributed UI panels.
- **Distribution**: PWA install on iOS / Android / desktop browsers. Capacitor wrap is an option down the road if native-shell features emerge as needed.
- **Network**: direct WebSocket to the user's own Huxley instance. Remote access via the user's Tailscale / VPN / reverse proxy of choice. No cloud backend of its own.
- **Brand**: just "Huxley". Same as the platform.

## What `huxley-firmware` will be

The ESP32 client for users who want a tactile device in the room — a physical hold-to-talk button, speaker, mic, no screen. Streams PCM over LAN to the framework. Same WebSocket protocol as `web/` and `huxley-web`. Wakeword detection optional (push button is the primary input). Mainly motivated today by the AbuelOS-persona user's accessibility needs (a blind user benefits more from a tactile button than a tablet), but useful for any user who wants always-on voice without keeping an app open.

Out of scope for now; filed for after `huxley-web` is in real use.

## Cross-repo discipline

When `huxley-web` exists as its own repo:

- Both repos reference [`docs/protocol.md`](./protocol.md) as the contract. PRs that change the protocol must update the doc and bump a protocol version.
- Capability mismatches (PWA expects feature X, framework version doesn't have it) are negotiated via the planned capability handshake — not by guessing.
- Major framework releases note any client-impacting protocol changes in the changelog. PWA pins a minimum framework version.
- Bug reports go to the repo that owns the bug. "The PWA freezes when I hold PTT" → `huxley-web`. "The server doesn't send `audio_done` when expected" → `huxley`.
