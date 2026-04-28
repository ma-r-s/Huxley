"""Unit tests for `MicRouter` — the mic-frame dispatcher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from huxley.turn.mic_router import MicAlreadyClaimedError, MicRouter


@pytest.fixture
def default() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def router(default: AsyncMock) -> MicRouter:
    return MicRouter(default_handler=default)


class TestDefaultDispatch:
    async def test_dispatch_goes_to_default_handler(
        self, router: MicRouter, default: AsyncMock
    ) -> None:
        await router.dispatch(b"pcm")
        default.assert_awaited_once_with(b"pcm")

    async def test_is_claimed_false_when_default(self, router: MicRouter) -> None:
        assert router.is_claimed is False


class TestClaim:
    async def test_claim_routes_to_new_handler(
        self, router: MicRouter, default: AsyncMock
    ) -> None:
        claim = AsyncMock()
        router.claim(claim)

        await router.dispatch(b"pcm")

        claim.assert_awaited_once_with(b"pcm")
        default.assert_not_awaited()

    async def test_is_claimed_true_when_active(self, router: MicRouter) -> None:
        router.claim(AsyncMock())
        assert router.is_claimed is True

    async def test_release_restores_default(self, router: MicRouter, default: AsyncMock) -> None:
        claim = AsyncMock()
        handle = router.claim(claim)

        handle.release()
        await router.dispatch(b"pcm")

        default.assert_awaited_once_with(b"pcm")
        claim.assert_not_awaited()
        assert router.is_claimed is False

    async def test_release_is_idempotent(self, router: MicRouter, default: AsyncMock) -> None:
        handle = router.claim(AsyncMock())
        handle.release()
        handle.release()  # second release must not crash

        await router.dispatch(b"pcm")
        default.assert_awaited_once_with(b"pcm")

    async def test_second_claim_raises(self, router: MicRouter) -> None:
        """At-most-one-claim invariant: a second `claim()` while one is
        active raises. Closes the race the Stage 2 critic flagged where
        a direct-entry `start_input_claim` firing concurrently with a
        tool-dispatched `InputClaim` side effect would capture the
        other claim's handler as `_previous` and leak on release.
        """
        first = AsyncMock()
        second = AsyncMock()
        router.claim(first)

        with pytest.raises(MicAlreadyClaimedError):
            router.claim(second)

    async def test_release_then_claim_works(self, router: MicRouter, default: AsyncMock) -> None:
        """After releasing a claim, a new claim is allowed. Covers the
        sequential flow of a voice memo finishing followed by an
        incoming call — each is fine in isolation."""
        first = AsyncMock()
        second = AsyncMock()

        first_handle = router.claim(first)
        first_handle.release()
        second_handle = router.claim(second)

        await router.dispatch(b"pcm")
        second.assert_awaited_once_with(b"pcm")
        first.assert_not_awaited()
        default.assert_not_awaited()

        second_handle.release()
        assert router.is_claimed is False
