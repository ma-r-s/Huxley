"""Unit tests for the reconnect retry loop.

Covers the F2 behavior: transient failure is recovered, not permanently
bricked; backoff delays follow the documented schedule; audible cue
fires only from attempt 4 onward; the loop exits on `should_continue`
flipping to False (e.g. user pressed PTT mid-retry).
"""

from __future__ import annotations

import pytest

from huxley.reconnect import (
    AUDIBLE_THRESHOLD,
    BACKOFF_FLOOR_S,
    BACKOFF_SCHEDULE_S,
    no_signal_tone_pcm,
    run_reconnect_loop,
)


class _FakeSleep:
    """Records every delay it was asked to sleep without actually sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.mark.asyncio
async def test_recovers_on_first_attempt_after_transient_failure() -> None:
    sleep = _FakeSleep()
    results = [False, False, True]
    announce_calls = 0

    async def connect() -> bool:
        return results.pop(0)

    async def announce() -> None:
        nonlocal announce_calls
        announce_calls += 1

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=announce,
        should_continue=lambda: True,
        sleep=sleep,
    )

    assert attempts == 3
    # 1s + 3s + 10s — the first three entries of the backoff schedule.
    assert sleep.delays == [1.0, 3.0, 10.0]
    # AUDIBLE_THRESHOLD=3; cue fires *before* attempt with attempts_done >= 3.
    # Here success came on attempt 3, so cue never fires (still below threshold
    # at each attempt's pre-check: 0, 1, 2).
    assert announce_calls == 0


@pytest.mark.asyncio
async def test_audible_cue_fires_from_attempt_four_onward() -> None:
    sleep = _FakeSleep()
    # Fail 4 times, succeed on 5th.
    results = [False, False, False, False, True]
    announce_calls = 0

    async def connect() -> bool:
        return results.pop(0)

    async def announce() -> None:
        nonlocal announce_calls
        announce_calls += 1

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=announce,
        should_continue=lambda: True,
        sleep=sleep,
    )

    assert attempts == 5
    # Attempts 1-3: no cue (attempts_done = 0, 1, 2 at pre-check). Attempts
    # 4 & 5: cue fires (attempts_done = 3, 4 at pre-check). Matches the spec:
    # "after the third failure, fire an audible cue".
    assert announce_calls == 2


@pytest.mark.asyncio
async def test_backoff_floor_applies_after_schedule_exhausted() -> None:
    sleep = _FakeSleep()
    # Fail len(schedule)+2 times then succeed — so the last 3 attempts
    # should use the floor.
    n_fails = len(BACKOFF_SCHEDULE_S) + 2
    results = [False] * n_fails + [True]
    announce_was_none = True

    async def connect() -> bool:
        return results.pop(0)

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=None,
        should_continue=lambda: True,
        sleep=sleep,
    )

    assert attempts == n_fails + 1
    # Expected delays: the full schedule, then floor repeated for the
    # attempts beyond it.
    expected = list(BACKOFF_SCHEDULE_S) + [BACKOFF_FLOOR_S] * 3
    assert sleep.delays == expected
    assert announce_was_none


@pytest.mark.asyncio
async def test_exits_when_should_continue_returns_false_before_first_attempt() -> None:
    sleep = _FakeSleep()

    async def connect() -> bool:
        pytest.fail("connect should never be called when should_continue starts False")
        return True

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=None,
        should_continue=lambda: False,
        sleep=sleep,
    )

    assert attempts == 0
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_exits_mid_loop_when_should_continue_flips_during_sleep() -> None:
    sleep = _FakeSleep()
    flip_after_n_sleeps = 2

    calls = 0

    async def connect() -> bool:
        nonlocal calls
        calls += 1
        return False

    def should_continue() -> bool:
        return len(sleep.delays) < flip_after_n_sleeps

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=None,
        should_continue=should_continue,
        sleep=sleep,
    )

    # Loop: sleep #1 → should_continue() True → connect → sleep #2 → after
    # sleep, should_continue() False → return with attempts=1.
    assert attempts == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_connect_exception_treated_as_failure_and_retried() -> None:
    sleep = _FakeSleep()
    call = 0

    async def connect() -> bool:
        nonlocal call
        call += 1
        if call == 1:
            msg = "socket.gaierror would land here"
            raise RuntimeError(msg)
        return True

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=None,
        should_continue=lambda: True,
        sleep=sleep,
    )

    assert attempts == 2
    assert call == 2


@pytest.mark.asyncio
async def test_announce_exception_does_not_abort_loop() -> None:
    sleep = _FakeSleep()
    # Fail enough times to cross AUDIBLE_THRESHOLD, then succeed.
    results = [False, False, False, True]

    async def connect() -> bool:
        return results.pop(0)

    async def announce() -> None:
        msg = "imagine the client WS dropped between send_audio and here"
        raise RuntimeError(msg)

    attempts = await run_reconnect_loop(
        connect_attempt=connect,
        announce=announce,
        should_continue=lambda: True,
        sleep=sleep,
    )

    assert attempts == 4


def test_no_signal_tone_is_valid_pcm16() -> None:
    pcm = no_signal_tone_pcm()
    assert len(pcm) > 0
    # PCM16 = 2 bytes per sample; must be even length.
    assert len(pcm) % 2 == 0
    # Default params: two 120ms beeps + one 90ms gap at 24kHz = roughly
    # (2*120 + 90)*24 samples. Allow some rounding.
    expected_samples = (2 * 120 + 90) * 24
    actual_samples = len(pcm) // 2
    assert abs(actual_samples - expected_samples) < 100


def test_audible_threshold_matches_spec() -> None:
    # Guard rail: the F2 triage spec and the `no_signal_tone_pcm` docstring
    # both reference "after the third failure". If someone changes this to
    # 1 they've re-opened F2's footgun (cue on every blip is noise).
    assert AUDIBLE_THRESHOLD == 3
