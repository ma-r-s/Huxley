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

    Reads from an asyncio.Queue. Runs the blocking PyAudio write in a thread.
    """

    def __init__(self, config: Settings, queue: asyncio.Queue[bytes]) -> None:
        self._config = config
        self._queue = queue
        self._running = False

    async def start(self) -> None:
        """Start the speaker output in a background thread."""
        self._running = True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._playback_loop)

    def stop(self) -> None:
        self._running = False

    def _playback_loop(self) -> None:
        """Blocking loop — runs in a thread."""
        try:
            import pyaudio
        except ImportError:
            logger.warning("pyaudio_not_available", msg="Running without speaker output")
            return

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._config.audio_sample_rate,
            output=True,
        )

        try:
            while self._running:
                try:
                    # Use a timeout so we can check self._running periodically
                    data = asyncio.get_event_loop().run_until_complete(
                        asyncio.wait_for(self._queue.get(), timeout=0.5)
                    )
                    stream.write(data)
                except (TimeoutError, RuntimeError):
                    continue
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
