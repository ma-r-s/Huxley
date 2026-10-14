"""Tests for huxley-skill-search.

Drives the skill through `setup -> handle` with a `FakeSearchProvider`
and a `FakePageFetcher`. No real `ddgs`, no network. Each test class is
a contract the skill must honor: the output shape and its caps, page
excerpts within budget, the headlines lane, failure modes carrying
`say_to_user`, the chime only on success, the cache, the breaker.
"""

from __future__ import annotations

import asyncio
import json
import wave
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape

import pytest

from huxley_sdk import PlaySound
from huxley_sdk.testing import make_test_context
from huxley_skill_search.skill import SearchSkill, rank_hits

from .conftest import (
    FakePageFetcher,
    FakeSearchProvider,
    SearchHit,
    SearchProviderError,
    SearchRateLimitedError,
    SearchTimeoutError,
    fixture_text,
)

if TYPE_CHECKING:
    from pathlib import Path


# --- Helpers ---


def _make_ctx(
    tmp_path: Path,
    *,
    extra: dict[str, object] | None = None,
    language: str | None = None,
) -> Any:
    config: dict[str, object] = {}
    if extra:
        config.update(extra)
    return make_test_context(
        config=config,
        persona_data_dir=tmp_path,
        language=language,
    )


def _write_pcm_wav(path: Path, frames: bytes = b"\x00\x01" * 100) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(frames)


def _hit(
    title: str = "A headline",
    url: str = "https://www.example.com/article",
    snippet: str = "Some text",
) -> SearchHit:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").removeprefix("www.")
    return SearchHit(title=title, url=url, snippet=snippet, source=host)


async def _skill(
    tmp_path: Path,
    provider: FakeSearchProvider,
    *,
    fetcher: FakePageFetcher | None = None,
    extra: dict[str, object] | None = None,
    language: str | None = None,
) -> tuple[SearchSkill, Any]:
    skill = SearchSkill(provider=provider, fetcher=fetcher or FakePageFetcher())
    ctx = _make_ctx(tmp_path, extra=extra, language=language)
    await skill.setup(ctx)
    return skill, ctx


def _events(ctx: Any, name: str, level: str = "ainfo") -> list[dict[str, Any]]:
    method = getattr(ctx.logger, level)
    return [c.kwargs for c in method.await_args_list if c.args and c.args[0] == name]


def _gnews(items: list[tuple[str, float]]) -> str:
    now = datetime.now(UTC)
    body = "".join(
        f"<item><title>{escape(t)}</title>"
        f"<pubDate>{format_datetime(now - timedelta(hours=h), usegmt=True)}</pubDate></item>"
        for t, h in items
    )
    return f"<rss><channel>{body}</channel></rss>"


# ---------------------------------------------------------------------------


