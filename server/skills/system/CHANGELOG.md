# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member. The simplest first-party skill — a good template for a stateless utility skill.

### Added

- `SystemSkill` with two voice tools: `get_time` (formatted date string in the configured timezone) and `set_volume` (PlaySound side effect with a target volume hint).
- Per-language formatting (Spanish "son las dos y media" vs English "two thirty").
- `config_schema` declared with a single `timezone` field — the simplest first-party form-renderer demo.
- `data_schema_version = 1`.

### Notes

- No persisted state; both tools are stateless operations.
- `timezone` defaults to `America/Bogota`; override per persona.
