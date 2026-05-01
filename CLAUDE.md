# Huxley

A voice agent framework. You give it a persona (who the agent is) and a set of skills (what it can do); it does the rest. The first persona shipped on it is **Abuelo**, a Spanish-language assistant for an elderly blind user.

**📖 Full product + architecture docs: [`docs/`](./docs/)**. Start with [`docs/vision.md`](./docs/vision.md) and [`docs/concepts.md`](./docs/concepts.md). New visitors should read [`README.md`](./README.md) first.

This file is the quick-start for contributors and AI collaborators. For _why_, _what_, and _how_, read the docs.

> _Abuelo_ is the canonical persona, never the framework. Framework code is `huxley`, SDK is `huxley_sdk`.

## Repo layout

Top-level reads as the architecture: **server** (what runs) → **clients** (what
connects to it) → **site** (what markets it) + meta (docs, scripts).

```
Huxley/                              # repo root
├── pyproject.toml                   # uv workspace root
├── server/                          # The Huxley backend — Python, what runs
│   ├── runtime/                     # huxley package — the engine
│   │   ├── src/huxley/              #   app, session, turn, server, persona, constraints, loader, storage
│   │   ├── tests/
│   │   └── .env                     #   gitignored; HUXLEY_OPENAI_API_KEY etc
│   ├── sdk/                         # huxley-sdk — public skill contract
│   │   └── src/huxley_sdk/          #   Skill, Tool*, SkillContext, SideEffect/AudioStream/PlaySound, audio.load_pcm_palette, SkillRegistry
│   ├── skills/                      # First-party plug-ins (entry-point loaded)
│   │   ├── audiobooks/              #   huxley-skill-audiobooks
│   │   ├── news/                    #   huxley-skill-news (Open-Meteo + Google News RSS)
│   │   ├── radio/                   #   huxley-skill-radio (HTTP/Icecast via ffmpeg)
│   │   ├── search/                  #   huxley-skill-search (DuckDuckGo via ddgs, no API key)
│   │   ├── reminders/               #   huxley-skill-reminders (scheduled reminders, persistent, retry escalation)
│   │   ├── stocks/                  #   huxley-skill-stocks (Alpha Vantage; reference third-party-shape skill)
│   │   ├── system/                  #   huxley-skill-system (volume, time)
│   │   ├── telegram/                #   huxley-skill-telegram (MTProto comms)
│   │   └── timers/                  #   huxley-skill-timers (one-shot relative reminders)
│   └── personas/                    # Persona configs + assets the runtime loads
│       ├── _shared/                 #   Framework-shared assets (palette of all personas)
│       │   └── sounds/              #     Earcons rendered by scripts/synth_sounds.py — book_start, book_end, news_start, radio_start, search_start
│       ├── abuelos/                 #   canonical — slow, warm, audio-only target
│       │   ├── persona.yaml         #     version, name, voice, language, system_prompt, constraints, skills
│       │   ├── data/                #     gitignored: audiobook library + abuelos.db
│       │   └── README.md
│       ├── basic/                 #   terse counter-persona — proves skills are persona-agnostic
│       │   ├── persona.yaml
│       │   └── README.md
│       ├── chief/                   #   action-oriented executive assistant (en)
│       │   └── persona.yaml
│       ├── librarian/               #   quiet research authority (en, audiobooks + search)
│       │   └── persona.yaml
│       └── buddy/                   #   friendly kids companion (en, never_say_no, child_safe)
│           └── persona.yaml
├── clients/                         # Things that connect to the server
│   ├── pwa/                         # React/Vite dev client (Progressive Web App)
│   └── firmware/                    # ESP32-S3 client (will extract to huxley-firmware)
├── web/                             # Web app — landing (/) + docs (/docs/*) — Next.js/Fumadocs
├── docs/                            # Single source of truth
│   ├── vision.md                    # what Huxley is
│   ├── concepts.md                  # vocabulary (persona, skill, turn, side effect, ...)
│   ├── architecture.md              # framework internals
│   ├── extensibility.md             # what skills fit, where the design gaps are
│   ├── observability.md             # logging conventions + debugging workflow
│   ├── protocol.md                  # WebSocket contract for clients
│   ├── turns.md                     # turn coordinator spec
│   ├── decisions.md                 # ADR log
│   ├── roadmap.md                   # framework + persona roadmaps
│   ├── skill-marketplace.md         # T1.14 spec: v1/v2 split, secrets layout, schema versioning
│   ├── personas/{README,abuelos,basic,chief,librarian,buddy}.md
│   ├── skills/{README,authoring,installing,index,audiobooks,news,radio,search,telegram,timers,reminders}.md
│   ├── sounds.md                    # sound UX architecture (PlaySound, AudioStream, synthesis pipeline)
│   └── research/sonic-ux.md
├── scripts/                         # One-off ops + synth_sounds.py + smoke_t114.py + launchd plist
└── CLAUDE.md                        # this file

# Sibling repos (parallel to Huxley):
~/Projects/Personal/Code/huxley-registry/
                                     # ma-r-s/huxley-registry — discovery feed for
                                     # installable skills. JSON Schema + index.json +
                                     # per-skill detail files. PR-curated. Static
                                     # JSON served via raw.githubusercontent /
                                     # jsdelivr; no infra. Tier 1 of the v2
                                     # marketplace per docs/skill-marketplace.md.
```

