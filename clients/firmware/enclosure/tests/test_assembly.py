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
        # Seam plug: the yoke and header ride in with the board; the housing,
        # in its shuttle, drops into the cage on the tub side.
        "yoke": sh["yoke"],
        "header": env["xh_header"],
        "shuttle": sh["shuttle"],
        "housing": env["xh_housing"],
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


def _boxes_meet(a, b) -> bool:
    """Axis-aligned boxes intersect (BoundBox.overlaps is False when one box
    contains the other, which made the tub invisible as an obstacle)."""
    return (
        a.min.X <= b.max.X
        and b.min.X <= a.max.X
        and a.min.Y <= b.max.Y
        and b.min.Y <= a.max.Y
        and a.min.Z <= b.max.Z
        and b.min.Z <= a.max.Z
    )


def _overlap(shape, obstacles):
    total = 0.0
    bb = shape.bounding_box()
    for o in obstacles:
        if _boxes_meet(bb, o.bounding_box()):
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
# its back; "in from the front" is +Y. Holders go straight in.
STEPS = [
    ("rocker", (["rocker"], _path((-20.0, 0, 0)), ["tub"])),
    ("switch", (["switch"], _path((0, -20.0, 0)), ["tub", "rocker"])),
    ("plunger", (["plunger"], _path((20.0, 0, 0)), ["tub", "switch"])),
    ("lever", (["lever", "pin"], _path((0, -12.0, 0)), ["tub", "switch", "plunger"])),
    (
        "holder_0",
        (
            ["holder_0"],
            _path((0, -30.0, 0)),
            ["tub", "rocker", "switch", "lever", "pin"],
        ),
    ),
    (
        "holder_1",
        (
            ["holder_1"],
            _path((0, -30.0, 0)),
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
    (
        "plug housing into its cage",
        (
            ["shuttle", "housing"],
            _path((0, -30.0, 0)),
            ["tub", "holder_0", "holder_1", "bridge", "switch", "lever", "pin"],
        ),
    ),
    ("plug into faceplate", (["plug"], _path((0, 12.0, 0)), ["faceplate"])),
    ("speaker onto plug", (["speaker"], _path((0, 15.0, 0)), ["faceplate", "plug"])),
    (
        "board onto standoffs",
        (["board", "yoke", "header"], _path((0, 12.0, 0)), ["faceplate", "plug", "speaker"]),
    ),
    (
        "close the case",
        (
            ["faceplate", "plug", "speaker", "board", "yoke", "header"],
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
                "shuttle",
                "housing",
            ],
        ),
    ),
]


@pytest.mark.parametrize(("name", "step"), STEPS, ids=[s[0] for s in STEPS])
def test_each_part_has_a_clear_way_in(model, name, step):
    moving, path, installed = step
    blocked = _clear(model, moving, path, installed)
    assert blocked is None, f"{name}: blocked at offset {blocked[0]} by {blocked[1]} mm^3"


# Tub-side wires, laid in from the open front before the bridge goes on.
TUB_WIRES = ["cable_bat_pos", "cable_bat_sw", "cable_bat_neg", "cable_ptt_a", "cable_ptt_b"]


@pytest.mark.parametrize("name", TUB_WIRES)
def test_tub_wires_can_be_laid_in_from_the_front(name):
    """Every tub wire, away from its two soldered or crimped ends, has nothing
    of the tub in front of it: it can be laid into place from the open front
    (clips snap over it; lips flex and are left out). A wire threaded behind a
    closed wall could not be fitted once its ends are on."""
    from build123d import Box, Pos

    c = hx1.build()
    tub = c.shells["tub"]
    wire = c.envelopes[name]
    path, r = wire.centreline, 0.3
    blocked = []
    for k in range(3, 18):
        p = path.position_at(k / 20)
        depth = p.Y - hx1.Y_FRONT
        ray = Pos(p.X, hx1.Y_FRONT + depth / 2 - 0.6, p.Z) * Box(2 * r, depth - 1.2, 2 * r)
        if (ray & tub).volume > 0.01:
            blocked.append((round(p.X, 1), round(p.Y, 1), round(p.Z, 1)))
    assert blocked == [], f"{name}: tub material in front of it at {blocked}"
