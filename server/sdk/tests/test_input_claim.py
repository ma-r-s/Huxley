"""Tests for the `InputClaim` SDK surface (T1.4 Stage 2 commit 1).

Scope: the dataclasses, enums, and Protocol that skill authors import.
The coordinator/framework-side wiring (tool-dispatch hook, suspend/
resume of the voice provider, mic_router integration) lands in
subsequent commits. Here we verify the SDK shape in isolation:

- `ClaimEndReason` values are stable (used in logs and skill code).
- `InputClaim` is a `SideEffect` subtype and fits in `ToolResult`.
- `ClaimHandle` exposes `cancel()` + `wait_end()` and the framework
  wires them to real callables.
- `StartInputClaim` Protocol has the right shape — a `SkillContext`
  built with a custom callable satisfies it structurally.
- The test-fixture default (`_default_start_input_claim`) returns a
  handle that behaves reasonably without a real coordinator.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest

from huxley_sdk import (
    ClaimEndReason,
    ClaimHandle,
    InputClaim,
    SideEffect,
    ToolResult,
)
from huxley_sdk.testing import make_test_context


class TestClaimEndReason:
    def test_values_stable(self) -> None:
        # Skills branch on these values; they're part of the public surface.
        assert ClaimEndReason.NATURAL.value == "natural"
        assert ClaimEndReason.USER_PTT.value == "user_ptt"
        assert ClaimEndReason.PREEMPTED.value == "preempted"
        assert ClaimEndReason.ERROR.value == "error"

    def test_is_enum(self) -> None:
        # Ensure membership semantics work for skill-side switch/if chains.
        assert ClaimEndReason("natural") is ClaimEndReason.NATURAL
        with pytest.raises(ValueError):
            ClaimEndReason("mystery")


class TestInputClaim:
    async def test_is_side_effect(self) -> None:
        claim = InputClaim(on_mic_frame=AsyncMock())
        assert isinstance(claim, SideEffect)
        assert InputClaim.kind == "input_claim"

    async def test_fits_in_tool_result(self) -> None:
        claim = InputClaim(on_mic_frame=AsyncMock())
        result = ToolResult(output='{"ok": true}', side_effect=claim)
        assert result.side_effect is claim

    async def test_minimum_fields_only_on_mic_frame(self) -> None:
        # speaker_source and on_claim_end are optional.
        claim = InputClaim(on_mic_frame=AsyncMock())
        assert claim.speaker_source is None
        assert claim.on_claim_end is None

    async def test_all_fields(self) -> None:
        on_mic = AsyncMock()
        on_end = AsyncMock()

        async def _speaker_source() -> object:
            yield b"\x00\x00"

        src = _speaker_source()
        claim = InputClaim(
            on_mic_frame=on_mic,
            speaker_source=src,
            on_claim_end=on_end,
        )
        assert claim.on_mic_frame is on_mic
        assert claim.speaker_source is src
        assert claim.on_claim_end is on_end


class TestClaimHandle:
    async def test_cancel_forwards_to_underlying(self) -> None:
        calls: list[None] = []

        def _cancel() -> None:
            calls.append(None)

        async def _wait() -> ClaimEndReason:
            return ClaimEndReason.NATURAL

        handle = ClaimHandle(_cancel=_cancel, _wait_end=_wait)
        handle.cancel()
        handle.cancel()
        # `cancel()` is synchronous on the handle — the underlying callback
        # is just a plain function. We allow multiple calls (the framework's
        # real cancel is idempotent).
        assert len(calls) == 2

    async def test_wait_end_returns_reason(self) -> None:
        async def _wait() -> ClaimEndReason:
            return ClaimEndReason.USER_PTT

        handle = ClaimHandle(_cancel=lambda: None, _wait_end=_wait)
        reason = await handle.wait_end()
        assert reason is ClaimEndReason.USER_PTT

    async def test_wait_end_blocks_until_resolved(self) -> None:
        # Simulates the real coordinator-wired shape: wait_end resolves
        # only when the underlying future/event completes.
        event = asyncio.Event()
        recorded_reason: ClaimEndReason | None = None

        async def _wait() -> ClaimEndReason:
            await event.wait()
            return ClaimEndReason.PREEMPTED

        handle = ClaimHandle(_cancel=lambda: None, _wait_end=_wait)

        async def _waiter() -> None:
            nonlocal recorded_reason
            recorded_reason = await handle.wait_end()

        task = asyncio.create_task(_waiter())
        await asyncio.sleep(0)  # let the waiter enter wait_end
        assert recorded_reason is None, "wait_end returned before resolution"
        event.set()
        await task
        assert recorded_reason is ClaimEndReason.PREEMPTED


class TestSkillContextIntegration:
    async def test_default_start_input_claim_returns_natural_end(self) -> None:
        """Test contexts without a real coordinator get a no-op handle
        that cleanly resolves with NATURAL end — lets skills call
        `start_input_claim` in unit tests without a full framework."""
        ctx = make_test_context()
        handle = await ctx.start_input_claim(InputClaim(on_mic_frame=AsyncMock()))
        assert isinstance(handle, ClaimHandle)
        reason = await handle.wait_end()
        assert reason is ClaimEndReason.NATURAL
        # cancel() on the no-op handle is harmless.
        handle.cancel()

    async def test_can_inject_custom_start_input_claim(self) -> None:
        """A skill test can override `start_input_claim` via dataclasses.replace
        or (for test helpers) object.__setattr__ on the frozen dataclass, the
        same pattern used for `inject_turn` in the timers tests."""
        recorded: list[InputClaim] = []

        async def _record(claim: InputClaim) -> ClaimHandle:
            recorded.append(claim)

            async def _wait() -> ClaimEndReason:
                return ClaimEndReason.USER_PTT

            return ClaimHandle(_cancel=lambda: None, _wait_end=_wait)

        ctx = make_test_context()
        object.__setattr__(ctx, "start_input_claim", _record)

        claim = InputClaim(on_mic_frame=AsyncMock())
        handle = await ctx.start_input_claim(claim)
        assert recorded == [claim]
        reason = await handle.wait_end()
        assert reason is ClaimEndReason.USER_PTT

    def test_skillcontext_still_frozen(self) -> None:
        """Adding fields didn't break dataclass frozenness."""
        ctx = make_test_context()
        with pytest.raises(FrozenInstanceError):
            ctx.logger = None  # type: ignore[misc]
