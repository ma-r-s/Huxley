"""Unit tests for `MediaTaskManager` — the audio-stream task slot.

T1.3 ships the manager as a structural extraction; behavior matches the
pre-refactor coordinator (single slot, cancel-and-wait semantics,
no-op DuckingController).
"""

from __future__ import annotations

import asyncio

import pytest

from huxley.turn.arbitration import Decision
from huxley.turn.media_task import DuckingController, MediaTaskManager
from huxley_sdk import Urgency, YieldPolicy


async def _long_running() -> None:
    """A coroutine that runs until cancelled."""
    try:
        await asyncio.sleep(5)
    except asyncio.CancelledError:
        raise


async def _quick() -> None:
    await asyncio.sleep(0)


class TestLifecycle:
    async def test_fresh_manager_is_idle(self) -> None:
        m = MediaTaskManager()
        assert m.task is None
        assert m.is_running is False
        assert m.current_yield_policy is None

    async def test_start_spawns_task(self) -> None:
        m = MediaTaskManager()
        m.start(_long_running())
        assert m.task is not None
        assert m.is_running is True
        await m.stop()

    async def test_stop_cancels_running_task(self) -> None:
        m = MediaTaskManager()
        task = m.start(_long_running())

        await m.stop()

        assert task.done()
        assert task.cancelled()
        assert m.task is None
        assert m.is_running is False

    async def test_stop_is_idempotent(self) -> None:
        m = MediaTaskManager()
        await m.stop()
        await m.stop()

    async def test_stop_after_natural_completion_is_safe(self) -> None:
        m = MediaTaskManager()
        m.start(_quick())
        for _ in range(20):
            if not m.is_running:
                break
            await asyncio.sleep(0.001)

        await m.stop()
        assert m.task is None


class TestYieldPolicy:
    async def test_start_records_yield_policy(self) -> None:
        m = MediaTaskManager()
        m.start(_long_running(), yield_policy=YieldPolicy.YIELD_ABOVE)
        assert m.current_yield_policy is YieldPolicy.YIELD_ABOVE
        await m.stop()

    async def test_stop_clears_yield_policy(self) -> None:
        m = MediaTaskManager()
        m.start(_long_running(), yield_policy=YieldPolicy.YIELD_ABOVE)
        await m.stop()
        assert m.current_yield_policy is None


class TestDecide:
    async def test_decide_when_idle_returns_speak_now(self) -> None:
        m = MediaTaskManager()
        assert m.decide(Urgency.INTERRUPT) is Decision.SPEAK_NOW

    async def test_decide_when_busy_uses_current_policy(self) -> None:
        m = MediaTaskManager()
        m.start(_long_running(), yield_policy=YieldPolicy.YIELD_ABOVE)
        try:
            assert m.decide(Urgency.AMBIENT) is Decision.DROP
            assert m.decide(Urgency.INTERRUPT) is Decision.PREEMPT
            assert m.decide(Urgency.CHIME_DEFER) is Decision.DUCK_CHIME
        finally:
            await m.stop()

    async def test_decide_after_task_finishes_treats_as_idle(self) -> None:
        m = MediaTaskManager()
        m.start(_quick(), yield_policy=YieldPolicy.YIELD_CRITICAL)
        for _ in range(20):
            if not m.is_running:
                break
            await asyncio.sleep(0.001)

        # Task done → arbitration sees idle branch even before stop() called.
        assert m.decide(Urgency.AMBIENT) is Decision.SPEAK_NOW


class TestDuckingController:
    async def test_duck_for_is_noop_stub(self) -> None:
        d = DuckingController()
        # No-op must complete without touching any state.
        await d.duck_for(500)
        await d.duck_for(0)


@pytest.fixture(autouse=True)
async def _cancel_pending_tasks() -> None:
    yield
    # Safety: cancel any lingering tasks from tests that forgot to stop().
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
