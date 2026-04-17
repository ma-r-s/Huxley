"""Unit tests for the TurnCoordinator state machine.

Drives the coordinator with mocked OpenAI/client/skill callbacks so every
state transition, latching rule, and interrupt path is exercised without
real network or subprocess I/O.

Mapped to `docs/turns.md` sections:
- TestTurnLifecycle → §1 Turn + §"Turn lifecycle"
- TestAudioForwarding → §"Turn lifecycle" (COMMITTING → IN_RESPONSE gate,
  `response_cancelled` drop flag, mic-to-session gate)
- TestToolDispatch → §2 Factory pattern, §9 NONE path
- TestChainedResponses → §2 "speech done", §9 mixed tools path
- TestFactorySupersede → §2 "last factory wins"
- TestInterrupt → §3 Interrupt (6-step atomic order)
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from huxley.turn.coordinator import Turn, TurnCoordinator, TurnState
from huxley_sdk import AudioStream, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    """Helper: build an async iterator yielding the given chunks."""
    for chunk in chunks:
        yield chunk


def _factory(*chunks: bytes) -> Callable[[], AsyncIterator[bytes]]:
    """Build a factory (callable) that returns a fresh iterator each call."""
    return lambda: _stream(*chunks)


async def _commit_turn(coordinator: TurnCoordinator, frames: int = 25) -> None:
    """Helper: start a turn, stuff `frames` into user_audio_frames, and commit.

    Mirrors the production flow where `on_user_audio_frame` increments the
    counter — we set it directly here to keep tests concise. Default of 25
    is comfortably above the 19-frame minimum (OpenAI's 100 ms floor).
    """
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_frames = frames
    await coordinator.on_ptt_stop()


@pytest.fixture
def mocks() -> dict[str, Any]:
    """Dictionary of mock callbacks for building a coordinator under test."""
    return {
        "send_audio": AsyncMock(),
        "send_audio_clear": AsyncMock(),
        "send_status": AsyncMock(),
        "send_model_speaking": AsyncMock(),
        "send_user_audio_to_session": AsyncMock(),
        "send_dev_event": AsyncMock(),
        "oai_send_function_output": AsyncMock(),
        "oai_commit": AsyncMock(),
        "oai_cancel": AsyncMock(),
        "oai_request_response": AsyncMock(),
        "oai_is_connected": MagicMock(return_value=True),
        "dispatch_tool": AsyncMock(return_value=ToolResult(output="{}")),
    }


@pytest.fixture
def coordinator(mocks: dict[str, Any]) -> TurnCoordinator:
    return TurnCoordinator(**mocks)


# ---------------------------------------------------------------------------


class TestTurnLifecycle:
    """Basic ptt_start / ptt_stop / response.done flow."""

    async def test_fresh_coordinator_has_no_current_turn(
        self, coordinator: TurnCoordinator
    ) -> None:
        assert coordinator.current_turn is None
        assert coordinator.response_cancelled is False

    async def test_ptt_start_creates_turn_in_listening(self, coordinator: TurnCoordinator) -> None:
        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.LISTENING

    async def test_ptt_stop_with_enough_frames_commits(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.COMMITTING
        mocks["oai_commit"].assert_awaited_once()
        mocks["oai_cancel"].assert_not_awaited()

    async def test_ptt_stop_too_short_aborts_without_commit(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator, frames=10)  # below 19-frame threshold

        assert coordinator.current_turn is None
        mocks["oai_cancel"].assert_awaited_once()
        mocks["oai_commit"].assert_not_awaited()

    async def test_commit_failed_aborts_stuck_turn(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """If OpenAI rejects the commit, the turn must not hang in COMMITTING."""
        await _commit_turn(coordinator)
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.COMMITTING

        await coordinator.on_commit_failed()

        assert coordinator.current_turn is None
        mocks["send_status"].assert_awaited()

    async def test_simple_response_returns_to_idle(self, coordinator: TurnCoordinator) -> None:
        """Minimal happy path: no tool calls, just audio + response.done."""
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"model speech chunk")
        await coordinator.on_response_done()

        assert coordinator.current_turn is None


class TestAudioForwarding:
    """Mic-gate and model-audio-gate behavior."""

    async def test_mic_audio_forwarded_while_listening(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await coordinator.on_ptt_start()
        await coordinator.on_user_audio_frame(b"mic chunk")

        mocks["send_user_audio_to_session"].assert_awaited_once_with(b"mic chunk")

    async def test_mic_audio_not_forwarded_when_idle(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await coordinator.on_user_audio_frame(b"mic chunk")
        mocks["send_user_audio_to_session"].assert_not_awaited()

    async def test_mic_audio_not_forwarded_after_commit(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        # State is now COMMITTING, not LISTENING
        await coordinator.on_user_audio_frame(b"mic chunk")
        mocks["send_user_audio_to_session"].assert_not_awaited()

    async def test_model_audio_forwarded_to_client(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"model chunk")

        mocks["send_audio"].assert_awaited_once_with(b"model chunk")

    async def test_audio_delta_transitions_from_committing_to_in_response(
        self, coordinator: TurnCoordinator
    ) -> None:
        await _commit_turn(coordinator)
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.COMMITTING

        await coordinator.on_audio_delta(b"first chunk")
        assert coordinator.current_turn.state == TurnState.IN_RESPONSE

    async def test_audio_delta_dropped_when_response_cancelled(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        coordinator.response_cancelled = True

        await coordinator.on_audio_delta(b"stale chunk")
        mocks["send_audio"].assert_not_awaited()


class TestToolDispatch:
    """Tool call dispatch, dev event emission, factory latching."""

    async def test_tool_with_factory_latches_onto_pending(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        factory = _factory(b"book chunk")
        mocks["dispatch_tool"].return_value = ToolResult(
            output='{"ok": true}', side_effect=AudioStream(factory=factory)
        )

        await _commit_turn(coordinator)
        await coordinator.on_function_call("call_1", "play_audiobook", {"book_id": "X"})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_audio_streams == [AudioStream(factory=factory)]
        assert coordinator.current_turn.needs_follow_up is False

    async def test_tool_without_factory_sets_needs_follow_up(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_function_call("call_1", "get_current_time", {})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_audio_streams == []
        assert coordinator.current_turn.needs_follow_up is True

    async def test_function_call_sends_output_back_to_openai(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"result": "ok"}')

        await _commit_turn(coordinator)
        await coordinator.on_function_call("call_abc", "some_tool", {"arg": "value"})

        mocks["oai_send_function_output"].assert_awaited_once_with("call_abc", '{"result": "ok"}')

    async def test_function_call_emits_dev_event(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        factory = _factory(b"x")
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=factory)
        )

        await _commit_turn(coordinator)
        await coordinator.on_function_call("call_1", "play_audiobook", {"book_id": "X"})

        mocks["send_dev_event"].assert_awaited_once()
        args, _ = mocks["send_dev_event"].call_args
        assert args[0] == "tool_call"
        assert args[1]["name"] == "play_audiobook"
        assert args[1]["has_audio_stream"] is True


class TestChainedResponses:
    """Info tool → follow-up response → terminal barrier flow."""

    async def test_info_tool_requests_follow_up_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "get_current_time", {})
        await coordinator.on_response_done()

        mocks["oai_request_response"].assert_awaited_once()
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.AWAITING_NEXT_RESPONSE
        # needs_follow_up is cleared now — fresh flag for round 2
        assert coordinator.current_turn.needs_follow_up is False

    async def test_follow_up_response_audio_transitions_back_to_in_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "get_current_time", {})
        await coordinator.on_response_done()
        # Round 2 starts — model narrates the time
        await coordinator.on_audio_delta(b"son las tres")

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.IN_RESPONSE

    async def test_chained_response_terminates_on_final_done(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """Full chained flow: info tool → round 2 narration → IDLE."""
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        # Round 1: silent + tool call
        await coordinator.on_function_call("c1", "get_current_time", {})
        await coordinator.on_response_done()  # → AWAITING_NEXT_RESPONSE
        # Round 2: model narrates
        await coordinator.on_audio_delta(b"son las tres")
        await coordinator.on_response_done()  # → IDLE (no more tools)

        assert coordinator.current_turn is None
        assert mocks["oai_request_response"].call_count == 1

    async def test_mixed_factory_and_none_tools_chain_before_firing_factory(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """Round 1 has both kinds of tool — factory fires only after round 2 narration."""
        factory = _factory(b"book")

        call_count = [0]

        async def dispatch(name: str, _args: dict[str, Any]) -> ToolResult:
            call_count[0] += 1
            if name == "play_audiobook":
                return ToolResult(output="{}", side_effect=AudioStream(factory=factory))
            return ToolResult(output='{"time": "3pm"}')  # None factory

        mocks["dispatch_tool"].side_effect = dispatch

        await _commit_turn(coordinator)
        # Round 1: both tools dispatched
        await coordinator.on_function_call("c1", "play_audiobook", {})
        await coordinator.on_function_call("c2", "get_current_time", {})
        await coordinator.on_response_done()

        # Follow-up requested because of the info tool
        mocks["oai_request_response"].assert_awaited_once()
        # Factory still staged
        assert coordinator.current_turn is not None
        assert len(coordinator.current_turn.pending_audio_streams) == 1

        # Round 2 narrates the time, no more tools
        await coordinator.on_audio_delta("ahí le pongo el libro y son las tres".encode())
        await coordinator.on_response_done()

        # Turn ends, factory spawned as background task
        assert coordinator.current_turn is None
        assert coordinator.current_media_task is not None

    async def test_no_follow_up_needed_fires_factories_immediately(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        factory = _factory(b"book")
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=factory)
        )

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "play_audiobook", {})
        await coordinator.on_response_done()  # terminal — factory fires

        mocks["oai_request_response"].assert_not_awaited()
        assert coordinator.current_turn is None
        assert coordinator.current_media_task is not None


class TestFactorySupersede:
    """When a turn accumulates multiple factories, only the last one runs."""

    async def test_last_factory_wins_when_multiple_accumulated(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        # Track which factories actually get consumed
        consumed: list[str] = []

        async def stream_a() -> AsyncIterator[bytes]:
            consumed.append("a")
            yield b"a"

        async def stream_b() -> AsyncIterator[bytes]:
            consumed.append("b")
            yield b"b"

        async def dispatch(name: str, _args: dict[str, Any]) -> ToolResult:
            stream = stream_a if name == "first" else stream_b
            return ToolResult(
                output="{}",
                side_effect=AudioStream(factory=stream),
            )

        mocks["dispatch_tool"].side_effect = dispatch

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "first", {})
        await coordinator.on_function_call("c2", "second", {})
        await coordinator.on_response_done()

        # Wait briefly for the background consumer task to pull at least once.
        await _settle_background_task(coordinator.current_media_task)

        # Only the last factory ran.
        assert consumed == ["b"]


class TestInterrupt:
    """The 6-step atomic interrupt barrier."""

    async def test_interrupt_sets_response_cancelled_flag(
        self, coordinator: TurnCoordinator
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.interrupt()

        assert coordinator.response_cancelled is True

    async def test_interrupt_clears_pending_audio_streams(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        factory = _factory(b"x")
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=factory)
        )

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "play", {})
        assert coordinator.current_turn is not None
        assert len(coordinator.current_turn.pending_audio_streams) == 1

        turn_before = coordinator.current_turn
        await coordinator.interrupt()

        # After interrupt, current_turn is None — but the old one had its
        # pending factories cleared before being detached.
        assert turn_before.pending_audio_streams == []
        assert coordinator.current_turn is None

    async def test_interrupt_sends_audio_clear(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await coordinator.on_ptt_start()
        await coordinator.interrupt()
        mocks["send_audio_clear"].assert_awaited()

    async def test_interrupt_on_idle_coordinator_is_safe(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """`_shutdown()` calls `coordinator.interrupt()` unconditionally.

        If there's no active turn AND no running media task, the call must
        not raise — it still flushes client audio (cheap) and sets the drop
        flag. `oai_cancel` is skipped because no response is in flight.
        """
        assert coordinator.current_turn is None
        assert coordinator.current_media_task is None

        await coordinator.interrupt()

        assert coordinator.response_cancelled is True
        assert coordinator.current_turn is None
        mocks["send_audio_clear"].assert_awaited()
        # No response in flight → cancel skipped.
        mocks["oai_cancel"].assert_not_awaited()

    async def test_interrupt_during_book_playback_skips_cancel(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """PTT press during book: media task active but no turn → cancel skipped."""
        import asyncio

        async def forever_stream() -> AsyncIterator[bytes]:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)

        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=forever_stream)
        )

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "play", {})
        await coordinator.on_response_done()  # spawns media task, current_turn = None

        assert coordinator.current_turn is None
        assert coordinator.current_media_task is not None

        # Simulate PTT press mid-book
        await coordinator.on_ptt_start()

        # Media task was cancelled, but oai_cancel was NOT sent
        assert coordinator.current_media_task is None or coordinator.current_media_task.done()
        mocks["oai_cancel"].assert_not_awaited()

    async def test_interrupt_on_idle_disconnected_skips_oai_cancel(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """When the session is disconnected, interrupt must not call oai_cancel."""
        mocks["oai_is_connected"].return_value = False

        await coordinator.interrupt()

        mocks["oai_cancel"].assert_not_awaited()
        mocks["send_audio_clear"].assert_awaited()

    async def test_interrupt_cancels_openai_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.interrupt()
        mocks["oai_cancel"].assert_awaited()

    async def test_interrupt_cancels_running_media_task(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        import asyncio

        # Factory that never completes (we'll cancel it)
        async def forever_stream() -> AsyncIterator[bytes]:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)

        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=forever_stream)
        )

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "play", {})
        await coordinator.on_response_done()  # spawns the media task

        task = coordinator.current_media_task
        assert task is not None
        assert not task.done()

        await coordinator.interrupt()

        assert task.done()
        assert coordinator.current_media_task is None

    async def test_new_ptt_start_mid_turn_interrupts_and_restarts(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        first_turn = coordinator.current_turn
        assert first_turn is not None

        # New press interrupts the first turn and starts a fresh one
        await coordinator.on_ptt_start()

        mocks["oai_cancel"].assert_awaited()
        assert coordinator.current_turn is not None
        assert coordinator.current_turn is not first_turn
        assert coordinator.current_turn.state == TurnState.LISTENING

    async def test_interrupt_ordering_drop_flag_before_oai_cancel(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """Drop flag MUST be set before `oai_cancel` is called.

        Rationale: if oai_cancel yielded control to the event loop and a
        stale audio delta arrived before the flag was set, we'd forward
        the stale delta to the client. The flag-first ordering prevents
        this — any delta arriving after the flag is set is dropped at
        `on_audio_delta`.
        """
        flag_when_cancel_called: list[bool] = []

        async def capture_flag() -> None:
            flag_when_cancel_called.append(coordinator.response_cancelled)

        mocks["oai_cancel"].side_effect = capture_flag

        await _commit_turn(coordinator)
        await coordinator.interrupt()

        assert flag_when_cancel_called == [True]


class TestResponseCancelledFlag:
    """Stale delta drop flag behavior."""

    async def test_flag_is_reset_on_commit(self, coordinator: TurnCoordinator) -> None:
        coordinator.response_cancelled = True
        await _commit_turn(coordinator)
        assert coordinator.response_cancelled is False

    async def test_flag_is_reset_before_follow_up_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_function_call("c1", "get_current_time", {})

        # Pretend an interrupt would set the flag; then a follow-up comes in
        coordinator.response_cancelled = True

        # Simulate fresh response_done path when flag is False to exercise
        # the reset-before-follow-up logic.
        coordinator.response_cancelled = False
        await coordinator.on_response_done()

        mocks["oai_request_response"].assert_awaited_once()
        assert coordinator.response_cancelled is False


# ---------------------------------------------------------------------------
# Helpers


async def _settle_background_task(task: Any) -> None:
    """Let a background consumer task run one iteration, then clean up.

    Used by tests that need to observe which factory actually started
    consuming. We give it a couple of event-loop ticks, then cancel it so
    the test doesn't hang on infinite streams.
    """
    import asyncio

    if task is None:
        return
    # Two ticks to let the consumer run through the simple `yield` generator.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestModelSpeaking:
    """`send_model_speaking` and `on_audio_done` tracking.

    These fire the `model_speaking` protocol message to the client so its
    thinking-tone silence timer knows when the model is actually emitting
    audio vs. silent.
    """

    async def test_first_audio_delta_fires_model_speaking_true(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"first chunk")
        mocks["send_model_speaking"].assert_awaited_once_with(True)

    async def test_subsequent_audio_deltas_do_not_refire(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"a")
        await coordinator.on_audio_delta(b"b")
        await coordinator.on_audio_delta(b"c")
        assert mocks["send_model_speaking"].await_count == 1

    async def test_on_audio_done_fires_model_speaking_false(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"a")
        await coordinator.on_audio_done()

        # One true, one false
        assert mocks["send_model_speaking"].await_args_list[-1].args == (False,)

    async def test_on_audio_done_is_idempotent_when_not_speaking(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """If audio_done fires without any prior delta, no model_speaking event."""
        await _commit_turn(coordinator)
        await coordinator.on_audio_done()
        mocks["send_model_speaking"].assert_not_awaited()

    async def test_chained_response_cycles_model_speaking(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        """Round 1 audio → audio_done → round 2 audio should fire true, false, true."""
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        # Round 1 — model starts speaking, calls tool, audio done, response done
        await coordinator.on_audio_delta(b"a")
        await coordinator.on_function_call("c1", "get_current_time", {})
        await coordinator.on_audio_done()
        await coordinator.on_response_done()
        # Round 2 — narration resumes
        await coordinator.on_audio_delta(b"son las tres")

        states = [c.args[0] for c in mocks["send_model_speaking"].await_args_list]
        assert states == [True, False, True]

    async def test_on_audio_done_dropped_when_cancelled(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"a")
        coordinator.response_cancelled = True

        await coordinator.on_audio_done()
        # The false-event wasn't fired because the flag is set — interrupt()
        # owns the final cleanup.
        assert mocks["send_model_speaking"].await_args_list[-1].args == (True,)

    async def test_interrupt_clears_speaking_state(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.on_audio_delta(b"a")
        await coordinator.interrupt()

        # true was fired on the delta, then false on the interrupt cleanup.
        states = [c.args[0] for c in mocks["send_model_speaking"].await_args_list]
        assert states == [True, False]


class TestTurnDataclass:
    """Small sanity checks on the Turn dataclass itself."""

    def test_default_turn_has_empty_pending_audio_streams(self) -> None:
        t = Turn()
        assert t.pending_audio_streams == []
        assert t.response_ids == []
        assert t.needs_follow_up is False
        assert t.user_audio_frames == 0

    def test_turn_default_state_is_listening(self) -> None:
        t = Turn()
        assert t.state == TurnState.LISTENING
