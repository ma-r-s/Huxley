# Skill: search

Persona-agnostic open-web search. The LLM calls `search_the_web(query, max_results)` when it needs current or recent information it doesn't already know — today's weather _conditions_, sports scores, what just happened, prices, events of the day. Returns up to 5 ranked hits as JSON; the LLM narrates the answer in its persona's voice.

The skill does **zero narration**. It returns data; the LLM decides how to speak it based on the persona's `system_prompt` + constraints.

## What it does

| Tool             | Args                                                  | Returns                                    |
| ---------------- | ----------------------------------------------------- | ------------------------------------------ |
| `search_the_web` | `query: string`, `max_results?: int` (1–5, default 3) | `{ result_count, results[], say_to_user }` |

Each result has `title`, `source` (root domain — pre-extracted so the LLM doesn't read URLs character-by-character), `url`, and a cleaned `snippet` (max 280 chars, URLs stripped).

`say_to_user` is non-null **only** on failure paths (empty results, rate-limited, timeout, error). When set, the tool description tells the LLM to relay it to the user — this is how the skill honors `never_say_no` at the contract level.

## Data source (no API key)

[`ddgs`](https://pypi.org/project/ddgs/) — DuckDuckGo via the `ddgs` Python package. Free, no signup, sync API wrapped in `asyncio.to_thread` with a 4-second hard timeout. Calls are issued with `region="wt-wt"` (worldwide, no regional skew); the query language guides relevance.

A 5-minute in-memory TTL cache (case-insensitive on the query, partitioned by `max_results`) saves a network round-trip when the user asks for the same thing twice.

## Configuration

Persona's `skills.search` block:

| Key           | Required | Default      | Notes                                                                                                                                                                                                   |
| ------------- | -------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `safesearch`  | no       | `"moderate"` | One of `off` / `moderate` / `strict`. A child-safe persona sets `strict`.                                                                                                                               |
| `start_sound` | no       | _none_       | Sound palette role (e.g. `search_start`). Omit for no chime.                                                                                                                                            |
| `sounds_path` | no       | `sounds`     | Sound palette directory (relative to persona `data_dir` or absolute). Personas typically point this at `../../_shared/sounds` to use the framework-shared palette — see [`../sounds.md`](../sounds.md). |

Skill internals (cache TTL, snippet length, circuit-breaker thresholds, the 5-result cap, recovery message text) are **not** persona-overridable. They're mechanical; if a deployer needs to vary one, expose it then.

## How AbuelOS uses it

The system prompt tells the LLM: use it for current info, never for stable facts (capitals, definitions), never for digesting today's news (that's `get_news`). Pre-narrate "a ver, déjame buscar" before calling so the user doesn't sit in silence during the fetch. Cite sources by name (`según El País`), never read URLs.

User says _"qué tiempo hace en Madrid ahora mismo"_ → LLM says _"a ver, un momento"_ → chime → LLM narrates the answer in the persona's slow, warm tone, citing one or two sources.

## Failure modes

Every failure carries a `say_to_user` line for `never_say_no` compliance — the tool description forces the LLM to speak it.

| Failure                              | Detection                                  | Behavior                                                                                  |
| ------------------------------------ | ------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Empty results, clean response        | provider returned `[]`, no throttle signal | `say_to_user`: "No he encontrado nada sobre eso. ¿Quieres que pruebe con otras palabras?" |
| Rate-limited (DDG 202 / 429)         | provider raised `SearchRateLimitedError`   | `say_to_user`: "Ahora mismo no puedo buscar. Dame un momento e intenta de nuevo."         |
| Timeout (4s hard cap)                | `asyncio.wait_for` raised `TimeoutError`   | `say_to_user`: "La búsqueda tardó demasiado. ¿Lo intentamos de nuevo?"                    |
| Other exception                      | provider raised `SearchProviderError`      | `say_to_user`: "Algo no fue bien con la búsqueda. Inténtalo otra vez en un momento."      |
| Empty / blank query                  | skill defends                              | Same as "empty results" — LLM asks user to repeat.                                        |
| Cache hit                            | match within TTL                           | No HTTP request; returns cached payload as-is.                                            |
| `start_sound` configured but missing | file not in palette                        | Logs `search.start_sound_missing` warning; runs silently.                                 |

Recovery messages ship in **es / en / fr** built into the skill — not persona-overridable. They're skill-mechanical, not persona-flavored.

### Circuit breaker

After **3 consecutive** failures (rate-limit / timeout / error in any combination), the breaker opens for **60 seconds**. Subsequent queries short-circuit to the rate-limited recovery message without hitting DDG. The first successful response after the window resets the counter. This spares the user a 4-second hang on every query during a DDG outage. Internal-only; not configurable.

## The chime mechanism (PlaySound)

Same pattern as the news skill. When `start_sound` is set and the WAV exists, `search_the_web` returns `ToolResult(side_effect=PlaySound(pcm=...))` on success. The chime is queued right after `request_response()` for the follow-up round, lands on the WebSocket ahead of the model's audio (FIFO). User hears: chime → model voice.

**No chime on failure paths.** Empty / rate-limited / timeout / error all return without `side_effect` — the `say_to_user` line is the only signal.

## Observability

Events emitted (per [`observability.md`](../observability.md) namespacing):

| Event                    | When                                                 | Fields                                                |
| ------------------------ | ---------------------------------------------------- | ----------------------------------------------------- |
| `search.setup_complete`  | At skill init                                        | `safesearch`, `ui_language`, `chime`                  |
| `search.reconfigure`     | On every session connect (language flip)             | `ui_language`                                         |
| `search.dispatch`        | Tool entry — **`query_hash` only**, no full query    | `query_hash` (sha256[:8]), `query_len`, `max_results` |
| `search.dispatch_full`   | Full query (debug level only)                        | `query`                                               |
| `search.short_query`     | Query length < 3 (signal, not a filter)              | `query_len`                                           |
| `search.cache_hit`       | TTL cache hit                                        | `query_hash`, `hits`                                  |
| `search.results`         | Successful response                                  | `query_hash`, `count`, `top_domains`                  |
| `search.empty`           | Provider returned `[]` cleanly                       | `query_hash`                                          |
| `search.rate_limited`    | Provider raised `SearchRateLimitedError`             | `query_hash`, `reason`                                |
| `search.timeout`         | 4-second deadline exceeded                           | `query_hash`, `reason`                                |
| `search.error`           | Generic `SearchProviderError`                        | `query_hash`, `exception_type`                        |
| `search.circuit_opened`  | 3 consecutive failures hit the threshold             | `duration_s`                                          |
| `search.circuit_blocked` | A query short-circuited because the breaker was open | `query_hash`, `seconds_until_close`                   |

**Privacy**: queries leak intent (medical lookups, legal questions). The skill logs only an 8-char SHA-256 prefix at info level; the full query stays at debug. Single-user systems are still better off keeping the discipline.

## File layout

```
server/skills/search/
├── pyproject.toml                        # huxley-skill-search; depends on huxley-sdk + ddgs
├── src/huxley_skill_search/
│   ├── __init__.py                       # exports SearchSkill
│   ├── skill.py                          # tool dispatch, ToolResult construction, cache, breaker
│   ├── provider.py                       # SearchProvider Protocol + DuckDuckGoProvider impl
│   └── py.typed
└── tests/
    ├── conftest.py                       # FakeSearchProvider (programmable hits / raises)
    └── test_skill.py
```

The provider boundary (`provider.py`) is a Protocol so tests inject a `FakeSearchProvider` — no `ddgs`, no network, no monkey-patching. The same shape lets a future deployer drop in a Brave/SearXNG/Perplexity-backed provider without touching the skill.

## Honest limitations

- **`ddgs` reliability**: DuckDuckGo's HTML/API shifts; the package gets rate-limited under load. The 4s timeout + circuit breaker + recovery messages soften this, but a sustained DDG outage means the skill is just unavailable. No backup provider in v1.
- **No pre-synthesized answer**: the skill returns snippets; the LLM synthesizes. Slightly more tokens than a Tavily/Perplexity-style "answer" field, but free + no API key.
- **Snippet freshness**: DDG snippets are sometimes weeks old. The skill doesn't filter by date — the LLM has to read the snippet to gauge recency.
- **No `recency` parameter**: the LLM rephrases queries naturally ("today X", "this week Y"). A typed `recency` enum was considered and cut as prompt-token waste.
- **Region**: hardcoded `wt-wt` (worldwide). A persona that wants region-skewed results would need an additive config knob; not surfaced today.
