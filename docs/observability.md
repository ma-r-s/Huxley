# Observability

> **The dev contract**: you describe a symptom in human language, the LLM developer reads the log, you find the bug together — without the LLM having to ask "what were you doing?"

This document defines what makes that contract possible: the structured event vocabulary every Huxley component emits, the namespacing convention, and what skill authors do to participate.

## Why this matters

Voice is hard to debug. The user can't paste a stack trace. The bug is often a timing issue, a state-context issue, or a "model didn't do what we expected." Without good logs, debugging is back-and-forth: "what did the agent say?", "did it try to call the tool?", "was there silence?" — each question is a round trip.

The fix is logs that **tell the story**: every state transition, every decision branch, every message in and out, with enough context that a reader who wasn't there can reconstruct what happened. That reader is usually an LLM helping you debug.

The dream interaction:

> **You**: "I asked it to play a book, it said it would, but no audio came."
>
> **LLM (reading log)**: "I see — `coord.tool_dispatch` fired with `has_factory=true`, then `coord.factory_started`, then `coord.factory_ended` with `cancelled=true` 200 ms later. Something cancelled the media task. Looking at `coord.interrupt` — none. Looking at `coord.session_disconnected` — yes. The voice provider session dropped, which cancelled the factory. The auto-reconnect didn't restart playback because that's not its job. Want a 'resume in-progress book on auto-reconnect' feature?"

That's the goal. It only works if the log carries the right events with the right fields.

## Naming convention

Every log event uses a dotted namespace + direction prefix. This makes events `grep`-able, filterable, and identifiable at a glance.

| Namespace      | Source                                                  | Examples                                                               |
| -------------- | ------------------------------------------------------- | ---------------------------------------------------------------------- |
| `coord.*`      | TurnCoordinator decisions (the core state machine)      | `coord.ptt_start`, `coord.interrupt`, `coord.tool_dispatch`            |
| `session.rx.*` | Messages received from the voice provider (OpenAI)      | `session.rx.function_call`, `session.rx.error`                         |
| `session.tx.*` | Messages sent to the voice provider                     | `session.tx.commit`, `session.tx.cancel`, `session.tx.function_output` |
| `server.rx.*`  | Messages received from the audio client                 | `server.rx.ptt_start`, `server.rx.ptt_stop`, `server.rx.wake_word`     |
| `server.tx.*`  | Messages sent to the audio client                       | `server.tx.state`, `server.tx.model_speaking`                          |
| `app.*`        | Application orchestration (lifecycle, guard rejections) | `app.session_end`, `app.ptt_rejected`                                  |
| `<skill>.*`    | Per-skill events (skill author owns the namespace)      | `audiobooks.resolve`, `audiobooks.stream_started`, `system.volume_set` |
| `client.*`     | Telemetry forwarded from the audio client (web/ESP32)   | `client.silence_timer_started`, `client.thinking_tone_on`              |

A skill named `audiobooks` emits events like `audiobooks.factory_built`, `audiobooks.stream_ended`. The framework reserves `coord.`, `session.`, `server.`, `app.`, `client.`; everything else is skill territory.

The `client.*` events come in via the `client_event` WebSocket message type — clients emit `{"type": "client_event", "event": "<name>", "data": {...}}` and the server logs them as `client.<name>` with the data fields spread. Pure observability — the framework takes no action. This closes the "client-side blackbox" gap; thinking-tone state, silence-timer fires, and PTT UI transitions are now visible in the same log file as the server-side flow.

## Context fields

Every event carries the minimum context needed to be useful in isolation:

| Field   | Source                                         | When present                                                                                    |
| ------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `turn`  | The current turn's short UUID (8 chars)        | Auto-injected by the coordinator's bound logger; available to skills via the SDK logger context |
| `state` | The current `TurnState` value                  | Passed explicitly on decision-point events (`tool_dispatch`, `interrupt`, `response_done`)      |
| Custom  | Whatever fields make the event reconstructable | Per-event, named for what they are                                                              |

Given an event like:

```
coord.interrupt  turn=abc12345  prev_state=IN_RESPONSE  has_media=false  will_cancel=true  pending_factories=1
```

A reader knows: turn `abc12345` was in `IN_RESPONSE`, no media task was running, an OpenAI cancel will be sent, and one factory was dropped. That's a complete picture without other context.

## What goes at info vs debug

### `info` — the story

A typical turn produces ~14 info lines. Reading the info log gives you a complete narrative of what happened.

Roughly:

