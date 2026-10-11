"""Nothing in HX-1 is loose: every part that could rattle has a rigid stop
within 0.5 mm in every direction.

Crush ribs take up the play in normal use, but they can creep; behind them
there must be a hard stop close by, or a drop would let the part travel and
knock. Moving a part 0.5 mm in any direction must push it into material of a
printed piece (or a screw) that it did not touch at rest.
"""

from __future__ import annotations

import pytest
from build123d import Pos
from huxcase.concepts import hx1

MOVE = 0.5
DIRS = {
    "+x": (1, 0, 0),
    "-x": (-1, 0, 0),
    "+y": (0, 1, 0),
    "-y": (0, -1, 0),
    "+z": (0, 0, 1),
    "-z": (0, 0, -1),
}


@pytest.fixture(scope="module")
def model():
    c = hx1.build()
    parts = {p.name: p.shape for p in c.parts}
    sh = c.shells
    faceplate = [
        sh["faceplate"],
        sh["faceplate_standoffs"],
        sh["faceplate_boots"],
        sh["faceplate_light"],
    ]
    rigid = {
        "faceplate": faceplate,
        "tub": [sh["tub"]],
        "bridge": [sh["bridge"]],
        "plug": [sh["grille_plug"]],
        "pin": [sh["ptt_pin"]],
        "screws": [c.envelopes["board_screws"], c.envelopes["bridge_screw"]],
        "speaker": [c.envelopes["speaker"]],
    }
    return c, parts, rigid


# part -> (what holds it, directions a spring or a modelled-elsewhere feature owns)
CASES = {
    "holder_0": (("tub", "bridge"), ()),
    "holder_1": (("tub", "bridge"), ()),
    "tact_switch": (("tub",), ()),
    "speaker": (("plug", "tub", "faceplate"), ()),
    "board_pcb": (("faceplate", "screws", "tub"), ()),
    "ptt_pin": (("tub", "faceplate"), ()),
    "grille_plug": (("faceplate", "speaker"), ()),
    "bridge": (("tub", "screws"), ("-y",)),  # its M3 x 6 holds it from the front
    # The rocker's own snap ears hold it outward (-x); they are not modelled.
    "rocker": (("tub",), ("-x",)),
    # The spring pushes the plunger onto the stem along x.
    "plunger": (("tub",), ("+x", "-x")),
    # Wires: the long runs sit in clips (prongs either side, a lip in front);
    # along their own length and toward the wall they are free by nature.
    "cable_bat_neg": (("tub",), ("+y", "+z", "-z")),
    "cable_bat_pos": (("bridge",), ("+y", "+z", "-z")),
}


def _solid(c, parts, name):
    if name == "rocker":  # the whole switch: its bezel is what stops it inward
        return parts["rocker_bezel"] + parts["rocker_body"] + parts["rocker_tabs"]
    if name in c.envelopes:
        return c.envelopes[name]
    if name in c.shells:
        return c.shells[name]
    return parts[name]


@pytest.mark.parametrize("name", sorted(CASES))
def test_part_has_a_hard_stop_in_every_direction(model, name):
    c, parts, rigid = model
    holders, exempt = CASES[name]
    shape = _solid(c, parts, name)
    stops = [s for h in holders for s in rigid[h] if s is not shape]
    loose = []
    for d, (dx, dy, dz) in DIRS.items():
        if d in exempt:
            continue
        moved = Pos(dx * MOVE, dy * MOVE, dz * MOVE) * shape
        # Material of a stop that the moved part enters and the part at rest did not.
        new = 0.0
        for s in stops:
            inter = moved & s
            if inter.volume > 1e-6:
                new += (inter - shape).volume if (inter & shape).volume > 1e-6 else inter.volume
        if new <= 0.01:
            loose.append(d)
    assert loose == [], f"{name} can travel more than {MOVE} mm toward {loose}"
