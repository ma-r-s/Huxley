"""HX-1 Duo: HX-1 Classic repacked around two 18650s, smaller than HX-1.

Packing moves (see README "HX-1 Duo"):
- The board is rotated 90 deg in its plane so the BAT/SPK plugs and the RTC
  socket on its underside both land in the channel between two upright cells
  at +/-20.7 mm. Neither cell has to clear a plug.
- The speaker sits under the board and its narrow magnet nests in the same
  channel, so the cells overlap the speaker's height instead of stacking.
- No bulkhead: the whole sealed interior is the speaker's back box.
- Board on top puts the ceramic antenna at the top of the device, out of
  the hand; three of the board's side keys end up on the top edge.
- PTT: an orange bar on the right side (right thumb). The right cell sits
  right behind that wall, so the bar is a lever: pinned at its bottom end
  inside a tilted channel (shallow over the cell, deep above it), its top end
  presses an MX switch housed above the cell. At rest it stands 3 mm proud
  and parallel to the side.
The internal assembly sits 1 mm left of centre (X0) so the right wall has room
for the channel and the left cell still hugs its wall.
Device frame: X right, Y back (front face at -Y), Z up.
"""

from __future__ import annotations

import math

from build123d import Axis, Box, Cylinder, Location, Pos, Rot, fillet

from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_front, rounded_box

W = 68.0
T = 2.4
X0 = -1.0  # internal assembly offset; frees 2 mm for the PTT channel on the right
R_FACE = 9.0  # front silhouette corners; tighter than HX-1 so the cells reach the corners
Y_FRONT = -18.0
BOARD_TOP_Y = Y_FRONT + T + 6.0
PCB_BOTTOM_Y = BOARD_TOP_Y + hw.PCB_T
CELL_GAP = 2.5  # PCB underside to cell can, clears LEDs and chips
CELL_X = 20.7  # from the assembly centre; the board's plug zone fits between the cells
# The binding constraint is the speaker basket's back rim (r 15.8 at 12.3 mm
# deep) grazing the cells' inner flanks, not the board: 4.2 mm stays behind the PCB.
CELL_Y = 5.9
assert CELL_Y - hw.CELL_BAY_D / 2 >= PCB_BOTTOM_Y + CELL_GAP
CELL_Z0 = 7.6  # clears the 6.6 mm inner corner radius; room under the cells for dock contacts
CELL_TOP = CELL_Z0 + hw.CELL_BAY_L
Y_BACK = CELL_Y + hw.CELL_BAY_D / 2 + 0.2 + T
D = Y_BACK - Y_FRONT
assert X0 - CELL_X - hw.CELL_BAY_D / 2 >= -W / 2 + T + 0.15  # left cell clears its wall

SPK_Z = T + 0.4 + hw.SPK_D / 2
SPK_LOC = Location((X0, Y_FRONT + T + 0.5, SPK_Z), (-90, 0, 0))
BOARD_Z = SPK_Z + hw.SPK_D / 2 + 0.3 + hw.PCB_R + 0.3 + 0.2
H = BOARD_Z + hw.PCB_R + 0.3 + 0.1 + T
# Board +x -> device -Z, board +y -> device +X, board +z (top face) -> device -Y.
BOARD_LOC = Location((X0, BOARD_TOP_Y, BOARD_Z)) * Rot(90, 0, 0) * Rot(0, 0, -90)

# PTT lever on the right side.
X_OUT = W / 2
PIVOT_Z, SWITCH_Z, SWITCH_Y = 50.0, 90.0, 2.0
PIVOT_X = X_OUT - 1.0  # pin sits inside the wall, between the channel's side walls
TRAVEL = hw.MX_TRAVEL
PRESS_DEG = math.degrees(math.atan(TRAVEL / (SWITCH_Z - PIVOT_Z)))
BAR_T, BAR_W = 3.0, 18.0
BAR_Z0, BAR_Z1 = PIVOT_Z - 2.5, SWITCH_Z + 6.0
STEM_TOP_X = X_OUT - 0.2  # just under the bar's inner face at rest
MX_LOC = Location((STEM_TOP_X - hw.MX_STEM_TOP, SWITCH_Y, SWITCH_Z), (0, 90, 0))  # stem -> +X
CLEAR = 0.4


def to_device(x: float, y: float) -> tuple[float, float]:
    """Board (x, y) -> device (x, z) on the board plane."""
    return X0 + y, BOARD_Z - x


