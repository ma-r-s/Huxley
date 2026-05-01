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
4. `uv run ruff check server/` + `uv run mypy server/sdk/src server/runtime/src` +
   per-package `pytest` all green.
5. For audio/protocol changes: manual browser smoke per
   [`docs/verifying.md`](./verifying.md). Audio regressions don't show up in
   `pytest`.

## Gate 4 — Document

For every item, walk this checklist explicitly. The act of checking is the work
— not just "I think nothing changed."

- [ ] Affected `docs/*.md` (architecture, protocol, `skills/*`, `server/personas/*`,
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

- `server/skills/audiobooks/src/huxley_skill_audiobooks/skill.py` — `_fuzzy_score` (SequenceMatcher), `_resolve_book` (fuzzy iter over `_catalog`), `prompt_context()` (manual dump of `_catalog[:50]` as Spanish lines)
- `server/skills/radio/src/huxley_skill_radio/skill.py` — `_station_choices()` (manual prompt dump), case-insensitive station name iter
- `server/skills/news/src/huxley_skill_news/skill.py` — `dict[str, tuple[float, dict]]` cache layer with manual TTL/key composition
- Future, per `docs/roadmap.md` v2: contacts (messaging), music library, recipes — all need fuzzy search + prompt awareness

The repeated pattern across 3 shipped skills + 3 planned skills is the validation.

### Design (Gate 2 — locked 2026-04-18 after user sign-off on four decisions)

**Decision 1 — Scope: full Catalog primitive, not a thin helper module.**
The framework is committing to "personal content + LLM dispatch" as the headline
differentiator; building a real primitive matches that thesis. A thin helper
(`huxley_sdk.search.fuzzy_match`) would do half the work and force a rewrite when
the full primitive lands.

**Decision 2 — Persistence: in-memory rebuilt at `setup()`, with FTS5 upgrade
path baked into the API.** All current and near-future Abuelo skills have small
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

- `server/sdk/src/huxley_sdk/catalog.py` — public `Catalog` class + `Hit` dataclass + `_fold` accent-stripper
- `server/sdk/src/huxley_sdk/types.py` — extend `SkillContext` with `catalog(name) -> Catalog` factory method (returns a fresh in-memory Catalog per name; framework doesn't share state across skills)
- `server/sdk/src/huxley_sdk/__init__.py` — export `Catalog`, `Hit`
- `server/sdk/tests/test_catalog.py` — primitive tests (insert, search, fold, prompt format, tool def)

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

- [ ] **Regression parity**: `test_catalog_matches_legacy_audiobooks_resolution` — load full Abuelo-style audiobook fixture, run 10 queries the old `_resolve_book` handled correctly + 3 misspelling cases. Top-1 must match. _This is the "drop-in refactor" proof; without it, "65 tests pass" means nothing because those tests mock the fuzzy layer._
- [ ] **Misspelling tolerance**: query "naufrago" (no accent, missing g) → top hit "Relato de un náufrago"
- [ ] **Stopword noise**: query "el" against 5 "El X" titles → no result scores above a low threshold
- [ ] **Determinism**: same fixture + same query → byte-identical top-10 across 100 runs
- [ ] **Prompt parity**: `as_prompt_lines(50)` on the audiobook fixture produces byte-identical output to the current `prompt_context()` (so system prompt hash is preserved across the refactor)

### Tests (Gate 3 — to be filled after impl)

To be added in `server/sdk/tests/test_catalog.py`:

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

- The original `as_search_tool` cut from v1 (after critic + Mario's scoping) was the right call. Building it would have added ~50 LOC of code + tests with zero current callers; Abuelo's max-100-item catalogs always fit in prompt context.
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

- **Stage 1c.0 — SpeakingState authority doc** ✅ **done**
  (`ce46787`, 2026-04-18). Written into `docs/architecture.md` under
  "Turn coordinator internals → Authority contract." Defines:
  `SpeakingState` authoritative for "client speaker indicator";
  `FocusManager` authoritative for "who holds the claim"; coordinator
  owns the bridge. Transition table documents every DIALOG/CONTENT
  FocusState change and the corresponding SpeakingState write.
  Resolves Open Question 2 below.

- **Stage 1c.1 — Wire FocusManager into Application lifecycle** ✅
  **done** (`d1719c2`, 2026-04-18). `Application` constructs
  `FocusManager.with_default_channels()`, `start()` in `run()` (after
  storage init, before skill setup), `stop()` in `_shutdown` (after
  coordinator interrupt, before skill teardown). Coordinator accepts
  an optional `focus_manager` parameter (reference held but unused
  until 1c.2). 253 tests still green; no behavior change.

- **Stage 1c.2 — Route CONTENT through FocusManager** ✅ **done**
  (`373d8da`, 2026-04-18). `_start_content_stream` creates
  `Activity(channel=CONTENT, content_type=NONMIXABLE,
observer=ContentStreamObserver(...))` + `fm.acquire(activity)` +
  `fm.wait_drained()`. `_stop_content_stream` →
  `fm.release(CONTENT, interface_name)` + `fm.wait_drained()`.
  `wait_drained` is a new FocusManager method wrapping
  `self._mailbox.join()` — blocks until every queued event has been
  fully processed, including observer notifications. Preserves
  `interrupt()`'s strict step order (pump dies before `force_release`
  runs, per 1a). `current_media_task` still works as a back-compat
  sync accessor via a coordinator-local cache of the observer ref.
  `focus_manager` is now a required kwarg on `TurnCoordinator`; 3 test
  files got `FocusManager` fixtures. 253 tests still green.

- **Stage 1c.3 — `SkillContext.inject_turn(prompt)` MVP** ✅ **done**
  (`229cdfb`, 2026-04-18). Added `inject_turn: Callable[[str],
Awaitable[None]]` field on `SkillContext` (no-op default for test
  contexts; real callable wired from `TurnCoordinator.inject_turn` in
  `app._build_skill_context`). Coordinator creates
  `Activity(channel=DIALOG, content_type=NONMIXABLE, observer=
DialogObserver(...))`, `fm.acquire()` + `fm.wait_drained()` (content
  stream gets BACKGROUND/MUST_PAUSE → pump cancels), `send_audio_clear`
  to flush preempted chunks, then `send_conversation_message` +
  `request_response`. Normal `on_audio_delta`/`on_audio_done`/
  `on_response_done` flow drives the turn to completion; terminal
  `_apply_side_effects` releases DIALOG. `interrupt()` and
  `on_session_disconnected` also release DIALOG so FM stacks stay
  consistent across barriers. MVP scope: skipped silently when a user
  or synthetic turn is already active (skill retries later; queue is
  Stage 1d work). 5 new unit tests cover idle-inject, skip-when-
  active, content-preemption, natural release, interrupt-release. 258
  core tests green. **Ships `inject_turn` as a working framework
  surface — unblocks reminder skill MVP (T1.8).**

**Stage 1b — Server-side duck PCM envelope** ✅ **done** (`061996a`,
2026-04-18). `ContentStreamObserver` grew linear gain
envelope state (`_gain`, `_ramp_target`, `_ramp_start_time`,
`_ramp_start_gain`) + `_apply_gain` helper that per-sample
interpolates PCM16 across the ramp window (avoids click at chunk
boundaries). `BACKGROUND/MAY_DUCK` now ramps to 0.3 over 100ms and
keeps the pump running — classic AVS duck, not the old fallback to
pause. `FOREGROUND` after a duck ramps back up to 1.0.
`BACKGROUND/MUST_PAUSE` is unchanged (still hard cancel — spoken-
word content shouldn't overlap with injected narration). Fast path
for the common case `gain == 1.0 && no ramp active` returns chunks
byte-identical to input (no allocation, no math). 4 new unit tests
cover: fast path, duck attenuation (max sample ≈ amplitude × 0.3),
duck-then-resume rearms to 1.0, MUST_PAUSE still cancels. No new
deps (`struct` + `time` stdlib only). Scaffolding for future
MIXABLE streams; today's content (audiobooks/news/radio) is all
NONMIXABLE so MAY_DUCK doesn't fire through production code paths
— but the primitive is unit-tested in isolation and ready to fire
the first time a MIXABLE stream lands.

**Stage 1d.1 — inject_turn queue + dedup_key** ✅ **done** (`a45f72c`,
2026-04-18). The "request was silently dropped because a
turn was active" hole from 1c.3 is closed: `inject_turn` now queues
when busy and drains in `_apply_side_effects` when a turn ends
**without** a pending content stream. Content always wins over a
queued reminder (preempting a freshly-started book to fire a stale
reminder is bad UX); the queue waits for the next quiet moment.
`dedup_key` (opaque string): if the queued list has a same-key
entry, the new request replaces it (last-writer-wins); if a same-key
inject is currently firing, the new one is silently dropped.
`SkillContext.inject_turn` signature relaxed to `Callable[...,
Awaitable[None]]` so skills can pass `dedup_key=...`. 7 new unit
tests in `TestInjectTurnQueue`. The first user-visible consumer
(timers skill) doesn't pass `dedup_key` today — its IDs are unique
per-timer — but a future medication-reminder skill will use
`dedup_key="med_<schedule_id>_<date>"` to handle re-fires from the
scheduler.

**Stage 1d.2 — TTL + outcome handle** ~~shelved (2026-04-21)~~.
Originally speced as `InjectedTurnHandle.wait_outcome()` resolving
to a `TurnOutcome` enum (`DELIVERED | EXPIRED | CANCELLED | PREEMPTED`).

**Design decision (2026-04-21)**: `.wait_outcome()` is the wrong
abstraction for the reminder ack problem. The right pattern — used
by every major AVS-derived skill (Alexa Reminders, Google Assistant
Routines) — is LLM-driven acknowledgment: the reminder skill exposes
an `acknowledge_reminder(id)` tool and trusts the LLM to call it when
the user says "ya me la tomé". The framework does not need to know
whether a turn was "acknowledged"; that's application-layer semantics.
`InjectedTurnHandle.cancel()` remains useful and is already
in-spec via Stage 1d.1. `expires_after` TTL is a reasonable future
add but has no current consumer. **Do not build `.wait_outcome()` —
it would create a framework API that leaks skill-level semantics
upward through the abstraction boundary.**

**Stage 1d.3 — `InjectPriority` enum (two-tier)** ✅ **done** (`bc5a4e2`,
2026-04-19). Added `InjectPriority = NORMAL | PREEMPT` to
the SDK. `inject_turn(prompt, *, priority=NORMAL)` signature
extended. `NORMAL` preserves the 1d.1 "content wins at turn-end"
policy; `PREEMPT` drains the queue even when the draining turn
spawned a content stream (the stream request is dropped). Neither
tier barges into a user mid-speech — priority only decides
content-vs-queue at turn-end, not user-right-to-finish. Closes the
"10-hour audiobook strands medication reminder" failure mode
flagged by the post-Stage-3 critic (issue B / PQ-1). 5 new tests:
preempt-over-content, normal-still-waits, preempt-ahead-of-earlier-
normal, preempt-from-idle-same-as-normal, preempt-doesn't-barge-into-
user-turn.

**Stage 1f — Stale FACTORY owner after inject_turn preemption**
✅ **done** (`061996a`, 2026-04-18, shipped alongside 1b).
`coordinator.inject_turn` now calls
`self._speaking_state.force_release()` after `fm.acquire` +
`wait_drained` (pump is dead by this point) and before sending the
prompt. Clears the stale FACTORY owner left by the preempted pump's
CancelledError path. Effect: the client sees `model_speaking=True`
→ `False` transition at preemption, then `True` again when the
injected turn's first audio delta arrives — a real transition
cue instead of one unbroken span. Low-cost fix (one await),
idempotent (`force_release` on owner=None is a no-op, so
inject_turn from idle doesn't break). Verified via existing
TestInjectTurn tests — they still pass.

**Stage 1e — `docs/observability.md` update** ✅ **done** (`7ba76bb`,
2026-04-18). Documented every framework log event that
shipped during Stage 1 but wasn't in the observability canon:

- Added `focus.*` to the namespace table.
- Rewrote the example narrative to use current event names
  (`coord.audio_stream_started/ended` instead of the deleted
  `coord.factory_started/ended`; `session.rx.tool_call` instead
  of `session.rx.function_call`; `has_audio_stream=true` instead
  of `has_factory=true`).
- Updated the dream-interaction example at the top with current
  vocabulary.
- Added a "Focus events — what they tell you" section: table of
  every `focus.*` event with fields, plus a worked example showing
  the `inject_turn` preempting an audiobook through the FM.
- Added an "Inject_turn queue events" section explaining
  `coord.inject_turn_queued/dequeued/deduped/dropped` and how to
  diagnose "the reminder didn't speak" symptoms.

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

### Post-Stage-3 critic findings (2026-04-19, belated Gate-2 for Stages 1d+3)

Spawned a fresh critic against the full Stage-1-through-Stage-3
substrate before Stage 2. Full report in session transcript; summary
here. Three tiers of finding:

**🔴 Ship-fix** — ✅ **shipped in `c2fa2b1`**:

- **(#1) dedup_key leak on non-standard turn-end paths.** `_current_injected_dedup_key` was only cleared on the natural `_apply_side_effects` return path. Interrupt and `on_session_disconnected` left it set — a same-key inject_turn after either barrier would be silently dropped as "already firing." Fix: defensive `_current_injected_dedup_key = None` alongside `current_turn = None` in all three paths.
- **(#6/#7) Duck envelope was end-to-end unreachable.** Stage 1b shipped the PCM duck envelope but no content stream produced it: (a) `ContentType` lived in `focus/vocabulary.py` (framework-internal), so skills couldn't mark an AudioStream as MIXABLE; (b) FM patience defaulted to 0 for CONTENT Activities, so FocusManager sent MUST_STOP instead of MAY_DUCK. Fix: moved `ContentType` to SDK, added `AudioStream.content_type` field (default NONMIXABLE), coordinator reads it and sets `patience=5min` for MIXABLE. New end-to-end test composes mixable→dialog preempt and asserts the duck envelope actually attenuates samples.
- **(#4) Supervisor tests burned 5–7s each via real `asyncio.sleep`.** Fix: injectable `sleep` parameter on `TaskSupervisor` (default `asyncio.sleep`); tests inject a near-zero stub. Suite now runs in ~0.3s.

**🟠 PQ — product questions that needed Mario's call**:

- **PQ-1 — audiobook-strands-medication.** During a 10-hour audiobook the Stage-1d.1 queue policy ("content always wins at turn-end") means a medication reminder queued mid-book never fires. Mario's call: ship two-tier `InjectPriority` (NORMAL default preserves content; PREEMPT drains over content). ✅ **shipped in `bc5a4e2`** (Stage 1d.3).
- **PQ-2 — timers fire_prompt hard-coded for Abuelo.** Default was Spanish / warm-friend register embedded in the skill; non-Spanish personas inherit broken narration. Mario's call: persona-config override (`timers.fire_prompt` in persona.yaml with `{message}` substitution; empty/missing-placeholder falls back to default with a warning log). ✅ **shipped in `c6bd19e`**.
- **PQ-3 — Stage 3 "done" hid persistence gap.** Original Stage 3 entry marked itself done without acknowledging tasks die on restart — a real gap for medication reminders. Mario's call: relabel Stage 3 → Stage 3a (in-memory), file Stage 3b (persistence) as queued. ✅ **shipped in `c6bd19e`**. Stage 3c (PermanentFailure elapsed_s semantics) also filed.

**🟡 Pre-Stage-2 cleanup** — queued tiny items, see next section.

### Pre-Stage-2 cleanup (queued, tiny items from post-Stage-3 critic)

Small items the critic flagged that aren't blocking but should land
before Stage 2 stacks more on:

4. **Tighten `SkillContext.inject_turn` / `background_task` typing to
   Protocols.** ✅ **done** (`a286205`, 2026-04-19). Added
   `InjectTurn` and `BackgroundTask` Protocol classes to
   `huxley_sdk/types.py`; `SkillContext` fields now carry those types
   instead of `Callable[..., ...]`. Protocol `__call__` methods spell
   out keyword arguments by name, so a skill calling
   `inject_turn(prompt, dedup_ky=...)` (typo) now fails mypy instead
   of becoming silent `**kwargs`. `prompt` made positional-only with
   `/` so the test-fixture `_noop_inject_turn`'s `_prompt` name
   doesn't collide with the Protocol's `prompt`. 286 core + 60 SDK +
   17 timers tests green; no skill-side changes required (structural
   typing — existing callables already satisfy the shape).

5. **Extract `_post_turn_sequence()` from `_apply_side_effects`.**
   ✅ **done** (`2a3eb2c`, 2026-04-19). Extracted as
   `_dispatch_post_turn(streams, turn_id)` with a docstring listing
   the three branches (PREEMPT-over-content / content-wins /
   quiet-moment). `_apply_side_effects` is now focused on turn
   teardown; the drain policy lives where Stage 1d.2 (TTL expiry)
   and Stage 2 (`InputClaim` cleanup) can add branches without
   bloating the cleanup method. Pure extraction, no behavior change;
   286 core tests still green without modification. Opted against
   the discriminated-union-of-intent the critic suggested — three
   branches today, YAGNI; revisit if the branch count doubles.

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

### Stage 2 — `InputClaim` + `MicRouter` wiring ✅ **done** (2026-04-19)

**Status**: shipped end-to-end on 2026-04-19. The MVP call loop runs against the real OpenAI Realtime API. **Effort (actual)**: ~1 day vs the 5-7 day re-scoped estimate (and 2-week pre-pivot estimate). The post-pivot scope reduction held — no YieldPolicy, no Arbitrator, MicRouter pre-extracted in T1.3, focus management substrate from Stage 1 carried the matrix without new logic.

**Progress** (every commit pinned by hash):

- ✅ **Pre-work spike** (`00c17e9`) — characterized OpenAI Realtime suspend/resume behavior against the real API (<$1). Findings in `docs/research/realtime-suspend.md`. Critical: "pause" ≠ "stop reading" — the model keeps generating server-side and buffers hundreds of KB of audio without an explicit `response.cancel`.
- ✅ **Commit 1** (`c4c90af`) — SDK surface. `InputClaim` SideEffect, `ClaimHandle`, `ClaimEndReason(NATURAL|USER_PTT|PREEMPTED|ERROR)`, `StartInputClaim` Protocol, `SkillContext.start_input_claim` field with no-op default. 12 new SDK tests.
- ✅ **Commit 2** (`07a3eca`) — Provider `suspend()/resume()` contract + OpenAI Realtime impl + `StubVoiceProvider` parity + 11 tests. Suspend: cancel + clear + set flag. Resume: clear flag, zero wire traffic. Receive loop drops content events while suspended; lifecycle events pass through.
- ✅ **Commit 3a** (`3597d8b`) — `MicRouter.claim()` enforces at-most-one-claim invariant via `MicAlreadyClaimedError`. Closes the critic-flagged race where a direct-entry claim could capture another claim's `_previous` handler.
- ✅ **Commit 3b** (`6d5450a`) — `ClaimObserver` on the CONTENT channel (per critic — not DIALOG, to avoid same-channel stacking conflicts with PREEMPT injects). `coordinator.start_input_claim` direct-entry method with proper `ClaimHandle` (`cancel()` + `wait_end()`). All four `ClaimEndReason` exit paths wired: NATURAL via handle.cancel, USER_PTT via interrupt, PREEMPTED via FocusManager NONE delivery default, ERROR via mic-router-busy or handler exception. 14 tests including the matrix-defining `test_preempt_inject_ends_claim_with_preempted`.
- ✅ **Commit 3c** (`38b695e`) — tool-dispatched path. `ToolResult.side_effect = InputClaim(...)` latches on `Turn.pending_input_claim`; terminal barrier starts via `_dispatch_post_turn` (claim wins over content stream; PREEMPT inject still wins over both). Pre-barrier-PREEMPT drop fires `on_claim_end(PREEMPTED)` so skills see the lifecycle even when the claim never started. 5 new tests.
- ✅ **Commit 4** (`5a26448`) — AudioServer routes. `GET /call/ring` (HTTP, header auth, returns 200/401/409/503) + `WS /call?secret=` (path-based routing, query-param auth). Both go through `process_request` on the existing port — no new dep, AudioServer remains "all connections from outside the server." 9 tests against real `serve()`.
- ✅ **Commit 5** (`89f62c2`) — `huxley-skill-calls` package. `answer_call` / `reject_call` / `end_call` tools; `on_ring(params) -> bool` and `on_caller_connected(ws)` framework hooks; PCM relay via `InputClaim.on_mic_frame` (grandpa→caller) and `speaker_source` async iterator backed by `asyncio.Queue` (caller→grandpa); persona-overridable Spanish/Abuelo-toned prompts for ring + four end reasons; secret precedence `HUXLEY_CALLS_SECRET` env > persona config. 26 unit tests with a `FakeWS` stand-in.
- ✅ **Commit 6** (`14204d1`) — Application wiring. New `_wire_call_hooks_if_any()` runs after `setup_all`, duck-types skills for `(secret, on_ring, on_caller_connected)` shape, calls new `AudioServer.set_call_hooks(...)` setter. Framework stays skill-agnostic — duck-type instead of importing calls. `start_input_claim` wired into `SkillContext` from `coordinator.start_input_claim`. Abuelo persona.yaml gets `calls:` block.

**Live verification on the running server** (2026-04-19, post-commit-6):

- Boot: `calls.setup_complete has_secret=True` → `app.call_hooks_wired skill=calls` → `audio_server_listening calls_enabled=True` → `huxley_ready ... tools=[..., answer_call, reject_call, end_call]`.
- Ring smoke (curl): no-secret → 401, wrong-secret → 401, valid-secret → 200 with `server.rx.ring`, `calls.ring_accepted from_name=Mario`, `coord.inject_turn` firing the announcement to OpenAI.

**Final test count**: 338 core + 72 SDK + 30 timers + 26 calls + 60 audiobooks = **526 unit tests green** across the workspace.

**The conversation interactions matrix** (the original "Alexa-style focus management") is now fully populated in code via FocusManager composition — every cell falls out of the substrate without dedicated if/else logic. Rows marked ⚙️ were updated after Stage 2b moved InputClaim CONTENT → COMMS (2026-04-24):

| Active             | Incoming                            | Outcome                                                                                                                                       | Wired by                    |
| ------------------ | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| Audiobook (NMX)    | User PTT                            | Book pauses; user speaks                                                                                                                      | Stage 1a                    |
| Audiobook          | inject_turn(NORMAL)                 | Queues; fires at quiet turn-end                                                                                                               | Stage 1d.1                  |
| Audiobook          | inject_turn(BLOCK_BEHIND_COMMS)     | Book parks BACKGROUND (30min patience); alert narrates; book auto-resumes at saved position                                                   | Stage 2b ⚙️ + Stage 5       |
| Audiobook          | inject_turn(PREEMPT)                | Book parks BACKGROUND (30min patience); reminder narrates; book auto-resumes at saved position                                                | Stage 1d.3 + Stage 2b ⚙️    |
| Music (MIX)        | inject_turn                         | Music ducks to 0.3 gain; voice overlays                                                                                                       | Stage 1b                    |
| User speaking      | inject_turn (any)                   | Queues — never barges user                                                                                                                    | Stage 1d.1                  |
| Call (COMMS)       | User PTT                            | Claim ends USER_PTT; "Llamada finalizada"                                                                                                     | Stage 2 commit 3b + 2b ⚙️   |
| Call               | inject_turn(NORMAL)                 | Queues behind call; fires at claim-end via next synthetic turn                                                                                | Stage 2b ⚙️ (post-ship fix) |
| Call               | inject_turn(BLOCK_BEHIND_COMMS)     | Queues behind call; fires at claim-end                                                                                                        | Stage 2b ⚙️ + Stage 5       |
| Call               | inject_turn(PREEMPT)                | Claim ends PREEMPTED; alert narrates                                                                                                          | Stage 2 commit 3b           |
| Call               | Concurrent InputClaim               | Second claim raises `ClaimBusyError`; first claim unaffected                                                                                  | Stage 2b ⚙️                 |
| Call               | Audiobook tool call                 | Book acquires CONTENT on priority 300; call on COMMS (150) wins; book parks BACKGROUND with patience; book resumes on call-end                | Stage 2b ⚙️                 |
| Any content        | Patience expires while backgrounded | Observer's `on_patience_expired` fires BEFORE terminal NONE — skill narrates eviction (audiobooks says "pausé tu libro por la llamada larga") | Stage 2b ⚙️                 |
| Tool latches claim | PREEMPT queued                      | Claim dropped pre-start; on_claim_end(PREEMPTED)                                                                                              | Stage 2 commit 3c           |

**Lessons captured**:

- (a) The Stage 1 focus-management pivot paid off here. Modeling claim as a CONTENT-channel NONMIXABLE Activity meant zero new preemption logic — the matrix above is a documentation artifact, not code. Same substrate handles audiobooks, calls, and any future skill that needs "this thing is playing-ish."
- (b) The pre-work spike (~$1 in API spend) saved days. The "stop reading isn't pause" finding would have surfaced as a billing leak + correctness bug a week into commit 5; instead it shaped commit 2's contract from day one.
- (c) Duck-typed skill discovery for framework hooks (`hasattr` over the registry) keeps `huxley` core from importing skill packages. Same pattern reusable when Stage 4 ClientEvent lands — skills register subscriptions, framework iterates without knowing names.

**Known follow-ups** (filed below as separate entries, not blocking Stage 2 done):

- T1.4 Stage 2.1 — expose `ClaimHandle` for side-effect-dispatched claims so the calls skill can cancel cleanly when the caller WS closes (currently waits for grandpa PTT or PREEMPT inject).
- T1.4 Stage 2.2 — voicemail / missed-call inject_turn when ring fires but answer never dispatches (timeout + reject paths).
- T1.4 Stage 2.3 — per-caller secrets in calls skill (currently a single shared secret; 20-line change before the second family member joins).
- T1.4 Stage 4 — proper `ClientEvent` wire protocol; migrate calls skill from HTTP-POST + path-routing-WS to the unified ClientEvent surface.

**Original effort estimate (pre-pivot)**: ~2 weeks. **Depends on (now resolved)**: T1.3 (`MicRouter`), Stage 1 (`YieldPolicy` enum — dropped by pivot).

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

### Stage 2.1 — `ctx.cancel_active_claim` for side-effect-dispatched claims ✅ **done** (`5ea2c8c`, 2026-04-19)

**Effort (actual)**: ~45 min vs the 1–2h estimate. Path (1) shipped per the recommendation: smaller diff, no `ToolResult` contract change, motivating consumer's bug closed.

**Shipped**:

- `coordinator.cancel_active_claim(*, reason=NATURAL) -> bool` — looks up `_claim_obs`, sets the end reason, drives `_end_input_claim` → FM release → observer's `_end` chain. Idempotent (returns False if no active claim or already ending). 4 new coordinator tests.
- `CancelActiveClaim` Protocol on the SDK + new `SkillContext.cancel_active_claim` field defaulting to a no-op for test fixtures (returns False so skill tests can branch cleanly).
- Wired in `Application._build_skill_context` from `coordinator.cancel_active_claim`.
- Calls skill's `_on_caller_disconnected` replaced its TODO workaround with a real `await self._ctx.cancel_active_claim(reason=ClaimEndReason.NATURAL)`. Now caller-WS-close drives the full end chain → `on_claim_end(NATURAL)` → "Mario colgó" inject narration. 2 new calls-skill tests covering the active and no-claim cases.

**Path (2) deferred** (return `ClaimHandle` from side-effect dispatch, extending `ToolResult` with an `on_side_effect_started` callback) — leave for when a second skill needs full lifecycle handles. Per CLAUDE.md "rule of three" instinct: extract the abstraction at the third consumer, not the second.

342 core (+4) + 28 calls (+2) tests green. docs/skills/calls.md scope-limits list updated to remove the gap.

### Stage 2.2 / 2.3 — ❌ ripped per scope correction (2026-04-19)

Both were filed against the now-abandoned "custom PWA as caller" model. Under the single-user PWA framing (see `docs/clients.md`), the PWA isn't a caller — it's the Huxley user's own interface. Inter-user communication belongs in skills bridging to third-party apps. These entries are retained only as historical context for why they existed; no work to be done against them.

- Stage 2.2 (voicemail / missed-call inject) — obsolete because the calls skill being torn out is being replaced by `huxley-skill-comms-telegram`, which inherits Telegram's built-in missed-call notification mechanics at no cost.
- Stage 2.3 (per-caller secrets) — obsolete because Telegram identities replace the shared-secret model entirely. Per-caller routing is Telegram's problem, not ours.

### Stage 2b — Complete the COMMS channel: InputClaim migration + pause/resume contract + concurrent-claim policy + patience notification

**Status**: done (2026-04-24, co-landed with Stage 5 + T2.7) · **Effort**: L — shipped across SDK + core + audiobooks + telegram + tests + docs. 358 core (+6), 61 audiobooks, 42 telegram, 30 timers, 72 SDK all green; ruff + mypy --strict clean. · **Motivation**: finish the four-channel focus model honestly.

**Problem.** `InputClaim` registers on `Channel.CONTENT` (`coordinator.start_input_claim:1073`). Audiobooks also register on CONTENT. Call-during-audiobook evicts the book. `Channel.COMMS` is defined with priority 150 and full FM arbitration support but has zero call sites. Fixing the channel assignment alone is a one-line change but the 2026-04-23 critic pass revealed that the UX it promises — "book pauses, call runs, book resumes where it was" — does not actually work against today's `ContentStreamObserver` + `AudioStream.factory` contract, because the factory closure captures `start_position` at build time and re-calling it after a pump cancellation restarts at the original segment start, not where it was cancelled. Plus two related correctness gaps (concurrent claims, silent patience expiry).

**Why it matters.** Finishing the four-channel model honestly. Shipping only the rename leaves the system documentably-fixed but experientially-broken — the exact "docs lie about the system" pattern that triggered the 2026-04-23 review. If we ship it, it has to actually work.

### Validation (Gate 1)

Verified in code during 2026-04-23 critic pass:

- `coordinator.py:1073` — `InputClaim` Activity hard-coded to `Channel.CONTENT`
- `coordinator.py:1012-1014` — each `start_input_claim` mints a fresh `interface_name = f"claim:{id}"` from a monotonic counter; FM same-interface replacement will NOT fire across two concurrent claims, they'll stack
- `coordinator.py:1071` — `self._claim_obs = observer` unconditionally overwrites any prior claim reference
- `observers.py:155-184` — on `BACKGROUND/MUST_PAUSE`, `_cancel_pump` closes the async generator; on return to `FOREGROUND`, `_spawn_pump_if_idle` calls `self._stream.factory()` again, which creates a fresh iterator via `stream()`, which restarts from the **originally captured** `start_position`. No live-position lookup. Pump-resume does not resume content.
- `audiobooks/skill.py:605-700` — `_build_factory` closure captures `start_position` at build time; `stream()` uses that captured value on every invocation
- `focus/manager.py:291-303` — `_handle_patience_expired` emits only `NONE/MUST_STOP` + a log line; no user-visible signal path

### Design (Gate 2 — locked 2026-04-23 post-critic)

Four concerns, one commit-pair:

**(A) Channel migration (the original 1-line change):**

```python
# coordinator.start_input_claim
activity = Activity(
    channel=Channel.COMMS,                        # was CONTENT
    interface_name="claim:active",                # was f"claim:{id}"; see (C)
    content_type=ContentType.NONMIXABLE,
    observer=observer,
    patience=timedelta(0),                        # COMMS claims don't stack
)
```

**(B) Real pause/resume contract:**

Audiobook factory closures change from "capture start position at build time" to "read live position at each invocation." Two-part change:

1. `audiobooks/skill.py _build_factory` — closure takes `book_id` + `path` (+ `speed`) and reads `start_position` from `self._get_live_position(book_id)` at the top of `stream()`, NOT from a captured parameter. First invocation reads the position the user tool-called with (stored into the skill's live-position state on tool call); later invocations (post-pump-cancel-then-respawn) read the last-known position from the `finally`-saved storage row.
2. `ContentStreamObserver` gets explicit docs (and a contract test) that `_spawn_pump_if_idle` after a prior `_cancel_pump` is a supported transition. Existing implementation already handles this correctly at the observer level — the fix is purely skill-side.

Audiobook `AudioStream` acquires with `patience=timedelta(minutes=30)` (see (D) for the value rationale).

**(C) Concurrent-claim policy: reject the second claim.**

Decision: Abuelo's user model is one-call-at-a-time; general Huxley power users can adopt call-waiting later if a real need surfaces. Keep it simple:

- Change `interface_name` to the literal `"claim:active"` (single-slot on COMMS) so same-interface-replace is well-defined if reached.
- `coordinator.start_input_claim` raises `ClaimBusyError` if `self._claim_obs is not None` (before constructing the new Activity). Skill (telegram) catches; Telegram skill sends a `DISCARDED_CALL` / `BUSY` to the peer. A clean protocol-level rejection.
- Existing `_claim_obs` overwrite (line 1071) moves behind the busy check.

**(D) Patience expiry is a user-visible event, not a silent one.**

New extension point on `ChannelObserver`:

```python
class ChannelObserver(Protocol):
    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None: ...
    async def on_patience_expired(self) -> None: ...   # NEW, default no-op
```

`FocusManager._handle_patience_expired` calls `observer.on_patience_expired()` BEFORE the terminal `NONE/MUST_STOP` notification. `ContentStreamObserver` implements it by invoking a new optional callback on `AudioStream`:

```python
@dataclass(frozen=True, slots=True)
class AudioStream(SideEffect):
    # ... existing fields ...
    on_patience_expired: Callable[[], Awaitable[None]] | None = None
```

Audiobooks skill wires it to `ctx.inject_turn("Pausé tu libro porque la llamada fue larga. Dime 'sigue con el libro' cuando quieras retomar.", dedup_key="book_patience_lost")`. User hears the event. No silent state mutation.

Patience value: `timedelta(minutes=30)`. Rationale: covers virtually every realistic call length for Abuelo; avoids the 2-hour pathological case the critic flagged; bounded so an abandoned book doesn't linger indefinitely. Call ends within 30min → auto-resume; longer → user narrated on expiry and can say "sigue con el libro" any time afterward.

### Critic notes (Gate 2 — ran 2026-04-23)

Full critic report captured in session transcript; findings and resolutions:

- 🔴 **#1 pause/resume doesn't work in code** → **resolved in (B)**: factory reads live position at invocation time.
- 🔴 **#2 silent patience expiry** → **resolved in (D)**: new observer hook + audiobook narration.
- 🟠 **#3 concurrent-claim bug** → **resolved in (C)**: single-slot interface_name + busy rejection.
- 🟠 **#8 existing tests may assert CONTENT for claims** → **locked into DoD below**: grep + update any `_stacks[Channel.CONTENT]` assertions, add a regression test for COMMS-based claim.
- 🟡 **#7 sequencing — Stage 2b alone makes timers worse** → **addressed by co-landing Stage 5** in the same commit pair.

Critic's verdict: "stop and redesign." Redesigned above; Gate 3 cleared to proceed.

### Definition of Done (locked 2026-04-23)

- [ ] `coordinator.start_input_claim`: busy-check + raise `ClaimBusyError` on second claim; interface_name literal `"claim:active"`; Activity on `Channel.COMMS` with `patience=0`
- [ ] `ClaimBusyError` added to SDK; telegram skill catches it and sends peer rejection
- [ ] `AudioStream` gains optional `on_patience_expired` callback field
- [ ] `ChannelObserver` Protocol gains `on_patience_expired()` method with default no-op
- [ ] `FocusManager._handle_patience_expired` calls observer hook before terminal NONE/MUST_STOP notification
- [ ] `ContentStreamObserver` implements `on_patience_expired()` to invoke the AudioStream's callback if set
- [ ] Audiobooks `_build_factory` closure reads `start_position` from `self._get_live_position(book_id)` at each `stream()` invocation, not from a captured parameter
- [ ] Audiobooks acquires with `patience=timedelta(minutes=30)` + wires `on_patience_expired` to inject_turn narration
- [ ] Tests: **regression** test `test_call_hangup_resumes_audiobook_from_saved_position` — start book, start claim at T=3s, release claim at T=6s, assert book resumes from within 5s of position 3s (not back to 0)
- [ ] Tests: concurrent-claim rejection (two claims → second gets ClaimBusyError, first unaffected)
- [ ] Tests: patience expiry invokes observer hook then NONE (order locked)
- [ ] Tests: audit + update any existing tests asserting `_stacks[Channel.CONTENT]` for claim activity
- [ ] Manual smoke: audiobook + 3s call → book resumes; audiobook + >30min call → narrated + manual resume
- [ ] ADR `2026-04-XX — Focus plane completion: COMMS live, pause/resume contract, concurrent-claim rejection, patience-expiry hook`
- [ ] Docs: Stage 2 interactions matrix updated with COMMS rows; `concepts.md` + `architecture.md` updated (covered by T2.7 co-landing)

### Dependencies

- **Co-lands with Stage 5** (same commit pair) to prevent the "timers preempt calls during the gap" regression.
- **Co-lands with T2.7** docs reconciliation.
- **Blocks T1.11** (messaging) — messaging UX depends on final focus-plane shape.

### Post-ship smoke-test corrections (2026-04-24)

Gate 5 shipped in commit `32d4be3`; Gate-5 critic follow-ups in `72fa1ad`. Live smoke testing against real Telegram + OpenAI Realtime then surfaced four more issues that the unit tests did not catch. All fixed in the post-smoke-test follow-up commit. Details and rationale captured in [the 2026-04-24 ADR](./decisions.md#2026-04-24--post-smoke-test-fixes-to-the-focus-plane-is_ended-gate-playback-drain-wait-idle-inject-during-claim-claim-title-for-ui):

1. `on_claim_end` skill callbacks firing `inject_turn` queued their own request behind themselves (observer hadn't scrubbed `_claim_obs` yet) — fixed by gating on `observer.is_ended`.
2. `inject_turn_and_wait` returned at server-side `response_done`, not after client finished playing — announcement got flushed by the subsequent `start_input_claim`'s `audio_clear`. Fixed by computing drain time from cumulative audio bytes and sleeping until playback expected to complete.
3. `NORMAL`-priority inject from idle during a live claim preempted the claim. Fixed by extending the `_claim_obs`-gate to NORMAL (was only BLOCK_BEHIND_COMMS). Only PREEMPT now barges.
4. Orb showed "Listening" during an active call and didn't react to the peer's voice. Fixed by adding `InputClaim.title`, plumbing it through `claim_started` wire message, and driving the `live` orb state from the real playback analyser with a `sqrt` boost for Telegram's Opus-compressed peer audio.

**Lesson locked in**: skill callbacks fired from inside FocusManager's actor task (`on_claim_end`, `on_patience_expired`) must schedule any `ctx.inject_turn(...)` via `asyncio.create_task`, not await inline — `inject_turn` internally awaits `fm.wait_drained()`, which waits for `Queue.join` on a mailbox the FM actor is currently processing, deadlocking. Documented in `docs/skills/telegram.md` and captured as a memory entry.

---

### T1.10 — `huxley-skill-comms-telegram` ✅ done (`441120c`, 2026-04-22)

**Status**: done · **Effort**: spike + ~1 week implementation across multiple sessions.

Replaces the ripped-out `huxley-skill-calls` with the right shape: a skill that bridges Huxley to Telegram as a transport for both real-time voice calls and async messages. Family members reach the Huxley user via their existing Telegram clients — no Huxley-branded app on their side.

**Design**:

- **Real-time voice**: [`py-tgcalls`](https://pypi.org/project/py-tgcalls/) (wraps [`ntgcalls`](https://github.com/pytgcalls/ntgcalls) C++/WebRTC backend). Active maintenance (Feb 2026 release). Prebuilt wheels for macOS arm64, Linux x86_64, Linux arm64-v8a (OrangePi5 ready), Windows. Requires a Telegram **userbot** (real user account, not a bot account — Telegram bots can't make voice calls, officially).
- **Async messaging**: standard `python-telegram-bot` / Pyrogram `sendVoice` / `sendMessage` for voice notes + text. Uses the same userbot identity.
- **Outbound**: Huxley user says "llama a Mario" → skill initiates a Telegram voice call to Mario's account → Mario answers in his regular Telegram app.
- **Inbound**: Mario calls the userbot from his Telegram → skill accepts + bridges audio to the Huxley user via `InputClaim`.
- **Messages**: skill can send voice notes + text to configured contacts; can also receive them and deliver to the Huxley user via `inject_turn` in a quiet moment.

**Operational concerns** (for the setup doc):

- Needs a dedicated Telegram user account with a phone number for SMS verification (Mario has a SIM lying around). Separate from personal account.
- API credentials from `my.telegram.org/apps` (free).
- Session file holds the bot's Telegram identity — back up, don't commit.
- Userbot pattern is Telegram-TOS-"discouraged" but tolerated for non-spammy legitimate use. Family-only calls/messages are invisible to abuse systems.

**Pre-work — 1-day verification spike** (runs BEFORE skill implementation):

- Install py-tgcalls on macOS arm64 (Mario's dev env), register the userbot, place an outbound call CLI-style to Mario's phone. Measure latency + audio quality.
- Verify arm64 install path for future OrangePi5 deployment.
- Confirm the audio format bridges cleanly to Huxley's `InputClaim` (WebRTC Opus → PCM16 24kHz transcode path).
- Document failure modes (network blip, call reject, account edge cases).
- Output: `docs/research/telegram-voice.md` characterization report + throwaway `spikes/test_telegram_call.py`. If the spike reveals dealbreakers, fall back to Twilio and file that as an alternative T1.10 variant.

**Platform substrate used**: `InputClaim`, `provider.suspend/resume`, `MicRouter` single-claim invariant, `cancel_active_claim`, `inject_turn(PREEMPT)`. All shipped; nothing new needed framework-side.

**Shipped**:

- `huxley-skill-comms-telegram`: full bidirectional voice over Telegram p2p (ExternalMedia outbound + py-tgcalls record inbound).
- Outbound transport: ExternalMedia.AUDIO + dedicated OS send thread at strict 10 ms cadence; `AudioParameters(24000, 1)`; no FIFO, no ffmpeg, no Python-side resampling.
- Inbound: 48 kHz stereo PCM16 from py-tgcalls → decimation + channel-average downsample to 24 kHz mono in Python.
- Diagnostic tool: `tgcalls-diag/call.py` with tone/ext/mic/silence modes for isolating transport vs. audio-source issues.

**Lessons**:

- `ExternalMedia` lives in `pytgcalls.types`, NOT `ntgcalls`. Wrong import causes silent ImportError that kills `place_call` before a single frame is sent. Every prior ExternalMedia attempt was failing for this reason.
- `send_frame` is decorated (`@statictypes`, `@mtproto_required`); calling it outside the event loop does not produce a coroutine `asyncio.run_coroutine_threadsafe` recognizes. Fix: wrap in a plain `async def _send()` closure.
- Multiple stale server processes on the same port: when diagnosing "my fix didn't work", check `lsof -i :PORT` first. The browser may be talking to an old process.
- Heartbeat `mic_chunks_window=375` per 2s window confirms browser AudioContext is genuinely at 24 kHz; `silence_pct=0.0` in steady state confirms WebSocket delivery has no timing issues.
- **Inbound audio silence (post-ship fix, `80e8f38`)**: the announce-before-accept ordering caused near-silent inbound audio (peer_mean_rms ~0.1). Root cause: `inject_turn` returns after `request_response()` (~1s), not after the LLM finishes speaking (~3s), so `accept_call` was still delayed ~1s — enough to degrade pytgcalls WebRTC inbound audio quality. Fix: accept immediately (no delay), inject_turn after, sleep 3s to let LLM speak, then start_input_claim. `peer_audio_chunks()` flushes the inbound queue before yielding so the 3s buffered window doesn't replay as a delay.

### Stage 3a — Supervised `background_task` (in-memory) ✅ done (`521f269`, 2026-04-18)

> **Split rationale (PQ-3 from 2026-04-19 critic):** the original
> "Stage 3" triage entry labeled itself `done` without acknowledging
> the in-memory-only scope. Persistence across restart is a real gap
> that blocks T1.8 evolved reminders (a medication reminder the user
> set before a server restart should still fire). Stage 3a = this
> commit, Stage 3b = the persistence work (filed below as a separate
> queued entry).

Shipped. `huxley.background.TaskSupervisor` owns a pool of named
asyncio tasks. `SkillContext.background_task(name, coro_factory, *,
restart_on_crash=True, max_restarts_per_hour=10,
on_permanent_failure=None) -> BackgroundTaskHandle` is the skill-
facing API. Crashes log via `aexception`, restart with exponential
backoff (2s, 4s, 8s, ..., capped 60s), and after the per-hour budget
is exhausted fire `dev_event("background_task_failed", ...)` plus the
caller's optional `on_permanent_failure(PermanentFailure)` callback.
Application wires the supervisor into the lifecycle: instantiated in
`__init__`, `stop()` in `_shutdown` after `skill_registry.teardown_all`.
Timers skill refactored to use `ctx.background_task(..., restart_on_crash=False)`
instead of raw `asyncio.create_task` — first real consumer.

**Effort (actual)**: ~2 hours (vs. 3-day estimate). Smaller than
expected because the SDK side is just one new field + a default
no-op-with-real-task fallback for tests; the supervisor itself is
~150 LOC; the timers refactor is ~10 LOC. Tests took longer than
the implementation (10 supervisor tests cover crash + restart +
budget exhaustion + cancel + stop + name uniqueness + callback
robustness).

**Deliverables (original spec, all shipped)**:

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

### Stage 3b — Persistent supervised tasks across restart ✅ **done** (`6e1fce9`, 2026-04-19)

**Effort (actual)**: ~3h (vs 1-day estimate) — the skill-owned approach chosen below needed far less plumbing than the framework primitive originally speced.

**Design pivot (Gate 2 critic, 2026-04-19)**: the original spec called for a framework-level primitive — `persist_key` arg on `ctx.background_task`, supervisor serializes `(name, coro_factory, kwargs)` to `SkillStorage`, `restore_all()` driven from `Application.run()`. Critic pushed back: `coro_factory` is a closure (`lambda: self._fire_after(...)`) and serializing it forces either a "factories must be config-pure" skill contract or a factory registry — real SDK cost for one consumer. T1.8 reminders and T1.9 messaging both persist different shapes (cron-spec, thread cursor), so they won't share the primitive. Chose **skill-owned persistence** instead: timers skill does its own `SkillStorage` writes, framework stays inert. Extract the pattern when a second skill needs the same shape; premature extraction is the bigger risk.

**Deliverables (shipped)**:

- **SDK / framework — `b16ee3f`**: added `list_settings(prefix) -> list[(key, value)]` and `delete_setting(key)` to the `SkillStorage` Protocol. Framework adapter passes through with proper `ESCAPE '\'` on the LIKE query so prefixes containing `%` or `_` don't glob. 10 new unit tests (`TestListAndDelete` + `TestNamespacedSkillStorage`) cover prefix matching, wildcard escape, namespace isolation, delete scoping.
- **Timers skill — `<this commit>`**: each `set_timer` writes `timer:<id>` → `{"v":1, "fire_at": ISO, "message": str, "fired_at": null}` before scheduling. `_fire_after` stamps `fired_at` after the sleep and before awaiting `inject_turn`; deletes only when commit (`fired = True`) ran so mid-sleep cancellation (teardown) preserves entries. `setup()` enumerates `timer:*`, applies the restore policy (below), primes `_next_id = max(ids) + 1`.
- **Critic's required dedup guard**: the `fired_at` field catches the "process died between narration and delete" failure mode. Restore unconditionally skips + deletes entries with `fired_at` set — preferring a missed reminder to a double-dose reminder (user-safety call for medication use case).

**Restore policy** (fully documented in `docs/skills/timers.md`):

| State                        | Action                                                                   |
| ---------------------------- | ------------------------------------------------------------------------ |
| `fired_at` set               | Delete + skip (dedup — no double dose on crash-between-fire-and-delete). |
| `now - fire_at > 1h`         | Delete + skip (stale; intent is past).                                   |
| `fire_at` past but within 1h | Fire immediately (1s scheduled). Better late than never.                 |
| `fire_at` future             | Reschedule with `fire_at − now` remaining.                               |
| Malformed (JSON / key)       | Skip with warning log. No delete — future migration opportunity.         |

**Tests added**: 10 new (`TestPersistence`), 27 timer tests total (was 17). Cover: entry written on schedule, entry deleted on fire, teardown preserves entries, reschedule on restore fires correctly, stale-but-recoverable fires immediately, stale-past-threshold dropped, `fired_at`-set dropped (critical dedup), `_next_id` primed past existing, malformed entries skipped, empty storage is noop.

**Decisions deferred to first real user**:

- Clock skew mitigation beyond the stale-threshold guard. UTC wall clock on a fixed device is fine for Abuelo; revisit if timers get deployed somewhere with unstable NTP.
- Schema version migration. Every entry carries `"v": 1`; the first real schema change writes the migration code.
- `cancel_timer` / `list_timers` tools (still out of scope — no user flow needs them yet, but now a one-liner each).

**First consumer beneficiary**: T1.8 evolved reminders (persistent medication reminders) now has half its work done — persistence pattern is proven. T1.8 picks up cron/recurrence logic on top of this foundation.

**Lessons**: (a) the critic's "skill-owned, not framework-owned" call was right — adding `list_settings` + `delete_setting` was strictly smaller and more reusable than the `persist_key=` alternative. (b) `fired_at` dedup is cheap (one extra storage write per fire) but removes the worst failure mode. Not something I'd have arrived at without the critic flagging the medication-double-dose scenario.

### Stage 3c — PermanentFailure elapsed_s semantics ✅ **done** (`a286205`, 2026-04-19)

Renamed `PermanentFailure.elapsed_s` → `elapsed_in_window_s` to match
the supervisor's actual computation (`now - window_start`, where
`window_start` resets every `_BUDGET_WINDOW_S` of quiet). Docstring
now explains the window-reset semantics explicitly. `supervisor.py`
call site + `background.task_permanently_failed` log field +
`background_task_failed` dev_event payload all renamed to match.
Decision: pure rename + doc — no new `first_crash_time` tracking,
since no caller today needs total-age semantics and YAGNI. 286 core
tests green; no test referenced the field by name.

### Stage 4 — `ClientEvent` skill subscription + `server_event` outbound

**Status**: done (`718547bc`, 2026-04-29; review-fix follow-up
`<this commit>`, 2026-04-29) · **Effort actual**: ~1 session for
impl + ~30 min for review fixes — well below the original 1.5–2
day estimate. The pre-impl critic round collapsed scope (no
capability handshake, no protocol version bump, no firmware bundle,
no `hardware.*` namespace); the post-impl critic round caught two
🔴 + four 🟠 (see Lessons below). **Closes the I/O plane.** All
five primitives (`AudioStream`, `inject_turn`, `InputClaim`,
`background_task`, `client_event` / `server_event`) are now live.

**Review-fix follow-up** (commit `<this commit>`, 2026-04-29): the
post-ship critic on `718547bc` found two correctness bugs
(re-entrant subscribe crashes recv loop via `zip(strict=True)`
ValueError; `ClientEventPanel.tsx` referenced CSS variables that
weren't defined anywhere → silent pretend-theming) and four
edge-case concerns. All fixed:

- `_dispatch_client_event` snapshots the subs list before iterating
  (`subs = list(self._client_event_subs.get(event, ()))`). Closes
  the re-entrant-subscribe crash.
- `AudioServer.disable_client_event_dispatch()` flips a gate at the
  start of `Application._shutdown()`, BEFORE the unregister loop
  and BEFORE `teardown_all`. Late-arriving `client_event` no longer
  fires handlers through a half-stopped FocusManager / coordinator.
- `SkillRegistry.teardown_all()` now takes an optional `on_error`
  callback and is resilient to individual failures: one skill's
  teardown raising never blocks the rest. Framework-side passes a
  structlog `aexception` callback (SDK stays log-impl-agnostic).
- `ClientEventPanel.tsx` drops the `var(--bg, ...)` indirection —
  uses literal hex (slate-800 background, slate-700 inputs, blue-500
  accent) so the dev panel reads OK regardless of host page theme.
  Adds Escape-to-close (modal-dialog standard).
- 5 new regression tests pin the fixes:
  `test_handler_self_subscribing_does_not_crash_dispatch`,
  `test_dispatch_uses_snapshot_not_live_reference`,
  `test_disable_blocks_dispatch_to_skills`,
  `test_disable_is_idempotent`,
  `test_handler_persists_across_eviction`.

The 🟡 critic items (auto-close UX, `var()` consistency with future
PWA theming, `target.tagName` fallthrough cases, etc.) were noted
but not landed in the fix commit — they're cleanup, not correctness.

**Scope** (locked 2026-04-29 after critic round 1 → v2): the
original spec called for a capability-handshake (`client_hello`
with `capabilities[]`, 500ms timeout, `client_has_capability`) and
a firmware bundle (K1/K3 buttons + `hardware.*` namespace). Both
got cut after the critic correctly observed:

1. `client_event` is **already partially shipped** — `protocol.md:27`
   documents the wire shape (`{event, data?}`) and `server.py:285`
   handles inbound for telemetry-only logging. The new work is
   **adding skill-subscription dispatch + symmetric `server_event`
   outbound**, not re-defining the protocol.
2. The capability handshake solves a problem this single-developer
   monorepo doesn't have. Old clients tolerate unknown message
   types (firmware: `hux_app.c:265-269` logs and ignores; PWA: same
   pattern). Heterogeneous client matrix is hypothetical.
3. K1/K3 firmware wiring without a production consumer is
   "infrastructure for a deferred feature." When the first
   hardware-shape skill (panic, hass, knob…) is triaged, **that
   commit lands the firmware wiring alongside its real consumer**
   — not before. The architecture invites this addition cheaply
   when needed; nothing degrades by waiting.

#### Deliverables — framework side

- **Wire-protocol shape** unchanged from what's already shipped:
  - `{"type": "client_event", "event": "<key>", "data": {...}}` (C→S)
  - `{"type": "server_event", "event": "<key>", "data": {...}}` (S→C, **new**)
  - Field name is **`data`**, not `payload`. Matches `protocol.md`
    and `server.py` ground truth. `docs/skills/README.md`'s use of
    `payload` was aspirational and gets corrected.
- **`AudioServer` skill-subscription dispatch**: in `server.py`'s
  existing `case "client_event":` handler (line 277), keep the
  existing `client.<event>` telemetry log AND dispatch to any
  registered skill subscribers in parallel.
- **`AudioServer.send_server_event(event, data)`** — symmetric to the
  existing `send_dev_event`. Sends `{"type": "server_event", "event":
..., "data": ...}` over the active connection. No-op (with debug
  log) if no client connected.
- **`SkillContext.subscribe_client_event(key, handler)`** — registry-
  backed, auto-cleanup at skill teardown. Concurrent dispatch via
  `asyncio.gather(return_exceptions=True)`. If a handler raises,
  log via `aexception("client_event.handler_failed", key=...,
skill=...)` and the gather yields the rest of the results
  unaffected. Subscriptions persist across reconnects (skills don't
  tear down on transient WS disconnect).
- **`SkillContext.emit_server_event(key, data)`** — convenience
  wrapper around `AudioServer.send_server_event`. Skill-side error
  handling is the same (no-op + debug log if no client).
- **No `client_has_capability`, no `client_hello`, no 500ms
  timeout, no protocol version bump.** Compat across client matrix
  is via "unknown message type → log and ignore," which is
  already how every client behaves.
- **Namespace conventions** (convention-only, no runtime check):
  - `huxley.*` reserved for framework events. None emitted today;
    the namespace is documented as off-limits to skills.
  - Skills use `<skill-name>.*` for their own events.
  - **No `hardware.*` reservation in this commit.** Speculative; add
    when a second hardware-event class lands (rule of three).

#### Deliverables — PWA dev client

- **Shift+E panel** for firing arbitrary `client_event`: text input
  for key, JSON textarea for `data`, "Send" button. Validation:
  malformed JSON shows inline error. ~80 LOC SvelteKit.
- **Sidebar log** for incoming `server_event`: timestamp, key,
  pretty-printed JSON. Auto-scroll. ~40 LOC.
- The panel is a dev affordance; no user-facing doc beyond a code
  comment at the panel's top of file.

#### Tests — framework side

Located in `server/runtime/tests/unit/`:

- `subscribe_client_event` registers handler; inbound matching
  event invokes it with parsed `data`.
- Multiple subscribers to the same key: all called even if one
  raises. The raising handler's exception is logged; other
  handlers complete normally (assert via mock side-effects).
- Unsubscribe at skill teardown: handler is removed; subsequent
  events don't reach it.
- Unknown key (no subscribers): the existing `client.<event>`
  telemetry log still happens; no error; debug log notes "no
  subscribers."
- `emit_server_event` with no client connected: no-op + debug log.
- `emit_server_event` with client connected: WS receives a
  `server_event` frame with the right key + data.
- Subscriptions persist across reconnect: subscribe before WS
  closes; reconnect; new client_event reaches the handler.
- Concurrent dispatch: two slow handlers run in parallel (assert
  both started before either completed via timestamp ordering).

#### UX validation

- **Browser dev client smoke**: open the PWA, hit Shift+E, fire
  `dev.ping` with payload `{"hi": 1}`; observe a toy test skill (in
  the runtime tests, not in production) calls `inject_turn`. Then
  the test skill emits `dev.pong`; dev client log shows the
  incoming `server_event`. Both directions covered.

#### Docs touched

- `docs/protocol.md` — add a `server_event` row mirroring the
  existing `client_event` row. Confirm shape: `{event, data?}`. Note
  that `client_event` now ALSO dispatches to skill subscribers (was
  telemetry-only).
- `docs/skills/README.md` — drop the "⚠️ Planned — not yet shipped"
  warning at line 436. Update the worked example to use `data` (not
  `payload`). Document that subscriptions are concurrent +
  exception-isolated. Document persistence across reconnect.
- `docs/concepts.md` — keep the existing `subscribe_client_event`
  reference (line 111); confirm it's still accurate.
- `docs/extensibility.md` — flip the "no client→server signals
  beyond audio" gap to closed. List what hardware-shape skills now
  compose without further framework changes (hardware buttons via
  any client that can emit `client_event`, smart-home announcers,
  wearable health alerters, etc.).
- `docs/io-plane.md` — historical artifact; leave the banner. The
  Stage 4 section in that doc is superseded by the v2 shape; not
  worth surgically editing a banner-disclaimed document.

#### Out of scope (deferred)

- **Capability handshake** — re-introduce when a real
  heterogeneous-client need emerges (some clients have LEDs, some
  don't, etc.). Today: hypothetical.
- **Firmware K1/K3 wiring** — lands with the first consuming skill
  (panic / hass / volume-knob — whichever ships first). Comment in
  `hux_button.c:33` already invites the addition.
- **Wildcard / pattern matching** in `subscribe_client_event`.
- **Framework-emitted `huxley.*` events** — namespace reserved but
  unused.
- **`hardware.*` namespace reservation** — premature.
- `${ENV_VAR}` interpolation in `persona.yaml`, F6 log-noise
  cleanup — separate commits if shipped at all.

### Stage 5 — `InjectPriority.BLOCK_BEHIND_COMMS` (severity tier for urgent reminders that respect active calls)

**Status**: done (2026-04-24, co-landed with Stage 2b + T2.7) · **Effort**: S — `InjectPriority.BLOCK_BEHIND_COMMS` added to SDK, coordinator `_dispatch_post_turn` branches on it, timers retrofitted, 4 new priority tests green. · **Motivation**: urgent-reminder tier that preempts content but respects live calls.

**Problem.** Urgent-but-not-conversational alerts (medication reminders, future doorbell/smoke-alarm narrations) need a severity tier that **preempts CONTENT** (audiobooks stop, book's patience covers it) but **queues behind COMMS** (doesn't interrupt an active call). Today's two-tier `InjectPriority.NORMAL | PREEMPT` has no slot for this: NORMAL queues behind everything including CONTENT (medication reminder waits hours behind an audiobook — filed as PQ-1); PREEMPT drains over CONTENT AND over COMMS (post-Stage-2b, interrupts calls). The timers skill currently uses PREEMPT, which works only because calls live on CONTENT today — as soon as Stage 2b ships, timers would start interrupting calls.

**Why it matters.** Correct severity modeling for the urgent-reminder pattern. Without this tier, Stage 2b's COMMS move introduces a regression (or we keep timers on PREEMPT, which is then semantically wrong). Co-shipping Stage 2b + Stage 5 closes the loop atomically.

### Scope collapse from critic review (2026-04-23)

Original scope proposed an `inject_alert(prompt, dedup_key=...)` SDK primitive with its own Protocol, `SkillContext.inject_alert` field, and Activity on `Channel.ALERT`. Critic finding #6: since `inject_alert` would narrate through the DIALOG LLM path anyway (no LLM-less siren consumer exists today), a separate primitive is a **priority label in Activity-shape clothing**. Equivalent behavior ships as 15 lines via a new `InjectPriority` variant — reuses every existing machinery (queue, dedup, drain, release) and doesn't introduce a new SDK surface that has to be maintained against a speculative future consumer. Builder's rule ("don't add speculative abstractions") applies.

`Channel.ALERT` stays defined in `focus/vocabulary.py` with its priority 200 slot as a **reserved tier** for future non-LLM sirens/alarms. The moment a skill needs ALERT-priority, LLM-free audio (siren pattern, not narration), wire ALERT with its own observer type and SDK surface. Today that consumer does not exist.

### Validation (Gate 1)

- `Channel.ALERT` defined in `focus/vocabulary.py:35`, priority 200 in `CHANNEL_PRIORITY`. Zero call sites anywhere in `server/` outside the enum definition and priority map. Verified by grep.
- `InjectPriority` enum lives in `huxley_sdk.types`; two variants today (`NORMAL`, `PREEMPT`). `TurnCoordinator._dispatch_post_turn` branches on priority for drain policy.
- `huxley-skill-timers` urgent-reminder fire path uses `inject_turn(priority=InjectPriority.PREEMPT)` — per Stage 1d.3 PQ-1 notes. Post-Stage-2b this will preempt calls. Concrete regression.

### Design (Gate 2 — locked 2026-04-23 post-critic)

**New priority variant:**

```python
# server/sdk/src/huxley_sdk/types.py
class InjectPriority(StrEnum):
    NORMAL = "normal"
    BLOCK_BEHIND_COMMS = "block_behind_comms"   # NEW — between NORMAL and PREEMPT
    PREEMPT = "preempt"
```

**Coordinator semantics** (additive branch in `_dispatch_post_turn`):

| When fired                         | `NORMAL`                     | `BLOCK_BEHIND_COMMS`                            | `PREEMPT`                                     |
| ---------------------------------- | ---------------------------- | ----------------------------------------------- | --------------------------------------------- |
| Idle                               | Fires immediately            | Fires immediately                               | Fires immediately                             |
| Active user turn (DIALOG)          | Queues, fires at turn-end    | Queues, fires at turn-end                       | Queues, fires at turn-end (never barges user) |
| Active audiobook/radio (CONTENT)   | Queues, fires at content-end | **Preempts content (same as PREEMPT)**          | Preempts content                              |
| Active call (COMMS, post-Stage-2b) | Queues, fires at claim-end   | **Queues, fires at claim-end (same as NORMAL)** | Preempts claim (claim ends PREEMPTED)         |

`BLOCK_BEHIND_COMMS` = "PREEMPT semantics vs CONTENT, NORMAL semantics vs COMMS." That's the whole behavioral contract.

**Timers retrofit:**

```python
# server/skills/timers/src/huxley_skill_timers/skill.py
# Before:
await self._ctx.inject_turn(prompt, priority=InjectPriority.PREEMPT)
# After:
await self._ctx.inject_turn(prompt, priority=InjectPriority.BLOCK_BEHIND_COMMS)
```

No new SDK surface, no new Protocol, no new coordinator method. The `InjectTurn` Protocol already accepts `priority` as a keyword arg; just pass the new enum value.

### Critic notes (Gate 2 — ran 2026-04-23)

Full critic report captured in session transcript; findings and resolutions:

- 🟠 **#4 ALERT dedup_key collision across channels** → **obviated**: no separate channel, single DIALOG dedup namespace, same key-space as existing inject_turn. No new collision surface.
- 🟠 **#5 2-hour queue-behind-call for medication is unsafe** → **deferred but flagged**: the TTL-on-queued-inject feature was shelved in Stage 1d.2. This concern brings it back. NOT in Stage 5 scope; file as separate ticket if medication reminders start shipping with known-long-call households.
- 🟠 **#6 ALERT channel is overbuilt for current needs** → **accepted and adopted**: this is why the scope collapsed to a priority enum.
- 🟡 **#7 sequencing** → **co-land with Stage 2b** in the same commit pair.

Critic's verdict "stop and redesign" addressed by this rescoping.

### Definition of Done (locked 2026-04-23)

- [ ] `InjectPriority.BLOCK_BEHIND_COMMS` enum value added to `huxley_sdk.types`
- [ ] `TurnCoordinator._dispatch_post_turn` (and any priority-branch code) handles the new variant: same as PREEMPT vs CONTENT, same as NORMAL vs COMMS
- [ ] `huxley-skill-timers` urgent path changed from `PREEMPT` to `BLOCK_BEHIND_COMMS`
- [ ] Tests: idle-fires, content-preempts, call-queues-and-fires-at-claim-end, dialog-queues-at-turn-end, dedup_key still works
- [ ] Tests: regression — timers-urgent-during-call no longer ends the claim (was PREEMPT; now BLOCK_BEHIND_COMMS queues)
- [ ] Docs: `concepts.md` severity-tier section; `skills/README.md` inject_turn priority guide updated with new variant
- [ ] ADR entry (shared with Stage 2b: "Focus plane completion: COMMS live, BLOCK_BEHIND_COMMS priority tier, ALERT reserved")
- [ ] `Channel.ALERT` documented in-code and in concepts.md as **reserved for future non-LLM alert sounds (siren, alarm); no current consumer**

### What we're explicitly NOT building

- `inject_alert` as a separate SDK primitive (collapsed into priority; revisit when a non-LLM siren skill materializes)
- `Channel.ALERT` wire-up (reserved; revisit trigger as above)
- TTL on queued injects (noted but out-of-scope; file as separate if medication safety case forces it)

### Dependencies

- **Co-lands with Stage 2b** (same commit pair — separately is strictly worse per critic finding #7)
- **Enables**: timers correct semantics without regression against calls

---

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

`server/runtime/tests/unit/test_summarize.py` (10 tests, AsyncOpenAI mocked at module level):

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

`server/runtime/tests/unit/test_turn_coordinator.py` → `TestToolErrorEnvelope`:

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

`server/skills/audiobooks/tests/test_skill.py` → `TestSpeedControl`:

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
- `server/personas/abuelos/persona.yaml` — AUDIOLIBROS section restructured + new VELOCIDAD section
- `docs/skills/audiobooks.md` — out of scope tonight; the user-facing tool spec lives in the tool description string itself, which is what the LLM reads

### Ship (Gate 5)

- Commit hash filled in by the commit step.
- **Lessons**: This bug class — model lying about tool execution because the tool can't do what was claimed — is the _third_ hallucination instance after news (fabricated headlines) and radio (fabricated "what's playing"). Pattern is consistent: weak/missing tool → model fakes via wrong tool → user re-asks. Future skills should explicitly map "things the user might ask for" to tool capabilities and either ship the capability or honestly forbid the claim. The persona prompt addition ("NUNCA digas X sin haber llamado primero a Y") is the right shape for closing the loop, but only meaningful when Y exists.
- **Position math drift**: with the current `bytes_read / BYTES_PER_SECOND` calculation and `-re` throttling, output_seconds == wall_seconds. atempo affects what content is in those seconds, not the rate at which they emerge. The math `book_advance = output_seconds * speed` is correct in this regime.

**Follow-up bug (fixed same day, 2026-04-18)**: when `set_speed` is called and nothing is actively streaming but a `last_id` exists in storage (the natural flow: PTT to interrupt → "más lento"), the original implementation only persisted the value and returned a plain ack. Result: user heard silence, model said "ahora se reproduce a un ritmo más pausado" (misleading), user had to ask again. Fix: `_set_speed` now resumes the last book at the new speed when no stream is live but `last_id` exists. `_play` loads the just-persisted speed from storage so the new tempo applies on the resume. Two new regression tests: `test_set_speed_with_saved_book_resumes_at_new_speed` (paused-then-slowdown path) and `test_set_speed_with_no_saved_book_only_acks` (truly fresh path stays ack-only). 65 audiobooks tests green (was 63).

---

## T1.8 — `huxley-skill-reminders` (full medication/appointment UX)

**Status**: done (`a4beba69`, 2026-04-29; review-fix follow-up
`9d0ccff5`, 2026-04-29; RRULE migration `d45cce88`, 2026-04-29) ·
**Effort**: ~1 session for impl + ~1 session for the post-ship
review fixes + ~1 session for the recurrence-model upgrade
(matched the ~1-week estimate budgeted for design + critic + impl +
docs). Zero framework changes — composes existing `inject_turn` +
`background_task` + skill-owned SQLite storage.

**MVP shipped (2026-04-18)**: `server/skills/timers/` — proves the
full inject_turn path works end-to-end. User says "recuérdame en 5
minutos X" → LLM calls `set_timer` → skill spawns asyncio task → 5min
later `ctx.inject_turn` fires → framework preempts any content
stream, narrates the reminder. Abuelo persona system prompt gained
a TEMPORIZADORES section. 13 skill tests + workspace integration.
Known gaps (see `docs/skills/timers.md` for detail): no persistence
across restart; no list/cancel tools; no ack/retry semantics; seconds
only (no date-specific scheduling).

**Problem.** Medication + appointment reminders are the first concrete user
benefit of the I/O plane. Without them, "the agent can speak proactively"
is an abstract capability with no shipped consumer.

**Why it matters.** Mario's father specifically flagged reminders as a
daily-use need. Medication reminders are also the canonical
"retry-until-acknowledged" pattern — they validate that the framework's
choice to push retry semantics to the skill (not the primitive) is the
right call.

**Sketch**:

- Persona config declares a reminder list in YAML as a **seed** (initial
  defaults); `{id, when, prompt, kind, retry}` fields. `kind` in
  `{medication, appointment, generic}` drives the urgency tier default.
- **Storage pattern (decided 2026-04-21)**: YAML is read-only seed data.
  All runtime reads and writes go through SQLite — same pattern as the
  timers skill's `list_settings` / `store_setting` storage primitives.
  On `setup()`, skill imports YAML seed into SQLite if the table is empty
  (idempotent), then operates entirely from SQLite. This means reminders
  survive server restart and user-added reminders (`add_reminder` tool)
  are persisted without touching the YAML file.
- `setup()`: import YAML seed → SQLite, register
  `background_task("scheduler", ...)`
- Scheduler loop: pick next due → sleep until due → fire
  `inject_turn(prompt, urgency=...)` → LLM narrates reminder in its
  response; skill exposes `acknowledge_reminder(id)` tool for the LLM
  to call when user confirms ("ya me la tomé"). If no ack within configured
  retry window and `kind == medication`, re-fire at escalating urgency.
- **Ack pattern (decided 2026-04-21)**: acknowledgment is LLM-driven, not
  framework-driven. The skill exposes `acknowledge_reminder(id)` as a tool;
  the LLM calls it when the user's PTT response indicates acknowledgment.
  `InjectedTurnHandle.wait_outcome()` is **not used** — that abstraction
  leaks application-layer semantics (what "acknowledged" means) into the
  framework. See Stage 1d.2 note for the rationale.
- Tool surface: `add_reminder`, `list_reminders`, `cancel_reminder`,
  `acknowledge_reminder`
- Prompt context: list upcoming reminders so the LLM can mention them on
  request

**Framework changes needed**: none. Uses existing `inject_turn` +
`background_task` + skill-owned SQLite storage.

### Critic notes (Gate 2 — ran 2026-04-29)

A fresh critic agent reviewed a more ambitious dual-channel design
(ALERT loop + DIALOG narration, plus `dismiss_on_ptt` and `loop` on
`PlaySound`/`AudioStream`, plus TTL on `inject_turn`) and was hostile
to it. Findings, all incorporated:

1. **Ship T1.8 exactly as already specced — zero framework changes.**
   The MVP timers skill at `server/skills/timers/` already proves the
   `inject_turn(BLOCK_BEHIND_COMMS)` proactive-narration path. Adding
   `loop` / `dismiss_on_ptt` / `channel=ALERT` / `ttl_seconds` now is
   solving speculative failure modes (OpenAI-down, mass alert-skill
   proliferation) without data forcing them. The 2026-04-24 ADR
   explicitly trapped this trade ("ALERT gets wired when a concrete
   skill needs non-narrated audio in that tier") — reminders is not
   that skill if narrated alerts work.
2. **Cloud-down resilience is misleading.** Dual-channel buys
   OpenAI-unreachable resilience but NOT server-down resilience.
   Server uptime in grandpa's house is almost certainly worse than
   OpenAI Realtime's. The right answer for true alarm reliability is
   firmware-side scheduling, not SDK abstractions today.
3. **`dismiss_on_ptt=True` re-opens the bug class the 2026-04-24
   post-smoke patch closed.** Adding a fourth case onto
   `on_ptt_start`'s three existing branches creates the same race
   surface between dismissal and a queued `BLOCK_BEHIND_COMMS`
   inject. Decline.
4. **Boot reconciliation policy belongs in the skill** (already
   settled by ADR 2026-04-19 — skill-owned persistence). Confirmed.
5. **Caregiver escalation skipped from v1.** Cannot design correctly
   without observing the actual failure mode (didn't hear / forgot /
   button confusion / dead device). Defer until grandpa misses
   reminders in real use.
6. **TTL on `inject_turn` (D7) stays deferred.** Independently
   useful, but landing it with reminders bundles unrelated work.
   File separately if a real consumer surfaces.

The earlier 2026-04-21 decisions stand:

- YAML is read-only seed; SQLite is the runtime store.
- Ack is LLM-driven via `acknowledge_reminder(id)` tool (no
  `wait_outcome`).

### Definition of Done (locked 2026-04-29)

- [ ] New workspace package `server/skills/reminders/`, entry-point
      `huxley-skill-reminders`, mirror of `huxley-skill-timers` shape.
- [ ] Tools (es / en / fr descriptions): `add_reminder`,
      `list_reminders`, `cancel_reminder`, `snooze_reminder`,
      `acknowledge_reminder`.
- [ ] Persistent state via `SkillStorage` under prefix `reminder:` —
      one JSON entry per reminder. Schema versioned (`v: 1`).
- [ ] Scheduler runs as a single supervised
      `ctx.background_task("scheduler", ...)`; on each tick picks the
      next-due `pending` reminder, sleeps, fires
      `inject_turn(BLOCK_BEHIND_COMMS)`, transitions state.
- [ ] Boot reconciliation with kind-specific `late_window`:
      medication=15min, appointment=2h, generic=1h. Past-but-within
      → fire on next tick (catch-up). Past-but-outside → mark
      `missed` (medication safety: don't double-dose).
      `state='fired'` on boot → resume retry timer (medication only).
- [ ] Retry escalation for `kind='medication'`: up to 3 re-fires at
      5 / 10 / 30 minutes. After exhaustion, mark `missed`.
- [ ] Recurrence: `recurrence: 'daily' | 'weekly' | None`. On ack
      OR on `missed`, schedule next instance (recurrence outlasts a
      single missed dose).
- [ ] YAML seed import on first boot only (idempotent — gated on
      `reminder:` prefix being empty in storage).
- [ ] `prompt_context()` surfaces missed reminders since last
      session start so the LLM weaves them into the next reply,
      then transitions `missed` → `surfaced`.
- [ ] Abuelo persona system prompt gains a RECORDATORIOS section
      (es/en/fr) and a `skills.reminders` block (timezone, language
      i18n templates, optional seed).
- [ ] Tests: happy path, restart-catch-up (within window), restart-
      mark-missed (outside window), ack cancels retry, snooze
      reschedules, cancel removes, recurrence schedules next on
      ack, missed-with-recurrence still schedules next, malformed
      entries skipped, prompt_context lists missed.
- [ ] Docs: `docs/skills/reminders.md`, `docs/skills/README.md`
      index entry, `docs/personas/abuelos.md` mention, Abuelo
      `persona.yaml` change.
- [ ] `uv run ruff check server/`, `uv run mypy server/sdk/src
server/runtime/src server/skills/reminders/src`, and
      `uv run --package huxley-skill-reminders pytest` all green.
- [ ] Workspace `huxley` runtime tests still green.

### Tests (Gate 3 — filled at ship 2026-04-29)

`server/skills/reminders/tests/test_skill.py` — 52 tests across:

- **`TestAddReminder`** — happy path, empty message rejection, missing
  `when_iso` rejection, naive-datetime rejection, past-time rejection,
  invalid-kind rejection, invalid-recurrence rejection, default kind is
  `generic`, unique ids across calls.
- **`TestListReminders`** — empty list, chronological order, excludes
  acked + cancelled.
- **`TestCancelReminder`** — cancels pending, unknown id error,
  already-terminal idempotent.
- **`TestSnoozeReminder`** — reschedules, fired→pending reset,
  out-of-range rejection, terminal-row rejection.
- **`TestAcknowledgeReminder`** — terminal transition, unknown id
  error, recurrence schedules next instance.
- **`TestPromptContext`** — time + tz banner (es / en), missed
  surfacing, terminal rows excluded.
- **`TestBootReconciliation`** — future pending kept, past pending
  within window kept pending, past pending outside window → missed,
  past pending outside window with recurrence schedules next, fired
  medication with retries left resumes, fired medication retries
  exhausted → missed, fired non-medication → acked, malformed entry
  skipped without crash.
- **`TestMedicationRetry`** — first fire transitions to fired,
  retry-budget exhaustion marks missed, one-shot kinds (appointment
  / generic) do not retry.
- **`TestRecurrence`** — fire-with-recurrence schedules next,
  missed-with-recurrence still schedules next on boot.
- **`TestSeedImport`** — imported on first boot, idempotent across
  reboots, invalid entries skipped.
- **`TestSchedulerFires`** — end-to-end: pre-seeded overdue pending
  - real scheduler → inject_turn fires + state transitions to
    acked.
- **`TestUnknownTool`** — error envelope.
- **`TestTeardown`** — cancels scheduler, preserves storage.
- **`TestDefaultLateWindows`** — encodes safety property
  (medication < generic < appointment).
- **`TestPersonaConfig`** — custom late_window override, invalid
  override falls back.
- **`test_prompt_context_localized`** — parametrized es / en / fr.

All 52 pass. Workspace check: 376 runtime + 72 SDK + 30 timers + 52
reminders = 530 tests green; ruff clean for new code (4 pre-existing
SIM117 errors in `server/runtime/tests/unit/test_firmware_contract.py`
predate this work — flagged for separate housekeeping); mypy --strict
clean across `server/sdk/src server/runtime/src server/skills/reminders/src`.

### Docs touched (Gate 4 — filled at ship 2026-04-29)

- New: `docs/skills/reminders.md` — full skill doc (tools, persona
  config, state machine, persistence, boot reconciliation, what's
  not in v1, logging events).
- `docs/skills/README.md` — index entry alongside the timers split
  rationale.
- `docs/personas/abuelos.md` — proactive-speech note now lists
  `reminders` alongside `timers`.
- `CLAUDE.md` — repo-layout tree adds `reminders/` and refines the
  timers description; commands list adds the reminders pytest line.
- `server/personas/abuelos/persona.yaml` — RECORDATORIOS section in
  es / en / fr system prompts; new `skills.reminders` block with
  timezone, fire_prompt, i18n.{en,fr}.fire_prompt, optional
  late_window overrides commented out, optional seed list commented
  out.

ADR review (2026-04-29): no new ADR needed. The dual-channel /
ALERT-loop / `dismiss_on_ptt` ideas were considered and explicitly
declined in the Gate-2 critic notes above — that decision is captured
in the triage entry, doesn't rise to ADR-level, and consistent with
the 2026-04-24 ADR closing line ("ALERT gets wired when a concrete
skill needs non-narrated audio in that tier" — reminders does not).

### Ship (Gate 5 — done 2026-04-29)

**Commits**:

- `a4beba69` — initial implementation (skill code, tests, persona
  wiring, docs).
- `9d0ccff5` — post-ship review fixes (commit-before-inject for
  medication safety, recurrence idempotency, drop dead
  `_STATE_SURFACED`, `asyncio.Lock` on `_allocate_id`, persona-
  prompt exception so ack phrases bypass `echo_short_input`).
  Adds 10 regression tests pinning the fixes.
- `d45cce88` — recurrence model upgrade: enum → RFC 5545 RRULE
  via `python-dateutil`. Mario's call ("do the correct thing, not
  the patch") after the review flagged DST handling as a latent
  bug for non-Bogota personas. Schema bump v1 → v2 with
  transparent on-read migration. Adds `series_start` so COUNT/UNTIL
  rules terminate correctly across the recurring-row chain.
  Persona prompts (es/en/fr) gain RRULE pattern examples; tool
  description teaches the LLM to compose the strings. 13 new
  regression tests covering DST in `America/New_York` (both spring-
  forward and fall-back), weekday-only / biweekly / monthly-by-day
  / COUNT-exhaustion patterns, v1→v2 migration, invalid-rule
  rejection, tz fallback.
- `255d3e85` — RRULE review fixes (post-`d45cce88`). Second-round
  critic found two 🔴 in the validator: (a) embedded `DTSTART:` in
  the rule string silently shadowed our `series_start` kwarg,
  jumping next-fire dates by years on medication rows; (b)
  UNTIL-past validated as parseable then degraded to one-shot with
  no LLM feedback. Plus 🟠 snooze docstring/code drift (the comment
  said "ladder resumes at fired_count = 1" but the code only flipped
  state, leaving fired_count non-zero so the next fire skipped to
  the 10-min interval). All fixed; 8 new regression tests including
  documented DST-gap behavior at 02:30 AM and a v1-mid-retry-
  medication migration path. Doc + tool description tightened so
  the LLM gets correct `when_iso` guidance for BYDAY rules.
- `b93041c3` — third-round review fixes. Found two more 🔴 plus a
  🟡: (a) snooze on a `state=missed` row silently no-oped while
  returning `ok: true` to the LLM (medication-safety UX hazard:
  user thinks the snooze worked, no fire ever happens, infinite
  re-surface loop); (b) `FREQ=DAILY;COUNT=1` — a valid one-shot —
  was incorrectly rejected as "no future occurrences" because the
  validator anchored its probe on `now` instead of `when_iso`,
  AND used `rrule.after()` which is microsecond-sensitive against
  rrulestr's seconds-truncated dtstart; (c) `EXDATE:` / `RDATE:`
  in compound rule strings bypassed the round-2 DTSTART guard,
  with EXDATE silently dropping the user's first fire from the
  recurring chain. All fixed; 5 new regression tests. Validator
  now threads `when_iso` through and probes via `next(iter(rrule),
None)` to dodge the microsecond truncation. Compound-rule guard
  now covers `(DTSTART, RDATE, EXDATE)`. Stale `_STATE_SURFACED`
  references in docstring + doc cleaned up.

**Effort actual**: ~1 session for impl + ~1 session for review-fix
follow-up. Total matches the 1-week estimate (estimate budgeted for
design + critic + impl + docs; design + critic happened first,
leaving focused implementation; the post-ship review surfaced four
correctness bugs and one prompt issue that the original critic pass
hadn't anticipated).

**Lessons**:

1. **The post-ship review caught what the design critic couldn't.**
   The Gate-2 critic correctly steered scope down (declined the
   ALERT-channel design); it did not catch correctness bugs in the
   shipped state-machine code. Different review modes catch
   different bugs: the design critic prevents over-building; the
   post-ship code review catches semantic-vs-implementation drift.
   Both passes were necessary. The fix-commit ratio (4 correctness
   bugs found in a 1 600-LOC skill on first review) is a useful
   prior — assume future skills of similar scope will need the same
   second pass.
2. **`_allocate_id` defensive scan caught a real bug.** The original
   code read `_meta:next_id` with default `"1"` if absent, and
   `_reconcile_on_boot` set the meta key only AFTER processing
   entries. A `_schedule_next_recurrence` call inside that loop
   allocated id 1, colliding with an original row whose state
   transition hadn't been written yet. Fix: scan-on-missing-or-stale
   in `_allocate_id` PLUS prime the meta key before the loop. Both
   landed because either alone would have been fragile in the test
   path that bypasses `setup()`. The follow-up review surfaced a
   second related bug — concurrent `_allocate_id` callers can race
   on real (I/O-yielding) storage; fixed with `asyncio.Lock`.
3. **Commit-before-inject is not optional for medication safety.**
   The original implementation narrated first then saved state. A
   crash mid-narration on next boot meant re-narration: double
   dose. The fix mirrors the timers skill's well-trodden pattern.
   Generalizable rule: **any skill whose `inject_turn` represents
   an action with safety-relevant cost (medication, money, "send
   message") must persist the post-action state BEFORE inviting the
   action.** Document this in `docs/skills/README.md` whenever a
   future skill in this category is added.
4. **Recurrence creation must be idempotent.** Boot reconciliation
   is the canonical "called multiple times for the same input"
   path, so any side effect inside it (creating new rows, emitting
   inject_turn, etc.) needs an idempotency key. The original code
   had none and would have produced N duplicate rows after N
   restarts. Cheap to fix once known (one storage scan), expensive
   in production data integrity if missed.
5. **Critic was right to say "ship the smaller version."** The
   ALERT-channel / `dismiss_on_ptt` design would have added 80 LOC
   to the SDK + a coordinator branch + risked re-opening the
   2026-04-24 PTT race bugs. The shipped version composes existing
   primitives only and total skill code (skill + tests) is ~2 200
   LOC self-contained after the review fixes.
6. **Single-loop scheduler over per-task scheduler was the right
   call.** Timers uses one task per timer with
   `restart_on_crash=False`; reminders uses one supervised loop
   over storage with `restart_on_crash=True`. The state machine
   carries the truth, so a crashed scheduler is recoverable by
   re-reading rows on the next loop iteration.
7. **`prompt_context()` cache pattern**. The Skill protocol's
   `prompt_context` is sync, so the missed-surface cache must be
   refreshed by writers (`_save_entry`, `_load_all_entries`).
   Pulling that into a helper kept the two refresh paths consistent
   and avoided per-turn storage round-trips. The follow-up review
   noted that an aspirational `_STATE_SURFACED` transition was
   added to compensate for prompt_context being sync — that turned
   into a dead state because the transition path was never wired.
   **Drop unreachable states from the model**; if you can't actually
   transition into a state, don't define the state. The current
   model accepts that missed reminders surface until the LLM
   explicitly clears them via ack/cancel — simpler and correct.
8. **Reach for the standard format, not a special-case enum.**
   The original `recurrence: 'daily' | 'weekly'` enum was solving a
   problem (recurrence) that already has an industry-standard
   format (RFC 5545 RRULE). The enum let DST drift silently AND
   failed to express patterns the LLM could trivially produce
   ("every weekday", "every 2 weeks", "for 7 days"). The migration
   to RRULE was ~30 LOC + 1 dependency + a schema bump and turned
   "future feature requests" into "the LLM already knows how to do
   that." Generalizable rule: if a domain (recurrence, time zones,
   calendar events, etc.) has a standard, use it — even if today's
   ask is a subset. The enum is the speculative-special-case
   anti-pattern; the standard is the one that ages well.
9. **Schema versioning, even at v1, pays for itself.** Bumping
   `_ENTRY_VERSION` 1 → 2 with a lazy on-read migration cost ~10
   LOC and made the RRULE swap painless on storage that already had
   v1 rows from development testing. Every future schema change can
   reuse the same pattern; the alternative ("there are no v1 rows
   yet, just rename the field") would have set the wrong precedent.
10. **Validation probes need to anchor on the user's input, not on
    `now()`.** Round-2's `_validate_rrule` probed `rrule.after(now+1s)`
    to detect "no future occurrences." That worked for UNTIL-past
    but rejected `FREQ=DAILY;COUNT=1` (a valid one-shot anchored at
    `when_iso`) because the sole occurrence at `dtstart=now` was
    already past the +1s window. Round-3 fix anchors the probe on
    `when_iso` and uses `next(iter(rrule), None)` to dodge the
    microsecond-truncation issue (`rrulestr` rounds dtstart to whole
    seconds; `.after(when_with_microseconds, inc=True)` returns None
    even when `when` is itself an occurrence). Generalizable rule:
    when validating user-supplied input, probes should anchor on
    user-supplied state, not on the wall clock.
11. **The handler-returns-`ok`-while-doing-nothing pattern is the
    most insidious failure mode.** Rounds 1–2 caught it once
    (round-1 F31's "row state didn't save before inject_turn"),
    round 3 caught it again (snooze-on-missed). Both shapes:
    handler accepts the input, does part of the work, returns
    success — but the work doesn't reach the consumer. From the
    LLM's perspective, the call succeeded, so it tells the user
    confidently. Lesson: when a handler can't fulfill the
    semantics, **fail loudly**. Silent success is worse than
    explicit error because the LLM's response treats silent
    success as ground truth.

---

## T1.9 — `huxley-skill-messaging` _(retired 2026-04-23)_

**Status**: retired → moved to Deferred as **D6**. The generic "messaging-as-abstraction-over-providers" framing was wrong; Huxley is per-provider-skill-shaped. Superseded by **T1.11** (telegram messaging features inside `huxley-skill-telegram`). See D6 for revisit trigger.

---

## T1.10 — `huxley-skill-calls`

**Status**: partial (2026-04-19) · split into two deliverables · **Effort remaining**: ~1 week for panic button + auto-answer · **Blocked by**: T1.4 Stage 4 (`ClientEvent` for panic button)

**Progress note (2026-04-19)**:

- Outbound voice-command calling is **shipped** under a different skill name: `huxley-skill-comms-telegram` (commit `4627ee1`). Uses Telegram (userbot + py-tgcalls + ntgcalls) as the transport instead of Twilio, which eliminates the paid infra dependency and keeps all call audio on Mario's family's existing tools. Bidirectional live-PCM on p2p is proven working after 5 iterative spikes; see `docs/research/telegram-voice.md` §"Bidirectional live-PCM on p2p" for the recipe and `docs/skills/telegram.md` (renamed from `comms-telegram.md` under T2.6, 2026-04-23) for the skill design.
- **Still open under this ticket**: (a) panic button, (b) incoming-call auto-answer. Panic button is blocked on T1.4 Stage 4 (ClientEvent); auto-answer is blocked only on skill code + a persona config for the whitelist.
- **Peer-hangup detection shipped** (`43f5e33`, 2026-04-21): three smoke-test bugs fixed together — (1) `ClaimObserver._speaker_pump` now ends the claim with `NATURAL` when the speaker_source exhausts (peer hung up), driving the full teardown chain and sending `input_mode("assistant_ptt")` to the client; (2) on `NATURAL` end, the skill schedules `inject_turn` via `create_task` (NOT `await` — calling synchronously would deadlock on `fm.wait_drained` from inside the FM actor's callback chain) so the LLM narrates "la llamada ha terminado"; (3) `coordinator.on_ptt_start` returns early after `interrupt()` when `active_claim` was True, making PTT a pure hangup gesture — next PTT opens a fresh conversation.
- **PTT beep bug fixed** (`296919e` + `2ab89da`, 2026-04-22): two-commit fix for the continuous thinking-tone after PTT hangup. Root cause: on a local server the round-trip is <5ms — `audio_clear` + `input_mode:assistant_ptt` arrive before the user lifts their finger, so the cancel calls were no-ops; then `pttStop()` started a fresh silence timer with nothing left to cancel it. Fix: capture `pttWasClaimHangup = (inputMode === "skill_continuous")` at `pttStart()` time and skip `startSilenceTimer` in `pttStop()` when the flag is set. Also moved `end_event.set()` in `_observer_on_end` to after the client notifications (race fix), and guarded the post-`wait_drained` `skill_continuous` notification with `if self._claim_obs is observer` (prevents mis-sequenced mode messages when a fast speaker source exhausts during `wait_drained`).
- Call-provider question is RESOLVED: Telegram, not Twilio. No monthly cost, integrates natively with how Mario's family already reaches each other. Keep Twilio as a fallback if Telegram policy ever turns unfriendly to userbots.

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

## T1.11 — `huxley-skill-telegram` — messaging features (send + receive + proactive inbox)

**Status**: done (2026-04-24, smoke-tested live by Mario) · **Task**: — · **Effort**: M (1 day once blockers cleared; was sized at ~1 week pre-Stage-2b)

**Problem.** The Telegram skill ships full-duplex voice calls (inbound + outbound) but no text messaging — no send, no read, no proactive "Carlos sent you a message" turn. The same skill owns the Pyrogram session, the contact list, the auth context, and already feeds the COMMS channel. Messaging has been the obvious completion since calls landed, deferred only because calls were the harder architectural problem and because messaging was misfiled under T1.9 as a provider-neutral abstraction.

**Why it matters.** Completes the first canonical communications skill as a worked example of "one skill, many mechanisms" composing cleanly on the I/O plane. Also the proactive-inbox half is a concrete stress-test for `inject_turn` with dedup semantics — the first real consumer of proactive turns outside timers. And it's the canonical consumer of the `MESSAGE_THREAD` treatment in T2.5 (skill UI plane).

### Validation (Gate 1)

- `huxley-skill-telegram` (renamed from `huxley-skill-comms-telegram` under T2.6) exists, ships, has 42 tests, handles calls in production. Pyrogram session is live; contact resolution works for calls; adding message tools reuses all of that.
- `inject_turn` + `inject_turn_and_wait` just shipped (commits `579e44a`, `ea032e0` — 2026-04-22). Proactive inbound-message announcements were the use case the primitive was designed for but hasn't been built against yet.
- T1.9's 2026-04-21 redesign note already concluded "fold messaging into comms-telegram." That decision stands; T1.11 is just the work ticket that acts on it.
- MESSAGE_THREAD treatment is in the T2.5 v1 treatments list without a concrete consumer — this item is the consumer that validates the treatment's design.

### Design (Gate 2 — sketch; lock before Gate 3)

**Tools (voice-driven):**

- `send_message(contact, text)` — outbound text via Pyrogram userbot. Contact resolution reuses the skill's existing catalog (same fuzzy matcher calls already use).
- `read_unread(from=None)` — read unread messages; optional contact filter. Returns message bodies as narration-ready text.
- `reply_to_last(text)` — contextual reply to the most recently-referenced contact in the current conversation. LLM-side context tracking, no new server state.
- `send_voice_note(contact)` — record + send as voice message. Deferred to Gate-2-+1 pass (needs mic-claim coordination with existing InputClaim plumbing).

**Proactive inbox (background_task):**

- Pyrogram `add_handler` for `MessageHandler` filters inbound from known contacts (whitelist from persona config).
- On inbound message, fire `ctx.inject_turn(prompt=f"Anuncia que {contact} te envió un mensaje: {preview}")` with `dedup_key=f"msg:{contact_id}"` so rapid-fire messages from the same sender don't stack turns.
- Voice notes on inbound: two options — (a) transcribe server-side via Whisper and narrate the text; (b) route audio via InputClaim for passthrough listening. Lock choice at Gate 2 critic. Default to (a) — transcription is simpler, narration is on-brand, and grandpa can still ask "repite" to hear it again.

**UI surface (requires T2.5 shipped):**

- `MESSAGE_THREAD` view per active conversation. Skill emits `ctx.update_view("thread:{contact_id}")` on send + receive.
- Inbox-level `LIST` view of unread-by-contact, tap-to-open-thread.
- Outbound from UI: `Action(tool="send_message", args=...)` from a compose input → same tool path as voice dispatch.

**Shared with calls:**

- One Pyrogram session lifecycle. The current skill's `setup()`/`teardown()` stays authoritative — message handlers register alongside call handlers.
- Contact catalog: already a Catalog primitive consumer. Messaging reads the same catalog, no duplication.

### Critic notes (Gate 2 — completed 2026-04-24)

Critic agent ran against the v1 design (send_message + inbound MessageHandler + inject_turn). Five real findings, all incorporated:

1. **Dedup is "drop in-flight, replace queued" — not "coalesce all"**. Verified at `coordinator.py:1441-1467`: a same-key inject that arrives while another is firing is silently dropped. Per-contact `dedup_key=msg:<user_id>` would lose rapid-fire messages. **Fix**: skill-side debounce buffer (per-contact, configurable seconds; default 2.5s) coalesces bursts into one inject. The `dedup_key` becomes defense-in-depth, not the primary mechanism.
2. **No backfill on restart is a real safety gap for Abuelo**. A 4am crash + missed "¿estás bien, papá?" is invisible to a blind user with no notification surface. **Fix**: bounded backfill on connect — last 6h, max 50 messages, coalesced per-contact, single inject on first idle.
3. **MessageHandler echo loop**. Default filters catch the userbot's own outbound. **Fix**: `filters.private & filters.incoming`.
4. **Unknown-sender silent drop**. Family contacts that haven't messaged the userbot aren't in `_user_id_to_name`; their messages would be dropped silently. **Fix**: announce as `"un número desconocido"` with body, mirroring the inbound-call UX.
5. **MessageHandler registration race**. Must register before `app.start()` or messages arriving in the first seconds are missed. **Fix**: `_wire_peer_audio_handler` renamed to `_wire_handlers`, registers MessageHandler in the same pre-start pass.

Implementation shape decision (UX-driven elegance):

- Pure-logic `InboxBuffer` in new `inbox.py` (per-contact debounce + coalesce, no Pyrogram coupling, unit-testable in isolation).
- Transport stays single-class (one Pyrogram session = one transport).
- Skill stays single-class with thin handlers delegating to the buffer.

Skipped from v1 (file as T1.11.b follow-ups): `read_unread`, `reply_to_last`, voice notes (send + receive), MESSAGE_THREAD UI.

### Definition of Done (locked 2026-04-24)

- [ ] `send_message(contact, text)` tool with Spanish error strings, 4096-char Telegram cap, contact list interpolated into description (mirrors `call_contact`)
- [ ] `TelegramTransport` accepts `on_message` callback, `send_text(user_id, text)`, `fetch_unread(since_seconds, max_messages)`; registers `MessageHandler(filters.private & filters.incoming)` BEFORE `app.start()`
- [ ] New `inbox.py` — `InboxBuffer` class: per-contact debounce + coalesce, configurable debounce window, asyncio-native, no Pyrogram imports
- [ ] Skill `_on_inbound_message`: whitelist lookup → unknown-fallback as `"un número desconocido"` → buffer.add → debounce-flush callback fires `inject_turn(NORMAL, dedup_key=f"msg_burst:{user_id}")`
- [ ] Bounded backfill on connect: last 6h, max 50 messages, per-contact coalesce, single inject on first idle
- [ ] Inbound during active call: queues behind COMMS via existing Stage 2b machinery (regression test asserts no interruption)
- [ ] Tests: `inbox.py` unit (debounce, multi-message coalesce, multi-contact independence, cap, flush_all); `transport.py` (send_text happy/error, fetch_unread filtering, MessageHandler filter set); `skill.py` (send tool happy/contact-not-found/text-empty/text-too-long, inbound known/unknown contact, debounced flush, backfill summary inject, queue-behind-call regression)
- [ ] Docs: `docs/skills/telegram.md` extended with messaging section (debounce + backfill rationale + footguns); `docs/extensibility.md` messaging entry no longer a gap; ADR if any architectural decision crystallized
- [ ] Memory file if a durable lesson emerges (e.g., dedup semantics gotcha)

### Blocked by

- **T1.4 Stage 2b** (complete the COMMS channel — InputClaim migration + pause/resume + concurrent-claim rejection + patience-expiry hook) and **T1.4 Stage 5** (`InjectPriority.BLOCK_BEHIND_COMMS` severity tier). Messaging UX depends on the final focus-plane shape: inbound message announcements inject via `inject_turn(NORMAL)` and need the queue-behind-COMMS-call behavior that Stage 2b establishes; any future urgent-message escalation would use Stage 5's new priority. **Stage 2b and Stage 5 co-land as one commit pair** (Gate 2 decision, 2026-04-23). 2026-04-23 review concluded messaging should land on a complete focus plane.
- **T1.4 Stage 3** (`background_task` supervision helper) for the inbound listener. Can work around with a raw `asyncio.create_task` in the interim — see `docs/extensibility.md` on the unsupervised pattern — but do it properly.
- **T2.5** is a soft dependency: the messaging tools land without UI; MESSAGE_THREAD + inbox views are a follow-up gate after T2.5 ships.

### Depends on / depended on by

- **Depends on** the rename in **T2.6** (already done, 2026-04-23 — `aed1048`).
- **Depends on** Stage 2b + Stage 5 for correct focus-plane semantics.
- **Enables** retirement of the "messaging" gap in `docs/extensibility.md`.
- **Canonical consumer of** MESSAGE_THREAD treatment in T2.5.

### Implementation log (2026-04-24)

**Shape**: three files instead of one. `inbox.py` (new) holds the pure-logic per-sender debounce/coalesce buffer with no Pyrogram or asyncio-loop coupling — unit-testable in isolation against an injected flush callback. `transport.py` gains an `on_message` callback param, `send_text`, `fetch_unread`, and a renamed `_wire_handlers` that registers the Pyrogram MessageHandler before `app.start()`. `skill.py` stays single-class with thin handlers (`_send_message`, `_on_inbound_message`, `_flush_inbox`, `_run_backfill`) delegating buffer logic to the inbox.

**Critic round 2 surfaced 5 must-fixes, all incorporated**:

1. **Straddle-race double-inject (high)**. First cut popped the sender state from `_senders` before spawning the flush task. Messages arriving during the flush would create a fresh state and fire a second independent inject — exactly the bug the buffer was meant to prevent. Fix: state stays resident through the flush, late arrivals append to the same state, post-flush hook starts a follow-up debounce. Locked by a regression test that uses a parked event to slow the flush and assert late arrivals land in a follow-up burst, not a parallel one.
2. **Unknown-sender spam default (high)**. Default was "announce" — opens a DoS vector for an always-on-audio user. Symmetric fix: `inbound.unknown_messages: drop` default (mirrors `auto_answer: contacts_only` for calls), opt-in `announce` for locked-down deployments.
3. **Spanish accents in user-facing strings (high)**. The CLAUDE.md ASCII rule applies to comments/identifiers, not user-facing strings the LLM has to TTS. Without accents the model mispronounces "número" / "envió" / "colgó" / "máximo". Fixed in send_message description, error messages, and call-end inject prompt.
4. **`_end_tasks` not awaited at teardown (high)**. Two task sets for the same purpose (`_tasks` + `_end_tasks`) was smell; teardown only awaited one. Unified into `_tasks` via a single `_spawn_task` helper.
5. **Cosmetic/structural (med/low)**: dropped `dedup_key=msg_backfill` (single-shot inject, no resend path); removed duplicate 4096-char check from transport (skill is sole authority + has the nice Spanish error); added `transport.is_in_call` public property; skipped MessageHandler registration when `on_message=None`; slice instead of `del` for the per-sender cap.

**Definition of Done check**:

- [x] `send_message(contact, text)` tool with Spanish error strings, 4096 cap, contact list interpolated into description
- [x] `TelegramTransport` accepts `on_message` callback, `send_text`, `fetch_unread`; registers `MessageHandler(filters.private & filters.incoming)` before `app.start()`
- [x] New `inbox.py` — `InboxBuffer` class with straddle-safe per-sender debounce/coalesce
- [x] Skill `_on_inbound_message`: whitelist lookup → unknown-sender drop-by-default (announce opt-in) → buffer.add → flush callback fires `inject_turn(NORMAL, dedup_key=msg_burst:<user_id>)`
- [x] Bounded backfill on connect (default 6h, 50 msg cap), per-contact coalesce, single inject on first idle
- [x] Inbound during active call: queues behind COMMS via existing Stage 2b machinery (NORMAL priority test asserts the `dedup_key=msg_burst` shape)
- [x] Tests: 90 telegram (27 inbox + 48 skill + 15 transport — grew during smoke-test fixes for backfill body inclusion + body cap)
- [x] Docs: `docs/skills/telegram.md` extended with messaging section + debounce rationale + footguns; `docs/extensibility.md` messaging entry rewritten (no longer a gap)
- [x] Browser smoke test (Mario, 2026-04-24): verified live message read-aloud, the new instruction-style prompt, and the post-restart backfill flow with bodies. Mario's verdict: "It actually seems to work really well!"

**Smoke-test surfaced 3 more issues beyond the critic round** (2026-04-24):

1. **The inject prompt was a bare fact, not an instruction**. First live message: the LLM treated `"mario te dijo: 'X'"` as a notification ("Got it, do you want to reply?") and never read the body aloud. Fix: rewrote `build_announcement` and `build_backfill_announcement` as explicit instructions ("Léeselo al usuario tal cual y pregúntale si quiere responder"). Pattern matches the working `_on_claim_end` call-ended inject. The lesson is general: **inject prompts that include user-relevant CONTENT need explicit "léeselo al usuario" framing** — bare statements get treated as silent notifications.
2. **Backfill fired before the OpenAI Realtime session was ready**. Setup completed at T+0, backfill `inject_turn` fired at T+0.3s, OpenAI session connected at T+0.5s. The inject acquired focus but `session.tx.conversation_message` never followed — the inject was effectively lost. Fix: added a 5s delay (`_BACKFILL_STARTUP_DELAY_S`) before the backfill `inject_turn` so the session has time to connect. Live messages don't have this problem — they arrive only after the user is already interacting (session is up by definition).
3. **Backfill prompt offered to read messages but didn't include the bodies**. LLM said "Tienes 1 mensaje, ¿quieres que te lo lea?" but had nothing to read on follow-up. Fix: changed `build_backfill_announcement` to take `dict[str, list[str]]` (per-sender bodies, not just counts) and inline the bodies in the prompt with a per-sender cap of 5 to keep the prompt size sane.

**Lessons captured to memory**: the dedup-in-flight semantics + skill-side debounce/coalesce pattern. See [`feedback_inject_dedup_in_flight.md`](file:///Users/mario/.claude/projects/-Users-mario-Projects-Personal-Code-Huxley/memory/feedback_inject_dedup_in_flight.md). Also: inject prompts as instructions, not facts — see [`feedback_inject_prompts_are_instructions.md`](file:///Users/mario/.claude/projects/-Users-mario-Projects-Personal-Code-Huxley/memory/feedback_inject_prompts_are_instructions.md).

**Note on `_run_backfill` task supervision**: T1.4 Stage 3 `background_task` supervision was listed as a blocker, but the backfill is fire-once per session (not a long-lived loop) so a tracked `asyncio.create_task` via the skill's `_spawn_task` helper is sufficient. Persisting backfill state across restarts isn't required because Telegram's own unread cursor is the source of truth — every restart re-reads what's actually unread per Telegram, no skill-side bookkeeping needed.

## T1.12 — Session history persistence + retrieval

**Status**: done (2026-04-30; pending Mario browser smoke as the final DoD gate) · **Commits**: `3e52bff7` (entry filed) → `77394c6c` (storage v2 + gold test) → `4d92d94d` (provider→app handoff race fix) → `2588057e` (server protocol + docs/protocol.md) → `96865ef2` (PWA + docs/decisions.md ADRs) · **Effort actual**: ~1 day across server + PWA, larger than the ~½+½ estimate (the critic round expanded scope: resume-window logic, callback-signature refactor, privacy-floor delete path).

### Lessons

- **The critic round paid for itself, again.** Three of the four locked design changes came from the critic, not the original sketch. Auto-reconnect fragmentation alone would have shipped a feature that demoed cleanly and degraded into nonsense in week one of real use. Do not skip Gate 2 on design-shaped items — the cost is one agent call.
- **Boundary semantics ≠ technical lifecycle.** WS-connect/disconnect was the obvious "session" definition; it was wrong. The user's mental model is one of conversation continuity across reconnects. Whenever the data model surfaces to a user, ask: is the technical boundary the same as the user-visible boundary? If not, name them separately. (Logged as the 2026-04-30 ADR in `docs/decisions.md`.)
- **`_pending_summary` was a cleaner fix than guards-and-flags.** Original instinct was to add `_end_fired` flags and gate both the receive-loop's finally and `disconnect()` against double-firing. The cleaner pattern Mario wrote: compute summary BEFORE cancelling the receive task, stash on `self._pending_summary`, finally reads + clears. Single firing path, no flag race. Worth remembering when teardown sequencing comes up again.
- **Test-first paid more than usual.** The gold integration test (the critic's "single regression catch-all") was written red BEFORE any storage code. Watching it fail with `AttributeError: 'Storage' object has no attribute 'start_or_resume_session'` made the API contract concrete. The test stayed unchanged through implementation; nothing drifted.
- **Parallel collaboration with Mario worked well.** I did storage + protocol + tests; Mario did the app-layer wiring + provider race fix in parallel. Coordination via the locked DoD bullets and the triage entry — both of us could see the same contract. No churn.
- **Mid-task collaboration risk to watch:** I almost overwrote Mario's in-flight `app.py` edits when I read working-tree state and saw 4 files I didn't touch. The instinct to commit ALL pending changes is wrong when there are two collaborators. Default to `git add <specific-file>` not `git add -A`.

**Effort**: in_progress (2026-04-29) → done (2026-04-30) · ~1 day actual

**Problem.** The PWA's `SessionsSheet` displays three hardcoded sample conversations. The server has no notion of a "session" beyond a single `conversation_summary` row that gets overwritten on every disconnect. Users cannot browse what they talked about previously, even though the UI clearly promises they can.

**Why it matters.** Once Huxley is used regularly, looking back at past conversations is a core product expectation — particularly for the AbuelOS persona, where a caregiver may want to review what the elderly user discussed (medication reminders heard, news topics covered, calls placed). The current UI is a lie; the gap is one of the four flagged in the 2026-04-29 PWA-vs-server audit (Phase A/B of which shipped in commit `1ec47ab3`).

### Validation (Gate 1)

`clients/pwa/src/components/SessionsSheet.tsx` consumes a `sessions: Session[]` prop. `clients/pwa/src/App.tsx:163-189` populates that prop with a hardcoded array sourced from i18n samples. There is no WebSocket message in either direction to list or fetch sessions — verified via `grep "session" server/runtime/src/huxley/server/server.py` and the protocol table in `docs/protocol.md`. Storage today persists only `conversation_summaries` (single-row-overwrite-on-disconnect), used to inject context on warm reconnect — not a browsable history.

### Design (Gate 2 — locked 2026-04-29 after critic round)

**Schema v2** in `server/runtime/src/huxley/storage/db.py`:

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at     TEXT,
    last_turn_at TEXT,                  -- updated on each record_turn; for resume-window check
    turn_count   INTEGER NOT NULL DEFAULT 0,
    preview      TEXT,                   -- first user-role turn, truncated; null until a user turn lands
    summary      TEXT
);
CREATE TABLE session_turns (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('user','assistant')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, idx)
);
CREATE INDEX idx_session_turns_session ON session_turns(session_id);
```

Migration: every row in `conversation_summaries` becomes a synthetic `sessions` row with `summary` set, `started_at = created_at`, `ended_at = created_at`, `turn_count = 0`. Drop `conversation_summaries`. Migration is inline in `_init_schema_version` keyed off `schema_version < 2` — first migration shipped, no generic runner yet (see deferred T2.1 follow-up).

**Conversation continuity via resume window.** New storage method `start_or_resume_session(idle_window_min: int = 30) -> int`:

- If the most recent session has `last_turn_at >= now - idle_window`, return its id (resume).
- Else, INSERT a new row.

This collapses fragmenting WS reconnects (auto-reconnect, language switch, cost kill, browser refresh) into one user-visible conversation. WS-connect/disconnect remains the technical lifecycle; the user-visible "conversation" is a separate logical unit grouped by idle gap.

**Storage API** (added to `Storage` class):

- `start_or_resume_session(idle_window_min: int = 30) -> int`
- `record_turn(session_id, role, text)` — appends, updates `last_turn_at`, increments `turn_count`, sets `preview` lazily on first user turn
- `end_session(session_id, summary: str | None)` — sets `ended_at` + `summary`; idempotent across reconnects (each end overwrites)
- `list_sessions(limit: int = 50) -> list[SessionMeta]` — DESC by id, all sessions (including any "live" one — UI handles)
- `get_session_turns(session_id: int) -> list[Turn]` — turns in idx order
- `delete_session(session_id: int)` — removes row + cascades turns (privacy floor)
- `get_latest_summary()` — repointed: `SELECT summary FROM sessions WHERE summary IS NOT NULL ORDER BY id DESC LIMIT 1`
- `clear_summaries()` — `UPDATE sessions SET summary = NULL` (preserves session metadata, breaks LLM context chain). Used by `_on_reset` dev tool.
- Drop: `save_summary` (provider stops calling it; replaced by app-owned `end_session`).

**Capture pipeline** at `app.py`. Tap point is `_on_transcript(role, text)` (line 454):

```python
async def _on_transcript(self, role, text):
    if self._active_session_id is None:
        self._active_session_id = await self.storage.start_or_resume_session()
    await self.storage.record_turn(self._active_session_id, role, text)
    await self.server.send_transcript(role, text)
```

`_on_session_end` finalizes:

```python
async def _on_session_end(self, summary: str | None):  # NEW: summary param
    if self._active_session_id is not None:
        await self.storage.end_session(self._active_session_id, summary)
        self._active_session_id = None
    await self.coordinator.on_session_disconnected()
    ...
```

**Critical: provider callback signature changes.** `voice/provider.py` `on_session_end: Callable[[], Awaitable[None]]` → `Callable[[str | None], Awaitable[None]]`. The OpenAI provider already generates the summary in `disconnect()`; pass it through the callback instead of round-tripping via storage. Provider stops calling `_storage.save_summary(summary)` — that path is replaced by app-owned `end_session(session_id, summary)`. Fixes a race where `_on_session_end` (firing from receive-loop `finally`) read the previous session's summary and attached it to the new row.

**Protocol additions — additive, no version bump.** Stays at protocol 2. ESP32 has zero use for sessions; bumping forces lockstep cost across every client for a PWA-only feature. Old clients ignore unknown types (already documented in `docs/protocol.md`).

- Client→server: `list_sessions` (no payload), `get_session { id }`, `delete_session { id }`
- Server→client: `sessions_list { sessions: [SessionMeta] }`, `session_detail { id, turns: [Turn] }`, `session_deleted { id }`

`SessionMeta` shape: `{ id, started_at, ended_at, last_turn_at, turn_count, preview, summary }` (raw ISO strings — client formats relative time).

**PWA wiring**: `useWs` adds `sessionsList` + `sessionDetail` state and `listSessions()` / `getSession(id)` / `deleteSession(id)` methods. `SessionsSheet` consumes `ws.sessionsList`; replaces hardcoded array. Click → triggers `getSession(id)` → opens new `SessionDetailSheet` showing read-only transcript with a "Delete" button.

### Critic Notes

Critic round 2026-04-29 (general-purpose agent, fresh context, full design + file refs). Verdict was strong: design ships a feature that works in a demo and falls apart in real usage. Findings + dispositions:

- **Auto-reconnect fragmentation** — WS-connect = session is wrong; auto-reconnect/language-switch/cost-kill creates many tiny rows for one logical conversation. **Incorporated**: `start_or_resume_session(idle_window=30min)`.
- **Summary attribution race** — `_on_session_end` fires from receive-loop `finally`, BEFORE provider's `disconnect()` writes the new summary. As designed, every row would have the previous session's summary. **Incorporated**: changed `on_session_end` callback signature to pass `summary: str | None` directly; app owns the write.
- **Schema over-normalized** — argued JSON-blob would be simpler since we don't query inside transcripts. **Rejected**: blob means transcripts persist only on disconnect; a 90-min conversation that crashes mid-flight loses everything. Separate `session_turns` table writes incrementally → per-turn durability. Worth the join.
- **Protocol bump unnecessary** — ESP32 doesn't need sessions; bump forces lockstep across clients. **Incorporated**: stays at protocol 2; additive types only.
- **Lazy start breaks proactive turns** — first transcript may be `role=assistant` (proactive turn before user engages); `preview = first user turn` invariant violated. **Incorporated**: `preview` set lazily only on first user turn; null until then. Session row still created on first transcript (any role), so proactive turns are captured.
- **Privacy/retention** — caregiver-review use case stores PII unencrypted with no delete UI. **Incorporated** (floor): `delete_session(id)` + `clear_summaries()` from day one; PWA delete button on detail sheet. **Deferred** (documented in `docs/decisions.md`): retention window, encryption-at-rest, multilingual columns on `session_turns`, transcript-accuracy disclaimer.
- **Telegram calls / messages aren't captured** — caregivers reviewing the day will see no record of a 4-min Telegram call. **Deferred**: out of scope; tracked under T1.10/T1.11. Schema is extensible (could add `kind` column later).

### Definition of Done (locked)

- [ ] Schema v2 ships; v1 DBs migrate cleanly on startup with no data loss in `conversation_summaries`.
- [ ] **Gold integration test passes** (the test the critic flagged as the "single regression catch-all"): connect → 2 user turns → disconnect → reconnect within idle window → 1 more user turn → disconnect. Assert: `list_sessions` returns ONE row, transcript contains all 3 user turns in order, `summary` is the one written on the second disconnect.
- [ ] Storage unit tests for every new method including `start_or_resume_session` (resume-within-window AND new-session-after-window cases) and the migration step.
- [ ] `provider.on_session_end` signature change: `() → (str | None)`. All call sites and tests updated.
- [ ] Reset (`_on_reset`) preserves session list, nullifies summaries (verified: subsequent `get_latest_summary` returns None until next disconnect populates one).
- [ ] `delete_session(id)` removes the row + cascades its turns. PWA delete button works end-to-end.
- [ ] Proactive turn (assistant-initiated, no user reply) captures the session row with `preview = NULL`; PWA renders a fallback ("Started by {persona}").
- [ ] PWA: `EXPECTED_PROTOCOL` stays at 2. `SessionsSheet` consumes `ws.sessionsList` (no hardcoded samples). Click opens `SessionDetailSheet` with the actual transcript. `bun run check` green.
- [ ] `ruff check server/` + `mypy server/sdk/src server/runtime/src` + per-package pytest all green.
- [ ] `docs/protocol.md` updated with the six new message types (additive section, no version bump).
- [ ] `docs/decisions.md` ADR added documenting: (a) WS-connect vs logical-conversation boundary choice and the resume-window heuristic; (b) deferred items (retention, encryption, multilingual, transcript-accuracy, Telegram-call capture).
- [ ] Mario browser smoke: open SessionsSheet → see real list (or empty state) → after a real conversation, refresh → new entry appears → click → transcript shows → delete → entry vanishes.

### Implementation order

1. Write the gold integration test first (red).
2. Storage schema v2 + migration + new methods + storage tests (green for storage layer).
3. Provider `on_session_end` callback signature change + callsite updates + provider tests.
4. App layer wiring (`_on_transcript` lazy create, `_on_session_end` finalize).
5. Server protocol handlers (six new message types).
6. PWA: useWs + SessionsSheet + SessionDetailSheet.
7. Docs: `protocol.md` + `decisions.md`.
8. Mario smoke + close gate.

## T1.13 — Hot persona swap (single server, multi-persona)

**Status**: in_progress (2026-05-01) · **Effort**: ~3–4 days (server refactor, PWA rewire, critic round, tests, docs)

**Problem.** Today, switching personas requires running multiple server processes (one per persona) on different ports and listing each as a `name:url` pair in `VITE_HUXLEY_PERSONAS`. The PWA picker just chooses which port to talk to. That's a deployment artifact leaking into UX: an end user shouldn't have to start terminals or manage ports to switch the assistant's "face."

**Why it matters.** Persona-switching is a first-class user-visible product affordance, not a re-deployment. For the AbuelOS-target end user (elderly, blind), "open a terminal" is not a workflow — and even for self-hosted dev users, the multi-process model is wrong because it scales linearly with persona count. The architecture today binds `Application` to "the one persona this process can serve." That binding is the bug.

### Validation (Gate 1)

- `server/runtime/src/huxley/__main__.py:50` constructs exactly one `Application(config, persona)` per process, where `persona` is resolved at startup via `resolve_persona_path` + `load_persona`. There is no in-process swap path.
- `server/runtime/src/huxley/app.py:101` builds `self.storage = Storage(persona.data_dir / f"{persona.name.lower()}.db")` — storage is constructor-bound to the persona, no rebind.
- `server/runtime/src/huxley/app.py:104-113` constructs `AudioServer(...)` inside `Application.__init__`. The TCP listener is owned by the persona-bound app, so a swap can't preserve the connection.
- `clients/pwa/src/App.tsx:25-49` reads `VITE_HUXLEY_PERSONAS` env var and parses `name:url` pairs; PWA connects to one URL per persona. `useWs.switchPersona(url)` (`clients/pwa/src/lib/useWs.ts:369-381`) closes the WS and opens a new one to a different URL.
- `server/personas/` has 5 personas shipped (abuelos, basicos, buddy, chief, librarian) — all functional; the only thing standing between them and the user is the deployment shape.

### Design (Gate 2 — locked 2026-05-01 pending critic round)

**The user's mental model is the design's north star.** Huxley is one device. It currently wears the abuelos face; the user wants it to wear basicos. They expect a face change, not a reincarnation. The WebSocket connection — the user's "phone line to the device" — survives. The persona — the device's "soul" — changes.

**Topology — `Runtime` above `Application`:**

```
Runtime (process singleton)
├── AudioServer            ← single TCP listener, lifelong
├── PersonaRegistry        ← enumerates server/personas/<name>/persona.yaml at startup
├── default_persona: str   ← from HUXLEY_PERSONA env, or auto-pick
└── current_app: Application
    ├── Storage              ← per-persona DB
    ├── SkillRegistry, Provider, Coordinator, FocusManager, ...
```

`AudioServer` lifts from `Application` to `Runtime`. Its callbacks indirect through `Runtime`, which forwards to `runtime.current_app`. The TCP listener stays bound across persona swaps; the in-process state behind it changes.

**Wire protocol — additive, but bumps to v3** because `hello`'s shape changes meaningfully:

- Server→client at `hello`: `{ type: "hello", protocol: 3, current_persona: "abuelos", available_personas: [{name, display_name, language}, ...] }`. PWA discovers personas at runtime; `VITE_HUXLEY_PERSONAS` env var dies.
- Client→server: `{ type: "select_persona", name: "basicos" }`.
- Server→client: `{ type: "persona_changed", persona: "basicos" }` after the swap commits.
- Status frames narrate the swap user-visibly: `"Cambiando a basicos…"` → swap earcon → `"Listo"`. Hardware clients TTS-speak the status; the PWA renders it in the orb status line.

**Why v3 not additive-on-v2:** the meaning of "what's at this URL" fundamentally changes. Pre-T1.13: one URL = one persona. Post-T1.13: one URL = a runtime that hosts any persona at the registry's discretion. An old client + new server combination would parse the new `hello` fields as unknown and fall back to the old `switchPersona(url)` path, which now silently fails because there's only one URL. Bumping forces version mismatch to surface at the handshake.

**Swap algorithm — pre-validate, then commit, then teardown:**

```python
async def select_persona(self, name: str) -> None:
    if self.current_app and self.current_app.persona.name == name:
        return  # no-op

    if self._claim_or_stream_active():
        await self._send_status_for_locale("end_call_first")
        return  # PWA also gates the picker; server enforces too.

    self._swap_lock.engage()  # drops audio frames + ptt events for ~1s
    await self._send_status_for_locale("switching_to", persona=name)
    await self._play_swap_earcon()

    new_persona = self._registry.load(name)
    new_app = Application(self._config, new_persona)
    try:
        await new_app.start()  # light: storage init + skill setup, no OpenAI yet
    except Exception:
        await new_app.shutdown()  # cleanup partial init
        await self._send_status_for_locale("switch_failed", persona=name)
        self._swap_lock.release()
        raise  # OLD app is intact, untouched

    # Atomic swap. Single Python assignment.
    old_app, self.current_app = self.current_app, new_app

    await self._send_persona_changed(name)
    await self._send_state(AppState.IDLE)  # next user PTT opens new OpenAI session
    self._swap_lock.release()

    # Teardown old in background — don't block the user.
    # Old's provider.disconnect(save_summary=True) writes the summary into
    # the OLD persona's DB via the T1.12 path.
    asyncio.create_task(old_app.shutdown())
```

**Why pre-validate:** if the new persona fails to start (skill error, DB corrupt, persona.yaml bad), the OLD app is still alive — abort with a status frame, keep the user's session. The current `Application` constructor is sync; `start()` is async-light (storage init + skill setup, no OpenAI session). Failure surfaces in <1s and stays contained.

**Atomicity:** the swap is a single Python assignment. AudioServer reads `runtime.current_app` once per event; even without locks, you get either old or new but never half-built. `SwapLock` suppresses noise during the ~1s rebuild — it's a noise gate, not a correctness primitive.

**OpenAI session on swap — lazy.** New persona's session opens on the next `wake_word`. Mirrors existing semantics: user PTTs → CONNECTING → CONVERSING with new persona. No double-billing race, no rate-limit overlap. Tradeoff: ~500ms latency on the first PTT after swap. Worth it for code simplicity.

**Active-claim/stream gating:** the PWA's persona picker is disabled while `activeClaimId !== null` or `activeStream !== null`. Tooltip: `"Termina la llamada actual primero"`. Server enforces too — sends `error` status if a swap is requested mid-claim. The contract is symmetrical: client can't request, server can't act.

**Audible UX (blind-user floor):** new earcon `persona_swap.wav` rendered by `scripts/synth_sounds.py` and added to `server/personas/_shared/sounds/`. Plays during teardown, distinct from existing earcons (book_start/end, news_start, etc.). Status frames must be persona-aware so they speak in the **new** persona's language post-swap.

**Default persona:** `HUXLEY_PERSONA` env var becomes "default on first connect when no in-band selection has happened yet." Auto-discovery (single-persona dirs) still works for dev-tier setups.

### Critic Notes

_To be filled in after the Gate 2 critic round (next step before any code)._

### Definition of Done (locked pending critic)

- [ ] `Runtime` class exists; owns `AudioServer` + `PersonaRegistry` + `current_app`.
- [ ] `Application` constructor unchanged shape; `Application.shutdown()` audited as complete (every resource released cleanly so subsequent swap doesn't leak).
- [ ] `Application.start()` is light — storage init + skill setup only, no OpenAI session.
- [ ] `PersonaRegistry.list() -> list[PersonaSummary]` enumerates `server/personas/<name>/persona.yaml` at startup; `PersonaRegistry.load(name) -> PersonaSpec` resolves on demand.
- [ ] `Runtime.select_persona(name)` implements the pre-validate-then-commit-then-background-teardown algorithm.
- [ ] Protocol bumped 2→3. `hello` carries `current_persona` + `available_personas`. `select_persona` / `persona_changed` round-trip works end-to-end.
- [ ] PWA: `VITE_HUXLEY_PERSONAS` removed (deleted from any `.env*` and from `App.tsx`). Persona picker reads `ws.availablePersonas` + `ws.currentPersona`. `selectPersona(name)` sends in-band; updates on `persona_changed`. Picker disabled during `activeClaimId || activeStream`.
- [ ] PWA `EXPECTED_PROTOCOL = 3`.
- [ ] `persona_swap.wav` rendered + checked into `server/personas/_shared/sounds/`.
- [ ] **Gold integration test passes**: connect with no persona param → server uses default → `select_persona` to a different name → `persona_changed` arrives → `current_app.persona.name` reflects new → original persona's storage DB still contains its summary, new persona's storage is independent.
- [ ] **Failure-mode test passes**: `select_persona` to a name whose `start()` raises → status frame fires, OLD app still serves the session, no half-built state.
- [ ] **Mid-claim test passes**: `select_persona` while `_claim_or_stream_active()` returns true → server rejects with status frame, no swap occurs.
- [ ] `ruff check server/` + `mypy server/sdk/src server/runtime/src` + per-package pytest all green.
- [ ] `docs/protocol.md` updated for v3 + new message types. `docs/architecture.md` updated for Runtime topology. `docs/decisions.md` ADR added documenting the in-band-swap call (vs. reconnect) + the connection-persists-soul-changes principle.
- [ ] Mario browser smoke: PWA shows all 5 shipped personas in the picker; tap one → audible swap → orb pulses through → new persona greets on first PTT; reset works on each persona; sessions persist per-persona.

### Implementation order

1. **Critic round (Gate 2)** before any code. Spawn fresh agent with this entry + design + relevant file refs. Lock DoD after.
2. **`PersonaRegistry`** — thin wrapper over filesystem enumeration. New file `server/runtime/src/huxley/persona_registry.py` (or extend `persona.py`).
3. **Audit `Application.shutdown()`** — verify completeness; add explicit teardown for any resource that's currently process-lifelong-by-convention. Idempotency tests.
4. **Lift `AudioServer`** from `Application` to a new `Runtime` class. `Application` accepts callbacks externally instead of constructing `AudioServer`. Refactor entrypoint (`__main__.py`) to construct Runtime → Runtime constructs first Application.
5. **Swap algorithm** in `Runtime.select_persona`. Unit test with stub providers.
6. **Protocol additions** — `select_persona` / `persona_changed` in `server.py`, `hello` shape change, version bump 2→3.
7. **Render swap earcon** via `scripts/synth_sounds.py`.
8. **PWA rewire** — drop env var, consume server-pushed list, in-band `selectPersona`, gate picker, bump `EXPECTED_PROTOCOL`.
9. **Docs** — `protocol.md`, `architecture.md`, `decisions.md` ADR.
10. **Tests** — unit on swap algorithm, integration on round-trip, failure-mode, mid-claim rejection.
11. **Mario smoke + close gate.**

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

`server/runtime/tests/unit/test_storage.py` → `TestWalAndSchemaVersion`:

- `test_journal_mode_is_wal`
- `test_schema_version_recorded_on_fresh_db`
- `test_schema_version_idempotent_on_reinit`
- `test_schema_version_mismatch_logged_not_crashed`

`server/runtime/tests/unit/test_storage_backup.py` → `TestEnsureDailySnapshot`:

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
  Abuelo daily-driver pattern; per-persona override can be added when a
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

`server/runtime/tests/unit/test_cost.py`:

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

`server/runtime/tests/unit/test_openai_realtime_event_handler.py`:

- `TestHandleAudioDelta` — base64 decode + dispatch
- `TestHandleFunctionCall` — args parse + malformed-JSON fallback
- `TestHandleTranscript` — assistant + user role routing
- `TestHandleError` — silent-cancel + commit-empty + other-codes paths
- `TestHandleResponseDone` — audio.done + response.done with/without
  usage + cost-tracker exception isolation
- `TestHandleUnknownEvents` — unknown event types are silent no-ops

`server/runtime/tests/integration/test_session_replay.py`:

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

## T2.5 — Skill UI plane (treatments model + capability-based clients)

**Status**: queued (low priority) · **Task**: — · **Effort**: L (~4–6 weeks; do not start until T1.4 stages complete and skill APIs have stabilized)

**Problem.** Skills today are voice-only. Huxley-web is framed as a dev tool. The framework has no vocabulary for a skill to declare UI that renders on UI-capable clients and is absent on audio-only clients (ESP32). Without this layer, huxley-web stays a debug surface, and the "install a skill, get the full skill" story the README promises is half-finished — a skill installs its Python package and voice tools but cannot install its UI at the same time.

**Why it matters.** Parallel to the I/O plane, but for visual output. Completes the per-skill distribution story. Load-bearing constraint: whatever lands must work for voice-only clients (ESP32) without skill authors having to think about it, and must not drag a TypeScript toolchain onto Python skill authors.

### Validation (Gate 1)

Shape of the gap, confirmed in a design conversation (2026-04-23) walking audiobooks, radio, news, and timers through a hypothetical UI layer. Each surfaces distinct requirements:

- **Audiobooks**: browseable library + now-playing with scrub
- **Radio**: station list + live-stream player without scrub
- **News**: at-a-glance weather card + persistent headlines list + per-item non-tool actions (open source URL)
- **Timers**: multi-instance live countdowns + proactive firing event (voice + visual + ephemeral toast)

Identified that a primitives-based declarative model (Text/Button/List at HTML-atom altitude) reproduces APL's complexity without value. Treatment-based model — interaction archetypes (`CATALOG`, `MEDIA_PLAYER`, `TIMER_BOARD`, etc.) — matches the altitude Huxley already uses for FocusManager channels and I/O plane primitives.

Existing skills have visible UI pressure already: audiobooks has no way to show what's playing, timers has no way to show live countdowns, news has no way to persist headlines past narration. Adding these one-off to huxley-web would entrench it as a debug surface and fork skill implementations.

### Design (Gate 2 — sketch locked 2026-04-23; critic review pending before work starts)

**Locked premises** (product answers, Mario 2026-04-23):

1. **huxley-web target user**: general power users who want a customizable assistant. Not elderly-specific; caregiver/grandpa UX is a persona-level concern, not a product default. Information density is OK; literate, sighted, engaged user assumed.
2. **One client at a time**: server keeps the `1008` second-connection rejection. "Dashboard while another client is active" is explicitly out of scope. Revisit trigger: first concrete multi-client use case.
3. **Audio + UI split via capability handshake**: protocol uses capability-based handshake (`{"capabilities": ["audio", "ui"]}`); huxley-web ships with both capabilities; ESP32 advertises only `["audio"]`. Splitting huxley-web into separate audio-only and ui-only builds deferred until a real use case forces it.

**Core model — four concepts, each doing one job:**

1. **Treatments** — a curated library of interaction archetypes owned by huxley-web. Each treatment is a rich React component with its own behavior (live countdown, scrub interpolation, artwork). Adding a treatment is an ADR-level decision, same cadence as adding a FocusManager channel.

   v1 set:

   | Treatment        | Cardinality                | Interaction shape                                                           |
   | ---------------- | -------------------------- | --------------------------------------------------------------------------- |
   | `CATALOG`        | 1 view contains N items    | Browseable library with now-playing state                                   |
   | `MEDIA_PLAYER`   | 1 per view                 | Now-playing card: artwork, title, scrub (or live badge), transport controls |
   | `LIST`           | 1 view contains N items    | Flat items with optional check/progress state                               |
   | `DASHBOARD_CARD` | 1 per view                 | At-a-glance read: focal datum + supporting lines + freshness                |
   | `TIMER_BOARD`    | 1 view contains N timers   | Running countdowns with per-timer controls                                  |
   | `CALL`           | 1 per view                 | In-call state with mute/hangup and partner identity                         |
   | `MESSAGE_THREAD` | 1 view contains N messages | Chronological message list with compose affordance                          |

2. **Views** — stateful surfaces a skill declares. Each view renders one treatment instance. Skill calls `ctx.update_view(view_id)` after state changes; framework diffs and pushes to subscribed clients.

3. **Actions** — two kinds. `Action(tool=..., args=...)` dispatches a tool call (same path as LLM). `Action(kind="open_url" | "copy" | ...)` is client-local. Full client-local kind vocabulary locked at Gate 2.

4. **Notifications** — ephemeral events. Not views. `ctx.notify(Notification(...))` fires a toast; stacks with auto-dismiss. Sibling to `PlaySound` (one-shot) vs `AudioStream` (stateful view). Separation load-bearing because views are replaceable state and notifications are stacking events with auto-dismiss — different lifecycles.

**Protocol additions** (five new messages, zero existing changes):

- `view.render` (server → client): full treatment tree for a view
- `view.update` (server → client): partial patch
- `view.action` (client → server): UI tap → tool dispatch or client-local action
- `notification` (server → client): ephemeral alert
- `notification.ack` (client → server): user dismissed

**SDK additions:**

- `ViewDefinition`, `View`, `Action`, `Notification` types
- `Treatment` enum (fixed in v1; dynamic treatments explicitly disallowed — forces intentionality)
- `ctx.update_view(view_id)` and `ctx.notify(notification)` on `SkillContext`
- `Skill.views` property + `Skill.render_view(view_id)` method (symmetric with existing `tools` + `handle`)

**Worked example (audiobooks):**

```python
@property
def views(self) -> list[ViewDefinition]:
    return [
        ViewDefinition(id="library", treatment="CATALOG", title="Biblioteca"),
        ViewDefinition(id="player",  treatment="MEDIA_PLAYER"),
    ]

async def render_view(self, view_id: str) -> View:
    if view_id == "library":
        return Catalog(items=[
            Item(id=b.id, title=b.title, subtitle=b.author,
                 badge="En curso" if b.id == self._now_playing else None,
                 action=Action(tool="play_book", args={"id": b.id}))
            for b in self._catalog
        ])
    if view_id == "player":
        ...  # MediaPlayer instance or MediaPlayer.idle()

# After tool-handler state change:
await self._ctx.update_view("library")
await self._ctx.update_view("player")
```

Tool-handler actions dispatched from UI buttons go through the same path as LLM-dispatched tools (symmetric). Live ticking (countdown, scrub) is local to the treatment component on the web side — `expires_at` pushed once, clock arithmetic in the browser.

### Open questions (belong in the entry; resolve at Gate 2 critic or during implementation)

- **Action kind vocabulary** — full v1 set beyond `tool`, `open_url`. Candidates: `copy`, `share`, `dismiss`, `notification_ack`. Lock before SDK types ship.
- **View lifecycle** — does `MediaPlayer` render idle when nothing plays, or hide? Skill-decides or web-decides? Surface at first retrofit.
- **Treatment versioning** — loose-JSON contract (unknown fields ignored, missing fields defaulted), document explicitly so skill and web-client versions can drift independently.
- **I18n** — all display strings are skill-provided; treatments own zero copy (no built-in "No items" fallbacks — skill passes `empty_text`). Same treatment works for Abuelo (es) and Basic (en).
- **Authentication** — making huxley-web a product client exposes views over the network. Currently `:8765` accepts any connection. Out of scope for this item, but named as a blocker for any public-network deployment.
- **Accessibility** — best-effort screen-reader support, not a design driver (premise #1 above).
- **Mobile/responsive** — treatments responsive; skills provide content not layout. Web-client implementation concern, not SDK.
- **Escape hatch (custom web components, HA-style)** — explicitly deferred. Revisit trigger: a real skill hits a wall the v1 treatment set cannot model.

### Critic notes (Gate 2 — pending)

Critic must run **before** Gate 3 begins. Prompt should specifically test:

1. Is seven treatments enough for the v2 skill roadmap (podcasts, Spotify, hue, calendar, tasks, messaging, telegram `CALL`)? If any requires a new treatment, the initial count is wrong.
2. Does "view renders one treatment instance" hold universally, or does a real skill need multiple treatments composed in one view? (News uses two views, one treatment each — does that generalize?)
3. Is the tool-call vs client-local action split clean, or does it create two parallel dispatch paths that will desync?
4. Is the "notifications are not views" separation load-bearing, or a distinction without a difference that adds API surface?
5. What assumption does the design make about network reliability? View subscriptions over WebSocket have no retry/catch-up story yet.
6. Is the one-client-at-a-time constraint (premise #2) actively helping, or just kicking the can? What breaks when it's eventually lifted?
7. Does "Treatment enum fixed in v1" prevent legitimate experimentation by skill authors, or is the friction the point?

### Definition of Done (unlocked — finalize at Gate 2 critic)

Placeholder DoD for sizing; critic will refine:

- [ ] Capability handshake in protocol; server gates message delivery by advertised capability
- [ ] Five new WebSocket message types defined and documented in `docs/protocol.md`
- [ ] SDK types: `ViewDefinition`, `View`, `Action`, `Notification`, `Treatment` enum, per-treatment data classes
- [ ] `ctx.update_view()` and `ctx.notify()` on `SkillContext`
- [ ] Treatments implemented in huxley-web for v1 retrofits: `CATALOG`, `MEDIA_PLAYER`, `LIST`, `DASHBOARD_CARD`, `TIMER_BOARD`
- [ ] Three skill retrofits: audiobooks (`CATALOG` + `MEDIA_PLAYER`), news (`DASHBOARD_CARD` + `LIST`), timers (`TIMER_BOARD` + `Notification`)
- [ ] ESP32-shape test client that advertises only `["audio"]`; verifies no UI traffic reaches it
- [ ] Tests: SDK contract tests for view types; per-skill view-render tests; protocol capability-gating tests; web-client treatment rendering from fixture view trees
- [ ] Docs: new `docs/ui-plane.md`; updates to `concepts.md`, `architecture.md`, `protocol.md`, `skills/README.md`, `clients.md`; new ADR for treatments-vs-primitives
- [ ] huxley-web reframed as a product client (kills "web UI is a dev tool, not a product" framing in `CLAUDE.md` and `docs/`)

### Tests (Gate 3 — seeds; fill after DoD locked)

- **SDK**: view type construction, action kind validation, treatment enum coverage, notification envelope validation
- **Protocol**: capability handshake happy path + unknown-capability rejection; message-gating by capability (UI msg to audio-only client is a bug); handshake schema round-trip
- **Per-skill**: `render_view` returns correct shape for each internal state; `update_view` emits after tool-call state changes; notification fires on timer expiry
- **Web-client**: treatment rendering from fixture view trees; action dispatch from tap; local-tick correctness (timer countdown, media scrub); empty-state rendering for every treatment

### Docs touched (Gate 4 — likely set; confirm at ship)

- `docs/ui-plane.md` — new spec
- `docs/concepts.md` — treatment / view / action / notification vocabulary
- `docs/architecture.md` — UI plane alongside I/O plane
- `docs/protocol.md` — five new message types + capability handshake
- `docs/skills/README.md` — skill-author guide for declaring views
- `docs/clients.md` — reframe huxley-web as product client, document capability model
- `docs/decisions.md` — ADR for treatments-vs-primitives decision and one-client-at-a-time premise
- `docs/extensibility.md` — update "real design gaps" list; UI layer no longer a gap
- `CLAUDE.md` — remove "web UI is a dev tool, not a product" framing
- `README.md` — mention UI story alongside the skill distribution model

### Ship (Gate 5 — open)

Sized at ~4–6 weeks of focused work. **Do not start until** T1.4 stages complete and the skill APIs of audiobooks, news, and timers have stabilized — retrofitting UI onto still-moving skill internals would be wasted effort. Filing at low priority; this is an ecosystem-completeness item, not a blocker for current personas.

**Cross-reference**: `MESSAGE_THREAD` treatment's canonical consumer is **T1.11** (Telegram messaging). Land T1.11 with the UI gate intentionally stubbed out and wire MESSAGE_THREAD + inbox views as T2.5 ships.

---

## T2.6 — Rename `huxley-skill-comms-telegram` → `huxley-skill-telegram`

**Status**: done (2026-04-23) · **Task**: — · **Effort**: S — shipped in a single atomic commit. 42 telegram + 352 core + web typecheck all green post-rename.

**Problem.** The `comms-` prefix was a placeholder from an earlier framing where "comms" was going to be a category of skills with provider-specific submodules. Huxley doesn't work that way — skills are flat installable units, not plugin-of-plugins. With messaging landing (T1.11) alongside calls in the same skill, the name should just be the service: `huxley-skill-telegram`.

**Why it matters.** Before a third-party skill author sees the Telegram skill as the worked example, the name should match the pattern Huxley is actually advertising (`huxley-skill-<service>`). Renames get harder once the package ships to PyPI and lands in external personas; cheaper now.

### Validation (Gate 1)

- README skill table uses `huxley-skill-comms-telegram` in the shipped list. No external personas depend on the old name yet.
- The `comms-` prefix has no counterpart elsewhere in the repo — there is no other `huxley-skill-comms-*` skill and none planned. The prefix is a vestige, not a namespace.
- Retirement of T1.9 (generic-messaging abstraction) removes the last rationale for keeping `comms-` as a category marker.

### Design (Gate 2 — trivial, skip critic)

Mechanical rename across:

- `server/skills/comms-telegram/` → `server/skills/telegram/`
- `huxley_skill_comms_telegram` import path → `huxley_skill_telegram`
- `pyproject.toml` package name, entry point, test target, workspace manifest
- `server/personas/*/persona.yaml` skill keys (`comms-telegram:` → `telegram:`)
- Docs: `docs/skills/comms-telegram.md` → `docs/skills/telegram.md`; references in `docs/roadmap.md`, `README.md`, `CLAUDE.md`, other triage entries
- Memory files if any reference the old name
- Git log references in prior triage entries get a **rename note**, not a history rewrite (history stays as-is)

No behavioral change. All 42 tests should remain green; if they don't, the rename was done wrong.

### Definition of Done

- [ ] Package directory renamed; import path updated throughout `server/`
- [ ] `pyproject.toml` + workspace manifest + entry points updated
- [ ] All personas updated (`server/personas/abuelos/persona.yaml`, `server/personas/basicos/persona.yaml` if referenced)
- [ ] All docs updated (skill doc file moved; all references throughout `docs/` and `README.md` updated)
- [ ] `CLAUDE.md` project-level references updated if any
- [ ] `uv run ruff check` + `uv run mypy server/sdk/src server/runtime/src` green
- [ ] `uv run --package huxley-skill-telegram pytest` green (all 42 tests)
- [ ] End-to-end smoke: start server, call to a whitelist contact works (existing regression)
- [ ] Single commit (mechanical rename should be atomic)

### Tests (Gate 3)

No new tests. Proof of correctness is the existing 42 passing against the renamed package.

### Docs touched (Gate 4 — expected)

- `server/skills/telegram/` (rename from `comms-telegram`)
- `docs/skills/telegram.md` (rename from `comms-telegram.md`)
- `docs/roadmap.md` — shipped skill table
- `docs/triage.md` — T1.10, T1.11, any other cross-references
- `README.md` — skill table entry
- `CLAUDE.md` — if referenced

### Cross-refs

- Enables T1.11 to land under the new name (or T1.11 absorbs the rename into its first commit if timing lines up).
- Superseded T1.9's "generic messaging abstraction" framing confirmed — per-provider skills named after the service.

---

## T2.7 — Focus-plane documentation reconciliation pass

**Status**: done (2026-04-24, co-landed with Stage 2b + Stage 5) · **Task**: — · **Effort**: S — `concepts.md` focus-management section rewritten; `extensibility.md` updated (proactive-notifications + live-calls gaps closed); `skills/README.md` priority guide updated for three-tier model; `io-plane.md` got a stronger historical-artifact disclaimer; ADR 2026-04-24 filed in `decisions.md`.

**Problem.** The 2026-04-23 honest-assessment review surfaced drift between the focus-plane story the docs tell and the state of the code. Specifically: `concepts.md` and `io-plane.md` describe `COMMS` and `ALERT` channels with language that implies they are wired or imminently wired, while in reality no code creates Activities on either channel; `InputClaim` is hardcoded to `CONTENT`; `io-plane.md` is self-labeled "partially superseded" with pre-pivot vocabulary still present. The drift directly caused a false red flag about whether the orchestration system was "ready" (it is), and blocked confident application of the model to messaging-UX questions.

**Why it matters.** The system itself is well-built; the story about the system is out of sync. Trust in the framework depends on the docs and code agreeing. T1.4 Stage 2b + Stage 5 materially change the focus-plane shape: COMMS becomes live with the InputClaim migration; ALERT stays **reserved-not-wired** (Stage 5 rescoped post-critic from a separate `inject_alert` primitive to a `InjectPriority.BLOCK_BEHIND_COMMS` enum variant — see Stage 5 entry). Reconciliation co-lands naturally with that work rather than as a separate chore.

### Validation (Gate 1)

- `docs/concepts.md` Focus Management section: _"Not yet wired: patience-expiry path, ALERT and COMMS channels, InputClaim on DIALOG/mic routing."_ Inaccurate: patience-expiry IS wired (see `_handle_patience_expired`); InputClaim IS wired but on CONTENT, not "DIALOG/mic" as the doc hints.
- `docs/io-plane.md` — self-labeled "partially superseded" with pre-pivot `Urgency` / `YieldPolicy` / `Arbitrator` vocabulary still present.
- `docs/architecture.md` Focus Management section — not audited in the 2026-04-23 review; likely has similar drift.
- Interactions matrix in T1.4 Stage 2 (`docs/triage.md` line ~1025-ish) — describes behavior correctly for CONTENT-based calls; needs rows updated for COMMS-based calls post-Stage-2b and new rows for ALERT post-Stage-5.

### Design (Gate 2 — trivial, skip critic)

Walk three docs line-by-line against actual code:

1. **`docs/concepts.md`** — Focus Management section. Rewrite the "Not yet wired" disclaimer to reflect current state: patience IS live; COMMS is live (holds `InputClaim` claims post-Stage-2b); ALERT is defined + arbitrated by FM but has **no callable surface and no current consumer — reserved for future LLM-free alert sounds (siren, alarm)**.
2. **`docs/architecture.md`** — Focus Management subsection + any Stage 2/3 prose that references CONTENT-holding-calls.
3. **`docs/io-plane.md`** — either rewrite to AVS-focus-management vocabulary (substantial) or add a stronger "historical artifact" disclaimer and point readers to `concepts.md` + `architecture.md` as the current truth. Decide at start of work; probably disclaimer-only is the right scope given the pivot ADR already exists.
4. **`docs/skills/README.md`** — priority-tier guide updated with `InjectPriority.BLOCK_BEHIND_COMMS` (new) and explicit "when to use which" narrative for the now-three-tier severity model.

Plus **one new ADR** in `docs/decisions.md`: "2026-04-XX — Focus plane completion: COMMS live, BLOCK_BEHIND_COMMS priority tier, pause/resume contract, concurrent-claim rejection, patience-expiry hook, ALERT reserved" capturing every load-bearing decision from the Stage 2b + Stage 5 + T2.7 co-land.

### Definition of Done

- [ ] `concepts.md` Focus Management section accurately describes current code state, including **ALERT reserved with no callable surface**
- [ ] `architecture.md` Focus Management section audited + corrected; Stage 2 prose updated to reflect COMMS-holds-calls
- [ ] `io-plane.md` either rewritten or explicitly marked historical with current-truth pointers
- [ ] ADR filed (shared with Stage 2b / Stage 5)
- [ ] Stage 2 interactions matrix in triage extended with COMMS rows and a note that ALERT rows would fire only if ALERT became wired
- [ ] `extensibility.md` audited for any claims about unwired channels
- [ ] `skills/README.md` severity-tier guide rewritten with three-tier story (`NORMAL` / `BLOCK_BEHIND_COMMS` / `PREEMPT`)

### Sequencing

- **Start** alongside Stage 2b + Stage 5 implementation — the code-side changes and doc updates co-land naturally
- **Must ship** in the same commit pair as Stage 2b/5, not as a separate lagging commit

### Docs touched (Gate 4)

See DoD bullets. No new files beyond the ADR.

## T2.8 — Move telegram MTProto creds out of repo root

**Status**: queued · **Task**: — · **Effort**: S (~1 hour)

**Problem.** The telegram skill loads MTProto credentials from two files at the
repo root: `telegram` (api_id + api_hash + bot token, exported from
`my.telegram.org/apps`) and `telegram.phones` (per-contact phone numbers).
Both are gitignored, but their presence at repo root makes the tree look
like it contains leaked secrets to anyone skimming, and conflates "framework
state" with "user-and-persona-specific config." After the
2026-04-28 server/clients/site restructure this is the last
pollution at the repo root.

**Why it matters.** Repo root readability — a clean top level should only
have the architectural buckets (`server/`, `clients/`, `site/`, `docs/`,
`scripts/`) plus standard meta. Per-persona secrets belong with the persona
that uses them. Also: the current layout silently couples "where the server
runs from" to "where the telegram skill finds its creds." Decoupling means
the skill can be loaded by any persona that has the right files in its
data dir, not just one that runs from a particular cwd.

### Validation (Gate 1)

- `server/skills/telegram/src/huxley_skill_telegram/skill.py` line 422
  comment: _"in `.env` at packages/core/; dev/test can put them directly in
  the…"_ (path is even stale — references the pre-restructure location).
- `.gitignore` lines 11–12: `/telegram` and `/telegram.*` anchored to
  repo root with the comment _"Mario exports the my.telegram.org/apps
  page as a plain file in repo root; kept out of git"_.
- `ls /Users/mario/Projects/Personal/Code/Huxley/` shows `telegram` and
  `telegram.phones` at root today.

### Design (Gate 2 — trivial, skip critic)

1. Introduce a `secrets_dir: str` field on the telegram skill's persona
   config block (`server/personas/abuelos/persona.yaml`). Default:
   `${persona.data_dir}/secrets/telegram/` (resolved at load time via the
   existing `SkillContext` data-dir API).
2. Skill reads `<secrets_dir>/telegram` (api_id/api_hash/bot token) and
   `<secrets_dir>/contacts.phones` (renamed from `telegram.phones` to drop
   the redundant prefix once it lives under a telegram-named dir).
3. Move the two files locally:
   `mv telegram server/personas/abuelos/data/secrets/telegram/telegram`
   `mv telegram.phones server/personas/abuelos/data/secrets/telegram/contacts.phones`
4. Update `.gitignore`: drop the root-anchored `/telegram` entries; the
   per-persona data dir is already gitignored via
   `server/personas/*/data/`, so the new location is automatically covered.
5. Update the comment in `skill.py` to point at the new location.
6. Skill regression test: existing tests load creds via a fixture; update
   the fixture to point at a temp `secrets_dir` and verify the loader
   honors it.

### Definition of Done

- [ ] `secrets_dir` field added to telegram skill config (with sensible
      default under `${persona.data_dir}/secrets/telegram/`)
- [ ] Skill reads from `secrets_dir`, not cwd
- [ ] Files moved out of repo root on Mario's laptop (mechanical mv, no
      git change since they're gitignored)
- [ ] `.gitignore` cleaned: root-anchored `/telegram` patterns removed
- [ ] `skill.py` location-comment updated
- [ ] Existing telegram test suite still green (90 tests); fixture
      updated to use temp `secrets_dir`
- [ ] One regression test asserting the loader respects `secrets_dir`
- [ ] `server/personas/abuelos/persona.yaml` documents the field
- [ ] `docs/skills/README.md` mentions per-skill secrets convention
- [ ] After ship: `ls Huxley/` shows zero `telegram*` entries

### Tests (Gate 3)

- Regression: `test_loads_creds_from_configured_secrets_dir` — set
  `secrets_dir=tmp_path`, drop `telegram` + `contacts.phones` files
  there, assert the skill reads them and doesn't touch cwd.

### Docs touched (Gate 4)

- `docs/skills/README.md` — note the per-skill-secrets pattern
- `docs/skills/<telegram skill doc>` — document the new config field
- `CLAUDE.md` — drop the "telegram creds at root" footnote once it's no
  longer true
- `.gitignore` — drop the `/telegram*` block, leave the explanatory
  comment at the new location (or delete it entirely)

### Why deferred

Bundled with the 2026-04-28 restructure proposal but explicitly held back
because (a) it requires a small skill code change, not just file moves, and
(b) keeping that restructure layout-only made the diff easier to review.
Trigger to revisit: any session that's already touching the telegram skill
for an unrelated reason — fold this in then.

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
| #98  | Strip remaining `Abuelo` hardcoded refs               | 30 min   | **done 2026-04-18** |
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
Abuelo today (one user, one operator, no upgrade-pain incidents yet). Ship
when (a) the first non-Mario user shows up wanting to try Huxley, OR (b) a
dependency upgrade burns 30+ minutes of Pi-vs-Mac debugging, OR (c) a
contributor explicitly asks for it. Container would: pin Python 3.13 + uv +
ffmpeg, expose port 8765, bind-mount `server/personas/<name>/data/` for the
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

## D5 — Skill UI architecture (huxley-web skill extension system) _(superseded 2026-04-23)_

**Status**: superseded by **T2.5** (skill UI plane — treatments model + capability-based clients). This entry preserved for historical context; **the design it describes is not the current direction**. The backing research note at `docs/research/skill-ui-architecture.md` reflects the superseded design and should be read as a historical artifact, not as a spec.

**Why superseded.** D5's conclusion was "skills ship arbitrary Svelte/JS bundles, huxley-web dynamically imports them (WidgetKit-on-the-web model)." A fresh design pass on 2026-04-23, triggered by walking audiobooks / radio / news / timers through the hypothetical UI layer, landed on the opposite answer: a curated **treatments** library (interaction archetypes like `CATALOG`, `MEDIA_PLAYER`, `TIMER_BOARD`) owned by huxley-web, with skills emitting declarative view data only.

The arbitrary-bundle model was rejected this time on three grounds D5 itself flagged as open problems:

1. **Authorship bar**: Python skill authors are Python people. Forcing a JS toolchain on every skill contradicts Huxley's "install one thing" premise.
2. **Design token versioning**: D5 openly admits this needs "an explicit contract with semver + deprecation cycles" — a multi-year commitment huxley-web can't credibly make pre-1.0.
3. **Non-web clients**: D5 says skill UI "must be progressive enhancement, not a behavioral dependency" — which is exactly the problem treatments-with-capability-handshake solves natively.

The "too limiting — prevents diagrams, contribution maps, custom counters" objection to closed vocabularies was correct at the _primitive-atom_ altitude (Text + Button + List). T2.5 operates at the _interaction-archetype_ altitude (CATALOG, MEDIA_PLAYER, TIMER_BOARD), which is categorically different: you don't build a timer out of primitives, you declare "I am a timer board" and pass data. Custom renderings become a future escape-hatch concern (explicitly deferred in T2.5), not a v1 requirement.

**What to read instead**: T2.5 for the current design. `docs/research/skill-ui-architecture.md` remains on disk as a record of what was rejected and why — do not act on it.

**Salvageable bits worth carrying forward** (from D5's "small things that can land independently"):

- `server_event("content.paused")` from audiobooks/radio — orthogonal to the UI plane choice, useful regardless.
- `hello.skills` manifest extension — replaced by T2.5's capability handshake + `view.render` / `view.update` message types, same idea different shape.
- Server→client `wake_word` mirror — orthogonal.

**Revisit trigger**: if a shipped skill genuinely cannot be expressed in the T2.5 treatment set, that's the signal to revisit the arbitrary-bundle escape hatch (T2.5's explicitly deferred follow-up). Not before.

---

## D6 — `huxley-skill-messaging` (provider-neutral messaging abstraction)

**Was**: T1.9 · **Retired**: 2026-04-23 · **Revisit when**: a second messenger provider skill ships (WhatsApp, Signal, SMS via Twilio, etc.) AND a common abstraction emerges naturally from real duplication across skills — not before.

**Reason for retirement.** The original framing sketched `huxley-skill-messaging` as a provider-neutral layer over "WhatsApp / Telegram / Twilio / Signal — provider TBD." A 2026-04-21 redesign already acknowledged this was wrong by folding messaging into `huxley-skill-comms-telegram`. The 2026-04-23 design conversation made that conclusion permanent and generalized it: **Huxley is per-provider-skill-shaped, not multi-provider-abstraction-shaped.**

**Why the abstraction is wrong**:

- Each messenger has different contact models, auth, rate limits, media support. Unified = lowest common denominator.
- Alexa, Siri, Google Assistant don't unify these either. Each provider is its own integration.
- The LLM routes user intent ("text Carlos") to the right tool (`send_telegram_message` or `send_whatsapp_message`) — no framework-level abstraction needed.
- Per-provider skills keep auth, contacts, and session lifecycle co-located. Splitting per-function across providers would duplicate all three.

**What replaced it**:

- **T1.11** — `huxley-skill-telegram` gains messaging features (send, read, proactive inbox) inside the same skill that owns calls.
- Future: WhatsApp / Signal / SMS each get their own skill (`huxley-skill-whatsapp`, etc.) if and when demand shows up. Each ships its own tools, its own auth, its own background listener.

**Revisit trigger specifics**: if/when Huxley has shipped 2+ messenger skills and a _specific_ cross-cutting concern emerges that every one of them re-implements (e.g., a common inbox-dedup policy, a common contact-importance ranking), that's the signal to consider lifting the shared slice into a helper module — NOT a provider-neutral skill. The abstraction, if it ever exists, is a Python helper in `huxley_sdk`, not a skill entry point.

---

## D7 — TTL on queued inject_turn / stale-alert drop

**Was**: deferred at Stage 1d.2 (2026-04-21 — `.wait_outcome()` shelved) · re-raised at Stage 5 critic (2026-04-23 finding #5) · **Revisit when**: first medication / safety-critical reminder skill ships for a user who routinely has multi-hour calls

**Reason for deferral.** An `inject_turn` fired with `InjectPriority.BLOCK_BEHIND_COMMS` (Stage 5) queues behind active COMMS claims. If the claim is a 2-hour phone call with grandpa's doctor, a medication reminder that should have fired at 8:00 AM fires at 10:00 AM — grandpa took his morning pill two hours late, or (worse) already took it without the system knowing and now the reminder tells him to take it again. For medication and similar safety-adjacent reminders, late-fire is worse than no-fire; a TTL with drop-callback lets the skill log "missed dose" and escalate via a different path (call a family member, etc.) instead of firing stale.

**Why it matters (but doesn't block Stage 5).** Huxley today has `huxley-skill-timers` (short cooking timers, not medication) and planned `huxley-skill-reminders` (T1.8, queued). Only T1.8's urgent medication path would hit the TTL case in practice, and T1.8 hasn't started. If it ships with the audiobook-only-strands motivation from PQ-1 and without long-call exposure, there's no urgency. If the skill expands to medication for a user in heavy-call households, TTL becomes safety-critical.

**Sketch if ever picked up**:

- `inject_turn(prompt, ..., ttl: timedelta | None = None, on_dropped: Callable[[], Awaitable[None]] | None = None)`
- Queue entry stamps an enqueue timestamp; at drain time, if `now - enqueued_at > ttl`, fire `on_dropped()` instead of the LLM turn
- Drop is logged with a structured event (`coord.inject_turn_dropped_ttl`)
- Observable via the existing `coord.inject_turn_*` log family — no new protocol surface

**Revisit trigger specifics**: a real reminder skill shipping with a real user whose call patterns expose the gap. Not speculative-future-users; a measurable incident.

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
`20407f0`). Verify in `server/sdk/src/huxley_sdk/types.py`.

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

**Status**: presumed done unless re-flagged. Verify in `server/runtime/src/huxley/logging.py`.

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

**Status**: presumed done unless re-flagged. Verify in `server/runtime/src/huxley/config.py`.

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
and the first tool is slow. Abuelo's tools are fast (time query: DB read; audiobook:
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

**Status**: presumed done unless re-flagged. Verify in `server/sdk/src/huxley_sdk/types.py` + `server/runtime/src/huxley/storage/skill.py`.

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

**Status**: presumed done unless re-flagged. Verify in `server/sdk/src/huxley_sdk/testing.py`.

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

**Status**: presumed done unless re-flagged. Verify in `server/runtime/src/huxley/logging.py`.

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

**Status**: open / unknown. Verify with `grep -rn "assert " server/skills/`.

**Symptom.** `server/skills/audiobooks/src/huxley_skill_audiobooks/skill.py`
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

**Root cause.** The strings were written when Abuelo was the only persona and
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

**Status**: presumed done unless re-flagged. Verify in `server/runtime/src/huxley/turn/coordinator.py`.

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

**Status**: presumed done unless re-flagged. Verify in `server/runtime/src/huxley/logging.py`.

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

---

## Field findings — 2026-04-19 grandpa demo

First real session with grandpa today. ~1.5 hours of actual use (22:11 → 23:04 in `/tmp/huxley-server.log`). Findings filed below; each is its own work item with severity tag.

### What worked (no action needed; captured for memory)

- **Spanish recognition is solid.** Whisper handled grandpa's natural speech with all its hesitations: _"Pero que para tu tengo que."_, _"radio, ¿qué? ¿Cómo es que digo?"_ — transcribed correctly, model responded sensibly.
- **Tool dispatch chain works end-to-end.** Across the session: `list_stations`, `play_station`, `stop_radio`, `play_audiobook`, multiple narration follow-ups. Every tool the LLM picked, fired correctly.
- **Audiobook fuzzy resolve works on real spoken queries.** _"Ponme 100 años de soledad"_ → matched `Gabriel García Márquez/100 Años de soledad.m4b` even with case + spelling variation (Soledad vs soledad). Score 0.647.
- **PTT interrupt is clean.** Multiple mid-stream interrupts (Radio Nacional at 22:32, the Cien años stream at 22:44) — `coord.interrupt` fired, content stream cancelled, next turn started without junk audio bleed-through.
- **Session auto-reconnect worked once.** At 22:20 the OpenAI session dropped; reconnected within ~1s, conversation continued. (See F2 for when it didn't.)

### F1 — 🔴 Emergency / accident intent has no real handler

**Status**: filed (2026-04-19) · **Effort**: ~1 day for MVP panic skill · **Blocks**: deploying to grandpa as a primary device

**Evidence** (server log line, verbatim transcript):

> 22:34:13 user: "Tuve un accidente. ¿Usted me puede ayudar?"
> 22:34:15 assistant: "Lamento escuchar que tuviste un accidente. Si crees que necesitas atención médica, lo más importante es buscar ayuda profesional rápidamente. Si estás en una situación de emergencia, por favor, llama a servicios de emergencia o a un familiar o amigo que pueda ayudarte..."

**Why this is the worst finding of the demo**: the user is **blind** and **elderly**. He cannot dial a phone. "Llama a servicios de emergencia o a un familiar" is the wrong answer for his physical reality. The framework was literally built so this kind of moment doesn't get a generic LLM-platitude response.

**Solution sketch**: a new `huxley-skill-panic` (or extend `huxley-skill-calls` with outbound) that:

1. Listens for accident/emergency intent via the LLM (tool dispatch on phrases like "accidente", "ayuda", "no me siento bien", "me caí")
2. Plays a distinctive, loud earcon at grandpa's end (different from any other sound — unmistakably "the device is doing something serious")
3. `inject_turn(PREEMPT)` narrates _"Voy a llamar a Mario ahora mismo"_ so grandpa knows help is on the way
4. Fires an outbound HTTP push to **all configured family endpoints** (PWAs registered as receivers) with a high-priority alert payload
5. Optionally opens a one-way audio stream so grandpa can keep talking even before anyone picks up — the family hears him, can speak back when they answer

The receive-side on the family PWA is an inverse of today's `/call/ring`: instead of the family ringing grandpa, grandpa rings the family. Same `InputClaim` substrate works for the audio relay; only the direction of the trigger changes.

**Why this should jump the queue ahead of T1.8 reminders**: the demo just gave us the user-shaped problem the whole framework exists to solve. Reminders are nice-to-have; emergency response is what justifies the OrangePi5-at-grandpa's-house deployment in the first place.

### F2 — 🔴 Connection failure leaves system in IDLE forever (no retry)

**Status**: done (2026-04-19) · **Effort**: ~1h · **Blocks**: deploying anywhere with imperfect internet

**Evidence**:

> 23:04:14.473 coord.session_disconnected
> 23:04:14.474 state_transition CONVERSING → IDLE
> 23:04:14.474 state_transition IDLE → CONNECTING (auto-attempt)
> 23:04:14.476 ERROR connection_failed
> socket.gaierror: [Errno 8] nodename nor servname provided, or not known
> 23:04:14.481 state_transition CONNECTING → IDLE trigger=failed

DNS resolution to OpenAI failed (transient — your network blip OR an upstream DNS hiccup). The framework attempted ONE reconnect, that failed, and then it sat in IDLE indefinitely. A blind elderly user has no way to know the device is offline; he'd press PTT, hear nothing back, and assume the device is broken.

**Solution**: in the `_on_session_end` / `_enter_connecting` paths, on `connection_failed` retry with exponential backoff (1s / 3s / 10s / 30s, then every 60s indefinitely while still configured to reconnect). After the third failure, fire an audible inject*turn at the device — *"No tengo conexión, intentando otra vez."\_ So grandpa gets an audio cue that the system is alive and trying.

**Definition of Done**:

- DNS-failure-then-recovery scenario test (mocked transport that fails N times then succeeds; assert reconnect)
- Audible inject after 3 failed attempts
- Indefinite retry afterward (don't give up — the system shouldn't permanently brick on a network blip)
- Log every attempt with `app.reconnect_attempt` so the timeline is debuggable

**Ship notes (2026-04-19)**:

- Retry loop extracted to `server/runtime/src/huxley/reconnect.py` so the backoff policy is testable without an Application graph. Pure `run_reconnect_loop(connect_attempt, announce, should_continue, sleep)` with injected sleep.
- Backoff: `(1s, 3s, 10s, 30s)` then 60s floor indefinitely. Exits when `should_continue()` flips False (shutdown, user PTT reconnected, or success).
- Audible cue from attempt 4 onward: synthesized double-beep PCM16 @ 24kHz played via `server.send_audio()` (no persona asset required, no LLM needed — the whole point is that we're offline). 9 unit tests in `test_reconnect.py`.
- Deviation from spec: the cue is a beep tone, not a spoken "No tengo conexión" inject_turn. `inject_turn` requires a live session; during an outage there isn't one. Proper voiced announcement would need a pre-recorded persona asset or local TTS — filed as a follow-up if Mario wants a voice message instead of a tone.
- Commit: `453f840`.
- Lessons: extract retry policies as pure functions with injected sleep — mocking `asyncio.sleep` globally is a trap; a callable sleep parameter gives deterministic tests in 0.02s.

### F3 — 🟠 _"¿Cuántos libros tienes?"_ doesn't use the catalog

**Status**: filed (2026-04-19) · **Effort**: ~30 min · **Blocks**: nothing (graceful but vague)

**Evidence**:

> 22:42:27 user: "¿Cuántos libros tienes?"
> 22:42:31 assistant: "Tengo una colección de varios audiolibros de literatura clásica y contemporánea en español, que incluye títulos de autores latinoamericanos y obras de diversos géneros. Si estás interesado en algún libro en particular o tipo de género, no dudes en decírmelo y estaré encantado de ayudarte a encontrar algo que te guste."

The audiobooks skill ships `list_in_progress` AND a `prompt_context()` that includes the catalog. But the LLM gave a generic "tengo varios" instead of the actual count (19 books). Either:

1. The audiobooks `prompt_context()` is too verbose and the catalog count gets lost in the noise
2. The persona's system_prompt doesn't direct the LLM to ground answers in `prompt_context` data
3. Both

**Quick diagnosis**: read what `audiobooks.prompt_context()` actually returns today, plus Abuelo's system*prompt section about audiobooks. Probably one small change resolves it (lead `prompt_context` with *"Tienes N audiolibros disponibles: ..."_ and / or add a system_prompt sentence _"Cuando el usuario pregunte cuántos libros tienes, dale el número exacto del prompt context."\_).

### F4 — 🟠 _"¿De dónde puedo pedir comida?"_ got generic Rappi/Uber Eats answer

**Status**: filed (2026-04-19) · **Effort**: future skill, ~1-2 days · **Blocks**: nothing

**Evidence**:

> 22:36:19 user: "Buenas, tengo hambre, es hora de almorzar, ¿de dónde puedo pedir comida?"
> 22:36:24 assistant: "Para pedir comida, puedes buscar servicios de entrega a domicilio populares como Rappi, Uber Eats o Domicilios..."

A blind elderly user can't open Rappi. Useless answer. Future work: a `huxley-skill-domicilios` (or generic `huxley-skill-favoritos`) with grandpa's actual usual restaurants + their phone numbers — the LLM would say _"¿Quieres que llame a tu pollo asado de siempre?"_ and one tool dispatch later, the calls skill rings the restaurant. Same outbound-call substrate F1 needs.

Filed for after F1 — same plumbing, more specific data.

### F5 — 🟡 Radio audio bleeding into mic (echo / hardware concern)

**Status**: filed (2026-04-19) · **Effort**: hardware-side fix · **Blocks**: ESP32 deployment design

**Evidence**:

> 22:31:25.692 transcript role=user text='radio, ¿qué? ¿Cómo es que digo? López Gómez periodista.'

This was Radio Nacional's audio bleeding into grandpa's laptop mic and being transcribed AS IF grandpa said it. Today's symptom is benign (model just confused), but on a higher-volume speaker system (the planned ESP32-driven device) bleed could trigger spurious tool calls — _"...play next station..."_ heard from the radio could literally call `play_station` on a different one.

**Mitigation**: when picking the ESP32 hardware (mic + speaker), pick a dev kit with hardware AEC (e.g., `XMOS XVF3000`-class chips, or a dedicated codec like the WM8960). Software AEC in Python is not a winning fight for real-time audio. **Note for the hardware spec doc** (when we write it).

### F6 — 🟡 `session.rx.error code=response_cancel_not_active` noise on every interrupt

**Status**: filed (2026-04-19) · **Effort**: ~30 min · **Blocks**: nothing (we ignore the error)

Every clean interrupt sends a `response.cancel` to a response that's already done. OpenAI returns a `response_cancel_not_active` error; we log it at info and move on. Functionally harmless but it's noise in the log.

**Fix sketch**: track `response_in_flight: bool` in the coordinator (set on `commit_and_request_response` / `request_response`, cleared on `on_response_done` and `on_audio_done`). Skip the cancel send when not in flight. Alternative: drop the OpenAI-side error from log entirely (just stop reporting it at info level).

---
