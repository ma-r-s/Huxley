# `huxley-web` — UI surface inventory

> **How to use this doc**: paste alongside [`huxley-web-brief.md`](./huxley-web-brief.md) into your AI design tool. The brief describes what the app **is**; this doc enumerates every signal, message, capability, and config surface the app must **render**. It's the contract the tool maps to concrete UI slots.
>
> Last updated: 2026-04-19. Lives in the `huxley` repo at `docs/research/huxley-web-ui-surfaces.md`.

Everything below is grouped as **TODAY** (shipped; the PWA must render these for v1) vs **PLANNED** (framework work not yet shipped; the design should accommodate a place for these so we don't rebuild the navigation when they land).

---

## 1. Device state surface — TODAY

The server sends a `state` message any time the device's finite-state machine transitions. The PWA must visibly reflect state at all times — this is the single most-load-bearing signal in the UI because a blind user relies on audio cues + a sighted admin relies on the visual reflection.

| State        | What it means to the user                                                                 | Typical duration          |
| ------------ | ----------------------------------------------------------------------------------------- | ------------------------- |
| `IDLE`       | Device is off the voice session. Button press wakes it.                                   | Hours to days between use |
| `CONNECTING` | Spinning up the OpenAI Realtime session. User can hold PTT now; audio queues until ready. | ~1–2 seconds, rarely more |
| `CONVERSING` | Session is live. Press-and-hold is instant.                                               | Active use session        |

Implicit sub-states the PWA should distinguish visually while in `CONVERSING`:

- **Waiting for user** — idle, ready to listen.
- **Listening** — user is holding PTT; mic is capturing.
- **Committing** — user released; mic buffer sent; awaiting model response.
- **Responding** — Huxley is speaking back (see `model_speaking: true`).
- **Content playing** — a long-form audio stream is running (audiobook, radio, news read-aloud). User can interrupt with PTT.

The `state` enum + `model_speaking` boolean + presence of audio chunks in the last N seconds together determine which sub-state the UI is in. The brief's Tier-0 use-case #3 ("device status at a glance") maps to this.

---

## 2. Status string surface — TODAY

Parallel to `state`, the server sends short human-readable `status` strings (Spanish, configurable per persona). These are meant to appear in a persistent status strip somewhere in the UI — not a toast / not a banner, a steady-state label the user can glance at.

Vocabulary (AbuelOS persona, as shipped):

- `"Escuchando… (suelta para enviar)"` — while listening
- `"Muy corto — mantén el botón mientras hablas"` — input too short
- `"Enviado — esperando respuesta…"` — committed, awaiting model
- `"Respondiendo…"` — model is speaking
- `"Listo — mantén el botón para responder"` — idle ready

These are persona-configured (a different persona ships different strings). The PWA just renders the current string; the server updates it as state changes.

---

## 3. Transcript surface — TODAY

The server sends one `transcript` message per turn of conversation, typed by role:

```
{"type": "transcript", "role": "user" | "assistant", "text": "..."}
```

Transcript messages arrive as the conversation progresses; `user` lines come from Whisper transcription of the uploaded PCM, `assistant` lines come from the model's output. A conversation is a stream of alternating turns.

UI requirements:

- Live-scrolling conversation view (chat-app-style).
- Distinguish user-said vs Huxley-said visually.
- Accessible: readable font, adequate contrast, works with screen readers.
- Scrollable history within the current session.
- No edit, no delete, no search in v1.
- Transcripts vanish on session end for v1 (no persistence); Phase 2 adds local history.

Message-timing: transcripts can arrive before, during, or after the matching audio. Don't block rendering on audio arrival — they're independent streams.

---

## 4. Audio I/O surface — TODAY

### Output (Huxley → user's ears)

The server streams `audio` messages with base64 PCM16 @ 24 kHz mono. Expected rate: one frame every 20–50 ms while Huxley is speaking or playing content.

Controls the PWA may offer:

- **Volume** (client-local, not sent back to server; or proxied through the `set_volume` message described below).
- **Interrupt** — this is just PTT; holding the button while audio is playing pre-empts it on the server side via `audio_clear`.
- **Mute** (future; not in the protocol today).

The `set_volume` server→client message asks the PWA to adjust playback volume. Some hardware clients (ESP32) will drive a physical volume; the PWA can ignore or reflect visually. The `system` skill's `set_volume` tool (dispatched when user says "sube el volumen") triggers this.

### Input (user's mic → Huxley)

Press-and-hold to talk. The PWA sends `ptt_start` + streams `audio` chunks + sends `ptt_stop`. See the brief for the AudioWorklet contract.

The hold-to-talk affordance is the single most important control in the UI. It has to be:

- Always accessible (large, reachable, available without navigating)
- Tactile-friendly (works on touch, pointer, spacebar)
- Visually reactive (shows pressed state; shows "listening" signal)

---

## 5. Model-speaking signal — TODAY

```
{"type": "model_speaking", "value": true|false}
```

Fires on the edges of Huxley's speech — `true` when first audio chunk goes out, `false` when the last one ends. Useful for:

- A visual "Huxley is speaking" indicator (pulsing waveform, animated dot, etc.).
- Showing the interrupt affordance prominently only while the signal is `true` (nothing to interrupt when it's `false`).
- Muting transcript-arrival animation until speech starts (so the transcript doesn't animate before the audio plays).

---

## 6. Dev events surface — TODAY

```
{"type": "dev_event", "kind": "...", "payload": {...}}
```

Structured observability stream. The server emits these for every skill action, framework decision, and tool dispatch. Current `kind`s include:

- `tool_call` — payload: `{name, args, output, has_audio_stream}`. One per tool dispatch.
- `tool_error` — payload: `{name, exception_class, message}`. When a skill raised.
- `background_task_failed` — permanent failure from `background_task` supervisor.

In the PWA these are developer-tier signals. Renders as a collapsible debug panel, not the main UI. Use them to power:

- A "what did Huxley just do" timeline (useful for Mario debugging his persona).
- An admin "health view" that surfaces recent errors.

V1 scope: show when the admin explicitly opens a debug panel, hide by default. Not user-facing.

---

## 7. Dev / admin events the server emits but the PWA may want — TODAY

These are already logged server-side but not pushed to the client; the server **could** forward them as dev_events with minor work (Phase 2). Listed here so the design accommodates a place for them:

- Recent `transcript` history for past sessions (if storage persists them)
- Active `timer:<id>` entries (the timers skill persists these to its SkillStorage)
- Current audiobook position (audiobooks skill persists per-book progress to storage)
- Radio currently-playing station id
- Cost / usage telemetry (the cost-tracker observes OpenAI usage)

Design implication: plan for a "what's active right now" panel that can show small cards for each of these when Phase 2 plumbs them through.

---

## 8. Config / settings surfaces — TODAY (server side) / PLANNED (PWA exposure)

The server reads config from `persona.yaml` + env vars at startup. Today there's no protocol message to edit config at runtime; the Huxley user edits the YAML and restarts the server.

For the PWA, plan for an admin screen that exposes:

| Field                                 | Scope   | Where it lives today                 |
| ------------------------------------- | ------- | ------------------------------------ |
| `HUXLEY_OPENAI_API_KEY`               | Device  | `server/runtime/.env`                 |
| `HUXLEY_PERSONA` (which persona runs) | Device  | env var                              |
| Voice provider model                  | Device  | `HUXLEY_OPENAI_MODEL` env var        |
| Persona voice (e.g. `coral`, `alloy`) | Persona | `persona.yaml` → `voice`             |
| Persona language (`es`, `en`, …)      | Persona | `persona.yaml` → `language_code`     |
| Persona system prompt                 | Persona | `persona.yaml` → `system_prompt`     |
| Persona behavioral constraints        | Persona | `persona.yaml` → `constraints`       |
| Enabled skills                        | Persona | `persona.yaml` → `skills:` map       |
| Per-skill config (below)              | Skill   | Each skill's block in `persona.yaml` |
| Device data dir path                  | Device  | implicit (`server/personas/<name>/data/`)   |

The admin UI can surface all of these. V1 probably only exposes API-key + persona picker; the rest is Phase 2 when an edit-and-save protocol message exists.

---

## 9. Skill catalog — TODAY (what capabilities the device has)

Each entry: skill name + the tools it exposes + what kind of UI surface each one might plausibly want in Phase 2 (when `ClientEvent` ships and skills can push widgets). The design tool can use this to design for the shape even though rendering isn't hooked up yet.

### audiobooks (`huxley-skill-audiobooks`)

Long-form spoken-word playback with bookmark resume.

**Tools** (the LLM invokes these when the user asks):

- `search_audiobooks(query)` — fuzzy search library, returns candidate matches.
- `play_audiobook(book_id, from_beginning?)` — start playback at the last-known position (or from zero).
- `resume_last()` — resume the most recently played book.
- `audiobook_control(action)` — `pause` | `stop` | `forward_30s` | `backward_30s` | `set_speed`.
- `get_progress(book_id)` — current position in a book.
- `list_in_progress()` — all books with saved progress.

**Plausible UI surface** (Phase 2):

- A "Now Playing" card: book title, author, cover, current position / total, playback speed, progress bar, play/pause + seek + speed controls.
- A "Library" panel: list of books with per-book progress indicators, tap to resume.
- A "Bookmark" affordance: add-bookmark-at-current-position.

### news (`huxley-skill-news`)

Open-Meteo weather + Google News RSS summarization.

**Tools**:

- `get_news()` — return today's headlines (configured country, interests, language).
- `get_weather()` — current weather at configured location.

**Plausible UI surface**:

- A "Today" card: weather + top 3 headlines, tap a headline to have Huxley read it.
- No interactive feed; this skill is voice-first by design.

### radio (`huxley-skill-radio`)

HTTP/Icecast streams via ffmpeg.

**Tools**:

- `list_stations()` — all configured stations.
- `play_station(station_id)` — start streaming one.
- `stop_radio()` / `resume_radio()`.

**Plausible UI surface**:

- A "Stations" grid/list with station names + descriptions, tap to play.
- A "Now Playing" strip when a station is active: station name, stop button.
- (Per-station metadata like "currently playing track" isn't available — radio is live, Huxley doesn't know what's on air right now.)

### timers (`huxley-skill-timers`)

One-shot proactive reminders; persistent across server restart.

**Tools**:

- `set_timer(seconds, message)` — schedule a reminder. When it fires, Huxley speaks the message.

**Plausible UI surface**:

- "Active timers" list: each shows countdown + message + cancel affordance.
- "Set a timer" quick-add (textbox + duration picker), same backend as the voice flow.

### system (`huxley-skill-system`)

Device/time basics.

**Tools**:

- `set_volume(level)` — set output volume 0-100.
- `get_current_time()` — current time in the persona's timezone.

**Plausible UI surface**:

- A volume slider (already protocol-exposed; `set_volume` server→client message fires).
- No UI for `get_current_time` — it's informational.

### `huxley-skill-telegram` — T1.10 / T1.11

Outbound Telegram calls / voice messages / text via the maintained `py-tgcalls` + `kurigram` stack. See [`telegram-voice.md`](./telegram-voice.md).

**Planned tools** (shape not final):

- `call(contact_name)` — place a Telegram voice call to a configured contact.
- `send_voice_message(contact_name, text)` — bot records-and-sends a voice note.
- `send_text(contact_name, text)` — bot sends a text message.

**Plausible UI surface**:

- A "Contacts" panel showing configured recipients (admin manages this).
- An "Active call" card when a call is in progress: contact name, duration, hang-up.
- A "Recent messages sent/received" log (nice-to-have).

### (planned) emergency / panic — F1

Not yet a skill. Triggered by voice intent ("tuve un accidente"); uses the same comms transport (Telegram, Twilio, or whatever's wired) to alert configured contacts with high priority.

**Plausible UI surface**:

- Nothing in the user's own PWA (the user IS the one needing help).
- On the device-admin side of the PWA: a "configure panic contacts" screen (who to alert, in what order, what threshold-of-intent-confidence to trust).

---

## 10. Persona and its UI-relevant fields — TODAY

The persona YAML exposes several fields the PWA can surface directly:

| Field                    | UI use                                                                 |
| ------------------------ | ---------------------------------------------------------------------- |
| `name`                   | Display the running persona's name (e.g. "AbuelOS") in an admin header |
| `voice`                  | Voice ID (read-only display; change requires server restart for v1)    |
| `language_code`          | Page language for UI strings (Spanish for AbuelOS; English for Basic)  |
| `transcription_language` | Same                                                                   |
| `timezone`               | Formatting for timestamps in the transcript / timers UI                |
| `ui_strings`             | Persona-configurable localized strings (e.g. `listening:`, `ready:`)   |

The `ui_strings` block (see `server/personas/abuelos/persona.yaml`) is deliberately client-agnostic and meant for clients like the PWA to use. Currently the `clients/pwa/` dev client respects these; `huxley-web` should too.

---

## 11. Persistence layer — what already persists across server restart

The PWA can assume these exist even after a server restart (which will be automatic + invisible once `huxley-firmware` / OrangePi5 deployment is real):

- Audiobook positions (per book_id → position_s)
- Conversation summaries (latest + history)
- Timer entries (any pending timer survives a restart; fires when due)
- Generic settings KV (other skills' persisted state)

Not persisted: live session transcript (Phase 2), active radio stream (Phase 2), current cost telemetry (Phase 2).

---

## 12. What is deliberately NOT a UI surface

Clearly out of scope — the design tool should not try to find UI for these:

- **Framework logs** (`logger.ainfo` structured events) — these are server-side only. The PWA sees a subset via `dev_event`, but raw logs live in `logs/huxley.log` on the device, read via SSH.
- **Skill authoring** — writing new skills is a Python task; the PWA doesn't ship a code editor.
- **Persona authoring** — YAML editing; the PWA doesn't ship a YAML editor for v1 (maybe for persona.system_prompt Phase 3, not sooner).
- **Tool results** — the tool's response text goes back to the LLM, which narrates it; the PWA never directly renders a tool's raw output.
- **Audio hardware config** — the PWA doesn't pick mics or speakers; the browser owns audio devices.

---

## 13. Design-time decisions the design tool can make freely

Things that are **not** spec'd here and are up to the design conversation:

- Visual identity (colors, type, motion language, illustration)
- Layout and navigation pattern (tabs, sidebar, dashboard, single-screen)
- Specific widget designs for the skill surfaces listed above
- Copy beyond the functional strings listed in §2
- Empty states, onboarding, install prompts, error copy
- How admin-only features are gated (toggle, separate screen, long-press, etc.)
- Whether transcripts scroll or modal, whether they auto-hide, etc.
- Tone of animation (calm vs lively)
- Accessibility features beyond screen reader + large text (reduced motion, high contrast, etc. — best-practice defaults apply)

These are design choices. Mario works them out with the tool; this doc deliberately doesn't prescribe them.

---

## 14. What the design tool should ask for when unclear

When the tool has a question about "should this widget behave A or B":

- If the answer depends on Huxley's behavior (what the device does, what a skill exposes), the answer is in this doc or [`protocol.md`](../protocol.md). Cite the section.
- If the answer is a UX preference, ask Mario.
- If the answer depends on data the PWA doesn't have yet (something behind Phase 2 work), design the slot for the data to arrive later, leave it empty or "not available yet" in v1.
