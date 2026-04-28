"""Unit tests for `SpeakingState` — the named-owner speaker flag."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from huxley.turn.speaking_state import SpeakingOwner, SpeakingState


@pytest.fixture
def notify() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def state(notify: AsyncMock) -> SpeakingState:
    return SpeakingState(notify=notify)


class TestAcquire:
    async def test_idle_to_owned_fires_notify_true(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.USER)
        notify.assert_awaited_once_with(True)
        assert state.owner is SpeakingOwner.USER
        assert state.is_speaking is True

    async def test_reacquire_same_owner_is_noop(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.USER)
        await state.acquire(SpeakingOwner.USER)
        assert notify.await_count == 1

    async def test_overwrite_owner_does_not_re_notify(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        """Different owner overwrites silently — still speaking to the client."""
        await state.acquire(SpeakingOwner.FACTORY)
        await state.acquire(SpeakingOwner.USER)
        assert notify.await_count == 1
        assert state.owner is SpeakingOwner.USER


class TestRelease:
    async def test_release_matching_owner_fires_notify_false(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.USER)
        notify.reset_mock()

        released = await state.release(SpeakingOwner.USER)

        assert released is True
        notify.assert_awaited_once_with(False)
        assert state.is_speaking is False

    async def test_release_wrong_owner_is_noop(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.USER)
        notify.reset_mock()

        released = await state.release(SpeakingOwner.FACTORY)

        assert released is False
        notify.assert_not_awaited()
        assert state.owner is SpeakingOwner.USER

    async def test_release_when_idle_is_noop(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        released = await state.release(SpeakingOwner.USER)
        assert released is False
        notify.assert_not_awaited()


class TestForceRelease:
    async def test_force_release_when_owned_fires_notify(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.FACTORY)
        notify.reset_mock()

        released = await state.force_release()

        assert released is True
        notify.assert_awaited_once_with(False)

    async def test_force_release_when_idle_is_noop(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        released = await state.force_release()
        assert released is False
        notify.assert_not_awaited()


class TestTransfer:
    async def test_transfer_changes_owner_without_notify(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.FACTORY)
        notify.reset_mock()

        ok = state.transfer(SpeakingOwner.FACTORY, SpeakingOwner.COMPLETION)

        assert ok is True
        assert state.owner is SpeakingOwner.COMPLETION
        notify.assert_not_awaited()

    async def test_transfer_wrong_from_owner_is_noop(
        self, state: SpeakingState, notify: AsyncMock
    ) -> None:
        await state.acquire(SpeakingOwner.USER)

        ok = state.transfer(SpeakingOwner.FACTORY, SpeakingOwner.COMPLETION)

        assert ok is False
        assert state.owner is SpeakingOwner.USER
