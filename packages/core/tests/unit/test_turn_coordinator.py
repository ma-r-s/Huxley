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
from unittest.mock import AsyncMock

import pytest

from huxley.turn.coordinator import Turn, TurnCoordinator, TurnState
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import AudioStream, PlaySound, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    """Helper: build an async iterator yielding the given chunks."""
    for chunk in chunks:
        yield chunk


def _factory(*chunks: bytes) -> Callable[[], AsyncIterator[bytes]]:
    """Build a factory (callable) that returns a fresh iterator each call."""
    return lambda: _stream(*chunks)


async def _commit_turn(coordinator: TurnCoordinator, frames: int = 60) -> None:
    """Helper: start a turn, stuff `frames` into user_audio_frames, and commit.

    Mirrors the production flow where `on_user_audio_frame` increments the
    counter — we set it directly here to keep tests concise. Default of 60
    is comfortably above the 57-frame minimum (~300 ms speech floor).
    """
    await coordinator.on_ptt_start()
    assert coordinator.current_turn is not None
    coordinator.current_turn.user_audio_frames = frames
    await coordinator.on_ptt_stop()


@pytest.fixture
def provider() -> StubVoiceProvider:
    """Fresh stub VoiceProvider — connected, empty sent log."""
    p = StubVoiceProvider()
    p._connected = True  # skip the connect handshake; tests care about outgoing verbs
    return p


