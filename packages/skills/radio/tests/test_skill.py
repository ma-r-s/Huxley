"""Tests for huxley-skill-radio.

Drives the skill through `setup → handle` with a `FakeRadioPlayer`.
No real ffmpeg, no real network.
"""

from __future__ import annotations

import json
import wave
from typing import TYPE_CHECKING

import pytest

from huxley_sdk import AudioStream, CancelMedia
from huxley_sdk.testing import make_test_context
from huxley_skill_radio.skill import LAST_STATION_KEY, RadioSkill

from .conftest import FakeRadioPlayer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


# --- Helpers ---


_DEFAULT_STATIONS = [
    {"id": "alpha", "name": "Alpha FM", "url": "https://example.com/alpha"},
    {"id": "beta", "name": "Beta News", "url": "https://example.com/beta"},
    {"id": "gamma", "name": "Gamma Cultural", "url": "https://example.com/gamma"},
]


def _make_ctx(
    tmp_path: Path,
    *,
    extra: dict[str, object] | None = None,
) -> object:
    config: dict[str, object] = {
        "stations": _DEFAULT_STATIONS,
        "default": "alpha",
        "language_code": "es",
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


async def _drain(factory: object) -> list[bytes]:
    chunks: list[bytes] = []
    iterator: AsyncIterator[bytes] = factory()  # type: ignore[operator]
    async for chunk in iterator:
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------


class TestSetup:
    async def test_required_config_keys_validated(self, tmp_path: Path) -> None:
        ctx = make_test_context(
            config={"stations": _DEFAULT_STATIONS},  # missing `default`
            persona_data_dir=tmp_path,
        )
        skill = RadioSkill(player=FakeRadioPlayer())
        with pytest.raises(ValueError, match="missing required config"):
            await skill.setup(ctx)

    async def test_default_must_match_a_station_id(self, tmp_path: Path) -> None:
        ctx = make_test_context(
            config={"stations": _DEFAULT_STATIONS, "default": "nonexistent"},
            persona_data_dir=tmp_path,
        )
        skill = RadioSkill(player=FakeRadioPlayer())
        with pytest.raises(ValueError, match="default"):
            await skill.setup(ctx)

    async def test_stations_list_must_be_non_empty(self, tmp_path: Path) -> None:
        ctx = make_test_context(
            config={"stations": [], "default": "alpha"},
            persona_data_dir=tmp_path,
        )
        skill = RadioSkill(player=FakeRadioPlayer())
        with pytest.raises(ValueError, match="non-empty list"):
            await skill.setup(ctx)

    async def test_each_station_must_have_id_name_url(self, tmp_path: Path) -> None:
        ctx = make_test_context(
            config={
                "stations": [{"id": "alpha", "name": "Alpha"}],  # missing url
                "default": "alpha",
            },
            persona_data_dir=tmp_path,
        )
        skill = RadioSkill(player=FakeRadioPlayer())
        with pytest.raises(ValueError, match="url"):
            await skill.setup(ctx)

    async def test_setup_loads_chime_when_configured(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        _write_pcm_wav(sounds_dir / "radio_start.wav", frames=b"\xab\xcd" * 50)

        ctx = _make_ctx(tmp_path, extra={"start_sound": "radio_start"})
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(ctx)
        assert "radio_start" in skill._sounds
        assert skill._sounds["radio_start"] == b"\xab\xcd" * 50


class TestPlayStation:
    async def test_default_station_played_when_no_arg(self, tmp_path: Path) -> None:
        player = FakeRadioPlayer()
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("play_station", {})
        data = json.loads(result.output)

        assert data["playing"] is True
        assert data["station_id"] == "alpha"
        assert isinstance(result.side_effect, AudioStream)

        # Drain the factory; FakeRadioPlayer should have been called with alpha's URL
        await _drain(result.side_effect.factory)
        assert player.urls_streamed == ["https://example.com/alpha"]

    async def test_named_station_played(self, tmp_path: Path) -> None:
        player = FakeRadioPlayer()
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("play_station", {"station": "beta"})
        data = json.loads(result.output)
        assert data["station_id"] == "beta"
        assert isinstance(result.side_effect, AudioStream)
        await _drain(result.side_effect.factory)
        assert player.urls_streamed == ["https://example.com/beta"]

    async def test_station_name_fuzzy_resolves_to_id(self, tmp_path: Path) -> None:
        """If LLM passes a name instead of id, skill recovers via case-insensitive match."""
        player = FakeRadioPlayer()
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("play_station", {"station": "beta news"})
        data = json.loads(result.output)
        assert data["playing"] is True
        assert data["station_id"] == "beta"

    async def test_unknown_station_returns_error_payload(self, tmp_path: Path) -> None:
        player = FakeRadioPlayer()
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("play_station", {"station": "delta"})
        data = json.loads(result.output)

        assert data["playing"] is False
        assert data["error"] == "unknown_station"
        assert data["requested"] == "delta"
        # Available list helps the LLM offer alternatives (never_say_no)
        assert {"id": "alpha", "name": "Alpha FM"} in data["available"]
        # No AudioStream side effect on error
        assert result.side_effect is None
        # No url streamed
        assert player.urls_streamed == []

    async def test_play_station_writes_last_id(self, tmp_path: Path) -> None:
        player = FakeRadioPlayer()
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))

        await skill.handle("play_station", {"station": "gamma"})
        last = await skill._storage.get_setting(LAST_STATION_KEY)  # type: ignore[union-attr]
        assert last == "gamma"

    async def test_chime_yielded_as_first_chunk(self, tmp_path: Path) -> None:
        sounds_dir = tmp_path / "sounds"
        sounds_dir.mkdir()
        chime_bytes = b"\xff\xee" * 20
        _write_pcm_wav(sounds_dir / "radio_start.wav", frames=chime_bytes)

        player = FakeRadioPlayer(default_chunks=[b"after_chime_1", b"after_chime_2"])
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path, extra={"start_sound": "radio_start"}))

        result = await skill.handle("play_station", {})
        assert isinstance(result.side_effect, AudioStream)
        chunks = await _drain(result.side_effect.factory)
        # First chunk is the chime; subsequent chunks are the radio stream
        assert chunks[0] == chime_bytes
        assert chunks[1:] == [b"after_chime_1", b"after_chime_2"]

    async def test_no_chime_when_persona_omits_start_sound(self, tmp_path: Path) -> None:
        player = FakeRadioPlayer(default_chunks=[b"radio_only"])
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))  # no start_sound

        result = await skill.handle("play_station", {})
        assert isinstance(result.side_effect, AudioStream)
        chunks = await _drain(result.side_effect.factory)
        assert chunks == [b"radio_only"]


