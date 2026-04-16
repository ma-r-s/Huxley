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

- [P0] **Rename / namespace cleanup**: `abuel_os` → `huxley`. Repo path `AbuelOS/` → `Huxley/` (TBD with Mario).
- [P0] **Workspace split**: one repo, multiple uv-workspace packages: `packages/sdk/`, `packages/core/`, `packages/skills/audiobooks/`, `packages/skills/system/`. Skills become installable.
- [P0] **Persona loader**: `personas/<name>/persona.yaml` parsed at startup. Currently the persona is hard-coded in `config.py`'s default system prompt; this moves it out into config.
- [P0] **Constraint registry**: named constraints (`never_say_no`, `confirm_destructive`, `child_safe`) defined in core, composed into the system prompt by the persona.
- [P0] **Generic side effects**: `ToolResult.audio_factory` → `side_effect: SideEffect | None` where `SideEffect` is a protocol/union. Audio is the only implementation initially.
- [P1] **Skill SDK README + cookbook**: a third-party skill author can write a working skill in under 30 minutes with no Huxley-internals knowledge.

### Later

- **Voice provider abstraction**: extract OpenAI Realtime as one implementation of a `VoiceProvider` interface. Trigger: a credible second provider exists. Not speculative.
- **More side-effect kinds**: notifications, state changes, image output. Trigger: a real skill needs them.
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

### v1 — Mario's bar

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
| End-to-end smoke test with grandpa              | ❌ never tested with him directly                                    |

### v2 — next skills

Once AbuelOS v1 is stable. Each is its own `huxley-skill-*` package.

1. **`huxley-skill-news`** — read headlines from a configurable source
2. **`huxley-skill-music`** — streaming radio and local music
3. **`huxley-skill-messaging`** — outbound text to Mario / family via WhatsApp or voice memo. **This is the concrete escape hatch that makes the `never_say_no` constraint more than a verbal promise.**
4. **`huxley-skill-contacts`** — config-driven contact list that messaging depends on

### v∞ — when firmware lands

Requires Huxley framework gaps to close first (proactive speech).

- ESP32 walky-talky client — replaces browser as production client, same WebSocket protocol
- Physical always-findable button — the one UI element grandpa touches
- Proactive speech support — needed for reminders, inbound messages
- **Reminders** — meds, appointments
- **Memory / recall** — _"¿de qué hablamos ayer?"_
- **Companionship mode** — open-ended chat

### Excluded from AbuelOS

| Feature                      | Why                                                                 |
| ---------------------------- | ------------------------------------------------------------------- |
| Wake word                    | Fragile for elderly users; PTT button is more reliable              |
| Religious content            | Mario confirmed out of scope                                        |
| Privacy / no-log mode        | Not a concern for this user                                         |
| Offline operation            | Not worth the complexity for v1                                     |
| Languages other than Spanish | This persona is Spanish-only; other personas can do other languages |

(These are _AbuelOS persona_ exclusions. Other personas built on Huxley may include any of them.)
