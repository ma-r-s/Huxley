# huxley-skill-reminders

Persistent reminders for [Huxley](https://github.com/ma-r-s/Huxley). Calendar-time + RFC 5545 RRULE recurrence, kind-aware retry escalation, catch-up at boot.

> **Status**: bundled with the Huxley repo as a workspace member.

## What it does

- **`set_reminder`** — "remind me to take my pill at 9am every day" — schedules a one-shot or recurring reminder. Recurrence rules accept full RFC 5545 RRULE syntax (`FREQ=DAILY`, `FREQ=WEEKLY;BYDAY=MO,WE,FR`, etc.). Tz-aware so DST transitions don't drift the local fire time.
- **`list_reminders`** — "what reminders do I have" — speaks the upcoming list.
- **`cancel_reminder`** — "cancel my pill reminder" — removes by id (or by fuzzy match on name).

When a reminder fires, the skill emits a proactive `inject_turn` so the persona narrates it in voice. Kind-aware retry: a missed `medication` reminder retries louder + more often than a missed `lunch` reminder. Catch-up at boot: reminders that should have fired during downtime surface once the persona is ready.

## Configure

```yaml
skills:
  reminders:
    timezone: "America/Bogota" # IANA TZ name
    fire_prompt: "Recordatorio:" # prefix the persona uses when narrating
    seed: 42 # optional; deterministic ids for tests
    i18n:
      es:
        kind_medication: "medicamento"
        kind_appointment: "cita"
        # ... per-kind locale strings
      en:
        kind_medication: "medication"
        kind_appointment: "appointment"
```

`config_schema = None` — locale i18n maps + per-kind retry policies don't fit JSON Schema cleanly; v2's PWA falls back to "edit YAML directly."

## Storage

Reminder rows persist in `ctx.storage` under `reminder:<id>` keys. Each row stores fire-at, RRULE (for recurring), kind, retry state, last-fire timestamp.

## Requirements

- `python-dateutil` for RRULE evaluation (declared in `pyproject.toml`).
- No external services.

## Development

```bash
uv run --package huxley-skill-reminders pytest server/skills/reminders/tests
uv run ruff check server/skills/reminders
uv run mypy server/skills/reminders/src
```

## License

MIT — see [`LICENSE`](LICENSE).
