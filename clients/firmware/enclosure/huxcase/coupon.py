"""Fit-test coupon: print once on the P2S, report which holes fit, tune fits.py.

Plate (print flat, as exported) and one peg (print standing, as exported):

- Back row: vertical holes for the 6 mm peg, clearance per side 0.10 to 0.30.
- Front row: the same ladder as horizontal bores into the front face (they
  print oval, which is why they get their own row).
- Right: a pocket for Mario's 12 x 12 tact switch at fits.PART with crush
  ribs; heat-set insert bores M2 and M3; a pivot hole for an M2 screw.

Report, per row: the tightest hole the peg goes into by hand and stays
(-> fits.SNUG), and the tightest where it slides and turns freely
(-> fits.SLIDE / fits.SLIDE_HBORE). Then: does the switch drop in and stay,
do the inserts go in straight, does an M2 screw turn freely in the pivot hole.

The pin block is the lever pivot's own test: a printed Ø2.4 pin (lying flat,
as the lever pin prints) in four horizontal holes, .15 to .30 per side. Report
the tightest one it turns freely in without wobble (-> PIN_BORE in hx1).

Run: uv run python -m huxcase.coupon   (writes out/coupon_*.stl)
"""

from __future__ import annotations

from pathlib import Path

from build123d import Axis, Box, Cylinder, Pos, Rot, chamfer, export_stl

from huxcase import fits
from huxcase import hardware as hw
from huxcase.shapes import label

LADDER = (0.10, 0.15, 0.20, 0.25, 0.30)
PEG_D, PEG_L = 6.0, 16.0
PLATE = (82.0, 40.0, 9.0)
HOLE_X = tuple(7.0 + 11.0 * i for i in range(len(LADDER)))
VERT_Y, HBORE_Z, HBORE_DEPTH = 31.0, 4.5, 12.0


def plate():
    w, d, h = PLATE
    out = Pos(w / 2, d / 2, h / 2) * Box(w, d, h)
    out = chamfer(out.edges().group_by(Axis.Z)[0], fits.FOOT_CHAMFER)
    for x, c in zip(HOLE_X, LADDER, strict=True):
        r = PEG_D / 2 + c
        out -= Pos(x, VERT_Y, h / 2) * Cylinder(r, h + 2)
        out -= (
            Pos(x, HBORE_DEPTH / 2 - 0.5, HBORE_Z) * Rot(90, 0, 0) * Cylinder(r, HBORE_DEPTH + 1)
        )
        tag = f".{round(c * 100):02d}"  # mm per side
        out -= Pos(x, VERT_Y - 7.0, h - 0.4) * label(tag, 3.0, 1.0)
        out -= Pos(x, HBORE_DEPTH + 3.5, h - 0.4) * label(tag, 3.0, 1.0)

    # Tact switch pocket with crush ribs, exactly as on the HX-1 cradle.
    px, py = 71.0, 29.0
    pocket = hw.TACT_W / 2 + fits.PART
    depth = hw.TACT_BODY + 0.3
    out -= Pos(px, py, h - depth / 2) * Box(2 * pocket, 2 * pocket, depth + 0.01)
    rib_in, rw = pocket - fits.CRUSH_RIB, fits.CRUSH_RIB_W / 2
    for y0, y1 in ((-pocket, -rib_in), (rib_in, pocket)):
        for off in (-3.0, 3.0):
            out += Pos(px + off, py + (y0 + y1) / 2, h - depth / 2) * Box(2 * rw, y1 - y0, depth)
    for sx in (-1, 1):
        for sy in (-1, 1):
            out -= Pos(px + sx * 6.25, py + sy * 2.5, h / 2) * Box(1.4, 1.6, h + 2)  # legs

    # Heat-set insert bores (6 mm deep) and an M2 pivot hole.
    for x, bore, tag in (
        (64.0, fits.INSERT_BORE["M2"], "M2"),
        (71.0, fits.INSERT_BORE["M3"], "M3"),
    ):
        out -= Pos(x, 12.0, h - 3.0) * Cylinder(bore / 2, 6.01)
        out -= Pos(x, 5.5, h - 0.4) * label(tag, 2.5, 1.0)
    out -= Pos(78.0, 12.0, h / 2) * Cylinder(1.0 + fits.PIVOT, h + 2)
    out -= Pos(78.0, 5.5, h - 0.4) * label("P", 2.5, 1.0)
    return out


def peg():
    out = Pos(0, 0, PEG_L / 2) * Cylinder(PEG_D / 2, PEG_L)
    out = chamfer(out.edges().group_by(Axis.Z)[0], fits.FOOT_CHAMFER)
    return chamfer(out.edges().group_by(Axis.Z)[-1], 0.6)


PIN_LADDER = (0.15, 0.20, 0.25, 0.30)  # per side, around the printed Ø2.4 lever pin


def pin_block():
    """The lever pin's seat, four ways: horizontal holes (as the tub prints
    them) at four clearances, labelled on top. Print flat, as exported."""
    from huxcase.concepts import hx1

    w, d, h = 34.0, 10.0, 6.0
    out = Pos(w / 2, d / 2, h / 2) * Box(w, d, h)
    out = chamfer(out.edges().group_by(Axis.Z)[0], fits.FOOT_CHAMFER)
    for i, c in enumerate(PIN_LADDER):
        x = 4.5 + 8.3 * i
        r = hx1.PIN_D / 2 + c
        out -= Pos(x, d / 2, 2.6) * Rot(90, 0, 0) * Cylinder(r, d + 2)
        out -= Pos(x, d / 2, h - 0.4) * label(f".{round(c * 100):02d}", 2.4, 1.0)
    return out


def pin():
    """A test pin printed exactly like the lever pin: Ø2.4, lying flat."""
    from huxcase.concepts import hx1

    return Pos(0, 0, hx1.PIN_D / 2) * Rot(90, 0, 0) * Cylinder(hx1.PIN_D / 2, 16.0)


def main() -> int:
    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    export_stl(plate(), str(out / "coupon_plate.stl"))
    export_stl(peg(), str(out / "coupon_peg.stl"))
    export_stl(pin_block(), str(out / "coupon_pin_block.stl"))
    export_stl(pin(), str(out / "coupon_pin.stl"))
    print(f"wrote coupon_plate, coupon_peg, coupon_pin_block, coupon_pin .stl in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
