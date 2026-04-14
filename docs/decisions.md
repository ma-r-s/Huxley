# Architectural Decisions

Append-only log of non-obvious calls. Format: date · context · decision · consequences. Each entry has a stable heading for cross-linking from other docs.

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

**Context**: The dev browser client had three distinct buttons at one point: "Iniciar sesión" to start an OpenAI session, "Interrumpir reproducción" to stop an audiobook, and the big red PTT button to talk. This was never going to fly on the actual hardware: the production device is a walky-talky with **one** physical button, and grandpa is blind — he cannot distinguish "first the rectangular button, then the round one, then press-and-hold." Every interaction has to collapse onto the same gesture: press-and-hold the one button.

**Decision**: The browser client exposes **exactly one button**. Press-and-hold semantics depend on the server-side state machine, but the physical gesture is identical in every state:

- **IDLE** — press sends `wake_word`, client waits for `CONVERSING`, then auto-activates PTT and plays a short audible tone ("dígame" cue). User keeps holding, speaks, releases to commit.
- **CONVERSING** — press immediately activates PTT + plays the ready tone. If the model is speaking, client and server both cut the queued audio (the existing interrupt-layers fix).
- **PLAYING** — press sends `wake_word`, which on the server stops the audiobook player, saves position, fires `audio_clear`, then transitions to `CONNECTING`. Client auto-activates PTT once `CONVERSING` is reached, same flow as from IDLE.
- **CONNECTING** — press is queued; activates as soon as the transition reaches `CONVERSING`.

**Consequences**:

- The browser dev client now mirrors the hardware exactly. What grandpa does on the ESP32 walky-talky is what Mario does on the browser — same gesture, same states, same server contract.
- **An audible "ready" tone is essential**, not optional. For a blind user, "the button is now live" must be audible, not visual. The tone fires from the client's existing `AudioPlayback` context (a short 880 Hz sine with 5 ms fades) the moment the mic actually goes live.
- **There is no "release cancel" affordance.** If the user releases before `CONVERSING` is reached (pending state), the client silently cancels the pending activation — no commit, no error — and the server stays in `CONVERSING` until the next press.
- The UI still shows the state badge (Inactivo / Conectando / Conversando / Reproduciendo) for Mario's dev debugging, but it's purely informational — grandpa never sees it.
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

**Context**: Five production bugs in rapid succession — _book jumps in without ack_, _double ack on play_, _seek stream fights model speech_, _interrupt leaves queued audio playing_, _tool instruction text leaks into speech_ — all turned out to be symptoms of one missing abstraction. We were manually coordinating _"the model's speech stream"_ and _"the tool's side-effect audio stream"_ at three different layers (session manager, application orchestrator, audiobooks skill), with a different hand-rolled mechanism in each. Every bug fix added another flag (`_pending_tool_action`, `_response_cancelled`, `_assistant_speaking`, `paused=True`, `on_audio_clear`, deferred `create_task` for the tool action, etc.) until the defer/dispatch logic in `_handle_function_call` was load-bearing in ways no single reader could hold in their head. The next skill with an audio side effect (music, news) would hit the same class of bugs and require another round of tweaks.

**Decision**: Introduce a `TurnCoordinator` as the single authority for audio sequencing around tool calls. Full spec in [`turns.md`](./turns.md). Summary:

- **Turn** is the atomic unit: one user-assistant exchange, lifecycle explicit (LISTENING → COMMITTING → SPEAKING → TOOL_DISPATCH → BARRIER → APPLYING_EFFECTS → IDLE).
- **`AudioChannel`s** (`speech`, `media`, `tone`, `status`) replace the current single `send_audio` pipe. Within a turn, `speech` drains before `media` begins — the coordinator enforces this via a barrier fired on OpenAI's `response.done` event.
- **`AudioEffect`** replaces `ToolAction`. Tools declare _"I produce a stream on channel X"_ via a factory; the skill no longer loads a paused player, waits for a state transition, or cares about timing. Factories are invoked by the coordinator only after the speech barrier.
- **`InterruptBarrier`** is first-class and atomic: on user PTT start mid-turn, flush every channel, cancel pending effects, cancel the model response. One entrypoint, one rule, replacing the current three layers of partial clearing.
- The **`PLAYING` state is removed** from the state machine. Media playback is a channel state, not a session state. The OpenAI Realtime API bills per token with no per-minute connection fee, so the original justification for disconnecting during playback ("save API cost") does not hold — idle sessions cost zero. Removing PLAYING drops first-press latency after mid-book interrupts from ~1 s (reconnection) to ~0 s.

**Consequences**:

- **Skill authors stop coordinating audio.** `AudiobooksSkill._play` returns `(ToolResult, AudioEffect)` and the coordinator handles everything downstream. Future skills (news, music, messaging) cost ~30 lines each instead of ~150.
- **All five recent bugs become impossible by construction.** Not "fixed with a new flag" — structurally impossible. The spec's §"How each current bug disappears" walks through each.
- **Protocol delta is minor but breaking in principle**: `audio` and `audio_clear` messages grow an optional `channel` field (defaults to `speech` and `all` respectively, so legacy behavior is preserved). `state` drops `PLAYING` from its enum. New optional `turn` message for dev observability. The ESP32 firmware (when built) starts on the new protocol — no migration.
- **Session manager shrinks dramatically.** `_handle_function_call` goes from ~70 lines of deferred-dispatch logic to ~10 lines of "forward to coordinator." App.py loses `_on_audiobook_chunk`/`_on_audiobook_finished`/`_on_audio_clear`/`_assistant_speaking` — maybe -250 net lines across `session/manager.py`, `app.py`, and `skills/audiobooks.py`.
- **Test coverage improves**: the coordinator state machine is unit-testable with mocked channels and events, which the current distributed defer-logic is not.
- **Migration is staged**: eight ordered steps in `turns.md` "Migration plan", each leaves the repo green. Realistic estimate **2-3 focused days** (the earlier "~1 day" was under-scoped; I flagged it in the planning review).
- **Decisions explicitly deferred**: rollback of storage side effects on interrupted turns, audible error recovery, cross-channel priority preemption, persistent turn history. All tracked as gaps in `turns.md` "Open questions."
- **Reversibility**: the introduction is additive (new types + coordinator alongside old logic) through step 4, so the refactor can be aborted mid-way if the abstraction turns out wrong in practice.
