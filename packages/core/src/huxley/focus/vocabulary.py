"""Focus vocabulary — channels, focus states, content types, activities.

Dependency-free by design: nothing in this module imports from
`huxley.turn`, `huxley.persona`, `huxley_sdk`, or anywhere else in
Huxley. That keeps the focus package at the bottom of the import graph
so `FocusManager`, observers, and the coordinator can all depend on it
without risking cycles.

See `docs/architecture.md#focus-management` for the model and
`docs/io-plane.md` for the AVS lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol


class Channel(StrEnum):
    """Named resource scopes on the single speaker.

    Priorities live in `CHANNEL_PRIORITY`; lower number = higher
    priority (AVS convention).
    """

    DIALOG = "dialog"
    COMMS = "comms"
    ALERT = "alert"
    CONTENT = "content"


CHANNEL_PRIORITY: dict[Channel, int] = {
    Channel.DIALOG: 100,
    Channel.COMMS: 150,
    Channel.ALERT: 200,
    Channel.CONTENT: 300,
}


class FocusState(StrEnum):
    """Focus level of an Activity on its channel. Verbatim from AVS.

    Invariants (enforced by `FocusManager`):
    - `FOREGROUND` → `MixingBehavior.PRIMARY`.
    - `NONE` → `MixingBehavior.MUST_STOP`.
    """

    FOREGROUND = "foreground"
    BACKGROUND = "background"
    NONE = "none"


class ContentType(StrEnum):
    """How an Activity behaves when backgrounded. Verbatim from AVS.

    - `MIXABLE` → `MAY_DUCK` on BACKGROUND.
    - `NONMIXABLE` → `MUST_PAUSE` on BACKGROUND.
    """

    MIXABLE = "mixable"
    NONMIXABLE = "nonmixable"


class MixingBehavior(StrEnum):
    """What an Activity should do on a focus transition. Verbatim from AVS."""

    PRIMARY = "primary"
    MAY_DUCK = "may_duck"
    MUST_PAUSE = "must_pause"
    MUST_STOP = "must_stop"


class ChannelObserver(Protocol):
    """Callback receiving focus changes for one Activity.

    Contract:

    1. Return quickly. Target <10ms typical; absolute ceiling <100ms.
       Heavy work (I/O, subprocess spawn, etc.) goes to
       `asyncio.create_task`.
    2. Idempotent for repeated states — observer tracks its own
       prev_state where needed.
    3. Do NOT call `FocusManager.stop()` from within this callback —
       deadlock. The manager detects and raises on attempted
       re-entrance.
    4. MAY call `acquire()` / `release()` from within this callback
       — those just enqueue. Processed after the current transition's
       remaining notifications complete (one-tick delay is intentional).
    """

    async def on_focus_changed(self, new_focus: FocusState, behavior: MixingBehavior) -> None: ...


@dataclass(frozen=True, slots=True)
class Activity:
    """One claim on a channel's speaker.

    Dedup is by `(channel, interface_name)` tuple. Two Activities on the
    same channel with the same `interface_name` cannot coexist — a
    re-acquire replaces the prior Activity (the prior's observer gets
    `NONE/MUST_STOP`).

    Intentionally no `__eq__` override. Overriding `__eq__` by
    `interface_name` alone would break cross-channel `in` checks and
    set/list operations. The manager scans explicitly via
    `_remove_by_interface(channel, name)`.

    `patience` belongs to the incumbent (the Activity being displaced),
    not the newcomer — Huxley's deliberate flip from AVS's model.
    See `docs/io-plane.md#patience-attribution`.
    """

    channel: Channel
    interface_name: str
    content_type: ContentType
    observer: ChannelObserver
    patience: timedelta = timedelta(0)


def mixing_for_background(content_type: ContentType) -> MixingBehavior:
    """Derive the MixingBehavior an Activity receives when going to BACKGROUND.

    | ContentType | → | MixingBehavior |
    | ----------- | - | -------------- |
    | MIXABLE     | → | MAY_DUCK       |
    | NONMIXABLE  | → | MUST_PAUSE     |
    """
    return (
        MixingBehavior.MAY_DUCK
        if content_type is ContentType.MIXABLE
        else MixingBehavior.MUST_PAUSE
    )
