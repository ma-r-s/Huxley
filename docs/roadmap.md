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

- ✅ **Workspace split**: one repo, multiple uv-workspace packages: `packages/sdk/`, `packages/core/`, `packages/skills/audiobooks/`, `packages/skills/system/`. Skills installable via `huxley.skills` entry points.
- ✅ **Generic side effects**: `ToolResult.side_effect: SideEffect | None` with `AudioStream` as the first kind. Coordinator dispatches by `isinstance`. Legacy `audio_factory=` still works as a deprecated alias.
- ✅ **Persona loader**: `personas/<name>/persona.yaml` parsed at startup (version, name, voice, language, timezone, system_prompt, constraints, skills). Resolution order: `HUXLEY_PERSONA` env var > default `personas/abuelos`. Data lives under `personas/<name>/data/`.
- ✅ **Constraint registry**: `never_say_no`, `confirm_destructive`, `child_safe`, `no_religious_content` defined in `huxley.constraints`; persona composes them into the system prompt at connect time. Unknown names fail at load.
- [P0] **Rename / namespace cleanup**: Repo path `AbuelOS/` → `Huxley/` (final stage of the active refactor).
- [P1] **Skill SDK README + cookbook**: a third-party skill author can write a working skill in under 30 minutes with no Huxley-internals knowledge.

### Later

- [P1] **Proactive notifications** (`ctx.notify(text)`): the single missing primitive that prevents reminders / inbound-message skills today. SDK gains a method that injects a synthetic system turn through `SessionManager`; protocol gains a server-initiated turn-start message. Should land before AbuelOS-v∞ work begins. See [`extensibility.md`](./extensibility.md) for the gap analysis.
- [P1] **Per-skill secret interpolation** in `persona.yaml`: support `${HUXLEY_TELEGRAM_TOKEN}` so personas declare the shape of the secret without storing it. Decide before stage 4 ships.
- [P2] **Background-task pattern for skills** (BLE, MQTT, polling daemons): formalize the "skill spawns an asyncio task in setup()" convention into an SDK helper so the framework can supervise / restart / log task crashes. Today it works but the framework is blind to failures.
- [P2] **Sonic UX layer** — three-earcon system (listening chirp / thinking drone / error tone) per the framework in [`research/sonic-ux.md`](./research/sonic-ux.md). Current `playThinkingTone()` violates the vocal-band masking rule (440Hz sine pulse inside the 200Hz–4kHz speech band); current silence threshold (400ms) over-triggers (framework says 1500ms). Error tone doesn't exist. Concrete deltas listed in the research doc's "Current state vs. target framework" section. Blocked on picking three sounds — needs human ear, not more code.
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

The first persona Huxley runs in production. Spec lives at [`personas/abuelos.md`](./personas/abuelos.md).

### v1 — the AbuelOS deployment bar

> _"The moment I can speak to the assistant and it helps me find a book, listen to it, and move forward, backwards, stop and resume another time — that's v1 done."_

| Capability                                      | Status                                                               |
| ----------------------------------------------- | -------------------------------------------------------------------- |
| Search for a book by natural phrase             | ✅                                                                   |
| Start playback from a search result             | ✅                                                                   |
| Pause / resume mid-sentence                     | ✅                                                                   |
| Navigate forward / backward by seconds          | ✅                                                                   |
| Navigate by chapter (_"el siguiente capítulo"_) | ❌ (chapter awareness pending)                                       |
| Stop playback                                   | ✅                                                                   |
| Resume later (_"sigue con el libro"_)           | ✅                                                                   |
| Every negative response offers an alternative   | ⚠️ partial — coverage in `search` and `control` paths still has gaps |
| End-to-end smoke test with target user          | ❌ not yet                                                           |

### v2 — next skills

Once AbuelOS v1 is stable. Each is its own `huxley-skill-*` package.

1. **`huxley-skill-news`** — read headlines from a configurable source
2. **`huxley-skill-music`** — streaming radio and local music
3. **`huxley-skill-messaging`** — outbound text to a family/caretaker contact via WhatsApp or voice memo. **This is the concrete escape hatch that makes the `never_say_no` constraint more than a verbal promise.**
4. **`huxley-skill-contacts`** — config-driven contact list that messaging depends on

### v∞ — when firmware lands

Blocked on the **Proactive notifications** framework primitive (see Huxley → Later above) — reminders and inbound messages need it. Tracking the gap in [`extensibility.md`](./extensibility.md).

- ESP32 walky-talky client — replaces browser as production client, same WebSocket protocol
- Physical always-findable button — the only UI element the user touches
- **Reminders** — meds, appointments
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
