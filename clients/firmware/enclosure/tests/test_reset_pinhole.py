"""The reset pinhole (docs/remote-operation.md W7): a paperclip through the
case's side, and the bumper, reaches the board's RESET button and nothing
else; and the case stands clear of every edge button, BOOT above all (BOOT
held in at a reset boots download mode, F-0020)."""

from __future__ import annotations

import pytest
from build123d import Cylinder, Plane
from huxcase import bumper as bm
from huxcase.concepts import hx1


@pytest.fixture(scope="module")
def parts():
    return {p.name: p.shape for p in hx1.build().parts}


def _along(s0: float, s1: float, d: float):
    """A rod of diameter d on RESET's press line, s0..s1 mm out from its tip."""
    tip, u = hx1.key_line("RESET")
    return Plane(origin=tip + u * ((s0 + s1) / 2), z_dir=u).location * Cylinder(d / 2, s1 - s0)


def test_a_paperclip_reaches_reset_and_nothing_else(parts):
    clip = _along(0.05, hx1.reset_mouth_s() + 6, 1.0)  # in from outside the bumper
    for name, shape in parts.items():
        assert (clip & shape).volume < 1e-4, name
    assert (clip & bm.bumper()).volume < 1e-4


def test_the_bore_guides_it_straight(parts):
    """The tub's corner block surrounds the bore from the button's pocket to
    the wall, so the clip can't tilt off the button."""
    ring = _along(hx1.KEY_CLEAR + 0.3, hx1.reset_mouth_s() - 1.0, hx1.RESET_BORE + 1.6) - _along(
        hx1.KEY_CLEAR, hx1.reset_mouth_s() + 1, hx1.RESET_BORE + 0.2
    )
    assert (ring & parts["tub_frosted"]).volume >= 0.9 * ring.volume


def test_boot_is_far_off_the_line():
    tip, u = hx1.key_line("RESET")
    boot, _ = hx1.key_line("BOOT")
    off = (boot - tip) - u * (boot - tip).dot(u)
    assert off.length >= 5.0


@pytest.mark.parametrize("key", ["reset", "boot", "key3", "key2", "key1"])
def test_the_case_stands_off_every_edge_button(parts, key):
    for shell in ("tub_frosted", "tub_grey", "faceplate"):
        assert parts[shell].distance_to(parts[f"board_key_{key}"]) >= hx1.KEY_CLEAR - 0.05, shell
