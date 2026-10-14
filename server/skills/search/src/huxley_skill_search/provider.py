"""Search-provider boundary as a Protocol so tests inject a fake.

Production uses `DdgsProvider`, which wraps the `ddgs` metasearch
library. Tests build a `FakeSearchProvider` that satisfies the same
Protocol without touching the network. Same pattern as the news skill's
`HttpClient` / `FakeHttpClient`.

Failure modes are surfaced as discrete exception types so the skill can
tell empty results from rate-limited / timeout / error paths. Every
response and every error carries the attempts it took (backend, outcome,
latency) so the skill can log each one.

Why several backends (T1.48, measured on the production Pi 2026-10-13):
`ddgs` scrapes public search pages, and which ones answer depends on the
day and the IP. From the Pi, DuckDuckGo, Brave and Mojeek returned
nothing at all, Yahoo answered 3 of 8 queries, Yandex 8 of 8 in about
1 s. So the provider tries one backend, then the next, inside one
deadline, and the order lives in one constant.
"""

from __future__ import annotations

import asyncio
import functools
import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search result, normalized across providers.

    `source` is the URL's hostname with leading `www.` stripped. The skill
    pre-extracts it because realtime LLMs sometimes spell URLs letter by
    letter instead of citing sources by name.
    """

    title: str
    url: str
    snippet: str
    source: str
    # News results only: when the engine says it was published (ISO
    # string, as given; may be approximate).
    published: str | None = None


@dataclass(frozen=True, slots=True)
class SearchAttempt:
    """One try against one backend, for the log."""

    backend: str
    outcome: str  # "ok" | "weak" | "empty" | "timeout" | "rate_limited" | "error"
    latency_ms: int
    count: int = 0
    error_class: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Provider's response on a clean call. `hits` may be empty."""

    hits: list[SearchHit]
    backend: str = ""
    attempts: tuple[SearchAttempt, ...] = field(default_factory=tuple)


class SearchProviderError(Exception):
    """Generic search failure: network error, parse error, etc."""

    def __init__(self, message: str, *, attempts: tuple[SearchAttempt, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


class SearchRateLimitedError(SearchProviderError):
    """Provider signaled rate limiting (HTTP 202, 429, or library equivalent)."""


class SearchTimeoutError(SearchProviderError):
    """Provider didn't return within the configured deadline."""


class SearchProvider(Protocol):
    """Minimal async surface the skill needs from a search backend.

    `search` is the web search; `news` is a best-effort news-article
    search that never raises (except cancellation): it returns an empty
    response with the failed attempt when it can't answer.
    """

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
        region: str,
    ) -> SearchResponse:
        """Run `query`, return up to `max_results` hits.

        `safesearch` is one of `"off"`, `"moderate"`, `"strict"`. `region`
        is a ddgs-style `<country>-<language>` code (`"co-es"`, `"us-en"`,
        `"wt-wt"` for no region). Implementations translate as needed.

        Raises:
            SearchRateLimitedError: backend signaled throttling.
            SearchTimeoutError: backend exceeded deadline.
            SearchProviderError: any other failure.
        """
        ...

    async def news(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
        region: str,
    ) -> SearchResponse:
        """Recent news articles for `query` (with URLs), best effort."""
        ...


# --- Production: the `ddgs` metasearch package ---


def extract_source(url: str) -> str:
    """Pull a clean hostname out of a URL for `source` display.

    Returns an empty string for malformed URLs. Doesn't map to a "human"
    name (`bbc.co.uk` stays `bbc.co.uk`); the LLM cites it naturally.
    """
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname or ""
    except (ValueError, AttributeError):
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


# (query, region, safesearch, max_results, backend, timeout_s) -> raw rows.
SyncSearchFn = Callable[[str, str, str, int, str, float], list[dict[str, Any]]]

# Tried in order until one returns hits or the deadline runs out. "auto"
# is ddgs's own mix (Wikipedia first, then the engines in random order).
DEFAULT_BACKENDS: tuple[str, ...] = ("yandex", "yahoo", "auto")
DEFAULT_DEADLINE_S = 8.0
DEFAULT_ATTEMPT_TIMEOUT_S = 3.0
# News articles: one attempt, ddgs's own mix (Bing News answered from the
# Pi when DuckDuckGo and Yahoo News didn't). It runs beside the web
# search; the skill waits for it only briefly after the web search is
# back (see skill._NEWS_GRACE_S).
NEWS_BACKEND = "auto"
NEWS_TIMEOUT_S = 3.0
# Don't start a new attempt with less than this left: it would only add
# a doomed request before the same failure message.
MIN_ATTEMPT_S = 1.0
# Video, social, logins and Google's own redirect pages: never fetched for
# an excerpt and ranked last by the skill. A web response made mostly of
# these is "weak": the next backend gets a turn and the hits are merged.
LOW_VALUE_DOMAINS: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "pinterest.com",
    "pinterest.es",
    "linkedin.com",
    "reddit.com",
    "news.google.com",
)
# A response with fewer useful hits than this (or than its own size or
# max_results, if smaller) is weak. Measured: Yandex answered "remedios para el dolor de
# estomago" with two Pinterest pins, a TikTok and two unrelated pages.
MIN_USEFUL_HITS = 3


