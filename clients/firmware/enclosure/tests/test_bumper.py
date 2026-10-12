"""The TPU bumper (huxcase.bumper): thin sides and thick corners (Mario,
2026-10-12), clear of every control, and the lever safe from a table."""

from __future__ import annotations

import math

import pytest
from huxcase import bumper as bm
from huxcase.concepts import hx1


@pytest.fixture(scope="module")
def band():
    return bm.bumper()


@pytest.fixture(scope="module")
def parts():
    return {p.name: p.shape for p in hx1.build().parts}


def test_one_solid(band):
    assert band.is_valid
    assert len(band.solids()) == 1


def test_thin_sides_thick_corners():
    """Mario asked for corner protection without a wide band: the outer
    corners are tighter than the case's R8, so the TPU fills the round."""
    cx, cz = hx1.W / 2 - hx1.R_FACE, hx1.H - hx1.R_FACE
    ox, oz = hx1.W / 2 + bm.WALL - bm.R_OUT, hx1.H + bm.WALL - bm.R_OUT
    corner = math.hypot(ox - cx, oz - cz) + bm.R_OUT - hx1.R_FACE
    assert bm.WALL <= 1.5
    assert corner >= 4.0


def test_size(band):
    bb = band.bounding_box()
    assert bb.size.X <= hx1.W + 2 * bm.WALL + 0.01  # 73 wide
    assert bb.max.Z - bm.LOOP_LIFT - bm.LOOP_HOLE - bm.LOOP_RIM <= hx1.H + bm.WALL + 0.01


@pytest.mark.parametrize(
    ("part", "clear"),
    [
        ("ptt_bar", 0.9),  # the lever moves; TPU dragging on it could hold the switch closed
        ("rocker_cap", 1.0),
        ("grille_plug", 0.25),
        ("case_screws_face", 1.5),
        ("case_screws_floor", 0.5),
        ("board_usb_c", 3.0),
    ],
)
def test_clear_of_the_controls(band, parts, part, clear):
    assert band.distance_to(parts[part]) >= clear


def test_touches_the_case_only_where_the_lips_clamp(parts, monkeypatch):
    """Nominal round the band: with the lips' clamp off, it touches nothing."""
    monkeypatch.setattr(bm, "CLAMP", 0.0)
    free = bm.bumper()
    for name in ("faceplate", "tub_grey", "tub_frosted"):
        assert (free & parts[name]).volume < 1e-3, name


def test_a_table_cannot_click_the_lever():
    """Lying on its right side, a flat surface pushes the lever in at most as
    far as it stands proud of the band; at the switch that stays short of
    the click (hx1.click_travel), with margin."""
    proud = hx1.BAR_T - bm.WALL
    at_switch = proud * (hx1.TS_Z - hx1.PIVOT_Z) / (hx1.BAR_Z1 - hx1.PIVOT_Z)
    assert proud > 0
    assert at_switch < 0.8 * hx1.click_travel()


def test_lever_window_opens_to_the_back(band):
    """The case goes in front first and the lever slides along the U:
    nothing of the band behind the lever, over its length."""
    from huxcase.shapes import bbox_solid

    behind = bbox_solid(
        hx1.X_OUT, hx1.X_OUT + 3,
        bm.LEVER_Y0, hx1.Y_BACK + bm.LIP_T + 1,
        bm.LEVER_Z[0] + 0.5, bm.LEVER_Z[1] - 0.5,
    )  # fmt: skip
    assert (band & behind).volume < 1e-3


def test_loop_takes_a_cord(band):
    from build123d import Cylinder, Pos, Rot

    hz = bm._top() + bm.LOOP_LIFT + bm.LOOP_HOLE / 2
    cord = Pos(bm.LOOP_X, bm.LOOP_Y, hz) * Rot(90, 0, 0) * Cylinder(bm.LOOP_HOLE / 2 - 0.05, 20)
    assert (band & cord).volume < 1e-3
