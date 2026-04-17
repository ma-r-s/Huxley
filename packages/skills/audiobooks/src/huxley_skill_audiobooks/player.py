"""Audiobook playback via ffmpeg subprocess.

Stateless helper that exposes two operations:

- `probe(path)` — ffprobe metadata lookup (duration, chapters, tags)
- `stream(path, start_position)` — async generator that spawns ffmpeg,
  yields PCM16 chunks at realtime pace, and tears down the subprocess
  cleanly on cancellation

Each call to `stream()` is an independent subprocess. There's no shared
mutable state, no locks, no pause/resume semantics. Turn sequencing is
owned by the `TurnCoordinator` — see `docs/turns.md`. "Pause" and
"rewind" are modelled as new `stream()` invocations from a different
start position; the prior stream is cancelled by the coordinator.

See `docs/decisions.md` — _"Audiobook audio streams through the
WebSocket, not local playback"_ — for why we decode to PCM16 24 kHz mono
and forward the bytes to the same audio channel as OpenAI model audio.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

SAMPLE_RATE = 24_000
BYTES_PER_SAMPLE = 2  # PCM16 little-endian
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE  # 48_000
CHUNK_DURATION_S = 0.1  # 100 ms chunks
CHUNK_SIZE = int(BYTES_PER_SECOND * CHUNK_DURATION_S)  # 4800 bytes


class PlayerError(Exception):
    """Raised when the audiobook player fails to probe or decode a file."""


class AudiobookPlayer:
    """Stateless ffmpeg wrapper. Probe + stream, nothing else."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path

    async def probe(self, path: str | Path) -> dict[str, Any]:
        """Return ffprobe metadata (duration, chapters, tags) for `path`."""
        proc = await asyncio.create_subprocess_exec(
            self._ffprobe_path,
            "-v",
            "quiet",
            "-show_format",
            "-show_chapters",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            msg = f"ffprobe failed for {path}"
            raise PlayerError(msg)
        try:
            data: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError as exc:
            msg = f"ffprobe produced invalid JSON for {path}"
            raise PlayerError(msg) from exc
        return data

    async def stream(
        self,
        path: str | Path,
        start_position: float = 0.0,
    ) -> AsyncIterator[bytes]:
        """Yield PCM16 chunks from `path` at realtime pace, starting at `start_position`.

        Spawns `ffmpeg -re -ss <start> -i <path> ... -f s16le -` and reads
        its stdout in `CHUNK_SIZE`-byte chunks. `-re` throttles ffmpeg's
        output to realtime playback rate, which gives us natural backpressure
        over the WebSocket without explicit rate limiting.

        Cancellation: if the caller's task is cancelled mid-iteration, the
        generator's finally block terminates ffmpeg. The subprocess is
        never leaked.
        """
        start = max(0.0, start_position)
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg_path,
            "-loglevel",
            "quiet",
            "-re",  # throttle to realtime playback rate
            "-ss",
            str(start),
            "-i",
            str(path),
            "-ac",
            "1",  # mono
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",  # raw PCM16 LE
            "-",  # stdout
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        try:
            while True:
                try:
                    chunk = await proc.stdout.readexactly(CHUNK_SIZE)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        yield exc.partial
                    return
                yield chunk
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(asyncio.CancelledError):
                await proc.wait()
