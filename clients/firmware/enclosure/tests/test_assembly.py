"""HX-1 can actually be assembled: every part has a clear way in.

A part can sit correctly at rest and still have no path to get there (the
first rev D did: the holders hit the screw blocks on the way in). Each step
below moves a part along its real insertion path, in the real order, past
everything installed before it. Along the way it may never overlap anything
more than it does at rest (crush ribs and seats touch at rest by design).

Crush ribs and the switch latch flex out of the way, so they are not
obstacles. Wires and the spring are flexible and are left out.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from build123d import Pos
from huxcase.concepts import hx1

STEP_MM = 2.0
TOL = 0.05  # mm^3


@pytest.fixture(scope="module")
def model():
    c = hx1.build()
    parts = {p.name: p.shape for p in c.parts}
    sh, env = c.shells, c.envelopes
    rocker = parts["rocker_bezel"] + parts["rocker_body"] + parts["rocker_tabs"]
    faceplate = sh["faceplate"] + sh["faceplate_standoffs"] + sh["faceplate_boots"]
    faceplate += sh["faceplate_light"]
    return {
        "tub": sh["tub"],
        "rocker": rocker,
        "switch": env["tact_switch"] + env["tact_stem"],
        "plunger": sh["plunger"],
        "lever": parts["ptt_bar"],
        "pin": sh["ptt_pin"],
        "holder_0": env["holder_0"],
        "holder_1": env["holder_1"],
        "bridge": sh["bridge"],
        "faceplate": faceplate,
        "plug": sh["grille_plug"],
        "speaker": env["speaker"],
        "board": env["board"] + env["board_screws"],
    }


def _path(start, end=(0.0, 0.0, 0.0), via=None):
    """Points from start to end (and through via), every STEP_MM, ending at rest."""
    legs = [start, *(via or []), end]
    pts = []
    for a, b in pairwise(legs):
        d = sum((bi - ai) ** 2 for ai, bi in zip(a, b, strict=True)) ** 0.5
        n = max(1, int(d / STEP_MM))
        pts += [
            tuple(ai + (bi - ai) * k / n for ai, bi in zip(a, b, strict=True)) for k in range(n)
        ]
    return [*pts, end]


def _overlap(shape, obstacles):
    total = 0.0
    bb = shape.bounding_box()
    for o in obstacles:
        if bb.overlaps(o.bounding_box()):
            total += (shape & o).volume
    return total


def _clear(m, moving, path, installed):
    """The first path point where `moving` overlaps more than it does at rest."""
    obstacles = [m[n] for n in installed]
    shape = sum((m[n] for n in moving[1:]), m[moving[0]]) if len(moving) > 1 else m[moving[0]]
    rest = _overlap(shape, obstacles)
    for p in path:
        hit = _overlap(Pos(*p) * shape, obstacles)
        if hit > rest + TOL:
            return p, round(hit - rest, 2)
    return None


# (what moves, its path, what is already in) in assembly order. The tub lies on
# its back; "in from the front" is +Y. Holders go in 1 mm toward the middle, then
# out under their lips.
STEPS = [
    ("rocker", (["rocker"], _path((-20.0, 0, 0)), ["tub"])),
    ("switch", (["switch"], _path((0, -20.0, 0)), ["tub", "rocker"])),
    ("plunger", (["plunger"], _path((20.0, 0, 0)), ["tub", "switch"])),
    ("lever", (["lever", "pin"], _path((0, -12.0, 0)), ["tub", "switch", "plunger"])),
    (
        "holder_0",
        (
            ["holder_0"],
            _path((1.0, -30.0, 0), via=[(1.0, 0, 0)]),
            ["tub", "rocker", "switch", "lever", "pin"],
        ),
    ),
    (
        "holder_1",
        (
            ["holder_1"],
            _path((-1.0, -30.0, 0), via=[(-1.0, 0, 0)]),
            ["tub", "rocker", "switch", "plunger", "lever", "pin", "holder_0"],
        ),
    ),
    (
        "bridge",
        (
            ["bridge"],
            _path((0, -30.0, 0)),
            ["tub", "holder_0", "holder_1", "switch", "lever", "pin"],
        ),
    ),
    ("plug into faceplate", (["plug"], _path((0, 12.0, 0)), ["faceplate"])),
    ("speaker onto plug", (["speaker"], _path((0, 15.0, 0)), ["faceplate", "plug"])),
    ("board onto standoffs", (["board"], _path((0, 12.0, 0)), ["faceplate", "plug", "speaker"])),
    (
        "close the case",
        (
            ["faceplate", "plug", "speaker", "board"],
            _path((0, -20.0, 0)),
            [
                "tub",
                "rocker",
                "switch",
                "plunger",
                "lever",
                "pin",
                "holder_0",
                "holder_1",
                "bridge",
            ],
        ),
    ),
]


@pytest.mark.parametrize(("name", "step"), STEPS, ids=[s[0] for s in STEPS])
def test_each_part_has_a_clear_way_in(model, name, step):
    moving, path, installed = step
    blocked = _clear(model, moving, path, installed)
    assert blocked is None, f"{name}: blocked at offset {blocked[0]} by {blocked[1]} mm^3"
