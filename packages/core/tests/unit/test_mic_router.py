"""Unit tests for `MicRouter` — the mic-frame dispatcher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from huxley.turn.mic_router import MicRouter


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

    async def test_nested_claims_stack_lifo(self, router: MicRouter, default: AsyncMock) -> None:
        """Inner claim release restores the outer claim, not the default."""
        outer = AsyncMock()
        inner = AsyncMock()

        outer_handle = router.claim(outer)
        inner_handle = router.claim(inner)

        inner_handle.release()
        await router.dispatch(b"a")
        assert outer.await_count == 1
        assert inner.await_count == 0

        outer_handle.release()
        await router.dispatch(b"b")
        default.assert_awaited_once_with(b"b")
