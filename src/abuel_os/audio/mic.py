"""Microphone capture module.

Runs PyAudio capture in a thread executor and feeds PCM frames into an
async queue. Gracefully stubs out when PyAudio is not installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from abuel_os.config import Settings

logger = structlog.get_logger()


class MicCapture:
    """Captures audio from the microphone via PyAudio.

    Call `asyncio.create_task(mic.run())` to start. Frames are placed into
    the provided asyncio.Queue for the AudioRouter to consume.
    """

    def __init__(self, config: Settings, queue: asyncio.Queue[bytes]) -> None:
        self._config = config
        self._queue = queue
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Async capture loop — blocking PyAudio read runs in thread executor."""
        try:
            import pyaudio
        except ImportError:
            await logger.awarning(
                "pyaudio_not_available", msg="Mic capture disabled — install pyaudio"
            )
            return

        self._running = True
        pa = pyaudio.PyAudio()
        frame_size = int(
            self._config.audio_sample_rate * self._config.audio_frame_duration_ms / 1000
        )
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._config.audio_sample_rate,
            input=True,
            input_device_index=self._config.audio_device_index,
            frames_per_buffer=frame_size,
        )
        read_frame = functools.partial(stream.read, frame_size, exception_on_overflow=False)
        loop = asyncio.get_running_loop()

        try:
            while self._running:
                data = await loop.run_in_executor(None, read_frame)
                try:
                    self._queue.put_nowait(data)
                except asyncio.QueueFull:
                    with contextlib.suppress(asyncio.QueueEmpty):
                        self._queue.get_nowait()
                    self._queue.put_nowait(data)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
