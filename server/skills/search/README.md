# huxley-skill-search

Web search for [Huxley](https://github.com/ma-r-s/Huxley): [`ddgs`](https://pypi.org/project/ddgs/) metasearch with backend fallback, fresh Google News headlines, and excerpts of the best pages. Free, no API key, in-memory cache, circuit-breaker.

> **Status**: bundled with the Huxley repo as a workspace member. The first first-party skill to adopt T1.14's `config_schema` convention — a useful reference for skill authors.

## What it does

- **`search_the_web`**: for anything that may have changed since the model's training. Runs a web search through `ddgs` (Yandex, then Yahoo, then ddgs's own mix, inside 8 s), asks Google News and a news search for fresh articles in parallel, fetches the top pages within 2.5 s and returns compact JSON: `today`, matching `headlines`, and `results` with source, date, snippet and, for the best two, an excerpt of the page.

In-memory TTL cache for repeated identical queries within a session. Consecutive-failure circuit breaker: opens for ~60s after 3 failed searches in a row. Full behavior, measurements and events: [`docs/skills/search.md`](../../../docs/skills/search.md).

## Configure

```yaml
skills:
  search:
    safesearch: "moderate" # off | moderate | strict
    region: co-es # country-language; picks the Google News edition (default wt-wt: none)
    timezone: America/Bogota # what "today" means
    preferred_domains: [eltiempo.com, elespectador.com] # ranked up one place
    sounds_path: "sounds"
    start_sound: search_start # opt-in chime
```

This skill is the canonical example of `config_schema` declared:

```python
config_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "safesearch": {
            "type": "string",
            "enum": ["off", "moderate", "strict"],
            "default": "moderate",
            "title": "Safe search",
            "x-huxley:help": "How aggressively to filter explicit results...",
        }
    },
}
```

(plus `region`, a pattern-checked string). v2's PWA Skills panel will render those with help text. The other config fields (`timezone`, `preferred_domains`, `start_sound`, `sounds_path`) are persona-author / framework-shared plumbing and stay un-schemaed.

## Requirements

- Network access to the search engines ddgs uses, `news.google.com`, and the result pages.
- No API key.

## Development

```bash
uv run --package huxley-skill-search pytest server/skills/search/tests
uv run ruff check server/skills/search
uv run mypy server/skills/search/src
```

## License

MIT — see [`LICENSE`](LICENSE).
