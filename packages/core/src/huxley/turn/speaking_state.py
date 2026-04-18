"""Single-owner speaker flag.

Replaces the boolean `_model_speaking` that the coordinator used to flip
by hand at six call sites. Each speaker producer (user-turn model audio,
factory stream, completion synthetic turn, injected turn, input-claim
speaker source) identifies itself with a `SpeakingOwner`. The state
machine guarantees exactly one owner at a time; `release(expected)` is
a safe no-op when something else has taken over, which removes the
"check the flag before clearing" dance that used to scatter through the
coordinator.

Notifications on `None ↔ owned` transitions are forwarded to the client
via the `notify` callback passed at construction (same wire event as
before, `model_speaking: bool`).

T1.4 Stages 1-2 use the `INJECTED` and `CLAIM` owners, which ship with
T1.3 so downstream stages don't have to retouch this module.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class SpeakingOwner(Enum):
    """Who currently owns the speaker. Exactly one at a time or `None`."""

    USER = "user"
    FACTORY = "factory"
    COMPLETION = "completion"
    INJECTED = "injected"
    CLAIM = "claim"


class SpeakingState:
    """Named-owner speaker flag + client notification.

    Construction-time callback `notify(is_speaking: bool)` is fired on
    every `None → owner` and `owner → None` transition (including
    `force_release`). Owner-to-owner transfers do not fire notify —
    the client already sees `model_speaking=true`.
    """

    def __init__(self, notify: Callable[[bool], Awaitable[None]]) -> None:
        self._notify = notify
        self._owner: SpeakingOwner | None = None

    @property
    def owner(self) -> SpeakingOwner | None:
        return self._owner

    @property
    def is_speaking(self) -> bool:
        return self._owner is not None

    async def acquire(self, owner: SpeakingOwner) -> None:
        """Take the speaker. Fires notify(True) on idle → owned."""
        if self._owner is owner:
            return
        was_idle = self._owner is None
        self._owner = owner
        if was_idle:
            await self._notify(True)

    async def release(self, expected: SpeakingOwner) -> bool:
        """Release iff currently owned by `expected`. Returns True on release.

        Safe no-op if another owner has taken over — used when a factory's
        natural-end cleanup races with an interrupt that already cleared
        ownership.
        """
        if self._owner is not expected:
            return False
        self._owner = None
        await self._notify(False)
        return True

    async def force_release(self) -> bool:
        """Clear whoever owns the speaker. Fires notify(False) iff was owned.

        Used by `interrupt()`, `on_session_disconnected`, and `on_audio_done`
        where we want to drop the flag regardless of who holds it.
        """
        if self._owner is None:
            return False
        self._owner = None
        await self._notify(False)
        return True

    def transfer(self, from_owner: SpeakingOwner, to_owner: SpeakingOwner) -> bool:
        """Atomic ownership change. No notify (still speaking, different owner).

        Returns True on success, False if `from_owner` is not the current
        owner (race lost to a preempt or release).
        """
        if self._owner is not from_owner:
            return False
        self._owner = to_owner
        return True
