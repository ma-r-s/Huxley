"""Coordinator + ClaimObserver integration tests (T1.4 Stage 2 commit 3b).

Exercises the direct-entry path (`coordinator.start_input_claim`) end-
to-end: InputClaim → CONTENT-channel Activity → provider.suspend →
MicRouter.claim → optional speaker pump → end (NATURAL / USER_PTT /
PREEMPTED / ERROR) → provider.resume + skill callback + handle resolve.

Tool-dispatched path via ToolResult.side_effect is covered separately
in Stage 2 commit 3c.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.focus.manager import FocusManager
from huxley.turn.coordinator import TurnCoordinator
from huxley.voice.stub import StubVoiceProvider
from huxley_sdk import ClaimEndReason, InputClaim, ToolResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def provider() -> StubVoiceProvider:
    p = StubVoiceProvider()
    p._connected = True
    return p


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
async def focus_manager() -> AsyncIterator[FocusManager]:
    fm = FocusManager.with_default_channels()
    fm.start()
    yield fm
    await fm.stop()


@pytest.fixture
def coordinator(mocks: dict[str, Any], focus_manager: FocusManager) -> TurnCoordinator:
    return TurnCoordinator(**mocks, focus_manager=focus_manager)


async def _drain(ticks: int = 5) -> None:
    """Let queued asyncio tasks reach completion — same pattern as the
    timers suite. The cancel-via-create_task path in `start_input_claim`
    needs a few event-loop ticks to settle."""
    for _ in range(ticks):
        await asyncio.sleep(0)


# --- Direct-entry start: FOREGROUND path ---


class TestStartInputClaimForeground:
    async def test_foreground_suspends_provider(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        on_mic = AsyncMock()
        await coordinator.start_input_claim(InputClaim(on_mic_frame=on_mic))
        assert provider.is_suspended is True

    async def test_foreground_swaps_mic_router(self, coordinator: TurnCoordinator) -> None:
        on_mic = AsyncMock()
        await coordinator.start_input_claim(InputClaim(on_mic_frame=on_mic))
        # A subsequent mic frame reaches the skill handler, not the provider.
        await coordinator._mic_router.dispatch(b"\x01\x02")
        on_mic.assert_awaited_once_with(b"\x01\x02")

    async def test_foreground_without_speaker_source_no_pump(
        self, coordinator: TurnCoordinator
    ) -> None:
        on_mic = AsyncMock()
        await coordinator.start_input_claim(InputClaim(on_mic_frame=on_mic))
        assert coordinator._claim_obs is not None
        assert coordinator._claim_obs._speaker_task is None

    async def test_foreground_with_speaker_source_forwards_chunks(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
    ) -> None:
        async def speaker() -> AsyncIterator[bytes]:
            yield b"\xaa\xbb"
            yield b"\xcc\xdd"

        claim = InputClaim(on_mic_frame=AsyncMock(), speaker_source=speaker())
        await coordinator.start_input_claim(claim)
        # Let the speaker pump emit both chunks.
        await _drain(ticks=10)
        send_audio: AsyncMock = mocks["send_audio"]
        forwarded = [args[0] for args, _ in send_audio.call_args_list]
        assert b"\xaa\xbb" in forwarded
        assert b"\xcc\xdd" in forwarded


# --- End reasons ---


class TestClaimEndNatural:
    async def test_cancel_ends_with_natural(self, coordinator: TurnCoordinator) -> None:
        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        handle = await coordinator.start_input_claim(claim)

        handle.cancel()
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)

        assert reason is ClaimEndReason.NATURAL
        on_end.assert_awaited_once_with(ClaimEndReason.NATURAL)
        # Observer scrubbed so a new claim can start.
        assert coordinator._claim_obs is None

    async def test_cancel_restores_mic_router(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        on_mic = AsyncMock()
        handle = await coordinator.start_input_claim(InputClaim(on_mic_frame=on_mic))
        handle.cancel()
        await handle.wait_end()

        # After end, dispatch goes back to the default (provider.send_user_audio).
        await coordinator._mic_router.dispatch(b"\x01\x02")
        on_mic.assert_not_awaited()
        assert coordinator._mic_router.is_claimed is False

    async def test_cancel_resumes_provider(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        handle = await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        assert provider.is_suspended is True
        handle.cancel()
        await handle.wait_end()
        assert provider.is_suspended is False

    async def test_cancel_is_idempotent(self, coordinator: TurnCoordinator) -> None:
        handle = await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        handle.cancel()
        handle.cancel()
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.NATURAL


class TestClaimEndUserPtt:
    async def test_ptt_ends_claim_with_user_ptt(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        handle = await coordinator.start_input_claim(claim)

        # Simulate grandpa holding PTT during the call — the coordinator's
        # interrupt() path fires.
        await coordinator.interrupt()

        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.USER_PTT
        on_end.assert_awaited_once_with(ClaimEndReason.USER_PTT)
        # Provider is resumed so the about-to-start PTT turn can feed audio.
        assert provider.is_suspended is False
        assert coordinator._claim_obs is None


class TestClaimEndPreempted:
    async def test_session_disconnect_ends_claim_with_error(
        self, coordinator: TurnCoordinator
    ) -> None:
        """Session drop during a call is an ERROR end — not a natural
        close, not a user-driven hangup."""
        on_end = AsyncMock()
        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        )
        await coordinator.on_session_disconnected()
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.ERROR
        on_end.assert_awaited_once_with(ClaimEndReason.ERROR)


class TestWaitEndBlocksUntilEnd:
    async def test_wait_end_blocks_while_claim_active(self, coordinator: TurnCoordinator) -> None:
        handle = await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(handle.wait_end(), timeout=0.05)
            msg = "wait_end should have blocked"
            raise AssertionError(msg)
        # Still active.
        handle.cancel()
        await handle.wait_end()


# --- Contract: suspend-before-swap ordering ---


class TestOrderingInvariants:
    async def test_suspend_happens_before_mic_swap(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        """Critical invariant from the spike: suspend the provider BEFORE
        swapping the mic. The inverse order leaves a window where a mic
        frame forwards to the (still-active) provider even though we've
        already committed to the claim."""
        order: list[str] = []

        # Wrap provider.suspend to log ordering.
        orig_suspend = provider.suspend

        async def tracked_suspend() -> None:
            order.append("suspend")
            await orig_suspend()

        provider.suspend = tracked_suspend  # type: ignore[method-assign]

        orig_claim = coordinator._mic_router.claim

        def tracked_claim(handler):  # type: ignore[no-untyped-def]
            order.append("claim")
            return orig_claim(handler)

        coordinator._mic_router.claim = tracked_claim  # type: ignore[method-assign]

        await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        assert order == ["suspend", "claim"]


# --- Error path: mic router busy ---


class TestClaimVsInjectTurn:
    """The "conversation interactions matrix" cell: claim active when a
    PREEMPT-priority inject_turn fires (medication reminder during a
    call). Expected behavior flows entirely out of FocusManager:
    DIALOG acquire forces CONTENT to NONE → claim observer fires
    _end with default PREEMPTED → provider resumes → LLM generates
    the reminder audio normally.
    """

    async def test_preempt_inject_ends_claim_with_preempted(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        from huxley_sdk import InjectPriority

        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        handle = await coordinator.start_input_claim(claim)
        assert provider.is_suspended is True

        # Medication reminder fires mid-call.
        await coordinator.inject_turn("Hora de la pastilla.", priority=InjectPriority.PREEMPT)

        # Claim ended with PREEMPTED — the default reason when FM-forced NONE
        # arrives with no explicit reason latched (inject_turn doesn't
        # touch _claim_obs; it only acquires DIALOG).
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.PREEMPTED
        on_end.assert_awaited_once_with(ClaimEndReason.PREEMPTED)
        # Provider resumed so the injected turn's LLM response can flow.
        assert provider.is_suspended is False
        # Provider received the inject prompt + response request.
        assert ("send_conversation_message", "Hora de la pastilla.") in provider.sent
        assert ("request_response",) in provider.sent


class TestToolDispatchedClaim:
    """`ToolResult.side_effect = InputClaim(...)` path — commit 3c.

    A tool returning an `InputClaim` latches it on the current turn.
    At the terminal barrier (`_apply_side_effects`), the framework
    starts it via `start_input_claim`, dropping any pending audio
    stream (claim wins over content). Matches the AudioStream timing
    pattern so the model's pre-narration ("starting recording now")
    plays before the mic swaps.
    """

    async def _run_tool_turn(
        self,
        coordinator: TurnCoordinator,
        claim: InputClaim,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """Helper: fire a user turn whose single tool call returns an
        InputClaim side-effect, drive the state machine through commit →
        response → tool-dispatch → response.done so the terminal barrier
        runs and dispatches the claim."""
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=claim)
        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_frames = 60
        await coordinator.on_ptt_stop()
        # Tool dispatched — latches the claim on the turn.
        await coordinator.on_tool_call("call_1", "start_record", {})
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_input_claim is claim
        # Drive to terminal barrier.
        await coordinator.on_audio_done()
        await coordinator.on_response_done()

    async def test_latches_pending_input_claim_on_tool_dispatch(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        claim = InputClaim(on_mic_frame=AsyncMock())
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=claim)
        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_frames = 60
        await coordinator.on_ptt_stop()
        await coordinator.on_tool_call("call_1", "start_record", {})
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_input_claim is claim

    async def test_starts_claim_at_terminal_barrier(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        on_mic = AsyncMock()
        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=on_mic, on_claim_end=on_end)
        await self._run_tool_turn(coordinator, claim, mocks, provider)
        # Claim started — observer exists + mic swapped + provider suspended.
        assert coordinator._claim_obs is not None
        assert provider.is_suspended is True
        await coordinator._mic_router.dispatch(b"\x01")
        on_mic.assert_awaited_once_with(b"\x01")

    async def test_claim_wins_over_audio_stream_in_same_turn(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """Rare edge case: one turn produces both an AudioStream and an
        InputClaim (two tool calls, chained). Latest tool wins per side-
        effect type, and claim wins over streams at the barrier."""
        # Craft a tool that returns an audio stream first, then the next
        # tool returns a claim. Simulate by manually populating the turn.
        claim = InputClaim(on_mic_frame=AsyncMock())

        async def _factory() -> AsyncIterator[bytes]:
            yield b"\x00\x01"

        from huxley_sdk import AudioStream

        stream = AudioStream(factory=_factory)

        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=stream)
        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_frames = 60
        await coordinator.on_ptt_stop()
        await coordinator.on_tool_call("call_1", "play", {})

        # Second tool returns the claim — overwrite dispatch_tool.
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=claim)
        await coordinator.on_tool_call("call_2", "record", {})
        assert coordinator.current_turn is not None
        assert len(coordinator.current_turn.pending_audio_streams) == 1
        assert coordinator.current_turn.pending_input_claim is claim

        await coordinator.on_audio_done()
        await coordinator.on_response_done()

        # Claim won: observer alive, content stream NOT started.
        assert coordinator._claim_obs is not None
        assert coordinator._content_obs is None

    async def test_preempt_inject_drops_pending_claim_and_fires_on_end(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """If a PREEMPT inject_turn is queued while the user's turn is
        latching a claim, the terminal barrier fires the inject instead
        of the claim. The dropped claim's `on_claim_end(PREEMPTED)`
        still fires so the skill sees one lifecycle callback."""
        from huxley_sdk import InjectPriority

        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=claim)

        # Start turn, dispatch tool (latches claim), then queue a PREEMPT
        # before terminal barrier fires.
        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_frames = 60
        await coordinator.on_ptt_stop()
        await coordinator.on_tool_call("call_1", "start_record", {})

        # Queue a PREEMPT — since current_turn is active, inject_turn queues.
        await coordinator.inject_turn("pastilla", priority=InjectPriority.PREEMPT)
        assert len(coordinator._injected_queue) == 1

        # Barrier fires. PREEMPT drains, claim dropped.
        await coordinator.on_audio_done()
        await coordinator.on_response_done()

        # Dropped claim's on_end fired with PREEMPTED.
        on_end.assert_awaited_once_with(ClaimEndReason.PREEMPTED)
        # Claim never started — no observer.
        assert coordinator._claim_obs is None
        # Injected turn took over (sent conversation_message).
        assert ("send_conversation_message", "pastilla") in provider.sent

    async def test_interrupt_before_barrier_drops_pending_claim_silently(
        self,
        coordinator: TurnCoordinator,
        mocks: dict[str, Any],
        provider: StubVoiceProvider,
    ) -> None:
        """If PTT fires between tool dispatch and terminal barrier, the
        turn is interrupted and the pending claim never starts. Skill's
        on_claim_end does NOT fire — from the skill's perspective the
        tool never fully succeeded (interrupt before terminal)."""
        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        mocks["dispatch_tool"].return_value = ToolResult(output="{}", side_effect=claim)

        await coordinator.on_ptt_start()
        assert coordinator.current_turn is not None
        coordinator.current_turn.user_audio_frames = 60
        await coordinator.on_ptt_stop()
        await coordinator.on_tool_call("call_1", "start_record", {})
        assert coordinator.current_turn is not None
        assert coordinator.current_turn.pending_input_claim is claim

        # Interrupt before the barrier.
        await coordinator.interrupt()

        # Claim never started, on_end never called.
        assert coordinator._claim_obs is None
        on_end.assert_not_awaited()


class TestClaimFailsIfRouterBusy:
    async def test_mic_router_busy_fires_error_end(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        """If the MicRouter is already claimed when the observer tries
        to start (leaked / racing consumer), the observer catches the
        `MicAlreadyClaimedError` internally, resumes the provider, and
        ends with `ClaimEndReason.ERROR` — never leaking the exception
        up through the FocusManager mailbox where it'd kill the actor."""
        # Pre-claim the router manually.
        coordinator._mic_router.claim(AsyncMock())

        on_end = AsyncMock()
        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        )
        # The observer's ERROR path resumes the provider itself (before
        # firing _on_end). The FM release from the observer's _end is
        # what scrubs _claim_obs; we give it a few ticks.
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.ERROR
        on_end.assert_awaited_once_with(ClaimEndReason.ERROR)
        # Provider un-suspended even though the claim failed.
        assert provider.is_suspended is False


