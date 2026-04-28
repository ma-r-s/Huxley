"""Unit tests for `huxley.background.TaskSupervisor`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from huxley.background.supervisor import TaskSupervisor

if TYPE_CHECKING:
    from huxley_sdk import PermanentFailure


@pytest.fixture
def dev_event() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def supervisor(dev_event: AsyncMock) -> TaskSupervisor:
    # Inject a no-op sleep so the restart-backoff loop doesn't burn real
    # seconds in tests. Production wires `asyncio.sleep` via the default.
    async def _fast_sleep(_duration: float) -> None:
        # A single event-loop tick is enough to let the task state settle
        # between restart attempts without real wall-clock wait.
        await asyncio.sleep(0)

    return TaskSupervisor(send_dev_event=dev_event, sleep=_fast_sleep)


class TestNaturalCompletion:
    async def test_task_runs_and_completes_cleanly(self, supervisor: TaskSupervisor) -> None:
        ran = asyncio.Event()

        async def coro() -> None:
            ran.set()

        handle = supervisor.start("hello", coro, restart_on_crash=False)
        assert handle.name == "hello"

        await asyncio.wait_for(ran.wait(), timeout=1.0)
        # Give the supervisor's `_run` a tick to finish + clean up.
        await asyncio.sleep(0.01)
        # Task removed from pool after natural completion.
        assert "hello" not in supervisor._tasks


class TestRestartOnCrash:
    async def test_crash_then_succeed_completes(
        self, supervisor: TaskSupervisor, dev_event: AsyncMock
    ) -> None:
        attempts = 0
        done = asyncio.Event()

        async def coro() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("first attempt fails")
            done.set()

        # Fixture's `_fast_sleep` is a single event-loop yield, so the
        # real 2s backoff collapses to ~0ms. Test completes instantly.
        supervisor.start("flaky", coro, max_restarts_per_hour=10)
        await asyncio.wait_for(done.wait(), timeout=1.0)
        assert attempts == 2
        # No permanent failure dev_event fired.
        dev_event.assert_not_awaited()

    async def test_repeated_crashes_exhaust_budget_fires_permanent_failure(
        self, supervisor: TaskSupervisor, dev_event: AsyncMock
    ) -> None:
        # Restart budget = 2 → after 3rd crash declare permanent failure.
        # With the fast-sleep fixture, backoffs collapse; test runs in <100ms.
        attempts = 0
        callback_seen: list[PermanentFailure] = []

        async def coro() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError(f"crash {attempts}")

        async def on_failure(failure: PermanentFailure) -> None:
            callback_seen.append(failure)

        supervisor.start(
            "doomed",
            coro,
            max_restarts_per_hour=2,
            on_permanent_failure=on_failure,
        )
        # Await the supervisor's run-loop task directly — it finishes once
        # permanent_failure fires + callback runs. Deterministic; no poll.
        task = supervisor._tasks.get("doomed")
        assert task is not None
        import contextlib

        with contextlib.suppress(Exception):
            await task

        assert callback_seen, "on_permanent_failure callback should have fired"
        failure = callback_seen[0]
        assert failure.name == "doomed"
        assert failure.last_exception_class == "RuntimeError"
        assert failure.restart_count == 3
        assert "doomed" not in supervisor._tasks
        # dev_event also fired with the failure payload.
        dev_event.assert_awaited()
        call_args = dev_event.await_args
        assert call_args is not None
        assert call_args.args[0] == "background_task_failed"
        assert call_args.args[1]["name"] == "doomed"

    async def test_restart_disabled_does_not_restart(
        self, supervisor: TaskSupervisor, dev_event: AsyncMock
    ) -> None:
        attempts = 0

        async def coro() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("boom")

        supervisor.start("oneshot", coro, restart_on_crash=False)
        # Give it a tick to crash + clean up.
        await asyncio.sleep(0.05)
        assert attempts == 1  # no retry
        assert "oneshot" not in supervisor._tasks
        # restart_on_crash=False is NOT a permanent failure — just an
        # unsupervised crash. dev_event must not fire.
        dev_event.assert_not_awaited()


class TestCancellation:
    async def test_handle_cancel_stops_running_task(self, supervisor: TaskSupervisor) -> None:
        started = asyncio.Event()

        async def coro() -> None:
            started.set()
            await asyncio.sleep(3600)

        handle = supervisor.start("longrunning", coro, restart_on_crash=False)
        await asyncio.wait_for(started.wait(), timeout=1.0)

        handle.cancel()
        # Give the supervisor a tick to process the CancelledError + cleanup.
        await asyncio.sleep(0.05)
        assert "longrunning" not in supervisor._tasks


class TestStop:
    async def test_stop_cancels_all_tasks(
        self, supervisor: TaskSupervisor, dev_event: AsyncMock
    ) -> None:
        events: list[asyncio.Event] = []

        async def make_long(ev: asyncio.Event) -> None:
            ev.set()
            await asyncio.sleep(3600)

        for i in range(3):
            ev = asyncio.Event()
            events.append(ev)
            supervisor.start(f"t{i}", lambda e=ev: make_long(e), restart_on_crash=False)

        # Wait for all to be running.
        for ev in events:
            await asyncio.wait_for(ev.wait(), timeout=1.0)
        assert len(supervisor._tasks) == 3

        await supervisor.stop()

        assert supervisor._tasks == {}

    async def test_stop_idempotent_when_empty(self, supervisor: TaskSupervisor) -> None:
        await supervisor.stop()
        await supervisor.stop()  # second call must not raise


class TestNameUniqueness:
    async def test_starting_same_name_while_running_raises(
        self, supervisor: TaskSupervisor
    ) -> None:
        async def coro() -> None:
            await asyncio.sleep(3600)

        handle = supervisor.start("dup", coro, restart_on_crash=False)
        with pytest.raises(ValueError, match="already running"):
            supervisor.start("dup", coro, restart_on_crash=False)
        handle.cancel()
        await asyncio.sleep(0.05)

    async def test_starting_after_natural_completion_with_same_name_succeeds(
        self, supervisor: TaskSupervisor
    ) -> None:
        async def quick() -> None:
            return None

        supervisor.start("recyclable", quick, restart_on_crash=False)
        await asyncio.sleep(0.05)  # let it complete
        # Same name should now be reusable.
        supervisor.start("recyclable", quick, restart_on_crash=False)
        await asyncio.sleep(0.05)
        await supervisor.stop()


class TestPermanentFailureCallbackRobustness:
    async def test_callback_raise_does_not_recurse(
        self, supervisor: TaskSupervisor, dev_event: AsyncMock
    ) -> None:
        """A callback that itself raises must not break the supervisor —
        we log and move on. The on_permanent_failure path is the safety
        net; if it fails too, the task is still considered terminated.
        """
        attempts = 0
        callback_invocations = 0

        async def coro() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("crash")

        async def bad_callback(_: Any) -> None:
            nonlocal callback_invocations
            callback_invocations += 1
            raise RuntimeError("callback also broken")

        supervisor.start(
            "nested_failure",
            coro,
            max_restarts_per_hour=1,
            on_permanent_failure=bad_callback,
        )
        # Await the supervisor's run-loop deterministically.
        task = supervisor._tasks.get("nested_failure")
        assert task is not None
        import contextlib

        with contextlib.suppress(Exception):
            await task

        assert "nested_failure" not in supervisor._tasks
        assert callback_invocations == 1  # called exactly once
        # dev_event still fired before the callback ran.
        dev_event.assert_awaited()
