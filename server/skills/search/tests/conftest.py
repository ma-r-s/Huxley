"""Test fixtures for huxley-skill-search.

`FakeSearchProvider` is the test double for the search-provider boundary
(a programmable list of hits or an exception to raise) and
`FakePageFetcher` the one for page and feed GETs (a dict of URL
substring to body, error or delay). No `ddgs`, no network, no
monkey-patching.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from huxley_skill_search.pages import PageFetchError
from huxley_skill_search.provider import (
    SearchAttempt,
    SearchHit,
    SearchProviderError,
    SearchRateLimitedError,
    SearchResponse,
    SearchTimeoutError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@dataclass
class FakeSearchProvider:
    """In-memory `SearchProvider` impl.

    Set `hits` for a successful response, or `raises` to an exception
    instance for a failure path. Tracks each call so tests can assert
    `safesearch`, `region` and `max_results` were forwarded.

    `raises` is consumed FIFO when set to a list: useful for the
    circuit-breaker test which needs N consecutive failures followed
    by a success.
    """

    hits: list[SearchHit] = field(default_factory=list)
    # `BaseException` (not `Exception`) so tests can inject
    # `asyncio.CancelledError`.
    raises: BaseException | list[BaseException] | None = None
    backend: str = "fake"
    # The news lane: articles returned by `news()` (never raises).
    news_hits: list[SearchHit] = field(default_factory=list)
    news_outcome: str | None = None  # override, e.g. "timeout"
    calls: list[dict[str, object]] = field(default_factory=list)
    news_calls: list[dict[str, object]] = field(default_factory=list)

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
        region: str,
    ) -> SearchResponse:
        self.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "safesearch": safesearch,
                "region": region,
            }
        )
        if isinstance(self.raises, list) and self.raises:
            exc = self.raises.pop(0)
            raise exc
        if isinstance(self.raises, BaseException):
            raise self.raises
        attempt = SearchAttempt(
            backend=self.backend,
            outcome="ok" if self.hits else "empty",
            latency_ms=1,
            count=len(self.hits),
        )
        return SearchResponse(
            hits=list(self.hits)[:max_results], backend=self.backend, attempts=(attempt,)
        )

    async def news(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
        region: str,
    ) -> SearchResponse:
        self.news_calls.append({"query": query, "region": region})
        outcome = self.news_outcome or ("ok" if self.news_hits else "empty")
        hits = list(self.news_hits)[:max_results] if outcome == "ok" else []
        attempt = SearchAttempt(
            backend="news:fake", outcome=outcome, latency_ms=1, count=len(hits)
        )
        return SearchResponse(hits=hits, backend="news:fake", attempts=(attempt,))


@dataclass
class FakePageFetcher:
    """In-memory `PageFetcher`: the first key that is a substring of the
    URL decides. `pages` gives a body, `errors` a `PageFetchError`
    reason, `delays` seconds to sleep first (for budget tests). Any URL
    with no match fails with `no_canned_response`.
    """

    pages: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    delays: dict[str, float] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)

    async def fetch(self, url: str, *, timeout_s: float) -> str:
        self.requested.append(url)
        for key, seconds in self.delays.items():
            if key in url:
                await asyncio.sleep(seconds)
        for key, reason in self.errors.items():
            if key in url:
                raise PageFetchError(reason)
        for key, body in self.pages.items():
            if key in url:
                return body
        raise PageFetchError("no_canned_response")


__all__ = [
    "FIXTURES",
    "FakePageFetcher",
    "FakeSearchProvider",
    "SearchHit",
    "SearchProviderError",
    "SearchRateLimitedError",
    "SearchResponse",
    "SearchTimeoutError",
    "fixture_text",
]
