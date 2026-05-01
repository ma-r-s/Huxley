# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member.

### Added

- `NewsSkill` with two voice tools: `get_news` (Google News RSS, country/category-filtered) and `get_weather` (Open-Meteo forecast).
- In-memory TTL cache (default 300s) so repeated requests within a turn don't re-fetch.
- `start_sound` chime played as a `PlaySound` side effect when a news/weather payload lands; persona opts in by setting `start_sound: news_start`.
- Per-language tool descriptions + interest fallbacks via persona `i18n.<lang>` blocks.
- `config_schema = None` declared (i18n + interest-list shape).
- `data_schema_version = 1`.

### Notes

- No API keys required (Open-Meteo + Google News RSS are both public).
- The HTTP client is `httpx`; tests use `pytest-httpx` for offline mocks.
