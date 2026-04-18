# Huxley

A voice agent framework. You give it a persona (who the agent is) and a set of skills (what it can do); it does the rest. The first persona shipped on it is **AbuelOS**, a Spanish-language assistant for an elderly blind user.

**📖 Full product + architecture docs: [`docs/`](./docs/)**. Start with [`docs/vision.md`](./docs/vision.md) and [`docs/concepts.md`](./docs/concepts.md). New visitors should read [`README.md`](./README.md) first.

This file is the quick-start for contributors and AI collaborators. For _why_, _what_, and _how_, read the docs.

> _AbuelOS_ is the canonical persona, never the framework. Framework code is `huxley`, SDK is `huxley_sdk`.

## Repo layout

```
Huxley/                 # repo root
├── pyproject.toml      # uv workspace root
├── packages/
│   ├── sdk/            # huxley-sdk: skill author surface
│   │   └── src/huxley_sdk/   # Skill, Tool*, SkillContext, SideEffect/AudioStream/PlaySound, audio.load_pcm_palette, SkillRegistry
│   ├── core/           # huxley: framework runtime
│   │   ├── src/huxley/       # app, session, turn, server, persona, constraints, loader, storage
│   │   ├── tests/
│   │   └── .env              # gitignored; HUXLEY_OPENAI_API_KEY etc
│   └── skills/
│       ├── audiobooks/       # huxley-skill-audiobooks (entry-point loaded)
│       ├── news/             # huxley-skill-news (Open-Meteo + Google News RSS)
│       ├── radio/            # huxley-skill-radio (HTTP/Icecast streams via ffmpeg)
│       └── system/           # huxley-skill-system (entry-point loaded)
├── personas/
│   ├── abuelos/              # canonical persona — slow, warm, audio-only target
│   │   ├── persona.yaml      # version, name, voice, language, system_prompt, constraints, skills
│   │   ├── data/             # gitignored: audiobook library + abuelos.db
│   │   ├── sounds/           # earcons (book_start.wav, book_end.wav, news_start.wav)
│   │   └── README.md
│   └── basicos/              # terse counter-persona — proves skills are persona-agnostic
│       ├── persona.yaml
│       └── README.md
├── scripts/
│   └── migrate-data-to-persona.sh  # one-time move from legacy packages/core/data/
├── web/                # SvelteKit dev client
├── docs/               # Single source of truth
│   ├── vision.md       # what Huxley is
│   ├── concepts.md     # vocabulary (persona, skill, turn, side effect, ...)
│   ├── architecture.md # framework internals
│   ├── extensibility.md # what skills fit, where the design gaps are
│   ├── observability.md # logging conventions + debugging workflow
│   ├── protocol.md     # WebSocket contract for clients
│   ├── turns.md        # turn coordinator spec
│   ├── decisions.md    # ADR log
│   ├── roadmap.md      # framework + persona roadmaps
│   ├── personas/{README,abuelos,basicos}.md
│   ├── skills/{README,audiobooks,news,radio}.md
│   ├── sounds.md       # sound UX architecture (PlaySound, AudioStream, earcons)
│   └── research/sonic-ux.md
└── CLAUDE.md           # this file
```

All five refactor stages have shipped — framework / SDK / skills / personas / constraints / entry-point loading are all in place. See [`docs/roadmap.md`](./docs/roadmap.md) for what's next.

## Commands

Python (uv workspace; lint/typecheck/test work from repo root):

