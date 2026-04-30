# `huxley-skill-reminders`

Persistent scheduled reminders with kind-aware retry escalation, recurrence, and missed-reminder catch-up. The first concrete user benefit of the I/O plane: medication and appointment reminders that survive server restarts, retry until acknowledged for medications, surface missed instances on next interaction, and respect the focus-management rules (preempt audiobooks, queue behind active calls).

Sister skill to [`timers`](timers.md). The split is intentional:

|                     | `timers`                                         | `reminders`                                                                                           |
| ------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Time format         | relative seconds (`set_timer(seconds=300, ...)`) | absolute ISO 8601 with offset (`add_reminder(when_iso="2026-04-30T08:00:00-05:00", ...)`)             |
| Lifetime            | one-shot                                         | one-shot or recurring (RFC 5545 RRULE — daily, weekly, weekdays-only, biweekly, monthly, COUNT, …)    |
| Retry               | none                                             | medication kind retries up to 3× at 5 / 10 / 30 min until ack                                         |
| Surface area        | one tool (`set_timer`)                           | five (`add_reminder`, `list_reminders`, `cancel_reminder`, `snooze_reminder`, `acknowledge_reminder`) |
| Boot reconciliation | drop entries older than 1h                       | kind-aware late-window: medication=15min, appointment=2h, generic=1h                                  |

The user speaks naturally; the persona's system prompt routes "recuérdame en 5 minutos…" to `set_timer` and "recuérdame mañana a las 8…" to `add_reminder`.

## What it does

1. User says **"recuérdame todos los días a las 8 que tome la pastilla del corazón"**.
2. The LLM reads the current time + persona timezone from the skill's `prompt_context`, computes ISO 8601 with offset for the next 8am local, and calls `add_reminder(message="tomar la pastilla del corazón", when_iso="2026-04-30T08:00:00-05:00", kind="medication", recurrence_rule="FREQ=DAILY")`.
3. The skill writes the reminder to `SkillStorage` keyed `reminder:<id>` and wakes its scheduler.
4. The single supervised scheduler `background_task` sleeps until the soonest pending row's `next_fire_at`.
5. At fire time, the scheduler calls `ctx.inject_turn(prompt, priority=BLOCK_BEHIND_COMMS)`. The framework:
   - preempts any active audiobook (pump cancels, position saved, audiobook backgrounds with patience and auto-resumes after the reminder drains)
   - **queues behind any active Telegram call** (`BLOCK_BEHIND_COMMS` semantics — narrates after the call ends, never interrupts grandpa mid-conversation)
