# Changelog

## 0.1.0 — 2026-05-01

Initial release. Bundled with Huxley as a workspace member. The first first-party skill to adopt T1.14's `config_schema` convention.

### Added

- `SearchSkill` with one voice tool: `search_the_web` (DuckDuckGo via `ddgs`, no API key).
- In-memory TTL cache for repeated identical queries.
- Consecutive-failure circuit breaker: opens for ~60s on N back-to-back failures so a DDG outage doesn't make every query hang for 4s.
- `start_sound` chime + per-language tool descriptions.
- `config_schema` declared (`safesearch` enum: off / moderate / strict) — the first first-party skill demonstrating the JSON-Schema convention v2's PWA renders against.
- `data_schema_version = 1`.

### Notes

- No API key required.
- The cache + circuit breaker are in-memory only; both vanish at restart.
