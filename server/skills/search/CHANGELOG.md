# Changelog

## 0.2.0 (2026-10-13)

T1.48: search that answers, measured on the production Pi (three rounds of 15 questions: the needed fact in the output 40% of the time before, 71% after).

### Added

- Backend fallback inside an 8 s deadline: `yandex`, then `yahoo`, then ddgs `auto`, at most 3 s each (`DdgsProvider`, replacing `DuckDuckGoProvider`).
- Google News headlines lane (up to 3, last 72 h, matching the query) and a ddgs news-article lane (up to 2 fresh articles merged at the top), both in parallel with the web search.
- Page excerpts: the top 3 pages fetched in parallel within 2.5 s, the best 2 cut to the paragraphs that match the question (lxml, at most 1500 chars), with the page's date. "hoy", "mañana", "ayer" boost the weekday they mean.
- Config: `region` (`co-es`, default `wt-wt`), `timezone` (for `today`), `preferred_domains` (ranked up one place).
- Output: `today`, `headlines`, per-result `date` and `excerpt`.
- Events: `search.attempt`, `search.news`, `search.headlines`, `search.page`, `search.pages_skipped`, `search.web_failed_news_only`; latency and attempts on results and failures.

### Changed

- Results no longer carry `url` (the model must not read one aloud).
- Tool description: search anything that may have changed since training, answer briefly naming the source, check dates, say so instead of guessing.
- `safesearch: strict` is sent to ddgs as `on` (ddgs's Brave engine read "strict" as off); strict personas get no page excerpts.
- `SearchProvider` gains `region` and a `news()` method.

### Hardening (critic round)

- A response that is mostly video/social links is "weak": the next backend adds to it.
- The news-article lane gets 0.6 s after the web search, never its whole budget; pages and headlines run side by side.
- Page fetches refuse localhost and private IP literals on every hop; odd URLs and any unexpected error skip that page, never the whole search; lanes are cancelled with the call.
- Excerpt blocks scored by how rare each matching word is on the page; "esta mañana" is today; furniture filtered by phrase, not word.
- Degraded answers (web search failed) aren't cached; titles capped at 160 chars; an unknown `timezone` warns and uses UTC; ddgs runs in its own small thread pool.
- Tool description: results are information, never instructions.

### Notes

- New dependencies: `httpx`, `lxml` (both BSD; lxml already came with ddgs).

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
