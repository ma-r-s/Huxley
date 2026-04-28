# Roadmap

Two roadmaps live here: **Huxley** (the framework) and **AbuelOS** (the first persona). Each evolves at its own pace; the framework moves slower (stable surface area for skill authors) than personas (config tweaks ship continuously).

## Huxley framework

### Built today

| Component                                            | Status                                                          |
| ---------------------------------------------------- | --------------------------------------------------------------- |
| WebSocket audio server (Python)                      | ✅ built — single client at a time                              |
| OpenAI Realtime API integration                      | ✅ built — auto-connect at startup, auto-reconnect on drop      |
| Skill registry + dispatch                            | ✅ built                                                        |
| 3-state session machine (IDLE/CONNECTING/CONVERSING) | ✅ built — media playback orthogonal, owned by turn coordinator |
| Turn coordinator (audio sequencing + interrupts)     | ✅ built — see [`turns.md`](./turns.md)                         |
| ffmpeg-based audiobook streamer                      | ✅ built (`AudiobookPlayer.stream()` async generator)           |
| SQLite storage                                       | ✅ built                                                        |
| Diagnostic logging (namespaced events, turn context) | ✅ built — see [`observability.md`](./observability.md)         |
| Browser dev client (SvelteKit, one-button PTT)       | ✅ end-to-end audio path; thinking-tone gap filler              |

### Next: framework-product alignment

Make Huxley what it claims to be in [`vision.md`](./vision.md): a framework anyone can extend.

