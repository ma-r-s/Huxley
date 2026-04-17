# Architecture

This is the architecture of **Huxley the framework** — the parts that are persona-agnostic and skill-agnostic. Persona spec lives in [`personas/`](./personas/), skill spec in [`skills/`](./skills/). Diagrams use the AbuelOS persona as the worked example because it's the canonical one, but the architecture is identical for any persona.

> **Refactor in progress**: stage 1 (rename + workspace + SDK extraction) shipped on 2026-04-16. The Python namespaces are now `huxley` (framework runtime, in `packages/core/`) and `huxley_sdk` (skill author surface, in `packages/sdk/`). Stages 2–5 add entry-point-loaded skill packages, persona YAML loading, and the persona-data move; until they land, the two skills (`audiobooks`, `system`) still live inside `packages/core/src/huxley/skills/` and are constructed inline in `app.py`. The plan lives in `~/.claude/plans/proud-conjuring-papert.md`.

## System overview

```mermaid
flowchart LR
    subgraph Client["Audio client (browser today, ESP32 later)"]
        Mic[🎤 Mic]
        Spk[🔊 Speaker]
        UI[PTT button]
    end

    subgraph Huxley["Huxley framework (Python server)"]
        WS[AudioServer<br/>WebSocket :8765]
        App[Application<br/>orchestrator]
        SM[StateMachine]
        Sess[VoiceProvider<br/>OpenAI Realtime]
        Coord[TurnCoordinator]
        Reg[SkillRegistry]
        Skills[Skills:<br/>audiobooks, system, ...]
        Player[AudiobookPlayer<br/>ffmpeg subprocess]
        DB[(SQLite<br/>positions + summaries)]
    end

    OpenAI[OpenAI Realtime API]

    Mic -- PCM16 24 kHz --> WS
    WS -- PCM16 24 kHz --> Spk
    UI -- ptt_start / ptt_stop / wake_word --> WS
    WS -- state / status / transcript / model_speaking / dev_event / audio_clear --> UI

    WS <--> App
    App --> SM
    App <--> Sess
    App <--> Coord
    App <--> Reg
    Coord --> Skills
    Skills --> Player
    Skills --> DB
    Sess <-- WebSocket --> OpenAI
```

## Core invariants

### Audio path: client owns I/O, framework owns the brain

Huxley never touches audio hardware. Every client — browser for dev, ESP32 for production — captures the mic, drives the speaker, and streams PCM16 at 24 kHz over WebSocket. Huxley relays audio to the voice provider, dispatches tool calls, runs skills, manages state. This is why the same framework code works for any client without re-architecture.