def is_low_value(host: str) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in LOW_VALUE_DOMAINS)


# The skill's vocabulary to ddgs's ("on" | "moderate" | "off"). ddgs's
# Brave engine reads anything but "on" and "moderate" as OFF, so passing
# "strict" through turned filtering off there. (Most ddgs engines ignore
# safesearch altogether; see docs/skills/search.md.)
_DDGS_SAFESEARCH = {"strict": "on", "moderate": "moderate", "off": "off"}


def _ddgs_text(
    query: str,
    region: str,
    safesearch: str,
    max_results: int,
    backend: str,
    timeout_s: float,
) -> list[dict[str, Any]]:
    # Local import keeps the `ddgs` import out of test paths that only
    # touch the Protocol. A fresh client per attempt: a reused one keeps
    # the cookies of a refused session (measured on the Pi: a shared
    # client kept getting nothing from Yahoo while a fresh one got 5).
    from ddgs import DDGS

    client = DDGS(timeout=max(1, math.ceil(timeout_s)))
    return list(
        client.text(
            query,
            region=region,
            safesearch=safesearch,
            max_results=max_results,
            backend=backend,
        )
    )


def _ddgs_news(
    query: str,
    region: str,
    safesearch: str,
    max_results: int,
    backend: str,
    timeout_s: float,
) -> list[dict[str, Any]]:
    from ddgs import DDGS

    client = DDGS(timeout=max(1, math.ceil(timeout_s)))
    return list(
        client.news(
            query,
            region=region,
            safesearch=safesearch,
            max_results=max_results,
            backend=backend,
        )
    )


# ddgs calls run in their own small pool, not the loop's default one: an
# attempt that timed out keeps its thread until ddgs gives up (with
# backend "auto" that can be several engines, about 11 s in a simulation),
# and the default pool is shared with the rest of the process.
_POOL_WORKERS = 4
_pool: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=_POOL_WORKERS, thread_name_prefix="huxley-search")
    return _pool


async def _in_pool(fn: SyncSearchFn, *args: Any) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor(), functools.partial(fn, *args))


def _classify(exc: BaseException) -> str:
    """Map a backend exception to an attempt outcome.

    ddgs raises `RatelimitException` / `TimeoutException` / `DDGSException`
    (the module path has moved between versions, so match by class name).
    `DDGSException("No results found.")` is what a blocked engine says as
    well as a truly empty one; it counts as "empty" and the next backend
    gets a turn either way.
    """
    if isinstance(exc, TimeoutError):
        return "timeout"
    cls = type(exc).__name__.lower()
    if "ratelimit" in cls or "rate_limit" in cls:
        return "rate_limited"
    if "timeout" in cls:
        return "timeout"
    if "no results" in str(exc).lower():
        return "empty"
    return "error"


