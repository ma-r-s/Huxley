"""The 2026-10-12 06:44 sequence through the real coordinator and provider.

Press 1 is being answered; press 2 interrupts and is released while
press 1's cancelled response is still live. Before the fix, that
response's late `response.done` ended press 2's turn (logged as
`coord.response_done state=committing` 0 ms after the commit), and the
next press's `response.create` was rejected by OpenAI. Press 2 must stay
open until its own response, which is requested once the cancelled one
is done.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.config import Settings
from huxley.focus.manager import FocusManager
from huxley.turn.coordinator import TurnCoordinator, TurnState
from huxley.voice.openai_realtime import OpenAIRealtimeProvider
from huxley.voice.provider import VoiceProviderCallbacks
from huxley_sdk import SkillRegistry, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from huxley.persona import PersonaSpec

# 20 ms at -6 dBFS: audible, so the silent-press gate lets it through.
_FRAME = b"\x00\x40" * 480


class _WS:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, msg: str) -> None:
        self.sent.append(json.loads(msg))


@pytest.fixture
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


def _event(kind: str, response_id: str, gen: int, **extra: Any) -> dict[str, Any]:
    if kind in ("response.created", "response.done"):
        response = {"id": response_id, "metadata": {"huxley_gen": str(gen)}, **extra}
        return {"type": kind, "response": response}
    return {"type": kind, "response_id": response_id, **extra}


async def test_late_done_of_a_cancelled_response_does_not_end_the_next_turn(
    persona: PersonaSpec, focus_manager: FocusManager
) -> None:
    coordinator: TurnCoordinator | None = None

    def forward(name: str) -> Any:
        async def call(*args: Any) -> None:
            assert coordinator is not None
            await getattr(coordinator, name)(*args)

        return call

    storage = AsyncMock()
    provider = OpenAIRealtimeProvider(
        config=Settings(openai_api_key="test-key"),
        persona=persona,
        skill_registry=SkillRegistry(),
        storage=storage,
        callbacks=VoiceProviderCallbacks(
            on_audio_delta=forward("on_audio_delta"),
            on_tool_call=forward("on_tool_call"),
            on_response_done=forward("on_response_done"),
            on_audio_done=forward("on_audio_done"),
            on_commit_failed=forward("on_commit_failed"),
            on_session_end=AsyncMock(),
        ),
    )
    ws = _WS()
    provider._ws = ws  # type: ignore[assignment]
    send_audio = AsyncMock()
    coordinator = TurnCoordinator(
        send_audio=send_audio,
        send_audio_clear=AsyncMock(),
        send_status=AsyncMock(),
        send_model_speaking=AsyncMock(),
        send_dev_event=AsyncMock(),
        provider=provider,
        dispatch_tool=AsyncMock(return_value=ToolResult(output="{}")),
        focus_manager=focus_manager,
    )

    async def press() -> None:
        assert coordinator is not None
        await coordinator.on_ptt_start()
        for _ in range(20):  # 400 ms
            await coordinator.on_user_audio_frame(_FRAME)
        await coordinator.on_ptt_stop()

    delta = {"delta": "AAAA"}

    await press()  # press 1
    await provider._handle_server_event(_event("response.created", "r1", 0))
    await provider._handle_server_event(_event("response.output_audio.delta", "r1", 0, **delta))
    assert coordinator.current_turn is not None
    assert coordinator.current_turn.state == TurnState.IN_RESPONSE

    await press()  # press 2: interrupts r1, released while r1 is still live
    turn2 = coordinator.current_turn
    assert turn2 is not None
    assert turn2.state == TurnState.COMMITTING
    assert ws.sent[-1]["type"] == "input_audio_buffer.commit"  # create held back

    # r1's late events after press 2's commit.
    await provider._handle_server_event(_event("response.output_audio.delta", "r1", 0, **delta))
    await provider._handle_server_event(_event("response.done", "r1", 0, status="cancelled"))
    assert coordinator.current_turn is turn2
    assert turn2.state == TurnState.COMMITTING  # not ended by r1's done
    assert ws.sent[-1] == {
        "type": "response.create",
        "response": {"metadata": {"huxley_gen": "1"}},
    }
    assert send_audio.await_count == 1  # r1's late delta never reached the client

    # Press 2's own answer.
    await provider._handle_server_event(_event("response.created", "r2", 1))
    await provider._handle_server_event(_event("response.output_audio.delta", "r2", 1, **delta))
    assert turn2.state == TurnState.IN_RESPONSE
    await provider._handle_server_event(_event("response.done", "r2", 1, status="completed"))
    assert coordinator.current_turn is None
    assert send_audio.await_count == 2
