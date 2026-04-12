"""Audio router — distributes mic frames to consumers.

Always feeds the wake word detector. When in conversation mode,
also sends frames to the session manager. Handles sample rate
conversion between 24kHz (API) and 16kHz (wake word).
"""

from __future__ import annotations

import asyncio
import struct
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from abuel_os.session.manager import SessionManager
    from abuel_os.types import WakeWordDetectorProtocol

logger = structlog.get_logger()


def downsample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Downsample PCM16 audio from 24kHz to 16kHz.

    Uses simple linear interpolation. For every 3 input samples,
    we produce 2 output samples.
    """
    samples = struct.unpack(f"<{len(pcm_24k) // 2}h", pcm_24k)
    n = len(samples)
    out: list[int] = []
    ratio = 24000 / 16000  # 1.5

    i = 0
    while True:
        src_idx = i * ratio
        idx = int(src_idx)
        if idx >= n - 1:
            break
        frac = src_idx - idx
        sample = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
        out.append(max(-32768, min(32767, sample)))
        i += 1

    return struct.pack(f"<{len(out)}h", *out)


class AudioRouter:
    """Routes mic audio frames to wake word detector and session manager.

    The mic captures at 24kHz (Realtime API native rate).
    Wake word detection needs 16kHz, so we downsample for that path.
    """

    def __init__(
        self,
        mic_queue: asyncio.Queue[bytes],
        wakeword_detector: WakeWordDetectorProtocol,
        session_manager: SessionManager,
    ) -> None:
        self._mic_queue = mic_queue
        self._wakeword = wakeword_detector
        self._session = session_manager
        self._conversation_mode = False
        self._suppress_wakeword = False
        self._running = False

    @property
    def conversation_mode(self) -> bool:
        return self._conversation_mode

    @conversation_mode.setter
    def conversation_mode(self, value: bool) -> None:
        self._conversation_mode = value

    @property
    def suppress_wakeword(self) -> bool:
        """When True, wake word detection is suppressed (model is speaking)."""
        return self._suppress_wakeword

    @suppress_wakeword.setter
    def suppress_wakeword(self, value: bool) -> None:
        self._suppress_wakeword = value

    async def run(self) -> None:
        """Main routing loop. Reads from mic queue and distributes frames."""
        self._running = True
        while self._running:
            try:
                frame_24k = await asyncio.wait_for(self._mic_queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            # Always feed wake word detector (unless suppressed)
            if not self._suppress_wakeword:
                frame_16k = downsample_24k_to_16k(frame_24k)
                await self._wakeword.process_frame(frame_16k)

            # Feed session when in conversation mode
            if self._conversation_mode and self._session.is_connected:
                await self._session.send_audio(frame_24k)

    def stop(self) -> None:
        self._running = False