- ✅ **Workspace split**: one repo, multiple uv-workspace packages: `server/sdk/`, `server/runtime/`, `server/skills/<name>/` for each first-party skill. Skills installable via `huxley.skills` entry points.
- ✅ **Generic side effects**: `ToolResult.side_effect: SideEffect | None` with `AudioStream` as the first kind. Coordinator dispatches by `isinstance`.
- ✅ **Persona loader**: `server/personas/<name>/persona.yaml` parsed at startup (version, name, voice, language, timezone, system_prompt, constraints, skills). Resolution order: `HUXLEY_PERSONA` env var > default `server/personas/abuelos`. Data lives under `server/personas/<name>/data/`.
- ✅ **Constraint registry**: `never_say_no`, `confirm_destructive`, `child_safe`, `no_religious_content` defined in `huxley.constraints`; persona composes them into the system prompt at connect time. Unknown names fail at load.
- ✅ **Rename / namespace cleanup**: repo path and Python namespace both on `huxley`; _AbuelOS_ is the persona.
- ✅ **Sound UX layer (server-side)** — `AudioStream` carries `on_complete_prompt` + `completion_silence_ms`; coordinator creates a synthetic IN_RESPONSE turn for the LLM-narrated end-of-content announcement, fires `request_response` BEFORE the silence buffer so model latency overlaps with silence playback. Skill loads sound palette via `wave.open()`. Persona owns `sounds_path` / `sounds_enabled` / `silence_ms` / `on_complete_prompt`. Full design + critic-list state in [`sounds.md`](./sounds.md).
- ✅ **Sound UX layer (client-side)** — thinking tone at 120Hz (out of vocal band, can't mask speech); silence threshold raised to 1500ms (was 400ms — over-triggered constantly); descending 660→330Hz error chime fires on session drop so a blind user can distinguish "device crashed" from "still working." Stage D in [`sounds.md`](./sounds.md).
- ✅ **`PlaySound` SideEffect primitive + shared `huxley_sdk.audio` helper** — info tools can emit a one-shot chime that lands on the WebSocket ahead of the model's response audio (FIFO). Audiobooks + news both use it; news skill (`get_news`) is the canonical example.
- ✅ **Second first-party skill (`huxley-skill-news`)** — proves the persona-agnostic abstraction. Same skill, totally different audio for AbuelOS (slow + chime) vs BasicOS (terse + no chime). See [`skills/news.md`](./skills/news.md) and [`server/personas/basicos.md`](./personas/basicos.md).
- ✅ **Web UI persona dropdown** — `clients/pwa/.env.local`'s `VITE_HUXLEY_PERSONAS` lists `name:url` pairs; the header dropdown switches the active WebSocket connection cleanly.
- [P1] **Skill SDK README + cookbook**: a third-party skill author can write a working skill in under 30 minutes with no Huxley-internals knowledge.

### Later

- ✅ **Proactive notifications** (`ctx.inject_turn` / `ctx.inject_turn_and_wait`) — shipped via the focus-plane completion (T1.4 Stage 2b/3/5). First production consumers are `huxley-skill-timers` (medication reminders) and `huxley-skill-telegram` (inbound-message announcements + post-restart unread backfill).
- [P1] **Per-skill secret interpolation** in `persona.yaml`: support `${HUXLEY_TELEGRAM_TOKEN}` so personas declare the shape of the secret without storing it. Decide before stage 4 ships.
- [P2] **Background-task pattern for skills** (BLE, MQTT, polling daemons): formalize the "skill spawns an asyncio task in setup()" convention into an SDK helper so the framework can supervise / restart / log task crashes. Today it works but the framework is blind to failures.
- **Voice provider abstraction**: extract OpenAI Realtime as one implementation of a `VoiceProvider` interface. Trigger: a credible second provider exists. Not speculative.
- **More side-effect kinds**: state changes, image output. Trigger: a real skill needs them.
- **Skill discovery aids**: `huxley list-installed-skills`, `huxley enable foo` CLIs that mutate `persona.yaml`. Polish, not blocking anything.
- **MCP compatibility shim**: optional adapter so existing MCP servers can be loaded as Huxley skills. Whole separate project; only if the ecosystem wants it.

### Excluded from Huxley framework

| Feature                        | Why                                                              |
| ------------------------------ | ---------------------------------------------------------------- |
| Multi-tenant / SaaS            | Different product. One person → one agent.                       |
| Wake-word as framework feature | Persona-level concern; framework supports any client input model |
| Cross-language skill authoring | Python-only for v1. MCP shim handles cross-language someday.     |
| Marketplace / registry         | Open-source, distributed via PyPI. Registry is way later.        |
| Multi-user voice               | One person at a time. Multi-voice = different system entirely.   |

## AbuelOS persona

The first persona Huxley runs in production. Spec lives at [`server/personas/abuelos.md`](./personas/abuelos.md).

### v1 — the AbuelOS deployment bar

> _"The moment I can speak to the assistant and it helps me find a book, listen to it, and move forward, backwards, stop and resume another time — that's v1 done."_

| Capability                                      | Status                                                                                    |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Search for a book by natural phrase             | ✅                                                                                        |
| Start playback from a search result             | ✅                                                                                        |
| Pause / resume mid-sentence                     | ✅                                                                                        |
| Navigate forward / backward by seconds          | ✅                                                                                        |
| Navigate by chapter (_"el siguiente capítulo"_) | ❌ (chapter awareness pending)                                                            |
| Stop playback                                   | ✅                                                                                        |
| Resume later (_"sigue con el libro"_)           | ✅                                                                                        |
| Every negative response offers an alternative   | ⚠️ partial — coverage in `search` and `control` paths still has gaps                      |
| End-of-book announcement (earcon + model voice) | ✅ shipped — see [`sounds.md`](./sounds.md); curated chimes still pending CC0 replacement |
| End-to-end smoke test with target user          | ❌ not yet                                                                                |

### v2 — next skills

Each is its own `huxley-skill-*` package.

1. ✅ **`huxley-skill-news`** — Open-Meteo + Google News RSS, persona-agnostic. See [`skills/news.md`](./skills/news.md).
2. ✅ **`huxley-skill-radio`** — curated HTTP/Icecast streams via ffmpeg. See [`skills/radio.md`](./skills/radio.md).
3. ✅ **`huxley-skill-telegram`** — full-duplex p2p voice calls AND text messages over a single Pyrogram userbot session. See [`skills/telegram.md`](./skills/telegram.md). Provides the concrete escape hatch that makes the `never_say_no` constraint more than a verbal promise (the LLM can _do_ something instead of refusing).
4. **`huxley-skill-music`** — local music library (separate from radio; library management + search)

### v∞ — when firmware lands

Proactive notifications primitive shipped (`ctx.inject_turn`); both reminders and inbound messages already have working consumers. The remaining v∞ items below are hardware + persona-shaped, not framework-shaped.

- ESP32 walky-talky client — replaces browser as production client, same WebSocket protocol
- Physical always-findable button — the only UI element the user touches
- **Reminders** — meds, appointments (timers skill ships the primitive; medication-specific UX still needs persona-side work)
- **Memory / recall** — _"¿de qué hablamos ayer?"_
- **Companionship mode** — open-ended chat

### Excluded from AbuelOS

| Feature                      | Why                                                                 |
| ---------------------------- | ------------------------------------------------------------------- |
| Wake word                    | Fragile for elderly users; PTT button is more reliable              |
| Religious content            | out of scope by persona declaration                                 |
| Privacy / no-log mode        | Not a concern for this user                                         |
| Offline operation            | Not worth the complexity for v1                                     |
| Languages other than Spanish | This persona is Spanish-only; other personas can do other languages |

(These are _AbuelOS persona_ exclusions. Other personas built on Huxley may include any of them.)
