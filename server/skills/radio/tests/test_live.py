"""live_edge: a radio server's connect burst is skipped, never replayed.

Time is compressed: 1000 bytes per second, so a 10-byte chunk is 10 ms and
a 300-chunk burst stands for Caracol's 30 s (measured 2026-10-14: 32 s of
audio in the first 4 s of a connection).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from huxley_skill_radio.live import live_edge

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

BPS = 1000
CHUNK = 10  # bytes: 10 ms
KNOBS = {"start_s": 0.05, "max_s": 0.15, "settle_window_s": 0.03, "settle_max_s": 0.5}


def chunk(i: int) -> bytes:
    return i.to_bytes(4, "big") + bytes(CHUNK - 4)


def index(c: bytes) -> int:
    return int.from_bytes(c[:4], "big")


async def run(source: AsyncIterator[bytes]) -> tuple[list[int], list[tuple[int, str]], float]:
    skips: list[tuple[int, str]] = []

    async def on_skip(ms: int, why: str) -> None:
        skips.append((ms, why))

    loop = asyncio.get_running_loop()
    began = loop.time()
    out = [index(c) async for c in live_edge(source, BPS, on_skip=on_skip, **KNOBS)]
    return out, skips, loop.time() - began


async def live(first: int, n: int) -> AsyncIterator[bytes]:
    for i in range(first, first + n):
        await asyncio.sleep(CHUNK / BPS)
        yield chunk(i)


async def test_a_connect_burst_is_skipped_to_its_newest_part() -> None:
    async def source() -> AsyncIterator[bytes]:
        for i in range(300):  # the burst: 3 "seconds" at once
            yield chunk(i)
        await asyncio.sleep(0.05)
        async for c in live(300, 60):
            yield c

    out, skips, _ = await run(source())
    assert out[0] >= 295  # never the old part of the burst
    assert out == list(range(out[0], 360))  # then continuous, to the end
    assert [why for _, why in skips] == ["start"]
    assert skips[0][0] >= 2900


async def test_a_stream_with_no_burst_plays_whole() -> None:
    out, skips, _ = await run(live(0, 50))
    assert out == list(range(50))
    assert skips == []


async def test_a_burst_mid_stream_jumps_to_live_and_keeps_pace() -> None:
    async def source() -> AsyncIterator[bytes]:
        async for c in live(0, 30):
            yield c
        for i in range(30, 130):  # ffmpeg reconnected: another burst
            yield chunk(i)
        async for c in live(130, 30):
            yield c

    out, skips, elapsed = await run(source())
    assert out == sorted(out)
    assert out[-1] == 159
    assert len(out) < 130  # most of the burst was cut
    assert "burst" in [why for _, why in skips]
    # Paced at real time: nothing poured downstream at burst speed.
    assert elapsed >= 0.8 * len(out) * CHUNK / BPS


async def test_closing_the_stream_stops_the_source() -> None:
    stopped = asyncio.Event()

    async def source() -> AsyncIterator[bytes]:
        try:
            i = 0
            while True:
                await asyncio.sleep(CHUNK / BPS)
                yield chunk(i)
                i += 1
        finally:
            stopped.set()

    stream = live_edge(source(), BPS, **KNOBS)
    got = [await stream.__anext__() for _ in range(3)]
    assert [index(c) for c in got] == sorted(index(c) for c in got)
    await stream.aclose()
    await asyncio.wait_for(stopped.wait(), 1)


async def test_the_end_of_a_stream_drains_what_is_left() -> None:
    async def source() -> AsyncIterator[bytes]:
        async for c in live(0, 5):
            yield c

    out, _, _ = await run(source())
    assert out == list(range(5))
