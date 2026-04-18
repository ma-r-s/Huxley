# Huxley

> Voice agent framework. Personas declare who the agent is; skills declare what it does.

Huxley is a Python framework for building real-time voice agents. You give it a **persona** (a YAML file with name, voice, language, system prompt, behavioral constraints, and a list of enabled skills) and a set of **skills** (Python packages that expose tools to the LLM). Huxley handles the rest: WebSocket audio I/O, voice provider integration, turn coordination, tool dispatch, side-effect sequencing.

The dream: adding a capability to your voice agent is `pip install huxley-skill-foo` plus one line in your persona file.

## What it does

- **Real-time voice conversation** — bidirectional PCM16 audio over WebSocket, currently via OpenAI Realtime API.
- **Entry-point skill system** — third-party Python packages register against the `huxley.skills` entry-point group. Each tool can return text and/or a side effect (`AudioStream` today; future: notifications, state changes).
- **Turn coordinator** — a state machine that sequences model speech and tool-produced audio so the agent's spoken acknowledgement always plays before its actions, with atomic interrupt semantics.
- **Persona-as-config** — agent identity (name, voice, language, timezone, system prompt, constraints, skill list) lives in `personas/<name>/persona.yaml`, not code.
- **Headless** — Huxley is a server. Clients (browser dev UI, ESP32 hardware, anything that speaks the WebSocket protocol) own the microphone and speaker.
- **Structured logging** — every framework decision and skill action emits a namespaced event with turn-level context, designed so problems can be diagnosed by reading the log.

## What it doesn't do

- Not a chatbot framework — Huxley is voice-first, with audio as the primary modality.
- Not multi-tenant — one user, one agent. Multi-user SaaS is out of scope.
- Not a model — Huxley wraps OpenAI's Realtime API today; the architecture leaves room for other providers, but it doesn't train or serve models.
- Not proactive (yet) — skills answer user turns; they can't interrupt the user. See [`docs/extensibility.md`](./docs/extensibility.md) for the gap.

## Status

Pre-1.0. Framework runs end-to-end against a browser dev client. One persona ships in the repo (AbuelOS — Spanish-language assistant with audiobook playback + system controls). Two first-party skills ship as workspace packages (`huxley-skill-audiobooks`, `huxley-skill-system`). See [`docs/roadmap.md`](./docs/roadmap.md) for what's next.

