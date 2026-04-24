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


class TestInputModeEvents:
    """Client-facing input_mode / claim_started / claim_ended events
    fire on claim lifecycle (T1.10 protocol for mic-mode handoff)."""

    async def test_start_emits_claim_started_and_skill_continuous(
        self, focus_manager: FocusManager, provider: StubVoiceProvider
    ) -> None:
        send_input_mode = AsyncMock()
        send_claim_started = AsyncMock()
        send_claim_ended = AsyncMock()
        coord = TurnCoordinator(
            send_audio=AsyncMock(),
            send_audio_clear=AsyncMock(),
            send_status=AsyncMock(),
            send_model_speaking=AsyncMock(),
            send_dev_event=AsyncMock(),
            send_input_mode=send_input_mode,
            send_claim_started=send_claim_started,
            send_claim_ended=send_claim_ended,
            provider=provider,
            dispatch_tool=AsyncMock(return_value=ToolResult(output="{}")),
            focus_manager=focus_manager,
        )
        await coord.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))

        send_claim_started.assert_awaited_once()
        args, _ = send_claim_started.await_args
        assert args[0].startswith("claim:")
        send_input_mode.assert_awaited_once()
        _, kwargs = send_input_mode.await_args
        assert send_input_mode.await_args.args[0] == "skill_continuous"
        assert kwargs["reason"] == "claim_started"
        assert kwargs["claim_id"].startswith("claim:")

    async def test_end_emits_claim_ended_and_assistant_ptt(
        self, focus_manager: FocusManager, provider: StubVoiceProvider
    ) -> None:
        send_input_mode = AsyncMock()
        send_claim_ended = AsyncMock()
        coord = TurnCoordinator(
            send_audio=AsyncMock(),
            send_audio_clear=AsyncMock(),
            send_status=AsyncMock(),
            send_model_speaking=AsyncMock(),
            send_dev_event=AsyncMock(),
            send_input_mode=send_input_mode,
            send_claim_started=AsyncMock(),
            send_claim_ended=send_claim_ended,
            provider=provider,
            dispatch_tool=AsyncMock(return_value=ToolResult(output="{}")),
            focus_manager=focus_manager,
        )
        handle = await coord.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        send_input_mode.reset_mock()
        handle.cancel()
        await handle.wait_end()

        send_claim_ended.assert_awaited_once()
        args, _ = send_claim_ended.await_args
        assert args[0].startswith("claim:")
        assert args[1] == "natural"

        # After end, input_mode goes back to assistant_ptt.
        mode_args = send_input_mode.await_args.args
        assert mode_args[0] == "assistant_ptt"
        assert send_input_mode.await_args.kwargs["reason"] == "claim_ended"

    async def test_preempted_end_uses_claim_preempted_reason(
        self, focus_manager: FocusManager, provider: StubVoiceProvider
    ) -> None:
        # An InjectPriority.PREEMPT request while a claim is live fires
        # ClaimEndReason.PREEMPTED; the mic-mode flip carries a distinct
        # reason so a dev UI can tell "user hung up" from "medication
        # reminder kicked them out."
        send_input_mode = AsyncMock()
        coord = TurnCoordinator(
            send_audio=AsyncMock(),
            send_audio_clear=AsyncMock(),
            send_status=AsyncMock(),
            send_model_speaking=AsyncMock(),
            send_dev_event=AsyncMock(),
            send_input_mode=send_input_mode,
            send_claim_started=AsyncMock(),
            send_claim_ended=AsyncMock(),
            provider=provider,
            dispatch_tool=AsyncMock(return_value=ToolResult(output="{}")),
            focus_manager=focus_manager,
        )
        obs = coord._claim_obs
        assert obs is None
        await coord.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        send_input_mode.reset_mock()
        # Fire the preempted end path directly via the observer hook.
        live = coord._claim_obs
        assert live is not None
        live.set_end_reason(ClaimEndReason.PREEMPTED)
        await coord._end_input_claim(live)

        # Assert the last input_mode flip used the preempted reason.
        reasons = [c.kwargs.get("reason") for c in send_input_mode.await_args_list]
        assert "claim_preempted" in reasons


