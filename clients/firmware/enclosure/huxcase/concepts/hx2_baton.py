"""HX-2 Baton: hold it like a microphone.

A round head (board flat under a dot-grille top, LED band, speaker facing
front) on a grip that carries the 18650. The PTT is a hinged trigger blade on
the front of the grip under the index finger, pressing an MX switch.
Device frame: X right, Y back (front at -Y), Z up.
"""

from __future__ import annotations

from build123d import Axis, Box, Cylinder, Location, Pos, Rot, fillet

from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.concepts._roundtop import R_OUT, RoundTop, T
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_back

HEAD_Z0 = 84.0
H = 148.0
TOP = RoundTop(z_top=H)
GRIP_W, GRIP_Y0, GRIP_Y1 = 36.0, -24.0, 20.0
CELL_XY = (0.0, 6.0)
CELL_Z0 = 2.6
SPK_Z = 107.2
SPK_LOC = Location((0, -25.5, SPK_Z), (-90, 0, 0))
PIVOT_Z = 84.0
MX_Z = 46.0
MX_PLATE_Y = -14.0
MX_LOC = Location((0, MX_PLATE_Y, MX_Z), (90, 0, 0))  # stem -> device -Y


def _grip(w: float, y0: float, y1: float, z0: float, z1: float, r: float):
    g = Pos(0, (y0 + y1) / 2, (z0 + z1) / 2) * Box(w, y1 - y0, z1 - z0)
    return fillet(g.edges().filter_by(Axis.Z), r)


