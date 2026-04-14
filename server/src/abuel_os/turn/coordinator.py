"""TurnCoordinator — the single authority for audio sequencing around tool calls.

Stub module for migration step 1. State machine logic and tests arrive in
step 2. Wiring into `session/manager.py` and `app.py` arrives in step 3.
Audiobooks skill migrates to this in step 4.

See `docs/turns.md` for the full spec, including:
- Turn lifecycle (7 states, chained-response handling)
- Factory pattern (`ToolResult.audio_factory`, coordinator-invoked)
- Interrupt atomicity (6-step barrier method)
- Mid-chain interrupt rule
- `PLAYING` state removal
- Client-side thinking tone
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Callable


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


@dataclass
class Turn:
    """One user-assistant exchange. See `docs/turns.md#1-turn`.

    `pending_factories` accumulates across all response cycles in this
    turn (appended on every function call whose result has a non-None
    `audio_factory`). Fired only at the terminal `APPLYING_FACTORIES`
    state; cleared on interrupt.

    `needs_follow_up` is per-response: set when a function call with
    `audio_factory=None` is dispatched in the current response, read
    on `response.done` to decide whether to request a follow-up
    `response.create`, cleared at the start of each new response.
    """

    id: UUID = field(default_factory=uuid4)
    state: TurnState = TurnState.IDLE
    user_audio_frames: int = 0
    response_ids: list[str] = field(default_factory=list)
    pending_factories: list[Callable[[], AsyncIterator[bytes]]] = field(default_factory=list)
    needs_follow_up: bool = False


class TurnCoordinator:
    """Sequences model speech and tool audio within a single Turn.

    **Stub — step 1.** Real state machine logic lands in step 2. Wiring
    into session/manager and app.py lands in step 3. See `docs/turns.md`
    for the complete spec.

    Owns three cross-turn fields:
    - `current_turn`: the live Turn, or None if idle
    - `current_media_task`: the asyncio.Task running a long-running
      factory (audiobook). Outlives turns — a book started in one turn
      keeps playing until the next turn's interrupt cancels it.
    - `response_cancelled`: drop flag preserved from
      `session/manager.py`; set by `interrupt()` to discard stale OpenAI
      audio deltas in the race window before `response.cancel` takes effect.
    """

    def __init__(self) -> None:
        self.current_turn: Turn | None = None
        self.current_media_task: asyncio.Task[None] | None = None
        self.response_cancelled: bool = False
