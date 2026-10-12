"""The coordinator's waiting sound (T1.41).

Mario, 2026-10-12: "it told me it was gonna look for some books and then
it kept silent for like 10 seconds... it feels like it's not doing
anything." While the user waits on a reply or a tool and the client is
quiet, a soft blip repeats; real sound, a press, or the turn's end stops it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.focus.manager import FocusManager
from huxley.turn import coordinator as coordinator_module
from huxley.turn.coordinator import TurnCoordinator
from huxley.turn.state import TurnSource
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import AudioStream, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


@pytest.fixture
def mocks() -> dict[str, Any]:
    provider = StubVoiceProvider()
    provider._connected = True
    return {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "send_cue_audio": AsyncMock(),
        "provider": provider,
        "dispatch_tool": AsyncMock(return_value=ToolResult(output="{}")),
    }


@pytest.fixture
def coordinator(
    mocks: dict[str, Any], focus_manager: FocusManager, monkeypatch: pytest.MonkeyPatch
) -> TurnCoordinator:
    monkeypatch.setattr(coordinator_module, "WAIT_REPLY_S", 0.05)
    monkeypatch.setattr(coordinator_module, "WAIT_TOOL_S", 0.03)
    coord = TurnCoordinator(**mocks, focus_manager=focus_manager)
    coord._wait_cue._period_s = 0.02
    return coord


async def _commit(coordinator: TurnCoordinator) -> None:
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_bytes = 300 * 48
    await coordinator.on_ptt_stop()


def _slow_tool(seconds: float, result: ToolResult | None = None) -> AsyncMock:
    async def dispatch(_name: str, _args: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(seconds)
        return result or ToolResult(output="{}")

    return AsyncMock(side_effect=dispatch)


async def test_a_long_tool_wait_gets_blips_and_the_answer_stops_them(
    coordinator: TurnCoordinator, mocks: dict[str, Any]
) -> None:
    await _commit(coordinator)
    await coordinator.on_audio_delta(b"\x00\x01" * 100)  # "un momento, lo busco"
    mocks["dispatch_tool"].side_effect = _slow_tool(0.25).side_effect
    await coordinator.on_tool_call("call_1", "find_books", {"query": "x"})
    blips = mocks["send_cue_audio"].await_count
    assert blips >= 2  # the 0.25 s search was not silent

    await coordinator.on_audio_delta(b"\x00\x01" * 100)  # the answer
    await asyncio.sleep(0.08)
    assert mocks["send_cue_audio"].await_count == blips  # stopped by real sound
    # The cue never went through the normal send (it stays out of stats).
    assert all(call.args[0] != b"" for call in mocks["send_audio"].await_args_list)


async def test_a_quick_answer_gets_no_blip(
    coordinator: TurnCoordinator, mocks: dict[str, Any]
) -> None:
    await _commit(coordinator)
    await asyncio.sleep(0.02)  # under the reply threshold
    await coordinator.on_audio_delta(b"\x00\x01" * 100)
    await asyncio.sleep(0.1)
    assert mocks["send_cue_audio"].await_count == 0


async def test_a_press_stops_the_cue_before_anything_else(
    coordinator: TurnCoordinator, mocks: dict[str, Any]
) -> None:
    await _commit(coordinator)
    await asyncio.sleep(0.12)  # waiting on the reply: blipping
    assert mocks["send_cue_audio"].await_count >= 1
    n = mocks["send_cue_audio"].await_count
    await coordinator.on_ptt_start()  # he presses again
    await asyncio.sleep(0.08)
    assert mocks["send_cue_audio"].await_count == n  # nothing into the open mic


async def test_injected_turns_never_arm_it(
    coordinator: TurnCoordinator, mocks: dict[str, Any]
) -> None:
    await _commit(coordinator)
    assert coordinator.current_turn is not None
    coordinator._wait_cue.disarm("test")
    coordinator.current_turn.source = TurnSource.INJECTED  # a reminder
    mocks["dispatch_tool"].side_effect = _slow_tool(0.15).side_effect
    await coordinator.on_tool_call("call_1", "acknowledge_reminder", {})
    assert mocks["send_cue_audio"].await_count == 0


async def test_a_tool_result_from_a_replaced_turn_is_dropped(
    coordinator: TurnCoordinator, mocks: dict[str, Any]
) -> None:
    """A press during a slow tool starts a new turn: the old result must
    not attach its stream to it (critic, 2026-10-12)."""
    stream = AudioStream(factory=lambda: _empty())
    mocks["dispatch_tool"].side_effect = _slow_tool(
        0.1, ToolResult(output='{"ok": true}', side_effect=stream)
    ).side_effect
    await _commit(coordinator)
    tool = asyncio.create_task(coordinator.on_tool_call("call_1", "play_book", {}))
    await asyncio.sleep(0.02)
    await coordinator.on_ptt_start()  # a new turn while the tool runs
    await tool
    assert coordinator.current_turn is not None
    assert coordinator.current_turn.pending_audio_streams == []


async def _empty() -> AsyncIterator[bytes]:
    return
    yield b""
