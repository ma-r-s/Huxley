"""Play a live stream at its live edge.

Radio servers send a burst of recent audio to every new connection so a
player can start at once: Caracol's sends about 30 s (32 s of audio in the
first 4 s, measured from the Pi on 2026-10-14). ffmpeg's `-re` then played
that burst at real time, so every resume after a press replayed the last
half minute the listener had just heard, and the radio stayed 30 s behind
live. `live_edge` reads as fast as audio arrives, waits until it comes no
faster than about real time (the burst has landed), keeps only the newest
second and paces the rest at real time; a burst mid-stream (ffmpeg
reconnecting) is cut back the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

START_S = 1.0
"""Audio kept when playback (re)starts: the newest second of what arrived."""
MAX_S = 3.0
"""More than this waiting means a burst: cut back to the newest START_S."""
SETTLE_WINDOW_S = 0.25
"""Arrival is measured over windows this long: at most twice real time in
one window means the burst has landed (the live edge)."""
SETTLE_MAX_S = 5.0
"""Start anyway after this long, burst or not."""


async def live_edge(
    source: AsyncIterator[bytes],
    bytes_per_second: int,
    *,
    start_s: float = START_S,
    max_s: float = MAX_S,
    settle_window_s: float = SETTLE_WINDOW_S,
    settle_max_s: float = SETTLE_MAX_S,
    on_skip: Callable[[int, str], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    """Yield `source`'s chunks at real time, from the live edge.

    `on_skip(ms, why)` hears every cut and stall: "start" (the connect
    burst cut), "burst" (one mid-stream), "underrun" (nothing left to play
    mid-stream: `ms` is how long playback waited)."""
    loop = asyncio.get_running_loop()
    queue: deque[bytes] = deque()
    queued = 0
    received = 0
    done = False
    skipped = {"start": 0, "burst": 0}
    start_bytes = int(start_s * bytes_per_second)
    max_bytes = int(max_s * bytes_per_second)

    def keep_newest(limit: int, why: str) -> None:
        nonlocal queued
        while queue and queued - len(queue[0]) >= limit:
            old = queue.popleft()
            queued -= len(old)
            skipped[why] += len(old)

    async def read() -> None:
        nonlocal queued, received, done
        try:
            async for chunk in source:
                queue.append(chunk)
                queued += len(chunk)
                received += len(chunk)
                if queued > max_bytes:
                    keep_newest(start_bytes, "burst")
        finally:
            done = True

    async def report() -> None:
        for why, n in skipped.items():
            if n and on_skip is not None:
                await on_skip(n * 1000 // bytes_per_second, why)
            skipped[why] = 0

    async def settle() -> None:
        """Wait until audio stops pouring in and a start's worth is in
        hand (the cushion against the stream's own jitter: Caracol's
        audio arrives in blocks about 0.8 s apart), then keep the newest."""
        began = loop.time()
        calm = 2 * settle_window_s * bytes_per_second
        while not done and loop.time() - began < settle_max_s:
            before = received
            await asyncio.sleep(settle_window_s)
            if queued >= start_bytes and received - before <= calm:
                break
        keep_newest(start_bytes, "start")
        # A cut while settling is the connect burst, whichever path cut it.
        skipped["start"] += skipped["burst"]
        skipped["burst"] = 0

    reader = asyncio.create_task(read())
    try:
        due: float | None = None
        while True:
            if not queue:
                if done:
                    break
                stalled = loop.time()
                await settle()
                if due is not None and on_skip is not None:
                    await on_skip(int((loop.time() - stalled) * 1000), "underrun")
                due = None
                continue
            if due is None:
                await report()
                due = loop.time()
            elif (delay := due - loop.time()) > 0:
                await asyncio.sleep(delay)
            if skipped["burst"]:
                await report()
            if not queue:
                continue
            chunk = queue.popleft()
            queued -= len(chunk)
            yield chunk
            due += len(chunk) / bytes_per_second
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
