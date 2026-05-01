# huxley-skill-search

Web search for [Huxley](https://github.com/ma-r-s/Huxley) via DuckDuckGo. Free, no API key, in-memory cache, circuit-breaker.

> **Status**: bundled with the Huxley repo as a workspace member. The first first-party skill to adopt T1.14's `config_schema` convention — a useful reference for skill authors.

## What it does

- **`search_the_web`** — "search for..." / "look up..." / "what is..." — runs a DuckDuckGo query via the [`ddgs`](https://pypi.org/project/ddgs/) package and returns the top hits as structured JSON for the LLM to narrate.

In-memory TTL cache for repeated identical queries within a session. Consecutive-failure circuit breaker: opens for ~60s on N back-to-back failures so a DDG outage doesn't make every query hang for 4s.

## Configure

```yaml
skills:
  search:
    safesearch: "moderate" # off | moderate | strict
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

v2's PWA Skills panel will render that into a single dropdown with help text. The other config fields (`start_sound`, `sounds_path`) are persona-author / framework-shared plumbing and stay un-schemaed.

## Requirements

- Network access to `duckduckgo.com`.
- No API key.

## Development

```bash
uv run --package huxley-skill-search pytest server/skills/search/tests
uv run ruff check server/skills/search
uv run mypy server/skills/search/src
```

## License

MIT — see [`LICENSE`](LICENSE).
