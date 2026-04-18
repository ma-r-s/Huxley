"""Priority primitives for I/O-plane arbitration.

Skills use these to describe the priority of a proactive turn or stream
claim; the framework uses them to arbitrate the single "does this new
owner preempt the current owner" decision.

Shipped with T1.3 (coordinator refactor) so that T1.4 Stages 1-2 only
wire new paths — they don't expand the enum vocabulary. See
`docs/io-plane.md` for the full contract + the 20-case arbitration
table that lives in `huxley.turn.arbitration`.
"""

from __future__ import annotations

from enum import Enum


class Urgency(Enum):
    """How urgent an injected turn or a claim's speaker_source is.

    The framework maps this to a behavior (drop / defer with chime /
    preempt / critical preempt) via `arbitrate()`.
    """

    AMBIENT = "ambient"
    """Speak only if idle; drop silently when something else owns the speaker."""

    CHIME_DEFER = "chime_defer"
    """Play the tier chime now, hold the speech payload for the user's next PTT."""

    INTERRUPT = "interrupt"
    """Preempt current media, speak immediately."""

    CRITICAL = "critical"
    """Top priority. Preempts everything, including other media claims."""


class YieldPolicy(Enum):
    """How willing the current speaker-owner is to yield.

    Declared by whoever currently owns the speaker — the factory stream,
    an active `InputClaim`, a user turn. Arbitration uses this alongside
    an incoming `Urgency` to pick an outcome.
    """

    IMMEDIATE = "immediate"
    """Yield to anything above `AMBIENT` (most permissive)."""

    YIELD_ABOVE = "yield_above"
    """Yield to `INTERRUPT` and `CRITICAL`. Sensible default for media."""

    YIELD_CRITICAL = "yield_critical"
    """Yield only to `CRITICAL`. For calls + other hard-to-yield claims."""
