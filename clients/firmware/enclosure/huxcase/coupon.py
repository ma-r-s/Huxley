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

The plug pieces test the seam plug with Mario's own JST-XH parts before the
case is printed: the header block holds a B4B-XH-A exactly as the yoke does,
the cage block is the tub's cage pocket, and the shuttle is the real one.
Press the header into its block and the housing into the shuttle (both should
grip and not fall out), stand the shuttle in the cage block (it should rock a
little each way and not fall out sideways), then push the header block down
onto it about 0.5 mm off centre, down until it sits on the cage block's four
corner pillars (that is where the case closes). Report: does the mouth steer
it in, does it mate without catching, does it hold together there?

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


PLUG_POST = 4.0  # header block: its base sits this far behind the header's base


def _plug_base():
    """Half sizes of both plug blocks' bases: the cage block's pillars land on
    the header block's corners."""
    from huxcase.concepts import hx1

    cx, cy = hx1.CAGE_POCKET
    return cx + fits.MIN_WALL + 3.5, cy + fits.MIN_WALL + 3.5


def plug_header_block():
    """Faceplate half of the seam plug: the yoke's grip on the header, standing
    on a base. Print flat, as exported (mouth up)."""
    from huxcase.concepts import hx1

    post, base_h = PLUG_POST, 2.0  # the header's 3.4 mm tails hang free above the base
    grip, ribs = hx1.yoke_grip(-post)
    kx, ky = _plug_base()
    base = Pos(0, 0, -post - base_h / 2) * Box(2 * kx, 2 * ky, base_h)
    base = chamfer(base.edges().group_by(Axis.Z)[0], fits.FOOT_CHAMFER)
    return Pos(0, 0, post + base_h) * (base + grip + ribs)


def plug_cage_block():
    """Tub half: the shuttle's cage pocket as in HX-1 (end walls, corner tabs,
    the floor with its wire slot). Print flat, as exported (pocket up)."""
    from huxcase.concepts import hx1

    cx, cy = hx1.CAGE_POCKET
    kx, ky = cx + fits.MIN_WALL, cy + fits.MIN_WALL
    depth, floor_t, tab = hx1.XH_MATED_FLOOR - hx1.CAGE_S0, 1.2, hx1.CAGE_TAB
    bx, by = _plug_base()
    out = Pos(0, 0, floor_t / 2) * Box(2 * bx, 2 * by, floor_t)
    out = chamfer(out.edges().group_by(Axis.Z)[0], fits.FOOT_CHAMFER)
    # Corner pillars stop the header block exactly where the case closes, so
    # the coupon mates to the real depth (XH_SHORT short of fully seated).
    stop = floor_t + hx1.XH_MATED_FLOOR + PLUG_POST
    for sx in (-1, 1):
        for sy in (-1, 1):
            out += Pos(sx * (bx - 1.2), sy * (by - 1.2), stop / 2) * Box(2.0, 2.0, stop)
    for sx in (-1, 1):
        out += Pos(sx * (cx + kx) / 2, 0, floor_t + depth / 2) * Box(kx - cx, 2 * ky, depth)
        for sy in (-1, 1):
            out += Pos(sx * (cx - tab / 2), sy * (cy + ky) / 2, floor_t + depth / 2) * Box(
                tab, ky - cy, depth
            )
    row = -hw.XH_HEADER_D / 2 + hw.XH_ROW
    pl = hw.XH_PLATE_L / 2
    out -= Pos(0, row, floor_t / 2) * Box(2 * (pl - 0.75), 3.4, floor_t + 2)
    return out


def main() -> int:
    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    export_stl(plate(), str(out / "coupon_plate.stl"))
    export_stl(peg(), str(out / "coupon_peg.stl"))
    export_stl(pin_block(), str(out / "coupon_pin_block.stl"))
    export_stl(pin(), str(out / "coupon_pin.stl"))
    export_stl(plug_header_block(), str(out / "coupon_plug_header.stl"))
    export_stl(plug_cage_block(), str(out / "coupon_plug_cage.stl"))
    from huxcase import printability as pr

    shuttle = pr.printed_pieces()["shuttle"]
    pose = next(p for p in pr.POSES if p.piece == "shuttle")
    export_stl(pr.posed(shuttle, pose), str(out / "coupon_plug_shuttle.stl"))
    print(
        f"wrote coupon_plate, _peg, _pin_block, _pin, _plug_header, _plug_cage, _plug_shuttle in {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
