# Architectural Decisions

Append-only log of non-obvious calls. Format: date · context · decision · consequences. Each entry has a stable heading for cross-linking from other docs.

> **Naming note**: ADRs predating 2026-04-16 reference "Abuelo" because that was the project's name at the time. Today, **Abuelo** is the canonical persona, and the framework itself is called **Huxley**. Historical ADRs are preserved verbatim — they record the decision in the language used at the time. New ADRs use the current naming.

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

**Decision**: Server accepts at most one active client. When a second
connection arrives, the server **closes the existing client with code
`1001 — Replaced by new client`** and accepts the new one. No
per-client scoping anywhere.

**Consequences**:

- Simpler state. No `client_id` plumbing.
- Evict-old rather than reject-new is deliberate: the new connection
  is almost always a browser reload or a re-flashed device, and
  should win over a stale socket that may or may not be alive.
- If multi-user ever matters, revisit — it would require refactoring `AudioServer` to track sessions per client and disambiguate server-sent events.
- The browser dev client and the future ESP32 client cannot both be connected at once. That's fine: dev and prod are different environments.

> Earlier drafts of this ADR (and `protocol.md` through v0.2.3) said the
> second connection was rejected with `1008`. That was never the
> implementation; corrected 2026-04-24 after firmware-contract pytest
> locked down the actual behaviour.

---

## 2026-04-12 — Monorepo

**Context**: Considered splitting `server/` and `clients/pwa/` into separate git repos to isolate concerns.

**Decision**: One git at the repo root. The two halves are tightly coupled via the WebSocket protocol — every protocol change touches both sides and must ship as one atomic commit.

**Consequences**:

- Root holds `.git/`, `.gitignore`, `.claude/`, `CLAUDE.md`, plus `server/`, `clients/pwa/`, and `docs/`.
- Future `clients/firmware/` slots in as another sibling without further restructuring.
- Protocol changes don't require cross-repo coordination — one PR, one reviewer.

---

## 2026-04-12 — `data/` and `models/` live under `server/`

**Context**: Initially placed at repo root as "shared." But no other project reads them — the web client and any future ESP32 client access audiobooks indirectly through WebSocket tool calls, never by reading files.

**Decision**: Move audiobook library and wake-word models under `server/data/` and `server/models/`. The server is the sole reader.

**Consequences**:

- Config defaults are clean relative paths (`data/audiobooks`, `data/abuel_os.db`) when running from `server/`.
- Repo root stays minimal (`server/`, `clients/pwa/`, `docs/`, `CLAUDE.md`).
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

**Context**: The dev browser client had three distinct buttons at one point: "Iniciar sesión" to start an OpenAI session, "Interrumpir reproducción" to stop an audiobook, and the big red PTT button to talk. This was never going to fly on the actual hardware: the production device is a walky-talky with **one** physical button, and the Abuelo persona's target user is blind — they cannot distinguish "first the rectangular button, then the round one, then press-and-hold." Every interaction has to collapse onto the same gesture: press-and-hold the one button.

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

> **Naming follow-up (2026-04-15)**: this ADR refers to the field as `audio_factory` because that was the v3 spec name at decision time. It shipped as `side_effect: SideEffect | None` with `AudioStream(factory=...)` as the first kind — see [`server/sdk/src/huxley_sdk/types.py`](../server/sdk/src/huxley_sdk/types.py) for the canonical surface. The decision is unchanged; only the field name was generalized to admit future side-effect kinds (e.g. `PlaySound`, `Notification`).

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

## 2026-04-16 — Abuelo becomes a persona; the project is renamed Huxley

**Context**: The project started as "Abuelo" — an assistant targeting an elderly blind Spanish-speaking user. The skill system, the WebSocket protocol, the turn coordinator, the audio path are all generic — none are Abuelo-specific. Continuing to call the framework "Abuelo" conflates two things (the framework and the canonical instance of it), confuses anyone discovering the project, and fights against the goal of making it open-source-and-extensible. Mario's vision: "anyone with a chatbot need can build a voice agent on this; the Abuelo use case is one persona among many."

**Decision**: Rename the project to **Huxley** — the voice agent framework. The original assistant becomes a **persona** named **Abuelo** that lives at [`server/personas/abuelos/`](./personas/abuelos.md). The vocabulary becomes:

- **Persona** = who the agent is (config: name, voice, language, personality, constraints, list of skills). YAML.
- **Skill** = what the agent can do (Python package, exports a class implementing the SDK protocol). Installable via PyPI under `huxley-skill-*` convention.
- **Constraint** = a named behavioral rule layered onto the system prompt (`never_say_no`, `confirm_destructive`, `child_safe`). The "nunca decir no" rule, formerly project-wide, becomes the `never_say_no` constraint that the Abuelo persona declares.
- **Side effect** = the generalized version of `audio_factory` — what a tool produces beyond text. Audio is one kind. The framework can be extended to support more (notifications, state changes) without touching skills that don't use them.

