# Triage — work tracker

Living source of truth for what's in flight, queued, blocked, deferred, and done.
Mini-ADR for each non-trivial item (problem · why it matters · proposed solution ·
effort). Item-level status lives here so any session can pick up where the last
left off.

## How to use

- **New finding** → add under the right tier with status `queued`, link to a task ID
  if one exists.
- **Starting work** → flip status to `in_progress`, add a date stamp.
- **Shipped** → flip to `done`, add commit hash, leave the writeup in place. Prune
  done items quarterly to keep the doc readable.
- **Pulling out of scope** → move to "Deferred" with the trigger that should
  revisit it. Don't delete deferred items — the trigger is the contract.

## Status legend

`queued` · `in_progress` · `blocked` (note blocker) · `done` (note commit) ·
`deferred` (note revisit trigger)

---

# Workflow per item

Every triage item moves through five gates. **Trivial items** (< 1 day,
mechanical) collapse Gates 1–2 into ~5 minutes and skip the critic. **Non-trivial
items** (any Tier 1, or anything design-shaped) get the full path. The work
artifacts live in the entry itself — not buried in commits.

## Gate 1 — Validate the problem exists

Before flipping `queued` → `in_progress`, prove the problem is real. Add a
"Validation" subsection with evidence.

- **Bugs**: paste a reproduction (log line, failing test, recorded session).
- **Missing primitives**: cite specific current or imminent code that suffers
  without it. _Adding a primitive because it sounds elegant is the failure mode
  this gate exists to prevent._

If you cannot validate: move to Deferred with reason "could not validate", or
delete the entry.

## Gate 2 — Design + critic

For non-trivial items only (Tier 1, or anything estimated > 1 day):

1. Sketch the design in a "Design" subsection.
2. **Spawn a critic agent** with full context — problem statement, design
   sketch, relevant code paths. Use the prompt skeleton at the bottom of this
   section.
3. Capture findings in "Critic Notes". For each: incorporate, or document
   why dismissed.
4. **Lock the Definition of Done** as a bullet list. This is the contract for
   "shipped." Anything outside the bullets is scope creep — file as a separate
   triage item.

Trivial items skip this gate.

## Gate 3 — Implement

1. Write code.
2. **Write the regression test that proves the symptom is gone** alongside (or
   before) the fix. The test is the proof Gate 1's problem is solved.
3. Write contract tests for any new abstraction surface (unit + integration).
4. `uv run ruff check packages/` + `uv run mypy packages/sdk/src packages/core/src` +
   per-package `pytest` all green.
5. For audio/protocol changes: manual browser smoke per
   [`docs/verifying.md`](./verifying.md). Audio regressions don't show up in
   `pytest`.

## Gate 4 — Document

For every item, walk this checklist explicitly. The act of checking is the work
— not just "I think nothing changed."

- [ ] Affected `docs/*.md` (architecture, protocol, `skills/*`, `personas/*`,
      `extensibility.md`, `concepts.md`, `observability.md`)
- [ ] [`docs/decisions.md`](./decisions.md) ADR — if any architectural decision
      was made or reaffirmed
- [ ] [`CLAUDE.md`](../CLAUDE.md) — if methodology / convention / commands changed
- [ ] Skill authoring docs — if SDK surface changed
- [ ] [`README.md`](../README.md) — if user-facing setup, features, or commands changed
- [ ] Memory file under
      `~/.claude/projects/-Users-mario-Projects-Personal-Code-Huxley/memory/` —
      if non-obvious knowledge worth carrying across sessions

If nothing applies: write `Docs: none affected (verified each)` in the entry.
The verified-each clause forces explicit consideration.

## Gate 5 — Ship + capture

1. Commit referencing the triage ID: `feat(skill): add Catalog primitive (T1.1)`.
2. Flip status to `done` with commit hash + date in the entry.
3. Add a "Lessons" line: anything surprising? Critic right or wrong? Anything
   for future-self?
4. Update a memory file if a real durable lesson emerged.

## Critic agent prompt skeleton

When spawning the Gate 2 critic, use this prompt structure (fill from the
entry):

> You are reviewing a proposed solution in the Huxley voice-agent framework.
> Your job is to find every reason this design is wrong, overcomplicated,
> missing the point, or has a simpler alternative. Mario has explicitly asked
> for ruthless honesty over politeness.
>
> **Problem**: <paste from triage entry>
> **Why it matters**: <paste>
> **Proposed design**: <paste>
> **Relevant code paths**: <list with file:line refs>
> **Definition of Done (proposed)**: <paste>
>
> Answer concretely:
>
> 1. Does this design actually solve the problem stated? Where does it fall
>    short of the Definition of Done?
> 2. What is the simplest possible alternative? Is it strictly worse, or
>    competitive?
> 3. What does this design make harder for the next active items
>    (`docs/triage.md` Tier 1) — especially proactive turns, messaging, custom
>    hardware client?
> 4. What hidden assumption is the design making about the user, the runtime,
>    the data, or the persona?
> 5. If you had to bet on what about this design will need to change within 3
>    months of shipping, what is it?
> 6. What test would catch the most likely subtle regression?

## Per-item template

When adding a new entry to the Active sections, use this skeleton:

```md
## T<tier>.<n> — Short title

**Status**: queued · **Task**: #N · **Effort**: S/M/L

**Problem.** <one paragraph>

**Why it matters.** <one paragraph>

### Validation (Gate 1)

<evidence the problem is real — log, repro, code citation>

### Design (Gate 2 — non-trivial only)

<sketch>

### Critic notes (Gate 2 — non-trivial only)

<findings + responses>

### Definition of Done (locked at Gate 2)

- bullet
- bullet

### Tests (Gate 3)

- regression test for the symptom
- contract tests for new abstraction surfaces

### Docs touched (Gate 4)

- list, or `none affected (verified each)`

### Ship (Gate 5)

- commit hash · date · one-line lessons
```

---

# Active — Tier 1 (framework dream)

These advance the central thesis: a voice-agent framework whose load-bearing
differentiator is "LLM understands rough natural-language intent and dispatches to
user-installable custom tools, including for personal content."

## T1.1 — `Catalog` / `SearchableIndex` SDK primitive

**Status**: done (2026-04-18) · **Task**: #86 · **Effort**: ~3 commits · **See Ship section below for final state**

**Problem.** Every personal-content skill reinvents fuzzy-search + prompt-context.
Audiobooks does `SequenceMatcher` + `prompt_context()` dump (top 50). Radio does
`_station_choices()`. News does its own dict cache. Future skills (contacts,
recipes, music files, voice notes, photos) will reinvent it again. The framework's
core differentiator is "personal content + LLM dispatch" and the SDK provides zero
help with the personal-content half.

**Why it matters.** Highest-leverage SDK addition. Collapses code in 3 existing
skills. De-risks the next 5. Without it, every new personal-content skill
re-imports fuzzy-match.

### Validation (Gate 1)

Code paths reinventing the same pattern, all confirmed:

- `packages/skills/audiobooks/src/huxley_skill_audiobooks/skill.py` — `_fuzzy_score` (SequenceMatcher), `_resolve_book` (fuzzy iter over `_catalog`), `prompt_context()` (manual dump of `_catalog[:50]` as Spanish lines)
- `packages/skills/radio/src/huxley_skill_radio/skill.py` — `_station_choices()` (manual prompt dump), case-insensitive station name iter
- `packages/skills/news/src/huxley_skill_news/skill.py` — `dict[str, tuple[float, dict]]` cache layer with manual TTL/key composition
- Future, per `docs/roadmap.md` v2: contacts (messaging), music library, recipes — all need fuzzy search + prompt awareness

The repeated pattern across 3 shipped skills + 3 planned skills is the validation.

### Design (Gate 2 — locked 2026-04-18 after user sign-off on four decisions)

**Decision 1 — Scope: full Catalog primitive, not a thin helper module.**
The framework is committing to "personal content + LLM dispatch" as the headline
differentiator; building a real primitive matches that thesis. A thin helper
(`huxley_sdk.search.fuzzy_match`) would do half the work and force a rewrite when
the full primitive lands.

**Decision 2 — Persistence: in-memory rebuilt at `setup()`, with FTS5 upgrade
path baked into the API.** All current and near-future AbuelOS skills have small
catalogs (19 books, 7 stations, ~100 contacts) where rebuild-from-source-of-truth
is fast (sub-second) and avoids the staleness problem that persistent indexes
have. The `Catalog` interface stays stable; backend swaps to FTS5 later when a
skill genuinely needs persistence (10k music files etc.).

**Decision 3 — Spanish handling: pre-fold on insert + query.** Lowercase +
`unicodedata.normalize('NFKD')` strip-accents, applied symmetrically to stored
fields and incoming queries. ~5 LOC. Avoids the C-extension territory that
custom FTS5 tokenizers require. Language-agnostic enough for a future English
persona without rework.

**Decision 4 — Two delivery modes always available.** Both
`catalog.as_prompt_lines(limit)` and `catalog.as_search_tool(name, description)`
exposed on every Catalog instance. Skill picks per use case. Cost is zero; avoids
forcing a guess about future skill needs.

**Locked API** (revised post-critic 2026-04-18 + Mario scoping confirmation that no Huxley deployment will ever ship a >100-item catalog):

```python
catalog = ctx.catalog()  # default name; ctx.catalog("playlists") only when skill has multiple
await catalog.upsert(
    id="garcia-cien-anos",
    fields={"title": "Cien años de soledad", "author": "Gabriel García Márquez"},
    payload={"path": "...", "duration": 1234.5},
)
hits = await catalog.search("garcia marquez", limit=5)
# → [Hit(id, score, fields, payload)]

prompt_text = catalog.as_prompt_lines(limit=50)
# → "Biblioteca:\n- \"Cien años de soledad\" por Gabriel García Márquez\n- ..."
```

**Module layout**:

