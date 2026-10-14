"""Where the loudness stages sit (T1.47): the voice gain on model audio
only, the wire limiter on everything sent, the persona's `audio:` block,
and the shipped sounds at their targets."""

from __future__ import annotations

import base64
import json
import math
import struct
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import structlog

from huxley.config import Settings
from huxley.focus.manager import FocusManager
from huxley.loudness import (
    CUE_TARGET_LUFS,
    PROGRAM_TARGET_LUFS,
    TRUE_PEAK_CEILING_DBFS,
    WIRE_CEILING_DBFS,
    db_to_gain,
    integrated_lufs,
    momentary_max_lufs,
    pcm_samples,
    sample_peak_dbfs,
    true_peak_dbfs,
)
from huxley.persona import PersonaSpec, load_persona
from huxley.reconnect import no_signal_tone_pcm
from huxley.runtime import Runtime
from huxley.server.server import AudioServer, _ConnInfo
from huxley.turn.coordinator import TurnCoordinator
from huxley.turn.speaking_state import SpeakingOwner
from huxley.turn.wait_cue import BLIP_TARGET_LUFS, render_blip
from huxley.voice.openai_realtime import OUTPUT_GAIN_DB
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import AudioStream, PlaySound, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from huxley.app import Application

REPO = Path(__file__).resolve().parents[4]
PERSONAS = REPO / "server" / "personas"
CEILING = int(32767 * db_to_gain(WIRE_CEILING_DBFS))


def _tone(amplitude: int, samples: int = 4800) -> bytes:
    return struct.pack(
        f"<{samples}h",
        *(int(amplitude * math.sin(2 * math.pi * 997 * i / 24000)) for i in range(samples)),
    )


def _peak(pcm: bytes) -> int:
    s = pcm_samples(pcm)
    return max(max(s), -min(s))


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


def _coordinator(fm: FocusManager, **kwargs: Any) -> tuple[TurnCoordinator, AsyncMock]:
    provider = StubVoiceProvider()
    provider._connected = True
    send_audio = AsyncMock()
    coord = TurnCoordinator(
        send_audio=send_audio,
        send_audio_clear=AsyncMock(),
        send_status=AsyncMock(),
        send_model_speaking=AsyncMock(),
        send_dev_event=AsyncMock(),
        provider=provider,
        dispatch_tool=AsyncMock(return_value=ToolResult(output="{}")),
        focus_manager=fm,
        **kwargs,
    )
    return coord, send_audio


async def _commit(coord: TurnCoordinator) -> None:
    await coord.on_ptt_start()
    assert coord.current_turn is not None
    coord.current_turn.user_audio_bytes = 300 * 48
    await coord.on_ptt_stop()


class TestVoiceGain:
    async def test_off_by_default_bit_exact(self, focus_manager: FocusManager) -> None:
        coord, send_audio = _coordinator(focus_manager)
        await _commit(coord)
        pcm = _tone(20000)
        await coord.on_audio_delta(pcm)
        assert send_audio.await_args_list[-1].args == (pcm,)

    async def test_model_audio_gets_the_gain(self, focus_manager: FocusManager) -> None:
        coord, send_audio = _coordinator(focus_manager, voice_gain_db=6.0)
        await _commit(coord)
        pcm = _tone(4000)
        await coord.on_audio_delta(pcm)
        out = send_audio.await_args_list[-1].args[0]
        assert integrated_lufs(out * 5) == pytest.approx(integrated_lufs(pcm * 5) + 6.0, abs=0.1)

    async def test_model_audio_never_goes_over_the_ceiling(
        self, focus_manager: FocusManager
    ) -> None:
        coord, send_audio = _coordinator(focus_manager, voice_gain_db=7.0)
        await _commit(coord)
        with structlog.testing.capture_logs() as logs:
            for _ in range(5):
                await coord.on_audio_delta(_tone(20000))
            await coord.on_audio_done()
        assert all(_peak(c.args[0]) <= CEILING for c in send_audio.await_args_list)
        done = next(e for e in logs if e["event"] == "coord.audio_done")
        assert done["voice_limited_ms"] > 0
        assert done["voice_cut_db"] > 0

    async def test_each_response_starts_with_a_clean_limiter(
        self, focus_manager: FocusManager
    ) -> None:
        # A completion turn keeps the speaker flag through the hand-over:
        # the cut from the last response must not carry into the next.
        coord, send_audio = _coordinator(focus_manager, voice_gain_db=6.0)
        await _commit(coord)
        await coord.on_audio_delta(_tone(30000))
        await coord.on_audio_done()
        await coord._speaking_state.acquire(SpeakingOwner.FACTORY)
        quiet = _tone(3000)
        await coord.on_audio_delta(quiet)
        g = db_to_gain(6.0)
        expected = [int(x * g) for x in pcm_samples(quiet)]
        assert list(pcm_samples(send_audio.await_args_list[-1].args[0])) == expected

    async def test_a_skill_sound_is_not_gained(self, focus_manager: FocusManager) -> None:
        chime = _tone(3000)
        coord, send_audio = _coordinator(focus_manager, voice_gain_db=7.0)
        coord._dispatch_tool = AsyncMock(
            return_value=ToolResult(output="{}", side_effect=PlaySound(pcm=chime))
        )
        await _commit(coord)
        await coord.on_tool_call("c1", "get_news", {})
        await coord.on_response_done()
        assert send_audio.await_args_list[-1].args == (chime,)

    async def test_a_stream_is_not_gained(self, focus_manager: FocusManager) -> None:
        chunk = _tone(3000)

        async def factory() -> AsyncIterator[bytes]:
            yield chunk

        coord, send_audio = _coordinator(focus_manager, voice_gain_db=7.0)
        coord._dispatch_tool = AsyncMock(
            return_value=ToolResult(output="{}", side_effect=AudioStream(factory=factory))
        )
        await _commit(coord)
        await coord.on_tool_call("c1", "play", {})
        await coord.on_response_done()
        obs = coord._content_obs
        assert obs is not None and obs.task is not None
        await obs.task
        assert (chunk,) in [c.args for c in send_audio.await_args_list]


