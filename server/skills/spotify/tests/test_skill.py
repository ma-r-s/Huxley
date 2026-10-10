"""Tests for huxley-skill-spotify: setup → handle → drain the stream.

Soloist is faked (`FakeSoloist`), except in `TestAgainstRealClient`,
which runs the real `SoloistClient` against a local WebSocket server
replaying the event sequence observed from Soloist on 2026-10-10. The
PCM capture is always a real localhost TCP server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import wave
from typing import TYPE_CHECKING, Any

import pytest
import websockets

from huxley_sdk import AudioStream, CancelMedia, ContentType
from huxley_skill_spotify import skill as skill_module
from huxley_skill_spotify.capture import CHUNK_BYTES
from huxley_skill_spotify.search import Match
from huxley_skill_spotify.skill import SpotifySkill
from huxley_skill_spotify.soloist import SoloistClient

from .conftest import FakeSearch, FakeSoloist, PcmServer, Recorded, make_ctx

if TYPE_CHECKING:
    from pathlib import Path

ARTIST = Match(
    uri="spotify:artist:panchos", kind="artist", title="Los Panchos", artist="Los Panchos"
)
CHUNK = bytes([1]) * CHUNK_BYTES


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
    client: SoloistClient | None = None,
) -> tuple[SpotifySkill, Any, Recorded]:
    client = client or FakeSoloist()
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


async def _consume_until(
    stream: AudioStream, count: int
) -> tuple[asyncio.Task[None], list[bytes]]:
    """Run the stream in a task until `count` chunks arrived."""
    got: list[bytes] = []
    enough = asyncio.Event()

    async def consume() -> None:
        async for chunk in stream.factory():
            got.append(chunk)
            if len(got) >= count:
                enough.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(enough.wait(), 2.0)
    return task, got


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _settle() -> None:
    # Let scheduled narration tasks run.
    for _ in range(3):
        await asyncio.sleep(0)


async def _play(skill: SpotifySkill, query: str = "x") -> AudioStream:
    stream = (await skill.handle("play_music", {"query": query})).side_effect
    assert isinstance(stream, AudioStream)
    return stream


class TestPlay:
    async def test_play_streams_the_match_with_earcon(
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
        assert stream.on_patience_expired is not None

        task, got = await _consume_until(stream, 3)
        await _cancel(task)

        assert got[0] == b"\x10\x00" * 2400  # earcon first
        assert got[1:3] == [CHUNK, CHUNK]
        # Volume is pinned only once playback is confirmed.
        assert client.commands() == ["play", "set_volume", "pause"]
        assert client.sent[0]["uri"] == "spotify:artist:panchos"

    async def test_first_chunks_while_soloist_turns_active_are_not_a_takeover(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """A freshly started Soloist reports is_active false until a moment
        after playback starts. That must not read as someone taking over."""
        pcm_server.hold_open.set()
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        task, got = await _consume_until(await _play(skill), 5)  # still inactive
        client.activate()
        await asyncio.sleep(0.05)
        await _cancel(task)
        await _settle()

        assert len(got) >= 5
        assert recorded.injected == []

    async def test_second_factory_call_resumes_without_earcon(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server, earcon=True)
        stream = await _play(skill)
        task, _ = await _consume_until(stream, 2)
        await _cancel(task)
        client.sent.clear()

        # A reminder paused it (MUST_PAUSE); FOREGROUND calls the factory again.
        task, got = await _consume_until(stream, 2)
        await _cancel(task)

        assert got[:2] == [CHUNK, CHUNK]  # no earcon
        play = next(c for c in client.sent if c["command"] == "play")
        assert "uri" not in play  # resume, not restart

    async def test_cancel_before_play_leaves_the_next_run_to_start_it(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        stream = await _play(skill)
        run = stream.factory()
        first = asyncio.ensure_future(run.__anext__())
        first.cancel()  # cancelled while connecting the capture
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await first
        assert "play" not in client.commands()

        task, _ = await _consume_until(stream, 1)
        await _cancel(task)

        play = next(c for c in client.sent if c["command"] == "play")
        assert play["uri"] == "spotify:artist:panchos"

    async def test_cancel_queues_pause_tagged_with_its_generation(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, _ = await _skill(tmp_path, pcm_server)
        task, _ = await _consume_until(await _play(skill), 4)
        await _cancel(task)

        pause = client.sent[-1]
        play = next(c for c in client.sent if c["command"] == "play")
        assert pause["command"] == "pause"
        assert pause["generation"] == play["generation"] == client.generation


class TestStreamEnds:
    async def test_takeover_after_being_active_ends_narrates_and_does_not_pause(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        client.activate()
        chunks: list[bytes] = []
        async for chunk in (await _play(skill)).factory():
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
        chunks: list[bytes] = []
        async for chunk in (await _play(skill)).factory():
            chunks.append(chunk)
            client.state.status = "idle"  # the album ran out
        await _settle()

        assert len(chunks) == 1
        assert "pause" not in client.commands()
        assert recorded.injected == []

    async def test_paused_from_another_app_frees_the_speaker(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "PAUSED_GRACE_S", 0.05)
        pcm_server.hold_open.set()
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        chunks: list[bytes] = []
        stream = await _play(skill)
        async for chunk in stream.factory():
            chunks.append(chunk)
            client.state.status = "paused"
        await _settle()

        assert len(chunks) >= 2  # the grace period, then out
        assert client.commands().count("pause") == 0
        assert recorded.injected == []

    async def test_stale_playing_from_old_context_is_not_a_start(
        self, tmp_path: Path, pcm_server: PcmServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(skill_module, "START_TIMEOUT_S", 0.05)
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        client.start_playing = False
        client.state.status = "playing"  # still the previous context
        client.state.context_uri = "spotify:playlist:old"

        assert await _drain(await _play(skill)) == []
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:start_failed"]

    async def test_soloist_error_ends_start_at_once(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        client.start_playing = False
        stream = await _play(skill)

        async def soloist_errors() -> None:
            await asyncio.sleep(0.05)
            await client._update(last_error="track unavailable", error_count=1)

        erroring = asyncio.create_task(soloist_errors())
        chunks = await asyncio.wait_for(_drain(stream), 2.0)  # well under START_TIMEOUT_S
        await erroring
        await _settle()

        assert chunks == []
        assert client.commands()[-1] == "pause"
        assert [key for _, key in recorded.injected] == ["spotify:start_failed"]

    async def test_capture_refused_narrates_offline(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        skill._pcm_port = 1  # nothing listens there
        assert await _drain(await _play(skill)) == []
        await _settle()
        assert "play" not in client.commands()
        assert [key for _, key in recorded.injected] == ["spotify:player_offline"]

    async def test_capture_closing_without_audio_narrates_offline(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        """Docker Desktop accepts the port even with the sound server down:
        connect, then immediate close."""
        pcm_server.chunks = []
        skill, _, recorded = await _skill(tmp_path, pcm_server)
        assert await _drain(await _play(skill)) == []
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:player_offline"]

    async def test_capture_dropping_mid_song_narrates_the_cut(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, recorded = await _skill(tmp_path, pcm_server)
        chunks = await _drain(await _play(skill))  # 3 chunks, then the source closes
        await _settle()
        assert chunks == [CHUNK] * 3
        assert client.commands()[-1] == "pause"
        assert [key for _, key in recorded.injected] == ["spotify:interrupted"]

    async def test_patience_expiry_is_spoken(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, recorded = await _skill(tmp_path, pcm_server)
        stream = await _play(skill)
        assert stream.on_patience_expired is not None
        await stream.on_patience_expired()
        await _settle()
        assert [key for _, key in recorded.injected] == ["spotify:patience_expired"]


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


SIN_TI = {
    "uri": "spotify:track:sinti",
    "entity_type": "track",
    "decorations": {
        "identity": {"name": "Sin Ti"},
        "creators": [{"entity": {"decorations": {"identity": {"name": "Los Panchos"}}}}],
    },
}


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
        task, _ = await _consume_until(stream, 1)
        await _cancel(task)
        assert client.commands()[0] == command
        play = next(c for c in client.sent if c["command"] == "play")
        assert "uri" not in play

    async def test_whats_playing_after_a_press_answers_and_resumes(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        task, _ = await _consume_until(await _play(skill), 2)
        await _cancel(task)  # the press that asked "¿qué canción es esta?"
        client.state.item = SIN_TI

        result = await skill.handle("whats_playing", {})

        assert json.loads(result.output) == {"ok": True, "title": "Sin Ti", "by": "Los Panchos"}
        assert isinstance(result.side_effect, AudioStream)

    async def test_whats_playing_after_pausa_does_not_resume(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        skill, client, _ = await _skill(tmp_path, pcm_server)
        task, _ = await _consume_until(await _play(skill), 2)
        await _cancel(task)
        await skill.handle("music_control", {"action": "pause"})
        client.state.item = SIN_TI

        result = await skill.handle("whats_playing", {})

        assert json.loads(result.output)["title"] == "Sin Ti"
        assert result.side_effect is None

    async def test_whats_playing_with_nothing(self, tmp_path: Path, pcm_server: PcmServer) -> None:
        skill, _, _ = await _skill(tmp_path, pcm_server)
        result = await skill.handle("whats_playing", {})
        assert json.loads(result.output)["reason"] == "nothing_playing"
        assert result.side_effect is None


class FakeSoloistServer:
    """Answers `play` with the event sequence real Soloist sent on
    2026-10-10: a stale "playing" for the old context first, then the
    new context, a track, and only then device_changed(active)."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    async def handler(self, ws: Any) -> None:
        async def emit(event: dict[str, Any]) -> None:
            await ws.send(json.dumps(event))

        async for raw in ws:
            message = json.loads(raw)
            self.received.append(message)
            match message["command"]:
                case "get_auth_state":
                    await emit(
                        {
                            "type": "auth_state",
                            "logged_in": True,
                            "is_active": False,
                            "device_name": "Huxley",
                        }
                    )
                case "play":
                    await emit({"type": "playback_changed", "status": "playing"})
                    await emit({"type": "context_changed", "context": {"uri": message.get("uri")}})
                    await emit({"type": "track_changed", "item": SIN_TI})
                    await emit({"type": "playback_changed", "status": "buffering"})
                    await emit(
                        {"type": "device_changed", "is_active": True, "device_name": "Huxley"}
                    )
                    await emit({"type": "playback_changed", "status": "playing"})
                case "pause":
                    await emit({"type": "playback_changed", "status": "paused"})

    def commands(self) -> list[str]:
        return [m["command"] for m in self.received]


