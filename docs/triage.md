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

**Status**: done (2026-05-01; Mario smoke verified) · **Commits**: `6b58dbe8` (entry filed) → `b4884ff4` (design revision after Gate 2 round 1) → `0e32684e` (persona helpers + tests) → `ab5f1aea` (Runtime + AudioServer lift + ?persona reconnect + hello extras) → `b83232e0` (Gate 3 critic-round-2 fixes: eager swap-connect, swap lock, teardown timeout, public dispatch surface, swap tests) → `7047871e` (full doc pass: concepts/protocol/architecture/decisions + 4 ADRs) → `fa698d07` (PWA picker + persona_swap earcon) → `6b252021` (post-smoke: PersonaSummary.name = directory basename, not spec.name) → `512ff48f` (post-smoke: selectPersona sets CONNECTING not IDLE) → `06d83c5d` (post-smoke: language threads into swap; teardown gates \_on_state_transition) · **Effort actual**: ~2 days across server + PWA + docs + post-smoke fixes. Smoke caught three real bugs the critic agents missed.

### Lessons

- **Two critic rounds paid off, both differently.** Round 1 (design phase, in-band → reconnect) reduced ~75% of the work. Round 2 (implementation phase, after the code shipped) caught a structurally-broken lazy-connect path that would have failed Mario's first smoke. The pattern is now proven: critic before code AND critic after code, on anything load-bearing. The cost of two agent calls is trivial against the cost of one wrong-direction commit.
- **"User-visible UX is identical" beats "the abstraction is more elegant."** I sold Mario on in-band swap on aesthetic grounds ("the connection persists; the soul changes"). The critic correctly named it as poetry — the user couldn't perceive the difference between in-band and a 50ms reconnect given active-claim gating. Reconnect won by being smaller and structurally correct. Lesson: when both paths look identical to the user, pick the smaller one and document the choice.
- **Boundary semantics ≠ technical lifecycle (T1.12 lesson, generalized).** T1.12 said WS-connect ≠ conversation; T1.13 says WS-connect ≠ persona-swap, but ALSO says WS-connect = persona-swap-trigger. The user-visible "conversation" and the user-visible "persona switch" are different from but overlap with the WS lifecycle. Naming the layers separately (Runtime above Application; conversation above session) makes both T1.12 and T1.13 simpler.
- **Profile abstractions are usually a Mac-app convention bleeding into a server-process system.** I almost shipped `huxley start <profile>` with a `~/.huxley/profiles/<name>/` registry. Mario's pushback ("just another folder") was right — every other server daemon (nginx/postgres/redis) handles multi-instance via the cwd-based pattern. Profile abstractions look ergonomic but cost real surface area for zero capability gain.
- **Persona-as-different-person was a structural argument, not a stylistic one.** The "persona = theme on shared world" alternative would have forced a state-split (`data/user.db` + `data/<persona>.db`), a `SkillScope` enum, skill-author cognitive overhead, and a hybrid mental model where the persona seems to know some things about the user but not others. Once we noticed conversation summary + transcripts MUST be per-persona (LLM context segmentation), every other split was forced. Lesson: when one boundary is structurally forced, lean into it everywhere instead of papering it over.
- **Test-first for the regression, not for the happy path.** The round-2 critic flagged that the storage-lock-race fix was the headline of the commit but had no regression test. Landing the rapid-back-and-forth-swap test alongside the fix (instead of in step 11) closed that loop. The discipline: when a commit's body says "fixes race X," there must be a test covering X in the same commit, even if other tests are deferred.
- **Mid-task collaboration with Mario:** Mario was implementing app-layer wiring while I was writing storage; we collaborated through the locked DoD bullets in the triage entry. No churn. T1.12's lesson about `git add <specific-file>` (not `-A`) carried through — at no point did I clobber his in-flight edits.
- **The `_pending_summary` pattern (T1.12) generalizes.** T1.13's `_teardown_task` tracking + `asyncio.shield(timeout)` follows the same shape: capture intermediate state on `self`, let the asynchronous handoff read it without a flag race. Worth remembering when the next teardown-with-async-cleanup race comes up.
- **Smoke caught three bugs the critics missed.** Both critic rounds (design + post-implementation) were rigorous; both still missed bugs that only surfaced when a real client connected with non-default-language: (1) `PersonaSummary.name` conflated YAML's display-name with the directory-basename id that `?persona=` looks up, breaking the Basic→basic lookup on case-sensitive filesystems; (2) `selectPersona` set IDLE during the swap window, firing the unexpected-session-drop error tone and leaving PTT broken until state messages caught up; (3) AudioServer fires `on_persona_select` BEFORE `on_language_select`, so the new app opened in the persona's default language and then immediately disconnected+reconnected when on_language_select fired with the client's language — the disconnect-state leaked CONVERSING→IDLE to the new connection because the OLD app's `_on_state_transition` relayed it via the shared AudioServer. Lesson: critic rounds find design issues; smoke finds runtime issues. **Don't claim done before smoke.** Both are needed.
- **Canonical id vs display name is a recurring footgun.** Persona's directory basename (`abuelos`) is the wire id; the YAML's `name:` field (`Abuelo`) is the human label. Conflating them broke filesystem lookups. Same lesson applies broadly: any time there's a display name and a wire/storage id, name them differently in the type system (`name` for the canonical id, `display_name` for the human label) and document which is which.
- **Shared resources between concurrent Applications need source-aware gating.** `AudioServer` is process-singleton (correct, per T1.13 design). But the OLD app being torn down in the background AND the NEW current_app share that singleton. The OLD app's `_on_state_transition` relays state to `self.server.send_X` — which routes to whatever WS the AudioServer currently has, i.e. the NEW persona's connection. Fix was to gate on `_shutting_down` so the dying app can't speak to the wire. **The next concurrent-Application bug will look the same shape.** Watch for it: anywhere an Application's instance method writes through a shared resource, ask "what happens if this is the OLD instance during background teardown?"
- **Lazy-connect-on-swap is a surface that wants to be eager.** The original locked design said lazy connect on swap (cost-saving). Round 2 critic showed the state machine doesn't have an IDLE→CONNECTING-on-PTT path, so lazy was structurally broken. Round 3 then showed that even with auto_connect=True, opening in the persona's DEFAULT language was wrong — it had to open in the requested language. Both were "make it eager" fixes. Lesson: when a design has multiple deferred steps that compose, check that the deferral chain actually works end-to-end.

### Effort

in_progress (2026-05-01) → done (2026-05-01) · ~1.5 days actual

**Problem.** Today, switching personas requires running multiple server processes (one per persona) on different ports and listing each as a `name:url` pair in `VITE_HUXLEY_PERSONAS`. The PWA picker just chooses which port to talk to. That's a deployment artifact leaking into UX: an end user shouldn't have to start terminals or manage ports to switch the assistant's "face."

**Why it matters.** Persona-switching is a first-class user-visible product affordance, not a re-deployment. For the AbuelOS-target end user (elderly, blind), "open a terminal" is not a workflow — and even for self-hosted dev users, the multi-process model is wrong because it scales linearly with persona count. The architecture today binds `Application` to "the one persona this process can serve." That binding is the bug.

### Validation (Gate 1)

- `server/runtime/src/huxley/__main__.py:50` constructs exactly one `Application(config, persona)` per process, where `persona` is resolved at startup via `resolve_persona_path` + `load_persona`. There is no in-process swap path.
- `server/runtime/src/huxley/app.py:101` builds `self.storage = Storage(persona.data_dir / f"{persona.name.lower()}.db")` — storage is constructor-bound to the persona, no rebind.
- `server/runtime/src/huxley/app.py:104-113` constructs `AudioServer(...)` inside `Application.__init__`. The TCP listener is owned by the persona-bound app, so a swap can't preserve the connection.
- `clients/pwa/src/App.tsx:25-49` reads `VITE_HUXLEY_PERSONAS` env var and parses `name:url` pairs; PWA connects to one URL per persona. `useWs.switchPersona(url)` (`clients/pwa/src/lib/useWs.ts:369-381`) closes the WS and opens a new one to a different URL.
- `server/personas/` has 5 personas shipped (abuelos, basic, buddy, chief, librarian) — all functional; the only thing standing between them and the user is the deployment shape.

### Product story (locked first; everything below is its consequence)

**A persona is a distinct entity, not a theme.** When a persona is not active, that persona is not present — their Telegram is offline, their reminders are paused, their conversation context is dormant. Switching to a persona is reuniting with them; they catch up on what happened while away the same way they would after a process restart. Information does not flow between personas; if you tell abuelos something, librarian does not know it.

This matches what the LLM contexts actually are. We don't paper over the segmentation; we name it as the contract.

**Two layers of scoping:**

- **Profile** (= human). Each human gets their own Huxley runtime: their own personas, secrets, data, port. Two humans in one house = two processes, two URLs. There is no profile abstraction in code — a profile is just a directory with `personas/`, `.env`, and a port. Same convention as nginx-sites or postgres-clusters.
- **Persona** (= face for one human). Within a runtime, the picker swaps faces.

One client connects to one runtime. The picker switches personas inside that runtime. Cross-profile isolation is filesystem-enforced (different cwds, different `.env`, different DBs); cross-persona isolation inside one runtime is enforced by per-persona DBs (T1.12).

### Design (Gate 2 — locked 2026-05-01 after critic round)

**The chosen wire shape is `?persona=<name>` reconnect, not in-band swap.** The Gate 2 critic correctly named in-band as aesthetic-not-functional: the user-visible difference between a 50ms WS reconnect and an in-band swap is zero (we already gate the swap on `!_claim_or_stream_active()`). Reconnect dominates: smaller surface area, no protocol bump, no audio-frame-during-swap race, no reference-rebinding atomicity concerns. A poetic "soul change vs reincarnation" framing is not worth 400 LOC + a wire-protocol bump + concurrency hazards the user can't perceive.

**Topology — `Runtime` above `Application`:**

```
Runtime (process singleton)
├── AudioServer            ← single TCP listener, lifelong
├── PersonaRegistry        ← enumerates ./personas/<name>/persona.yaml at startup
├── current_app: Application | None
└── _teardown_task: asyncio.Task | None
    ├── (per Application)
    ├── Storage              ← per-persona DB
    └── SkillRegistry, Provider, Coordinator, FocusManager, ...
```

`AudioServer` lifts from `Application` to `Runtime`. AudioServer's callbacks indirect through `Runtime`, which forwards to `runtime.current_app`. The TCP listener stays bound across reconnects; the persona behind it changes.

**Wire protocol — strictly additive, no version bump.** Stays at protocol 2.

- Connect URL: `ws://host:port/ws?persona=<name>` selects a persona at handshake. Omit the param → server uses `HUXLEY_PERSONA` env or auto-discovery (alphabetic-with-loud-log when ambiguous).
- Server→client at `hello`: `{ type: "hello", protocol: 2, current_persona: "abuelos", available_personas: [{name, display_name, language}, ...] }`. New fields are additive; old clients ignore them.
- No new `select_persona` / `persona_changed` message types. Persona-switch UX is "PWA closes WS, reopens with new `?persona=`" — exact same pattern as `setLanguage`.
- Status frames narrate the swap as today: `"Cambiando a basic…"` is sent on the OLD WS just before close, in the OUTGOING persona's `ui_strings`; new earcon `persona_swap.wav` plays during the brief gap; new connection's `hello` carries the new `current_persona` and the PWA chip updates. Status `"Listo"` fires on the new WS, in the INCOMING persona's `ui_strings`.

**Why no protocol bump:** additive fields on `hello` are safe — the existing PWA and firmware ignore unknown message fields (already documented in `docs/protocol.md`). Bumping forces tight client/server lockstep with no payoff. The reconnect mechanism uses an existing query-string pattern.

**Swap algorithm:** runs inside `Runtime` on every WS connect. Pseudocode:

```python
async def on_connection(self, ws, query: dict[str, str]) -> None:
    requested = query.get("persona") or self._resolve_default()

    # Storage-lock-race fix: if a teardown task is in flight (provider
    # summary write may still be writing to the requested persona's DB),
    # await it before constructing a new Application that would open the
    # same DB file. Critical for rapid back-and-forth swap (A→B→A within
    # 500ms of each other).
    if self._teardown_task is not None:
        await self._teardown_task
        self._teardown_task = None

    if self.current_app is None or self.current_app.persona.name != requested:
        # Build new before tearing down old.
        new_persona = self._registry.load(requested)
        new_app = Application(self._config, new_persona)
        try:
            await new_app.start()  # light: storage init + skill setup, no OpenAI
        except Exception:
            await new_app.shutdown()  # cleanup partial init
            await logger.aexception("runtime.persona_load_failed", persona=requested)
            await ws.close(code=1011, reason="persona_load_failed")
            return

        old_app, self.current_app = self.current_app, new_app
        await logger.ainfo("runtime.persona_swap_committed", from_=old_app and old_app.persona.name, to=requested)

        if old_app is not None:
            self._teardown_task = asyncio.create_task(old_app.shutdown())

    # Wire ws to current_app.
    await self.current_app.handle_connection(ws)
```

**Why pre-validate (build new before tearing down old):** if the new persona fails to start (bad yaml, skill setup error, DB corrupt), the OLD app is still alive AND the WS gets a clean close with an explanatory code. No half-built state, no orphaned current_app.

**OpenAI session on swap — lazy.** New persona's session opens on the next `wake_word`. Mirrors existing semantics: user PTTs → CONNECTING → CONVERSING with new persona.

**Active-claim/stream gating:** the PWA's persona picker is disabled while `activeClaimId !== null` or `activeStream !== null`. UX-only; no server enforcement needed because `?persona=` reconnect WILL terminate any active claim/stream by closing the WS — that's the user explicitly saying "abandon current state and switch." The PWA's gate stops the user from doing that accidentally; if they hit it through other means, the resulting teardown is correct.