- `packages/sdk/src/huxley_sdk/catalog.py` — public `Catalog` class + `Hit` dataclass + `_fold` accent-stripper
- `packages/sdk/src/huxley_sdk/types.py` — extend `SkillContext` with `catalog(name) -> Catalog` factory method (returns a fresh in-memory Catalog per name; framework doesn't share state across skills)
- `packages/sdk/src/huxley_sdk/__init__.py` — export `Catalog`, `Hit`
- `packages/sdk/tests/test_catalog.py` — primitive tests (insert, search, fold, prompt format, tool def)

**Scoring**: SequenceMatcher ratio per field, max across fields. Preserves the
current audiobooks behavior (which we know works on the live library) exactly —
refactor is drop-in. Hits sorted by descending score; ties broken by insertion
order. Accent folding (NFKD strip + lowercase) applied symmetrically to stored
fields and incoming queries before scoring.

### Critic Notes (Gate 2)

Spawned a critic against the locked design. Five findings; outcome:

- **(1) Don't ship equal-weight Jaccard** — accepted. Switched scoring backend
  to SequenceMatcher. Drop-in refactor is now provable via regression-parity
  test against the existing `_resolve_book`.
- **(2) Cut `as_search_tool` from v1** — accepted after Mario's scoping
  confirmation: max audiobook library 100, max contacts 10, music never
  ships locally. No catalog Huxley will ever ship needs search-on-demand
  delivery; everything fits in prompt context. Removing this cuts ~50 LOC
  of public API surface + speculative test surface. Add when caller
  materializes.
- **(3) Alias-list support for contacts** — deferred. With 10 contacts max,
  the contacts skill can fold aliases into the field string ("Carlos
  Carlitos mi hermano") and let the model do alias resolution from prompt
  context. Revisit when a future skill genuinely needs structured alias
  lookup.
- **(4) `catalog.clear()` for mid-session reindex** — deferred. Use case
  (reminders skill) is blocked on ProactiveTurn (T1.4), months out.
  Audiobooks/radio/contacts don't mutate mid-session in this deployment.
- **(5) SequenceMatcher typo tolerance** — resolved by accepting Finding (1).
  Same backend → same typo behavior → regression test posture preserved.
- **(6) Default `name` parameter** — accepted. `ctx.catalog()` is the
  common case; `ctx.catalog("playlists")` only when a skill has multiple.
- **(7) Async API over sync backend** — confirmed kept. FTS5 swap stays
  invisible to skills.

Plus the critic's 5 specific test asserts locked into the DoD test list below.

**Decided NOT to (final):**

- `as_search_tool` method (cut per critic + scoping)
- Alias-list support `fields: dict[str, str | list[str]]` (deferred)
- `catalog.clear()` (deferred)
- Multi-skill shared catalogs (each skill gets its own namespace)
- File-watcher invalidation (skills can re-call `setup()` if needed; framework doesn't watch)
- Per-field weighting in v1 (max-across-fields suffices for current shape)
- Persistent FTS5 backend in v1 (in-memory matches current scale and Mario-confirmed future scope)

`upsert/search` are async even for the in-memory backend so a future FTS5 swap is a backend change, not an API change.

### Definition of Done

- [ ] `huxley_sdk.catalog` module: `Catalog` class with `upsert`, `search`, `as_prompt_lines` methods; `Hit` dataclass; `_fold` helper
- [ ] `SkillContext.catalog(name="default")` factory method on the existing dataclass
- [ ] `huxley_sdk.__init__` exports `Catalog`, `Hit`
- [ ] Spanish accent-folding works symmetrically (stored + query); covered by tests
- [ ] Scoring uses SequenceMatcher backend (preserves existing audiobooks behavior)
- [ ] `as_prompt_lines` produces the same shape audiobooks already uses (drop-in refactor)
- [x] Audiobooks skill refactored: replace `_fuzzy_score`, `_resolve_book`, `prompt_context()` catalog dump with Catalog calls; existing tests still pass (61 after removing 4 fuzzy-score unit tests now covered by SDK Catalog tests)
- [ ] ~~Radio skill refactored: replace `_station_choices()` with Catalog `as_prompt_lines`~~ — **dropped after closer look (2026-04-18)**. Radio's tool description uses inline comma-separated format (`"caracol (Caracol Radio), blu (Blu Radio)"`) while `as_prompt_lines` is newline-bulleted. Forcing the conversion would make the prompt uglier, not cleaner. The exact-id-with-case-insensitive-name-fallback lookup pattern is also not Catalog-shaped. Radio gets zero functional benefit from the refactor and a real readability cost. Kept as-is.
- [ ] News skill: NOT refactored — its dict-cache use case is different (TTL + URL keys, not fuzzy match) and shouldn't bend the Catalog shape
- [ ] All shipped tests still green (337 across SDK + core + skills after audiobooks refactor)

**Critic-flagged regression asserts (locked into Gate 3 test list):**

- [ ] **Regression parity**: `test_catalog_matches_legacy_audiobooks_resolution` — load full AbuelOS-style audiobook fixture, run 10 queries the old `_resolve_book` handled correctly + 3 misspelling cases. Top-1 must match. _This is the "drop-in refactor" proof; without it, "65 tests pass" means nothing because those tests mock the fuzzy layer._
- [ ] **Misspelling tolerance**: query "naufrago" (no accent, missing g) → top hit "Relato de un náufrago"
- [ ] **Stopword noise**: query "el" against 5 "El X" titles → no result scores above a low threshold
- [ ] **Determinism**: same fixture + same query → byte-identical top-10 across 100 runs
- [ ] **Prompt parity**: `as_prompt_lines(50)` on the audiobook fixture produces byte-identical output to the current `prompt_context()` (so system prompt hash is preserved across the refactor)

### Tests (Gate 3 — to be filled after impl)

To be added in `packages/sdk/tests/test_catalog.py`:

- `TestCatalogInsert` — basic upsert, dup id replaces, payload preserved
- `TestCatalogSearch` — exact match, fuzzy, multi-field, accent-folded, empty query
- `TestCatalogScoring` — deterministic order, descending score, ties broken by insertion
- `TestAsPromptLines` — formatting, limit, empty catalog, header customization
- Plus the 5 critic-flagged regression asserts above

Plus refactor of existing audiobooks + radio tests (no new behavior, but
assertions move from skill internals to Catalog API).

### Docs touched (Gate 4 — to be filled after impl)

- `docs/concepts.md` — add Catalog to the vocabulary section
- `docs/skills/README.md` — Catalog usage example in the skill-author guide
- `docs/triage.md` — this entry's Ship section
- `docs/concepts.md` — added Catalog to the vocabulary section
- `docs/skills/README.md` — added "Using a Catalog" section with worked example; updated `prompt_context()` section to note empty default + reference Catalog

### Ship (Gate 5 — done 2026-04-18)

Three commits; final state:

- **Step 1**: `feat(sdk): Catalog primitive for personal-content skills` — `huxley_sdk.catalog` module, `SkillContext.catalog()` factory, exports, 31 SDK tests including the 5 critic-flagged regression asserts.
- **Step 1.5**: in-line addition of `Catalog.get(id)` and `__iter__` (needed by audiobooks for exact-id resolution and in-progress enumeration). 6 more SDK tests.
- **Step 2**: `refactor(skills/audiobooks): drop in Catalog primitive` — `_fuzzy_score` deleted, `_resolve_book` and `_search` reroute through `catalog.search()`, `prompt_context()` uses `as_prompt_lines()`, `_list_in_progress` uses `__iter__`. Helpers `_hit_summary`/`_hit_to_book` bridge between Catalog Hits and the legacy flat-dict shape callers expect — kept the refactor minimally invasive in the rest of the skill. Test helpers added in `test_skill.py` and `test_coordinator_skill_integration.py`.
- **Step 3 (radio)**: dropped after closer look. Radio's `_station_choices()` is inline-comma format vs Catalog's newline-bullet — forcing the conversion would degrade output, not improve it. Radio kept as-is. Documented the decision in DoD.
- **News**: never in scope; its dict cache is a different shape (TTL + URL keys, not fuzzy match) and shouldn't bend the Catalog API.

**Final test count**: 60 SDK + 179 core + 61 audiobooks + 18 news + 19 radio = **337 tests, all green**. Was 297 before the Catalog work (net +40: +37 new SDK tests, -4 audiobook unit tests for `_fuzzy_score` deleted as covered by SDK tests, +5 audiobook tests for the new test helpers' edge cases, +X net other adjustments).

**Lessons**:

- The original `as_search_tool` cut from v1 (after critic + Mario's scoping) was the right call. Building it would have added ~50 LOC of code + tests with zero current callers; AbuelOS's max-100-item catalogs always fit in prompt context.
- The Gate 2 critic spawn paid for itself in ONE finding (#1: Jaccard scoring would have regressed audiobooks ranking on the live library). Switching the backend to SequenceMatcher made the refactor a true drop-in instead of a behavior change.
- `Catalog.get(id)` and `__iter__` were needed by the audiobooks refactor and weren't in the original API. Adding them mid-refactor was cheap because the API didn't ship yet — caught at exactly the right moment. If the Catalog had shipped without them and audiobooks tried to refactor later, we'd have either bent existing methods or added them in a follow-up. The "build the primitive AND its first real consumer in the same change" pattern is what surfaced this.
- The radio decision (skip refactor) is itself a finding — not every "personal-content skill" wants `as_prompt_lines`-style bullet output. Inline-comma format is a real shape too. The Catalog primitive serves the audiobooks-shape; future skills should evaluate per-shape rather than assuming the primitive applies.
- Test-side helpers (`_book_at`, `_book_with_title_substring`) bridge between the Catalog API and pre-refactor test assertions. Letting tests use a flat-dict view via these helpers kept the refactor diff small in the test files (touching ~20 lines instead of ~100).

**Follow-ups filed**:

- None opened. The deferred items (`as_search_tool`, alias-list support, `catalog.clear()`) all have explicit revisit triggers in the "Decided NOT to" list and the broader Tier 3 follow-up note. Will reopen as new triage items when those triggers fire.

---

## T1.2 — I/O plane spec (`docs/io-plane.md`)

**Status**: spec drafted (2026-04-18), awaiting critic review · **Task**: #87 · **Effort**: spec complete; implementation staged across T1.3 (refactor prereq) + T1.4 (primitives) + T1.8/T1.9/T1.10 (skills that use them)

**Scope expansion (2026-04-18)**: this triage item was originally
"ProactiveTurn spec." During design, Mario's father surfaced two load-
bearing requirements (panic button, instant-connect inbound calls) that
pulled the design into a broader architectural question: what are Huxley's
abstract primitives for skill-extensible I/O? A narrow ProactiveTurn spec
would have forced skill-specific concepts ("call," "emergency," "ring")
into the framework. The expanded scope defines three sibling primitives
that together let any skill extend the runtime without the framework
knowing what it does.

**Problem.** Current `TurnCoordinator` and `AudioServer` hard-code:

- User turns originate from PTT (no "framework wants to speak now" entry point)
- Mic PCM routes to OpenAI (no "skill takes over mic" mechanism)
- Wire protocol has fixed message types (no "hardware button / arbitrary client
  event" generic channel)
- Background tasks are unsupervised (skills spawn `asyncio.create_task`;
  framework blind to crashes — explicit gap in `docs/extensibility.md`)

Each of these blocks a different planned skill class. Together, they're "the
I/O plane" — the mechanisms that connect clients to skills.

**Why it matters.** The mandate from Mario: "The framework should allow for
this extensibility without it ever thinking about specific skills. Just allow
for new behaviors in such an abstract way that implementation has a direction
around the model that Huxley itself is." Adding skill-specific concepts to
the framework is a one-way architectural debt.

### Locked design decisions (Mario 2026-04-18)

1. **Scope: expanded (option B)**. Design all three primitives (turn
   injection, `InputClaim`, `ClientEvent` subscription) + supervised
   `background_task` helper as a coherent I/O-plane spec, not as separate
   narrow items.

2. **Urgency top tier renamed `CRITICAL`** (not `RING`). Framework doesn't
   know what lives in the top tier; skills decide. A future fire-alarm
   skill uses the same tier as a future calls skill.

3. **`ctx.notify()` renamed `ctx.inject_turn()`**. Primitive name
   reflects the mechanism (synthetic-turn injection), not a use case
   (notifying the user).

4. **Wire protocol: hybrid**. Framework-reserved message types stay fixed.
   Add one generic `{"type": "client_event", "event": "<namespaced-key>",
"payload": {...}}` for skill subscriptions.

Plus a fifth locked decision from the critic pass: **earcons deferred**.
Three urgency tiers have distinct persona-owned earcons; the audio files
are curated separately from Stage 1 implementation. Missing earcons log a
warning and play nothing (framework doesn't block on audio curation).

### Spec artifact

`docs/io-plane.md` — authoritative design, includes:

- Abstract model (three streams + turn loop)
- Guiding principle (framework names mechanisms, not use cases)
- Five primitives in full detail with SDK surfaces, arbitration, tests
- Composition examples for reminders, messaging, calls, voice-memo, panic
  button
- Staged implementation plan (Stage 0 refactor, Stages 1-4 primitives)
- Descope candidates + revisit triggers
- Single open product question (earcon sourcing)

### Deliverables of T1.2 itself

- [x] `docs/io-plane.md` spec written
- [x] Triage entries updated: T1.3 (refactor deltas), T1.4 (staged impl)
- [x] New triage entries filed: T1.8 (reminders), T1.9 (messaging), T1.10 (calls)
- [x] `docs/concepts.md` updated with new vocabulary
- [x] `docs/protocol.md` updated with hybrid wire protocol
- [x] `docs/skills/README.md` updated with skill-author sections per primitive
- [ ] Critic review against "the dream of Huxley" — spawned after doc pass
- [ ] Critic findings incorporated
- [ ] Final commit of spec package

---

## T1.3 — Coordinator refactor (extract collaborators for I/O plane)

**Status**: done (2026-04-18) · **Task**: #88 · **Effort**: 1 session ·
**Risk**: T2.3 shipped (integration-test harness in place) — refactor
landed without behavior change (223 tests green, was 179 pre-refactor;
+44 tests across the new modules).

**Problem.** `coordinator.py` is 586 LOC juggling PTT lifecycle, model deltas,
tool dispatch, six side-effect kinds, atomic interrupts, completion-prompt
synthesis, synthetic turn injection, and `model_speaking` flag ownership
transfer. Adding the I/O-plane primitives (T1.4) without restructuring
produces unmaintainable code.

**Why it matters.** Must precede T1.4. The "ownership transfer of
`model_speaking`" gymnastics in `_consume_audio_stream` is already a code
smell; turn injection + `InputClaim` compound it into an unreviewable mess.

### Scope expansion (2026-04-18 after T1.2 spec)

The I/O plane spec (`docs/io-plane.md`) reshaped what the refactor needs to
extract. Four collaborators (was three), each with a shape informed by the
primitives they'll support:

**1. `SpeakingState` — owns the "who's on the speaker" flag.**

- Currently: boolean `model_speaking`.
- Refactored: named-owner enum — `"user" | "factory" | "completion" |
"injected" | "claim"` | `None`.
- `acquire(new_owner)` / `release(expected_owner)` methods. Release is a
  safe no-op if the owner has already changed (another claim preempted).
- Rationale: the current boolean forces guard-checks scattered across the
  coordinator (`if self._model_speaking: ...`). Named owners centralize
  the state machine; `SpeakingState.owner == "injected"` is self-documenting.
- T1.3 MUST land with the named-owner shape even though some owners
  (`"injected"`, `"claim"`) aren't used yet. Otherwise T1.4 retouches
  SpeakingState in every stage.

**2. `MediaTaskManager` — owns the running audio stream task.**

- Currently: single `asyncio.Task` slot (`current_media_task`) with ad-hoc
  cancellation in `_stop_current_media_task`.
- Refactored: encapsulates the task + `arbitrate(incoming_urgency,
yield_policy) -> Decision` method. Decision is one of `preempt |
duck_chime | hold | drop`.
- Includes a `DuckingController` stub (no-op in Stage 0, wired in Stage 1):
  `duck_for(ms: int)` ramps output gain down, holds, ramps up.
- Rationale: arbitration logic lives in one place, testable as a pure
  function. Stage 1 fills in the duck_chime branch; Stage 0 only needs
  the interface.
- Ducking is server-side software gain (multiply PCM samples by ratio).
  Simple and client-agnostic. Client-side ducking is a deferred improvement.

**3. `TurnFactory` — single turn-creation entry point.**

- Currently: Turn objects constructed in three places (`on_ptt_start`,
  `_maybe_fire_completion_prompt`, implicit completion flow) with subtle
  shared invariants.
- Refactored: `TurnFactory.create(*, source: TurnSource, initial_state)`
  where `TurnSource` is an enum: `USER | COMPLETION | INJECTED`.
- Stage 0 MUST include `TurnSource.INJECTED` as an enum value even though
  nothing creates injected turns yet. Same reasoning as SpeakingState —
  don't force retouch.

**4. `MicRouter` — new collaborator.**

- Currently: mic PCM flows directly `AudioServer.on_audio_frame` →
  `coordinator.on_user_audio_frame` → `provider.send_user_audio`. Hard-
  coded routing.
- Refactored: `MicRouter` is the single destination dispatcher. Default
  handler = voice provider. An active `InputClaim` swaps the handler;
  claim end restores default.
- `MicRouter.claim(on_frame: Callable)` → returns a handle;
  `handle.release()` restores default.
- Stage 0 MUST extract the interface even though only the default handler
  exists yet. Stage 4 wires `InputClaim` through it.

### Deliverables

- [x] `SpeakingState` module extracted with named-owner API
      (`huxley.turn.speaking_state` — `SpeakingOwner` enum with
      `user|factory|completion|injected|claim` + `acquire/release/
force_release/transfer`)
- [x] `MediaTaskManager` module extracted with `arbitrate()` +
      `DuckingController` stub (`huxley.turn.media_task` +
      `huxley.turn.arbitration` pure function, 16-case table)
- [x] `TurnFactory` module extracted with `TurnSource` enum including
      `INJECTED` (`huxley.turn.factory`, `huxley.turn.state`)
- [x] `MicRouter` module extracted with claim/release API (only default
      handler wired — `huxley.turn.mic_router`)
- [~] `TurnCoordinator` reduced: 644 → 623 LOC. The <400 LOC target
  needs further extraction (side-effect dispatcher, completion-prompt
  driver) that's deliberately deferred; this PR moved state ownership
  only. Follow-up if needed once T1.4 stages clarify where the seams
  really should sit.
- [x] All existing coordinator + skill-integration tests pass unchanged
      (196 → 223 with the new unit suites)
- [x] New unit tests per extracted collaborator
      (`test_speaking_state`, `test_mic_router`, `test_media_task_manager`,
      `test_arbitration`)
- [x] `docs/architecture.md` updated with the new collaborator list +
      "Turn coordinator internals" section
- [x] `docs/turns.md` updated: `current_media_task` → `MediaTaskManager`,
      interrupt step 4 uses `media_tasks.stop()` + `speaking_state.force_release()`

### Lessons

- The "ownership transfer gymnastics" in `_consume_audio_stream` became
  one `speaking_state.transfer(FACTORY, COMPLETION)` call. Named owners
  were the right shape; the boolean was hiding the real intent.
- Coordinator LOC barely moved because the orchestration (PTT lifecycle,
  provider events, interrupt sequence, tool dispatch) is inherently
  coordinator-shaped. Extracting state ownership is the whole point of
  T1.3; the method count dropped from 22 to 21 but method _complexity_
  dropped noticeably — every `self._model_speaking = True; await
self._send_model_speaking(True)` pair collapsed to a single
  `acquire()`.
- `Urgency` + `YieldPolicy` shipped in the SDK (`huxley_sdk.priority`) so
  skills can declare them in T1.4. `Decision` stayed framework-internal.

Recommended discipline: extract one collaborator per commit, tests green
between each. If any single commit's diff exceeds ~300 LOC, split further.

---

## T1.4 — I/O plane implementation (staged primitives)

**Status**: Stage 1 substrate shipped (focus management); Stages 2-4 need re-scoping post-pivot (see below) · **Task**: #89 · **Scope**: implements `docs/io-plane.md` primitives — vocabulary has since evolved (see 2026-04-18 ADR "Pivot from arbitration model to AVS focus-management")

**Problem.** See T1.2 for full context. The I/O plane (turn injection,
`InputClaim`, `ClientEvent` subscription, supervised `background_task`) is
the framework-level infrastructure every v∞ skill needs.

**Why it matters.** Unblocks reminders, messaging, calls, panic button,
voice-memo, memory recall, companionship-mode greetings, and any future
skill that extends the runtime beyond request/response text. The primitives
are framework-level; each should land before its first-consumer skill ships.

### Pivot note (2026-04-18, mid-Stage-1)

The plan recorded below — `Urgency` + `YieldPolicy` + `arbitrate()` +
`DuckingController` as the coordination vocabulary — was started at T1.3
and partially realized. During Stage 1 design it became clear the
tuple-based arbitration model was less composable than AVS's channel-
oriented focus management, which models the same decisions as stacked
Activities per named channel (DIALOG / COMMS / ALERT / CONTENT) with
FocusState transitions.

**What shipped instead** (T1.4 Stage 1, three commits):

- `1f9b232` — `huxley.focus.vocabulary` (Channel, FocusState, MixingBehavior,
  Activity, ContentType, ChannelObserver) + `huxley.focus.manager`
  (`FocusManager` actor-pattern arbitrator with mailbox serialization).
  34 tests.
- `a1afabd` — `huxley.turn.observers` (`DialogObserver` + `ContentStreamObserver`
  implementing `ChannelObserver`). 14 tests.
- `31a18cf` — `TurnCoordinator` rewired to drive `ContentStreamObserver` directly
  (FOREGROUND/PRIMARY on start, NONE/MUST_STOP on stop). Deleted:
  `MediaTaskManager`, `DuckingController`, `arbitrate()`, `Urgency`,
  `YieldPolicy`, and their tests (~500 LOC removed). `SpeakingState` kept —
  it's still the right shape for the DIALOG-channel speaker flag today.

### Stage 1 — Gate-2 critic findings (2026-04-18, belated Gate-2 review)

Spawned a critic against the shipped Stage 1 substrate (three commits
above). Mario asked for ruthless honesty. Verdict: substrate is solid
enough to build Stage 2 on, with one must-fix and handful of should-fix
items. Full critic report in session transcript; summary here.

**🔴 Must-fix before Stage 2** (real latent bugs) — ✅ **shipped in `b7c30e6`** (Stage 1a):

- **(#2) Self-cancel guard in `ContentStreamObserver._cancel_pump`.**
  ⚠️ **Revised assessment after implementation**: the critic's "deadlock"
  claim was wrong. Empirically verified: Python raises `CancelledError`
  (not `RuntimeError`) at `await task` when the cancel flag is set by
  the preceding `task.cancel()`, and `contextlib.suppress` catches it —
  the task completes cleanly and `on_natural_completion` fires. No
  deadlock, no silent failure. Additionally, the Stage 2 path the
  critic worried about (`FocusManager.release` from inside `on_eof`)
  doesn't actually reenter synchronously — the actor serializes via the
  mailbox, so NONE delivery happens after the pump task has already
  completed. The guard is kept as **defensive programming**: calling
  `task.cancel()` on a task that's finishing naturally leaves it in a
  transient "cancelling" state that could interact surprisingly with
  `asyncio.shield` / cancel-aware code in the future. Cost: 4 lines.
  Tests (`TestContentStreamObserverSelfCancel`) verify reentrant NONE
  delivery behaves identically to a normal natural-end flow.
- **(#5) `on_session_disconnected` race**: `force_release()` before
  `_stop_content_stream()` has multiple await points between them.
  Pump can re-acquire FACTORY in the gap, surviving past cleanup.
  Fix: reorder — stop content stream first, then force_release.
- **(#6) Same race in `interrupt()` step 3 vs step 4.** Identical fix.
- **(#1) Transition-order invariant test.** Critic notes "old-off before
  new-on" holds in `_handle_acquire`/`_handle_release`/
  `_handle_stop_foreground` but isn't locked down by a test — a future
  refactor could silently break it. No code change; add regression test.
- **(#7) Trivial cleanup**: `asyncio.get_event_loop()` →
  `asyncio.get_running_loop()` in `FocusManager._start_patience_timer`
  (deprecated call path).

**🟠 Defer to Stage 2 planning** (architectural concerns, not bugs):

- **(#3) `_notify_safe` swallows all observer exceptions including bugs.**
  Add `dev_event("focus_observer_failed", ...)` routing + richer context
  (`was_foreground`, `had_pump_task`). Needs plumbing from FocusManager
  to coordinator's `send_dev_event` — coordinator integration work,
  book it with Stage 2.
- **(#4) `_stop_content_stream` clears `self._content_obs = None` before
  awaiting NONE.** Latent — reader-during-teardown gets stale view. Stage 2
  FocusManager wiring restructures this path entirely (observer ref lives
  in Activity, not coordinator), so fixing now is churn. Revisit at Stage 2.
- **(#8) Test: reentrant `acquire()` from within observer callback.**
  Docstring claims this is safe; no test locks it down. Stage 2 will
  rely on it (inject_turn acquires DIALOG from within a tool handler).
- **(#9) Test: same-channel LIFO stack semantics with patience > 0.**
  Existing test only covers patience=0. Add A1 (patience=60s) →
  displaced by A2 → A2 released → A1 re-promoted with correct call
  history.
- **(#10) `FocusManager.stop()` doesn't await observer-spawned follow-up
  tasks.** Contract question: if a DialogObserver's `on_stop` kicks off
  async work (storage write, etc.), `fm.stop()` returns before it
  completes. Document "observers must not spawn unsupervised tasks
  from on_stop" or add a join mechanism. Resolve at Stage 2 when
  `inject_turn` adds real cleanup semantics.
- **Design A — SpeakingState vs FocusManager authority contract.**
  Two sources of truth for "is anyone speaking right now": DIALOG
  channel's FG state (claimed the speaker) vs `SpeakingState.owner`
  (actual audio flow). Today synced by convention. Critic recommends
  writing the contract into `docs/architecture.md` explicitly:
  `SpeakingState` = "client speaking indicator should be on";
  `FocusManager` DIALOG FG = "a turn has claimed the speaker."
  Do alongside Stage 2 when Activity↔SpeakingState relationship
  becomes concrete.
- **Design B — "Mechanical swap" claim in the pivot ADR is optimistic.**
  Migrating from direct-drive observer to FocusManager-owned Activity
  changes: `current_media_task` becomes None until first pump spawns
  (async-acquire); `_content_obs` field goes away (query FocusManager
  instead); closures currently capturing coordinator state may need
  restructuring. Critic budgets **1-2 extra days in Stage 2** for this.

**✅ Dismissed:**

- **(Design C)** Critic flagged `AudioStream.content_type` as exposed to
  skills but unexercised. Verified: `content_type` is NOT a public SDK
  field. `ContentType` only lives in `focus/vocabulary.py`, framework-
  internal. Non-issue.

### What's left for the rest of T1.4 (post-pivot rescope)

Stage 1 originally planned `inject_turn` + arbitration + ducking as one
chunk. Post-pivot, that chunk breaks into smaller pieces — several
shippable in isolation. Re-ordered by "smallest user-visible win":

**Stage 1a — Must-fix commit** ✅ **done** (`b7c30e6`, 2026-04-18).
The 🔴 critic findings above, one focused commit, no behavior change
beyond bug elimination. 253 tests green (was 244; +9 new regression
tests). Ordering fix verified — `TestCleanupOrdering` tests correctly
fail if reordering is reverted.

**Stage 1c — `inject_turn` MVP** (reordered ahead of 1b after plan-level
review 2026-04-18; see session transcript). Broken into four small
commits instead of one 3-day monolith. Each is individually
smoke-testable with no regression from the previous state.

- **Stage 1c.0 — SpeakingState authority doc** ✅ **done** (`<this
commit>`, 2026-04-18). Written into `docs/architecture.md` under
  "Turn coordinator internals → Authority contract." Defines:
  `SpeakingState` authoritative for "client speaker indicator";
  `FocusManager` authoritative for "who holds the claim"; coordinator
  owns the bridge. Transition table documents every DIALOG/CONTENT
  FocusState change and the corresponding SpeakingState write.
  Resolves Open Question 2 below.

- **Stage 1c.1 — Wire FocusManager into Application lifecycle**
  (~80 LOC, ~1h, queued). `Application` constructs +
  `FocusManager.with_default_channels()`, `start()` in `run()`,
  `stop()` in `_shutdown`. Coordinator gets a reference via
  constructor. **Still direct-drives the observer — no behavior
  change.** Smoke: audiobook playback and interrupt paths unchanged.

- **Stage 1c.2 — Route CONTENT through FocusManager** (~150 LOC,
  ~2-3h, queued). `_start_content_stream` creates
  `Activity(channel=CONTENT, interface_name, content_type=NONMIXABLE,
observer=ContentStreamObserver(...))` and calls
  `fm.acquire(activity)` instead of
  `obs.on_focus_changed(FOREGROUND)`. `_stop_content_stream` →
  `fm.release(CONTENT, interface_name)`. `current_media_task` becomes
  a query through FocusManager's stack (critic's Design B concern
  applies here — budget carefully). **Invisible to users; substrate
  now driving.**

- **Stage 1c.3 — `SkillContext.inject_turn(prompt)` MVP** (~80 LOC,
  ~2h, queued). Add method to `SkillContext`. Implementation: framework
  creates `Activity(channel=DIALOG, ...)` on coordinator's FocusManager.
  Content channel Activity (if present) goes BACKGROUND → MUST_PAUSE
  (NONMIXABLE) → pump cancels via the path already shipped in 1a. LLM
  narrates the injected prompt. **Ships inject_turn as a working
  feature.** Unblocks reminder skill MVP (T1.8). Single urgency tier
  (preempt); queue, TTL, dedup all deferred to Stage 1d.

**Stage 1b — Server-side duck PCM envelope (~1 day, queued, moved
after 1c).** Replaces the MUST_PAUSE fallback for MAY_DUCK+MIXABLE
Activities with a real PCM gain ramp. Open Question 3 decided:
ducking lives **inside `ContentStreamObserver`** (not a shared
`huxley.audio.ducking` module); extract later if a second consumer
needs it. Linear ramp, 100ms duration, 0.3 target gain. PCM16 scaling
via `struct` (no numpy dep). Polishes the UX of 1c.3 from hard-pause
to smooth duck.

**Stage 1d — Hold queue + TTL + dedup (~2 days, queued).** Add
CHIME_DEFER-equivalent semantics on top of 1c: `inject_turn(urgency=
LOW_PRIORITY)` queues instead of preempting; `expires_after` drops
from queue silently; `dedup_key` replaces on collision; next PTT
drains FIFO. Needs `InjectedTurnHandle.wait_outcome()` to become
useful (first consumer: medication reminder retry loop).

**Stage 1e — `docs/observability.md` update (~30 min, queued).**
Document the `focus.acquire`, `focus.release`, `focus.change`,
`focus.patience_expired`, `focus.observer_failed`, `focus.observer_slow`
events that shipped in Stage 1 Part 1 but aren't yet in the
observability canon. Minor gap noted during doc realignment commit
`9695e0f`.

**Stage 2 — `InputClaim` + `MicRouter` wiring.** Unchanged in scope;
see original Stage 2 section below. Open question (below) on whether
it still follows Stage 1c/d or comes first.

**Stage 3 — Supervised `background_task`.** Unchanged in scope.

**Stage 4 — `ClientEvent` + `server_event` + capabilities handshake.**
Unchanged in scope.

### Open questions (resolve before picking up Stage 2)

1. **Stage order post-pivot.** Originally: inject_turn → InputClaim →
   background_task → ClientEvent (to validate `MicRouter` suspend/resume
   early against a simpler `inject_turn`). Post-pivot, Stage 1c's
   `inject_turn` MVP is simpler (a DIALOG Activity acquire via
   FocusManager), which weakens the "keep InputClaim early to de-risk"
   argument. Worth a fresh critic pass before starting Stage 2. **Flag
   to resolve when we pick up Stage 2.**

2. **SpeakingState authority contract** (Design A above). ✅ **resolved
   Stage 1c.0 (this commit)**. Documented in `docs/architecture.md`
   "Turn coordinator internals → Authority contract" with a transition
   table covering every DIALOG/CONTENT FocusState change and the
   coordinator's corresponding SpeakingState write. Summary:
   `SpeakingState` is authoritative for "client speaker indicator";
   `FocusManager` is authoritative for "who holds the claim";
   coordinator bridges. They can transiently disagree; reconciliation
   happens at barriers.

3. **Duck envelope location**: ✅ **resolved 2026-04-18 at Stage 1b
   kickoff**. Lives **inside `ContentStreamObserver`** (not a shared
   `huxley.audio.ducking` module). One consumer today; extract later
   if a second observer type wants ducking (YAGNI).

### (Archived) original Stage 1 plan — `inject_turn` + arbitration + ducking

Kept for traceability. Arbitration / DuckingController / Urgency /
YieldPolicy references below describe the pre-pivot vocabulary and are
**superseded** by the focus-management substrate. When Stage 2+ picks up,
re-derive deliverables from the new vocabulary (Channel, FocusState,
MixingBehavior), not from this list.

**Effort estimate (pre-pivot)**: ~1.5 weeks. **Depended on**: T1.3.

**Deliverables (pre-pivot, superseded)**:

- `Urgency` + `YieldPolicy` + `Decision` + `TurnOutcome` enums in SDK
- `AudioStream.yield_policy: YieldPolicy = YIELD_ABOVE` field
- `SkillContext.inject_turn(prompt, *, urgency, dedup_key, expires_after) -> InjectedTurnHandle` (note: `tag` param dropped — redundant with dedup_key + logger's skill binding)
- `InjectedTurnHandle` with `.acknowledge()`, `.cancel()`, `.wait_outcome()` returning `TurnOutcome`
- Arbitration pure function `huxley.turn.arbitration.arbitrate(urgency, yield_policy) -> Decision` with 5 outcomes (`SPEAK_NOW | PREEMPT | DUCK_CHIME | HOLD | DROP`)
- `DuckingController` wired into `MediaTaskManager` (server-side PCM gain envelope)
- Coordinator integration: accepts `inject_turn`, routes through `TurnFactory(source=INJECTED)`, arbitrates, acquires `SpeakingState` as `"injected"` owner; `DUCK_CHIME` outcome plays tier earcon on top of ducked media
- Earcon playback slot: framework plays persona-owned `notify_chime_defer` / `notify_interrupt` / `notify_critical` roles. Missing → log warning + play nothing (audio curation is separate task)
- TTL expiry with persona-level defaults; expiry emits `coord.inject_expired`
- Dedup: hash `dedup_key` per in-memory queue; replace on collision
- Multi-item hold queue: FIFO drain on PTT, each deferred turn plays as a separate proactive turn in arrival order

**Tests (pre-pivot, superseded)**:

- Pure-function arbitration tests (16-row table covering all urgency × yield_policy combinations + the idle path)
- Coordinator unit tests for each decision outcome
- Queue behavior: hold, drain on PTT, TTL expiry mid-flight, dedup replace
- **TTL expiry during active media** (critic IS3): CHIME_DEFER with 5s TTL during audiobook; PTT after 10s must not fire the expired turn but must emit the `N pendiente(s) expiraron` note
- **Multi-item FIFO drain** (critic IS5): two CHIME_DEFER queued; PTT drains both in insertion order
- `wait_outcome()` resolves correctly for each terminal state (ACKNOWLEDGED, DELIVERED, EXPIRED, PREEMPTED, CANCELLED)
- Earcon-missing graceful degradation

**UX validation (pre-pivot, superseded)**: manual trigger from a dev
endpoint (no background_task yet — that's Stage 3). Fires `inject_turn` at
each urgency tier. Browser smoke confirms the four decision behaviors.

**Docs touched (original plan)**:

- `docs/concepts.md` — new entry for turn injection (done in this triage pass — the Urgency/YieldPolicy section; replaced 2026-04-18 with "Focus management" section)
- `docs/skills/README.md` — "using `inject_turn`" section (written; currently bannered as "planned — SDK surface not yet shipped; vocabulary will change post-pivot")
- `docs/observability.md` — new event names (`coord.inject_turn`, `coord.arbitrate`, `coord.inject_expired`, `coord.inject_preempted`) — **not yet added; arbitration events won't exist; focus events (`focus.acquire`, `focus.release`, `focus.change`, etc.) shipped in Stage 1 Part 1 but aren't documented in `observability.md` yet**

### Stage 2 — `InputClaim` + `MicRouter` wiring

Pulled earlier (was Stage 4 in original plan). Rationale: it's the
lynchpin of the motivating use case (panic button + instant-connect
calls). Validating `MicRouter` and the provider suspend/resume contract
early de-risks the remaining stages.

**Effort**: ~2 weeks. **Depends on**: T1.3 (`MicRouter`), Stage 1 (`YieldPolicy` enum).

**Deliverables**:

- `InputClaim` SideEffect type in SDK
- `ClaimEndReason` enum (`NATURAL | USER_PTT | PREEMPTED | ERROR`)
- `ClaimHandle` with `.cancel()` and `.wait_end()`
- **Two entry points** for claim activation:
  - `ToolResult.side_effect = InputClaim(...)` — for tool-dispatched claims (voice memo)
  - `SkillContext.start_input_claim(claim) -> ClaimHandle` — direct entry point for event-driven latching (panic button, auto-connect inbound call) where no tool call is in the causal chain
- Both paths land in the same `MicRouter.claim(handler)` + `provider.suspend()` sequence
- Latch invariant (test-enforced): **suspend provider FIRST, then swap mic routing** — prevents audio leak during the swap window
- `on_claim_end(reason)` fires on all termination paths
- Claim's `yield_policy` participates in arbitration — a `YIELD_CRITICAL` claim only yields to CRITICAL-urgency injected turns

**Tests**:

- Both entry points latch correctly (ToolResult + direct context method)
- Mic frames reach handler (not voice provider) while active
- `speaker_source` frames reach `send_audio`
- PTT during claim fires `on_claim_end(USER_PTT)`, cleanup runs, voice resumes
- Natural end fires `on_claim_end(NATURAL)`
- CRITICAL `inject_turn` during `YIELD_CRITICAL` claim fires `on_claim_end(PREEMPTED)`; the critical turn plays after cleanup
- Non-CRITICAL `inject_turn` during `YIELD_CRITICAL` claim: turn held (does not preempt)
- **Provider suspend/resume contract** (critic IS4): idempotency (multi-suspend, resume-without-suspend); session ID preserved across suspend/resume; pending assistant audio discarded not replayed; inference blocked while suspended
- Suspend-first-then-swap ordering test (fake provider records call order)

**UX validation**:

- Tool-driven: throwaway voice-memo skill. `record_memo(seconds=10)` returns `InputClaim(on_mic_frame=writer.write, speaker_source=None)`. Speak 10s; confirm WAV file created; voice provider resumes; next PTT works; no leaked tasks.
- Direct-entry: manual test harness calls `ctx.start_input_claim(...)` simulating an event-driven flow; confirm latch behaves identically.

**Docs touched**:

- `docs/skills/README.md` — "using `InputClaim`" section (done in this triage pass, covers both entry points)
- `docs/architecture.md` — audio routing description

**Stage-2 integration checkpoint — full cascade smoke**: after Stage 2, the
first two primitives compose. Add one integration test (in the T2.3
replay harness) that exercises the cascade: simulated `inject_turn`
fires during an active `InputClaim`, arbitration runs, `on_claim_end`
triggers, claim-preempted outcome surfaces correctly. Keeps the
end-to-end path from degrading silently as Stages 3+4 pile on.

### Stage 3 — Supervised `background_task`

**Effort**: ~3 days. **Depends on**: Application startup (already in `app.py`).

**Deliverables**:

- `SkillContext.background_task(name, coro_factory, *, restart_on_crash, max_restarts_per_hour, on_permanent_failure)`
- `PermanentFailure` dataclass (last exception + restart count + elapsed)
- `huxley.background.TaskSupervisor` (new module): owns the task pool, crash logging via `aexception`, restart with exponential backoff
- Permanent failure: log + `dev_event("background_task_failed")` + invoke `on_permanent_failure` callback if provided (with its own supervision — callback raising doesn't recurse)
- Teardown: all tasks cancelled on skill teardown

**Tests**:

- Task runs normally
- Task crashes, restarts once, succeeds
- Task crashes repeatedly, exceeds budget, permanent-fail event fires
- `on_permanent_failure` callback fires with failure details
- Callback that raises is logged + stops (no recursion into itself)
- Teardown cancels cleanly

**UX validation**: extend a hello skill to deliberately crash its background task. Confirm restart. Bump past budget; confirm callback fires + dev event.

**Docs touched**:

- `docs/skills/README.md` — "using `background_task`" section (done in this triage pass; includes `on_permanent_failure` for life-safety skills)
- `docs/observability.md` — new events documented

### Stage 4 — `ClientEvent` + `server_event` + capabilities handshake

**Effort**: ~3 days. **Depends on**: `AudioServer` accepts new message types; `hello` message extended.

**Deliverables**:

- Wire protocol additions: `{"type": "client_event", ...}` (C→S) and `{"type": "server_event", ...}` (S→C)
- `hello` message gains `capabilities: list[str]` array; old clients (no field) treated as `capabilities=[]`
- `AudioServer` dispatches inbound `client_event` messages through a subscription registry
- `SkillContext.subscribe_client_event(key, handler)` — unsubscribe automatic at teardown
- `SkillContext.emit_server_event(key, payload)` — no-op with debug log if client capabilities don't include `server_event`
- Namespace convention documented: `huxley.*` reserved; skills use `<skill-name>.*`; no framework-side validation

**Tests**:

- Single subscriber: handler called with payload
- Multiple subscribers to same key: all called
- Unsubscription on teardown: handler no longer called after skill stops
- Unknown key: logs at debug
- `emit_server_event` skipped with debug log when capability absent
- Capabilities fallback (old client without field → treated as empty capabilities)

**UX validation**: browser dev client dev panel (or Shift+E) fires a `hello.ping` client event → toy skill subscribed calls `inject_turn("pong")`. Separately, toy skill emits `hello.pong` server event → browser dev client logs receipt. Both directions covered.

**Docs touched**:

- `docs/protocol.md` — hybrid wire protocol documented (done in this triage pass, dual-purpose client_event + symmetric server_event + capabilities)
- `docs/skills/README.md` — "using `subscribe_client_event` / `emit_server_event`" sections

### Skill-level follow-ons (file as separate triage items, depend on T1.4 stages)

- **T1.8 — `huxley-skill-reminders`** (after Stages 1 + 3): medication +
  appointment reminders via `inject_turn` + `background_task`. First real
  user-facing benefit of the I/O plane.
- **T1.9 — `huxley-skill-messaging`** (after Stages 1 + 3): inbound
  WhatsApp via `background_task` + `inject_turn`; outbound trigger via
  `ClientEvent` (Stage 4) optional.
- **T1.10 — `huxley-skill-calls`** (after all four stages — especially
  Stage 2 for `InputClaim` and Stage 4 for the panic-button
  `ClientEvent`): two-way calls using all four primitives. Requires
  voice-call provider integration (Twilio/SIP); separate design effort.

---

## T1.5 — Real LLM summarization on reconnect

**Status**: done (2026-04-18) · **Task**: #90 · **Effort**: ~110 LOC + 10 tests

**Problem.** Today's `disconnect(save_summary=True)` injects raw "last 20 transcript
lines" into the next session's system prompt. After 22 reconnects in a 20-hour
audiobook session, the model is reading lines that may have nothing to do with
current state. "The assistant forgets what we were just doing" is the most jarring
possible failure for an elderly user who relies on continuity.

**Why it matters.** OpenAI's 30–60 min forced session reset already hits us
multiple times per long listening session. Without real summarization the
continuity loss is invisible until the user notices.

### Validation (Gate 1)

`voice/openai_realtime.py:171-173` (pre-fix):

```python
if save_summary and self._transcript_lines:
    transcript = "\n".join(self._transcript_lines[-20:])
    await self._storage.save_summary(transcript)
```

The "summary" is literally a `\n`.join of the last 20 raw transcript lines.
On reconnect, `connect()` reads that string and appends it to the system
prompt as `"Contexto de la conversación anterior: <raw lines>"`. Worst
case: 20 lines of "user: pause / assistant: ahí va" — useless.

### Design (Gate 2)

New `huxley.summarize` module with one function: `summarize_transcript(lines,
api_key) -> str | None`. Calls `gpt-4o-mini` (cheap chat completion, NOT
the realtime API) with a Spanish system prompt instructing 3-sentence
context summary. Caps input to last 60 lines. Wrapped in try/except;
returns `None` on any failure.

Wired into `OpenAIRealtimeProvider.disconnect()` — replaces the raw-tail
join. **Falls back to raw-tail when `summarize_transcript` returns
`None`** so disconnect always saves _something_; this preserves the prior
behavior as the safety net rather than silently dropping context on
summarizer outage.

`openai>=1.60` is already a core dep; uses `AsyncOpenAI` directly. No new
dependency.

Decided NOT to:

- Add a separate `huxley.config` knob for the summary model. The model
  string is a module constant; if a future persona needs a different
  summarization model, this becomes a kwarg on `summarize_transcript`.
- Pre-compute summaries periodically. Disconnect is the natural trigger
  (and is bounded — at most 22 per 20-hour session); pre-computing during
  idle would burn tokens for sessions that don't reconnect.
- Inject `dev_event` for the summary call. Browser dev client doesn't
  display summaries today; can be added later.

### Definition of Done

- [x] `huxley.summarize.summarize_transcript(lines, api_key, *, model, max_lines, max_output_tokens) -> str | None` implemented using `AsyncOpenAI`
- [x] Returns `None` on empty transcript, missing API key, API exception, no choices, empty content
- [x] Caps input to `max_lines` (default 60) — last lines kept (recent state)
- [x] `OpenAIRealtimeProvider.disconnect(save_summary=True)` calls `summarize_transcript` and falls back to raw `\n`.join of last 20 lines on `None`
- [x] Logs `summarize.completed` (info), `summarize.failed` (error), `summarize.skipped_no_api_key` / `summarize.empty_choices` / `summarize.empty_content` (warnings)
- [x] All 268 tests green (was 258, +10 new in `test_summarize.py`)

### Tests (Gate 3)

`packages/core/tests/unit/test_summarize.py` (10 tests, AsyncOpenAI mocked at module level):

- `test_returns_summary_text_on_success`
- `test_strips_whitespace_from_summary`
- `test_returns_none_for_empty_transcript` (no API call attempted)
- `test_returns_none_for_missing_api_key` (no API call attempted)
- `test_returns_none_when_api_raises`
- `test_returns_none_when_choices_empty`
- `test_returns_none_when_content_empty_string`
- `test_caps_input_to_max_lines` (only last `DEFAULT_MAX_LINES` sent)
- `test_uses_default_model` (verifies `gpt-4o-mini`)
- `test_includes_system_prompt` (verifies Spanish summarization instruction)

### Docs touched (Gate 4)

- `docs/triage.md` — this entry updated with full audit trail
- ADR — none. Module pick is a runtime concern; the "why summarize" rationale lives in this entry.
- `docs/observability.md` — `summarize.*` events follow the existing namespacing convention; no doc convention change needed.

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: keeping the raw-tail fallback in `disconnect` made the summarizer additive rather than replacing — disconnect always saves something, even if the summarizer breaks tomorrow. Mocking `AsyncOpenAI` at the module level (`monkeypatch.setattr(summarize_module, "AsyncOpenAI", factory)`) is much cleaner than mocking the network — no stub openai server needed. Test runtime jumped from ~0.3s to ~2s after adding summarize tests because openai client import is heavy; acceptable.
- **Follow-up**: regenerate-on-stale (if a session stays connected for hours but transcript moved on) — out of scope for v1, file as a separate triage item if observed in practice.

---

## T1.6 — Per-skill error envelope

**Status**: done (2026-04-18) · **Task**: #91 · **Effort**: ~30 LOC (matched estimate)

**Problem.** When a skill's `handle()` raises, the exception propagates up the
asyncio call chain and likely kills the receive loop. For a blind elderly user,
silence = device broken with no recovery path. Today, any bug in any skill takes
down the agent mid-conversation.

**Why it matters.** Critical pre-ship. Without this, a single skill bug = silent
dead device for the user.

### Validation (Gate 1)

Code path traced:

- `voice/openai_realtime.py:297` calls `on_tool_call` from receive loop
- `coordinator.py:262` (pre-fix) calls `await self._dispatch_tool(name, args)` with no try/except
- `sdk/registry.py:67` calls `await self._skills[skill_name].handle(tool_name, args)` with no try/except
- `voice/openai_realtime.py:347` catches generic `Exception` in `_receive_loop`, logs, then `finally:` calls `on_session_end()` → triggers `_auto_reconnect`

So failure mode = skill exception → session dies → 2s reconnect → no `tool_output` ever sent for that call → user hears silence + reconnect chime.

### Design (Gate 2)

Wrap dispatch in `try/except Exception` (not `BaseException` — preserve `asyncio.CancelledError`). On exception, send structured error JSON as `tool_output` so OpenAI's response loop continues; LLM verbalizes apology naturally on next response round.

Decided NOT to:

- Add a `PlaySound` error chime (requires curated `error.wav`; deferred until persona has one).
- Surface `skill_name` to the error envelope (registry private field; logging `tool` + `args` is sufficient context).

### Definition of Done

- [x] `coord.on_tool_call` wraps `_dispatch_tool` in `try/except Exception`
- [x] Structured error `tool_output` sent via `provider.send_tool_output(call_id, ...)`
- [x] `current_turn.needs_follow_up = True` so model produces audible apology
- [x] `coord.tool_error` log event with `tool`, `args`, `exception_class`, full traceback via `aexception`
- [x] `tool_error` dev event so browser surfaces failures live
- [x] Regression tests prove: (a) no exception propagates, (b) error tool_output sent, (c) needs_follow_up set, (d) dev_event emitted, (e) no audio stream latched, (f) `SkillNotFoundError` (routing failure) handled by same envelope
- [x] All 230 tests green (was 224, +6 new)

### Tests (Gate 3)

`packages/core/tests/unit/test_turn_coordinator.py` → `TestToolErrorEnvelope`:

- `test_skill_exception_does_not_propagate`
- `test_skill_exception_sends_error_tool_output`
- `test_skill_exception_sets_needs_follow_up`
- `test_skill_exception_emits_tool_error_dev_event`
- `test_skill_exception_does_not_latch_audio_stream`
- `test_skill_not_found_error_handled_same_way`

### Docs touched (Gate 4)

- `docs/observability.md` — new "Skill failures" section documenting the `coord.tool_error` and `tool_error` dev event, and the no-session-death guarantee
- `docs/triage.md` — this entry updated with full audit trail

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: Validation-first (tracing the actual failure path) caught that the failure mode was bigger than "skill exception logged" — it was full session death. Worth doing for every Tier 1 item.
- **Follow-up**: when persona has an `error.wav` curated, wire `PlaySound` in this code path so blind users get audio confirmation that something went wrong (separate small triage item).

---

## T1.7 — Audiobook playback speed control

**Status**: done (2026-04-18) · **Effort**: ~140 LOC + 8 tests

**Problem.** Discovered live by Mario: model said "voy a poner el libro **a una velocidad más lenta**" but `audiobook_control` had no speed parameter. Model hallucinated speed adjustment by pause+resume cycles. Nothing actually slowed; user had to keep asking. Same hallucination class as the news/radio "lying about tool execution" bug.

**Why it matters.** For an elderly user, slowing narration is a real accessibility win — and the prior absence wasn't just a missing feature, it was actively misleading: the assistant claimed to do something it couldn't. Tightening the prompt without adding the feature would have made the assistant honestly say "no soporto" but would not have helped the user.

### Validation (Gate 1)

Captured live in browser session 2026-04-18T14:17–14:18:

- Turn at 14:17:40: user "Ponme... Baskerville", model said "y a una velocidad más lenta", fired `play_audiobook` (no speed param exists)
- 14:17:57: user "más lento", model fired `audiobook_control(action=resume)` — wrong tool, no speed change
- 14:18:04: user "más lento", model said "Voy a reproducir el audiolibro más despacio", fired `audiobook_control(pause)` then `(resume)` — pure hallucination

Tool spec confirmed: enum was `[pause, resume, rewind, forward, stop]`. No speed.

### Design (Gate 2)

ffmpeg's `atempo` filter changes tempo without pitch shift; single-filter range 0.5x-2.0x. Three deliberate choices:

1. **Add `set_speed` to `audiobook_control`'s action enum** rather than a separate tool — keeps tool count down (LLM already has 14) and stays semantically grouped with playback control.
2. **Persist via per-skill storage** (`current_speed` key, default 1.0) so speed survives across `play_audiobook` calls — set once, every subsequent play uses it. The user shouldn't have to slow down every new book.
3. **Position math fix**: at non-1.0 speed, `book_advance = wall_elapsed * speed`. Three call sites needed updating — `_build_factory.stream` finally block, `_get_progress`, and the new `_set_speed`. Refactored into `_live_position()` helper to centralize.

`set_speed` while a book is playing returns an `AudioStream` side-effect with the new factory, which the coordinator's existing `_apply_side_effects` handles cleanly: cancels old media, starts new one. Old stream's `finally` block writes its position; new stream's `start_position` was captured at set_speed time. Race is benign — both paths write to the same position key, last write wins, drift is sub-second.

Decided NOT to:

- Discrete speed buckets (0.75/1.0/1.25). Float lets the LLM map "un poquito más lento" to 0.85, "mucho más lento" to 0.7, etc.
- Save speed per book. Speed preference is about the user, not the book.
- Use chained atempo for sub-0.5 or super-2.0. Range matches normal accessibility need.

### Definition of Done

- [x] `AudiobookPlayer.stream(path, start_position, speed=1.0)` accepts speed; adds `-af atempo=N` when speed != 1.0
- [x] `audiobook_control` action enum gains `set_speed`; new `speed` parameter in tool spec
- [x] Speed persisted in skill storage under `CURRENT_SPEED_KEY`; clamped to `[MIN_SPEED, MAX_SPEED] = [0.5, 2.0]`
- [x] All `_build_factory` call sites (`_play`, rewind/forward) load persisted speed and pass to factory
- [x] `_live_position()` helper centralizes position math; multiplies elapsed by current speed
- [x] `_set_speed` handler: persists value, restarts current stream from live position at new tempo (returns AudioStream side-effect); ack-only when nothing playing
- [x] Persona prompt teaches the new action with example mappings ("0.85 para un poco más lento" etc.) AND forbids claiming speed change without calling the tool
- [x] All 298 tests green (was 290, +8 new in `TestSpeedControl`); existing 6 audiobook test assertions updated to include `speed=1.0` kwarg

### Tests (Gate 3)

`packages/skills/audiobooks/tests/test_skill.py` → `TestSpeedControl`:

- `test_set_speed_with_no_value_returns_friendly_message` — defense vs missing arg
- `test_set_speed_persists_when_no_book_playing` — ack path, persisted, no side effect
- `test_set_speed_clamps_below_min` — 0.1 → 0.5
- `test_set_speed_clamps_above_max` — 5.0 → 2.0
- `test_play_uses_persisted_speed` — set_speed once, then play loads 0.75 from storage
- `test_set_speed_during_playback_returns_audio_stream` — restart path with live position injection
- `test_position_math_under_non_unit_speed` — speed=0.5 means 10s wall = 5s book advance
- `test_no_book_playing_live_position_is_none`

Plus stream mock signatures in `test_skill.py` and `test_coordinator_skill_integration.py` updated to accept `speed` kwarg, and 6 existing assertions updated to include `speed=1.0`.

### Docs touched (Gate 4)

- `docs/triage.md` — this entry
- `personas/abuelos/persona.yaml` — AUDIOLIBROS section restructured + new VELOCIDAD section
- `docs/skills/audiobooks.md` — out of scope tonight; the user-facing tool spec lives in the tool description string itself, which is what the LLM reads

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: This bug class — model lying about tool execution because the tool can't do what was claimed — is the _third_ hallucination instance after news (fabricated headlines) and radio (fabricated "what's playing"). Pattern is consistent: weak/missing tool → model fakes via wrong tool → user re-asks. Future skills should explicitly map "things the user might ask for" to tool capabilities and either ship the capability or honestly forbid the claim. The persona prompt addition ("NUNCA digas X sin haber llamado primero a Y") is the right shape for closing the loop, but only meaningful when Y exists.
- **Position math drift**: with the current `bytes_read / BYTES_PER_SECOND` calculation and `-re` throttling, output_seconds == wall_seconds. atempo affects what content is in those seconds, not the rate at which they emerge. The math `book_advance = output_seconds * speed` is correct in this regime.

**Follow-up bug (fixed same day, 2026-04-18)**: when `set_speed` is called and nothing is actively streaming but a `last_id` exists in storage (the natural flow: PTT to interrupt → "más lento"), the original implementation only persisted the value and returned a plain ack. Result: user heard silence, model said "ahora se reproduce a un ritmo más pausado" (misleading), user had to ask again. Fix: `_set_speed` now resumes the last book at the new speed when no stream is live but `last_id` exists. `_play` loads the just-persisted speed from storage so the new tempo applies on the resume. Two new regression tests: `test_set_speed_with_saved_book_resumes_at_new_speed` (paused-then-slowdown path) and `test_set_speed_with_no_saved_book_only_acks` (truly fresh path stays ack-only). 65 audiobooks tests green (was 63).

---

## T1.8 — `huxley-skill-reminders`

**Status**: queued · **Effort**: ~1 week · **Blocked by**: T1.4 Stage 1 (`inject_turn`) + Stage 3 (`background_task`)

**Problem.** Medication + appointment reminders are the first concrete user
benefit of the I/O plane. Without them, "the agent can speak proactively"
is an abstract capability with no shipped consumer.

**Why it matters.** Mario's father specifically flagged reminders as a
daily-use need. Medication reminders are also the canonical
"retry-until-acknowledged" pattern — they validate that the framework's
choice to push retry semantics to the skill (not the primitive) is the
right call.

**Sketch**:

- Persona config declares a reminder list: each has `{id, when, prompt,
kind, retry}` fields; `kind` in `{medication, appointment, generic}`
  drives the urgency tier default
- `setup()`: load reminders from persona data (YAML or SQLite), register a
  `background_task("scheduler", ...)`
- Scheduler loop: pick next due → sleep until due → fire
  `inject_turn(prompt, urgency=...)` → on ack (handle's ack callback fires
  when user PTTs within the default window), mark fired. If no ack within
  configured retry window and `kind == medication`, re-fire at escalating
  urgency
- Tool surface: `add_reminder`, `list_reminders`, `cancel_reminder`
- Prompt context: list upcoming reminders so the LLM can mention them on
  request

**Framework changes needed**: none. Uses existing `inject_turn` +
`background_task` + `Catalog` (for reminder list storage + fuzzy lookup).

**UX validation**: set a medication reminder for 1 minute in the future;
verify: the interrupt earcon plays, book (if playing) is cancelled, model
speaks "Es hora de la pastilla"; grandpa PTTs "ya me la tomé"; reminder
marked acknowledged. Set a second reminder, don't acknowledge; verify re-
fire at escalating urgency.

---

## T1.9 — `huxley-skill-messaging`

**Status**: queued · **Effort**: ~1 week (plus webhook provider integration, not scoped here) · **Blocked by**: T1.4 Stages 1 + 3 (optionally 4 for outbound trigger via `ClientEvent`)

**Problem.** Inbound family messages ("Carlos te mandó un mensaje...") are
the second biggest expected user value after reminders. Outbound messaging
(send a voice memo to Carlos) is the messaging counterpart; depends on a
hardware button or voice command trigger.

**Why it matters.** The `never_say_no` constraint's biggest credibility
test is "user says 'avísale a Carlos que estoy bien' and the agent can
actually do it." Without messaging, the constraint is a verbal promise
without substance.

**Sketch (inbound only for first pass)**:

- Webhook listener (WhatsApp Business API, Telegram, or Twilio — provider
  choice separate concern) runs as a `background_task`
- On inbound message: resolve sender via `Catalog` of known contacts,
  fire `inject_turn` at `CHIME_DEFER` urgency with dedup key
  `msg:{contact_id}` (coalesces multiple pings from same contact)
- Tool surface: `send_message(contact, text)` (outbound), `list_messages`
- Contact list managed by a companion `huxley-skill-contacts` (bundled or
  separate; TBD)

**Outbound**: later pass. Could ride on a hardware "mic button for memo"
via `ClientEvent`, or voice command `"mándale un mensaje a Carlos"`.

**UX validation**: webhook delivers a test inbound message; verify
chime+defer: chime ducks book, message held; grandpa PTTs "¿qué decía?" →
LLM narrates the message.

---

## T1.10 — `huxley-skill-calls`

**Status**: queued · **Effort**: ~2-3 weeks (plus call-provider integration) · **Blocked by**: T1.4 Stage 2 (`InputClaim`) + Stage 4 (`ClientEvent` for panic button) + Stage 3 (`background_task` for SIP listener)

**Problem.** Two musts surfaced during T1.2 design:

1. **Panic button** — grandpa presses a physical button → instant outbound
   call to family
2. **Instant-connect inbound** — family calls from their phone → device
   auto-answers, audio plane swaps, no grandpa action required

Both are life-safety features; grandpa's biggest fear is having an
emergency and not reaching anyone.

**Why it matters.** Directly validates the I/O plane's core claim: skills
extend the runtime without the framework knowing what they do. The calls
skill uses ALL four primitives (`inject_turn` for the "Mario te está
llamando" announcement, `InputClaim` for the duplex audio plane,
`ClientEvent` for the panic button input, `background_task` for the
call-provider listener) and framework code never contains the word
"call."

**Sketch**:

- Voice-provider choice: Twilio Programmable Voice is the likely pick
  (WebRTC capable, proven, Python SDK). Evaluate alternatives during
  detailed design.
- Inbound listener: `background_task` registered against the call
  provider's webhook events
- Inbound flow: on incoming call, check caller against whitelisted
  emergency contacts. If whitelisted → auto-answer (brief
  `inject_turn("{name} te está llamando")` at `CRITICAL` urgency, then
  emit `InputClaim(mic→call, speaker←call)` side effect). If not
  whitelisted → a traditional ring pattern (requires a future
  `LoopingAudioStream` or similar — skip in v1; unknown inbound callers
  go to voicemail instead)
- Outbound flow via panic button: skill subscribes to
  `calls.panic_button` ClientEvent, on fire picks next-priority
  emergency contact, dials out via call provider, emits `InputClaim` when
  peer answers
- Outbound flow via voice command: `call_contact(name)` tool; same
  `InputClaim` path after dialing

**Framework changes**: none beyond the I/O plane primitives. Framework
code has no knowledge of calls, caller IDs, emergency contacts, or SIP.

**Hardware coupling**: one of the ESP32 hardware buttons is dedicated to
`calls.panic_button`. Firmware sends `{"type": "client_event", "event":
"calls.panic_button", "payload": {}}`. Device must be wearable/on-person
for the panic button to be useful (lanyard, clip-on, or similar).

**UX validation**: end-to-end with a real Twilio account, a real family
phone, and the ESP32 device or a stub simulating the panic button event.
Verify:

- Press panic button → grandpa hears "Llamando a Mario"; Mario's phone
  rings; on answer, two-way audio works
- Mario calls from his phone (whitelisted) → grandpa hears "Mario te está
  llamando"; ~1 second later audio plane is swapped; two-way call works
- Mid-call, PTT on the ESP32 button → call ends cleanly, voice agent
  resumes

**Open design work (before implementation starts)**:

- Call provider selection + integration sketch
- Emergency contact list format (lives in persona config? separate
  storage?)
- Whitelist semantics (per-contact auto-answer flag? quiet hours
  override? priority ordering for panic-button dialing sequence?)
- Hardware button specification for ESP32 firmware

---

# Active — Tier 2 (pre-ship hardening)

## T2.1 — Storage WAL + daily snapshot

**Status**: done (2026-04-18) · **Task**: #92 · **Effort**: ~120 LOC + 12 tests (estimated 50 LOC; backup module + tests grew it)

**Problem.** Audiobook positions live in a single SQLite file with no WAL, no
backup, no migration framework. The user's only state is "where I was in this
book." Losing it is invisible until next interaction. For a system whose UX is
"resume my book," losing the position is a silent UX disaster.

### Validation (Gate 1)

`Storage.init()` (pre-fix) opened the DB without `PRAGMA journal_mode=WAL` and
without `synchronous=NORMAL`, leaving the default rollback-journal mode that
risks corruption on crash. No backup mechanism existed in code or in
`scripts/launchd/`. No `schema_meta` table — schema changes would be silent
breakage.

### Design (Gate 2 — light, mechanical item)

Three independent changes:

1. **WAL mode** — `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` at
   connection time. WAL prevents partial-write corruption and allows
   concurrent readers; NORMAL synchronous is safe under WAL with the small
   risk of losing the last few transactions on power loss (acceptable for
   this data class).
2. **Schema versioning** — `schema_meta` table + `_init_schema_version`
   startup helper. Records current version on fresh DB; logs drift on
   mismatch (no migration runner yet — that lands when first migration is
   actually needed).
3. **Daily snapshot helper** (`huxley.storage.backup`) — uses SQLite's
   online backup API (`sqlite3.Connection.backup`), which is safe to run
   while the main process holds the DB open. Idempotent: today's snapshot
   exists → no-op (but still prunes). Snapshots beyond `retention_days`
   are deleted by parsing the YYYY-MM-DD suffix from the filename. Wired
   into `Application.start()` so the launchd auto-start path gets backups
   for free without a separate cron.

Decided NOT to:

- Use a launchd cron — Application.start() runs at every login (already
  via launchd KeepAlive), so backups happen on the same trigger.
  Eliminates a second moving part.
- Build a migration runner now — adds surface for future schema changes
  without a current customer. Schema version tracking is enough scaffolding.

### Definition of Done

- [x] `Storage.init()` enables WAL + synchronous=NORMAL
- [x] `schema_meta` table created; `SCHEMA_VERSION = 1` recorded on fresh DB
- [x] Drift logged via `storage_schema_version_mismatch` on version
      mismatch (proceeds without crashing)
- [x] `huxley.storage.backup.ensure_daily_snapshot` created with retention
      pruning, called from `Application.start()` before `storage.init()`
- [x] `Storage.db_path` exposed as read-only property so backup module
      doesn't need internal access
- [x] All 242 tests green (was 230, +12 new in `test_storage.py`
      `TestWalAndSchemaVersion` and the new `test_storage_backup.py`)

### Tests (Gate 3)

`packages/core/tests/unit/test_storage.py` → `TestWalAndSchemaVersion`:

- `test_journal_mode_is_wal`
- `test_schema_version_recorded_on_fresh_db`
- `test_schema_version_idempotent_on_reinit`
- `test_schema_version_mismatch_logged_not_crashed`

`packages/core/tests/unit/test_storage_backup.py` → `TestEnsureDailySnapshot`:

- `test_returns_none_when_source_db_missing`
- `test_creates_snapshot_with_dated_filename`
- `test_default_backup_dir_is_sibling_backups_folder`
- `test_custom_backup_dir`
- `test_idempotent_returns_none_when_today_snapshot_exists`
- `test_prunes_snapshots_older_than_retention`
- `test_prune_runs_even_when_no_new_snapshot_created`
- `test_prune_ignores_files_that_dont_match_naming`

### Docs touched (Gate 4)

- `docs/triage.md` — this entry updated with full audit trail
- ADR — none. WAL + schema versioning + backup mechanism are runtime
  concerns, not architectural decisions affecting framework consumers.
  Entry serves as the audit trail.
- `docs/observability.md` — `storage_snapshot_created` event is
  self-documenting; no convention change needed.

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: SQLite's online backup API (stdlib, not aiosqlite-specific)
  is the right tool for live DB snapshots. Test pruning with explicit date
  injection (`today=` kwarg) — much cleaner than freezegun. The first cut
  of the test had an off-by-one in the expected survivors list (cutoff
  semantics: `<` not `<=`); regression test caught it on first run.

---

## T2.2 — Cost observability + bug-canary ceiling

**Status**: done (2026-04-18) · **Task**: #93 · **Effort**: ~270 LOC + 16 tests (estimated 80 LOC; price table + threshold tracking grew it)

**Problem.** Tool retry loop bug → silent bill spike. No tracking of cumulative
cost per session or per day. No threshold logging. No kill switch.

**Why it matters.** Bug detection more than spend control. A 10x daily bill =
something is wrong, not "user used a lot today." Catching that early saves
investigation time.

### Validation (Gate 1)

`response.done` events from OpenAI carry a `usage` payload (`input_token_details`,
`output_token_details`, `cached_tokens`). The receive loop in
`voice/openai_realtime.py` previously fired the void `on_response_done` callback
and discarded the usage data entirely. No tokens were tracked, no cost computed,
no thresholds checked.

### Design (Gate 2 — light)

New `huxley.cost` module with three pieces:

1. **`PRICES` table + `compute_cost_usd(model, usage)`** — pricing for the two
   shipped models (mini + full Realtime), verified 2026-04-18 from
   openai.com/api/pricing. Cached token portions billed at cached rate.
   Unknown models fall back to mini pricing with a warning log so a future
   model rollout doesn't silently zero the bill.

2. **`CostThresholds` dataclass** with three tiers:
   - `warn_usd = 0.50` (1x a normal day; informational)
   - `bug_canary_usd = 5.00` (10x normal; "investigate")
   - `kill_switch_usd = 20.00` (100x normal; "stop now")

3. **`CostTracker`** persists daily totals to `Storage` under
   `cost:YYYY-MM-DD:cents` (cents as int avoid float drift). Threshold
   warnings idempotent within a day via `cost:YYYY-MM-DD:warned`. Optional
   `on_kill_switch` callback fires once when ceiling crossed.

Wired into `OpenAIRealtimeProvider.__init__(cost_tracker=...)`. Receive loop
extracts `data["response"]["usage"]` on `response.done`, calls
`tracker.record(usage)` — wrapped in try/except so cost-tracking failure can
never affect the session. Application wires the kill switch to
`provider.disconnect(save_summary=True)` so context survives the forced halt.

Decided NOT to:

- Surface in `dev_event` (the original sketch mentioned it). Browser dev
  client doesn't display cost today; can be added later by reading the
  `cost:*` settings keys directly.
- Make thresholds persona-configurable. Default thresholds work for the
  AbuelOS daily-driver pattern; per-persona override can be added when a
  persona legitimately needs higher ceilings.
- Break out per-session cost (only daily). Daily is the load-bearing
  granularity for "is something wrong?"

### Definition of Done

- [x] `huxley.cost.compute_cost_usd(model, usage)` returns USD for a `response.done.usage` payload, with cached-token handling
- [x] `PRICES` table includes both shipped models; unknown model falls back with warning
- [x] `CostTracker.record(usage)` persists daily total cents, logs `cost.response_done` info event with model + per-response cost + day total
- [x] Threshold warnings (`warn` / `bug_canary` / `kill_switch`) fire at most once per day each, persisted to Storage
- [x] Kill-switch callback invoked exactly once per day when ceiling crossed; wired in `Application` to `provider.disconnect(save_summary=True)`
- [x] OpenAI provider's receive loop extracts `usage` from `response.done` and calls `tracker.record(usage)` with try/except so cost-tracking failure can never affect the session
- [x] All 258 tests green (was 242, +16 new in `test_cost.py`)

### Tests (Gate 3)

`packages/core/tests/unit/test_cost.py`:

`TestComputeCostUsd` (8 tests):

- mini pricing for simple usage
- full-model pricing for same usage shape
- cached tokens billed at cached rate
- cache-without-breakdown fallback (assumes text)
- unknown model falls back to mini pricing
- missing token-detail subkeys treated as zero
- missing top-level keys treated as zero
- known-models table includes both shipped models

`TestCostTrackerAccumulates` (4 tests):

- records cents to Storage
- accumulates across multiple records
- zero-cost record is no-op (no Storage write)
- per-day keys are independent (clock injection)

`TestCostTrackerThresholds` (4 tests):

- warn fires below kill-switch threshold; kill-switch does NOT fire
- kill-switch callback invoked when ceiling crossed
- threshold warning idempotent within a day (kill switch only fires once)
- thresholds reset on new day (kill switch can fire again next day)

### Docs touched (Gate 4)

- `docs/triage.md` — this entry updated with full audit trail
- ADR — none. Cost tracking is a runtime concern, not architectural.
  Pricing table cross-references the existing ADR `2026-04-18 — Default
model is gpt-4o-mini-realtime-preview` for the source-of-truth on prices.
- `docs/observability.md` — `cost.response_done`, `cost.threshold_crossed`,
  `cost.kill_switch_triggered` events follow the existing namespacing
  convention; no doc convention change needed.
- `README.md` / `CLAUDE.md` — no user-facing setup or contributor command
  changed.

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: Gate 1 trace through the receive loop revealed the discard
  cleanly — knowing `data.get("response", {}).get("usage")` was the right
  extraction point came from reading the existing event-parsing code, not
  guessing. Cents-as-int avoided a class of float-formatting bugs the
  first sketch had. The clock-injection pattern (`clock=Callable[[], datetime]`)
  is much cleaner than freezegun for time-based tests; reuse it.
- **Follow-up**: surface daily-total in browser dev client (small UI
  addition; new triage item if it becomes painful not to see). Per-persona
  threshold config when the first persona needs different defaults.

---

## T2.3 — Integration smoke tests against real OpenAI Realtime

**Status**: done — Layer 1 (2026-04-18) · **Task**: #95 · **Effort**: ~330 LOC + 16 tests + 1 fixture · **Unblocks**: T1.3

**Problem.** Voice-first project, text-first test surface. Audio regressions
slip through. The single biggest risk on the active list — T1.3 coordinator
refactor — has no automated test net. Manual browser smoke is the only thing
catching subtle regressions today.

**Why it matters.** T1.3 is "refactor without behavior change". The way to
verify no behavior change is tests that exercise the full receive-loop +
coordinator + skill + side-effect path. Without these, T1.3 is a leap of faith.

### Validation (Gate 1)

`OpenAIRealtimeProvider._receive_loop` (pre-refactor) inlined the full
per-event branching: parse, audio decode, tool args parse, transcript
append, error code matching, response.done usage extraction, cost
tracking. Every behavior was reachable only by spinning up a real
WebSocket — no Python-level test could exercise the dispatch path.
Refactoring the coordinator (T1.3) without an automated regression net
in this code path was indeed a leap of faith.

### Design (Gate 2)

Two-layer plan from the original triage entry. **Layer 2 (live test
against real OpenAI) deferred** for tonight's autonomous work — running
it would burn the user's API tokens overnight without supervision. Layer
1 (recorded-fixture replay) shipped.

**Refactor first**: extracted `_handle_server_event(self, data)` from
`_receive_loop`. The receive loop now does only `json.loads + handle`;
all per-event branching is in the new method, directly testable.
Behavior-preserving — all existing tests stayed green after the
extraction.

**Layer 1 implementation**:

- `tests/integration/replay.py` — `RecordedSession` dataclass +
  `load_session(path)` JSONL parser (skips `//` comments + blanks for
  human-authoring) + `replay(provider, session)` async helper that feeds
  events through `_handle_server_event`.
- `tests/integration/fixtures/audiobook_play_basic.jsonl` — first
  hand-authored fixture: user transcript → assistant ack → 2 audio
  chunks → audio.done → tool call → response.done with usage payload.
  Replace with recorded real-API capture when the recorder lands.
- `tests/integration/test_session_replay.py` — three end-to-end scenario
  tests verifying full callback sequencing + transcript accumulation +
  cost tracker invocation + loader robustness.
- `tests/unit/test_openai_realtime_event_handler.py` — 13 direct unit
  tests of `_handle_server_event` covering every branch: audio delta
  base64 decode, function call args parse + malformed-JSON fallback,
  user/assistant transcript routing, error codes (cancel-not-active /
  buffer-empty / model-not-found / other), audio.done, response.done
  with-and-without usage, cost-tracker exception isolation, unknown
  event no-op.

Decided NOT to (this round):

- Build a full `FixtureReplayProvider` implementing the entire
  `VoiceProvider` protocol. The current `_handle_server_event` direct
  call covers the same ground for receive-loop logic, with much less
  surface to maintain. Promote to a full provider impl when send-side
  testing (commit/cancel/etc.) needs the same harness.
- Live API test. Layer 2. Unblocked from T1.3 since Layer 1 covers the
  refactor's regression need; Layer 2 is for OpenAI API drift detection
  and can ship later as a nightly job.
- Build a session recorder. The replay loader accepts JSONL of the
  same shape OpenAI sends, so a future recorder is just a JSONL writer
  in a wrapped provider.

### Definition of Done

- [x] `_handle_server_event` extracted from `_receive_loop`,
      behavior-preserving (existing 173 core tests still green after extraction)
- [x] Direct unit tests cover every branch of `_handle_server_event`
      (13 tests in `test_openai_realtime_event_handler.py`)
- [x] JSONL fixture loader + replay helper in `tests/integration/replay.py`
- [x] One representative fixture (`audiobook_play_basic.jsonl`)
- [x] Three scenario tests using fixture replay verify full chain (callbacks
      sequence, transcript accumulation, cost tracking, loader robustness)
- [x] All 284 tests green (was 268, +16 new)
- [ ] Layer 2 (live API smoke gated behind `HUXLEY_INTEGRATION=1`) —
      deferred to follow-up triage item; not a blocker for T1.3

### Tests (Gate 3)

`packages/core/tests/unit/test_openai_realtime_event_handler.py`:

- `TestHandleAudioDelta` — base64 decode + dispatch
- `TestHandleFunctionCall` — args parse + malformed-JSON fallback
- `TestHandleTranscript` — assistant + user role routing
- `TestHandleError` — silent-cancel + commit-empty + other-codes paths
- `TestHandleResponseDone` — audio.done + response.done with/without
  usage + cost-tracker exception isolation
- `TestHandleUnknownEvents` — unknown event types are silent no-ops

`packages/core/tests/integration/test_session_replay.py`:

- `TestAudiobookPlayBasic` — full callback sequence + cost recording
- `TestLoaderHandlesCommentsAndBlankLines` — JSONL parser robustness

### Docs touched (Gate 4)

- `docs/triage.md` — this entry updated; T1.3 status will note the
  unblock when it's picked up.
- ADR — none. The `_handle_server_event` extraction is a refactor with
  the rationale captured here.
- `docs/observability.md` — no new event names introduced.
- `README.md` — no user-facing change.

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: Extracting a previously-inline method to make it
  directly testable is one of the highest-leverage refactors available
  — paid for itself within the same gate (13 tests + 3 integration in
  ~330 LOC). The JSONL+comments fixture format is much more
  human-readable than I expected; can imagine future scenarios being
  authored directly without a recorder. Layer 2 (live API) deferred is
  honest given the autonomous-overnight constraint; tracking as a new
  triage item below.
- **Follow-up triage item to file** (T2.4 candidate): live API smoke
  test gated behind `HUXLEY_INTEGRATION=1`, runs nightly. Needs
  user-supervised first run to verify token cost.

---

# Deferred (with revisit trigger)

## D1 — `never_say_no` enforcement (layered defense)

**Was**: Tier 2 candidate · **Task**: #94 · **Revisit when**: first real user session shows actual model refusals

**Reason for deferral.** The layered fix (tool-side discipline + refusal pattern
detection + LLM-as-judge tie-breaker) is real work but not blocking. Today's
defense is the prompt; on-the-record observation will tell us how often it leaks.
If first-user sessions show frequent refusals, this jumps to Tier 1.

## D2 — Integration smoke tests against real OpenAI

**Status**: pulled forward to active Tier 2 as **T2.3** (2026-04-18). Coordinator refactor (T1.3) is the riskiest item on the list and refactor without behavior change is exactly where the test net matters. See T2.3 in Active Tier 2 above.

## D3 — Tier 3 polish (6 items)

| Task | Title                                                 | Effort   | Status              |
| ---- | ----------------------------------------------------- | -------- | ------------------- |
| #96  | Add `prompt_context()` to Skill Protocol with default | 30 min   | **done 2026-04-18** |
| #97  | Auto-namespace tool names (`<skill>.<tool>`)          | ~50 LOC  | queued              |
| #98  | Strip remaining `AbuelOS` hardcoded refs              | 30 min   | **done 2026-04-18** |
| #99  | Allow second WS client as monitor in dev              | ~4 hours | queued              |
| #101 | systemd unit + install script for Linux deployment    | ~30 min  | queued              |
| #102 | `Dockerfile` + `docker-compose.yml`                   | ~2 hours | deferred            |

**#96 — done**. Added `prompt_context(self) -> str` (returns `""` by default) to the `Skill` Protocol in `huxley_sdk/types.py`. Skills that subclass `Skill` explicitly inherit the empty default — mypy / IDE autocomplete now recognize the method, and a typo (`prompt_contxt`) gets flagged instead of silently doing nothing. Existing duck-typed skills (audiobooks, news, radio, system) are unchanged; the `SkillRegistry.get_prompt_context` keeps its `getattr` fallback for backward compatibility, and that fallback can be removed once those four skills explicitly subclass `Skill`. 4 new tests in `TestPromptContext` cover: skill without override → empty contribution, skill with override → text returned, multiple skills → joined with blank line, empty contribution → filtered.

**#98 — done**. Removed the hardcoded `"abuelos"` fallback from `persona.py`'s persona resolution; replaced with autodiscovery (uses the only persona under `./personas/`, raises clear `PersonaError` otherwise). Deleted dead `wakeword_model_path = "models/hey_abuela.tflite"` + `wakeword_threshold` fields from `Settings` (no code reads them). Updated `__main__.py` error message and module docstrings. The two remaining hits to `grep -ri abuel packages/core/src/` are honest contextualization comments in `cost.py` and `constraints/__init__.py` (calibration notes, not behavior). 6 new tests in `TestResolvePersonaPath` cover CLI > env > autodiscovery > clear-error precedence.

**Revisit when**: any session has spare cycles, OR the first community skill is
about to land (#97 becomes urgent), OR ESP32 hardware arrives (#99 becomes
urgent), OR Pi deployment is about to start (#101 becomes urgent).

**#101 — systemd unit + install script for Linux deployment**

`scripts/launchd/` ships the macOS auto-start path. Pi deployment needs the
Linux mirror: a `scripts/systemd/huxley.service` unit + `install.sh` that copies
to `/etc/systemd/system/`, runs `daemon-reload`, and enables the service.
Roadmap (`docs/roadmap.md`) already mentions the gap. Same shape as launchd:
auto-start at boot, restart on crash with backoff, picks up `.env` from
`packages/core/`, runs as the deploying user. Daily snapshot is already
cross-platform (T2.1 fires from `Application.start()` — no cron needed).
**Ship before the first Pi deployment.**

**#102 — `Dockerfile` + `docker-compose.yml` (deferred)**

Deferred per the cost/benefit analysis 2026-04-18: Docker is genuinely useful
for the framework's "anyone can install Huxley" story, but premature for
AbuelOS today (one user, one operator, no upgrade-pain incidents yet). Ship
when (a) the first non-Mario user shows up wanting to try Huxley, OR (b) a
dependency upgrade burns 30+ minutes of Pi-vs-Mac debugging, OR (c) a
contributor explicitly asks for it. Container would: pin Python 3.13 + uv +
ffmpeg, expose port 8765, bind-mount `personas/<name>/data/` for the
audiobook library + DB, bind-mount `.env` for the API key. Multi-arch build
(amd64 + arm64) via buildx. Don't deprecate the bare-metal path when this
ships — keep both supported.

**Revisit trigger for #102**: any of the three conditions above. Otherwise
quarterly check that the bare-metal path still works on the active Mac/Pi
deployments.

## D4 — `VoiceProvider` abstraction redesign

**Reason for deferral.** Current shape is leaked from OpenAI Realtime semantics
and won't fit a non-OpenAI provider cleanly. Saving it now is cargo cult — the
abstraction will be redesigned in light of the actual second provider's shape.

**Revisit when**: a credible second voice provider (local Whisper+Llama+Piper, or
a different cloud Realtime API) is actually being integrated.

---

# Historical reviews

The sections below are issue analyses from earlier critic reviews. Several were
shipped during the refactor stages (1–4); status of each is "presumed done unless
re-flagged" — check `git log` for the actual fix commit before re-acting.

---

## 2026-04-17 — second critic review

Root cause analysis and solution proposals for every issue raised in the second
independent code review. Issues are ordered: blockers first, real concerns second,
nitpicks last.

---

### B1 — `pause` and `stop` do not cancel playback

**Status**: presumed done (CancelMedia SideEffect shipped in stage 3 — commit
`20407f0`). Verify in `packages/sdk/src/huxley_sdk/types.py`.

**Symptom.** `audiobook_control(action="pause")` and `audiobook_control(action="stop")`
return a `ToolResult` with no side effect. The coordinator sees a plain result,
sets `needs_follow_up=True` so the model can narrate the confirmation, and continues.
The `current_media_task` (the live ffmpeg stream) is never touched. The user hears
"Okay, pausing" while the book keeps playing.

**Root cause.** The `SideEffect` vocabulary only has one kind: `AudioStream` (start
something). There is no "stop the running stream" kind. The coordinator's
`on_tool_call` path has two branches: got an `AudioStream` → latch it for the
terminal barrier; got nothing → set `needs_follow_up`. There is no third branch for
"cancel whatever is currently running."

The `current_media_task` is owned exclusively by the coordinator. Skills have no
handle to it, and `ToolResult` has no field that maps to it. The design gap is
architectural: the `SideEffect` abstraction was designed for "start" actions only.

**Proposed solution.** Add a `CancelMedia(SideEffect)` subclass to `huxley_sdk/types.py`:

```python
@dataclass(frozen=True, slots=True)
class CancelMedia(SideEffect):
    """Side effect: cancel the currently running media task, if any."""
    kind: ClassVar[str] = "cancel_media"
```

In `coordinator.py` `on_tool_call`, add a third branch:

```python
elif isinstance(result.side_effect, CancelMedia):
    await self._stop_current_media_task()
    self.current_turn.needs_follow_up = True  # model narrates confirmation
```

The cancellation happens immediately when the tool call is processed — not deferred
to the terminal barrier — so the stream stops before the model's narration plays.
`needs_follow_up=True` is correct: the model receives the tool output
(`{"paused": true}`) and generates a spoken confirmation. The blind user always
gets an audio acknowledgement.

In the audiobooks skill `_control` method, `pause` and `stop` return:

```python
case "pause":
    return ToolResult(
        output=json.dumps({"paused": True}),
        side_effect=CancelMedia(),
    )
case "stop":
    return ToolResult(
        output=json.dumps({"stopped": True}),
        side_effect=CancelMedia(),
    )
```

**Test gap.** `TestPauseRequestsFollowUp` only checks that a follow-up response is
requested. It must also assert `coordinator.current_media_task is None` after the
tool call. A new test should simulate a running task and verify it is cancelled
before the follow-up response fires.

**Effort.** Small. Three files touched: `types.py` (new class), `coordinator.py`
(new branch in `on_tool_call`), `skill.py` (two return statements). The existing
test is updated; one new test added.

---

### B2 — Log file handle has no `atexit` registration

**Status**: presumed done unless re-flagged. Verify in `packages/core/src/huxley/logging.py`.

**Symptom.** If the process is killed (SIGKILL, kernel OOM, hard power-off), any
lines buffered in `_file_handle` but not yet written to disk are lost. Since the
debugging workflow is logging-first — a remote collaborator reads the log to
diagnose what happened — losing the last lines on a crash is exactly when the log
matters most.

**Root cause.** `setup_logging()` opens `_file_handle` as a local variable, which
is then captured by the `_TeeProcessor` instance. Python's garbage collector will
close it at shutdown in the normal case. But `atexit` handlers do not run on
SIGKILL, and they do run on normal interpreter exit, `sys.exit()`, and unhandled
exceptions — so the gap is specifically hard crashes. The `flush()` call after
every line (line 146) ensures no _line-level_ data loss during normal operation,
but the last partial internal-buffer write is at risk on crash.

**Proposed solution.** Store the handle at module level and register an `atexit`
handler:

```python
_log_file_handle: IO[str] | None = None

def setup_logging(...):
    global _log_file_handle
    ...
    _log_file_handle = log_file.open("w", encoding="utf-8")
    import atexit
    atexit.register(_close_log_file)
    ...

def _close_log_file() -> None:
    global _log_file_handle
    if _log_file_handle is not None:
        try:
            _log_file_handle.flush()
            _log_file_handle.close()
        except OSError:
            pass
        _log_file_handle = None
```

This also prevents handle leaks if `setup_logging` is called more than once
(e.g., in tests): the previous handle is closed before the new one is opened.

**Effort.** Minimal. One file, a handful of lines.

---

### C1 — `openai_api_key` defaults to `""` instead of `None`

**Status**: presumed done unless re-flagged. Verify in `packages/core/src/huxley/config.py`.

**Symptom.** A developer who sets `HUXLEY_OPENAI_API_KEY=` (explicitly empty) in
their shell gets past the `__main__.py` guard (which checks `if not config.openai_api_key`)
and sees an obscure 401 error from OpenAI rather than a clean startup failure with
a useful message.

**Root cause.** `config.py` line 37: `openai_api_key: str = ""`. The empty string
is falsy in Python, so the guard catches it, but the type annotation says `str` and
masks the intent. A cleaner type is `str | None = None`, which makes "not configured"
unambiguous at the type level.

**Proposed solution.** Straightforward:

```python
openai_api_key: str | None = None
```

Update `__main__.py` guard:

```python
if not config.openai_api_key:
    logger.error("HUXLEY_OPENAI_API_KEY is required — set it in .env")
    raise SystemExit(1)
```

And update the type annotation in `OpenAIRealtimeProvider.__init__` to handle
`str | None` (assert or raise before the connect call).

**Effort.** Trivial. Two files.

---

### C2 — Concurrent tool calls within one response serialize

**Status**: doc-only acknowledgement intended; verify the comment is in `coordinator.py` `on_tool_call`.

**Symptom.** If the model issues two tool calls in one response (OpenAI Realtime
sends two `response.function_call_arguments.done` events in sequence), the second
waits for the first `_dispatch_tool` to complete. Skills that do I/O (ffprobe
subprocess, DB write) add that latency to the second tool's execution serially.

**Root cause.** `on_tool_call` in `coordinator.py` awaits `_dispatch_tool` inline.
The receive loop processes one WebSocket message at a time; the coroutine for the
second tool call cannot start until `on_tool_call` for the first one returns.

**Discussion.** This is not strictly a bug for Huxley's current use case. The OpenAI
Realtime API sends tool call results one at a time, and the model can't generate the
next response until all tool outputs for the current response are submitted. So
serializing tool dispatch is observable only when two tools are in the same response
and the first tool is slow. AbuelOS's tools are fast (time query: DB read; audiobook:
DB read + ffprobe), so the serialization is invisible in practice.

**Proposed solution.** Document it explicitly rather than fix it now. The correct
future fix — if it ever matters — is to collect all tool calls for a response into
a list, then `asyncio.gather` their dispatch and send all outputs at once. This
requires buffering until `response.done`, which is a non-trivial coordinator
change. Flag on the roadmap under "multi-tool parallelism." Add a comment in
`on_tool_call`:

```python
# Tool calls within a response are dispatched serially. If a future persona
# needs parallel dispatch (multiple I/O-heavy tools in one response), collect
# them and asyncio.gather before sending outputs. See docs/triage.md C2.
```

**Effort.** Zero to document; medium to fix (coordinator restructuring).

---

### C3 — Audiobook position under-counts what the user actually heard

**Status**: doc-only acknowledgement intended; verify the comment is in audiobooks `_build_factory`.

**Symptom.** When the user interrupts playback, the saved resume position can be
ahead of what they actually heard. Under event loop pressure (heavy tool calls,
slow WebSocket), chunks pile up in asyncio's read buffer from ffmpeg before
`send_audio` is awaited. Those bytes are counted in `bytes_read` but not yet
delivered to the speaker. The user resumes and hears a small jump forward.

**Root cause.** `skill.py` `_build_factory` line 413: `bytes_read += len(chunk)` is
incremented when the chunk is read from ffmpeg stdout, not when it is sent to the
client. The `yield chunk` returns immediately; the `_consume_audio_stream` loop then
calls `await self._send_audio(chunk)`. The bytes have been consumed from ffmpeg
before they have been delivered to the client.

**Discussion.** The discrepancy is bounded by the asyncio event loop cycle time and
the WebSocket send buffer. In practice, on a local network, this is well under one
second — a minor nuisance, not a correctness failure. The truly correct solution
would track `bytes_sent` at the consumer side (`_consume_audio_stream`), but this
requires threading a position callback back into the generator or restructuring the
`AudioStream` API. That is disproportionate complexity for the problem size.

**Proposed solution.** Accept the current behavior, document the known limitation
and its bound, and add a comment in `_build_factory`:

```python
# `bytes_read` counts bytes from ffmpeg stdout, not bytes delivered to the
# client. Under event loop pressure, position can be ahead of what was heard
# by at most one asyncio event loop cycle (~1-5ms of audio). Acceptable for
# a resume-position UX; not acceptable for frame-accurate seeking.
```

If this ever becomes a real problem (e.g., for a future seek-by-timestamp feature),
the fix is to pass a `position_callback: Callable[[float], Awaitable[None]]` into
the `AudioStream` dataclass and call it from `_consume_audio_stream` after each
`send_audio`. That keeps the position tracking at the consumer (coordinator) where
the delivered-bytes count is exact.

**Effort.** Zero to document; medium if the callback approach is taken later.

---

### C4 — `SkillStorage` protocol missing the `default` parameter

**Status**: presumed done unless re-flagged. Verify in `packages/sdk/src/huxley_sdk/types.py` + `packages/core/src/huxley/storage/skill.py`.

**Symptom.** Skills cannot use `await ctx.storage.get_setting("key", default="x")`
even though the underlying `Storage.get_setting` supports it. Skill authors who
look at `NamespacedSkillStorage` and try the default kwarg get a `TypeError`.

**Root cause.** The `SkillStorage` protocol in `types.py` line 129 declares
`get_setting(self, key: str) -> str | None` without a `default` parameter.
`NamespacedSkillStorage.get_setting` passes through to `Storage.get_setting` but
also omits the `default`. The protocol and the adapter are both incomplete.

**Proposed solution.** Add `default` to both:

In `types.py`:

```python
async def get_setting(self, key: str, default: str | None = None) -> str | None: ...
```

In `storage/skill.py`:

```python
async def get_setting(self, key: str, default: str | None = None) -> str | None:
    return await self._storage.get_setting(f"{self._ns}:{key}", default)
```

**Effort.** Trivial. Two files, one line each.

---

### C5 — `FakeSkill` ignores `tool_name`; all tools return the same result

**Status**: presumed done unless re-flagged. Verify in `packages/sdk/src/huxley_sdk/testing.py`.

**Symptom.** `FakeSkill(name="x", result=ToolResult(...))` returns the same
`ToolResult` no matter which tool is called. Tests that register a multi-tool skill
via `FakeSkill` cannot assert different outcomes per tool, and a test that
accidentally calls the wrong tool gets a success result with no signal.

**Root cause.** `testing.py` `FakeSkill.handle()` ignores its `tool_name` argument
and always returns `self._result`. The class was written for single-tool test
scenarios and never extended.

**Proposed solution.** Make `result` accept either a single result or a per-tool
dict. Backward-compatible:

```python
@dataclass
class FakeSkill:
    name: str
    result: ToolResult | dict[str, ToolResult]
    ...
    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if isinstance(self.result, dict):
            if tool_name not in self.result:
                raise ValueError(
                    f"FakeSkill '{self.name}': no result registered for tool '{tool_name}'. "
                    f"Registered: {list(self.result.keys())}"
                )
            return self.result[tool_name]
        return self.result
```

All existing tests continue to work (they pass a single `ToolResult`). New tests
can pass `result={"play": ToolResult(...), "pause": ToolResult(...)}`.

**Effort.** Trivial. One file.

---

### C6 — `flush()` on every log line causes syscall pressure at DEBUG

**Status**: presumed done unless re-flagged. Verify in `packages/core/src/huxley/logging.py`.

**Symptom.** At `HUXLEY_LOG_LEVEL=DEBUG`, every audio delta frame and every chunk
forwarded to `send_audio` generates a structlog event. Each event calls
`self._fh.flush()` in `_TeeProcessor.__call__`. On a Raspberry Pi or any
resource-constrained device, this adds a syscall to every audio frame's hot path
and will cause noticeable audio jitter.

**Root cause.** `logging.py` line 146: `self._fh.flush()` is unconditional. At
INFO+ this is benign (few events per second). At DEBUG on an audio path this is
high-frequency (hundreds of events per second during playback).

**Proposed solution.** Flush only at WARNING+ to guarantee those lines reach disk
promptly; rely on the `atexit` flush (see B2) for lower-severity events:

```python
if method in ("warning", "error", "critical"):
    self._fh.flush()
```

This keeps crash-adjacent log lines durable without adding a syscall to every
audio frame. The `atexit` handler (once B2 is fixed) ensures INFO/DEBUG lines are
not lost on normal exit.

**Effort.** Trivial. One line change. Depends on B2 being fixed first.

---

### N1 — `assert` as runtime guards in skill code

**Status**: open / unknown. Verify with `grep -rn "assert " packages/skills/`.

**Symptom.** `packages/skills/audiobooks/src/huxley_skill_audiobooks/skill.py`
contains 11 guards of the form `assert self._storage is not None`. Python strips
`assert` statements when running with `python -O` (optimized mode), so these guards
disappear in production builds.

**Root cause.** Defensive checks written during development. The intent is correct
— guard against calling `handle()` before `setup()` — but `assert` is the wrong
mechanism.

**Proposed solution.** Replace with explicit checks:

```python
if self._storage is None:
    raise RuntimeError(f"{self.name}: handle() called before setup()")
```

Or — more Pythonically — use a private property that raises on unset access:

```python
@property
def _storage_required(self) -> NamespacedSkillStorage:
    if self._storage is None:
        raise RuntimeError(f"{self.name}: not set up")
    return self._storage
```

Then call `self._storage_required.get_setting(...)` instead.

**Effort.** Small. One file, 11 sites. Mechanical but not risky.

---

### N2 — Spanish UI strings hardcoded in framework code

**Status**: open by design — Spanish-everywhere is acceptable today. Revisit when a non-Spanish persona ships.

**Symptom.** `coordinator.py` lines 144, 163, 196, 396 contain Spanish status
strings (`"Escuchando… (suelta para enviar)"`, `"Muy corto — mantén el botón
mientras hablas"`, `"Listo — mantén el botón para responder"`). These are sent to
the web client for display. They are in the `TurnCoordinator` — framework code that
is supposed to be persona-agnostic.

**Root cause.** The strings were written when AbuelOS was the only persona and
extracted from app logic without being lifted out of the framework layer.

**Discussion.** There are two design choices here:

_Option A (minimal):_ Move the strings to `app.py`, which already knows about the
persona. Pass them to `TurnCoordinator` at construction as a `status_strings: dict`
argument with English defaults. `persona.yaml` grows a `ui_strings:` section. No
SDK change required.

_Option B (proper):_ Add a `ui_strings` mapping to `PersonaSpec` with keys like
`listening`, `too_short`, `ready`. The coordinator takes a `StatusStrings` dataclass
at construction. This is the right abstraction but requires a bit more YAML surface
and a new type.

Option A is the right call now — Huxley only has one persona and the change is
mechanical. Option B is worth revisiting when a second persona exists that needs
different strings.

**Effort.** Small. `coordinator.py`, `app.py`, `persona.yaml`, `persona.py`.

---

### N3 — `Turn.response_ids` field is never populated

**Status**: presumed done unless re-flagged. Verify in `packages/core/src/huxley/turn/coordinator.py`.

**Symptom.** `coordinator.py` line 71: `response_ids: list[str] = field(default_factory=list)`.
No code anywhere appends to this list. It is initialized empty and stays empty for
the lifetime of every `Turn`.

**Root cause.** Leftover from an earlier design where response IDs were tracked for
cancellation correlation. The cancellation mechanism changed and this field was
never wired up or removed.

**Proposed solution.** Delete the field. Verify with a grep that nothing references
it (the field is on a private dataclass; nothing outside `coordinator.py` could
reasonably use it).

**Effort.** Trivial.

---

### N4 — `import copy` inside a hot `__call__` path

**Status**: presumed done unless re-flagged. Verify in `packages/core/src/huxley/logging.py`.

**Symptom.** `logging.py` line 141: `import copy` is inside `_TeeProcessor.__call__`.
Python caches imports after the first call so there is no measurable overhead, but
it is non-idiomatic and confusing to readers who expect to find imports at the top
of the module.

**Root cause.** The import was added late (possibly after `from __future__ import
annotations` was in place) and placed where it was needed without being hoisted.

**Proposed solution.** Move `import copy` to the top of `logging.py`.

**Effort.** Trivial.

---

### N5 — `CLAUDE.md` references `server/` paths that no longer exist

**Status**: done. Current `CLAUDE.md` references `packages/core/`.

**Symptom.** `CLAUDE.md` "Definition of Done" section (line ~106) references
`cd server && uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest
tests/unit/` and "Config defaults assume the server runs from `server/`". The
`server/` directory does not exist — it was renamed to `packages/core/` in Stage 1
of the refactor.

**Root cause.** The definition-of-done section was not updated when the package
layout changed. The rest of CLAUDE.md (the Commands section at the top) is correct;
the DoD section at the bottom was missed.

**Proposed solution.** Update the stale DoD section to match the Commands section.
The correct commands are already documented at the top of CLAUDE.md.

**Effort.** Trivial.

---

### Priority order (as written 2026-04-17)

| ID  | What                                | Effort  | When        |
| --- | ----------------------------------- | ------- | ----------- |
| B1  | pause/stop don't cancel playback    | Small   | Next commit |
| B2  | Log handle no atexit                | Minimal | Next commit |
| C4  | SkillStorage missing `default`      | Trivial | Next commit |
| C5  | FakeSkill ignores tool name         | Trivial | Next commit |
| N3  | `response_ids` dead field           | Trivial | Next commit |
| N4  | `import copy` in hot path           | Trivial | Next commit |
| N5  | CLAUDE.md stale paths               | Trivial | Next commit |
| C1  | `openai_api_key` defaults to `""`   | Trivial | Next commit |
| C6  | flush() pressure at DEBUG           | Trivial | After B2    |
| N1  | `assert` as runtime guards          | Small   | Next sprint |
| N2  | Spanish strings in framework code   | Small   | Next sprint |
| C2  | Concurrent tool dispatch serializes | Doc now | Next sprint |
| C3  | Position off-by-one on cancellation | Doc now | Future      |
