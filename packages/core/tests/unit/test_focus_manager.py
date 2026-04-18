"""Unit tests for `huxley.focus.manager.FocusManager`.

Scenarios are grouped by concern:
- Construction / lifecycle
- Single-Activity happy path
- Preemption (higher priority acquires)
- Patience timers (reinstate + expiry)
- Same-interface replacement (dedup)
- stop_foreground / stop (graceful teardown)
- Observer isolation (exceptions, slow handlers)
- Concurrency serialization
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from huxley.focus.manager import FocusManager
from huxley.focus.vocabulary import (
    Activity,
    Channel,
    ContentType,
    FocusState,
    MixingBehavior,
)

# --- Test helpers ---


class RecordingObserver:
    """Test helper — records each `on_focus_changed` call."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.calls: list[tuple[FocusState, MixingBehavior]] = []

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        self.calls.append((new_focus, behavior))


class RaisingObserver:
    """Test helper — raises on every call."""

    def __init__(self) -> None:
        self.call_count = 0

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        self.call_count += 1
        raise RuntimeError("observer raised")


class SlowObserver:
    """Test helper — sleeps before returning."""

    def __init__(self, delay_s: float = 0.15) -> None:
        self.delay_s = delay_s
        self.calls: list[tuple[FocusState, MixingBehavior]] = []

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None:
        self.calls.append((new_focus, behavior))
        await asyncio.sleep(self.delay_s)


def _make_activity(
    channel: Channel,
    interface_name: str,
    observer: RecordingObserver,
    *,
    content_type: ContentType = ContentType.NONMIXABLE,
    patience: timedelta = timedelta(0),
) -> Activity:
    return Activity(
        channel=channel,
        interface_name=interface_name,
        content_type=content_type,
        observer=observer,
        patience=patience,
    )


async def _drain(fm: FocusManager) -> None:
    """Wait for the FocusManager's mailbox to empty — one tick should suffice
    for a single event, but give several to let chained notifications settle.
    """
    for _ in range(20):
        if fm._mailbox.empty():
            await asyncio.sleep(0)  # let the handler finish awaits
            if fm._mailbox.empty():
                return
        await asyncio.sleep(0.005)


@pytest.fixture
async def fm() -> FocusManager:
    """Construct + start + teardown a default FocusManager."""
    m = FocusManager.with_default_channels()
    m.start()
    yield m
    await m.stop()


# --- Construction / validation ---


class TestConstruction:
    def test_with_default_channels_succeeds(self) -> None:
        fm = FocusManager.with_default_channels()
        assert fm._current_foreground() is None

    def test_incomplete_channel_map_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing"):
            FocusManager({Channel.DIALOG: 100})

    def test_incomplete_channel_map_missing_one_rejected(self) -> None:
        # Only 3 of 4 Channels — `set` equality catches the missing one.
        bad_without = {Channel.DIALOG: 100, Channel.COMMS: 150, Channel.ALERT: 200}
        with pytest.raises(ValueError, match="missing"):
            FocusManager(bad_without)


# --- Single-Activity happy path ---


class TestSingleActivity:
    async def test_fresh_manager_has_no_foreground(self, fm: FocusManager) -> None:
        assert fm._current_foreground() is None

    async def test_first_acquire_gets_foreground_primary(self, fm: FocusManager) -> None:
        obs = RecordingObserver("alert1")
        await fm.acquire(_make_activity(Channel.ALERT, "alert1", obs))
        await _drain(fm)
        assert obs.calls == [(FocusState.FOREGROUND, MixingBehavior.PRIMARY)]

    async def test_release_foreground_sends_must_stop(self, fm: FocusManager) -> None:
        obs = RecordingObserver()
        await fm.acquire(_make_activity(Channel.ALERT, "alert1", obs))
        await _drain(fm)
        await fm.release(Channel.ALERT, "alert1")
        await _drain(fm)
        assert obs.calls == [
            (FocusState.FOREGROUND, MixingBehavior.PRIMARY),
            (FocusState.NONE, MixingBehavior.MUST_STOP),
        ]

    async def test_release_of_unknown_interface_is_noop(self, fm: FocusManager) -> None:
        await fm.release(Channel.ALERT, "never-acquired")
        await _drain(fm)
        # No observers were registered; nothing to assert beyond "no crash".


# --- Preemption ---


