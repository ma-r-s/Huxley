"""Test fixtures for huxley-skill-search.

`FakeSearchProvider` is the test double for the search-provider boundary
— a programmable list of hits or an exception to raise. Tests construct
it with the canned response they need; no `ddgs`, no network, no
monkey-patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from huxley_skill_search.provider import (
    SearchHit,
    SearchProviderError,
    SearchRateLimitedError,
    SearchResponse,
    SearchTimeoutError,
)


@dataclass
class FakeSearchProvider:
    """In-memory `SearchProvider` impl.

    Set `hits` for a successful response, or `raises` to an exception
    instance for a failure path. Tracks each call so tests can assert
    `safesearch` and `max_results` were forwarded correctly.

    `raises` is consumed FIFO when set to a list — useful for the
    circuit-breaker test which needs N consecutive failures followed
    by a success.
    """

    hits: list[SearchHit] = field(default_factory=list)
    # `BaseException` (not `Exception`) so tests can inject
    # `asyncio.CancelledError`, which inherits from BaseException since
    # Python 3.8 — Exception-typed `raises` would silently skip it.
    raises: BaseException | list[BaseException] | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
    ) -> SearchResponse:
        self.calls.append({"query": query, "max_results": max_results, "safesearch": safesearch})
        if isinstance(self.raises, list) and self.raises:
            exc = self.raises.pop(0)
            raise exc
        if isinstance(self.raises, BaseException):
            raise self.raises
        return SearchResponse(hits=list(self.hits))


__all__ = [
    "FakeSearchProvider",
    "SearchHit",
    "SearchProviderError",
    "SearchRateLimitedError",
    "SearchResponse",
    "SearchTimeoutError",
]
