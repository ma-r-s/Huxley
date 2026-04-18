"""Turn creation — single entry point.

Centralizes the `Turn(...)` construction sites that used to live inline
in `coordinator.py`. The indirection gives T1.4 Stage 1 one place to
enrich injected turns with urgency + earcon + TTL without the coordinator
growing a `match source:` branch on construction.

See `docs/io-plane.md` for the broader I/O-plane shape this is
pre-positioned for.
"""

from __future__ import annotations

from .state import Turn, TurnSource, TurnState


class TurnFactory:
    """Creates `Turn` instances with a uniform shape.

    Stateless today. Stage 1 of T1.4 extends `create()` with an
    `inject_spec` keyword that populates urgency + earcon + TTL on
    injected turns.
    """

    def create(
        self,
        *,
        source: TurnSource,
        initial_state: TurnState,
    ) -> Turn:
        return Turn(source=source, state=initial_state)
