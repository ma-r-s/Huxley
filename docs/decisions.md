# Architectural Decisions

Append-only log of non-obvious calls. Format: date · context · decision · consequences. Each entry has a stable heading for cross-linking from other docs.

> **Naming note**: ADRs predating 2026-04-16 reference "AbuelOS" because that was the project's name at the time. Today, **AbuelOS** is the canonical persona, and the framework itself is called **Huxley**. Historical ADRs are preserved verbatim — they record the decision in the language used at the time. New ADRs use the current naming.

## Template

```markdown
## YYYY-MM-DD — Short title

**Context**: What situation required a decision. What alternatives were on the table.
**Decision**: What we chose.
**Consequences**: What this enables, what it costs, what to revisit and when.
```

---

## 2026-04-12 — Python server does not own audio hardware

**Context**: Initial design had Python capturing mic via PyAudio and playing to the speaker directly. This coupled the server to host audio hardware and made the browser dev UI a second-class citizen that had to work around it.

**Decision**: Python is a WebSocket audio relay. Clients (browser today, ESP32 later) own mic and speaker and stream PCM16 24 kHz over WebSocket. Python relays audio to OpenAI, dispatches tool calls, runs skills, owns state.

**Consequences**:

- Same protocol for browser and future hardware clients — no re-architecture when firmware lands.
- Server has zero audio libraries. Simpler deps, simpler tests.
- The audio path has no unit tests (can't fake a browser microphone). Manual browser smoke is the Definition of Done for audio-path changes.
- PyAudio, the dev-mode keyboard PTT, and the internal audio router were all deleted.

---

## 2026-04-12 — One WebSocket client at a time

**Context**: The server could support N clients with per-client session scoping. Grandpa is a single user and this is a one-user system.

**Decision**: Server rejects a second connection with WebSocket close code `1008`. No per-client scoping anywhere.

**Consequences**:

- Simpler state. No `client_id` plumbing.
- If multi-user ever matters, revisit — it would require refactoring `AudioServer` to track sessions per client and disambiguate server-sent events.
- The browser dev client and the future ESP32 client cannot both be connected at once. That's fine: dev and prod are different environments.

---

## 2026-04-12 — Monorepo

**Context**: Considered splitting `server/` and `web/` into separate git repos to isolate concerns.

**Decision**: One git at the repo root. The two halves are tightly coupled via the WebSocket protocol — every protocol change touches both sides and must ship as one atomic commit.

**Consequences**:

- Root holds `.git/`, `.gitignore`, `.claude/`, `CLAUDE.md`, plus `server/`, `web/`, and `docs/`.
- Future `firmware/` slots in as another sibling without further restructuring.
- Protocol changes don't require cross-repo coordination — one PR, one reviewer.

---

## 2026-04-12 — `data/` and `models/` live under `server/`

**Context**: Initially placed at repo root as "shared." But no other project reads them — the web client and any future ESP32 client access audiobooks indirectly through WebSocket tool calls, never by reading files.

**Decision**: Move audiobook library and wake-word models under `server/data/` and `server/models/`. The server is the sole reader.

**Consequences**:

- Config defaults are clean relative paths (`data/audiobooks`, `data/abuel_os.db`) when running from `server/`.
- Repo root stays minimal (`server/`, `web/`, `docs/`, `CLAUDE.md`).
- If a second component ever needs to read audiobooks, promote back to root — unlikely by design.

---

## 2026-04-12 — M4B as the preferred audiobook format

**Context**: Audiobooks can come as a single MP3, folder of MP3 chapters, M4A, M4B, OGG, etc. The skill needs chapter navigation (_"adelanta un capítulo"_) and metadata (title, author, description for natural-language search).

**Decision**: M4B (single file, embedded chapter markers, embedded metadata) is the preferred content format. Folder-of-MP3s + `metadata.json` sidecar is the documented fallback. Both are decoded through `ffmpeg` (see decision below).

**Consequences**:

- One file per book, no sidecars to go stale.
- Mario's content pipeline: prefer M4B sources (LibriVox, converted from Audible, ripped with `m4b-tool`); fall back to MP3 folders only when M4B isn't available.
- Any new audio format requires updating `_AUDIOBOOK_EXTENSIONS` in the skill and documenting in [`skills/audiobooks.md`](./skills/audiobooks.md).

---

## 2026-04-13 — One-button UX contract

**Context**: The dev browser client had three distinct buttons at one point: "Iniciar sesión" to start an OpenAI session, "Interrumpir reproducción" to stop an audiobook, and the big red PTT button to talk. This was never going to fly on the actual hardware: the production device is a walky-talky with **one** physical button, and the AbuelOS persona's target user is blind — they cannot distinguish "first the rectangular button, then the round one, then press-and-hold." Every interaction has to collapse onto the same gesture: press-and-hold the one button.

**Decision**: The browser client exposes **exactly one button**. Press-and-hold semantics depend on the server-side state machine, but the physical gesture is identical in every state:

- **IDLE** — press sends `wake_word`, client waits for `CONVERSING`, then auto-activates PTT and plays a short audible tone ("dígame" cue). User keeps holding, speaks, releases to commit.
- **CONVERSING** — press immediately activates PTT + plays the ready tone. If the model is speaking, client and server both cut the queued audio (the existing interrupt-layers fix).
- **PLAYING** — press sends `wake_word`, which on the server stops the audiobook player, saves position, fires `audio_clear`, then transitions to `CONNECTING`. Client auto-activates PTT once `CONVERSING` is reached, same flow as from IDLE.
- **CONNECTING** — press is queued; activates as soon as the transition reaches `CONVERSING`.

**Consequences**:

- The browser dev client now mirrors the hardware exactly. What the end user does on the ESP32 walky-talky is what Mario does on the browser — same gesture, same states, same server contract.
- **An audible "ready" tone is essential**, not optional. For a blind user, "the button is now live" must be audible, not visual. The tone fires from the client's existing `AudioPlayback` context (a short 880 Hz sine with 5 ms fades) the moment the mic actually goes live.
- **There is no "release cancel" affordance.** If the user releases before `CONVERSING` is reached (pending state), the client silently cancels the pending activation — no commit, no error — and the server stays in `CONVERSING` until the next press.
- The UI still shows the state badge (Inactivo / Conectando / Conversando / Reproduciendo) for Mario's dev debugging, but it's purely informational — the end user never sees it.
- ESP32 firmware inherits the same state machine and client flow verbatim; the only difference is hardware GPIO replacing the browser's pointer events.

---

## 2026-04-13 — Audiobook audio streams through the WebSocket, not local playback

**Context**: The original audiobook player was `mpv` launched by the Python server, playing through the server host's speakers via an IPC socket. This directly contradicted the earlier _"Python server does not own audio hardware"_ decision: it worked for localhost browser dev only because the browser and the mpv process happened to share a machine. With the browser on another laptop, or with the production ESP32 client, audiobook audio would have played on the Pi instead of the user's device. The "client owns audio I/O" invariant was broken the moment playback started.

**Decision**: Replace `mpv` with `ffmpeg` as an async subprocess. `ffmpeg -re -ss <pos> -i <file> -ac 1 -ar 24000 -f s16le -` decodes the book to 24 kHz mono PCM16 on stdout at realtime playback rate. Python reads the stdout pipe in 100 ms chunks and forwards each chunk through `AudioServer.send_audio` — the exact same channel that carries OpenAI model audio. The client plays both audio sources with one code path (`AudioPlayback`), at one sample rate, with zero per-source branching.

**Consequences**:

- The "client owns audio I/O" invariant is fully honored. The server never opens a speaker.
- **Same audio format for both sources** — 24 kHz mono PCM16 via `server.send_audio`. The client doesn't know (or care) whether a chunk came from the model or from a book.
- **Pause** = stop reading ffmpeg stdout; ffmpeg blocks on the full pipe buffer and resumes cleanly. **Seek** = kill and respawn ffmpeg at a new `-ss` position. Both semantics are handled inside `AudiobookPlayer` without any mpv IPC.
- New WebSocket message `audio_clear` (server → client) so seek can drop any queued audio from the old position on the client side — keeps seek snappy.
- **Backpressure is free**: `-re` makes ffmpeg emit at realtime, so reading at any pace naturally paces the WebSocket. No explicit rate limiting.
- 24 kHz mono is lossy vs. original 44.1 kHz stereo, but fine for spoken audiobook content (voice content tops out around 8 kHz). We gain one unified audio path and drop the mpv dep entirely.
- **System prerequisites**: both `ffmpeg` and `ffprobe` binaries on PATH. On macOS `brew install ffmpeg`; on Pi `apt install ffmpeg`. Usually already installed alongside mpv.
- `mpv` and all its IPC wrapper (`server/src/abuel_os/media/mpv.py`, `test_mpv_client.py`) are deleted.

---

## 2026-04-13 — Turn-based coordinator for voice tool calls

> **Naming follow-up (2026-04-15)**: this ADR refers to the field as `audio_factory` because that was the v3 spec name at decision time. It shipped as `side_effect: SideEffect | None` with `AudioStream(factory=...)` as the first kind — see [`packages/sdk/src/huxley_sdk/types.py`](../packages/sdk/src/huxley_sdk/types.py) for the canonical surface. The decision is unchanged; only the field name was generalized to admit future side-effect kinds (e.g. `PlaySound`, `Notification`).

**Context**: Five production bugs in rapid succession — _book jumps in without ack_, _double ack on play_, _seek stream fights model speech_, _interrupt leaves queued audio playing_, _tool instruction text leaks into speech_ — all turned out to be symptoms of one missing abstraction. We were manually coordinating _"the model's speech stream"_ and _"the tool's side-effect audio stream"_ at three different layers (session manager, application orchestrator, audiobooks skill), with a different hand-rolled mechanism in each. Every bug fix added another flag (`_pending_tool_action`, `_response_cancelled`, `_assistant_speaking`, `paused=True`, `on_audio_clear`, deferred `create_task` for the tool action, etc.) until the defer/dispatch logic in `_handle_function_call` was load-bearing in ways no single reader could hold in their head. The next skill with an audio side effect (music, news) would hit the same class of bugs and require another round of tweaks.

**Decision**: Introduce a `TurnCoordinator` as the single authority for audio sequencing around tool calls. Full spec in [`turns.md`](./turns.md) — this ADR is the high-level contract; the spec is the implementation reference. The design went through two rounds of independent critic review plus one round of self-review for over-engineering; all three rounds materially changed the spec. The current shape (spec v3):

- **Turn** is the atomic unit: one user-assistant exchange. Lifecycle spans **multiple OpenAI response cycles** when information tools need narration (IDLE → LISTENING → COMMITTING → IN_RESPONSE ↔ AWAITING_NEXT_RESPONSE → APPLYING_FACTORIES → IDLE, with INTERRUPTED as a sibling terminal from most states). v1 treated a turn as a single response — the critic caught this; v2 handled chained responses explicitly.
- **One audio pipe, not named channels.** v2 proposed `AudioChannel` / `AudioRouter` with per-channel client queues and a `channel` field on the WebSocket protocol. The one-dimensional v1 problem is sequencing (model speech first, then tool audio, in the same turn), not concurrent multi-stream mixing. Sequencing is a coordinator responsibility — v3 has the coordinator forward chunks in order to the same `server.send_audio` pipe, with one field on the coordinator (`current_media_task: asyncio.Task | None`) tracking the long-running media factory. Named channels are deferred until v2-level skills need concurrent streams (background music under news, priority preemption) — the trigger is the first legitimate multi-stream use case, not speculation.
- **Factory pattern** replaces `ToolAction`. `ToolResult` grows an optional `audio_factory: Callable[[], AsyncIterator[bytes]] | None` field. The presence or absence of the factory is the signal: `None` = info tool (model narrates tool output in a follow-up response), `not None` = side-effect tool (model pre-narrates per tool description; coordinator invokes the factory after all speech is done). No `AudioEffect` wrapper, no `EffectKind` enum, no `START_STREAM`/`CLEAR_STREAM`/`NONE`. Just a callable-or-not.
- The **skill never invokes factories**. Factories are invoked only by the coordinator, in the turn's `APPLYING_FACTORIES` state. For rewind/forward, the new position lives in the factory closure — skill does not persist `saved_position = new_pos` before the stream starts. An interrupted rewind leaves storage untouched, closing an interrupt-atomicity gap in the current code.
- **Interrupt is a method on `TurnCoordinator`**, not a separate class. Atomic 6-step sequence: drop flag first → clear pending factories → send audio_clear → cancel `current_media_task` → cancel model response → mark turn INTERRUPTED. The `_response_cancelled` drop flag currently in `session/manager.py:82` is **preserved** (not deleted) — just moved onto the coordinator. v1 silently dropped it; v2 and v3 keep it because it's load-bearing for the race between "cancel sent" and "OpenAI stopped emitting deltas."
- **`pending_factories` latching rules** are explicit: appended on every function call whose result has `audio_factory != None`, fired only after the terminal `response.done` of the chain, dropped entirely on interrupt. An interrupted turn is always fully cancelled — no partial factory application. **Mid-chain interrupts** (turn with three chained rounds, user interrupts in round 2) drop all accumulated factories from earlier rounds.
- The **`PLAYING` state is removed** from the session state machine. Media playback is tracked by `current_media_task` on the coordinator, not by session state. Realtime API bills per token with no per-minute fee; idle sessions cost zero. First-press latency after mid-book interrupts drops from ~1 s (reconnection) to ~0 s. Mic privacy is unchanged — PTT gating was always independent of state.

**Consequences**:

- **Skill authors stop coordinating audio.** `AudiobooksSkill._play` returns `ToolResult(..., audio_factory=lambda: AudiobookPlayer.stream(path, start=pos))`. The coordinator handles everything downstream. Future skills (news, music, messaging) cost ~30 lines each.
- **All five recent bugs become impossible by construction.** Not "fixed with a new flag" — structurally impossible: book-jumps-in (coordinator waits for speech before invoking factory), double-ack (no `response.create` after terminal tool calls), seek-overlap (rewind goes through the same factory pattern as play), interrupt-leakiness (atomic `interrupt()` with 6-step order), instruction-text-leak (tools never dictate what the model says).
- **Protocol delta is small.** `state` drops `PLAYING`; add an optional `turn` message for dev observability. **No changes to `audio` or `audio_clear` messages** — v3 cuts the `channel` field that v2 proposed. This avoids a breaking protocol change for zero v1 capability loss.
- **Session manager shrinks dramatically.** `_handle_function_call` goes from ~70 lines of deferred-dispatch logic to ~10 lines of "forward to coordinator." `app.py` loses `_on_audiobook_chunk`/`_on_audiobook_finished`/`_on_audio_clear`/`_assistant_speaking` and its `_enter_playing`/`_exit_playing` callbacks. Net: **~-100 LOC across the whole refactor** (v2 projected ~0; v3's channel cut is the delta).
- **Client-side thinking tone** (not server-side) fills any silence gap > 400 ms post-PTT-release or post-round-end with a 440 Hz pulsing cue. Uses existing protocol signals (`ptt_stop` sent, `model_speaking: false` received) as anchors — no new WebSocket messages. Client's `AudioPlayback` stays exactly as it is today; the only addition is a `playThinkingTone()` method and a silence-detection timer in `ws.svelte.ts`.
- **Test coverage improves**: the coordinator state machine is unit-testable with mocked events, which the current distributed defer-logic is not. Net ~90 unit tests (up from 88); v2 projected ~100 tests including channel-specific tests that v3 doesn't need.
- **Migration is staged in 6 steps**, down from v2's 8 (channel-implementation and client-per-channel-queues steps are gone). Additive through step 3, so the refactor is reversible if the abstraction turns out wrong under real code. Realistic estimate **2 focused days** for steps 1-5, plus a half day for step 6 and smoke testing.
- **Decisions explicitly deferred**: rollback of storage side effects on interrupted turns, audible error recovery, **named channels / multi-stream routing (added when a v2 skill needs concurrent audio sources, not speculatively)**, priority preemption, persistent turn history, server-side enforcement of "one tool at a time." All tracked as gaps in `turns.md` "Open questions."
- **Review process mattered.** v1 was rewritten after two independent critic reviews that caught load-bearing holes, then v2 was rewritten after self-review caught the channel layer as speculative over-engineering. Notable: _"chained responses"_ (a turn spanning multiple `response.done` events) was invisible without the first critic; _`_response_cancelled` flag preservation_ was invisible without the second; _"channels are speculative; sequencing solves the real problem"_ was invisible until Mario asked _"is this as simple as possible, or did we fall into any rabbit hole?"_ as a deliberate last-chance simplification prompt. Future load-bearing refactors should budget for at least one critic round AND one deliberate simplification pass before code is written.

---

## 2026-04-16 — AbuelOS becomes a persona; the project is renamed Huxley

**Context**: The project started as "AbuelOS" — an assistant targeting an elderly blind Spanish-speaking user. The skill system, the WebSocket protocol, the turn coordinator, the audio path are all generic — none are AbuelOS-specific. Continuing to call the framework "AbuelOS" conflates two things (the framework and the canonical instance of it), confuses anyone discovering the project, and fights against the goal of making it open-source-and-extensible. Mario's vision: "anyone with a chatbot need can build a voice agent on this; the AbuelOS use case is one persona among many."

**Decision**: Rename the project to **Huxley** — the voice agent framework. The original assistant becomes a **persona** named **AbuelOS** that lives at [`personas/abuelos/`](./personas/abuelos.md). The vocabulary becomes:

- **Persona** = who the agent is (config: name, voice, language, personality, constraints, list of skills). YAML.
- **Skill** = what the agent can do (Python package, exports a class implementing the SDK protocol). Installable via PyPI under `huxley-skill-*` convention.
- **Constraint** = a named behavioral rule layered onto the system prompt (`never_say_no`, `confirm_destructive`, `child_safe`). The "nunca decir no" rule, formerly project-wide, becomes the `never_say_no` constraint that the AbuelOS persona declares.
- **Side effect** = the generalized version of `audio_factory` — what a tool produces beyond text. Audio is one kind. The framework can be extended to support more (notifications, state changes) without touching skills that don't use them.

The Python namespace (`abuel_os/`) and repo path (`AbuelOS/`) are renamed as part of the next refactor, and the skill SDK is extracted into `packages/sdk/` so third-party authors import from a stable surface.

**Consequences**:

- **Documentation reorganized**: `vision.md` describes Huxley the framework; `personas/abuelos.md` is the AbuelOS persona spec (what was in vision.md before). New: `concepts.md` (vocabulary), `personas/README.md` (how to write a persona), `observability.md` (logging/debugging workflow).
- **Skill author surface area becomes stable**: skills depend on `huxley_sdk`, never on framework internals. Persona authors are non-developers writing YAML.
- **The `never_say_no` rule is opt-in per persona**, not framework-mandated. AbuelOS keeps it; other personas (a child's tutor, a developer's assistant) may not need it.
- **The repo grows a workspace structure**: `packages/sdk/`, `packages/core/`, `packages/skills/audiobooks/`, `packages/skills/system/`, `personas/abuelos/`. uv workspaces handle the multi-package layout.
- **Historical ADRs preserved verbatim**. They reference "AbuelOS" because that was the project's name at the time. The naming note at the top of this file flags this for new readers.
- **What does NOT change for v1**: the 3-state session machine, the turn coordinator, the audio path, the WebSocket protocol, the OpenAI Realtime integration. Those are framework, and they were already persona-agnostic by accident — the rename just makes the role explicit.

---

## 2026-04-18 — Default model is `gpt-4o-mini-realtime-preview`

**Context**: Initial dev ran on `gpt-4o-realtime-preview` (full model). Text input tokens are ~8x more expensive on the full model than on mini ($5 vs $0.60 per 1M tokens). For AbuelOS, the agent dispatches tool calls in Spanish against a fixed set of skills — not reasoning-heavy work. Mini is sufficient.

**Decision**: Default `Settings.openai_model` is `gpt-4o-mini-realtime-preview`. `.env` can override to the full model for A/B testing.

**Consequences**:

- Session startup overhead (system prompt + 14 tool schemas re-sent on every reconnect) drops to ~$0.002 per reconnect.
- If a future persona needs heavier reasoning (multi-step agent work, long-form narration), it can override `HUXLEY_OPENAI_MODEL` in that persona's launch env.
- Voice quality is unchanged — voice is a separate axis (`coral`, `alloy`, etc.) and works identically on both models.

---

## 2026-04-18 — Keep Realtime session alive during media playback

**Context**: During audiobook/radio playback the OpenAI Realtime session sits open but idle — no audio flows either direction. An optimization considered: proactively close the OpenAI session when media starts, reopen it on next PTT. This would eliminate the ~22 reconnects triggered during a 20-hour listening session by `conversation_max_minutes = 55` + OpenAI's own 30–60 min session cap.

**Decision**: Keep the session alive. Let OpenAI decide when to disconnect. Rely on `disconnect(save_summary=True)` → `_auto_reconnect()` to preserve context across forced resets.

**Consequences**:

- **Cost of keeping it alive is effectively $0 during silence.** OpenAI bills per Response created (audio in + audio out + the system prompt + tools as input context for that response). A session that opens, sends `session.update`, and closes without ever creating a Response incurs no token charges. So 22 silent reconnects across a 20-hour book = ~$0.00, not the ~$0.05 first estimated.
- **Context continuity wins over cost.** The model knows what book is playing, what the user asked for five minutes ago, and the flow of the conversation. Proactive disconnect would force the model to re-orient (`get_progress` / `list_in_progress`) on every resume. For a blind elderly user, that's a noticeably dumber assistant with zero meaningful cost savings.
- **`conversation_max_minutes` in `Settings` still forces a Huxley-side disconnect every N minutes** — but that path already calls `save_summary=True` and auto-reconnects. Raise the knob only if summary-on-reconnect ever proves insufficient.
- **Revisit if**: a future cost audit shows idle sessions aren't truly $0 (OpenAI could change this), OR a persona ships with a huge prompt whose per-reconnect bill matters even when no Response is created.

---

## 2026-04-18 — Pivot I/O-plane coordination from arbitration to AVS focus-management

**Context**: T1.2's I/O-plane spec (`docs/io-plane.md`) proposed coordination via
an arbitration model: each speaker claim carries an `Urgency` (AMBIENT /
CHIME_DEFER / INTERRUPT / CRITICAL), each current stream owner carries a
`YieldPolicy` (IMMEDIATE / YIELD_ABOVE / YIELD_CRITICAL), and a pure
`arbitrate(urgency, yield_policy) → Decision` function over 20 cases picked one
of five outcomes (SPEAK_NOW / PREEMPT / DUCK_CHIME / HOLD / DROP). T1.3 shipped
the scaffolding for this: `MediaTaskManager` (task slot + `decide()` hook),
`arbitrate()` pure function, `DuckingController` stub, `Urgency` + `YieldPolicy`
enums in the SDK. T1.4 Stage 1 was planned to wire it: `inject_turn` → arbitrate
→ preempt/duck, with an `InjectedTurnHandle` and a TTL/dedup queue on top.

Starting T1.4 Stage 1 design, the tuple-based model started feeling like a
coarser re-expression of what is actually a stacked-claims-per-resource problem:

- Every "channel" (dialog, calls, alerts, content) is really just a named slot
  with its own priority. Arbitration between two claims on the same channel
  (e.g., two media streams) is different from arbitration across channels —
  the tuple form couldn't express this without adding a channel parameter
  everywhere.
- "Patience" for being displaced — the AVS concept where a BACKGROUND Activity
  gets N seconds to finish its sentence before being dropped — has no clean
  place in the tuple model. It ended up as an ad-hoc TTL queue in Stage 1's
  plan, which is exactly the thing AVS designed `FocusState.BACKGROUND +
MixingBehavior.MUST_PAUSE + patience timer` to replace.
- Ducking is orthogonal to preemption: the same "higher-priority thing wants
  the speaker" event can mean "duck the music under the alert" OR "preempt
  the music entirely" OR "hold the alert for a convenient moment," depending
  on the two Activities' `ContentType` (MIXABLE / NONMIXABLE). The tuple model
  conflated these into one `Decision` enum.
- Amazon's Alexa Voice Service (AVS) solved this shape 10 years ago with a
  channel-oriented focus-management model. The vocabulary transfers cleanly
  to Huxley's single-speaker case.

**Decision**: Pivot the coordination substrate from arbitration to AVS-style
focus management, **mid-Stage-1**. Replace `Urgency` / `YieldPolicy` /
`arbitrate()` / `Decision` / `MediaTaskManager` with:

- `Channel` — `DIALOG` (100), `COMMS` (150), `ALERT` (200), `CONTENT` (300).
  Lower number = higher priority (AVS convention).
- `FocusState` — `FOREGROUND`, `BACKGROUND`, `NONE`. Verbatim AVS.
- `MixingBehavior` — `PRIMARY`, `MAY_DUCK`, `MUST_PAUSE`, `MUST_STOP`.
  Verbatim AVS.
- `ContentType` — `MIXABLE` / `NONMIXABLE`. Determines background behavior
  (MIXABLE → MAY_DUCK; NONMIXABLE → MUST_PAUSE).
- `Activity(channel, interface_name, content_type, observer, patience)` —
  one claim, dedup'd by `(channel, interface_name)`.
- `FocusManager` — single-task actor draining a mailbox of `Acquire / Release /
PatienceExpired / StopForeground / StopAll` events. Serialized mutation,
  races impossible by construction.
- `ChannelObserver` protocol — `async def on_focus_changed(new_focus, behavior)`.
  Observers are thin adapters owned by the coordinator or skills;
  `DialogObserver` fires `on_stop` on NONE; `ContentStreamObserver` owns the
  stream pump task.

One deliberate AVS flip: **patience belongs to the incumbent** (being-displaced
Activity), not the acquiring one. Documented in `io-plane.md#patience-attribution`.

**Consequences**:

- **`Urgency` / `YieldPolicy` / `arbitrate()` / `Decision` / `MediaTaskManager` /
  `DuckingController` are deleted** — ~500 LOC removed across code + tests.
  Three commits: `1f9b232` (FocusManager + vocabulary), `a1afabd` (observers),
  `31a18cf` (coordinator wiring + scaffolding deletion).
- **Stage 1 is partially done**: the substrate ships; `inject_turn` itself and
  its skill-facing surface are not wired. Stages 2-4 (`InputClaim`,
  `background_task`, `ClientEvent`) are orthogonal to the coordination
  substrate and unaffected by the pivot.
- **`SpeakingState` stays**. It tracks the named-owner model-speech flag —
  distinct concern from focus-channel arbitration, and the FocusManager's
  actor loop can't expose the synchronous `is_speaking` check the coordinator
  needs. A later follow-up could fold it into a `DialogObserver`-driven flag
  once the trade-off is clearly worth the mechanical complexity.
- **`docs/io-plane.md` is partially superseded**. The primitives' shapes
  (AudioStream, inject_turn, InputClaim, ClientEvent, background_task) still
  describe the right thing, but the coordination vocabulary (Urgency /
  YieldPolicy / arbitrate) is gone. The doc has a banner marking it as
  pre-pivot; full rewrite queued until T1.4 Stage 2 direction is locked.
- **Ducking is not yet implemented**. `ContentStreamObserver` logs
  `focus.duck_not_implemented` on MAY_DUCK and falls back to pause per AVS
  contract. Server-side PCM gain envelope is a concrete future deliverable
  — still scoped, just under a different module (likely as part of
  `ContentStreamObserver`'s BACKGROUND handler, not a separate
  `DuckingController`).
- **Skill-facing docs hold**. `skills/README.md`'s forward-looking sections
  (`inject_turn`, `InputClaim`, `background_task`, `subscribe_client_event`)
  are bannered as "planned; vocabulary may change post-pivot" until Stage 2
  lands. Tradeoff: readers get a preview of the future surface, with honest
  acknowledgement that the names on enums may shift.
- **Revisit if**: the focus-management model fails to compose naturally when
  we build `inject_turn`, `InputClaim`, or calls-as-a-skill. If it does fail
  in Stage 2+, revert the pivot (restore arbitrate + tuple model) — the
  critic gate for Stage 2 should explicitly test composition against all
  three skills, not just inject_turn.
