"""Provider-level tests for AlphaVantageClient.

We mock Alpha Vantage's HTTP responses with `pytest-httpx` so the
test suite has no network dependency. Each test pins one corner of
the response surface — see provider.py for the corresponding error
classes.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from huxley_skill_stocks.provider import (
    ALPHA_VANTAGE_BASE,
    AlphaVantageClient,
    AuthError,
    ProviderError,
    RateLimitError,
    UnknownTickerError,
)

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock

# Match every Alpha Vantage call regardless of query params. pytest-httpx
# requires either an exact URL match or a compiled regex; queries vary
# per test (api key, symbol, function), so a prefix regex is right.
_url_pattern = re.compile(re.escape(ALPHA_VANTAGE_BASE) + r"(\?.*)?$")


def _quote_payload(
    symbol: str = "AAPL",
    price: str = "150.00",
    change: str = "1.50",
    change_percent: str = "1.0101%",
    previous_close: str = "148.50",
    volume: str = "12345678",
) -> dict[str, dict[str, str]]:
    return {
        "Global Quote": {
            "01. symbol": symbol,
            "02. open": "149.00",
            "03. high": "151.00",
            "04. low": "148.75",
            "05. price": price,
            "06. volume": volume,
            "07. latest trading day": "2026-05-01",
            "08. previous close": previous_close,
            "09. change": change,
            "10. change percent": change_percent,
        }
    }


@pytest.mark.asyncio
async def test_get_quote_returns_parsed_quote(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_url_pattern, json=_quote_payload())
    client = AlphaVantageClient(api_key="test-key")

    quote = await client.get_quote("AAPL")

    assert quote.symbol == "AAPL"
    assert quote.price == Decimal("150.00")
    assert quote.change == Decimal("1.50")
    assert quote.change_percent == Decimal("1.0101")
    assert quote.previous_close == Decimal("148.50")
    assert quote.volume == 12345678


@pytest.mark.asyncio
async def test_get_quote_uppercases_symbol(httpx_mock: HTTPXMock) -> None:
    # Lower-case ticker still works.
    httpx_mock.add_response(url=_url_pattern, json=_quote_payload())
    client = AlphaVantageClient(api_key="test-key")

    quote = await client.get_quote("aapl")
    assert quote.symbol == "AAPL"

    # And the URL the client built actually uppercased it.
    request = httpx_mock.get_request()
    assert request is not None
    assert "symbol=AAPL" in str(request.url)


@pytest.mark.asyncio
async def test_rate_limit_note_is_classified(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_url_pattern,
        json={"Note": "Thank you for using Alpha Vantage..."},
    )
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(RateLimitError):
        await client.get_quote("AAPL")


@pytest.mark.asyncio
async def test_information_field_also_classifies_as_rate_limit(
    httpx_mock: HTTPXMock,
) -> None:
    # Alpha Vantage uses "Information" for some quota messages now.
    httpx_mock.add_response(
        url=_url_pattern,
        json={"Information": "We have detected your API key as DEMO..."},
    )
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(RateLimitError):
        await client.get_quote("AAPL")


@pytest.mark.asyncio
async def test_error_message_field_is_auth_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_url_pattern,
        json={"Error Message": "Invalid API call. Please retry or visit..."},
    )
    client = AlphaVantageClient(api_key="bogus")

    with pytest.raises(AuthError):
        await client.get_quote("AAPL")


@pytest.mark.asyncio
async def test_empty_global_quote_is_unknown_ticker(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_url_pattern,
        json={"Global Quote": {}},
    )
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(UnknownTickerError):
        await client.get_quote("ZZZZ")


@pytest.mark.asyncio
async def test_missing_global_quote_key_is_unknown_ticker(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_url_pattern,
        json={},
    )
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(UnknownTickerError):
        await client.get_quote("ZZZZ")


@pytest.mark.asyncio
async def test_non_200_is_provider_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_url_pattern, status_code=500)
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(ProviderError):
        await client.get_quote("AAPL")


@pytest.mark.asyncio
async def test_non_json_body_is_provider_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_url_pattern,
        content=b"<html>nginx error</html>",
        headers={"Content-Type": "text/html"},
    )
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(ProviderError):
        await client.get_quote("AAPL")


@pytest.mark.asyncio
async def test_malformed_quote_fields_are_provider_error(
    httpx_mock: HTTPXMock,
) -> None:
    # price is non-numeric — Decimal() rejects it.
    httpx_mock.add_response(
        url=_url_pattern,
        json=_quote_payload(price="not-a-number"),
    )
    client = AlphaVantageClient(api_key="test-key")

    with pytest.raises(ProviderError):
        await client.get_quote("AAPL")


def test_empty_api_key_rejected_at_construction() -> None:
    with pytest.raises(AuthError):
        AlphaVantageClient(api_key="")


@pytest.mark.asyncio
async def test_change_percent_strips_trailing_percent_sign(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_url_pattern,
        json=_quote_payload(change_percent="-2.3456%"),
    )
    client = AlphaVantageClient(api_key="test-key")

    quote = await client.get_quote("AAPL")
    assert quote.change_percent == Decimal("-2.3456")