See [decision 2026-04-12 — Python server does not own audio hardware](./decisions.md#2026-04-12--python-server-does-not-own-audio-hardware).

### One audio pipe out

There is **one** audio channel out to the client (`server.send_audio`). Both LLM model audio AND tool-produced audio (audiobook playback, future media) flow through it, in the exact same PCM16 24 kHz mono format. The client has one playback code path and cannot tell the two sources apart. The TurnCoordinator sequences them so model speech always comes before tool audio in the same turn.

See [decision 2026-04-13 — Audiobook audio streams through the WebSocket](./decisions.md#2026-04-13--audiobook-audio-streams-through-the-websocket-not-local-playback) and [`turns.md`](./turns.md).

### Persona is config, not code

The framework loads a `persona.yaml` at startup and uses it to build the system prompt, register the listed skills, and configure the voice provider. Swap the persona file → swap the agent. Code does not know "this is for a blind elderly user" — that knowledge lives entirely in the persona file and the constraint definitions it references.

## State machine

The session-level state machine has 3 states:

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> CONNECTING: wake_word
    CONNECTING --> CONVERSING: connected
    CONNECTING --> IDLE: failed
    CONVERSING --> IDLE: timeout
    CONVERSING --> IDLE: disconnect
```

- **IDLE** — no voice provider session. Resting state.
- **CONNECTING** — opening the session, sending `session.update` with tool schemas.
- **CONVERSING** — session open, PTT works, tool calls dispatch, audiobook playback may be happening — media is orthogonal to session state.

Media playback is **not** a session state. It's tracked by `TurnCoordinator.current_media_task`, which outlives turns: a book started in turn N keeps playing until turn N+M interrupts it. The voice provider session stays open during book playback (idle sessions cost zero tokens), and pressing PTT mid-book goes through the turn coordinator's interrupt method rather than a state transition.

See [`turns.md`](./turns.md) and [decision 2026-04-13 — Turn-based coordinator for voice tool calls](./decisions.md#2026-04-13--turn-based-coordinator-for-voice-tool-calls).

## Sequence — a PTT turn in CONVERSING

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Client as Browser / ESP32
    participant Server as AudioServer
    participant Coord as TurnCoordinator
    participant Sess as VoiceProvider
    participant LLM as OpenAI Realtime

    User->>Client: holds button
    Client->>Server: { type: "ptt_start" }
    Server->>Coord: on_ptt_start()
    Note over Coord: new Turn(LISTENING)
    loop while button held
        Client->>Server: { type: "audio", data: PCM16 }
        Server->>Coord: on_user_audio_frame(pcm)
        Coord->>Sess: send_audio(pcm)
        Sess->>LLM: input_audio_buffer.append
    end
    User->>Client: releases button
    Client->>Server: { type: "ptt_stop" }
    Server->>Coord: on_ptt_stop()
    Coord->>Sess: commit_and_respond()
    Sess->>LLM: buffer.commit + response.create
    LLM-->>Sess: response.audio.delta (streaming)
    Sess-->>Coord: on_audio_delta(pcm)
    Coord->>Server: send_model_speaking(true)<br/>send_audio(pcm)
    Server-->>Client: { type: "audio", data: PCM16 }
    LLM-->>Sess: response.audio.done
    Sess-->>Coord: on_audio_done()
    Coord->>Server: send_model_speaking(false)
    LLM-->>Sess: response.done
    Sess-->>Coord: on_response_done()
    Note over Coord: terminal — no factory, turn ends
```

## Sequence — a tool call that starts an audiobook

```mermaid
sequenceDiagram
    autonumber
    participant LLM as OpenAI Realtime
    participant Sess as VoiceProvider
    participant Coord as TurnCoordinator
    participant Skill as audiobooks skill
    participant Player as AudiobookPlayer
    participant Ffmpeg as ffmpeg
    participant DB as Storage
    participant WS as AudioServer

    Note over LLM: model pre-narrates ack<br/>(persona-language)
    LLM-->>Sess: response.audio.delta (ack chunks)
    Sess-->>Coord: on_audio_delta(...)
    Coord->>WS: send_audio(...)
    LLM-->>Sess: response.function_call<br/>play_audiobook({book_id})
    Sess-->>Coord: on_function_call(call_id, name, args)
    Coord->>Skill: dispatch("play_audiobook", args)
    Skill->>DB: get_audiobook_position(book_id)
    Skill->>Player: probe(path)
    Skill->>DB: set_setting(LAST_BOOK_SETTING)
    Skill-->>Coord: ToolResult(output, audio_factory=closure)
    Note over Coord: factory latched onto pending_factories
    Coord->>Sess: send_function_output(call_id, output)
    Sess->>LLM: conversation.item.create
    LLM-->>Sess: response.audio.done
    Sess-->>Coord: on_audio_done()
    LLM-->>Sess: response.done
    Sess-->>Coord: on_response_done()
    Note over Coord: terminal barrier — invoke factory
    Coord->>Skill: factory() → generator
    Skill->>Player: stream(path, start_position)
    Player->>Ffmpeg: spawn with -re -ss <pos> -f s16le -
    loop realtime PCM streaming
        Ffmpeg-->>Player: PCM16 chunk (100 ms)
        Player-->>Skill: yield chunk
        Skill-->>Coord: yield chunk
        Coord->>WS: send_audio(pcm)
        WS->>WS: → client over WebSocket
    end
    Note over Skill: generator finally block<br/>saves terminal position on cancel/EOF
```

**Key insights**:

1. **A skill never touches the coordinator, state machine, or the voice provider directly.** It returns a `ToolResult` with an optional `audio_factory` closure (or other side effect). The framework executes side effects at the right moment.
2. **Speech before factories, always.** The coordinator forwards the model's audio deltas first, then invokes pending factories on `response.done`. Tool audio never jumps in without an ack — structurally impossible, not "fixed with a flag."
3. **Same audio pipe for everything.** Model speech and tool audio both travel through `server.send_audio`. The client doesn't branch on source.
4. **Atomic interrupts.** A new `ptt_start` during a live turn runs `coordinator.interrupt()`: drop flag → clear pending factories → audio_clear → cancel media task → cancel LLM response → mark turn interrupted. The running media task's `finally` block persists any terminal state (e.g. audiobook position), so seek/forward/interrupt are all transaction-safe without eager storage writes.

## Dependency flow (no cycles)

```mermaid
flowchart TD
    App[app.py]
    Server[server/server.py]
    Session[session/manager.py]
    Coord[turn/coordinator.py]
    Skills[skills/*]
    Registry[skills/__init__.py<br/>SkillRegistry]
    State[state/machine.py]
    Player[media/audiobook_player.py]
    Storage[storage/db.py]
    Types[types.py]

    App --> Server
    App --> Session
    App --> Coord
    App --> Registry
    App --> State
    App --> Player
    App --> Storage
    Registry --> Skills
    Skills --> Player
    Skills --> Storage
    Session --> Registry
    Session --> Storage
    State --> Types
    Skills --> Types
    Registry --> Types
    Server --> Types
    Coord --> Types
```

Dependencies flow **downward**. `types.py` is the universal leaf — everyone imports from it, it imports from nothing. `app.py` is the root — nothing imports from it, it wires everything.

After the SDK extraction (next refactor), skills will depend only on `huxley_sdk`, never on framework internals. This is the boundary that makes third-party skills possible.

## Where to look in code

| Concern                           | File                                                 |
| --------------------------------- | ---------------------------------------------------- |
| Orchestrator / all wiring         | `packages/core/src/huxley/app.py`                    |
| WebSocket audio server            | `packages/core/src/huxley/server/server.py`          |
| State machine + transitions       | `packages/core/src/huxley/state/machine.py`          |
| Turn coordinator + factory fire   | `packages/core/src/huxley/turn/coordinator.py`       |
| Voice provider (OpenAI Realtime)  | `packages/core/src/huxley/session/manager.py`        |
| OpenAI event schemas              | `packages/core/src/huxley/session/protocol.py`       |
| Skill protocol + ToolResult       | `packages/sdk/src/huxley_sdk/types.py`               |
| Skill registry + dispatch         | `packages/sdk/src/huxley_sdk/registry.py`            |
| SkillContext + SkillStorage       | `packages/sdk/src/huxley_sdk/types.py`               |
| FakeSkill (test helper)           | `packages/sdk/src/huxley_sdk/testing.py`             |
| Audiobooks skill (still inline)   | `packages/core/src/huxley/skills/audiobooks.py`      |
| Audiobook ffmpeg stream generator | `packages/core/src/huxley/media/audiobook_player.py` |
| System skill (still inline)       | `packages/core/src/huxley/skills/system.py`          |
| SQLite wrapper                    | `packages/core/src/huxley/storage/db.py`             |
| Config (env-driven settings)      | `packages/core/src/huxley/config.py`                 |

After stages 2–4 land, the two skills move out:

```
packages/skills/audiobooks/src/huxley_skill_audiobooks/   # built-in, entry-point loaded
packages/skills/system/src/huxley_skill_system/           # built-in, entry-point loaded
personas/abuelos/persona.yaml                             # the AbuelOS persona
personas/abuelos/data/                                    # audiobooks library + sqlite db
```
