# Writing a Skill

A Huxley skill is a Python package that teaches the agent to do something new — play music, control lights, send messages, query an API. This document is for skill authors.

For the conceptual model, see [`../concepts.md`](../concepts.md). For a full worked example, see [`audiobooks.md`](./audiobooks.md). For an honest map of which skill ideas fit the framework today and where the real limits are, see [`../extensibility.md`](../extensibility.md).

> **SDK status**: the Huxley SDK (`huxley_sdk`) lives at `packages/sdk/`. Skill authors import from it: `from huxley_sdk import Skill, ToolDefinition, ToolResult, SkillContext`. The two first-party skills (`audiobooks`, `system`) live under `packages/skills/<name>/` and are loaded via `huxley.skills` entry points exactly like a third-party skill would be. Their layout is the canonical reference for the structure described below.

## The Skill protocol

Skills are structurally typed (PEP 544 `Protocol`), not nominal subclasses. Implement the interface and the registry accepts you — no inheritance required.

```python
from huxley_sdk import Skill, ToolDefinition, ToolResult

class MySkill:
    @property
    def name(self) -> str: ...

    @property
    def tools(self) -> list[ToolDefinition]: ...

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult: ...

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...
```

- **`name`** — unique identifier for logging and registry lookups.
- **`tools`** — list of tool schemas exposed to the LLM (see below).
- **`handle`** — dispatch entry point. Route on `tool_name`.
- **`setup`** / **`teardown`** — lifecycle hooks. Load catalogs in `setup`, persist state in `teardown`.

## Anatomy of a tool definition

```python
ToolDefinition(
    name="search_audiobooks",
    description="Searches the user's local audiobook library by title or author.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search text (title, author, or partial name)",
            }
        },
        "required": ["query"],
    },
)
```

- **`name`** — globally unique across all skills enabled in a persona. Registration fails loudly on collisions.
- **`description`** — **written in the persona's language**. The LLM uses it to decide when to call the tool. Vague descriptions cause bad dispatch; precise descriptions are worth their weight.
- **`parameters`** — standard JSON Schema. The LLM fills these from conversation context.

### Multilingual descriptions

A skill that supports multiple personas may need to expose its tool descriptions in multiple languages. The convention (still being designed): the skill receives the persona's `language` in its context at `setup()` and returns the appropriate description set. For now (single-language Huxley deployments), hardcode the description in the language your target persona uses.

## Returning results

Tools return a `ToolResult`:

```python
ToolResult(
    output=json.dumps({"results": [...], "message": "Found 3 books"}),
)
```

- **`output`** is JSON text sent back to the LLM as the function-call output. The LLM narrates it to the user.
- **`side_effect`** _(optional)_ is a `SideEffect` the framework runs around the model's response. Skills with no side effect leave it `None`. Available kinds:
  - `AudioStream(factory, on_complete_prompt?, completion_silence_ms?, content_type?)` — long-running PCM stream (audiobook playback). Factory fires at the turn's terminal barrier. `content_type` defaults to `ContentType.NONMIXABLE` (spoken-word — an injected turn hard-cancels the stream). Set to `ContentType.MIXABLE` for music / ambience so the framework ducks it under an injected turn instead of cutting it. Today all shipped content is NONMIXABLE; MIXABLE is wired end-to-end and ready for the first music skill.
  - `PlaySound(pcm)` — short one-shot chime that plays just before the model's response audio (used by info tools that want a sonic intro — e.g. news chime). See [`docs/sounds.md`](../sounds.md).
  - `CancelMedia()` — stop the running media task immediately (pause/stop tools).
  - `SetVolume(level)` — forward a volume change to the client.

### Info tools vs side-effect tools

| Kind                         | `side_effect`          | Examples                                     | Framework behavior                                                                                                                            |
| ---------------------------- | ---------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **Info (no chime)**          | `None`                 | `search_audiobooks`, `get_current_time`      | Coordinator requests a follow-up response so the model can narrate the result. Multi-round chained turn.                                      |
| **Info (with chime)**        | `PlaySound(pcm)`       | `get_news`                                   | Same as above + the chime is sent right after `request_response()`, hitting the WebSocket ahead of the model's audio (FIFO).                  |
| **Audio-stream side-effect** | `AudioStream(factory)` | `play_audiobook`, `audiobook_control` (seek) | Coordinator latches the AudioStream; after the model's terminal `response.done`, the factory fires and streams PCM through the audio channel. |

