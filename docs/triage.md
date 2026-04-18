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

**Status**: queued · **Task**: #86 · **Effort**: large (2 weeks: spec + impl + 3-skill refactor)

**Problem.** Every personal-content skill reinvents fuzzy-search + prompt-context.
Audiobooks does `SequenceMatcher` + `prompt_context()` dump (top 50). Radio does
`_station_choices()`. News does its own dict cache. Future skills (contacts,
recipes, music files, voice notes, photos) will reinvent it again. The framework's
core differentiator is "personal content + LLM dispatch" and the SDK provides zero
help with the personal-content half.

**Why it matters.** Highest-leverage SDK addition. Collapses code in 3 existing
skills. De-risks the next 5. Without it, every new personal-content skill
re-imports fuzzy-match.

**Proposed solution.** `ctx.catalog(name)` returns a `SearchableCatalog` backed by
SQLite FTS5 (no extra deps; FTS5 ships with sqlite3) with Spanish accent-folding
tokenizer. Two delivery modes for the LLM-side handoff:

```python
catalog = ctx.catalog("audiobooks")
await catalog.upsert(id="brave-new-world", fields={"title": "...", "author": "..."}, payload={...})
hits = await catalog.search("mundo feliz", limit=5)
prompt_lines = catalog.as_prompt_lines(limit=50)            # small catalogs: dump in system prompt
search_tool  = catalog.as_search_tool("search_audiobooks")  # large catalogs: expose tool to LLM
```

The dual-mode matters: 19 books → dump in prompt; 10,000 music files → search-on-
demand tool. Same primitive.

**Spec questions to answer first**: lifecycle (rebuilt on `setup()` vs
persistent), multi-field weighting (title vs author), invalidation on file-watcher
events, where the SQLite file lives (per-skill namespaced under `data_dir`).

---

## T1.2 — `ProactiveTurn` spec (`docs/proactive-turns.md`)

**Status**: queued · **Task**: #87 · **Effort**: 1 week of design (spec only, no code)

**Problem.** Current `TurnCoordinator` assumes user-originated turns. There is no
entry point for "framework wants to start speaking now." Every v∞ feature on the
roadmap (reminders, inbound messages, memory recall, companionship-mode greetings)
requires this primitive.

**Why it matters.** Existential for the framework dream past v2. Adding it as a
5th `SideEffect` kind is the wrong shape — it inverts the coordinator's causality
and the current state machine cannot handle it.

**Spec must answer**:

1. **Trigger sources**: time-based (cron-style), external events (webhook, MQTT),
   internal (audiobook-end announcement is already a proto-proactive turn —
   formalize the pattern).
2. **Interrupt policies** (per-notification, declared by the skill):
   - `now` — cancel current_media_task immediately, speak
   - `defer` — queue, speak when current activity naturally ends
   - `chime+defer` — earcon now, hold speech until user PTTs to ask
   - `now_if_idle` — speak immediately only if state is idle, else defer
3. **Coordinator state model**: how does `current_media_task` arbitration work?
   Who owns `model_speaking` during proactive speech?
4. **SDK surface**: `await ctx.notify(text, *, interrupt_policy="defer", expires_after=None, dedup_key=None)`
5. **Wire protocol**: new server-initiated `assistant_turn_start{reason: "proactive"}`
   message. Client behavior on receive (PTT during proactive should still interrupt).
6. **Background-task supervision**: skills running schedulers/listeners need
   framework-managed task lifecycle with crash logging + restart.

**Output**: `docs/proactive-turns.md` written before any line of T1.4 code.

---

## T1.3 — Coordinator refactor (extract `SpeakingState` / `MediaTaskManager` / `TurnFactory`)

**Status**: queued · **Task**: #88 · **Effort**: ~2 weeks · **Risk**: needs T2.4 (integration tests) for safe verification — currently deferred; see decision below

**Problem.** `coordinator.py` is 586 LOC juggling PTT lifecycle, model deltas, tool
dispatch, six side-effect kinds, atomic interrupts, completion-prompt synthesis,
synthetic turn injection, and `model_speaking` flag ownership transfer. Adding
ProactiveTurn (T1.4) into this without restructuring will produce code that's
unmaintainable.

**Why it matters.** Must precede T1.4. The "ownership transfer of `model_speaking`"
gymnastics in `_consume_audio_stream` is already a code smell; proactive turns
will compound it.

**Proposed solution.** Extract three internal collaborators; `TurnCoordinator`
becomes the thin orchestrator:

- `SpeakingState` — owns `model_speaking` flag transitions
  (factory_audio takes ownership → model reclaims → completion_prompt re-owns →
  proactive will own too)
- `MediaTaskManager` — owns `current_media_task` lifecycle (start, cancel,
  on_complete, arbitration when proactive interrupts)
- `TurnFactory` — creates Turns (user-originated today, completion-prompt synthetic
  today, ProactiveTurn future)