class TestCancelActiveClaim:
    """Stage 2.1 — `coordinator.cancel_active_claim()` for skills that
    dispatch claims via `ToolResult.side_effect` and need to end them
    from outside the observer (caller WS closes, voice memo finishes,
    etc.). Direct-entry callers use the `ClaimHandle.cancel()` they
    already have; this is the side-effect-path equivalent."""

    async def test_returns_false_when_no_claim_active(
        self, coordinator: TurnCoordinator
    ) -> None:
        result = await coordinator.cancel_active_claim()
        assert result is False

    async def test_ends_active_claim_with_natural_default(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        on_end = AsyncMock()
        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        )
        result = await coordinator.cancel_active_claim()
        assert result is True
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.NATURAL
        on_end.assert_awaited_once_with(ClaimEndReason.NATURAL)
        assert provider.is_suspended is False

    async def test_custom_reason_propagates(
        self, coordinator: TurnCoordinator
    ) -> None:
        on_end = AsyncMock()
        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        )
        await coordinator.cancel_active_claim(reason=ClaimEndReason.ERROR)
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.ERROR

    async def test_idempotent_when_claim_already_ended(
        self, coordinator: TurnCoordinator
    ) -> None:
        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock())
        )
        await coordinator.cancel_active_claim()
        await handle.wait_end()
        # Second cancel — claim is already ended, returns False cleanly.
        result = await coordinator.cancel_active_claim()
        assert result is False
