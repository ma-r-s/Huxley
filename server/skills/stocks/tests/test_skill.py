"""StocksSkill tests — exercises setup() + handle() against a fake
Alpha Vantage client so the test suite is fully offline.

These tests are also the lint test for `huxley_sdk.testing` from
the consumer side: a third-party skill author should be able to
write tests against the SDK without needing to import internal
runtime modules. If anything breaks here that would break for an
external author too, the fix belongs in the SDK, not in this skill.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from huxley_sdk.testing import make_test_context
from huxley_skill_stocks.provider import (
    AuthError,
    ProviderError,
    Quote,
    RateLimitError,
    UnknownTickerError,
)
from huxley_skill_stocks.skill import StocksSkill

# ----------------------------------------------------------------------
# Fake Alpha Vantage client. Tests construct one, register quotes /
# errors keyed by ticker, then inject it into the skill via the
# constructor's `client` keyword argument.


class FakeClient:
    def __init__(self) -> None:
        self._quotes: dict[str, Quote] = {}
        self._errors: dict[str, Exception] = {}
        self.calls: list[str] = []

    def add_quote(
        self,
        symbol: str,
        *,
        price: str = "150.00",
        change: str = "1.50",
        change_percent: str = "1.0101",
    ) -> None:
        self._quotes[symbol] = Quote(
            symbol=symbol,
            price=Decimal(price),
            change=Decimal(change),
            change_percent=Decimal(change_percent),
            previous_close=Decimal(price) - Decimal(change),
            volume=1_000_000,
        )

    def add_error(self, symbol: str, exc: Exception) -> None:
        self._errors[symbol] = exc

    async def get_quote(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        self.calls.append(symbol)
        if symbol in self._errors:
            raise self._errors[symbol]
        if symbol not in self._quotes:
            msg = f"unknown ticker: {symbol}"
            raise UnknownTickerError(msg)
        return self._quotes[symbol]


async def _setup_skill(
    *,
    config: dict[str, Any] | None = None,
    api_key: str | None = "test-key",
    client: FakeClient | None = None,
) -> tuple[StocksSkill, FakeClient]:
    fake = client or FakeClient()
    skill = StocksSkill(client=fake)  # type: ignore[arg-type]
    ctx = make_test_context(config=config or {})
    if api_key is not None:
        await ctx.secrets.set("api_key", api_key)
    await skill.setup(ctx)
    return skill, fake


def _output(result: Any) -> dict[str, Any]:
    return json.loads(result.output)


# ----------------------------------------------------------------------
# config_schema — the v1 marketplace contract this skill is the
# canonical example of.


def test_config_schema_declares_required_api_key() -> None:
    schema = StocksSkill.config_schema
    assert schema is not None
    assert "api_key" in schema["required"]


def test_config_schema_marks_api_key_as_secret() -> None:
    """`format: "secret"` is the convention v2's PWA renders as a
    password input + routes to ctx.secrets, not into persona.yaml."""
    schema = StocksSkill.config_schema
    assert schema is not None
    assert schema["properties"]["api_key"]["format"] == "secret"


def test_config_schema_covers_three_json_schema_shapes() -> None:
    """Stocks is the worked example for the three field shapes v2's
    PWA form-renderer must support: secret string, array, enum."""
    schema = StocksSkill.config_schema
    assert schema is not None
    props = schema["properties"]
    assert props["api_key"]["type"] == "string"
    assert props["watchlist"]["type"] == "array"
    assert props["currency"]["enum"] == ["USD", "EUR", "GBP", "JPY"]


def test_data_schema_version_starts_at_1() -> None:
    assert StocksSkill.data_schema_version == 1


# ----------------------------------------------------------------------
# setup() — secret loading, config parsing, soft-fail on missing key.


@pytest.mark.asyncio
async def test_setup_reads_api_key_from_ctx_secrets() -> None:
    # If ctx.secrets has the key, skill.setup() builds a client.
    skill, _ = await _setup_skill(api_key="real-key")
    # Skill is configured; calls a tool to confirm.
    assert skill._client is not None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_setup_soft_fails_when_api_key_missing() -> None:
    # Skill registers but `_client` stays None; tools return error
    # results instead of crashing.
    skill, _ = await _setup_skill(api_key=None)
    assert skill._client is None  # type: ignore[attr-defined]

    result = await skill.handle("get_stock_price", {"ticker": "AAPL"})
    body = _output(result)
    assert body["error"] == "not_configured"


@pytest.mark.asyncio
async def test_setup_normalizes_watchlist_uppercase_strips() -> None:
    skill, _ = await _setup_skill(config={"watchlist": [" aapl ", "msft", "", "  "]})
    assert skill._watchlist == ["AAPL", "MSFT"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_setup_falls_back_to_default_currency() -> None:
    skill, _ = await _setup_skill(config={})
    assert skill._currency == "USD"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_setup_uppercases_currency() -> None:
    skill, _ = await _setup_skill(config={"currency": "eur"})
    assert skill._currency == "EUR"  # type: ignore[attr-defined]


# ----------------------------------------------------------------------
# get_stock_price — happy path + every error surface.


@pytest.mark.asyncio
async def test_get_stock_price_returns_payload_with_say_to_user() -> None:
    fake = FakeClient()
    fake.add_quote("AAPL", price="150.00", change="1.50", change_percent="1.0101")
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("get_stock_price", {"ticker": "AAPL"})
    body = _output(result)
    assert body["symbol"] == "AAPL"
    assert body["price"] == "150.00"
    assert body["currency"] == "USD"
    # say_to_user is what the LLM relays — must mention the symbol
    # and price.
    assert "AAPL" in body["say_to_user"]
    assert "150.00" in body["say_to_user"]


@pytest.mark.asyncio
async def test_get_stock_price_lowercase_ticker_is_normalized() -> None:
    fake = FakeClient()
    fake.add_quote("AAPL")
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("get_stock_price", {"ticker": "aapl"})
    body = _output(result)
    assert body["symbol"] == "AAPL"
    assert "AAPL" in fake.calls


@pytest.mark.asyncio
async def test_get_stock_price_missing_ticker_arg_is_invalid_args() -> None:
    skill, _ = await _setup_skill()
    result = await skill.handle("get_stock_price", {})
    body = _output(result)
    assert body["error"] == "invalid_args"


@pytest.mark.asyncio
async def test_get_stock_price_unknown_ticker_returns_friendly_error() -> None:
    fake = FakeClient()
    fake.add_error("ZZZZ", UnknownTickerError("unknown"))
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("get_stock_price", {"ticker": "ZZZZ"})
    body = _output(result)
    assert body["error"] == "unknown_ticker"
    assert "ZZZZ" in body["say_to_user"]


@pytest.mark.asyncio
async def test_get_stock_price_rate_limited_message() -> None:
    fake = FakeClient()
    fake.add_error("AAPL", RateLimitError("rate-limited"))
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("get_stock_price", {"ticker": "AAPL"})
    body = _output(result)
    assert body["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_get_stock_price_auth_failure_message() -> None:
    fake = FakeClient()
    fake.add_error("AAPL", AuthError("bad key"))
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("get_stock_price", {"ticker": "AAPL"})
    body = _output(result)
    assert body["error"] == "auth_failed"


@pytest.mark.asyncio
async def test_get_stock_price_generic_provider_error() -> None:
    fake = FakeClient()
    fake.add_error("AAPL", ProviderError("network blew up"))
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("get_stock_price", {"ticker": "AAPL"})
    body = _output(result)
    assert body["error"] == "provider_error"


# ----------------------------------------------------------------------
# get_watchlist_summary


@pytest.mark.asyncio
async def test_watchlist_summary_returns_each_ticker() -> None:
    fake = FakeClient()
    fake.add_quote("AAPL", price="150")
    fake.add_quote("MSFT", price="300")
    skill, _ = await _setup_skill(config={"watchlist": ["AAPL", "MSFT"]}, client=fake)

    result = await skill.handle("get_watchlist_summary", {})
    body = _output(result)
    symbols = [row["symbol"] for row in body["watchlist"]]
    assert symbols == ["AAPL", "MSFT"]
    assert "AAPL" in body["say_to_user"]
    assert "MSFT" in body["say_to_user"]


@pytest.mark.asyncio
async def test_watchlist_summary_empty_watchlist_returns_friendly_message() -> None:
    skill, _ = await _setup_skill()  # default config: empty watchlist
    result = await skill.handle("get_watchlist_summary", {})
    body = _output(result)
    assert body["watchlist"] == []
    # No `error` field — empty watchlist is a successful "nothing to
    # report" state, not a failure.
    assert "error" not in body


@pytest.mark.asyncio
async def test_watchlist_summary_aggregates_partial_failures() -> None:
    # One ticker errors, the others succeed — summary still returns,
    # but the failing ticker is marked.
    fake = FakeClient()
    fake.add_quote("AAPL")
    fake.add_error("BADX", ProviderError("network"))
    skill, _ = await _setup_skill(config={"watchlist": ["AAPL", "BADX"]}, client=fake)

    result = await skill.handle("get_watchlist_summary", {})
    body = _output(result)
    rows = {row["symbol"]: row for row in body["watchlist"]}
    assert "price" in rows["AAPL"]
    assert rows["BADX"].get("error") == "no_data"


@pytest.mark.asyncio
async def test_watchlist_summary_rate_limit_short_circuits() -> None:
    # Rate-limit on ANY ticker aborts the whole sweep — repeated
    # requests in the same minute would just compound the limit.
    fake = FakeClient()
    fake.add_error("AAPL", RateLimitError("limit"))
    skill, _ = await _setup_skill(config={"watchlist": ["AAPL", "MSFT"]}, client=fake)

    result = await skill.handle("get_watchlist_summary", {})
    body = _output(result)
    assert body["error"] == "rate_limited"


# ----------------------------------------------------------------------
# compare_stocks


@pytest.mark.asyncio
async def test_compare_stocks_two_tickers() -> None:
    fake = FakeClient()
    fake.add_quote("AAPL")
    fake.add_quote("MSFT")
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("compare_stocks", {"tickers": ["AAPL", "MSFT"]})
    body = _output(result)
    symbols = [q["symbol"] for q in body["quotes"]]
    assert symbols == ["AAPL", "MSFT"]
    assert body["missing"] == []


@pytest.mark.asyncio
async def test_compare_stocks_marks_missing() -> None:
    fake = FakeClient()
    fake.add_quote("AAPL")
    fake.add_error("ZZZZ", UnknownTickerError("nope"))
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("compare_stocks", {"tickers": ["AAPL", "ZZZZ"]})
    body = _output(result)
    assert [q["symbol"] for q in body["quotes"]] == ["AAPL"]
    assert body["missing"] == ["ZZZZ"]


@pytest.mark.asyncio
async def test_compare_stocks_all_missing_returns_no_data_error() -> None:
    fake = FakeClient()
    fake.add_error("ZZZZ", UnknownTickerError("nope"))
    fake.add_error("YYYY", UnknownTickerError("nope"))
    skill, _ = await _setup_skill(client=fake)

    result = await skill.handle("compare_stocks", {"tickers": ["ZZZZ", "YYYY"]})
    body = _output(result)
    assert body["error"] == "no_data"


@pytest.mark.asyncio
async def test_compare_stocks_needs_at_least_two_tickers() -> None:
    skill, _ = await _setup_skill()
    result = await skill.handle("compare_stocks", {"tickers": ["AAPL"]})
    body = _output(result)
    assert body["error"] == "invalid_args"


@pytest.mark.asyncio
async def test_compare_stocks_non_list_tickers_is_invalid_args() -> None:
    skill, _ = await _setup_skill()
    result = await skill.handle("compare_stocks", {"tickers": "AAPL"})
    body = _output(result)
    assert body["error"] == "invalid_args"


# ----------------------------------------------------------------------
# Tool registry


def test_skill_declares_three_tools() -> None:
    skill = StocksSkill()
    names = [t.name for t in skill.tools]
    assert names == ["get_stock_price", "get_watchlist_summary", "compare_stocks"]


def test_skill_name_is_stable_id() -> None:
    skill = StocksSkill()
    # The persona's `skills.<name>:` block matches this string; changing
    # it is a breaking change for every user with the skill enabled.
    assert skill.name == "stocks"


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error() -> None:
    skill, _ = await _setup_skill()
    result = await skill.handle("not_a_real_tool", {})
    body = _output(result)
    assert body["error"] == "unknown_tool"