All five refactor stages have shipped — framework / SDK / skills / personas / constraints / entry-point loading are all in place. T1.14 (skill marketplace v1) shipped 2026-05-01: `ctx.secrets`, optional `Skill.config_schema` + `data_schema_version`, authoring docs, static directory page. See [`docs/roadmap.md`](./docs/roadmap.md) for what's next.

## Commands

Python (uv workspace; lint/typecheck/test work from repo root):

```bash
uv sync                                                                          # install all workspace packages
uv run ruff check server/                                                        # lint
uv run ruff format server/                                                       # format
uv run mypy server/sdk/src server/runtime/src                                    # strict type check
uv run --package huxley-sdk pytest server/sdk/tests                              # SDK tests
uv run --package huxley pytest server/runtime/tests                              # runtime tests
uv run --package huxley-skill-audiobooks pytest server/skills/audiobooks/tests   # audiobooks skill
uv run --package huxley-skill-news pytest server/skills/news/tests               # news skill
uv run --package huxley-skill-radio pytest server/skills/radio/tests             # radio skill
uv run --package huxley-skill-search pytest server/skills/search/tests           # search skill
uv run --package huxley-skill-timers pytest server/skills/timers/tests           # timers skill
uv run --package huxley-skill-reminders pytest server/skills/reminders/tests     # reminders skill
uv run --package huxley-skill-stocks pytest server/skills/stocks/tests           # stocks skill
cd server/runtime && uv run huxley                                               # run the server (loads .env from server/runtime/)
# Run Basic in parallel for persona A/B testing:
cd server/runtime && HUXLEY_PERSONA=basic HUXLEY_SERVER_PORT=8766 uv run huxley
# Re-render the shared earcon palette (writes server/personas/_shared/sounds/*.wav):
uv run --package huxley --group synth python scripts/synth_sounds.py
```

PWA dev client (run from `clients/pwa/`):

```bash
cd clients/pwa
bun install
bun dev                              # http://localhost:5174
bun run check                        # tsc --noEmit
```

Web app — landing + docs (run from `web/`):

```bash
cd web
bun install
bun run dev                          # http://localhost:3000 (/ = landing, /docs/* = docs)
bun run build                        # production build
```

Firmware (ESP-IDF must be sourced first):

```bash
. ~/esp/esp-idf/export.sh
cd clients/firmware
idf.py build                                              # compile for ESP32-S3
idf.py -p /dev/cu.usbmodem2101 flash monitor              # flash + serial

# Tests (see clients/firmware/README.md §Tests for when to run which tier):
cd clients/firmware/tests && cmake -B build && cmake --build build --target check
uv run --package huxley pytest server/runtime/tests/unit/test_firmware_contract.py
clients/firmware/tools/smoke.sh                           # end-to-end boot-to-READY
```

## Rules