class TestSetup:
    async def test_setup_runs_with_defaults(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider())
        assert skill._safesearch == "moderate"
        assert skill._region == "wt-wt"
        assert skill._timezone == "UTC"
        assert skill._preferred == ()
        assert skill._sounds == {}

    async def test_setup_validates_safesearch(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid safesearch"):
            await _skill(tmp_path, FakeSearchProvider(), extra={"safesearch": "bogus"})

    async def test_setup_validates_region(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid region"):
            await _skill(tmp_path, FakeSearchProvider(), extra={"region": "colombia"})

    async def test_preferred_domains_are_normalized(self, tmp_path: Path) -> None:
        skill, _ = await _skill(
            tmp_path,
            FakeSearchProvider(),
            extra={"preferred_domains": ["https://www.ElTiempo.com/", "semana.com", ""]},
        )
        assert skill._preferred == ("eltiempo.com", "semana.com")

    async def test_preferred_domains_must_be_a_list(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="preferred_domains"):
            await _skill(tmp_path, FakeSearchProvider(), extra={"preferred_domains": "x.com"})

    async def test_setup_accepts_strict_for_child_safe_personas(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(), extra={"safesearch": "strict"})
        assert skill._safesearch == "strict"

    async def test_setup_loads_chime_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "search_start.wav", frames=b"\x00\x01" * 50)
        skill, _ = await _skill(
            tmp_path, FakeSearchProvider(), extra={"start_sound": "search_start"}
        )
        assert skill._sounds["search_start"] == b"\x00\x01" * 50

    async def test_setup_warns_when_start_sound_missing(self, tmp_path: Path) -> None:
        skill, ctx = await _skill(
            tmp_path, FakeSearchProvider(), extra={"start_sound": "search_start"}
        )
        assert skill._sounds == {}
        assert len(_events(ctx, "search.start_sound_missing", "awarning")) == 1

    async def test_teardown_leaves_injected_fetcher_alone(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider())
        await skill.teardown()  # no owned client: nothing to close, no error
        await SearchSkill().teardown()  # setup never ran


class TestOutputShape:
    async def test_result_fields_and_no_url(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(
            hits=[
                _hit(
                    title="Madrid hoy",
                    url="https://www.elpais.com/madrid/2026/10/12/madrid-hoy.html",
                    snippet="Lo que ha pasado hoy en Madrid",
                ),
            ]
        )
        skill, _ = await _skill(tmp_path, provider, extra={"timezone": "America/Bogota"})
        data = json.loads((await skill.handle("search_the_web", {"query": "Madrid hoy"})).output)

        assert data["say_to_user"] is None
        assert data["result_count"] == 1
        assert len(data["today"]) == 10  # YYYY-MM-DD
        result = data["results"][0]
        assert result["source"] == "elpais.com"
        assert result["title"] == "Madrid hoy"
        assert result["date"] == "2026-10-12"  # from the URL
        assert "url" not in result  # the model must not read URLs aloud
        assert "excerpt" not in result  # the fake fetcher had no page
        assert "headlines" not in data  # wt-wt region: no headlines lane

    async def test_snippet_strips_urls_and_truncates(self, tmp_path: Path) -> None:
        long_snippet = "Mucho texto " * 40 + " https://example.com/long/url/path más texto"
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=[_hit(snippet=long_snippet)]))
        data = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        snippet = data["results"][0]["snippet"]
        assert "https://" not in snippet
        assert len(snippet) <= 283
        assert snippet.endswith("...")

    async def test_total_output_is_bounded(self, tmp_path: Path) -> None:
        # Worst case: 5 hits with long snippets, 3 big pages, 3 headlines.
        big_page = (
            "<html><body><article>"
            + "".join(
                f"<p>Millonarios párrafo largo {i} " + "texto " * 60 + "</p>" for i in range(50)
            )
            + "</article></body></html>"
        )
        hits = [
            _hit(title="T" * 200, url=f"https://site{i}.com/a", snippet="palabra " * 200)
            for i in range(5)
        ]
        fetcher = FakePageFetcher(
            pages={
                "news.google.com": _gnews(
                    [(f"Millonarios noticia {w} larga " * 6 + " - Fuente", 1.0) for w in "abc"]
                ),
                "site": big_page,
            }
        )
        skill, _ = await _skill(
            tmp_path, FakeSearchProvider(hits=hits), fetcher=fetcher, extra={"region": "co-es"}
        )
        out = (await skill.handle("search_the_web", {"query": "Millonarios"})).output
        data = json.loads(out)
        excerpts = [r["excerpt"] for r in data["results"] if "excerpt" in r]
        assert len(excerpts) == 2  # never more than two pages
        assert all(len(e) <= 1500 for e in excerpts)
        assert len(out) < 7500


