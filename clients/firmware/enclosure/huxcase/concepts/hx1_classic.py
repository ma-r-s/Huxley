"""HX-1 rev A: the first, one-cell HX-1 (superseded by hx1.py, kept for comparison).

Board faces the front (mics toward the user), speaker above it behind a dot
grille, 18650 standing upright behind the board, PTT bar on the left side
under the right thumb. Device frame: X right, Y back (front face at -Y), Z up.
"""

from __future__ import annotations

from build123d import Axis, Box, Cylinder, Location, Pos, Rot, Solid, fillet

from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_front, on_left, rounded_box

W, D, H = 66.0, 35.0, 125.0
Y_FRONT, Y_BACK = -18.0, 17.0
T = 2.4
SEAM_Y = -6.0

BOARD_Z = 31.0  # USB-C face lands on the inner bottom wall
BOARD_TOP_Y = Y_FRONT + T + 6.0  # 6 mm over the tallest top-side part
BOARD_LOC = Location((0, BOARD_TOP_Y, BOARD_Z), (90, 0, 0))  # board +z -> device -Y

CELL_POS = (-5.0, 4.0, 2.6 + hw.CELL_BAY_L / 2)
BULKHEAD_Z = 78.0
SPK_Z = 101.0
SPK_LOC = Location((0, Y_FRONT + T + 0.5, SPK_Z), (-90, 0, 0))  # cone faces -Y

PTT_Z, PTT_Y = 62.0, 5.0
PTT_PLATE_X = -W / 2 + 8.2  # keycap top ends 3.5 mm proud of the side
PTT_LOC = Location((PTT_PLATE_X, PTT_Y, PTT_Z), (0, -90, 0))  # stem -> device -X
BAR_U = 2.0


def _board_to_device(x: float, y: float) -> tuple[float, float]:
    """Board (x, y) -> device (x, z) on the board plane."""
    return x, BOARD_Z + y


def _keycap(length: float, width: float) -> Solid:
    cap = Pos(0, 0, 9.35) * Box(length, width, 4.7)
    cap = fillet(cap.edges().filter_by(Axis.Z), width / 2 - 0.1)
    cap = fillet(cap.edges().group_by(Axis.Z)[-1], 1.2)
    return cap


