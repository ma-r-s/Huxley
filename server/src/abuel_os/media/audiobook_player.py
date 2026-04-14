"""Audiobook player — ffmpeg subprocess decoder that streams PCM16 to a callback.

Replaces the earlier mpv-based player that played audio locally on the host.
Audio now streams through the same PCM16 24 kHz channel as the OpenAI model
audio, so every audio client (browser, ESP32) plays it through its own speakers.

See `docs/decisions.md` — _"Audiobook audio streams through the WebSocket, not
local playback"_ — for the rationale.

**Pause semantics**: we stop reading from ffmpeg's stdout. ffmpeg blocks on a
full pipe buffer; on resume we read again and playback continues seamlessly.

**Seek semantics**: kill and respawn ffmpeg at the new `-ss` position. An
`on_audio_clear` callback fires between stop and load so the client can drop
any queued audio from the old position.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

logger = structlog.get_logger()

SAMPLE_RATE = 24_000
BYTES_PER_SAMPLE = 2  # PCM16 little-endian
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE  # 48_000
CHUNK_DURATION_S = 0.1  # 100 ms chunks
CHUNK_SIZE = int(BYTES_PER_SECOND * CHUNK_DURATION_S)  # 4800 bytes


class PlayerError(Exception):
    """Raised when the audiobook player fails to load or decode a file."""


class AudiobookPlayer:
    """Streaming PCM16 audio decoder backed by ffmpeg.

    Loads an audio file, decodes to 24 kHz mono PCM16, and emits chunks via
    `on_chunk` at realtime playback rate. The caller forwards chunks over the
    WebSocket to the active audio client.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        on_chunk: Callable[[bytes], Awaitable[None]] | None = None,
        on_finished: Callable[[], Awaitable[None]] | None = None,
        on_audio_clear: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._on_chunk = on_chunk
        self._on_finished = on_finished
        self._on_audio_clear = on_audio_clear

        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # starts unpaused

        self._current_path: str | None = None
        self._start_position: float = 0.0
        self._bytes_read: int = 0
        self._duration: float = 0.0

    @property
    def is_playing(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def position(self) -> float:
        """Current playback position in seconds (start + bytes already read)."""
        return self._start_position + (self._bytes_read / BYTES_PER_SECOND)

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def current_path(self) -> str | None:
        return self._current_path

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

    async def load(
        self,
        path: str | Path,
        start_position: float = 0.0,
        *,
        paused: bool = False,
    ) -> None:
        """Begin decoding from the given position.

        If `paused=True`, ffmpeg is spawned but chunks are not emitted until
        `resume()` is called. This is the "load but don't start streaming yet"
        mode the audiobooks skill uses to pre-stage a book while the model
        narrates its verbal acknowledgement — the book begins streaming only
        after the ack finishes and state transitions to PLAYING.
        """
        async with self._lock:
            await self._stop_internal()

            try:
                meta = await self.probe(path)
            except PlayerError:
                raise
            except Exception as exc:
                msg = f"Cannot probe {path}: {exc}"
                raise PlayerError(msg) from exc

            fmt = meta.get("format") or {}
            duration_str = fmt.get("duration")
            self._duration = float(duration_str) if duration_str is not None else 0.0

            self._current_path = str(path)
            self._start_position = max(0.0, start_position)
            self._bytes_read = 0
            if paused:
                self._pause_event.clear()
            else:
                self._pause_event.set()

            await self._spawn_ffmpeg(self._current_path)
            await logger.ainfo(
                "audiobook_loaded",
                path=self._current_path,
                start=self._start_position,
                duration=self._duration,
                paused=paused,
            )

    async def pause(self) -> None:
        """Stop reading ffmpeg stdout; ffmpeg blocks when the pipe fills."""
        self._pause_event.clear()

    async def resume(self) -> None:
        self._pause_event.set()

    async def stop(self) -> None:
        """Stop playback and kill ffmpeg."""
        async with self._lock:
            await self._stop_internal()

    async def seek(self, position: float) -> None:
        """Seek to an absolute position by killing and respawning ffmpeg.

        Fires `on_audio_clear` between stop and load so the client drops
        any audio still queued from the old position.
        """
        async with self._lock:
            if self._current_path is None:
                return
            path = self._current_path
            await self._stop_internal()
            if self._on_audio_clear is not None:
                await self._on_audio_clear()

            if self._duration > 0:
                self._start_position = max(0.0, min(position, self._duration))
            else:
                self._start_position = max(0.0, position)
            self._bytes_read = 0
            self._pause_event.set()
            await self._spawn_ffmpeg(path)

    # --- Internal ---

    async def _spawn_ffmpeg(self, path: str) -> None:
        """Spawn ffmpeg at `self._start_position`. Caller must hold `_lock`."""
        self._process = await asyncio.create_subprocess_exec(
            self._ffmpeg_path,
            "-loglevel",
            "quiet",
            "-re",  # throttle output to realtime playback rate
            "-ss",
            str(self._start_position),
            "-i",
            path,
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
        self._read_task = asyncio.create_task(self._read_loop())

    async def _stop_internal(self) -> None:
        """Stop playback without acquiring `_lock`. Caller must hold it."""
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
        self._read_task = None

        if self._process is not None:
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()
            with contextlib.suppress(asyncio.CancelledError):
                await self._process.wait()
            self._process = None

    async def _read_loop(self) -> None:
        """Read PCM chunks from ffmpeg stdout and emit via `on_chunk`."""
        assert self._process is not None
        assert self._process.stdout is not None
        stdout = self._process.stdout

        try:
            while True:
                await self._pause_event.wait()

                try:
                    chunk = await stdout.readexactly(CHUNK_SIZE)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        self._bytes_read += len(exc.partial)
                        if self._on_chunk is not None:
                            await self._on_chunk(exc.partial)
                    break

                self._bytes_read += len(chunk)
                if self._on_chunk is not None:
                    await self._on_chunk(chunk)

            # Natural EOF — playback finished
            if self._on_finished is not None:
                await self._on_finished()
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("audiobook_read_loop_error")
