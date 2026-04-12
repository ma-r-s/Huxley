"""Dev-mode keyboard controller.

Replaces hardware wake word + PTT button when running on a Mac.

  Enter         → wake word (start session from IDLE/PLAYING)
  Space (hold)  → push-to-talk: mic open while held, commit on release
  Ctrl+C / q    → quit

Runs stdin in raw mode so individual keystrokes are read immediately.
Restores terminal on exit.
"""

from __future__ import annotations

import asyncio
import os
import select
import signal
import sys
import termios
import time
import tty
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger()

_SPACE = b" "
_ENTER = b"\r"
_CTRL_C = b"\x03"
_Q = b"q"
_PTT_RELEASE_TIMEOUT = 0.35  # seconds after last Space until PTT considered released
# Must be > macOS initial key-repeat delay (~225ms) so held key doesn't flicker


class DevKeyboard:
    """Unified dev keyboard controller for wake word + PTT.

    Implements WakeWordDetectorProtocol (on_detected, enabled, setup,
    process_frame) so it drops into app.py where a WakeWordDetector lives.
    PTT callbacks (on_ptt_start, on_ptt_stop) are wired additionally by app.py.
    """

    def __init__(
        self,
        on_detected: Callable[[], Awaitable[None]] | None = None,
        on_ptt_start: Callable[[], Awaitable[None]] | None = None,
        on_ptt_stop: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.on_detected = on_detected
        self.on_ptt_start = on_ptt_start
        self.on_ptt_stop = on_ptt_stop
        self._enabled = True
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- WakeWordDetectorProtocol ---

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def setup(self) -> None:
        self._loop = asyncio.get_running_loop()
        print(
            "\n\033[1;32m[DEV] Enter = wake  |  Hold Space = talk  |  q = quit\033[0m\n",
            flush=True,
        )
        self._task = asyncio.create_task(self._run(), name="dev_keyboard")

    async def process_frame(self, pcm_16k: bytes) -> None:
        """No-op — keyboard doesn't process audio."""

    # --- Internal ---

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._blocking_loop)

    def _blocking_loop(self) -> None:
        """Raw-mode stdin poll loop. Runs in a thread executor."""
        assert self._loop is not None
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())

        ptt_active = False
        last_space_t = 0.0

        try:
            while True:
                readable, _, _ = select.select([sys.stdin], [], [], 0.05)

                if readable:
                    ch = sys.stdin.buffer.read(1)

                    if ch in (_CTRL_C, _Q):
                        os.kill(os.getpid(), signal.SIGINT)
                        break

                    if ch == _ENTER and not ptt_active:
                        asyncio.run_coroutine_threadsafe(self._fire_wake_word(), self._loop)

                    if ch == _SPACE:
                        last_space_t = time.monotonic()
                        if not ptt_active:
                            ptt_active = True
                            asyncio.run_coroutine_threadsafe(self._fire_ptt_start(), self._loop)

                # Detect Space release: no Space received within timeout
                if ptt_active and (time.monotonic() - last_space_t) > _PTT_RELEASE_TIMEOUT:
                    ptt_active = False
                    asyncio.run_coroutine_threadsafe(self._fire_ptt_stop(), self._loop)

        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)

    async def _fire_wake_word(self) -> None:
        if self.on_detected and self._enabled:
            await logger.ainfo("keyboard_wake_word")
            await self.on_detected()

    async def _fire_ptt_start(self) -> None:
        await logger.ainfo("ptt_start")
        if self.on_ptt_start:
            await self.on_ptt_start()

    async def _fire_ptt_stop(self) -> None:
        await logger.ainfo("ptt_stop")
        if self.on_ptt_stop:
            await self.on_ptt_stop()


# Keep old name as alias so any leftover imports don't break
KeyboardWakeWord = DevKeyboard
