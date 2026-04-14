# Architecture

## System overview

```mermaid
flowchart LR
    subgraph Client["Audio client (browser today, ESP32 later)"]
        Mic[🎤 Mic]
        Spk[🔊 Speaker]
        UI[PTT button]
    end

    subgraph Server["Python server (Raspberry Pi or any host)"]
        WS[AudioServer<br/>WebSocket :8765]
        App[Application<br/>orchestrator]
        SM[StateMachine]
        Sess[SessionManager]
        Reg[SkillRegistry]
        Skills[Skills:<br/>audiobooks, system, ...]
        Player[AudiobookPlayer]
        DB[(SQLite<br/>positions + summaries)]
        Ffmpeg[ffmpeg subprocess<br/>PCM16 24 kHz on stdout]
    end

    OpenAI[OpenAI Realtime API]

    Mic -- PCM16 24 kHz --> WS
    WS -- PCM16 24 kHz --> Spk
    UI -- ptt_start / ptt_stop / wake_word --> WS
    WS -- state / status / transcript / model_speaking / dev_event / audio_clear --> UI

    WS <--> App
    App --> SM
    App <--> Sess
    App <--> Reg
    App --> Player
    Reg --> Skills
    Skills --> Player
    Skills --> DB
    Player <-- stdout PCM --> Ffmpeg
    Player -- on_chunk --> App
    Sess <-- WebSocket --> OpenAI
```

