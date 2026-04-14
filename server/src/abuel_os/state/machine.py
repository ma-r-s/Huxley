"""Finite state machine for the application lifecycle.

4 states, ~8 transitions. Hand-rolled because the state space is small
and a library dependency isn't justified.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from abuel_os.types import AppState, InvalidTransitionError

logger = structlog.get_logger()

# (from_state, trigger) -> to_state
_TRANSITIONS: dict[tuple[AppState, str], AppState] = {
    (AppState.IDLE, "wake_word"): AppState.CONNECTING,
    (AppState.CONNECTING, "connected"): AppState.CONVERSING,
    (AppState.CONNECTING, "failed"): AppState.IDLE,
    (AppState.CONVERSING, "start_playback"): AppState.PLAYING,
    (AppState.CONVERSING, "timeout"): AppState.IDLE,
    (AppState.CONVERSING, "disconnect"): AppState.IDLE,
    (AppState.PLAYING, "wake_word"): AppState.CONNECTING,
    (AppState.PLAYING, "playback_finished"): AppState.IDLE,
}


class StateMachine:
    """Manages application state transitions with async callbacks.

    Usage:
        sm = StateMachine()
        sm.on_enter(AppState.CONNECTING, my_connect_handler)
        await sm.trigger("wake_word")  # IDLE -> CONNECTING
    """

    def __init__(self) -> None:
        self._state = AppState.IDLE
        self._on_enter: dict[AppState, list[Callable[[], Awaitable[Any]]]] = {}
        self._on_exit: dict[AppState, list[Callable[[], Awaitable[Any]]]] = {}
        self._on_transition_cbs: list[Callable[[AppState], Awaitable[Any]]] = []

    @property
    def state(self) -> AppState:
        return self._state

    def on_enter(self, state: AppState, callback: Callable[[], Awaitable[Any]]) -> None:
        """Register a callback to run when entering a state."""
        self._on_enter.setdefault(state, []).append(callback)

    def on_exit(self, state: AppState, callback: Callable[[], Awaitable[Any]]) -> None:
        """Register a callback to run when exiting a state."""
        self._on_exit.setdefault(state, []).append(callback)

    def on_transition(self, callback: Callable[[AppState], Awaitable[Any]]) -> None:
        """Register a callback fired after every successful transition with the new state."""
        self._on_transition_cbs.append(callback)

    async def trigger(self, event: str) -> None:
        """Execute a state transition.

        Raises InvalidTransitionError if the transition is not in the table.
        Runs on_exit callbacks for the old state, then on_enter for the new state.
        """
        key = (self._state, event)
        new_state = _TRANSITIONS.get(key)

        if new_state is None:
            msg = f"No transition from {self._state.name} on event '{event}'"
            raise InvalidTransitionError(msg)

        old_state = self._state
        await logger.ainfo(
            "state_transition",
            from_state=old_state.name,
            trigger=event,
            to_state=new_state.name,
        )

        for cb in self._on_exit.get(old_state, []):
            await cb()

        self._state = new_state

        # Observers fire BEFORE on_enter callbacks so they see each transition
        # in order. An on_enter callback can trigger a nested transition (e.g.
        # _enter_connecting triggers "connected" → CONVERSING after the OpenAI
        # session opens), and the nested trigger would otherwise broadcast its
        # state before the outer trigger's observer fires with a now-stale
        # local `new_state`, leaving the client stuck on the outer state.
        for obs in self._on_transition_cbs:
            await obs(new_state)

        for cb in self._on_enter.get(new_state, []):
            await cb()

    def valid_triggers(self) -> list[str]:
        """Return triggers valid from the current state."""
        return [event for (state, event) in _TRANSITIONS if state == self._state]
