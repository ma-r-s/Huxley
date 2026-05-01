# Concepts

The Huxley vocabulary, in the order you encounter it.

## Persona

**Who the agent is.**

A persona is a YAML file that declares the agent's identity — name, voice, language, personality, values — and the list of skills it has access to. It's pure configuration; no Python.

```yaml
name: Abuelo
language: es-CO
voice: alloy
personality: |
  Eres un compañero amable de don Carlos, un señor de 90 años...
constraints:
  - never_say_no
skills:
  - audiobooks: { library: ./data/audiobooks }
  - system: {}
```

A persona is shareable. You can clone someone else's persona file, install the skills it lists, and have an identical agent. Personas live in the `server/personas/` directory; Huxley discovers all of them at startup and loads one at a time.

Personas can declare multiple languages in a single YAML file via an `i18n:` block (per-language `system_prompt`, `ui_strings`, and skill overrides). Clients pick the language at connect time with a `?lang=<code>` query param on the WebSocket URL; the framework resolves the persona for that language and hands skills a `SkillContext` whose `language` field flips accordingly. See [`server/personas/README.md#multilingual-personas`](./personas/README.md#multilingual-personas).

How to write one: [`server/personas/README.md`](./personas/README.md).

### Persona = distinct entity, not theme

This is a product contract, not just an implementation detail.

**A persona is a distinct entity that the user talks to. It is not a theme layered on a shared user profile.** Each persona has its own conversation memory, its own session history, its own reminders, its own Telegram identity, its own audiobook progress. Switching personas is reuniting with a different person — information does not flow between them. If you tell abuelos something, librarian does not know it.

Why this contract:

- The LLM's conversation summary and transcript context are intrinsically per-persona. Mixing librarian's summary into abuelos's instructions confuses the model. Once **any** state is per-persona, mixing user-scoped data on top creates a hybrid where the persona seems to know some things but not others — worse than either pure model. Owning the boundary is more honest than papering over it.
- Multi-user households work cleanly: abuelos for grandpa with grandpa's Telegram, buddy for the kid with the kid's Telegram. Privacy is filesystem-enforced (per-persona DBs, per-persona MTProto sessions).
- The "missed reminder when I switched personas" footgun reframes from a bug to documented behavior: when you're not talking to abuelos, abuelos isn't around. On return, abuelos catches up via the same skill-setup-reads-DB mechanism that already handles process restarts.

When a persona is **not** active, it is genuinely **absent**: its Telegram is offline, its reminders are paused, its conversation context is dormant. The runtime hosts ONE active persona at a time (`current_app` in [`huxley.runtime`](../server/runtime/src/huxley/runtime.py)); inactive personas are not running in the background. See the [hot persona swap ADR](./decisions.md#2026-05-01--persona-is-a-distinct-entity-not-a-theme-t113) for the rationale and consequences in full.

### Persona switch = reconnect, not theme change

Switching personas is implemented as a WebSocket reconnect with a `?persona=<name>` query parameter. The PWA closes the WS, opens a new one with the new param, and the server swaps the active `Application` before sending hello. From the user's seat this looks like a brief loading state — same flow as a language switch (`?lang=<code>` reconnect). See the [reconnect-vs-in-band ADR](./decisions.md#2026-05-01--hot-persona-swap-via-reconnect-not-in-band-t113) and the [protocol contract](./protocol.md#persona-selection-via-query-param-t113).

### One Huxley process = one human

A Huxley process hosts one human's set of personas. Two humans in one house = two Huxley processes, each in its own working directory with its own `personas/`, `.env`, port, and DBs. There is no profile abstraction — multi-instance follows the standard Unix-daemon convention (one cwd per instance, launchd plist or systemd unit per instance). See [`docs/architecture.md`](./architecture.md#runtime-topology) for the canonical layout and the [no-profile-abstraction ADR](./decisions.md#2026-05-01--multi-instance-deployment-via-cwds-no-profile-abstraction-t113).

## Skill

**What the agent can do.**

A skill is a Python package. It declares one or more **tools** (function definitions the LLM can call), implements a handler for each, and returns a result that may include text and/or a side effect.

A skill never imports framework internals. It uses the Huxley SDK, which gives it a typed context (storage, config, logger) and the types it needs. This contract is what keeps skills portable — a skill works against any persona that enables it.

A skill is `pip install`-able. Built-in skills live in `server/skills/`; community skills are published on PyPI under the `huxley-skill-*` prefix.

How to write one: [`skills/README.md`](./skills/README.md).

## Tool

**A function the LLM can call.**

A skill exposes one or more tools. Each tool has a name, a description (in the persona's language), and a JSON Schema for its parameters. The LLM decides when to call a tool based on the description; the skill handles the call and returns a result.

Tools are how skills extend the agent's capabilities. The agent can do anything for which there's a tool installed, and only that.

## Turn

**One round of user-assistant exchange.**

A turn starts when the user begins speaking and ends when the agent has fully responded (and any side effects have been kicked off). A turn may span multiple LLM responses if the agent needs to call an info tool and narrate the result — still one turn from the user's perspective.

The Turn Coordinator is the framework component that owns the turn lifecycle. Skill authors don't think about turns; the framework handles them.

The full state machine lives in [`turns.md`](./turns.md).

## Tool result

**What a skill returns.**

A `ToolResult` has two parts:

- `output`: a string (usually JSON) that the LLM reads and narrates to the user
- `side_effect` (optional): something the framework should execute in the world after the model finishes speaking

## Side effect

**Something a skill produces beyond text.**

Some tools have observable effects: an audiobook starts playing, a notification fires, a light turns off. These are side effects. The framework sequences them — they fire _after_ the agent finishes speaking (or, in the case of pre-response chimes, immediately before), never colliding with model speech mid-word, so the user always hears one stream at a time.

Side effect kinds today:

- **`AudioStream`** — long-running PCM byte stream (audiobook playback). Coordinator invokes the factory at the turn's terminal barrier.
- **`PlaySound`** — short one-shot PCM clip (news-intro chime, etc.) for info tools that want a sonic cue marking "I'm responding now." Coordinator queues the bytes right after firing `request_response()` so the chime hits the WebSocket ahead of the model's audio deltas (FIFO).
- **`CancelMedia`** — stop the running media task immediately (for pause/stop tools).
- **`SetVolume`** — forward a volume command to the client.

The architecture is designed so other kinds — `Notification`, `StateChange`, future ones — can be added without touching skills that don't use them.

## Factory

**A side effect that produces a stream over time.**

An audio stream is a sequence of PCM chunks. The skill doesn't yield them directly — it returns a _factory_ (a callable that, when invoked by the framework, returns the chunks). This indirection lets the framework cancel the stream cleanly when the user interrupts, without the skill having to think about cancellation semantics.

## Catalog

**A skill's index of personal-content items.**

Huxley's headline differentiator is "LLM understands rough natural-language intent and dispatches to user-installable custom tools, including for personal content" — audiobooks, radio stations, contacts, recipes, anything the user owns. Every personal-content skill needs the same shape: index items by string fields, fuzzy-match user phrases against them (accent-insensitive for Spanish), inject baseline awareness into the system prompt.

Rather than every skill reinventing the matching logic with different bugs, the SDK provides a `Catalog` primitive. Skills construct one in `setup()` via `ctx.catalog()`, `upsert` items, and use `search(query)` and `as_prompt_lines()` to drive the LLM. See [`skills/README.md`](./skills/README.md#using-a-catalog) for the usage pattern.

The current backend is in-memory with `SequenceMatcher`-based fuzzy matching; the API is stable enough that a future SQLite FTS5 backend swap (when a skill needs persistence or 10k+ scale) is invisible to skill code.

## I/O plane

**The framework's mechanism for skill-extensible streams.**

Huxley is an audio-first agent runtime. Below the skill line, everything reduces to three streams (mic input, speaker output, client events) plus the turn loop. The I/O plane is the set of framework primitives that let skills claim, route, inject into, or subscribe to these mechanisms — **without the framework ever knowing what the skill is doing**.

Five primitives (all documented in [`io-plane.md`](./io-plane.md)):

- **`AudioStream` / `PlaySound` / `CancelMedia` / `SetVolume`** — claim the speaker output stream (already shipped)
- **Turn injection (`ctx.inject_turn`)** — a skill synthesizes a turn into the turn loop from outside the user's speech path
- **`InputClaim`** — a skill takes over the mic stream (and optionally the speaker) for a duration
- **`ClientEvent` subscription (`ctx.subscribe_client_event`)** — skills subscribe to string-keyed control events from the client
- **`background_task` (`ctx.background_task`)** — skills register supervised long-running tasks (schedulers, listeners)

**Guiding principle**: the framework names mechanisms, not use cases. Nothing in `huxley_sdk` or `huxley` core mentions "call," "reminder," "message," or "emergency." Those live in skills. A future skill names itself what it is.

## Focus management (Channel + FocusState + MixingBehavior)

**The vocabulary the framework uses to arbitrate who owns the speaker at any moment.**

Channels are named resource scopes on the single speaker: `DIALOG` (conversation with the user, priority 100), `COMMS` (inbound/outbound calls, priority 150), `ALERT` (urgent announcements, priority 200 — reserved, see below), `CONTENT` (audiobooks, radio, other streams, priority 300). Lower-numbered channels win against higher-numbered ones (AVS convention).

Every `Activity` registered on a channel gets delivered a `(FocusState, MixingBehavior)` pair as the focus picture changes:

- `FocusState` — `FOREGROUND` (I'm the primary speaker), `BACKGROUND` (someone above me is speaking; I may duck or pause), `NONE` (I'm displaced entirely).
- `MixingBehavior` — `PRIMARY`, `MAY_DUCK`, `MUST_PAUSE`, `MUST_STOP`. Derived from FocusState + the Activity's `ContentType` (`MIXABLE` → `MAY_DUCK` on background; `NONMIXABLE` → `MUST_PAUSE`).

`FocusManager` is the serialized actor that enforces invariants: exactly one FOREGROUND Activity across all channels; same `(channel, interface_name)` replaces its prior Activity; displaced Activities get a configurable `patience` grace period at BACKGROUND before being cleared. On patience expiry the observer's `on_patience_expired()` hook fires BEFORE the terminal NONE/MUST_STOP — skills can narrate the eviction so state changes are never silent for the user. Skills never talk to `FocusManager` directly — they either return `SideEffect` side-effects from a tool call (`AudioStream`, `InputClaim`) or call a `SkillContext` method like `inject_turn(prompt)`, and the framework translates those into focus acquires/releases.

Live paths through `FocusManager`:

- **DIALOG** — `ctx.inject_turn(prompt)` creates a synthetic INJECTED turn with a DIALOG Activity. Highest priority (100). Channel priority means DIALOG preempts every other channel; the LLM narrates the prompt; DIALOG releases when the turn ends.
- **COMMS** — an `InputClaim` side-effect (or direct `ctx.start_input_claim` call) registers an Activity on COMMS. Priority 150 means COMMS preempts CONTENT but yields to DIALOG. Single-slot by policy: a second claim raises `ClaimBusyError` so the skill rejects the peer cleanly (call-waiting stacking is explicitly out of scope). Telegram calls live here.
- **CONTENT** — any `AudioStream` side-effect returned from a skill's `handle()` gets an Activity on CONTENT; pump runs while FOREGROUND, cancels on BACKGROUND/MUST_PAUSE (NONMIXABLE) or NONE. On BACKGROUND/MAY_DUCK (MIXABLE streams) the pump keeps running with a 100ms ramp to 0.3 gain — classic AVS ducking. Audiobooks set `patience=30min` so a COMMS claim parks the book in BACKGROUND rather than evicting it; on claim-end, FM promotes CONTENT back to FOREGROUND and the skill's factory re-reads the current position from storage to resume from where it paused.
- **Patience expiry** — when a backgrounded Activity times out, FM calls its `on_patience_expired()` hook before the final NONE/MUST_STOP so the skill can narrate the eviction (e.g., audiobooks inject_turn a "pausé tu libro por la llamada larga" message).

Reserved (defined in the enum, FM arbitrates it correctly if used, but no callable surface exposed): **ALERT** (priority 200). Sits between COMMS and CONTENT. Intended for future LLM-free alert sounds (sirens, alarms) that overlay content but don't interrupt live calls. No current consumer — the urgent severity tier today is expressed as `InjectPriority.BLOCK_BEHIND_COMMS` on a DIALOG-shaped inject_turn (same effective priority, reuses existing narration machinery). ALERT gets wired when a concrete skill needs non-narrated audio in that tier.

See [`io-plane.md`](./io-plane.md) for the historical composition vocabulary and [`architecture.md#focus-management`](./architecture.md#focus-management) for the actor model.

## Voice provider

**The thing that turns audio into text and text into audio.**

Huxley uses OpenAI's Realtime API today. The voice provider interface is internal to the framework and abstracted behind the Turn Coordinator, so a future Huxley could swap in Anthropic, a local Whisper+Llama+TTS pipeline, or anything else without skills knowing.

For MVP, this abstraction stays minimal. We don't ship multiple providers. The seam is just there for someday.

## Constraint

**A named behavioral rule layered onto the system prompt.**

Personas don't rewrite the system prompt from scratch — they compose it from their `personality` string plus named constraints.

| Constraint             | Effect                                                                                                                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `never_say_no`         | The agent never returns a bare "no" or "I can't." Every negative includes an alternative or escalation.                                                                                  |
| `confirm_destructive`  | The agent confirms before any irreversible action.                                                                                                                                       |
| `child_safe`           | Filters profanity and adult topics.                                                                                                                                                      |
| `no_religious_content` | Avoids initiating or deepening religious topics; redirects politely if the user brings them up.                                                                                          |
| `echo_short_input`     | When the user says only one or two words, the agent echoes what it understood before acting — prevents acting on a mishear.                                                              |
| `confirm_if_unclear`   | Before calling any tool, the agent evaluates whether it understood the request. If the audio was cut or the intent ambiguous, it asks one short clarifying question instead of guessing. |

Constraint definitions live in `server/runtime/src/huxley/constraints/`. Adding one is a one-file PR.

## Client

**What the user talks to.**

Huxley is headless — it listens on a WebSocket and speaks to whoever connects. Clients own audio hardware (mic, speaker). The browser dev client (in `clients/pwa/`) is the MVP client; an ESP32 walky-talky is the eventual production client.

The protocol between Huxley and its clients is in [`protocol.md`](./protocol.md). Any hardware or software that implements it is a valid client.

## How it all fits

```
┌──────────────────────────────────────────────────────┐
│  Persona (persona.yaml)                              │
│  • Identity, language, personality, constraints      │
│  • List of skills + their config                     │
└──────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Huxley framework                                    │
│  • Loads persona + skills                            │
│  • Builds system prompt for the LLM                  │
│  • Manages the voice session (turn coordinator)      │
│  • Dispatches tool calls to skills                   │
│  • Sequences side effects (audio, notifications)     │
└──────────────────────────────────────────────────────┘
                  ▲                ▼                ▼
                  │                │                │
        ┌─────────┴──────┐  ┌──────┴──────────┐  ┌──┴────────┐
        │  Voice provider│  │  Skill (Python) │  │  Client   │
        │  (OpenAI       │  │  via Huxley SDK │  │ (browser, │
        │  Realtime)     │  │                 │  │  ESP32)   │
        └────────────────┘  └─────────────────┘  └───────────┘
```

That's the whole conceptual model. Everything else — turn coordinator internals, ffmpeg streaming, websocket protocol, thinking tone — is implementation detail behind these primitives.
