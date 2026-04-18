# Concepts

The Huxley vocabulary, in the order you encounter it.

## Persona

**Who the agent is.**

A persona is a YAML file that declares the agent's identity — name, voice, language, personality, values — and the list of skills it has access to. It's pure configuration; no Python.

```yaml
name: AbuelOS
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

A persona is shareable. You can clone someone else's persona file, install the skills it lists, and have an identical agent. Personas live in the `personas/` directory; Huxley loads one at startup based on config.

How to write one: [`personas/README.md`](./personas/README.md).

## Skill

**What the agent can do.**

A skill is a Python package. It declares one or more **tools** (function definitions the LLM can call), implements a handler for each, and returns a result that may include text and/or a side effect.

A skill never imports framework internals. It uses the Huxley SDK, which gives it a typed context (storage, config, logger) and the types it needs. This contract is what keeps skills portable — a skill works against any persona that enables it.

A skill is `pip install`-able. Built-in skills live in `packages/skills/`; community skills are published on PyPI under the `huxley-skill-*` prefix.

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

Channels are named resource scopes on the single speaker: `DIALOG` (conversation with the user), `COMMS` (inbound/outbound calls), `ALERT` (urgent announcements), `CONTENT` (audiobooks, radio, other streams). Lower-numbered channels win against higher-numbered ones (AVS convention).

Every `Activity` registered on a channel gets delivered a `(FocusState, MixingBehavior)` pair as the focus picture changes:

- `FocusState` — `FOREGROUND` (I'm the primary speaker), `BACKGROUND` (someone above me is speaking; I may duck or pause), `NONE` (I'm displaced entirely).
- `MixingBehavior` — `PRIMARY`, `MAY_DUCK`, `MUST_PAUSE`, `MUST_STOP`. Derived from FocusState + the Activity's `ContentType` (`MIXABLE` → `MAY_DUCK` on background; `NONMIXABLE` → `MUST_PAUSE`).

`FocusManager` is the serialized actor that enforces invariants: exactly one FOREGROUND Activity across all channels; same `(channel, interface_name)` replaces its prior Activity; displaced Activities get a configurable `patience` grace period at BACKGROUND before being cleared. Skills never talk to `FocusManager` directly — they return `SideEffect` side-effects (today, just `AudioStream`; tomorrow, inject_turn / InputClaim equivalents) and the framework translates them into focus acquires/releases.

The Stage 1 substrate ships now; the coordinator drives a `ContentStreamObserver` directly today, and wires in the `FocusManager` once skill-level arbitration (inject_turn, ducking) lands.

See [`io-plane.md`](./io-plane.md) for the composition vocabulary and [`architecture.md#focus-management`](./architecture.md#focus-management) for the actor model.

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

Constraint definitions live in `packages/core/src/huxley/constraints/`. Adding one is a one-file PR.

## Client

**What the user talks to.**

Huxley is headless — it listens on a WebSocket and speaks to whoever connects. Clients own audio hardware (mic, speaker). The browser dev client (in `web/`) is the MVP client; an ESP32 walky-talky is the eventual production client.

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