The model is told (via the tool description) to **pre-narrate** the side effect — e.g. _"Putting on the book for you."_ or _"a ver, un momento"_ — _before_ calling the tool. The framework guarantees the narration plays before the factory does.

### Loading sound files

The SDK ships `huxley_sdk.audio.load_pcm_palette(directory, roles)` for skills that bundle WAV files (chimes, tones). It expects PCM16 / 24 kHz / mono and silently skips wrong-format files — wrong-format would play as garbage through the channel anyway. Both audiobooks (`book_start.wav`, `book_end.wav`) and news (`news_start.wav`) use it.

### The factory closure pattern

For tools that compute a parameter at dispatch time (e.g. a rewound position), capture the value in a closure rather than persisting it eagerly:

```python
def _build_factory(self, book_id: str, path: str, start_position: float):
    async def stream():
        bytes_read = 0
        try:
            async for chunk in self._player.stream(path, start_position=start_position):
                bytes_read += len(chunk)
                yield chunk
        finally:
            elapsed = bytes_read / BYTES_PER_SECOND
            await self._storage.save_audiobook_position(
                book_id, start_position + elapsed,
            )
    return stream
```

Why: if the turn is interrupted before the framework invokes the factory, the closure is never executed and storage stays at the last actually-played position. The skill never writes the new position eagerly during dispatch. This gives interrupt-atomicity without a transaction model.

## Persona constraints — what your skill should respect

