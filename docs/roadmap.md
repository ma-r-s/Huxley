# Roadmap

Two roadmaps live here: **Huxley** (the framework) and **Abuelo** (the first persona). Each evolves at its own pace; the framework moves slower (stable surface area for skill authors) than personas (config tweaks ship continuously).

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
- ✅ **Rename / namespace cleanup**: repo path and Python namespace both on `huxley`; _Abuelo_ is the persona.
- ✅ **Sound UX layer (server-side)** — `AudioStream` carries `on_complete_prompt` + `completion_silence_ms`; coordinator creates a synthetic IN_RESPONSE turn for the LLM-narrated end-of-content announcement, fires `request_response` BEFORE the silence buffer so model latency overlaps with silence playback. Skill loads sound palette via `wave.open()`. Persona owns `sounds_path` / `sounds_enabled` / `silence_ms` / `on_complete_prompt`. Full design + critic-list state in [`sounds.md`](./sounds.md).
- ✅ **Sound UX layer (client-side)** — thinking tone at 120Hz (out of vocal band, can't mask speech); silence threshold raised to 1500ms (was 400ms — over-triggered constantly); descending 660→330Hz error chime fires on session drop so a blind user can distinguish "device crashed" from "still working." Stage D in [`sounds.md`](./sounds.md).
- ✅ **`PlaySound` SideEffect primitive + shared `huxley_sdk.audio` helper** — info tools can emit a one-shot chime that lands on the WebSocket ahead of the model's response audio (FIFO). Audiobooks + news both use it; news skill (`get_news`) is the canonical example.
- ✅ **Second first-party skill (`huxley-skill-news`)** — proves the persona-agnostic abstraction. Same skill, totally different audio for Abuelo (slow + chime) vs Basic (terse + no chime). See [`skills/news.md`](./skills/news.md) and [`server/personas/basic.md`](./personas/basic.md).
- ✅ **Web UI persona dropdown** — `clients/pwa/.env.local`'s `VITE_HUXLEY_PERSONAS` lists `name:url` pairs; the header dropdown switches the active WebSocket connection cleanly.
- ✅ **Skill SDK README + cookbook**: a third-party skill author can write a working skill in under 30 minutes with no Huxley-internals knowledge. See [`skills/README.md`](./skills/README.md) (SDK reference), [`skills/authoring.md`](./skills/authoring.md) (build-your-first-skill walkthrough), [`skills/installing.md`](./skills/installing.md) (operator-side install + smoke), [`skills/index.md`](./skills/index.md) (directory of known skills).
- ✅ **Skill marketplace v1 (T1.14)**: per-skill secrets at `<persona>/data/secrets/<skill>/values.json` via async `ctx.secrets`; optional `Skill.config_schema` (JSON Schema 2020-12 with `format: secret` + `x-huxley:help` extensions); optional `Skill.data_schema_version` persisted in `schema_meta` with mismatch warnings (no auto-migration in v1). Reference skill: [`huxley-skill-stocks`](https://pypi.org/project/huxley-skill-stocks/). See [`skill-marketplace.md`](./skill-marketplace.md). v2 (caregiver-installer UX, PWA Skills panel, JSON registry, self-restart machinery) is deferred.
- ✅ **PyPI release** — [`huxley-sdk`](https://pypi.org/project/huxley-sdk/) at 0.1.1 + 9 first-party skills at 0.1.0 (audiobooks, news, radio, reminders, search, stocks, system, telegram, timers). Every skill installs externally via `uv add huxley-skill-<name>`; the wheel METADATA pulls `huxley-sdk>=0.1.1,<0.2` from PyPI automatically. The marketplace is genuinely external — anyone with a Huxley checkout can `uv add` any skill from anywhere on the internet.
- ✅ **Discovery registry** — [`ma-r-s/huxley-registry`](https://github.com/ma-r-s/huxley-registry) — Tier 1 of the v2 marketplace per [`skill-marketplace.md` § Marketplace v2 research](./skill-marketplace.md). Static JSON Schema + index.json + per-skill detail files; PR-curated; federated by fork; canonical feed at https://raw.githubusercontent.com/ma-r-s/huxley-registry/main/index.json. v2 (Tier 2 dynamic API + ratings + Tier 3 PWA install button) deferred until first community skill submission justifies the work.

### Later

- ✅ **Proactive notifications** (`ctx.inject_turn` / `ctx.inject_turn_and_wait`) — shipped via the focus-plane completion (T1.4 Stage 2b/3/5). First production consumers are `huxley-skill-timers` (medication reminders) and `huxley-skill-telegram` (inbound-message announcements + post-restart unread backfill).
- ~~[P1] Per-skill secret interpolation in `persona.yaml`~~ — superseded by T1.14's `ctx.secrets` API. Per-skill secrets now live in `<persona>/data/secrets/<skill>/values.json`, not interpolated into YAML.
- [P2] **Background-task pattern for skills** (BLE, MQTT, polling daemons): formalize the "skill spawns an asyncio task in setup()" convention into an SDK helper so the framework can supervise / restart / log task crashes. Today it works but the framework is blind to failures.
- **Voice provider abstraction**: extract OpenAI Realtime as one implementation of a `VoiceProvider` interface. Trigger: a credible second provider exists. Not speculative.
- **More side-effect kinds**: state changes, image output. Trigger: a real skill needs them.
- **Skill discovery aids**: `huxley list-installed-skills`, `huxley enable foo` CLIs that mutate `persona.yaml`. Polish, not blocking anything.
- **MCP compatibility shim**: optional adapter so existing MCP servers can be loaded as Huxley skills. Whole separate project; only if the ecosystem wants it.

### Speculative shapes — not committed work

Two ideas the architecture leaves room for but that aren't being built. Captured here so they don't get lost; both appeared in early landing copy and were cut when the landing was tightened.

#### Marketplace v2 — caregiver-installer UX

Marketplace v1 (T1.14) shipped the developer-primary path: SDK additions, authoring conventions, a worked third-party reference skill, and a static markdown directory page that takes PRs. v2 layers a **caregiver-friendly install + configure UX** on top of v1's primitives — the same skills, a different audience.

The pieces v2 adds (purely additive — no v1 rewrites):

- **PWA Skills panel**: WS endpoints + a sheet that reads installed skills, renders forms from each skill's `config_schema`, writes config back to `persona.yaml` via `ruamel.yaml` round-trip, writes secrets to the per-persona dir, toggles enable/disable.
- **Self-restart machinery**: `install_skill` server endpoint + pip orchestration + atomic-swap-venv approach. The hard part — `os.execv` has real foot-guns (SQLite WAL torn state, partial pip-install bricks the venv, C-extension compile time on a Pi can be 60-90s).
- **Curated registry**: a separate `huxley/skills` GitHub repo with `index.json`, JSON Schema, CI, tier system (official vs community), and a Marketplace tab in the PWA.
- **Real OAuth**: SDK helper + redirect URL handler in the runtime + PWA "Authenticate with X" button. Uses v1's flat-secret + JSON-encode convention internally — same on-disk bytes.
- **`set_json` / `get_json`** typed accessors on `SkillSecrets` — sugar over v1's `set/get` with `json.dumps/json.loads`. Same on-disk bytes; v1 callers keep working.

**Not shipping because:** v1's developer-primary surface is the cheapest test of the marketplace thesis ("third-party skills will get written"). If real third-party skills emerge from v1, v2 earns its place. **Trigger to start:** a credible cohort of caregiver-installers materializes (i.e. v1 has produced enough community skills that the install-via-terminal ceremony is the bottleneck), AND we've decided to ship Huxley to non-developer households.

#### `huxley-grows` — build agent that writes new skills from voice

A higher-tier skill that takes a request like _"make me a skill that reads my Notion inbox,"_ runs an LLM build agent against the Huxley SDK, generates a candidate `huxley-skill-notion` package, lints/tests it, and offers to install. Failure modes are first-class: if the build can't be done (no API, ambiguous spec, broken auth), the agent says so precisely instead of producing a hallucinated capability.

This sits on top of the SDK and the marketplace registry — the build agent's output is just another `huxley-skill-*` package, and install goes through the same registry. From the user's perspective, find-or-build is the same ceremony: a new skill on your box, same API, same permissions, same voice command.

**Not shipping because:** needs marketplace v2's curated registry first, and the quality bar is high (a hallucinated skill that _almost_ works is worse than a refusal). **Trigger to start:** v2 registry exists with enough surface area that find-misses are common, and a separate experiment shows agentic skill generation can hit a usable success rate against the SDK.

### Excluded from Huxley framework

| Feature                          | Why                                                                                                                                                                                                                |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Multi-tenant / SaaS              | Different product. One person → one agent.                                                                                                                                                                         |
| Wake-word as framework feature   | Persona-level concern; framework supports any client input model                                                                                                                                                   |
| Cross-language skill authoring   | Python-only for v1. MCP shim handles cross-language someday.                                                                                                                                                       |
| Closed-source / SaaS marketplace | Skills are open-source, distributed via PyPI. v1 directory is a static markdown page in this repo (`docs/skills/index.md`); v2 is a curated GitHub repo with `index.json`. No server-hosted catalog, no paid tier. |
| Multi-user voice                 | One person at a time. Multi-voice = different system entirely.                                                                                                                                                     |

## Abuelo persona

The first persona Huxley runs in production. Spec lives at [`server/personas/abuelos.md`](./personas/abuelos.md).

### v1 — the Abuelo deployment bar

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

### Excluded from Abuelo

| Feature                      | Why                                                                 |
| ---------------------------- | ------------------------------------------------------------------- |
| Wake word                    | Fragile for elderly users; PTT button is more reliable              |
| Religious content            | out of scope by persona declaration                                 |
| Privacy / no-log mode        | Not a concern for this user                                         |
| Offline operation            | Not worth the complexity for v1                                     |
| Languages other than Spanish | This persona is Spanish-only; other personas can do other languages |

(These are _Abuelo persona_ exclusions. Other personas built on Huxley may include any of them.)
