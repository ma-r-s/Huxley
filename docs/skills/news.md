# Skill: news

Persona-agnostic news + weather skill. Returns structured JSON; the LLM
narrates per its persona's tone (slow/warm vs terse/bullets). Same skill,
same JSON, totally different audio depending on which persona consumes it
— see [BasicOS](../personas/basicos.md) as the proof of that abstraction.

## What it does

| Tool          | Args                                                   | Returns                                                          |
| ------------- | ------------------------------------------------------ | ---------------------------------------------------------------- |
| `get_news`    | `query?: string`, `category?: string` (see enum below) | `{ location, fetched_at, weather, items[], item_count, filter }` |
| `get_weather` | —                                                      | `{ location, fetched_at, weather }`                              |

Categories: `local`, `national`, `world`, `sports`, `tech`, `business`,
`science`, `entertainment`, `health`, `weather` (re-routes to `get_weather`).

The skill does **zero narration**. It returns data; the LLM decides how to
speak it based on the persona's `system_prompt` + constraints.

## Data sources (no API keys)

- **Weather**: [Open-Meteo](https://open-meteo.com/) — free, no key, takes
  lat/lng, returns current + today's forecast as JSON. Weather codes are
  WMO integers translated to English condition keys (`partly_cloudy`,
  `light_rain`, …); the LLM translates to the persona's language.
- **News**: [Google News RSS](https://news.google.com/rss) — free, no key,
  filtered by country + language. Default (no args) hits the curated
  "Top stories" feed for the country — Google's algorithm decides what
  matters, the skill doesn't try to out-think it. Categories use
  topic-specific feeds (`/topic/SPORTS`, `/topic/TECHNOLOGY`, etc.).
  Search via `get_news(query=...)` uses the search RSS endpoint.

A 5-minute in-memory TTL cache (configurable) saves a network round-trip
when the user asks for the same slice twice in quick succession.

## Configuration

Persona's `skills.news` block:

| Key                 | Required | Default  | Notes                                                                |
| ------------------- | -------- | -------- | -------------------------------------------------------------------- |
| `location`          | yes      | —        | Human-readable location name; appears in narration prompts           |
| `latitude`          | yes      | —        | For Open-Meteo                                                       |
| `longitude`         | yes      | —        | For Open-Meteo                                                       |
| `country_code`      | yes      | —        | ISO 3166-1 alpha-2 (`CO`, `US`, `ES`, …) for Google News             |
| `language_code`     | yes      | —        | ISO 639-1 (`es`, `en`, …) for Google News + tool descriptions        |
| `units`             | no       | `metric` | `metric` or `imperial`                                               |
| `max_items`         | no       | `8`      | Headlines per fetch                                                  |
| `max_age_hours`     | no       | `24`     | Skip items older than this                                           |
| `interests`         | no       | `[]`     | List of strings; appears in tool description as a hint to the LLM    |
| `cache_ttl_seconds` | no       | `300`    | In-memory cache TTL                                                  |
| `start_sound`       | no       | _none_   | Sound palette role (e.g. `news_start`); omit for no chime            |
| `sounds_path`       | no       | `sounds` | Sound palette directory (relative to persona `data_dir` or absolute) |

## How personas consume it differently

**[AbuelOS](../personas/abuelos.md)** uses it as a slow, warm digest with a
chime intro. The persona's `system_prompt` tells the LLM to "narrate news
like you're telling a friend what happened today." Combined with
`never_say_no` and `start_sound: news_start`, the user hears: pre-narration
("a ver, le cuento") → chime → ~60s narrated digest → "¿quiere que le
cuente más?".

**[BasicOS](../personas/basicos.md)** uses it as a terse 5-bullet briefing.
Same skill, same JSON, no chime, no `never_say_no`. The persona's
`system_prompt` says "máximo cinco puntos, cada uno una sola frase." User
hears: pre-narration ("un momento") → 5 short bullets, period.

If the news skill ever assumes "warm tone" or "always plays a chime,"
BasicOS surfaces it. The split is enforced by the architecture, not by
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

- **No hyperlocal news.** Google News indexes by country + language +
  topic, not by city. So `category: "local"` for an AbuelOS configured
  for Villavicencio = "Colombian national news," not "what happened in
  Villavicencio yesterday." Real city-level coverage would need RSS
  feeds from local outlets — additive future work via a `local_feeds:
[...]` config field.
- **No "since last check" dedupe.** Asking for news twice in 6 minutes
  gets you the same content (cache TTL). Cross-session dedupe would
  need persistent storage; deferred until users actually complain.
- **WMO weather codes returned as English keys.** LLMs translate
  fluidly into the persona's language at narration time; one less
  per-language translation table to maintain.

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
| Cache hit                            | No HTTP request; returns cached payload as-is                                          |
| Empty news feed                      | Returns weather + `items: []`; LLM phrases the gap                                     |
| `start_sound` configured but missing | Logs `news.start_sound_missing` warning; runs silently                                 |
| Required config missing              | Skill `setup()` raises `ValueError` — startup fails fast with a clear message          |
