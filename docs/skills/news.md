# Skill: news

Persona-agnostic news + weather skill. Returns structured JSON; the LLM
narrates per its persona's tone (slow/warm vs terse/bullets). Same skill,
same JSON, totally different audio depending on which persona consumes it
— see [Basic](../personas/basic.md) as the proof of that abstraction.

## What it does

| Tool          | Args                                                   | Returns                                                          |
| ------------- | ------------------------------------------------------ | ---------------------------------------------------------------- |
| `get_news`    | `query?: string`, `category?: string` (see enum below) | `{ location, fetched_at, weather, interests, filter, items[], item_count }` |
| `get_weather` | none                                                   | `{ location, fetched_at, weather }`                              |

Categories: `local`, `national`, `world`, `sports`, `tech`, `business`,
`science`, `entertainment`, `health`, `weather` (re-routes to `get_weather`).

Each item is `{ title, source, age_hours, related? }`. `related` holds up
to two other outlets' headlines for the same story, as "Headline
(Source)", taken from the cluster Google puts in the item's description.
They say what a bare headline leaves out (on 2026-10-13 the model read
"the government filed a bill for something called sometimiento"; the
related headline was "rebaja punitiva del 50% de la pena"). `weather`
is `{ current, today, tomorrow, units }`; `today` and `tomorrow` carry
`high`, `low`, `condition_key` and `rain_chance_pct`.

The skill does **zero narration**. It returns data; the LLM decides how to
speak it based on the persona's `system_prompt` + constraints.

## Data sources (no API keys)

