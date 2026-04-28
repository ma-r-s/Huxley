"""Reconnect retry loop for the OpenAI session.

Lives in its own module so the backoff policy is independently testable
with fake connect/announce callables — no Application graph required.

Behavior (see `docs/triage.md` F2):
- Delay schedule: 1s, 3s, 10s, 30s, then 60s floor indefinitely.
- After 3 consecutive failures, fire an audible cue before every
  subsequent attempt so a blind user hears the device is alive + trying.
- Loop exits when `should_continue()` returns False — shutdown, state
  moved away from IDLE (e.g. user pressed PTT), or a successful connect.
"""

from __future__ import annotations

import contextlib
import math
import struct
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()

# Delays (seconds) between reconnect attempts. Indices 0..N-1 are the
# exponential ramp; once exhausted, every subsequent attempt waits
# BACKOFF_FLOOR_S. Tune by changing the tuple — no other code reads its
# length. 1s is intentionally short so a transient blip recovers fast.
BACKOFF_SCHEDULE_S: tuple[float, ...] = (1.0, 3.0, 10.0, 30.0)
BACKOFF_FLOOR_S: float = 60.0

# Attempt count at which the audible cue starts firing (inclusive).
# Counted as "attempts made so far"; the cue plays *before* the Nth+1
# attempt, so the first beep is heard right before attempt 4. Matches
# "after the third failure, announce" from the F2 spec.
AUDIBLE_THRESHOLD: int = 3


def _sleep_for(attempt_idx: int) -> float:
    """Delay before the `attempt_idx`-th attempt (0-indexed)."""
    if attempt_idx < len(BACKOFF_SCHEDULE_S):
        return BACKOFF_SCHEDULE_S[attempt_idx]
    return BACKOFF_FLOOR_S


async def run_reconnect_loop(
    *,
    connect_attempt: Callable[[], Awaitable[bool]],
    announce: Callable[[], Awaitable[None]] | None,
    should_continue: Callable[[], bool],
    sleep: Callable[[float], Awaitable[None]],
) -> int:
    """Retry `connect_attempt` with backoff until it returns True.

    Returns the number of attempts made. `sleep` is injected so tests
    can pass a fake that records delays without real wall-clock time.
    """
    attempts = 0
    while should_continue():
        delay = _sleep_for(attempts)
        if attempts >= AUDIBLE_THRESHOLD and announce is not None:
            with contextlib.suppress(Exception):
                await announce()
        await logger.ainfo(
            "app.reconnect_attempt",
            attempt=attempts + 1,
            delay_s=delay,
        )
        await sleep(delay)
        if not should_continue():
            return attempts

        attempts += 1
        try:
            success = await connect_attempt()
        except Exception:
            await logger.aexception("app.reconnect_attempt_error", attempt=attempts)
            success = False

        if success:
            await logger.ainfo("app.reconnect_succeeded", attempts=attempts)
            return attempts
        await logger.awarning("app.reconnect_failed", attempt=attempts)

    return attempts


def no_signal_tone_pcm(
    *,
    sample_rate_hz: int = 24_000,
    freq_hz: float = 660.0,
    beep_ms: int = 120,
    gap_ms: int = 90,
    beeps: int = 2,
    amplitude: int = 6_000,
) -> bytes:
    """Synthesize a short "no signal" double-beep as PCM16 mono.

    Framework-level default so the retry loop has an audible cue without
    any persona asset wiring. Personas can supply their own message later
    (e.g. a pre-recorded voice file) — that would replace the call site,
    not this helper.
    """
    beep_samples = int(sample_rate_hz * beep_ms / 1000)
    gap_samples = int(sample_rate_hz * gap_ms / 1000)
    omega = 2 * math.pi * freq_hz / sample_rate_hz
    out = bytearray()
    gap = b"\x00\x00" * gap_samples
    for b in range(beeps):
        for i in range(beep_samples):
            # Fade in/out (8ms ramp) to avoid clicks at beep edges.
            ramp = min(
                1.0, i / (sample_rate_hz * 0.008), (beep_samples - i) / (sample_rate_hz * 0.008)
            )
            v = int(math.sin(omega * i) * amplitude * ramp)
            out.extend(struct.pack("<h", v))
        if b < beeps - 1:
            out.extend(gap)
    return bytes(out)