- Always `uv` for Python, `bun` for web. Never `npm` / `pip` / `yarn`.
- `ruff` for lint+format; `mypy --strict` must pass.
- Skills implement the Skill protocol from `types.py`. Skills opt in to behavioral constraints (`never_say_no`, etc.) per the persona that runs them — see [`docs/skills/README.md`](./docs/skills/README.md#persona-constraints--what-your-skill-should-respect).
- No circular imports — dependencies flow downward; see [`docs/architecture.md`](./docs/architecture.md#dependency-flow-no-cycles).
- Config defaults assume the server runs from `server/runtime/` (loads `.env` from that directory).
- **Any code change that invalidates a doc must update the doc in the same commit.** No stale docs.
- **Every new event handler / decision point gets a structured log line.** See [`docs/observability.md`](./docs/observability.md) for the convention. If a future bug isn't diagnosable from the log, the fix is also adding the log line that would have caught it.

## Methodology

Global `~/.claude/CLAUDE.md` is the baseline. This section concretizes it for Huxley.

### When to plan vs. just do

**Plan mode** (propose → confirm → execute):

- WebSocket protocol changes — they ripple across `server/` and `clients/pwa/`
- Session lifecycle, turn coordinator, or skill dispatch changes
- Anything that crosses framework / SDK / skill boundaries
- New skill or new persona (their spec doc is part of the plan)
- Anything that touches the persona or constraint definitions

**Just do**: bug fixes, single-file refactors, test additions, doc fixes, UI tweaks that don't touch the protocol.

### Definition of Done

Before presenting work as done:

- **Python**: `uv run ruff check server/ && uv run mypy server/sdk/src server/runtime/src && uv run --package huxley pytest server/runtime/tests` — all green.
- **Web**: `cd clients/pwa && bun run check` — green.
- **Protocol or audio-path changes**: above + manual browser smoke test (`bun dev`, hold PTT, speak, verify transcript + audio playback).
- **Server changes that affect runtime behavior**: the running server must be on the new code (kill the old process and restart). Don't claim a change is deployed if you haven't verified the running pid is from the latest commit.
- **Docs**: if the change affects behavior described in `docs/`, the doc is updated in the same commit.

### The dev loop — Mario tests, Claude reads logs

The work division is:

- **Mario runs the browser smoke tests.** He's the human ear — he can detect audio glitches, interrupt latency, "this feels wrong" UX things that logs can't capture. His job ends at "I did X, here's what I heard."
- **Claude reads the server log + web dev log + network frames and diagnoses.** Don't ask Mario what he did step-by-step — read the timeline from logs. If the log doesn't show what happened, that's a logging bug, fix it in the same commit.

When you add a new feature or fix a bug, ask: **"if this breaks in production, what log line would I need to diagnose it?"** Then add that line. The framework's logging convention is documented in [`docs/observability.md`](./docs/observability.md).

A session that ends with "I had to ask what you did because the log didn't show enough" is a defect — capture the missing event, don't paper over it.

**Server restart discipline**: after any commit that changes server runtime behavior, the previously-running process is stale. Kill it and restart yourself before asking Mario to smoke test. "I committed it" ≠ "it's running on the new code." See Definition of Done above.

### Critic pattern

- Non-trivial change on **one side** (server OR web): one fresh reviewer agent with spec + code + tests.
- Change touching **both sides** (protocol / session contract): two parallel reviewer agents — one per side. A single reviewer can't hold both contexts cleanly.
- For **architecture or product reshapes** (anything that changes the vocabulary, the boundary, the contract): spawn the critic _before_ writing the final plan. Incorporate their feedback into v1, not as a patch on v2.

### Integration tests

Tests hitting the OpenAI Realtime API or real `ffmpeg` live in `server/runtime/tests/integration/`, marked `@pytest.mark.integration`. Skipped by default; run with `uv run --package huxley pytest -m integration` (requires `HUXLEY_OPENAI_API_KEY`).

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

### Dream check — does this match what Huxley is trying to be?

Huxley has a clear vision: a **voice agent framework** that ships with **Abuelo** as its first persona. The framework names mechanisms, not use cases. Personas name the product.

Before shipping any non-trivial commit, re-read [`docs/vision.md`](./docs/vision.md) and [`docs/concepts.md`](./docs/concepts.md) with fresh eyes and ask:

1. **Am I adding framework scope or persona scope?** Framework scope requires stable surface area, skill-author ergonomics, persona-agnostic semantics. Persona scope (Abuelo's prompt, its skill list, its constraints) can iterate freely. If a commit mixes both, split it.
2. **Am I naming a mechanism or a use case?** Framework code should not contain words like "call," "reminder," "emergency," "audiobook" — those are skill-level concepts. If you wrote one of those words in `server/runtime` or `server/sdk`, something's in the wrong layer.
3. **Is this for "grandpa" or for the framework?** Abuelo-specific UX (slow speech, Spanish, warm tone, no dialect assumptions) lives in the persona. The framework stays neutral.
4. **Is this solving a real problem today, or a speculative one?** If speculative, cut it. See Simplicity below.

If the commit drifts from any of these, flag it in the commit message and escalate as a behavioral question — that's Mario's lane, not yours.

### Simplicity — Huxley specifically

- The web UI is a dev tool, not a product. No features beyond what's needed to exercise the server.
- No ESP32 scaffolding until firmware work actually starts.
- **The framework grows slowly.** Stable surface area for skill authors matters more than feature count. New abstractions added only when a real use case forces them — never speculatively.
- **Personas can experiment freely.** Persona-level changes (the Abuelo persona's prompt, its skill list, its constraints) are cheap. Try things; iterate.
- **Skills opt into persona constraints, not the other way around.** A skill targeting Abuelo should respect `never_say_no`. A skill targeting a different persona may not need to.
- For the Abuelo persona specifically: every user-facing failure must have an audio surface. The target user is blind — visual-only failure modes don't exist for them.

## Decisions

See [`docs/decisions.md`](./docs/decisions.md) for the full architectural decision log. The 2026-04-16 entry documents the framework / persona / skill split and the rename from Abuelo-the-project to Huxley.