def build() -> Concept:
    head = Pos(0, 0, (HEAD_Z0 + H) / 2) * Cylinder(R_OUT, H - HEAD_Z0)
    head = fillet(head.edges().group_by(Axis.Z)[-1], 4.0)
    head = fillet(head.edges().group_by(Axis.Z)[0], 6.0)
    grip = _grip(GRIP_W, GRIP_Y0, GRIP_Y1, 0.0, HEAD_Z0 + 8.0, 14.0)
    grip = fillet(grip.edges().group_by(Axis.Z)[0], 5.0)
    outer = head + grip

    head_in = Pos(0, 0, (HEAD_Z0 + T + H - T) / 2) * Cylinder(R_OUT - T, H - HEAD_Z0 - 2 * T)
    grip_in = _grip(GRIP_W - 2 * T, GRIP_Y0 + T, GRIP_Y1 - T, T, HEAD_Z0, 14.0 - T)
    body = outer - head_in - grip_in
    body -= Pos(0, 10.0, HEAD_Z0 + T / 2) * Cylinder(2.0, T + 1)  # wire pass-through

    add, facet, port = TOP.usb_facet()
    body = body + add - facet - port - TOP.slide_slot() - TOP.grille()

    pts = hex_holes(18.0, 2.8, 1.5, 4.0)
    body -= Location((0, -31.0, SPK_Z), (90, 0, 0)) * hole_field(pts, 1.5, 10.0)

    # Trigger channel on the grip front + liner + MX mount plate.
    ch_x, ch_z0, ch_z1 = 10.5, 34.0, HEAD_Z0 + 1.0
    body -= bbox_solid(-ch_x, ch_x, GRIP_Y0 - 1, GRIP_Y0 + T + 2.6, ch_z0, ch_z1)
    fy0, fy1 = GRIP_Y0 + T + 2.6, GRIP_Y0 + T + 4.2  # channel floor, clear of blade travel
    liner = bbox_solid(-ch_x - 1.6, ch_x + 1.6, fy0, fy1, ch_z0 - 1.6, ch_z1)
    liner += bbox_solid(-ch_x - 1.6, -ch_x, GRIP_Y0, fy1, ch_z0 - 1.6, ch_z1)
    liner += bbox_solid(ch_x, ch_x + 1.6, GRIP_Y0, fy1, ch_z0 - 1.6, ch_z1)
    liner += bbox_solid(-ch_x - 1.6, ch_x + 1.6, GRIP_Y0, fy1, ch_z0 - 1.6, ch_z0)
    liner -= Pos(0, (fy0 + fy1) / 2, MX_Z) * Box(16.0, 4.0, 16.0)
    plate = bbox_solid(
        -GRIP_W / 2 + T, GRIP_W / 2 - T, MX_PLATE_Y - 1.5, MX_PLATE_Y, MX_Z - 11, MX_Z + 11
    ) - Pos(0, MX_PLATE_Y - 0.75, MX_Z) * Box(hw.MX_CUTOUT, 3, hw.MX_CUTOUT)

    # Trigger blade, hinged at the top of the grip.
    blade_y0 = MX_PLATE_Y - hw.MX_STEM_TOP - 0.2
    blade = bbox_solid(-9.5, 9.5, blade_y0 - 6.6, blade_y0, 38.0, PIVOT_Z - 1.0)
    blade = fillet(blade.edges().filter_by(Axis.Z), 3.0)
    knuckle = Pos(0, blade_y0 - 3.3, PIVOT_Z - 1.0) * Rot(0, 90, 0) * Cylinder(3.3, 19.0)
    blade += knuckle
    pin = Pos(0, blade_y0 - 3.3, PIVOT_Z - 1.0) * Rot(0, 90, 0) * Cylinder(1.0, 23.0)

    cell_bay = Pos(*CELL_XY, CELL_Z0 + hw.CELL_BAY_L / 2) * Cylinder(
        hw.CELL_BAY_D / 2, hw.CELL_BAY_L
    )
    bulkhead = TOP.bulkhead(TOP.plug_floor - 0.2)
    spk_ring = Pos(0, -21.5, SPK_Z) * Rot(90, 0, 0) * (Cylinder(23.0, 3.0) - Cylinder(20.25, 4))
    spk_ring &= Pos(0, 0, (HEAD_Z0 + H) / 2) * Cylinder(R_OUT - T + 0.01, H - HEAD_Z0)
    boots = TOP.mic_boots()

    zc = TOP.pcb_top + hw.USB_C_CENTER_Z
    body -= on_back(label("HX-2", 3.0), 12.5, 30.2, zc - 1.0)
    body -= on_back(label("5V", 2.6), -11.0, 30.2, zc - 1.0)

    lo, hi = TOP.ring_cuts()
    grip_part = body & bbox_solid(-50, 50, -50, 50, -5, HEAD_Z0 - 0.15)
    lower = body & bbox_solid(-50, 50, -50, 50, HEAD_Z0 + 0.15, lo - 0.15)
    band = body & bbox_solid(-50, 50, -50, 50, lo + 0.15, hi - 0.15)
    top = body & bbox_solid(-50, 50, -50, 50, hi + 0.15, H + 5)
    lanyard = Pos(0, GRIP_Y1 - 4.0, -3.0) * (
        Rot(90, 0, 0) * Cylinder(5.5, 4.0) - Rot(90, 0, 0) * Cylinder(2.6, 6.0)
    )

    board = hw.place(hw.board_parts(), TOP.board_loc, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    cell = hw.place(hw.cell(), Location((*CELL_XY, CELL_Z0 + hw.CELL_BAY_L / 2)))
    mx = hw.place(hw.mx_switch(), MX_LOC)

    parts = [
        Part("grip", grip_part, "shell_dark"),
        Part("shell_lower", lower, "shell"),
        Part("led_band", band, "translucent"),
        Part("top_cap", top, "shell_dark"),
        Part("trigger_liner", liner, "shell"),
        Part("mx_plate", plate, "shell_inner"),
        Part("bulkhead", bulkhead, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("lanyard_loop", lanyard, "accent_dark"),
        Part("trigger", blade, "accent"),
        Part("trigger_pin", pin, "screw"),
        *board,
        *spk,
        *cell,
        *mx,
    ]
    envelopes = {
        "board": TOP.board_loc * hw.board_keepout(top=5.9),
        "speaker": SPK_LOC * hw.speaker()[0].shape,
        "cell": cell_bay,
        "mx": MX_LOC * bbox_solid(-7.8, 7.8, -7.8, 7.8, -hw.MX_BELOW_PLATE - hw.MX_SOCKET, 0),
        "trigger_travel": bbox_solid(
            -9.5, 9.5, blade_y0 - 6.6, blade_y0 + hw.MX_TRAVEL, 38.0, PIVOT_Z - 4.5
        ),
    }
    shells = {
        "body": body,
        "liner": liner,
        "plate": plate,
        "bulkhead": bulkhead,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
    }
    allowed = {
        ("board", "mic_boots"),
        ("mx", "plate"),
        ("mx", "trigger_travel"),
    }
    back_cc = cavity_cc(
        head_in & bbox_solid(-50, 50, -50, 50, HEAD_Z0 + T, TOP.plug_floor - 1.8),
        [envelopes["speaker"], spk_ring],
    )
    return Concept(
        "hx2", "HX-2 Baton", parts, envelopes, shells, allowed, {"back_volume_cc": back_cc}
    )
