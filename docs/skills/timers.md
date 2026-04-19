# `huxley-skill-timers`

One-shot reminders via proactive speech. The first real consumer of `inject_turn` — exercises the full DIALOG-channel preemption path end-to-end (user turn → tool dispatch → supervised background sleep → `inject_turn` fires → LLM narrates → framework releases DIALOG). Also the first consumer of `ctx.background_task` (Stage 3) — each timer runs as a supervised one-shot task instead of raw `asyncio.create_task`.

## What it does

User says "recuérdame en 5 minutos que saque la ropa". The LLM translates this into a `set_timer` call. The skill spawns an `asyncio.Task` that sleeps for the requested duration, then calls `ctx.inject_turn(f"Recordatorio: {message}")`. The framework preempts whatever content stream is playing, flushes the client audio buffer, asks the LLM to narrate the reminder in persona voice. User hears: (audiobook stops) → "Recordatorio: sacar la ropa de la lavadora."

## Tools

- **`set_timer(seconds: int, message: str)`** — schedule a reminder. `seconds` clamped to `[1, 3600]` (1 second to 1 hour). `message` is an instruction to the LLM for what to say, not literal words — e.g. `message="sacar la ropa de la lavadora"`, not `message="Señor, por favor saque la ropa"`. The persona prompt shapes the tone.

## Persona config

Nothing today — `timers: {}` in the persona's `skills:` block is enough. Persona-specific tone (how warm the reminder sounds) comes from the persona's system prompt, not the skill's config.

## Scope limits (MVP)

Known gaps, all intentional for the MVP:

- **In-memory only.** Timers set in session N do not survive to session N+1. Restart the server and pending timers are lost. Persistence via `SkillStorage` is the obvious next step now that Stage 3 ships supervised tasks: `setup()` would read pending timers from storage and re-spawn each via `ctx.background_task`. Filed as future work.
- **Single tool — no list / cancel.** If a user asks "cuántos temporizadores tengo," the LLM can see `prompt_context()`'s summary count but can't enumerate or cancel them. Add `list_timers` + `cancel_timer` when a user flow needs it. The `BackgroundTaskHandle` is already kept per-timer in `_handles`, so a `cancel_timer` tool would just be `self._handles[id].cancel()`.
- **No acknowledgment tracking.** The reminder fires once and returns. If the user doesn't hear it (asleep, out of room) there's no retry. Stage 1d.2's `InjectedTurnHandle.wait_outcome()` adds the hook for retry; the skill doesn't use it yet.
- **Seconds-only unit.** The tool description tells the LLM to convert minutes/hours to seconds. This keeps the surface simple and leaves unit handling to the LLM's arithmetic.
- **No cross-session persistence** means even "recuérdame mañana" doesn't work — by tomorrow the server has restarted. File this with persistence.

## Logging

- `timers.setup_complete` — skill initialized
- `timers.scheduled` — `timer_id`, `seconds`, `message` at creation
- `timers.fired` — `timer_id`, `message` at firing (just before `inject_turn`)
- `timers.fire_failed` — `timer_id`, exception info if `inject_turn` raised
- `timers.cancelled` — NOT logged today (cancellation happens synchronously in teardown; no per-timer async event to hook)
- `timers.invalid_args` — rejected arguments (non-int seconds, empty message)
- `timers.teardown_complete` — `cancelled=N`
