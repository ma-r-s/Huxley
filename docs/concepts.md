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

Some tools have observable effects: an audiobook starts playing, a notification fires, a light turns off. These are side effects. The framework sequences them — they fire _after_ the agent finishes speaking, never during, so the user always hears the acknowledgement before the thing happens.

Today the only side effect kind is `AudioStream` (for audiobook playback). The architecture is designed so other kinds — `Notification`, `StateChange`, future ones — can be added without touching skills that don't use them.

## Factory

**A side effect that produces a stream over time.**

An audio stream is a sequence of PCM chunks. The skill doesn't yield them directly — it returns a _factory_ (a callable that, when invoked by the framework, returns the chunks). This indirection lets the framework cancel the stream cleanly when the user interrupts, without the skill having to think about cancellation semantics.

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
