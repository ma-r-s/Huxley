"""HX-3 Radio: a little landscape table radio you carry by its handle.

Speaker left, board right (both face the front), 18650 lying along the
bottom under the speaker. The PTT is the handle itself: an orange squeeze bar
under the top rail, keyboard-spacebar mechanism (one MX switch + 3u
stabiliser) so it feels the same wherever the fingers close on it.
Device frame: X right, Y back (front at -Y), Z up.
"""

from __future__ import annotations

from build123d import Axis, Box, Cylinder, Location, Pos, Rot, fillet

from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_front, rounded_box

W, D, H = 128.0, 36.0, 72.0
Y_FRONT, Y_BACK = -18.0, 18.0
T = 2.4
SEAM_Y = -6.0

BOARD_X, BOARD_Z = W / 2 - T - 29.2, 31.0
BOARD_TOP_Y = Y_FRONT + T + 6.0
BOARD_LOC = Location((BOARD_X, BOARD_TOP_Y, BOARD_Z), (90, 0, 0))

SPK_X, SPK_Z = -W / 2 + T + 21.0, H - T - 21.0
SPK_LOC = Location((SPK_X, Y_FRONT + T + 0.5, SPK_Z), (-90, 0, 0))

CELL_X0 = -51.0  # clears the 11.6 mm inner corner radius
CELL_POS = (CELL_X0 + hw.CELL_BAY_L / 2, 4.6, T + 0.1 + hw.CELL_BAY_D / 2)

# Handle: an inverted U on top. The MX plate faces down inside the top rail.
HANDLE_X, HANDLE_Z1 = 52.0, 122.0
RAIL_Z0 = 102.0
OPEN_X = 37.0
HANDLE_D = 20.0
MX_PLATE_Z = RAIL_Z0 + 6.0
MX_LOC = Location((0, 0, MX_PLATE_Z), (180, 0, 0))  # stem -> device -Z
BAR_U = 3.0


def _board_to_device(x: float, y: float) -> tuple[float, float]:
    return BOARD_X + x, BOARD_Z + y


