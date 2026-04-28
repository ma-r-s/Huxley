# Project brief: `huxley-web` PWA

> **How to use this doc**: paste this entire file into your AI design tool (v0, Lovable, Claude, etc.) as the project brief. It defines what you're building, who it's for, what the technical contract is, and what's explicitly out of scope. It deliberately leaves design decisions (visual identity, layout, navigation patterns, animation, copy beyond functional strings) to the design conversation. The doc gives the AI tool grounding; you supply the design taste.
>
> **Companion doc**: [`huxley-web-ui-surfaces.md`](./huxley-web-ui-surfaces.md) is the enumerated UI-surface contract — every piece of state, event, skill tool, and config field the PWA can expose, with plausible UI shapes for each. Paste it alongside this brief when the design tool needs concrete slots to place widgets into. This brief describes _what_ and _why_; the surfaces doc describes _what's available to render_.
>
> Last updated: 2026-04-19 (rev 2 — rewritten after scope correction: the PWA is the Huxley user's interface to their own Huxley, not a family-coordination app). Lives in the `huxley` repo at `docs/research/huxley-web-brief.md`. Will move to the `huxley-web` repo as its README/docs once that repo exists.

---

## What you are building

A **Progressive Web App** called **Huxley**.

It's the consumer-facing client for a voice-agent platform also called Huxley. The platform runs as a Python server on the user's own computer — local machine, home server, small SBC like an OrangePi5, whatever they've got. This PWA is the **user's interface to their own Huxley**. Hold-to-talk, hear the response, see what's happening, configure the device, admin it.

You are not building the voice assistant itself. You are not building features for other people (the user's family, friends, etc.) to reach the user — that's what ordinary apps like Telegram or WhatsApp are for, integrated as skills on the server. You are building **the human-facing surface** for one person to use their Huxley.

Think **claude.ai** for a personal, local-first voice agent.

## Naming convention (please respect)

Huxley is the name of both the platform and the PWA — same brand, different surfaces. Claude works the same way: it's both the model and the consumer app at claude.ai. Context disambiguates.

- **Huxley** — the product. Both the platform (Python runtime, skill system, voice provider integration) and this PWA. Inside this PWA, the product name in the header / install prompt / icon is just **"Huxley"**.
- **Persona** — a YAML configuration that customizes Huxley for a specific user. Defines the voice, language, system prompt, behavioral constraints, and the set of enabled skills. A Huxley instance runs exactly one persona.
- **AbuelOS** — one _example_ persona that ships with the Huxley framework. Spanish-language, warm-toned, configured for an elderly user who benefits from large text and high contrast. Not a product name. It's mentioned in this brief to give the design tool a concrete example of what a persona looks like, but **the PWA must not assume the persona is AbuelOS**. Other personas will exist.
- **Skill** — a module the persona enables. Provides tools the voice agent can invoke (read an audiobook, set a timer, send a Telegram message, etc.). Skills can eventually contribute UI to the PWA via a structured event protocol (see "Skill-extensible UI" below).
- **User** — the human interacting with their Huxley through this PWA. Always exactly one person per PWA install, always exactly one PWA install per Huxley instance. **There is no multi-user, no family, no sharing.** If the user's friends want to reach them, that's Telegram / WhatsApp / email — not this PWA.

## Who uses this app

Exactly one person: **the Huxley user**. The human whose Huxley it is. That's the entire model.

This person might be:

- An elderly Spanish-speaking user running the AbuelOS persona — the voice agent is configured for their daily needs (audiobooks, news, reminders). They hold the PWA on a tablet next to them and press-and-hold to talk. Large text, high contrast, calm animation.
- A software developer running a developer persona — their voice agent is configured for journaling, quick notes, code-adjacent conversation. Denser UI works for them.
- A busy parent running a household-manager persona — shopping lists, reminders, kids' schedules.
- Any future Huxley user running any future persona.

**The PWA must adapt to the persona it's paired with** (language, possibly visual density) but remains fundamentally the same app. Persona-driven accessibility settings (text size, contrast, motion reduction) can live either in persona config or in PWA-local settings; prefer PWA-local for anything that's a personal preference of the user vs intrinsic to the persona.

## What the app does, prioritized

### Tier 0 — core voice interaction (must work for v1)

These are the reason the PWA exists. If any of these is broken, the app has failed.

1. **Hold-to-talk.**
   A single big affordance: press and hold (pointer / touch / keyboard spacebar) to speak; release to send. No other input modality matters at this tier. The device-side voice agent handles the conversation; the PWA is the microphone + speaker.
2. **Hear the response.**
   Audio comes back over the WebSocket as PCM chunks; play them through the device's speaker with no buffering gap the user notices.
3. **See what's happening.**
   A status indicator that distinguishes at minimum: idle / listening (user is holding-to-talk) / committing (sent, awaiting response) / responding (Huxley is speaking back) / content-playing (a skill is playing long-form audio like an audiobook or news). State transitions come from the server; the PWA renders them.
4. **See the conversation.**
   Live transcript of what the user said and what Huxley said, streamed as it arrives. Scrollable. No edit, no search (those are Tier 2).
5. **Interrupt whatever's happening.**
   The hold-to-talk gesture is also the universal interrupt: starting to talk while Huxley is speaking or while content is playing cancels the current output and begins a new user turn. The server handles the state transition; the PWA just sends the signal.

### Tier 1 — config (strongly wanted for real use)

6. **Connect to a Huxley instance.**
   On first run, prompt for the device URL (`ws://host:port` or `wss://...`). Persist it. Reconnect automatically when the connection drops. If the user runs multiple Huxley instances (rare but possible — e.g. one on their laptop, one on a home server), let them switch between them.
7. **Pick a persona.**
   The server exposes the list of available personas; the PWA lets the user choose. Switching personas triggers a server-side reconnect to the new persona's session.
8. **Set the voice provider's API key.**
   Huxley uses a voice provider (currently OpenAI Realtime). The user's API key is required. The PWA can proxy this to the server via a secure config endpoint, or display a command for the user to paste into their server's `.env`. The former is nicer; the latter is simpler.

### Tier 2 — admin / observability

9. **Recent conversations.**
   A list of past sessions: date, duration, a preview of the first few exchanges. Tap one → see the full transcript. Privacy-sensitive; keep local to the device.
10. **Device health.**
    Is the voice provider connected? How much has been spent this billing cycle (if the cost-tracker skill is loaded)? How much storage is being used by persona data (audiobooks, conversation history)? When was the last session?
11. **Logs.**
    Structured-event log from the server, filterable by namespace (skill name, `coord.*`, `focus.*`, etc.). This is the window into what the device is doing; useful when something feels broken.
12. **Restart skills / the whole server.**
    A button that tells the server to re-init its skills or fully restart. Useful after editing config.

### Tier 3 — skill-extensible UI (framework work not yet built)

Huxley skills will eventually be able to push structured events to the PWA and have the PWA render skill-specific surfaces. This is a planned framework primitive (`ClientEvent`) not yet shipped. When it lands, the PWA should support:

13. **Skill surfaces.**
    Each loaded skill can own a panel in the PWA. Examples of what this might look like:
    - An **audiobooks** skill might surface: currently playing book + position, a library list with covers, a tap-to-resume affordance.
    - A **timers** skill might surface: list of active timers with countdowns, a tap-to-cancel affordance.
    - A **news** skill might surface: today's headlines, tap to have Huxley read one aloud.
    - A **comms** skill (e.g. Telegram bridge) might surface: recent messages from the user's comms apps, integrated into the conversation flow.
      These are all skills' own UI, not the PWA's. The PWA provides widget primitives (list, card, button, transcript, audio controls) and renders whatever the skill declares it needs.

The Tier 3 work is out of scope for the first pass of the PWA. But **don't architect yourself into a corner**: design the main conversation surface with room for pluggable side panels.

## How the app talks to the device

The Huxley framework defines a wire protocol. The PWA speaks it. The protocol is documented in the `huxley` repo at `docs/protocol.md`; the relevant bits for this PWA:

### Primary conversation WebSocket

```
ws(s)://<device-host>:<port>/
```

One connection per PWA. On connect, the server sends a `hello` message with the protocol version and the current state; the PWA sends messages to drive the conversation and receives messages back.

**Client → server:**

- `{"type": "wake_word"}` — start a new voice session (triggers OpenAI session connect if not already connected).
- `{"type": "ptt_start"}` — user started holding the button.
- `{"type": "audio", "data": "<base64 PCM16 24kHz>"}` — one chunk of user mic audio while PTT is active.
- `{"type": "ptt_stop"}` — user released the button; server commits the audio and requests a response.
- `{"type": "reset"}` — dev-only: force-disconnect + restart the voice session.
- `{"type": "client_event", "event": "<name>", "data": {...}}` — pure telemetry. The server logs it under `client.<name>`; useful for observability. No behavioral effect.

**Server → client:**

- `{"type": "hello", "protocol": <version>}` — handshake, first message.
- `{"type": "state", "value": "IDLE" | "CONNECTING" | "CONVERSING"}` — device state machine.
- `{"type": "status", "message": "..."}` — human-readable status string (e.g. "Escuchando…", "Respondiendo…", "Conectado").
- `{"type": "audio", "data": "<base64 PCM16 24kHz>"}` — a chunk of response audio for the PWA to play.
- `{"type": "audio_clear"}` — drop any queued response audio immediately (fires on interrupt or seek).
- `{"type": "transcript", "role": "user" | "assistant", "text": "..."}` — one transcript line.
- `{"type": "model_speaking", "value": true|false}` — the assistant is / is not currently emitting audio.
- `{"type": "set_volume", "level": <0-100>}` — the server asks the client to change playback volume. Optional — the PWA can ignore if it doesn't manage volume.
- `{"type": "dev_event", "kind": "...", "payload": {...}}` — developer observability. Render in a debug panel if the PWA shows one.

### Audio capture / playback contract

- **Format**: PCM16 (signed 16-bit little-endian samples), mono, **24 kHz**. In both directions.
- **Capture**: use the **`AudioWorklet`** API. The browser's native `MediaRecorder` produces compressed WebM/Opus which the server doesn't decode. AudioWorklet gives raw `Float32Array` samples; downsample from the browser's native rate (typically 48 kHz) to 24 kHz, convert to Int16, base64-encode, and send as a `{"type": "audio", ...}` message. ~30–50 lines of JS in a worklet processor.
- **Playback**: base64-decode incoming `audio` frames, enqueue them to an `AudioContext` (either via a chain of `AudioBufferSourceNode` instances or via an `AudioWorkletNode`). Handle `audio_clear` by draining the queue instantly.
- **Latency**: keep the playback queue shallow (≤200ms) so interrupts feel immediate. The server fires `audio_clear` on interrupt; the PWA should respond within a single audio frame's worth of time.
- **Mic permission**: required. Request on first PTT attempt; if denied, surface a clear "habilita el micrófono" message.

### Comms note (calls, emergency, family)

**This PWA has no calling features. It has no emergency receive. It has no family integration.** If the user's voice agent needs to reach other people (to call family, send an emergency alert, share a voice note), that capability is implemented as a **skill on the server**, using a third-party comms platform (e.g. Telegram via `huxley-skill-telegram`). Family members interact with the user via _their_ regular apps — Telegram, WhatsApp, phone calls — not through this PWA.

The PWA talks to the Huxley user. Huxley talks to the world through its skills.

## Network & deployment

- **Default topology**: the device runs on the user's own machine or home LAN. The PWA connects directly via `ws://` or `wss://`. No cloud backend, no account system, no sync server.
- **Remote access**: if the user runs Huxley at home and wants to access it from their phone while out, they set up Tailscale (or similar mesh VPN) and point the PWA at the Tailscale name. The PWA doesn't care about the network path; it just uses whatever URL the user configures.
- **No backend of its own**: the PWA is a pure front-end. All state lives on the device; the PWA is a thin control surface over the WebSocket.
- **Optional**: if the user self-hosts with a public-reachable URL + TLS cert (via Tailscale Funnel, Cloudflare Tunnel, or a traditional reverse proxy), the PWA works over `wss://` exactly the same. But MVP should work fine over plain `ws://` to a Tailscale name.

## Constraints & non-goals

### Must work on

- iOS Safari (PWA install to home screen, full functionality from the standalone launch)
- Android Chrome (PWA install)
- Desktop browsers: Chrome, Safari, Firefox, Edge
- Keyboard support: spacebar as the push-to-talk trigger anywhere on the page (it already works on the dev client; the PWA inherits this)
- Touch: large primary button; hold-to-talk via `pointerdown`/`pointerup`; touch gestures are the primary interaction

### Should be

- **Calm.** Opens for a purpose, does the thing, puts down. Not busy, not loud, not notification-heavy.
- **Reliable.** Voice is the primary interface; if the PWA glitches during a hold-to-talk, the user thinks the device is broken.
- **Accessible.** The AbuelOS persona's user is blind; font size and contrast are non-cosmetic concerns. Screen-reader friendly. Motion can be reduced via OS prefers-reduced-motion.
- **Persona-adaptive.** Respect the persona's language for UI strings where possible (Spanish UI for the AbuelOS persona), fallback to the user's browser language otherwise. English / Spanish at minimum for v1.
- **Installable.** Proper PWA manifest, icon set, service worker for offline shell (the actual voice agent needs the server online, but the PWA shell should open without network).
- **Fast to first voice.** Open the app → hold the button → be talking within a second. No splash screens, no login flows at the cold path.

### Should NOT

- Be a general-purpose voice assistant. The intelligence lives on the server. The PWA is a dumb client.
- Try to do LLM calls, speech recognition, or TTS in the browser. All of that is server-side.
- Implement calling, messaging, family coordination, or anything that looks like inter-user communication. That's a _skill_ on the server, bridging to _external apps_. Not this PWA's problem.
- Require account creation, login, or a cloud backend. The PWA is paired to one Huxley instance; that's the whole auth model. If the server optionally uses a shared token (to prevent random Tailscale peers from using it), the PWA accepts the token as a config string.
- Try to be pretty at the cost of reliability. Prefer boring and working over polished and flaky.

## Brand voice / tone

- **Calm.** This is a voice app. The eyes aren't the point.
- **Personal.** The user is talking to _their_ Huxley. Copy should read as first-person to the user ("tu asistente", not "el asistente"), not third-person-marketing.
- **Persona-aware for copy.** For the AbuelOS persona: warm, Spanish-Colombian Spanish, forms like _"abuelo"_ only if the persona's user wants that. For other personas: match their tone.
- **Sparse.** Minimum chrome; big targets; clear hierarchy. A blind user's family member opens this app to make sure things are working; they shouldn't have to parse a dashboard.

## Tech stack — recommendations, not requirements

Pick the framework with your design tool. Suggested defaults:

- **Svelte / SvelteKit** or **React / Next.js** both work cleanly. The in-tree dev client `clients/pwa/` is Svelte, so there's precedent; not required.
- **Tailwind** for styling.
- **Workbox** for service-worker scaffolding.
- **AudioWorklet** for mic capture (no library hides this well; custom processor in ~50 lines).
- **IndexedDB** or **localStorage** for local persistence (device URL, API key, recent-transcript cache).
- **Capacitor** as the eventual native-wrap escape hatch _only if_ the PWA Web Push story proves insufficient for use cases that emerge later. (The app has no push requirements at v1 — all state is real-time-only via WebSocket — so Capacitor is not a day-one concern.)

## Reference: contract verifier

The Huxley repo includes `spikes/test_caller.py` (and will add more spike scripts over time) that exercise the wire protocol end-to-end from a Python client. Use them as executable contract references when you're unsure what bytes should be on the wire.

## Glossary

- **Huxley** — the platform and the PWA (context disambiguates).
- **Persona** — YAML config that customizes Huxley for a user. AbuelOS is one example.
- **Skill** — Python package providing tools the voice agent can invoke. Persona-agnostic; opts into persona constraints.
- **Tool** — a function a skill exposes that the LLM can dispatch during a conversation.
- **Side effect** — what happens after a tool dispatches. Long-form audio playback, for instance, is a side effect of a "play audiobook" tool.
- **Inject turn** — when a skill speaks proactively (e.g. a timer going off) without the user prompting.
- **Tailnet** — the private mesh VPN Tailscale provides; the typical remote-access path for a self-hosted Huxley.

## What success looks like for v1

A user installs this PWA on their phone / tablet / laptop, points it at their Huxley device, holds the button, and is talking to their voice agent within seconds. They see what they said, they hear what it said back, they close the app. They open it again later and it reconnects seamlessly. When they want to change persona, tweak config, or check on something, they tap into settings.

Everything else (skill UIs, admin panels, multi-device, install onboarding polish) is layered on top of that core loop. Get the core loop right first.
