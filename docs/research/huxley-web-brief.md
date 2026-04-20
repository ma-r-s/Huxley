# Project brief: `huxley-web` PWA

> **How to use this doc**: paste this entire file into your AI design tool (v0, Lovable, Claude, etc.) as the project brief. It defines what you're building, who it's for, what the technical contract is, and what's out of scope. It deliberately leaves design decisions (visual identity, layout, navigation patterns, animation, copy beyond functional strings) to the design conversation. The doc gives the AI tool grounding; you supply the design taste.
>
> Last updated: 2026-04-19. Lives in the `huxley` repo at `docs/research/huxley-web-brief.md`. Will move to the `huxley-web` repo as its README/docs once that repo exists.

---

## What you are building

A **Progressive Web App** called **Huxley**.

It's the consumer-facing client for a voice-agent platform also called Huxley. The platform's first persona is **AbuelOS** — a Spanish-language voice assistant for an elderly blind user (in this case, the project owner's grandfather, who lives in Villavicencio, Colombia). The platform runs as a Python server on a small computer at grandpa's home; family members use this PWA on their phones, tablets, and laptops to interact with that device.

You are not building the voice assistant itself. You are not building grandpa's interface (he uses a separate browser client today, eventually a physical button-and-speaker device). You are building **the family's window into grandpa's world** — the app they open to call him, the app that rings when he needs help, the app the owner uses to keep the device running.

## Naming convention (please respect)

- **Huxley** — the product. Both the platform / framework AND this PWA. Same name, different surfaces. Just like "Claude" refers to both Anthropic's model and the claude.ai consumer app: context disambiguates. Inside this PWA, the product name in the header / install prompt / icon is just **"Huxley"**.
- **AbuelOS** — the _persona_ configuration of Huxley running at grandpa's home. Not a product name. Should not appear in user-facing UI of this PWA (the user calls grandpa, not "AbuelOS"; the device's persona name is internal config).
- **Grandpa** — the end user of the device. Use Spanish-affectionate forms in the UI when referring to him in copy: _"abuelo"_, the _owner_'s contact name, etc. Don't hard-code "grandpa" — make this configurable.
- The PWA never claims to BE the voice assistant. It interacts with one. UI copy should reflect that: "Llamando a abuelo" not "Hablando con AbuelOS".

## Who uses this app

One PWA, multiple roles. Same install adapts to who's signed in.

### Owner / admin (1 user — Mario today)

- The person who set up the device at grandpa's home.
- Has the most-trusted access: device config, shared secret rotation, persona settings, debug panels, ability to see / cancel active calls from other family members.
- Most frequent caller: opens the app daily to chat with grandpa.
- Also a regular caller and emergency receiver — admin functions are added on top, not in place of the basic experience.

### Family caller (3–10 users — mom, brother, niece, family friends)

- Each installs the same PWA on their device.
- Has their own identity inside the app: name, avatar, push notification token.
- Can: call grandpa, receive his calls, see his status (idle / on a call / playing audiobook), see who else in the family is currently talking to him.
- Cannot: edit device config, rotate secrets, see other people's call transcripts.
- Mostly invoked: "call grandpa" + "answer when grandpa calls."

### Emergency receiver (everyone in the family)

- Not a separate role — a _mode_ the app enters when grandpa says _"ayuda"_, _"tuve un accidente"_, _"me caí"_, or similar emergency intent.
- All family members' phones ring with high-priority push: full-screen takeover, loud sound, can't be silently dismissed.
- First to answer takes the call. The others see "Mom is talking to abuelo now" and can join, listen, or close.
- This is the most important UX of the entire app. **Design every other surface assuming this surface exists and pre-empts it.**

## What the app does (use cases, prioritized)

### Tier 0 — must work for v1, never broken

These are the use cases the app exists for. If any of these is unreliable, the app has failed.