class TestResumeRadio:
    async def test_resume_with_no_history_returns_no_history_payload(self, tmp_path: Path) -> None:
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("resume_radio", {})
        data = json.loads(result.output)
        assert data["playing"] is False
        assert data["reason"] == "no_history"
        # never_say_no — must include a constructive next step in the message
        assert "emisora" in data["message"].lower() or "station" in data["message"].lower()
        assert result.side_effect is None

    async def test_resume_uses_last_station_id(self, tmp_path: Path) -> None:
        player = FakeRadioPlayer()
        skill = RadioSkill(player=player)
        await skill.setup(_make_ctx(tmp_path))
        await skill._storage.set_setting(LAST_STATION_KEY, "gamma")  # type: ignore[union-attr]

        result = await skill.handle("resume_radio", {})
        data = json.loads(result.output)
        assert data["playing"] is True
        assert data["station_id"] == "gamma"
        await _drain(result.side_effect.factory)  # type: ignore[union-attr]
        assert player.urls_streamed == ["https://example.com/gamma"]


class TestStopRadio:
    async def test_stop_emits_cancel_media(self, tmp_path: Path) -> None:
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("stop_radio", {})
        data = json.loads(result.output)
        assert data["stopped"] is True
        assert isinstance(result.side_effect, CancelMedia)


class TestListStations:
    async def test_returns_id_name_description(self, tmp_path: Path) -> None:
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(_make_ctx(tmp_path))

        result = await skill.handle("list_stations", {})
        data = json.loads(result.output)

        assert data["count"] == 3
        assert data["default"] == "alpha"
        ids = [s["id"] for s in data["stations"]]
        assert ids == ["alpha", "beta", "gamma"]


class TestToolDescriptions:
    async def test_descriptions_in_spanish_when_configured(self, tmp_path: Path) -> None:
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(_make_ctx(tmp_path))

        play = next(t for t in skill.tools if t.name == "play_station")
        # The pre-narration hint must be in the description so the model
        # knows to say something while the stream loads
        assert "antes" in play.description.lower() or "a ver" in play.description.lower()
        # Station ids must be listed so the LLM passes real ones
        assert "alpha" in play.description
        assert "beta" in play.description

    async def test_descriptions_in_english_when_configured(self, tmp_path: Path) -> None:
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(_make_ctx(tmp_path, extra={"language_code": "en"}))

        play = next(t for t in skill.tools if t.name == "play_station")
        assert "before" in play.description.lower() or "moment" in play.description.lower()
        assert "alpha" in play.description


class TestUnknownTool:
    async def test_returns_error_payload(self, tmp_path: Path) -> None:
        skill = RadioSkill(player=FakeRadioPlayer())
        await skill.setup(_make_ctx(tmp_path))
        result = await skill.handle("nonsense_tool", {})
        data = json.loads(result.output)
        assert "unknown_tool" in data["error"]
