"""Tests for huxley-skill-spotify: setup → handle → drain the stream.

Soloist is faked (`FakeSoloist`); the PCM capture is a real localhost
TCP server, so the read path is the production one.
"""

from __future__ import annotations

import asyncio
import json
import wave
from typing import TYPE_CHECKING, Any

import pytest

from huxley_sdk import AudioStream, CancelMedia, ContentType
from huxley_skill_spotify import skill as skill_module
from huxley_skill_spotify.capture import CHUNK_BYTES
from huxley_skill_spotify.search import Match
from huxley_skill_spotify.skill import SpotifySkill

from .conftest import FakeSearch, FakeSoloist, PcmServer, Recorded, make_ctx

if TYPE_CHECKING:
    from pathlib import Path

ARTIST = Match(
    uri="spotify:track:a1",
    kind="artist",
    title="Los Panchos",
    artist="Los Panchos",
    queue=("spotify:track:a2", "spotify:track:a3"),
)


def _earcon(tmp_path: Path) -> bytes:
    frames = b"\x10\x00" * 2400
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    with wave.open(str(sounds / "radio_start.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(frames)
    return frames


async def _skill(
    tmp_path: Path,
    pcm: PcmServer,
    *,
    match: Match | None = ARTIST,
    earcon: bool = False,
) -> tuple[SpotifySkill, FakeSoloist, Recorded]:
    client = FakeSoloist()
    recorded = Recorded()
    config: dict[str, Any] = {"pcm": pcm.address}
    if earcon:
        _earcon(tmp_path)
        config.update(start_sound="radio_start", sounds_path="sounds")
    skill = SpotifySkill(client=client, search=FakeSearch(match))  # type: ignore[arg-type]
    await skill.setup(make_ctx(tmp_path, recorded, config=config))
    return skill, client, recorded


async def _drain(stream: AudioStream) -> list[bytes]:
    return [chunk async for chunk in stream.factory()]


async def _settle() -> None:
    # Let scheduled narration tasks run.
    for _ in range(3):
        await asyncio.sleep(0)


class TestPlay:
    async def test_play_streams_the_match_with_earcon_and_queue(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server, earcon=True)
        result = await skill.handle("play_music", {"query": "Los Panchos", "kind": "artist"})

        payload = json.loads(result.output)
        assert payload == {
            "ok": True,
            "playing": "Los Panchos",
            "by": "Los Panchos",
            "kind": "artist",
        }
        stream = result.side_effect
        assert isinstance(stream, AudioStream)
        assert stream.content_type is ContentType.NONMIXABLE
        assert stream.patience is not None and stream.patience.total_seconds() == 30 * 60
        assert stream.preroll_ms == 100

        chunks = await _drain(stream)
        assert chunks[0] == b"\x10\x00" * 2400  # earcon first
        assert chunks[1:] == [bytes([1]) * CHUNK_BYTES] * 3
        assert client.commands() == [
            "set_volume",
            "play",
            "add_to_queue",
            "add_to_queue",
            "pause",  # source closed: the finally pauses Soloist
        ]
        assert client.sent[1]["uri"] == "spotify:track:a1"
        assert [c["uri"] for c in client.sent[2:4]] == ["spotify:track:a2", "spotify:track:a3"]

    async def test_second_factory_call_resumes_without_earcon(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server, earcon=True)
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)
        await _drain(stream)
        client.sent.clear()

        # A reminder paused it (MUST_PAUSE); FOREGROUND calls the factory again.
        chunks = await _drain(stream)

        assert chunks == [bytes([1]) * CHUNK_BYTES] * 3  # no earcon
        play = next(c for c in client.sent if c["command"] == "play")
        assert "uri" not in play  # resume, not restart
        assert "add_to_queue" not in client.commands()

    async def test_cancel_queues_pause_tagged_with_its_generation(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _ = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)

        got: list[bytes] = []
        streaming = asyncio.Event()

        async def consume() -> None:
            async for chunk in stream.factory():
                got.append(chunk)
                if len(got) >= 4:
                    streaming.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(streaming.wait(), 2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        pause = client.sent[-1]
        play = next(c for c in client.sent if c["command"] == "play")
        assert pause["command"] == "pause"
        assert pause["generation"] == play["generation"] == client.generation


class TestStreamEnds:
    async def test_takeover_ends_stream_narrates_and_does_not_pause(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)

        chunks: list[bytes] = []
        async for chunk in stream.factory():
            chunks.append(chunk)
            if len(chunks) == 2:
                client.state.is_active = False  # a phone picked another device
        await _settle()

        assert len(chunks) == 2
        assert "pause" not in client.commands()
        assert [key for _, key in recorded.injected] == ["spotify:takeover"]
        assert "otro aparato" in recorded.injected[0][0]

    async def test_end_of_music_ends_stream_quietly(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)

        chunks: list[bytes] = []
        async for chunk in stream.factory():
            chunks.append(chunk)
            client.state.status = "idle"  # the album ran out
        await _settle()

        assert len(chunks) == 1
        assert "pause" not in client.commands()
        assert recorded.injected == []

    async def test_start_timeout_narrates_and_pauses(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "START_TIMEOUT_S", 0.05)
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        client.start_playing = False
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)

        chunks = await _drain(stream)
        await _settle()

        assert chunks == []
        assert client.commands()[-1] == "pause"
        assert [key for _, key in recorded.injected] == ["spotify:start_failed"]

    async def test_soloist_error_ends_start_at_once(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        client.start_playing = False
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)

        async def soloist_errors() -> None:
            await asyncio.sleep(0.05)
            await client._update(last_error="track unavailable", error_count=1)

        erroring = asyncio.create_task(soloist_errors())
        chunks = await asyncio.wait_for(_drain(stream), 2.0)  # well under START_TIMEOUT_S
        await erroring
        await _settle()

        assert chunks == []
        assert [key for _, key in recorded.injected] == ["spotify:start_failed"]

    async def test_unreachable_capture_narrates_offline(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        skill._pcm_port = 1  # nothing listens there
        stream = (await skill.handle("play_music", {"query": "x"})).side_effect
        assert isinstance(stream, AudioStream)

        assert await _drain(stream) == []
        await _settle()
        assert "play" not in client.commands()
        assert [key for _, key in recorded.injected] == ["spotify:player_offline"]


class TestFailures:
    @pytest.mark.parametrize(
        ("connected", "logged_in", "reason"),
        [(False, True, "player_offline"), (True, False, "player_not_paired")],
    )
    async def test_unavailable_player_answers_with_spoken_alternative(
        self,
        tmp_path: Path,
        pcm_server: PcmServer,
        connected: bool,
        logged_in: bool,
        reason: str,
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        client.state.connected = connected
        client.state.logged_in = logged_in

        result = await skill.handle("play_music", {"query": "x"})

        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["reason"] == reason
        assert "radio" in payload["say"]  # never_say_no: offer something
        assert result.side_effect is None

    async def test_not_found(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _ = await _skill(tmp_path, pcm_server, match=None)
        result = await skill.handle("play_music", {"query": "zzz"})
        payload = json.loads(result.output)
        assert payload["reason"] == "not_found"
        assert payload["say"]
        assert result.side_effect is None


class TestControl:
    async def test_pause_cancels_media_and_pauses_soloist(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("music_control", {"action": "pause"})
        assert isinstance(result.side_effect, CancelMedia)
        assert client.commands() == ["pause"]

    @pytest.mark.parametrize(
        ("action", "command"), [("next", "skip_next"), ("previous", "skip_prev")]
    )
    async def test_skip_then_resume(
        self, tmp_path: Path, pcm_server: PcmServer, action: str, command: str
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        stream = (await skill.handle("music_control", {"action": action})).side_effect
        assert isinstance(stream, AudioStream)
        await _drain(stream)
        assert client.commands()[0] == command
        play = next(c for c in client.sent if c["command"] == "play")
        assert "uri" not in play

    async def test_whats_playing_answers_and_resumes(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        client.state.item = {"name": "Sin Ti", "artists": [{"name": "Los Panchos"}]}
        result = await skill.handle("whats_playing", {})
        assert json.loads(result.output) == {"ok": True, "title": "Sin Ti", "by": "Los Panchos"}
        assert isinstance(result.side_effect, AudioStream)

    async def test_whats_playing_with_nothing(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("whats_playing", {})
        assert json.loads(result.output)["reason"] == "nothing_playing"
        assert result.side_effect is None


class TestSetup:
    async def test_background_client_started(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        _, _, recorded = await _skill(tmp_path, pcm_server)
        assert recorded.tasks == ["spotify.soloist"]

    async def test_bad_pcm_address_fails_fast(self, tmp_path: Path) -> None:
        skill = SpotifySkill(client=FakeSoloist(), search=FakeSearch(None))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="host:port"):
            await skill.setup(make_ctx(tmp_path, Recorded(), config={"pcm": "nope"}))

    async def test_tools_are_spanish_for_spanish_sessions(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, _, _ = await _skill(tmp_path, pcm_server)
        names = [t.name for t in skill.tools]
        assert names == ["play_music", "music_control", "whats_playing"]
        assert "nunca inventes" in skill.tools[0].description
