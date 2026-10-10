"""Read Soloist's audio as Huxley PCM.

Soloist plays into a PulseAudio null sink; the sound server serves that
sink's monitor over TCP (`module-simple-protocol-tcp`) already resampled
to Huxley's wire format, PCM16 mono 24 kHz. Each stream opens its own
connection:

- The null sink runs on its own clock, so reads are paced in real time
  (no `-re`, no ffmpeg).
- With no reader connected the monitor discards audio, so a new
  connection starts at "now": nothing stale from before a pause.

See `dev/entrypoint.sh` for the server side.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SAMPLE_RATE = 24_000
BYTES_PER_SECOND = SAMPLE_RATE * 2
CHUNK_BYTES = BYTES_PER_SECOND // 10  # 100 ms, like the radio skill


class CaptureError(Exception):
    """The PCM source couldn't be reached."""


async def open_capture(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect before playback starts, so the first notes aren't missed."""
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), 3.0)
    except (OSError, TimeoutError) as exc:
        raise CaptureError(f"pcm source {host}:{port} unreachable: {exc}") from exc


async def read_chunks(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """Yield 100 ms chunks until the source closes or drops."""
    while True:
        try:
            yield await reader.readexactly(CHUNK_BYTES)
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                yield exc.partial
            return
        except OSError:  # reset mid-stream: same as closed
            return
