"""Tests for huxley-skill-news.

Drives the skill through `setup → handle` with a `FakeHttpClient` that
returns canned Open-Meteo JSON + Google News RSS XML. No real network.
"""

from __future__ import annotations

import json
import wave
from typing import TYPE_CHECKING

import pytest

from huxley_sdk import AudioStream, PlaySound
from huxley_sdk.testing import make_test_context
from huxley_skill_news.skill import NewsSkill

from .conftest import FakeHttpClient

if TYPE_CHECKING:
    from pathlib import Path


# --- Helpers ---


def _open_meteo_response(temp: float = 18.0, code: int = 2) -> str:
    """Minimal Open-Meteo JSON. Code 2 = partly cloudy."""
    return json.dumps(
        {
            "current": {
                "temperature_2m": temp,
                "relative_humidity_2m": 60,
                "wind_speed_10m": 12.0,
                "weather_code": code,
            },
            "daily": {
                "temperature_2m_max": [22.0],
                "temperature_2m_min": [14.0],
                "weather_code": [code],
            },
        }
    )


def _google_news_rss(items: list[tuple[str, str]] | None = None) -> str:
    """Build a minimal Google News RSS feed.

    Each (title, source) pair becomes an <item>. Pub dates are recent so
    items pass the freshness filter.
    """
    if items is None:
        items = [
            ("Headline one - El Foo", "El Foo"),
            ("Headline two - El Bar", "El Bar"),
        ]
    item_xml = "\n".join(
        f"""
        <item>
            <title>{title}</title>
            <description>&lt;a href="..."&gt;{title}&lt;/a&gt;</description>
            <pubDate>Tue, 17 Apr 2026 22:00:00 GMT</pubDate>
        </item>
        """
        for title, _ in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    {item_xml}
  </channel>
</rss>"""


def _make_ctx(
    tmp_path: Path,
    *,
    extra: dict[str, object] | None = None,
) -> object:
    config: dict[str, object] = {
        "location": "Testville",
        "latitude": 4.142,
        "longitude": -73.626,
        "country_code": "CO",
        "language_code": "es",
        "max_age_hours": 999,  # tests use fixed pubDate; don't filter on age
    }
    if extra:
        config.update(extra)
    return make_test_context(config=config, persona_data_dir=tmp_path)


def _write_pcm_wav(path: Path, frames: bytes = b"\x00\x01" * 100) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(frames)


# ---------------------------------------------------------------------------


class TestSetup:
    async def test_required_config_keys_are_validated(self, tmp_path: Path) -> None:
        ctx = make_test_context(
            config={"location": "Testville"},  # missing latitude/longitude/etc.
            persona_data_dir=tmp_path,
        )
        skill = NewsSkill(http=FakeHttpClient())
        with pytest.raises(ValueError, match="missing required config"):
            await skill.setup(ctx)

    async def test_setup_loads_chime_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "news_start.wav", frames=b"\x00\x01" * 50)

        ctx = _make_ctx(tmp_path, extra={"start_sound": "news_start"})
        skill = NewsSkill(http=FakeHttpClient())
        await skill.setup(ctx)
        assert "news_start" in skill._sounds
        assert skill._sounds["news_start"] == b"\x00\x01" * 50

    async def test_setup_runs_silently_with_no_chime_config(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)  # no start_sound
        skill = NewsSkill(http=FakeHttpClient())
        await skill.setup(ctx)
        assert skill._sounds == {}
        assert skill._start_sound_role is None


class TestGetNews:
    async def test_default_call_fetches_top_stories_and_weather(self, tmp_path: Path) -> None:
        http = FakeHttpClient(
            responses={
                "open-meteo.com": _open_meteo_response(),
                "news.google.com/rss?": _google_news_rss([("Big story - El País", "El País")]),
            }
        )
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_news", {})
        data = json.loads(result.output)

        assert data["location"] == "Testville"
        assert "fetched_at" in data
        assert data["weather"]["current"]["temperature"] == 18.0
        assert data["weather"]["current"]["condition_key"] == "partly_cloudy"
        assert data["item_count"] == 1
        assert data["items"][0]["title"] == "Big story"
        assert data["items"][0]["source"] == "El País"

    async def test_category_filter_uses_topic_feed(self, tmp_path: Path) -> None:
        http = FakeHttpClient(
            responses={
                "open-meteo.com": _open_meteo_response(),
                "topic/SPORTS": _google_news_rss([("Match recap - Marca", "Marca")]),
            }
        )
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_news", {"category": "sports"})
        data = json.loads(result.output)

        assert data["filter"]["category"] == "sports"
        assert data["items"][0]["category"] == "sports"
        assert any("topic/SPORTS" in u for u in http.requested_urls)

    async def test_query_uses_search_feed(self, tmp_path: Path) -> None:
        http = FakeHttpClient(
            responses={
                "open-meteo.com": _open_meteo_response(),
                "rss/search?q=Petro": _google_news_rss([("Petro habla - El Tiempo", "El Tiempo")]),
            }
        )
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_news", {"query": "Petro"})
        data = json.loads(result.output)

        assert data["filter"]["query"] == "Petro"
        assert any("rss/search?q=Petro" in u for u in http.requested_urls)

    async def test_fetch_failure_returns_structured_error(self, tmp_path: Path) -> None:
        http = FakeHttpClient(
            responses={"open-meteo.com": _open_meteo_response()},
            raises={"news.google.com": "network_error:ConnectError"},
        )
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_news", {})
        data = json.loads(result.output)

        assert data["error"] == "fetch_failed"
        assert data["reason"] == "network_error:ConnectError"
        assert "retry_after_seconds" in data
        # Errors do NOT trigger the chime — that signals success.
        assert result.side_effect is None

    async def test_chime_emitted_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "news_start.wav")

        http = FakeHttpClient(
            responses={
                "open-meteo.com": _open_meteo_response(),
                "news.google.com": _google_news_rss(),
            }
        )
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path, extra={"start_sound": "news_start"}))

        result = await skill.handle("get_news", {})
        assert isinstance(result.side_effect, PlaySound)
        assert len(result.side_effect.pcm) > 0

    async def test_no_chime_when_persona_has_no_sound_config(self, tmp_path: Path) -> None:
        http = FakeHttpClient(
            responses={
                "open-meteo.com": _open_meteo_response(),
                "news.google.com": _google_news_rss(),
            }
        )
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))  # no start_sound

        result = await skill.handle("get_news", {})
        assert result.side_effect is None
        # And no AudioStream either — this is an info tool, not media playback
        assert not isinstance(result.side_effect, AudioStream)

    async def test_cache_returns_same_payload_within_ttl(self, tmp_path: Path) -> None:
        http = FakeHttpClient(
            responses={
                "open-meteo.com": _open_meteo_response(),
                "news.google.com": _google_news_rss(),
            }
        )
        skill = NewsSkill(http=http)
        ctx = _make_ctx(tmp_path)
        await skill.setup(ctx)

        await skill.handle("get_news", {})
        request_count_after_first = len(http.requested_urls)
        await skill.handle("get_news", {})
        # Second call hits cache → no new HTTP requests
        assert len(http.requested_urls) == request_count_after_first
        # Cache hit must be logged so it's visible in production logs
        # (otherwise debugging "the chime fired but the news is the same"
        # requires guessing whether the tool ran with stale data).
        cache_hit_calls = [
            c
            for c in ctx.logger.ainfo.await_args_list  # type: ignore[attr-defined]
            if c.args and c.args[0] == "news.cache_hit"
        ]
        assert len(cache_hit_calls) == 1

    async def test_category_weather_routes_to_weather_handler(self, tmp_path: Path) -> None:
        http = FakeHttpClient(responses={"open-meteo.com": _open_meteo_response()})
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_news", {"category": "weather"})
        data = json.loads(result.output)
        # Weather-handler shape: no `items` key
        assert "items" not in data
        assert "weather" in data
        # And no Google News fetch happened
        assert not any("news.google.com" in u for u in http.requested_urls)


class TestGetWeather:
    async def test_returns_current_and_forecast(self, tmp_path: Path) -> None:
        http = FakeHttpClient(responses={"open-meteo.com": _open_meteo_response(temp=25.0)})
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_weather", {})
        data = json.loads(result.output)

        assert data["location"] == "Testville"
        assert data["weather"]["current"]["temperature"] == 25.0
        assert data["weather"]["today"]["high"] == 22.0
        assert data["weather"]["today"]["low"] == 14.0

    async def test_imperial_units_propagate_to_open_meteo(self, tmp_path: Path) -> None:
        http = FakeHttpClient(responses={"open-meteo.com": _open_meteo_response()})
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path, extra={"units": "imperial"}))

        await skill.handle("get_weather", {})
        url = next(u for u in http.requested_urls if "open-meteo.com" in u)
        assert "temperature_unit=fahrenheit" in url
        assert "wind_speed_unit=mph" in url

    async def test_fetch_failure_returns_structured_error(self, tmp_path: Path) -> None:
        http = FakeHttpClient(raises={"open-meteo.com": "timeout"})
        skill = NewsSkill(http=http)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("get_weather", {})
        data = json.loads(result.output)
        assert data["error"] == "fetch_failed"
        assert data["reason"] == "timeout"


class TestToolDescriptions:
    """Tool descriptions are LLM-facing — they shape dispatch behavior."""

    async def test_descriptions_in_spanish_when_configured(self, tmp_path: Path) -> None:
        skill = NewsSkill(http=FakeHttpClient())
        await skill.setup(_make_ctx(tmp_path))
        get_news = next(t for t in skill.tools if t.name == "get_news")
        assert "noticias" in get_news.description.lower()
        # Pre-narration hint must be present so the model doesn't leave dead air
        assert "antes" in get_news.description.lower() or "momento" in get_news.description.lower()

    async def test_descriptions_in_english_when_configured(self, tmp_path: Path) -> None:
        skill = NewsSkill(http=FakeHttpClient())
        await skill.setup(_make_ctx(tmp_path, extra={"language_code": "en"}))
        get_news = next(t for t in skill.tools if t.name == "get_news")
        assert "news" in get_news.description.lower()
        # Pre-narration hint
        assert "before" in get_news.description.lower() or "moment" in get_news.description.lower()

    async def test_interests_appear_in_tool_description(self, tmp_path: Path) -> None:
        skill = NewsSkill(http=FakeHttpClient())
        await skill.setup(_make_ctx(tmp_path, extra={"interests": ["politica", "futbol"]}))
        get_news = next(t for t in skill.tools if t.name == "get_news")
        assert "politica" in get_news.description
        assert "futbol" in get_news.description


class TestUnknownTool:
    async def test_returns_error_payload(self, tmp_path: Path) -> None:
        skill = NewsSkill(http=FakeHttpClient())
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("nonsense_tool", {})
        data = json.loads(result.output)
        assert "unknown_tool" in data["error"]