def build() -> Concept:
    cy = (Y_FRONT + Y_BACK) / 2
    outer = Pos(0, cy, H / 2) * rounded_box(W, D, H, 14.0, 4.0)
    inner = Pos(0, cy, H / 2) * rounded_box(W - 2 * T, D - 2 * T, H - 2 * T, 14.0 - T, 0.0)
    body = outer - inner

    pts = hex_holes(18.0, 2.8, 1.5, 4.0)
    holes = Location((SPK_X, Y_FRONT + 1.2, SPK_Z), (90, 0, 0)) * hole_field(pts, 1.5, 4.0)
    body -= holes
    disc = Pos(SPK_X, Y_FRONT + 0.4, SPK_Z) * Rot(90, 0, 0) * Cylinder(23.5, 0.8)
    body -= disc
    grille_disc = disc - holes
    body -= on_front(label("HX-3", 3.2), SPK_X, Y_FRONT, 10.0)
    body -= on_front(label("huxley", 2.4), BOARD_X + 19.0, Y_FRONT, 66.0)

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

    # Engraved ring on the face marking where the board (and its mics) live.
    ring = (
        Pos(BOARD_X, Y_FRONT + 0.2, BOARD_Z)
        * Rot(90, 0, 0)
        * (Cylinder(31.0, 0.6) - Cylinder(30.4, 1.0))
    )
    body -= ring

    lx, lz = _board_to_device(*hw.CHG_LED)
    body -= Pos(lx, Y_FRONT + 1.2, lz) * Rot(90, 0, 0) * Cylinder(0.8, 4.0)
    pipe = (
        Pos(lx, (Y_FRONT + BOARD_TOP_Y - 1.2) / 2, lz)
        * Rot(90, 0, 0)
        * Cylinder(0.75, BOARD_TOP_Y - 1.2 - Y_FRONT - 0.1)
    )

    usb_y = BOARD_TOP_Y - hw.USB_C_CENTER_Z
    body -= bbox_solid(BOARD_X - 4.8, BOARD_X + 4.8, usb_y - 1.9, usb_y + 1.9, -0.1, T + 0.1)
    body -= bbox_solid(BOARD_X - 6.6, BOARD_X + 6.6, usb_y - 3.6, usb_y + 3.6, -0.1, 1.2)
    sx = BOARD_X + 17.64
    body -= bbox_solid(sx - 2.8, sx + 2.8, BOARD_TOP_Y - 2.6, BOARD_TOP_Y + 0.6, -0.1, T + 0.1)

    standoffs = None
    for hx, hy in hw.HOLES:
        x, z = _board_to_device(hx, hy)
        s = (
            Pos(x, (Y_FRONT + T + BOARD_TOP_Y) / 2, z)
            * Rot(90, 0, 0)
            * (Cylinder(2.6, BOARD_TOP_Y - Y_FRONT - T) - Cylinder(1.2, 20))
        )
        standoffs = s if standoffs is None else standoffs + s

    spk_ring = (
        Pos(SPK_X, Y_FRONT + T + 2.0, SPK_Z)
        * Rot(90, 0, 0)
        * (Cylinder(23.0, 4.0) - Cylinder(20.25, 5.0))
    )
    spk_ring &= Pos(0, cy, H / 2) * Box(W - 2 * T + 0.02, D, H - 2 * T + 0.02)
    # Speaker chamber: wall between speaker and board, shelf over the cell.
    wall_x = SPK_X + 21.5
    chamber = bbox_solid(wall_x, wall_x + 1.6, Y_FRONT + T, Y_BACK - T, 24.0, H - T)
    chamber += bbox_solid(-W / 2 + T, wall_x + 1.6, Y_FRONT + T, Y_BACK - T, 24.0, 25.6)

    # Handle (separate print) with the squeeze bar.
    hy0, hy1 = -HANDLE_D / 2, HANDLE_D / 2
    handle = Pos(0, 0, (H - 2 + HANDLE_Z1) / 2) * Box(2 * HANDLE_X, HANDLE_D, HANDLE_Z1 - H + 2)
    handle = fillet(handle.edges().filter_by(Axis.Y).group_by(Axis.Z)[-1], 10.0)
    opening = Pos(0, 0, (H + RAIL_Z0) / 2) * Box(2 * OPEN_X, HANDLE_D + 2, RAIL_Z0 - H)
    opening = fillet(opening.edges().filter_by(Axis.Y).group_by(Axis.Z)[-1], 6.0)
    handle -= opening
    handle -= bbox_solid(-200, 200, -50, 50, -10, H - 0.2)  # sits on the body
    rail_in = bbox_solid(-OPEN_X + 2, OPEN_X - 2, hy0 + 2, hy1 - 2, RAIL_Z0 + 2, HANDLE_Z1 - 2)
    channel = bbox_solid(-29.6, 29.6, -9.0, 9.0, RAIL_Z0 - 0.1, MX_PLATE_Z)
    handle -= rail_in + channel
    plate = bbox_solid(-OPEN_X + 2, OPEN_X - 2, hy0 + 2, hy1 - 2, MX_PLATE_Z, MX_PLATE_Z + 1.5)
    spacing = hw.STAB_SPACING[BAR_U]
    half = hw.MX_CUTOUT / 2
    plate -= MX_LOC * (
        bbox_solid(-half, half, -half, half, -2, 0.1)
        + bbox_solid(-spacing / 2 - 3.5, -spacing / 2 + 3.5, -6, 6, -2, 0.1)
        + bbox_solid(spacing / 2 - 3.5, spacing / 2 + 3.5, -6, 6, -2, 0.1)
    )
    handle += plate
    handle -= on_front(label("PTT", 3.0), 0, hy0, (RAIL_Z0 + HANDLE_Z1) / 2 - 1.0)

    bar_len = 19.05 * BAR_U - 1.1
    bar = MX_LOC * bbox_solid(-bar_len / 2, bar_len / 2, -8.0, 8.0, 4.0, 11.7)
    bar = fillet(bar.edges().filter_by(Axis.X), 3.0)
    bar -= MX_LOC * bbox_solid(-8.3, 8.3, -8.3, 8.3, 0.0, 7.0)
    for s in (-spacing / 2, spacing / 2):
        bar -= MX_LOC * bbox_solid(s - 3.8, s + 3.8, -6.5, 6.5, 0.0, 6.6)

    front = body & bbox_solid(-100, 100, -50, SEAM_Y - 0.15, -10, 200)
    back = body & bbox_solid(-100, 100, SEAM_Y + 0.15, 50, -10, 200)

    feet = None
    for fx in (-W / 2 + 14, W / 2 - 14):
        for fy in (Y_FRONT + 8, Y_BACK - 8):
            f = Pos(fx, fy, -0.6) * Cylinder(4.0, 1.2)
            feet = f if feet is None else feet + f

    board = hw.place(hw.board_parts(), BOARD_LOC, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    cell = hw.place(hw.cell(), Location(CELL_POS, (0, 90, 0)))
    mx = hw.place(hw.mx_switch(), MX_LOC)
    stab = hw.place([hw.stabiliser_pair(spacing)], MX_LOC)

    parts = [
        Part("shell_front", front, "shell"),
        Part("shell_back", back, "shell"),
        Part("handle", handle, "shell_dark"),
        Part("grille_disc", grille_disc, "shell_dark"),
        Part("chamber", chamber, "shell_inner"),
        Part("standoffs", standoffs, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("light_pipe", pipe, "clear"),
        Part("feet", feet, "rubber"),
        Part("squeeze_bar", bar, "accent"),
        *board,
        *spk,
        *cell,
        *mx,
        *stab,
    ]
    cell_bay = Location(CELL_POS, (0, 90, 0)) * Cylinder(hw.CELL_BAY_D / 2, hw.CELL_BAY_L)
    envelopes = {
        "board": BOARD_LOC * hw.board_keepout(top=5.9),
        "speaker": SPK_LOC * hw.speaker()[0].shape,
        "cell": cell_bay,
        "mx": MX_LOC * bbox_solid(-7.8, 7.8, -7.8, 7.8, -hw.MX_BELOW_PLATE - hw.MX_SOCKET, 0),
        "stab": stab[0].shape,
        "bar_travel": MX_LOC
        * bbox_solid(-bar_len / 2, bar_len / 2, -8.0, 8.0, 4.0 - hw.MX_TRAVEL, 11.7),
    }
    shells = {
        "body": body,
        "handle": handle,
        "chamber": chamber,
        "standoffs": standoffs,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
        "light_pipe": pipe,
    }
    allowed = {
        ("board", "standoffs"),
        ("board", "mic_boots"),
        ("board", "light_pipe"),
        ("mx", "handle"),
        ("stab", "handle"),
        ("mx", "bar_travel"),
        ("stab", "bar_travel"),
    }
    back_cc = cavity_cc(
        inner & bbox_solid(-W / 2, wall_x, -50, 50, 25.6, H),
        [envelopes["speaker"], spk_ring],
    )
    return Concept(
        "hx3", "HX-3 Radio", parts, envelopes, shells, allowed, {"back_volume_cc": back_cc}
    )