```bash
uv sync                                                    # install all workspace packages
uv run ruff check packages/                                # lint
uv run ruff format packages/                               # format
uv run mypy packages/sdk/src packages/core/src             # strict type check
uv run --package huxley-sdk pytest packages/sdk/tests                              # SDK tests (19)
uv run --package huxley pytest packages/core/tests                                 # framework tests (113)
uv run --package huxley-skill-audiobooks pytest packages/skills/audiobooks/tests   # audiobooks skill (55)
uv run --package huxley-skill-news pytest packages/skills/news/tests               # news skill (18)
uv run --package huxley-skill-radio pytest packages/skills/radio/tests             # radio skill (19)
cd packages/core && uv run huxley                                                  # run the server (loads .env from packages/core)
# Run BasicOS in parallel for persona A/B testing:
cd packages/core && HUXLEY_PERSONA=basicos HUXLEY_SERVER_PORT=8766 uv run huxley
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
- Config defaults assume the server runs from `packages/core/` (loads `.env` from that directory).
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

- **Python**: `uv run ruff check packages/ && uv run mypy packages/sdk/src packages/core/src && uv run --package huxley pytest packages/core/tests` — all green.
- **Web**: `cd web && bun run check` — green.
- **Protocol or audio-path changes**: above + manual browser smoke test (`bun dev`, hold PTT, speak, verify transcript + audio playback).
- **Server changes that affect runtime behavior**: the running server must be on the new code (kill the old process and restart). Don't claim a change is deployed if you haven't verified the running pid is from the latest commit.
- **Docs**: if the change affects behavior described in `docs/`, the doc is updated in the same commit.

### The dev loop — describe-symptom-read-log

The primary debugging mode is conversational: a contributor tests via the browser dev client, describes a symptom, Claude (or another collaborator) reads the server log and diagnoses. This works because every meaningful event is logged with structured context.

When you add a new feature or fix a bug, ask: **"if this breaks in production, what log line would I need to diagnose it?"** Then add that line. The framework's logging convention is documented in [`docs/observability.md`](./docs/observability.md).

If a session ever ends with "I had to ask the contributor what was happening because the log didn't show enough" — that's a bug in the logging, fix it.

### Critic pattern

- Non-trivial change on **one side** (server OR web): one fresh reviewer agent with spec + code + tests.
- Change touching **both sides** (protocol / session contract): two parallel reviewer agents — one per side. A single reviewer can't hold both contexts cleanly.
- For **architecture or product reshapes** (anything that changes the vocabulary, the boundary, the contract): spawn the critic _before_ writing the final plan. Incorporate their feedback into v1, not as a patch on v2.

### Integration tests

Tests hitting the OpenAI Realtime API or real `ffmpeg` live in `packages/core/tests/integration/`, marked `@pytest.mark.integration`. Skipped by default; run with `uv run --package huxley pytest -m integration` (requires `HUXLEY_OPENAI_API_KEY`).

### Session start for this repo

1. Read this file + `docs/vision.md` + `docs/concepts.md` + `docs/roadmap.md`
2. **Read [`docs/triage.md`](./docs/triage.md)** — living tracker for what's in flight, queued, blocked, deferred. Source of truth for "what should I work on now?"
3. Glance at `~/.claude/projects/-Users-mario-Projects-Personal-Code-Huxley/memory/MEMORY.md`
4. `git status` + `git log -5 --oneline`
5. If resuming work, read any open plan file or the last few commits

When picking up a task: flip its status in `docs/triage.md` to `in_progress` with a date stamp before starting; flip to `done` with the commit hash when shipped. New findings get added to `triage.md` as a fresh entry (problem · why · solution · effort), not just buried in a commit message.

### Triage discipline — the five gates

Every triage item moves through five gates documented in [`docs/triage.md`](./docs/triage.md#workflow-per-item). Skipping a gate is a defect, not a shortcut.

1. **Validate** — prove the problem exists with concrete evidence in the entry. No "this might be nice" items get worked.
2. **Design + critic** (non-trivial only) — sketch the design, then **spawn a fresh critic agent** with the prompt skeleton in `triage.md`. Lock the Definition of Done as a bullet list.
3. **Implement** — code + regression test that proves the symptom is gone + contract tests for new abstraction surfaces. ruff + mypy + pytest green.
4. **Document** — walk the doc checklist explicitly. If nothing applies, write `Docs: none affected (verified each)` in the entry.
5. **Ship + capture** — commit references the triage ID; entry gets the commit hash + a one-line lessons note; memory file updated if a durable lesson emerged.

Trivial items (< 1 day, mechanical) collapse Gates 1–2 into a few minutes and skip the critic. Anything Tier 1 or design-shaped goes through the full path. **The work artifacts (validation evidence, design sketch, critic notes, DoD, tests, docs touched, lessons) live in the triage entry — not in commit messages alone.**

### Simplicity — Huxley specifically

- The web UI is a dev tool, not a product. No features beyond what's needed to exercise the server.
- No ESP32 scaffolding until firmware work actually starts.
- **The framework grows slowly.** Stable surface area for skill authors matters more than feature count. New abstractions added only when a real use case forces them — never speculatively.
- **Personas can experiment freely.** Persona-level changes (the AbuelOS persona's prompt, its skill list, its constraints) are cheap. Try things; iterate.
- **Skills opt into persona constraints, not the other way around.** A skill targeting AbuelOS should respect `never_say_no`. A skill targeting a different persona may not need to.
- For the AbuelOS persona specifically: every user-facing failure must have an audio surface. The target user is blind — visual-only failure modes don't exist for them.

## Decisions

See [`docs/decisions.md`](./docs/decisions.md) for the full architectural decision log. The 2026-04-16 entry documents the framework / persona / skill split and the rename from AbuelOS-the-project to Huxley.
