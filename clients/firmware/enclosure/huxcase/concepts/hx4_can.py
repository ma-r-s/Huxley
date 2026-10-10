"""HX-4 Can: a soda-can-sized cylinder that stands on the table.

Board flat under a dot-grille top (talk down into it), translucent LED band
under the board, speaker on the front behind a curved grille, 18650 upright
at the back, vertical 3u PTT bar on the left side for the fingers.
Device frame: X right, Y back (front at -Y), Z up.
"""

from __future__ import annotations

from build123d import Axis, Cylinder, Location, Pos, Rot, fillet

from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.concepts._roundtop import R_OUT, RoundTop, T
from huxcase.concepts.hx1_classic import _keycap
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_back, on_left

H = 89.0
TOP = RoundTop(z_top=H)
CELL_XY = (0.0, 21.8)
CELL_Z0 = 2.6
SPK_Z = 47.0
SPK_LOC = Location((0, -25.5, SPK_Z), (-90, 0, 0))
PTT_Z = 40.0
PTT_LOC = Location((-R_OUT + 8.2, 0, PTT_Z), (0, -90, 0))
BAR_U = 3.0


def build() -> Concept:
    outer = Pos(0, 0, H / 2) * Cylinder(R_OUT, H)
    outer = fillet(outer.edges().group_by(Axis.Z)[-1], 4.0)
    outer = fillet(outer.edges().group_by(Axis.Z)[0], 3.0)
    inner = Pos(0, 0, (T + H - T) / 2) * Cylinder(R_OUT - T, H - 2 * T)
    body = outer - inner

    add, facet, port = TOP.usb_facet()
    body = body + add - facet - port - TOP.slide_slot() - TOP.grille()

    pts = hex_holes(18.0, 2.8, 1.5, 4.0)
    body -= Location((0, -31.0, SPK_Z), (90, 0, 0)) * hole_field(pts, 1.5, 10.0)

    spacing = hw.STAB_SPACING[BAR_U]
    bar_len = 19.05 * BAR_U - 1.1
    pocket_outer = (PTT_LOC * bbox_solid(-30.1, 30.1, -10.1, 10.1, -1.5, 8.2)) & outer
    pocket_inner = PTT_LOC * bbox_solid(-29.5, 29.5, -8.5, 8.5, 0.0, 12.0)
    half = hw.MX_CUTOUT / 2
    cutouts = PTT_LOC * (
        bbox_solid(-half, half, -half, half, -2, 0.1)
        + bbox_solid(-spacing / 2 - 3.5, -spacing / 2 + 3.5, -6, 6, -2, 0.1)
        + bbox_solid(spacing / 2 - 3.5, spacing / 2 + 3.5, -6, 6, -2, 0.1)
    )
    pocket = pocket_outer - pocket_inner - cutouts
    body -= pocket_inner

    cell_bay = Pos(*CELL_XY, CELL_Z0 + hw.CELL_BAY_L / 2) * Cylinder(
        hw.CELL_BAY_D / 2, hw.CELL_BAY_L
    )
    bulkhead = TOP.bulkhead(TOP.plug_floor - 0.2) - cell_bay - pocket_outer
    spk_ring = Pos(0, -21.5, SPK_Z) * Rot(90, 0, 0) * (Cylinder(23.0, 3.0) - Cylinder(20.25, 4))
    spk_ring &= Pos(0, 0, H / 2) * Cylinder(R_OUT - T + 0.01, H)
    boots = TOP.mic_boots()

    body -= on_back(label("HX-4", 3.2), 0, R_OUT, 30.0, sink=0.8)
    body -= on_back(label("huxley", 2.4), 0, R_OUT, 24.0, sink=0.8)
    body -= on_left(label("PTT", 2.6), -R_OUT, 0, 6.5)
    zc = TOP.pcb_top + hw.USB_C_CENTER_Z
    body -= on_back(label("5V", 2.6), -11.0, 30.2, zc - 1.0)

    lo, hi = TOP.ring_cuts()
    lower = body & bbox_solid(-50, 50, -50, 50, -5, lo - 0.15)
    band = body & bbox_solid(-50, 50, -50, 50, lo + 0.15, hi - 0.15)
    top = body & bbox_solid(-50, 50, -50, 50, hi + 0.15, H + 5)

    feet = None
    for i in range(3):
        f = Rot(0, 0, 90 + i * 120) * Pos(25.0, 0, -0.6) * Cylinder(4.5, 1.2)
        feet = f if feet is None else feet + f

    board = hw.place(hw.board_parts(), TOP.board_loc, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    cell = hw.place(hw.cell(), Location((*CELL_XY, CELL_Z0 + hw.CELL_BAY_L / 2)))
    mx = hw.place(hw.mx_switch(), PTT_LOC)
    stab = hw.place([hw.stabiliser_pair(spacing)], PTT_LOC)
    keycap = PTT_LOC * _keycap(bar_len, 13.0)

    parts = [
        Part("shell_lower", lower, "shell"),
        Part("led_band", band, "translucent"),
        Part("top_cap", top, "shell_dark"),
        Part("ptt_pocket", pocket, "shell"),
        Part("bulkhead", bulkhead, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("feet", feet, "rubber"),
        Part("ptt_bar", keycap, "accent"),
        *board,
        *spk,
        *cell,
        *mx,
        *stab,
    ]
    envelopes = {
        "board": TOP.board_loc * hw.board_keepout(top=5.9),
        "speaker": SPK_LOC * hw.speaker()[0].shape,
        "cell": cell_bay,
        "mx": PTT_LOC * bbox_solid(-7.8, 7.8, -7.8, 7.8, -hw.MX_BELOW_PLATE - hw.MX_SOCKET, 0),
        "stab": stab[0].shape,
        "ptt_travel": PTT_LOC
        * bbox_solid(-bar_len / 2, bar_len / 2, -6.5, 6.5, 7.0 - hw.MX_TRAVEL, 11.7),
    }
    shells = {
        "body": body,
        "pocket": pocket,
        "bulkhead": bulkhead,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
    }
    allowed = {
        ("board", "mic_boots"),
        ("mx", "pocket"),
        ("stab", "pocket"),
        ("mx", "ptt_travel"),
        ("stab", "ptt_travel"),
    }
    back_cc = cavity_cc(
        inner & bbox_solid(-50, 50, -50, 50, T, TOP.plug_floor - 1.8),
        [envelopes["speaker"], spk_ring, cell_bay, pocket_outer],
    )
    return Concept(
        "hx4", "HX-4 Can", parts, envelopes, shells, allowed, {"back_volume_cc": back_cc}
    )
