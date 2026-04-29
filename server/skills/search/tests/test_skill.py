"""Tests for huxley-skill-search.

Drives the skill through `setup → handle` with a `FakeSearchProvider`.
No real `ddgs`, no network. Each test class corresponds to a contract
the skill is required to honor — failure modes carry `say_to_user`,
chime fires only on success, cache works, circuit breaker opens after
N consecutive failures, etc.
"""

from __future__ import annotations

import asyncio
import json
import wave
from typing import TYPE_CHECKING

import pytest

from huxley_sdk import PlaySound
from huxley_sdk.testing import make_test_context
from huxley_skill_search.skill import SearchSkill

from .conftest import (
    FakeSearchProvider,
    SearchHit,
    SearchProviderError,
    SearchRateLimitedError,
    SearchTimeoutError,
)

if TYPE_CHECKING:
    from pathlib import Path


# --- Helpers ---


def _make_ctx(
    tmp_path: Path,
    *,
    extra: dict[str, object] | None = None,
    language: str | None = None,
) -> object:
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


# ---------------------------------------------------------------------------


class TestSetup:
    async def test_setup_runs_with_defaults(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path))
        assert skill._safesearch == "moderate"
        assert skill._sounds == {}
        assert skill._start_sound_role is None

    async def test_setup_validates_safesearch(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        with pytest.raises(ValueError, match="invalid safesearch"):
            await skill.setup(_make_ctx(tmp_path, extra={"safesearch": "bogus"}))

    async def test_setup_accepts_strict_for_child_safe_personas(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path, extra={"safesearch": "strict"}))
        assert skill._safesearch == "strict"

    async def test_setup_loads_chime_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "search_start.wav", frames=b"\x00\x01" * 50)

        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path, extra={"start_sound": "search_start"}))
        assert "search_start" in skill._sounds
        assert skill._sounds["search_start"] == b"\x00\x01" * 50

    async def test_setup_warns_when_start_sound_missing(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, extra={"start_sound": "search_start"})
        skill = SearchSkill(provider=FakeSearchProvider())
        # No file written — load_pcm_palette returns empty dict, skill warns.
        await skill.setup(ctx)
        assert skill._sounds == {}
        warn_calls = [
            c
            for c in ctx.logger.awarning.await_args_list  # type: ignore[attr-defined]
            if c.args and c.args[0] == "search.start_sound_missing"
        ]
        assert len(warn_calls) == 1


