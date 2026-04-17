# Review notes

Honest self-assessment of the repo as of 2026-04-17, written for a third-party reviewer. Separate from [`verifying.md`](./verifying.md), which is a step-by-step smoke test — this doc is _"what will draw comments, and why we're shipping anyway."_

## Verdict

**Ready for review.** Framework end-to-end paths work from a fresh clone, six months of refactor are captured as six atomic commits with clear messages, docs cover vision / architecture / protocol / concepts / skills / personas / turns / observability / extensibility / research, strict typing and linting are green, 144 Python tests pass.

The items below are known rough edges. None are secrets; all are called out somewhere in the repo. Surface them yourself before a reviewer does.

## What will still draw comments

### 1. No automated test for the audio path

`TurnCoordinator` is tested, skill logic is tested up to the factory closure, but the actual PCM-bytes-to-browser WebSocket path has no coverage. Manual smoke test 2 in [`verifying.md`](./verifying.md) is the only verification. Fixing is multi-session work (fake WebSocket client + assertion harness on byte streams). Worth doing before v1.0.

### 2. Thinking tone violates the sonic-UX framework we documented

The client-side `playThinkingTone()` is a 440 Hz sine pulse — inside the vocal band (200 Hz–4 kHz), which [`research/sonic-ux.md`](./research/sonic-ux.md) rule 11 explicitly forbids because it masks model speech. The current state vs. target is written up in that doc. Tracked as P2 on the roadmap. A reviewer with audio ears will catch it within a minute.

### 3. OpenAI Realtime API access is a real gate

Not every OpenAI account has Realtime beta access. A reviewer without it can verify:

- Persona loads, constraints compose, skills register via entry points
- SQLite schema initialises at the right path
- WebSocket server binds and accepts connections
- Unit tests pass

…and gets `invalid_request_error.invalid_api_key` at the OpenAI session stage. Documented in `verifying.md`.

### 4. Gitignored audiobook library

`personas/abuelos/data/` is gitignored (user-owned media, large files). A fresh clone loads `audiobooks.catalog_loaded count=0`. Reviewers must drop a sample `.m4b`/`.mp3` into `personas/abuelos/data/audiobooks/` to exercise the playback path. Documented in both `README.md` and `verifying.md`.

### 5. Spanish-first canonical persona

The bundled persona is AbuelOS — Spanish, Colombian register, Bogotá timezone. A reviewer expecting English-first docs will notice. The persona schema is language-agnostic; only the shipped example is Spanish. A template English persona would help, but hasn't been written yet.

### 6. Python 3.13 requirement is tight

`match` statements, modern typing (`X | None`, `type` statements disabled via ruff config because they break mypy + `from __future__ import annotations`), and `ClassVar[str]` in dataclass bases. Reviewers on 3.12 will see import errors on `from __future__` edge cases. Called out in `verifying.md` prereqs.

## Non-issues that look like issues

### `personas/abuelos/data/` referenced in `persona.yaml` but empty in git

Correct. The YAML points at a directory for user data; the directory is gitignored because it holds per-user audiobooks + a SQLite DB. The runtime handles a missing/empty library gracefully (`count=0` log, skill still registers).

### Voice is `coral` in the bundled persona, not `alloy`

Intentional — `coral` was chosen for AbuelOS based on listening tests. `README.md`'s schema example uses `alloy` as the generic stand-in. Both valid OpenAI Realtime voice IDs.

### `docs/decisions.md` references "AbuelOS" as a former project name

Preserved on purpose. ADRs document past states; rewriting them to match the current framing would erase the decision history. The rename from "AbuelOS the project" to "Huxley the framework / AbuelOS the persona" is itself an ADR entry.

### `packages/core/pyproject.toml` depends on `huxley-skill-audiobooks` + `huxley-skill-system`

Looks like a layering violation but is deliberate and documented inline: core needs the first-party skills installed into the workspace venv so their entry points register, otherwise a fresh `uv sync` ships with no skills. Stage 4's persona loader reads the skill list from YAML; the explicit deps are only there to keep `uv sync` from being a two-step dance. Removable once personas carry their own deps.

### Deprecation alias `ToolResult.audio_factory=` removed without a compat release

Stage 3 shipped the `side_effect=AudioStream(...)` form with a deprecated `audio_factory=` alias that warned once per process. Stage 5 removed the alias without a minor-version bridge because the framework is pre-1.0, no third-party skills exist yet, and the alias was in the tree for a single session. A reviewer who expects semver-style deprecation cycles will flag this — the pre-1.0 status is the answer.

## Where the verdict comes from

Ship-blockers would be:

- Tests failing or flaky — **not the case** (144 green, deterministic)
- Docs contradicting the code — **not the case** (stage 5 cleaned the last stale claims, [`verifying.md`](./verifying.md) tests the contract)
- Install path broken on fresh clone — **not the case** (`.env` at repo root + `uv sync --all-packages` verified to boot)
- Architectural questions the reviewer would legitimately raise that the docs don't answer — **not the case** (`architecture.md`, `extensibility.md`, `decisions.md` between them cover the _why_)

The six items in "will still draw comments" above are all documented, all prioritised on the roadmap or explicitly deferred, and none is a correctness bug. Reviewer feedback on any of them is useful signal, not a reason to delay review.

## How to handle feedback

Run incoming reviewer questions through this filter:

1. **Is it a bug?** → fix it, update the relevant doc in the same commit ([`CLAUDE.md`](../CLAUDE.md) hard rule).
2. **Is it one of the rough edges above?** → point them at this doc, note the roadmap priority, move on.
3. **Is it a new design question the docs don't answer?** → open an ADR candidate in [`decisions.md`](./decisions.md), respond in thread with the framing, let it settle before changing code.

Don't rewrite architecture on the first round of feedback. Wait for two reviewers to raise the same concern before touching a boundary.