The Python namespace (`abuel_os/`) and repo path (`Abuelo/`) are renamed as part of the next refactor, and the skill SDK is extracted into `packages/sdk/` so third-party authors import from a stable surface.

**Consequences**:

- **Documentation reorganized**: `vision.md` describes Huxley the framework; `server/personas/abuelos.md` is the Abuelo persona spec (what was in vision.md before). New: `concepts.md` (vocabulary), `server/personas/README.md` (how to write a persona), `observability.md` (logging/debugging workflow).
- **Skill author surface area becomes stable**: skills depend on `huxley_sdk`, never on framework internals. Persona authors are non-developers writing YAML.
- **The `never_say_no` rule is opt-in per persona**, not framework-mandated. Abuelo keeps it; other personas (a child's tutor, a developer's assistant) may not need it.
- **The repo grows a workspace structure**: `packages/sdk/`, `packages/core/`, `server/skills/audiobooks/`, `server/skills/system/`, `server/personas/abuelos/`. uv workspaces handle the multi-package layout.
- **Historical ADRs preserved verbatim**. They reference "Abuelo" because that was the project's name at the time. The naming note at the top of this file flags this for new readers.
- **What does NOT change for v1**: the 3-state session machine, the turn coordinator, the audio path, the WebSocket protocol, the OpenAI Realtime integration. Those are framework, and they were already persona-agnostic by accident — the rename just makes the role explicit.

---

## 2026-04-18 — Default model is `gpt-4o-mini-realtime-preview`

**Context**: Initial dev ran on `gpt-4o-realtime-preview` (full model). Text input tokens are ~8x more expensive on the full model than on mini ($5 vs $0.60 per 1M tokens). For Abuelo, the agent dispatches tool calls in Spanish against a fixed set of skills — not reasoning-heavy work. Mini is sufficient.

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

## 2026-04-19 — Skill-owned persistence over a framework primitive

