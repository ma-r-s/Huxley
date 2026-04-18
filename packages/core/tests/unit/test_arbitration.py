"""Exhaustive tests for the I/O-plane arbitration pure function.

16 total cases: 4 idle (current_owner_yield=None, one per Urgency) plus
12 busy (4 Urgency, 3 YieldPolicy). See `docs/io-plane.md#arbitration`
for the table.
"""

from __future__ import annotations

import pytest

from huxley.turn.arbitration import Decision, arbitrate
from huxley_sdk import Urgency, YieldPolicy


class TestIdle:
    """No current owner — every urgency speaks immediately."""

    @pytest.mark.parametrize("urgency", list(Urgency))
    def test_idle_returns_speak_now(self, urgency: Urgency) -> None:
        assert arbitrate(urgency, None) is Decision.SPEAK_NOW


class TestAmbient:
    """Ambient drops whenever something else owns the speaker."""

    @pytest.mark.parametrize("policy", list(YieldPolicy))
    def test_ambient_drops_when_busy(self, policy: YieldPolicy) -> None:
        assert arbitrate(Urgency.AMBIENT, policy) is Decision.DROP


class TestChimeDefer:
    def test_chime_defer_preempts_immediate_owner(self) -> None:
        assert arbitrate(Urgency.CHIME_DEFER, YieldPolicy.IMMEDIATE) is Decision.PREEMPT

    def test_chime_defer_ducks_yield_above(self) -> None:
        assert arbitrate(Urgency.CHIME_DEFER, YieldPolicy.YIELD_ABOVE) is Decision.DUCK_CHIME

    def test_chime_defer_ducks_yield_critical(self) -> None:
        assert arbitrate(Urgency.CHIME_DEFER, YieldPolicy.YIELD_CRITICAL) is Decision.DUCK_CHIME


class TestInterrupt:
    def test_interrupt_preempts_immediate(self) -> None:
        assert arbitrate(Urgency.INTERRUPT, YieldPolicy.IMMEDIATE) is Decision.PREEMPT

    def test_interrupt_preempts_yield_above(self) -> None:
        assert arbitrate(Urgency.INTERRUPT, YieldPolicy.YIELD_ABOVE) is Decision.PREEMPT

    def test_interrupt_ducks_yield_critical(self) -> None:
        """YIELD_CRITICAL owners hold out for CRITICAL — INTERRUPT ducks past them."""
        assert arbitrate(Urgency.INTERRUPT, YieldPolicy.YIELD_CRITICAL) is Decision.DUCK_CHIME


class TestCritical:
    """CRITICAL always preempts — no policy blocks it."""

    @pytest.mark.parametrize("policy", list(YieldPolicy))
    def test_critical_always_preempts(self, policy: YieldPolicy) -> None:
        assert arbitrate(Urgency.CRITICAL, policy) is Decision.PREEMPT
