"""FocusManager — serialized arbitrator over a single audio resource.

Single-task actor pattern: one `asyncio.Task` drains an `asyncio.Queue`
of events. All state mutation happens inside that task; callers enqueue
via `acquire` / `release` / `stop_foreground` / `stop`, never mutate
directly. Races between concurrent callers are impossible by construction.

Corresponds to AVS Focus Management with one deliberate flip: patience
belongs to the incumbent (being-displaced) Activity, not the acquiring
Activity. See `docs/io-plane.md#patience-attribution`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeAlias

import structlog

from huxley.focus.vocabulary import (
    CHANNEL_PRIORITY,
    Activity,
    Channel,
    FocusState,
    MixingBehavior,
    mixing_for_background,
)

logger = structlog.get_logger()

_SLOW_OBSERVER_MS = 100


# --- Event ADT ---


@dataclass(frozen=True, slots=True)
class Acquire:
    """A skill / coord wants an Activity to claim focus on its channel."""

    activity: Activity


@dataclass(frozen=True, slots=True)
class Release:
    """A skill / coord is giving up its Activity."""

    channel: Channel
    interface_name: str


@dataclass(frozen=True, slots=True)
class PatienceExpired:
    """Patience timer for a backgrounded Activity fired — emitted by the
    timer callback onto the same mailbox so serialization holds."""

    channel: Channel
    interface_name: str


@dataclass(frozen=True, slots=True)
class StopForeground:
    """Force the current FOREGROUND Activity to release."""


@dataclass(frozen=True, slots=True)
class StopAll:
    """Force every Activity on every channel to release, highest priority first."""


FocusEvent: TypeAlias = Acquire | Release | PatienceExpired | StopForeground | StopAll


# --- Manager ---


class FocusManager:
    """Arbitrator for one audio resource (e.g. the speaker).

    All public methods enqueue events; none mutate state directly.
    A single task (`_run`) drains the mailbox, processing one event at
    a time. Observer notifications fire from within that task — observer
    code therefore sees a consistent `FocusManager` snapshot.
    """

    @classmethod
    def with_default_channels(cls) -> FocusManager:
        """Factory — construct with the canonical 4-channel priority map."""
        return cls(dict(CHANNEL_PRIORITY))

    def __init__(self, priorities: dict[Channel, int]) -> None:
        """Construct with an explicit `Channel -> priority` map.

        Every `Channel` enum value must appear in the map. Missing or extra
        keys raise `ValueError` immediately — fail-fast at construction.
        """
        if set(priorities) != set(Channel):
            missing = set(Channel) - set(priorities)
            extra = set(priorities) - set(Channel)
            raise ValueError(
                "FocusManager priorities must cover every Channel; "
                f"missing={missing} extra={extra}"
            )
        self._priorities: dict[Channel, int] = dict(priorities)
        self._stacks: dict[Channel, list[Activity]] = {c: [] for c in Channel}
        self._patience_timers: dict[tuple[Channel, str], asyncio.TimerHandle] = {}
        self._mailbox: asyncio.Queue[FocusEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Spawn the actor task. Must be called from within a running event loop.

        Sync — just creates the task. No I/O happens until the first event
        is enqueued. Idempotent: raises if already started (caller bug).
        """
        if self._task is not None:
            raise RuntimeError("FocusManager already started")
        self._task = asyncio.create_task(self._run(), name="focus_manager")

    async def stop(self) -> None:
        """Drain pending events, issue StopAll, then cancel the actor.

        Must NOT be called from within an observer — the actor task
        awaiting itself would deadlock. Guarded.

        Sequence:
        1. Re-entrance check (raise if called from within the actor).
        2. Cancel pending patience timers so no stray `PatienceExpired`
           events hit the mailbox after we start teardown.
        3. Enqueue `StopAll`; wait for `Queue.join()` so every pending
           event (including the StopAll) has been fully processed —
           observers get their terminal MUST_STOP notifications.
        4. Cancel the actor task.
        """
        if self._task is not None and asyncio.current_task() is self._task:
            raise RuntimeError("cannot stop FocusManager from within its actor task")
        for handle in self._patience_timers.values():
            handle.cancel()
        self._patience_timers.clear()
        if self._task is not None:
            await self._mailbox.put(StopAll())
            await self._mailbox.join()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # --- Public API (enqueue only) ---

    async def acquire(self, activity: Activity) -> None:
        """Request focus for `activity`. Delivered asynchronously via its observer."""
        await self._mailbox.put(Acquire(activity))

    async def release(self, channel: Channel, interface_name: str) -> None:
        """Release the Activity identified by `(channel, interface_name)`."""
        await self._mailbox.put(Release(channel, interface_name))

    async def stop_foreground(self) -> None:
        """Force the current FOREGROUND Activity to NONE/MUST_STOP."""
        await self._mailbox.put(StopForeground())

    # --- Actor loop ---

    async def _run(self) -> None:
        while True:
            event = await self._mailbox.get()
            try:
                await self._handle(event)
            except Exception:
                await logger.aexception(
                    "focus.event_failed",
                    event_type=type(event).__name__,
                )
            finally:
                # Match get() with task_done() so stop() can Queue.join() to
                # wait for the StopAll event (and anything queued before it)
                # to be fully processed before the actor task is cancelled.
                self._mailbox.task_done()

    async def _handle(self, event: FocusEvent) -> None:
        match event:
            case Acquire(activity):
                await self._handle_acquire(activity)
            case Release(channel=ch, interface_name=name):
                await self._handle_release(ch, name)
            case PatienceExpired(channel=ch, interface_name=name):
                await self._handle_patience_expired(ch, name)
            case StopForeground():
                await self._handle_stop_foreground()
            case StopAll():
                await self._handle_stop_all()

    async def _handle_acquire(self, activity: Activity) -> None:
        prev_fg = self._current_foreground()

        # Same-interface replacement: if an Activity with this
        # (channel, interface_name) already exists, remove it. If it was
        # the foreground, skip the BACKGROUND phase — replacements don't
        # grant a grace period.
        displaced = self._remove_by_interface(activity.channel, activity.interface_name)
        if displaced is prev_fg:
            prev_fg = None
        # Cancel any patience timer for this interface — fresh acquire
        # means the prior patience (if any) no longer applies.
        self._cancel_patience_timer(activity.channel, activity.interface_name)

        self._stacks[activity.channel].append(activity)
        new_fg = self._current_foreground()

        await logger.ainfo(
            "focus.acquire",
            channel=activity.channel.value,
            interface=activity.interface_name,
            content_type=activity.content_type.value,
            patience_ms=int(activity.patience.total_seconds() * 1000),
            became_foreground=new_fg is activity,
            displaced=displaced.interface_name if displaced is not None else None,
        )

        # Outgoing FG transition.
        if prev_fg is not None and prev_fg is not new_fg:
            if prev_fg.patience > timedelta(0):
                behavior = mixing_for_background(prev_fg.content_type)
                await self._notify_safe(prev_fg, FocusState.BACKGROUND, behavior)
                self._start_patience_timer(prev_fg)
            else:
                # patience=0 — skip BACKGROUND, go straight to NONE + remove.
                self._remove_by_interface(prev_fg.channel, prev_fg.interface_name)
                await self._notify_safe(prev_fg, FocusState.NONE, MixingBehavior.MUST_STOP)

        # Incoming FG transition.
        if new_fg is not None and new_fg is not prev_fg:
            # If this Activity was previously backgrounded (patience timer
            # running), cancel it before promoting.
            self._cancel_patience_timer(new_fg.channel, new_fg.interface_name)
            await self._notify_safe(new_fg, FocusState.FOREGROUND, MixingBehavior.PRIMARY)

        # Replaced Activity (different from the outgoing FG case) always
        # gets MUST_STOP — it's gone from every stack.
        if displaced is not None and displaced is not prev_fg:
            await self._notify_safe(displaced, FocusState.NONE, MixingBehavior.MUST_STOP)

    async def _handle_release(self, channel: Channel, interface_name: str) -> None:
        prev_fg = self._current_foreground()
        removed = self._remove_by_interface(channel, interface_name)
        if removed is None:
            # Already gone (race with PatienceExpired / acquire's replace / stop_all).
            return
        self._cancel_patience_timer(channel, interface_name)
        new_fg = self._current_foreground()

        await logger.ainfo(
            "focus.release",
            channel=channel.value,
            interface=interface_name,
            was_foreground=removed is prev_fg,
        )

        if removed is prev_fg:
            await self._notify_safe(removed, FocusState.NONE, MixingBehavior.MUST_STOP)
            if new_fg is not None:
                self._cancel_patience_timer(new_fg.channel, new_fg.interface_name)
                await self._notify_safe(new_fg, FocusState.FOREGROUND, MixingBehavior.PRIMARY)
        else:
            # Released a BACKGROUND Activity — no FG change. Tell its
            # observer NONE so it can release resources.
            await self._notify_safe(removed, FocusState.NONE, MixingBehavior.MUST_STOP)

    async def _handle_patience_expired(self, channel: Channel, interface_name: str) -> None:
        activity = self._find(channel, interface_name)
        if activity is None:
            # Already removed — no-op. Expected when acquire/release raced.
            return
        self._remove_by_interface(channel, interface_name)
        self._cancel_patience_timer(channel, interface_name)
        await logger.ainfo(
            "focus.patience_expired",
            channel=channel.value,
            interface=interface_name,
        )
        await self._notify_safe(activity, FocusState.NONE, MixingBehavior.MUST_STOP)

    async def _handle_stop_foreground(self) -> None:
        fg = self._current_foreground()
        if fg is None:
            return
        self._remove_by_interface(fg.channel, fg.interface_name)
        self._cancel_patience_timer(fg.channel, fg.interface_name)
        new_fg = self._current_foreground()
        await self._notify_safe(fg, FocusState.NONE, MixingBehavior.MUST_STOP)
        if new_fg is not None:
            self._cancel_patience_timer(new_fg.channel, new_fg.interface_name)
            await self._notify_safe(new_fg, FocusState.FOREGROUND, MixingBehavior.PRIMARY)

    async def _handle_stop_all(self) -> None:
        # Highest-priority (lowest number) channels first so more-important
        # observers see MUST_STOP before less-important ones.
        for channel in sorted(self._priorities, key=lambda c: self._priorities[c]):
            stack = self._stacks[channel]
            while stack:
                activity = stack.pop()
                self._cancel_patience_timer(activity.channel, activity.interface_name)
                await self._notify_safe(activity, FocusState.NONE, MixingBehavior.MUST_STOP)

    # --- Helpers ---

    async def _notify_safe(
        self, activity: Activity, focus: FocusState, behavior: MixingBehavior
    ) -> None:
        """Invoke one observer, isolating exceptions and logging slow handlers.

        Each notify has its own try/except so one observer's bug doesn't
        abort the wider transition (e.g., new-FG PRIMARY still fires even
        if old-FG BACKGROUND raised).
        """
        started = time.monotonic()
        try:
            await activity.observer.on_focus_changed(focus, behavior)
        except Exception:
            await logger.aexception(
                "focus.observer_failed",
                interface=activity.interface_name,
                channel=activity.channel.value,
                focus=focus.value,
                behavior=behavior.value,
            )
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms > _SLOW_OBSERVER_MS:
            await logger.awarning(
                "focus.observer_slow",
                interface=activity.interface_name,
                elapsed_ms=int(elapsed_ms),
            )
        await logger.ainfo(
            "focus.change",
            interface=activity.interface_name,
            channel=activity.channel.value,
            new_state=focus.value,
            behavior=behavior.value,
        )

    def _start_patience_timer(self, activity: Activity) -> None:
        """Schedule PatienceExpired to fire via the mailbox (not direct callback).

        Routing through the mailbox keeps the serialization invariant —
        patience expiry is just another event, processed in order.
        """
        loop = asyncio.get_event_loop()
        key = (activity.channel, activity.interface_name)
        channel = activity.channel
        interface_name = activity.interface_name

        def _fire() -> None:
            self._mailbox.put_nowait(PatienceExpired(channel, interface_name))
            self._patience_timers.pop(key, None)

        self._patience_timers[key] = loop.call_later(activity.patience.total_seconds(), _fire)

    def _cancel_patience_timer(self, channel: Channel, interface_name: str) -> None:
        handle = self._patience_timers.pop((channel, interface_name), None)
        if handle is not None:
            handle.cancel()

    def _current_foreground(self) -> Activity | None:
        """Return the current FOREGROUND Activity, or `None` if idle.

        "Current FG" = top of the highest-priority non-empty channel stack.
        """
        for channel in sorted(self._priorities, key=lambda c: self._priorities[c]):
            stack = self._stacks[channel]
            if stack:
                return stack[-1]
        return None

    def _remove_by_interface(self, channel: Channel, interface_name: str) -> Activity | None:
        stack = self._stacks[channel]
        for i, a in enumerate(stack):
            if a.interface_name == interface_name:
                del stack[i]
                return a
        return None

    def _find(self, channel: Channel, interface_name: str) -> Activity | None:
        for a in self._stacks[channel]:
            if a.interface_name == interface_name:
                return a
        return None
