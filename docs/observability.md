# Observability

> **The dev contract**: you describe a symptom in human language, the LLM developer reads the log, you find the bug together — without the LLM having to ask "what were you doing?"

This document defines what makes that contract possible: the structured event vocabulary every Huxley component emits, the namespacing convention, and what skill authors do to participate.

## Why this matters

Voice is hard to debug. The user can't paste a stack trace. The bug is often a timing issue, a state-context issue, or a "model didn't do what we expected." Without good logs, debugging is back-and-forth: "what did the agent say?", "did it try to call the tool?", "was there silence?" — each question is a round trip.

The fix is logs that **tell the story**: every state transition, every decision branch, every message in and out, with enough context that a reader who wasn't there can reconstruct what happened. That reader is usually an LLM helping you debug.

The dream interaction:

> **You**: "I asked it to play a book, it said it would, but no audio came."
>
> **LLM (reading log)**: "I see — `coord.tool_dispatch` fired with `has_audio_stream=true`, then `coord.audio_stream_started` and `focus.acquire channel=content`, then `coord.audio_stream_ended cancelled=true` 200 ms later. Something cancelled the pump. Looking at `coord.content_held` — none. Looking at `focus.acquire` — yes, `channel=dialog`: a reminder's `inject_turn` fired, and the book's Activity has `patience_ms=0`, so it was evicted rather than parked. Want the book to get a patience?"

That's the goal. It only works if the log carries the right events with the right fields.

## Naming convention

Every log event uses a dotted namespace + direction prefix. This makes events `grep`-able, filterable, and identifiable at a glance.