def build() -> Concept:
    cx, cy = 0.0, (Y_FRONT + Y_BACK) / 2
    outer = Pos(cx, cy, H / 2) * rounded_box(W, D, H, 12.0, 3.0)
    inner = Pos(cx, cy, H / 2) * rounded_box(W - 2 * T, D - 2 * T, H - 2 * T, 12.0 - T, 0.0)
    body = outer - inner

    # Speaker grille: hex dot field in front of the cone.
    pts = hex_holes(18.0, 2.8, 1.5, 4.0)
    holes = Location((0, Y_FRONT + 1.2, SPK_Z), (90, 0, 0)) * hole_field(pts, 1.5, 4.0)
    body -= holes
    # Black speaker panel inlaid 0.8 mm into the face (two-tone front).
    panel = Pos(0, Y_FRONT + 0.4, 99.0) * Box(54.0, 0.8, 44.0)
    panel = fillet(panel.edges().filter_by(Axis.Y), 9.0)
    body -= panel
    face_panel = panel - holes
    # Engraved monospace labels.
    body -= on_front(label("HX-1", 3.2), -20.0, Y_FRONT, 9.0)
    body -= on_front(label("huxley", 2.4), 19.0, Y_FRONT, 9.3)
    body -= on_left(label("PTT", 2.6), -W / 2, PTT_Y, PTT_Z + 22.5)

    # Mic ports + boots that duct each port straight onto its mic.
    boots = None
    for mx, my in hw.MICS:
        x, z = _board_to_device(mx, my)
        body -= Pos(x, Y_FRONT + 1.2, z) * Rot(90, 0, 0) * Cylinder(0.6, 4.0)
        boot = (
            Pos(x, (Y_FRONT + T + BOARD_TOP_Y - 1.5) / 2, z)
            * Rot(90, 0, 0)
            * (Cylinder(2.1, BOARD_TOP_Y - 1.5 - Y_FRONT - T) - Cylinder(1.0, 20))
        )
        boots = boot if boots is None else boots + boot

    # Charge LED light pipe.
    lx, lz = _board_to_device(*hw.CHG_LED)
    body -= Pos(lx, Y_FRONT + 1.2, lz) * Rot(90, 0, 0) * Cylinder(0.8, 4.0)
    pipe = (
        Pos(lx, (Y_FRONT + BOARD_TOP_Y - 1.2) / 2, lz)
        * Rot(90, 0, 0)
        * Cylinder(0.75, BOARD_TOP_Y - 1.2 - Y_FRONT - 0.1)
    )

    # USB-C opening + recess for the plug overmould (wall thinned to 1.2 mm).
    usb_y = BOARD_TOP_Y - hw.USB_C_CENTER_Z
    body -= bbox_solid(-4.8, 4.8, usb_y - 1.9, usb_y + 1.9, -0.1, T + 0.1)
    body -= bbox_solid(-6.6, 6.6, usb_y - 3.6, usb_y + 3.6, -0.1, 1.2)
    # Recessed service slot under the battery ON/OFF slide switch.
    body -= bbox_solid(15.0, 20.6, BOARD_TOP_Y - 2.6, BOARD_TOP_Y + 0.6, -0.1, T + 0.1)

    # Board standoffs (heat-set inserts; screws come from behind the PCB).
    standoffs = None
    for hx, hy in hw.HOLES:
        x, z = _board_to_device(hx, hy)
        s = (
            Pos(x, (Y_FRONT + T + BOARD_TOP_Y) / 2, z)
            * Rot(90, 0, 0)
            * (Cylinder(2.6, BOARD_TOP_Y - Y_FRONT - T) - Cylinder(1.2, 20))
        )
        standoffs = s if standoffs is None else standoffs + s

    # Speaker: clamp ring on the front wall + bulkhead that seals the back volume.
    ring_y0 = Y_FRONT + T
    spk_ring = (
        Pos(0, ring_y0 + 2.0, SPK_Z) * Rot(90, 0, 0) * (Cylinder(23.0, 4.0) - Cylinder(20.25, 5.0))
    )
    bulkhead = bbox_solid(
        -W / 2 + T, W / 2 - T, Y_FRONT + T, Y_BACK - T, BULKHEAD_Z, BULKHEAD_Z + 1.6
    )

    # PTT pocket: MX plate recessed 8.2 mm into the left wall.
    half = hw.MX_CUTOUT / 2
    spacing = hw.STAB_SPACING[BAR_U]
    bar_len = 19.05 * BAR_U - 1.1
    pocket_outer = PTT_LOC * bbox_solid(-21.6, 21.6, -10.1, 10.1, -1.5, 8.2)
    pocket_inner = PTT_LOC * bbox_solid(-20.0, 20.0, -8.5, 8.5, 0.0, 9.0)
    cutouts = PTT_LOC * (
        bbox_solid(-half, half, -half, half, -2, 0.1)
        + bbox_solid(-spacing / 2 - 3.5, -spacing / 2 + 3.5, -6, 6, -2, 0.1)
        + bbox_solid(spacing / 2 - 3.5, spacing / 2 + 3.5, -6, 6, -2, 0.1)
    )
    pocket = pocket_outer - pocket_inner - cutouts
    bulkhead -= pocket_outer
    body -= pocket_inner

    # Seam: front and back halves with a 0.3 mm shadow gap.
    front = body & bbox_solid(-50, 50, -50, SEAM_Y - 0.15, -10, 200)
    back = body & bbox_solid(-50, 50, SEAM_Y + 0.15, 50, -10, 200)

    # Front-panel screws in the four corners (TE-style exposed hardware).
    screws = None
    for sx in (-W / 2 + 6.5, W / 2 - 6.5):
        for sz in (6.5, H - 6.5):
            s = Pos(sx, Y_FRONT + 0.4, sz) * Rot(90, 0, 0) * Cylinder(2.3, 1.0)
            screws = s if screws is None else screws + s

    lanyard = Pos(-21.0, cy, H + 4.0) * Cylinder(5.0, 8.0) - Pos(-21.0, cy, H + 4.5) * Rot(
        90, 0, 0
    ) * Cylinder(1.6, 20.0)

    keycap = PTT_LOC * _keycap(bar_len, 13.0)

    board = hw.place(hw.board_parts(), BOARD_LOC, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    cell = hw.place(hw.cell(), Location(CELL_POS))
    mx = hw.place(hw.mx_switch(), PTT_LOC)
    stab = hw.place([hw.stabiliser_pair(spacing)], PTT_LOC)

    parts = [
        Part("shell_front", front, "shell"),
        Part("shell_back", back, "shell"),
        Part("face_panel", face_panel, "shell_dark"),
        Part("ptt_pocket", pocket, "shell"),
        Part("bulkhead", bulkhead, "shell_inner"),
        Part("standoffs", standoffs, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("light_pipe", pipe, "clear"),
        Part("lanyard_post", lanyard, "accent_dark"),
        Part("screws", screws, "screw"),
        Part("ptt_bar", keycap, "accent"),
        *board,
        *spk,
        *cell,
        *mx,
        *stab,
    ]

    envelopes = {
        "board": BOARD_LOC * hw.board_keepout(top=5.9),
        "speaker": SPK_LOC * hw.speaker()[0].shape,
        "cell": Pos(*CELL_POS) * Cylinder(hw.CELL_BAY_D / 2, hw.CELL_BAY_L),
        "mx": PTT_LOC * bbox_solid(-7.8, 7.8, -7.8, 7.8, -hw.MX_BELOW_PLATE - hw.MX_SOCKET, 0),
        "stab": stab[0].shape,
        "ptt_travel": PTT_LOC
        * bbox_solid(-bar_len / 2, bar_len / 2, -6.5, 6.5, 7.0 - hw.MX_TRAVEL, 11.7),
    }
    shells = {
        "body": body,
        "pocket": pocket,
        "bulkhead": bulkhead,
        "standoffs": standoffs,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
        "light_pipe": pipe,
    }
    allowed = {
        ("board", "standoffs"),
        ("board", "mic_boots"),
        ("board", "light_pipe"),
        ("mx", "pocket"),  # the switch clips into the pocket's plate
        ("stab", "pocket"),
        ("mx", "ptt_travel"),
        ("stab", "ptt_travel"),
    }
    back_cc = cavity_cc(
        inner & bbox_solid(-50, 50, -50, 50, BULKHEAD_Z + 1.6, H),
        [envelopes["speaker"], spk_ring, pocket_outer],
    )
    return Concept(
        id="hx1a",
        name="HX-1 rev A (one cell)",
        parts=parts,
        envelopes=envelopes,
        shells=shells,
        allowed=allowed,
        notes={"back_volume_cc": back_cc},
    )