**Context**: T1.4 Stage 3b needed timers that survive a server restart (Mario's elderly blind user cannot be asked to re-set a medication timer after a reboot). The original triage spec called for a framework primitive: a `persist_key: str` argument on `ctx.background_task(...)` that would have the `TaskSupervisor` serialize `(name, coro_factory, kwargs)` to `SkillStorage` and restore them on boot via `Application.run()`. A Gate-2 critic pushed back: `coro_factory` is a Python closure, so either skills must guarantee their factories are config-pure (a significant new contract) or the framework maintains a factory-name registry (complexity cost).

**Decision**: Skills own their persistence. The framework grows only two additions to `SkillStorage`:

- `list_settings(prefix="") -> list[(key, value)]` — enumerate a composite-key family (`timer:*`, `position:*`, …).
- `delete_setting(key)` — remove an entry outright (no empty-string tombstones leaking into list callers).

Timers (the Stage 3b forcing function) writes `timer:<id>` JSON entries via `ctx.storage.set_setting`, reads them in `setup()` via `ctx.storage.list_settings("timer:")`, and applies its own restore policy (stale threshold, `fired_at` dedup, `_next_id` priming). The supervisor stays inert; no new skill contract.

**Consequences**:

- **Saved** a new SDK surface (`persist_key`), a new `coro_factory` contract, and framework-level restore orchestration. Instead, skills get two primitive storage operations that are generically useful beyond persistence (`list_settings` is the obvious right answer for any composite-key consumer).
- **Each persistent skill writes its own restore logic.** Today that's fine — timers is the only consumer. When T1.8 reminders lands with recurring medication schedules, and T1.9 messaging lands with inbound thread cursors, the restore shapes will likely NOT look alike (cron-spec evaluation vs. last-seen-seq comparison). If they DO rhyme surprisingly, extract a framework helper; if not, skill-owned keeps being cheap.
- **Revisit if**: three or more skills end up re-implementing the same restore pattern. The threshold is "rule of three," not "rule of two."
- **Doesn't close**: Stage 3b's `fired_at` dedup is specific to the medication-reminder worst case (double-dose > missed-dose). A future skill with different safety semantics (a radio scheduler, say) might prefer "re-fire on crash." The policy lives in the skill, which is the right layer for this kind of choice.

## 2026-04-24 — Focus plane completion: COMMS live, `BLOCK_BEHIND_COMMS` priority, pause/resume contract, concurrent-claim rejection, patience-expiry hook, ALERT reserved

**Context**: The 2026-04-23 honest-assessment review surfaced significant drift between the focus-plane story the docs told and the code: `concepts.md` and `io-plane.md` described `COMMS` and `ALERT` channels as roadmap-imminent or partially-wired; in reality neither had any call site. `InputClaim` (Telegram calls) was hardcoded on `CONTENT`, which forced call-during-audiobook to evict the book rather than pause-and-resume. The urgent-reminder tier had no way to say "preempt content but respect active calls" — only `NORMAL` (misses the immediate-narration use case) or `PREEMPT` (drops everything including calls). A Gate-2 critic pass against the straw-man "just rename InputClaim to COMMS" proposal found additional latent bugs: the observer's pump-cancel-then-respawn path did not actually resume audiobook playback (the factory closure captured `start_position` at build time), concurrent-claim handling stacked weirdly because each claim minted a fresh `interface_name`, and patience expiry evicted silently with no user-facing narration path.

**Decision**: Land all four corrections in one commit (Stage 2b + Stage 5 + T2.7 co-landing):

1. **InputClaim migrates CONTENT → COMMS** (priority 150). Single-slot policy: `start_input_claim` uses the literal `interface_name="claim:active"` and raises `ClaimBusyError` on a second concurrent call. Skills catch and reject the peer (Telegram sends `DISCARDED_CALL`). Call-waiting / claim-stacking is explicitly out of scope.

2. **Audiobook factory reads live position on each invocation** rather than capturing `start_position` at build time. The pump-cancel-then-respawn path now correctly resumes from where the prior pump's `finally` block saved. Combined with `AudioStream.patience=timedelta(minutes=30)` on the audiobook Activity, a COMMS claim parks the book in BACKGROUND/MUST_PAUSE and FM promotes it back to FOREGROUND on claim-release.

3. **`ChannelObserver.on_patience_expired()` hook** fires BEFORE the terminal `NONE/MUST_STOP` notification when patience elapses. `ContentStreamObserver` forwards to a new optional `AudioStream.on_patience_expired` callback. Audiobooks wires this to an `inject_turn` acknowledgement so a very long call produces narrated feedback instead of silent state loss — non-negotiable for the blind-user persona.

4. **`InjectPriority.BLOCK_BEHIND_COMMS`** added as the third tier between `NORMAL` and `PREEMPT`. Preempts CONTENT, queues behind COMMS. Timers retrofits its fire path to this tier so a cooking timer pauses the audiobook for narration but waits for a call to end. The originally-scoped `inject_alert` primitive + separate ALERT channel wire-up was collapsed into this enum variant after the critic noted `inject_alert` would narrate through the same LLM DIALOG path anyway; the separate channel is a distinction without a runtime difference when the only consumers are narrated alerts. **ALERT the channel stays defined** (priority 200, full FM arbitration support) but has no callable surface — reserved for a future non-LLM alert-sound skill (siren, alarm) where channel separation is meaningful.

**Consequences**:

- **The four-channel focus model now matches the docs.** `DIALOG` and `CONTENT` were live; `COMMS` is live (holds calls); `ALERT` is honestly documented as reserved with no current consumer.
- **Call-during-audiobook UX is fixed.** Book auto-resumes from the cancellation position on hangup; no user command needed. Long calls (>30min) narrate an acknowledgement before giving up the book.
- **Urgent reminders get correct semantics.** A timer / future medication reminder using `BLOCK_BEHIND_COMMS` won't interrupt grandpa's doctor call.
- **Concurrent claims are a defined failure mode with graceful recovery,** not undefined stacking. The Telegram skill's existing transport-level busy-check is still the first line; coordinator-level `ClaimBusyError` is defense in depth.
- **Documentation reconciled.** `concepts.md` describes current state; `io-plane.md` gets a stronger historical-artifact disclaimer with pointers to authoritative sources; `skills/README.md` has the three-tier priority guide.
- **Known follow-up (filed as D7)**: a `BLOCK_BEHIND_COMMS` alert queued behind a 2-hour call fires 2 hours late — safety-adjacent for medication. TTL on queued injects is deferred until a real consumer (reminders skill) ships.
- **Revisit**: if ALERT stays unused for 6+ months prune it; if a skill needs call-waiting, promote single-slot to a `claim_policy` field; if `BLOCK_BEHIND_COMMS` dominates real usage, consider making it the default — all data-driven, not speculative.

## 2026-04-24 — Post-smoke-test fixes to the focus plane: is_ended gate, playback-drain wait, idle-inject-during-claim, claim title for UI

**Context**: Same-day smoke-testing of the 2026-04-24 focus-plane completion (commit `32d4be3` + critic follow-up `72fa1ad`) surfaced four distinct issues that the unit tests did not catch:

1. **"Fighting to tell me the call ended"** — a skill's `on_claim_end` callback firing `ctx.inject_turn("la llamada terminó")` got its request queued behind itself, because the coordinator's own post-callback scrub of `_claim_obs` runs AFTER the skill's callback returns. The `_claim_obs is not None` gate added in commit `72fa1ad` was TOO aggressive — a claim in the middle of ending is functionally over.
2. **Announcement guillotined by the claim** — `inject_turn_and_wait` returned at server-side `response_done`, which fires when the LLM has finished sending audio but before the client has finished playing it. The next `start_input_claim` emits `audio_clear` (buffer flush) and cut the tail of the announcement mid-sentence. Observed reliably on every inbound Telegram call.
3. **Idle-NORMAL inject during an active call preempted the call** — the critic's SB-1 from the prior commit had identified `BLOCK_BEHIND_COMMS`; the fix missed that `NORMAL` has the same problem. NORMAL-idle-fire wasn't checking for an active claim.
4. **Orb had no in-call identity** — during a live Telegram call the status label read "Listening" and the ring animation used synthesized audio, not the peer's actual voice.

**Decision**: Four targeted fixes shipped in a single follow-up commit.

1. **Gate the claim-busy check on `observer.is_ended`.** `ClaimObserver` sets `_ended = True` at the top of `_end()`, BEFORE firing skill callbacks. The coordinator's inject-gate now checks `_claim_obs is not None and not _claim_obs.is_ended`. Skills firing `inject_turn` from `on_claim_end` see the claim as "tearing down, not active" and fire through instead of queueing. New regression test `test_inject_from_on_claim_end_fires_through_not_queued` locks this in.

2. **Wait for client playback to drain in `inject_turn_and_wait`.** Coordinator tracks cumulative audio bytes per response (`_turn_audio_bytes_sent`) and the monotonic time of the first delta (`_turn_audio_first_sent_at`). After `response_done` signals, `_wait_for_client_playback_drain()` sleeps `max(0, first_sent_at + bytes/48000 + 80ms_safety - now)` before returning. `AudioStream.patience` field unrelated — this is a DIALOG-side fix, not a content-side fix. 4 unit tests cover duration, no-audio no-op, past-drain no-op, and counter reset on fresh stream.

3. **Extend the idle-claim gate to all non-PREEMPT priorities.** `NORMAL` now also queues behind an active claim. Only `PREEMPT` (unconditional urgency, e.g. fire alarm) barges through. Symmetry with `BLOCK_BEHIND_COMMS` was the intent from day one; the earlier fix just missed the NORMAL case.

4. **Claim title surfaces to UI.**
   - SDK: `InputClaim.title: str | None` new field.
   - Protocol: `claim_started` wire message gains `title` ("Mario", "Nota de voz", etc.).
   - Telegram skill passes `title=<contact_name>` for outbound + `title=<display>` for inbound.
   - Web client: status label flips to "Hablando con Mario" during `live` orb state.
   - Orb: `live` state now drives animation from the real playback analyser (same FFT path `speaking` uses) instead of synth audio. `sqrt(speakLevel)` boost compensates for Telegram's Opus-compressed peer audio being ~3-5× quieter at the analyser than LLM TTS.

**Consequences**:

- **The focus plane is now actually usable in production for Telegram calls.** The smoke-test catch rate confirms the unit tests weren't sufficient here — the behavior that bit (order-of-operations between skill callback and coordinator scrub, client-buffer playback vs. server response_done, non-linear dynamic range in telephony audio) required live audio to surface.
- **Unit test suite grew by 8 tests** (4 drain-wait, 3 idle-claim-inject, 1 on-claim-end regression). Total core tests: 370 (was 358 at ship of 32d4be3). All existing tests still green.
- **Known residual edge case**: `inject_turn_and_wait`'s drain computation uses the coordinator-side byte count, not a client acknowledgement. A client that drops behind real-time playback (slow CPU, tab backgrounded) would miss the drain window and the announcement could still be flushed. Acceptable given the 80ms safety margin; revisit if real users hit it.
- **Revisit**: If Mario's testing exposes more cases, track the per-case velocity — the smoke-test loop is currently paying for itself, so keep running it after any focus-plane change.

---

## 2026-04-30 — Session boundary is logical, not technical (T1.12)

**Context**: T1.12 ships browsable session history. The original sketch defined a "session" as one WebSocket connect/disconnect cycle. The Gate-2 critic round flagged that this would fragment the user's mental model on every common usage shape: auto-reconnect after idle, language switch, cost-kill, browser refresh, network blip — each currently triggers a fresh `provider.connect()` and would each create a new row. A user who talks for two hours through a 60-minute Realtime session timeout would get two rows; a caregiver reviewing their day would see twelve rows for what felt like three conversations.

**Decision**: WS-connect/disconnect remains the technical lifecycle, but the user-visible "conversation" is a separate logical unit grouped by idle gap.

- New storage method `start_or_resume_session(idle_window_min: int = 30)`: returns the most recent session's id if its `last_turn_at` falls within the window; otherwise inserts a new row. Resume clears `ended_at` so the row reads as live again.
- The 30-minute default is a guess based on casual-conversation cadence. Tunable in storage; may move to a persona-level config if usage data shows different gap distributions per use case.
- `_on_transcript` lazily calls `start_or_resume_session` on the first turn after a connect, so empty sessions (connected, never spoke) never hit storage.

**Consequences**:

- A WS reconnect that lands within the window writes turns onto the same row as before — the caregiver sees one conversation, not two. Same for language switches and cost-kill restarts.
- `end_session(id, summary)` is idempotent across reconnects: each disconnect overwrites `ended_at` + `summary`. The row's final summary is whatever the LATEST disconnect computed; the gold integration test pins this.
- The OpenAI Realtime provider's existing summary-chain (each new session loads `get_latest_summary` to inject context) continues to work — the warm-reconnect text is now read from `sessions.summary` instead of the retired `conversation_summaries` table.
- Threshold drift: if 30 minutes proves wrong (too high → unrelated conversations merge; too low → fragmentation returns), it's a single constant in `storage/db.py`. Track via support reports or smoke-test feedback.

---

## 2026-04-30 — Session capture: provider→app callback handoff, not provider→storage (T1.12)

**Context**: Pre-T1.12, the OpenAI Realtime provider wrote the session summary directly via `storage.save_summary(text)` from inside `disconnect()`. The `on_session_end()` callback fired from the receive-loop's `finally` clause, which runs BEFORE `disconnect()`'s post-receive-task body computes the new summary. Result: the framework's `_on_session_end` was reading `storage.get_latest_summary()` and attributing the **previous** session's summary to the **current** session's row. Critic flagged this as "every row gets the wrong summary in production."

**Decision**: Provider stops touching storage. The `on_session_end` callback signature changes from `() → None` to `(summary: str | None) → None`. Provider computes the summary BEFORE cancelling the receive task and stashes it on `_pending_summary`; the receive loop's `finally` reads + clears it and passes to `on_session_end(summary)`. App layer owns the storage write via `storage.end_session(active_session_id, summary)` against the session id captured in `_on_transcript`.

**Consequences**:

- Layering win: provider is now purely a transport + summary generator. Storage of session-level state is the framework's responsibility, attributed to the right id from the same code path that opened the session.
- The race is gone: summary, session id, and `end_session` write all happen in one place (app's `_on_session_end`).
- A late transcript line arriving between summary computation and receive-task cancellation is dropped — accepted (microsecond window, transient OpenAI tail-events, no production impact).
- Stub provider matches the new signature so end-to-end tests don't drift.

---

## 2026-04-30 — Session protocol additions: additive, no version bump (T1.12)

**Context**: T1.12 needed three new client→server message types (`list_sessions`, `get_session`, `delete_session`) and three server→client replies (`sessions_list`, `session_detail`, `session_deleted`). The original sketch bumped `EXPECTED_PROTOCOL` from 2 → 3 to force a hard handshake mismatch on stale clients. Critic flagged that the ESP32 firmware client has zero use for browsable session history (no screen, no UI to render the list); bumping the version forces lockstep maintenance cost across every client for a PWA-only feature.

**Decision**: Stay at protocol version 2. The new types are additive — old clients ignore unknown message types via the existing log-and-skip default in their dispatch loops (already documented for `dev_event` and `server_event`). New clients talking to old servers degrade silently to "empty list" rather than crashing.

**Consequences**:

- ESP32 firmware doesn't need to add session-handling code or bump its `EXPECTED_PROTOCOL`. Future PWA-only protocol additions follow this pattern.
- Forward compat: a version bump remains the right move when, say, a future change to `claim_started` adds a new required field. We are not relaxing the bump rule, just clarifying its scope — bumps are for breaking changes, additions are free.

---

## 2026-04-30 — Deferred items on the session-history feature (T1.12)

T1.12 ships with a privacy floor (`delete_session` + `clear_summaries`) but explicitly defers several real concerns. Documenting them so they don't get rediscovered as "we forgot":

- **Retention policy**: no automatic expiry. Sessions accumulate forever until manually deleted. Acceptable for the canonical AbuelOS-persona use case today (one user, one device, finite storage). Revisit when (a) any persona ships to multiple users, or (b) a session count exceeds ~10k rows on a single device.
- **Encryption at rest**: SQLite file is plaintext. The DB lives on a personal device the user owns; the OS-level disk encryption is the floor. Revisit if Huxley personas ship into shared infrastructure.
- **Multilingual sessions**: a single session that switches `es-CO` → `en` mid-conversation has turns in two languages but no `language` column on `session_turns`. Caregiver UI can't filter or render per-language. Revisit if mid-conversation language switch becomes a documented user pattern.
- **Transcript accuracy**: OpenAI Whisper is unreliable on dialect-heavy Spanish (a known pain point per Mario's user research). The caregiver-review use case implies the elderly user is being judged by transcripts that may misquote them. No UI affordance currently flags this. Revisit when the caregiver workflow is real (today it's hypothetical product framing).
- **Telegram call/message capture**: a 4-minute Telegram call that landed inside a session does not appear in the transcript — the call's audio/text path bypasses the framework's transcript pipeline. Caregivers reviewing the day will see a gap. Tracked under T1.10 / T1.11; the schema is extensible (could add a `kind` column to `session_turns`) but that design work belongs with whatever ships richer Telegram audit.

---

## 2026-05-01 — Persona is a distinct entity, not a theme (T1.13)

**Context**: T1.13 needed to land a persona-swap UX, but the swap design forces a deeper question: what is a persona? Two clean models surfaced during design:

1. **Persona = theme** — there is one user with one world (reminders, contacts, Telegram, history). Personas are different voices/personalities/skill-sets layered on top of that shared world. A reminder set in abuelos fires regardless of which persona is currently active.
2. **Persona = distinct entity** — each persona is a separate "person" the user talks to. Each has their own memory, conversation history, reminders, Telegram, audiobook progress. Switching personas is reuniting with a different person; information does not flow between them.

**Decision**: persona = distinct entity. Each persona has their own world.

**Why** — the segmentation is **forced by reality**, not chosen for elegance. The LLM's conversation summary and transcript context are intrinsically per-persona: injecting librarian's summary into abuelos's instructions confuses the model. Once any data is per-persona, mixing user-scoped reminders/audiobooks/contacts on top creates a hybrid where the persona seems to know some things about the user but not others. That's worse than either pure model. The "different person" framing matches what's structurally true and gives the user a clean mental model: "if I told abuelos something, abuelos remembers; librarian doesn't."

The "missed reminder when persona swaps away" footgun (a reminder set in abuelos doesn't fire while librarian is active) reframes from a bug to documented behavior: when you're not talking to abuelos, abuelos isn't around. On return, abuelos catches up via the same skill-setup-reads-DB mechanism that already handles process restarts.

**Consequences**:

- Per-persona DBs are correct, not a code-shape accident. Session history, reminders, audiobook progress, Telegram session state — all scoped to one persona, no cross-leak.
- Multi-user households work cleanly: abuelos for grandpa with grandpa's Telegram, buddy for the kid with the kid's Telegram. Privacy is filesystem-enforced.
- No `SkillScope` enum, no `data/user.db` split, no skill-author cognitive overhead. Skills always operate inside the active persona's world.
- Inactive personas are genuinely **absent** while another persona is active — not running in the background. Their MTProto is offline; their reminders are paused. On reactivation, skill `setup()` rehydrates pending state from storage.
- Defers any "watcher mode" / cross-persona notification work until a real product need surfaces. Today's framing is honest about the boundary.

---

## 2026-05-01 — Hot persona swap via reconnect, not in-band (T1.13)

**Context**: with the runtime supporting multi-persona via the new `Runtime` layer, the UX call was how persona-switch should travel over the wire. Two options:

1. **In-band** `select_persona { name }` over the existing WebSocket. The connection persists; the server tears down the old Application and brings up the new one in-place, pushing status frames + a `persona_changed` message through the same socket.
2. **Reconnect** with `?persona=<name>` query parameter. PWA closes the WS, opens a new one with the new query param. Server parses the param at handshake, swaps Application before sending hello.

**Decision**: reconnect.

**Why** — both paths look identical from the user's seat. The picker is gated on `!_claim_or_stream_active()` (no live call, no live audiobook), so the WS dropping for ~50ms during the swap is invisible: no audio is interrupted, no state is lost from the user's perspective. The "soul change vs reincarnation" framing the in-band path was sold on is aesthetic, not functional. The cost of in-band is real: a `Runtime`-level swap-lock to drop client events during teardown, atomic reference rebinding the audio dispatcher, audio-frame-during-swap concurrency hazards, and a new `select_persona` / `persona_changed` message-type pair. None of that pays for the user-perceived UX.

The reconnect path mirrors the existing `setLanguage(code)` pattern (which also reconnects with `?lang=<code>`), so it costs zero new mental model on the client side.

**Consequences**:

- Wire protocol stays at version 2. The `hello` payload gets two additive fields (`current_persona`, `available_personas`); old clients ignore unknown keys.
- AudioServer doesn't need to know about persona-swap — it just routes events to whichever `current_app` Runtime is pointing at. Runtime owns the swap algorithm; AudioServer owns the listener.
- The atomic-swap concern collapses: the WebSocket is closed during the swap window by definition, so no audio frames hit the wrong Application.
- The PWA's existing `switchPersona(url)` becomes `switchPersona(name)` — same close-and-reopen flow, different URL construction.
- A subsequent design that wants in-band swap (e.g. for a hardware client where TCP reconnects are expensive) can layer it on top without regressing the reconnect path; both can coexist if needed.

---

## 2026-05-01 — Multi-instance deployment via cwds, no profile abstraction (T1.13)

**Context**: T1.13 makes one Huxley process serve multiple personas via the picker. But the next deployment shape — two humans in one house, each with their own Huxley — requires running **two processes** with separate persona sets, secrets, and ports. The question was whether the framework should ship a `huxley start <profile>` CLI with profiles registered under `~/.huxley/profiles/<name>/`, or rely on plain Unix-daemon convention (one cwd per instance, port via env var).

**Decision**: no profile abstraction. Multi-instance is "another working directory."

**Why** — a profile abstraction was a Mac-app convention bleeding into a server-process system that doesn't need it. Every other server daemon (nginx sites, postgres clusters, redis instances) handles multi-instance the same way: a directory with config + data, a port, and a service definition (launchd plist / systemd unit) per instance. Adding `huxley start <name>` would be more code, more surface area, and more documentation for zero capability gain.

The cwd-based pattern is self-explanatory and inherits all the existing tooling around Unix-daemon shape (launchd, backup-this-folder, scoped permissions, …).

**Consequences**:

- The household scenario is documented as: per-human directory with `personas/`, `.env`, and a port; one `cd <dir> && uv run huxley` per human; production uses a launchd plist per instance with different `WorkingDirectory`. See `docs/architecture.md` for the canonical layout.
- A `huxley init <dir>` scaffolder may land later as docs polish (creates the dir layout + template `.env` + picks a free port) — that's CLI ergonomics, not architecture, and can come whenever a real user friction shows up.
- Privacy boundary between instances is filesystem-enforced (different `.env`, different DBs, different MTProto sessions). No cross-process state shared by design.
- Two Huxley processes on the same machine are "two parallel Huxleys" — there is no notion of a multi-instance manager. The framework does not actively forbid running multiple processes (port-binding naturally prevents same-port conflicts; different-port conflicts are user-level). Documented as unsupported-but-allowed in `concepts.md`.

---

## 2026-05-01 — T1.13 critic-round-2 fixes (eager swap-connect, swap lock, teardown timeout)

**Context**: between the T1.13 design landing and the implementation completing, an independent critic agent reviewed the shipped code (commits `0e32684e`, `ab5f1aea`). Three findings forced a follow-up commit (`b83232e0`).

1. **The "lazy connect on swap" path was structurally broken.** The locked design said "user PTT triggers connect via the existing state-machine path" — false. The state machine has no IDLE→CONNECTING transition on PTT, only on `wake_word`, and the PWA does not emit `wake_word` after a persona-swap reconnect. So a user who swapped personas and then pressed PTT got rejected by `Application.on_ptt_start`'s IDLE-state guard. The swap appeared to succeed but the new persona was unreachable.
2. **Concurrent `_switch_to_persona` calls leak Applications.** With no serialization, two simultaneous swap requests (PWA in two tabs, StrictMode double-mount, rapid picker clicks) both write `self.current_app = new_app`, the loser's freshly-built Application is silently overwritten — never shutdown, holding open SQLite + FocusManager + (now that swap eagerly connects) a live OpenAI session.
3. **A stuck teardown can DoS the swap path.** `_teardown_task` was awaited unconditionally. If a buggy skill teardown deadlocks an `await`, every subsequent swap blocks forever.

**Decisions**:

- Swap path eagerly connects (`auto_connect=True` in `_shim_persona_select`'s call to `_switch_to_persona`). Boot path was already eager; swap now mirrors it. Idle Realtime sessions cost $0 (see `memory/project_realtime_costs.md`) so eager-connect on swap is functionally free.
- `_switch_to_persona` body wrapped in `asyncio.Lock`. Concurrent swaps serialize; the loser's swap runs after the winner commits and tears down the correct `old_app`. No leaked Applications.
- Teardown await is `asyncio.wait_for(asyncio.shield(task), timeout=10.0)`. On timeout: log loudly + abandon. `shield` so the underlying teardown still runs in the background — we just stop blocking on it.
- Application's 9 AudioServer-callback methods (wake*word, ptt_start/stop, audio_frame, reset, language_select, list_sessions, get_session, delete_session) renamed from `\_on*\_`to`on\__`— they're now the public dispatch surface. Runtime shims call them directly. Future renames of the`\_on_\*`-prefixed-private convention won't silently break Runtime.

**Consequences**:

- Smoke is functional on first swap (the §1 fix).
- Resource-leak class of bug eliminated for swap concurrency (§2).
- Swap-path liveness preserved against buggy teardown code (§10).
- "Public dispatch surface" is now named in code (§3); test renames + protocol changes hit it loudly.
- Test coverage for the swap algorithm landed alongside the fixes (`server/runtime/tests/unit/test_runtime.py`, 10 tests including rapid-back-and-forth and concurrent-swap regressions).

---

## 2026-05-01 — T1.13 post-smoke fixes (canonical id, swap-window state, language-threading, teardown gating)

**Context**: with critic rounds 1 and 2 closed and the implementation locked, Mario ran the final DoD bullet (browser smoke). Three runtime bugs surfaced that neither critic round caught — they all required real client state to manifest. Three follow-up commits (`6b252021`, `512ff48f`, `06d83c5d`) closed the gate.

1. **`PersonaSummary.name` conflated wire id with display label.** `list_personas()` returned `spec.name` (the YAML's `name:` field, e.g. `"Buddy"`, `"Basic"`) as the persona's `name` — but `?persona=` looks up the **directory basename** (`buddy/`, `basicos/`) for filesystem resolution. macOS's case-insensitive FS made `Buddy → buddy/` work by accident (same length); `Basic → basicos/` failed (different length). Critics couldn't catch this — they reasoned about the contract; the bug was in the runtime mapping.

2. **`selectPersona` set `appState=IDLE` during the swap window.** Triggered the unexpected-session-drop error tone (App.tsx:222) on every intentional swap. Plus the PWA's PTT handler dispatched on `IDLE` and fired `wake_word` against the server's already-CONVERSING session — rejected, no listening tone. Critics reviewed the design; the App.tsx error-tone effect wasn't in the read radius.

3. **AudioServer fires `on_persona_select` before `on_language_select`.** The swap auto-connected the new persona's OpenAI session in its DEFAULT language. Then `on_language_select` ran, saw `current=en != requested=es`, called `provider.disconnect(save_summary=True)` to switch — and the disconnect transition CONVERSING→IDLE leaked through the OLD app's `_on_state_transition` (shared AudioServer) to the NEW client. PWA fired the error tone, lost track of state, PTT broke. Critics couldn't trace this — it requires composing the runtime swap algorithm with AudioServer's dispatch order with the client's language preference, all in head.

**Decisions**:

- **`PersonaSummary.name` is the directory basename** — the canonical id `?persona=` looks up. `display_name` carries the YAML label. The protocol doc and concepts doc clarify the distinction. The lesson generalizes: any time a thing has a wire/storage id AND a human label, name them as separate fields in the type system.
- **`selectPersona` sets `CONNECTING` during the swap window**, not IDLE. The error-tone effect only fires on CONVERSING→IDLE; CONNECTING is "transitioning" and doesn't trigger it. The PWA's PTT handler treats CONNECTING as "wait for state to settle" — sets pending without firing wake_word against an already-CONVERSING server.
- **The swap threads the requested language into the new Application**. AudioServer's `on_persona_select` callback signature gains a `language` arg; `Runtime._switch_to_persona` and `Application.__init__` accept it; `Application` resolves persona + sets `_active_language` upfront so the OpenAI session opens in the right language from the start. The subsequent `on_language_select` short-circuits at its `target == current` check — no disconnect+reconnect cascade.
- **Application's `_on_state_transition` gates on `_shutting_down`**. AudioServer is shared between the current app and any previous app being torn down in the background. Without the gate, the OLD app's CONVERSING→IDLE during teardown leaked to the new client. The gate ensures the dying app can't speak to the wire — the runtime already swapped current_app, the singleton has no business hearing from the dying instance.

**Consequences**:

- Persona swap is now silent (no error tone), the picker works for all language combinations, and PTT works immediately after a swap.
- The "canonical id vs display name" distinction lands as a durable framework convention (memory: `feedback_canonical_id_vs_display_name.md`).
- The "shared singleton + dying instance needs gating" pattern lands as a durable framework convention (memory: `feedback_shutting_down_gate.md`). Future hot-swap-style refactors will hit this same shape.
- "Critics catch design issues; smoke catches runtime issues. Don't claim done before smoke" lands as a process convention (memory: `feedback_smoke_after_critic.md`). The DoD's "Mario browser smoke" bullet is non-negotiable for any user-visible change.