Some personas declare behavioral constraints (see [`../concepts.md#constraint`](../concepts.md#constraint)). Skills targeting those personas should honor them. The current constraint set:

### `never_say_no`

If the persona enables `never_say_no`, your skill must not return dead-end negatives. Every tool response must include a `message` field with a constructive alternative or a clarifying question.

❌ Bad:

```python
return ToolResult(output=json.dumps({"error": "Not found"}))
```

✅ Good (in the persona's language):

```python
return ToolResult(output=json.dumps({
    "results": [],
    "message": "I don't have that exact book. The closest match is 'Cien años de soledad'. Would you like that?",
    "closest_match": {"id": "...", "title": "Cien años de soledad"},
}))
```

The LLM reads this and offers the alternative naturally. See [`../personas/abuelos.md`](../personas/abuelos.md) for the canonical worked example of `never_say_no` in production.

### `confirm_destructive`

If the persona enables `confirm_destructive`, any tool that performs an irreversible action should either:

- Take an explicit `confirmed: true` parameter, OR
- Have a separate "preview" tool that returns "what would happen if I did this," letting the model ask before calling the real action.

### `child_safe`

If your skill could surface adult or profane content (search results, news headlines, etc.), apply filtering when this constraint is active.

### `echo_short_input`

Prompt-level only — no skill action required. The model echoes very short inputs back to the user before acting. Your tool descriptions should be precise enough that even a single-word input like _"libros"_ resolves unambiguously.

### `confirm_if_unclear`

Prompt-level only — no skill action required. The model asks one clarifying question if it isn't confident it understood the request before calling a tool. Your tool descriptions should document unambiguous trigger phrases so the model can act directly on clear input.

### Forward-compatibility

A skill that doesn't know about a future constraint just won't handle it specially. The framework injects the matching system-prompt language regardless, so the LLM can still steer correctly. Skills opt in to constraint-aware behavior; they don't have to.

## Using a Catalog

If your skill ships personal-content items (audiobooks, radio stations, contacts, recipes, anything the user owns and refers to by name), use the SDK's `Catalog` primitive instead of rolling your own fuzzy match + prompt formatter. It's the framework's headline ergonomic for the personal-content + LLM-dispatch pattern.

```python
from huxley_sdk import Catalog, Hit, Skill, SkillContext, ToolResult

class RecipesSkill:
    @property
    def name(self) -> str:
        return "recipes"

    async def setup(self, ctx: SkillContext) -> None:
        self._catalog = ctx.catalog()
        for recipe in scan_recipes(ctx.persona_data_dir / "recipes"):
            await self._catalog.upsert(
                id=recipe["id"],
                fields={"title": recipe["title"], "cuisine": recipe["cuisine"]},
                payload={"path": recipe["path"], "duration_min": recipe["duration_min"]},
            )

    async def handle(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "play_recipe":
            hits = await self._catalog.search(args["query"], limit=1)
            if hits and hits[0].score > 0.5:
                top = hits[0]
                return ToolResult(output=json.dumps({"title": top.fields["title"]}))
            return ToolResult(output=json.dumps({"error": "no_match"}))

    def prompt_context(self) -> str:
        return self._catalog.as_prompt_lines(
            limit=50,
            header="Recetas disponibles",
            line=lambda h: f'- "{h.fields["title"]}" ({h.fields["cuisine"]})',
        )
```

**What you get for free**:

- **Accent-insensitive matching** for Spanish (`"garcia"` matches `"García"` cleanly). Symmetric — applied on both sides at insert and query.
- **Deterministic scoring** via `SequenceMatcher` ratio across fields, max-across-fields. Same algorithm the audiobooks skill used pre-Catalog, so refactoring onto Catalog is drop-in.
- **Consistent `as_prompt_lines` output** across every personal-content skill — the LLM sees the same shape from `audiobooks`, `recipes`, `contacts`, etc., so its prompt-pattern recognition transfers.
- **Stable API** for a future SQLite FTS5 backend swap when a skill genuinely needs persistence or 10k+ scale.

**Confidence threshold lives in the skill, not the Catalog.** `catalog.search()` returns scored hits; the skill decides what counts as "good enough" (audiobooks uses `> 0.5` for resolve, `> 0.3` for the broader search-results listing). This separation lets each skill tune its own UX without bending the primitive.

**Catalog is for skill-owned data**, not framework state. Don't put cached HTTP responses or session-scoped state in a Catalog — those have different lifecycle and access patterns. The news skill's TTL cache, for example, is a plain dict and stays a plain dict.

See [`audiobooks.md`](./audiobooks.md) for a full worked example.

## Optional: `prompt_context()` for baseline awareness

Some questions don't need a tool call — they need the LLM to already know. _"What books do you have?"_ is the canonical example: if the catalog is already in the session prompt, the LLM can answer immediately without round-tripping through `search_audiobooks`.

Skills that want to contribute baseline context to every session prompt implement `prompt_context()`. The simplest implementation is `return self._catalog.as_prompt_lines(...)` (see "Using a Catalog" above); skills that don't have a Catalog can build the string by hand.

**How it's wired**: at session connect time, the framework iterates registered skills and collects any non-empty `prompt_context()` strings, appending them to the system prompt before sending `session.update`.

**In the Skill protocol with an empty default** — skills that don't override it contribute nothing.

**Scaling rule**: keep each skill's context under a few hundred tokens. For collections that would blow past that (thousands of items), the framework will eventually grow a search-tool delivery mode on Catalog; today, just trim with `limit=N`.

**When _not_ to use it**: don't dump state that changes frequently — the context is only refreshed on session connect, not mid-conversation.

## Persistent state — `ctx.storage`

Skills that need data to survive across sessions or server restarts use `ctx.storage`, a per-skill KV adapter namespaced on the skill's name (so two skills can both store `last_id` without colliding). The framework wraps SQLite in WAL mode under the hood — skills see only a flat string-valued KV API.

```python
class SkillStorage(Protocol):
    async def get_setting(self, key: str, default: str | None = None) -> str | None: ...
    async def set_setting(self, key: str, value: str) -> None: ...
    async def list_settings(self, prefix: str = "") -> list[tuple[str, str]]: ...
    async def delete_setting(self, key: str) -> None: ...
```

**Composite keys are the vocabulary for richer data.** The API is intentionally flat — no nested types, no schema. Skills that store families of entries use colon-separated keys:

```python
# Timer persistence (skills/timers):
await ctx.storage.set_setting(f"timer:{timer_id}", json.dumps({...}))

# Audiobook progress (skills/audiobooks uses a dedicated table, but a future
# skill without migration access would do):
await ctx.storage.set_setting(f"position:{book_id}", str(seconds))
```

**`list_settings(prefix)`** enumerates every `(key, value)` whose key starts with `prefix`. Keys are returned WITHOUT the skill's namespace prefix. Use this for restore-on-boot patterns where `setup()` needs to see every `timer:*` entry.

```python
async def setup(self, ctx: SkillContext) -> None:
    for key, value in await ctx.storage.list_settings("timer:"):
        entry = json.loads(value)
        # ... reschedule the task via ctx.background_task
```

**`delete_setting(key)`** removes an entry outright. Prefer this over `set_setting(key, "")`: an empty-string tombstone would leak into every `list_settings` caller and force filter logic at every read. SQL `LIKE` queries escape `%` and `_` in the caller's prefix so keys containing those characters don't accidentally glob.

**Schema versioning**: if you persist JSON, include a `"v": 1` field from day one. Shape changes are inevitable; a version byte costs nothing and makes the first migration tractable instead of best-guess.

**When _not_ to use `ctx.storage`**:

- Large binary blobs — it's a string KV, not a file system. Use `ctx.persona_data_dir` for files.
- High-frequency writes — every `set_setting` is a SQLite commit. Fine for user events ("bookmark position on turn end"), not fine for per-frame state.

## Proactive speech — `ctx.inject_turn`

> ℹ️ **Shipped (Stages 1c.3 + 1d).** Today's API supports the queue
>
> - dedup behaviors below. **Not yet shipped**: `expires_after` TTL,
>   `InjectedTurnHandle` with `.wait_outcome()` for outcome-driven
>   retry, multi-urgency tiers (one preempt level today; defer-to-next-
>   idle is a future addition).

Some skills need to speak without the user asking first: a medication reminder fires at 9am; a message from family arrives; an appointment is 30 minutes away. The framework's turn loop normally only runs on user PTT, but `ctx.inject_turn` lets a skill inject a synthetic turn from outside.

```python
# Inside a skill's setup or background task:
await self._ctx.inject_turn("Es hora de la pastilla de las nueve.")
```

**Behavior**:

- **Idle (no turn in progress)**: fires immediately. Any playing content stream (audiobook, radio) is preempted via FocusManager — the LLM narrates the prompt in persona voice, then the framework releases focus. The content stays stopped (a future stage will add "resume or drop" based on the stream's patience).
- **Busy (a user or synthetic turn is in progress)**: the request is **queued**. It drains automatically when a turn ends without spawning a content stream. This protects against dropped reminders when the user happens to PTT at the moment a timer fires.

**Dedup** (optional `dedup_key`):

```python
await self._ctx.inject_turn(
    "Es hora de la pastilla de las nueve.",
    dedup_key="med_9am_2026-04-19",
)
```

`dedup_key` is an opaque string identifying the logical event. If a queued entry already has the same key, the new request **replaces** it (last-writer-wins). If the same key is currently firing (DIALOG already acquired with that key), the new request is **silently dropped** — repeating the same reminder while it's mid-narration would just create a confused stack. Skip `dedup_key=None` (default) to bypass dedup.

**Priority** (optional `priority`):

```python
from huxley_sdk import InjectPriority

# Social / routine: wait for a quiet moment (default is NORMAL).
await self._ctx.inject_turn(
    "Carlos te escribió.",
    dedup_key="msg_12345",
    # priority=InjectPriority.NORMAL — the default
)

# User-set timer: preempt an audiobook, but wait behind an active call.
await self._ctx.inject_turn(
    "Tu temporizador de 10 minutos terminó.",
    dedup_key="timer_42",
    priority=InjectPriority.BLOCK_BEHIND_COMMS,
)

# Unconditional urgent: preempt everything including live calls.
# Reserved for true top-severity (rare). Interrupts a Telegram call.
await self._ctx.inject_turn(
    "¡Alarma de incendios!",
    priority=InjectPriority.PREEMPT,
)
```

Three tiers, ordered by urgency:

- **`NORMAL`** (default) — queues behind everything. Drains when a turn ends WITHOUT a pending content stream AND no active COMMS claim. Right for social reminders, inbound-message announcements, chatter that can wait for the next quiet moment.
- **`BLOCK_BEHIND_COMMS`** — preempts CONTENT (audiobooks pause via patience and resume after; radio ducks or pauses per `ContentType`), but queues behind COMMS claims (active calls). Fires at claim-end. Right for the vast majority of "urgent enough to interrupt the book" cases — cooking timers, medication reminders, doorbell announcements, severity-tiered notifications. Interrupting a live phone call for these would be wrong UX.
- **`PREEMPT`** — preempts everything below DIALOG. A live Telegram call gets its claim ended with `PREEMPTED`; the reminder narrates. Use sparingly — reserved for genuinely top-severity events (fire alarm, evacuation, similar). Most skills that reach for this actually want `BLOCK_BEHIND_COMMS`.

None of the tiers barge into a user mid-speech. The queue always waits for turn-end; priority only decides content-vs-queue and claim-vs-queue at that boundary, never the user's right to finish a sentence. If a user is speaking when an alert fires, it queues and drains at the end of whatever turn follows (including any tool-call follow-up rounds).

**What `prompt` should contain**: an instruction for the LLM, not the literal words to speak. The persona prompt + the persona's voice transform your instruction into the actual utterance. For a medication reminder, `"Es hora de la pastilla de las nueve"` is fine — the LLM narrates that verbatim or close to it; for something needing more context, `"Dile al usuario que su hijo Carlos mandó un mensaje que dice '<text>', pregúntale si quiere escucharlo"` works too.

**Future surface (Stage 1d, not yet shipped)**:

```python
# Not yet available — preview of Stage 1d shape:
handle = await self._ctx.inject_turn(
    "Es hora de la pastilla de las nueve.",
    dedup_key="med_9am_2026-04-18",
    expires_after=timedelta(hours=2),
)
outcome = await handle.wait_outcome()  # ACKNOWLEDGED | DELIVERED | EXPIRED | PREEMPTED | CANCELLED
if outcome != TurnOutcome.ACKNOWLEDGED:
    # Reschedule at higher urgency via your own background task
    ...
```

**Don't build retry into the call itself.** If your skill needs retry-until-acknowledged semantics (medication reminders), use your own scheduler (today: plain `asyncio.create_task`; Stage 3: `ctx.background_task`) to re-call `inject_turn` on a cadence you control. Stage 1d will expose `handle.wait_outcome()` so retry can be outcome-driven; until then, retry blind.

**Who narrates**: the LLM, in persona voice. Your `prompt` is the instruction (what to say), not the rendered speech. Same pattern as audiobook's `AudioStream.on_complete_prompt`.

### Blocking variant — `ctx.inject_turn_and_wait`

> ℹ️ **Shipped.** Use when you need to announce something and then immediately start audio bridging.

```python
await self._ctx.inject_turn_and_wait("Llamada de María, contestando.")
# returns only after the LLM finishes speaking
await self._ctx.start_input_claim(InputClaim(...))
```

`inject_turn_and_wait` fires the same injected turn as `inject_turn`, but **blocks until `response_done` fires** (the LLM has finished generating and all PCM has been sent to the client). This eliminates the hardcoded `sleep()` that would otherwise be needed between "announce the event" and "start audio bridging."

**Fallback**: if the coordinator is busy (a turn is already in progress), it falls back to a plain enqueue — same as `inject_turn`. The blocking guarantee only holds when the coordinator is idle. Design accordingly: call it from a background task where you control the timing.

**When to use it**: any skill that must announce an event and then immediately claim the mic or start an audio source. Without the wait, your bridged audio queues behind the announcement PCM still in the client's playback buffer, causing a perceptible delay. The `telegram` inbound-call flow uses this for exactly that reason.

**When not to use it**: routine proactive reminders. `inject_turn` (non-blocking) is correct for timers, news alerts, and anything that doesn't immediately follow up with an audio source.

## Supervised background tasks — `ctx.background_task`

> ℹ️ **Shipped (T1.4 Stage 3).** Use this instead of
> `asyncio.create_task` for any long-running work. The framework
> sees crashes, restarts within budget, and cancels everything at
> shutdown.

Skills that schedule proactive events or listen for external input need long-running tasks. Don't spawn `asyncio.create_task` directly — the framework can't see crashes and your scheduler will silently die.

```python
from huxley_sdk import BackgroundTaskHandle, PermanentFailure, SkillContext

class MySkill:
    async def setup(self, ctx: SkillContext) -> None:
        self._ctx = ctx
        # Long-running scheduler — auto-restart if it crashes.
        self._scheduler: BackgroundTaskHandle = ctx.background_task(
            "scheduler",
            self._scheduler_loop,
            on_permanent_failure=self._on_scheduler_dead,
        )

    async def _scheduler_loop(self) -> None:
        while True:
            due = await self._next_due()
            await asyncio.sleep(max(0, (due.when - now()).total_seconds()))
            await self._ctx.inject_turn(due.prompt)

    async def _on_scheduler_dead(self, failure: PermanentFailure) -> None:
        # Restart budget exhausted — surface to user / page on-call / etc.
        ...
```

**Signature**:

```python
ctx.background_task(
    name: str,                        # unique within the supervisor pool
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    *,
    restart_on_crash: bool = True,    # False for one-shot tasks (e.g. timers)
    max_restarts_per_hour: int = 10,  # rate limit before declaring permanent failure
    on_permanent_failure: Callable[[PermanentFailure], Awaitable[None]] | None = None,
) -> BackgroundTaskHandle
```

**What you get**:

- **Crash logs** via `aexception` (full traceback, `name`, `restart_count`).
- **Automatic restart** with exponential backoff (2s, 4s, 8s, ..., capped at 60s).
- **Permanent failure** when crashes exceed `max_restarts_per_hour` within a 1-hour window: a `dev_event("background_task_failed", ...)` fires for the client, and your `on_permanent_failure` callback (if provided) is invoked with a `PermanentFailure` dataclass. The supervisor then drops the task — your callback is the place to reschedule it differently or surface the failure.
- **Coordinated shutdown**: every supervised task is cancelled at framework shutdown. Skills don't need to track tasks for cleanup; the supervisor's pool owns lifecycle.

**One-shot vs long-running**:

- One-shot tasks (the timers skill is the canonical example) pass `restart_on_crash=False`. Restarting a fired-too-early reminder makes no sense — it would re-sleep for the original duration, fire late, and confuse the user. The cost of `restart_on_crash=False` is "a crashed timer is a lost reminder, logged once."
- Long-running tasks (scheduler loops, webhook listeners) keep the default `True` — they should survive transient errors.

**Pre-shutdown cancel**: if your skill wants to cancel a specific task before framework shutdown (a `cancel_timer` tool, say), hold the returned `BackgroundTaskHandle` and call `.cancel()`. Otherwise rely on the supervisor's bulk cancel.

## Client events — `ctx.subscribe_client_event`

> ⚠️ **Planned — not yet shipped.** The `client_event` protocol message
> already reaches the server (`AudioServer` logs it), but the skill-side
> `subscribe_client_event` / `emit_server_event` SDK surface is T1.4
> Stage 4 work. API below is the target shape.

Hardware buttons, sensor data, client-side state transitions — anything the client wants the server to know about beyond audio. Clients emit `{"type": "client_event", "event": "<namespaced-key>", "payload": {...}}`. Skills subscribe by key.

```python
async def setup(self, ctx: SkillContext) -> None:
    ctx.subscribe_client_event("calls.panic_button", self._on_panic)

async def _on_panic(self, payload: dict) -> None:
    await self._ctx.inject_turn(
        "Llamando a Mario", urgency=Urgency.CRITICAL
    )
    # ... dial out via call provider ...
```

**Namespace your keys as `<skill-name>.<event>`.** The framework reserves `huxley.*` for its own telemetry. Multiple skills can subscribe to the same key; all subscribers are called.

**Unsubscribing**: automatic on skill teardown. Don't track subscriptions yourself.

**Not for PTT or audio** — those are framework-owned fixed message types. `client_event` is for everything else.

**Pushing events back to the client** — use `ctx.emit_server_event(key, payload)`. Symmetric to `client_event` but server-to-client. No-op (with debug log) if the client's capabilities array doesn't include `server_event`. If your skill's flow depends on a specific capability, check first:

```python
if not self._ctx.client_has_capability("calls.led_red"):
    # Degrade gracefully — audio-only confirmation instead
    ...
```

## Taking over the mic — `InputClaim`

> ⚠️ **Planned — not yet shipped.** This is T1.4 Stage 2 work.
> `MicRouter` shipped in T1.3 (one handler: the voice provider), but the
> `InputClaim` side-effect type, the claim lifecycle (`ClaimHandle`,
> `on_claim_end`, `ClaimEndReason`), and the provider suspend/resume
> contract aren't wired yet. The `yield_policy=YieldPolicy.YIELD_ABOVE`
> in the sample reflects pre-pivot vocabulary and will likely be replaced
> by a channel/priority expression. See `triage.md` T1.4.

Some skills need mic PCM to go somewhere other than the voice provider: a voice-memo skill writes mic to a file, a calls skill pipes mic to a remote peer. Return an `InputClaim` side effect from a tool result.

```python
from huxley_sdk import InputClaim, YieldPolicy

async def handle(self, tool_name: str, args: dict) -> ToolResult:
    if tool_name == "record_memo":
        writer = AudioWriter(self._memo_dir / f"{now_iso()}.wav")
        return ToolResult(
            output='{"recording": true}',
            side_effect=InputClaim(
                on_mic_frame=writer.write,
                speaker_source=None,
                on_claim_end=writer.close,
                yield_policy=YieldPolicy.YIELD_ABOVE,
            ),
        )
```

**While the claim is active**:

- Mic PCM frames go to your `on_mic_frame` handler, not the voice provider
- The voice provider session is suspended (resumes automatically when the claim ends)
- Optional `speaker_source` async iterator streams bytes to the client speaker (bidirectional I/O for calls)

**The claim ends when**:

- Your `speaker_source` iterator exhausts
- The user PTTs (escape hatch — always available)
- Your skill cancels via the returned handle
- A higher-priority `inject_turn` preempts (per your `yield_policy`)

In every case, your `on_claim_end(reason)` callback fires so you can clean up (flush files, close sockets, hang up calls).

**`yield_policy`** — same enum as `AudioStream`. Default `YIELD_CRITICAL` means only `CRITICAL`-urgency injected turns can preempt your claim. A voice-memo skill might use `YIELD_ABOVE` (lets `INTERRUPT`-urgency reminders through); a calls skill uses `YIELD_CRITICAL` (only another critical event interrupts an active call).

## Logging — make your skill debuggable

Skills get a logger via the SDK context. Use it. The framework's debugging workflow (described in [`../observability.md`](../observability.md)) depends on every component emitting structured events with the right namespace.

```python
async def handle(self, tool_name: str, args: dict) -> ToolResult:
    await self.log.info("audiobooks.dispatch", tool=tool_name, args_keys=list(args))
    result = await self._do_the_thing(args)
    await self.log.info("audiobooks.result", success=result.success)
    return result
```

The convention: `<skill_name>.<event>`. The framework auto-injects the `turn` ID, so you don't have to thread it through.

## Testing

Skills must have unit tests. Mock the infrastructure (`Storage`, any external clients), assert on `ToolResult.output` and — for side-effect tools — check `isinstance(result.side_effect, AudioStream)` and invoke `result.side_effect.factory()` to verify the underlying stream call.

For end-to-end coverage of how your skill behaves inside the framework (factory latching, mid-chain interrupts, follow-up rounds), see the integration test pattern in [`test_coordinator_skill_integration.py`](../../packages/core/tests/unit/test_coordinator_skill_integration.py) — it wires a real `TurnCoordinator` to a real skill with a mocked infrastructure.

Integration tests that hit real subprocess (ffmpeg) or real provider APIs live in `packages/core/tests/integration/` and are marked `@pytest.mark.integration`. Skipped by default.

## Distribution — making your skill installable

Built-in skills (audiobooks, calls, news, radio, system, timers) live in `packages/skills/<name>/` in this repo. Community skills are independent Python packages published on PyPI under the convention `huxley-skill-<name>`.

Skill-specific docs:

- [`audiobooks.md`](audiobooks.md) — long-form spoken audio playback with bookmark resume.
- [`telegram.md`](telegram.md) — outbound p2p Telegram voice calls via py-tgcalls + FIFO-bridged PCM.
- [`news.md`](news.md) — Open-Meteo weather + Google News RSS summarization.
- [`radio.md`](radio.md) — HTTP/Icecast streams via ffmpeg.
- [`timers.md`](timers.md) — one-shot reminders via proactive speech, persisted across restart.

A persona enables a skill by listing it in `persona.yaml`:

```yaml
skills:
  - my_skill: { config_key: value }
```

The framework matches the YAML key (`my_skill`) to the package name (`huxley-skill-my_skill`) and instantiates it with the config dict.

## File layout for a new skill (post-SDK-extraction)

```
huxley-skill-my-thing/
├── pyproject.toml            # depends on huxley-sdk
├── README.md                 # what it does, config, examples
├── src/
│   └── huxley_skill_my_thing/
│       ├── __init__.py       # exports MySkill class
│       └── skill.py
└── tests/
    └── test_my_skill.py
```

The two first-party skills (`audiobooks`, `system`) live under `packages/skills/<name>/` and are loaded via `huxley.skills` entry points exactly like a third-party skill would be. Their layout is the canonical reference for the structure above.