class TestPttClaimDebounce:
    """300ms debounce after claim latches — swallows the same-tap bounce
    that would otherwise end the claim the instant it connects."""

    async def test_ptt_within_300ms_of_claim_start_is_dropped(
        self, coordinator: TurnCoordinator
    ) -> None:
        await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        # Claim is live; _claim_started_at is "now". A PTT press at this
        # instant must not end it (bounce protection).
        assert coordinator._claim_obs is not None
        await coordinator.on_ptt_start()
        # Claim still active — the press was debounced.
        assert coordinator._claim_obs is not None
        assert coordinator.current_turn is None

    async def test_ptt_after_debounce_ends_claim_no_listening_turn(
        self, coordinator: TurnCoordinator
    ) -> None:
        """PTT during an active call = hangup only (not "start listening").

        The user tapped the button to end the call. They did NOT intend to
        immediately speak to the assistant — a second PTT opens the fresh
        conversation. This prevents the common case where grandpa presses
        once to hang up and accidentally starts a listening turn he didn't ask for.
        """
        import time

        await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        # Simulate time passing past the debounce window.
        coordinator._claim_started_at = time.monotonic() - 1.0

        await coordinator.on_ptt_start()

        # Claim ended — hangup succeeded.
        assert coordinator._claim_obs is None
        # No listening turn: PTT was a hangup gesture, not a "start talking" gesture.
        assert coordinator.current_turn is None


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


# --- Concurrent claims: single-slot rejection (Stage 2b, 2026-04-23) ---


class TestConcurrentClaimRejection:
    """Huxley's single-slot COMMS policy: a second `start_input_claim`
    call while one is active raises `ClaimBusyError` so the calling
    skill can reject the peer (e.g., Telegram sends `DISCARDED_CALL`).
    Call-waiting / claim-stacking is not supported today.
    """

    async def test_second_claim_raises_claim_busy_error(
        self, coordinator: TurnCoordinator
    ) -> None:
        from huxley_sdk import ClaimBusyError

        await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        # First claim is live. Second attempt raises.
        with pytest.raises(ClaimBusyError):
            await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))

    async def test_second_claim_rejection_leaves_first_claim_intact(
        self, coordinator: TurnCoordinator, provider: StubVoiceProvider
    ) -> None:
        from huxley_sdk import ClaimBusyError

        first_on_mic = AsyncMock()
        handle = await coordinator.start_input_claim(InputClaim(on_mic_frame=first_on_mic))
        assert provider.is_suspended is True

        with pytest.raises(ClaimBusyError):
            await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))

        # First claim still owns everything.
        assert coordinator._claim_obs is not None
        assert provider.is_suspended is True
        # Mic still routes to the first skill.
        await coordinator._mic_router.dispatch(b"\x05")
        first_on_mic.assert_awaited_once_with(b"\x05")
        # Ending the first works normally.
        handle.cancel()
        await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert coordinator._claim_obs is None

    async def test_claim_after_previous_ended_succeeds(self, coordinator: TurnCoordinator) -> None:
        """Single-slot, not single-use: once a claim ends, a new one works."""
        h1 = await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        h1.cancel()
        await asyncio.wait_for(h1.wait_end(), timeout=1.0)
        # Slot free; new claim succeeds.
        h2 = await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        assert coordinator._claim_obs is not None
        h2.cancel()
        await asyncio.wait_for(h2.wait_end(), timeout=1.0)


# --- inject_turn during an active claim (post-ship fix, 2026-04-24) ---


