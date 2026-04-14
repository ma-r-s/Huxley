# AbuelOS

Voice assistant for a blind elderly user. Conversational AI (OpenAI Realtime API) with an extensible skill system.

**📖 Full product + architecture docs: [`docs/`](./docs/)** — start with [`docs/vision.md`](./docs/vision.md).

This file is the quick-start for Claude and Mario. For _why_, _what_, and _how_, read the docs.

## Repo layout

```
AbuelOS/
├── server/     # Python — WebSocket audio server, OpenAI relay, skills, storage
├── web/        # SvelteKit — dev client (browser mic/speaker over WebSocket)
├── docs/       # Single source of truth: product, architecture, protocol, decisions
└── CLAUDE.md   # This file — quick-start + methodology
```

## Commands

Python server (run from `server/`):

```bash
cd server
uv sync                              # Install deps
uv run ruff check src/ tests/        # Lint
uv run ruff format src/ tests/       # Format
uv run mypy src/                     # Type check
uv run pytest tests/unit/ -v         # Unit tests
uv run python -m abuel_os            # Run the server
```

Web client (run from `web/`):

```bash
cd web
bun install
bun dev                              # http://localhost:5173
bun run check                        # svelte-check
```

## Rules

- Always use `uv` for Python, `bun` for web. Never `npm` / `pip` / `yarn`.
- `ruff` for linting and formatting; `mypy --strict` must pass.
- Skills must implement the `Skill` protocol from `types.py` and follow the ["nunca decir no" contract](./docs/skills/README.md#the-nunca-decir-no-contract--skill-author-rules).
- No circular imports — dependencies flow downward; see [`docs/architecture.md`](./docs/architecture.md#dependency-flow-no-cycles).
- Config defaults assume the server runs from `server/` (relative paths `data/...`, `models/...`).
- **Any code change that invalidates a doc must update the doc in the same commit.** No stale docs.

## Methodology

Global `~/.claude/CLAUDE.md` is the baseline. This section concretizes it for AbuelOS.

### When to plan vs. just do

**Plan mode** (propose → confirm → execute):

- WebSocket protocol changes — they ripple across `server/` and `web/`
- Session lifecycle, state machine, or skill dispatch changes
- Anything new that crosses the server ↔ web boundary
- Any new skill (even small ones — a spec doc under `docs/skills/` is part of the plan)

**Just do**: bug fixes, single-file refactors, test additions, doc fixes, UI tweaks that don't touch the protocol.

### Definition of Done

Before presenting work as done:

- **Server**: `cd server && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest tests/unit/` — all green.
- **Web**: `cd web && bun run check` — green.
- **Protocol or audio-path changes**: above + manual browser smoke test (`bun dev`, hold PTT, speak, verify transcript + audio playback). The audio path has no automated test — acknowledge that gap rather than skip it.
- **Docs**: if the change affects behavior described in `docs/`, the doc is updated in the same commit.

### Critic pattern

- Non-trivial change on **one side** (server OR web): one fresh reviewer agent with spec + code + tests.
- Change touching **both sides** (protocol / session contract): two parallel reviewer agents — one per side. A single reviewer can't hold both contexts cleanly.

### Integration tests

Tests hitting the OpenAI Realtime API or real `mpv` live in `server/tests/integration/`, marked `@pytest.mark.integration`. Skipped by default; run with `uv run pytest -m integration` (requires `ABUEL_OPENAI_API_KEY`).

### Session start for this repo

1. Read this file + `docs/vision.md` + `docs/roadmap.md`
2. Glance at `~/.claude/projects/-Users-mario-Projects-Personal-Code-AbuelOS/memory/MEMORY.md`
3. `git status` + `git log -5 --oneline`
4. If resuming work, read any open plan file or the last few commits

### Simplicity — AbuelOS specifically

- The web UI is a dev tool, not a product. No features beyond what's needed to exercise the server.
- No ESP32 scaffolding until firmware work actually starts.
- No new skills until grandpa has asked for them — not _"what might be useful."_
- Abuelo is blind: every user-facing failure must have an audio surface. Visual-only failure modes don't exist for him.

## Decisions

See [`docs/decisions.md`](./docs/decisions.md) for the full architectural decision log.
