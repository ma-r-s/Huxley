"""Core turn vocabulary — `TurnState`, `TurnSource`, `Turn`.

Lives in its own module so the coordinator, `TurnFactory`, and future
collaborators can reference the turn model without a cycle back through
`coordinator.py`.

See `docs/turns.md` for the lifecycle and `docs/io-plane.md` for how
`TurnSource.INJECTED` fits into the I/O plane.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from huxley_sdk import AudioStream, PlaySound


class TurnState(Enum):
    """Finite states for a single user-assistant Turn.

    See `docs/turns.md#turn-lifecycle` for the full state diagram and
    transition rules. The `BARRIER` state from v2 was collapsed into
    the `IN_RESPONSE → APPLYING_FACTORIES` transition in v3.
    """

    IDLE = "idle"
    LISTENING = "listening"
    COMMITTING = "committing"
    IN_RESPONSE = "in_response"
    AWAITING_NEXT_RESPONSE = "awaiting_next_response"
    APPLYING_FACTORIES = "applying_factories"
    INTERRUPTED = "interrupted"


class TurnSource(Enum):
    """How the current turn got created.

    `INJECTED` is reserved for T1.4 Stage 1 (`inject_turn` primitive).
    Nothing creates injected turns yet; the enum value ships with T1.3 so
    the next stage adds a construction path, not a vocabulary.
    """

    USER = "user"
    COMPLETION = "completion"
    INJECTED = "injected"


@dataclass
class Turn:
    """One user-assistant exchange. See `docs/turns.md#1-turn`."""

    source: TurnSource = TurnSource.USER
    id: UUID = field(default_factory=uuid4)
    state: TurnState = TurnState.LISTENING
    user_audio_frames: int = 0
    pending_audio_streams: list[AudioStream] = field(default_factory=list)
    needs_follow_up: bool = False
    # Latched PlaySound from an info-tool call. Sent to the audio channel
    # right after request_response() so the chime queues ahead of the model's
    # response audio (FIFO on the WebSocket). Latest tool wins — a new
    # PlaySound on a chained tool call replaces an earlier one.
    pending_play_sound: PlaySound | None = None
    # Summary tracking — emitted as coord.turn_summary at end-of-turn.
    started_at: float = field(default_factory=lambda: time.monotonic())
    tool_calls: int = 0
    response_done_count: int = 0
