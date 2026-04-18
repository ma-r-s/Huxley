"""Turn observers — bridge between FocusManager transitions and coord behavior.

Observers are thin adapters. The FocusManager tells them "your focus
changed"; they translate that into coordinator-side actions via
callbacks passed at construction time. Keeping observers in their own
module (rather than inline in `coordinator.py`) lets them be unit-tested
in isolation against mock callbacks, and it keeps `coordinator.py`
focused on orchestration.

See `docs/architecture.md#focus-management` for how the observers fit
into the wider system.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from huxley.focus.vocabulary import FocusState, MixingBehavior

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from huxley_sdk import AudioStream

logger = structlog.get_logger()


class DialogObserver:
    """Observer for a DIALOG-channel Activity (user / completion / injected turn).

    Fires `on_stop` once, when the Activity receives `NONE` focus
    (interrupted, stopped by the coordinator, or replaced via same-
    interface acquire). FOREGROUND and BACKGROUND are no-ops —
    `send_model_speaking` is driven by actual LLM audio events in the
    coordinator (`on_audio_delta` / `on_audio_done`), not by focus.

    Idempotent: a second `NONE` delivery (shouldn't happen by construction
    but defensive) is ignored.
    """

    def __init__(
        self,
        *,
        interface_name: str,
        on_stop: Callable[[], Awaitable[None]],
    ) -> None:
        self._interface_name = interface_name
        self._on_stop = on_stop
        self._stopped = False

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        _ = behavior  # FOREGROUND/BACKGROUND no-op; see class docstring
        if new_focus is FocusState.NONE and not self._stopped:
            self._stopped = True
            await self._on_stop()


class ContentStreamObserver:
    """Observer for a CONTENT-channel `AudioStream` side effect.

    Owns the pump task that iterates the `AudioStream.factory()` async
    iterator and forwards PCM chunks to `send_audio`.

    Lifecycle:

    - `FOREGROUND` → spawn the pump task. Duplicate FOREGROUND is a
      no-op (still the same owner).
    - `BACKGROUND MAY_DUCK` → log `focus.duck_not_implemented` and
      treat as `MUST_PAUSE` (cancel the task). PCM gain envelope is a
      post-Stage-1 follow-up; until then, MAY_DUCK falls back to pause
      per the AVS contract ("may duck; if ducking is not possible, the
      Activity must pause").
    - `BACKGROUND MUST_PAUSE` → cancel the task.
    - `NONE MUST_STOP` → cancel the task. If the task had already
      reached natural EOF before the NONE delivery, fire
      `on_natural_completion` (used by audiobook completion turns).

    The pump calls `on_eof` when it finishes naturally — the
    coordinator wires this to `focus_manager.release(channel,
    interface_name)` so a NONE delivery follows and the observer can
    distinguish "cancelled" from "completed."
    """

    def __init__(
        self,
        *,
        interface_name: str,
        stream: AudioStream,
        send_audio: Callable[[bytes], Awaitable[None]],
        on_eof: Callable[[], Awaitable[None]] | None = None,
        on_natural_completion: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._interface_name = interface_name
        self._stream = stream
        self._send_audio = send_audio
        self._on_eof = on_eof
        self._on_natural_completion = on_natural_completion
        self._task: asyncio.Task[None] | None = None
        self._natural_eof = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        """The pump task. Exposed for tests + for the coord to await shutdown."""
        return self._task

    @property
    def interface_name(self) -> str:
        """The stable `interface_name` this observer was constructed with."""
        return self._interface_name

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        match new_focus:
            case FocusState.FOREGROUND:
                await self._spawn_pump_if_idle()
            case FocusState.BACKGROUND:
                if behavior is MixingBehavior.MAY_DUCK:
                    await logger.ainfo(
                        "focus.duck_not_implemented",
                        interface=self._interface_name,
                        fallback="must_pause",
                    )
                await self._cancel_pump()
            case FocusState.NONE:
                await self._cancel_pump()
                if self._natural_eof and self._on_natural_completion is not None:
                    await self._on_natural_completion()

    async def _spawn_pump_if_idle(self) -> None:
        if self._task is not None and not self._task.done():
            return  # already pumping — duplicate FOREGROUND
        self._natural_eof = False
        self._task = asyncio.create_task(
            self._pump(),
            name=f"content_pump:{self._interface_name}",
        )

    async def _pump(self) -> None:
        try:
            async for chunk in self._stream.factory():
                await self._send_audio(chunk)
            self._natural_eof = True
            if self._on_eof is not None:
                await self._on_eof()
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception(
                "content_stream_pump_failed",
                interface=self._interface_name,
            )

    async def _cancel_pump(self) -> None:
        task = self._task
        if task is not None and not task.done():
            # Self-cancel guard: if an observer callback chain ends up
            # delivering NONE back to this observer while still executing
            # inside `_pump` (e.g., `on_eof` → caller routes through a
            # release that triggers NONE notification), `task is
            # current_task()`. Python doesn't deadlock here — CancelledError
            # fires at `await task` and is suppressed, the task still
            # completes — but we'd have called `task.cancel()` on a task
            # that's finishing naturally, leaving it in a "cancelling"
            # state briefly and potentially interacting with other
            # cancel-aware constructs (`asyncio.shield`, etc.) in
            # surprising ways. Skip the cancel-await dance; the task is
            # already on its way out, let it exit naturally.
            if task is asyncio.current_task():
                self._task = None
                return
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None
