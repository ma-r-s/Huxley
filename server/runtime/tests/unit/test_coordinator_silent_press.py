"""Silent presses are not committed.

2026-10-12: presses with nobody talking (the walkie's own stats: peak
-56 dBFS after the button click) were committed; Whisper transcribed the
silence as "Subtitulos realizados por la comunidad de Amara.org" and the
model answered it (06:44-06:45, 07:30, and a bench test).
"""

from __future__ import annotations

import math
import sys
from array import array
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
import structlog

from huxley.focus.manager import FocusManager
from huxley.turn.coordinator import TurnCoordinator
from huxley.turn.press_level import SILENT_PEAK_DBFS, PressLevel
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

RATE = 24_000


def _pcm(samples: list[int]) -> bytes:
    frame = array("h", samples)
    if sys.byteorder == "big":
        frame.byteswap()
    return frame.tobytes()


def _tone(ms: int, amplitude: int, hz: int = 220) -> bytes:
    n = RATE * ms // 1000
    return _pcm([int(amplitude * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n)])


def _room_noise(ms: int, peak: int = 50) -> bytes:
    """Deterministic noise in [-peak, peak]: 50 is -56 dBFS, the walkie's
    level with nobody talking."""
    n = RATE * ms // 1000
    return _pcm([((i * 37) % (2 * peak + 1)) - peak for i in range(n)])


def _click(ms: int = 10) -> bytes:
    n = RATE * ms // 1000
    return _pcm([32767 if i % 2 else -32768 for i in range(n)])


def _frames(pcm: bytes, frame_ms: int = 20) -> list[bytes]:
    size = RATE * 2 * frame_ms // 1000
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]


# ------------------------------------------------------------ PressLevel


class TestPressLevel:
    def test_click_in_the_first_100_ms_is_not_measured(self) -> None:
        level = PressLevel()
        for frame in _frames(_click(10) + _room_noise(90) + _room_noise(400)):
            level.feed(frame)
        assert level.measured_ms == 340  # 500 ms minus the first 100 and last 60
        assert level.peak == 50
        assert level.peak_dbfs == pytest.approx(-56.3, abs=0.1)
        assert level.is_silent

    def test_release_click_in_the_last_60_ms_is_not_measured(self) -> None:
        """The walkie's release click lands just before the mic stops."""
        level = PressLevel()
        for frame in _frames(_room_noise(500) + _click(10)):
            level.feed(frame)
        assert level.peak == 50
        assert level.is_silent

    def test_speech_is_not_silent(self) -> None:
        level = PressLevel()
        for frame in _frames(_room_noise(100) + _tone(300, 16_000)):
            level.feed(frame)
        assert level.peak_dbfs == pytest.approx(-6.2, abs=0.2)
        assert level.rms_dbfs == pytest.approx(-9.2, abs=0.3)
        assert not level.is_silent

    def test_soft_speech_is_not_silent(self) -> None:
        level = PressLevel()
        level.feed(_room_noise(100) + _tone(300, 1_000) + _room_noise(60))  # -30 dBFS peak
        assert not level.is_silent

    def test_frame_straddling_the_skip_boundary(self) -> None:
        """One 150 ms chunk (the iOS app sends up to 100 ms): only its last
        50 ms count."""
        level = PressLevel()
        level.feed(_click(100) + _tone(50, 8_000) + _room_noise(60))
        assert level.measured_ms == 50
        assert level.peak < 32767  # the click was skipped

    def test_nothing_measured_is_not_silent(self) -> None:
        """No evidence, no drop: a 150 ms press is all click and tail."""
        level = PressLevel()
        level.feed(_room_noise(150))
        assert level.samples == 0
        assert not level.is_silent

    def test_odd_length_frame_does_not_raise(self) -> None:
        level = PressLevel(skip_bytes=0, tail_bytes=0)
        level.feed(b"\x10\x00\x20")
        assert level.samples == 1
        assert level.peak == 16

    def test_digital_silence_reports_the_floor(self) -> None:
        level = PressLevel(skip_bytes=0, tail_bytes=0)
        level.feed(b"\x00\x00" * 480)
        assert level.peak_dbfs == -96.0
        assert level.rms_dbfs == -96.0
        assert level.is_silent

    def test_threshold_is_conservative(self) -> None:
        assert SILENT_PEAK_DBFS == -40.0


# ----------------------------------------------------------- coordinator


@pytest.fixture
def provider() -> StubVoiceProvider:
    p = StubVoiceProvider()
    p._connected = True
    return p


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


@pytest.fixture
def mocks(provider: StubVoiceProvider) -> dict[str, Any]:
    return {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "provider": provider,
        "dispatch_tool": AsyncMock(return_value=ToolResult(output="{}")),
    }


@pytest.fixture
def coordinator(mocks: dict[str, Any], focus_manager: FocusManager) -> TurnCoordinator:
    return TurnCoordinator(**mocks, focus_manager=focus_manager)


async def _press(coordinator: TurnCoordinator, pcm: bytes) -> None:
    await coordinator.on_ptt_start()
    for frame in _frames(pcm):
        await coordinator.on_user_audio_frame(frame)
    await coordinator.on_ptt_stop()


class TestSilentPress:
    async def test_silent_press_is_not_committed(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider, mocks: dict[str, Any]
    ) -> None:
        """The walkie press: a click, then 500 ms of room noise at -56 dBFS."""
        with structlog.testing.capture_logs() as logs:
            await _press(coordinator, _click(20) + _room_noise(580))

        assert ("commit_and_request_response",) not in provider.sent
        # Handled like a too-short press: buffer cleared, client told.
        assert ("cancel_current_response",) in provider.sent
        mocks["send_audio_clear"].assert_awaited()
        mocks["send_status"].assert_awaited_with(coordinator._status["too_short"])
        assert coordinator.current_turn is None

        [silent] = [e for e in logs if e["event"] == "coord.press_silent"]
        assert silent["audio_ms"] == 600
        assert silent["measured_ms"] == 440
        assert silent["peak_dbfs"] == pytest.approx(-56.3, abs=0.1)
        assert silent["threshold_dbfs"] == SILENT_PEAK_DBFS
        assert "rms_dbfs" in silent
        [stop] = [e for e in logs if e["event"] == "coord.ptt_stop"]
        assert stop["committed"] is False
        assert stop["reason"] == "silent"

    async def test_speech_press_commits_and_logs_its_level(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            await _press(coordinator, _click(20) + _room_noise(80) + _tone(400, 12_000))

        assert ("commit_and_request_response",) in provider.sent
        assert not [e for e in logs if e["event"] == "coord.press_silent"]
        [stop] = [e for e in logs if e["event"] == "coord.ptt_stop"]
        assert stop["committed"] is True
        assert stop["peak_dbfs"] > -10

    async def test_short_quiet_yes_still_commits(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        """A quick, soft "si": 240 ms press, -30 dBFS between the clicks."""
        await _press(
            coordinator, _click(20) + _room_noise(80) + _tone(80, 1_000) + _room_noise(60)
        )
        assert ("commit_and_request_response",) in provider.sent

    async def test_press_too_short_to_measure_still_commits(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        """160 ms: all of it is click and tail, nothing to judge by."""
        await _press(coordinator, _room_noise(160))
        assert ("commit_and_request_response",) in provider.sent

    async def test_too_short_press_keeps_its_reason(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            await _press(coordinator, _room_noise(60))
        [stop] = [e for e in logs if e["event"] == "coord.ptt_stop"]
        assert stop["reason"] == "too_short"
        assert not [e for e in logs if e["event"] == "coord.press_silent"]
        assert ("commit_and_request_response",) not in provider.sent