class TestExcerpts:
    async def test_top_pages_get_excerpts(self, tmp_path: Path) -> None:
        hits = [
            _hit(title="Millonarios ganó", url="https://www.diario.co/deportes/partido"),
            _hit(title="Otra", url="https://otra.co/nota"),
        ]
        fetcher = FakePageFetcher(pages={"diario.co": fixture_text("news_article.html")})
        skill, ctx = await _skill(tmp_path, FakeSearchProvider(hits=hits), fetcher=fetcher)
        data = json.loads(
            (
                await skill.handle(
                    "search_the_web", {"query": "resultado Millonarios Once Caldas"}
                )
            ).output
        )
        top = data["results"][0]
        assert "2-0" in top["excerpt"]
        assert top["date"] == "2026-10-13"  # from the page's meta
        assert "excerpt" not in data["results"][1]  # its fetch failed
        pages = _events(ctx, "search.page")
        assert {p["domain"]: p["outcome"] for p in pages} == {
            "diario.co": "ok",
            "otra.co": "error",
        }

    async def test_low_value_domains_not_fetched_and_ranked_last(self, tmp_path: Path) -> None:
        hits = [
            _hit(url="https://www.youtube.com/watch?v=1"),
            _hit(url="https://news.google.com/rss/articles/abc"),
            _hit(url="https://ok.co/a"),
            _hit(url="https://ok2.co/doc.pdf"),
        ]
        fetcher = FakePageFetcher(pages={"ok.co": fixture_text("news_article.html")})
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=hits), fetcher=fetcher)
        data = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        assert [r["source"] for r in data["results"]] == [
            "ok.co",
            "ok2.co",
            "youtube.com",
            "news.google.com",
        ]
        assert fetcher.requested == ["https://ok.co/a"]  # no video, no pdf, no redirect page

    async def test_slow_page_is_cut_at_budget(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("huxley_skill_search.skill._PAGE_BUDGET_S", 0.2)
        monkeypatch.setattr("huxley_skill_search.skill._MIN_PAGE_BUDGET_S", 0.05)
        hits = [_hit(url="https://slow.co/a"), _hit(url="https://fast.co/b")]
        fetcher = FakePageFetcher(
            pages={"co/": fixture_text("news_article.html")}, delays={"slow.co": 5.0}
        )
        skill, ctx = await _skill(tmp_path, FakeSearchProvider(hits=hits), fetcher=fetcher)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        data = json.loads((await skill.handle("search_the_web", {"query": "Millonarios"})).output)
        assert loop.time() - t0 < 1.0
        assert "excerpt" not in data["results"][0]
        assert "excerpt" in data["results"][1]
        outcomes = {p["domain"]: p["outcome"] for p in _events(ctx, "search.page")}
        assert outcomes == {"slow.co": "timeout", "fast.co": "ok"}

    async def test_at_most_three_pages_fetched(self, tmp_path: Path) -> None:
        hits = [_hit(url=f"https://s{i}.co/a") for i in range(5)]
        fetcher = FakePageFetcher()
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=hits), fetcher=fetcher)
        await skill.handle("search_the_web", {"query": "x"})
        assert len(fetcher.requested) == 3

    async def test_page_without_text_gives_no_excerpt(self, tmp_path: Path) -> None:
        fetcher = FakePageFetcher(pages={"app.co": fixture_text("app_shell.html")})
        skill, ctx = await _skill(
            tmp_path, FakeSearchProvider(hits=[_hit(url="https://app.co/c")]), fetcher=fetcher
        )
        data = json.loads((await skill.handle("search_the_web", {"query": "cartelera"})).output)
        assert "excerpt" not in data["results"][0]
        assert _events(ctx, "search.page")[0]["outcome"] == "no_text"


class TestRanking:
    def test_preferred_moves_up_at_most_one_place(self) -> None:
        hits = [_hit(url=f"https://{d}/a") for d in ("a.co", "b.co", "eltiempo.com", "c.co")]
        ranked = rank_hits(hits, ("eltiempo.com",))
        assert [h.source for h in ranked] == ["a.co", "eltiempo.com", "b.co", "c.co"]

    def test_subdomain_counts_as_preferred(self) -> None:
        hits = [_hit(url="https://a.co/x"), _hit(url="https://es.wikipedia.org/wiki/X")]
        ranked = rank_hits(hits, ("wikipedia.org",))
        assert ranked[0].source == "es.wikipedia.org"

    def test_no_preferences_keeps_engine_order(self) -> None:
        hits = [_hit(url=f"https://s{i}.co/a") for i in range(3)]
        assert rank_hits(hits, ()) == hits


