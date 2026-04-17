# Huxley

> Voice agent framework. Personas declare who the agent is; skills declare what it does.

Huxley is a Python framework for building real-time voice agents. You give it a **persona** (a YAML file with name, voice, language, personality, and a list of enabled skills) and a set of **skills** (Python packages that expose tools to the LLM). Huxley handles the rest: WebSocket audio I/O, voice provider integration, turn coordination, tool dispatch, side-effect sequencing.

The dream: adding a capability to your voice agent should be as easy as `pip install huxley-skill-foo` plus one line in your persona file.

## What it does

- **Real-time voice conversation** — bidirectional PCM16 audio over WebSocket, currently via OpenAI Realtime API.
- **Skill system** — third-party Python packages, loaded via entry points (planned), declaring tools the LLM can call. Each tool can return text and/or a side effect (audio stream, future: notifications, state changes).
- **Turn coordinator** — a state machine that sequences model speech and tool-produced audio so the agent's spoken acknowledgement always plays before its actions, with atomic interrupt semantics.
- **Persona-as-config** — agent identity (name, voice, language, personality, behavioral constraints, skill list) lives in YAML, not code.
- **Headless** — Huxley is a server. Clients (browser dev UI, ESP32 hardware, anything that speaks the WebSocket protocol) own the microphone and speaker.
- **Structured logging** — every framework decision and skill action emits a namespaced event with turn-level context, designed so problems can be diagnosed by reading the log.

## What it doesn't do

- Not a chatbot framework — Huxley is voice-first, with audio as the primary modality.
- Not multi-tenant — one user, one agent. Multi-user SaaS is out of scope.
- Not a model — Huxley wraps OpenAI's Realtime API today; the architecture leaves room for other providers, but it doesn't train or serve models.

## Status

Pre-1.0. Active refactor in progress to extract skills as installable packages and introduce a YAML persona loader. The framework runs end-to-end against a browser dev client; one persona is shipping (Spanish-language assistant with audiobook playback, system controls). See [`docs/roadmap.md`](./docs/roadmap.md).

## Quick start

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), [bun](https://bun.sh), and `ffmpeg` + `ffprobe` on PATH.

```bash
git clone <repo-url> huxley
cd huxley
uv sync
echo "HUXLEY_OPENAI_API_KEY=sk-..." > packages/core/.env

# Run the framework (currently must be from packages/core/ — fixed in next refactor)
cd packages/core && uv run huxley
```

In another terminal, run the dev client:

```bash
cd web
bun install
bun dev
```

Open `http://localhost:5173`, hold the button, speak.

## How it works

```
┌──────────────────────────────────────────────────────┐
│  Persona (yaml)                                      │
│  • Identity, language, personality, constraints      │
│  • List of skills + their config                     │
└──────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Huxley framework                                    │
│  • Loads persona + skills                            │
│  • Manages voice session (OpenAI Realtime today)     │
│  • Sequences turns + dispatches tool calls           │
│  • Routes side effects (audio playback, ...)         │
└──────────────────────────────────────────────────────┘
                  ▲                ▼                ▼
                  │                │                │
        ┌─────────┴──────┐  ┌──────┴──────────┐  ┌──┴────────┐
        │  Voice provider│  │  Skill (Python) │  │  Client   │
        │  (OpenAI       │  │  via Huxley SDK │  │  (browser,│
        │  Realtime)     │  │                 │  │   ESP32)  │
        └────────────────┘  └─────────────────┘  └───────────┘
```

Detailed concepts: [`docs/concepts.md`](./docs/concepts.md). Architecture: [`docs/architecture.md`](./docs/architecture.md). Wire protocol: [`docs/protocol.md`](./docs/protocol.md).

## Project layout

```
.
├── packages/
│   ├── sdk/        # huxley-sdk: skill author surface (Skill, ToolDefinition, ToolResult, SkillContext, ...)
│   └── core/       # huxley: framework runtime (turn coordinator, session manager, audio server, storage)
├── web/            # SvelteKit dev client — mic + speaker over WebSocket
├── docs/           # vision, concepts, architecture, observability, protocol, decisions, roadmap
└── pyproject.toml  # uv workspace root
```

Planned (active refactor):

```
packages/skills/<name>/   # built-in + community skills as installable packages
personas/<name>/          # YAML config + per-persona data (audiobook library, sqlite db, ...)
```

## Writing a skill

A skill is a Python class implementing the `Skill` protocol from `huxley_sdk`:

```python
from huxley_sdk import Skill, ToolDefinition, ToolResult, SkillContext

class MySkill:
    @property
    def name(self) -> str:
        return "my_skill"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="my_tool", description="...", parameters={...})]

    async def setup(self, ctx: SkillContext) -> None: ...
    async def handle(self, tool_name: str, args: dict) -> ToolResult: ...
    async def teardown(self) -> None: ...
```

Full guide: [`docs/skills/README.md`](./docs/skills/README.md).

## Writing a persona

A persona is a YAML file (loader landing in the next refactor stage):

```yaml
name: my-agent
language: en-US
voice: alloy
personality: |
  You are a helpful assistant.
constraints: [confirm_destructive]
skills:
  - system: {}
```

Full guide: [`docs/personas/README.md`](./docs/personas/README.md).

## Documentation

- [`docs/vision.md`](./docs/vision.md) — what Huxley is, who it's for
- [`docs/concepts.md`](./docs/concepts.md) — vocabulary (persona, skill, tool, turn, side effect, ...)
- [`docs/architecture.md`](./docs/architecture.md) — framework internals
- [`docs/observability.md`](./docs/observability.md) — logging conventions and the debugging workflow
- [`docs/protocol.md`](./docs/protocol.md) — WebSocket contract for clients
- [`docs/turns.md`](./docs/turns.md) — turn coordinator spec
- [`docs/decisions.md`](./docs/decisions.md) — architectural decision log
- [`docs/skills/`](./docs/skills/) — skill authoring guide and first-party skill specs
- [`docs/personas/`](./docs/personas/) — persona authoring guide

## Development

```bash
uv sync                                                # install workspace
uv run ruff check packages/                            # lint
uv run ruff format packages/                           # format
uv run mypy packages/sdk/src packages/core/src         # strict type check
uv run --package huxley-sdk pytest packages/sdk/tests  # SDK tests
uv run --package huxley pytest packages/core/tests     # framework tests
```

## License

MIT — see [`LICENSE`](./LICENSE).