class TestSuccessfulSearch:
    async def test_returns_results_with_pre_extracted_source(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(
            hits=[
                _hit(
                    title="Madrid hoy",
                    url="https://www.elpais.com/madrid/2026-04-29.html",
                    snippet="Lo que ha pasado hoy en Madrid",
                ),
            ]
        )
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("search_the_web", {"query": "qué pasó hoy en Madrid"})
        data = json.loads(result.output)

        assert data["result_count"] == 1
        assert data["say_to_user"] is None
        assert data["results"][0]["source"] == "elpais.com"
        assert data["results"][0]["title"] == "Madrid hoy"
        assert "elpais.com" in data["results"][0]["url"]

    async def test_max_results_clamped_to_cap(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        # Ask for 99 — must clamp to the cap (5)
        await skill.handle("search_the_web", {"query": "test", "max_results": 99})
        assert provider.calls[-1]["max_results"] == 5

        # Ask for 0 / negative — must clamp to 1
        await skill.handle("search_the_web", {"query": "test2", "max_results": 0})
        assert provider.calls[-1]["max_results"] == 1

    async def test_default_max_results_when_omitted(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        await skill.handle("search_the_web", {"query": "test"})
        # Default is the cap (5). Smoke test on 2026-04-29 showed
        # models economize too aggressively on max_results when the
        # description allowed 1-2; defaulting to 5 + a strict tool
        # description gets the model to send useful queries by default.
        assert provider.calls[-1]["max_results"] == 5

    async def test_safesearch_forwarded_to_provider(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, extra={"safesearch": "strict"}))

        await skill.handle("search_the_web", {"query": "x"})
        assert provider.calls[-1]["safesearch"] == "strict"

    async def test_snippet_strips_urls_and_truncates(self, tmp_path: Path) -> None:
        long_snippet = (
            "Mucho texto " * 40 + " https://example.com/long/url/path " + "más texto al final"
        )
        provider = FakeSearchProvider(hits=[_hit(snippet=long_snippet)])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("search_the_web", {"query": "x"})
        snippet = json.loads(result.output)["results"][0]["snippet"]
        # URL stripped
        assert "https://" not in snippet
        # Truncated
        assert len(snippet) <= 281  # 280 + ellipsis
        assert snippet.endswith("...")

    async def test_chime_emitted_on_success_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "search_start.wav")

        provider = FakeSearchProvider(hits=[_hit()])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, extra={"start_sound": "search_start"}))

        result = await skill.handle("search_the_web", {"query": "x"})
        assert isinstance(result.side_effect, PlaySound)
        assert len(result.side_effect.pcm) > 0

    async def test_no_chime_when_persona_has_no_sound_config(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("search_the_web", {"query": "x"})
        assert result.side_effect is None


class TestFailureModes:
    """Every failure mode must carry `say_to_user` and skip the chime."""

    async def test_empty_results_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        result = await skill.handle("search_the_web", {"query": "nada existe"})
        data = json.loads(result.output)

        assert data["result_count"] == 0
        assert data["say_to_user"] is not None
        assert "no he encontrado" in data["say_to_user"].lower()
        assert result.side_effect is None  # no chime on empty

    async def test_rate_limited_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("ddg_202"))
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        result = await skill.handle("search_the_web", {"query": "x"})
        data = json.loads(result.output)

        assert data["result_count"] == 0
        assert "no puedo buscar" in data["say_to_user"].lower()
        assert result.side_effect is None

    async def test_timeout_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchTimeoutError("4s_exceeded"))
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        result = await skill.handle("search_the_web", {"query": "x"})
        data = json.loads(result.output)
        assert "demasiado" in data["say_to_user"].lower()
        assert result.side_effect is None

    async def test_generic_error_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchProviderError("parse_error"))
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        result = await skill.handle("search_the_web", {"query": "x"})
        data = json.loads(result.output)
        assert data["say_to_user"] is not None
        assert result.side_effect is None

    async def test_empty_query_returns_recovery_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider()
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        result = await skill.handle("search_the_web", {"query": "   "})
        data = json.loads(result.output)
        assert data["result_count"] == 0
        assert data["say_to_user"] is not None
        # Provider was never called for a blank query
        assert provider.calls == []

    async def test_cancelled_error_propagates(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=asyncio.CancelledError())
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        with pytest.raises(asyncio.CancelledError):
            await skill.handle("search_the_web", {"query": "x"})


