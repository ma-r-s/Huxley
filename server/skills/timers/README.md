# huxley-skill-timers

One-shot relative timers for [Huxley](https://github.com/ma-r-s/Huxley). "Remind me in 5 minutes." Persistent, supervised, stale-restore-guarded.

> **Status**: bundled with the Huxley repo as a workspace member.

## What it does

- **`set_timer`** — "set a timer for 5 minutes" / "remind me in 30 seconds to take it out of the oven" — schedules a one-shot proactive announcement after the given relative duration. The skill's wait task fires `inject_turn(message)` when the timer expires.

For calendar-time reminders ("at 9am tomorrow") use [`huxley-skill-reminders`](../reminders/README.md) instead — the two skills cleanly split absolute vs. relative time.

## Persistence + supervision

- Timer rows live in `ctx.storage` under `timer:<id>` keys.
- The wait task for each timer is wrapped in `ctx.background_task` so the framework can see crashes (logged via `aexception`), restart within a budget, and cancel cleanly at shutdown.
- On boot, `setup()` enumerates `timer:*` and reschedules the survivors against wall-clock time.
- **Stale-restore guard**: if a restored timer's `fire_at` is more than `stale_restore_threshold_s` in the past, it's dropped silently — no spammy "your 7am wake-up timer is now firing at 6pm" surprises.

## Configure

```yaml
skills:
  timers:
    stale_restore_threshold_s: 300 # default 5 min; raise if you want catch-up
    fire_prompt: "El temporizador terminó:" # prefix the persona narrates
    i18n:
      es:
        seconds_word: "segundos"
        minutes_word: "minutos"
      en:
        seconds_word: "seconds"
        minutes_word: "minutes"
```

`config_schema = None` — locale i18n maps don't fit a JSON-Schema-rendered form; v2's PWA falls back to "edit YAML directly."

## Development

```bash
uv run --package huxley-skill-timers pytest server/skills/timers/tests
uv run ruff check server/skills/timers
uv run mypy server/skills/timers/src
```

## License

MIT — see [`LICENSE`](LICENSE).
