# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member.

### Added

- `RemindersSkill` with three voice tools: `set_reminder` (one-shot or recurring), `list_reminders`, `cancel_reminder`.
- RFC 5545 RRULE-based recurrence (DAILY, WEEKLY with BYDAY, etc.) via `python-dateutil`. Tz-aware so DST transitions don't drift the local fire time.
- Kind-aware retry escalation: a missed medication reminder retries louder + more often than a missed lunch reminder. Configured per-kind via the persona's i18n locale.
- Catch-up at boot: reminders that fired during downtime are surfaced via `inject_turn` once the persona is ready.
- `config_schema = None` declared (locale i18n maps + per-kind retry policies don't fit JSON Schema).
- `data_schema_version = 1`.

### Notes

- Reminder rows persisted in `ctx.storage` under `reminder:<id>` keys.
- Per-language fire-prompt phrasing comes from the persona's `i18n.<lang>` block.
