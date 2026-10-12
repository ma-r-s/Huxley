"""Every thin feature of HX-1 is printable with a 0.4 mm nozzle.

Walls drawn by the nozzle (thickness in X/Y of the print) need two
perimeters, 0.8 mm. Plates stacked in layers need whole layers. Bumps and
ribs that stand on a wall may be thinner: they are part of its perimeter.
"""

from __future__ import annotations

import pytest
from huxcase import fits
from huxcase import hardware as hw
from huxcase.concepts import hx1

WALLS = {
    "plunger tube": hx1.TUBE_R - hx1.TUBE_BORE,
    "lever knuckle round the pin": hx1.KNUCKLE_R - hx1.PIN_BORE / 2,
    "faceplate lip": hx1.LIP_W,
    "holder bridge": hx1.BRIDGE_T,
    "bridge front bar": hx1.CELL_BAR_T,
    "crush rib": fits.CRUSH_RIB_W,
    "case wall": hx1.T,
    "rear pin seat, outer wall": hx1.X_OUT - (hx1.PIVOT_X + hx1.PIN_BORE / 2),
    "rear pin seat, inner wall": (hx1.PIVOT_X - hx1.PIN_BORE / 2)
    - (hx1.X0 + hx1.CELL_X + hw.HOLDER_W / 2 + fits.PART),
}


@pytest.mark.parametrize("name", sorted(WALLS))
def test_walls_take_two_perimeters(name):
    assert WALLS[name] >= fits.MIN_WALL - 1e-9, f"{name}: {WALLS[name]:.2f} mm"


def test_plates_are_whole_layers():
    for name, t in {"grille flange": hx1.FLANGE_T, "floor pad": hx1.FLOOR_PAD}.items():
        layers = t / fits.LAYER
        assert abs(layers - round(layers)) < 1e-6 and round(layers) >= 3, name