class TestHeadlines:
    async def test_headlines_included_for_a_country_region(self, tmp_path: Path) -> None:
        fetcher = FakePageFetcher(
            pages={
                "news.google.com/rss/search": _gnews(
                    [("Colombia femenina venció 2-1 a Venezuela - El Tiempo", 1.0)]
                )
            }
        )
        skill, ctx = await _skill(
            tmp_path,
            FakeSearchProvider(hits=[_hit()]),
            fetcher=fetcher,
            extra={"region": "co-es"},
        )
        data = json.loads(
            (await skill.handle("search_the_web", {"query": "Colombia femenina Venezuela"})).output
        )
        assert data["headlines"] == [
            {
                "title": "Colombia femenina venció 2-1 a Venezuela",
                "source": "El Tiempo",
                "age_hours": 1.0,
            }
        ]
        assert _events(ctx, "search.headlines")[0]["outcome"] == "ok"
        assert any("gl=CO" in u for u in fetcher.requested)

    async def test_headlines_failure_does_not_break_search(self, tmp_path: Path) -> None:
        fetcher = FakePageFetcher(errors={"news.google.com": "http_503"})
        skill, ctx = await _skill(
            tmp_path,
            FakeSearchProvider(hits=[_hit()]),
            fetcher=fetcher,
            extra={"region": "co-es"},
        )
        data = json.loads((await skill.handle("search_the_web", {"query": "Millonarios"})).output)
        assert data["result_count"] == 1
        assert "headlines" not in data
        assert _events(ctx, "search.headlines")[0] == {
            "query_hash": _events(ctx, "search.dispatch")[0]["query_hash"],
            "outcome": "error",
            "reason": "http_503",
            "latency_ms": _events(ctx, "search.headlines")[0]["latency_ms"],
        }

    async def test_web_failure_with_headlines_still_answers(self, tmp_path: Path) -> None:
        fetcher = FakePageFetcher(
            pages={"news.google.com": _gnews([("Temblor de 5,0 sacude Colombia - Semana", 2.0)])}
        )
        provider = FakeSearchProvider(raises=SearchTimeoutError("deadline"))
        skill, ctx = await _skill(tmp_path, provider, fetcher=fetcher, extra={"region": "co-es"})
        result = await skill.handle("search_the_web", {"query": "temblor Colombia"})
        data = json.loads(result.output)
        assert data["say_to_user"] is None
        assert data["results"] == []
        assert data["headlines"][0]["source"] == "Semana"
        assert _events(ctx, "search.web_failed_news_only")[0]["kind"] == "timeout"
        # The web failure still counts toward the breaker.
        assert skill._circuit_failures == 1