class TestInjectTurnDuringActiveClaim:
    """Post-ship critic found a Stage 2b correctness gap: `inject_turn`
    from idle (`current_turn is None`) while a COMMS claim is live did
    NOT honor the `BLOCK_BEHIND_COMMS` / `NORMAL` priority contract —
    the idle-fire path called `_fire_injected_turn` unconditionally,
    acquiring DIALOG and yanking the claim. Only PREEMPT should
    bypass an active claim. These tests lock in the corrected
    behavior.
    """

    async def test_normal_during_active_claim_queues_rather_than_preempts(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        await coordinator.start_input_claim(claim)
        assert provider.is_suspended is True

        # NORMAL inject fires from idle (current_turn is None) during
        # the live claim — must queue, not preempt.
        await coordinator.inject_turn("Social reminder.", dedup_key="social")

        # Claim still live; no DIALOG turn started; request in queue.
        assert coordinator._claim_obs is not None
        assert coordinator.current_turn is None
        assert len(coordinator._injected_queue) == 1
        assert coordinator._injected_queue[0].prompt == "Social reminder."
        assert provider.is_suspended is True  # claim hasn't been preempted
        on_end.assert_not_awaited()

    async def test_block_behind_comms_during_active_claim_queues(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        from huxley_sdk import InjectPriority

        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        await coordinator.start_input_claim(claim)

        await coordinator.inject_turn(
            "Tu temporizador terminó.",
            dedup_key="timer_1",
            priority=InjectPriority.BLOCK_BEHIND_COMMS,
        )

        assert coordinator._claim_obs is not None
        assert coordinator.current_turn is None
        assert len(coordinator._injected_queue) == 1
        assert coordinator._injected_queue[0].priority is InjectPriority.BLOCK_BEHIND_COMMS
        assert provider.is_suspended is True
        on_end.assert_not_awaited()

    async def test_preempt_during_active_claim_still_barges(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        """PREEMPT's unconditional-urgency contract is unchanged:
        it still ends a live claim with PREEMPTED and fires the alert.
        Locks in the distinction between the new (NORMAL /
        BLOCK_BEHIND_COMMS) and unchanged (PREEMPT) behaviors.
        """
        from huxley_sdk import InjectPriority

        on_end = AsyncMock()
        claim = InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        handle = await coordinator.start_input_claim(claim)

        await coordinator.inject_turn(
            "¡Alarma!",
            priority=InjectPriority.PREEMPT,
        )

        # Claim evicted with PREEMPTED; alert is running as a synthetic turn.
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.PREEMPTED
        on_end.assert_awaited_once_with(ClaimEndReason.PREEMPTED)
        assert ("send_conversation_message", "¡Alarma!") in provider.sent


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

    async def test_returns_false_when_no_claim_active(self, coordinator: TurnCoordinator) -> None:
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

    async def test_custom_reason_propagates(self, coordinator: TurnCoordinator) -> None:
        on_end = AsyncMock()
        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), on_claim_end=on_end)
        )
        await coordinator.cancel_active_claim(reason=ClaimEndReason.ERROR)
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert reason is ClaimEndReason.ERROR

    async def test_idempotent_when_claim_already_ended(self, coordinator: TurnCoordinator) -> None:
        handle = await coordinator.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        await coordinator.cancel_active_claim()
        await handle.wait_end()
        # Second cancel — claim is already ended, returns False cleanly.
        result = await coordinator.cancel_active_claim()
        assert result is False