**Audible UX (blind-user floor):** new earcon `persona_swap.wav` rendered by `scripts/synth_sounds.py` into `server/personas/_shared/sounds/`. PWA plays it on `selectPersona()` between WS close and reopen. Status `"Cambiando a <name>…"` (in OLD persona's `ui_strings`) fires before the close. New persona's `hello` triggers `"Listo"` in NEW persona's `ui_strings`.

**Default persona resolution at boot — locked rule:**

1. `HUXLEY_PERSONA` env set → use it (existing behavior).
2. Single persona under `./personas/` → autodiscover (existing behavior).
3. Multiple personas under `./personas/` and no env var → **pick alphabetically with a loud `runtime.default_persona_alphabetic` log line.** Refusing to start would force the env var on multi-persona installs, exactly the deployment artifact T1.13 retires. The PWA picker lets the user change immediately.

**Multi-instance deployment (the household scenario) — no profile abstraction.** Each "human" runs a separate Huxley process in its own working directory. Convention is plain Unix-daemon shape:

```bash
mkdir -p ~/huxley-grandpa/personas && cp -r .../template-personas/abuelos ~/huxley-grandpa/personas/
# write ~/huxley-grandpa/.env with grandpa's secrets
mkdir -p ~/huxley-kid/personas && cp -r .../template-personas/buddy ~/huxley-kid/personas/
# write ~/huxley-kid/.env with kid's secrets

(cd ~/huxley-grandpa && HUXLEY_SERVER_PORT=8765 uv run huxley) &
(cd ~/huxley-kid     && HUXLEY_SERVER_PORT=8766 uv run huxley) &
```

Production: two launchd plists with different `WorkingDirectory`. Grandpa's ESP32 is flashed with `WS_URL=ws://homeip:8765/ws`. Kid's PWA opens `http://homeip:8766/`. Each client only ever sees its human's instance. The two processes share nothing — privacy is filesystem-enforced.

We do **not** ship a profile abstraction (`huxley start <name>`). The cwd-based pattern is self-explanatory and matches every other server daemon (nginx sites, postgres clusters, redis instances). A `huxley init <dir>` scaffolder may land later as docs polish; not required.

### Critic Notes (Gate 2 round 1 — design — complete 2026-05-01)

Critic agent ran the full design (then in-band) with file:line refs. Verdict: in-band was overscoped. Findings + dispositions:

- **In-band vs reconnect — aesthetic, not functional.** The user-visible difference between a 50ms WS reconnect and an in-band swap is zero given the active-claim gating. **Incorporated**: reversed to `?persona=` reconnect; ~75% of the design's complexity evaporates with this change.
- **Protocol bump 2→3 unnecessary** for reconnect path (no new message types, additive `hello` fields ignored by old clients). **Incorporated**: stay at protocol 2.
- **Storage-lock race on rapid back-and-forth swap** (A→B→A within 500ms): old A's `provider.disconnect(save_summary=True)` summary write to `abuelos.db` is still running when new A constructs and opens the same DB. SQLite WAL writer-lock collision (`database is locked`). **Incorporated**: `Runtime` tracks `_teardown_task`; awaits in-flight teardown before constructing a same-named Application. Test added to DoD.
- **Boot-time ambiguity** (multi-persona dir, no env var): design glossed it. **Incorporated**: alphabetic pick with loud log; explicit DoD bullet.
- **Skill state across swaps** (Telegram MTProto re-handshakes per persona; reminders schedules cancelled on teardown). **Incorporated as contract**: skills get full teardown + setup on persona swap. Reminders skill must catch up on `setup()` (verify it does; fix if not). Telegram backfill (T1.11) already covers the same path. Documented in concepts.md.
- **Cost-tracker daily totals split per-persona** (each persona's CostTracker has its own DB; multi-persona day fragments). **Incorporated as known footgun**: documented in `docs/decisions.md` ADR; not a T1.13 blocker.
- **PWA confirmation surface**: chip text updates on `hello` arrival — already happens via `current_persona` field. **Incorporated**: explicit DoD bullet that chip reflects post-swap persona.
- **Observability**: each swap phase logged. **Incorporated**: DoD bullet for `runtime.persona_swap_committed` / `runtime.persona_load_failed` / `runtime.default_persona_alphabetic` / teardown-complete events.
- **Class shape** (Runtime + PersonaRegistry, no separate PersonaSwitcher). **Incorporated**: reconnect path collapses the swap algorithm to ~30 LOC inside Runtime; no PersonaSwitcher needed.
- **Multi-process household scenario**: critic didn't address; Mario surfaced the question. **Decision**: cwd-based convention, no profile abstraction. Documented above.

### Critic Notes (Gate 3 round 2 — implementation — complete 2026-05-01)

After the server-side implementation landed (commits `0e32684e`, `ab5f1aea`), an independent critic agent reviewed the actual code against the locked design. Four findings forced a fix-up commit (`b83232e0`); summary in [`docs/decisions.md`](./decisions.md#2026-05-01--t113-critic-round-2-fixes-eager-swap-connect-swap-lock-teardown-timeout).

- **§4 — "lazy connect on swap" was structurally broken.** Locked design said "user PTT triggers connect via the existing state-machine path." False: state machine has no IDLE→CONNECTING transition on PTT, only on `wake_word`, and the PWA does not emit `wake_word` on a swap reconnect. So PTT after swap got rejected by `Application.on_ptt_start`'s IDLE-state guard. The new persona was unreachable. **Fix**: `_shim_persona_select` calls `_switch_to_persona(name, auto_connect=True)`. Idle Realtime sessions cost $0 so eager-connect on swap is functionally free (mirrors boot path). Lesson: "the existing state-machine path" had a critical asymmetry the design glossed over — would have hit Mario on first smoke.
- **§2 — concurrent swap leaks an Application.** Two `_switch_to_persona` calls overlapping (PWA in two tabs, StrictMode double-mount, rapid picker clicks) both ran their build-new path concurrently and the loser's freshly-built Application was silently reference-overwritten — never shutdown, holding open SQLite + FocusManager + (with §4 fix) a live OpenAI session. **Fix**: `asyncio.Lock` around `_switch_to_persona` body. Concurrent swaps serialize.
- **§10 — stuck teardown DoSes the swap path.** `_teardown_task` was awaited unconditionally; a buggy skill teardown deadlock would stall every subsequent swap forever. **Fix**: `asyncio.wait_for(asyncio.shield(task), timeout=10.0)`. On timeout: log loudly + abandon. `shield` so the underlying teardown still runs in the background.
- **§3 — Runtime accessed Application's private `_on_*` methods.** Silently brittle: a future rename of `_on_wake_word` would break Runtime at runtime, no type-check or test would catch it. **Fix**: renamed all 9 AudioServer-callback methods on Application from `_on_X` to `on_X`. They're now Application's public dispatch surface; Runtime shims call them directly.
- **§11 — swap tests landed alongside the fixes.** The headline storage-lock-race fix (round 1) was previously unverified. New `tests/unit/test_runtime.py` with 10 tests including rapid back-and-forth, concurrent swap, failure-mode, hello-extras contract.

Other round-2 findings (PWA TS type drift on hello extras, `list_personas()` re-enumeration cost per connection, "module functions vs `PersonaRegistry` class" deviation) deferred to step 8 (PWA wiring) and step 12 (docs polish) — non-blocking.

### Definition of Done (locked 2026-05-01 post-critic)

**Server-side (DONE)** — commits `0e32684e`, `ab5f1aea`, `b83232e0`:

- [x] Persona enumeration helpers (`list_personas`, `pick_default_persona_name`) — module functions; `PersonaRegistry` as a class was speced but the no-state shape didn't justify a class.
- [x] `Runtime` class — owns `AudioServer` + `current_app` + `_teardown_task` + `_swap_lock`. Implements the swap algorithm.
- [x] `Application.shutdown()` audited; reused as-is, no leaks identified. Now public (was `_shutdown`).
- [x] `Application.start(auto_connect=True)` is light — storage init + skill setup; OpenAI session opens via `wake_word` trigger only when `auto_connect=True`.
- [x] `__main__.py` refactored: constructs `Runtime` → `Runtime.run()`. Persona resolution moved into `Runtime.run()`.
- [x] Boot-time ambiguity rule (alphabetic + loud log) implemented in `pick_default_persona_name`. `test_multi_persona_alphabetic_fallback` covers it.
- [x] `?persona=<name>` parsing in `AudioServer._process_request`. `_shim_persona_select` triggers swap before hello.
- [x] `hello` payload carries `current_persona` + `available_personas`. Additive; protocol stays at 2.
- [x] **Rapid back-and-forth swap test passes**: `test_swap_back_to_same_persona_within_teardown_window` — exercises the storage-lock fix.
- [x] **Failure-mode test passes**: `test_unknown_persona_keeps_previous_current_app`.
- [x] **Concurrent swap test passes**: `test_concurrent_swap_serializes_no_leak` — exercises the round-2 §2 lock.
- [x] **Boot ambiguity test passes**: in `test_persona.py::TestPickDefaultPersonaName`.
- [x] Swap phases logged: `runtime.persona_swap_started/committed/failed`, `runtime.persona_load_failed/start_failed`, `runtime.old_app_shutdown_complete/failed`, `runtime.teardown_timeout_abandoned`, `persona.default_picked_alphabetically`.
- [x] `ruff check server/runtime/` + `mypy server/sdk/src server/runtime/src` + 444 runtime tests all green.

**Docs (DONE — this commit)**:

- [x] `docs/concepts.md` — added "Persona = distinct entity, not theme" + "Persona switch = reconnect, not theme change" + "One Huxley process = one human" sections under `## Persona`.
- [x] `docs/protocol.md` — added `?persona=` to handshake query params + additive `current_persona` / `available_personas` to hello + new "Persona selection via query param (T1.13)" section.
- [x] `docs/architecture.md` — added `## Runtime topology` section + updated system-overview diagram to show `Runtime` above `Application` + corrected dependency-flow note (`runtime.py` is now the root, not `app.py`) + updated "Where to look in code" table.
- [x] `docs/decisions.md` — three new ADRs (persona-as-distinct-entity, hot-swap-via-reconnect, multi-instance-via-cwds) + one round-2 fixes ADR.

**PWA (PENDING — step 8)**:

- [ ] `VITE_HUXLEY_PERSONAS` removed from any `.env*` + `App.tsx`'s `parsePersonas`. Persona picker reads `ws.availablePersonas` + `ws.currentPersona`. `selectPersona(name)` becomes "close WS, reopen with `?persona=name`" (extension of existing `setLanguage` pattern). Picker disabled during `activeClaimId || activeStream`. Chip text reflects `currentPersona` on each `hello`. PWA detects `current_persona !== requested` after a select-persona reconnect and surfaces a "couldn't switch" toast.
- [ ] `persona_swap.wav` rendered by `scripts/synth_sounds.py` and committed to `server/personas/_shared/sounds/`. PWA plays it between WS close and reopen.

**Skill catch-up verification (PENDING — step 10)**:

- [ ] Reminders catch-up verified: switch from abuelos (with a reminder set for T+5min) to librarian, wait past T+5min, switch back to abuelos. Reminder fires (or rolls up "while you were away") via `setup()` re-read.
- [ ] Telegram backfill verified for the persona-swap-and-return path (likely already works since T1.11 backfill handles process-restart; same code path).

**Smoke (PENDING — step 13)**:

- [ ] Mario browser smoke: PWA shows all 5 shipped personas in the picker; tap one → audible swap → reconnect → new persona on first PTT; rapid back-and-forth A→B→A works.

### Implementation order

1. **`PersonaRegistry`** — extend `persona.py` with a `list_personas()` helper alongside the existing `load_persona()`. Light addition.
2. **Audit `Application.shutdown()`** — list every resource it owns; verify each is released. Add idempotency tests. Fix any gaps.
3. **Confirm `Application.start()` is light** — read what it does; ensure no OpenAI session opens at start (eager-connect happens via state machine on `wake_word`, not on start).
4. **Lift `AudioServer`** out of `Application`. New `Runtime` class owns it. `Application` gets callbacks injected from outside.
5. **Refactor `__main__.py`** to construct Runtime → Runtime constructs first Application (or lazily on first connect — small detail).
6. **`Runtime.on_connection`** — `?persona=` parsing, `_teardown_task` await, swap-or-reuse logic.
7. **`hello` additive fields** — `current_persona`, `available_personas` from registry.
8. **PWA rewire** — drop env var, parse `available_personas` from `hello`, rebuild picker, `selectPersona(name)` reuses `setLanguage` pattern, gate picker.
9. **Render `persona_swap.wav`**, wire PWA to play it.
10. **Verify reminders catch-up + Telegram backfill** for the swap path.
11. **Tests** — boot ambiguity, rapid back-and-forth swap, failure-mode, gold round-trip, observability.
12. **Docs** — concepts.md, protocol.md, architecture.md, decisions.md.
13. **Mario smoke + close gate.**

## T1.14 — Skill marketplace v1 (developer-primary: SDK + reference skill + authoring docs)

**Status**: done (2026-05-02; Mario smoke verified — installed `huxley-skill-stocks` into `basic`, `get_stock_price` returned real AAPL quote at $280.14; PyPI release shipped off the back of v1 in `7b9b8be6` with doc sweep in `ba6a7272`). Architectural specification at [`docs/skill-marketplace.md`](./skill-marketplace.md) (split into v1 and v2). **This entry covers v1 only**; v2 is deferred until caregiver-shipping is decided. **Effort actual**: ~1 day end-to-end (estimate was ~2 weeks; the split-and-critic discipline kept rework low and Phase 1's SDK additions were smaller than expected once Phase 2 validated them). **Prerequisite**: T2.8 (move telegram creds out of repo root → established the per-persona secrets dir pattern).

**Commits**: `c113e398` (T2.8) → `ec854a88` (T2.8 critic) → `4587c511` (spec v3) → `e2009e02` (lint housekeeping) → `576740a4` (Phase 1 SDK) → `32c99fd7` (Phase 1 critic) → Phase 2 in `huxley-skill-stocks` repo (`9d92ef6` + `d4f85e7`) → `7566efd2` (triage flip) → `7a0e86ae` (Phase 3+4 docs) → `45b1f5e4` (cross-doc gap close) → `334974a1` (smoke harness) → `caf7895b` (T2.9 + ClassVar sweep) → `86ff1b60` (PyPI-ready polish) → `8b08d438` (framework-ships-empty: drop hard skill deps) → `e355d3a5` (docs: framework ships empty) → `fedb341d` (link huxley-registry repo) → `2b629d6e` (fold stocks into workspace) → `7b9b8be6` (PyPI release: huxley-sdk 0.1.1 + 9 skills 0.1.0) → `ba6a7272` (post-PyPI doc sweep + ADR).

**Problem.** Huxley's load-bearing thesis is skill extensibility ("LLM understands rough natural-language intent and dispatches to user-installable custom tools, including for personal content" — see `docs/vision.md`). Today, "installable" means "the developer edits the runtime's `pyproject.toml`, runs `uv sync`, edits `persona.yaml`, restarts the server" — and there is no standardized way for a third-party author to publish a `huxley-skill-*` package that other people can install. The SDK doesn't define a config-schema convention or a secrets-storage primitive. The framework is extensible in principle but not in practice.

**Why it matters.** Without this, Huxley scales linearly with how many skills its core team writes. With this, it scales with how many people choose to write `huxley-skill-*` packages. The v1 deliverable is the **substrate**: the SDK additions + authoring conventions + a worked third-party-shaped skill. If real third-party skills emerge from v1, v2 (caregiver UX layered on top) earns its place. If they don't, v2 was always going to be vapor — building it speculatively would be wasted work. v1 is the cheapest test of "the marketplace thesis is real."

### Validation (Gate 1)

- `docs/vision.md` names skill-extensibility as the load-bearing differentiator; no surface today supports a third-party author publishing.
- `server/personas/<name>/persona.yaml` is hand-edited to enable/disable skills. No standardized config-schema mechanism.
- `pyproject.toml` of the runtime hardcodes the first-party skill set as workspace deps. Third-party `huxley-skill-*` packages have no documented install path.
- No skill today declares a config schema; per-skill config (e.g. `news.country`, `telegram.api_id`) is hand-typed into `persona.yaml`.
- Per-skill secrets (Telegram creds, OAuth tokens) have no standardized storage. T2.8 is the queued one-off fix; T1.14 generalizes it.

### Design (Gate 2 — v1 locked 2026-05-01 after critic round 1)

The full architectural specification is [`docs/skill-marketplace.md`](./skill-marketplace.md). It splits the feature into v1 (developer-primary, what this entry covers) and v2 (caregiver expansion, deferred).

**v1 locked decisions** (table from spec):

| #   | Decision                                                                                                                                                                                             |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Audience: developer / contributor / self-hosting installer.** The elderly end user is not the installer either way (caregiver-installer is v2's audience).                                         |
| 2   | **Distribution: PyPI public packages** (`huxley-skill-<name>`), with `git+https://...` for private/in-development skills.                                                                            |
| 3   | **Discovery: static `docs/skills/index.md` directory page.** No JSON registry, no GitHub repo, no CI. v2 promotes this to a structured registry.                                                     |
| 4   | **Install: `uv add huxley-skill-foo` + manual restart.** No PWA install path, no `os.execv` self-restart machinery in v1 — that's v2's hardest engineering work and it deserves its own design pass. |
| 5   | **Configure: edit `persona.yaml` + drop secrets in `<persona>/data/secrets/<skill>/values.json`.** Authoring docs + the `huxley-skill-stocks` reference skill establish the convention.              |
| 6   | **Per-persona enable/disable: edit `persona.yaml` `skills:` list.** Already how it works today.                                                                                                      |
| 7   | **Trust: author-trusted (read the source).** No registry curation in v1; that's v2.                                                                                                                  |

**v1-v2 contracts** (the primitives that don't change between versions, so v2 is purely additive — see spec § Cross-version contracts):

- `Skill.config_schema: ClassVar[dict | None] = None` — **OPTIONAL**. Skills with simple configs declare a JSON Schema; skills with complex configs (i18n maps, list-of-records, contact dicts) leave it `None`. v2's PWA only renders forms for opt-in skills; falls back to "manual config required" for the rest.
- `Skill.data_schema_version: ClassVar[int] = 1` — persisted in the per-persona `schema_meta` table under key `skill_version:<name>` (existing table, just a new key prefix). v1 logs a `warning` event on mismatch (`skill.schema.upgrade_needed` / `downgrade_detected`); v1 does **not** auto-migrate. v2 gates cross-major upgrades behind explicit confirmation. Storage location pinned now so v2 layers UX on top without changing v1 on-disk format.
- `ctx.secrets: SkillSecrets` — `async get/set/delete/keys` (async to match `SkillStorage`). On-disk shape is **always** a flat `dict[str, str]`. Skills with nested data (OAuth refresh state) JSON-encode the dict themselves into a single key like `oauth_state`. v2's `set_json/get_json` is sugar over the same on-disk shape — additive, no migration. Pinned in spec § Secrets storage layout.
- Secrets storage layout: `<persona.data_dir>/secrets/<skill>/values.json`, perms `0700/0600`. No encryption-at-rest in v1 (defer until shared-machine deployments emerge).
- Persona.yaml stays the source of truth for both v1 and v2 (v2 PWA writes via `ruamel.yaml` round-trip to preserve comments + ordering).
- **Privacy carve-out for T1.13**: v1 skills install into the shared `uv` workspace venv. Per-persona privacy lives in the persona's data dir + `persona.yaml.skills:` enable list, **not** the venv. The enable list IS the trust boundary at runtime — a persona that doesn't list a skill never imports its setup code. v2 may revisit per-persona venvs if the threat model demands it; v1 does not.

### Critic Notes (round 1 — design — complete 2026-05-01)

Critic agent reviewed the original v1+v2-as-one-feature spec. Verdict: the spec architected for an audience that doesn't exist yet (the caregiver-installer), buying optionality at 4-5 weeks of engineering when the marketplace thesis is unproven. Most findings dispositioned by splitting into v1 (developer-primary, ~1.5 weeks, what we're building) and v2 (caregiver expansion, deferred).

- **§1 — caregiver-vs-end-user conflation.** The original spec said "end user" while meaning "caregiver-installer"; the elderly user is never the installer. **Incorporated**: spec now distinguishes _skill author_, _caregiver / installer_, and _end user_ explicitly. v1 audience is the developer-installer; v2 audience is the caregiver-installer.
- **§2 — Phase 1 alone doesn't ship marketplace value.** The original Phase 1 (PWA Skills panel without install) was "a settings panel for stuff you already installed via uv add" — the secrets-out-of-YAML refactor dressed as marketplace work. **Incorporated**: dropped the PWA panel from v1 entirely. v1's deliverable is SDK primitives + a third-party reference skill + authoring docs + static directory page. The PWA panel is v2 work. (Reference skill choice was originally Spotify; round 2 swapped it to `huxley-skill-stocks` — see Round 2 notes below.)
- **§3 — simplest alternative.** Critic suggested ship-Phase-1-plus-doc and stop. **Incorporated**: that's almost exactly what v1 is now (minus the panel). Earns the optionality.
- **§4 — `os.execv` self-restart hand-waved.** Real foot-guns (SQLite WAL torn state, partial-pip-install bricked venv, C-extension compile time on a Pi). **Incorporated**: deferred to v2 entirely. v2's design phase will engage seriously (probably staged-venv-and-swap pattern).
- **§5 — JSON Schema doesn't fit complex skill configs** (audiobooks i18n, telegram contacts dict). **Incorporated**: `config_schema` is OPTIONAL. Skills opt in; complex skills leave it `None`. v2's PWA shows "manual config required" for opt-out skills.
- **§6 — secrets layout concerns.** iCloud sync, OAuth refresh-token shape, schema versioning. **Incorporated** (partially): spec documents the no-sync warning + adds `data_schema_version`. OAuth-refresh shape: v1 callers JSON-encode strings into the secret; v2 may add `set_json` additively.
- **§8 — PR-based registry maintainer load.** **Incorporated** (deferred): no registry in v1. Mario's PR-review burden in v1 is reviewing PRs to `docs/skills/index.md` (low: ~one paragraph per submission). v2 design will engage with the tier system + delisting mechanism.
- **§9 — distribution gaps** (private skills, version skew, dep conflicts). **Incorporated**: spec documents `git+https://...` as supported in v1 for private/dev. Version skew + dep conflicts: developers handle themselves in v1; v2 gets explicit story.
- **§10 — skill data migration on upgrade.** **Incorporated**: `data_schema_version` is part of v1 SDK. Runtime logs warnings; v2's PWA gates cross-major upgrades.
- **§11 — interactions with T1.11/T1.13/proactive turns.** **Incorporated** (deferred): all v2 concerns since they require install machinery.
- **§12 — umbrella + sub-entries pattern overengineered.** **Incorporated**: dropped the umbrella. This entry covers all v1 phases as a single triage item with phase status checkboxes inside (matching T1.13's shape).
- **§15 — "both audiences first-class" was the load-bearing decision.** **Incorporated**: v1 = developer-primary only. The "both audiences" framing is reframed as "v1 serves developers; v2 layers caregiver UX on top of v1's primitives."

### Critic Notes (round 2 — design — complete 2026-05-01)

Critic agent reviewed v2 of the spec (after critic-round-1 split into v1+v2). Verdict: **not ready to implement, three real issues need fixing before code starts.** All incorporated:

- **Replace Spotify reference skill — OAuth-with-1hr-expiry is hostile to a v1 demo.** Spotify access tokens expire in 1 hour; the smoke-test path "install + use" stops working an hour after token paste, and fixing it properly drags real OAuth refresh into v1 (out of scope per row 4 of locked decisions). **Incorporated**: dropped Spotify. After Mario's pushback on weather (overlaps with existing `huxley-skill-news` Open-Meteo integration), settled on `huxley-skill-stocks` (Alpha Vantage). API-key-only, no expiry, free tier requires the key (proper test of secrets routing), and `config_schema` exercises three JSON-Schema shapes (secret string, array, enum) the v2 form-renderer must support — strictly more SDK surface validated than weather would have.
- **Resolve instance-venv question; document T1.13 privacy carve-out.** v1 ships skills into a shared `uv` workspace venv, not per-persona venvs. T1.13's "filesystem-enforced privacy" guarantee needs an explicit carve-out: per-persona privacy lives in the data dir + `persona.yaml.skills:` enable list, NOT the venv. **Incorporated**: spec § "Privacy carve-out for T1.13 (shared workspace venv)" pins this. Restart docs cover single-instance, launchd-supervised, and multi-instance (T1.13) cases.
- **Pin OAuth-blob convention + `data_schema_version` mismatch behavior.** Both were claimed "additive in v2" but only true if v1 normalizes the on-disk shape now. **Incorporated**: (a) Secrets are ALWAYS a flat `dict[str, str]`. Nested data JSON-encoded into a single key. v2's `set_json/get_json` writes the same on-disk bytes — no migration. Spec § Secrets storage layout shows the canonical OAuth-blob example. (b) `data_schema_version` persists in the existing `schema_meta` table under key `skill_version:<name>`. Mismatch behavior pinned: v1 logs warning, does NOT auto-migrate; v2 gates upgrades behind explicit confirmation. Spec § Schema versioning.

Smaller fixes also incorporated:

- `SkillSecrets` is **async** to match `SkillStorage` (both are filesystem I/O from async contexts).
- First-party `config_schema` adopter is `huxley-skill-search` (DuckDuckGo, simple `safesearch` enum + `region` string), not `huxley-skill-news` — search has a cleaner schema for the "first opt-in" demo without dragging API-key-secret routing onto a first-party skill.
- `huxley-skill-stocks` lives in **its own repo from day 1** (not `server/skills/spotify/`-style) — proves the third-party flow more authentically.
- `ruamel.yaml` round-trip noted for v2 PWA YAML editing (preserves comments + ordering).
- Restart story documents launchd `kickstart -k` and T1.13 multi-instance cases.
- Auto-aggregating the index page from per-skill frontmatter is a v2 nice-to-have; v1 hand-curates `docs/skills/index.md`.
- Cut narrative fluff (intro paragraphs, decorative ASCII art, duplicated v2-open-questions section).

### Critic Notes (round 3 — design — complete 2026-05-01)

Critic agent reviewed v3 of the spec (post-round-2 + stocks pivot). **Verdict**: "one or two small fixes, then ready" — leaning ready. All paper fixes, no redesign.

- **`ctx.skill_config` is the wrong attribute name.** Spec line 277 (now corrected) said skills read non-secret config from `ctx.skill_config`. The actual attribute on `SkillContext` is `ctx.config` (see `server/sdk/src/huxley_sdk/types.py:836`; every existing first-party skill reads `ctx.config`). Factual error in the worked-example contract — copy-paste authors would hit `AttributeError`. **Incorporated**: `ctx.skill_config` replaced with `ctx.config` everywhere in spec.
- **Schema-version event semantics on persona swap.** Spec didn't say the equal-version case is silent; T1.13 calls `setup()` on every reconnect, so a naive "checked" event would create log noise. Also didn't address torn-swap atomicity. **Incorporated**: spec § Schema versioning now says equal is silent (no event), the version-write happens in the same DB transaction as init writes (so torn swap rolls back atomically), and adds a DoD test "swap personas 3× → no `skill.schema.*` events fire after first boot."
- **Authoring docs at "1 day" is the wrong order of magnitude** for a doc the entire third-party ecosystem hangs on. **Incorporated**: bumped to 2-3 days; v1 effort estimate updated to ~2 weeks; added DoD bullet "self-test: walking the doc verbatim from a clean checkout produces a working install — if a step doesn't work as written, the doc is the bug."

Smaller fixes also incorporated (all in spec):

- **`huxley-skill-search` schema scoped to actual existing fields**: just `safesearch` enum. The originally claimed schema (`safesearch` + `region` string) would have conflated "adopt schema" with "add new feature" since `region` doesn't exist today. `start_sound` / `sounds_path` are persona-author / framework-shared fields, stay un-schemaed.
- **i18n merge interaction with `config_schema`**: schemas describe the **post-merge** view of `ctx.config`. Skills that use the i18n merge stay schemaless.
- **OAuth-blob corruption recovery**: skills `try: json.loads except json.JSONDecodeError: return None`; recovery is `await ctx.secrets.delete("oauth_state")` + re-auth. v1 does not auto-quarantine.
- **Gitignore inheritance**: secrets land at `<persona>/data/secrets/` which inherits the existing `<persona>/data/` gitignore exclusion. **No new `.gitignore` rule needed.**
- **Stocks bootstrap chicken-and-egg**: during v1 dev, stocks pins SDK as a `uv` path dep against Huxley repo's main; once Phase 1 publishes `huxley-sdk` to PyPI, switches to versioned pin (and that's the path the authoring docs document for external authors).
- **Stocks repo CI scope**: `ruff` + `mypy --strict` + `pytest` + a publishing GH Action template. Out of scope: coverage gates, signing, distribution beyond PyPI.
- **Smoke persona pinned to `basic`** (not Abuelo — stocks isn't a credible voice intent for the elderly Spanish-language end user; basic has no constraints to interfere with the plumbing-focused test).
- **`docs/skills/index.md` per-entry metadata schema** (5 fields: `name`, `description`, `install`, `docs_url`, `tier`) so the PR template is mechanical.

**What round 3 explicitly approved as solid** (so the implementer knows where the design is load-bearing): OAuth-blob convention, T1.13 privacy carve-out, `schema_meta` reuse, stocks-vs-Spotify-vs-weather decision, `SkillSecrets` async, v1/v2 split.

### Definition of Done (v1)

- [x] T2.8 prerequisite landed (per-persona secrets dir pattern in place; telegram skill is the first adopter).
- [x] **Phase 1: SDK additions** — `Skill.config_schema: ClassVar[dict | None] = None`, `Skill.data_schema_version: ClassVar[int] = 1`, `SkillContext.secrets: SkillSecrets` with **async** `get/set/delete/keys`, backed by `<persona.data_dir>/secrets/<skill>/values.json` (perms `0700/0600`). `data_schema_version` persists in `schema_meta` table under `skill_version:<name>`; mismatch logs `skill.schema.upgrade_needed` / `skill.schema.downgrade_detected`, no auto-migration in v1. Tests + mypy + ruff + per-package pytest green.
- [x] **`huxley-skill-search` adopts `config_schema`** as the first-party validation of the JSON-Schema convention (clean schema: `safesearch` enum only — `region` was descoped per round-2 to avoid conflating schema adoption with adding a new feature). Backward-compat: existing skills with `config_schema=None` still load.
- [x] **Phase 2: `huxley-skill-stocks` reference skill** — own repo at `~/Projects/Personal/Code/huxley-skill-stocks/` (not `server/skills/`). Initial commit `9d92ef6`. Declares `config_schema` with `api_key` (secret) + `watchlist` (array) + `currency` (enum). Uses async `ctx.secrets` for the API key. Three voice tools (`get_stock_price`, `get_watchlist_summary`, `compare_stocks`). 40 tests, ruff + mypy --strict + pytest green. CI workflow scaffolded for ubuntu-latest. Path-dep against `../Huxley/server/sdk` during v1 dev; switches to PyPI pin once `huxley-sdk` is published.
- [x] **Phase 3: `docs/skills/authoring.md`** — published. Worked example: `huxley-skill-stocks`. Covers project structure, the Skill protocol, `config_schema` convention with the two custom JSON-Schema extensions (`format: secret` + `x-huxley:help`), async `ctx.secrets` API, OAuth-blob convention with corruption recovery, `data_schema_version` semantics, soft-fail patterns, two-layer error classification, fully-offline test patterns, distribution + directory PR.
- [x] **Phase 3 self-test**: Mario installed `huxley-skill-stocks` from PyPI into `basic` per authoring.md; the steady-state install path (`uv add huxley-skill-stocks` + secrets dir + `persona.yaml` block + restart) worked verbatim. Voice smoke: "what's Apple stock at" routed to `get_stock_price`, returned real $280.14 quote.
- [x] **Phase 4: `docs/skills/index.md` directory page** — published. Static curated list of all 8 first-party skills + `huxley-skill-stocks` following the 5-field per-entry metadata schema (`name`, `description`, `install`, `docs_url`, `tier`). PR submission instructions included.
- [x] Mario smoke (2026-05-02): installed `huxley-skill-stocks` into `basic` persona, configured the Alpha Vantage `api_key` via `secrets/stocks/values.json` (perms `0700/0600`, gitignored). PTT: "what's Apple stock at" → `get_stock_price(symbol="AAPL")` → real quote $280.14 read aloud. API key did NOT appear in `git diff`. v1's developer-primary path is genuinely usable.
- [x] Persona-swap stability test: T1.13 smoke (`6b58dbe8`...`06d83c5d`) and the dedicated unit test [`test_skill_schema_version.py::test_three_consecutive_swaps_silent_after_first`](../../server/runtime/tests/unit/test_skill_schema_version.py) both confirm `skill.schema.*` events fire only on first boot of each (skill, persona) pair. Subsequent T1.14-era persona swaps via `?persona=` reconnect with stocks enabled stayed silent in the runtime log.
- [x] `ruff check server/` + `mypy server/sdk/src server/runtime/src` + per-package pytest all green.

### Implementation order

Phases are tracked inside this entry per critic-round-1 §12 (no umbrella + sub-entries). When work begins on each phase, flip its checkbox to in_progress and log lessons inline below.

| #   | Phase                  | Scope                                                                                                                                                                                                                                                                                                                                                                                            | Effort    |
| --- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------- |
| 0   | T2.8 (separate entry)  | Move telegram creds → `<persona>/data/secrets/telegram/`. Establishes the secrets-dir pattern T1.14 generalizes.                                                                                                                                                                                                                                                                                 | ~1 hour   |
| 1   | SDK additions          | `Skill.config_schema`, `Skill.data_schema_version` (persisted in `schema_meta`), async `SkillContext.secrets`. Tests, docstrings, backward-compat (defaults preserve existing behavior). `huxley-skill-search` adopts `config_schema` as the first-party validation.                                                                                                                             | ~2-3 days |
| 2   | Stocks reference skill | `huxley-skill-stocks` end-to-end (own repo, Alpha Vantage, secret + array + enum config). Pressure-tests SDK additions on a real third-party-shaped skill.                                                                                                                                                                                                                                       | ~3-4 days |
| 3   | Authoring docs         | `docs/skills/authoring.md` with worked example referencing `huxley-skill-stocks`. Covers project structure, Skill protocol, async secrets API, OAuth-blob convention (even though stocks doesn't need it — future OAuth skills have a documented path), PyPI publishing. **Self-test**: Mario walks the doc verbatim from a clean checkout; doc fails the DoD if a step doesn't work as written. | ~2-3 days |
| 4   | Static directory page  | `docs/skills/index.md` listing known skills with install commands. First-party skills + `huxley-skill-stocks`.                                                                                                                                                                                                                                                                                   | ~½ day    |

### v1 cross-phase open questions

Tracked in spec doc; v1-relevant subset:

_All Phase-1/2 questions resolved in critic round 2:_

- **Reference skill choice**: `huxley-skill-stocks` (Alpha Vantage). Not Spotify (1-hr token expiry forces OAuth refresh into v1), not weather (overlaps existing news/Open-Meteo).
- **Reference skill location**: own repo from day 1. Proves the third-party flow more authentically than in-repo `server/skills/`.
- **First-party `config_schema` adopter**: `huxley-skill-search` (DuckDuckGo, simple `safesearch` enum + `region` string). Cleaner than `news` for the first opt-in, no API-key complexity dragged onto a first-party skill.

Open at implementation time:

- Whether `huxley-skill-stocks` ships v1.0.0 to PyPI in Phase 2 or stays as a `git+https://...` install until authoring docs validate the publish flow. Pick during Phase 2.

### v2 (deferred) open questions

Documented in [`docs/skill-marketplace.md` § v2 open questions](./skill-marketplace.md). Each gets engaged during v2's design phase if/when caregiver-shipping is decided. NOT v1 blockers.

### Lessons

**Phase 1 (SDK additions)**:

- **`ClassVar` defaults on a `runtime_checkable` Protocol + `getattr` on the runtime side is a robust opt-in pattern.** Existing skills that don't declare `config_schema` / `data_schema_version` continue to load without modification; the Protocol's defaults give mypy something to type-check against when skills DO declare them. The dual-write defense (declared default + `getattr` with a default) is belt-and-suspenders for old SDK consumers.
- **The "split check from persist" pattern landed via the post-Phase-1 critic round.** The original implementation wrote schema versions in the same loop as the mismatch check; a torn `setup()` would advance `schema_meta` to the new version while skill state was uninitialized. The split (check before `setup_all`, persist after) means a torn setup leaves the OLD version intact and the next boot re-warns the same way. Cheap fix, real bug.
- **`asyncio.Lock` per-instance is the right granularity for `JsonFileSecrets`.** Cross-persona contention is impossible (different paths, different instances) so a global lock would have been overkill; same-skill concurrent writes are the only real race and the lock catches them.
- **The OAuth-blob convention (flat `dict[str, str]` + skill-side json-encoding for nested data) needs to handle hand-edited values.json gracefully too.** The `_read` coercion (`json.dumps if isinstance(v, dict | list) else str(v)`) makes the round-trip lossless whether a skill writes via `set()` or a user hand-edits a nested literal.

**Phase 2 (huxley-skill-stocks reference skill)**:

- **The two-layer error classification (provider raises typed errors → skill catches and translates) is the load-bearing pattern for any skill talking to a flaky third-party API.** Provider tests pin the HTTP surface; skill tests pin the user-facing behavior. Each layer is testable in isolation.
- **`_ok` / `_error` module-level helpers + `say_to_user` payload field** is the canonical convention copied from search/news. Worked example for the docs is now in stocks.
- **Worked-example skills must use real GitHub slugs.** The post-Phase-2 critic caught `marioruizsa` references in three files; the actual user is `ma-r-s`. Authors copying the CI workflow would've hit a 404 on first push. Always verify URLs against `git remote -v` before committing.
- **Skill packages need explicit LICENSE files** even when `pyproject.toml` declares the license — PyPI publishing flags it, downstream copyists lose it.

**Phase 3 (authoring docs)**:

- **The doc's job is to teach the load-bearing patterns, not document every feature.** Authoring.md walks through `huxley-skill-stocks` annotating each pattern (provider/skill split, `_ok`/`_error`, soft-fail, OAuth-blob, two-layer errors). The reference repo is the source of truth; the doc is annotation.
- **"If the doc is wrong, file an issue."** The self-test DoD bullet is intentionally framed as falsifiable — Mario walks the doc verbatim and any discrepancy is a doc bug.

**Phase 4 (directory page)**:

- **A static markdown table with a per-entry metadata schema is sufficient registry for v1.** v2 will promote to JSON + structured registry + PWA Marketplace tab; today's table is the discovery surface that earns the optionality.

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
- [ ] All personas updated (`server/personas/abuelos/persona.yaml`, `server/personas/basic/persona.yaml` if referenced)
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

**Status**: done (2026-05-01) — **prerequisite for T1.14** delivered. The per-persona `secrets/<skill>/values.json` pattern this item established is the foundation T1.14 generalizes via `ctx.secrets`. **Effort actual**: ~1.5 hours (original 1h estimate undersold because the original Validation misread the loader — see corrected Validation below).

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

### Validation (Gate 1 — corrected 2026-05-01)

The original entry described the symptom but misread the cred-loading code. Corrected understanding:

- **Root files `telegram` + `telegram.phones`**: Mario's hand-exported reference notes (the my.telegram.org/apps page text + a phone-number memo from the T1.10 spike). **No code reads them.** They exist only because Mario put them there during the spike and never moved them.
- **Actual cred-loading** (`server/skills/telegram/src/huxley_skill_telegram/skill.py:425-427`): `os.environ.get("HUXLEY_TELEGRAM_API_ID") or cfg.get("api_id")`. Same pattern for `api_hash`, `userbot_phone`. Env vars in `server/runtime/.env` are the actual source of truth today; persona.yaml is a fallback.
- **Contacts** come from `cfg.get("contacts")` in `persona.yaml` `skills.telegram.contacts:` — NOT from `telegram.phones`. The root `telegram.phones` file is dead reference data.
- `.gitignore` lines 11-12: `/telegram` and `/telegram.*` anchored to repo root.

So T2.8 is two things at once:

1. **Bookkeeping**: kill the dead reference files at repo root.
2. **Pattern foundation**: introduce a per-persona `values.json` loader path that T1.14 will generalize via `ctx.secrets`.

### Design (Gate 2 — trivial, skip critic)

1. **Introduce a per-persona JSON cred file** at `<persona.data_dir>/secrets/telegram/values.json` containing `{"api_id": "...", "api_hash": "...", "userbot_phone": "..."}` (all string values, matching T1.14's flat-`dict[str, str]` shape).
2. **Telegram skill cred resolution priority order** (in `setup()`):
   1. `<persona.data_dir>/secrets/telegram/values.json` (NEW — the T1.14-compatible path)
   2. `os.environ.get("HUXLEY_TELEGRAM_*")` (existing env-var fallback; keeps the running server alive during the transition)
   3. `cfg.get("api_id")` etc. from persona.yaml (existing fallback for dev/test)
3. **Migrate Mario's creds**: write the env-var values into the new `values.json` at `server/personas/abuelos/data/secrets/telegram/values.json`. Once T1.14 ships and the values.json path is the canonical source, the env vars in `.env` can be dropped (deferred to a later cleanup; T2.8 keeps them as fallback so nothing breaks mid-transition).
4. **Clean up repo root**: delete `/telegram` and `/telegram.phones` from the repo root (they are gitignored, so this is a local `rm`, no commit). The hand-exported reference content from `/telegram` (api_id/api_hash + the my.telegram.org boilerplate Mario pasted) goes into `<persona>/data/secrets/telegram/source.txt` for Mario's own future reference. `/telegram.phones` is dead spike data; archived under the same dir as `notes.txt` rather than deleted, so Mario can decide later.
5. **Update `.gitignore`**: drop the root-anchored `/telegram` and `/telegram.*` patterns. The per-persona data dir is already gitignored via `server/personas/*/data/`, so the new location is automatically covered.
6. **Update the comment** in `skill.py:420-424` to point at the new location and reflect the new priority order.
7. **Skill regression test**: assert the loader respects `values.json` when present and falls back to env vars when absent.

### Definition of Done

- [x] `<persona>/data/secrets/telegram/values.json` loader added to skill (`_load_creds_from_secrets_file` helper).
- [x] Cred resolution priority: values.json → env vars → persona.yaml. Env vars stay as fallback during transition.
- [x] Mario's creds migrated: `server/personas/abuelos/data/secrets/telegram/values.json` (perms `0700` dir, `0600` file).
- [x] Repo-root reference files moved into `<persona>/data/secrets/telegram/`: `telegram` → `source.txt`, `telegram.phones` → `notes.txt`.
- [x] `.gitignore` cleaned: root-anchored `/telegram` and `/telegram.*` patterns dropped; comment updated to point at the new location.
- [x] `skill.py` cred-loading comment rewritten to describe the new priority order.
- [x] Existing telegram test suite still green (was 90, now 93 with 3 new regression tests).
- [x] Regression tests: `test_secrets_file_takes_precedence_over_env_and_yaml`, `test_secrets_file_only_also_works`, `test_malformed_secrets_file_falls_back_to_env`.
- [x] `docs/skills/README.md` § "Per-skill secrets" added.
- [x] `docs/skills/telegram.md` § "Credentials" rewritten with the priority order + JSON shape.
- [x] After ship: `ls Huxley/` shows zero `telegram*` entries.

### Tests (Gate 3)

Three regression tests (replace the original "loads_creds_from_configured_secrets_dir" plan with three sharper cases):

- `test_secrets_file_takes_precedence_over_env_and_yaml` — values.json beats env vars beats persona.yaml.
- `test_secrets_file_only_also_works` — file alone, no env vars, no persona.yaml fields.
- `test_malformed_secrets_file_falls_back_to_env` — corrupt JSON does not break boot; falls through to env-var fallback.

### Docs touched (Gate 4)

- [x] `docs/skills/README.md` — § "Per-skill secrets" added.
- [x] `docs/skills/telegram.md` — § "Credentials" rewritten with priority + JSON shape; `api_id`/`api_hash`/`userbot_phone` removed from the persona.yaml example block.
- [x] `.gitignore` — root-anchored `/telegram*` block dropped; the `*.session` lines moved under a new comment block pointing at the new secrets-dir convention.
- [x] `server/runtime/.env` (gitignored, comment-only update) — env-var values retained as fallback; comment rewritten to point at values.json as preferred source.
- [x] `docs/triage.md` (this entry) — Validation corrected (the original misread the cred-loading code), Definition of Done flipped, lessons captured below.

### Lessons

- **The triage entry's Validation must reflect what the code actually does, not what we remember.** The original Validation said the skill reads files at repo root; in reality it reads env vars, and the root files are dead reference notes. Reading the code carefully before writing the design saves the round-trip.
- **The values.json shape is now under contract** — it's what T1.14's `ctx.secrets` will read. Any changes between now and then need to preserve the flat `dict[str, str]` invariant (or T1.14 will need a migration step).
- **Env vars stay as fallback during the transition** — keeping a fallback path means the running server doesn't break if the secrets file is moved/renamed/deleted accidentally. Drop them once T1.14 ships and the per-persona pattern is canonical.

### Why deferred

Bundled with the 2026-04-28 restructure proposal but explicitly held back
because (a) it requires a small skill code change, not just file moves, and
(b) keeping that restructure layout-only made the diff easier to review.
Trigger to revisit: any session that's already touching the telegram skill
for an unrelated reason — fold this in then.

## T2.9 — Migrate huxley-skill-telegram to ctx.secrets

**Status**: done (2026-05-01). Follow-up to T2.8 + T1.14 Phase 1. **Effort actual**: ~20 min, plus ~30 min to declare `config_schema` + `data_schema_version` ClassVars across the other 6 first-party skills for consistency.

**Problem.** The telegram skill still loads creds via the file-based helper `_load_creds_from_secrets_file()` introduced in T2.8, even though T1.14 Phase 1 shipped the official `ctx.secrets` API that reads the SAME on-disk file. The helper was the right call before the SDK had a proper accessor, but keeping it after Phase 1 creates two ways to do the same thing — confusing for future skill authors who copy the telegram skill as a reference.

**Why it matters.** Authoring docs (T1.14 Phase 3) reference `ctx.secrets` as THE convention. If the canonical first-party adopter isn't using it, the docs ship with a contradiction. Plus: the helper duplicates the same json-encode-on-read coercion that `JsonFileSecrets._read` already does, so there's a real maintenance overhead.

**Plan.** In `server/skills/telegram/src/huxley_skill_telegram/skill.py`:

1. Replace the three `_load_creds_from_secrets_file(...)` results with three `await ctx.secrets.get("api_id" / "api_hash" / "userbot_phone")` calls (the api/hash/phone keys map 1:1 to the file's keys, so the on-disk file doesn't need to change).
2. Delete the `_load_creds_from_secrets_file` helper from the skill (it's a duplicate of `JsonFileSecrets._read` with identical semantics).
3. Update the priority-order comment to say "values.json (via `ctx.secrets`) → env vars → persona.yaml".
4. Test fixtures already provide a no-op `FakeSecrets` (added in Phase 1); existing values-file tests should be rewritten to populate `FakeSecrets` instead of writing actual JSON files.

**DoD**: ruff + mypy + 97 telegram tests still green; the helper is gone.

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

**Done by T1.44** (2026-10-12, branch `t144-inject-waits`): `expires_after` on `inject_turn` / `inject_turn_and_wait`, and `inject_turn_and_wait` returns EXPIRED instead of an `on_dropped` callback; reminders mark a fire nobody heard in its late window as missed.

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

## T1.15 — Marketplace v2 (caregiver expansion)

**Status**: done (2026-05-02; Mario voice smoke pending — implementation, planning critic, and post-impl critic-fixes all landed overnight). v2 was originally deferred per the T1.14 spec; Mario greenlit it as the next workstream after PyPI shipped.

**Commits**:

- Phase A (read-only Skills panel + Marketplace tab placeholder): `c3c91650` server, `e856f6cf` PWA cards/tabs, `2904d9b4` critic-round-1 (email scrub, current_config scrub, swap-push), `45808040` full-screen sheets, `949729f3` animation polish.
- Phase B (writes via hot reload): `c8dd6fd5` ruamel persona.yaml, `86958d70` `_reload_current_persona` + `force` flag, `81470590` server WS handlers, `e022b21e` PWA editable inputs, `cd59d685` critic-round-1 (mid-call guard, in-flight save, write-lock, docs).
- Phase C (Marketplace registry browse): `1bf13691` registry feed → cards, `52f22120` critic-round-1 (max-body cap, encodeURIComponent, refetch on error).
- Phase D (auto-install + `os.execv` self-restart): `c328dfcc` end-to-end with planning critic upfront, post-impl critic round 1 in this commit set (mid-call gate covers active streams via `is_busy` accessor; Maintenance Restart button gated server-side; `_active_stream_id` tracking; PWA regex aligned with server; 30s overlay watchdog; hello-fallback transition for dropped install_event; ticking elapsed counter; Esc-to-close on confirm modal; 3 new tests for the gating paths).

**Problem.** v1 (T1.14) shipped the developer-primary substrate: SDK + reference skill + authoring docs + PyPI distribution. Useful for skill authors, useless for caregivers. v2 layers a PWA-driven UX on top of v1's primitives: browse, install, configure, toggle — without leaving the assistant.

**Why it matters.** Caregivers (and Mario as the operator-of-Huxley-for-Abuelo) can now extend Huxley without touching `persona.yaml` or running `uv add` from a terminal. Closes the gap between "the framework supports skills" and "the operator can install one without instructions."

**Effort actual**: ~12 hours of continuous work, four phases shipped sequentially, three planning/post-impl critic rounds. Mario was asleep for Phase D — the planning critic ran upfront per his directive, post-impl critic + fixes ran overnight.

**Lessons**:

1. **Critic-before-implementation pays for the foot-gunny work.** Phase D had explicit "block on these conditions before merge" findings from the planning critic. Building the standalone restart machinery first (per planning critic §B) caught FD-leak / EADDRINUSE concerns separately from install-subprocess concerns; either bug would have been identifiable in isolation.
2. **Locked decisions stay locked.** Mario approved 5 product decisions before bedtime; the implementation honored them all without drift. The post-impl critic caught one violation (mid-call gate ignored streams) which was incorporated.
3. **`os.execv` works in dev (`uv run huxley`) and prod (launchd) without a wrapper.** PID is preserved; launchd doesn't notice the re-exec. Verified empirically via the standalone restart smoke before wiring install.
4. **Discriminated event frames future-proof a state machine.** Phase D's `install_event {kind: "started" | "complete"}` lets v2 (uninstall, queued, rolled_back) slot in additively without new top-level frame types. Planning critic §E.1.
5. **Public accessors over private-attr reach.** The implementation initially reached into `audio_server._active_claim_id`; post-impl critic flagged + the fix added `has_active_claim`, `has_active_stream`, `is_busy` accessors. Renames now break the gate at compile time, not runtime.

**Deferred to post-D**:

- Uninstall (`uv remove`). Symmetric foot-guns; needs its own design pass.
- Per-skill detail sheet inside the PWA (currently the Marketplace card click opens the registry's GitHub page externally).
- Boot-loop quarantine: if a freshly-installed skill's `setup()` raises, the runtime's lazy-boot path now sends a `status` frame to the PWA (Phase D post-impl critic §3 mitigation), but the broken skill still gets re-tried on every connect. A "quarantine after N consecutive failures" path would be cleaner.
- PWA-side install/uninstall test coverage (per Phase B + C critics).

**Triage descendants** (queued from the four critic rounds):

- Audit all 9 first-party skills for missing `teardown()` methods (Phase B critic finding) — chip task already filed.
- Singleflight lock for marketplace fetch concurrency (Phase C critic §1) — cheap stampede; relevant when Phase D's "refetch after install" runs concurrently from multiple tabs.
- Federation registry-URL override via env var (Phase C critic §9) — speculative until first operator asks.

---

## T1.16 — Session-readiness signal (don't tell user "ready" when not)

**Status**: done (2026-05-02 · `d3cc5509`). Mario voice smoke pending.

**Problem.** Mario reported: on cold reload, PTT plays the listening + thinking tones locally but the turn never completes. Skills panel sits on "Loading…" forever. Refresh doesn't fix it. Intermittent — sometimes the same reload works.

**Validation.** Reading `/private/tmp/huxley_server.log` around the failure window showed three compounding root causes:

1. **No "session ready" signal.** The server sent `state: IDLE` after every handshake regardless of whether `_shim_persona_select` had actually succeeded. If lazy-boot persona-load raised, the exception was caught at `server/server.py:343`, logged, and the connection proceeded as if everything were fine. PWA showed "Hold to talk" but `current_app is None`, so every PTT silently dropped.
2. **`_shim_get_skills_state` silently dropped responses on `build_skills_state` exception.** The `try/except → log → return` shape meant the PWA got no reply at all when the runtime hit a malformed entry-point — perpetual "Loading…" with no way for the client to distinguish "request lost" from "server raised."
3. **`get_skills_state` was fire-once on sheet mount.** Under React StrictMode's double-mount + the server's `client_evicted` race (visible in the log as same-millisecond connect/evict/disconnect storms), the request landed on a socket that had been replaced on the server; the response was sent on a socket the PWA had already discarded as stale. PWA never retried.

The third one was the StrictMode-amplified instance of the broader pattern: any time a frame is fire-once, a socket-replacement race can lose it.

**Why it matters.** "PTT looks ready but does nothing" is the worst kind of failure — the user has no diagnostic affordance and no way to tell the system from a cold socket. For the Abuelo target user (blind, audio-only), this is uniquely dangerous — there's no visual cue to "actually you should retry."

**Design + critic** (collapsed; this is a 2-hour fix, not Tier 1):

- Add `session_ready: bool` + `session_error: string | null` to hello extras. Strict additive — old PWA defaults `sessionReady=true`. Server sets `_last_session_error` in `_shim_persona_select` on raise; clears on success.
- PTT + orb hint gate on `sessionReady` in `App.tsx`. When false, show the server's `session_error` string instead of "Hold to talk." `pressPtt()` early-bails so the local audio init never starts.
- `_shim_get_skills_state` always responds: empty payload `{skills: []}` + `status` frame on internal exception, never silent return.
- `useWs.ts onopen` fires `get_skills_state` itself, not just on sheet mount. Survives StrictMode races for free.
- `useWs.ts onclose` flips `sessionReady=false` so the gap between disconnect and reconnect doesn't show stale "ready."
- Three new structured log events: `runtime.session_ready`, `runtime.session_failed`, `runtime.session_degraded` — bracket every connection's readiness lifecycle. See `docs/observability.md#session-readiness`.

**Implement.** One commit, four files server-side (`runtime.py`, `test_runtime.py`, `protocol.md`, `observability.md`) + four PWA files (`useWs.ts`, `App.tsx`, three locale files). Four new tests covering: lazy-boot failure → `session_ready=false`, successful swap → flip to `true` + clear error, swap-with-live-app → stays `true`, defensive `get_skills_state` empty-payload-on-exception path. ruff + mypy --strict + 554 runtime tests + tsc all green.

**DoD**:

- [x] `runtime.session_failed` log when lazy boot raises, with persona + error.
- [x] `runtime.session_ready` log on successful persona-select.
- [x] `_get_hello_extras` returns the new fields; old clients still work.
- [x] PTT disabled when `sessionReady=false`; orb hint shows server's error string.
- [x] `get_skills_state` request fires on every WS open, not just sheet mount.
- [x] `_shim_get_skills_state` always sends a response.
- [x] Test for each of: lazy-boot fail, swap-success-clears, swap-fail-keeps-ready, get_skills_state-defensive.
- [x] `docs/protocol.md` documents the new hello fields.
- [x] `docs/observability.md` documents the three new log events with a "diagnose this class of bug" recipe.
- [ ] Mario voice smoke: cold reload → PTT works without retries; force a broken persona via env → PWA shows `session_error` string + PTT disabled.

**Lessons** (will be filled at gate close):

- Pre-T1.16 hello carried persona/install state but no readiness signal — protocol additivity means the field can be added without a version bump, but it took a real bug to notice the gap.
- The StrictMode double-mount + server-side `client_evicted` race is a permanent feature of this dev setup. Fire-once frames are the common failure mode; assume any "send X on mount" needs a corresponding "send X on every onopen" path.
- "Log + return" is a lurking bug: it looks defensive but it strands the client. Prefer "log + send empty/error response" as the defensive pattern when the response is what unblocks the UI.

---

## T1.17 — Retry pyrogram session-DB init on `OperationalError: database is locked`

**Status**: done (2026-05-02). T1.16 made it diagnosable; T1.17 makes it not happen.

**Problem.** Cold reload of the PWA frequently triggers two near-simultaneous WS connects (React StrictMode double-mount + the server's own `client_evicted` flow). The Telegram skill's `setup()` opens a Pyrogram client whose backing SQLite session file gets locked when the previous Application's pyrogram client is still tearing down — `sqlite3.OperationalError: database is locked` on the very first `UPDATE` in `version()`. Captured live during T1.16 verification:

```
runtime.session_failed reason=lazy_boot_swap_failed persona=abuelos
  error="persona 'abuelos' failed to load: OperationalError"
```

T1.16 makes this visible to the user (PWA shows the error string instead of pretending IDLE) but doesn't recover from it. User sees "Server not ready" and has to manually reload.

**Why it matters.** It's a classic transient — a single retry with backoff would clear it on the next attempt because the previous teardown completes within hundreds of ms. Today it surfaces as a flaky cold-reload that requires manual retry, exactly the foot-gun T1.16 was supposed to make rare.

**Validation.** Reproducible by reloading the PWA twice in quick succession. Log shows `runtime.session_failed` with the OperationalError; the next reload (~5s later) succeeds.

**Effort**: ~10 lines + a unit test. Live in `huxley-skill-telegram`'s `setup()` (NOT in the runtime — this is skill-layer concern; the framework shouldn't know about pyrogram).

**Fix sketch**:

- Wrap the `Client.start()` call (or wherever `Storage.open()` happens) in a small retry loop: 3 attempts, 200ms / 500ms / 1s backoff, only on `sqlite3.OperationalError` whose message matches "database is locked".
- Each retry logged as `telegram.session_db_lock.retry attempt=N`. Final failure logged as `telegram.session_db_lock.exhausted` and re-raised so the runtime's existing failure path (now T1.16-aware) still kicks in for genuinely persistent locks.

**Lessons hint**: T1.16 made this diagnosable, T1.17 makes it not happen. Pattern: "surfacing the failure" and "not having the failure" are two separate fixes; ship the surfacing first (cheap, defensive), schedule the recovery second (right layer, narrower blast radius).

---

## T1.18 — Audit first-party skills for missing `teardown()` (and a stocks-specific fix)

**Status**: done (2026-05-02). Audit revealed 8 of 9 first-party skills already define teardown; Stocks correctly omits it (no persistent state). Real fix was the SDK guard, not per-skill changes.

**Problem.** During Mario's T1.15 voice smoke (2026-05-02 12:57), persona-swap teardown raised `AttributeError: 'StocksSkill' object has no attribute 'teardown'` and aborted the rest of `teardown_all`:

```
ERROR skill.teardown_failed skill=stocks
Traceback ...
  File ".../huxley_sdk/registry.py", line 155, in teardown_all
    await skill.teardown()
AttributeError: 'StocksSkill' object has no attribute 'teardown'
```

Caught + logged but non-fatal — the swap completed. The bug is two-shaped:

1. **`huxley_sdk.SkillRegistry.teardown_all` calls `skill.teardown()` blindly.** No `getattr(..., None)` guard, no Protocol default. Any skill that doesn't define teardown raises AttributeError. This is the framework half — the SDK's contract is unclear about whether teardown is required or optional.
2. **`StocksSkill` doesn't implement teardown.** The reference third-party-shape skill is the worst possible offender — every operator who copies it as a starting point inherits the bug. This is the skill half.

The T1.15 Phase B critic flagged that several first-party skills probably miss teardown; the chip task to audit all 9 was filed but not actioned.

**Why it matters.** Every persona swap (which T1.19 will show happens more often than necessary) hits this teardown path. The error is logged at ERROR level, which makes the operator log noisy. Worse: an AttributeError early in `teardown_all` may abort the loop before later skills' teardowns run, leaking their resources.

**Validation.** Live trace from `/private/tmp/huxley_server.log` at 12:57:58.029. Reproducible on every persona swap.

**Effort**: ~30 min for the SDK guard + audit; per-skill teardown additions vary (most are a no-op or a simple `await self._client.close()`).

**Fix sketch (SDK-first)**:

1. **SDK**: change `teardown_all` to `if hasattr(skill, "teardown"): await skill.teardown()` OR — better — make `Skill.teardown` a default `async def teardown(self) -> None: pass` in the Protocol class so subclasses don't need to opt in. Decide: required-and-explicit vs. optional-with-default. The latter is friendlier for skill authors; the former forces them to think about cleanup.
2. **First-party audit**: enumerate all 9 skills (audiobooks / news / radio / search / reminders / stocks / system / telegram / timers); for each, decide if it owns teardown-able state (open clients, background tasks, file handles) and add an explicit teardown that closes / cancels / flushes. Stocks is the canary; the others may need real teardowns.
3. **Test**: SDK contract test asserts `teardown_all` survives a skill missing teardown without losing other skills' cleanup.

**DoD**:

- [ ] SDK decision (default vs. required) documented in `docs/skills/authoring.md`.
- [ ] All 9 first-party skills audited; teardown added where state needs cleanup, no-op where genuinely stateless.
- [ ] SDK regression test: `teardown_all` with a deliberately-broken skill mid-list runs the others.
- [ ] No `skill.teardown_failed` events on a clean persona-swap smoke.

---

## T1.19 — Same-persona swap short-circuit uses display label vs directory basename

**Status**: done (2026-05-02). Same id-vs-display-name foot-gun T1.13 fixed for hello extras but missed here.

**Problem.** Live trace from 2026-05-02 12:57:52:

```
runtime.persona_swap_started from_=Basic to=basic
storage_initialized path=.../basic/data/basic.db
news.setup_complete ...
session_connected ...
state_transition CONNECTING -> CONVERSING
```

User stayed on the same persona (directory `basic/`, YAML display name `"Basic"`) — but a full swap fired anyway. The short-circuit at [runtime.py:303-305](server/runtime/src/huxley/runtime.py:303) reads:

```python
if not force and old_app is not None and old_app.persona.name == name:
    return  # no-op — already on this persona
```

`old_app.persona.name` is the YAML's display label (`"Basic"`); `name` is the requested directory basename (`"basic"`). For any persona where the two differ — which is most of them, including all four shipped (`abuelos`/Abuelo, `basic`/Basic, `chief`/Chief, `librarian`/Librarian) — the comparison fails and the runtime does a full swap-with-teardown. Wastes:

- One Realtime API handshake (~700ms latency added to the next PTT)
- Full skill `setup_all` cycle (storage init, secrets re-read, network calls in `setup` like Open-Meteo geocoding)
- Provider summary write on the OLD app's teardown
- The teardown bug T1.18 fires on every same-persona reconnect

**Why it matters.** Every PWA reload triggers a same-persona "swap" that's actually a no-op intent. With T1.19, that's a free reconnect; without it, every reload pays ~1s of needless re-setup + Realtime handshake. Multiplies the surface area for the T1.17 (pyrogram lock) and T1.18 (missing teardown) bugs because each unnecessary swap is an additional teardown + setup cycle.

**Validation.** Reproducible on any persona where YAML `name:` differs from directory basename. The four shipped personas all qualify; the test fixture `_MINIMAL_PERSONA_YAML` makes them agree by construction (`name: {dir}`), which is why the existing TestBasicSwap and TestHelloExtras tests didn't catch this — same shadowing pattern as the T1.13 `test_current_persona_is_directory_basename_not_yaml_label` regression.

**Fix sketch**:

```python
if (
    not force
    and old_app is not None
    and old_app.persona.data_dir.parent.name == name  # was: .name
):
    return
```

`data_dir.parent.name` is the directory basename, the canonical id `?persona=` resolves against. Same fix the post-T1.13 patch applied to `_get_hello_extras`.

**DoD**:

- [ ] Short-circuit uses directory basename, not YAML name.
- [ ] Regression test using a persona where `dir_basename != yaml_name` (mirror the T1.13 `test_current_persona_is_directory_basename_not_yaml_label` shape).
- [ ] Live smoke: reload PWA on same persona → no `runtime.persona_swap_started` event in the log; the connection short-circuits + only language-select fires.

**Lessons hint** (will fill at gate close): the id-vs-display-name pair belongs to a Persona summary type; every comparison or display site should pull from a typed accessor, not a stringly-typed `.name`. T1.13 fixed two sites, T1.19 fixes the third — there's a deeper refactor implied (rename `PersonaSpec.name` → `display_name` and add an explicit `id` field) but it's out of scope for the bug fix.

---

## T1.20 — Hello watchdog: detect half-dead WS and self-heal via reconnect

**Status**: done (2026-05-02). Mario hit a "PWA looks ready, doesn't answer, skills+marketplace stuck loading" wedge that survived a `window.location.reload()` + a Vite dev-server restart.

**Problem.** Reproduced live during the T1.17/T1.18/T1.19 smoke. Symptoms:

- Server log shows `client_connected` + `client_evicted` storms (StrictMode + the existing eviction flow).
- Server received `get_skills_state` requests and emitted `server.tx.skills_state count=9` responses against the live `_client`.
- PWA's React-held WebSocket reported `readyState=1` (OPEN), `bufferedAmount=0`, all handlers attached.
- **`ws.addEventListener("message", ...)` registered on the live React-held socket received zero frames** in 1.5s after sending `get_skills_state` — outbound writes worked, inbound delivery was dead.
- A _fresh_ raw `new WebSocket("ws://localhost:5174/ws?lang=en")` opened in the same browser console received `hello`, `state`, `input_mode`, `skills_state` instantly. The proxy + server were fine; the React-held socket specifically was wedged.
- `window.location.reload()` did not recover — the new mount produced another wedged socket. Stopping + restarting Vite + reload finally recovered, but only because every reconnect attempt was a fresh PWA load against a fresh proxy.

The wedge is reproducible enough to have hit Mario twice in one session. T1.16 + T1.17 + T1.19 reduce its triggers; T1.20 makes the PWA self-heal when it does occur.

**Why it matters.** From the user's POV: "it says ready, it doesn't answer, skills don't load, refresh doesn't help." Worst-class failure — system claims healthy state with no recovery path. Pre-T1.20, only manual Vite restart cleared it.

**Validation.** Live trace at 2026-05-02 13:24-13:27. Recovery confirmed by force-closing the React-held socket via dev console (`ws.close(4001, "forced-reset-debug")`) — the existing `onclose → setTimeout(connect, 2000)` reconnect path produced a fresh socket that received `skills_state` immediately. PWA went from "Loading…" to "6 of 9 enabled" within 2 seconds.

**Root cause hypothesis.** TCP-level connection survives in both directions but the WebSocket frame layer (or Vite's WS proxy buffering, or the browser's `permessage-deflate` state) gets desynchronized after the StrictMode + `client_evicted` storm. Outbound frames serialize correctly; inbound frames either don't reach `onmessage` or arrive but the deflate decoder rejects them silently. The browser still reports `readyState=1` because no close frame has been observed.

Confirming the exact mechanism would require packet-level inspection of Vite's proxy. T1.20 sidesteps the diagnostic and chooses recovery over root-cause: if the hello frame doesn't arrive within the SLA, the socket is broken regardless of why.

**Design (collapsed; reactive timeout, not Tier 1)**:

- `ws.onopen` arms a 3s timer (`HELLO_WATCHDOG_MS`).
- `case "hello"` in the message dispatcher disarms it.
- `ws.onclose` clears it defensively (so a normal close doesn't trip a stale timer).
- If the timer fires while the socket is still open and current, force-close with code `4002` and reason `"no-hello-watchdog"`. The existing onclose path schedules a reconnect in 2s.
- User-facing status: "Connection unhealthy — reconnecting…" so a smoke-testing operator sees the recovery instead of guessing at why the orb hint flickered.

3s SLA chosen because the server emits hello within milliseconds of accepting the WS upgrade — anything past 3s is wedged with overwhelming probability. Errs slightly long to avoid false-positive recoveries on a slow loopback; if the timer over-fires under real load, drop to 2s.

**Implement.** One file (`clients/pwa/src/lib/useWs.ts`): module-level constant, one ref, three short blocks (arm, disarm, clear). No protocol change, no server-side change.

**DoD**:

- [x] Watchdog armed in `ws.onopen`, disarmed on `hello`, cleared on `onclose`.
- [x] Timer fires `ws.close(4002, "no-hello-watchdog")` only if socket is current + open.
- [x] PWA reconnect path picks up automatically after force-close.
- [x] Healthy connect verified live: skills_state populates within 1s of WS open.
- [x] tsc green.
- [ ] Live re-test under a deliberate wedge (next time the wedge naturally reproduces, confirm the watchdog fires and recovers without manual intervention).

**Lessons**:

- Browser WebSocket "OPEN" is necessary but not sufficient for a healthy connection. Without an inbound liveness signal, the React layer can't distinguish "open and quiet" from "open and dead." Hello-or-watchdog is the cheapest such signal because the server sends it unprompted.
- Recovery beats diagnosis when the failure is rare and the fix is cheap. T1.20 doesn't explain _why_ the deflate state gets desynced — it just heals it. If the wedge becomes common, escalate to packet-level instrumentation.
- The same lesson as T1.16: every protocol assertion ("ready", "open", "connected") needs an explicit liveness check from a real frame, not from a status flag.

**Follow-up if this recurs**: a heartbeat (ping every 30s, expect pong within 5s) generalizes the watchdog beyond just connection-time. Doesn't ship until we observe a post-hello wedge — speculative now. **Update 2026-05-02: superseded by T1.21 — the post-hello wedge was observed within hours, ping/pong heartbeat shipped as part of the full diagnostic infrastructure.**

---

## T1.21 — WebSocket diagnostic infrastructure (the floor under T1.16/T1.20)

**Status**: done (2026-05-02). Live-verified in Mario's Chrome — recovery loop visible end-to-end in the panel.

**Problem.** Mario hit the post-hello WS wedge a second time within hours of T1.20 shipping (skills/marketplace stuck loading, audio sent but no response). Diagnosed via dev console: PWA's React-held WS reports `readyState=1` (OPEN) but no inbound frames reach `onmessage`; outbound `ws.send(...)` appears to succeed. Server's response was actually going to a different WS instance — server-side `_client` had been evicted in favor of a newer connection while the React layer held a phantom that no one was listening to.

T1.20's hello watchdog can't catch this — it only checks the first 3s of the connection. Mario's request: stop patching, build proper observability so any future "looks ready but isn't" failure is _visible_ without re-instrumenting the client.

**Why it matters.** Every prior triage entry (T1.16 readiness, T1.17 pyrogram retry, T1.18 teardown audit, T1.19 swap short-circuit, T1.20 hello watchdog) was a single-symptom fix. The pattern: a WS-protocol assertion silently desynced from reality. Without frame-level visibility on both sides + correlation IDs, each new symptom required a new bespoke debugging session. T1.21 is the floor under that pattern — make every WS frame, every connection lifecycle event, and every liveness signal observable on both ends, with cross-referenceable conn_ids.

**Validation.** Reproduced live during the T1.21 smoke (2026-05-02 09:45-09:46 in the Chrome MCP tab). Panel showed the wedge unfolding in real time:

```
09:44:46  open + hello (conn_id c60159ab) + 3 inbound frames
09:45:17  ping #1 → no reply
09:45:23  ping-miss consecutive:1
09:45:47  ping #2 → no reply
09:45:53  ping-miss consecutive:2
09:46:17  ping #3 → no reply
09:46:23  ping-miss consecutive:3
09:46:23  watchdog-fired reason:"ping-miss-3"
09:46:23  close-4003 reason:"ping-miss-watchdog"
09:46:26  open + hello (NEW conn_id 84b83e60) + frames flowing again
```

The pre-T1.21 user experience: silent dead PTT until a manual reload. The post-T1.21 experience: ~95s of degraded heartbeat → automatic close + reconnect → fresh socket. No user intervention required, no operator instrumentation required.

**Design** (collapsed, this is infrastructure not a one-line fix):

Three layers, each independently useful:

1. **Server-side correlation** (`server/runtime/src/huxley/server/server.py`):
   - Per-connection `_ConnInfo` dataclass: `conn_id` (uuid hex prefix), `connected_at_ms`, frame counts in each direction, byte counts, per-type histograms, close code/reason, closed_at_ms. Replaces `_client: ServerConnection | None`.
   - `structlog.contextvars.bind_contextvars(conn_id=...)` at handler start so every log line within the connection's asyncio task carries `conn_id` automatically. The `merge_contextvars` processor was already in the chain (`logging.py:93`); T1.21 just exercises it.
   - `_send_raw` chokepoint replaces direct `ws.send` calls — every TX increments counters, every TX type updates the histogram. Identical semantics to the old `_send` (silent drop on no-conn, suppress `ConnectionClosed`); the difference is accounting.
   - Bounded `_recent_conns: deque[_ConnInfo]` (cap 32) holds connection records after close so the diag endpoint can show recent history including eviction reasons.
   - `conn_id` echoed in the `hello` payload so the PWA's panel renders the same id the operator sees in `grep conn_id=ab12cd34 huxley.log`.

2. **PWA frame log** (`clients/pwa/src/lib/wsLog.ts`, `useWs.ts`):
   - `WsLog` class — bounded ring buffer (500 entries) of `WsLogEntry { ts, dir, type, size, preview }`. Logs every rx/tx/lifecycle event. Audio frames get a special compressed entry (`audio: ~3500B PCM`) so they don't drown the log.
   - `useWs` instantiates one `WsLog` per hook instance via `useRef`'s lazy initializer. Lives in a ref (not state) so logging is hot-path-free — no re-render per frame; the panel polls instead.
   - Every `sendRaw` and every `ws.onmessage` and every onopen/onclose feeds the log. Lifecycle events include close codes (1001 = eviction, 4002 = hello watchdog, 4003 = ping watchdog, 1006 = network drop).
   - Heartbeat (ping every 30s, pong expected within 5s) added at the same layer. Three consecutive misses → force-close → existing reconnect path → fresh socket. Timer cleanup wired in `onclose` so a stale interval can't fire against a dead socket.

3. **Diagnostic panel** (`clients/pwa/src/components/DiagnosticPanel.tsx`):
   - Full-screen overlay, monospace font, dark background. Z-index 9999 so it covers any open sheet.
   - Header: live `conn_id`, ready state, ping RTT, miss count (red when nonzero).
   - Three columns: client totals (last rx/tx ago, byte counts), rx-by-type histogram, tx-by-type histogram.
   - Collapsed `<details>` with the full server snapshot JSON (refresh on demand via the "Refresh server" button — fires `get_diag` WS frame, server replies `diag_state`).
   - Frame log table: time, direction (color-coded), type, size, JSON preview. Newest first. Capped at 200 rendered rows.
   - Buttons: Refresh server, Copy JSON, Clear log, Close.
   - Toggles: `Ctrl+Shift+\` (or `Cmd+Shift+\` on macOS — Chrome doesn't bind it) and `?diag=1` URL param (auto-opens; useful for shared links / screenshots). Escape closes.
   - Polls `ws.getDiagnostic()` every 200ms while open; ref-based snapshot read is ~free.
   - Critic-found bug: initial implementation had useEffect deps `[open, ws]`; since `ws` is a fresh hook return on every parent render, the effect re-fired on every App render, sending 8 `get_diag` requests in seconds. Fixed by stashing `ws` in a ref + keying off `[open]` only. Lesson logged below.

**Implement.** Single feature shipped in one commit (this one). Files:

- Server: `server/runtime/src/huxley/server/server.py` (+~180 lines for `_ConnInfo`, contextvars binding, `_send_raw`, `_send_diag_state`, `get_diag`/`ping` cases).
- PWA: `clients/pwa/src/lib/wsLog.ts` (new, ~190 lines), `clients/pwa/src/lib/useWs.ts` (+~140 lines for log wiring + heartbeat machinery + diag accessor), `clients/pwa/src/components/DiagnosticPanel.tsx` (new, ~310 lines), `clients/pwa/src/App.tsx` (+15 lines for state + shortcut + mount).

**DoD**:

- [x] Server: every log line within a connection's handler carries `conn_id` automatically (verified: `grep conn_id=0ab76336 huxley.log` returns the full session).
- [x] Server: `_ConnInfo` tracks frame counts + per-type histograms; close-time log line summarizes the connection.
- [x] Server: `get_diag` WS frame returns full snapshot (current conn + recent + state + runtime).
- [x] Server: `ping`/`pong` echo with original ts so client computes RTT.
- [x] PWA: every WS frame logged into `WsLog` ring buffer. Audio frames compressed.
- [x] PWA: lifecycle log captures open/close-CODE/watchdog-fired/ping-miss/pong with details.
- [x] PWA: heartbeat sends ping every 30s, expects pong within 5s, force-closes after 3 misses.
- [x] PWA: panel auto-opens with `?diag=1`, toggles via Ctrl+Shift+\ + Escape closes.
- [x] PWA: panel renders header + three-column totals + collapsed server snapshot + frame log table.
- [x] PWA: "Copy JSON" button puts the full diagnostic on clipboard.
- [x] tsc + ruff + mypy --strict + 555 server tests green.
- [x] Live verification in Mario's Chrome via Claude-in-Chrome MCP — full recovery loop observed in the panel.

**Lessons**:

- Custom React hooks that return a fresh object every render are landmines for `useEffect` deps. The diag panel's initial useEffect ran 8x in seconds because `ws` (the `useWs` return) was a new identity each render. Fix: ref-stash the unstable value, key the effect on stable inputs only. Same antipattern likely lurks elsewhere — worth a sweep.
- "Open and quiet" looks identical to "open and dead" without a liveness probe. The browser WebSocket API gives `readyState=1` for both. The only reliable signal is round-tripping a frame the server unconditionally answers (hello during connect, pong post-connect). Anything else assumes the protocol invariants hold; T1.16/T1.20/T1.21 are all variations on this lesson.
- Diagnostic infrastructure pays for itself the first time it's used. The wedge T1.21 caught was happening anyway; pre-T1.21 it was a 30-minute debugging session per occurrence. Post-T1.21 it's "open the panel, see the ping-miss timeline, the watchdog already recovered."
- Correlation IDs threaded through _both_ sides cut diagnosis time by an order of magnitude. `grep conn_id=84b83e60` on the server log returns exactly the session the user was in; the same id is in the panel header. No more "what was happening when you saw that?"
- Recovery > diagnosis > prevention is the right priority order for hard-to-reproduce protocol bugs. T1.16 surfaced (diagnosis), T1.20 healed connection-time (recovery), T1.21 generalized to the whole connection lifetime (recovery + diagnosis + prevention via heartbeat). The diagnostic panel makes future occurrences cheap to handle.

**Follow-ups (queued, not this commit)**:

- Server-side `/diag` HTTP endpoint (currently we expose via `get_diag` WS frame; an HTTP route would allow ops tooling to scrape state without an active WS).
- Persist the WsLog ring buffer to localStorage on close so a post-mortem after a tab crash still has the timeline.
- Add a "Replay this session" button to the panel that exports a structured timeline + opens it in a separate review tool.
- PWA tests covering the heartbeat machinery + force-close path. Currently regression-pinned only via the live smoke.
- Server-side test: verify `conn_id` propagates through every `server.rx.*` and `server.tx.*` log line.

---

## T1.22 — Debounce disconnect to survive React StrictMode's double-mount (the actual root cause of "skills not loading")

**Status**: done (2026-05-02). The bug T1.16/T1.17/T1.18/T1.19/T1.20/T1.21 were all observing or mitigating; this is the root cause fix.

**Problem.** The "PWA looks ready, skills stuck loading, audio sent never gets a response" failure has been hitting Mario for hours across multiple commits. T1.16 surfaced it as a session_ready signal, T1.20 added a hello watchdog, T1.21 added the diagnostic panel + ping watchdog — each layer caught more of the symptom but never the cause. With T1.21's per-frame visibility, the actual mechanism was finally observable end-to-end.

**Validation (the diagnostic flow that finally pinned it).**

Reproduced live in Mario's Chrome (via Claude-in-Chrome MCP) at 2026-05-02 11:18-11:35:

1. Diagnostic panel showed conn_id `d1185e17` on a fresh reload. RX BY TYPE = `hello: 1, state: 1, input_mode: 1`. **No `skills_state`.**
2. `grep d1185e17 huxley.log`:
   - `client_connected` for `d1185e17` at 16:21:30.207 (UTC).
   - `server.rx.get_skills_state` at 16:21:30.224 (PWA's request).
   - `server.tx.skills_state count=9` at 16:21:30.228 (server reply).
   - **Server sent the response. The PWA's `ws.onmessage` never fired for it.**
3. From the dev console, sent 5 `get_skills_state` in tight succession on the React-held WS. Server received and replied to all 5. PWA's `addEventListener('message', ...)` on the same WS captured zero. `bufferedAmount=0`, `readyState=1`. Bytes left the server. Bytes never reached the JS handler.
4. From the same console, opened a fresh `new WebSocket('ws://localhost:5174/ws')`. All 4 frames including `skills_state` arrived correctly. Same browser, same Vite proxy, same server.
5. Disabled `permessage-deflate` server-side. Bug unchanged. Not compression.
6. Direct connection bypassing Vite (`ws://localhost:8765/ws`). Worked. So Vite is involved.
7. **Disabled `<StrictMode>` in `main.tsx`.** Bug vanished. Skills loaded immediately. Re-enabled StrictMode → bug returned.

That isolated it. **React 18 StrictMode's mount-cleanup-mount cycle creates two near-simultaneous WebSockets.** The server treats them as one client at a time (newest wins via eviction), but somewhere in the Vite-proxy + websockets-Python + browser-WS stack, the second WS — after the first is evicted — experiences silent inbound frame loss after the first ~3 frames. Root cause beyond that boundary likely needs packet-level inspection of Vite's WS proxy under the eviction-then-reuse pattern, but the application-level fix is straightforward: **don't open two sockets in the first place.**

**Why this hid for so long.** Server logs always showed correct `server.rx.get_skills_state` + `server.tx.skills_state count=9` pairs — server was doing its job. PWA `readyState=1` reported the WS as healthy. Vite's proxy was handling other traffic correctly. T1.20's hello watchdog couldn't catch it (hello arrived). T1.21's ping watchdog took 95s to detect (3 missed pongs at 30s intervals). The pattern was visible only when you correlated server-side `client_connected` + `client_evicted` events with the PWA-side frame log — which is exactly what T1.21 made possible.

**Design.**

The standard StrictMode-safe pattern for setup/cleanup of expensive resources: defer the cleanup so a quick re-setup can cancel it. Implementation in `useWs.ts`:

- `pendingDisconnectRef = useRef<Timeout | null>(null)` — stores the deferred close.
- `disconnect()` no longer eagerly closes. It schedules a 50ms `setTimeout` that does the actual close + state cleanup. If a second `disconnect()` arrives before the timer fires, it replaces the prior timer.
- `connect()` cancels any pending disconnect at its top. If the existing socket from before is still alive (per the existing `readyState` guard), `connect()` returns early — no new socket is created, the server sees one connection across the whole StrictMode mount-cleanup-mount cycle.

Result: StrictMode dev runs the same lifecycle, but only one server-side connection is created. The frame-loss pattern (caused by ws1 → close-1001 → ws2 race in the Vite/websockets stack) doesn't trigger.

**Fix scope:**

- `clients/pwa/src/lib/useWs.ts` — added `pendingDisconnectRef`, debounced `disconnect()`, cancel-on-`connect()` at top.
- Drive-by from earlier in the session: moved `wsLogRef.current?.recordRx(ev.data)` BEFORE the `if (socketRef.current !== ws) return` gate in `onmessage` so even gated frames are logged. Doesn't fix this bug (the bug was that onmessage didn't fire at all), but the gate semantics are still valid for state dispatch — just shouldn't apply to observability.

**Verification.** With T1.22 + StrictMode re-enabled:

- DiagnosticPanel shows: `conn_id: f1e30f66, ready: OPEN, RX BY TYPE: hello:1 state:1 input_mode:1 skills_state:1`. All 4 frames.
- DeviceSheet → Skills section shows "Manage skills — 8 of 9 enabled" instead of "Loading…".
- Server log shows a single `client_connected` per page load (instead of the StrictMode pair + `client_evicted`).

**DoD**:

- [x] React StrictMode re-enabled in `main.tsx`.
- [x] `disconnect()` debounced to 50ms.
- [x] `connect()` cancels any pending disconnect at entry.
- [x] No new server connection is created during StrictMode's mount-cleanup-mount.
- [x] All four handshake-burst frames (hello/state/input_mode/skills_state) arrive at the React-held WS.
- [x] Skills panel shows actual skill count, not "Loading…".
- [x] tsc + ruff + mypy --strict green.
- [ ] Test for the debounce (regression-pin).

**Lessons**:

- The standard React 18 dev-server pattern (`<StrictMode>`) is incompatible with naive setup/cleanup of WebSocket connections. The intended "did you handle the double mount?" check works for observers but breaks producers — opening a fresh socket then opening another fresh socket racing on the same server is not always safe at the protocol layer.
- **Debounce-cleanup is the canonical StrictMode-safe pattern** for any expensive resource (sockets, network connections, timers tied to external state). 50ms is enough to span the StrictMode mount-cleanup-mount cycle (which is sub-millisecond in practice) without being noticeable to a user-driven disconnect.
- Each prior triage entry (T1.16-T1.21) was solving a downstream symptom of a single root cause. We shipped six fixes' worth of diagnostic infrastructure before finding the one-page debounce that resolves all of them. The diagnostic infrastructure was worth it — without T1.21's frame log + conn_id correlation, this would still be open. But the cost was a session of "patching at the wrong layer." Lesson: when a class of bugs keeps happening, the fix should target the input not the output, and "the input" sometimes means "stop creating the bad state in the first place."
- Vite's WS proxy + websockets-Python + browser WebSocket has a real interaction bug under the rapid open-evict-open pattern. The fix here avoids triggering it client-side; if a future scenario can't avoid it (e.g. genuine multi-tab use cases), the Vite layer would need investigation. Filed as a follow-up below.

**Follow-ups (queued, not this commit)**:

- PWA test for the debounce: regression-pin `connect()` + `disconnect()` within 50ms → no new socket, no server connection churn.
- Investigate the Vite-proxy/websockets/browser interaction that loses frames after the open-evict-open pattern. Out of scope for the framework fix; would need packet capture + reproduction outside Huxley to file upstream.
- Audit other `useEffect` setup/cleanup pairs in `useWs.ts` and `App.tsx` for similar StrictMode hazards. The mic + audio playback init might benefit from the same pattern.
- Now that the root cause is fixed, T1.20's hello watchdog and T1.21's ping watchdog are belt-and-suspenders — they catch genuine network drops, not the StrictMode bug. Consider adjusting their thresholds (faster detection) since they're no longer paying for a bug that produced ~95s of bad UX.

---

## T1.23 — Telegram contact management (contacts.json + voice add/remove/list)

**Status**: `done` (2026-05-02) · **Effort**: ~3h

**Problem.** Contacts were baked into `persona.yaml` as a static dict. Adding a new contact required editing a 700-line config file, restarting the server, and knowing where to look. No voice management existed. Contacts are persona *data* (mutable, grows over time), not config (structural), and were in the wrong layer.

**Why it matters.** For the Abuelo persona, the contacts list is the primary social network of the device. It needs to be maintainable without technical file editing, and ideally voice-manageable for the primary user.

**Solution.**
- `contacts.json` in `persona_data_dir` (alongside `abuelos.db` and the Telegram session). Loaded at `setup()`, merged on top of YAML contacts (JSON wins on collision). Written atomically on every `add_contact` / `remove_contact` call.
- YAML `contacts:` block remains for immutable baseline contacts (family members unlikely to change). `_yaml_contacts` frozenset tracks which names came from YAML so `remove_contact` can warn when a YAML contact is removed in-session but will restore on restart.
- 3 new LLM tools: `add_contact(name, phone)`, `remove_contact(name)`, `list_contacts()`.
- Phone validation: normalize (strip whitespace/hyphens/parens, auto-prepend `+`), then validate against `+\d{7,15}`.
- Resolution on add: if `inbound.enabled`, immediately attempt `resolve_contact()` to update the `user_id→name` reverse map for inbound caller recognition. Soft-fails with a note if the person hasn't messaged the userbot yet.
- `persona.yaml` system prompt updated with contact management instructions.

**DoD.**
- [x] `_load_contacts_file` / `_save_contacts_file` module helpers, atomic write.
- [x] Setup merges YAML + JSON, tracks which source each contact came from.
- [x] 3 new tools + 3 new handler methods + localized strings (ES/EN/FR).
- [x] 12 new tests in `TestContactManagement`.
- [x] 4 existing tool-surface tests updated to reflect expanded tool list.
- [x] `docs/skills/telegram.md` updated with dynamic contacts section.
- [x] `persona.yaml` system prompt updated for new tools.
- [x] ruff + mypy + 112 tests green.

**Docs**: `docs/skills/telegram.md` updated.

**Lessons**: Contacts as config was a layering mistake caught early. The `_yaml_contacts` / `_json_contacts` split is a clean pattern for "immutable baseline + mutable overlay" — applicable to any skill that reads persona config at startup and allows runtime writes.

## T1.24 — Telegram `default_country_code` + tool-first prompt pattern

**Status**: `done` (2026-05-15) · **Effort**: ~4h (1h skill code, 3h prompt iteration loop)

**Problem.** Smoke testing T1.23 surfaced two layered issues that the LLM's read-back was masking:

1. **Phone-validation gap**: regex `+\d{7,15}` accepted obviously-wrong numbers. First test saved Elsa as `+5730000000` (10 digits, missing country code) — the user dictated incompletely, the LLM transcribed faithfully, and the skill saved it. Per-country tight validation isn't framework-shaped (Huxley ships to multiple countries), but a persona-declared default country code with skill-side normalization IS.
2. **Hallucinated saves**: the `gpt-4o-mini-realtime-preview` model under prompts with multi-step confirmation flows would generate "ya está guardado" without invoking `add_contact` (`tool_calls=0`). Two separate smoke tests reproduced this. The model is biased toward low-latency conversational responses over write-tool invocations.

**Why it matters.** The Abuelo user is blind. The LLM's narration IS the user's only interface to system state. A hallucinated save creates a phantom contact in the user's mental model that doesn't exist in storage — and they discover the gap only when a call fails.

**Solution.**
- Per-persona `skills.telegram.default_country_code: "+57"` config. Skill normalizes bare digits AND short `+`-prefixed numbers (≤10 digits not matching the country code) by prepending the default. Foreign contacts (`+34...`, `+1...` ≥11 digits) pass through unchanged.
- Tool description embeds `country_code_hint` so the LLM sees the default at decision time.
- Defense in depth at multiple layers: tool description hint → LLM read-back → skill normalization → post-save narration.
- Persona prompt rewritten to mirror the audiobooks anti-hallucination pattern (the only one in the prompt that's never hallucinated): *"llama a `add_contact` ANTES de decir 'guardado'... la frase es para acompañar la acción, no la reemplaza."* Same wording shape, same model behavior — fixed the hallucination on first try after 4 iterations of "do step A, then B, then C" prompts failed.
- 9 new tests in `TestDefaultCountryCode` covering: config load, validation, bare digits, "+local" digits (the exact bug), explicit foreign codes, invalid config fallback, tool-description hint presence/absence.

**Smoke verification (2026-05-15)**:
- *"Agrega a Patricia, su número es 318-123-4567"* → LLM read back *"+57 310-123-4567"* (caught a digit transcription error, didn't catch it on confirmation but the +57 was present) → user confirmed → `add_contact(phone="+573101234567")` actually fired → saved `+573101234567` → narration matched storage → `list_contacts` confirmed presence.
- `contacts.json` shape correct.
- Zero hallucinations across two consecutive add+list cycles.

**DoD.**
- [x] `default_country_code` config field loaded + validated in `setup()`.
- [x] Tool description embeds the default for LLM context.
- [x] Skill normalizes both bare digits AND `+`-prefixed local digits when default is set.
- [x] 9 new tests pass; 121 total in the telegram suite.
- [x] `docs/skills/telegram.md` documents the config + the defense-in-depth approach.
- [x] Abuelo persona system prompt rewritten in the audiobook-pattern shape.
- [x] Server-side smoke test: hallucination gone, tool actually invoked, country code applied.

**Docs**: `docs/skills/telegram.md` updated. Persona system_prompt updated.

**Lessons**:
- **Mirror prompt patterns that already work for the same model.** Four prompt iterations failed to fix the hallucination by reasoning about what *should* work. The fix came from copying the wording of the audiobooks section (proven to never hallucinate with this model) verbatim into contacts. Match observed model behavior, not theoretical instruction-following.
- **Defense in depth for write paths.** The LLM is one of three layers (tool description hint → LLM read-back → skill normalization). When any one layer fails, the next catches it. Tested by deliberately constructing the LLM-failure case in `test_add_contact_with_plus_but_local_digits_prepends_cc`.
- **Country code is persona data, not framework primitive.** Resisted promoting to a framework-level `locale.country` field — only Telegram needs it today, premature to abstract.
- **Phone validation is fundamentally country-dependent.** E.164 (`+\d{7,15}`) is the global truth and stays. Country-specific tightening (digit-count rules per country) belongs in libphonenumber territory; deferred until we hit a case the heuristic miss.

## T1.25: OpenAI shut down our Realtime model and the beta interface (Huxley is down)

**Status**: `in_progress` (2026-10-10, code shipped; awaiting Mario browser smoke) · **Effort**: ~3h (wire-format migration, contained to `huxley/voice/` + cost table)

**Problem.** OpenAI removed `gpt-4o-mini-realtime-preview` on 2026-05-07 and the Realtime beta interface (`OpenAI-Beta: realtime=v1`) on 2026-05-12. Huxley defaults to both. Every connect fails, so every persona is silent.

**Validation (2026-10-10).**
- `GET /v1/models/gpt-4o-mini-realtime-preview` → `404`. Same key: `gpt-realtime-2.1` → 200, `gpt-realtime-2.1-mini` → 200, `gpt-live-1` → 200.
- developers.openai.com/api/docs/deprecations: "2026-05-07 gpt-4o-mini-realtime-preview → gpt-realtime-mini" and "2026-05-12 OpenAI-Beta: realtime=v1 → Realtime API". `gpt-realtime-mini` is itself marked deprecated on the models page; its successor is `gpt-realtime-2.1-mini`.
- No unit test pinned the `session.update` payload or the connect headers, so the suite stayed green through the outage.

**Probe (2026-10-10).** Scratch script: TTS'd Spanish audio ("Hola, ¿qué hora es ahora mismo?") → GA endpoint, manual commit (PTT shape, `turn_detection: null`), whisper-1 transcription with `language: es`, one `get_time` tool round trip.
- `gpt-realtime-2.1`: session accepted, transcript exact, first audio 0.90s, tool call 1.42s, answer correct.
- `gpt-realtime-2.1-mini`: same, first audio 0.82s, tool call 0.94s, answer correct. `usage` shape unchanged (reasoning tokens are folded into `output_token_details.text_tokens`), so `cost.py` math carries over.

**Solution.**
- Drop the beta header. GA `session.update`: `session.type: "realtime"`, `output_modalities: ["audio"]`, `audio.input.{format, transcription, turn_detection}`, `audio.output.{format, voice}`.
- Rename the three server events: `response.output_audio.delta`, `response.output_audio.done`, `response.output_audio_transcript.done`. Everything else Huxley uses (`input_audio_buffer.*`, `conversation.item.create`, `response.create/cancel/done`, `function_call_arguments.done`, input transcription completed, `error`) is unchanged.
- Default model → `gpt-realtime-2.1-mini`: same audio price as the shut-down model ($10 in / $20 out per 1M), cheaper cached text ($0.06), adds reasoning. `gpt-realtime-2.1` is a one-line env override (~3x audio cost).
- Cost table: replace the two dead models with `gpt-realtime-2.1-mini` and `gpt-realtime-2.1`; unknown-model fallback points at the new default.

**DoD.**
- [x] Provider connects without the beta header and sends the GA session shape. `TestConnectGAInterface` (2 tests) fails on the pre-fix code, passes after.
- [x] Parser handles the GA event names (fixtures + replay test updated).
- [x] Default model + `.env.example` + cost table updated. 557 runtime tests, all skill suites, SDK, ruff, mypy green.
- [x] Live server boots and reaches CONVERSING on the new default. Scripted PWA-shaped smoke (`ptt_start` → 20ms TTS'd Spanish frames → `ptt_stop`) through the real server: "¿qué hora es?" → `get_current_time` → spoken answer, first audio 0.74s after release. "Ponme El Principito" → `search_audiobooks` → `play_audiobook` → `coord.response_done pending_audio_streams=1` → `coord.audio_stream_started`, book resumed at the saved position.
- [x] Model-unavailable log hint names both causes (retired model vs. account access). Pre-fix, the hint blamed billing. Observed on the wire: a retired model opens the socket and sends `error.code=model_not_found`; the beta header gets `error.code=beta_api_shape_disabled`.
- [x] Docs: `architecture.md` sequence diagrams, `verifying.md`, web env-var + run-the-server pages.
- [ ] Mario browser smoke: PTT, Spanish question, tool call, audiobook start.

**Smoke note.** On "Ponme El Principito" the model went straight to the tool calls with no spoken ack; the user hears the `book_start` earcon (2.3s preroll) and then the book. Worth listening for in the browser smoke; if it feels abrupt, it's a persona-prompt tweak, not a framework change.

**Docs**: `docs/architecture.md`, `docs/verifying.md`, `web/content/docs/reference/environment-variables.mdx`, `web/content/docs/use/run-the-server.mdx`. `decisions.md` and `research/realtime-suspend.md` keep the old event names on purpose (historical records). Checked `protocol.md` (client contract unchanged), `observability.md` (no event names changed except `cost.unknown_model_using_default_pricing`, not documented there), `concepts.md`, skill docs: none affected.

**Lessons**:
- **Pin the third-party wire contract in a test.** The suite was green for five months of total outage because nothing asserted what we send to OpenAI. A payload-shape test is cheap and is the only thing that ties our code to the provider's current contract.
- **A provider deprecation is a production incident with a date on it.** OpenAI announced both shutdowns in September 2025. Nobody was watching. Follow-up candidate: a weekly check that `GET /v1/models/$HUXLEY_OPENAI_MODEL` returns 200, with an audible alert for the Abuelo deployment.
- **Error hints must name every cause that produces the error.** "Add credits" sent the diagnosis the wrong way for exactly the failure we hit.

---

## T1.26: GPT-Live, reshape Huxley around it, or use it as a skill?

**Status**: `done` (2026-10-10, decided: no GPT-Live; walkie-talkie stays on Realtime) · **Follow-up**: background delegation for slow work, unplanned

**Question (Mario, 2026-10-10).** OpenAI's GPT-Live (openai.com/index/introducing-gpt-live, July 2026) is full duplex and does tool work through delegation. Should Huxley reshape into "a hardware device that uses GPT-Live plus tool calls" while keeping everything Huxley does?

**Facts (developers.openai.com, read 2026-10-10).**
- `gpt-live-1` is in the API at `v1/live/sessions` (WebRTC, WebSocket, SIP, plus a server "sideband"). Our key has access.
- Billed **$0.05 per minute of open session**, per second, silence included; muting the mic still bills. Backend model tokens on top. Realtime (T1.25) bills per response, so an idle session costs $0.
- The voice model never calls tools itself. It **delegates**: either OpenAI runs a Responses model with our function tools ("Responses delegation"), or our server is the backend ("client delegation") and feeds results back with `session.commentary.append` (spoken), `session.thinking.append` (silent) or `session.instructions.append`. Max 500 tokens per append.
- No manual turns and **no end-of-spoken-response event**: "GPT-Live decides when to speak; remove manual audio commits." Transcript deltas carry no item IDs.
- Voices: default `marin`; the GPT-Live voice list is English and Brazilian Portuguese only, with a warning about accents and fluency gaps in some languages. Abuelo is Spanish on `coral`.
- Docs recommend closing idle sessions and reopening on push-to-talk; continuity via fork (`store: true`, 30 days) or 8k tokens of seeded history.

**Probe (2026-10-10, scratch script, ~$0.02).** Same TTS'd Spanish question ("Hola, ¿qué hora es ahora mismo?"), voice `coral`, one `get_time` function returning 15:42. Times are from the end of the user's speech (GPT-Live) or PTT release (Realtime).

| | gpt-live-1 (Responses delegation, `gpt-6-luna` backend) | gpt-realtime-2.1-mini (Huxley today) |
| --- | --- | --- |
| First sound | +0.14s "Claro." | +1.27s |
| Tool call | +0.53s (delegation started at -0.19s, before speech ended) | +0.81s |
| Actual answer | +3.69s "Son las 15:42" after filler "Voy a consultar la hora ahora mismo" | +1.27s "Son las 15:42" |
| Said the time before the tool returned? | No | No |
| Spanish transcript | exact | exact |
| Billed | 13s of session ($0.011) + backend tokens | ~950 tokens |

Two findings that matter for B: `coral` works on GPT-Live (it's not limited to the listed voices), and **GPT-Live streams output audio continuously, silence included** (RMS 0 for the first 3s and the last 5s of a 12.6s stream). Huxley's coordinator treats arriving audio as "model speaking"; that assumption breaks.

**Follow-up probe: client delegation (Huxley as the backend), same question.** Huxley answers the instant `session.delegation.created` arrives (no backend model).
- GPT-Live decides the user finished at +0.54 to +0.69s; Huxley's answer lands at the same moment.
- Default prompt: GPT-Live still says a filler line first and speaks the time at +3.4 to +4.4s (3 runs). The delay is its pacing, not the backend.
- Prompt "if the result arrives before you start speaking, say it directly": answer at +1.37s and +2.35s (2 runs), still with a short filler ("Un momento... Son las 15:42"). Roughly at parity with Realtime's +1.27s.
- **Correction:** the first probe's "Realtime is 2.4s faster" was mostly GPT-Live's default filler pacing, not an architectural cost. Mario's direction (2026-10-10): one abstraction, not modes. With client delegation that's viable: GPT-Live is the single voice, Huxley's skills are the backend.

**Decision input (Mario, 2026-10-10): Huxley is walkie-talkie, project-wide.** The trigger is a held button (on screen or physical). The user talks only while holding; the device answers only after release. No open mic, ever. That removes everything GPT-Live adds (end-of-speech detection, backchannels, barge-in) and collides with its contract: GPT-Live has no manual commit ("stream audio continuously; GPT-Live decides when to speak") and can speak while the button is held.

**Conclusion.** Stay on the turn-based Realtime API (T1.25). Borrow GPT-Live's one idea that fits: hand slow or hard work (search, deep questions) to a stronger background model, say "un momento" now, announce the result when it's ready (via `inject_turn`). That's a possible framework item; needs a plan + critic before any code.

**Options and critic notes** (fresh Plan agent, cold brief, 2026-10-10):
- **A. Device talks to GPT-Live directly, Huxley as sideband.** Rejected. Breaks "one audio pipe out": audiobook, radio, earcons and Telegram calls would need mixing in ESP32 firmware, which has no Opus/DTLS/AEC today (`clients/firmware/components/hux_net`, `hux_audio`). Hard-codes an OpenAI endpoint into hardware, against "Not Alexa" in `vision.md`.
- **B. GPT-Live as a second `VoiceProvider`.** Not a provider swap. `VoiceProvider` is turn-shaped (`commit_and_request_response`, `on_response_done`, `on_audio_done` in `voice/provider.py`). The coordinator starts book audio after the final `response.done` (`turn/coordinator.py` ~587-620) and drops stale audio by response boundary after an interrupt. Without those events the "book jumps in" bugs from `turns.md` come back. `cost.py` can't see per-minute billing, so the kill switch goes blind. A model that "keeps talking while the backend works" risks a spoken "listo" before the tool ran, the worst failure for a blind user (see T1.24). Realistically a parallel LiveCoordinator: weeks.
- **C. Stay on Realtime GA (done in T1.25).** No architecture change.
- **D. GPT-Live as a skill (recommended to try).** A "charla" skill opens a GPT-Live WebSocket and bridges it through `InputClaim` (mic in, `speaker_source` out; `huxley_sdk/types.py`), exactly how Telegram calls work. Cost is bounded by the chat length; a PTT press ends the claim and hands back to Realtime for commands. Framework untouched. It lines up with the Abuelo roadmap's "Companionship mode — open-ended chat", where full duplex, backchannels and patience with pauses actually matter. Needs echo cancellation (PWA captures with `echoCancellation: false`, `clients/pwa/src/lib/audio/capture.ts:61`; field finding F5 already saw radio bleed into the mic).

**Cost sketch** (estimates, not measured): Abuelo pattern of ~30-60 min talk/day plus hours of media. Realtime 2.1-mini: media is free (idle session), talk is token-billed at the same rate as the old model. GPT-Live: $45-90/month for talk time alone, plus open-session tails and backend tokens; leaving a session open through 4h/day of audiobooks adds ~$360/month.

**Recommendation.** Don't reshape. Huxley's split (device does I/O, server is the brain) is what lets a hardware device use any voice engine; GPT-Live fits as a skill, not as the core. Run the experiment below first.

**Cheapest experiment (~1 day, <$5).** Standalone script, outside Huxley: replay ~20 recorded Abuelo-style Spanish commands plus some free chat over WebSocket to `gpt-live-1` and `gpt-realtime-2.1-mini`. Measure end-of-speech-to-confirmation latency, transcript accuracy, how often it confirms before the tool result arrives, and billed cost. Then 10 minutes of live chat with the real user on the best voice. If he doesn't prefer GPT-Live blind, stop.

**Decision needed from Mario.** Whether to pursue D (a companionship skill) at all; it's a new skill, so it goes through plan mode per CLAUDE.md.

---

## T1.27: Huxley on Mario's iPhone (PWA fixes, native shell, native audio)

**Status**: `in_progress` (2026-10-10, round 2 on the phone awaiting Mario; round 1 shipped in `49a833f2` server, `bfdb70f7` native audio) · **Effort**: ~1.5 days

**Problem.** Mario wants to use Huxley from his iPhone as if it were the product. As a home-screen PWA it had: a white band at the top, transcripts out of view, a mic prompt on every launch, a permanent Dynamic Island live activity, stalls and stuck "thinking". As a Capacitor app (commit `ef8e8389`) the band and the per-launch prompt went away, but replies played from the earpiece ("like a phone call") and the island still showed a live activity while Huxley spoke.

**Validation.** Mario's reports and screenshot (island speaker pill on "Escuchando", 2026-10-10). Root cause, measured: WKWebView renders audio in a WebKit helper process with its own audio session. iOS shows that as a live activity, and WebKit's play-and-record routes output to the earpiece. Releasing the web mic between presses (`f060da9a`) fixed the earpiece only partially and not the island.

**Solution.**
- PWA reliability fixes for iOS WebKit: `28bd26de`, `9cd2bf36`.
- Native shell via Capacitor 8 with live-reload dev mode: `ef8e8389`, `3d012a06`.
- Native audio plugin (`clients/pwa/ios/App/App/HuxleyAudioPlugin.swift`, JS `src/lib/audio/native.ts`): AVAudioEngine in the app's process, `.playAndRecord` + `.defaultToSpeaker`, mic engine runs only while the button is held, session released 3 s after idle. 48 kHz to 24 kHz PCM16 conversion in `PCM16Resampler.swift`.

**Finding along the way (fixed).** The server's too-short rule counted chunks (25, sized for the web worklet's 5.3 ms frames). The native plugin sends 100 ms chunks, so every press under 2.5 s was rejected as "muy corto", a quick "Sí" included. The rule now measures audio duration (133 ms). Also correct for the ESP32, whose chunk size differs again.

**DoD.**
- [x] Simulator: native mic prompt once, `client.mic_acquired native=True`, frames flow, reply plays, no web-audio events.
- [x] `bun run ios:test`: resampler keeps length, pitch, level and continuity for 48k, 48k small buffers, 44.1k, 16k and stereo input; two mutated copies (flush per chunk, wrong output rate) fail it.
- [x] Server: 0.7 s native press commits (`user_audio_ms=490`); regression tests for large and small chunks. 564 runtime tests, ruff, mypy green.
- [x] Mario on the phone (iPhone 15 Pro, 19:38 UTC): "Working well." Log: `mic_acquired native=True ms=160`, first frame 262 ms after the press, 1.2 s press committed (`audio_ms=1199`, 12 chunks; the old rule would have rejected it), `native_capture_summary peak=17948`, transcript "¿Escuchas?" exact, reply played.

**Round 2: latency + critic fixes (2026-10-10).**

Latency, measured: press to recording was ~160 ms on the phone (cold session + engine built per press); a press soon after another was 7 ms on the simulator, so the cost was session activation plus engine build. Release dropped the mic's partial 100 ms block plus frames in flight (`active=false` before the bridge delivered them), often the end of the last word.
- Session stays active while the app is in the foreground; capture engine built once (tap installed once, `prepare()` after each stop), a press only starts it. Simulator: `mic_acquired ms=6-16`, `warm_session=True warm_engine=True`.
- Release drains: the plugin keeps recording until the tap hands over its next block, JS keeps forwarding until `stopCapture` resolves, and `ptt_stop` goes out after (`mic.finish()`). A 0.7 s press now delivers 794 ms of audio (was 490). A quick re-press still orders `ptt_stop` before the next `ptt_start` (measured 28 ms apart).
- Smaller hardware buffers don't help: an AVAudioEngine tap delivers at least 100 ms. `AVAudioSinkNode` would, at the cost of realtime-thread code; not needed since the server waits for the release anyway.

Critic (fresh agent, cold brief) found 7 real bugs; all fixed:
1. A barge-in cut its own ready tone (`stopPlayback` stopped the shared effects node). Now three nodes: voice, one-shot tone, thinking loop; stop clears voice and loop only, like the web build.
2. A stale `playbackIdle` (from a stop, or an underrun) could end a newer reply: orb flat, `onceIdle` early. Chunks carry a sequence number; JS ignores an idle that a newer chunk overtook.
3. Barge-in waited for a ~160 ms mic open before stopping the reply. Now ~10 ms (warm open).
4. End of utterance lost (above).
5. Interruption / background left `pendingBuffers` stuck or resumed a stale reply later. Now both drop the reply, emit idle, and stop capture; a watchdog forces idle 1 s after the queued audio should have ended.
6. In a call, an engine stopped by a phone call or route change left the call deaf (`hasStream` still true). Now the plugin emits `captureEnded`; a route change mid-press rebuilds the engine and carries on (the resampler follows format changes; `ios:test` covers it).
7. A rejected `play()` left JS busy forever. Now it emits idle.
Also: the category check includes options (play-and-record without `defaultToSpeaker` routes to the earpiece); a route change restarts the thinking loop; deactivation errors are logged; the tap uses `format: nil` (a fixed format can raise an uncatchable exception); removed Capacitor's template storyboard, which built a second, unused bridge controller.

Second critic (fresh agent, on round 2): the drain is sound (the last frame reaches JS before `stopCapture` resolves; a stop can't hang or resolve twice). It found 3 real bugs plus hardening, all fixed in round 3:
1. After a web-view reload (iOS kills WebKit's process, Capacitor reloads the page) the plugin kept the old high sequence number, so every idle was ignored and the orb stuck on "speaking". Now sequence numbers are taken in order (`lastSeq = seq`) and the page calls a new `reset()` on load, which also stops anything the old page left recording or playing.
2. A route change while idle (headset unplugged) left the warm engine stale: the next press failed with "access denied". Now a failed start rebuilds the engine once and retries (`mic_acquired retried=True`, `native_capture_retry`).
3. A call whose mic iOS took in the foreground (Siri, an alarm, a declined call) stayed deaf until the next tap. Now the plugin emits `captureAvailable` when the session comes back and the app reopens the call's mic.
Hardening: a rejected release promise can't block later presses; failures clear `sessionActive`; the watchdog is pushed back after a route change; going to the background stops the tone node; a media-services reset rebuilds the playback engine (it stayed dead before); the capture engine is `reset()` after each stop (no leftover samples opening the next press) and no longer `prepare()`d ahead (start is 6 ms without it, and a prepared input unit is not worth the doubt about the mic indicator).

DoD round 2 + 3:
- [x] Simulator: warm press 6-16 ms (6 ms without `prepare()`); drained summaries; second press records after the engine `reset()`; re-press ordering; background and return re-warms (183 ms, off the press path); no native error events; `client.device shell=native_app audio=native`.
- [x] `bun run ios:test` 6/6 (new: format switch mid-press), `bun run check` green.
- [x] Mario on the phone (21:02-21:16): "Sí." transcribed exact with the drain (`flush_ms=48`). Without `prepare()` the warm start was 101 ms, all before recording; with it 5-7 ms (first press after launch 109 ms). Orange dot: stays lit while tapping every ~300 ms, goes off when he stops (iOS lingers it a few seconds by design), so a prepared input engine doesn't hold the mic. ~100 ms taps correctly rejected as too short.
- [x] Round 4: the resampler held back its last ~15 ms per press (4800 frames in, 2040 out, not 2400); `flush()` at release sends it before `ptt_stop`. `ios:test` now demands exact length (a copy without the flush fails, 136 frames short).
- [ ] Mario: interrupt a reply mid-sentence and hear the ready tone.

**Follow-up (product question, not started).** No background-audio mode: locking the phone stops a reply or an audiobook. Enabling it means keeping the WebSocket alive in the background too (it lives in WebKit, which iOS suspends), so it's a bigger change. Only worth it if the phone becomes a real client, not just a dev one.

**Docs**: `verifying.md` (native audio section), `observability.md` (native client events), `protocol.md` + `turns.md` + web `turns.mdx` / `connect-a-client.mdx` (133 ms rule).

**Lessons**:
- **A threshold in "frames" is a hidden contract with one client's chunk size.** Measure the quantity you mean (milliseconds of audio), not a proxy for it.
- **Log what the hardware gave you.** A garbled simulator test turned out to be a muted Mac speaker, found by hand. The per-press `native_capture_summary` (rate, frames in/out, peak) now shows a silent mic in one log line.

---

## T1.28: Spotify skill (play music from a household Spotify Premium account)

**Status**: `done` (2026-10-12; `7d97f0ee`, `4b765bd2`, `0ab5480b`, `52c1fd27`, coordinator fix `8f8c33ad`) · **Effort**: ~2-3 days

**Problem.** Mario wants Huxley to play music from Spotify ("pon algo de Los Panchos"), using a Premium Family member account he set aside for it. A Spotify skill was never built: in T1.14 it was only rejected as the marketplace's *reference* skill (hourly token expiry made the v1 demo fragile), not as a skill.

**Validation (Gate 1, 2026-10-10).** Spike with go-librespot 0.10.3 (Homebrew) on the Mac, pipe output, paired to the account via spotify.com/pair:
- Login, Connect registration, track and format selection all work; the account shows "Premium Family" in its settings.
- Every track fails at the last step: `Spotify refused the audio key (code 1)`, at 160 and 96 kbps, for playlists and single tracks.
- Cause, per librespot's pinned issue #1649 (maintainers, Nov 2025 to Oct 2026) and Music Assistant's docs: Spotify stopped issuing audio keys to librespot-family clients for Premium accounts created since December 2024, rolling out account by account. This account is new, so that path is closed.
- The player's own session token can't search either: the Web API answers 429 on the first request (Spotify throttles that client id).
- Spotify's replacement: **Soloist** (August 2026), an official headless Spotify Connect client for Linux (ARMv7 / AArch64 / x86_64), audio through PipeWire or PulseAudio, local WebSocket API (`--ws ADDR:PORT`): `play` {uri: track/album/playlist/episode}, `play` (resume), `pause`, `skip_next`, `skip_prev`, `seek`, `set_volume`, `add_to_queue`; events `playback_state`, `track_changed`, `playback_changed`, `auth_state`, `error`. Needs a Soloist API key generated by a Premium account at developer.spotify.com/dashboard/soloist, a one-time pairing from a Spotify app on the same LAN, and a build newer than 90 days. Not for macOS.
- Spotify Web API (for search): development mode needs a Premium app owner, caps an app at 5 users and search at 10 results (Feb 2026 migration guide); fine for one household.

**Critic (Gate 2, fresh Plan agent, cold brief, 2026-10-10)** on the first sketch (FIFO pipe sink, subprocess, MIXABLE unspecified):
1. *FIFO capture is wrong for Huxley.* Every press cancels the stream, so the reader detaches; `module-pipe-sink` then stalls Soloist or drops, and the FIFO keeps the pre-pause tail ("siguiente" would open with up to ~2 s of the old song). **Adopted:** Soloist plays into a `module-null-sink` (its own clock); each stream factory opens a fresh capture of the sink's monitor (`module-simple-protocol-tcp` on 127.0.0.1, s16le 24 kHz mono, so the sound server resamples and ffmpeg goes away; `parec`/`pw-record` as fallback). No reader = monitor discards = nothing stale.
2. *Every voice command arrives with the music already stopped* (the press cancelled it). So next/previous/"what's playing" act, then return a resume `AudioStream`; pause is `CancelMedia`. The factory is re-callable (the observer calls it again on FOREGROUND after MUST_PAUSE): first call `play {uri}` + earcon, later calls `play` (resume), no earcon. **Adopted.**
3. *NONMIXABLE, not MIXABLE.* Verified: the observer can ramp a ducked stream's gain, but nothing mixes it with speech; both go down the one audio channel and would interleave. **Adopted:** NONMIXABLE with `patience` ~30 min (reminders and calls pause the music, it comes back).
4. *Never await Soloist in the factory's `finally`* (it runs inside the FocusManager actor's cancel path). **Adopted:** one ordered command queue owned by a `ctx.background_task` WebSocket client; `finally` does `put_nowait(pause, generation)`; stale-generation pauses dropped. Failures narrated via `create_task(inject_turn(...))`, as audiobooks does.
5. *Failures after the tool returns are silent* (`command_result` only means dispatched). **Adopted:** `handle()` checks cached `auth_state` first; the factory waits up to ~10 s for `playing` or `error`; `is_active:false` / `device_changed` mid-stream ends it and says the music moved to another device; idle at the end of a context ends the stream.
6. *Soloist under a systemd user unit, not a skill subprocess* (survives server restarts and persona swaps; owns pairing). The Soloist key goes in the unit's EnvironmentFile, not `ctx.secrets`. The 90-day build expiry is scriptable (mr-do-player downloads the current build from Spotify's CDN with If-Modified-Since at start); the skill logs `expiry_days_left` and has a spoken failure if it lapses. **Adopted.**
7. *Search.* Client-credentials tokens can't use `market=from_token`: set `market` explicitly. Artist: search `artist:"X"` tracks (max 10), play the first, queue the rest. The critic suggested a curated catalog for v1 instead of search. **Decision (mine, reversible): search stays in v1.** Open search is the point of Spotify over radio ("pon algo de Los Panchos" for any artist), and Mario is creating the Web API app anyway; the model says back what it found, so a wrong match is audible. A small curated alias list ("la música de siempre") can come later.
8. *Volume:* pin Soloist at 100 on every play; volume stays with the client (`SetVolume`), so a phone can't silently mute Abuelo. **Adopted.**
9. *Dev loop:* Docker/OrbStack on macOS are NATed, so LAN pairing fails. Run the whole server on the Orange Pi (production layout); fallback a bridged Linux VM. Check whether Soloist supports code pairing. Unit tests use a fake Soloist WebSocket and a fake PCM TCP server, so they run on the Mac.
10. *No new SDK surface*; "spotify" never appears in `server/runtime` or `server/sdk`. Fixed in passing: the `AudioStream.patience` comment claimed radio uses the 5 min MIXABLE default; radio is NONMIXABLE (default).

**Design (locked).**
- Host: Soloist as a systemd user unit on the Orange Pi, default sink `huxley_spotify` (null sink), `module-simple-protocol-tcp` recording its monitor on 127.0.0.1 as s16le/24000/mono. Build fetched from Spotify at start.
- Skill `huxley-skill-spotify`: config `soloist_ws`, `pcm_tcp`, `market`, `start_sound`; secrets `client_id`, `client_secret` (Web API search).
- Soloist client: background task, reconnecting, caches `auth_state` / `playback_state`, ordered command queue with generations.
- Tools: `play_music(query)` (search, return what will play + a NONMIXABLE `AudioStream`, patience 30 min, earcon on first call only), `music_control(action: pause|resume|next|previous)`, `whats_playing()`.
- Abuelo prompt: never invent a song; songs and artists go to this skill, stations to radio.

**Definition of Done.**
- Fake-Soloist tests: first factory call plays the URI with earcon, later calls resume without; `finally` queues the pause without awaiting and stale generations are dropped; watchdog, takeover and idle each end the stream and schedule narration; logged-out, expired and not-found outputs carry a spoken message plus an alternative.
- ruff, mypy, pytest green; `grep -ri spotify server/runtime server/sdk` empty.
- Smoke on the Pi by voice: play, a press silences at once, "sigue" resumes with no old tail, "siguiente" starts clean, a reminder pauses and the music returns, stopping Soloist gives a spoken failure, playing from a phone gives a spoken notice.
- Log events: `spotify.ws_state`, `spotify.auth_state`, `spotify.play_requested` / `play_started` (`latency_ms`), `spotify.stream_ended` (`reason`), `spotify.expiry_days_left`.
- Docs: `docs/skills/spotify.md` (deploy, pairing, build refresh, failure table), skills index, Abuelo prompt, this entry.

**Implementation (Gate 3, 2026-10-10).** The Orange Pi was busy, so Soloist runs on the Mac in Docker (`server/skills/spotify/dev/`: Debian + PulseAudio null sink + `module-simple-protocol-tcp`, Soloist downloaded from Spotify at start). Pairing needs Spotify Connect discovery on the LAN, which a container on macOS can't join: `pair-on-mac.sh` forwards a fixed port into Soloist's pairing endpoint and announces the device from the Mac with `dns-sd` (verified: `getInfo` answers through the bridge and Spotify apps on the network query it).
- Search verified live with the household's app: "Los Panchos" → artist mode (10 tracks), "Sin ti Los Panchos" → the track, "Bésame mucho" → Los Panchos' version, "boleros clásicos" → a 200-bolero playlist.
- Live server: `spotify.setup_complete search=True earcon=True`, `spotify.ws_state connected=True` against the container; Abuelo's skill list includes `spotify`.
- Tests: 31 (fake Soloist + a real local TCP PCM server + pytest-httpx), covering the DoD list; the stale-generation test fails with the rule disabled. ruff, mypy strict green; no "spotify" in runtime/sdk.
- Found along the way: `ntgcalls` 2.2.1b3 (pinned in our lock for Telegram calls) was removed from PyPI along with every 2.2.x before 2.2.5, so the committed lock couldn't install fresh anywhere. Relocking for the new member moved it to 2.2.5 (inside Telegram's `>=2.2.1b2`); Telegram's 121 tests pass. A real Telegram call should be smoke-tested on 2.2.5.

**Pairing (2026-10-10).** The Spotify web player can't see local devices (browsers can't do LAN discovery); Mario paired from the desktop app on the Mac: "pairing settled", login stored, restored on every start ("logged in as ...", build valid 89 days).

**Live checks against real Soloist.** A raw play captured 7.5 s of music through the TCP capture (RMS 2,000-4,200 per second, peaks to 21k). Two things only the real thing showed: the track title is at `item.decorations.identity.name` (artists under `decorations.creators`), not `item.name`; and after `play`, Soloist reports `playing` for the old context first and turns `is_active` only after playback starts. Soloist also plays artist URIs although its docs list only track/album/playlist/episode, so an artist now plays natively (no queue).

**Implementation critic (fresh agent, on 7d97f0ee)**, all fixed in round 2:
1. False "moved to another device" on the first play (inactive until playback starts; the fake hid it). Now takeover = active then not; the fake starts inactive like real Soloist; the regression test fails on the old logic.
2. Errors from queued `add_to_queue` / `set_volume` counted as a failed start. Queueing removed (artists play natively); `set_volume` sent after the start is confirmed; a start counts only when the requested context is playing.
3. A capture that closed was silent, a reset escaped as "cancelled", and behind Docker the offline case was never seen (connect then EOF). Now resets end the stream, a mid-song drop says `interrupted`, a close before any audio says `player_offline`.
4. `whats_playing` always resumed. Now only if a press stopped the music in the last 2 min (not after "pausa" or a takeover).
5. Artist filtering could return nothing; quotes broke `artist:"X"`. Moot: artists play by URI.
6. Queue leakage between requests: moot, no queue.
7. A partial first Soloist download wedged the container. Downloads now go to `.new` first.
8. The stream's `finally` could run late on cancel. The factory now wraps the run in `contextlib.aclosing`.
Also: paused from an app for 3 s frees the speaker; a call longer than the patience is spoken (`on_patience_expired`); a run cancelled before its `play` leaves the next run to start the request; `playback_changed` without status keeps the last one; non-dict messages can't kill the client; token responses without `access_token` are a `SearchError`; search timeout 5 s; PulseAudio logs to stderr; the image always rebuilds (cached); pairing script cleans up on Ctrl-C and gives up after 15 min. `expiry_days_left` is logged by the container at start, not the skill (Soloist's WebSocket doesn't expose it); an expired build exits with code 10 and reads as `player_offline`.

**End to end (skill against real search, real Soloist, real capture):** "Los Panchos" → `spotify:artist:...`; 8.2 s of audio (earcon + music, RMS 4,316); no false narrations; whats_playing answered "Contigo - Versión Alternativa" by Los Panchos and offered to resume; cancel paused Soloist within a second. 41 tests; ruff, mypy strict green.

**Docs for other households:** user guide `web/content/docs/use/connect-spotify.mdx` (Premium, own keys, pairing on Mac and Linux, switching accounts, troubleshooting); `docs/skills/spotify.md` updated; the site's skills page fixed (it showed a made-up Spotify config and said per-skill secrets weren't shipped).

**Voice smoke (Mario, phone app, 2026-10-10).** Play, interrupt, "siguiente", "pausa" all worked. "¿Qué canción es esta?" failed: the model said "déjame ver" and the music came back with no answer. Cause in the log: whats_playing returned the resume `AudioStream`, and a tool with an AudioStream gets no follow-up round (`has_audio_stream=True`, `follow_up=False`), so its output was never spoken. Fix 1 (info tool + "say it, then call music_control(resume)") failed again for Mario: once the model resumed without saying the title, once it answered from memory without calling the tool. Measured with a two-turn scripted voice test on the test server (`song_smoke.py`, TTS'd questions through the real server and Realtime model): 2 of 3. Fix 2: the answer turn does one thing (say an exact line, "«Es X, de Y.»"), and the skill queues `inject_turn("call music_control resume, say nothing")`, which the coordinator drains after the answer turn: 5 of 5, music back ~1-1.5 s after the answer. Persona prompt and tool description: always call whats_playing, even if said before.

Mario's retry: he heard "Es..." and then the music. The transcript was complete, so the scripted test (which only read transcripts) missed it. Cause: a framework bug, filed and fixed as T1.31. With it fixed: 4 of 4 runs said the full line, the music came back, and no `audio_clear` reached the client after speech (the test now counts them; against the old code it counts 1).

**In real use (server log, 2026-10-10 23:36 to 2026-10-12 08:49).** Three song questions on the phone after the fix: "Es Cristiano Hipócrita, de Apostoles del Rap.", "Es Música para calmar la ansiedad de los perros, ...", "Es Smooth Operator - Single Version, de Sade.", each followed by the resume turn (`preempts_audio=False`, so no clear cut the answer) and `music_control(resume)`. Spotify also played 37 times over two days, including from the ESP32 client.

**Watch.** 2026-10-12 08:41 (ESP32 client): after "pausa", the user said "Ponlo." and the model answered "¿Dijiste 'pausa'? Listo, dejé en pausa." A misheard or misread resume; one instance. If it repeats, look at the transcription on the device's mic and the persona's resume phrasing.

---

## T1.29: Verify MIXABLE (ducking) end to end before any skill relies on it

**Status**: `queued` (2026-10-10, found by the T1.28 critic) · **Effort**: ~0.5 day to verify; more if a mixer is needed

**Problem.** `AudioStream` docs tell skill authors to pick `MIXABLE` for "ambient / musical content", which keeps the stream pumping at ~0.3 gain while speech overlays. But there is no mixer: the ducked stream's chunks and the model's speech go down the same single audio channel, and the clients (PWA `playback.ts`, iOS native, ESP32) play chunks back to back. Likely result: speech and music interleave, chopping both. No skill uses `MIXABLE` today, and tests cover the focus and gain mechanics, not what a client hears.

**Solution.** Write a coordinator-level test that ducks a stream and overlays model audio, and inspect what reaches the client channel. If it interleaves, either add a server-side mix for the ducked case or change the docstring to steer music to `NONMIXABLE` with a patience until a mixer exists.

---

## T1.30: Resume the interrupted media after a question

**Status**: `done` (2026-10-12, merge `09c736c6`: `2dd8c901`, `14ee088e`, `c0885fb1`) · **Effort**: ~1 day

**Problem.** A press during an audiobook, radio or Spotify stopped the media for good (`coordinator.interrupt()` → stream `NONE`). 2026-10-12 real use: 35 presses stopped media, 12 of them not about media (volume x6, "¿me escuchas?" x2, a Telegram message, "¿en qué capítulo vamos?"); Mario re-asked 6 times ("sigue con la radio", "continúa la canción"); turning the volume down killed the music it was for; the model claimed media was playing after it had stopped. Also: media kept streaming to nobody while the walkie was disconnected (the Beatles ran 33 min, the board was there for ~16), and a provider reconnect stopped media.

**Decision.** This was a proposal waiting on Mario (should media come back on its own?). On 2026-10-12 night Mario asked for everything that failed that day to be fixed, scope "everything"; built. Morning of 2026-10-12, Mario: the answer was clear and not his to make (standard assistant behavior). Kept.

**Fix.** A press pauses content (a hold); the turn's end resumes it after the answer has played, unless the turn ran CancelMedia, started other media, was only the persona's stop words ("para", "ya basta", "apaga la música"), or held it over 30 min. No client for 5 s pauses it until a client reconnects. A provider reconnect leaves it alone; a press during an outage stops it (the user stays in control). The model gets a note before the commit. Audiobooks rewind 2 s plus a tenth of the pause on resume; earcons only on the first run; a finished stream never restarts; Spotify's whats_playing workaround removed. ADR 2026-10-12 supersedes io-plane rule 5.

**T1.30's listed risks.** Stop words: covered by the transcript check even when the model doesn't call the tool, erring toward silence. Calls and long questions: 30 min bound; calls keep their own patience. Audiobook position: rewind on resume.

**Critic.** Earcons replayed on resume; `model_speaking=false` before `audio_clear` (the walkie would play music over the question); a press during an outage did nothing; absence had no cap; a reminder dropping the new stream let the old one back; shutdown leaked audio to a new persona; the drain estimate needed a playhead: all fixed. Tests checked against seven deliberately broken versions.

**Tests.** `test_coordinator_content_holds.py` (34), observer holds, server presence, skills. On the walkie: `bench.py --wifi --steps resume`.

**Left.** Whether the model calls the stop tool for "para" (watch `coord.content_released resumed=true` right after a stop request); a reminder at a press's end still kills the radio (radio has no patience: Mario's call); reminders spoken while no client is connected are lost; only abuelos has localized strings.

**Lesson.** Arm the resume where the turn is cleared, not at each known end path.
---

## T1.31: A queued inject_turn cut off the end of the previous spoken answer

**Status**: `done` (2026-10-10) · **Effort**: ~1h

**Problem.** `_fire_injected_turn` always sent `audio_clear` to flush preempted content audio. But the Realtime model streams its answer far faster than real time (a ~2 s sentence arrives in ~0.3 s), so when a turn ends the client is still playing it. A queued inject drained at that turn end flushed the answer: Mario heard "Es..." instead of "Es Northern Sky, de Nick Drake." Any queued inject did this (a timer or reminder that fires while the user is asking something), not just Spotify's.

**Validation.** Server log 2026-10-10 23:26: answer audio sent 08.81-09.10, inject dequeued and fired 09.11, music resumed 10.02; Mario heard only "Es...". Scripted voice test against the old code: 1 `audio_clear` after the answer's speech.

**Fix.** Send `audio_clear` only when the inject actually preempts something producing audio (a running content stream or a claim). Otherwise the narration queues behind the answer on the client. `coord.inject_turn` logs `preempts_audio`.

**Tests.** `test_queued_request_after_spoken_answer_keeps_client_audio` (fails with the unconditional clear); `test_inject_preempts_active_content_stream` now also asserts the clear still happens when content is preempted. Runtime suite 565 green, mypy, ruff. Voice test 4 of 4 with no clears.

**Docs.** None affected (verified: no current doc describes the unconditional clear; `decisions.md` and older triage entries describe a related, already-fixed guillotine historically).

**Lesson.** Test what the user hears, not what the model said: a transcript is complete even when the audio was cut. The voice test now counts `audio_clear` after speech.

---

## T1.32: A client that reads slowly gets dropped: the server's send buffer has no bound

**Status**: `proposal` (2026-10-11, needs Mario: it changes how the coordinator waits on a client) · **Effort**: S-M

**Problem.** The Realtime model makes a long answer 3-6x faster than real time and the server forwards it as it comes. A client that plays as it receives (the ESP32 walkie when its buffer is full) reads at real time, so the rest piles up on the server. `ws.send()` does await `drain()`, but macOS auto-tunes the socket's send buffer up to `net.inet.tcp.autosndbufmax` = 4 MB (~65 s of audio), so the coordinator never feels it. Everything sent after that (the `websockets` keepalive ping, `audio_clear`, state) queues behind the backlog: the ping waited 30 s, `websockets` failed the connection after 20 s (`client_disconnected close_code=1006`), and a barge-in `audio_clear` arrived 2.1 s late.

**Validation.** Bench, 2026-10-11 (firmware F-0015, F-0016): `server.tx.model_speaking false` at 00:36:56.576, received by the board at 00:37:26.352 (29.8 s), connection failed at 00:37:26.375. `netstat` showed the socket's send queue pinned while the board read at real time.

**Firmware side, done.** The walkie's ring now holds a whole reply (4 MB, 87 s), so in practice it reads at network speed and nothing piles up (last message lag 2.8 s on a 150 s story, no disconnect), and it reconnects after any close. The gap left: a reply longer than ~87 s, or any future client that plays as it reads.

**Proposal.** Bound what the server queues per client: set `SO_SNDBUF` on each accepted socket (e.g. 256 KB, which turns off auto-tuning) so `drain()` reaches the coordinator, or pace forwarded model audio to a few seconds ahead of real time. Either way the coordinator's `on_audio_delta` would then block on a slow client, which needs a look at what else waits on it (the provider's receive loop). Not done because it changes the turn coordinator's timing for every client.

---

## T1.33: A restart overlap took Abuelo down for 55 s

**Status**: `done` (2026-10-12, merge `00a0ff44`; `a8ea1e23`, `73ac16fe`, `4c89d8ce`, `fc786f84`) · **Effort**: M

**Problem.** 2026-10-12 08:49Z: the server was restarted (by hand, `uv run huxley`) while the old process was still in its 5.6 s shutdown (3.5 s summarizing). Telegram's session DB was locked; its retry reused a PyTgCalls object that can't start twice (`PyTgCallsAlreadyRunning` on every retry); `setup_all` propagated the error and the whole persona was down: the walkie had no assistant for 55 s, silently. Timers and reminders teardown then asserted (setup never ran).

**Fix.** A fresh client pair per Telegram retry (verified offline against the real libraries with the session file held locked: four attempts, each "database is locked", every handle closed); a failed skill setup disables only that skill, tears it down and logs `skill.setup_failed`; its tools stay offered and answer through the tool-error path, so the model says it out loud (removing them would let the prompt's named tools be "called" with nothing happening); timers/reminders teardown safe without setup; one server per port (`logs/huxley-<port>.lock`, a new one waits up to 30 s, then exits 1) and a 20 s shutdown limit (the summary call has no timeout); same-persona reload stops the old app first (else every config edit would disable Telegram, whose running client holds the session file).

**Review.** Design critic before coding, code critic after; both changed the result.

**Docs.** architecture, observability, decisions, skills README/authoring/telegram, CLAUDE.md, run-the-server, launchd README.

**Not done.** A disabled Telegram stays off until the next reload (no background reconnect); no lock on the persona's data folder across ports; `reconfigure_all`, prompt text and tool list can still take a session down the same way; disabled skills aren't shown in the PWA. Pre-existing: client-event dispatch is switched off on the first persona swap and never back on (no skill subscribes yet).

**Lesson.** A retry must rebuild the object that failed, and test fakes must refuse a second start the way the real library does.

---

## T1.34: Mishearing, wrong tools and wordy replies in real use

**Status**: `done` (2026-10-12, merge `1ae453eb`: `aa62b2e6..e82725a4`; `88cdff45`) · **Effort**: M

**Problem.** From the 2026-10-12 log audit (Mario on the walkie, 06:44-08:41Z): the model misheard music when Whisper's transcript was right (Adele played as Sade, "Pon Blue Radio" played Adele, "Los Destellos" became the prompt's example genre and played Los Panchos, "Weird Fishes de Radiohead" answered as "Voltages de Radio Alta" twice); wrong tools ("sigue con la canción" skipped to the next song, "ponlo" paused the audiobook while Spotify played, "el segundo" unresolved, "¿cuáles tienes?" listed 5 of 19 books); 19 of 88 replies ended with an unprompted offer; "¿Dijiste 'hola'?" x7.

**Why.** The persona said anything that sounds English is a transcription error; example nouns ("boleros", "Los Panchos") became the model's guesses; ask-first applied to undoable actions; "pausa" was mapped to the audiobook control; the echo constraint fired on clear commands.

**Fix.** Names kept as heard in any language; no example nouns (tests guard it); best guess then act, never the same guess after "no"; controls follow what was last playing; ordinals pick from the last list; empty search lists the whole library; no echo for clear commands or answers to the assistant's own question; no closing offers. `play_music` returns two runners-up as `alternatives`. Spotify re-ranks a song search by the heard title (letters, difflib), and searches the artist's own tracks when none of the top five is close (`88cdff45`).

**Evidence.** `bench.py --wifi --steps hearing` injects yesterday's requests as speech (macOS `say`, never played) and checks each tool call: 10 of 11 right on the first run. The miss, "Weird Fishes de Radiohead": run 1 the model searched "irfixes Radiohead" and Spotify played Let Down (Weird Fishes was the runner-up; fixed by the re-rank, unit-tested on those exact candidates); run 2 the model itself asked for "Creep Radiohead": a hearing limit of the realtime model (Whisper's transcript was garbled too, "nuyer fiches"), which no skill can undo.

**Behavior choice, accepted by Mario 2026-10-12.** "Pon música" with nothing named continues the last music Spotify had loaded; asks only if nothing was ever played; never picks a default genre or artist. Same for "pon un libro".

**2026-10-13, Mario live ("not doing what I ask")**: he asked for Daft Punk's "Voyager" said the Spanish way; heard as "boya", "bodiaguerra", "boya gordo", "BOYA GUERRA". The model dropped the odd word and searched the artist alone twice, and answered "No, por un bodiaguerra" with `resume`; the fourth try played it. Spotify's search finds Voyager from "boya Daft Punk", "boya guerra Daft Punk" and "boya gordo Daft Punk" (checked on the Pi), so the miss was the model dropping the word: the tool text now says any word besides the artist is the song's name, kept as it sounded, and a "no, ..." correction is a new search, never `resume` (es, en, fr). Separately "canción anterior" 5 s into an autoplayed song restarted it twice: Soloist's `skip_prev` only goes back in a song's first seconds; music `previous` now seeks to 0 first and waits (`spotify.previous`). Checked on the firmware session's `hearing` bench (clips 16-18): "canción anterior" first went to `resume` (fixed in the tool text, 1b4f826d; then `spotify.previous rewound=true landed=true`, Soloist does report the position after a paused seek), and "boya" was flaky (the model once re-spelled it "Boady"); after the as-it-sounded wording (6eed6206) row 16 passed 4 of 4, all playing Voyager.

**Left.** The model's hearing and tool discipline (`gpt-realtime-2.1-mini`). Measured 2026-10-12 night on `hearing` (11 requests, injected speech): mini 49/55 over five runs (misses: the Weird Fishes title as "irfixes"/"Creep"/"High Hopes", "Ponlo" answered "ya sigo con la música" with no tool call, "para la radio" with no call); `gpt-realtime-2.1` 22/22 over two runs, at $0.0139 per response vs $0.003 (about 4.6x; yesterday's use was ~$0.80/day on mini). A general persona rule now says: no "sigo"/"pongo"/"listo" without the tool call in the same turn. Mario, 2026-10-12 morning: stay on mini.

---

## T1.35: Silent presses answered, presses unanswered, the 60-minute expiry, log gaps

**Status**: `done` (2026-10-12, merge `c6eb7ca3`: `16eb46cc`, `c2ce78f7`, `59161739`, `57aeb543`, `29269ae5`, `0d70921c`) · **Effort**: ~1 day

**Validation.** 2026-10-12 06:44-06:45Z: 11 presses committed with nothing said, transcribed as "Subtítulos realizados por la comunidad de Amara.org" and answered; 3x `conversation_already_has_active_response` after a cancelled response's late `response.done` ended the next turn, and those presses got no answer. `session_expired` at 07:31 and 08:31 (the 55-min timer cancelled its own disconnect; on 10-11 the session expired every hour). Board log lines arrived as `info` whatever their level (structlog overwrote the client's `level`). Tool outputs and OpenAI close codes weren't logged.

**Fix.** Silent-press gate (`turn/press_level.py`: peak under -40 dBFS outside the first 100 / last 60 ms is not committed; every `coord.ptt_stop` logs `peak_dbfs`/`rms_dbfs`); a cancel generation in `response.metadata`, events of cancelled responses dropped, a new request waits for the live one (5 s backstop, `session.tx.response_create_forced`); session refresh at the first idle moment after 50 min (`session_refresh_minutes`; not during a call; the summary computed while the session is up, 10 s cap); tool outputs (300 chars) and close codes logged; client-event fields that clash with the log's own keys become `client_<key>` (`client_level`).

**Evidence on the walkie.** `bench.py --wifi --steps silent`: before (server of 2026-10-12 03:49) the empty press (peak -55 dBFS, rms -67) was transcribed as "Subtítulos realizados por la comunidad de Amara..."; after: see the morning run.

**Critic.** No blockers; should-fix items done in `0d70921c` (a suspend dropping a held-back create, a lost done, the refresh's dead window, a click defeating the gate).

**Left.** A refresh can land seconds after a book starts (T1.30 made the reconnect leave media alone); the client still sees the session drop and come back once per refresh; an old `HUXLEY_CONVERSATION_MAX_MINUTES` in `.env` is silently ignored. Watch for `session.rx.metadata_rejected`.

**Lesson.** A timer that disconnects from inside the task it would cancel never fires: test what happens after `cancel()`.

---

**Still open (2026-10-12 15:56Z)**: two quick near-silent presses passed the peak gate (peak -27.3 and -29.4 dBFS) and were transcribed as "Subtítulos realizados por la comunidad de Amara.org", Whisper's hallucination on silence, which the model answered. Peak alone can't separate these; the VAD gate in progress was stopped (agent cancelled), so this needs a fresh attempt.

**Also seen (2026-10-12 18:31:53Z)**: a bench turn's injected "¿Qué hora es?" was transcribed right, but the model answered "¿Pediste que tú lo pongas?" with no `get_current_time` call (audio kept: `data/recordings/2026/10/12/183155-394f2a0a-002-model.ogg`). With the earlier "No, ¿qué hora es?" that paused a book, the realtime model sometimes misreads short questions that the transcription gets right. Watch whether it repeats on the recordings. Again on the Pi, 2026-10-12 21:43:13Z: "Un bachata." was answered with "no tengo forma de crear botones dentro de WhatsApp" (Mario's next press: "No, pon Bachata en Spotify"). Two-word requests are where it happens; the recordings for both are kept.

**Quiet presses with one loud instant (2026-10-12)**: a 19.6 s test press of room noise (peak -22.8 dBFS, the press averaging -55.4) passed the peak gate and was answered on the Pi as "Amara.org" ("¿Qué es 3, 2, 1?"). Measured on the day's 30 recorded presses (decoded from `data/recordings/`): speech averaged -19 to -43.5 dBFS over the press (the logs' 234 spoken presses: never below -45.6) with its loudest 250 ms at -14 to -38.9; the near-silent phantoms averaged -52 to -58 (that one's loudest 250 ms: -40.3). Fixed in `press_level`: a press is also silent when it averages under -50 dBFS AND no 250 ms of it reaches -38 (`silent_why=quiet`; a long press with one short word still passes); `coord.ptt_stop` logs `window_dbfs`. Replayed on the 30 recordings: all 23 spoken presses kept, the phantom and one empty press dropped. Not caught: loud handling noise (three phantoms at 19:58:38-40 peaked at full scale, averaging -17 to -21): no level tells a knock from a voice. **Lesson**: my test used `talk`/`release` with the real mic open for 20 s, which isn't silent: bench presses use injected speech only.

## T1.36: Session gaps: a press during the refresh, injects with no session, "ya", "sigue"

**Status**: `done` (2026-10-12; merge `f3ffc9b7`: `773d7771`, `b7663753`, `da125112`; `c389d108`; PWA `06517a36`) · **Effort**: ~half a day

**Problem.** A fresh critic on how tonight's merges (T1.30, T1.33-T1.35) interact: (A) the 50-minute refresh counts "music playing, no model traffic" as idle, and a press in its ~3 s gap went through the rejected path that T1.30 makes stop the media for good: the book stopped and the press got no answer; (B) "ya" was both a T1.30 stop word and the reminder confirmation, so "ya" after a reminder stopped the parked book, and the tool texts called "ya" a pause; (C) a timer or reminder firing with no session never ended its turn, kept the music parked and blocked later injects; (D) "sigue" during a press's hold made the model call the resume tool, replacing the held stream (20 s rewind, earcon again); the PWA beeped its drop tone on every refresh.

**Fix.** (A) a press within 5 s of a drop puts a `"session"` hold on the stream, released by the new `on_session_connected`; later presses still stop it; the walkie plays the reconnect double beep for a rejected press (it had no sound for it). (C) injects queue while the session is down and fire on connect; a book ending in the gap skips its end prompt (the chime still plays); `inject_turn_and_wait` waits up to 10 s so a Telegram call is still announced. The coordinator tracks the session itself: `provider.is_connected` stays true after a server-side close (found by the agent's critic). (B) "ya" out of the stop words and tool texts; test: reminder confirmations are never stop words. (D) the held-content note says "to keep going, call no tool". PWA: the drop tone waits 5 s.

**Tests.** 14 new (A, C), the persona guard (B); runtime 711.

**Open.** A press early in what becomes a long outage resumes the book when the session returns (up to T1.30's 30 min): Mario's call. Race: a new session closed by the server in the instant before `on_session_connected` can still swallow an inject.

**Lesson.** Review the combination, not just each branch: four reviewed branches still met in four bugs.

---

## T1.37: Mario's morning test, 2026-10-12: "pausa la música" resumed; the Wi-Fi starved the walkie

**Status**: `in_progress` (2026-10-12) · **Effort**: S (the overrule); the network is open

**Evidence** (11 presses, 08:05-08:11 local, `server/runtime/logs`): "Pon Weird Fishes de Radiohead" played Weird Fishes / Arpeggi; "Pausa la música", transcribed right, made the model call `music_control(resume)` twice: the stop words stopped the paused stream and the resume started the music again (third try, "PAUSA", worked). "¿Cómo se llama el cantante de la banda Genesis?" first searched audiobooks for "El origen Henesis" (right on the second try); a 380 ms press with an empty transcript got "¡Hola! ¿Qué te gustaría hacer ahora? Puedo ayudarte..."; "Mejor el volumen" got a question instead of an action. Mic: speech peaks -2.9 to -25 dBFS with the button clicks left out (server `coord.ptt_stop`); the board's 0 dBFS peaks and 19-45 clipped samples per press are the lever's clicks, not the voice: no gain change needed.

**Network.** Two stalls: 08:07:34-08:07:53 (server on time, `sock_unsent` 6 KB to 131 KB, sends blocked up to 3.7 s, the board got 100-200 ms of audio per second at rssi -48..-52) and 08:09:41-08:10:47 (sends blocked up to 37.5 s; the server dropped the WebSocket 1006, the board reconnected in 3 s). The enclosure session measured, board idle: 9.3 % ping loss Mac to board (p90 106 ms) against 0 % to the router; the board sits on the main Deco's 2.4 GHz radio, ch 9 at 40 MHz, next to a neighbour on ch 11 at 40 MHz. It is A/B testing HT20 on the board.

**Fix (the overrule).** When the press's transcript is only stop words, the turn's own streams are dropped (or stopped when the transcript arrives within 3 s): `coord.streams_overruled`. Tests: `TestStopWordsOverruleTheModel` (2 of 3 fail without it). bench.py `resume` now fails if any stream starts after "para la música".

**Wi-Fi, measured on the board** (enclosure session's diagnostics, merged `96290a59`): the walkie joined channel 9 at 40 MHz; its own scan hears five of our mesh nodes on channel 9 (-43 to -85 dBm, each with a hidden backhaul SSID) and the nearest neighbour at -97 dBm, so the contention is mostly our own mesh. A/B with the radio streaming, 3 rounds x 2.5 min per width, amp off: starved 31, 0, 0 s at 20 MHz and 0, 1, 24 s at 40 MHz: no difference while the network was calm (ping loss 0 %, against 9.3 % at 08:13). 20 MHz is now the default anyway (standard for a crowded 2.4 GHz band, no measured cost; `wifibw 40` until a restart). `dev.py link` reports, per half hour of real use, the share of playing time that was gaps (from the speaker's own counters): 3.3 % in the night's choppy half hour, 34.3 % at 08:00-08:30 this morning. Watch it over the next days to see if 20 MHz moves it.

**Open.** The Wi-Fi: if gaps stay high with 20 MHz, the router's 2.4 GHz can go to 20 MHz on ch 1 or 6 (Mario's router), or the server onto Ethernet. Bandwidth: the walkie gets PCM as base64 JSON, ~64 KB/s; Opus would need ~3-4 KB/s and would have ridden out the first stall (protocol + firmware decoder: a plan, not a patch). Model misses (Genesis, the empty press's offer, the volume question) stay with mini (Mario's choice).

---

## T1.38: Opus audio for the walkie (server side of firmware F-0024)

**Status**: `done` (2026-10-12, merge `2fdc6a88`: `53cc0a87`, `23eb8f33`); on the board after the next idle window · **Effort**: M

**Problem.** Base64 PCM at ~64 KB/s doesn't survive the walkie's 2.4 GHz link (ping loss up to 8.3 %, p90 up to 665 ms in episodes; the Mac's link 0 % meanwhile): TCP delivers a fraction of it and playback cut for seconds (T1.37). Mario: do it the way others do.

**Design (borrowed).** xiaozhi-esp32's WebSocket protocol: the client opts in (`?audio=opus`), the server's hello carries `audio_params` {opus, 24000, 1, 60}, audio both ways is one raw Opus packet per binary message, JSON stays for the rest. `opuslib_next` (what xiaozhi's Python server uses) over the system libopus. PCM clients (the PWA) unchanged.

**Built.** Server encoder AUDIO, VBR, 32 kbit/s (~240 B per 60 ms packet, ~4 KB/s); a sound's partial last frame is padded and sent before `model_speaking:false` / `stream_ended`, or after 250 ms idle outside a stream (100 ms would race real-time 100 ms chunks); `audio_clear` drops pending audio and resets the encoder first; no binary before the hello; the decoder resets on `ptt_start`; bad packets logged and dropped. `server.tx.audio_stats` gains bytes sent. Review: a PCM send in flight could leak JSON audio to a walkie that just switched (fixed).

**Tests.** `test_server_opus.py`, `test_firmware_contract.py` additions, all on the real libopus; Opus-mode tests fail on the old server; runtime 752.

**Open.** libopus on the Orange Pi (`apt install libopus0`), not verified there. On the board: bench (turn, bargein, stall, slowdown, resume) and `dev.py link` against this morning's 34 %.

---

## T1.39: Spotify audiobooks on the device

**Status**: `in_progress` (2026-10-12; built, waiting on Mario's smoke on the walkie) · **Effort**: S

**Ask** (Mario): Spotify added audiobooks to Premium; can the device play them? On 2026-10-07 Spotify started rolling audiobooks out to 180+ markets, Colombia included (rollout "over the coming months"); Premium there includes 12 hours of listening, with the Audiobooks+ add-on (15 hours) or bought titles beyond it.

**Validate** (silent: Soloist's sink had no reader). The Web API (our app token, `market=CO`) already returns audiobooks: "Cien años de soledad" = `spotify:show:1oVPQwdET6CETd7KFZAghH`, its chapters are `spotify:episode:` items. Soloist accepted that URI, loaded chapter 1 ("Créditos iniciales", 0:18) and logged `stopped` 0.3 s later, no error event. A podcast episode (`spotify:episode:5ieh7vEheH2hPvPmMjQWBW`) played normally on the same Soloist (0:09 reached), so Soloist plays episodes; audiobook chapters are refused. Soloist's docs never mention audiobooks. Soloist's last context restored after each test (Rap God, paused).

**Cause found** (Spotify's docs + Mario's account, read in his browser). Soloist is not excluded: its README and launch post list "music, podcasts, and audiobooks: the whole catalog". Mario's account is the Premium Familiar plan holder and has the hours (12 h, 0 used, page "Tiempo de escucha de audiolibros"). But Soloist is logged in as a different account (the household member on the same plan; Soloist log "logged in as 31dm..."), and Spotify's support page "Audiolibros en los planes Premium" says only the plan holder gets audiobook time; members get none unless the holder buys it for them (Audiolibros+ para miembros, or top-ups). A member with 0 hours is refused, which matches the 0.3 s stop.

**Mario's call**: buy hours for the household account (add-on or top-up), or pair Soloist with his own account (free 12 h, but the device then shares his account: his history, and one stream at a time with his phone).

**Unblocked**: Mario bought hours for the household account; the same silent test then played (0:09 of the credits reached). Replaying the book continued at 0:09 into chapter 1: Spotify keeps the place, nothing to store.

**Mario's call**: turn the local audiobooks skill off for Abuelo (books only from Spotify).

**Built**: `play_music` `kind: "audiobook"` (search `type=audiobook`, authors as `by`); a fresh start idle within 3 s is a refusal (`REFUSED_S`), spoken (`audiobook_refused` / `start_failed`) instead of ending as a finished album; `book_not_found`; `whats_playing` names book + chapter. Abuelo: `audiobooks` block commented out, the book instructions in es/en/fr now point at `play_music` / `music_control` (no speed, no seek: Spotify books have neither).

**Tests**: spotify 68 (6 new: refusal spoken for a book and for music, book kind reaches search and plays the show, whats_playing names the book, book not found, search reads authors); runtime 757, with a guard that no persona prompt names a tool of a skill it doesn't enable (fails on the old abuelos prompt with the skill off).

**Docs**: docs/skills/spotify.md (Audiobooks, failures), docs/personas/abuelos.md (skills, rules, v1 criteria), docs/skills/audiobooks.md (Abuelo no longer uses it).

**Smoke 1 (Mario, 15:58Z)**: "pon el audiolibro del extranjero": Spotify refused it (idle 1.2 s after start; `spotify.start_refused` fired, so the silence check worked) because "El extranjero" is purchase-only (the app shows "Comprar", "Cien años de soledad" shows "Incluido con Premium"; the Web API doesn't expose which). Then the model, told to say Spotify wouldn't play it, said "el audiolibro quedó en reproducción": it trusted the tool's `ok: true`. Fix: refusals state the fact and give the exact line (the whats_playing format), naming both causes (not included, or no hours).

**Smoke 2 (15:59Z)**: "pon silencios de soledad" (misheard "cien años de soledad") was searched as "Silencio", kind `any`, and played an unrelated song: the model cut the heard words instead of passing them as heard. Then "pon el audiolibro de cien años de soledad" played the book from chapter 1, where the earlier test had left it (Spotify's resume works end to end on the walkie).

**DoD left**: Mario asks for a book on the walkie: it plays, "pausa" / "sigue" / "siguiente capítulo" work, "¿qué está sonando?" names the book.

## T1.40: Books for grandpa: everything he'd ask of a Spotify audiobook, and every failure, spoken plainly

**Status**: `in_progress` (2026-10-12) · **Effort**: M

**Ask** (Mario, after T1.39 worked): think as grandpa, the least technical user possible. A book that isn't available must be said clearly; "¿qué libros me recomiendas?" must get books he can actually play; next / previous chapter, start over, and so on; and Mario, testing, wants to erase a book's progress.

**Facts that bound the design** (2026-10-12):
- Soloist's WebSocket (reference): `play` takes only `uri` (track, album, playlist, episode; no offset), `skip_next`, `skip_prev` ("previous track or restart current"), `seek {position_ms}`, repeat/shuffle; `playback_state.position {position_ms, timestamp_ms, speed}` and `position_sync` give the position; `options.playback_speed` is reported but there is no command to set it. So: chapters and seeking yes, speed no.
- Spotify keeps each account's place in each book (T1.39: a replay resumed at 0:09). Playing chapter 1 from 0:00 moves that place back to the start: that is "desde el principio", and Mario's reset.
- The Web API with our app token lists a book's chapters (number, name, `spotify:episode:` URI, duration) but can't tell included from purchase-only: "Cien años de soledad" (included) and "El extranjero" (purchase-only, "Comprar" in the app) both answer `is_playable: true` and every chapter `restrictions.reason: payment_required`. Whether a household-account user token (OAuth, one approval) shows the difference is untested.
- Search returns reading guides next to the books ("El extranjero (Guía de lectura): Resumen y análisis").

**Grandpa's requests** (tools are new and book-only, so the small model never mixes them with music):

| He says | Tool | What happens |
|---|---|---|
| "pon Cien años de soledad", "pon el libro de <autor>" | `play_book(title)` | search books (guides and summaries dropped, his language first), play; continues where he left it |
| "sigue con el libro", "pon mi libro", "pon el libro" | `play_book()` | the last book he listened to, where he left it; none yet: asks which book |
| "desde el principio", "empiézalo de nuevo" | `play_book(title?, from_beginning: true)` | chapter 1 at 0:00 (also Mario's reset) |
| "siguiente capítulo", "el capítulo anterior", "repite el capítulo", "ve al capítulo 5" | `book_control(next_chapter / previous_chapter / restart_chapter / go_to_chapter)` | moves and keeps playing; at the ends: "ya es el último / el primer capítulo" |
| "regresa un poco", "repite eso", "adelanta cinco minutos" | `book_control(back / forward, seconds)` | default 30 s, kept inside the chapter |
| "pausa", "sigue" | `book_control(pause / resume)` | as music |
| "¿qué libro es?", "¿en qué capítulo voy?", "¿cuánto le falta?" | `book_status()` | book, author, chapter X of N, minutes left in the chapter, about how long in the book; the book comes back after |
| "¿qué libros me recomiendas?", "¿qué libros hay de <autor>?", "¿qué libros he empezado?" | `find_books(query?)` | up to 5 (title, author, hours); books Spotify refused here are left out; started ones marked; no query: the started books, then asks what author or kind (no default taste) |
| "más despacio" | (persona) | speed can't change: said so, offers to keep listening |

**Every failure, as an exact line** (the T1.39 lesson: a stated fact plus the exact words, or the model contradicts it):
- not found: "No encontré ese libro en Spotify. ¿Busco otro?"
- refused, never played here: "Ese libro no se puede poner: Spotify lo vende aparte, o ya se acabaron las horas de libros de este mes. ¿Busco otro?"
- refused, but it played here before (so it is included): "Se acabaron las horas de libros de este mes en Spotify."
- the book ended: "Se terminó <libro>." and asks if he wants another
- another device took it: "El libro pasó a sonar en otro aparato."
- the sound cut: "Se cortó el libro. ¿Lo vuelvo a poner?"
- Spotify offline / not connected: as music, offering the radio
- chapter control with no book: "No hay ningún libro puesto."

**State the skill keeps** (`ctx.storage`): the current book (uri, title, author), the books started (for "sigue" and the list), and the books Spotify refused here (never recommended again). Positions stay Spotify's.

**To verify on Soloist before relying on it** (silent tests, walkie idle): does `play <chapter uri>` keep playing into the next chapter; `skip_prev` at 0:00 vs mid-chapter; `seek`; position events.

**Later, needs Mario** (one OAuth approval by the household account): check whether a user token marks included books; if it does, recommendations list only playable books and a refusal can say exactly which cause.

**Soloist, measured silently on "Cien años de soledad" (2026-10-12)**:
- `play <chapter uri>` makes the chapter its own context: it plays alone, stops at its end, `skip_prev` disappears from `available_actions`, `skip_next` does nothing. But it moves the book's resume point (a later `play <book>` resumed in that chapter). So chapters are reached inside the book, never by URI.
- In the book: `skip_next` goes to the next chapter at 0:00; `skip_prev` at a chapter's start goes to the previous chapter at 0:00, mid-chapter it restarts the chapter, on the first chapter it restarts it. Three rapid `skip_prev` moved back three chapters.
- A skip while paused starts playback (so a multi-chapter jump plays a blip of each chapter: the stream stays mute until the target chapter is on). `seek` while paused stays paused.
- `playback_state.position` / `position_sync`: `{position_ms, timestamp_ms, speed}`; `decorations.playback.duration_ms` on the item.
- Reset: in the book, back to "Créditos iniciales", seek 0, pause: the next `play <book>` starts at the credits. Done for Mario's test progress.

**Critic (fresh agent) round 1**, 3 blockers / 7 majors, taken (v2):
- B1 every idle in a book stream is classified by position against the chapter list: the end (last chapter, near its end) / a cut / a refusal, each spoken; B2 no refused list: a list of books that played here, plus book seconds counted per month, so a refusal of a book that played here is "hours", otherwise "must be bought"; B3 pause / resume / next / previous stay in `music_control` and act on what Soloist has loaded (next / previous are chapters in a book), `whats_playing` answers for books too, `play_music` loses `kind: audiobook`; new book tools only for book-only things.
- M1 the last music and the last book are kept apart ("pon música" after a book plays music); M2 "desde el principio" past the first chapters asks book or chapter, and the place before is kept for "vuelve a donde iba"; M3 a finished book is remembered (replaying it is not a refusal); M4 Spotify leaving the book's show on its own is the book's end; M5 falling asleep: after 60 min with no press the book pauses at the end of a chapter; M6 a resumed book backs up 2 s + a tenth of the pause (max 20 s); M7 chapters by their name ("Capítulo 5"), never index.
- M8 recommendations can't be trusted until OAuth tells included books apart: until then "recomiéndame" reads books that played here and asks for an author or kind; a search reads at most 3, numbered. m1 plainer lines, one cause, always an alternative; m2 "el segundo" matches the last list read; m3 a weak title match is confirmed before spending hours; m5 position tracking.

**Built** (c862d210, fa14c0f4; critic round 2 fixes 80bb424c; probing and the seek fix f0e3033c): see the commit bodies and `docs/skills/spotify.md` "Audiobooks". Spotify tests 113. Critic round 2 on the code (2 blockers, 5 majors, 7 minors) all taken: refusals on any play of a book (not only fresh starts) said as hours / unavailable; "sigue" after a cut restarts the book by URI; a press mid chapter-move finishes it on resume; positions only move while playing and reset on a track change; the end only in the last chapter's last 15 s; a finished book starts over without the second question; author + wrong title still confirmed.

**Mario's direction during the build**: close the included-or-not gap ourselves: recommendations try each unknown book for a moment and offer only the ones that play (a known book refused too = no hours); "paid apart" and "not offered" are one line ("no está disponible"); test now, fix while he tests.

**Smoke 3 (Mario, 16:54-16:57Z, server on dbf99fb7)**: "¿qué libros tienes?" went to `get_news` (find_books now lists that phrasing); "dame sugerencias de autores importantes" searched the whole sentence and got a leadership book (vague requests now become a concrete topic or author); "Mujeres a través de la historia" played and "¿qué está sonando?" said book, chapter and time left correctly; a resumed book played about a second, cut, then resumed (the back-up seek was sent before "play"; now after, silent until it lands). Deployed f0e3033c at 17:00Z.

**Open**: Mario's smoke on f0e3033c (resume without the blip, a recommendation, a chapter jump, a refused book); the "Amara.org" phantom transcript on near-silent presses (T1.35) showed up again at 16:57:43Z.

**Bug found in use (2026-10-12 20:30:25Z)**: "Pon Siddhartha" got "Este mes ya no quedan horas para escuchar libros" while the account had hours. Measured against Soloist directly (silent, 20:32Z): Sherlock Holmes, El amor en los tiempos del cólera and Mujeres a través de la historia all play; only Siddhartha is refused (buffering, then idle in 0.2-0.9 s, with or without a seek). Two faults: `find_books`' check had called it "ok" after 0.35 s, before Spotify's verdict (the first "playing" comes at once); and `_refusal_cause` read "a book known to play, refused" as the hours. Fixed: a check says "ok" only for a book still playing at the end of 1.6 s; a refusal is the hours only when the budget is spent or a control book known to play is refused too, tried after the stream ends (`spotify.refusal_explained`); a book that played in the last 30 min means the hours are there; a refused book drops its "ok" and is checked as unavailable before as playable. Three regression tests fail on the old code. The server session's page reads only `refusal_explained cause=book_no_hours` as "no hours". **Lesson**: a positive signal that arrives before the other side has decided isn't a verdict; and a guess about the cause of a failure ("hours") must be checked against a control before it's said to the user.

## T1.41: A waiting sound while a tool runs (no dead air)

**Status**: `in_progress` (2026-10-12; built, waiting on Mario's smoke on the walkie) · **Effort**: S-M (turn coordinator)

**Problem** (Mario, live): "it told me it was gonna look for some books and then it kept silent for like 10 seconds... it feels like it's not doing anything." The log: 22 s from "Un momento, déjame buscar" to the answer (two find_books calls). `docs/turns.md` promises a thinking tone that fills silence, but only the PWA has one, it starts only at `ptt_stop`, and the walkie has none; during a tool the server sends the client nothing at all.

**Decided** (Mario asked "the same sound as connection lost, or a different one?"): different. The walkie's offline cue is a falling pair (440 then 330 Hz) meaning "something's wrong"; waiting must sound like "working, wait": soft, steady, repeating. Using one sound for both would teach the listener that searching means trouble.

**Design v1** (T1.41): the server, not each client: only the coordinator knows a tool is running, and the walkie then needs no firmware change (it plays any audio while `model_speaking` is false, without the rebuffer).
- A `WaitCue` in `server/runtime/src/huxley/turn/`: armed when the user is waiting (a committed `ptt_stop`; a tool call's dispatch); it starts only when the client has gone quiet (`_client_playhead_remaining_s()` is 0: audio is forwarded faster than real time, so "nothing sent" is not "nothing heard") and stayed quiet `WAIT_CUE_AFTER_S` = 1.5 s (the PWA's threshold, past normal model latency); then one soft blip every 1.2 s.
- Disarmed before any real sound: model audio (`on_audio_delta`), a `PlaySound`, a content stream's first chunk, a claim; and on a press, an interrupt, the turn's end, a session drop. Never for injected turns (a reminder isn't something the user is waiting for).
- The sound: one steady soft note (about 520 Hz, 90 ms, raised-cosine envelope, about -24 dBFS), generated in code: a small speaker can't play the PWA's 120 Hz, and a steady repeat reads as "working", unlike the offline cue's falling pair. Sent as plain audio (`model_speaking` stays false).
- Logs: `coord.wait_cue_start` (reason, waited_ms), `coord.wait_cue_stop` (reason, blips).
- The PWA's own post-release tone overlaps it at 1.5 s: the critic decides whether it goes.

**Critic** (fresh agent, on design v1; all taken): B1 stopping by cancelling the task could clear the walkie's shared Opus queue and drop the answer's first packets: stop is a flag, a blip being written finishes. B2 a 90 ms blip splits across 60 ms Opus frames (a click and a false `spk.underrun`): 120 ms. M1 a blip could reach the open mic: disarm is the first statement of `on_ptt_start` and of `interrupt`, and every `current_turn` change. M2 no limit: stops after 30 s (`reason=cap`). M3 1.5 s after release would blip before one ordinary answer in nine (measured p90 2.02 s): 2.5 s for replies, 1.5 s after the playhead for tools. M4 the PWA's own tone collided: removed, visual "thinking" kept until model_speaking / audio_clear / press. Minors: user turns only; a second tool keeps the running cue; cue audio out of the playhead and out of `server.tx.audio_stats`; louder struck note (about -16 dBFS, D5, every 1.5 s); the firmware no longer counts the gap before a reply as an underrun. Found on the way, fixed: a tool result that lands after a press started a new turn was attached to it (`coord.tool_result_stale`).

**Tests**: `test_wait_cue.py` (6: whole Opus frames and level, quiet first, no blip before the threshold, a send in flight finishes and nothing follows, the cap, re-arming), `test_coordinator_wait_cue.py` (5: a long tool wait blips and the answer stops it, a quick answer doesn't, a press stops it, injected turns never arm, a stale tool result is dropped; that last one fails without its fix). Runtime 768. PWA `bun run check` green. Firmware build + host tests green.

**Docs**: `turns.md` "The waiting cue" (replaces the client thinking-tone section), ADR 2026-10-12 "The waiting sound comes from the server", `sounds.md` Stage D superseded, `roadmap.md`, `observability.md` (the three new events).

**DoD left**: Mario's walkie smoke: a book search (blips during the wait), a plain question (no blip), a press during the blips (no leak), and his ear on the level. Firmware with the underrun reset flashed when the walkie is idle.

## T1.42: The caregiver's view: insight and alerts, never an event log

**Status**: `in_progress` (2026-10-12: v0 built on branch `t142-watch`, see "v0 built" below; the archive built on branch `t142-archive`, see "Archive built"; design v1 in [`remote-operation.md`](./remote-operation.md) W6, part of T1.43) · **Owner**: the "Huxley improvement plan" session (handed over 2026-10-12 at Mario's request: diagnostics, monitoring and alerting architecture live there; features and firmware stay in the firmware session) · **Effort**: L

**Brief** (Mario): alerts and a beautiful, text-light way of knowing what's going on, so he can fix things from his home: audio cutting up on the device ("check what may be going on"), the Orange Pi down, the device out of battery, something unusual, OpenAI credits out or running low, audiobook hours low (buy more), the internet was down, the device not seen in a couple of days; and how the device is being used. **Not wanted**: a complex dashboard, individual events cluttering the board, everything shown with the important part buried; only what tells him something is going on.

**What's measurable today**: link quality and gaps (`spk.alive` underruns, `server.tx.audio_stats`, `dev.py link`), last seen / disconnects, session errors, turns and what was used (books, music, radio, calls), book seconds per month (T1.40 shelf), OpenAI spend (`cost.response_done`). **Needs something**: the Pi being down (an outside watcher: the Pi can't report its own death), credits (OpenAI has no balance API: spend against the balance Mario tops up), battery (F-0025, needs the solder fix), alerts reaching Mario (a channel to choose).

**Proposal v0** (T1.42, before a critic; Mario's two channel answers decide the plumbing):
- One page, read in seconds. Top line: "All good" or "2 things need you". Then only the things that need him, each a plain sentence, why it matters, and one action ("About 2 h of audiobook time left this month. Buy more hours."; "The walkie hasn't connected since Saturday 21:10. Check it's charged and switched on."; "Audio was cutting up yesterday evening, about 1 minute in 10. Check the Wi-Fi near him."; "OpenAI credits: about USD 4 left, about 5 days at this pace. Top up."; "The Orange Pi stopped checking in 20 min ago."). Everything healthy folds into one quiet line ("Walkie online, Wi-Fi good, server up 12 days, latest version"). One small "how he uses it" block: this week's days used, conversations a day, hours of books, music and radio, calls. No event lists, no charts unless one says something a sentence can't.
- Alerts: the same "needs you" items, pushed once when they appear and once when they clear, never repeated, quiet at night.
- Where it comes from: a small watch module in the server turns its own state and logs into those sentences every few minutes (rules with thresholds, each rule tested); the Pi checks in with an outside watcher (a dead-man's switch such as healthchecks.io) that alerts if it goes silent; credits are spend (from the server's own cost lines) against the balance Mario enters after each top-up.

**Design v1** (2026-10-12, after T1.43's two critics; full text in [`remote-operation.md`](./remote-operation.md) W6):
- Proposal v0's page stays as is, plus one plain archive one click away (day, then conversation, then audio and transcript, calls included): Mario's other brief asked to "see each call and hear its recording" and to know "exactly when it's being used". The archive is where that lives, never the front page.
- `huxley-watch` is its own process on the production box, not a module inside the voice server: it must keep alerting when the server is down or crash-looping, and it can never glitch audio. It reads the logs (kept by date, T1.43 W1), the recordings (T1.43 W3) and the server's `/health`; it builds a disposable cache, so no new database and no writes in the server. It pings the outside dead-man check.
- Tailscale only (Mario's Mac and iPhones are on the tailnet); it never opens the voice WebSocket (one client at a time would evict the walkie).
- Rules: offline > 15 min in the day or not seen for days; audio cutting up; battery < 20 % (only once F-0025's wiring exists: shown as unavailable until then); crash, rollback, update probation failure; server restart loop; OpenAI connect or quota failures; spend against the entered balance; audiobook hours low (`book:seconds:YYYY-MM` against `book_hours_per_month`, `spotify.start_refused cause=book_no_hours`, `spotify.book_hours_out`); Telegram or Soloist logged out; disk > 80 %; Access token or 4G SIM near expiry. Each sent once on appearing and once on clearing, quiet at night. T1.41's waiting cue never counts as an underrun.
- Open (Mario): the alert channel (Telegram bot recommended; ntfy or email) and whether Tailscale-only is fine.

**v0 built** (2026-10-12, branch `t142-watch`): [`server/watch/`](../server/watch/README.md), its own uv package and process (`uv run --package huxley-watch huxley-watch`, `--once` for cron and tests). Reads the logs (archives, `keep/`, each line once, line by line, damaged lines skipped and counted), `logs/bench.jsonl`, the persona DB read-only and the server's instance lock; writes only its own state dir.
- The page: Mario rejected the first, sentence-based page ("numbers in a paragraph is bad UX") and approved a visual mockup, which v0 follows. No sentences: a status dot and "All good" / "N need you" ("Testing until HH:MM" inside a test window, "Stopped checking" when stale, also on the page `--once` writes); one card per open item (big number, label, action chip); six tiles (walkie, Wi-Fi, audio gaps, server, battery not wired, OpenAI); today hour by hour (walkie connected, him and Huxley, music/books/radio/calls, bench and test windows hatched, now marker); the last 7 days (stacked hours, presses, "no logs"); the month's audiobook gauge; audio gaps per half hour on a continuous 12 h axis with the threshold; calls per person. Rendered only from the dict `/status.json` serves. CSS charts, light and dark, phone width; checked by screenshot on real logs.
- Rules (pure, each tested): walkie offline, audio gaps (`dev.py link`'s arithmetic, equal on real logs), server down (instance lock pid or the log; a persona swap is not a stop), restart loop, unclean start, skill setup failures, OpenAI unreachable / quota / spend today / credit left, audiobook hours, Spotify and Telegram down, disk, logs unreadable, alerts failing. Hysteresis on gaps, credit, books and disk; an open item never "clears" because a window or another item hides it.
- Alerts: Telegram or the log, once on appearing and once on clearing, held 22:00-07:00, "came and went", chunked under 4096 characters, retried, failures counted into a card, state across restarts; errors logged by type only (tokens). The healthchecks.io ping.
- Bench windows (the firmware tools' `bench.jsonl`): "link test" silences offline and gap alerts; bench runs also leave their activity out of use; capped at 6 h. Only the walkie's presses count, and activity only while it was connected (25 of 449 real presses were the phone app's).
- Two critics (fresh agents), every finding fixed with a regression test that failed before: swaps read as stops, re-announced streams drawn as seconds (today's radio 4.3 min against 35.6 real: now 35.6), false clears, whole-file reads, secrets in error text, stale "All good", unchunked alerts, and more. 156 tests.
- Not in: the archive (the recorder is in main; next); battery (F-0025); rollback and probation (W4); `/health` (W1): a server that hangs without exiting looks quiet, not down; Access token and SIM expiry. What the server doesn't log: [observability.md](./observability.md#what-huxley-watch-reads).
- Next: install on the box beside the server unit (a systemd unit, the secrets file, the healthchecks.io check), Mario's Telegram bot, a week of real alerts to tune the thresholds.

**Archive built** (2026-10-12, branch `t142-archive`; [README](../server/watch/README.md#the-archive)): Mario's "see each call and hear its recording", from the day strip. A day picker above the strip (any day the logs cover); a tap on a block opens the day's list under it, scrolled to that time: one list in time order, read straight from that day's logs, of what was said (`transcript`), the tools called (name, short arguments), the recordings (play rows: mic, model, a call's two legs) and what played (a row per stream and call). Bench rows fold into one row; a marker where 10 minutes passed. `/audio/...` serves the recorder's Ogg files read-only (traversal and symlinks out refused, byte ranges); `/day/YYYY-MM-DD.json` is built from that day's log files only (byte-level skipping, what was open at midnight carried from the run's start, each file's day start kept so today is not rescanned, past days cached). A join of transcripts to recordings by time was built first and dropped at Mario's word ("it should just be the text with the timestamp and then the tool calls"); the critic then found it would mispair: a transcript came 19 s after its press in the 2026-10-12 log. Found on the way: `model_speaking` is raised by streams, so the "Huxley" lane drew songs as Huxley talking (164 min today); answers now start at `coord.audio_start`. Checked in a browser on the real logs (a Him block, a call, a past day, bench folded, phone width, a recording played muted).

**OpenAI budget card** (2026-10-12, branch `t142-openai-credit`; [README](../server/watch/README.md#openai-budget)): Mario's "how much credit is left and how much has been used, with a graph, like a battery". He won't type numbers in, so the battery is the month's budget left: the monthly hard spend limit (OpenAI added them 2026-07; Mario set $30) minus the spend since the 1st, UTC. Spend is OpenAI's billed amount from the Admin API's Costs route with an admin key (hourly, cached, never the key in a log or page), else the server's `cost.response_done` estimate, each day marked. The limit is read from `GET /v1/organization/spend_limit` (and a project's), else `[openai] monthly_limit_usd`. The card: budget left, used, the 7-day pace (days with spend), "hits $0" or "left at reset"; the battery line with the projection dashed to the reset or to $0; daily spend bars; the chip is the alert. New items: "Credit gone" (`credit_balance_exhausted` joins `insufficient_quota`), "Budget hit" (the `*_spend_limit_exceeded` codes), "Budget runs out" (the projection). The entered balance (`balance_usd`) stays only as a fallback without a limit; top-ups were not built (nothing typed by hand). A failed or 404 read falls back to the estimate and the config with one log line. A critic (fresh agent) found a project-limit failure discarding the org limit, the key riding redirects (urllib copies headers), naive cached times crashing every cycle, the config limit counted against a project, today's first minutes lowering the pace, billing taken as final at midnight: all fixed with tests that failed before (03d822b5). Rendered on the real logs: $27.25 of $30 left ("or less": no logs before 10 Oct). 277 watch tests.

**Battery chart and "battery empty"** (2026-10-12; 3c262f29, a3f61e2b, 03138521): Mario's "I can't see the battery history, I can just see the percentage". A full-width "Battery, last 24 h" card: the walkie's own readings, one point per 5 minutes, the 20 % line dashed, the latest reading labelled, a tip per half hour with % and volts. The first cut broke the line on 15 minutes without a reading and drew the evening as loose specks: the firmware reads every 20 minutes (F-0025), so the line now breaks only where the connection spans show the walkie offline for over 15 minutes. With the firmware's cutoff (F-0030, a8e8ef5b) a last reading carries `cutoff: true` and the walkie parks offline until charged: one critical card, "Battery empty", in place of "Walkie offline", closed when it connects. Lesson: a reading's absence is not a state; join it to the signal that is (the connection). Critic (fresh agent, 2026-10-12), all six taken: at a cutoff "battery low" closed (a false "charged again") and reopened as "plug it in" while on the charger, so low is now held open under "empty" until a real charge; an old cutoff hid a later outage, so "empty" needs the cutoff reading inside the last connection; a restart-closed span missed a break (overlap, not containment); the right-edge reading lost its tip; and in the deploy scripts a plain busy deploy killed a waiting one, and a failed fetch or sync still restarted the server on old code (now: replace via a pid file, `flock`, fail before restarting).

## T1.43: Running the walkie from afar (grandpa's retirement home)

**Status**: `in_progress` (2026-10-12; Mario answered: shared Orange Pi, password Wi-Fi, the case opens once more, push after a scan) · **Owner**: the "Huxley improvement plan" session (server, ops, observability); firmware items built by the firmware session · **Effort**: L (weeks; phased)

**Ask** (Mario, 2026-10-12): the walkie goes to grandpa at his retirement home, on its Wi-Fi, far from Mario's house. From home: update firmware, record every voice in and out with transcripts, see everything the speaker played (book, music, call, silence), a dashboard on the Orange Pi (hours of Spotify, calls and to whom, each call's recording, battery), keep developing while grandpa uses it, and "what may I be missing". Grandpa agreed to full recording.

**Validation (Gate 1)**, measured today, every item a break at the retirement home (full table in [`remote-operation.md`](./remote-operation.md#where-things-stand-measured-2026-10-12)):
- The server runs on the Mac (pid 94414, port 8765, the walkie at 192.168.68.69 connected to it); the Orange Pi runs no Huxley. The Pi reset 3 times in 2 days (journal boots -3..-1 end with no shutdown): power cuts (Mario: heavy rain). 75-81 °C, no fan, 31 containers under Watchtower, 273 MB free.
- The walkie dials `ws://192.168.68.71:8765/` from `secrets.h`; the socket has no auth and carries `install_skill`, `restart_server`, `set_skill_secret`, `?persona=`, `list_sessions`; a second client evicts the walkie, which then waits for a press.
- Updates and the remote console need the board's port-80 door and the Mac's LAN IP; an image is kept only after `dev.py` confirms it.
- One Wi-Fi network compiled in; open networks refused; reconnect every 2 s forever; the task watchdog only logs; the rocker is unreachable; F-0020's download-mode fix is software polling.
- Battery not wired (F-0025); no charging detection; never drained and recharged.
- No audio recorded; logs pruned to 10 runs at each start; DB snapshots only at app start, same disk; persona starts on the first connection; `main` 31 commits ahead of `origin`.

**Design (Gate 2)**: [`remote-operation.md`](./remote-operation.md): ten decisions, workstreams W1-W8, the order. In short: production stays at Mario's house on a UPS (dedicated box recommended); a Cloudflare Tunnel over `wss://` with an Access token and a device token, production granting by token, admin off; dev and prod never share an account; updates pulled through the server link, kept only after proving the next update works; voices recorded as the Opus that crossed the wire, everything else a timeline in the logs, no new database; T1.42 as the front page in its own process; everything needing the cable or the open case done in one session before handover; a 7-day dress rehearsal on a foreign network.

**Critic notes (Gate 2)**: two fresh critics, server and firmware, 2026-10-12. Blockers taken: address-based admin lockdown would give the tunnel admin (cloudflared shares the network namespace) -> grant by token; dev and prod sharing Telegram, Spotify, OpenAI and the persona DB; power and clock first; the confirm proving the next update's path; a rollback image that can't come back (protocol mismatch parks it, NVS wiped across format changes); resets that can still boot download mode (remove R15, RST pinhole, charge-only adapter); battery behaviour at cutoff. Cut on the critics' advice: an analytics database, the recovery partition, secure boot and flash encryption, captive-portal automation, the server-sent hash. Full list in the doc's "Critic notes".

**Definition of Done** (locked per phase when Mario answers; the whole plan's exit is phase 6):
- Phase 0: UPS with clean shutdown on the box, router and modem; dead-man check alerting Mario's phone; code pushed after the private-data scan; production box and dev accounts decided.
- Phase 1: production on the box, persona at boot, `/health`, logs by date, recorder writing Ogg/Opus per press, answer and call leg with `recorder.segment` lines, offline alerts.
- Phase 2: the case-open list done once (battery, VBUS, R15, partition table, flash dump, efuse summary; the pinhole was dropped), drain-and-recharge test passed.
- Phase 3: the twin board on `wss://` through the tunnel with both tokens; production refuses everything but the device role.
- Phase 4: two updates and one forced rollback on the twin board through the server link only; the ladder, watchdog supervisor and black box tested on the bench.
- Phase 5: T1.42's page, archive and every rule, each rule with a test.
- Phase 6: 7 days of grandpa's exact build on a foreign network with no manual touch, the plug pulled mid-book, home internet cut, cloudflared restarted mid-answer and mid-call, the restore drill under an hour, every alert received.

**Firmware items** (the firmware session files them as F-entries when Mario says go): W4 and W5 of the doc, plus W7's partition table and tests.

**Progress**: logs kept (5721956f); device contract schema 1 in protocol.md (106b408a); the Pi has the code, a venv (runtime 775/775 there) and a verified-but-uninstalled unit (62b42aa8); main pushed to origin after a private-data scan (Mario OK'd the retirement-home wording); recorder (W3) merged (6376cdbf; abuelos records since c9689f84; end to end checked: mic and model Ogg files decode at the logged durations); a reminder or call parking a stream now announces it (5c4a775d); bench and planned test runs marked in logs/bench.jsonl (36 backfilled); firmware F-0026..F-0028 filed by the firmware session. huxley-watch v0 merged (0c521266): Mario rejected the sentence page and approved the visual one (status, tiles, day strip, 7-day bars, audiobook gauge, gap bars, calls), two critic rounds, 156 tests; running on the Mac at 127.0.0.1:8780 until the Pi cutover. Persona at boot (`HUXLEY_PERSONA_AT_BOOT`, off by default) and `GET /health` v1 (loopback and Tailscale, 404 otherwise; no per-skill probes yet) on branch `t143-boot-health`; follow-ups before relying on it in T1.44. Archive merged (3554a495: the day list, play buttons). Battery tile and alert (93795427). Audiobook gauge from the billing day (66e653d0). **Production moved to the Pi 2026-10-12 21:03Z** (walkie offline 107 s; /health ok, recording on the Pi; page at http://100.75.152.70:8780). W4's server half (firmware images behind the device token, the admin routes `/admin/firmware`, `/admin/firmware-offer`, `/admin/dev-cmd`, the accept flow with its 1012 reconnect, the rollback guard) on branch `t143-ota-server`, with W2's production role on the voice socket (device token or local, the device's message filter, no eviction of the device). T1.44 merged and deployed with persona at boot (66e7f5f6; smoke: offline-at-due PASS, across-restart PASS, cut-mid-narration repeated: fix in flight). Production role + OTA server half (e4dea36d) and the dead-peer bound (ad6f87d1: a switched-off walkie took 15 min 56 s to notice, now 30 s) deployed 22:06Z; **walkie.ma-r-s.com live 22:07Z** behind the tunnel, proven from outside (401 without token, token passes, /health and /admin 404). **2026-10-12 22:22Z: the walkie runs on wss://walkie.ma-r-s.com (source external, token valid; TLS connect 1.85 s, heap min 81 KB) and remote firmware updates work end to end through the tunnel** (firmware 4827d527 pulled, self-tested, accepted in 43 s; three pulls, one rollback). No-repeat-after-drop rule (e60305ca) and offer withdraw (fe03044d) deployed 22:23Z (933e85c); valid-on-accepted (64104add) deploying when idle.

---

**Left** (2026-10-12, the wrap-up list Mario asked for; check off with the commit):
- In flight: the archive (day list: transcripts, tool calls, play buttons; T1.42); proactive turns wait for the walkie (T1.44); remote updates, server half (/firmware, /admin, the accept flow) + firmware half (F-0028).
- Mario's calls (2026-10-12): billing day is the 14th (audiobook gauge: 15 h/month add-on, renews the 14th); UPS **frozen** (money; power cuts still take the walkie down meanwhile); healthchecks.io **frozen** (the Pi dies with the power, so it would only report what Mario already knows); Telegram bot **dropped** (Mario checks the page instead; alerts stay on the page, the delivery channel is the page itself); the Pi move needs nothing from him (user units with lingering, no sudo). Still his: reserve 192.168.68.75 in the router (DHCP), until the tunnel hostname replaces the IP.
- Ours, no input: (6) reach over the internet: Cloudflare tunnel + Access token + device token check, production role (W2); (7) remote updates finished both sides + the W7 open-case flash layout (firmware session); (8) the 7-day dress rehearsal on a foreign network (phase 6); later: nightly call transcription, turn ids on transcript lines.
- Power cut, 2026-10-13 17:05Z to 17:19Z (Mario's house; UPS still frozen): the walkie lost its server for 15 minutes. On boot, the server, the page, the tunnel and Linger all came back unattended, and the walkie reconnected 80 s after boot. Soloist didn't: a container restart keeps its files, the killed PulseAudio's pid file pointed at a recycled pid, and the container looped on "Daemon startup failed" (Spotify down). Fixed by clearing `$XDG_RUNTIME_DIR/pulse` in `server/skills/spotify/dev/entrypoint.sh`, reproduced and verified on the Pi with `docker kill` and `docker start`. The firmware session found the power had been flickering since 14:43Z (boots ended near 15:53Z, 17:01Z and 17:05:57Z), so Spotify was down 2 h 40 min: Mario hit "not available" at 17:05Z. The page caught it ("Soloist down" at 14:54Z, an unclean restart per cut), but nothing reached Mario: it has no push channel since the Telegram bot was dropped. Page fixes (no push needed): "Soloist down" no longer closes at each restart ("working again" while dead, then reopened 10 min later), and the server tile shows today's restarts and how many were unclean. Push channel: Mario chose ntfy (2026-10-13; free, open source, no account, no ads found; he had dropped the Telegram bot as something he'd never check, and a lock-screen push is not that). The page posts each "Needs you" and "Fixed" to a secret topic; quiet hours still hold everything until morning.
- Handover day, on site (Mario, 2026-10-13): the Telegram skill's account is still a family member's test number; it must become his own number before handover (log the Pyrogram session in again with his number, SMS code on his phone, and check his contacts list in the gitignored `data/` still matches). Also on site: his home's Wi-Fi added over USB (`clients/firmware/tools/wifi.py add`), and someone there named who charges it.
- **Dead-Pi alert** (2026-10-13; `scripts/pi/deadman-worker/`, 41b7fb96): a Cloudflare Worker (`huxley-walkie-deadman`, cron every 5 min, KV `huxley-deadman`) checks https://walkie.ma-r-s.com answers 401 (the server's own "token required") and pushes "needs you" after two failed checks, "fixed" when it's back, held in quiet hours. Checks work in production (healthy, nothing sent). **Delivery blocked**: ntfy.sh refuses pushes from Workers with `429 42908 daily message quota reached` (anonymous limits are per IP, and Workers share Cloudflare's egress IPs); a free ntfy account doesn't help (per-account limits only on paid tiers). Options for Mario: ntfy Supporter (about $6/month, also lifts limits for the page), or another channel. Until then it keeps checking and logs `push_error`.
- Hardening, ours (from the production-role critic): on production "local" means trusted by address, so a host-network container on the shared Pi (the tunnel connector itself is one, though it adds proxy headers and is treated as external) could reach the voice socket with full rights. Close it with an admin secret for the local role (header or query), Tailscale-only admin routes already need X-Huxley-Admin. **Admin half done (t143-admin-token, 2026-10-13):** validated on the Pi first: the server listens on 0.0.0.0:8765; the home Wi-Fi is refused without the device token (source `other`), but loopback and Tailscale get `local` with no secret, and three containers share the host network (Home Assistant, sticky-home, the tunnel; the tunnel adds proxy headers so it counts as external). `/admin/dev-cmd` drives the walkie's console: reboot, `battery park`, `battery cutoff off`, `server ...` (its server address) and `ota` (Wi-Fi changes are USB-console only, per the firmware session), so anything compromised on the box could point the walkie elsewhere and cut grandpa off until a visit. With `HUXLEY_ADMIN_TOKEN` set, `X-Huxley-Admin` must carry it (constant time, never logged); `scripts/pi/admin.sh` reads it from the server's `.env` and pipes it to curl. Critic (fresh agent): no bypass found; taken: production without an admin token now refuses (503) instead of falling back to "any value", a startup warning for a missing, short, non-ASCII or spaced token, admin.sh reads the .env with the server's own parser and refuses anything but `key=value` (curl reads a file for `name@file`), and a tokenless local `?evict=1` could still knock the walkie off again and again (it stays off until its next press), so eviction needs the admin token too (header; a browser can't send one, so the PWA never evicts, which it never did on its own). Left open on purpose: the voice socket's `local` role (talk to the persona, which can call and message family) stays tokenless, because Mario chose no app tokens for the iPhone and PWA over Tailscale; closing it would reverse that call. ~~Also: huxley-watch ignores SIGTERM while sleeping between cycles (a restart waits 90 s for systemd's kill); make the loop wake on the signal.~~ Done 2b45a185, and the guess was wrong: reproduced on the Pi, Python's wait does wake on SIGTERM; the signal never reached it. This user manager can't signal the whole control group, so only the main process (`uv run`) got SIGTERM, and in a restart it didn't pass it on. The unit now starts the venv's entry point; a restart takes 0.3 s.
- **When OpenAI won't serve, say so** (2026-10-13, branch `t143-unavailable`, 82870404 + critic fixes in the next commit). Problem: two silent failures for grandpa. A press with no session played only the double beep, whatever the cause (no credit, the monthly cap, an outage); and a turn OpenAI refused with a billing code was only logged (`session.rx.error`), so he heard silence after speaking. Built: a persona key `unavailable_sound` (abuelos: `sounds/unavailable.wav`, rendered separately; until it lands, the beep and `app.unavailable_sound_invalid reason=missing`). A press with no session still beeps while held; its release plays the clip once the session has been down 60 s or OpenAI refused the account (`app.unavailable_played reason=down_long|refused:<code>`), so a planned refresh only beeps. The refusal mark clears only when a response completes (`app.refusal_cleared`), not on a connect. A refused question ends with the clip (`session.response_refused`, `coord.response_refused`) and the next press works; a refused reminder or timer says nothing and waits at the head of the queue, its attempt not counted. Found on the way: a refused handshake's exception says only `HTTP 429` (the code is in the response body), so a refusal at connect was invisible both to this and to huxley-watch's `connection_failed` text rule; the provider now names it (`OpenAI refused the session: <code>`, plus `connection_failed refused=<code>`). Realtime may also report a refused answer as a failed `response.done` instead of an `error`: both are handled here, but the watch reads only `session.rx.error code=` (watch side done in the next commit: `session.response_refused` and `connection_failed refused=` now feed openai.quota and openai.limit). Critic (fresh agent) changed the design in four places: the clip played during the press, which the walkie talks over (now at release); refused proactive turns spoke "unavailable" unprompted (a timer at 3 a.m.) and every later inject refired them with the "say it again" note (now silent, attempt given back); any connect cleared the refusal mark, though OpenAI can accept a session and refuse each answer (now a completed reply clears it); a symlinked `data/` broke the clip path (now resolved against the persona dir). Also taken: a log when the clip is skipped for a press, `response_cancelled` set, exception messages searched instead of the whole traceback. Before the critic I had also found a press during the refusal handling could lose its new turn (now detached synchronously, two regression tests). Not checked against the live API (that needs an account with no credit). 39 new tests.


## T1.44: Before the first client: proactive turns and the provider connect

**Status**: `in_progress` (2026-10-12): implemented on branch `t144-inject-waits`, two critic rounds, awaiting Mario's walkie smoke and the merge (see Ship); found by the critic of persona at boot, T1.43 W1 · **Effort**: M

**Problem.** With `HUXLEY_PERSONA_AT_BOOT=true` the persona runs before the walkie reconnects, which surfaces two gaps that lazy boot hid:

- **Proactive turns don't wait for a client.** Only content streams start held while nobody is connected (`TurnCoordinator._client_absent`); `inject_turn` goes ahead. A reminder still inside its late window (reminders skill `_DEFAULT_LATE_WINDOWS`: medication 15 min, generic 1 h, appointment 2 h) fires at setup and is spoken to nobody. Generic and appointment reminders never re-fire; medication ones lean on the 5/10/30 min retries. Lazy boot delivered them with the walkie connected. The same already happens whenever the walkie drops while the persona runs.
- **A provider connect that fails outside a session end is never retried.** `_enter_connecting` drops to IDLE and only `_on_session_end` schedules `_auto_reconnect`, so a failed connect at boot waits for a client's `wake_word` (the walkie sends one per connection), and `/health` says `ok: false` meanwhile. On a connected walkie the same failure leaves presses beeping until it reconnects.

**Why it matters.** Production should set the flag (T1.43 W1). A reminder spoken to nobody is a missed pill or appointment for a blind user, and the deploy script will judge a restart by `/health`.

**Solution sketch.** (1) A framework mechanism, not a reminders tweak: proactive turns wait while no client is present and are delivered when one is back, with the skill keeping the say on staleness (a deadline the coordinator honours, or a "nobody heard it" signal so the skill re-fires). Needs design and a critic. (2) Run the existing reconnect loop when a connect fails at boot or on `wake_word` (its no-signal beep goes nowhere without a client), or retry from `Runtime._boot_persona`.

**Validation.** Code citations above; the critic's scratch probe of 2026-10-12 (reminder fired at setup with no client connected).

### Design (Gate 2)

Walkie numbers from the firmware owner (measured): a server restart reconnects in about 3 s, a long outage backs off to 45-60 s between tries (a new IP retries fast), and the firmware reboots after 60 min without a server. Most of the time the walkie isn't connected it is reconnecting. So a few minutes of waiting covers a restart or a Wi-Fi blip; anything longer means the walkie is really away, and each skill's own expiry decides. The live server's `/health` says `ok: false` for about 3.5 s after a start, until the walkie's hello: normal, not a fault.

**The rule.** An injected turn is spoken only when someone can hear it. It waits while there is no client, no voice session, a turn in progress, a live claim (except PREEMPT), or an earlier injected turn still playing on the client. When the blocker clears it is delivered, in order, unless its expiry passed first. Delivered means the client played it to the end and was still connected afterwards (or the user pressed into it). A turn cut off by a disconnect, a replaced connection or a session drop goes back to the head of the queue and is said whole later.

**Skill surface (SDK, backward compatible).** One optional parameter and one return value:

- `inject_turn(prompt, *, dedup_key=None, priority=NORMAL, expires_after: timedelta | None = None) -> None`
- `inject_turn_and_wait(prompt, *, dedup_key=None, priority=NORMAL, expires_after=None) -> InjectOutcome` (gains `priority` and `expires_after`; returned None before)
- `InjectOutcome`: `DELIVERED`, `EXPIRED` (still waiting when `expires_after` ran out), `DROPPED` (replaced by a same-key turn, a same-key turn already being said, or the persona shut down), and `QUEUED`, only for a call from inside a tool handler of the turn in progress: waiting there would deadlock (the turn can't end until the handler returns), so it queues and returns at once, as the old fallback did.

`expires_after` counts from the call and only applies while the turn waits; a turn being said is never cut for it, and a turn cut off after its deadline is EXPIRED, not said again. `None` (the default) waits until delivered or the process ends: today's queue never expired, and the product rule leaves staleness to the skill. `inject_turn_and_wait` now waits for the outcome in every case (busy, no session, no client) instead of falling back to a plain enqueue; its 10 s session wait goes away.

**Where it waits.** `TurnCoordinator._injected_queue`, as today. `_InjectedRequest` gains `expires_at` (monotonic), `queued_at`, an outcome future and an expiry timer that resolves EXPIRED at the deadline (so a waiting skill learns at once, not when the client returns).

**How presence gates it.** The coordinator keeps `_client_present`, set at once by `on_client_presence` (content keeps its 5 s grace through `_client_absent`; a turn has no stop/start cost to ride out). Its first value is `AudioServer.has_client`, so a persona started at boot begins absent. The server also reports a client replaced by a new connection as gone (presence False, then True after the new handshake): a Wi-Fi drop the server hasn't noticed yet ends with the walkie reconnecting and evicting the dead socket, and what was being said to that socket was lost.

**When it drains.** At turn end, as today, plus `_drain_waiting(trigger)` when the client comes back, the session connects (exists), the previous turn's playback is confirmed, or a claim ends (scheduled with `create_task`: a claim's end runs inside the FocusManager). Drain: idle, session up, client present, nothing unconfirmed: fire the head; with a live claim, only the first PREEMPT. On a client's return the drain runs before the no-client content hold is released, so the narration's DIALOG parks the content first (the order `on_session_connected` already uses).

**Confirming delivery.** When an injected turn ends, its request becomes `_unconfirmed`. A task waits until the client's playhead has passed (`_client_playhead`, the estimate content holds already use, capped at 120 s), then pings the client (a WebSocket ping: the pong proves it received everything sent before it; websockets resolves a pong for any earlier ping and fails pending pings with `ConnectionClosed` when the socket closes, and its keepalive closes a dead socket in 20-40 s). Pong: DELIVERED. Socket closed, client gone or replaced (a presence epoch that changed): back to the head of the queue. A pong that never comes within 60 s (the socket is alive by keepalive, yet unanswered) counts as delivered with a warning, since saying it again would repeat it. While a turn is unconfirmed the next waiting turn doesn't fire: deliveries are serialized, so a drop or a press during turn A can't swallow turn B queued behind it in the client's buffer. A press while unconfirmed confirms it (the user is there). `inject_turn_and_wait` returns at confirmation, which replaces its old drain sleep (`_wait_for_client_playback_drain` and its byte counters go).

**A disconnect mid-delivery.** Client gone while an injected turn is being said: cancel the response, release DIALOG, end the turn (the `current_turn` setter disarms the WaitCue), put the request back at the head. Session drop mid-turn: the same; `on_session_disconnected` aborted the turn and lost the request until now (the planned 50-min refresh during a reminder lost it). A press interrupting an injected turn stays DELIVERED: the user heard it start and chose to talk.

**Order and dedup.** FIFO by first arrival; a turn put back goes to the head. A queued turn with the same `dedup_key` is replaced in place, keeping its place (a reminder's retry stays ahead of a later message), and the replaced one resolves DROPPED. A same key being said or awaiting confirmation drops the newcomer (today's rule; the confirmation window counts as being said). The priority branches at turn end are unchanged.

**The earcon.** None exists today: proactive earcons were deferred at T1.2 (sound sourcing) and never shipped, so a turn delivered late sounds like one delivered on time: the model's voice. Not added here: which sound is a taste call, so it is a follow-up, not a T1.44 dependency.

**Persistence.** Memory only, unchanged: a restart loses waiting turns. Skills that persist re-fire from their own storage at setup. After this change that holds for every reminder kind: non-medication rows stay `fired` until DELIVERED, and the boot reconcile re-fires a `fired` non-medication row inside its late window instead of closing it as acked (a crash in the second between delivery and the save could repeat it; generic and appointment only). Medication already resumed its retries from `fired`. Timers: no (the `fired_at` guard against double narration skips a fired timer on restore; timers last at most an hour; documented). Telegram: the unread backfill on connect re-announces unread messages (6 h).

**Skills.**

- reminders: each fire goes through `inject_turn_and_wait` in its own supervised task (the scheduler loop must not block), with `dedup_key=f"reminder:{id}"` (a retry replaces a waiting fire), `priority=BLOCK_BEHIND_COMMS` and `expires_after = due + late_window[kind] - now`, `due` being this fire's own due time (a retry's too, as the boot reconcile measures it). DELIVERED: non-medication becomes `acked`. EXPIRED: non-medication becomes `missed` (prompt_context brings it up in the next conversation: the audible trail); medication is logged and its retries go on (its last fire already marks it missed). DROPPED: nothing (a retry took over, or shutdown).
- telegram: message bursts and the unread backfill wait with no expiry ("while unread": the skill never marks messages read, so an announcement stays worth saying until heard) and bursts drop their `dedup_key` (while waiting, a second burst under the same key would replace the first one unheard; the InboxBuffer already prevents double flushes). The inbound call announcement waits at most 30 s (the call is already accepted and the caller hears silence); anything but DELIVERED hangs up and queues a "missed call" turn with no expiry.
- timers: `expires_after` = the skill's stale threshold (1 h default, the tolerance it already applies to a timer delayed by a restart), `dedup_key=f"timer:{id}"` so the coordinator's log lines name the timer. No outcome handling needed.

**Logs.** `coord.inject_turn_queued` gains reasons `no_client` and `awaiting_playback` and `expires_in_s`; new `coord.inject_turn_expired` (dedup_key, priority, waited_s), `coord.inject_turn_cut_off` (dedup_key, reason `client_left`/`session_down`, requeued), `coord.inject_turn_delivered` (dedup_key, waited_s, by `pong`/`press`/`pong_timeout`), `coord.inject_turn_undelivered` (dedup_key, reason, requeued); `coord.inject_turn_dequeued` gains `trigger`; `coord.inject_turn_dropped` gains reasons `replaced` and `shutdown`; `coord.inject_and_wait_in_tool` (warning, the QUEUED guard). Server: `server.client_ping` (ok, rtt_ms or reason). App: `app.reconnect_scheduled` (trigger `connect_failed`/`session_end`) and `app.reconnect_already_running`.

**WaitCue (T1.41).** Injected turns never arm it: arming stays limited to `TurnSource.USER` at commit and tool call. Every disarm point stays; the new cut-off path ends the turn through the `current_turn` setter, which disarms. `test_coordinator_wait_cue.py` runs unchanged.

**Part 2: the failed connect.** `_enter_connecting`'s failure path schedules `_auto_reconnect` (the loop in `huxley/reconnect.py`) unless shutting down or a loop already runs (an attempt from inside the loop fails into the same path and finds itself running). A walkie's `wake_word` still triggers an immediate attempt; the loop stops once the state leaves IDLE. Its no-signal beep goes nowhere without a client (dropped and counted, as today).

**Wire.** No protocol message changes. The ping is an RFC 6455 control frame both clients already answer (browsers by spec; the walkie's esp_websocket_client answers the server's keepalive pings today).

**Revised after the critic (these override the paragraphs above where they differ):**

- **Presence is read live, not cached.** The server owns a listener epoch: a number per connection, reported only once that connection's hello is out (`AudioServer.listener_epoch`, None otherwise). The coordinator reads it whenever it gates, fires or confirms; `on_client_presence` stays as the trigger for cutting off and draining. The confirming ping goes to the epoch the turn was said to (`client_received_all(epoch)`): a pong from a newer connection proves nothing about audio sent to the old one. After a failed confirmation nothing fires again until the epoch changes.
- **No pong is not delivered.** The whole ping (including the write, which waits on a full send buffer) is bounded at 15 s (the walkie's own send timeout is 5 s); on timeout the server aborts the connection, which reports the client gone, and the turn goes back to the queue. An eviction's close of the old socket is bounded at 2 s, then aborted.
- **A press counts as heard only if the turn's audio had started playing** (its first audio's place on the client's playhead) or the turn ran a tool; otherwise it goes back to the head of the queue and follows the user's turn. Only `on_ptt_start` resolves DELIVERED; `interrupt()` (reset, shutdown) puts a live injected turn back instead.
- **Default expiry: 10 minutes.** `expires_after` defaults to `DEFAULT_INJECT_EXPIRY` (10 min, exported by the SDK); `None` waits until heard. Ten minutes covers a 3 s restart and the walkie's 45-60 s backoff many times over (the firmware owner: "a wait of a few minutes covers a restart or a Wi-Fi blip"), and a notice no skill thought about (a battery warning, a Spotify notice) is not read out hours later. Reminders, timers and Telegram pass their own.
- **No await between the gate and the turn.** `_fire` re-checks every condition synchronously just before creating the turn (a press during an awaited log line used to be overwritten); a failed check puts the request back at the head. The queue also drains after a cut-off and after a failed confirmation (guarded by the epoch rule).
- **Said again, with a note.** A re-delivered turn carries a persona-translatable note (`inject_repeat_note`: the user did not hear it, say it again in full), so the model doesn't answer "as I said".
- **Cancelling `inject_turn_and_wait` withdraws a turn still waiting** (DROPPED, `coord.inject_turn_withdrawn`); one already being said finishes.
- **The deadlock guard is by task**, not a context flag copied into every task a handler spawns: `inject_turn_and_wait` returns QUEUED only when awaited in the very task running a tool handler, or a skill's `setup()` / `reconfigure()` (waiting in setup at boot would hold the swap lock the walkie's connection waits on).
- **Medication retries count from the hearing.** Found while checking the retry chain: the scheduler only fires `pending` rows and a medication fire leaves the row `fired`, so the 5/10 min retries never fired in a running process (only after a restart's reconcile). Now a fire waits for its outcome: DELIVERED schedules the next retry from that moment (`pending`), EXPIRED marks the dose missed (a retry would be past its window too), DROPPED leaves it `fired` for the next boot. No fire stacks on a waiting one.
- **Telegram:** a burst's flush awaits its outcome, so while one announcement for a sender waits, that sender's later messages gather in the inbox buffer and follow as one more announcement when it is heard (at most two per sender, no dedup key needed). The call announcement and the missed-call turn are `BLOCK_BEHIND_COMMS` (with NORMAL a stream started by the turn in progress would win).
- **Timers:** expiry = the timer's own duration, at least 10 min, at most the stale threshold (1 h).
- **`/health`** gains `injects_waiting` and `oldest_inject_wait_s`, for huxley-watch.
- `close()` counts an unconfirmed turn whose playback had ended as delivered (a reload must not repeat it), anything else DROPPED. The client playhead resets when a client leaves.

### Critic notes (Gate 2)

One fresh critic, 2026-10-12, read the design, the code and the brief. Findings and responses:

- **B1, "no pong in 60 s = delivered" is backwards; `ping()` itself can hang** on a full send buffer (websockets waits on `drain()` with no limit, so the keepalive's timeout never starts); eviction's close can hang the reconnecting walkie's handshake. _Taken_: bounded ping, timeout = abort + not delivered; eviction close bounded then aborted. `TCP_USER_TIMEOUT` and a 10/10 keepalive: filed as a follow-up (transport hardening that also stops content playing into a dead socket; turns are covered by the bounded ping).
- **B2, a cached presence flag goes stale** (notifications reach only `current_app`; an app started during boot, a swap or a reload misses them), which loops paid responses into no client; `has_client` is true before the hello; the ping went to whichever connection is current. _Taken_: live listener epoch from the server, hello required, confirm by epoch, no refire on the same epoch after a failure.
- **B3, a press before the turn's audio started counted as heard** (a drained turn queues behind the previous answer, and the press empties the buffer); `interrupt()` also runs on reset and shutdown. _Taken_ as above.
- **Level-triggered drain / single dispatcher task.** _Partly_: the race it points at (an await between the gate and the turn) is fixed by the synchronous re-check in `_fire`, and the missing trigger (a cut-off finishing after the client came back) by draining after every requeue. A dispatcher task was not added: one more task to own and cancel, for no case the re-check misses.
- **The default of waiting forever is unsafe for untouched skills.** _Taken_: 10-minute default, `None` for "until heard". A queue depth cap was not added: every first-party producer is bounded (reminders by rows, Telegram at two per sender, timers by timers); depth is logged and in `/health`.
- **Re-delivery into the same conversation.** _Partly_: the repeat note instead of deleting conversation items (a provider API surface for a wording problem). No cap on attempts: after a failure nothing fires until a new connection, so attempts are bounded by the walkie's reconnects and the expiry. Waiting for a "stable" connection (first message after hello) was not added: the PWA may send nothing, and the epoch rule already stops tight loops.
- **The deadlock guard leaks** through copied context; setup and client-event handlers can deadlock too; raise instead of QUEUED. _Partly_: guard by task identity, extended to setup and reconfigure. QUEUED kept: the old fallback returned at once there, so raising would break a caller the SDK documented as merely wrong. Client-event handlers run as gathered tasks the guard can't see: documented (await nothing long there; the system skill already spawns its own task).
- **Cancel should withdraw.** _Taken._
- **Medication retries count fires, not hearings.** _Taken_, and the runtime retry bug fixed with it.
- **Telegram**: keep the burst key and merge (taken in another form: the awaited flush gathers later messages into one follow-up announcement); "while unread" isn't a real expiry and backfill repeats heard messages (true; marking messages read on DELIVERED would show read ticks to the family: **Mario's call**, not done); call and missed-call priorities (taken).
- **Which client is the listener**: Mario's PWA evicting the walkie would hear and confirm grandpa's reminder. `server.client_ping` logs the connection id and its shell. Counting only the firmware client as the listener for a device persona is **Mario's call**, not done.
- **`/health` for huxley-watch.** _Taken._
- Nits taken: playhead reset on presence loss; presence false at eviction before the close; timers' expiry by duration; `close()` by playhead; the SDK no-op returns DELIVERED. Not taken: the last medication fire being marked missed before it is said (predates T1.44, unchanged).

### Definition of Done (locked 2026-10-12)

- An injected turn with no client connected is not spoken and is spoken once a client connects (regression test that fails on the old code).
- A waiting turn whose `expires_after` passes resolves EXPIRED and is never said; the 10-minute default applies when none is given; `None` waits until heard.
- Several waiting turns are said in order, one at a time (the next after the previous is confirmed).
- `dedup_key` while waiting: replaced in place (the old one DROPPED); a same key being said or awaiting confirmation drops the newcomer.
- A client leaving or replaced mid-turn, or before playback is confirmed, re-delivers the turn whole (with the repeat note); an unanswered or timed-out ping does too; a session drop mid-turn re-delivers; a press into a playing turn counts as heard, a press before its audio started re-delivers it after the user's turn.
- Presence is read live from the server (hello'd connection, epoch); a not-yet-current app can't loop turns into no client.
- The WaitCue never arms for injects (`test_coordinator_wait_cue.py` green, plus tests through the new paths).
- A live claim when the client returns: NORMAL and BLOCK_BEHIND_COMMS wait for the claim's end, PREEMPT goes through, the claim's end drains.
- Cancelling `inject_turn_and_wait` withdraws a waiting turn; from a tool handler or setup it returns QUEUED without deadlocking.
- Part 2: a failed connect at boot starts the reconnect loop, which connects on a later attempt; no second loop while one runs.
- Reminders: late-window expiry per fire; non-medication acked on DELIVERED, missed on EXPIRED, re-fired by boot within its window if still `fired`; medication retries scheduled from DELIVERED and actually firing at runtime, missed on EXPIRED; ack, cancel and snooze withdraw a waiting fire.
- Telegram: bursts and backfill wait until heard, a sender's later messages follow as one announcement; the call announcement waits 30 s (BLOCK_BEHIND_COMMS), otherwise hangs up and queues a missed-call turn.
- Timers: expiry = duration, at least 10 min, at most the stale threshold.
- SDK backward compatible (old calls type-check; nothing bumped on PyPI). `/health` additive fields only. No protocol message changes.
- ruff, ruff format --check, mypy --strict, the touched packages' tests and the full runtime suite green.
- Docs in the same commit set: turns.md, concepts.md, skills/README.md, observability.md, skills/reminders.md, skills/telegram.md, skills/timers.md, decisions.md (ADR), triage (this entry, D7).
### Critic notes (second, on the finished diff)

A second fresh critic, 2026-10-12, reproduced each finding with a probe. All fixed in `fix(runtime): second critic round` unless noted:

- **A tap left a waiting turn with nothing to start it.** A press put a not-yet-playing turn back, then was too short (or its commit failed): no drain followed. Now a dropped press drains.
- **Content played between two waiting turns** (the next fires only after the previous is confirmed, and the dispatch hold was gone by then): a Spotify play then a pause, book time skipped. Now an `inject_queue` hold keeps it parked until the next fires or the confirmation settles.
- **A response that said nothing counted as delivered** (a failed response; a one-shot reminder acked unheard). Now no audio and no tool means not heard, fired again.
- **A press while the injected turn took focus** still sent the prompt and `response.create` (the orphan response played as the press's answer). `_fire_injected_turn` re-checks after its awaits and sends nothing for a replaced turn.
- A popped request was invisible during an awaited log line (close, dedup). The dequeue line is now synchronous and `_fire` resolves DROPPED when closed.
- A failed send left the injected turn holding DIALOG and dropped the request; a skill cancelling mid-fire could leave a half-made turn. The turn is ended and the request put back; the direct fire is shielded from the caller's cancellation.
- **Urgency over arrival** (taken; the critic left it to Mario): a 30-second call announcement queued behind non-expiring message announcements expired while grandpa listened to them. Drains now take the most urgent tier first (PREEMPT, BLOCK_BEHIND_COMMS, NORMAL), arrival order within a tier. Standard practice for alerts; reversible in one function.
- `None` re-fired on every reconnect with no cap: at most 5 attempts per turn, then DROPPED.
- `_confirm_delivery` had no exception guard: guarded; the backstop timeout logs.
- Nits taken: a generic repeat note (it said "the connection dropped" after a press too), the site's cheat sheet, the timers doc (a restored timer's remaining time), a cancel during `_submit` withdraws, ASCII in comments, a stale "5/10/30" comment. Not taken: the protocol return-type change breaking typed third-party fakes (old calls still type-check; documented).
- Found by me while it ran (`fix(runtime): a tool-only inject ...`): a tool-only inject ("call resume now, say nothing") was put back by a press while awaiting its confirmation and ran its tool again; a drain that raised on a client's return left the no-client content hold.
- Checked by the critic, nothing serious: the WaitCue gates and disarm points, the server's epoch and bounded ping, Part 2's single loop, reminders' retry indices and ack/cancel/snooze, telegram teardown, `/health`.

### Tests (Gate 3)

- `server/runtime/tests/unit/test_coordinator_inject_waits.py` (new, 41): the regression (no client: not spoken; a client connects: spoken), written against the API the old coordinator had too and checked failing on main's sources; expiry while waiting and the 10-minute default; order one at a time and urgency first; dedup replace-in-place and drop-while-heard; cut-offs (client leaving mid-turn and before playback ends, evicted after the turn and before the ping, an unanswered and a never-returning ping, a session drop, a press into vs before the audio, a tool-only turn and a press, a cut-off past its expiry); races (a press while focus is taken, a tap after a press put a turn back, a silent response, the attempts cap, a failed send, a cancel mid-fire); the WaitCue never arming through the new paths; a live call when the client returns; content parked until the narration ends and between two turns, and the no-client hold released when the narration fails; withdraw, close, `/health` summary; QUEUED from a tool handler and from setup while a spawned task can wait.
- `test_server_client_ping.py` (new, 9): listener epoch only after the hello, pong, a newer connection proving nothing, close before the pong, a closed socket, a missing pong and a ping stuck on a full buffer both aborting, a real websockets client.
- Part 2 in `test_app_session_gap.py`: a failed boot connect retried by the loop; no second loop (both fail on main).
- Changed on purpose: the 10 s session wait (now up to its expiry), the drain-sleep tests (now the playhead confirmation), FIFO chaining and same-key re-fire (wait for the previous to be heard), eviction presence (`[True, False, True, False]`), `/health` fields.
- Skills: reminders (late-window expiry per kind and with a persona override; the retry firing at runtime, counted from the hearing; no retry stacking on a waiting fire; EXPIRED to missed and mentioned; boot re-fire in and out of the window; an ack withdrawing a waiting fire), telegram (an unheard call hangs up and queues a missed-call turn; later messages follow as one announcement), timers (expiry by duration, capped).
- Mutation checks: fifteen deliberate breaks, each turning a named test red.
- Counts: runtime 949, SDK 81, reminders 99, telegram 127, timers 35, the other skills unchanged and green; ruff, ruff format, mypy --strict clean; `test_focus_manager.py` did not flake in a dozen full runs.

### Docs touched (Gate 4)

`turns.md` (the waiting rule), `concepts.md`, `architecture.md`, `observability.md` (inject events, `server.client_ping`, `app.reconnect_*`, the `inject_queue` hold, `/health`), `protocol.md` (`/health` gains two fields; no WebSocket message changes), `extensibility.md`, `skills/README.md`, `skills/reminders.md`, `skills/telegram.md`, `skills/timers.md`, `personas/README.md` (`inject_repeat_note`), `decisions.md` (ADR "Proactive turns wait for a listener"), the site's `build-skill/proactive.mdx`, `cookbook/notifications.mdx` and `reference/skill-cheat-sheet.mdx`, abuelos' translated note, D7 closed. README, CLAUDE.md: none affected (checked: no command or setup change).

### Ship (Gate 5)

Branch `t144-inject-waits` (worktree), on base f0897867: 7d23267e (design), 36fd744b (framework), d05ff5a3 (skills), 833661eb (self-found fixes), then the second critic round. Not merged, not pushed. Before relying on it: Mario's smoke on the walkie (a reminder while it is off Wi-Fi, then back; a reminder through a server restart with persona at boot; pull the plug mid-narration). Merging into main: `docs/protocol.md` and `docs/triage.md` also changed on main since the base (small, separate sections).

**Follow-ups** (file when this merges): audiobooks' "paused for the long call" note, Spotify's notices and the system skill's battery warning take the 10-minute default and could pick their own; marking Telegram messages read once heard (read ticks the family would see: Mario's call); counting only the firmware client as grandpa's listener when the PWA evicts it (Mario's call); an earcon before proactive speech (`vision.md`); `TCP_USER_TIMEOUT` and a tighter keepalive (content still plays into a dead socket for 20-40 s); the reminders boot reconcile's retry interval index (off by one, predates T1.44); the last medication fire marked missed before it is said (predates T1.44).

**Smoke R2 (2026-10-12, merged 66e7f5f6, live on the Pi)**: a narration cut by a Wi-Fi drop had reached the walkie whole and played from its ring while offline; the server marked it `no_pong` and repeated it after the reconnect, so it was heard twice. Fixed on `t144-heard-after-drop`: the reconnect's `device.hello` now reports `played_after_drop_ms`, `ring_left_ms`, `drop_uptime_ms` (firmware 0e9a3347), and a turn cut after it was all sent waits up to 2 s for that hello and counts as heard when the ring drained and played at least what was unplayed at the drop (turns.md).

**Lessons**: both critics earned their keep. The first turned "a pong timeout is delivered" and a cached presence flag around before any code; the second found four bugs in code that passed 940 tests, three of them in the gaps between awaits. The repo's medication retries had never fired at runtime: nothing tested the scheduler past the first fire.

## T1.45: A book recommender that only offers well-known books

**Status**: `in_progress` (2026-10-13): code, docs and the page/app views built on branches `t145-shelf` and `t145-page-books`; `shelf.json` waits for Spotify's `/audiobooks` block to lift (about 17:30 Bogota, 2026-10-14), then the merge, the deploy and Mario's walkie smoke · **Effort**: M (one skill, a build script, the persona, the page card; no framework change)

**Problem** (Mario, 2026-10-13): make book search work as a recommender for his grandfather, never offering obscure or invented books; a list of well-known books to pick from; a books section on the page (recommended, in progress, read). His calls: never mention length; treat AI-voiced editions like human ones; pick the books with the API and judgement ("no idea on his favorites").

**Why it matters**: a blind listener can't browse a catalogue. A recommendation he can't place (a self-help title, "Parte 1" of a dramatized fantasy) wastes a turn and some of the month's listening hours, and he won't ask twice.

### Validation (Gate 1)

- Spotify's Web API gives a development-mode app no recommendations, browse, charts or bulk lookups (cut February 2026). Audiobook search works in market CO with authors, narrators, languages and a synopsis; there is no field for AI narration.
- Production, 2026-10-13: "algo bueno para recomendar en audiolibro" offered El mito de Sísifo, La montaña eres tú (self-help) and "Alas de sangre (Versión dramatizada): Parte 1"; "La guía de la galaxia" brought "PLEYADIANOS LOS HIJOS DE LAS ESTRELLAS" as the third. Theme searches return reading guides ("Fiche de lecture", "Lektürehilfe", "Resumen y análisis").

### Design (Gate 2)

Planned by a planning agent, built by a builder agent; a user interrupt stopped the builder at 18:02 with the work uncommitted, and Claude finished it in the main session (suite fixes, critic check, merge with main, docs, the real build).

- **Shelf files** (repo-tracked: famous books, not personal data): `server/personas/abuelos/books/shelf.yaml`, the curated source (134 books, 11 spoken categories, tags, a one-sentence pitch each, pinned URIs, preferred narrators; chosen by a curator agent with search-only API checks); `shelf.json`, written only by the build, read only by the skill. Config `book_shelf` (default `../books/shelf.json`, relative to the persona data dir).
- **Build** `huxley-spotify-shelf <persona_dir> [--write] [--only TITLE]` (`shelf_build.py`): per book, search "title author" then the title; keep an edition only if the title is close (0.75; subtitle, edition note and ". " subtitle dropped; digits count), an author surname matches, the language is the persona's, it isn't a guide or abridgement (title, synopsis phrases, or Spotify's "Abridged edition" note), and it lists chapters (else the next edition; a pinned one must); a pinned URI wins, then the narrator, the score, Spotify's order; OK / CHECK / REJECT per book, exit 1 on a reject, atomic 0644 write, nothing written when nothing passed. Requests rationed: one `/audiobooks` lookup per edition, none for one the previous shelf.json has; a run stopped by a 429 writes what it checked. Retries 429 three times honouring Retry-After up to 2 minutes (build only).
- **Skill** (`shelf.py` pure functions; `books.Shelf` renamed `BookMemory`): reload on mtime/size; an invalid file keeps the last good shelf, a missing one falls back to T1.40's open search. `find_books(query?, like?, more?, listened?)` browses categories (plus the book in progress), a title, category, tag or author, "algo como", "otros", and what he listened to; the skill words the offer in `then`. `play_book`: a shelf title plays the checked edition without `ask_which` (or his own edition if he started one); a theme goes to the shelf. `book_opinion(liked|disliked, title?)`.
- **Memory**: `book:ended`, `book:opinion`, `book:offered`; abandoned = started, never finished, not the last book, unheard 21 days. Ranking excludes finished, disliked, refused, started; liked author +2, finished author +1, liked category +1, disliked author -3; ties to the least recently offered.
- **The page contract**: `book:summary:<id>` (DB key `spotify:book:summary:<id>`, JSON text: uri, title, author, chapters, chapter, position_ms, last_at, finished, opinion, source) written on every place, end and opinion, serialized, backfilled at setup. The page's Books card (24c673d4) reads it with `shelf.json`.

### Critic notes (Gate 2)

One fresh critic on the plan, 2026-10-13 (seven blockers). How each landed:

1. **First offers wait on probes** (a book's playability can't be known from the Web API; the critic estimated 10 s typical, 34 s worst). Measured instead of guessed, production logs 2026-10-13: one probe about 2 s; a `find_books` with three unknown books 6.1-6.2 s from the tool call to the answer (the T1.41 waiting sound plays); 0 refusals in about 20 probes; known books skip it. Kept: probe at offer time, remembered per book. Not built: an idle-time warmer (Soloist commands while nobody listens, against a 6 s cost that shrinks as books become known). Revisit if the logs show offers over 8 s.
2. **Persona dir with a symlinked `data/`**: the path is relative to the data dir with `normpath` (`../books/shelf.json`), absolute paths accepted; `data/` is a real directory on the Pi and the Mac.
3. **The theme guard broke garbled titles** ("Sidarta"): redirect only when the words match a shelf category or tag and no shelf title or author; anything else keeps search and `ask_which`.
4. **Edition identity**: another edition of a book in memory (same author, close title) counts as that book for exclusions and opinions, and a shelf title continues his own edition.
5. **Regressions for authors and titles off the shelf**: a book or author not on the shelf is searched, keeping only that book or that author's books (still probed); "¿de qué trata <título>?" matches shelf titles and offers that book with its pitch.
6. **Summary write race** (a place and a book's end land together): `update_summary` merges only the fields it's given, under a lock; finished survives; title and chapters carried by a `Label`.
7. **429 retry in the shared client**: the live skill never retries (a turn would stall); only the build's client honours `Retry-After` (3 retries, up to 2 minutes each), with a pause between books, and a stopped run keeps what it checked. The critic's warning came true anyway (see Build result). Study-guide filtering on the synopsis uses phrases only a guide uses; a pinned URI overrides a synopsis flag.

### Code review (Gate 3)

One fresh reviewer on the finished code, 2026-10-13 (the plan critic never saw code). One blocker, six should-fix, three minor; every repro failed on the code, every one fixed with a test (the theme and author ones broken on purpose to see their tests fail):

1. **Blocker: titles with a theme word went to the shelf** ("Las aventuras de Tom Sawyer", "Breve historia del tiempo", "El misterio del tren azul" answered with adventure, history or mystery offers, again on every retry): a category or tag only when every word said names a kind of book on the shelf.
2. A named book offered alone started the shelf's edition over his own: the offer carries his edition.
3. "El dos" (the offer says "Uno, Dos, Tres") went to open search: the numbers pick.
4. "Otros" after a single-book offer hit "no tengo libros de eso": books like that one.
5. An off-shelf author brought back books he finished or didn't like, and only one edition of a named title was tried: the shelf's exclusions apply (same editions logic), every edition gets its chance.
6. `more` with a new subject repeated the old offer: a fresh offer, logged (`shelf_more_new_subject`).
7. Ranking recomputed every book's editions per book (1.6-2.4 s on a Mac for the whole shelf with 10-20 books remembered, growing as he listens): a `Judge` per offer finds each book's editions once; a test counts the comparisons.
8. One edition answering 404 stopped every build at that book: that edition counts as having no chapters, the next one is tried.
9. Books not re-checked kept stale yaml fields: rebuilt from shelf.yaml, only the edition, narrator and date kept.
10. Missing log lines: `shelf_blocked`, `none_playable` for off-shelf searches, `like_nothing`.

### Definition of Done

- Spotify suite green, ruff, ruff format, mypy clean; runtime suite still green.
- The real build run on the Pi (search only), `shelf.json` committed.
- Docs: `docs/skills/spotify.md`, `docs/personas/abuelos.md`, persona.yaml (es/en/fr), `docs/decisions.md`, the skill README, this entry.
- Merged with the page's Books card; deployed when idle; Mario's walkie smoke: "¿qué libros me recomiendas?", "quiero un libro de misterio", "otros", "el segundo", "pon algo de aventuras", "algo como Cien años de soledad", "me gustó".

### Build result (Gate 3)

Run on the Pi, search only (the branch's files copied to `/tmp`, nothing of the running server touched), 2026-10-13:

- **Run 1** (builder's code plus the "1984" and "Sapiens." fixes): 134 books, 131 OK, 2 CHECK, 1 REJECT, 133 written. The CHECKs were narrator spellings ("Voz artificial" vs Spotify's "AI Voice", "Varios narradores" vs "Various Narrators": fixed in shelf.yaml); the REJECT was El juego del ángel, whose pinned edition lists no chapters (unpinned; the build now takes the next edition that lists chapters). The alternatives printed 14 editions Spotify marks "Abridged edition", which the title check couldn't see: the guide check now reads Spotify's edition note, in the build and in the live skill.
- **Run 2**: the edition-note rule rejected three pinned editions as abridged (1984, El tiempo entre costuras, Doña Bárbara: unpinned so the build picks a full one); stopped at about book 130 by a 429 its 30 s cap couldn't wait out, and wrote nothing (the old rule).
- **Run 3**: blocked from the first book. `/v1/audiobooks/{id}` and its chapters answer `429 Retry-After: 68658` (about 19 hours); `/v1/search` answers 200. Cause: the curator's probes plus four full runs in three hours, each paging every chapter of every chosen edition. The live skill shares the app, so until the block lifts books play but are treated like music (no chapter commands, no end-of-book line; `spotify.book_info_failed`); grandpa doesn't have the walkie yet. Fixed in the build: one `/audiobooks` request per edition, none for an edition the previous shelf.json has, a stopped run keeps what it checked, 3 retries up to 2 minutes.

`shelf.json` is not built yet: one run after the block lifts (about 17:30 Bogota, 2026-10-14), then the deploy.

### Tests (Gate 3)

Spotify suite 229 passed, 1 skipped (main: 126), the review round's 9 included. `test_shelf.py` (40: validation, reload and keeping the last good file, matching titles with digits and subtitles, categories, tags, authors, themes, ranking with exclusions, edition equivalence and boosts, paging, Spanish lists with "e" before an "i" sound); `test_shelf_build.py` (29: guides, Lektürehilfe, Fiche de lecture, abridged by title and by Spotify's edition note, wrong author, wrong language, a guide synopsis, far titles, "1984" and "Sapiens." from the real build, pinned editions kept, looked up, rejected or gone, an edition without chapters giving way to the next, a pinned one without chapters rejected, one lookup per edition and none for one already verified, a rate limit mid-run keeping what was checked, a network error before anything and nothing-passed writing nothing, `--only` keeps the rest, dry run, 0644 output that loads in the skill, setup errors); `test_books.py` (browsing never searches, "otros", nothing fits, refused shelf books skipped, a shelf title plays the curated edition or his own, a theme redirected, opinions move the ranking, a missing shelf falls back, the summary key and its exact fields, the backfill, an abridged edition never picked); `test_soloist_and_search.py` (the build client waits out three 429s and gives up after; the live client never retries). The abridged rule was broken on purpose to see its two tests fail.

### Docs touched (Gate 4)

`docs/skills/spotify.md` (the shelf section, find_books, offers and measured probes, config, logs), `docs/personas/abuelos.md`, `server/personas/abuelos/persona.yaml` (es/en/fr book bullets, `book_opinion`), `docs/decisions.md` (ADR: recommendations only from the shelf), `server/skills/spotify/README.md` (the book tools were never listed), this entry.

### Ship (Gate 5)

Branch `t145-shelf` (worktree) on main 8af68aa4: the skill, the build, the shelf source, the persona and the docs (this commit); `shelf.json` after the block lifts. Branch `t145-page-books`: 24c673d4 (the page's Books card), 92f1de4b (merge of main), 21e932da (the phone app's Books tab). Not merged, not deployed.

**Lessons**: a background agent stopped by an interrupt sends no notice; check agents after any interrupt. Spotify rations `/audiobooks` for a development-mode app far below what a "verify everything" build spends: measure the request count of a batch job before running it twice.

## T1.46: A phone app of how grandpa uses the walkie

**Status**: `in_progress` (2026-10-13: merged and live on the Pi at /app/; the Books tab ships with T1.45) · **Effort**: M

**Problem** (Mario): he wants to understand how his grandfather uses the walkie, from his iPhone: when he last used it, how much against his usual, who he talks to, what he enjoys, where he struggles. The caregiver's page (`/`, T1.42) is a reliability dashboard: dense, about the device and the server, and it can't tell "quiet" from "unwell", or a bad answer from a good one.

**Validation**: the 2026-10-12 logs hold everything the design needs except one thing. Each press's words are recoverable: transcripts carry no turn, but each committed press is one input item transcribed in order; pairing them oldest-first matched 304 of 304 committed presses (1.2 s after the release at the median, 5.5 s at p99, 19.3 s at most). The struggles have their signals: 32 empty transcripts and 10 Whisper hallucinations ("Subtitulos por la comunidad de Amara.org", "Gracias por ver el video"), 9 silent presses, `coord.tool_error` and the skills' own "couldn't" lines (`spotify.unavailable reason=`, `spotify.start_refused`...), corrections ("No! Quiero escuchar al grupo Menudo!") and repeats (four `get_battery` in two minutes). The missing piece: a book's chapter index (only its name was logged at a start), so "ch. 5 of 19" needed the Spotify skill to log it.

**Design** (approved by Mario after a design critic; binding rules from that critic):
- Three tabs (Today, Trends, Talks), bottom tab bar, one scrolling screen each, at `/app/`: one self-contained HTML file (inline CSS and JS, no build, no CDN: it runs on the tailnet), hash routes, fetch on open, every 60 s while visible and on a pull at the top; dark mode, safe areas, 44 px targets, the page's look. A home-screen PWA: manifest, icons from a Pillow script, Apple meta tags; a service worker only in a secure context (iOS runs none over http on the tailnet), shell and last `/app.json` only.
- Data: `/app.json` (`schema: 1`, `app.AppStatus` TypedDicts), built in the same cycle from the page's Inputs and segments: no second log parser. `talks.py` turns the kept events into requests and verdicts.
- Honesty: "his usual" only after 14 days with the walkie mostly online (half the daytime, bench left out), until then "Learning his routine, day N of 14"; offline days hatched, never low use; device state on every quiet stretch and day; compared only with his own range; struggles by five named rules (tool error, not heard, no answer in 20 s, corrected within 60 s, asked again within 90 s; skips and volume excluded); his timezone, labelled when the phone's differs.
- Privacy: a day's words fetched live from `/day/YYYY-MM-DD.json` (`no-store`), never cached; the worker never touches `/day/` or `/audio/`; recordings stay on `/`.

**Definition of Done**:
- `/app.json` with every field above, from the cycle, documented in the README; `watch.app_json_built` (debug) each cycle; a failed build costs only the app (`watch.step_failed step=app`).
- `/app/`, its manifest, worker and icons served with the right types and caches; package data shipped by hatch.
- Each struggle rule and its exclusions unit-tested; the learning state before 14 days and a range after; offline days hatched; people; favourites; first use; conversation counts; routes and types; the page self-contained.
- ruff, ruff format, mypy --strict on `server/watch/src`, all watch tests green.
- Looked at: screenshots at 393x852, light and dark, real logs and a 20-day fixture; no horizontal scroll at 375 px.
- A fresh critic on the finished code; its findings fixed or answered here.

**Built** (2026-10-13): `3bb433ee` the Spotify skill logs `chapter_index` and `chapters` at a start, a move and an end (`book` on `spotify.book_nav`); `b929968f` the app (`app.py`, `talks.py`, `static/`, routes, the LogReader keeping his transcripts, tool calls and failures, `spotify.track`, the book lines).

**Critic notes** (one fresh agent on the finished code, 2026-10-13; every finding taken, with a test that fails without the fix):
1. "Asked again" fired on empty-query pairs: 9 of 13 real verdicts were `get_current_time` asked twice. Now a missing query on either side is never a repeat; `query_of` reads only text arguments (a volume level or an id is not a query).
2. "Corrected" was too loose ("no tienes otros?", "otra vez", "otro libro" counted) and fired around skips. Now only a leading "no," (or "no!", "no.", a bare "no") or "esa no"/"ese no", and never when either request is a skip or a volume change.
3. Struggles quoted hallucinations as his words. Now `said` is null for a garbled transcript and the detail says `transcribed as "..."`; "last used" shows "not heard" instead (`heard: false`).
4. An empty transcript with an answer within 20 s was "not heard": the model hears the audio, not the transcript, so it heard him. No longer a struggle.
5. The words pairing could shift (A/B/C): a committed press whose audio was never sent, or one whose session died, waited for words that never came and took the next press's. Now a press waits only after `session.tx.commit`, and `session_connected` / `app.reconnect_succeeded` / `huxley_starting` empty the queue. Regression tests for both shifts.
6. A 5-second call read "talked". Now under 20 s is "called", and the length shows (to the second).
7. Today's partial bar sat inside the usual band as if comparable. Now it says "so far" and the band is drawn only behind whole days.
8. "Used today" didn't say when the walkie had been away. Now "walkie offline N% of the day so far" from 5 %.
9. The share online counted from the daytime start even on the day the logs begin, so that day read "walkie offline". Now it counts from where the logs begin, and Trends marks that day "logs begin".
10. A reminder speaking after his press counted as his answer. Now only `coord.audio_start owner=user`.
11. The service worker cached `/app.json` whole, his words included. Now its offline copy has `last_used.said` and each struggle's `said` and `detail` taken out.
12. Nits: the Talks battery mark reads `battery_low_pct` from the JSON (was 20 hard-coded); the README's first-use wording now matches the code (qualifying days only).

**Tests** (Gate 3): `test_talks.py` 27 (each rule and exclusion, the pairing and both shifts, injected audio), `test_app.py` 27 (learning before 14 days and a range after, by time of day, offline and partial days, bench, people talked/called, favourites, chapter progress, first use, struggles and day counts, staleness, the log summary), `test_app_web.py` 9 (routes, types and caches, redirect, 404s, manifest, self-contained page, no-store days, the worker's stripped copy, a failed build costs only the app); spotify `test_books.py` +1. Mutation checks: each rule's guard removed turns a named test red. Totals: watch 366, spotify 126; ruff, ruff format, mypy --strict clean.

**Docs touched** (Gate 4): `server/watch/README.md` (The phone app: tabs, honesty rules, the schema, the home screen, privacy; the log's `watch.app_json_built`), `docs/observability.md` (what the app reads), `docs/skills/spotify.md` (the chapter fields), `CLAUDE.md` (layout line).

**Looked at**: screenshots at 393x852 light and dark on the real 2026-10-12 logs (learning state, 87 of 309 struggles on Mario's test day) and on a 20-day synthetic fixture (range state); no element past the edge at 320 and 375 px. Fixed from the screenshots: a long struggle list (now newest 6, "show all"), the day view's back button scrolling away (now in the header), a whole minute of highlighted rows (now the half minute from the press), "1.2 h-1.7 h" (now "1.2-1.7 h").

**Ship** (Gate 5): branch `t146-phone-app`, not merged or pushed. Before relying on it: merge, restart huxley-watch on the Pi, add `http://100.75.152.70:8780/app/` to the iPhone home screen.

**Lessons**: the critic's best catch was in the words pairing: correct on a clean day (304 of 304), it shifts by one after a single press whose audio never left. Order-based joins need an explicit "this item exists" event (`session.tx.commit`) and a reset on every session boundary.

---

## T1.47: One loudness level for everything the walkie plays

**Status**: `in_progress` (2026-10-13: merged and live on the Pi; the walkie's prompts re-levelled in firmware 5f699660; waiting on Mario's ear on the walkie) · **Effort**: M (runtime audio path, SDK field, two skills, assets)

**Problem** (Mario, live): the chimes jump out, and he pushes the volume to max for the voice. Every source plays at its own level.

**Why it matters**: grandpa can't see a volume control and shouldn't need one between a chime, the answer and the radio. A chime 10 dB over the voice startles; a voice 7 dB under everything else is the thing he strains to hear.

### Validation (Gate 1)

Measured 2026-10-13 with ffmpeg `ebur128` (integrated LUFS, true peak):

- AI voice, 14 answers from the recorder's `*-model.ogg` (what the walkie got): -24.8 to -28.4, median -27.1; true peak -4.7 at most, about -9 typical.
- Walkie firmware prompts about -26; `unavailable.wav` -23.0.
- Chimes: `book_end` -20.5, `news_start` -19.1, `search_start` -17.7 (true peak +0.3: clips), `book_start` -16.8, `radio_start` -15.2, `persona_swap` -15.2. `no_signal_tone` momentary max -20.9.
- Radio (25 s each, fetched on the Pi): Caracol -17.4 (-13.9 on an earlier day), Blu -23.3 (-19.4), La W -18.1, Javeriana -15.2.
- Spotify: unmeasured; Soloist has no normalization option.

### Design (Gate 2)

One target, -20 LUFS integrated, for program audio; cues 3 dB under; peaks at most -1.5 dBTP decoded on the walkie. One pure-Python module, `server/runtime/src/huxley/loudness.py` (no numpy on the Pi; benchmarked there: K-weighting 8.4 ms per second of audio, gain plus limiter about 7 ms), shared by the runtime, the scripts and the tests. Full write-up: `docs/sounds.md` "Loudness".

- **Measures**: BS.1770 at 24 kHz (libebur128's coefficient formulas). Program audio integrated. Cues by momentary max (loudest 400 ms) at -19: a chime is heard at its strike, its integrated value depends on its tail, and a clip under 400 ms has none; -19 lands the palette at -22.4 to -23.8 integrated. Reads within 0.2 LU of ffmpeg on every test signal and shipped sound.
- **Voice**: fixed gain plus `PeakLimiter` in `TurnCoordinator.on_audio_delta`, model audio only. Gain = persona `audio.voice_gain_db`, else the provider's calibration `openai_realtime.OUTPUT_GAIN_DB` = +7 (measured). abuelos pins 7 (measured on its voice). The recorder taps the wire, so `model` files are post-gain.
- **Streams**: SDK `AudioStream.loudness_key`; `ContentStreamObserver` runs a `StreamLeveler` before the duck envelope. The first `preroll_ms` the stream yields (its earcon) passes as is. Leveler: K-weighted 100 ms blocks, 400 ms window; silence gate -45 LUFS; relative gate -10 LU; program estimate seeded by the first window, running mean then exponential with a 30 s time constant; gain target minus estimate within -20/+6 dB, 6 dB/s down, 2 dB/s up; start at the key's learned gain (persisted in the persona's settings, `audio.level_gain_db:<key>`), else -6 dB; limiter last at -3.5 dBFS. Radio `radio:<id>`; Spotify `spotify:book:<uri>`, `spotify:music`.
- **Wire**: one `PeakLimiter` in `AudioServer.send_audio` at -3.0 dBFS sample peak, the ceiling for whatever reaches it (call audio, persona or third-party sounds).
- **Limiter**: no added latency; the chunk is its own lookahead (per 1 ms block, envelope at each boundary = min of this and the next block's need, so the ramp never lets a sample over); hold 15 ms, release 150 ms; a peak in a chunk's first millisecond ramps (no step) and the few samples over are clamped.
- **Assets**: `synth_sounds.py` normalizes by momentary max with true-peak and wire ceilings (same waveforms, correlation 1.0, only the level changed); `no_signal_tone_pcm` amplitude 6000 to 7440; the waiting blip by loudness at -26 momentary max (keeps T1.41's place under a voice now 7 dB louder); `render_persona_clip.py` levels to -20 and gains `--level` to re-level a committed clip; `unavailable.wav` re-leveled to -20.2 (peaks allowed +2.9 of +3.0).
- **Walkie firmware prompts** (owned by the firmware session, not touched here): target -20 LUFS integrated, true peak at most -1.5 dBFS decoded, like `unavailable.wav`. Done there in parallel (5f699660, firmware F-0027): after the Opus round trip `offline` -19.9 LUFS, `battery_empty` -20.1 (were -26.6 and -26.1), flashed to go with this branch's +7 dB voice.

### Critic notes (Gate 2, one fresh critic, blockers only)

1. *Every stream starts too loud: unknown keys at 0 dB, learned gains lost on every deploy.* Fixed: unknown keys start at -6 dB (most sources are louder than -20), the gain comes down at 6 dB/s and goes up at 2, and learned gains persist in the persona's settings.
2. *Pumping on music: tau 10 s, a gate release after 15 s, +10 dB of boost ride verses up.* Fixed: tau 30 s (a 20 s verse 6 dB down now moves the gain about 2 dB, not 5), gate release dropped, boost capped at +6. Not taken: tau 60 s or a reset at track change (track change is a skill concept; 30 s corrects a quieter track within a minute). Soloist has no normalization to use instead (checked before T1.47).
3. *The limiter clicks: a gain step when a peak lands in a chunk's first millisecond; 50 ms release with no hold ripples.* Fixed: that block ramps, and the few samples over are clamped (no step, no added latency; the suggested 1 ms carry would need flushing on every `audio_clear` and response end); hold 15 ms, release 150 ms.
4. *-2 dBFS is too tight for 32 kbit/s Opus.* Measured through the server's own encoder and decoder: speech limited at -2.0 decodes to -1.65 dBTP, at -3.0 to -2.6; heavily limited radio at -3.0 decodes to -1.25, at -3.5 to -1.69 (unlimited Caracol gains 1.2 dB). Wire and voice -3.0, streams -3.5.
5. *The ceiling is a property of the wire, yet the design had limiters only at two sources: Telegram call audio and persona sounds bypass both.* Fixed: one limiter in `AudioServer.send_audio`; gains stay at the sources (a boosted source still needs its own limiter, or PCM16 overflows before the wire sees it).
6. *The voice gain default must not be 0, and the calibration belongs to the provider.* Agreed: `OUTPUT_GAIN_DB` lives in the provider module, used unless the persona overrides; with 0 every other persona hears streams and cues over a voice at -27, the same bug. Not taken: a table keyed by model and voice (one measurement so far).
- Minor, not taken: a separate field instead of reusing `preroll_ms` for the leveler bypass. The field already means "pre-roll before actual content"; documented that it plays as is, so it must ship at the cue level.

### Post-build review (one fresh reviewer, after the three commits)

Five findings, all fixed with a regression test that fails without the fix:

1. *The leveler locked quiet after a lasting drop of over 10 LU*: the relative gate compared each window with the program estimate, which the gated windows could then never bring down (5 s at -9 then 40 s at -21 sat at -31.8, and saved that gain). Now the gate's reference is every window over the silence gate, averaged over 10 s (BS.1770 likewise gates against the absolutely gated level): a lasting drop gets through in 5 to 15 s and is followed. Recovery from such a drop takes about a minute (the estimate averages energy, so it comes down slower than it goes up: a 12 LU drop is within 1.5 LU after 90 s). A dB-domain estimate recovers in 30 s but plays every stream about 0.5 dB loud; kept energy, the measure the standard is defined in, and the asymmetry errs quiet.
2. *The release snap let samples over the ceiling* (by up to 0.007 dB when a block needed between 0.999 and 1): it snaps only when nothing in the block needs a cut.
3. *The wire limiter was never reset*: a loud unleveled source cut at a press would fade the next reply in. Reset on `audio_clear` and on each new connection.
4. *The voice limiter reset only when the speaker flag was down*, which a completion turn keeps through the hand-over. It now also resets when a response's audio ends (`on_audio_done`, cancelled or not).
5. *`_level_stopped` called `get_running_loop()` in the pump's `finally`*, which raises if the loop is closing: guarded.
- Also: a key's gain is learned only once a window was measured (a stream stopped in its first 400 ms used to save -6 dB as "learned"); the resume test now checks the gain carries over; the blip is rendered once (cached).

### Definition of Done

- Program audio measures -20 ±1 LUFS: the voice with its gain, `unavailable.wav`, each radio station after the leveler settles; cues at -19 ±0.5 momentary max; nothing over -1.5 dBTP.
- The limiter never exceeds its ceiling; the leveler converges from 10 dB off either way, holds through silence and quiet passages, caps its boost.
- The voice gain applies to model audio only, and only when configured; a stream is leveled only with a key, and its preroll is untouched.
- `audio.level` logs each leveled stream at start, every 30 s and at stop; `coord.audio_done` and `server.tx.audio_stats` say what the limiters cut.
- ruff, ruff format, mypy strict, runtime, SDK, radio, Spotify (and audiobooks, news, watch) suites green.
- Mario's ear on the walkie: one volume for the chime, the answer and the radio.

### Tests (Gate 3)

- `test_loudness.py` (29): BS.1770 against ffmpeg on regenerated signals (sines at 100 Hz / 997 Hz / 5 kHz, seeded white and pink noise, within 0.2 LU), gating, momentary max of a short burst, true peak of an inter-sample over; limiter never over the ceiling (noise, a 150 Hz sine, impulses in chunks' first millisecond, three chunk sizes, fixed and ramped gains), hold and release back to the fast path, odd bytes; leveler converges from 10 dB under and over, cuts a loud source, holds through silence, ignores a quiet passage, caps boost, starts where the key left off, rises slowly, reports every 30 s.
- `test_loudness_wiring.py` (19): voice gain off is bit-exact, on gains and limits model audio and logs its cuts, never touches a `PlaySound` or a stream; wire limiter passes quiet audio byte for byte and caps loud; persona `audio` block (default None, bounds, unknown key, abuelos pinned at 7); every shipped earcon at -19 ±0.5 momentary max under both ceilings, `unavailable.wav` at -20 ±0.5, the tone and the blip at their targets.
- `test_turn_observers.py` (+5): no key untouched; keyed stream leveled with its preroll intact (also split across chunks); learned gain saved at stop; a resume keeps the leveler and logs `resume` then `cancelled`.
- Radio and Spotify tests assert the keys. `test_firmware_contract.py` byte-pattern payloads now quiet PCM (the old patterns were full-scale noise the wire limiter rightly changes); `test_wait_cue.py` blip peak about -9 dBFS.
- After the review round: `test_loudness.py` 32, `test_loudness_wiring.py` 25, observers +5.
- Suites (rebased on main 312e4182): runtime 1256, SDK 81, radio 19, Spotify 126, audiobooks 67, news 18, telegram 127, timers 35, reminders 99, search 34, stocks 40, system 20, watch 366, all green.

### Measured result (ffmpeg `ebur128`)

| Sound | Before | After |
|---|---|---|
| Voice, 14 answers together | -26.9 LUFS, TP -4.7 | -20.0 LUFS, TP -2.6 (-2.4 decoded from Opus) |
| `unavailable.wav` | -23.0, TP -5.9 | -20.2, TP -3.0 |
| Chimes (6) | -15.2 to -20.5, one clipping at +0.3 | M max -19.0 each, -22.4 to -23.8 integrated, TP -4.4 to -10.0 |
| no-signal tone / waiting blip | M max -20.9 / -32.9 | M max -19.0 / -26.0 |
| Radio, first play (Blu, Caracol, La W, Javeriana) | -23.3, -17.4, -18.1, -15.2 | -20.4, -20.1, -20.7, -19.2 (last 15 s: -19.9, -19.7, -20.7, -18.6) |
| Radio, replay with the learned gain | | -19.9, -20.0, -20.6, -19.1 |

### Docs touched (Gate 4)

`docs/sounds.md` (new "Loudness": the standard, the stages and why each lives where, limiter, leveler, before and after; palette and pipeline level notes), `docs/observability.md` (`audio.*` namespace, "Loudness events", `model` files are post-gain), `docs/personas/README.md` (`audio` field and section; clips at -20), `docs/architecture.md` (one pipe, one loudness; code map), `docs/skills/README.md`, `docs/skills/radio.md`, `docs/skills/spotify.md`, `web/content/docs/concepts/side-effects.mdx` and `reference/skill-cheat-sheet.mdx` (`loudness_key`), `server/personas/abuelos/persona.yaml` (`audio.voice_gain_db: 7`). Not touched: `protocol.md` (no wire change), `server/sdk/CHANGELOG.md` (not kept since 0.1.1).

### Ship (Gate 5)

- Commits on `t147-loudness`, rebased on main 312e4182: 49e80f3f (framework), 3675d0b5 (radio, Spotify), e82ec363 (abuelos, this entry), a65ff2d8 (review round), plus this note. Not merged, pushed or deployed: merge, deploy and the restart are Mario's call.
- On the walkie after deploy: Mario will likely turn the volume down (the voice is 7 dB louder). The firmware prompts are already at -20 (5f699660), so until this deploys they are the loud ones.

**Follow-ups**: measure Telegram call audio from logs (only the wire limiter touches it; level it if it proves off); measure other OpenAI voices if a persona uses one (only coral is measured); `audio.level` lines will say what Spotify actually measures.

## T1.48: Web search and news that actually answer grandpa

**Status**: `in_progress` (2026-10-13: merged and live on the Pi; waiting on Mario's walkie smoke) · **Effort**: M (two skills + persona; no framework change)

**Problem.** Search was almost never used, and failed when it was: the one production call (2026-10-12, "cartelera Trocál Parque") hit `search.timeout reason=deadline_exceeded:4.0s`. The persona told the model not to search "stable facts you already know", but its knowledge is stale (it would name the previous Colombian president). Only snippets reached the model, and snippets rarely hold a score, a price or today's plates. News came back as 8 headlines with every clustered headline run together as a "summary" (6.9 KB), `category: local` was the national feed, and the model narrated vaguely ("algo llamado sometimiento", "cada noticia tiene distintos enfoques").

**Why it matters.** A blind user can't check anything; a wrong confident answer (the old president, last year's match) is worse than "no lo encontré claro".

### Validation (Gate 1)

- Production logs (Pi, 2026-10-12/13): one `search.dispatch` then `search.timeout` at 4.0 s; 5 `get_news` calls, each 8 items, `output_len` 6855; transcript of 2026-10-13 23:24 shows the vague narration above.
- Engine probe from the Pi, 2026-10-13, ddgs 9.14.1, fresh client per query: `yandex` 8/8 (about 1 s), `yahoo` 3/8 then 6/6 in another run (flaky), ddgs `auto` 8/8 but 3.7 s on a cold first call, `duckduckgo` / `brave` / `mojeek` 0/6 ("No results found.": blocked). A reused client kept a refused Yahoo session (shared: nothing; fresh: 5 hits). `import ddgs` 0.03-0.1 s; reuse saves about 0.35 s on Yandex: no warm-up.
- Page fetch probe (top 3 pages of 10 queries): most answer in 0.3-1.9 s; about one in four times out at 2.5 s or returns 403/502; a few are JavaScript shells or bot walls (empty text).
- Google News probe: a plain "Villavicencio" search returned 101 items, 5 from the last day, five copies of one accident; `when:2d` and `Villavicencio OR "del Meta"` gave 20 local items in the last day.
- Found on the way: in ddgs 9.14 only Brave and Mojeek read `safesearch`, and Brave read the skill's `"strict"` as OFF.

### Design (Gate 2)

Search (`server/skills/search/`):
- `DdgsProvider`: backends `yandex`, `yahoo`, `auto` in order inside an 8 s deadline, at most 3 s per attempt, none started with under 1 s left; worst failure wins (rate limited, timeout, error), all-empty is an honest empty; `region` from config (abuelos `co-es`); `strict` sent as ddgs `on`.
- Two news lanes in parallel with the web search: Google News RSS headlines (3, last 72 h, sharing two query words, copies dropped; `region` picks the edition) and a ddgs news-article search (articles with URLs; fresh by URL date, else by the engine's date, which can only overstate age).
- Merge: up to 2 fresh articles first, alternating with web results; persona `preferred_domains` up one place; video/social last.
- Excerpts: top 3 fetchable pages in parallel within 2.5 s, best 2 kept; lxml readability-style extraction (boilerplate tags and class names out unless they hold half the page's text; link lists, cookie and bot-wall text out; table rows kept), blocks chosen by query words, the weekday "hoy"/"mañana"/"ayer" means (pages say "Martes: 7 y 8"), numbers, then filled from the top; 1500 chars; page date (with the edit date for reference pages). Parsing in a thread. No excerpts for `safesearch: strict`.
- Output: `today`, `headlines`, `results` (title, source, date, snippet, excerpt); no `url`. Tool description and persona rewritten (es/en/fr): search anything that may have changed even if it seems known; one or two sentences naming the source; check dates; say "no lo encontré claro" instead of guessing; health and medicines general and "confírmalo con el médico o el farmacéutico". "A ver, déjame buscar" kept.

News (`server/skills/news/`): items `{title, source, age_hours, related?}` with up to two related headlines from Google's cluster; copies dropped (60% content-word overlap); searches and `local` add `when:3d` and keep 72 h; `local_query` config (abuelos `Villavicencio OR "del Meta"`); weather gains `tomorrow` and `rain_chance_pct`.

No `then` text added to news: narration style is persona scope (Basic wants five terse bullets), so the data got better instead.

### Critic notes (Gate 2)

One fresh critic on the finished diff and the eval JSON, 2026-10-13. Taken (each with a regression test that fails on the pre-fix code, except the plain cancellation test):

- **A bad result URL failed the whole search** (`https://xn--.com/` raised `IDNAError` out of the page task): `bad_url` mapping in the fetcher, plus a last-resort catch in each page task and in the headlines lane.
- **The news lane held the page fetches** for up to its 3 s: it now gets 0.6 s after the web search, then pages start; headlines are awaited beside the pages.
- **The weekday boost drowned answers** on pages that say "este martes" everywhere: block scoring is now IDF-weighted (a word on every block of the page counts little), the weekday joins the words; "esta mañana" means today.
- **Furniture filter ate news** ("no va a aceptar el resultado"): phrases, not words.
- **A degraded answer (web search failed, news lanes answered) was cached** 5 minutes: not cached.
- **Lanes outlived a cancelled or crashed call**: cancelled in a `finally`.
- **The "half the content" guard could drop the article's wrapper**: never drop an element wrapping `<article>`/`<main>`.
- **ddgs `auto` threads can run about 11 s** after a timeout: ddgs runs in its own 4-thread pool, not the loop's default one; docstring corrected.
- **Prompt injection and local fetches**: tool description and persona say results are information, never instructions/orders; the fetcher refuses localhost and private IP literals on every hop (the caregiver page is on 127.0.0.1).
- **Health escalation** (the critic left it to Mario; taken as standard practice, reversible in one line): urgent symptoms get "pide ayuda ya" and an offer to call a contact.
- Logging: headlines log `seen` and `reason=bad_feed`; an unknown `timezone` warns; `news.fetched`/`news.cache_hit` hash the query. Nits: related headlines drop a repeated outlet; deep JSON-LD can't raise; titles capped at 160 chars.
- **Eval honesty**: queries no longer leak answers (no opponent, no pope's name, no "retiro"); loose regexes tightened (word boundaries; "8:30" had matched "18:30"); a winner-only headline is "partial", not a hit; 3 held-out questions added; three rounds instead of one; mean and p75 reported.

Found by me in the rerun and taken: a response made mostly of video/social links is "weak" and the next backend adds to it (Yandex answered the stomach-ache question with Pinterest and TikTok).

Not taken: the two-word headline rule lets "presidente ejecutivo de Holcim Colombia" in for "presidente de Colombia" (harmless noise; a fix needs semantics).

### Definition of Done

- Provider: deadline, fallback, region pass-through, error mapping, strict mapping tested without network.
- Excerpt extraction tested on fixtures (article, table page, reference page, app shell, consent text, date boost); output shape and caps (2 excerpts x 1500, under 7.5 KB) tested; headlines and news lanes tested; news dedupe, freshness windows, related headlines, local query, weather tomorrow tested.
- Eval on the Pi, old vs new, 15 search questions + 2 routed; table below.
- ruff, ruff format, mypy (sdk, runtime, watch, search, news), all suites green.
- Docs: `skills/search.md`, `skills/news.md`, `observability.md`, `skills/index.md`, `skills/README.md`, persona docs, persona.yaml, this entry.
- Mario's walkie smoke after merge: "¿hay pico y placa hoy?", "¿quién es el presidente?", "¿cómo quedó Millonarios?", "noticias de Villavicencio", "¿va a llover mañana?".

### Eval (Gate 3): `scripts/search_eval.py` on the Pi, three rounds, 2026-10-13 evening

Old code (main) and new code interleaved, same machine, same questions. Verdicts: regex on the tool output, then read by hand (euro, fuel, cinema have no regex; "partial" = half the answer, e.g. the winner without the score). Each cell is round 2 / 3 / 4 (round 1 used the leaky queries and loose regexes the critic flagged and is not counted).

| case | expected fact | old | new |
|---|---|---|---|
| match | Colombia won 2-1 (women's friendly, 13 Oct) | FAIL / no / yes | partial / partial / partial |
| president | Abelardo de la Espriella | yes / FAIL / yes | yes / yes / yes |
| dollar | TRM 13 Oct: 3.194,44 | no / no / no | yes / yes / yes |
| age | Shakira: 49 | yes / yes / yes | yes / yes / yes |
| death | Piero died 7 Oct, aged 81 | yes / yes / yes | yes / yes / yes |
| holiday | Next festivo: Mon 2 Nov | yes / no / no | no / yes / yes |
| tv | Millonarios kick-off 8:30 p.m. today | no / no / no | no / no / no |
| recipe (persona: no search needed) | Ajiaco: guascas, papa criolla | no / yes / no | yes / yes / yes |
| health | Stomach ache: doctor if strong | yes / yes / no | no / no / no |
| medication | Ibuprofen + losartan interact | yes / yes / yes | yes / yes / yes |
| pope | Lung cancer diagnosed after surgery | yes / no / no | yes / yes / yes |
| quake | Magnitude 5.0 today | no / no / no | yes / yes / yes |
| cinema | A film showing today in Villavicencio | no / no / no | no / no / no |
| plates | Tuesday: plates 7 and 8 | yes / no / no | yes / yes / yes |
| status | Nairo Quintana retired | no / no / FAIL | yes / yes / yes |
| euro (held out) | Euro in pesos for 13 Oct | yes / partial / yes | partial / partial / partial |
| mayor (held out) | Alexander Baquero | yes / yes / yes | yes / yes / yes |
| fuel (held out) | Gallon price, October 2026 | no / yes / yes | yes / yes / yes |
| weather tomorrow (get_weather) | tomorrow with rain chance | no / no / no | yes / yes / yes |
| news today (get_news) | today's top stories | yes / yes / yes | yes / yes / yes |

**The 15: old 18/45 (40%), new 32/45 (71%) plus 3 partial. Old timed out 3 times in 45 (its 4 s cap, the production failure); new never. Held out: old 7/9, new 6/9 plus 3 partial (no gain; the euro is the loss: old twice found a page with today's rate, new found last Friday's, labelled with its date). Latency per search: median 2.2 s old, 2.8 s new; p75 2.9 / 3.5 s; max 4.0 (a timeout) / 6.2 s (a weak fallback that tried all three backends). Median output 2.1 KB old, 4.3 KB new.** Round 1 (before the critic, leaky queries) read 6/15 old and 13/15 new.

Misses on the new side: cinema (JavaScript listing pages), stomach ache (every backend returned Pinterest/TikTok/unrelated pages; the persona's careful own advice, already seen in production, is the fallback), the match score (headlines say Colombia won, not 2-1), the kick-off time (no page with today's time came back without naming the opponent). Engines vary run to run (holiday: old hit in round 2 only, new in rounds 3 and 4).

### Tests (Gate 3)

- Search: 124 (was 34): `test_provider.py` (fallback, weak responses, deadline, error mapping, strict, news lane), `test_pages.py` (fixtures, caps, dates, IDF and date boost, furniture phrases, wrapper guard, bad URL, private hosts), `test_headlines.py`, `test_skill.py` (shape and caps, excerpts and budget, ranking, both news lanes and the grace, failures with attempts, lane cancellation, cache incl. degraded answers, breaker, descriptions, privacy, child-safe).
- News: 32 (was 18): item shape and related headlines, dedupe, 24 h vs 72 h windows, local query, weather tomorrow, hashed query in logs.
- Runtime 1194 green; ruff, ruff format, mypy clean (lxml added to the untyped-imports override).

### Docs touched (Gate 4)

`docs/skills/search.md` (rewritten: pipeline, measured backends, excerpts, config, events, limits), `docs/skills/news.md`, `docs/observability.md` (web search section), `docs/skills/index.md`, `docs/skills/README.md`, `docs/personas/{abuelos,buddy,chief,librarian}.md`, `server/personas/abuelos/persona.yaml` (es/en/fr), skill READMEs and CHANGELOGs (both 0.2.0), root `pyproject.toml` (mypy override). `CLAUDE.md` still says "DuckDuckGo via ddgs" for the search skill: left for Mario (agents don't edit it).

### Ship (Gate 5)

Branch `t148-search-news` (worktree), on base 5f699660: 6e1469b5 (search), 816d9e6c (news), f800de9e (persona, docs, eval), 012b745e (critic round). Not merged, not pushed, not deployed. Eval JSON (every tool output per question and round) was kept out of the repo.

**For Mario to decide**: (1) safe search for Buddy (the kids persona): ddgs's working engines ignore it, so `strict` filters little; the skill now withholds page text for strict personas, but titles and snippets are unfiltered; a filtered provider would be a separate item. (2) Output per search roughly doubled (about 4.3 KB median), more input tokens per search on the Realtime bill; the caps hold it under 7.5 KB. (3) The urgent-symptom line in the persona ("pide ayuda ya", offer to call a contact) was added as standard practice; reword or drop it if it isn't how you want grandpa spoken to.

**Lessons**: one eval run lied in both directions: engines answer differently each time, a loose regex ("8:30" matching "18:30") and leaky queries (the opponent's name, the pope's) inflated the first 13/15. Three rounds with fixed queries said 71% vs 40%. The critic paid for itself on the first finding: one malformed result URL would have turned every search for that query into "algo no fue bien".

## T1.49: The walkie says its connection state with sounds

**Status**: `in_progress` (2026-10-13): on the walkie since the night of 2026-10-13 (firmware 3c3ba563, with the startup light F-0034); left: the daytime board run with drops (blip, server drop, Wi-Fi drop, park) and Mario's ear · **Effort**: S server side (assets only); the firmware side is F-0033

**Problem** (Mario, 2026-10-13): with no internet the walkie is silent until he presses, and then says it has no connection. "Just pressing and being told is not good enough." Grandpa can't see a light; he can't tell off from searching from ready.

**Design.** Four sounds the walkie plays itself (no server when offline), rendered by `scripts/synth_sounds.py` into `server/personas/_shared/sounds/device/` and embedded by the firmware's tooling, at the cue level (-19 LUFS momentary max, T1.47):

- `startup`: power on. G6 C7 E7 glass tones 60 ms apart with a pure E8 sparkle, almost dry. Mario turned down the first bell arpeggio and asked for "something out of Black Mirror, extremely happy, minimalistic"; of three takes he picked this one ("B is especially good").
- `searching`: one short tick, repeated while it looks for Wi-Fi and the server, 6 dB under the cue level because it repeats.
- `connected`: a brighter rise once the server answers.
- `lost`: a falling, softer phrase when the connection drops.
- Pressing while searching gets the existing "no tengo conexión" prompt; otherwise no speech.

Behaviour on the walkie (sent to the firmware session; standard practice, Mario didn't object): startup at power on, then searching; the tick every ~3 s for 2 min, then every 30 s, stopping after about 17 min in all (battery, no beeping all night); `connected` on the first connection after power on and after a `lost`, never for a silent blip; `lost` only after 20 s unreachable (deploys and restarts take 3 to 20 s), then the searching schedule from its start; 22:00-07:00 the tick, `lost` and `connected` stay silent unless he presses or powers it on; logs `sound.status kind=...`. Shares the "server unreachable" state with F-0032's phone alert.

**Palette v2, dropped.** After the startup, Mario asked to rethink every sound in its style. A pure-glass v2 of all ten (device sounds plus the content cues) was rendered; his verdict: "you overdid it, now they all sound too similar". He chose to ship the approved set first (glass startup, bell searching/connected/lost, content cues unchanged) and redesign later: T1.50.

**Definition of Done**
- `synth_sounds.py` renders the four device sounds; each at the cue level (searching 6 dB under), true peak under the ceiling, no clicks (spectrogram checked). Done.
- `docs/sounds.md`: device sounds and the glass primitive documented. Done.
- Firmware (F-0033): the four sounds embedded, the state machine above with unit tests and a critic round (a crash-looping server no longer ticks forever: an outage ends only after 2 min connected), flashed at Mario's request the same night (3c3ba563). Left: the board run with drops in a daytime idle window (at night the ticks, `lost` and `connected` are held by design).
- Mario hears them on the walkie: power on, Wi-Fi off and on, server restart.

**Commits**: e8117a96 (four device sounds), a4e1cffc (glass startup, docs, this entry), 11d9ad4a (shipped set, T1.50 queued), 8af68aa4 (loudness test for the device sounds); firmware 3c3ba563 (F-0033).

**Startup light** (F-0034): the 7-LED ring is mirror-symmetric left/right in HX-1 (LED 0 position points down; the data chain starts at the 102.9 deg LED and runs clockwise, fixed in 8d3b30bf after a sideways first Orbit). Mario first picked "Orbit" in warm white from a mockup of the back, then changed direction on the walkie (2026-10-14, with the firmware session): startup is "Rainbow", every LED its own hue turning, the whole ring at once, from when the chime reaches the speaker; talking varies the intensity of all seven together (white, faint when silent, full at loud speech; a steady glow when offline). The four LEDs behind the holders get 3x gain (`led gain`). Same boot rule as the sound; a third of the brightness at night. Firmware b24eb0e9 (feature f41854bd).

## T1.50: Redesign the sound palette (Wii-inspired, many takes per sound)

**Status**: `queued` (2026-10-13, after T1.49 ships) · **Effort**: M (synth work plus Mario's listening rounds)

**Problem** (Mario, 2026-10-13): hearing the glass startup made him want to rethink every sound. The all-glass v2 was too uniform: "they all sound too similar". He wants inspiration from the Wii's sounds, many proposals for each sound, and they may be richer and more complex than the minimal glass tones.

**Approach**: per sound (the ten in `DEVICE_PALETTE` and `PALETTE`), several distinct takes that vary timbre, gesture and length, not just pitch; a listening sheet per round; keep the cue level, the vocal-band rule for cues before speech, and each sound telling its role apart by ear. The glass `startup` stays unless he says otherwise.
