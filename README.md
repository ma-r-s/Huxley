# Huxley

**An open-source framework for building real-time voice AI agents you can actually own.**

Huxley is a Python server. You give it a **persona** (a YAML file: name, voice, language, personality, behavioral constraints, list of skills) and a set of **skills** (Python packages that extend what the agent can do). It handles the rest: audio I/O, voice provider session, turn sequencing, tool dispatch, side-effect routing.

```bash
git clone <repo> huxley && cd huxley
echo "HUXLEY_OPENAI_API_KEY=sk-..." > packages/core/.env
uv sync && uv run huxley
# In another terminal:
cd web && bun install && bun dev
# Open http://localhost:5173, hold the button, speak.
```

---

## The problem with every other option

| Solution                     | What's wrong                                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Alexa / Google Home**      | Walled garden. Certification fees. Cloud-only. You don't control the persona, the data, or the skills.                                                                         |
| **OpenAI voice mode**        | One model, one personality. No self-hosting, no custom skills, no behavioral constraints.                                                                                      |
| **Pipecat / LiveKit Agents** | Great pipelines, blank slate. You still have to solve turn sequencing, audio collision, proactive speech, headless deployment, and the skill extensibility model from scratch. |
| **Build it yourself**        | You spend six months building plumbing instead of features.                                                                                                                    |

**Huxley's position:** opinionated enough to solve the hard problems (audio sequencing, interrupt semantics, behavioral constraints, proactive turns), open enough to extend (any skill, any persona, any client device).

---

## What Huxley handles for you

**Turn coordination** — The coordinator sequences everything through one audio channel. Model speech always finishes before tool-produced audio starts. Interrupts are atomic: drop flag → clear queue → flush client buffer → cancel response. You never hear two voices at once or a half-played sentence cut mid-word.

**Skill dispatch** — Skills are Python packages, loaded at startup via `huxley.skills` entry points. A skill declares tools (OpenAI function schemas), handles calls, and returns a result that may include an `AudioStream`, `PlaySound`, `InputClaim`, `CancelMedia`, or `SetVolume` side effect. The framework sequences all of it.

**Proactive speech** — Skills can inject turns without user input: a timer fires at 9am, an inbound call arrives, a news alert fires. `ctx.inject_turn()` queues it; `ctx.inject_turn_and_wait()` blocks until the LLM finishes speaking — useful for announcing events before bridging audio.

**Audio bridging (InputClaim)** — Skills can claim the mic and speaker for full-duplex use — p2p phone calls, voice memos, any external audio source. The framework routes the claim through a FocusManager that prevents collisions with model speech and other content streams.

**Persona-as-config** — The agent's entire identity lives in `persona.yaml`: voice, language, system prompt, behavioral constraints, skill list with per-skill config. Swap the file, get a different agent. No code change.

**Behavioral constraints** — Personas declare constraints (`never_say_no`, `confirm_destructive`, etc.); skills opt in to respecting them. Right for deploying to vulnerable users where "I can't do that" is never an acceptable response.

**Headless server** — Huxley is a WebSocket server. The browser dev client, an ESP32, a phone — anything that speaks the [wire protocol](./docs/protocol.md) owns the mic and speaker. The server owns the intelligence.

**Structured logging** — Every framework decision emits a namespaced, structured log event with turn-level context. If something breaks in production, the log tells you what happened without asking the user.

---

## First-party skills

| Skill                         | What it does                                                                                                                |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `huxley-skill-audiobooks`     | Play `.m4b`/`.mp3` audiobooks from a local library. Pause, resume, rewind, fast-forward. Persists position across restarts. |
| `huxley-skill-radio`          | Stream HTTP/Icecast radio stations via `ffmpeg`. Buffered playback with proactive reconnect on drop.                        |
| `huxley-skill-news`           | Weather (Open-Meteo) + headlines (Google News RSS). Cached, narrated in persona voice.                                      |
| `huxley-skill-timers`         | One-shot and recurring reminders. Fires `inject_turn` at the scheduled time; persisted in SQLite so they survive restarts.  |
| `huxley-skill-system`         | Volume control, current time.                                                                                               |
| `huxley-skill-comms-telegram` | Full-duplex p2p Telegram voice calls. Accepts inbound calls, places outbound, bridges mic and speaker through `InputClaim`. |

---

## Reference persona — AbuelOS

The canonical persona in the repo is **AbuelOS**: a Spanish-language companion for an elderly blind user. It enforces the `never_say_no` constraint (every request gets an attempt or a warm alternative, never a refusal), uses a slow warm voice, and enables audiobooks + radio + news + timers + Telegram calls. It's the worked example for everything in the framework — the hardest UX requirements, on the most constrained hardware.

---

## Architecture