**Audio path invariant**: there is **one** audio channel out to the client (`server.send_audio`). Both OpenAI model audio AND audiobook audio flow through it, in the exact same PCM16 24 kHz mono format. The client has one playback code path and cannot tell the two sources apart. See [decision 2026-04-13 — Audiobook audio streams through the WebSocket](./decisions.md#2026-04-13--audiobook-audio-streams-through-the-websocket-not-local-playback).

> **Note (design in flight)**: the audio path is being refactored from one shared `send_audio` channel into **named channels** (`speech`, `media`, `tone`, `status`) coordinated by a turn-based scheduler. This resolves a class of ordering bugs around tool-call side effects and model speech racing each other. The full spec is [`turns.md`](./turns.md) and the corresponding ADR is [decision 2026-04-13 — Turn-based coordinator for voice tool calls](./decisions.md#2026-04-13--turn-based-coordinator-for-voice-tool-calls). Until that refactor lands, the "single send_audio channel" invariant above still describes the runtime.

## Core rule — the client owns audio, the server owns the brain

Python never touches audio hardware. Every audio client — browser for dev, ESP32 for production — captures the mic, drives the speaker, and streams PCM16 at 24 kHz over WebSocket. The server relays audio to OpenAI, dispatches tool calls, runs skills, and manages state. This is why the same server code works for the browser and will work for the ESP32 walky-talky without re-architecture.

See [decision 2026-04-12 — Python server does not own audio hardware](./decisions.md#2026-04-12--python-server-does-not-own-audio-hardware).

## State machine

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> CONNECTING: wake_word
    CONNECTING --> CONVERSING: connected
    CONNECTING --> IDLE: failed
    CONVERSING --> IDLE: timeout
    CONVERSING --> IDLE: disconnect
```

- **IDLE** — no OpenAI session. Resting state.
- **CONNECTING** — opening the WebSocket to OpenAI, sending `session.update` with tool schemas.
- **CONVERSING** — session open, PTT works, tool calls dispatch, audiobook playback is happening (or not) — media is orthogonal to session state.

Media playback is **not** a session state. It's tracked by `TurnCoordinator.current_media_task`, which outlives turns: a book started in turn N keeps playing until turn N+M interrupts it. The OpenAI session stays open during book playback (idle sessions cost zero tokens), and pressing PTT mid-book goes through the turn coordinator's interrupt method rather than a state transition. See [`turns.md`](./turns.md#7-session-vs-turn-lifetime--playing-state-removed) and [decision 2026-04-13 — Turn-based coordinator for voice tool calls](./decisions.md#2026-04-13--turn-based-coordinator-for-voice-tool-calls).

The transition table lives in [`server/src/abuel_os/state/machine.py`](../server/src/abuel_os/state/machine.py) — that file is the authoritative source. Any change to the table must update this diagram in the same commit.

## Sequence — a PTT turn in CONVERSING

```mermaid
sequenceDiagram
    autonumber
    actor Grandpa
    participant Client as Browser / ESP32
    participant Server as AudioServer
    participant Coord as TurnCoordinator
    participant Sess as SessionManager
    participant OpenAI

    Grandpa->>Client: holds button
    Client->>Server: { type: "ptt_start" }
    Server->>Coord: on_ptt_start()
    Note over Coord: new Turn(LISTENING)
    loop while button held
        Client->>Server: { type: "audio", data: PCM16 }
        Server->>Coord: on_user_audio_frame(pcm)
        Coord->>Sess: send_audio(pcm)
        Sess->>OpenAI: input_audio_buffer.append
    end
    Grandpa->>Client: releases button
    Client->>Server: { type: "ptt_stop" }
    Server->>Coord: on_ptt_stop()
    Coord->>Sess: commit_and_respond()
    Sess->>OpenAI: buffer.commit + response.create
    OpenAI-->>Sess: response.audio.delta (streaming)
    Sess-->>Coord: on_audio_delta(pcm)
    Coord->>Server: send_model_speaking(true)<br/>send_audio(pcm)
    Server-->>Client: { type: "audio", data: PCM16 }
    OpenAI-->>Sess: response.audio.done
    Sess-->>Coord: on_audio_done()
    Coord->>Server: send_model_speaking(false)
    OpenAI-->>Sess: response.done
    Sess-->>Coord: on_response_done()
    Note over Coord: terminal — no factory, turn ends
```

## Sequence — a tool call that starts an audiobook

```mermaid
sequenceDiagram
    autonumber
    participant OpenAI
    participant Sess as SessionManager
    participant Coord as TurnCoordinator
    participant Skill as AudiobooksSkill
    participant Player as AudiobookPlayer
    participant Ffmpeg as ffmpeg
    participant DB as Storage
    participant WS as AudioServer

    Note over OpenAI: model pre-narrates ack<br/>"Ahí le pongo el libro, don"
    OpenAI-->>Sess: response.audio.delta (ack chunks)
    Sess-->>Coord: on_audio_delta(...)
    Coord->>WS: send_audio(...)
    OpenAI-->>Sess: response.function_call<br/>play_audiobook({book_id})
    Sess-->>Coord: on_function_call(call_id, name, args)
    Coord->>Skill: dispatch("play_audiobook", args)
    Skill->>DB: get_audiobook_position(book_id)
    Skill->>Player: probe(path)
    Skill->>DB: set_setting(LAST_BOOK_SETTING)
    Skill-->>Coord: ToolResult(output, audio_factory=closure)
    Note over Coord: factory latched onto pending_factories
    Coord->>Sess: send_function_output(call_id, output)
    Sess->>OpenAI: conversation.item.create
    OpenAI-->>Sess: response.audio.done
    Sess-->>Coord: on_audio_done()
    OpenAI-->>Sess: response.done
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

1. **A skill never touches the coordinator, state machine, or the session directly.** It returns a `ToolResult` with an optional `audio_factory` closure. The coordinator invokes the factory at the turn's terminal barrier, after the model finishes speaking.
2. **Speech before factories, always.** The coordinator forwards the model's audio deltas first, then invokes pending factories on `response.done`. The book never jumps in without an ack — structurally impossible, not "fixed with a flag."
3. **Same audio pipe for everything.** Model speech and book PCM both travel through `server.send_audio`. The client's `AudioPlayback` doesn't branch on source.
4. **Atomic interrupts.** A new `ptt_start` during a live turn runs `coordinator.interrupt()`: drop flag → clear pending factories → audio_clear → cancel media task → cancel OpenAI response → mark turn interrupted. The running media task's `finally` block persists the terminal position, so rewind/forward/interrupt are all transaction-safe without eager storage writes.

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

## Where to look in code

| Concern                           | File                                            |
| --------------------------------- | ----------------------------------------------- |
| Orchestrator / all wiring         | `server/src/abuel_os/app.py`                    |
| WebSocket audio server            | `server/src/abuel_os/server/server.py`          |
| State machine + transitions       | `server/src/abuel_os/state/machine.py`          |
| Turn coordinator + factory fire   | `server/src/abuel_os/turn/coordinator.py`       |
| OpenAI session lifecycle          | `server/src/abuel_os/session/manager.py`        |
| OpenAI event schemas              | `server/src/abuel_os/session/protocol.py`       |
| Skill registry + dispatch         | `server/src/abuel_os/skills/__init__.py`        |
| Skill protocol + ToolResult       | `server/src/abuel_os/types.py`                  |
| Audiobooks skill                  | `server/src/abuel_os/skills/audiobooks.py`      |
| Audiobook ffmpeg stream generator | `server/src/abuel_os/media/audiobook_player.py` |
| SQLite wrapper                    | `server/src/abuel_os/storage/db.py`             |
| Config (env + defaults)           | `server/src/abuel_os/config.py`                 |
