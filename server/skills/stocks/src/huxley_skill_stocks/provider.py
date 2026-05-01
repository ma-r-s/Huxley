"""Alpha Vantage HTTP client.

Thin wrapper around the GLOBAL_QUOTE endpoint. We keep this isolated
from the skill class so tests can hit it directly with `pytest-httpx`
mocks and so the skill class stays focused on the SDK contract.

Alpha Vantage free-tier quirks worth knowing:

- Rate limit: 25 requests/day, 5 per minute. Hitting it gets a 200
  with a `"Note"` field in the JSON ("Thank you for using Alpha
  Vantage... please consider upgrading"). We surface that as a
  `RateLimitError` so the skill can degrade gracefully.
- Invalid API key: 200 with `"Error Message"` in the JSON, not a
  401. Raised as `AuthError`.
- Unknown ticker: 200 with an empty `"Global Quote"` dict. Raised as
  `UnknownTickerError`.
- Network / timeout / non-200: bubbled as `ProviderError`.

The skill class catches `ProviderError` (the parent) and translates
to LLM-facing strings; tests pin each subclass independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"
DEFAULT_TIMEOUT_S = 5.0


class ProviderError(Exception):
    """Base class for Alpha Vantage failures."""


class RateLimitError(ProviderError):
    """Free-tier daily/per-minute quota hit."""


class AuthError(ProviderError):
    """API key is missing, malformed, or rejected."""


class UnknownTickerError(ProviderError):
    """Alpha Vantage returned an empty quote — ticker is unknown or delisted."""


@dataclass(frozen=True, slots=True)
class Quote:
    """One stock's current snapshot.

    `change_percent` is a Decimal in the range [-100.0, 100.0] (NOT
    a percentage of itself — already divided by 100 when Alpha Vantage
    reports e.g. "1.23%" we store 1.23 here, not 0.0123).
    """

    symbol: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    previous_close: Decimal
    volume: int


class AlphaVantageClient:
    """Async HTTP client for Alpha Vantage's GLOBAL_QUOTE endpoint."""

    def __init__(self, api_key: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        if not api_key:
            msg = "Alpha Vantage API key is empty"
            raise AuthError(msg)
        self._api_key = api_key
        self._timeout = timeout_s

    async def get_quote(self, symbol: str) -> Quote:
        """Fetch a single quote. Raises one of the ProviderError subclasses."""
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": symbol.upper(),
            "apikey": self._api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(ALPHA_VANTAGE_BASE, params=params)
        except httpx.HTTPError as exc:
            msg = f"Alpha Vantage network error: {exc!s}"
            raise ProviderError(msg) from exc

        if response.status_code != 200:
            msg = f"Alpha Vantage HTTP {response.status_code}"
            raise ProviderError(msg)

        try:
            payload = response.json()
        except ValueError as exc:
            msg = "Alpha Vantage returned non-JSON"
            raise ProviderError(msg) from exc

        # Free-tier rate-limit surface — 200 OK with a "Note" / "Information" field.
        if "Note" in payload or "Information" in payload:
            note = payload.get("Note") or payload.get("Information") or "rate limited"
            raise RateLimitError(str(note))

        # Auth surface — 200 OK with an "Error Message" field.
        if "Error Message" in payload:
            raise AuthError(str(payload["Error Message"]))

        quote = payload.get("Global Quote") or {}
        if not quote or not quote.get("01. symbol"):
            msg = f"unknown ticker: {symbol}"
            raise UnknownTickerError(msg)

        try:
            return Quote(
                symbol=str(quote["01. symbol"]),
                price=Decimal(str(quote["05. price"])),
                change=Decimal(str(quote["09. change"])),
                # "10. change percent" is a string like "1.2345%". Strip
                # the trailing % and any whitespace and parse the numeric part.
                change_percent=Decimal(
                    str(quote["10. change percent"]).strip().rstrip("%").strip()
                ),
                previous_close=Decimal(str(quote["08. previous close"])),
                volume=int(str(quote["06. volume"])),
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            msg = f"Alpha Vantage returned malformed quote: {exc!s}"
            raise ProviderError(msg) from exc
