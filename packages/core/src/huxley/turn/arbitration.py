"""Pure arbitration function for the I/O plane.

Given an incoming `Urgency` and the current speaker-owner's `YieldPolicy`
(or `None` when nothing owns the speaker), returns the `Decision` the
coordinator should take. No state, no side effects — exhaustively tested
over all 20 cases (16 busy + 4 idle).

Shipped with T1.3; wired by T1.4 Stage 1 (`inject_turn`). See
`docs/io-plane.md#arbitration` for the design rationale.
"""

from __future__ import annotations

from enum import Enum

from huxley_sdk import Urgency, YieldPolicy


class Decision(Enum):
    """Outcome of a single arbitration step."""

    SPEAK_NOW = "speak_now"
    """No current owner — speak immediately."""

    PREEMPT = "preempt"
    """Cancel the current owner, play the tier earcon, then speak."""

    DUCK_CHIME = "duck_chime"
    """Dip current stream, play the tier chime, hold speech for the next PTT."""

    HOLD = "hold"
    """Queue for the next PTT; no earcon now."""

    DROP = "drop"
    """Ambient event dropped while the speaker is busy."""


def arbitrate(urgency: Urgency, current_owner_yield: YieldPolicy | None) -> Decision:
    """Return the decision for a new speaker claim.

    `current_owner_yield=None` means no AudioStream / InputClaim / user turn
    currently owns the speaker (idle). Otherwise it's the `yield_policy` of
    whatever owns it right now — the caller snapshots this at the moment of
    decision.
    """
    if current_owner_yield is None:
        return Decision.SPEAK_NOW

    if urgency is Urgency.CRITICAL:
        return Decision.PREEMPT
    if urgency is Urgency.AMBIENT:
        return Decision.DROP
    if urgency is Urgency.CHIME_DEFER:
        # Only IMMEDIATE yields to a chime-defer; everything else ducks.
        return (
            Decision.PREEMPT
            if current_owner_yield is YieldPolicy.IMMEDIATE
            else Decision.DUCK_CHIME
        )
    # INTERRUPT: only YIELD_CRITICAL ducks (holds out for CRITICAL); others yield.
    return (
        Decision.DUCK_CHIME
        if current_owner_yield is YieldPolicy.YIELD_CRITICAL
        else Decision.PREEMPT
    )
