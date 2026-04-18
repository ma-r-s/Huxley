"""Owns the running audio-stream task + the arbitration hook.

Before T1.3 the coordinator held `current_media_task` and
`_stop_current_media_task()` inline. Those responsibilities move here
so T1.4 Stage 1 can add the arbitrate → duck/preempt/hold wiring in one
place.

Behavior in T1.3 is **unchanged** — the manager is a structural extraction
with a single task slot, the same cancel-and-wait semantics, and a
no-op `DuckingController` stub. Stage 1 fills in the duck envelope.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from .arbitration import Decision, arbitrate

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any

    from huxley_sdk import Urgency, YieldPolicy

logger = structlog.get_logger()


class DuckingController:
    """Server-side PCM gain envelope. Stub in T1.3; wired in T1.4 Stage 1.

    `duck_for(ms)` will ramp the output gain down, hold for `ms`
    milliseconds, then ramp back up. Today the method is a no-op — calling
    it is safe, but nothing audibly changes. See
    `docs/io-plane.md#ducking`.
    """

    async def duck_for(self, ms: int) -> None:
        _ = ms
        return None


class MediaTaskManager:
    """Owns a single running `asyncio.Task` for audio-stream playback.

    At most one task is active at a time; starting a new one cancels the
    previous (Stage 1 of T1.4 adds the `arbitrate()` gate before start so
    an in-flight injected turn can `DUCK_CHIME` instead of preempting).

    The manager has no notion of "whose stream" — ownership of the
    SpeakingState flag stays with the coordinator. That keeps this class
    purely about the task lifecycle.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._current_yield: YieldPolicy | None = None
        self.ducking = DuckingController()

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_yield_policy(self) -> YieldPolicy | None:
        """The `yield_policy` of whatever owns the speaker right now.

        Stage-1 `arbitrate()` snapshots this at the decision point. Stays
        `None` in T1.3 — the single `AudioStream` side effect today doesn't
        declare a policy, so the coordinator treats media as "idle" from
        arbitration's perspective.
        """
        return self._current_yield

    def decide(self, urgency: Urgency) -> Decision:
        """Snapshot the current owner's yield policy and run arbitration."""
        return arbitrate(urgency, self._current_yield if self.is_running else None)

    def start(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        yield_policy: YieldPolicy | None = None,
    ) -> asyncio.Task[None]:
        """Spawn a new media task. Caller is responsible for having stopped
        the previous one (via `await stop()`) if the arbitration outcome
        was `PREEMPT`.
        """
        self._task = asyncio.create_task(coro)
        self._current_yield = yield_policy
        return self._task

    async def stop(self) -> None:
        """Cancel the running task and wait for cleanup. Idempotent."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._current_yield = None