| Namespace      | Source                                                  | Examples                                                                                                                                            |
| -------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `coord.*`      | TurnCoordinator decisions (the core state machine)      | `coord.ptt_start`, `coord.interrupt`, `coord.tool_dispatch`                                                                                         |
| `focus.*`      | FocusManager arbitration (acquire / release / change)   | `focus.acquire`, `focus.release`, `focus.change`                                                                                                    |
| `background.*` | Supervised background-task lifecycle                    | `background.task_crashed`, `background.task_restarting`, `background.task_permanently_failed`                                                       |
| `session.rx.*` | Messages received from the voice provider (OpenAI)      | `session.rx.tool_call`, `session.rx.error`                                                                                                          |
| `session.tx.*` | Messages sent to the voice provider                     | `session.tx.commit`, `session.tx.cancel`, `session.tx.tool_output`, `session.tx.suspend`, `session.tx.resume`                                       |
| `server.rx.*`  | Messages received from the audio client                 | `server.rx.ptt_start`, `server.rx.ptt_stop`, `server.rx.wake_word`                                                                                  |
| `server.tx.*`  | Messages sent to the audio client                       | `server.tx.state`, `server.tx.model_speaking`, `server.tx.audio_stats` (once a second while audio flows: audio sent vs what the socket still holds; see [Opus audio](#opus-audio-the-walkies-wire-format)) |
| `app.*`        | Application orchestration (lifecycle, guard rejections) | `app.session_end`, `app.ptt_rejected`                                                                                                               |
| `recorder.*`   | The recorder (persona opt-in; see [Recordings](#recordings)) | `recorder.segment`, `recorder.write_failed`                                                                                                    |
| `<skill>.*`    | Per-skill events (skill author owns the namespace)      | `audiobooks.resolve`, `audiobooks.stream_started`, `system.volume_set`                                                                              |
| `client.*`     | Telemetry forwarded from the audio client (web/ESP32)   | `client.silence_timer_started`, `client.thinking_tone_on`                                                                                           |

A skill named `audiobooks` emits events like `audiobooks.factory_built`, `audiobooks.stream_ended`. The framework reserves `coord.`, `focus.`, `background.`, `session.`, `server.`, `app.`, `recorder.`, `client.`; everything else is skill territory.

The `client.*` events come in via the `client_event` WebSocket message type — clients emit `{"type": "client_event", "event": "<name>", "data": {...}}` and the server logs them as `client.<name>` with the data fields spread. A data field named like one of the log line's own keys (`event`, `level`, `timestamp`, `logger`, `exc_info`, `stack_info`, `conn_id`) is logged as `client_<key>`: the firmware's `huxley.firmware_log` carries the board's level (`W`, `E`, `I`) as `client_level`, while `level` stays the server's (`info`). Pure observability — the framework takes no action. This closes the "client-side blackbox" gap; thinking-tone state, silence-timer fires, and PTT UI transitions are now visible in the same log file as the server-side flow.

PWA client events worth knowing when debugging a phone (added 2026-10-10):

| Event                                                                                      | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client.device`                                                                            | Sent on every connection: `shell` (`native_app`, `home_screen` or `browser`) and `audio` (`native` plugin or `web`) say which client this is; user agent (iOS version), standalone vs Safari tab, viewport sizes (`inner`, `visual`, `lvh`/`dvh`/`svh`), safe-area insets. First stop for layout bugs. The ESP32 walkie sends `shell: firmware` with `board`, `version` (git hash of the build), `mac`, `reset` (why it last booted: `POWERON`, `PANIC`, `BROWNOUT`, ...), `boot_counter`, `rssi` and `volume`. |
| `client.ptt_press`                                                                         | Finger down on the orb, before any audio work. A press with nothing after it means the press got stuck client-side.                                                                                                                                                                                                                                                                                                                                                                                             |
| `client.mic_acquired` / `client.mic_acquire_failed`                                        | Stream obtained (`trigger=press` or `gesture`) or refused (`name=NotAllowedError` etc.).                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `client.mic_idle` / `client.mic_released`                                                  | Track muted between turns or while in the background / stream stopped (unmount).                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `client.ptt_released_during_mic_open`                                                      | Finger lifted while the mic was still opening; the press was cancelled cleanly.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `client.playback_suspended` / `client.playback_resumed` / `client.playback_resume_blocked` | Speaker context slept when idle / woke for new audio / a wake-up didn't take within 1.5 s (expected on the very first press of a launch; the finger lift unlocks it and queued audio then plays).                                                                                                                                                                                                                                                                                                               |
| `client.playback_pending_dropped`                                                          | Audio that had queued while the speaker was asleep was thrown away by a press. Should be rare; a reminder lost this way is a bug.                                                                                                                                                                                                                                                                                                                                                                               |
| `client.mic_ctx_not_running`                                                               | 1 s into a turn the mic context still isn't running, so frames aren't flowing.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `client.call_mic_recovered`                                                                | A call (skill-owned mic) got its mic back after starting without one (no gesture yet, or iOS ended the track in the background).                                                                                                                                                                                                                                                                                                                                                                                |
| `client.foreground_reconnect`                                                              | The app came back after `away_ms` and replaced a socket iOS may have killed.                                                                                                                                                                                                                                                                                                                                                                                                                                    |

The native iOS app (Capacitor, `clients/pwa/ios/`) records and plays through its own audio plugin, not the web view. Its mic events carry `native=True` and add detail:

| Event                                                                                                                          | Meaning                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client.mic_acquired` (native)                                                                                                 | Also `session_ms` (audio session switched on), `build_ms` (capture engine built), `start_ms` (engine started), `warm_session` / `warm_engine`. A warm press is `start_ms` only, ~5-15 ms. A cold one (both `warm_*` false) means the foreground warm-up didn't run or failed.                                                                                                                                     |
| `client.mic_released` (native, `reason=press_ended`)                                                                           | `flush_ms`: how long the release waited for the mic's last 100 ms block. Up to ~100 ms is normal; 400 is the deadline (the plugin didn't answer).                                                                                                                                                                                                                                                                 |
| `client.native_capture_summary`                                                                                                | One per press: hardware `in_rate` / `in_channels`, `chunks`, `in_frames`, `out_frames` (24 kHz), `peak` (0 to 32767), `drained` (false = the press was cut short by a call, the background, or a new press), `format_changes` (input format changed mid-press). A low `peak` (a few hundred) means the mic heard only room noise; `out_frames` far from `in_frames x 24000 / in_rate` means the conversion broke. |
| `client.mic_capture_ended`                                                                                                     | The plugin stopped recording on its own (`reason`: `interruption`, `background`, `route_change`, `media_services_reset`). A call reopens the mic on the next tap or return to the foreground.                                                                                                                                                                                                                     |
| `client.native_audio_warm` / `_warm_failed`                                                                                    | Session switched on and capture engine built ahead of presses (`reason`: `load`, `foreground`, `interruption_ended`). Events from `load` are usually lost (no socket yet).                                                                                                                                                                                                                                        |
| `client.native_capture_retry`                                                                                                  | A press found the capture engine stale (the route changed while idle) and rebuilt it once; `mic_acquired` then says `retried=True`.                                                                                                                                                                                                                                                                               |
| `client.native_audio_interruption`                                                                                             | iOS took the audio session (`type=began`, e.g. a phone call; the reply is dropped, not resumed later) or gave it back (`ended`).                                                                                                                                                                                                                                                                                  |
| `client.native_audio_engine_reset` / `client.native_capture_reset`                                                             | A route change (headphones, Bluetooth) stopped the playback / capture engine; it was restarted (`pending`, `loop`, `recording` say what was running).                                                                                                                                                                                                                                                             |
| `client.native_playback_watchdog`                                                                                              | Queued reply audio never reported finishing, so the plugin ended the reply a second after it should have. Means the engine dropped buffers silently; worth a look if it repeats.                                                                                                                                                                                                                                  |
| `client.native_play_failed` / `native_audio_restart_failed` / `native_audio_deactivate_failed` / `native_media_services_reset` | The plugin couldn't play a chunk / restart after a route change / release the session in the background / the iOS audio daemon restarted.                                                                                                                                                                                                                                                                         |

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
coord.ptt_stop             turn=abc12345  frames=28  audio_ms=149  committed=true
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

## Press levels — why a press didn't commit

**`coord.wait_cue_start`** (`reason` reply / tool, `playhead_remaining_ms`, `waited_ms`) and **`coord.wait_cue_stop`** (`reason`: speech, press, interrupt, sound, turn_end, new_turn, session_down, cap; `blips`, `cue_ms`) bracket the waiting sound (T1.41); no start line means the wait never reached the threshold. **`coord.tool_result_stale`** (`name`, `cancelled`): a tool finished after a press had already started a new turn, and its result was dropped.

`coord.ptt_stop` carries `peak_dbfs`, `rms_dbfs` and `window_dbfs` (the loudest 250 ms) for every press (measured without its first 100 ms and last 60 ms, which hold the button's press and release clicks), and `reason` (`too_short` or `silent`) when it wasn't committed. A silent press also logs **`coord.press_silent`** with `audio_ms`, `measured_ms`, the levels, `threshold_dbfs` and `why`: `peak` (its loudest sample under -40 dBFS) or `quiet` (averaging under -50 dBFS with no 250 ms reaching -38: room noise with a click in it). Reference levels on 2026-10-12: the walkie with nobody talking peaks at -56 dBFS, speech at -7 to 0 dBFS. A real question dropped as silent would show here with a peak just under the threshold.

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

## Content-hold events — why the music paused, came back, or didn't

A press pauses what plays and the turn's end resumes it; no client pauses it too ([ADR 2026-10-12](./decisions.md#2026-10-12-a-press-pauses-content-the-turns-end-resumes-it-t130)). The story of one pause:

- **`coord.content_held`**: the stream was paused. Fields: `reason` (`turn` = a press, `no_client`, `session` = a press while the voice session was down for 5 s or less, i.e. the planned refresh, `inject_queue` = a proactive turn is still playing and another waits behind it, T1.44), `was_playing`, `holds` (every reason in force).
- **`coord.content_resume_scheduled`** — the turn ended; the resume waits `delay_ms` for the client to finish playing the answer.
- **`coord.content_released`** — a hold was dropped. `resumed=true` means `factory()` was called again; `false` with `holds` non-empty means another reason still holds it, or focus is elsewhere (a call, an injected turn).
- **`coord.content_resumed`** — a pump actually restarted (any path, including FocusManager parking).
- **`coord.content_paused`**: an injected turn or a call took the speaker from a playing stream (focus, not a hold). `stopped=false`: parked, it comes back (then `coord.content_resumed`); `stopped=true`: evicted for good (the stream had no patience). Either way the client gets `stream_ended` (`interrupted`), so the walkie stops showing it as playing and the recorder files the reminder's voice as the model's.
- **`coord.streams_overruled`**: the press said only stop words but the model's tool started media anyway (2026-10-12: "Pausa la música" -> `music_control(resume)`): the turn's new stream is dropped (`dropped`), or stopped once a late transcript arrives (`late=true`, within 3 s). `heard` is the transcript.
- **`coord.content_stopped`** — the stream was ended instead of resumed. `reason`: `stop_words` (the press said only stop words; `heard` is the transcript), `parked_too_long` (`held_s`), `ptt_without_session` (a press over 5 s into a voice-session outage; `down_s`).
- **`coord.ptt_rejected_session_back`**: the app rejected a press, but the session was back by the time the coordinator saw it; what plays is left alone (the press still beeps).
- **`coord.session_connected`**: the voice session is up again; fires the head of the inject queue, then releases a `session` hold. Fields: `connected` (false: it dropped again before this ran, nothing happens), `queued_injects`, `content`, `holds`.
- **`coord.client_presence`** — a client became current (`present=true`) or the last one left. The `no_client` hold follows 5 s later.
- **`coord.content_held_note`** — the model was told, before the commit, that what played is paused.

"I said 'para' and the music came back": look for `coord.content_released resumed=true` after that turn, then at the turn's `coord.tool_dispatch` (no stop tool called) and the user transcript (not only stop words, so the backstop left it to the model).

## Inject_turn queue events — diagnosing dropped or queued reminders

An injected turn is spoken only when a client can hear it (T1.44, [turns.md](./turns.md#proactive-turns-wait-for-a-listener-t144)). It waits on a FIFO queue while there is no listener (a client whose hello is out), no voice session, a turn in progress, a live claim (except PREEMPT) or an earlier injected turn still playing, and counts as delivered once the client it was said to has played it and answered a ping. These events tell you why a reminder was said late, twice, or not at all:

- **`coord.inject_turn_queued`**: request couldn't fire immediately; queued. Fields: `reason` (`turn_in_progress`, `claim_live`, `no_session`, `no_client`, `awaiting_playback` (the previous injected turn is still playing), `behind_queue`), `queue_depth`, `dedup_key`, `priority`, `prev_state` (what turn was active), `expires_in_s` (null: waits until heard).
- **`coord.inject_turn_dequeued`**: a queued request is firing now. Fields: `trigger` (`turn_end`, `client_present`, `session_connected`, `playback_confirmed`, `claim_ended`, `cut_off`, `undelivered`, `press_dropped`, `inject`), `remaining`, `dedup_key`, `priority`, `waited_s`, `attempt` (2 and up: said again after a cut-off; the model gets the `inject_repeat_note` first).
- **`coord.inject_turn_fire_deferred`**: a request was about to fire but something changed meanwhile: back at the head of the queue. Fields: `reason`, `dedup_key`.
- **`coord.inject_turn_superseded`**: a press or a disconnect ended the turn while it was taking DIALOG focus; no prompt was sent (its request was settled by the press or the cut-off). Fields: `dedup_key`, `interface`.
- **`coord.inject_turn_fire_failed`** (warning): sending the prompt failed (the session closed under it); the turn was ended and the request put back. Fields: `dedup_key`, `error`, `requeued`.
- **`coord.inject_turn`**: the moment a request actually fires (whether immediate from `inject_turn` itself or drained from the queue). Fields: `interface`, `prompt_len`, `dedup_key`.
- **`coord.inject_turn_delivered`**: heard. `by` = `pong` (the client played it and answered a ping), `press` (the user pressed while it played), `presence` (no server wired: tests), `close_played` (the persona stopped after the client played it). Fields: `dedup_key`, `waited_s` (since the call), `playback_ms`, `attempts`, `epoch`.
- **`coord.inject_turn_undelivered`**: said, but not heard whole; back to the head of the queue (`requeued`, false if it expired meanwhile). `reason` = `client_left` (left or replaced before the confirmation), `no_pong` (the ping failed or timed out: the server aborts that connection), `press_before_audio` (a press emptied the client's buffer before it started playing), `silent` (the response said nothing and ran no tool; fired again), `confirm_error` (the confirmation raised: **`coord.inject_confirm_failed`** has the traceback). **`coord.inject_confirm_backstop_timeout`** (warning): the server's ping didn't return within its own bound plus 5 s. Fields: `dedup_key`, `epoch`, `listener`.
- **`coord.inject_turn_cut_off`**: a turn being said was cut short and goes back to the head of the queue. `reason` = `client_left`, `session_down` (the voice session dropped mid-turn), `interrupt` (a reset). Fields: `dedup_key`, `requeued`.
- **`coord.inject_turn_expired`**: its `expires_after` ran out before it could be heard; never said (again). Fields: `where` (`queue`: the timer fired while it waited; `dequeue`; or the cut-off reason), `dedup_key`, `priority`, `waited_s`, `attempts`, `listener` (null: nobody was connected).
- **`coord.inject_turn_withdrawn`**: the skill cancelled its `inject_turn_and_wait` while the turn waited (an ack, a cancelled call). Fields: `dedup_key`, `waited_s`.
- **`coord.inject_turn_deduped`**: an enqueue replaced a same-key entry already in the queue, in its place (the replaced one resolves DROPPED). Fields: `dedup_key`, `removed`.
- **`coord.inject_turn_dropped`**: never said: `reason=dedup_in_flight` (a same-key turn is being said or awaits its confirmation), `shutdown` (the persona stopped; `count`, `dedup_keys`; or a request made after it stopped), `too_many_attempts` (warning; fired 5 times without being heard). Fields: `dedup_key`.
- **`coord.inject_turn_preempted_content`**: a `PREEMPT`-priority inject drained at turn-end and displaced a pending content stream (the stream request is discarded). Fields: `remaining` (queue depth after pop), `dedup_key`, `dropped_streams` (count). Only fires for priority=PREEMPT; NORMAL always waits for a quiet moment instead.
- **`coord.inject_and_wait_cannot_wait`** (warning): `inject_turn_and_wait` was awaited in a tool handler or a skill's setup/reconfigure, where waiting would deadlock; queued, returned `QUEUED`. Fields: `dedup_key`.
- **`coord.inject_drain_failed`** (traceback): a drain scheduled after a claim's end raised.
- **`server.client_ping`**: the confirming ping. `ok`, `rtt_ms`, `conn_id`, `shell` (which client heard it: `firmware`, `browser`, ...); or `reason` = `not_current` (that connection is gone), `closed`, `timeout` (warning; no pong in 15 s, `action=abort`: the connection is cut so the walkie reconnects). **`server.evicted_close_aborted`**: an evicted socket didn't answer the close in 2 s and was aborted.

If a skill's `inject_turn` "didn't speak," look for these in order: `inject_turn_queued reason=no_client` with no `client_presence present=true` after it (nobody connected: `inject_turn_expired` closes the story), `inject_turn_dropped` (dedup'd against in-flight), `inject_turn_queued` followed by `inject_turn_dequeued` after a delay (queued, drained later), or no events at all (caller never invoked it). "It said it twice": an `inject_turn_undelivered` or `inject_turn_cut_off` between the two, and its `reason`. If a PREEMPT reminder displaced a book, look for `inject_turn_preempted_content`. `/health` shows `injects_waiting` and `oldest_inject_wait_s`.

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

## Skill schema versions — pin the upgrade path

T1.14's `data_schema_version` mechanism emits three events from the runtime, all under the `skill.schema.*` prefix:

| Event                                   | When                                                                                                                                                | Severity |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| `skill.schema.upgrade_needed`           | Skill's declared `data_schema_version` is greater than what's stored in `schema_meta` for this persona.                                             | warning  |
| `skill.schema.downgrade_detected`       | Declared is less than stored — the persona has data from a newer skill version than what's currently installed.                                     | warning  |
| `skill.schema.invalid_declared_version` | The skill declared a non-numeric value for `data_schema_version`. Author bug; runtime falls back to default `1` and skips the check for that skill. | error    |

All three carry `skill=<name>`, `declared=<int>`, `stored=<int>` (where applicable). v1 does NOT auto-migrate — the warning is the operator's signal to read the skill's CHANGELOG and apply migration steps.

**Equal-version is silent.** T1.13's hot persona swap calls `setup()` on every reconnect; emitting a heartbeat event would create per-swap log noise. If you see `skill.schema.*` events after the first boot of a (skill, persona) pair, something regressed — see [`docs/skill-marketplace.md` § Schema versioning](./skill-marketplace.md#schema-versioning-data_schema_version).

## Skill failures

When a skill's `handle()` raises, the coordinator catches it (see `docs/triage.md` T1.6) and emits two events:

- `coord.tool_error` (structured log, with `exception_class`, `tool`, `args`, full traceback) — this is the diagnostic line.
- `tool_error` dev event to the browser/client — surfaces the failure in the UI for live observation.

The session does not die. A structured error `tool_output` is sent back to OpenAI with a Spanish apology hint, and the model produces an audible acknowledgement on the next response round. Look for `coord.tool_error` first when a tool call seems to disappear silently.

When a skill's `setup()` raises, the persona still starts without that skill:

- `skill.setup_failed` (error, with traceback): `skill`, `exception_class`, `error`, `persona`, `cancelled_tasks` (supervised tasks its setup had started, now cancelled). The skill is disabled and torn down right away (a raising teardown logs `skill.teardown_failed`).
- `huxley_app_ready` lists the working skills in `skills` and the disabled ones in `disabled_skills`; `tools` still includes the disabled skills' tools.
- A later call to one of those tools logs `coord.tool_error` with `exception_class=SkillUnavailableError` and gets the same spoken apology as any failing tool.

A disabled skill stays disabled until the persona reloads or the server restarts. If a user says "it couldn't call" after a restart, grep `skill.setup_failed` in that run's log first.

## Startup, restarts and reloads

| Event                                | When                                                                                                                                                                                             | Key fields                                 |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| `runtime.instance_lock_acquired`     | Second line of every run. The server took `logs/huxley-<port>.lock`. `previous_pid` set means it had to wait for an older server on the same port to exit; the wait itself is printed to stderr. | `path`, `pid`, `waited_ms`, `previous_pid` |
| `runtime.shutdown_timeout`           | Shutdown (listener, app, interpreter exit) took longer than 20 s; a timer thread exits 1 without finishing, or finishes a requested restart, so the next server can start.                       | `timeout_s`, `restart`                     |
| `runtime.instance_lock_unavailable`  | The lock file could not be created or locked (read-only directory, no `flock`); the server runs without the one-server-per-port guard.                                                           | `path`, `error`                            |
| `runtime.reload_stopping_old_app`    | A same-persona reload (skill config or secret edited from the PWA) is stopping the running app before starting the new one.                                                                      | `persona`                                  |
| `runtime.reload_restored_previous`   | The reloaded app failed to start; the persona is running again as it was before the edit.                                                                                                        | `persona`                                  |
| `runtime.reload_restore_failed`      | That restore failed too; no app is loaded until the next connect. The next hello says `session_ready=false`.                                                                                     | `persona`                                  |
| `runtime.teardown_timeout_abandoned` | A previous app's teardown outlived its bound (10 s for a swap, 20 s for a reload); the swap or reload went ahead anyway.                                                                         | `what` (`previous_swap`, `reload_old_app`) |
| `huxley_ready` | The listener is about to start. Says whether the persona starts now or on the first connect, and which code is running. | `persona`, `persona_at_boot`, `commit`, `server` |
| `runtime.persona_boot_started` | `HUXLEY_PERSONA_AT_BOOT`: the configured persona is starting with no client. | `persona` |
| `runtime.persona_boot_ready` | It is running. `provider_connected=false` means the voice provider connect failed (`connection_failed` just before); the reconnect loop retries it (`app.reconnect_scheduled trigger=connect_failed`), and so does a client's `wake_word`. | `persona`, `provider_connected`, `client_connected`, `elapsed_ms` |
| `app.reconnect_scheduled` | The provider reconnect loop starts (1, 3, 10, 30 s, then every 60 s; a double beep before each attempt from the fourth). `trigger` = `session_end` (a session dropped) or `connect_failed` (a connect at boot or after a `wake_word` failed; T1.44: it used to wait for the next `wake_word`). Then `app.reconnect_attempt` / `app.reconnect_failed` / `app.reconnect_succeeded`. | `trigger` |
| `app.reconnect_already_running` | A failed connect found the loop already running (the loop's own attempt, or a `wake_word` between attempts); no second loop. | `trigger` |
| `runtime.persona_boot_failed` | (error, traceback) It did not start. The server stays up with no persona (`/health` says `persona: null`); the next client connect retries through the lazy path. | `persona`, `error`, `elapsed_ms` |
| `runtime.persona_boot_skipped` | (warning) `HUXLEY_PERSONA_AT_BOOT` is set but `HUXLEY_PERSONA` is not (`reason=no_persona_configured`; boot is lazy), or shutdown began before the boot ran (`shutting_down`). | `reason` |
| `runtime.connect_waits_for_persona_boot` | A client connected while the boot was running; its hello waits for the boot instead of starting a second app. | `requested` |
| `runtime.persona_boot_abandoned` | (warning) Shutdown came during the boot and the boot did not finish within 10 s; it was cancelled. | `timeout_s` |
| `runtime.persona_start_cancelled` | (warning) A persona start was cancelled midway (the line above); what it had opened was shut down. | `persona` |
| `runtime.persona_select_skipped` | A client connected during shutdown (or was waiting on the boot when it began); no persona is started for it. | `reason`, `requested` |

A server that gives up waiting for the old one (30 s) prints `[huxley] not starting: ...` to stderr and exits 1; under launchd that line is in `huxley.stderr.log` and KeepAlive starts it again later. It does not rotate the log first, so the stuck process's log is kept.

### `/health`

`GET /health` ([protocol](./protocol.md#http-health)) logs only what goes wrong: **`server.health_refused`** (warning) when a source other than loopback or Tailscale asked, or the request came through a reverse proxy (answered 404; `remote`, `reason` = `source` or `proxied`, `header`), **`server.health_failed`** (error, traceback) when building the answer raised (answered 500). A served request is **`server.health`** at debug (`remote`, `ok`): the watcher polls every few minutes. `injects_waiting` / `oldest_inject_wait_s` count the proactive turns waiting for a listener (T1.44): a reminder nobody has heard for a while is worth a caregiver alert.

## Voice provider session: responses, refresh, close

What the OpenAI provider sends and gets back around each response (added 2026-10-12):

- **`session.tx.response_create`**: a response was requested. `generation` is the cancel count stamped in the response's metadata; `deferred_ms` is present when the request was held back.
- **`session.tx.response_create_deferred`**: a response was requested while `active_response_id` was still live (usually the one an interrupt just cancelled). Sent when that response's `response.done` arrives. OpenAI rejects a second live response (`conversation_already_has_active_response`), which left three presses unanswered on 2026-10-12.
- **`session.tx.response_create_forced`** (warning): a held-back request waited 5 s and its `response.done` never came; sent anyway. Means the provider's response bookkeeping missed an event.
- **`session.rx.stale_done_forwarded`**: a call (`suspend()`) dropped a held-back request, so the cancelled response's `response.done` was passed to the coordinator to close the waiting turn.
- **`session.rx.metadata_rejected`** (error): OpenAI refused `response.metadata`; the provider stops sending it and stale detection falls back to the response live at each cancel.
- **`session.tx.cancel`**: `active_response_id` (what was cancelled, if known) and `dropped_deferred_create`.
- **`session.rx.response_created`** / **`session.rx.response_done`**: `response_id`, `status` (`completed`, `cancelled`, ...), `stale`. A stale response is one cancelled by an interrupt: its remaining events (audio, tool calls, `response.done`) never reach the coordinator, logged as **`session.rx.stale_dropped`** (audio at debug). Its late `response.done` used to end the next press's turn.
- **`session.rx.error code=conversation_already_has_active_response`**: should not happen any more; `will_retry=true` means the request is sent again when the live response finishes (once).
- **`session.tx.tool_output`**: `call_id`, `output` (first 300 characters) and `output_len`.
- **`session.refresh_due`** / **`session.refresh`**: OpenAI ends a session at 60 minutes. After `HUXLEY_SESSION_REFRESH_MINUTES` (default 50) the provider reconnects at the first idle moment. `refresh_due` says what it is waiting for (`input_claimed` (a call holds the mic; a disconnect would hang it up), `response_active`, `response_pending`, `user_audio`, `reply_playing`, `recent_traffic`); It waits for 10 s without traffic and 10 s after the last reply finished playing, then computes the summary with the session still up (`session.summary_timeout` if that took over 10 s; the raw transcript tail is used). `refresh` is the disconnect, followed by the usual `app.session_end` and auto-reconnect. A `session.rx.error code=session_expired` means no idle moment came in time.
- **`session_connection_closed`**: the WebSocket closed under us: `code` and `reason` from OpenAI's close frame, `sent_code`, and `age_s` (session age).

## Session readiness — diagnosing "told ready when not"

Three runtime-level events bracket every WS connection's session-readiness lifecycle. They were added in T1.16 after a class of bugs where the server emitted a normal-looking hello + `state: IDLE` even when `_shim_persona_select` had silently failed — leaving the PWA showing "Hold to talk" while every PTT silently dropped because `current_app is None`.

- `runtime.session_ready` (info) — fired after a successful `_shim_persona_select`. Always carries `persona=<basename>`. The next hello will set `session_ready=true`.
- `runtime.session_failed` (warning) — fired when lazy-boot persona-select raised, or when no persona could be picked at all. Carries `reason=<lazy_boot_swap_failed | no_persona_pickable>`, `persona=<requested>`, `error=<short class+msg>`. The next hello will set `session_ready=false` and `session_error=<human string>`.
- `runtime.session_degraded` (warning) — fired when a persona swap raised mid-session but the previous `current_app` is still serving. The hello stays `session_ready=true`; the user sees a `status` frame ("Could not switch to 'foo' — staying on current persona"). Useful for catching a click-through pattern where users repeatedly try to load a broken persona.

When triaging a "PWA looks ready but PTT does nothing" report, grep for these three events around the failed connect timestamp:

```
grep -E "runtime\.session_(ready|failed|degraded)" /private/tmp/huxley_server.log
```

If you see `session_failed` followed by no `session_ready`, the server is in a degraded-boot state — the PWA's orb hint will show the `session_error` string, but the operator-facing log carries the full traceback (always logged via `runtime.lazy_boot.first_swap_failed` or similar in the same connect block).

## Opus audio: the walkie's wire format

A client that connects with `?audio=opus` gets binary Opus packets instead of base64 PCM ([protocol](./protocol.md#opus-audio-audioopus)). The events that say what each connection got and where its audio went:

- **`client_connected`** carries `audio_format=pcm|opus`: what this connection speaks, decided once at connect. `diag_state` shows the same per connection.
- **`server.audio.opus_unavailable`** (warning): libopus could not be loaded (`reason`), so an opus client got PCM (`fallback=pcm`, and its hello has no `audio_params`). Logged at boot (`audio_server_listening` also says `opus_available`) and on each such connect. Fix: `brew install opus` / `apt install libopus0`, restart.
- **`server.audio.format_unsupported`** (warning): `?audio=` held something other than `opus` or `pcm`; the client got PCM.
- **`server.tx.audio_stats`**: `audio_ms` is always PCM duration handed to `send_audio`, whatever the encoding; `bytes_sent` is what went on the wire for it and `sends` the WebSocket messages; `audio_format` says which. PCM runs about 64 KB a second of audio, Opus about 4 KB (one ~240-byte packet per 60 ms).
- **`server.tx.opus_flush`** (info): a sound's last partial frame went out, `tail_ms` of audio plus `pad_ms` of silence. `reason=model_speaking` (reply over) or `stream_ended` is the normal end of a sound; `reason=idle` means no PCM came for 250 ms outside a stream (an earcon's end). No idle timer runs while a stream is announced.
- **`server.tx.opus_clear`** (debug): an `audio_clear` dropped `dropped_packets` not yet written (the link was backed up) and `dropped_pcm_ms` of pending PCM, and reset the encoder.
- **`server.tx.opus_cancelled`** (debug): the task sending a sound was cancelled (barge-in, a pause) while writing it; its `dropped_packets` and `dropped_pcm_ms` were dropped and the encoder reset, as a cancelled PCM send's remaining chunks never go out.
- **`server.tx.opus_encode_failed`** / **`server.rx.opus_decode_failed`** (warning): libopus refused a frame. The encoder drops its pending audio and restarts; a mic packet that fails to decode is dropped (`packet_bytes`, `error`, running `errors` count) and the connection stays up. Repeated decode failures mean the client's encoder does not match `audio_params`.
- **`server.tx.opus_before_hello_dropped`** (debug): audio for a connection whose hello was not out yet (a reconnect while something played) was dropped, so the client never sees binary before the hello that announces it.

## Recordings

A persona with `recording: {enabled: true}` ([persona fields](./personas/README.md#recording)) keeps every voice that crossed the wire with an Opus client as an Ogg Opus file, and everything else its speaker played as log lines (T1.43, [remote-operation W3](./remote-operation.md#w3-recording)). Nothing else is stored: no database, no transcripts beyond the ones already logged.

What is recorded, from the two points where Opus crosses the wire (`huxley.server.recorder`, tapped in `AudioServer`):

- **Speaker**: each packet as it was written to the socket. Audio queued and then dropped (by `audio_clear`, or a cancelled sender) never reached the client and is not in the file.
- **Mic**: each packet the client sent, as it arrived, before decoding or any gating. A press too short or too quiet to commit, and a packet that fails to decode, are kept.
- PCM clients (the PWA) are not recorded (`recorder.skipped_pcm_client`, once per connection).

The speaker's **source** comes from the control frames the client got, in this order: `claim` (between `claim_started` and `claim_ended`: the claim's speaker audio, e.g. the other side of a conversation a skill holds), `stream` (between `stream_started` and `stream_ended`), `model` (while `model_speaking` is true), else `sound` (earcons, the waiting cue, tones). Files are written only for voices: `mic` (one per press, one per claim), `model` (one per `model_speaking` span) and `claim` (one per claim). `stream` and `sound` segments are timeline lines only, and so are mic packets outside a press or claim (the few a client still sends before it hears a claim ended).

The persona's recorder starts with its app and is handed to the server by the runtime when that persona becomes current (boot, swap, reload); the previous persona's recorder closes what it had open right then (`end_reason=recorder_replaced`) and stops when that persona has shut down.

| Event                            | When                                                                                                                                                                       | Key fields                                                                                    |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `recorder.started` / `.stopped`  | The persona's recorder came up (app start) / finished (shutdown, swap, reload: open segments closed, files renamed, thread stopped).                                       | `path`, `max_queued_packets` / `segments`, `files`, `dropped`, `unattributed_drops`           |
| `recorder.segment`               | A segment ended. One line per file, and one per stream or sound stretch.                                                                                                   | see below                                                                                     |
| `recorder.write_failed` (warn)   | A disk operation failed (`op`: `open`, `write`, `close`, `rename`, `discard`, `start`, `stop`, `handle`). Audio is never affected; the segment goes on as a timeline line. | `op`, `error`, `path`, `source`, `conn_id`                                                    |
| `recorder.skipped_pcm_client`    | A PCM client connected while recording is on.                                                                                                                              | `conn_id`, `audio_format`                                                                     |
| `server.tx.audio_dropped_no_client` | Audio was sent with no client connected (it goes nowhere). First one at once, then at most every 5 s, plus what is pending when a client connects. Logged recording or not. | `count` (`send_audio` calls), `audio_ms`                                                  |

`recorder.segment` fields: `source` (`mic`, `model`, `stream`, `claim`, `sound`), `label` (the stream's label, or the claim's title, falling back to its skill; mic segments during a claim carry the claim's), `start` / `end` (ISO UTC, wall clock of the first and last packet), `duration_ms` (sum of the packets' own durations, read from each TOC byte), `packets`, `bytes`, `path` (relative to the persona's data dir; `null` for timeline-only segments), `end_reason`, `cut_by_clear`, `dropped` (packets the writer queue had no room for), `conn_id`; plus `stream_id` / `claim_id` when set, `invalid` (packets that are not valid Opus: counted, not written), `retagged_from` with `retagged_ms` (how much audio the retag moved) and `error` when they apply.

`end_reason`: `model_done` (`model_speaking` false), the frame's own reason for streams and claims (`natural`, `interrupted`, `user_ptt`, `preempted`, ...), `ptt_start` / `ptt_stop` (mic), `audio_clear` (with `cut_by_clear=true`), `next_source` (something else took the speaker), `idle` (a sound, or mic packets outside a press or claim, with nothing for 2 s), `disconnect`, `evicted`, `recorder_replaced` (a swap), `shutdown`.

**Finding a segment's file**: `<persona dir>/data/<path>`, e.g. `server/personas/abuelos/data/recordings/2026/10/12/153000-ab12cd34-003-model.ogg`. Names are `HHMMSS-<conn_id>-<seq>-<source>[-<label>].ogg` in UTC, so a directory listing is a timeline and `conn_id` matches every other log line of that connection. A `.ogg.part` is a file still being written, or one a crash cut short (playable up to its last second). To list a day's voices:

```
grep '"recorder.segment"' logs/huxley.log | jq -r 'select(.path) | "\(.start) \(.source) \(.label // "") \(.duration_ms)ms \(.path)"'
```

**One correction the source inference makes**: a stream's or claim's first packets can be written a few ms before its `stream_started` / `claim_started` (the pump runs while the coordinator announces it; `model_speaking` is already true for them, as it is for anything that owns the speaker). A `model` segment younger than 0.5 s and holding at most 1 s of audio when a stream or claim starts is taken as that stream's or claim's start: `retagged_from=model`, its file dropped for a stream, kept and named for a claim. (The audio cap keeps a short answer that was written faster than real time from being taken for a stream's first packets.)

**A known gap**: when something parks a live stream (an injected turn, a claim) and the model then speaks, the coordinator has sent no `stream_ended` (the client too is told the stream still plays), so that speech is logged as `source=stream` with no file. A reminder spoken over a book is the common case. The frames cannot tell it from the stream resuming; the fix is in the coordinator: announce the park as `stream_ended` and the resume as `stream_started` ([remote-operation W3](./remote-operation.md#w3-recording)).

**Not in v1**: turn ids on `transcript` and cost lines (to join a file to its transcript, match times); what the walkie actually played (the file is what was sent; its discarded ms on `audio_clear` are not reported yet); call transcription.

## What huxley-watch reads

[`huxley-watch`](../server/watch/README.md) (T1.42) turns this log into the caregiver's page and alerts, so renaming or dropping one of these events breaks a card or a chart silently: change `server/watch/` in the same commit. It reads:

- **Walkie online**: `client_connected`, `client.device` (`shell=firmware`, `mac`, `version`, `rssi`), `client_disconnected`, `client_evicted`. A new process (`huxley_starting`) ends every connection at the last line heard from it; `huxley_stopped` does not (a persona swap or reload logs it while the socket stays up). With no `client.huxley.firmware_log` line from its connection for 10 minutes, the walkie counts as offline. Overlapping connections are merged on the day strip, never summed.
- **Audio cutting up**: the board's `spk.alive` chunk counter and `spk.underrun silent_ms` (as `dev.py link`), its `info ... rssi=` line.
- **Server**: `huxley_starting`, `huxley_shutting_down`, `huxley_stopped`, and `huxley_app_ready` / `runtime.persona_swap_committed` (after a stop: a swap or reload, still running); a start with no stop since the previous one is unclean. The pid in `logs/huxley-<port>.lock` says whether the process is there. `skill.setup_failed` and `huxley_app_ready.disabled_skills` after the last start, persona swap or reload.
- **OpenAI**: `connection_failed` (the traceback's last line: `insufficient_quota` means credit; an `HTTP 429` alone is a rate limit) and `session_connected`; `session.rx.error code=insufficient_quota|billing_hard_limit_reached`; `cost.response_done` (today's spend, spend against the balance Mario enters; a billed answer clears a quota alert); `cost.threshold_crossed tier=bug_canary|kill_switch` and `cost.kill_switch_triggered` today.
- **What the speaker played**: `server.tx.stream_started` / `server.tx.stream_ended` paired by `stream_id` (a `stream_started` said again for a stream already open, when the client comes back, changes nothing; an `ended` then `started` with the same id is a pause, `coord.content_paused`), the kind from the skill line logged right after the start (`spotify.play_started` with a `spotify:show:` context is a book, other contexts music; `radio.stream_started`; `audiobooks.stream_started`); calls from `server.tx.claim_started` (`title` = the contact) / `server.tx.claim_ended`.
- **Him and Huxley**: `coord.ptt_start` / `coord.ptt_stop` (a press; only those whose `conn_id` is the walkie's), an answer from `coord.audio_start` to the next `server.tx.model_speaking` false (the flag alone is not an answer: a stream's or a claim's pump raises it too, for the whole song; neither carries the walkie's `conn_id`, so answers, like streams and calls, count only while the walkie was connected).
- **Books**: `spotify.start_refused cause=book_no_hours`, `spotify.book_hours_out`, `spotify.stream_ended` (`book`, `audio_ms`, `reason`); `spotify:book:seconds:<YYYY-MM>` in the persona DB.
- **Spotify and Telegram up**: `spotify.ws_state`, `spotify.auth_state`, `spotify.unavailable reason=player_not_paired`; `telegram.credentials_missing`, `telegram.transport.connected`.
- **Test windows**: `logs/bench.jsonl`, written by `clients/firmware/tools/bench.py` and `dev.py` (`start`, `end`, `what`): "link test ..." windows silence the offline and gap alerts; the rest also leave their activity out of use.
- **The archive** (one day, when the page asks), each line a row at its own time: `transcript` (`role`, `text`; no turn, so not joined to a recording), `session.rx.tool_call` (`name`, `args`), `recorder.segment` (`source`, `start`, `path`, `duration_ms`, `conn_id`, `claim_id`, `label`), and the streams and calls above.

Gaps: what a card would need that the server does not log yet (each a candidate for T1.43's `/health` or a new line):

- **A hung server looks quiet, not down.** Its process is still there and nothing says "alive" when no client is connected. `/health` (T1.43 W1) is the fix; the watcher reads it once `huxley.health_url` is set.
- **No running commit** at start (`huxley_starting` carries no version), so the page shows only the walkie's firmware version.
- **Telegram mid-run**: a session revoked or a dropped MTProto connection after setup logs nothing; only a setup failure or a failing call shows it.
- **Spotify logged out at start**: `spotify.auth_state` is logged only on a change, so a player that is logged out from the first message is seen only when a play is refused (`spotify.unavailable reason=player_not_paired`).
- **`connection_failed` has no structured reason** (only the traceback), so quota versus network is read from text.
- **Battery** (F-0025), **rollback and update probation** (T1.43 W4): no signal yet. (The box's disk the watcher reads itself.)
## When something looks wrong

The dev workflow:

1. **You** describe the symptom in conversation: "the book never started", "the tone played forever", "the model said it would but didn't".
2. **You** paste the relevant log section (from the symptom moment, plus a few seconds before and after).
3. **The LLM** reads the log and proposes a hypothesis. Often the bug is identifiable from the log alone.
4. **You** confirm or dispute, sometimes with one more log filter.
5. **The LLM** proposes a fix.

This works because every event in the log is structured, every decision is logged with its inputs, and the namespacing tells you instantly which component made which call. If a future bug isn't diagnosable from the log, the fix isn't just the bug — it's also adding the log line that would have caught it.