class TestAgainstRealClient:
    async def test_play_and_cancel_through_the_real_client(
        self, tmp_path: Path, pcm_server: PcmServer
    ) -> None:
        pcm_server.hold_open.set()
        fake = FakeSoloistServer()
        async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
            port = next(iter(server.sockets)).getsockname()[1]
            client = SoloistClient(f"ws://127.0.0.1:{port}", make_ctx(tmp_path, Recorded()).logger)
            pump = asyncio.create_task(client.run())
            try:
                assert await client.wait_until(lambda s: s.logged_in, 2.0)
                skill, _, recorded = await _skill(tmp_path, pcm_server, client=client)

                task, got = await _consume_until(await _play(skill, "Sin ti"), 5)
                await _cancel(task)
                assert await client.wait_until(lambda s: s.status == "paused", 2.0)

                assert len(got) >= 5
                assert recorded.injected == []  # no false takeover, no failure
                assert fake.commands()[-3:] == ["play", "set_volume", "pause"]
                assert next(m for m in fake.received if m["command"] == "play")["uri"] == (
                    "spotify:artist:panchos"
                )
                assert (await skill.handle("whats_playing", {})).output == json.dumps(
                    {"ok": True, "title": "Sin Ti", "by": "Los Panchos"}, ensure_ascii=False
                )
            finally:
                await _cancel(pump)


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
