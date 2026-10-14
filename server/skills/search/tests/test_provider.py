"""DdgsProvider: fallback order, deadline, error mapping. No network.

The provider's synchronous search function is injected, so each test
scripts what every backend does (rows, an exception, or a slow answer).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from huxley_skill_search.provider import (
    DdgsProvider,
    SearchProviderError,
    SearchRateLimitedError,
    SearchTimeoutError,
)


class RatelimitException(Exception):  # noqa: N818 - mirrors the ddgs class name
    pass


class DDGSException(Exception):  # noqa: N818 - mirrors the ddgs class name
    pass


def _rows(n: int = 2) -> list[dict[str, Any]]:
    return [
        {"title": f"T{i}", "href": f"https://www.site{i}.co/a", "body": f"body {i}"}
        for i in range(n)
    ]


class Script:
    """Per-backend behaviour: a list of rows, an exception, or (delay, rows)."""

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        query: str,
        region: str,
        safesearch: str,
        max_results: int,
        backend: str,
        timeout_s: float,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "query": query,
                "region": region,
                "safesearch": safesearch,
                "max_results": max_results,
                "backend": backend,
                "timeout_s": timeout_s,
            }
        )
        step = self.plan[backend]
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, tuple):
            delay, rows = step
            time.sleep(delay)
            return list(rows)
        return list(step)


async def _search(provider: DdgsProvider, **kw: Any) -> Any:
    args: dict[str, Any] = {"max_results": 5, "safesearch": "moderate", "region": "co-es"}
    args.update(kw)
    return await provider.search("presidente de Colombia", **args)


class TestFallback:
    async def test_first_backend_answers(self) -> None:
        script = Script({"a": _rows(3), "b": _rows(1)})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert response.backend == "a"
        assert len(response.hits) == 3
        assert [c["backend"] for c in script.calls] == ["a"]
        assert [a.outcome for a in response.attempts] == ["ok"]

    async def test_empty_then_next_backend(self) -> None:
        script = Script({"a": DDGSException("No results found."), "b": _rows(2)})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert response.backend == "b"
        assert [a.outcome for a in response.attempts] == ["empty", "ok"]

    async def test_error_then_next_backend(self) -> None:
        script = Script({"a": RuntimeError("boom"), "b": _rows(2)})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert response.backend == "b"
        assert response.attempts[0].outcome == "error"
        assert response.attempts[0].error_class == "RuntimeError"

    async def test_empty_rows_count_as_empty(self) -> None:
        script = Script({"a": [], "b": _rows(1)})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert [a.outcome for a in response.attempts] == ["empty", "ok"]

    async def test_rows_without_url_are_dropped(self) -> None:
        script = Script({"a": [{"title": "no url"}, *_rows(1)]})
        provider = DdgsProvider(backends=("a",), search_fn=script)
        response = await _search(provider)
        assert len(response.hits) == 1
        assert response.hits[0].source == "site0.co"

    async def test_max_results_caps_hits(self) -> None:
        script = Script({"a": _rows(9)})
        provider = DdgsProvider(backends=("a",), search_fn=script)
        response = await _search(provider, max_results=3)
        assert len(response.hits) == 3


class TestDeadline:
    async def test_slow_backend_times_out_and_next_one_answers(self) -> None:
        script = Script({"slow": (0.6, _rows(1)), "fast": _rows(2)})
        provider = DdgsProvider(
            backends=("slow", "fast"),
            deadline_s=2.0,
            attempt_timeout_s=0.2,
            search_fn=script,
        )
        t0 = time.monotonic()
        response = await _search(provider)
        assert response.backend == "fast"
        assert response.attempts[0].outcome == "timeout"
        assert time.monotonic() - t0 < 0.55

    async def test_whole_search_never_exceeds_deadline(self, monkeypatch: Any) -> None:
        # Every backend hangs; the deadline ends it with a timeout error.
        monkeypatch.setattr("huxley_skill_search.provider.MIN_ATTEMPT_S", 0.05)
        script = Script({"a": (0.6, []), "b": (0.6, []), "c": (0.6, [])})
        provider = DdgsProvider(
            backends=("a", "b", "c"),
            deadline_s=0.5,
            attempt_timeout_s=0.3,
            search_fn=script,
        )
        t0 = time.monotonic()
        with pytest.raises(SearchTimeoutError) as info:
            await _search(provider)
        assert time.monotonic() - t0 < 0.8
        assert all(a.outcome == "timeout" for a in info.value.attempts)
        # The second attempt only got what was left of the deadline.
        assert len(info.value.attempts) == 2

    async def test_no_attempt_started_with_too_little_time(self) -> None:
        clock = iter([0.0, 7.5, 7.5])  # start, then 7.5 s already gone
        script = Script({"a": _rows(1)})
        provider = DdgsProvider(
            backends=("a",), deadline_s=8.0, search_fn=script, clock=lambda: next(clock)
        )
        with pytest.raises(SearchTimeoutError):
            await _search(provider)
        assert script.calls == []

    async def test_attempt_budget_passed_to_backend(self) -> None:
        script = Script({"a": _rows(1)})
        provider = DdgsProvider(backends=("a",), attempt_timeout_s=3.0, search_fn=script)
        await _search(provider)
        assert script.calls[0]["timeout_s"] == pytest.approx(3.0)


class TestErrorMapping:
    async def test_all_empty_is_an_honest_empty_response(self) -> None:
        script = Script({"a": DDGSException("No results found."), "b": []})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert response.hits == []
        assert [a.outcome for a in response.attempts] == ["empty", "empty"]

    async def test_rate_limit_wins_over_empty(self) -> None:
        script = Script({"a": RatelimitException("202"), "b": []})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        with pytest.raises(SearchRateLimitedError) as info:
            await _search(provider)
        assert [a.backend for a in info.value.attempts] == ["a", "b"]

    async def test_timeout_class_name_maps_to_timeout(self) -> None:
        class TimeoutException(Exception):  # noqa: N818 - mirrors ddgs
            pass

        script = Script({"a": TimeoutException("slow"), "b": []})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        with pytest.raises(SearchTimeoutError):
            await _search(provider)

    async def test_other_errors_map_to_provider_error(self) -> None:
        script = Script({"a": ValueError("parse"), "b": DDGSException("No results found.")})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        with pytest.raises(SearchProviderError) as info:
            await _search(provider)
        assert not isinstance(info.value, SearchRateLimitedError | SearchTimeoutError)
        assert "a:error" in str(info.value)

    def test_needs_a_backend(self) -> None:
        with pytest.raises(ValueError, match="backend"):
            DdgsProvider(backends=())


class TestPassThrough:
    async def test_region_safesearch_and_query_reach_backend(self) -> None:
        script = Script({"a": _rows(1)})
        provider = DdgsProvider(backends=("a",), search_fn=script)
        await _search(provider, region="mx-es", safesearch="moderate", max_results=4)
        call = script.calls[0]
        assert call["region"] == "mx-es"
        assert call["safesearch"] == "moderate"
        assert call["max_results"] == 4
        assert call["query"] == "presidente de Colombia"

    def test_default_backend_order_starts_with_yandex(self) -> None:
        # Measured on the production Pi (T1.48): Yandex answered 8/8,
        # Yahoo 3/8, DuckDuckGo/Brave/Mojeek nothing.
        from huxley_skill_search.provider import DEFAULT_BACKENDS

        assert DEFAULT_BACKENDS[0] == "yandex"
        assert "auto" in DEFAULT_BACKENDS


class TestNews:
    async def test_news_returns_hits_with_engine_date(self) -> None:
        rows = [
            {"title": "N", "url": "https://n.co/2026/10/13/a", "body": "b", "date": "2026-10-13"}
        ]
        provider = DdgsProvider(search_fn=Script({}), news_fn=Script({"auto": rows}))
        response = await provider.news("x", max_results=5, safesearch="moderate", region="co-es")
        assert response.hits[0].published == "2026-10-13"
        assert response.attempts[0].backend == "news:auto"
        assert response.attempts[0].outcome == "ok"

    async def test_news_never_raises(self) -> None:
        provider = DdgsProvider(
            search_fn=Script({}), news_fn=Script({"auto": RatelimitException("202")})
        )
        response = await provider.news("x", max_results=5, safesearch="moderate", region="co-es")
        assert response.hits == []
        assert response.attempts[0].outcome == "rate_limited"

    async def test_news_is_bounded_by_its_timeout(self) -> None:
        provider = DdgsProvider(
            search_fn=Script({}), news_fn=Script({"auto": (0.6, [])}), news_timeout_s=0.1
        )
        response = await provider.news("x", max_results=5, safesearch="moderate", region="co-es")
        assert response.attempts[0].outcome == "timeout"


async def test_strict_maps_to_ddgs_on() -> None:
    # ddgs says "on"; its Brave engine read "strict" as OFF.
    script = Script({"a": _rows(1)})
    provider = DdgsProvider(backends=("a",), search_fn=script)
    await _search(provider, safesearch="strict")
    assert script.calls[0]["safesearch"] == "on"


class TestWeakResponses:
    @staticmethod
    def _social(n: int) -> list[dict[str, Any]]:
        return [
            {"title": f"S{i}", "href": f"https://www.tiktok.com/v/{i}", "body": "video"}
            for i in range(n)
        ]

    async def test_mostly_social_tries_the_next_backend_and_merges(self) -> None:
        weak = [*self._social(3), *_rows(2)]  # 2 useful of 5
        script = Script({"a": weak, "b": _rows(4)})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert [a.outcome for a in response.attempts] == ["weak", "ok"]
        # Useful first, URLs not repeated (site0/site1 came from both).
        sources = [h.source for h in response.hits]
        assert sources[:4] == ["site0.co", "site1.co", "site2.co", "site3.co"]
        assert len(response.hits) == 5
        assert response.hits[-1].source == "tiktok.com"

    async def test_weak_hits_are_kept_when_nothing_better_comes(self) -> None:
        weak = [*self._social(4), *_rows(1)]
        script = Script({"a": weak, "b": DDGSException("No results found.")})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider)
        assert [a.outcome for a in response.attempts] == ["weak", "empty"]
        assert response.backend == "a"
        assert response.hits[0].source == "site0.co"

    async def test_small_max_results_lowers_the_bar(self) -> None:
        script = Script({"a": [*_rows(1), *self._social(1)], "b": _rows(3)})
        provider = DdgsProvider(backends=("a", "b"), search_fn=script)
        response = await _search(provider, max_results=1)
        assert [a.outcome for a in response.attempts] == ["ok"]
