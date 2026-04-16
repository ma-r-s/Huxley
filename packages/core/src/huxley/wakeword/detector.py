"""Wake word detection wrapper.

Wraps openWakeWord (or provides a stub when not available).
The detector processes 16kHz PCM16 audio frames and calls a
callback when the wake word is detected.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()


class WakeWordDetector:
    """Detects a wake word in audio frames.

    On hardware with openwakeword installed, uses the real model.
    On dev machines, provides a no-op stub that never triggers.
    """

    def __init__(
        self,
        model_path: str,
        threshold: float = 0.7,
        on_detected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._model_path = model_path
        self._threshold = threshold
        self.on_detected = on_detected
        self._model: Any = None
        self._enabled = True

    async def setup(self) -> None:
        """Load the wake word model."""
        try:
            import openwakeword

            self._model = openwakeword.Model(
                wakeword_models=[self._model_path],
                inference_framework="tflite",
            )
            await logger.ainfo("wakeword_model_loaded", model=self._model_path)
        except ImportError:
            await logger.awarning(
                "openwakeword_not_available",
                msg="Wake word detection disabled — install openwakeword for hardware",
            )
            self._model = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def process_frame(self, pcm_16k: bytes) -> None:
        """Process a 16kHz PCM16 audio frame for wake word detection."""
        if not self._enabled or self._model is None:
            return

        loop = asyncio.get_running_loop()
        detected = await loop.run_in_executor(None, self._detect, pcm_16k)

        if detected and self.on_detected:
            await logger.ainfo("wake_word_detected")
            await self.on_detected()

    def _detect(self, pcm_16k: bytes) -> bool:
        """Blocking detection — runs in thread executor."""
        import numpy as np

        audio = np.frombuffer(pcm_16k, dtype=np.int16)
        predictions = self._model.predict(audio)

        return any(score > self._threshold for _model_name, score in predictions.items())
