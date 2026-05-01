"""StocksSkill — the worked-example reference for Huxley's third-party
authoring path. Voice-controlled stock quotes via Alpha Vantage.

What this skill demonstrates (and the authoring docs walk through):

- Declares `config_schema` with all three JSON-Schema shapes Huxley's
  PWA renders: a secret string (`api_key`), an array of strings
  (`watchlist`), and a string enum (`currency`). v2's PWA Skills panel
  generates an install form from this schema.
- Reads the API key from `ctx.secrets.get("api_key")` — the per-persona
  secrets store at `<persona>/data/secrets/stocks/values.json`.
- Reads plain (non-secret) config from `ctx.config` — the merged view
  of the persona's `skills.stocks:` block.
- Declares `data_schema_version` so the runtime can warn if a future
  bump leaves persisted data behind on an existing persona.
- Soft-fails when the API key is missing or invalid — the skill
  registers but the tools return LLM-facing error strings instead of
  preventing the persona from booting.

See https://github.com/marioruizsa/Huxley/blob/main/docs/skills/authoring.md
for the full walkthrough.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from huxley_sdk import ToolDefinition, ToolResult
from huxley_skill_stocks.provider import (
    AlphaVantageClient,
    AuthError,
    ProviderError,
    Quote,
    RateLimitError,
    UnknownTickerError,
)

if TYPE_CHECKING:
    from huxley_sdk import SkillContext, SkillLogger


# JSON Schema 2020-12 for the user-tunable config fields. The PWA
# (v2) renders one form field per property; the two custom extensions
# `format: "secret"` and `x-huxley:help` carry render hints. See
# docs/skill-marketplace.md § Config schema convention.
_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["api_key"],
    "properties": {
        "api_key": {
            "type": "string",
            "format": "secret",
            "title": "Alpha Vantage API key",
            "x-huxley:help": (
                "Get a free key at https://www.alphavantage.co/support/#api-key "
                "(no credit card; long-lived; 25 requests/day on the free tier)."
            ),
        },
        "watchlist": {
            "type": "array",
            "items": {"type": "string"},
            "title": "Default watchlist",
            "x-huxley:help": (
                "Ticker symbols to summarize when the user asks "
                '\'how\'s my watchlist\'. Example: `["AAPL", "MSFT", "GOOG"]`.'
            ),
            "default": [],
        },
        "currency": {
            "type": "string",
            "enum": ["USD", "EUR", "GBP", "JPY"],
            "default": "USD",
            "title": "Display currency",
            "x-huxley:help": (
                "Currency to mention in the spoken response. Alpha Vantage "
                "always returns USD; this is a presentation hint only — no "
                "FX conversion happens today."
            ),
        },
    },
}


class StocksSkill:
    """Voice-controlled stock quotes via Alpha Vantage."""

    config_schema: ClassVar[dict[str, Any] | None] = _CONFIG_SCHEMA
    data_schema_version: ClassVar[int] = 1

    def __init__(self, *, client: AlphaVantageClient | None = None) -> None:
        # `client` is keyword-only and reserved for tests that inject a
        # fake. Production setup() builds an AlphaVantageClient from
        # the API key in ctx.secrets.
        self._client: AlphaVantageClient | None = client
        self._logger: SkillLogger | None = None
        self._watchlist: list[str] = []
        self._currency: str = "USD"

    @property
    def name(self) -> str:
        return "stocks"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_stock_price",
                description=(
                    "Get the current price of a stock by ticker symbol. "
                    "Use this when the user asks about a specific company "
                    "or stock — e.g. 'what's Apple stock at', 'how is Tesla "
                    "doing today'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": (
                                "Stock ticker symbol, e.g. AAPL, MSFT, GOOG. "
                                "If the user names a company, resolve to the "
                                "primary US listing yourself before calling."
                            ),
                        }
                    },
                    "required": ["ticker"],
                },
            ),
            ToolDefinition(
                name="get_watchlist_summary",
                description=(
                    "Summarize the user's configured watchlist — current "
                    "price + day change for each ticker. Use this when the "
                    "user asks 'how's my watchlist' or 'what are my stocks "
                    "doing'. Returns an empty summary if no watchlist is "
                    "configured."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="compare_stocks",
                description=(
                    "Compare two or more stocks side-by-side. Use when the "
                    "user asks to compare stocks — 'compare Apple and "
                    "Microsoft', 'how does Tesla compare to Ford'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tickers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 5,
                            "description": "Two to five ticker symbols.",
                        }
                    },
                    "required": ["tickers"],
                },
            ),
        ]

    async def setup(self, ctx: SkillContext) -> None:
        self._logger = ctx.logger

        # Plain config from persona.yaml.
        cfg = ctx.config
        raw_watchlist = cfg.get("watchlist") or []
        self._watchlist = [
            str(t).upper().strip() for t in raw_watchlist if isinstance(t, str) and t.strip()
        ]
        currency = cfg.get("currency", "USD")
        self._currency = str(currency).upper() if isinstance(currency, str) else "USD"

        # API key from the per-persona secrets store. Soft-fail if
        # missing — the skill registers but tools return LLM-facing
        # errors so the user hears "I can't reach Alpha Vantage right
        # now" instead of the persona refusing to boot.
        api_key = await ctx.secrets.get("api_key")
        if not api_key:
            await self._logger.awarning(
                "stocks.api_key_missing",
                hint=(
                    "Drop {api_key: <key>} in <persona>/data/secrets/stocks/"
                    "values.json. Get a free key at "
                    "https://www.alphavantage.co/support/#api-key."
                ),
            )
            self._client = None
            return

        # Reuse a test-injected client if one was passed; otherwise
        # build the real one. The test path skips network entirely.
        if self._client is None:
            self._client = AlphaVantageClient(api_key)
        await self._logger.ainfo(
            "stocks.setup_complete",
            watchlist_size=len(self._watchlist),
            currency=self._currency,
        )

    async def handle(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if tool_name == "get_stock_price":
            ticker = str(args.get("ticker", "")).strip().upper()
            if not ticker:
                return _error("invalid_args", "I need a ticker symbol to look that up.")
            return await self._get_stock_price(ticker)

        if tool_name == "get_watchlist_summary":
            return await self._get_watchlist_summary()

        if tool_name == "compare_stocks":
            tickers_raw = args.get("tickers") or []
            if not isinstance(tickers_raw, list):
                return _error("invalid_args", "I need a list of tickers to compare.")
            tickers = [str(t).strip().upper() for t in tickers_raw if isinstance(t, str)]
            if len(tickers) < 2:
                return _error("invalid_args", "I need at least two tickers to compare.")
            return await self._compare_stocks(tickers)

        return _error("unknown_tool", f"I don't know how to handle '{tool_name}'.")

    # ------------------------------------------------------------------

    async def _get_stock_price(self, ticker: str) -> ToolResult:
        if self._client is None:
            return _error(
                "not_configured",
                "The stocks skill isn't set up yet — the Alpha Vantage API key is missing.",
            )
        try:
            quote = await self._client.get_quote(ticker)
        except UnknownTickerError:
            return _error(
                "unknown_ticker",
                f"I couldn't find a stock with ticker {ticker}. Double-check the symbol.",
            )
        except RateLimitError:
            return _error(
                "rate_limited",
                "The free Alpha Vantage tier is rate-limited right now — try again in a minute.",
            )
        except AuthError:
            return _error(
                "auth_failed",
                "The Alpha Vantage API key was rejected. Check the key file and try again.",
            )
        except ProviderError as exc:
            assert self._logger is not None
            await self._logger.awarning("stocks.provider_error", ticker=ticker, error=str(exc))
            return _error(
                "provider_error",
                "I couldn't reach the stock data service. Try again in a moment.",
            )

        return _ok(
            {
                "symbol": quote.symbol,
                "price": str(quote.price),
                "change": str(quote.change),
                "change_percent": str(quote.change_percent),
                "previous_close": str(quote.previous_close),
                "volume": quote.volume,
                "currency": self._currency,
                "say_to_user": self._format_quote(quote),
            }
        )

    async def _get_watchlist_summary(self) -> ToolResult:
        if not self._watchlist:
            return _ok(
                {
                    "watchlist": [],
                    "say_to_user": (
                        "Your watchlist is empty. Add tickers under "
                        "skills.stocks.watchlist in persona.yaml to populate it."
                    ),
                }
            )
        if self._client is None:
            return _error(
                "not_configured",
                "The stocks skill isn't set up yet — the API key is missing.",
            )

        rows: list[dict[str, Any]] = []
        lines: list[str] = []
        for ticker in self._watchlist:
            try:
                quote = await self._client.get_quote(ticker)
            except RateLimitError:
                return _error(
                    "rate_limited",
                    "I've hit the Alpha Vantage rate limit while pulling your "
                    "watchlist. Try again in a minute.",
                )
            except ProviderError:
                rows.append({"symbol": ticker, "error": "no_data"})
                lines.append(f"{ticker}: no data")
                continue
            rows.append(
                {
                    "symbol": quote.symbol,
                    "price": str(quote.price),
                    "change_percent": str(quote.change_percent),
                }
            )
            lines.append(self._format_quote_line(quote))

        return _ok(
            {
                "watchlist": rows,
                "currency": self._currency,
                "say_to_user": "Watchlist summary: " + "; ".join(lines),
            }
        )

    async def _compare_stocks(self, tickers: list[str]) -> ToolResult:
        if self._client is None:
            return _error(
                "not_configured",
                "The stocks skill isn't set up yet — the API key is missing.",
            )
        quotes: list[Quote] = []
        missing: list[str] = []
        for ticker in tickers:
            try:
                quotes.append(await self._client.get_quote(ticker))
            except UnknownTickerError:
                missing.append(ticker)
            except RateLimitError:
                return _error(
                    "rate_limited",
                    "I've hit the Alpha Vantage rate limit. Try again in a minute.",
                )
            except ProviderError:
                missing.append(ticker)

        if not quotes:
            return _error(
                "no_data",
                "I couldn't fetch quotes for any of those tickers. Double-check the symbols.",
            )

        body_lines = [self._format_quote_line(q) for q in quotes]
        if missing:
            body_lines.append(f"(no data for {', '.join(missing)})")
        return _ok(
            {
                "quotes": [
                    {
                        "symbol": q.symbol,
                        "price": str(q.price),
                        "change_percent": str(q.change_percent),
                    }
                    for q in quotes
                ],
                "missing": missing,
                "currency": self._currency,
                "say_to_user": "Comparison: " + "; ".join(body_lines),
            }
        )

    # ------------------------------------------------------------------
    # Formatting helpers — kept simple. The LLM rephrases as needed.

    def _format_quote(self, quote: Quote) -> str:
        direction = "up" if quote.change >= 0 else "down"
        return (
            f"{quote.symbol} is at {quote.price} {self._currency}, "
            f"{direction} {abs(quote.change)} ({abs(quote.change_percent)}%) "
            f"from yesterday's close of {quote.previous_close}."
        )

    def _format_quote_line(self, quote: Quote) -> str:
        sign = "+" if quote.change >= 0 else "-"
        return (
            f"{quote.symbol}: {quote.price} {self._currency} ({sign}{abs(quote.change_percent)}%)"
        )


# Module-level result helpers. Keeping them outside the class makes
# them trivially importable for tests that want to assert against a
# specific error_kind. Convention copied from `huxley-skill-search` /
# `huxley-skill-news`: success packs domain data + `say_to_user`;
# error packs `error` (machine-readable kind) + `say_to_user` (the
# string the LLM relays).


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(output=json.dumps(payload, ensure_ascii=False))


def _error(kind: str, say_to_user: str) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {"error": kind, "say_to_user": say_to_user},
            ensure_ascii=False,
        )
    )