1. **Receive an emergency call from grandpa.**
   The device pushes an alert; the PWA wakes (even from locked screen / app closed); rings loudly with a distinctive sound; shows a full-screen "Abuelo necesita ayuda" UI with a single big "Contestar" button. One tap → audio connection opens, family member is talking to him within ~2 seconds. Other family members see "Mom contestó" and can drop the alert.
2. **Call grandpa.**
   Tap a contact-card-sized "Llamar a abuelo" affordance. App rings the device (HTTP); the device announces the call to grandpa (auto-pickup with 3-second voice countdown — "Llamada de Mario, contestando en tres, dos, uno..."); the app opens an audio session and the family member is talking. Hangup ends the call.
3. **See device status at a glance.**
   On open: app shows whether the device is online, what state it's in (idle / listening / on-call-with-Mom / playing-audiobook), and basic health (last-seen timestamp, connection status to OpenAI). If the device is offline or unhealthy, this is the first thing visible — not buried.

### Tier 1 — strongly wanted for early use

4. **Receive a non-emergency call from grandpa.**
   Same flow as emergency, but with a less aggressive ring (normal notification, not full-screen takeover) and "Llamada de abuelo" branding. Grandpa initiated the call but it's social, not panic. The app's emergency-vs-normal distinction comes from the push payload's priority field.
5. **Per-user identity inside the app.**
   On first install, the app asks: "¿Cómo te llamas?" + optional avatar. This name flows into:
   - The grandpa-side announcement: "Llamada de Mario..." vs "Llamada de Mom..."
   - The presence indicator: "Mom is talking to abuelo right now"
   - Push attribution if a family member needs to be reached by another
6. **Configure the device (admin only).**
   Owner can: paste / regenerate the shared secret, set the device URL (e.g. `huxley-pi.tail-scale.ts.net:8765`), see the persona name + voice + language, view the list of loaded skills.
7. **Spanish UI by default.**
   The persona's user is Spanish-speaking; the family is Spanish-speaking. UI strings are Spanish. English add-on is a Phase 2 nice-to-have.

### Tier 2 — Phase 2 (after the family has been using v1 for a few weeks)

8. **Recent activity feed.**
   Last N conversations with grandpa: who called, when, transcript optional. Helps the family stay aware of how he's doing without grilling him.
9. **Set a reminder for him remotely.**
   "Recuerda a abuelo a las 8pm que tome la pastilla." App sends to the device's `set_timer` tool indirectly via a remote-instruction endpoint. Future framework primitive.
10. **Schedule a callback.**
    "Tell abuelo to call me at 3pm." Device's calls skill sets a reminder that triggers an outbound ring at the scheduled time.
11. **Multi-device support.**
    The app can manage / call multiple Huxley devices (e.g. another grandparent in another home). Each device gets its own card on the home screen.

### Tier 3 — distant dream

12. **Family group calls.** Mom + Mario + grandpa simultaneously.
13. **Cross-home calls.** Two Huxley devices in two different homes, talking to each other.
14. **Smart routing.** Mario's busy → call mom automatically.

## How the app talks to the device

The Huxley framework defines a wire protocol. The PWA speaks it. The protocol is documented in the `huxley` repo's `docs/protocol.md`; the relevant bits for this PWA:

### To ring grandpa (HTTP, single shot)

```
GET http(s)://<device-host>:8765/call/ring?from=<your-name>
Headers:
  X-Shared-Secret: <secret>

Responses:
  200 ringing\n           — accepted, grandpa-side announcement starting
  401 bad secret\n        — auth failed
  409 busy\n              — grandpa is already on a call (single-call-at-a-time)
  503 calls disabled\n    — device misconfigured (no secret on the server side)
```

### To carry the call's audio (WebSocket)

```
ws://<device-host>:8765/call?secret=<secret>

Send:    binary frames, PCM16 mono 24 kHz (your mic input → grandpa's speaker)
Receive: binary frames, PCM16 mono 24 kHz (grandpa's mic → your speaker)
Close:   normal close from either side ends the call
```

