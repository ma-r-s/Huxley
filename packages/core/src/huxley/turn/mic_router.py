"""Mic-frame dispatcher — single destination for user audio.

Today the coordinator has exactly one destination for mic PCM: the voice
provider. T1.4 Stage 2 adds the `InputClaim` primitive which swaps the
destination to a skill handler for the duration of a claim (phone call,
wake-word listener, etc.).

MicRouter is extracted in T1.3 (this stage) with only the default path
wired so that Stage 2 adds `claim()/release()` behavior without touching
coordinator internals.

See `docs/io-plane.md#input-claim` for the full contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    MicHandler = Callable[[bytes], Awaitable[None]]


class MicClaimHandle:
    """Handle returned by `MicRouter.claim()`. Release restores default."""

    def __init__(self, router: MicRouter, previous: MicHandler) -> None:
        self._router = router
        self._previous = previous
        self._released = False

    def release(self) -> None:
        """Restore the previous handler. Idempotent."""
        if self._released:
            return
        self._released = True
        self._router._restore(self._previous)


class MicRouter:
    """Owns the mic-frame destination.

    Stage-0 invariant: exactly one active handler at a time. Stage 2 will
    add at most one claim on top of the default, still single-owner.
    """

    def __init__(self, default_handler: MicHandler) -> None:
        self._default = default_handler
        self._current: MicHandler = default_handler

    async def dispatch(self, pcm: bytes) -> None:
        await self._current(pcm)

    def claim(self, on_frame: MicHandler) -> MicClaimHandle:
        """Route subsequent frames to `on_frame` until the handle is released."""
        previous = self._current
        self._current = on_frame
        return MicClaimHandle(self, previous)

    @property
    def is_claimed(self) -> bool:
        return self._current is not self._default

    def _restore(self, previous: MicHandler) -> None:
        self._current = previous
