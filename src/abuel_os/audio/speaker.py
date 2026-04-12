"""Speaker output module.

Reads PCM frames from an async queue and plays them through PyAudio.
Only used for Realtime API audio output — mpv handles its own audio.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from abuel_os.config import Settings

logger = structlog.get_logger()


class SpeakerOutput:
    """Plays PCM16 audio through the system speaker via PyAudio.

    Call `asyncio.create_task(speaker.run())` to start. Reads frames from
    the provided asyncio.Queue and writes them to PyAudio in a thread executor.
    """

    def __init__(self, config: Settings, queue: asyncio.Queue[bytes]) -> None:
        self._config = config
        self._queue = queue
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Async playback loop — blocking PyAudio write runs in thread executor."""
        try:
            import pyaudio
        except ImportError:
            await logger.awarning(
                "pyaudio_not_available", msg="Speaker output disabled — install pyaudio"
            )
            return

        self._running = True
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._config.audio_sample_rate,
            output=True,
        )
        loop = asyncio.get_running_loop()

        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except TimeoutError:
                    continue
                await loop.run_in_executor(None, stream.write, data)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
