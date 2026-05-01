# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member.

### Added

- `TimersSkill` with one voice tool: `set_timer(seconds, message)` — fires a proactive `inject_turn` after the elapsed time.
- Persistent across restarts: timer rows in `ctx.storage` under `timer:<id>`; `setup()` restores pending timers.
- Stale-restore guard: timers whose `fire_at` is more than `stale_restore_threshold_s` in the past at restart are dropped (no spammy "your 7am wake-up timer is now firing at 6pm" surprises).
- Per-language fire-prompt phrasing via persona `i18n.<lang>`.
- `config_schema = None` declared (locale i18n maps).
- `data_schema_version = 1`.

### Notes

- Not for calendar-time reminders ("at 9am tomorrow"); use `huxley-skill-reminders` for that.
- For lifelong supervision the framework's `ctx.background_task` wraps each timer's wait task with crash-restart semantics.
