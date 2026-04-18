"""End-to-end coordinator tests driven by `StubVoiceProvider`.

These tests exercise the full turn lifecycle through the coordinator —
from `on_ptt_start` all the way to `on_response_done` and audio-stream
playback — without any real network. The stub provider lets us script
incoming events from the LLM and assert on outgoing verbs the
coordinator invoked against the provider.

This closes the "no automated test for the audio path" gap named in
`docs/review-notes.md` — the audio goes through `send_audio` callbacks
which are `AsyncMock`s, but every outgoing provider verb and every
state transition is exercised.

Scenario coverage mirrors `docs/verifying.md`:
1. Info-tool round-trip (`get_current_time` style)
2. Side-effect tool round-trip (`play_audiobook` style)
3. Mid-stream interrupt
4. Cross-turn state (simulate resume by re-dispatching an AudioStream)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.turn.coordinator import TurnCoordinator, TurnState
from huxley.voice.provider import VoiceProviderCallbacks
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import AudioStream, SkillRegistry, ToolResult
from huxley_sdk.testing import FakeSkill

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _async_iter(*chunks: bytes) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


@pytest.fixture
async def wired() -> tuple[TurnCoordinator, StubVoiceProvider, SkillRegistry, dict[str, Any]]:
    """Coordinator + stub provider + empty registry wired together.

    Mirrors the real `app.py` wiring: provider is created first, then the
    coordinator is created against it, then the provider's callbacks are
    installed to point at the coordinator's event handlers. `install_callbacks`
    exists on the stub for exactly this "build order matters" reason.
    """
    provider = StubVoiceProvider()
    await provider.connect()

    registry = SkillRegistry()
    mocks = {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_dev_event": AsyncMock(),
    }
    coordinator = TurnCoordinator(
        **mocks,
        provider=provider,
        dispatch_tool=registry.dispatch,
    )
    provider.install_callbacks(
        VoiceProviderCallbacks(
            on_audio_delta=coordinator.on_audio_delta,
            on_tool_call=coordinator.on_tool_call,
            on_response_done=coordinator.on_response_done,
            on_audio_done=coordinator.on_audio_done,
            on_commit_failed=coordinator.on_commit_failed,
            on_session_end=coordinator.on_session_disconnected,
        )
    )
    return coordinator, provider, registry, mocks


async def _commit_turn(coordinator: TurnCoordinator) -> None:
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_frames = 60
    await coordinator.on_ptt_stop()


# ---------------------------------------------------------------------------


class TestInfoToolRoundTrip:
    """Smoke 1 from verifying.md — `get_current_time` style info tool."""

    async def test_full_round_trip(
        self,
        wired: tuple[TurnCoordinator, StubVoiceProvider, SkillRegistry, dict[str, Any]],
    ) -> None:
        coordinator, provider, registry, mocks = wired

        # Register a fake "time" skill that returns an info-only result.
        time_skill = FakeSkill(
            name="system",
            result=ToolResult(output='{"time": "3:00 PM"}'),
        )
        # Override the tool to match `get_current_time`.
        time_skill._tools[0] = type(time_skill._tools[0])(  # ToolDefinition
            name="get_current_time",
            description="Current time",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(time_skill)

        # PTT → commit
        await _commit_turn(coordinator)
        assert ("commit_and_request_response",) in provider.sent
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.COMMITTING

        # Provider fires tool call → coordinator dispatches → provider
        # returns tool output → response_done with follow_up requested.
        await provider.emit_tool_call("call_1", "get_current_time", {})
        assert coordinator.current_turn.tool_calls == 1
        assert coordinator.current_turn.needs_follow_up is True
        tool_outputs = [c for c in provider.sent if c[0] == "send_tool_output"]
        assert tool_outputs == [("send_tool_output", "call_1", '{"time": "3:00 PM"}')]

        # Round 1 response done → follow-up response requested
        await provider.emit_response_done()
        assert coordinator.current_turn.state == TurnState.AWAITING_NEXT_RESPONSE
        assert ("request_response",) in provider.sent

        # Round 2: model narrates the tool output
        await provider.emit_audio_delta(b"son las tres")
        assert coordinator.current_turn.state == TurnState.IN_RESPONSE
        mocks["send_audio"].assert_awaited_with(b"son las tres")

        # Final response done → turn ends
        await provider.emit_audio_done()
        await provider.emit_response_done()
        assert coordinator.current_turn is None


class TestSideEffectToolRoundTrip:
    """Smoke 2 — side-effect tool returns an AudioStream that fires after speech."""

    async def test_full_round_trip_with_audio_stream(
        self,
        wired: tuple[TurnCoordinator, StubVoiceProvider, SkillRegistry, dict[str, Any]],
    ) -> None:
        coordinator, provider, registry, mocks = wired

        chunks_played: list[bytes] = []

        def factory() -> AsyncIterator[bytes]:
            return _async_iter(b"book_chunk_1", b"book_chunk_2", b"book_chunk_3")

        mocks["send_audio"].side_effect = lambda c: chunks_played.append(c)

        skill = FakeSkill(
            name="audiobooks",
            result=ToolResult(
                output='{"playing": true, "title": "Cien Años"}',
                side_effect=AudioStream(factory=factory),
            ),
        )
        # Swap in the right tool name.
        skill._tools[0] = type(skill._tools[0])(
            name="play_audiobook",
            description="Play",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(skill)

        # Turn flow
        await _commit_turn(coordinator)

        # Pre-narration ("Ahí le pongo el libro.")
        await provider.emit_audio_delta(b"ahi le pongo el libro")
        assert mocks["send_model_speaking"].await_args.args == (True,)

        # Tool call
        await provider.emit_tool_call("call_1", "play_audiobook", {"book_id": "X"})
        assert coordinator.current_turn is not None
        assert len(coordinator.current_turn.pending_audio_streams) == 1
        assert coordinator.current_turn.needs_follow_up is False  # side effect, no narration round

        # Terminal events → media task spawns and streams
        await provider.emit_audio_done()
        await provider.emit_response_done()

        # Turn ended; media task spawned. Drain it.
        assert coordinator.current_turn is None
        task = coordinator.current_media_task
        assert task is not None

        # Wait for the factory to drain
        await task

        # Every book chunk landed on send_audio (after the pre-narration chunk)
        assert b"book_chunk_1" in chunks_played
        assert b"book_chunk_2" in chunks_played
        assert b"book_chunk_3" in chunks_played


class TestMidStreamInterrupt:
    """Smoke 3 — PTT press mid-playback drops the stream cleanly."""

    async def test_interrupt_during_playback(
        self,
        wired: tuple[TurnCoordinator, StubVoiceProvider, SkillRegistry, dict[str, Any]],
    ) -> None:
        import asyncio

        coordinator, provider, registry, _mocks = wired

        # A stream that runs forever — we'll cancel it.
        async def forever() -> AsyncIterator[bytes]:
            while True:
                yield b"x"
                await asyncio.sleep(0.005)

        skill = FakeSkill(
            name="audiobooks",
            result=ToolResult(
                output='{"playing": true}',
                side_effect=AudioStream(factory=forever),
            ),
        )
        skill._tools[0] = type(skill._tools[0])(
            name="play_audiobook",
            description="Play",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(skill)

        # Get a stream running
        await _commit_turn(coordinator)
        await provider.emit_tool_call("call_1", "play_audiobook", {})
        await provider.emit_response_done()
        assert coordinator.current_media_task is not None

        # Let it run a couple of ticks
        for _ in range(5):
            await asyncio.sleep(0)

        # User interrupts — new PTT press mid-playback
        await coordinator.on_ptt_start()

        # Old media task cancelled
        assert coordinator.current_media_task is None
        # Drop flag set
        assert coordinator.response_cancelled is True
        # Fresh turn in LISTENING
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.LISTENING


class TestProviderIntegration:
    """Smoke 4 — verify the coordinator exercises the full VoiceProvider surface."""

    async def test_provider_verbs_all_invoked_across_scenarios(
        self,
        wired: tuple[TurnCoordinator, StubVoiceProvider, SkillRegistry, dict[str, Any]],
    ) -> None:
        """End of a session should have touched connect, commit, tool_output, and cancel."""
        coordinator, provider, registry, _mocks = wired

        skill = FakeSkill(
            name="system",
            result=ToolResult(output='{"time": "3pm"}'),
        )
        skill._tools[0] = type(skill._tools[0])(
            name="get_current_time",
            description="Time",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(skill)

        # Full turn
        await _commit_turn(coordinator)
        await provider.emit_tool_call("c1", "get_current_time", {})
        await provider.emit_response_done()

        # User interrupts
        await coordinator.interrupt()

        verbs = {call[0] for call in provider.sent}
        assert "connect" in verbs
        assert "commit_and_request_response" in verbs
        assert "send_tool_output" in verbs
        assert "request_response" in verbs
        assert "cancel_current_response" in verbs
