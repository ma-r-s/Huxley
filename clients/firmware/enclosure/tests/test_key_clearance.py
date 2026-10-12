"""The case stands clear of every edge button on the board, BOOT above all:
BOOT held in at a reset boots download mode (F-0020). The tub's corner
block was shaped round the board with no clearance and touched BOOT's
plunger until 2026-10-12."""

from __future__ import annotations

import pytest
from huxcase.concepts import hx1


@pytest.fixture(scope="module")
def parts():
    return {p.name: p.shape for p in hx1.build().parts}


@pytest.mark.parametrize("key", ["reset", "boot", "key3", "key2", "key1"])
def test_the_case_stands_off_every_edge_button(parts, key):
    for shell in ("tub_frosted", "tub_grey", "faceplate"):
        assert parts[shell].distance_to(parts[f"board_key_{key}"]) >= hx1.KEY_CLEAR - 0.05, shell


def test_no_hole_to_the_buttons(parts):
    """Mario wants no holes in the case (2026-10-12): the left wall in front
    of the edge buttons is whole."""
    from huxcase.shapes import bbox_solid

    # the straight wall, below where it curves into its R8 corner (z 96)
    wall = bbox_solid(-hx1.W / 2, -hx1.W / 2 + hx1.T, -12.0, -8.4, 80.0, 95.0)
    assert abs((wall & parts["tub_frosted"]).volume - wall.volume) < 1e-3
