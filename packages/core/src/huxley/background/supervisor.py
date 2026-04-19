"""TaskSupervisor — owns a pool of named long-running async tasks.

Each task can be configured to restart on crash within a per-hour
budget. When the budget is exhausted, the task is marked permanently
failed: a structured log line + a `dev_event` to the client surface
the failure, and an optional `on_permanent_failure` callback fires.

Skills don't construct this directly — they call
`SkillContext.background_task(name, coro_factory, ...)` which the
framework wires to `TaskSupervisor.start`. The skill receives a
`BackgroundTaskHandle` that exposes `.cancel()` for pre-shutdown
cleanup; otherwise the supervisor's `stop()` cancels everything at
framework shutdown.

See `docs/skills/README.md#supervised-background-tasks` for the
skill-author API and `docs/observability.md` for the event vocabulary.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

import structlog

from huxley_sdk import BackgroundTaskHandle, PermanentFailure

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()

# How long a restart-budget window lasts. After this duration since the
# first restart, the counter resets and the task gets a fresh budget.
_BUDGET_WINDOW_S: float = 3600.0  # 1 hour, matching the param name semantics

# Cap on exponential backoff between crash restarts. Without a cap, a
# task that crashes 8 times would wait 256s — too long to recover from.
_MAX_BACKOFF_S: float = 60.0


class TaskSupervisor:
    """Owns and supervises a pool of named asyncio tasks.

    `start(name, coro_factory, ...)` spawns and tracks. Restart-on-crash
    is opt-in (default True for the long-running-loop case; one-shot
    callers like the timers skill pass `restart_on_crash=False`).
    `stop()` cancels every task and waits for cleanup — call once at
    framework shutdown.

    Names must be unique; re-starting under an existing live name raises
    `ValueError` (callers should `.cancel()` the existing handle first
    or pick a fresh name).
    """

    def __init__(
        self,
        *,
        send_dev_event: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._send_dev_event = send_dev_event
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(
        self,
        name: str,
        coro_factory: Callable[[], Awaitable[None]],
        *,
        restart_on_crash: bool = True,
        max_restarts_per_hour: int = 10,
        on_permanent_failure: Callable[[PermanentFailure], Awaitable[None]] | None = None,
    ) -> BackgroundTaskHandle:
        """Spawn a supervised task. Returns a handle the caller can `.cancel()`."""
        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            raise ValueError(
                f"Background task {name!r} already running — cancel its handle "
                "before starting a new one with the same name."
            )
        task = asyncio.create_task(
            self._run(
                name=name,
                coro_factory=coro_factory,
                restart_on_crash=restart_on_crash,
                max_restarts_per_hour=max_restarts_per_hour,
                on_permanent_failure=on_permanent_failure,
            ),
            name=f"bg:{name}",
        )
        self._tasks[name] = task

        def _cancel() -> None:
            task.cancel()

        return BackgroundTaskHandle(name=name, _cancel=_cancel)

    async def _run(
        self,
        *,
        name: str,
        coro_factory: Callable[[], Awaitable[None]],
        restart_on_crash: bool,
        max_restarts_per_hour: int,
        on_permanent_failure: Callable[[PermanentFailure], Awaitable[None]] | None,
    ) -> None:
        """Run loop: invoke `coro_factory()`, restart on crash within budget."""
        restart_count = 0
        window_start = time.monotonic()
        try:
            while True:
                try:
                    await coro_factory()
                    return  # natural completion — done, no restart
                except asyncio.CancelledError:
                    # Caller-initiated cancel via handle, or supervisor
                    # `stop()`. Propagate so the task is marked cancelled.
                    raise
                except Exception as exc:
                    await logger.aexception(
                        "background.task_crashed",
                        name=name,
                        restart_count=restart_count,
                        will_restart=restart_on_crash,
                    )
                    if not restart_on_crash:
                        return
                    # Reset the budget window if it expired.
                    now = time.monotonic()
                    if now - window_start >= _BUDGET_WINDOW_S:
                        window_start = now
                        restart_count = 0
                    restart_count += 1
                    if restart_count > max_restarts_per_hour:
                        await self._declare_permanent_failure(
                            name=name,
                            exc=exc,
                            restart_count=restart_count,
                            elapsed_s=now - window_start,
                            on_permanent_failure=on_permanent_failure,
                        )
                        return
                    # Exponential backoff: 2, 4, 8, 16, 32, 60, 60, ...
                    backoff_s = min(2.0**restart_count, _MAX_BACKOFF_S)
                    await logger.ainfo(
                        "background.task_restarting",
                        name=name,
                        restart_count=restart_count,
                        backoff_s=backoff_s,
                    )
                    await asyncio.sleep(backoff_s)
        finally:
            # Scrub from the pool whether we exit normally, by cancellation,
            # or via permanent failure. Stop() works on a snapshot so a
            # late pop is safe.
            self._tasks.pop(name, None)

    async def _declare_permanent_failure(
        self,
        *,
        name: str,
        exc: BaseException,
        restart_count: int,
        elapsed_s: float,
        on_permanent_failure: Callable[[PermanentFailure], Awaitable[None]] | None,
    ) -> None:
        """Log, fire dev_event, invoke caller callback (with its own
        try/except so a callback raise can't recurse)."""
        failure = PermanentFailure(
            name=name,
            last_exception_class=type(exc).__name__,
            last_exception_message=str(exc),
            restart_count=restart_count,
            elapsed_s=elapsed_s,
        )
        await logger.aerror(
            "background.task_permanently_failed",
            name=name,
            restart_count=restart_count,
            elapsed_s=elapsed_s,
            exception_class=failure.last_exception_class,
        )
        try:
            await self._send_dev_event(
                "background_task_failed",
                {
                    "name": name,
                    "restart_count": restart_count,
                    "elapsed_s": elapsed_s,
                    "exception_class": failure.last_exception_class,
                    "exception_message": failure.last_exception_message,
                },
            )
        except Exception:
            await logger.aexception("background.dev_event_failed", name=name)
        if on_permanent_failure is None:
            return
        try:
            await on_permanent_failure(failure)
        except Exception:
            # Don't let a misbehaving callback recurse into the supervisor
            # or kill the event loop. Log and move on.
            await logger.aexception(
                "background.on_permanent_failure_callback_raised",
                name=name,
            )

    async def stop(self) -> None:
        """Cancel every supervised task and await cleanup. Idempotent."""
        pending = list(self._tasks.values())
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Authoritative clear (matches the timers skill pattern: tasks
        # cancelled before their first instruction never run their finally).
        self._tasks.clear()
        await logger.ainfo("background.supervisor_stopped", cancelled=len(pending))
