# huxley-skill-news

News headlines + weather for [Huxley](https://github.com/ma-r-s/Huxley). Google News RSS + Open-Meteo, no API keys.

> **Status**: bundled with the Huxley repo as a workspace member.

## What it does

- **`get_news`** — "what's the news" — fetches Google News RSS for the persona's country, filters by configured `interests`, returns up to `max_items` headlines with timestamps.
- **`get_weather`** — "what's the weather" — Open-Meteo forecast for the persona's lat/lon. No API key.

Both tools cache for `cache_ttl_seconds` (default 300s) so repeated requests within a turn don't re-fetch.

## Configure

```yaml
skills:
  news:
    location: "Villavicencio"
    latitude: 4.142
    longitude: -73.626
    country_code: "CO"
    language_code: "en" # feed language (separate from the persona's UI language)
    units: "metric" # or "imperial"
    interests: ["economía", "tecnología"]
    max_items: 5
    max_age_hours: 24
    cache_ttl_seconds: 300
    start_sound: news_start # opt-in chime; persona-shared earcon palette
    sounds_path: "sounds"
```

`config_schema = None` — per-language i18n overrides + the user-defined `interests` list shape mean v2's PWA falls back to "edit YAML directly."

## Requirements

- Network access to `news.google.com/rss/...` and `api.open-meteo.com`.
- No API keys — both upstreams are public.

## Development

```bash
uv run --package huxley-skill-news pytest server/skills/news/tests
uv run ruff check server/skills/news
uv run mypy server/skills/news/src
```

Tests use `pytest-httpx` to mock both upstreams; no network in CI.

## License

MIT — see [`LICENSE`](LICENSE).
