"""Search-provider boundary as a Protocol so tests inject a fake.

Production uses `DuckDuckGoProvider`, which wraps the `ddgs` library.
Tests build a `FakeSearchProvider` that satisfies the same Protocol
without touching the network. Same pattern as the news skill's
`HttpClient` / `FakeHttpClient`.

Failure modes are surfaced as discrete exception types so the skill
can disambiguate empty results from rate-limited/timeout/error paths
— the critic's "empty 202 from DDG looks like success with zero
results" failure mode is the reason this lives at the provider boundary,
not in the skill.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search result, normalized across providers.

    `source` is the URL's hostname with leading `www.` stripped — the
    skill pre-extracts it because realtime LLMs sometimes spell URLs
    letter-by-letter ("e ele pe a ese punto com") instead of citing
    sources by name. Giving them a clean string ("elpais.com") avoids
    that.
    """

    title: str
    url: str
    snippet: str
    source: str


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Provider's response on a clean call. `hits` may be empty."""

    hits: list[SearchHit]


class SearchProviderError(Exception):
    """Generic search failure — network error, parse error, etc."""


class SearchRateLimitedError(SearchProviderError):
    """Provider signaled rate limiting (HTTP 202, 429, or library equivalent)."""


class SearchTimeoutError(SearchProviderError):
    """Provider didn't return within the configured deadline."""


class SearchProvider(Protocol):
    """Minimal async surface the skill needs from a search backend."""

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
    ) -> SearchResponse:
        """Run `query`, return up to `max_results` hits.

        `safesearch` is one of `"off"`, `"moderate"`, `"strict"` — same
        vocabulary the LLM-facing config exposes. Provider implementations
        translate to whatever their backend uses.

        Raises:
            SearchRateLimitedError: backend signaled throttling.
            SearchTimeoutError: backend exceeded deadline.
            SearchProviderError: any other failure.
        """
        ...


# --- Production: DuckDuckGo via the `ddgs` package ---


def _extract_source(url: str) -> str:
    """Pull a clean hostname out of a URL for `source` display.

    Returns empty string for malformed URLs — caller decides how to
    handle. Doesn't try to map to a "human" name (`bbc.co.uk` stays
    `bbc.co.uk`); the LLM cites it naturally enough.
    """
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname or ""
    except (ValueError, AttributeError):
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


class DuckDuckGoProvider:
    """`SearchProvider` impl backed by the `ddgs` package.

    `ddgs.DDGS().text(...)` is synchronous, so calls run in a worker
    thread under `asyncio.wait_for` to bound latency — DDG occasionally
    hangs and a 30-second hang would rot the voice turn. 4-second hard
    cap is the default; configurable per-instance for tests.
    """

    def __init__(self, *, timeout_s: float = 4.0) -> None:
        self._timeout_s = timeout_s

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
    ) -> SearchResponse:
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self._search_sync,
                    query,
                    max_results,
                    safesearch,
                ),
                timeout=self._timeout_s,
            )
        except TimeoutError as exc:
            raise SearchTimeoutError(f"deadline_exceeded:{self._timeout_s}s") from exc
        except Exception as exc:
            # ddgs raises a small family of exceptions
            # (`RatelimitException`, `TimeoutException`, `DDGSException`)
            # under `ddgs.exceptions`, but the exact module path has
            # shifted between versions. Match by class name to stay
            # robust across upgrades — costs nothing, avoids a brittle
            # import.
            cls = type(exc).__name__.lower()
            if "ratelimit" in cls or "rate_limit" in cls:
                raise SearchRateLimitedError(str(exc)) from exc
            if "timeout" in cls:
                raise SearchTimeoutError(str(exc)) from exc
            raise SearchProviderError(f"{type(exc).__name__}:{exc}") from exc

        hits: list[SearchHit] = []
        for entry in raw:
            url = str(entry.get("href") or entry.get("url") or "")
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=str(entry.get("title") or "").strip(),
                    url=url,
                    snippet=str(entry.get("body") or entry.get("snippet") or "").strip(),
                    source=_extract_source(url),
                )
            )
        return SearchResponse(hits=hits)

    @staticmethod
    def _search_sync(query: str, max_results: int, safesearch: str) -> list[dict[str, str]]:
        # Local import: keeps import-time cost (and the `ddgs`
        # dependency) out of test paths that only touch the Protocol.
        from ddgs import DDGS

        with DDGS() as client:
            results = client.text(
                query=query,
                region="wt-wt",  # worldwide, no regional skew — query language guides relevance
                safesearch=safesearch,
                max_results=max_results,
            )
            return list(results)