6. The LLM narrates: _"Oye, ya es hora de tu pastilla del corazón."_
7. User PTTs _"ya me la tomé"_. The LLM calls `acknowledge_reminder(id=...)`. The skill marks the row `acked` and (because `recurrence_rule="FREQ=DAILY"`) schedules a fresh `pending` row for tomorrow at 8.
8. If user never acks, the skill re-fires at +5min, then +10min, then +30min. After three unacked fires, marks `missed` and (per recurrence) still schedules tomorrow's instance.
9. If the server was down at the fire time, `setup()` reconciliation handles the row: within `late_window[kind]` → fire on next scheduler tick; beyond → mark `missed` (medication safety: don't double-dose). Either way, recurring reminders advance to the next instance so today's miss doesn't cancel tomorrow's reminder.
10. Missed reminders surface to the LLM via `prompt_context()` until the user is told about them.

## Tools

All five tools take a single object with the documented fields. Descriptions are localized (es / en / fr) and re-rendered when the session language flips via `reconfigure`.

### `add_reminder`

Required: `message: str`, `when_iso: str`. Optional: `kind: 'medication' | 'appointment' | 'generic'` (default `generic`), `recurrence_rule: <RFC 5545 RRULE string>` (default none).

- `message` is an instruction to the LLM for what to narrate, not literal words. The persona's `fire_prompt` template wraps it.
- `when_iso` MUST be ISO 8601 with timezone offset. Naive datetimes are rejected — the LLM is given the persona timezone in `prompt_context` so it can always produce an offset-bearing string. This is the FIRST occurrence of the series; subsequent instances are computed from the rule.
- Times in the past are rejected. The error message names the received and current values so the LLM can self-correct.
- `recurrence_rule` is an [RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545#section-3.3.10) RRULE string parsed by `dateutil.rrule.rrulestr`. Common shapes:
  - `FREQ=DAILY` — every day at the same wall-clock time
  - `FREQ=WEEKLY` — every week on the same weekday
  - `FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR` — weekdays only
  - `FREQ=WEEKLY;BYDAY=MO,WE,FR` — Mon / Wed / Fri
  - `FREQ=WEEKLY;INTERVAL=2` — every 2 weeks
  - `FREQ=MONTHLY;BYMONTHDAY=15` — 15th of every month
  - `FREQ=MONTHLY;BYDAY=1MO` — first Monday of every month
  - `FREQ=DAILY;COUNT=7` — for 7 days, then stop
  - `FREQ=DAILY;UNTIL=20260601T000000Z` — until June 1, 2026
  - The LLM is taught these patterns in its tool description; invalid strings are caught at `add_reminder` time and rejected with a parse-error message.
- Returns `{ok, id, scheduled_for, kind, recurrence_rule}`.

### `list_reminders`

Returns `{ok, reminders: [...]}` with all `pending` / `fired` / `missed` rows in `next_fire_at` order. Terminal rows (`acked`, `cancelled`, `surfaced`) are excluded so the list stays focused on what's actionable.

### `cancel_reminder`

Required: `id: int`. Marks the row `cancelled` (terminal). Idempotent — a second cancel on the same id is a no-op success.

### `snooze_reminder`

Required: `id: int`, `minutes: int` in `[1, 120]`. Reschedules `next_fire_at` to now + minutes. If the row was in `fired` state (medication mid-retry), transitions it back to `pending` so the retry ladder resets — explicit user "give me five more" shouldn't keep escalating during the snooze.

### `acknowledge_reminder`

Required: `id: int`. Marks the row `acked` (terminal). For `medication` rows, this is the canonical exit from the retry loop. If the original row had `recurrence_rule` set, the skill schedules the next instance via `dateutil.rrule.rrulestr(rule, dtstart=series_start).after(scheduled_for)` so the user's chosen time doesn't drift to whenever they happened to ack — and so `COUNT` / `UNTIL` rules terminate correctly across the chain.

## Persona config

```yaml
skills:
  reminders:
    timezone: America/Bogota # surfaced verbatim to the LLM
    fire_prompt: | # default; per-language overrides via i18n.<lang>.fire_prompt
      Suena un recordatorio que el usuario programó. Avísale con tono
      cálido y natural sobre: {message}. Empieza la frase como si se
      lo recordaras a un amigo... Si dice que ya lo hizo, llama a
      `acknowledge_reminder` con el id.
    # Optional per-kind override. Defaults: medication=15min,
    # appointment=2h, generic=1h. Tighter values trade narration
    # latency for safety; a 0-second window is rejected (we need
    # at least one tick of slack to fire-on-recovery).
    late_window_medication_s: 900
    late_window_appointment_s: 7200
    late_window_generic_s: 3600
    # Optional seed list — imported into SkillStorage on first boot
    # only (idempotent). Useful for baking grandpa's daily medications
    # into the persona file. Subsequent boots ignore the seed list;
    # the user can delete a seeded reminder without it resurrecting.
    seed:
      - message: "tomar la pastilla del corazón"
        when_iso: "2026-04-30T08:00:00-05:00"
        kind: medication
        recurrence_rule: FREQ=DAILY
      # Weekday-only therapy:
      - message: "fisioterapia"
        when_iso: "2026-05-04T15:00:00-05:00"
        kind: appointment
        recurrence_rule: FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
```

`fire_prompt` must contain `{message}`. `{kind}` is also available. Persona authors localize via `i18n.<lang>.fire_prompt` — the active session language picks the right one and `reconfigure` re-resolves on language flip.

## State machine

```
                            ┌──(non-medication fire — ack-on-fire)──> acked
                            │       └─ if recurring & series not exhausted: schedule next
                            │
pending ──(fire at next_fire_at)──> fired (medication only)
   │                            │
   │                            ├──(user PTT "ya me la tomé" → ack tool)──> acked
   │                            │       └─ if recurring & series not exhausted: schedule next
   │                            ├──(user "dame 5 más" → snooze)──> pending (ladder resets)
   │                            └──(no ack → retry timer)──> fired → … →  missed (budget=3 exhausted)
   │                                                                  └─ if recurring & series not exhausted: schedule next
   │
   ├──(boot reconciliation, past + outside late_window)──> missed
   │       └─ if recurring & series not exhausted: schedule next
   ├──(user cancelled)──> cancelled
```

Terminal states (`acked`, `cancelled`) are kept in storage for `list_reminders` until pruned manually. **`missed` is _not_ terminal** — it stays in `prompt_context` so the LLM can mention it; the LLM is instructed (via the persona prompt) to follow up by calling `acknowledge_reminder` (user did the action / will skip) or `cancel_reminder` (user says it doesn't matter) to clear it. If the LLM forgets, the row surfaces again next turn — annoying but bounded, and self-limiting because the LLM tends to follow through on the second mention.

**Recurrence semantics**: each row carries a `series_start` field (UTC, the very first occurrence) that successors inherit unchanged. `_next_recurrence` evaluates the RRULE with `dtstart=series_start` and queries `.after(current_scheduled_for)`. This makes `COUNT` / `UNTIL` rules terminate correctly across the chain — a `FREQ=DAILY;COUNT=3` series fires exactly three times no matter how many successors get created in between, because every row anchors on the same start. A returned `None` from `_next_recurrence` means the series is complete; the row terminates without a successor and a `reminders.recurrence_complete` log line records why.

Recurrence rolls forward on `acked` AND on `missed` so a single missed dose doesn't wipe out tomorrow's reminder. The next-instance creation is **idempotent**: `_schedule_next_recurrence` scans existing rows and skips creation if a successor for `(kind, recurrence_rule, message, scheduled_for ≈ next_when)` already exists. Without this guard, repeated boot reconciliation on a missed recurring reminder would fan out N successors after N restarts.

## Persistence

All state is in `SkillStorage` under the `reminder:` prefix:

- `reminder:<id>` — JSON-encoded `_Entry` (id, message, kind, scheduled_for, next_fire_at, recurrence_rule, series_start, state, fired_count, last_fired_at, …). Each row stamps a `v` field (currently `2`) for forward-compat, but `_Entry.from_json` does NOT branch on `v` — migration is **field-presence heuristic**: if `recurrence_rule` is missing AND legacy `recurrence` is present, translate the enum to the equivalent RRULE. The `v` field is informational today; a future v3 migration would add a `v == 2` branch then.
- `reminder:_meta:next_id` — monotonic id allocator. Primed by `setup()` past every existing row's id BEFORE the reconcile loop runs (otherwise `_schedule_next_recurrence` would collide with an original row's id). `_allocate_id` is wrapped in an `asyncio.Lock` so concurrent callers (scheduler-driven recurrence vs. tool-handler `add_reminder`) serialize their read-then-write — without the lock, a real I/O-awaiting `SkillStorage` lets the read interleave and the writes both produce the same id.
- `reminder:_meta:seed_imported` — `"1"` after the persona's `seed` list has been imported. Manual deletion of seeded rows doesn't trigger re-import.

The scheduler runs as a single supervised `background_task` with `restart_on_crash=True`. Different from timers (one task per timer, no restart) — for reminders, the storage IS the truth, so a crashed scheduler is recoverable by re-reading rows on the next loop iteration.

### Crash safety: commit-before-inject

`_fire` saves the post-fire state **before** calling `inject_turn`, mirroring the timers skill's `fired_at` pattern. Order matters for medication safety: if narration ran first and the process died before the state save, the next boot would see `state=pending` with `next_fire_at` past + within `late_window[medication]=15min`, and re-narrate. **Double-dose.** Saving first eliminates that window.

The trade-off this introduces: a transient `inject_turn` failure (OpenAI Realtime blip) burns a retry-budget slot for medication kind without grandpa hearing anything. A sustained outage spanning all three retries marks the row `missed` with zero successful narrations. We accept that — silent miss is safer than double dose. Operators diagnose the path via the `reminders.fire_failed` log alongside the `reminders.fired` line that records each attempt's `fired_count`.

For one-shot kinds with recurrence, `_schedule_next_recurrence` is also called BEFORE narration so a crash between terminal-save and recurrence-schedule doesn't lose tomorrow's reminder. `_schedule_next_recurrence` is itself idempotent (scans for an existing successor before creating one), so re-running it on boot reconcile is safe.

### Boot reconciliation policy

Per row, in order:

1. Terminal (`acked` / `cancelled`) → leave alone.
2. `missed` (from a prior boot) → if recurrence, schedule the next instance (idempotent — won't create a duplicate if a successor already exists). Original stays `missed` for surfacing in the next session's `prompt_context`.
3. `pending` future → leave alone; scheduler picks it up.
4. `pending` past, within `late_window[kind]` → leave `pending`; scheduler fires on next tick (catch-up).
5. `pending` past, beyond `late_window[kind]` → mark `missed`. Recurring rows still get the next instance scheduled.
6. `fired` (medication, mid-retry when the process died) → if retries remain AND the next retry is within window, restore to `pending` with `next_fire_at` recomputed; else mark `missed`.
7. `fired` (non-medication) → treated as already-narrated; transition to `acked` (we don't re-narrate appointments hours late). Recurring rows still advance.

The bias throughout: **err on the side of NOT re-narrating** when in doubt. For medication this prevents double-dosing; for appointments it prevents stale notices.

## `prompt_context()`

Two purposes:

1. **Time + tz banner** — surfaces the current UTC time and the persona's timezone label so the LLM can compute correct ISO offsets for `add_reminder.when_iso`. Without this the LLM either guesses UTC (wrong for any non-UTC user) or refuses ("I don't know your timezone").
2. **Missed surfacing** — lists every `state='missed'` row with id, kind, scheduled_for, and message. The LLM is instructed (via the persona prompt) to mention these naturally when it fits, NOT to insist the user take a hours-late dose, and to follow up with `acknowledge_reminder` or `cancel_reminder` to clear the row from the surface list. If the LLM forgets to clear, the row surfaces again next turn.

`prompt_context` is sync (per the Skill protocol) and so cannot await storage on every call. The skill maintains a `_missed_cache` snapshot refreshed after every storage write — fresh enough for prompt builds, no per-turn round-trip.

## What's not in v1 (deferred until grandpa needs it)

- **ALERT-channel local fallback** — narrated reminders only. If OpenAI Realtime is unreachable at fire time, the `inject_turn` failure is logged and the row's state is **already advanced** (commit-before-inject — see Crash safety above), so the user gets no narration but the medication-safety property is preserved. A sustained outage across all three medication retries ends in `missed` without any successful narration. A non-narrated alert tone on the ALERT channel was discussed and explicitly punted (see [`docs/triage.md`](../triage.md) T1.8 critic notes 2026-04-29). Revisit when a real outage causes a real missed dose.
- **Caregiver escalation** — exhausted medication retries mark `missed` and surface via `prompt_context`; they do NOT notify a caregiver via Telegram. Cannot design correctly without observing the actual failure mode (didn't hear / forgot / button confusion / dead device). Will be designed against the observed failure once we see it.
- **TTL on `inject_turn`** — currently if a reminder fires during a 4h Telegram call, the inject queues for 4h and narrates at call-end. For a medication that's potentially wrong (the next dose may already be due). D7 in `triage.md` will close this when a real consumer surfaces the gap.
- **Multi-device fan-out** — Huxley today is single-device per persona. AVS-style "ring on every Echo" is out of scope.

## Logging

All structured events use the `reminders.*` namespace per [observability.md](../observability.md):

- `reminders.added`, `reminders.cancelled`, `reminders.snoozed`, `reminders.acked`
- `reminders.fired` (per-fire — `id`, `kind`, `fired_count`, `message`)
- `reminders.missed` (terminal — `id`, `kind`, `reason`, `scheduled_for`, `fired_count`)
- `reminders.boot_within_window`, `reminders.boot_resumed_retry` — boot reconciliation outcomes
- `reminders.seeded`, `reminders.seed_skipped_invalid`
- `reminders.fire_failed` (full traceback) — when `inject_turn` raises during a fire

Read these in order to diagnose any "the reminder didn't go off" report. The `fired_count` field is the most useful: 0 means the scheduler never reached it; 1+ means narration was attempted at least once.
