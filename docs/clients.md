# Clients

Huxley is headless. The framework runs as a server and exposes a documented protocol; **clients** are separate codebases that speak that protocol. The framework doesn't ship a UI. Different clients serve different audiences.

This doc defines the client architecture, names the clients that exist or are planned, and documents the boundary between them and the framework.

## The Claude analogy

Anthropic ships **Claude** the model (an API), and several clients on top of it: **claude.ai** (the consumer web app), **Claude Code** (the CLI), **Claude apps** (mobile, desktop). Same brand, distinct codebases, distinct repos, distinct audiences, distinct release cycles. The model defines the protocol; each client speaks it.

Huxley follows the same shape:

- **Huxley** = the framework (this repo). Runtime, persona loader, skill registry, focus management, audio plumbing. Defines the protocol via [`docs/protocol.md`](./protocol.md).
- **`huxley-web`** (separate repo, planned) = the consumer-facing PWA. Family members install it on phones / tablets / laptops to call grandpa, receive his calls, manage the device.
- **`web/` in this repo** = the developer workbench. Quick PTT button + status display, used during framework development. Not the production client; not user-facing.
- **`huxley-firmware`** (separate repo, future) = the ESP32 hardware client. A physical button + speaker + mic in grandpa's living room.

The framework + the clients share branding ("Huxley") because that's the platform name. Context tells you which is meant: a developer reading `docs/` thinks "Huxley = the framework"; a grandpa's relative tapping the install icon thinks "Huxley = the app on my phone." Same name, different surfaces — exactly like Claude.

## Naming convention (canonical)

To keep "what is Huxley" unambiguous in commits, conversations, and design docs:

| Term                 | Meaning                                                                                                                                                                                                                                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Huxley**           | The platform. Refers to either the framework or any of its clients depending on context. When a family member installs the PWA, it's branded "Huxley." When a developer runs `uv run huxley`, that's also Huxley.                                                                                             |
| **AbuelOS**          | The first **persona** running on Huxley. A YAML file under `personas/abuelos/` plus its data dir. Spanish, elderly, blind, audiobooks + radio + news + timers + calls. **Not a product name.** Other personas (BasicOS) ship in this repo as counter-examples; future personas may ship as separate packages. |
| **`huxley`**         | The Python runtime package (PyPI). What `uv run huxley` invokes.                                                                                                                                                                                                                                              |
| **`huxley-sdk`**     | The skill-author surface (PyPI). What a skill package depends on.                                                                                                                                                                                                                                             |
| **`huxley-skill-*`** | Skill packages (PyPI). One per discrete capability; loaded via the `huxley.skills` entry point.                                                                                                                                                                                                               |
| **`huxley-web`**     | The PWA repo + npm package. Mario's family-side client. **Lives in its own repo**, not under this one.                                                                                                                                                                                                        |
| **`web/`**           | The developer workbench in this repo. Useful while building / debugging the framework.                                                                                                                                                                                                                        |
| **persona**          | A YAML config + assets that determines who Huxley is for a given user. Lives under `personas/<name>/`.                                                                                                                                                                                                        |
| **skill**            | A Python package providing one or more tools the LLM can call. Persona-agnostic; opts into persona behavioral constraints.                                                                                                                                                                                    |

When in doubt: **the framework is Huxley. The first persona is AbuelOS. The PWA is also Huxley.** Distinct things, common brand, no ambiguity for users (each surface is what it is).

## Why clients are separate repos

Different clients have different language ecosystems, different deployment cycles, and different audiences. Forcing them into one repo would conflate concerns and tempt internal-API shortcuts that violate the protocol contract.