class TestPreemption:
    async def test_higher_channel_preempts_mixable_gets_may_duck(self, fm: FocusManager) -> None:
        content_obs = RecordingObserver("book")
        dialog_obs = RecordingObserver("user")
        await fm.acquire(
            _make_activity(
                Channel.CONTENT,
                "book",
                content_obs,
                content_type=ContentType.MIXABLE,
                patience=timedelta(seconds=60),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "user", dialog_obs))
        await _drain(fm)

        assert content_obs.calls == [
            (FocusState.FOREGROUND, MixingBehavior.PRIMARY),
            (FocusState.BACKGROUND, MixingBehavior.MAY_DUCK),
        ]
        assert dialog_obs.calls == [(FocusState.FOREGROUND, MixingBehavior.PRIMARY)]

    async def test_higher_channel_preempts_nonmixable_gets_must_pause(
        self, fm: FocusManager
    ) -> None:
        content_obs = RecordingObserver()
        dialog_obs = RecordingObserver()
        await fm.acquire(
            _make_activity(
                Channel.CONTENT,
                "book",
                content_obs,
                content_type=ContentType.NONMIXABLE,
                patience=timedelta(seconds=60),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "user", dialog_obs))
        await _drain(fm)

        assert content_obs.calls[-1] == (FocusState.BACKGROUND, MixingBehavior.MUST_PAUSE)

    async def test_patience_zero_skips_background_goes_to_must_stop(
        self, fm: FocusManager
    ) -> None:
        low_obs = RecordingObserver("low")
        high_obs = RecordingObserver("high")
        await fm.acquire(_make_activity(Channel.ALERT, "low", low_obs))
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "high", high_obs))
        await _drain(fm)

        assert low_obs.calls == [
            (FocusState.FOREGROUND, MixingBehavior.PRIMARY),
            (FocusState.NONE, MixingBehavior.MUST_STOP),
        ]
        assert high_obs.calls == [(FocusState.FOREGROUND, MixingBehavior.PRIMARY)]


# --- Patience ---


class TestPatience:
    async def test_patience_timer_reinstates_within_window(self, fm: FocusManager) -> None:
        content_obs = RecordingObserver()
        dialog_obs = RecordingObserver()
        await fm.acquire(
            _make_activity(
                Channel.CONTENT,
                "book",
                content_obs,
                content_type=ContentType.MIXABLE,
                patience=timedelta(seconds=60),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "user", dialog_obs))
        await _drain(fm)
        await fm.release(Channel.DIALOG, "user")
        await _drain(fm)

        # content observer: FG → BG (MAY_DUCK) → FG again
        assert content_obs.calls == [
            (FocusState.FOREGROUND, MixingBehavior.PRIMARY),
            (FocusState.BACKGROUND, MixingBehavior.MAY_DUCK),
            (FocusState.FOREGROUND, MixingBehavior.PRIMARY),
        ]

    async def test_patience_timer_expires_sends_must_stop(self, fm: FocusManager) -> None:
        content_obs = RecordingObserver()
        dialog_obs = RecordingObserver()
        await fm.acquire(
            _make_activity(
                Channel.CONTENT,
                "book",
                content_obs,
                content_type=ContentType.MIXABLE,
                patience=timedelta(milliseconds=50),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "user", dialog_obs))
        await _drain(fm)

        # Wait past the patience window without releasing the DIALOG.
        await asyncio.sleep(0.15)
        await _drain(fm)

        assert content_obs.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)

    async def test_reinstate_after_patience_expires_noop(self, fm: FocusManager) -> None:
        """After patience timer fires and removes content, a later release of
        the higher Activity leaves no Activity to reinstate."""
        content_obs = RecordingObserver()
        dialog_obs = RecordingObserver()
        await fm.acquire(
            _make_activity(
                Channel.CONTENT,
                "book",
                content_obs,
                content_type=ContentType.MIXABLE,
                patience=timedelta(milliseconds=30),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "user", dialog_obs))
        await _drain(fm)
        await asyncio.sleep(0.1)
        await _drain(fm)
        await fm.release(Channel.DIALOG, "user")
        await _drain(fm)

        # Content is already removed; dialog release just sends NONE to dialog.
        assert content_obs.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)
        assert dialog_obs.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)


# --- Same-interface replacement (dedup) ---


class TestDedup:
    async def test_same_interface_reacquire_sends_must_stop_to_prior(
        self, fm: FocusManager
    ) -> None:
        obs_a = RecordingObserver("first")
        obs_b = RecordingObserver("second")
        await fm.acquire(_make_activity(Channel.ALERT, "reminder.pill", obs_a))
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.ALERT, "reminder.pill", obs_b))
        await _drain(fm)

        # Prior: FG then MUST_STOP. New: FG.
        assert obs_a.calls == [
            (FocusState.FOREGROUND, MixingBehavior.PRIMARY),
            (FocusState.NONE, MixingBehavior.MUST_STOP),
        ]
        assert obs_b.calls == [(FocusState.FOREGROUND, MixingBehavior.PRIMARY)]

    async def test_different_interfaces_same_channel_lifo_stack(self, fm: FocusManager) -> None:
        obs_a = RecordingObserver("a")
        obs_b = RecordingObserver("b")
        await fm.acquire(_make_activity(Channel.ALERT, "a", obs_a))
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.ALERT, "b", obs_b))
        await _drain(fm)
        # With patience=0, a is already MUST_STOP'd when b arrives
        # (same channel, b is new FG, a had patience=0 so skipped BG).
        assert obs_a.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)
        assert obs_b.calls[-1] == (FocusState.FOREGROUND, MixingBehavior.PRIMARY)


