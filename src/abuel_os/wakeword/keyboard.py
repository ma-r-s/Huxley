"""Dev-mode keyboard wake word trigger.

Pressing Enter in the terminal simulates a wake word detection. This lets
the full conversation flow be tested on macOS without a microphone or the
openWakeWord model installed.

Drop-in replacement for WakeWordDetector — same interface, zero hardware deps.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()


class KeyboardWakeWord:
    """Simulates wake word detection via keyboard (Enter key).

    Starts a background task that blocks on stdin.readline() in a thread
    executor. Each Enter press fires the on_detected callback (unless
    detection is suppressed via the enabled flag).
    """

    def __init__(
        self,
        on_detected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.on_detected = on_detected
        self._enabled = True
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def setup(self) -> None:
        """Print dev hint and start the stdin listener."""
        await logger.awarning(
            "dev_mode_keyboard_wakeword",
            msg="Press Enter to simulate wake word (dev mode)",
        )
        print(
            "\n\033[1;32m[DEV] Press ENTER to start talking  |  Ctrl+C to quit\033[0m\n",
            flush=True,
        )
        self._task = asyncio.create_task(self._listen(), name="keyboard_wakeword")

    async def process_frame(self, pcm_16k: bytes) -> None:
        """No-op — keyboard trigger doesn't process audio."""

    async def _listen(self) -> None:
        """Block on stdin in a thread executor; fire callback on each line."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception:
                break

            if not line:
                # EOF — stdin closed
                break

            if not self._enabled:
                await logger.adebug("keyboard_wakeword_suppressed")
                continue

            await logger.ainfo("keyboard_wake_word_triggered")
            if self.on_detected:
                await self.on_detected()