def _to_hits(raw: list[dict[str, Any]]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for entry in raw:
        url = str(entry.get("href") or entry.get("url") or "")
        if not url:
            continue
        published = entry.get("date")
        hits.append(
            SearchHit(
                title=str(entry.get("title") or "").strip(),
                url=url,
                snippet=str(entry.get("body") or entry.get("snippet") or "").strip(),
                source=extract_source(url),
                published=str(published) if published else None,
            )
        )
    return hits


class DdgsProvider:
    """`SearchProvider` backed by `ddgs`, with fallback inside a deadline.

    Each attempt runs the synchronous ddgs call in a worker thread under
    `asyncio.wait_for`. An attempt gets at most `attempt_timeout_s`; the
    whole search at most `deadline_s`. A failed or empty attempt hands
    the remaining time to the next backend. A timed-out thread can't be
    killed: ddgs's client timeout (set to the attempt budget) bounds each
    engine request, but backend "auto" may try several engines in turn,
    so the thread can run on for seconds after the caller moved on. It
    runs in this module's own small pool, never the loop's default one.

    `search_fn` and `clock` are injectable for tests.
    """

    def __init__(
        self,
        *,
        backends: Sequence[str] = DEFAULT_BACKENDS,
        deadline_s: float = DEFAULT_DEADLINE_S,
        attempt_timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
        search_fn: SyncSearchFn = _ddgs_text,
        news_fn: SyncSearchFn = _ddgs_news,
        news_timeout_s: float = NEWS_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not backends:
            raise ValueError("DdgsProvider needs at least one backend")
        self._backends = tuple(backends)
        self._deadline_s = deadline_s
        self._attempt_timeout_s = attempt_timeout_s
        self._search_fn = search_fn
        self._news_fn = news_fn
        self._news_timeout_s = news_timeout_s
        self._clock = clock

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
        region: str,
    ) -> SearchResponse:
        start = self._clock()
        attempts: list[SearchAttempt] = []
        collected: list[SearchHit] = []  # hits from weak attempts
        first_backend = ""
        safesearch = _DDGS_SAFESEARCH.get(safesearch, safesearch)
        for backend in self._backends:
            remaining = self._deadline_s - (self._clock() - start)
            if remaining < MIN_ATTEMPT_S:
                break
            budget = min(self._attempt_timeout_s, remaining)
            t0 = self._clock()
            try:
                raw = await asyncio.wait_for(
                    _in_pool(
                        self._search_fn,
                        query,
                        region,
                        safesearch,
                        max_results,
                        backend,
                        budget,
                    ),
                    timeout=budget,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts.append(
                    SearchAttempt(
                        backend=backend,
                        outcome=_classify(exc),
                        latency_ms=int((self._clock() - t0) * 1000),
                        error_class=type(exc).__name__,
                    )
                )
                continue
            hits = _to_hits(raw)
            latency_ms = int((self._clock() - t0) * 1000)
            if not hits:
                attempts.append(
                    SearchAttempt(backend=backend, outcome="empty", latency_ms=latency_ms)
                )
                continue
            useful = sum(1 for h in hits if not is_low_value(h.source))
            if useful >= min(MIN_USEFUL_HITS, len(hits), max_results):
                attempts.append(
                    SearchAttempt(
                        backend=backend, outcome="ok", latency_ms=latency_ms, count=len(hits)
                    )
                )
                return self._respond(collected + hits, max_results, backend, attempts)
            # Mostly social/video: keep these, let the next backend add more.
            attempts.append(
                SearchAttempt(
                    backend=backend, outcome="weak", latency_ms=latency_ms, count=len(hits)
                )
            )
            collected.extend(hits)
            first_backend = first_backend or backend
        if collected:
            return self._respond(collected, max_results, first_backend, attempts)
        return self._final(attempts)

    @staticmethod
    def _respond(
        hits: list[SearchHit], max_results: int, backend: str, attempts: list[SearchAttempt]
    ) -> SearchResponse:
        """Merge hits from one or more attempts: no repeated URL, useful first."""
        seen: set[str] = set()
        unique: list[SearchHit] = []
        for hit in hits:
            if hit.url not in seen:
                seen.add(hit.url)
                unique.append(hit)
        ordered = [h for h in unique if not is_low_value(h.source)] + [
            h for h in unique if is_low_value(h.source)
        ]
        return SearchResponse(
            hits=ordered[:max_results], backend=backend, attempts=tuple(attempts)
        )

    async def news(
        self,
        query: str,
        *,
        max_results: int,
        safesearch: str,
        region: str,
    ) -> SearchResponse:
        t0 = self._clock()
        budget = self._news_timeout_s
        safesearch = _DDGS_SAFESEARCH.get(safesearch, safesearch)
        try:
            raw = await asyncio.wait_for(
                _in_pool(
                    self._news_fn,
                    query,
                    region,
                    safesearch,
                    max_results,
                    NEWS_BACKEND,
                    budget,
                ),
                timeout=budget,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempt = SearchAttempt(
                backend=f"news:{NEWS_BACKEND}",
                outcome=_classify(exc),
                latency_ms=int((self._clock() - t0) * 1000),
                error_class=type(exc).__name__,
            )
            return SearchResponse(hits=[], backend="", attempts=(attempt,))
        hits = _to_hits(raw)[:max_results]
        attempt = SearchAttempt(
            backend=f"news:{NEWS_BACKEND}",
            outcome="ok" if hits else "empty",
            latency_ms=int((self._clock() - t0) * 1000),
            count=len(hits),
        )
        return SearchResponse(hits=hits, backend=attempt.backend, attempts=(attempt,))

    def _final(self, attempts: list[SearchAttempt]) -> SearchResponse:
        """Nothing returned hits: decide what the user hears.

        Worst failure wins (rate limited, then timeout, then error), so a
        throttled backend isn't reported as "nothing found". When every
        attempt answered "empty", the search is honestly empty.
        """
        done = tuple(attempts)
        outcomes = {a.outcome for a in attempts}
        summary = ",".join(f"{a.backend}:{a.outcome}" for a in attempts) or "no_attempt"
        if "rate_limited" in outcomes:
            raise SearchRateLimitedError(summary, attempts=done)
        if "timeout" in outcomes or not attempts:
            raise SearchTimeoutError(f"deadline:{self._deadline_s}s:{summary}", attempts=done)
        if "error" in outcomes:
            raise SearchProviderError(summary, attempts=done)
        return SearchResponse(hits=[], backend="", attempts=done)
