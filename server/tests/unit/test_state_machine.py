"""Tests for the finite state machine."""

from __future__ import annotations

import pytest

from abuel_os.state.machine import StateMachine
from abuel_os.types import AppState, InvalidTransitionError


class TestStateMachine:
    def test_initial_state_is_idle(self) -> None:
        sm = StateMachine()
        assert sm.state is AppState.IDLE

    async def test_wake_word_transitions_to_connecting(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        assert sm.state is AppState.CONNECTING

    async def test_connected_transitions_to_conversing(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        await sm.trigger("connected")
        assert sm.state is AppState.CONVERSING

    async def test_failed_connection_returns_to_idle(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        await sm.trigger("failed")
        assert sm.state is AppState.IDLE

    async def test_timeout_returns_to_idle(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        await sm.trigger("connected")
        await sm.trigger("timeout")
        assert sm.state is AppState.IDLE

    async def test_disconnect_returns_to_idle(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        await sm.trigger("connected")
        await sm.trigger("disconnect")
        assert sm.state is AppState.IDLE

    async def test_invalid_transition_raises(self) -> None:
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError, match="No transition from IDLE"):
            await sm.trigger("connected")

    async def test_invalid_trigger_from_conversing(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        await sm.trigger("connected")
        with pytest.raises(InvalidTransitionError):
            await sm.trigger("start_playback")

    def test_valid_triggers_from_idle(self) -> None:
        sm = StateMachine()
        assert sm.valid_triggers() == ["wake_word"]

    async def test_valid_triggers_from_conversing(self) -> None:
        sm = StateMachine()
        await sm.trigger("wake_word")
        await sm.trigger("connected")
        triggers = sm.valid_triggers()
        assert set(triggers) == {"timeout", "disconnect"}

    async def test_on_enter_callback_fires(self) -> None:
        sm = StateMachine()
        entered: list[str] = []
        sm.on_enter(AppState.CONNECTING, lambda: _append(entered, "connecting"))
        await sm.trigger("wake_word")
        assert entered == ["connecting"]

    async def test_on_exit_callback_fires(self) -> None:
        sm = StateMachine()
        exited: list[str] = []
        sm.on_exit(AppState.IDLE, lambda: _append(exited, "idle"))
        await sm.trigger("wake_word")
        assert exited == ["idle"]

    async def test_callbacks_fire_in_order_exit_then_enter(self) -> None:
        sm = StateMachine()
        order: list[str] = []
        sm.on_exit(AppState.IDLE, lambda: _append(order, "exit_idle"))
        sm.on_enter(AppState.CONNECTING, lambda: _append(order, "enter_connecting"))
        await sm.trigger("wake_word")
        assert order == ["exit_idle", "enter_connecting"]

    async def test_multiple_callbacks_per_state(self) -> None:
        sm = StateMachine()
        calls: list[int] = []
        sm.on_enter(AppState.CONNECTING, lambda: _append(calls, 1))
        sm.on_enter(AppState.CONNECTING, lambda: _append(calls, 2))
        await sm.trigger("wake_word")
        assert calls == [1, 2]


async def _append(lst: list[object], value: object) -> None:
    """Async helper to append to a list (callbacks must be async-compatible)."""
    lst.append(value)