def _bar(grow: float = 0.0):
    """The lever at rest: inner face on the wall plane, 3 mm proud, knuckle on the pin.

    `grow` inflates it (for cutting the channel with clearance).
    """
    g = grow
    slab = bbox_solid(
        X_OUT - g,
        X_OUT + BAR_T + g,
        SWITCH_Y - BAR_W / 2 - g,
        SWITCH_Y + BAR_W / 2 + g,
        BAR_Z0 - g,
        BAR_Z1 + g,
    )
    if g == 0:
        slab = fillet(slab.edges().filter_by(Axis.X), 3.0)
        slab = fillet(slab.edges().group_by(Axis.X)[-1], 0.8)
    knuckle = (
        Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(2.0 + g, BAR_W - 2.0 + 2 * g)
    )
    web = bbox_solid(
        PIVOT_X - g,
        X_OUT + g,
        SWITCH_Y - BAR_W / 2 + 1.0 - g,
        SWITCH_Y + BAR_W / 2 - 1.0 + g,
        PIVOT_Z - 2.0 - g,
        PIVOT_Z + 4.0 + g,
    )
    bar = slab + knuckle + web
    if g == 0:
        bar += Pos(X_OUT + BAR_T, SWITCH_Y, 75.0) * Rot(0, 90, 0) * Cylinder(1.4, 1.6)  # thumb dot
    return bar


def _pressed(shape, fraction: float):
    """Rotate about the pin by `fraction` of full travel (top end moves toward -X)."""
    pivot = Pos(PIVOT_X, SWITCH_Y, PIVOT_Z)
    return pivot * Rot(0, -PRESS_DEG * fraction, 0) * Pos(-PIVOT_X, -SWITCH_Y, -PIVOT_Z) * shape