### Audio capture / playback contract

- **Format**: PCM16 (signed 16-bit little-endian samples), mono, **24 kHz**.
- **Capture**: use the **`AudioWorklet`** API (NOT `MediaRecorder`). `MediaRecorder` produces compressed WebM/Opus blobs that the device-side server doesn't decode. AudioWorklet gives you raw `Float32Array` samples; downsample from the browser's native rate (usually 48 kHz) to 24 kHz, convert to Int16, send as binary WS frames. ~30–50 lines of JS in a worklet processor.
- **Playback**: queue incoming binary frames into an `AudioBufferSourceNode` chain, or use a streaming approach via `AudioWorkletNode`. Either works; latency is the consideration.
- **Ring frame size**: ~50 ms at 24 kHz = 1200 samples = 2400 bytes per frame. Browsers typically deliver mic chunks at ~10 ms granularity; you can batch or stream as you prefer.
- **Mic permission**: required. Request on first call attempt. If denied, show a clear "abre los permisos del micrófono" message — without mic, the app does not work.

### To receive grandpa's calls (TODO — not built yet on the device side)

When grandpa initiates a call (regular or emergency), the device will POST to a family-side push endpoint that this PWA registers. The exact shape is TBD on the framework side (filed as field-finding F1 / planned `huxley-skill-panic`). The PWA should be designed assuming:

- Each family member's PWA registers a **push endpoint** with the device on first install (web-push subscription URL + key).
- When grandpa fires the panic intent, the device POSTs an alert payload to _all_ registered endpoints with a priority field (`emergency` | `regular`).
- The PWA's service worker receives the push, shows a notification, and either auto-opens the app to the answer screen (Android) or relies on the user tapping the notification (iOS).
- Once tapped, the app opens a new caller WebSocket to `/call?secret=...` and the audio flows the same as outbound calls.

**Design implication**: even if the receive-side framework code doesn't exist when v1 ships, the PWA's UI architecture must accommodate "an alert just arrived, take over the screen, ring loudly." Don't paint yourself into a corner with a navigation pattern that can't pre-empt itself.

## Network & deployment

- **Today**: the device is at the project owner's grandfather's home, running on a laptop (eventually an OrangePi5). Family devices reach it via **Tailscale** mesh VPN — each phone / laptop / iPad has the Tailscale app installed and joined to the same tailnet. The device's URL is its tailscale name (e.g. `huxley-pi.tail-scale.ts.net:8765`).
- **No public DNS / TLS cert assumed for MVP**. Tailscale handles the encryption + name resolution. The PWA must work over plain `ws://` and `http://` to a Tailscale name.
- **Future**: a cloud relay service for non-Tailscale callers. Out of scope for v1.

This means: **the PWA never talks to a backend of its own.** All state lives on the device. The PWA is a thin client over the device's WebSocket / HTTP. No supabase, no firebase, no own database, no own backend. (Push notification SUBSCRIPTION endpoints are an exception — those need a global push service like web-push / FCM / APNs. But the data plane is purely device-to-PWA.)

## Constraints & non-goals

### Must work on:

- iOS Safari (PWA install to home screen)
- Android Chrome (PWA install)
- Desktop browsers: Chrome, Safari, Firefox, Edge
- Locked-screen push notifications: full reliability on Android via FCM; best-effort on iOS via Web Push (may need Capacitor wrap later for VoIP-quality push — design for the upgrade)

### Should be:

- **Calm.** This is a phone app, not a dashboard. Quiet, focused, opens for one purpose at a time. Closer to FaceTime / Signal than Slack.
- **Reliable.** A blind elderly user is on the other end. The family-side app failing to ring an emergency push is the worst possible outcome.
- **Spanish-first.** Strings, copy, voice, accessibility labels. English is a Phase 2 add.
- **Installable.** PWA with proper manifest, icon set, service worker. Add-to-home-screen on iOS + Android.