## Quick start

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), [bun](https://bun.sh), `ffmpeg` + `ffprobe` on PATH, and an **OpenAI API key with Realtime API access**.

```bash
git clone <repo-url> huxley
cd huxley
uv sync --all-packages
echo "HUXLEY_OPENAI_API_KEY=sk-..." > .env

# Run the framework (from the repo root)
uv run huxley
```

In another terminal, run the dev client:

```bash
cd web
bun install
bun dev
```

Open `http://localhost:5173`, hold the button, speak.

**First-run notes:**

- `personas/<name>/data/` is gitignored — the bundled AbuelOS persona loads an empty audiobook library on a fresh clone. Drop `.m4b`/`.mp3` files under `personas/abuelos/data/audiobooks/` (optionally in `Author/Title.m4b` subdirs) to populate it.
- To run a different persona, `HUXLEY_PERSONA=<dir_name> uv run huxley` where `<dir_name>` is a folder under `./personas/`.

## How it works

```
┌──────────────────────────────────────────────────────┐
│  Persona (yaml)                                      │
│  • Identity, language, system prompt, constraints    │
│  • List of skills + their config                     │
└──────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Huxley framework                                    │
│  • Loads persona + discovers skills (entry points)   │
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

Detailed concepts: [`docs/concepts.md`](./docs/concepts.md). Architecture: [`docs/architecture.md`](./docs/architecture.md). Wire protocol: [`docs/protocol.md`](./docs/protocol.md). What fits the framework / what doesn't: [`docs/extensibility.md`](./docs/extensibility.md).

## Project layout

```
.
├── packages/
│   ├── sdk/                      # huxley-sdk: skill-author surface
│   ├── core/                     # huxley: framework runtime
│   └── skills/
│       ├── audiobooks/           # huxley-skill-audiobooks
│       └── system/               # huxley-skill-system
├── personas/
│   └── abuelos/                  # canonical persona (Spanish, blind-user)
│       ├── persona.yaml
│       └── data/                 # gitignored: audiobook library + sqlite db
├── web/                          # SvelteKit dev client
├── scripts/                      # one-shot utilities (data migration, etc.)
├── docs/                         # vision, concepts, architecture, protocol, ...
└── pyproject.toml                # uv workspace root
```

## Writing a skill

A skill is a Python class implementing the `Skill` protocol from `huxley_sdk`, installed as a Python package that registers a `huxley.skills` entry point:

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

`pyproject.toml`:

```toml
[project.entry-points."huxley.skills"]
my_skill = "my_package:MySkill"
```

Full guide: [`docs/skills/README.md`](./docs/skills/README.md). Canonical reference implementations: `packages/skills/audiobooks/` and `packages/skills/system/`.

## Writing a persona

A persona is a YAML file at `personas/<name>/persona.yaml`:

```yaml
version: 1
name: MyAgent
voice: alloy
language_code: en
transcription_language: en
timezone: America/New_York
system_prompt: |
  You are a helpful assistant. Respond in English.
constraints:
  - confirm_destructive
skills:
  system: {}
```

Full guide: [`docs/personas/README.md`](./docs/personas/README.md). Canonical reference: `personas/abuelos/persona.yaml`.

## Documentation

- [`docs/vision.md`](./docs/vision.md) — what Huxley is, who it's for
- [`docs/concepts.md`](./docs/concepts.md) — vocabulary (persona, skill, tool, turn, side effect, ...)
- [`docs/architecture.md`](./docs/architecture.md) — framework internals
- [`docs/extensibility.md`](./docs/extensibility.md) — what skills fit the framework, where the limits are
- [`docs/observability.md`](./docs/observability.md) — logging conventions and the debugging workflow
- [`docs/protocol.md`](./docs/protocol.md) — WebSocket contract for clients
- [`docs/turns.md`](./docs/turns.md) — turn coordinator spec
- [`docs/decisions.md`](./docs/decisions.md) — architectural decision log
- [`docs/verifying.md`](./docs/verifying.md) — smoke-test checklist for a fresh checkout
- [`docs/skills/`](./docs/skills/) — skill authoring guide + first-party specs
- [`docs/personas/`](./docs/personas/) — persona authoring guide
- [`docs/research/`](./docs/research/) — design research notes (sonic UX, ...)

## Development

```bash
uv sync --all-packages                                          # install workspace
uv run ruff check packages/                                     # lint
uv run ruff format packages/                                    # format
uv run mypy packages/sdk/src packages/core/src \
            packages/skills/audiobooks/src \
            packages/skills/system/src                          # strict type check
uv run --directory packages/sdk pytest                          # SDK tests (10)
uv run --directory packages/core pytest                         # framework tests (108)
uv run --directory packages/skills/audiobooks pytest            # audiobooks skill tests (54)
cd web && bun run check                                         # svelte-check
```

172 Python tests + 0 svelte-check errors is the green bar. See [`CLAUDE.md`](./CLAUDE.md) for the contributor workflow and [`docs/verifying.md`](./docs/verifying.md) for an end-to-end smoke-test script.

## Run as a background service (macOS)

```bash
./scripts/launchd/install.sh    # starts at login, restarts on crash
tail -f ~/Library/Logs/Huxley/huxley.log
./scripts/launchd/uninstall.sh
```

See [`scripts/launchd/README.md`](./scripts/launchd/README.md). For Linux deployment (Raspberry Pi, home server) write a systemd unit — same shape, no helper script provided yet.

## License

MIT — see [`LICENSE`](./LICENSE).
