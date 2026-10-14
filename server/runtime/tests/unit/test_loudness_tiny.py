"""A near-silent window must read as silence, never raise (2026-10-14: a
song stopped mid-play on log10 of a mean square that divided to 0.0)."""

from __future__ import annotations

import math

import pytest

from huxley.loudness import lufs


@pytest.mark.parametrize("mean_square", [0.0, -0.0, -1e-9, 1e-320, 5e-324, math.nan])
def test_silence_and_underflow_read_as_minus_infinity(mean_square: float) -> None:
    assert lufs(mean_square) == -math.inf


def test_a_normal_level_is_unchanged() -> None:
    full = 32768.0 * 32768.0
    assert lufs(full) == pytest.approx(-0.691)