### Should NOT:

- Be a CRM. Tight scope: this device, this family, this conversation. No contact management, no general-purpose messaging.
- Run AI itself. The PWA never calls an LLM. The voice intelligence lives entirely on the device.
- Try to do skill authoring or persona editing through the UI. Those are developer-side concerns; admin functions in the PWA are limited to runtime config (secrets, URLs, on/off toggles).
- Require a custom backend. Stateless PWA + device-as-backend.

## Brand voice / tone

- **Spanish-Colombian Spanish**, warm but not folksy. _"Abuelo"_ not _"abuelito"_.
- Family-first language. The user is calling family, not a service.
- Calm. Even the emergency UI should be reassuring, not alarmist: _"Abuelo necesita ayuda — contesta"_, not _"⚠️ EMERGENCY ⚠️"_.
- Spare. A blind grandpa's family member is opening this app while distracted, sometimes panicked. Visual minimalism, big tap targets, clear hierarchy.

## Tech stack — recommendations, not requirements

You can pick the framework with your design tool. Suggested defaults:

- **Svelte / SvelteKit** or **React / Next.js** — both give you a clean PWA story. Svelte feels more aligned with the existing dev `web/` client (which is Svelte) but isn't required.
- **Tailwind** for styling.
- **Web Push API** for browser push subscriptions (with VAPID).
- **Workbox** for service worker scaffolding.
- **AudioWorklet** for mic capture (custom processor — there's no library that hides this well).
- For Tier 2 features (transcripts, activity feed): keep state local in IndexedDB; sync from device on demand.
- **Capacitor** as the eventual native-wrap escape hatch if/when iOS push reliability becomes a blocker.

## Reference: how to test calling without the PWA

The Huxley repo includes a Python smoke client at `spikes/test_caller.py` that opens `/call/ring` + `WS /call` and streams a sine tone. It mirrors what the PWA needs to do at the protocol level, in ~200 lines of Python. Use it as a contract reference when implementing the audio side; if the PWA can do what `test_caller.py` does, the audio path is correct.

## Out of scope (will be built separately on the device side)

These features the PWA might want but the device doesn't yet expose:

- **Outbound emergency push** (grandpa initiates → all family devices ring). Filed as F1 in the device's triage; design the PWA's _receive_ UX assuming this will arrive within weeks.
- **Per-caller secrets / identity** at the protocol level. Today there's a single shared secret; future will support per-family-member tokens. The PWA can pre-build the identity model; adapt the wire protocol when the device side ships it.
- **Recent activity / transcripts API.** Not exposed yet; planned for Phase 2.
- **Remote skill triggering** (e.g. "set a timer for grandpa from the app"). Planned for Phase 2.

## Glossary

- **Persona** — a YAML config that customizes Huxley for a specific user. AbuelOS is the first.
- **Skill** — a Python package providing tools the LLM can call (audiobooks, news, timers, calls, etc.).
- **Tool** — a function the LLM can dispatch from a conversation. Each skill exposes one or more.
- **Side effect** — what happens after a tool dispatches. The calls skill returns an `InputClaim` side effect that latches grandpa's mic to the family-side connection.
- **Inject turn** — when a skill speaks proactively (e.g. announces an incoming call) without the user asking first.
- **Tailnet** — the private mesh VPN Tailscale provides. The device + family phones all sit inside the same tailnet for the MVP.

## What success looks like for v1

A relative who is not the project owner — say, the owner's mother — installs this PWA on her iPhone, taps "Llamar a abuelo", and is talking to him within 5 seconds. When abuelo says _"ayuda"_ later that day, her phone rings on the lock screen, she taps the notification, and she's talking to him. She closes the app. That's the bar.

Everything else (admin panels, transcripts, multi-device, group calls) is later. Get the call loop right first.
