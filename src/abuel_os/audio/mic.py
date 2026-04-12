"""Microphone capture module.

Runs PyAudio capture in a thread and feeds PCM frames into an async queue.
This module is only functional on hardware with PyAudio installed.
On dev machines, it provides a no-op stub.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from abuel_os.config import Settings

logger = structlog.get_logger()


class MicCapture:
    """Captures audio from the microphone via PyAudio.

    Frames are placed into an asyncio.Queue for consumption by the AudioRouter.
    Runs the blocking PyAudio read in a thread executor.
    """

    def __init__(self, config: Settings, queue: asyncio.Queue[bytes]) -> None:
        self._config = config
        self._queue = queue
        self._running = False

    async def start(self) -> None:
        """Start capturing audio in a background thread."""
        self._running = True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._capture_loop)

    def stop(self) -> None:
        """Signal the capture loop to stop."""
        self._running = False

    def _capture_loop(self) -> None:
        """Blocking loop — runs in a thread."""
        try:
            import pyaudio
        except ImportError:
            logger.warning("pyaudio_not_available", msg="Running without mic capture")
            return

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

        try:
            while self._running:
                data = stream.read(frame_size, exception_on_overflow=False)
                try:
                    self._queue.put_nowait(data)
                except asyncio.QueueFull:
                    # Drop oldest frame to prevent buffer overrun
                    with contextlib.suppress(asyncio.QueueEmpty):
                        self._queue.get_nowait()
                    self._queue.put_nowait(data)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