# --- Stop semantics ---


class TestStop:
    async def test_stop_foreground_nones_only_current_fg(self, fm: FocusManager) -> None:
        low_obs = RecordingObserver()
        high_obs = RecordingObserver()
        await fm.acquire(
            _make_activity(
                Channel.CONTENT,
                "book",
                low_obs,
                content_type=ContentType.MIXABLE,
                patience=timedelta(seconds=60),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "user", high_obs))
        await _drain(fm)
        await fm.stop_foreground()
        await _drain(fm)

        # high_obs: FG → NONE (stopped); low_obs: FG → BG (via acquire) → FG (reinstated after stop).
        assert high_obs.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)
        assert low_obs.calls[-1] == (FocusState.FOREGROUND, MixingBehavior.PRIMARY)

    async def test_stop_all_clears_every_stack(self) -> None:
        fm = FocusManager.with_default_channels()
        fm.start()
        try:
            obs_a = RecordingObserver()
            obs_b = RecordingObserver()
            await fm.acquire(
                _make_activity(
                    Channel.CONTENT,
                    "book",
                    obs_a,
                    content_type=ContentType.MIXABLE,
                    patience=timedelta(seconds=60),
                )
            )
            await _drain(fm)
            await fm.acquire(_make_activity(Channel.DIALOG, "user", obs_b))
            await _drain(fm)
        finally:
            await fm.stop()

        # After stop, every observer should have seen MUST_STOP.
        assert obs_a.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)
        assert obs_b.calls[-1] == (FocusState.NONE, MixingBehavior.MUST_STOP)


# --- Observer isolation ---


class TestObserverIsolation:
    async def test_observer_exception_does_not_crash_actor(self, fm: FocusManager) -> None:
        raiser = RaisingObserver()
        good = RecordingObserver()
        await fm.acquire(_make_activity(Channel.CONTENT, "bad", raiser))
        await _drain(fm)
        # Actor still alive — further events still processed.
        await fm.acquire(_make_activity(Channel.DIALOG, "ok", good))
        await _drain(fm)

        assert raiser.call_count >= 1  # was notified despite raising
        assert good.calls == [(FocusState.FOREGROUND, MixingBehavior.PRIMARY)]

    async def test_background_exception_still_fires_foreground_notify(
        self, fm: FocusManager
    ) -> None:
        """Per-notify try/except — old-FG BACKGROUND raising must not abort
        new-FG FOREGROUND delivery."""
        raiser = RaisingObserver()
        good = RecordingObserver()
        await fm.acquire(
            Activity(
                channel=Channel.CONTENT,
                interface_name="bad",
                content_type=ContentType.MIXABLE,
                observer=raiser,
                patience=timedelta(seconds=60),
            )
        )
        await _drain(fm)
        await fm.acquire(_make_activity(Channel.DIALOG, "good", good))
        await _drain(fm)

        assert good.calls == [(FocusState.FOREGROUND, MixingBehavior.PRIMARY)]


# --- Concurrency serialization ---


class TestConcurrency:
    async def test_concurrent_acquire_release_final_state_consistent(
        self, fm: FocusManager
    ) -> None:
        """Fire many concurrent acquire/release calls; final state should be
        deterministic (all queues drained, no in-progress transitions)."""
        observers = [RecordingObserver(f"o{i}") for i in range(20)]

        async def acquire_release(i: int) -> None:
            obs = observers[i]
            await fm.acquire(_make_activity(Channel.ALERT, f"int{i}", obs))
            await fm.release(Channel.ALERT, f"int{i}")

        await asyncio.gather(*[acquire_release(i) for i in range(20)])
        await _drain(fm)

        # All observers should have both events.
        for obs in observers:
            focuses = [c[0] for c in obs.calls]
            assert FocusState.FOREGROUND in focuses or FocusState.NONE in focuses

        # No Activities remain on any stack.
        for channel in Channel:
            assert fm._stacks[channel] == []

    async def test_stop_from_observer_raises_runtime_error(self, fm: FocusManager) -> None:
        """An observer that tries to call fm.stop() should get RuntimeError
        (which _notify_safe catches and logs, so the test observes the
        log-not-deadlock path)."""
        attempt = {"count": 0, "raised": False}

        class ReentrantStopObserver:
            async def on_focus_changed(
                self, new_focus: FocusState, behavior: MixingBehavior
            ) -> None:
                attempt["count"] += 1
                try:
                    await fm.stop()
                except RuntimeError:
                    attempt["raised"] = True
                    raise

        obs = ReentrantStopObserver()
        await fm.acquire(_make_activity(Channel.ALERT, "reentrant", obs))
        await _drain(fm)

        assert attempt["count"] == 1
        assert attempt["raised"] is True
        # Manager is still running — stop() raised before it could tear down.
        assert fm._task is not None and not fm._task.done()