class TestForwarding:
    async def test_max_results_clamped_to_cap(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill, _ = await _skill(tmp_path, provider)
        await skill.handle("search_the_web", {"query": "test", "max_results": 99})
        assert provider.calls[-1]["max_results"] == 5
        await skill.handle("search_the_web", {"query": "test2", "max_results": 0})
        assert provider.calls[-1]["max_results"] == 1

    async def test_default_max_results_when_omitted(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill, _ = await _skill(tmp_path, provider)
        await skill.handle("search_the_web", {"query": "test"})
        assert provider.calls[-1]["max_results"] == 5

    async def test_safesearch_and_region_forwarded_to_provider(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill, _ = await _skill(
            tmp_path, provider, extra={"safesearch": "strict", "region": "co-es"}
        )
        await skill.handle("search_the_web", {"query": "x"})
        assert provider.calls[-1]["safesearch"] == "strict"
        assert provider.calls[-1]["region"] == "co-es"


class TestChime:
    async def test_chime_emitted_on_success_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "search_start.wav")
        skill, _ = await _skill(
            tmp_path, FakeSearchProvider(hits=[_hit()]), extra={"start_sound": "search_start"}
        )
        result = await skill.handle("search_the_web", {"query": "x"})
        assert isinstance(result.side_effect, PlaySound)
        assert len(result.side_effect.pcm) > 0

    async def test_no_chime_when_persona_has_no_sound_config(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=[_hit()]))
        result = await skill.handle("search_the_web", {"query": "x"})
        assert result.side_effect is None


class TestFailureModes:
    """Every failure mode must carry `say_to_user` and skip the chime."""

    async def test_empty_results_returns_recovery_message(self, tmp_path: Path) -> None:
        skill, ctx = await _skill(tmp_path, FakeSearchProvider(hits=[]), language="es")
        result = await skill.handle("search_the_web", {"query": "nada existe"})
        data = json.loads(result.output)
        assert data["result_count"] == 0
        assert "no he encontrado" in data["say_to_user"].lower()
        assert result.side_effect is None
        assert _events(ctx, "search.empty")[0]["attempts"].startswith("fake:empty")

    async def test_rate_limited_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("ddg_202"))
        skill, ctx = await _skill(tmp_path, provider, language="es")
        data = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        assert "no puedo buscar" in data["say_to_user"].lower()
        assert len(_events(ctx, "search.rate_limited", "awarning")) == 1

    async def test_timeout_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchTimeoutError("deadline:8.0s"))
        skill, ctx = await _skill(tmp_path, provider, language="es")
        result = await skill.handle("search_the_web", {"query": "x"})
        data = json.loads(result.output)
        assert "demasiado" in data["say_to_user"].lower()
        assert result.side_effect is None
        logged = _events(ctx, "search.timeout", "awarning")[0]
        assert logged["reason"] == "deadline:8.0s"
        assert "latency_ms" in logged

    async def test_generic_error_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchProviderError("parse_error"))
        skill, ctx = await _skill(tmp_path, provider, language="es")
        result = await skill.handle("search_the_web", {"query": "x"})
        assert json.loads(result.output)["say_to_user"] is not None
        assert result.side_effect is None
        assert _events(ctx, "search.error", "awarning")[0]["exception_type"] == (
            "SearchProviderError"
        )

    async def test_empty_query_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill, _ = await _skill(tmp_path, provider, language="es")
        data = json.loads((await skill.handle("search_the_web", {"query": "   "})).output)
        assert data["say_to_user"] is not None
        assert provider.calls == []

    async def test_cancelled_error_propagates(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(raises=asyncio.CancelledError()))
        with pytest.raises(asyncio.CancelledError):
            await skill.handle("search_the_web", {"query": "x"})

    async def test_every_attempt_is_logged(self, tmp_path: Path) -> None:
        skill, ctx = await _skill(tmp_path, FakeSearchProvider(hits=[_hit()]))
        await skill.handle("search_the_web", {"query": "x"})
        attempt = _events(ctx, "search.attempt")[0]
        assert attempt["backend"] == "fake"
        assert attempt["outcome"] == "ok"
        results = _events(ctx, "search.results")[0]
        assert results["backend"] == "fake"
        assert {"latency_ms", "excerpts", "headlines", "output_chars"} <= results.keys()


class TestRecoveryMessageLanguage:
    async def test_english_message(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=[]), language="en")
        msg = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        assert "didn't find" in msg["say_to_user"].lower()

    async def test_french_message(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=[]), language="fr")
        msg = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        assert "rien trouvé" in msg["say_to_user"].lower()

    async def test_reconfigure_flips_language(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(hits=[]), language="es")
        await skill.reconfigure(_make_ctx(tmp_path, language="en"))
        msg = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        assert "didn't" in msg["say_to_user"].lower()


class TestCache:
    async def test_cache_hit_skips_provider_call(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()])
        skill, _ = await _skill(tmp_path, provider)
        await skill.handle("search_the_web", {"query": "Madrid"})
        await skill.handle("search_the_web", {"query": "madrid"})
        assert len(provider.calls) == 1

    async def test_cache_distinguishes_max_results(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()])
        skill, _ = await _skill(tmp_path, provider)
        await skill.handle("search_the_web", {"query": "Madrid", "max_results": 1})
        await skill.handle("search_the_web", {"query": "Madrid", "max_results": 5})
        assert len(provider.calls) == 2

    async def test_failures_are_not_cached(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("x"))
        skill, _ = await _skill(tmp_path, provider)
        await skill.handle("search_the_web", {"query": "Madrid"})
        await skill.handle("search_the_web", {"query": "Madrid"})
        assert len(provider.calls) == 2