class _WS:
    def __init__(self) -> None:
        self.sent: list[str] = []

        class _T:
            def get_write_buffer_size(self) -> int:
                return 0

            def get_extra_info(self, name: str) -> Any:
                return None

        self.transport = _T()

    async def send(self, msg: str) -> None:
        self.sent.append(msg)


def _server() -> tuple[AudioServer, _WS]:
    ws = _WS()
    srv = AudioServer(
        host="127.0.0.1",
        port=0,
        on_wake_word=AsyncMock(),
        on_ptt_start=AsyncMock(),
        on_ptt_stop=AsyncMock(),
        on_audio_frame=AsyncMock(),
        on_reset=AsyncMock(),
    )
    srv._conn = _ConnInfo(
        conn_id="c1",
        remote="test",
        connected_at_ms=0,
        requested_persona=None,
        requested_language=None,
        ws=ws,  # type: ignore[arg-type]
    )
    return srv, ws


def _received(ws: _WS) -> bytes:
    return b"".join(base64.b64decode(json.loads(m)["data"]) for m in ws.sent)


class TestWireLimiter:
    async def test_quiet_audio_goes_out_byte_for_byte(self) -> None:
        srv, ws = _server()
        pcm = _tone(10000)
        await srv.send_audio(pcm)
        assert _received(ws) == pcm

    async def test_anything_too_loud_is_limited(self) -> None:
        srv, ws = _server()
        await srv.send_audio(_tone(32000), stats=False)
        assert _peak(_received(ws)) <= CEILING

    async def test_a_clear_ends_the_cut(self) -> None:
        srv, ws = _server()
        await srv.send_audio(_tone(32000))
        await srv.send_audio_clear()
        ws.sent.clear()
        quiet = _tone(10000)
        await srv.send_audio(quiet)
        assert _received(ws) == quiet


class TestPersonaAudioBlock:
    def _spec(self, tmp_path: Path, **audio: Any) -> PersonaSpec:
        return PersonaSpec.model_validate(
            {
                "name": "T",
                "voice": "coral",
                "language_code": "en",
                "transcription_language": "en",
                "timezone": "UTC",
                "system_prompt": "x",
                "data_dir": tmp_path,
                **({"audio": audio} if audio else {}),
            }
        )

    def test_default_is_the_provider_calibration(self, tmp_path: Path) -> None:
        assert self._spec(tmp_path).audio.voice_gain_db is None
        assert pytest.approx(7.0) == OUTPUT_GAIN_DB

    def test_override_and_bounds(self, tmp_path: Path) -> None:
        assert self._spec(tmp_path, voice_gain_db=0).audio.voice_gain_db == 0
        with pytest.raises(ValueError):
            self._spec(tmp_path, voice_gain_db=40)
        with pytest.raises(ValueError):
            self._spec(tmp_path, voice_gain=3)

    def test_abuelos_sets_it(self) -> None:
        assert load_persona(PERSONAS / "abuelos").audio.voice_gain_db == pytest.approx(7.0)


