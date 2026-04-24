# Observability

> **The dev contract**: you describe a symptom in human language, the LLM developer reads the log, you find the bug together — without the LLM having to ask "what were you doing?"

This document defines what makes that contract possible: the structured event vocabulary every Huxley component emits, the namespacing convention, and what skill authors do to participate.

## Why this matters

Voice is hard to debug. The user can't paste a stack trace. The bug is often a timing issue, a state-context issue, or a "model didn't do what we expected." Without good logs, debugging is back-and-forth: "what did the agent say?", "did it try to call the tool?", "was there silence?" — each question is a round trip.

The fix is logs that **tell the story**: every state transition, every decision branch, every message in and out, with enough context that a reader who wasn't there can reconstruct what happened. That reader is usually an LLM helping you debug.

The dream interaction:

> **You**: "I asked it to play a book, it said it would, but no audio came."
>
> **LLM (reading log)**: "I see — `coord.tool_dispatch` fired with `has_audio_stream=true`, then `coord.audio_stream_started` and `focus.acquire channel=content`, then `coord.audio_stream_ended cancelled=true` 200 ms later. Something cancelled the pump. Looking at `coord.interrupt` — none. Looking at `coord.session_disconnected` — yes. The voice provider session dropped, which cancelled the content stream. The auto-reconnect didn't restart playback because that's not its job. Want a 'resume in-progress book on auto-reconnect' feature?"

That's the goal. It only works if the log carries the right events with the right fields.

## Naming convention

Every log event uses a dotted namespace + direction prefix. This makes events `grep`-able, filterable, and identifiable at a glance.

| Namespace      | Source                                                  | Examples                                                                                                      |
| -------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `coord.*`      | TurnCoordinator decisions (the core state machine)      | `coord.ptt_start`, `coord.interrupt`, `coord.tool_dispatch`                                                   |
| `focus.*`      | FocusManager arbitration (acquire / release / change)   | `focus.acquire`, `focus.release`, `focus.change`                                                              |
| `background.*` | Supervised background-task lifecycle                    | `background.task_crashed`, `background.task_restarting`, `background.task_permanently_failed`                 |
| `session.rx.*` | Messages received from the voice provider (OpenAI)      | `session.rx.tool_call`, `session.rx.error`                                                                    |
| `session.tx.*` | Messages sent to the voice provider                     | `session.tx.commit`, `session.tx.cancel`, `session.tx.tool_output`, `session.tx.suspend`, `session.tx.resume` |
| `server.rx.*`  | Messages received from the audio client                 | `server.rx.ptt_start`, `server.rx.ptt_stop`, `server.rx.wake_word`                                            |
| `server.tx.*`  | Messages sent to the audio client                       | `server.tx.state`, `server.tx.model_speaking`                                                                 |
| `app.*`        | Application orchestration (lifecycle, guard rejections) | `app.session_end`, `app.ptt_rejected`                                                                         |
| `<skill>.*`    | Per-skill events (skill author owns the namespace)      | `audiobooks.resolve`, `audiobooks.stream_started`, `system.volume_set`                                        |
| `client.*`     | Telemetry forwarded from the audio client (web/ESP32)   | `client.silence_timer_started`, `client.thinking_tone_on`                                                     |