class TestSpeakerSourceNaturalEnd:
    """When the speaker_source iterator ends naturally (peer hung up a call),
    the claim ends with NATURAL reason — without requiring the skill to call
    ClaimHandle.cancel() or coordinator.cancel_active_claim().

    This is the fix for: peer hangs up → web UI stays stuck in 'En llamada'
    because the claim was never torn down.
    """

    async def test_natural_speaker_end_fires_on_claim_end_natural(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        """Speaker source exhausted → on_claim_end(NATURAL) fires."""
        on_end = AsyncMock()

        async def finite_speaker() -> AsyncIterator[bytes]:
            yield b"\xaa\xbb"
            yield b"\xcc\xdd"
            # Iterator ends — simulates peer hanging up.

        claim = InputClaim(
            on_mic_frame=AsyncMock(),
            speaker_source=finite_speaker(),
            on_claim_end=on_end,
        )
        handle = await coordinator.start_input_claim(claim)
        # Let the pump drain the iterator and self-release.
        reason = await asyncio.wait_for(handle.wait_end(), timeout=1.0)

        assert reason is ClaimEndReason.NATURAL
        on_end.assert_awaited_once_with(ClaimEndReason.NATURAL)

    async def test_natural_speaker_end_resumes_provider(
        self,
        coordinator: TurnCoordinator,
        provider: StubVoiceProvider,
    ) -> None:
        """Provider is resumed after the speaker source ends naturally."""

        async def finite_speaker() -> AsyncIterator[bytes]:
            yield b"\x01\x02"

        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), speaker_source=finite_speaker())
        )
        await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert provider.is_suspended is False

    async def test_natural_speaker_end_scrubs_claim_obs(
        self,
        coordinator: TurnCoordinator,
    ) -> None:
        """_claim_obs is cleared after natural speaker end."""

        async def finite_speaker() -> AsyncIterator[bytes]:
            yield b"\x01\x02"

        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), speaker_source=finite_speaker())
        )
        await asyncio.wait_for(handle.wait_end(), timeout=1.0)
        assert coordinator._claim_obs is None

    async def test_natural_speaker_end_restores_mic_router(
        self,
        coordinator: TurnCoordinator,
    ) -> None:
        """Mic router is released after the speaker source ends naturally."""
        on_mic = AsyncMock()

        async def finite_speaker() -> AsyncIterator[bytes]:
            yield b"\xde\xad"

        handle = await coordinator.start_input_claim(
            InputClaim(on_mic_frame=on_mic, speaker_source=finite_speaker())
        )
        await asyncio.wait_for(handle.wait_end(), timeout=1.0)

        # After end, mic frames go back to the default handler, not the skill.
        await coordinator._mic_router.dispatch(b"\x01\x02")
        on_mic.assert_not_awaited()
        assert coordinator._mic_router.is_claimed is False

    async def test_natural_speaker_end_sends_input_mode_assistant_ptt(
        self,
        focus_manager: FocusManager,
        provider: StubVoiceProvider,
    ) -> None:
        """Client receives input_mode('assistant_ptt') when peer hangs up
        — this is the signal that flips the web UI out of 'En llamada'."""
        send_input_mode = AsyncMock()
        coord = TurnCoordinator(
            send_audio=AsyncMock(),
            send_audio_clear=AsyncMock(),
            send_status=AsyncMock(),
            send_model_speaking=AsyncMock(),
            send_dev_event=AsyncMock(),
            send_input_mode=send_input_mode,
            send_claim_started=AsyncMock(),
            send_claim_ended=AsyncMock(),
            provider=provider,
            dispatch_tool=AsyncMock(return_value=ToolResult(output="{}")),
            focus_manager=focus_manager,
        )

        async def finite_speaker() -> AsyncIterator[bytes]:
            yield b"\xbe\xef"

        handle = await coord.start_input_claim(
            InputClaim(on_mic_frame=AsyncMock(), speaker_source=finite_speaker())
        )
        await asyncio.wait_for(handle.wait_end(), timeout=1.0)

        # The final input_mode call must flip back to assistant_ptt.
        # Note: for very fast speaker sources the claim can exhaust entirely
        # within start_input_claim's wait_drained() — in that case
        # "skill_continuous" is NOT sent (guarded), so "assistant_ptt" may
        # be the only call. For longer-lived sources the sequence is
        # ["skill_continuous", "assistant_ptt"]. Either way the LAST call
        # must be "assistant_ptt".
        modes_sent = [c.args[0] for c in send_input_mode.await_args_list]
        assert modes_sent, "send_input_mode was never called"
        assert modes_sent[-1] == "assistant_ptt", (
            f"expected last mode=assistant_ptt, got {modes_sent}"
        )
