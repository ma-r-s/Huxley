"""Turn-based audio coordination — see `docs/turns.md` for the full design.

This package owns the `TurnCoordinator` that sequences model speech and
tool-produced audio within a single user-assistant interaction (a Turn).
It replaces the ad hoc coordination previously scattered across
`session/manager.py`, `app.py`, and `skills/audiobooks.py`.
"""

from __future__ import annotations

from abuel_os.turn.coordinator import Turn, TurnCoordinator, TurnState

__all__ = ["Turn", "TurnCoordinator", "TurnState"]
