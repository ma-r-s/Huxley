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