| Concern         | Framework (`huxley`)                      | PWA (`huxley-web`)                   | Firmware (`huxley-firmware`) |
| --------------- | ----------------------------------------- | ------------------------------------ | ---------------------------- |
| Language        | Python 3.13                               | TypeScript / Svelte (Mario's choice) | C/Rust on ESP-IDF            |
| Build system    | `uv`                                      | `bun` / `vite`                       | `idf.py`                     |
| Dist channel    | PyPI (eventually)                         | npm + PWA install + maybe TestFlight | OTA + flashed binaries       |
| Audience        | Skill / persona authors, system operators | End users (family)                   | End users (grandpa's room)   |
| Release cadence | Semver, tied to skill API                 | Free to ship UX changes daily        | Ties to firmware OTA tooling |
| Test surface    | Python pytest                             | Component tests + browser smoke      | Hardware-in-the-loop         |

When all three are separate repos, the **only** API between them is the documented Huxley protocol. That's a feature.

## The protocol is the contract

[`docs/protocol.md`](./protocol.md) is the cross-repo spec. Anything a client speaks to the framework — and anything the framework sends back — is defined there. Both sides depend on it; neither depends on the other.

Today the protocol covers:

- **Default WebSocket** at `ws://<host>:<port>/` for the primary device user (today: the developer in `web/`; tomorrow: ESP32 firmware; potentially the PWA's grandpa-side mode).
  - Client → server: PTT events, audio frames (PCM16 mono @ 24 kHz), wake word, reset.
  - Server → client: state, status, transcript, audio chunks, model_speaking indicator, dev events.
- **HTTP `GET /call/ring`** for inbound-call triggering. Header-auth via `X-Shared-Secret`. Returns 200/401/409/503.
- **Caller WebSocket** at `/call?secret=<value>` for the family-side audio stream during a call. Same PCM format.

Planned additions:

- **Outbound-push endpoint** for grandpa-initiated calls / emergencies (the inverse of `/call/ring`). PWA registers a push receiver URL; framework POSTs there on emergency.
- **`ClientEvent`** wire protocol (T1.4 Stage 4) for typed app↔skill messaging beyond the call substrate.
- **Capability handshake** so the framework can adapt behavior based on the client (a PWA can render full transcripts; an ESP32 can't).

A client is in good standing if it speaks the documented protocol correctly. It doesn't need to know about Python, focus management, or skill internals.

## What today's `web/` is

The dev workbench. One PTT button, status display, persona switcher, transcript log. Useful while building the framework or debugging a skill. Also serves as today's grandpa-side client (browser on a laptop next to him, spacebar held to talk).

It will keep existing as long as it's useful for development. Once `huxley-web` can do everything `web/` does (including grandpa-side PTT mode) AND grandpa has the ESP32, `web/` can be deprecated. Until then, both have a job.

## What `huxley-web` is

The consumer PWA Mario's family installs. See [`docs/research/huxley-web-brief.md`](./research/huxley-web-brief.md) for the project brief that should be fed into a design tool to start the work.

Quick framing:

- **Audience**: the family (Mario the admin, mom + brother + niece as casual users).
- **Modes** (one app, several roles):
  - Call grandpa
  - Receive grandpa's call (regular or emergency)
  - View device status, recent transcripts, set reminders for him
  - Configure the device (admin-only: shared secret, persona settings)
- **Distribution**: PWA install on iOS / Android / desktop browsers. Capacitor wrap added later if web push proves unreliable on locked iOS.
- **Network**: works over Tailscale today; cloud relay later if scope grows.
- **Brand**: just "Huxley". Same as the platform.

## What `huxley-firmware` will be

The ESP32 client in grandpa's living room. Tactile button, speaker, mic, no screen. Streams PCM over LAN to the OrangePi5 (or whatever's running the framework). Same WebSocket protocol as today's `web/`. Wakeword detection optional (push button is the primary input).

Out of scope for now; filed for after the PWA is in real use.

## Cross-repo discipline

When `huxley-web` exists as its own repo:

- Both repos reference [`docs/protocol.md`](./protocol.md) as the contract. PRs that change the protocol must update the doc and bump a protocol version.
- Capability mismatches (PWA expects feature X, framework version doesn't have it) are negotiated via the planned capability handshake — not by guessing.
- Major framework releases note any client-impacting protocol changes in the changelog. PWA pins a minimum framework version.
- Bug reports go to the repo that owns the bug. "The PWA crashes when I press call" → `huxley-web`. "The PWA gets `503 calls disabled` even when calls is loaded" → `huxley`.