def _wav(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        return w.readframes(w.getnframes())


class TestShippedSoundsAreAtTheirTargets:
    @pytest.mark.parametrize(
        "path", sorted((PERSONAS / "_shared" / "sounds").glob("*.wav")), ids=lambda p: p.stem
    )
    def test_earcon(self, path: Path) -> None:
        pcm = _wav(path)
        assert momentary_max_lufs(pcm) == pytest.approx(CUE_TARGET_LUFS, abs=0.5)
        assert true_peak_dbfs(pcm) <= TRUE_PEAK_CEILING_DBFS
        assert sample_peak_dbfs(pcm) <= WIRE_CEILING_DBFS

    def test_unavailable_clip(self) -> None:
        pcm = _wav(PERSONAS / "abuelos" / "sounds" / "unavailable.wav")
        assert integrated_lufs(pcm) == pytest.approx(PROGRAM_TARGET_LUFS, abs=0.5)
        assert true_peak_dbfs(pcm) <= TRUE_PEAK_CEILING_DBFS
        assert sample_peak_dbfs(pcm) <= WIRE_CEILING_DBFS

    def test_no_signal_tone(self) -> None:
        assert momentary_max_lufs(no_signal_tone_pcm()) == pytest.approx(CUE_TARGET_LUFS, abs=0.5)

    def test_waiting_blip(self) -> None:
        assert momentary_max_lufs(render_blip()) == pytest.approx(BLIP_TARGET_LUFS, abs=0.5)


_APP_PERSONA_YAML = """\
version: 1
name: p
voice: coral
language_code: es
transcription_language: es
timezone: America/Bogota
system_prompt: |
  Test persona with no skills.
constraints: []
skills: {}
"""


@pytest.fixture
async def start_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[[str], Coroutine[Any, Any, Application]]]:
    """Start persona `p` (IDLE, no session) with extra YAML; shut down after."""
    persona_dir = tmp_path / "personas" / "p"
    (persona_dir / "data").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    started: list[Application] = []

    async def _start(extra: str) -> Application:
        (persona_dir / "persona.yaml").write_text(_APP_PERSONA_YAML + extra, encoding="utf-8")
        runtime = Runtime(Settings(openai_api_key="test-key"))
        await runtime._switch_to_persona("p", auto_connect=False)
        assert runtime.current_app is not None
        started.append(runtime.current_app)
        return runtime.current_app

    yield _start
    for application in started:
        await application.shutdown()


class TestApplication:
    async def test_the_provider_calibration_by_default(
        self, start_app: Callable[[str], Coroutine[Any, Any, Application]]
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            application = await start_app("")
        assert application.coordinator._voice_limiter is not None
        assert application.coordinator._voice_gain == pytest.approx(db_to_gain(OUTPUT_GAIN_DB))
        setup = next(e for e in logs if e["event"] == "audio.gain_setup")
        assert setup["voice_gain_db"] == OUTPUT_GAIN_DB
        assert setup["voice_gain_from"] == "provider"

    async def test_the_persona_turns_it_off(
        self, start_app: Callable[[str], Coroutine[Any, Any, Application]]
    ) -> None:
        application = await start_app("audio:\n  voice_gain_db: 0\n")
        assert application.coordinator._voice_limiter is None

    async def test_learned_stream_gains_survive_a_restart(
        self, start_app: Callable[[str], Coroutine[Any, Any, Application]]
    ) -> None:
        first = await start_app("")
        first._level_memory.set("radio:x", -3.5)
        await first._level_memory.save("radio:x")
        await first.shutdown()
        with structlog.testing.capture_logs() as logs:
            second = await start_app("")
        assert second._level_memory.get("radio:x") == pytest.approx(-3.5)
        setup = next(e for e in logs if e["event"] == "audio.gain_setup")
        assert setup["stream_keys"] == 1

    async def test_a_save_after_shutdown_is_harmless(
        self, start_app: Callable[[str], Coroutine[Any, Any, Application]]
    ) -> None:
        application = await start_app("")
        await application.shutdown()
        application._level_memory.set("radio:x", -1.0)
        with structlog.testing.capture_logs() as logs:
            await application._level_memory.save("radio:x")
        assert any(e["event"] == "audio.level_save_failed" for e in logs)