A skill named `audiobooks` emits events like `audiobooks.factory_built`, `audiobooks.stream_ended`. The framework reserves `coord.`, `focus.`, `background.`, `session.`, `server.`, `app.`, `client.`; everything else is skill territory.

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
coord.ptt_start            turn=abc12345  had_turn=false  had_media=false
coord.ptt_stop             turn=abc12345  frames=28  committed=true
session.tx.commit
coord.audio_start          turn=abc12345  state=in_response  owner=user
server.tx.model_speaking   value=true
session.rx.tool_call       name=play_audiobook  call_id=c1
coord.tool_dispatch        turn=abc12345  state=in_response  name=play_audiobook  has_audio_stream=true
audiobooks.factory_built   turn=abc12345  book_id=...  start_position=0.0
session.tx.tool_output     call_id=c1
coord.audio_done           turn=abc12345
server.tx.model_speaking   value=false
coord.response_done        turn=abc12345  state=in_response  follow_up=false  pending_audio_streams=1
coord.turn_ended           turn=abc12345
coord.audio_stream_started turn=abc12345  interface=turn.content.abc12345
focus.acquire              channel=content  interface=turn.content.abc12345  content_type=nonmixable  became_foreground=true
focus.change               channel=content  interface=turn.content.abc12345  new_state=foreground  behavior=primary
audiobooks.stream_started  turn=abc12345  path=...  start=0.0
server.tx.model_speaking   value=true
```

Read top to bottom: user pressed PTT (frame 28), coordinator committed, model started speaking ("ahí le pongo el libro"), called the play_audiobook tool, the audiobooks skill built a stream and announced it, model finished speaking, response done, the parent turn ended, the content stream's Activity was acquired on the FocusManager (CONTENT channel went FOREGROUND), pump spawned, first chunk acquired the FACTORY speaker.

Every event is grep-able. To filter to one turn: `grep "turn=abc12345"`. To see only coordinator decisions: `grep "^coord\."`. To see only focus arbitration: `grep "^focus\."`. To see what the agent sent the user: `grep "^server.tx\."`.

## Focus events — what they tell you

The `focus.*` namespace surfaces every move on the FocusManager's Activity stacks. These are mailbox-driven, so they appear slightly after the coordinator's `coord.*` event that triggered them (~5–50ms typical actor-loop processing).

| Event                    | When it fires                                                                                                                                | Key fields                                                                                                           |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `focus.acquire`          | An `Activity` was registered on a channel.                                                                                                   | `channel`, `interface`, `content_type`, `patience_ms`, `became_foreground`, `displaced`                              |
| `focus.release`          | An `Activity` was released by interface name.                                                                                                | `channel`, `interface`, `was_foreground`                                                                             |
| `focus.change`           | An observer was notified of a focus transition. Fires for every `(activity, focus_state, behavior)` delivered.                               | `channel`, `interface`, `new_state` (foreground/background/none), `behavior` (primary/may_duck/must_pause/must_stop) |
| `focus.duck_started`     | A MAY_DUCK `BACKGROUND` notification began the gain ramp on a `ContentStreamObserver`. Diagnostic for "is the duck envelope firing?"         | `interface`, `target_gain`, `duration_ms`                                                                            |
| `focus.patience_expired` | A backgrounded Activity's patience window elapsed without re-acquire — Activity is being cleared.                                            | `channel`, `interface`                                                                                               |
| `focus.observer_failed`  | An observer's `on_focus_changed` raised. Logged via `aexception` (full traceback). Other observers in the transition still get notified.     | `channel`, `interface`, `focus`, `behavior`                                                                          |
| `focus.observer_slow`    | An observer took more than 100ms to handle a notification. Likely a bug — observers should return fast and offload work to tasks.            | `interface`, `elapsed_ms`                                                                                            |
| `focus.event_failed`     | The actor loop's top-level handler caught an exception while processing an event. Should never happen; look here if focus state seems stuck. | `event_type`                                                                                                         |

A typical `inject_turn` preempting an audiobook:

```
timers.fired                  timer_id=1  message=...
coord.inject_turn             turn=fda08425  interface=turn.dialog.fda08425  prompt_len=56
focus.acquire                 channel=dialog  interface=turn.dialog.fda08425  content_type=nonmixable  became_foreground=true
audiobooks.stream_ended       book_id=...  cancelled=true  final_pos=22.4
focus.change                  channel=content  new_state=none  behavior=must_stop
focus.change                  channel=dialog  new_state=foreground  behavior=primary
session.tx.conversation_message text_len=56
session.tx.response_create
server.tx.model_speaking      value=false
coord.audio_done              turn=fda08425
coord.response_done           turn=fda08425  follow_up=false
coord.turn_ended              turn=fda08425
focus.release                 channel=dialog  interface=turn.dialog.fda08425  was_foreground=true
focus.change                  channel=dialog  new_state=none  behavior=must_stop
```

Read top to bottom: timer fired, coordinator created an injected turn and acquired DIALOG, FocusManager preempted the CONTENT activity (audiobook pump confirmed cancellation), DIALOG was promoted, prompt + request were sent to the LLM, model narrated, turn ended, DIALOG released cleanly.

If `coord.inject_turn` fires but no subsequent `focus.acquire channel=dialog` appears, the FocusManager actor is stuck — check for `focus.event_failed` or a long gap before the next `focus.*` event.

## Inject_turn queue events — diagnosing dropped or queued reminders

When `inject_turn` is called while a user or synthetic turn is in progress, the request goes onto a FIFO queue (Stage 1d). These events tell you why a reminder fired late, was deduped, or got dropped:

- **`coord.inject_turn_queued`** — request couldn't fire immediately; queued. Fields: `queue_depth`, `dedup_key`, `prev_state` (what turn was active).
- **`coord.inject_turn_dequeued`** — a queued request is firing now (drained at a turn-end with no pending content stream). Fields: `remaining`, `dedup_key`.
- **`coord.inject_turn_deduped`** — an enqueue replaced one or more same-key entries already in the queue. Fields: `dedup_key`, `removed`.
- **`coord.inject_turn_dropped`** — an enqueue was silently dropped because a same-key inject is currently firing. Fields: `reason=dedup_in_flight`, `dedup_key`.
- **`coord.inject_turn_preempted_content`** — a `PREEMPT`-priority inject drained at turn-end and displaced a pending content stream (the stream request is discarded). Fields: `remaining` (queue depth after pop), `dedup_key`, `dropped_streams` (count). Only fires for priority=PREEMPT; NORMAL always waits for a quiet moment instead.
- **`coord.inject_turn`** — the moment a request actually fires (whether immediate from `inject_turn` itself or drained from the queue). Fields: `interface`, `prompt_len`, `dedup_key`.

If a skill's `inject_turn` "didn't speak," look for these in order: `inject_turn_dropped` (dedup'd against in-flight), `inject_turn_queued` followed by `inject_turn_dequeued` after a delay (queued, drained later), or no events at all (caller never invoked it). If a PREEMPT reminder displaced a book, look for `inject_turn_preempted_content`.

## Background-task events — supervised lifecycle

The `background.*` namespace surfaces every `ctx.background_task(...)` lifecycle event. Use these to diagnose "my scheduler stopped firing" symptoms — was it cancelled by shutdown, did it crash and restart silently, did it exhaust its restart budget?

| Event                                             | When it fires                                                                                                     | Key fields                                                        |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `background.task_crashed`                         | A supervised task's coroutine raised. Logged via `aexception` (full traceback).                                   | `name`, `restart_count`, `will_restart`                           |
| `background.task_restarting`                      | Crash + `restart_on_crash=True` + within budget — restarting after exponential backoff.                           | `name`, `restart_count`, `backoff_s`                              |
| `background.task_permanently_failed`              | Restart budget exhausted (`max_restarts_per_hour` exceeded). The task is dropped from the supervisor.             | `name`, `restart_count`, `elapsed_in_window_s`, `exception_class` |
| `background.dev_event_failed`                     | The `dev_event("background_task_failed", ...)` post-failure notification itself raised.                           | `name`                                                            |
| `background.on_permanent_failure_callback_raised` | The skill-supplied `on_permanent_failure` callback raised. Doesn't recurse — supervisor logs and exits.           | `name`                                                            |
| `background.supervisor_stopped`                   | `TaskSupervisor.stop()` completed during framework shutdown. `cancelled` is the count of live tasks at stop time. | `cancelled`                                                       |

For one-shot tasks (timers, with `restart_on_crash=False`), a crash produces just `background.task_crashed will_restart=False` — no restart, no permanent failure event. The task quietly dies; the skill's `_handles` (or equivalent tracking) clears via the coroutine's own `finally` if it ran far enough, otherwise the supervisor's `stop()` cleans it up at shutdown.

## Timer persistence — restore events

The timers skill persists each pending timer via `ctx.storage` and enumerates them on `setup()` across a server restart (see [`docs/skills/timers.md`](./skills/timers.md#persistence-stage-3b)). Restore outcomes are observable via structured events:

| Event                            | When it fires                                                                                                             | Key fields                                                       |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `timers.restored`                | A pending entry was rescheduled via `ctx.background_task` with a future `fire_at`. Will fire on its original schedule.    | `timer_id`, `remaining_s`, `message`                             |
| `timers.restored_overdue`        | Restored entry's `fire_at` was already in the past but within the stale threshold. Fired 1 s after boot (crash recovery). | `timer_id`, `overdue_s`, `message`                               |
| `timers.restore_skipped_fired`   | Entry had `fired_at` set — crash between narration and delete. Dedup drop (no double-fire).                               | `timer_id`, `fired_at`                                           |
| `timers.restore_skipped_stale`   | `now − fire_at > stale_restore_threshold_s` (default 1 h, persona-overridable). Original intent is past; entry deleted.   | `timer_id`, `fire_at`, `age_s`                                   |
| `timers.restore_entry_malformed` | JSON or schema shape couldn't be parsed. Entry kept untouched (future migration opportunity).                             | `key`, `value` (truncated to 80 chars)                           |
| `timers.restore_key_malformed`   | Storage key didn't end in a numeric `timer:N` suffix. Entry kept untouched.                                               | `key`                                                            |
| `timers.stale_threshold_invalid` | Persona config's `stale_restore_threshold_s` wasn't a positive number. Default (1 h) kept.                                | `hint`, `value`                                                  |
| `timers.setup_complete`          | Skill initialization finished. Reports restore counts + the effective stale threshold so you can see "we picked up N."    | `restored`, `dropped`, `fire_prompt_source`, `stale_threshold_s` |

Diagnosing "my timer didn't fire after restart":

1. Grep for `timer_id=<N>` across the old process's log — was it ever scheduled (`timers.scheduled`)?
2. In the new process's log, look for `timers.setup_complete restored=…`. Was N in the restored count?
3. If dropped, look for `restore_skipped_*` events with that id — the reason line tells you why.
4. If restored but `inject_turn` never fired, jump to the [inject_turn queue events](#inject_turn-queue-events--diagnosing-dropped-or-queued-reminders) section — restore just re-enters the normal fire path.

## Telegram messaging events — diagnosing lost or stale messages

The `telegram.*` namespace covers both the call path (existing) and the messaging path (T1.11). Messaging-specific events:

| Event                                      | When it fires                                                                                                                                                   | Key fields                                  |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| `telegram.transport.inbound_message`       | Pyrogram `MessageHandler` matched a private incoming message (echo-filter passed). Always fires; downstream may drop.                                           | `user_id`, `sender_display`, `text_chars`   |
| `telegram.inbound.message_buffered`        | The skill accepted the message and appended it to the per-sender debounce buffer. Will trigger a flush after `debounce_seconds`.                                | `user_id`, `display`, `chars`               |
| `telegram.inbound.unknown_dropped`         | Sender's `user_id` isn't in `_user_id_to_name` and `inbound.unknown_messages == "drop"` (default). Includes `hint` field for promoting the contact.             | `user_id`, `sender_display`                 |
| `telegram.inbound.message_from_unknown`    | Same as above but `unknown_messages == "announce"` is set; the message is buffered as `"un número desconocido"`.                                                | `user_id`, `sender_display`                 |
| `telegram.inbound.flushing`                | The debounce timer fired and the coalesced burst is about to call `inject_turn`. `message_count` shows how many messages the LLM will hear in one announcement. | `user_id`, `display`, `message_count`       |
| `telegram.transport.fetch_unread_complete` | Backfill's Pyrogram pass finished walking dialogs.                                                                                                              | `messages`, `since_seconds`, `max_messages` |
| `telegram.backfill.skipped`                | Backfill no-op (no resolved contacts in reverse map).                                                                                                           | `reason`                                    |
| `telegram.backfill.no_unread`              | Backfill ran but found nothing within the window.                                                                                                               | (none)                                      |
| `telegram.backfill.injecting`              | Backfill is about to call `inject_turn` with the coalesced summary.                                                                                             | `total`, `per_sender_counts`                |
| `telegram.transport.sent_text`             | Outbound `send_message` reached Telegram successfully.                                                                                                          | `user_id`, `chars`                          |
| `telegram.send_failed`                     | Outbound failed (RPCError, timeout, etc.); LLM-facing Spanish error returned to the user.                                                                       | `name`                                      |

Diagnosing common failures:

1. **"I sent a message and Huxley didn't read it"** — look for `telegram.transport.inbound_message`. If absent, the MessageHandler isn't seeing it (check filter: `private & incoming` — outbound echoes are deliberately filtered out). If present but no `message_buffered` followed, sender was dropped — check for `unknown_dropped`.
2. **"My message was buffered but I never heard the announcement"** — look for `telegram.inbound.flushing`. If absent, the debounce timer never fired (check the buffer is alive, not closed by teardown). If `flushing` fires but no `coord.inject_turn` follows, the inject is being dropped — see [inject_turn queue events](#inject_turn-queue-events--diagnosing-dropped-or-queued-reminders).
3. **"Backfill fired but the LLM said 'I have a message' without reading it"** — the prompt wasn't an instruction to the LLM. Verify `text_len` on `session.tx.conversation_message` is large enough to include the message bodies; if it's short (< 100 chars), the prompt may be missing the body content.
4. **"Backfill on restart didn't fire at all"** — `_run_backfill` waits 5 s after `setup_complete` before injecting, so the OpenAI session has time to connect (otherwise the inject is lost — see the lessons in `docs/triage.md` T1.11). If you see `telegram.setup_complete` but never `telegram.backfill.injecting`, check whether the Pyrogram session re-authenticated mid-flight or the resolver returned no whitelisted user_ids.

## Skill failures

When a skill's `handle()` raises, the coordinator catches it (see `docs/triage.md` T1.6) and emits two events:

- `coord.tool_error` (structured log, with `exception_class`, `tool`, `args`, full traceback) — this is the diagnostic line.
- `tool_error` dev event to the browser/client — surfaces the failure in the UI for live observation.

The session does not die. A structured error `tool_output` is sent back to OpenAI with a Spanish apology hint, and the model produces an audible acknowledgement on the next response round. Look for `coord.tool_error` first when a tool call seems to disappear silently.

## When something looks wrong

The dev workflow:

1. **You** describe the symptom in conversation: "the book never started", "the tone played forever", "the model said it would but didn't".
2. **You** paste the relevant log section (from the symptom moment, plus a few seconds before and after).
3. **The LLM** reads the log and proposes a hypothesis. Often the bug is identifiable from the log alone.
4. **You** confirm or dispute, sometimes with one more log filter.
5. **The LLM** proposes a fix.

This works because every event in the log is structured, every decision is logged with its inputs, and the namespacing tells you instantly which component made which call. If a future bug isn't diagnosable from the log, the fix isn't just the bug — it's also adding the log line that would have caught it.