def build() -> Concept:
    cy = (Y_FRONT + Y_BACK) / 2
    outer = Pos(0, cy, H / 2) * rounded_box(W, D, H, R_FACE, 3.0)
    inner = Pos(0, cy, H / 2) * rounded_box(W - 2 * T, D - 2 * T, H - 2 * T, R_FACE - T, 0.0)
    body = outer - inner

    # Speaker grille in a black inlaid panel at the bottom of the face.
    pts = hex_holes(18.0, 2.8, 1.5, 4.0)
    holes = Location((X0, Y_FRONT + 1.2, SPK_Z), (90, 0, 0)) * hole_field(pts, 1.5, 4.0)
    body -= holes
    panel = Pos(0, Y_FRONT + 0.4, SPK_Z + 0.5) * Box(56.0, 0.8, 41.0)
    panel = fillet(panel.edges().filter_by(Axis.Y), 9.0)
    body -= panel
    face_panel = panel - holes

    # Mic ports with a tactile "speak here" ring, and boots onto each mic.
    boots = None
    for mx, my in hw.MICS:
        x, z = to_device(mx, my)
        body -= Pos(x, Y_FRONT + 1.2, z) * Rot(90, 0, 0) * Cylinder(0.6, 4.0)
        body -= (
            Pos(x, Y_FRONT + 0.2, z) * Rot(90, 0, 0) * (Cylinder(3.4, 0.8) - Cylinder(2.7, 1.0))
        )
        length = BOARD_TOP_Y - 1.5 - Y_FRONT - T
        boot = (
            Pos(x, Y_FRONT + T + length / 2, z)
            * Rot(90, 0, 0)
            * (Cylinder(2.1, length) - Cylinder(1.0, 20))
        )
        boots = boot if boots is None else boots + boot

    lx, lz = to_device(*hw.CHG_LED)
    body -= Pos(lx, Y_FRONT + 1.2, lz) * Rot(90, 0, 0) * Cylinder(0.8, 4.0)
    pipe = (
        Pos(lx, (Y_FRONT + BOARD_TOP_Y - 1.2) / 2, lz)
        * Rot(90, 0, 0)
        * Cylinder(0.75, BOARD_TOP_Y - 1.2 - Y_FRONT - 0.1)
    )

    # USB-C exits the left side (board -y edge). Its face sits 2.2 mm inside
    # the wall, so a boss brings the wall to it and a deep pocket takes the
    # plug overmould.
    usb_y = BOARD_TOP_Y - hw.USB_C_CENTER_Z
    usb_face = X0 + hw.USB_C_FACE_Y
    boss = bbox_solid(
        -W / 2 + T - 0.05, usb_face - 0.15, usb_y - 5.0, usb_y + 5.0, BOARD_Z - 8.0, BOARD_Z + 8.0
    )
    body += boss
    body -= bbox_solid(
        -W / 2 - 1, usb_face - 1.0, usb_y - 3.8, usb_y + 3.8, BOARD_Z - 6.8, BOARD_Z + 6.8
    )
    body -= bbox_solid(
        -W / 2 - 1, usb_face + 0.1, usb_y - 1.9, usb_y + 1.9, BOARD_Z - 4.8, BOARD_Z + 4.8
    )

    standoffs = None
    for hx, hy in hw.HOLES:
        x, z = to_device(hx, hy)
        s = (
            Pos(x, (Y_FRONT + T + BOARD_TOP_Y) / 2, z)
            * Rot(90, 0, 0)
            * (Cylinder(2.6, BOARD_TOP_Y - Y_FRONT - T) - Cylinder(1.2, 20))
        )
        standoffs = s if standoffs is None else standoffs + s

    board_env = BOARD_LOC * hw.board_keepout(top=5.9)
    spk_ring = (
        Pos(X0, Y_FRONT + T + 2.0, SPK_Z)
        * Rot(90, 0, 0)
        * (Cylinder(23.0, 4.0) - Cylinder(20.25, 5.0))
    )
    spk_ring &= inner
    spk_ring -= board_env

    # PTT channel: backing fills the gap between the right cell and the wall,
    # then the lever's swept volume (plus clearance) is cut out of both.
    cell_edge = X0 + CELL_X + hw.CELL_BAY_D / 2
    backing = bbox_solid(
        cell_edge + 0.2,
        X_OUT - T + 0.1,
        SWITCH_Y - 11.0,
        SWITCH_Y + 11.0,
        BAR_Z0 - 3.0,
        BAR_Z1 + 3.0,
    )
    body += backing
    channel = None
    for k in range(0, 11):
        s = _pressed(_bar(grow=CLEAR), k / 10)
        channel = s if channel is None else channel + s
    body -= channel
    body -= Pos(X_OUT - 5.0, SWITCH_Y, SWITCH_Z) * Box(
        8.0, 16.4, 16.4
    )  # MX top housing, stays inside the wall
    pin = Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.0, BAR_W + 3.0)
    bar = _bar() - Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.1, 40)
    # Plate the switch clips into; local x maps to device -Z, so -9..7.5 keeps it above the cell.
    mx_bracket = MX_LOC * (
        bbox_solid(-9.0, 7.5, -7.6, 7.6, -1.5, 0) - bbox_solid(-7, 7, -7, 7, -2, 0.1)
    )
    mx_bracket &= inner

    body -= on_front(label("huxley", 2.4), 19.0, Y_FRONT, H - 7.5)
    body -= on_front(label("HX-1", 3.2), -19.0, Y_FRONT, H - 7.8)

    seam_y = PCB_BOTTOM_Y + 1.0
    front = body & bbox_solid(-60, 60, -60, seam_y - 0.15, -10, 200)
    back = body & bbox_solid(-60, 60, seam_y + 0.15, 60, -10, 200)

    board = hw.place(hw.board_parts(), BOARD_LOC, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    cells = []
    for i, x in enumerate((X0 - CELL_X, X0 + CELL_X)):
        cells += [
            Part(f"{p.name}_{i}", p.shape, p.kind)
            for p in hw.place(hw.cell(), Location((x, CELL_Y, CELL_Z0 + hw.CELL_BAY_L / 2)))
        ]
    mx = hw.place(hw.mx_switch(), MX_LOC)

    parts = [
        Part("shell_front", front, "shell"),
        Part("shell_back", back, "shell"),
        Part("face_panel", face_panel, "shell_dark"),
        Part("standoffs", standoffs, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("mx_bracket", mx_bracket, "shell_inner"),
        Part("light_pipe", pipe, "clear"),
        Part("ptt_bar", bar, "accent"),
        Part("ptt_pin", pin, "screw"),
        *board,
        *spk,
        *cells,
        *mx,
    ]

    bays = [
        Pos(x, CELL_Y, CELL_Z0 + hw.CELL_BAY_L / 2) * Cylinder(hw.CELL_BAY_D / 2, hw.CELL_BAY_L)
        for x in (X0 - CELL_X, X0 + CELL_X)
    ]
    sweep = None
    for k in range(0, 11):
        s = _pressed(_bar(), k / 10)
        sweep = s if sweep is None else sweep + s
    envelopes = {
        "board": board_env,
        "speaker": SPK_LOC * hw.speaker()[0].shape,
        "cell_0": bays[0],
        "cell_1": bays[1],
        "mx": MX_LOC * bbox_solid(-7.8, 7.8, -7.8, 7.8, -hw.MX_BELOW_PLATE - hw.MX_SOCKET, 0),
        "bar_sweep": sweep,
    }
    shells = {
        "body": body,
        "standoffs": standoffs,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
        "light_pipe": pipe,
        "mx_bracket": mx_bracket,
    }
    allowed = {
        ("board", "standoffs"),
        ("board", "mic_boots"),
        ("board", "light_pipe"),
        ("mx", "mx_bracket"),
    }
    occupied = [envelopes["speaker"], spk_ring, *bays, boss, backing, standoffs]
    occupied += [p.shape for p in board if p.name == "board_pcb"]
    back_cc = cavity_cc(inner, occupied)
    return Concept(
        "hx1d",
        "HX-1 Duo",
        parts,
        envelopes,
        shells,
        allowed,
        {"back_volume_cc": back_cc},
    )
