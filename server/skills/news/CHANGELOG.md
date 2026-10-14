# Changelog

## 0.2.0 (2026-10-13)

T1.48: headlines a voice model can tell.

### Added

- `related`: up to two other outlets' headlines for the same story (from Google's cluster), so a bare headline can be explained.
- `category: local` searches Google News for `local_query` (new config, default the `location`) instead of the national feed.
- Weather: `tomorrow` and `rain_chance_pct` for today and tomorrow.
- `news.fetched` logs `with_related`, `oldest_age_h`, `latency_ms`.

### Changed

- Searches (`query`, local) add `when:3d` and keep items up to 72 h old; feeds keep `max_age_hours`.
- Near-identical headlines from several outlets are kept once; " | Source" repeated in a title is trimmed.
- Items are `{title, source, age_hours, related?}`: the old `summary` (every clustered headline run together) and `published_at`/`category` are gone. A top-stories payload went from about 6.9 KB to 3.8 KB.
- `news.fetched` and `news.cache_hit` log a hash of the query at info (the text only at debug).

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
- The HTTP client is `httpx`; tests use a dict-backed `FakeHttpClient` for offline runs.