- **Weather**: [Open-Meteo](https://open-meteo.com/) — free, no key, takes
  lat/lng, returns current + today's and tomorrow's forecast as JSON. Weather codes are
  WMO integers translated to English condition keys (`partly_cloudy`,
  `light_rain`, …); the LLM translates to the persona's language.
- **News**: [Google News RSS](https://news.google.com/rss) — free, no key,
  filtered by country + language. Default (no args) hits the curated
  "Top stories" feed for the country; Google's algorithm decides what
  matters, the skill doesn't try to out-think it. Categories use
  topic-specific feeds (`/topic/SPORTS`, `/topic/TECHNOLOGY`, etc.).
  Search via `get_news(query=...)` uses the search RSS endpoint, and so
  does `category: "local"`, with the persona's `local_query` (Google
  News has no city feed).
- **Freshness and copies** (T1.48): searches add `when:3d` so Google
  returns recent stories (a plain "Villavicencio" search came back
  relevance-ordered with 5 of 101 items from the last day) and keep
  items up to 72 hours old; the top-stories and topic feeds keep
  `max_age_hours` (24). Within one fetch, an item whose content words
  mostly repeat an earlier one's (60% of the shorter title) is dropped:
  one road accident came back as five headlines from five outlets. A
  title that repeats its outlet ("... | El Colombiano - El Colombiano")
  is trimmed.

A 5-minute in-memory TTL cache (configurable) saves a network round-trip
when the user asks for the same slice twice in quick succession.

## Configuration

Persona's `skills.news` block:

| Key                 | Required | Default  | Notes                                                                                                                                                                                                   |
| ------------------- | -------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `location`          | yes      | —        | Human-readable location name; appears in narration prompts                                                                                                                                              |
| `latitude`          | yes      | —        | For Open-Meteo                                                                                                                                                                                          |
| `longitude`         | yes      | —        | For Open-Meteo                                                                                                                                                                                          |
| `country_code`      | yes      | —        | ISO 3166-1 alpha-2 (`CO`, `US`, `ES`, …) for Google News                                                                                                                                                |
| `language_code`     | yes      | —        | ISO 639-1 (`es`, `en`, …) for Google News + tool descriptions                                                                                                                                           |
| `units`             | no       | `metric` | `metric` or `imperial`                                                                                                                                                                                  |
| `max_items`         | no       | `8`      | Headlines per fetch                                                                                                                                                                                     |
| `max_age_hours`     | no       | `24`     | Skip items older than this (searches and `local` look back at least 72 h)                                                                                                                               |
| `local_query`       | no       | `location` | Google News search text for `category: local`, e.g. `Villavicencio OR "del Meta"` (quotes keep Meta the company out)                                                                                   |
| `interests`         | no       | `[]`     | List of strings; appears in tool description as a hint to the LLM                                                                                                                                       |
| `cache_ttl_seconds` | no       | `300`    | In-memory cache TTL                                                                                                                                                                                     |
| `start_sound`       | no       | _none_   | Sound palette role (e.g. `news_start`); omit for no chime                                                                                                                                               |
| `sounds_path`       | no       | `sounds` | Sound palette directory (relative to persona `data_dir` or absolute). Personas typically point this at `../../_shared/sounds` to use the framework-shared palette — see [`../sounds.md`](../sounds.md). |

## How personas consume it differently

**[Abuelo](../personas/abuelos.md)** uses it as a slow, warm digest with a
chime intro. The persona's `system_prompt` tells the LLM to "narrate news
like you're telling a friend what happened today." Combined with
`never_say_no` and `start_sound: news_start`, the user hears: pre-narration
("a ver, le cuento") → chime → ~60s narrated digest → "¿quiere que le
cuente más?".

**[Basic](../personas/basic.md)** uses it as a terse 5-bullet briefing.
Same skill, same JSON, no chime, no `never_say_no`. The persona's
`system_prompt` says "máximo cinco puntos, cada uno una sola frase." User
hears: pre-narration ("un momento") → 5 short bullets, period.

If the news skill ever assumes "warm tone" or "always plays a chime,"
Basic surfaces it. The split is enforced by the architecture, not by
discipline.

## The chime mechanism (PlaySound)

When `start_sound` is set and the WAV exists in the persona's sound
palette (`sounds/<role>.wav`, PCM16/24kHz/mono), `get_news` returns
`ToolResult(side_effect=PlaySound(pcm=...))`. The coordinator queues the
chime PCM right after firing `request_response()` for the follow-up round
— it lands on the WebSocket ahead of the model's audio deltas (FIFO),
so the user hears chime → model voice with no gap. See
[`docs/sounds.md`](../sounds.md) for the full mechanism.

## Pre-narration hint

The `get_news` tool description tells the LLM to say something brief
("a ver" / "un momento") **before** invoking the tool. Network round-trip
to Google News + Open-Meteo is real wall-clock time (~0.5–2s); without
the hint, the user gets 1–2s of dead air after PTT release before the
chime even fires. The pre-narration covers the fetch.

This is purely a tool-description trick — no code in the coordinator.
The audiobook skill does the same for `play_audiobook` ("ahí le pongo
el libro").

## Honest limitations

- **Local news is a search, not a feed.** `category: "local"` searches
  Google News for `local_query`; it finds what Google indexed that names
  the place (regional papers, national outlets), ranked by relevance,
  and can include a person who happens to share the city's name.
- **No "since last check" dedupe.** Asking for news twice in 6 minutes
  gets you the same content (cache TTL). Copies are only removed within
  one fetch. Cross-session dedupe would need persistent storage;
  deferred until users actually complain.
- **No article text.** Google News links are encoded redirects; the
  model gets headlines (and `related` headlines), not summaries.
- **WMO weather codes returned as English keys.** LLMs translate
  fluidly into the persona's language at narration time; one less
  per-language translation table to maintain.

## Observability

| Event                     | When                         | Fields |
| ------------------------- | ---------------------------- | ------ |
| `news.setup_complete`     | At skill init                | `location`, `country`, `feed_language`, `ui_language`, `interests`, `local_query`, `chime` |
| `news.reconfigure`        | On every session connect     | `ui_language`, `feed_language` |
| `news.fetched`            | `get_news` fetched fresh     | `query_hash` (sha256[:8]; the query itself only at debug, `news.fetched_query`), `category`, `items`, `with_related` (items with related headlines), `oldest_age_h`, `latency_ms` |
| `news.weather_fetched`    | `get_weather` fetched fresh  | (none) |
| `news.cache_hit`          | TTL cache hit                | `key` (`news:<query_hash>:<category>` or `weather`), `items` |
| `news.fetch_failed`       | Upstream failed (warning)    | `url`, `reason` |
| `news.start_sound_missing`| Chime configured, file absent | `role`, `path` |

## File layout

```
server/skills/news/
├── pyproject.toml                   # huxley-skill-news; depends on huxley-sdk + httpx
├── src/huxley_skill_news/
│   ├── __init__.py                  # exports NewsSkill
│   ├── skill.py                     # tool dispatch, ToolResult construction
│   ├── fetcher.py                   # WeatherFetcher + NewsFetcher (Open-Meteo, Google News)
│   ├── http.py                      # HttpClient Protocol + HttpxClient impl
│   └── py.typed
└── tests/
    ├── conftest.py                  # FakeHttpClient (dict-backed test double)
    └── test_skill.py
```

The HTTP boundary (`http.py`) is a Protocol so tests inject a
`FakeHttpClient({url: response_text})` — no `httpx` in the test path,
no `respx` dependency, no monkey-patching.

## Failure modes

| Failure                              | Behavior                                                                               |
| ------------------------------------ | -------------------------------------------------------------------------------------- |
| Network timeout / DNS / non-2xx      | Returns `{ error: "fetch_failed", reason: "...", retry_after_seconds: 60 }`. No chime. |
| Open-Meteo returns one day only      | `tomorrow` fields are `null`; nothing else changes                                     |
| Cache hit                            | No HTTP request; returns cached payload as-is                                          |
| Empty news feed                      | Returns weather + `items: []`; LLM phrases the gap                                     |
| `start_sound` configured but missing | Logs `news.start_sound_missing` warning; runs silently                                 |
| Required config missing              | Skill `setup()` raises `ValueError` — startup fails fast with a clear message          |