```
┌────────────────────────────┐
│  persona.yaml              │   Identity, constraints, skill list
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  Huxley framework          │   WebSocket server, session manager,
│  (packages/core)           │   turn coordinator, focus manager,
│                            │   skill registry, storage
└────┬──────────┬────────────┘
     │          │          │
     ▼          ▼          ▼
┌─────────┐ ┌────────┐ ┌──────────────┐
│ Voice   │ │ Skills │ │ Client       │
│ provider│ │ (SDK)  │ │ (browser /   │
│ (OpenAI │ │        │ │  ESP32 / any)│
│  RT)    │ │        │ │              │
└─────────┘ └────────┘ └──────────────┘
```

The framework never imports skill code directly — skills register via Python entry points. The SDK gives skills a typed context (logger, namespaced storage, persona data dir, config, framework hooks) with no framework internals leaking through.

---

## Writing a skill

```python
# my_package/skill.py
from huxley_sdk import Skill, ToolDefinition, ToolResult, SkillContext, AudioStream

class LightsSkill:
    @property
    def name(self) -> str: return "lights"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="set_lights",
            description="Turn the lights on or off.",
            parameters={"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]},
        )]

    async def setup(self, ctx: SkillContext) -> None:
        self._api_key = ctx.config["api_key"]

    async def handle(self, tool_name: str, args: dict) -> ToolResult:
        # call your smart-home API here
        return ToolResult(output='{"ok": true}')

    async def teardown(self) -> None: ...
```

```toml
# pyproject.toml
[project.entry-points."huxley.skills"]
lights = "my_package.skill:LightsSkill"
```

Enable it in any persona:

```yaml
skills:
  lights:
    api_key: "..."
```

Full guide: [`docs/skills/README.md`](./docs/skills/README.md)

---

## Writing a persona

```yaml
# personas/myagent/persona.yaml
version: 1
name: MyAgent
voice: alloy
language_code: en
transcription_language: en
timezone: America/New_York
system_prompt: |
  You are a concise home assistant. Answer in English.
  You control lights, timers, and can read the weather.
constraints: []
skills:
  lights:
    api_key: "..."
  system: {}
  timers: {}
```

```bash
HUXLEY_PERSONA=myagent uv run huxley
```

Full guide: [`docs/personas/README.md`](./docs/personas/README.md)

---

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- [bun](https://bun.sh) (dev client only)
- `ffmpeg` + `ffprobe` on PATH (radio and Telegram skills)
- OpenAI API key with Realtime API access

---

## Development

```bash
uv sync --all-packages             # install workspace

# Lint + typecheck
uv run ruff check packages/
uv run mypy packages/sdk/src packages/core/src

# Tests (594 total)
uv run --package huxley-sdk pytest packages/sdk/tests/                            # 72
uv run --package huxley pytest packages/core/tests/                                # 352
uv run --package huxley-skill-audiobooks pytest packages/skills/audiobooks/tests/  # 61
uv run --package huxley-skill-timers pytest packages/skills/timers/tests/          # 30
uv run --package huxley-skill-news pytest packages/skills/news/tests/              # 18
uv run --package huxley-skill-radio pytest packages/skills/radio/tests/            # 19
uv run --package huxley-skill-comms-telegram pytest packages/skills/comms-telegram/tests/  # 42

# Dev client
cd web && bun run check
```

---

## Run as a background service

```bash
# macOS (launchd — starts at login, restarts on crash)
./scripts/launchd/install.sh
tail -f ~/Library/Logs/Huxley/huxley.log

# Linux — write a systemd unit, same shape
```

---

## Documentation

| Doc                                                    | What it covers                                     |
| ------------------------------------------------------ | -------------------------------------------------- |
| [`docs/vision.md`](./docs/vision.md)                   | What Huxley is and who it's for                    |
| [`docs/concepts.md`](./docs/concepts.md)               | Core vocabulary: persona, skill, turn, side effect |
| [`docs/architecture.md`](./docs/architecture.md)       | Framework internals                                |
| [`docs/protocol.md`](./docs/protocol.md)               | WebSocket wire protocol for clients                |
| [`docs/turns.md`](./docs/turns.md)                     | Turn coordinator spec                              |
| [`docs/skills/README.md`](./docs/skills/README.md)     | Skill authoring guide (full SDK surface)           |
| [`docs/personas/README.md`](./docs/personas/README.md) | Persona authoring guide                            |
| [`docs/extensibility.md`](./docs/extensibility.md)     | What the framework can and can't do today          |
| [`docs/observability.md`](./docs/observability.md)     | Logging conventions + debugging workflow           |
| [`docs/decisions.md`](./docs/decisions.md)             | Architectural decision log                         |
| [`docs/roadmap.md`](./docs/roadmap.md)                 | What's next                                        |

---

**Status:** pre-1.0 — framework runs end-to-end, AbuelOS persona is in daily use. Contributions welcome.

**License:** MIT
