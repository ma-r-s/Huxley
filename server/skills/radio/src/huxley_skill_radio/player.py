"""Radio playback via ffmpeg subprocess.

Stateless helper exposing one operation:

- `stream(url)` — async generator that spawns ffmpeg pulling from an HTTP
  / Icecast / Shoutcast URL, yields PCM16 chunks at realtime pace, and
  tears down the subprocess cleanly on cancellation.

Unlike `AudiobookPlayer`, there's no `probe()` (we don't read metadata),
no `start_position` (radio is live), and no natural EOF (radio plays
forever; only the user stops it). The audio path is otherwise identical
— PCM16 / 24 kHz / mono so the bytes drop into the same WebSocket
`audio` channel as everything else.

Reconnect flags (`-reconnect`, `-reconnect_streamed`,
`-reconnect_delay_max`) tell ffmpeg to retry on transient network drops
— important for radio, which is mostly listened to as a background
ambient stream where a 2-3 second blip is recoverable but a hard fail
is not.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SAMPLE_RATE = 24_000
BYTES_PER_SAMPLE = 2  # PCM16 little-endian
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE  # 48_000
CHUNK_DURATION_S = 0.1  # 100 ms chunks
CHUNK_SIZE = int(BYTES_PER_SECOND * CHUNK_DURATION_S)  # 4800 bytes

# Many radio servers reject default ffmpeg / curl user-agents (BBC is the
# most famous). Identify ourselves clearly so server admins can see us
# (and so we don't get false-blocked as anonymous bot traffic).
_USER_AGENT = "huxley-radio/0.1 (+https://github.com/mario/huxley)"


class PlayerError(Exception):
    """Raised when the radio player fails to open or decode a stream."""


class RadioPlayer:
    """Stateless ffmpeg wrapper for HTTP/Icecast/Shoutcast streams."""

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self._ffmpeg_path = ffmpeg_path

    async def stream(self, url: str) -> AsyncIterator[bytes]:
        """Yield PCM16 chunks from `url` at realtime pace.

        Spawns
        `ffmpeg -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5
                -user_agent <ua> -re -i <url> -ac 1 -ar 24000 -f s16le -`
        and reads stdout in `CHUNK_SIZE`-byte chunks. `-re` throttles
        ffmpeg to realtime playback rate; this gives us natural
        backpressure over the WebSocket without explicit rate-limiting.

        Cancellation: if the caller's task is cancelled, the generator's
        `finally` terminates ffmpeg. The subprocess is never leaked.

        Reconnect: ffmpeg auto-retries on network drops (up to ~5s
        between attempts). If the server itself dies, ffmpeg eventually
        exits non-zero and we raise `PlayerError`.
        """
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg_path,
            "-loglevel",
            "warning",
            # Reconnect on transient drops (HTTP only; ignored for non-HTTP).
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-user_agent",
            _USER_AGENT,
            "-re",  # throttle to realtime
            "-i",
            url,
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
                    break
                yield chunk
            returncode = await proc.wait()
            if returncode != 0:
                raise PlayerError(f"ffmpeg exited with code {returncode} for url={url}")
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(asyncio.CancelledError):
                await proc.wait()