**Risk note.** Refactor without behavior change is hard to verify by manual smoke
testing. T2.4 (integration smoke tests against real OpenAI) is currently deferred.
**Decision needed before starting**: pull T2.4 forward, OR proceed with heavy
manual smoke + git-rollback safety. Recommendation: pull T2.4 forward.

---

## T1.4 — `ProactiveTurn` implementation

**Status**: blocked by T1.2 (spec) + T1.3 (coordinator refactor) · **Task**: #89 · **Effort**: ~3 weeks

**Problem.** See T1.2 for full context.

**Why it matters.** Unblocks reminders, inbound messaging, memory recall,
companionship-mode greetings — every roadmap feature past v2.

**Implementation sketch** (per the T1.2 spec):

- `SessionManager.notify(text, interrupt_policy=...)` entry point
- Coordinator extensions for proactive-turn arbitration (uses T1.3's
  `MediaTaskManager`)
- Wire protocol: `assistant_turn_start{reason: "proactive"}` server-initiated
  message. Browser dev client + future ESP32 firmware both handle.
- Supervised background-task pattern in SDK so reminder schedulers and inbound-
  message listeners survive crashes.

---

## T1.5 — Real LLM summarization on reconnect

**Status**: queued · **Task**: #90 · **Effort**: ~100 LOC (1-2 days)

**Problem.** Today's `disconnect(save_summary=True)` injects raw "last 20 transcript
lines" into the next session's system prompt. After 22 reconnects in a 20-hour
audiobook session, the model is reading lines that may have nothing to do with
current state. "The assistant forgets what we were just doing" is the most jarring
possible failure for an elderly user who relies on continuity.

**Why it matters.** OpenAI's 30–60 min forced session reset already hits us
multiple times per long listening session. Without real summarization the
continuity loss is invisible until the user notices.

**Proposed solution.** On `disconnect(save_summary=True)`:

1. Fetch last N transcript lines from `Storage` (already there)
2. Call `gpt-4o-mini` (chat completion, NOT realtime) with system prompt:
   "Resume esta conversación en 3 frases concisas para que un asistente que se
   reconecta pueda continuar sin perder contexto."
3. Store summary string in `Storage.save_summary(text)`
4. On next `connect()`, inject summary into system prompt before
   catalog/tool context

**Cost.** ~$0.001 per disconnect (mini chat completion is cheap). Latency fits
into the natural ~2s reconnect window — no extra user-facing latency.

**Edge cases.** First connect (no prior summary): skip injection. Summary becomes
stale during long idle: regenerate-on-stale is probably fine for v1.

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

**Status**: queued · **Task**: #95 · **Effort**: ~1 week (pulled from D2 on 2026-04-18) · **Blocks**: T1.3

**Problem.** Voice-first project, text-first test surface. Audio regressions
slip through. The single biggest risk on the active list — T1.3 coordinator
refactor — has no automated test net. Manual browser smoke is the only thing
catching subtle regressions today.

**Why it matters.** T1.3 is "refactor without behavior change". The way to verify
no behavior change is integration tests that exercise the full receive-loop ↔
coordinator ↔ skill ↔ side-effect path. Without these, T1.3 is a leap of faith.

**Proposed solution.** Two layers:

1. **Recorded-fixture replay** (cheap, runs in CI, no API key required):
   - Capture or hand-author JSONL fixtures of OpenAI Realtime sessions
     (server-event sequences for representative scenarios: simple turn,
     audiobook play+interrupt, news fetch, radio start+stop, error responses).
   - `FixtureReplayProvider` implements `VoiceProvider` interface, plays the
     fixture as if it were the real OpenAI WebSocket.
   - Tests assert downstream behavior: state transitions, side-effect
     dispatching, audio frame ordering, summary persistence.

2. **One happy-path live test** (gated behind `HUXLEY_INTEGRATION=1`, runs
   nightly, requires API key):
   - Spin up the full server, simulate a client, do a `play_audiobook`
     round-trip with real OpenAI, assert audio frames flow + tool dispatched
     - cancel works.
   - Catches breaking changes in OpenAI's API shape that fixtures wouldn't
     catch.

Couples to T1.3: the refactor needs replay coverage of the coordinator's
existing behavior to verify the refactor preserves it.

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

## D3 — Tier 3 polish (4 items)

| Task | Title                                                 | Effort   |
| ---- | ----------------------------------------------------- | -------- |
| #96  | Add `prompt_context()` to Skill Protocol with default | 30 min   |
| #97  | Auto-namespace tool names (`<skill>.<tool>`)          | ~50 LOC  |
| #98  | Strip remaining `AbuelOS` hardcoded refs              | 30 min   |
| #99  | Allow second WS client as monitor in dev              | ~4 hours |

**Revisit when**: any session has spare cycles, OR the first community skill is
about to land (#97 becomes urgent), OR ESP32 hardware arrives (#99 becomes
urgent).

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
