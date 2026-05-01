# huxley-skill-stocks

Voice-controlled stock quotes for [Huxley](https://github.com/ma-r-s/Huxley), the voice agent framework. A reference skill demonstrating the third-party authoring path: own repo, PyPI distribution, `config_schema` form-rendering, `ctx.secrets` for API keys.

> **Status**: Reference skill for Huxley T1.14 (skill marketplace v1). Built and tested against `huxley-sdk` from the Huxley repo's main branch during v1 development. Switches to a versioned PyPI pin once `huxley-sdk` is published.

## What it does

Three voice tools on top of the [Alpha Vantage](https://www.alphavantage.co/) free tier:

- **`get_stock_price(ticker)`** — "what's Apple stock at" / "how is TSLA doing"
- **`get_watchlist_summary()`** — "how's my watchlist" — summarizes the persona's configured tickers
- **`compare_stocks(tickers)`** — "compare Apple and Microsoft" — short side-by-side

## Install

Requirements: Python 3.13+ (matches Huxley's runtime).

```bash
# From a Huxley persona's runtime venv:
uv add huxley-skill-stocks

# Or, while developing against an unreleased huxley-sdk:
uv add "huxley-skill-stocks @ git+https://github.com/ma-r-s/huxley-skill-stocks"
```

The skill registers under the entry-point name `stocks`. The skill name in `persona.yaml`'s `skills:` block, the per-persona secrets dir (`<persona>/data/secrets/<name>/`), and the entry-point key in `pyproject.toml` must all match.

## Configure

Add to your persona's `persona.yaml`:

```yaml
skills:
  stocks:
    watchlist: ["AAPL", "MSFT", "GOOG"]
    currency: USD
    # api_key lives in <persona>/data/secrets/stocks/values.json
```

Drop your Alpha Vantage API key in `<persona>/data/secrets/stocks/values.json`:

```json
{
  "api_key": "your-alpha-vantage-key"
}
```

Get a free key at [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key) — long-lived, no OAuth refresh.

Restart Huxley, press to talk: "what's Apple stock at?"

## Why this skill exists

This is Huxley's **canonical third-party reference skill** — the worked example the [authoring docs](https://github.com/ma-r-s/Huxley/blob/main/docs/skills/authoring.md) walk through. It's deliberately minimal but exercises every primitive a real third-party skill needs:

- **`config_schema`** declared (secret string + array + enum — the three JSON-Schema shapes Huxley's PWA renders).
- **`ctx.secrets`** for the API key (per-persona, `<persona>/data/secrets/stocks/values.json`).
- **`ctx.config`** for plain config (watchlist, currency).
- **Standalone repo** — proves the third-party authoring flow works end-to-end without dropping the skill into Huxley's `server/skills/` tree.
- **No OAuth** — Alpha Vantage uses a long-lived API key, so this skill doesn't drag token-refresh complexity into Huxley's v1 marketplace.

## Development

```bash
uv sync
uv run ruff check src tests
uv run mypy src
uv run pytest
```

Tests use `pytest-httpx` to mock Alpha Vantage responses; no network in CI.

## License

MIT.
