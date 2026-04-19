# `huxley-skill-timers`

One-shot reminders via proactive speech. The first real consumer of `inject_turn` — exercises the full DIALOG-channel preemption path end-to-end (user turn → tool dispatch → supervised background sleep → `inject_turn` fires → LLM narrates → framework releases DIALOG). Also the first consumer of `ctx.background_task` (Stage 3) — each timer runs as a supervised one-shot task instead of raw `asyncio.create_task`.

## What it does

User says "recuérdame en 5 minutos que saque la ropa". The LLM translates this into a `set_timer` call. The skill spawns an `asyncio.Task` that sleeps for the requested duration, then calls `ctx.inject_turn(f"Recordatorio: {message}")`. The framework preempts whatever content stream is playing, flushes the client audio buffer, asks the LLM to narrate the reminder in persona voice. User hears: (audiobook stops) → "Recordatorio: sacar la ropa de la lavadora."

## Tools

- **`set_timer(seconds: int, message: str)`** — schedule a reminder. `seconds` clamped to `[1, 3600]` (1 second to 1 hour). `message` is an instruction to the LLM for what to say, not literal words — e.g. `message="sacar la ropa de la lavadora"`, not `message="Señor, por favor saque la ropa"`. The persona prompt shapes the tone.

## Persona config

- **`fire_prompt`** _(optional)_ — template for the prompt `inject_turn` sends to the LLM when a timer fires. Must contain `{message}`, which is substituted with the user's reminder text. Defaults to a Spanish / AbuelOS-toned template (warm-friend register, "oye, recuerda que…"). Non-Spanish or non-warm personas should override; the default assumes Spanish and a warm tone, so a terse English persona gets a broken narration otherwise. If the configured value is missing the `{message}` placeholder the skill logs `timers.fire_prompt_missing_placeholder` and falls back to the default; empty strings are ignored.

Example (BasicOS-style terse English):

```yaml
skills:
  timers:
    fire_prompt: |
      A timer the user set has fired. Tell them briefly: {message}.
      One sentence, neutral tone.
```

## Persistence (Stage 3b)

Timers survive a server restart. The skill writes each pending timer to its namespaced `SkillStorage` as a JSON entry keyed `timer:<id>`; `setup()` enumerates those entries on boot via `ctx.storage.list_settings("timer:")` and re-registers each one with `ctx.background_task`.

**Entry schema** (`v: 1`):

```json
{
  "v": 1,
  "fire_at": "2026-04-19T14:05:00+00:00",
  "message": "sacar la ropa de la lavadora",
  "fired_at": null
}
```

**Restore policy** (on `setup()`, for each entry):

| State                                                              | Action                                                                                                                                  |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `fired_at` set                                                     | Delete + skip. Prevents double-fire if the process died between `inject_turn` and the entry delete — critical for medication reminders. |
| `now − fire_at > 1h` (the skill's own `_MAX_SECONDS` duration cap) | Delete + skip. Original intent is stale.                                                                                                |
| `fire_at` in the past but ≤ 1h late                                | Fire immediately (1 s scheduled). Better a late reminder than none.                                                                     |
| `fire_at` in the future                                            | Reschedule with `fire_at − now` remaining.                                                                                              |
| Malformed JSON / key                                               | Skip with a warning log. No delete — a future schema migration opportunity.                                                             |

`set_timer` writes the entry _before_ scheduling the supervised task. `_fire_after` stamps `fired_at` **after** the sleep completes but **before** awaiting `inject_turn`, so a crash during narration still flips the entry into dedup territory. The entry is deleted only when firing ran to the point of committing (`fired = True`); cancellation during the sleep (e.g., `teardown()` at server shutdown) preserves the entry untouched so the next boot can restore it.

**Wall-clock caveat**: `fire_at` is UTC wall clock. An NTP jump or manual clock change makes timers fire earlier / later by the skew. Fixed-device deployments (AbuelOS is one) rarely see this, and the stale-threshold guard catches the only dangerous shape (clock jumps days forward). Logs `timers.restore_skipped_stale` with `age_s` so "why didn't my timer fire" is diagnosable.

## Scope limits

- **Single tool — no list / cancel.** If a user asks "cuántos temporizadores tengo," the LLM can see `prompt_context()`'s summary count but can't enumerate or cancel them. Add `list_timers` + `cancel_timer` when a user flow needs it. The `BackgroundTaskHandle` is already kept per-timer in `_handles`, so a `cancel_timer` tool would just be `self._handles[id].cancel()` plus `self._delete_entry(id)`.
- **No acknowledgment tracking.** The reminder fires once and returns. If the user doesn't hear it (asleep, out of room) there's no retry. Stage 1d.2's `InjectedTurnHandle.wait_outcome()` adds the hook for retry; the skill doesn't use it yet.
- **Seconds-only unit.** The tool description tells the LLM to convert minutes/hours to seconds. This keeps the surface simple and leaves unit handling to the LLM's arithmetic.
- **1 h max duration.** Anything longer wants a different primitive (appointment / calendar). The stale-drop threshold matches the max so a restart picks up any live timer at worst "1 h late into a 1 h timer."

## Logging

- `timers.setup_complete` — skill initialized; fields: `fire_prompt_source`, `restored`, `dropped`
- `timers.scheduled` — `timer_id`, `seconds`, `message`, `fire_at` at creation
- `timers.fired` — `timer_id`, `message` at firing (just after `fired_at` stamp, just before `inject_turn`)
- `timers.fire_failed` — `timer_id`, exception info if `inject_turn` raised
- `timers.restored` — `timer_id`, `remaining_s`, `message` when a persisted entry was rescheduled on boot
- `timers.restore_skipped_fired` — `timer_id`, `fired_at` when dedup guard fires (crash mid-fire)
- `timers.restore_skipped_stale` — `timer_id`, `fire_at`, `age_s` when `> 1h` stale
- `timers.restore_entry_malformed` — `key`, `value` (truncated) for unparseable entries
- `timers.restore_key_malformed` — `key` when the `timer:N` suffix isn't numeric
- `timers.cancelled` — NOT logged today (cancellation happens synchronously in teardown; no per-timer async event to hook)
- `timers.invalid_args` — rejected arguments (non-int seconds, empty message)
- `timers.teardown_complete` — `cancelled=N`