class TestCircuitBreaker:
    async def test_opens_after_three_consecutive_failures(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("x"))
        skill, _ = await _skill(tmp_path, provider, language="es")
        for i in range(3):
            await skill.handle("search_the_web", {"query": f"q{i}"})
        before = len(provider.calls)
        data = json.loads((await skill.handle("search_the_web", {"query": "q4"})).output)
        assert len(provider.calls) == before
        assert "no puedo buscar" in data["say_to_user"].lower()

    async def test_success_resets_failure_count(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(
            raises=[SearchRateLimitedError("x"), SearchRateLimitedError("x")],
            hits=[_hit()],
        )
        skill, _ = await _skill(tmp_path, provider)
        for q in ("q1", "q2", "q3"):  # fail, fail, success
            await skill.handle("search_the_web", {"query": q})
        provider.raises = [SearchRateLimitedError("x"), SearchRateLimitedError("x")]
        for q in ("q4", "q5"):
            await skill.handle("search_the_web", {"query": q})
        assert len(provider.calls) == 5


class TestToolDescriptions:
    """Tool descriptions are LLM-facing: they shape when and how it searches."""

    async def test_spanish_description(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(), language="es")
        desc = skill.tools[0].description.lower()
        assert "antes" in desc and "momento" in desc  # pre-narration
        assert "say_to_user" in desc
        assert "get_news" in desc
        assert "direcciones web" in desc  # don't read URLs
        # T1.48: search what may have changed, even if it seems known.
        assert "aunque creas saberlo" in desc
        assert "cargo" in desc
        assert "today" in desc
        assert "adivinar" in desc  # say so instead of guessing

    async def test_english_description(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(), language="en")
        desc = skill.tools[0].description.lower()
        assert "before" in desc and "moment" in desc
        assert "say_to_user" in desc
        assert "even if you think you know it" in desc
        assert "guessing" in desc

    async def test_french_description(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider(), language="fr")
        desc = skill.tools[0].description.lower()
        assert "avant" in desc and "instant" in desc
        assert "say_to_user" in desc
        assert "même si tu crois le savoir" in desc

    async def test_max_results_capped_in_schema(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider())
        schema = skill.tools[0].parameters["properties"]["max_results"]
        assert schema["maximum"] == 5
        assert schema["minimum"] == 1


class TestUnknownTool:
    async def test_returns_error_payload(self, tmp_path: Path) -> None:
        skill, _ = await _skill(tmp_path, FakeSearchProvider())
        data = json.loads((await skill.handle("nonsense_tool", {})).output)
        assert "unknown_tool" in data["error"]


class TestPrivacyLogging:
    async def test_full_query_never_logged_at_info(self, tmp_path: Path) -> None:
        sensitive = "abogados divorcio en Madrid baratos"
        fetcher = FakePageFetcher(pages={"news.google.com": _gnews([])})
        skill, ctx = await _skill(
            tmp_path,
            FakeSearchProvider(hits=[_hit()]),
            fetcher=fetcher,
            extra={"region": "co-es"},
        )
        await skill.handle("search_the_web", {"query": sensitive})
        for call in ctx.logger.ainfo.await_args_list:
            assert sensitive not in json.dumps(call.kwargs, ensure_ascii=False, default=str)


class TestNewsLane:
    """Fresh news articles (with URLs) merged into the results."""

    @staticmethod
    def _today_path() -> str:
        from huxley_skill_search.pages import today_iso

        return today_iso("UTC").replace("-", "/")

    async def test_fresh_relevant_article_leads_and_gets_an_excerpt(self, tmp_path: Path) -> None:
        article = _hit(
            title="Pico y placa en Villavicencio este martes: placas 3 y 4",
            url=f"https://www.infobae.com/colombia/{self._today_path()}/pico-y-placa/",
        )
        provider = FakeSearchProvider(
            hits=[_hit(title="Web 1", url="https://w1.co/a"), _hit(url="https://w2.co/b")],
            news_hits=[article],
        )
        fetcher = FakePageFetcher(pages={"infobae.com": fixture_text("table_page.html")})
        skill, ctx = await _skill(tmp_path, provider, fetcher=fetcher)
        data = json.loads(
            (
                await skill.handle("search_the_web", {"query": "pico y placa Villavicencio hoy"})
            ).output
        )
        assert [r["source"] for r in data["results"]] == ["infobae.com", "w1.co", "w2.co"]
        assert "3 y 4" in data["results"][0]["excerpt"]
        assert data["results"][0]["date"] == self._today_path().replace("/", "-")
        news_log = _events(ctx, "search.news")[0]
        assert news_log["outcome"] == "ok"
        assert news_log["kept"] == 1

    async def test_stale_or_unrelated_articles_are_dropped(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(
            hits=[_hit(url="https://w1.co/a")],
            news_hits=[
                # Old URL date.
                _hit(title="Pico y placa Villavicencio enero", url="https://x.co/2025/01/05/pyp"),
                # Fresh but about something else.
                _hit(
                    title="Shakira anuncia gira",
                    url=f"https://y.co/{self._today_path()}/shakira",
                ),
                # No date anywhere: can't tell it's fresh.
                _hit(title="Pico y placa Villavicencio", url="https://z.co/pyp"),
            ],
        )
        skill, ctx = await _skill(tmp_path, provider)
        data = json.loads(
            (await skill.handle("search_the_web", {"query": "pico y placa Villavicencio"})).output
        )
        assert [r["source"] for r in data["results"]] == ["w1.co"]
        assert _events(ctx, "search.news")[0]["kept"] == 0

    async def test_engine_date_counts_when_url_has_none(self, tmp_path: Path) -> None:
        from dataclasses import replace

        from huxley_skill_search.pages import today_iso

        article = replace(
            _hit(title="Millonarios y Once Caldas: hora y canal", url="https://rcn.co/partido"),
            published=f"{today_iso('UTC')}T00:07:52+00:00",
        )
        provider = FakeSearchProvider(hits=[], news_hits=[article])
        skill, _ = await _skill(tmp_path, provider)
        data = json.loads(
            (await skill.handle("search_the_web", {"query": "Millonarios Once Caldas"})).output
        )
        assert data["results"][0]["source"] == "rcn.co"
        assert "date" not in data["results"][0]  # the engine's date isn't trusted for output

    async def test_news_lane_failure_is_logged_and_harmless(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()], news_outcome="timeout")
        skill, ctx = await _skill(tmp_path, provider)
        data = json.loads((await skill.handle("search_the_web", {"query": "x"})).output)
        assert data["result_count"] == 1
        assert _events(ctx, "search.news")[0]["outcome"] == "timeout"

    async def test_web_failure_with_fresh_article_still_answers(self, tmp_path: Path) -> None:
        article = _hit(
            title="Temblor en Colombia hoy de magnitud 5,0",
            url=f"https://eltiempo.com/{self._today_path()}/temblor",
        )
        provider = FakeSearchProvider(raises=SearchRateLimitedError("x"), news_hits=[article])
        skill, _ = await _skill(tmp_path, provider)
        data = json.loads(
            (await skill.handle("search_the_web", {"query": "temblor Colombia"})).output
        )
        assert data["say_to_user"] is None
        assert data["results"][0]["source"] == "eltiempo.com"

    def test_merge_alternates_and_dedupes(self) -> None:
        from huxley_skill_search.skill import merge_hits

        web = [_hit(url=f"https://w{i}.co/a") for i in range(4)]
        news = [
            _hit(url="https://n0.co/a"),
            _hit(url="https://w0.co/a"),
            _hit(url="https://n2.co/a"),
        ]
        merged = merge_hits(web, news, 5)
        assert [h.source for h in merged] == ["n0.co", "w0.co", "n2.co", "w1.co", "w2.co"]


class TestChildSafe:
    async def test_strict_persona_gets_no_page_text(self, tmp_path: Path) -> None:
        fetcher = FakePageFetcher(pages={"diario.co": fixture_text("news_article.html")})
        skill, ctx = await _skill(
            tmp_path,
            FakeSearchProvider(hits=[_hit(url="https://diario.co/a")]),
            fetcher=fetcher,
            extra={"safesearch": "strict"},
        )
        data = json.loads((await skill.handle("search_the_web", {"query": "Millonarios"})).output)
        assert "excerpt" not in data["results"][0]
        assert fetcher.requested == []
        assert _events(ctx, "search.pages_skipped")[0]["reason"] == "strict"