class TestRecoveryMessageLanguage:
    """Recovery messages follow the session's UI language, refreshed via reconfigure."""

    async def test_english_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="en"))

        result = await skill.handle("search_the_web", {"query": "x"})
        msg = json.loads(result.output)["say_to_user"]
        assert "didn't find" in msg.lower() or "did not" in msg.lower()

    async def test_french_message(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="fr"))

        result = await skill.handle("search_the_web", {"query": "x"})
        msg = json.loads(result.output)["say_to_user"]
        assert "rien trouvé" in msg.lower() or "n'ai rien" in msg.lower()

    async def test_reconfigure_flips_language(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        # Reconfigure to English mid-session
        await skill.reconfigure(_make_ctx(tmp_path, language="en"))

        result = await skill.handle("search_the_web", {"query": "x"})
        msg = json.loads(result.output)["say_to_user"]
        assert "didn't" in msg.lower() or "did not" in msg.lower()


class TestCache:
    async def test_cache_hit_skips_provider_call(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        await skill.handle("search_the_web", {"query": "Madrid"})
        await skill.handle("search_the_web", {"query": "Madrid"})

        assert len(provider.calls) == 1

    async def test_cache_is_case_insensitive(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        await skill.handle("search_the_web", {"query": "Madrid"})
        await skill.handle("search_the_web", {"query": "madrid"})

        assert len(provider.calls) == 1

    async def test_cache_distinguishes_max_results(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(hits=[_hit()])
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        await skill.handle("search_the_web", {"query": "Madrid", "max_results": 1})
        await skill.handle("search_the_web", {"query": "Madrid", "max_results": 5})

        # Different max_results = different cache key
        assert len(provider.calls) == 2

    async def test_failures_are_not_cached(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("x"))
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        # Need 2 calls — but circuit breaker would block 3rd. So only check 2.
        await skill.handle("search_the_web", {"query": "Madrid"})
        await skill.handle("search_the_web", {"query": "Madrid"})

        # Both calls hit the provider — failure was not cached
        assert len(provider.calls) == 2


class TestCircuitBreaker:
    async def test_opens_after_three_consecutive_failures(self, tmp_path: Path) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("x"))
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        # 3 failing calls trip the breaker
        for i in range(3):
            await skill.handle("search_the_web", {"query": f"q{i}"})

        # 4th call short-circuits — provider not called
        before = len(provider.calls)
        await skill.handle("search_the_web", {"query": "q4"})
        after = len(provider.calls)
        assert after == before

    async def test_circuit_short_circuit_returns_rate_limited_message(
        self, tmp_path: Path
    ) -> None:
        provider = FakeSearchProvider(raises=SearchRateLimitedError("x"))
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path, language="es"))

        for i in range(3):
            await skill.handle("search_the_web", {"query": f"q{i}"})

        result = await skill.handle("search_the_web", {"query": "post"})
        data = json.loads(result.output)
        # While circuit is open, response is rate-limited recovery
        assert "no puedo buscar" in data["say_to_user"].lower()

    async def test_success_resets_failure_count(self, tmp_path: Path) -> None:
        # 2 failures, then success, then 2 more failures — should NOT trip
        # because the success reset the counter.
        provider = FakeSearchProvider(
            raises=[
                SearchRateLimitedError("x"),
                SearchRateLimitedError("x"),
            ],
            hits=[_hit()],
        )
        skill = SearchSkill(provider=provider)
        await skill.setup(_make_ctx(tmp_path))

        await skill.handle("search_the_web", {"query": "q1"})  # fail 1
        await skill.handle("search_the_web", {"query": "q2"})  # fail 2
        await skill.handle("search_the_web", {"query": "q3"})  # success — resets counter
        # Now reload provider with 2 more failures
        provider.raises = [SearchRateLimitedError("x"), SearchRateLimitedError("x")]
        await skill.handle("search_the_web", {"query": "q4"})  # fail 1 again
        await skill.handle("search_the_web", {"query": "q5"})  # fail 2 again

        # Provider was called 5 times — circuit never opened
        assert len(provider.calls) == 5


class TestToolDescriptions:
    """Tool descriptions are LLM-facing — they shape dispatch behavior."""

    async def test_spanish_description_has_pre_narration_and_say_to_user_hints(
        self, tmp_path: Path
    ) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path, language="es"))
        tool = next(t for t in skill.tools if t.name == "search_the_web")
        desc = tool.description.lower()
        # Pre-narration hint (covers latency)
        assert "antes" in desc
        assert "momento" in desc or "ver" in desc
        # `say_to_user` instruction (the critic's mandatory addition)
        assert "say_to_user" in desc
        # Distinguishes itself from get_news
        assert "get_news" in desc
        # Source-citation hint (LLMs would otherwise spell URLs)
        assert "url" in desc

    async def test_english_description_present(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path, language="en"))
        tool = next(t for t in skill.tools if t.name == "search_the_web")
        desc = tool.description.lower()
        assert "before" in desc and "moment" in desc
        assert "say_to_user" in desc

    async def test_french_description_present(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path, language="fr"))
        tool = next(t for t in skill.tools if t.name == "search_the_web")
        desc = tool.description.lower()
        assert "avant" in desc and "instant" in desc
        assert "say_to_user" in desc

    async def test_max_results_capped_in_schema(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path))
        tool = next(t for t in skill.tools if t.name == "search_the_web")
        max_results_schema = tool.parameters["properties"]["max_results"]
        assert max_results_schema["maximum"] == 5
        assert max_results_schema["minimum"] == 1


class TestUnknownTool:
    async def test_returns_error_payload(self, tmp_path: Path) -> None:
        skill = SearchSkill(provider=FakeSearchProvider())
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("nonsense_tool", {})
        data = json.loads(result.output)
        assert "unknown_tool" in data["error"]


class TestPrivacyLogging:
    async def test_dispatch_log_does_not_contain_full_query_at_info(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        skill = SearchSkill(provider=FakeSearchProvider(hits=[_hit()]))
        await skill.setup(ctx)

        sensitive = "abogados divorcio en Madrid baratos"
        await skill.handle("search_the_web", {"query": sensitive})

        # Inspect every ainfo call — none should contain the full query.
        for call in ctx.logger.ainfo.await_args_list:  # type: ignore[attr-defined]
            kwargs = call.kwargs
            # Allow query_hash + query_len; never the full query at info
            assert "query" not in kwargs or kwargs.get("query") != sensitive