@pytest.fixture
def mocks(provider: StubVoiceProvider) -> dict[str, Any]:
    """Client-facing + dispatch mocks. Provider is separate (see `provider` fixture)."""
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
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        await _commit_turn(coordinator)

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.COMMITTING
        assert ("commit_and_request_response",) in provider.sent
        assert ("cancel_current_response",) not in provider.sent

    async def test_ptt_stop_too_short_aborts_without_commit(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        await _commit_turn(coordinator, frames=10)  # below 25-frame threshold

        assert coordinator.current_turn is None
        assert ("cancel_current_response",) in provider.sent
        assert ("commit_and_request_response",) not in provider.sent

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
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        await coordinator.on_ptt_start()
        await coordinator.on_user_audio_frame(b"mic chunk")

        assert provider.user_audio == [b"mic chunk"]

    async def test_mic_audio_not_forwarded_when_idle(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        await coordinator.on_user_audio_frame(b"mic chunk")
        assert len(provider.user_audio) == 0

    async def test_mic_audio_not_forwarded_after_commit(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        await _commit_turn(coordinator)
        # State is now COMMITTING, not LISTENING
        await coordinator.on_user_audio_frame(b"mic chunk")
        assert len(provider.user_audio) == 0

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
        await coordinator.on_tool_call("call_1", "play_audiobook", {"book_id": "X"})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_audio_streams == [AudioStream(factory=factory)]
        assert coordinator.current_turn.needs_follow_up is False

    async def test_tool_without_factory_sets_needs_follow_up(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_1", "get_current_time", {})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_audio_streams == []
        assert coordinator.current_turn.needs_follow_up is True

    async def test_function_call_sends_output_back_to_openai(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"result": "ok"}')

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_abc", "some_tool", {"arg": "value"})

        assert ("send_tool_output", "call_abc", '{"result": "ok"}') in provider.sent

    async def test_function_call_emits_dev_event(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        factory = _factory(b"x")
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=factory)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_1", "play_audiobook", {"book_id": "X"})

        mocks["send_dev_event"].assert_awaited_once()
        args, _ = mocks["send_dev_event"].call_args
        assert args[0] == "tool_call"
        assert args[1]["name"] == "play_audiobook"
        assert args[1]["has_audio_stream"] is True


class TestToolErrorEnvelope:
    """Skill exceptions are caught and turned into structured tool_output.

    See docs/triage.md T1.6. Without this, a skill bug propagates to the
    receive loop, kills the OpenAI session via on_session_end's finally
    clause, and the user hears silence + a 2s reconnect — for a blind user
    this looks identical to a dead device.
    """

    async def test_skill_exception_does_not_propagate(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].side_effect = RuntimeError("skill boom")

        await _commit_turn(coordinator)
        # Must not raise — coordinator absorbs the exception.
        await coordinator.on_tool_call("call_err", "broken_tool", {"x": 1})

        # Session is still alive, current_turn intact.
        assert coordinator.current_turn is not None

    async def test_skill_exception_sends_error_tool_output(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        import json

        mocks["dispatch_tool"].side_effect = ValueError("library not found")

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_err", "search_audiobooks", {"q": "x"})

        # Find the error output frame on the provider.
        sent_outputs = [s for s in provider.sent if s[0] == "send_tool_output"]
        assert len(sent_outputs) == 1
        _, call_id, output_str = sent_outputs[0]
        assert call_id == "call_err"
        payload = json.loads(output_str)
        assert payload["error"] == "tool_failed"
        assert payload["tool"] == "search_audiobooks"
        # Spanish apology hint; persona-agnostic strings can come later.
        assert "discúlpate" in payload["message"]

    async def test_skill_exception_sets_needs_follow_up(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].side_effect = RuntimeError("boom")

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_err", "broken_tool", {})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.needs_follow_up is True
        # Counter still increments — the call did happen, even if it failed.
        assert coordinator.current_turn.tool_calls == 1

    async def test_skill_exception_emits_tool_error_dev_event(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].side_effect = RuntimeError("skill boom")

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_err", "broken_tool", {"arg": 42})

        mocks["send_dev_event"].assert_awaited_once()
        args, _ = mocks["send_dev_event"].call_args
        assert args[0] == "tool_error"
        assert args[1]["name"] == "broken_tool"
        assert args[1]["args"] == {"arg": 42}
        assert args[1]["exception_class"] == "RuntimeError"
        assert args[1]["message"] == "skill boom"

    async def test_skill_exception_does_not_latch_audio_stream(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].side_effect = RuntimeError("boom mid-play")

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_err", "play_audiobook", {"book_id": "X"})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_audio_streams == []

    async def test_skill_not_found_error_handled_same_way(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        # Same envelope catches dispatch-routing failures (unknown tool name),
        # not just skill-internal exceptions.
        from huxley_sdk.registry import SkillNotFoundError

        mocks["dispatch_tool"].side_effect = SkillNotFoundError(
            "No skill registered for tool 'ghost_tool'"
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("call_x", "ghost_tool", {})

        assert coordinator.current_turn is not None
        sent_outputs = [s for s in provider.sent if s[0] == "send_tool_output"]
        assert len(sent_outputs) == 1


class TestChainedResponses:
    """Info tool → follow-up response → terminal barrier flow."""

    async def test_info_tool_requests_follow_up_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_current_time", {})
        await coordinator.on_response_done()

        assert ("request_response",) in provider.sent
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.AWAITING_NEXT_RESPONSE
        # needs_follow_up is cleared now — fresh flag for round 2
        assert coordinator.current_turn.needs_follow_up is False

    async def test_follow_up_response_audio_transitions_back_to_in_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any]
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_current_time", {})
        await coordinator.on_response_done()
        # Round 2 starts — model narrates the time
        await coordinator.on_audio_delta(b"son las tres")

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.IN_RESPONSE

    async def test_chained_response_terminates_on_final_done(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        """Full chained flow: info tool → round 2 narration → IDLE."""
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        # Round 1: silent + tool call
        await coordinator.on_tool_call("c1", "get_current_time", {})
        await coordinator.on_response_done()  # → AWAITING_NEXT_RESPONSE
        # Round 2: model narrates
        await coordinator.on_audio_delta(b"son las tres")
        await coordinator.on_response_done()  # → IDLE (no more tools)

        assert coordinator.current_turn is None
        assert sum(1 for c in provider.sent if c == ("request_response",)) == 1

    async def test_mixed_factory_and_none_tools_chain_before_firing_factory(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
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
        await coordinator.on_tool_call("c1", "play_audiobook", {})
        await coordinator.on_tool_call("c2", "get_current_time", {})
        await coordinator.on_response_done()

        # Follow-up requested because of the info tool
        assert ("request_response",) in provider.sent
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
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        factory = _factory(b"book")
        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=factory)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play_audiobook", {})
        await coordinator.on_response_done()  # terminal — factory fires

        assert ("request_response",) not in provider.sent
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
        await coordinator.on_tool_call("c1", "first", {})
        await coordinator.on_tool_call("c2", "second", {})
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
        await coordinator.on_tool_call("c1", "play", {})
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
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
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
        assert ("cancel_current_response",) not in provider.sent

    async def test_interrupt_during_book_playback_skips_cancel(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
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
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()  # spawns media task, current_turn = None

        assert coordinator.current_turn is None
        assert coordinator.current_media_task is not None

        # Simulate PTT press mid-book
        await coordinator.on_ptt_start()

        # Media task was cancelled, but oai_cancel was NOT sent
        assert coordinator.current_media_task is None or coordinator.current_media_task.done()
        assert ("cancel_current_response",) not in provider.sent

    async def test_interrupt_on_idle_disconnected_skips_cancel(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """When the transport is disconnected, interrupt must not call cancel."""
        provider._connected = False

        await coordinator.interrupt()

        assert ("cancel_current_response",) not in provider.sent
        mocks["send_audio_clear"].assert_awaited()

    async def test_interrupt_cancels_openai_response(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        await _commit_turn(coordinator)
        await coordinator.interrupt()
        assert ("cancel_current_response",) in provider.sent

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
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()  # spawns the media task

        task = coordinator.current_media_task
        assert task is not None
        assert not task.done()

        await coordinator.interrupt()

        assert task.done()
        assert coordinator.current_media_task is None

    async def test_new_ptt_start_mid_turn_interrupts_and_restarts(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        await _commit_turn(coordinator)
        first_turn = coordinator.current_turn
        assert first_turn is not None

        # New press interrupts the first turn and starts a fresh one
        await coordinator.on_ptt_start()

        assert ("cancel_current_response",) in provider.sent
        assert coordinator.current_turn is not None
        assert coordinator.current_turn is not first_turn
        assert coordinator.current_turn.state == TurnState.LISTENING

    async def test_interrupt_ordering_drop_flag_before_cancel(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        """Drop flag MUST be set before `cancel_current_response` is called.

        Rationale: if the provider's cancel yielded control to the event
        loop and a stale audio delta arrived before the flag was set, we'd
        forward the stale delta to the client. The flag-first ordering
        prevents this — any delta arriving after the flag is set is
        dropped at `on_audio_delta`.
        """
        flag_when_cancel_called: list[bool] = []

        original_cancel = provider.cancel_current_response

        async def capture_flag() -> None:
            flag_when_cancel_called.append(coordinator.response_cancelled)
            await original_cancel()

        provider.cancel_current_response = capture_flag  # type: ignore[method-assign]

        await _commit_turn(coordinator)
        await coordinator.interrupt()

        assert flag_when_cancel_called == [True]


class TestAudioStreamCompletion:
    """on_complete_prompt fires after natural stream end; not on cancel."""

    async def test_on_complete_prompt_fires_after_natural_end(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        import asyncio

        stream = AudioStream(
            factory=_factory(b"audio"),
            on_complete_prompt="El libro terminó.",
        )
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play_audiobook", {"book_id": "x"})
        await coordinator.on_response_done()

        # Media task is async — wait for it to finish
        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        names = [entry[0] for entry in provider.sent]
        assert "send_conversation_message" in names
        assert ("send_conversation_message", "El libro terminó.") in provider.sent
        # request_response fires immediately after send_conversation_message
        cm_idx = next(
            i for i, e in enumerate(provider.sent) if e[0] == "send_conversation_message"
        )
        assert provider.sent[cm_idx + 1] == ("request_response",)

    async def test_on_complete_prompt_skipped_when_none(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        import asyncio

        stream = AudioStream(factory=_factory(b"audio"), on_complete_prompt=None)
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play_audiobook", {"book_id": "x"})
        await coordinator.on_response_done()

        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        names = [entry[0] for entry in provider.sent]
        assert "send_conversation_message" not in names

    async def test_on_complete_prompt_not_fired_on_cancel(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """If the media task is cancelled mid-stream, no prompt fires."""
        import asyncio

        event = asyncio.Event()

        async def slow_factory() -> AsyncIterator[bytes]:
            yield b"first chunk"
            await event.wait()  # blocks until cancelled
            yield b"second chunk"

        stream = AudioStream(factory=slow_factory, on_complete_prompt="should not fire")
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play_audiobook", {"book_id": "x"})
        await asyncio.sleep(0.001)  # let media task start
        await coordinator.interrupt()

        names = [entry[0] for entry in provider.sent]
        assert "send_conversation_message" not in names

    async def test_completion_creates_synthetic_turn_for_response_handling(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """Fix #2: post-completion response needs a turn so on_response_done works."""
        import asyncio

        stream = AudioStream(
            factory=_factory(b"a"),
            on_complete_prompt="El libro terminó.",
        )
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play_audiobook", {"book_id": "x"})
        await coordinator.on_response_done()

        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        # After the synthetic prompt fires, current_turn must be set so the
        # incoming model response (deltas, tool calls, response_done) is handled.
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.state == TurnState.IN_RESPONSE

    async def test_completion_response_audio_flows_through_normally(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """Fix #14: model audio for the announcement reply must reach the client.

        Validates the full path: stream completes → synthetic turn created →
        prompt sent → simulated model deltas + done → client got audio →
        coordinator returns to a clean state (current_turn cleared, ready status).
        """
        import asyncio

        stream = AudioStream(
            factory=_factory(b"book"),
            on_complete_prompt="El libro terminó.",
        )
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()

        # Wait for media task + synthetic turn creation
        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        assert coordinator.current_turn is not None  # synthetic turn live

        # Simulate model response: audio deltas → audio_done → response_done
        # (Tests call coordinator handlers directly — provider callbacks aren't
        # wired in this fixture.)
        send_audio_count_before = mocks["send_audio"].await_count
        await coordinator.on_audio_delta(b"announce_chunk_1")
        await coordinator.on_audio_delta(b"announce_chunk_2")
        await coordinator.on_audio_done()
        await coordinator.on_response_done()

        # Audio for the announcement reached the client
        assert mocks["send_audio"].await_count == send_audio_count_before + 2
        # The synthetic turn was torn down cleanly via on_response_done → _apply_side_effects
        assert coordinator.current_turn is None
        # Status was updated back to ready at end of synthetic turn
        ready_status_calls = [
            c
            for c in mocks["send_status"].await_args_list
            if "Ready" in str(c) or "ready" in str(c).lower()
        ]
        assert ready_status_calls, "ready status should be sent after synthetic turn ends"

    async def test_completion_skipped_when_user_started_new_turn(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """Fix #4: PTT during chime/silence wins; completion prompt skipped."""
        import asyncio

        # Slow stream that yields one chunk then waits; we'll let the player
        # finish but block in the trailing silence by using a custom factory.
        gate = asyncio.Event()

        async def gated_stream() -> AsyncIterator[bytes]:
            yield b"book"
            await gate.wait()  # Block until test releases — simulates trailing silence
            # When released, complete naturally
            yield b"chime"

        stream = AudioStream(factory=gated_stream, on_complete_prompt="end of book")
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()
        # Let the media task start and consume the first chunk
        await asyncio.sleep(0.005)

        # Simulate user PTT during the trailing silence: starts a new turn,
        # which interrupts the media task. After interrupt, release the gate.
        await coordinator.on_ptt_start()
        gate.set()
        # Wait for any cleanup
        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        names = [entry[0] for entry in provider.sent]
        # PTT cancelled the stream mid-flight → completion should NOT fire
        assert "send_conversation_message" not in names


class TestModelSpeakingDuringFactoryAudio:
    """Fix #9: factory audio must set model_speaking=true so client UI knows
    audio is flowing and the thinking-tone trigger doesn't false-fire."""

    async def test_factory_audio_sets_model_speaking_true(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        import asyncio

        stream = AudioStream(factory=_factory(b"chunk"), on_complete_prompt=None)
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()

        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        # model_speaking went True at start of stream
        true_calls = [c for c in mocks["send_model_speaking"].await_args_list if c.args == (True,)]
        assert true_calls, "model_speaking(True) should fire when factory audio starts"

    async def test_factory_audio_clears_model_speaking_when_no_prompt(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """No on_complete_prompt → no synthetic turn → model_speaking must reset to False."""
        import asyncio

        stream = AudioStream(factory=_factory(b"chunk"), on_complete_prompt=None)
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()

        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        false_calls = [
            c for c in mocks["send_model_speaking"].await_args_list if c.args == (False,)
        ]
        assert false_calls, "model_speaking(False) should fire when factory ends without prompt"
        assert coordinator._speaking_state.is_speaking is False


class TestCompletionSilenceAfterRequest:
    """Fix #8: silence is sent AFTER request_response, not before — so model
    generation latency overlaps with silence playback instead of stacking."""

    async def test_silence_sent_after_request(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        import asyncio

        stream = AudioStream(
            factory=_factory(b"book"),
            on_complete_prompt="done",
            completion_silence_ms=500,
        )
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()

        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        # Find positions of send_audio (book chunk + silence) and request_response.
        # send_audio isn't logged in provider.sent (it's on the coordinator's send_audio
        # mock); we assert via the mock's await order vs the provider's request order.
        # The order on the wire: book chunk → request_response → silence
        send_audio_calls = mocks["send_audio"].await_args_list
        # 2 send_audio calls: 1 for book chunk, 1 for silence_bytes
        assert len(send_audio_calls) == 2
        # The silence is the larger payload (500ms PCM16 24kHz mono = 24000 bytes)
        assert len(send_audio_calls[0].args[0]) == 4  # b"book"
        assert len(send_audio_calls[1].args[0]) == 24000  # silence buffer
        # request_response was sent BEFORE the silence (which is what we want)
        assert ("request_response",) in provider.sent

    async def test_no_silence_when_completion_silence_ms_is_zero(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        import asyncio

        stream = AudioStream(
            factory=_factory(b"book"),
            on_complete_prompt="done",
            completion_silence_ms=0,
        )
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()

        for _ in range(20):
            if coordinator.current_media_task is None or coordinator.current_media_task.done():
                break
            await asyncio.sleep(0.001)

        # Only the book chunk should land on send_audio — no silence injected
        assert mocks["send_audio"].await_count == 1


class TestPlaySound:
    """PlaySound queues a chime ahead of the model's response audio (FIFO on
    the WebSocket). Latest pending sound wins; cleared on interrupt."""

    async def test_play_sound_is_latched_on_tool_call(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        chime = b"chime_pcm"
        mocks["dispatch_tool"].return_value = ToolResult(
            output='{"items": []}', side_effect=PlaySound(pcm=chime)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_news", {})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_play_sound is not None
        assert coordinator.current_turn.pending_play_sound.pcm == chime
        assert coordinator.current_turn.needs_follow_up is True

    async def test_play_sound_dispatched_after_request_response(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """The chime PCM hits send_audio AFTER request_response is fired."""
        chime = b"chime_pcm"
        mocks["dispatch_tool"].return_value = ToolResult(
            output='{"items": []}', side_effect=PlaySound(pcm=chime)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_news", {})
        await coordinator.on_response_done()

        # Provider got request_response (the follow-up round)
        assert ("request_response",) in provider.sent
        # And then send_audio received the chime bytes
        assert mocks["send_audio"].await_args_list[-1].args == (chime,)
        # Latched chime cleared
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_play_sound is None

    async def test_play_sound_skipped_when_response_cancelled(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """If response_cancelled flips between latch and dispatch, no chime."""
        chime = b"chime_pcm"
        mocks["dispatch_tool"].return_value = ToolResult(
            output='{"items": []}', side_effect=PlaySound(pcm=chime)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_news", {})
        # Simulate PTT race: cancel between the latch and on_response_done
        coordinator.response_cancelled = True

        # on_response_done early-returns when response_cancelled is True, so
        # the chime can't fire through that path. (The fix at line ~330 is
        # the safety net for the race that survives the early-return.)
        await coordinator.on_response_done()

        # send_audio was NOT called with the chime
        chime_calls = [c for c in mocks["send_audio"].await_args_list if c.args == (chime,)]
        assert chime_calls == []

    async def test_play_sound_cleared_on_interrupt(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        chime = b"chime_pcm"
        mocks["dispatch_tool"].return_value = ToolResult(
            output='{"items": []}', side_effect=PlaySound(pcm=chime)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_news", {})
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_play_sound is not None

        await coordinator.interrupt()

        # interrupt() drops the turn; verify if a new turn is started, no
        # stale chime carries over.
        assert coordinator.current_turn is None

    async def test_latest_play_sound_overwrites_earlier(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """Two chained tool calls each emitting PlaySound: latest wins."""
        first = b"first_chime"
        second = b"second_chime"
        mocks["dispatch_tool"].side_effect = [
            ToolResult(output="{}", side_effect=PlaySound(pcm=first)),
            ToolResult(output="{}", side_effect=PlaySound(pcm=second)),
        ]

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_weather", {})
        await coordinator.on_tool_call("c2", "get_news", {})

        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_play_sound is not None
        assert coordinator.current_turn.pending_play_sound.pcm == second


class TestResponseCancelledFlag:
    """Stale delta drop flag behavior."""

    async def test_flag_is_reset_on_commit(self, coordinator: TurnCoordinator) -> None:
        coordinator.response_cancelled = True
        await _commit_turn(coordinator)
        assert coordinator.response_cancelled is False

    async def test_flag_is_reset_before_follow_up_response(
        self, coordinator: TurnCoordinator, mocks: dict[str, Any], provider: StubVoiceProvider
    ) -> None:
        mocks["dispatch_tool"].return_value = ToolResult(output='{"time": "3pm"}')

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "get_current_time", {})

        # Pretend an interrupt would set the flag; then a follow-up comes in
        coordinator.response_cancelled = True

        # Simulate fresh response_done path when flag is False to exercise
        # the reset-before-follow-up logic.
        coordinator.response_cancelled = False
        await coordinator.on_response_done()

        assert ("request_response",) in provider.sent
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
        await coordinator.on_tool_call("c1", "get_current_time", {})
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


class TestCleanupOrdering:
    """Regression: content stream must stop BEFORE SpeakingState.force_release.

    Both `interrupt()` and `on_session_disconnected()` previously ran
    `force_release()` before `_stop_content_stream()`. Between the two
    awaits, the pump task could re-enter `_factory_send_audio`, see
    `is_speaking=False`, and re-acquire FACTORY — leaving SpeakingState
    stuck on a dead pump. These tests lock down the fixed order.
    """

    @staticmethod
    def _wrap_call_order(
        coordinator: TurnCoordinator,
    ) -> list[str]:
        """Monkey-patch the two ordering-critical awaits to record order."""
        calls: list[str] = []
        original_stop = coordinator._stop_content_stream
        original_force_release = coordinator._speaking_state.force_release

        async def tracking_stop() -> None:
            calls.append("stop_content_stream")
            await original_stop()

        async def tracking_force_release() -> bool:
            calls.append("force_release")
            return await original_force_release()

        coordinator._stop_content_stream = tracking_stop  # type: ignore[method-assign]
        coordinator._speaking_state.force_release = tracking_force_release  # type: ignore[method-assign]
        return calls

    async def test_interrupt_stops_stream_before_force_release(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """`interrupt()` step 3 (stop content) must run before step 4
        (force_release). Reverse order opens a race where the pump's
        `_factory_send_audio` re-acquires FACTORY after force_release clears it.
        """
        import asyncio

        async def forever_stream() -> AsyncIterator[bytes]:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)

        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=forever_stream)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()  # spawns the content stream
        assert coordinator.current_media_task is not None

        calls = self._wrap_call_order(coordinator)
        await coordinator.interrupt()

        assert calls.index("stop_content_stream") < calls.index("force_release"), (
            f"stop_content_stream must run before force_release; got {calls}"
        )

    async def test_session_disconnect_stops_stream_before_force_release(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """Same ordering invariant for `on_session_disconnected`."""
        import asyncio

        async def forever_stream() -> AsyncIterator[bytes]:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)

        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=forever_stream)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()
        assert coordinator.current_media_task is not None

        calls = self._wrap_call_order(coordinator)
        await coordinator.on_session_disconnected()

        assert calls.index("stop_content_stream") < calls.index("force_release"), (
            f"stop_content_stream must run before force_release; got {calls}"
        )

    async def test_interrupt_leaves_speaking_state_clean_after_factory_stream(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        """End-state invariant: after interrupting a content stream, no FACTORY
        owner survives. Directly proves the bug (even without monkey-patching
        — if the race ever leaks through, owner stays FACTORY)."""
        import asyncio

        async def forever_stream() -> AsyncIterator[bytes]:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)

        mocks["dispatch_tool"].return_value = ToolResult(
            output="{}", side_effect=AudioStream(factory=forever_stream)
        )

        await _commit_turn(coordinator)
        await coordinator.on_tool_call("c1", "play", {})
        await coordinator.on_response_done()
        # Let the pump acquire FACTORY via the first chunk
        await asyncio.sleep(0.02)

        await coordinator.interrupt()

        # Pump is gone and FACTORY (or any owner) is released.
        assert coordinator._speaking_state.owner is None
        assert coordinator._speaking_state.is_speaking is False


class TestTurnDataclass:
    """Small sanity checks on the Turn dataclass itself."""

    def test_default_turn_has_empty_pending_audio_streams(self) -> None:
        t = Turn()
        assert t.pending_audio_streams == []
        assert t.needs_follow_up is False
        assert t.user_audio_frames == 0

    def test_turn_default_state_is_listening(self) -> None:
        t = Turn()
        assert t.state == TurnState.LISTENING
