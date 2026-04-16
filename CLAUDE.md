# Huxley

A voice agent framework. You give it a persona (who the agent is) and a set of skills (what it can do); it does the rest. Built first for one grandfather (the **AbuelOS** persona); now anyone's.

**📖 Full product + architecture docs: [`docs/`](./docs/)**. Start with [`docs/vision.md`](./docs/vision.md) and [`docs/concepts.md`](./docs/concepts.md).

This file is the quick-start for Claude and Mario. For _why_, _what_, and _how_, read the docs.

> **Naming-in-flight**: this repo is being renamed from "AbuelOS" to "Huxley" — _AbuelOS_ is now the canonical persona, the framework is _Huxley_. The Python namespace (`abuel_os/`) and the repo path (`AbuelOS/`) still use the old name; rename happens in an upcoming refactor. Until then, "Huxley" in docs and "abuel_os" in code refer to the same thing.

## Repo layout

```
AbuelOS/                # repo (will become Huxley/)
├── server/             # Python — Huxley framework runtime
├── web/                # SvelteKit dev client
├── docs/               # Single source of truth
│   ├── vision.md       # what Huxley is
│   ├── concepts.md     # vocabulary (persona, skill, turn, side effect, ...)
│   ├── architecture.md # framework internals
│   ├── observability.md # logging conventions + debugging workflow
│   ├── protocol.md     # WebSocket contract for clients
│   ├── turns.md        # turn coordinator spec
│   ├── decisions.md    # ADR log
│   ├── roadmap.md      # framework + persona roadmaps
│   ├── personas/
│   │   ├── README.md   # how to write a persona
│   │   └── abuelos.md  # the grandfather's persona
│   └── skills/
│       ├── README.md   # how to write a skill
│       └── audiobooks.md # first-party skill spec
└── CLAUDE.md           # this file
```

After the planned workspace split: `packages/{sdk,core,skills/*}` + `personas/abuelos/persona.yaml`. See [`docs/architecture.md`](./docs/architecture.md) for the target shape.

## Commands

Python framework (run from `server/`):

```bash
cd server
uv sync                              # install deps
uv run ruff check src/ tests/        # lint
uv run ruff format src/ tests/       # format
uv run mypy src/                     # strict type check
uv run pytest tests/unit/ -v         # unit tests
uv run python -m abuel_os            # run Huxley
```

Web dev client (run from `web/`):

```bash
cd web
bun install
bun dev                              # http://localhost:5173
bun run check                        # svelte-check
```

## Rules

- Always `uv` for Python, `bun` for web. Never `npm` / `pip` / `yarn`.
- `ruff` for lint+format; `mypy --strict` must pass.
- Skills implement the Skill protocol from `types.py`. Skills opt in to behavioral constraints (`never_say_no`, etc.) per the persona that runs them — see [`docs/skills/README.md`](./docs/skills/README.md#persona-constraints--what-your-skill-should-respect).
- No circular imports — dependencies flow downward; see [`docs/architecture.md`](./docs/architecture.md#dependency-flow-no-cycles).
- Config defaults assume the server runs from `server/` (relative paths `data/...`, `models/...`).
- **Any code change that invalidates a doc must update the doc in the same commit.** No stale docs.
- **Every new event handler / decision point gets a structured log line.** See [`docs/observability.md`](./docs/observability.md) for the convention. If a future bug isn't diagnosable from the log, the fix is also adding the log line that would have caught it.

## Methodology

Global `~/.claude/CLAUDE.md` is the baseline. This section concretizes it for Huxley.

### When to plan vs. just do

**Plan mode** (propose → confirm → execute):

- WebSocket protocol changes — they ripple across `server/` and `web/`
- Session lifecycle, turn coordinator, or skill dispatch changes
- Anything that crosses framework / SDK / skill boundaries
- New skill or new persona (their spec doc is part of the plan)
- Anything that touches the persona or constraint definitions

**Just do**: bug fixes, single-file refactors, test additions, doc fixes, UI tweaks that don't touch the protocol.

### Definition of Done

Before presenting work as done:

- **Server**: `cd server && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest tests/unit/` — all green.
- **Web**: `cd web && bun run check` — green.
- **Protocol or audio-path changes**: above + manual browser smoke test (`bun dev`, hold PTT, speak, verify transcript + audio playback). The audio path has no automated test — acknowledge that gap rather than skip it.
- **Server changes that affect runtime behavior**: the running server must be on the new code (kill the old process and restart). Don't claim a change is deployed if you haven't verified the running pid is from the latest commit.
- **Docs**: if the change affects behavior described in `docs/`, the doc is updated in the same commit.

### The dev loop — describe-symptom-read-log

Mario's primary debugging mode is conversational: he tests via browser, describes a symptom, Claude reads the server log and diagnoses. This works because every meaningful event is logged with structured context.

When you add a new feature or fix a bug, ask: **"if this breaks in production, what log line would I need to diagnose it?"** Then add that line. The framework's logging convention is documented in [`docs/observability.md`](./docs/observability.md).

If a session ever ends with "I had to ask Mario what was happening because the log didn't show enough" — that's a bug in the logging, fix it.

### Critic pattern

- Non-trivial change on **one side** (server OR web): one fresh reviewer agent with spec + code + tests.
- Change touching **both sides** (protocol / session contract): two parallel reviewer agents — one per side. A single reviewer can't hold both contexts cleanly.
- For **architecture or product reshapes** (anything that changes the vocabulary, the boundary, the contract): spawn the critic _before_ writing the final plan. Incorporate their feedback into v1, not as a patch on v2.

### Integration tests

Tests hitting the OpenAI Realtime API or real `ffmpeg` live in `server/tests/integration/`, marked `@pytest.mark.integration`. Skipped by default; run with `uv run pytest -m integration` (requires `ABUEL_OPENAI_API_KEY`).

### Session start for this repo

1. Read this file + `docs/vision.md` + `docs/concepts.md` + `docs/roadmap.md`
2. Glance at `~/.claude/projects/-Users-mario-Projects-Personal-Code-AbuelOS/memory/MEMORY.md`
3. `git status` + `git log -5 --oneline`
4. If resuming work, read any open plan file or the last few commits

### Simplicity — Huxley specifically

- The web UI is a dev tool, not a product. No features beyond what's needed to exercise the server.
- No ESP32 scaffolding until firmware work actually starts.
- **The framework grows slowly.** Stable surface area for skill authors matters more than feature count. New abstractions added only when a real use case forces them — never speculatively.
- **Personas can experiment freely.** Persona-level changes (the AbuelOS persona's prompt, its skill list, its constraints) are cheap. Try things; iterate.
- **Skills opt into persona constraints, not the other way around.** A skill targeting AbuelOS should respect `never_say_no`. A skill targeting a different persona may not need to.
- For the AbuelOS persona specifically: every user-facing failure must have an audio surface. Grandpa is blind — visual-only failure modes don't exist for him.

## Decisions

See [`docs/decisions.md`](./docs/decisions.md) for the full architectural decision log. The 2026-04-16 entry documents the framework / persona / skill split and the rename from AbuelOS-the-project to Huxley.