- State transitions (what the coordinator decided)
- Decision points (which branch was taken, with the inputs)
- Messages sent/received between Huxley and OpenAI
- Messages sent/received between Huxley and the client (except audio)
- Skill-level milestones (tool dispatched, side effect spawned, position saved)
- Application lifecycle (startup, shutdown, session end, auto-reconnect)

### `debug` — the noise

When a specific timing issue needs diagnosis, enable debug. These events are too high-frequency for normal reading:

- Each audio delta forwarded (`coord.audio_fwd`)
- Each mic frame forwarded (`coord.mic_fwd`)
- Audio deltas dropped because of the cancel flag (`coord.audio_dropped`)
- Status messages sent to the client (the Spanish UI strings)
- Per-frame skill-internal noise

Default log level is info. Set `HUXLEY_LOG_LEVEL=debug` for the full firehose.

## Skill authors — making your skill debuggable

Your skill gets a logger via the SDK context. **Use it.** A skill that doesn't log is undiagnosable when something goes wrong, and that breaks the whole "describe-the-symptom" workflow.

```python
from huxley_sdk import Skill, ToolResult

class MySkill:
    async def setup(self, context):
        self.log = context.logger  # auto-namespaced to your skill name
        self.config = context.config

    async def handle(self, tool_name: str, args: dict) -> ToolResult:
        await self.log.info("dispatch", tool=tool_name, args_keys=list(args))
        try:
            result = await self._do_the_thing(args)
        except SomeError as exc:
            await self.log.warning("handler_failed", tool=tool_name, error=str(exc))
            return ToolResult(output=...)
        await self.log.info("done", tool=tool_name)
        return result
```

The SDK logger:

- Auto-namespaces every event with your skill name (`mySkill.dispatch`, not `dispatch`)
- Auto-injects the current turn ID
- Uses the same structured-field convention as the framework

### What to log

For every tool call: dispatch entry, the decision your handler made, the result.

For every meaningful state change inside your skill: log it. ("Resolved book by fuzzy match", "factory built with start_position=X", "subprocess spawned".)

For every external call: log before and after. (HTTP request, subprocess, file I/O.)

For every error path: log it, even if you handle it gracefully. The user-visible result is the right behavior, but the log line is how a future debugger knows what happened.

### What NOT to log

- Per-frame audio data (let the framework handle stream-level logging at debug)
- Secrets (API keys, credentials, PII the user shared)
- Verbose object dumps (log relevant fields, not whole structs)

## Reading a log

The log file lives at `logs/huxley.log` (or whatever `HUXLEY_LOG_FILE` is set to). It's JSON Lines — one event per line, one JSON object per event.

A typical turn looks like this (reformatted for readability):

```
server.rx.ptt_start
coord.ptt_start         turn=abc12345  had_turn=false  had_media=false
coord.ptt_stop          turn=abc12345  frames=28  committed=true
session.tx.commit
coord.audio_start       turn=abc12345  state=IN_RESPONSE
server.tx.model_speaking value=true
session.rx.function_call name=play_audiobook  call_id=c1
coord.tool_dispatch     turn=abc12345  state=IN_RESPONSE  name=play_audiobook  has_factory=true
audiobooks.factory_built turn=abc12345  book_id=...  start_position=0.0
session.tx.function_output call_id=c1
coord.audio_done        turn=abc12345
server.tx.model_speaking value=false
coord.response_done     turn=abc12345  state=IN_RESPONSE  follow_up=false  factories=1
coord.factory_started   turn=abc12345
coord.turn_ended        turn=abc12345
audiobooks.stream_started turn=abc12345  path=...  start=0.0
```

Read top to bottom: user pressed PTT (frame 28), coordinator committed, model started speaking ("ahí le pongo el libro"), called the play_audiobook tool, the audiobooks skill built a factory and announced it, model finished speaking, response done, factory fired, book started streaming.

Every event is grep-able. To filter to one turn: `grep "turn=abc12345"`. To see only coordinator decisions: `grep "^coord\."`. To see what the agent sent the user: `grep "^server.tx\."`.

## When something looks wrong

The dev workflow:

1. **You** describe the symptom in conversation: "the book never started", "the tone played forever", "the model said it would but didn't".
2. **You** paste the relevant log section (from the symptom moment, plus a few seconds before and after).
3. **The LLM** reads the log and proposes a hypothesis. Often the bug is identifiable from the log alone.
4. **You** confirm or dispute, sometimes with one more log filter.
5. **The LLM** proposes a fix.

This works because every event in the log is structured, every decision is logged with its inputs, and the namespacing tells you instantly which component made which call. If a future bug isn't diagnosable from the log, the fix isn't just the bug — it's also adding the log line that would have caught it.
