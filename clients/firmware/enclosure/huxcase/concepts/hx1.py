"""HX-1: the walkie, two 18650s, mouse-switch PTT, frosted light cap.

Packing (see README "HX-1"):
- The board is rotated 90 deg in its plane so the BAT/SPK plugs and the RTC
  socket on its underside both land in the channel between two upright cells
  at +/-20.7 mm. Neither cell has to clear a plug. That channel is also the
  cable duct: speaker lead up, battery leads up and down, all hidden.
- The speaker sits under the board and its narrow magnet nests in the same
  channel, so the cells overlap the speaker's height instead of stacking.
- No bulkhead: the whole sealed interior is the speaker's back box.
- Board on top puts the ceramic antenna at the top of the device, out of
  the hand; three of the board's side keys end up on the top edge.
- PTT: an orange lever on the right side (right thumb), pinned at its bottom
  end inside a tilted wall channel. Its top end compresses a spring that
  pushes a printed plunger onto a 12 x 12 tactile switch housed above the
  right cell (spring in series: progressive press, then the click, and the
  click point does not depend on print accuracy).
- Light: a frosted ring sits on the board's 7 rear RGB LEDs and carries their
  light up into a frosted top cap, so the top of the device glows.
The internal assembly sits 1 mm left of centre (X0) so the right wall has room
for the lever channel and the left cell still hugs its wall.
Device frame: X right, Y back (front face at -Y), Z up.
"""

from __future__ import annotations

import math

from build123d import Axis, Box, Circle, Cylinder, Location, Pos, Rot, extrude, fillet

from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_front, rounded_box

W = 68.0
T = 2.4
X0 = -1.0  # internal assembly offset; frees 2 mm for the PTT channel on the right
R_FACE = 9.0  # front silhouette corners; tight so the cells reach the corners
Y_FRONT = -18.0
BOARD_TOP_Y = Y_FRONT + T + 6.0
PCB_BOTTOM_Y = BOARD_TOP_Y + hw.PCB_T
CELL_GAP = 2.5  # PCB underside to cell can, clears LEDs and chips
CELL_X = 20.7  # from the assembly centre; the board's plug zone fits between the cells
# Binding constraint: the speaker basket's back rim (r 15.8 at 12.3 mm deep)
# grazing the cells' inner flanks, not the board. 4.6 mm stays behind the PCB,
# which is where the light ring lives.
CELL_Y = 5.9
assert CELL_Y - hw.CELL_BAY_D / 2 >= PCB_BOTTOM_Y + CELL_GAP
CELL_Z0 = 7.6  # clears the 6.6 mm inner corner radius; room under the cells for contacts
CELL_TOP = CELL_Z0 + hw.CELL_BAY_L
Y_BACK = CELL_Y + hw.CELL_BAY_D / 2 + 0.2 + T
D = Y_BACK - Y_FRONT
assert X0 - CELL_X - hw.CELL_BAY_D / 2 >= -W / 2 + T + 0.15  # left cell clears its wall

SPK_Z = T + 0.4 + hw.SPK_D / 2
SPK_FRONT_Y = Y_FRONT + T + 0.5
SPK_LOC = Location((X0, SPK_FRONT_Y, SPK_Z), (-90, 0, 0))  # local -y -> device +Z
SPK_TAB_R, SPK_TAB_DEPTH = 15.3, 12.2  # solder tabs on the basket rim (STEP)
BOARD_Z = SPK_Z + hw.SPK_D / 2 + 0.3 + hw.PCB_R + 0.3 + 0.2
H = BOARD_Z + hw.PCB_R + 0.3 + 0.1 + T
# Board +x -> device -Z, board +y -> device +X, board +z (top face) -> device -Y.
BOARD_LOC = Location((X0, BOARD_TOP_Y, BOARD_Z)) * Rot(90, 0, 0) * Rot(0, 0, -90)
CAP_Z = 97.0  # frosted top cap from here up
BACK_FROST_Z = 36.0  # the back (and the sides' back half) is frosted from here up
# KCD1-101 rocker on the left side, above the left cell, fully recessed: the
# back had only 20 mm of depth behind it before the light ring.
ROCKER_Y, ROCKER_Z, ROCKER_RECESS = 5.3, 91.5, 7.2  # rocker top 0.2 below the surface
# Reserved pocket for a 1S protection board, in the grey bottom third behind the speaker.
PROT_X, PROT_Y, PROT_Z = 6.0, 10.75, 23.0

# PTT lever on the right side.
X_OUT = W / 2
PIVOT_Z, TS_Z, SWITCH_Y = 50.0, 89.5, 2.5
PIVOT_X = X_OUT - 1.0  # pin sits inside the wall, between the channel's side walls
STOP = 3.8  # lever travel at the switch axis before it lands on the stop post
PRESS_DEG = math.degrees(math.atan(STOP / (TS_Z - PIVOT_Z)))
BAR_T, BAR_W = 3.0, 18.0
BAR_Z0, BAR_Z1 = PIVOT_Z - 7.0, 98.0  # tail below the pin is the rest stop
CLEAR = 0.4
# Series spring between the lever's inner face and the plunger flange.
SPRING_REST = hw.SERIES_SPRING_FREE - 0.4  # 0.4 mm preload holds the lever on its rest stop
FLANGE_X = X_OUT - SPRING_REST  # plunger flange face (spring seat)
PLUNGER_TIP_X = FLANGE_X - 1.0 - 2.5  # 1 mm flange + 2.5 mm shaft onto the stem
# Tactile switch: stem +X, base toward the board's centre line.
TS_BASE_X = PLUNGER_TIP_X - hw.TACT_BODY - hw.TACT_STEM_H
TS_LOC = Location((TS_BASE_X, SWITCH_Y, TS_Z)) * Rot(0, 90, 0)


def click_travel(force: float = hw.TACT_FORCE, shim: float = 0.0) -> float:
    """Lever travel at the switch axis until the spring reaches the switch's
    actuation force. `shim` = washers under the plunger flange (adds preload)."""
    preload = (hw.SERIES_SPRING_FREE - SPRING_REST + shim) * hw.SERIES_SPRING_K
    return max(0.0, (force - preload) / hw.SERIES_SPRING_K)


# Light ring (frosted PLA) on the board's rear LEDs, in board coordinates.
RING_R0, RING_R1 = 20.8, 29.0  # inner edge clears the header tails the PTT wires use
RING_Z0, RING_Z1 = -hw.PCB_T - 1.6, -hw.PCB_T - 4.1  # just over the 1.5 mm LEDs


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
        (PIVOT_Z if g else BAR_Z0) - g,
        BAR_Z1 + g,
    )
    if g == 0:
        slab = fillet(slab.edges().filter_by(Axis.X), 3.0)
        slab = fillet(slab.edges().group_by(Axis.X)[-1], 0.8)
    knuckle = (
        Pos(PIVOT_X, SWITCH_Y, PIVOT_Z)
        * Rot(90, 0, 0)
        * Cylinder(2.0 + g, BAR_W - 6.0 + 2 * g)  # 12 mm knuckle: an M2 x 16 + nut spans it
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
        bar += Pos(X_OUT + BAR_T, SWITCH_Y, 74.0) * Rot(0, 90, 0) * Cylinder(1.4, 1.6)  # thumb dot
        bar += (
            Pos(X_OUT - 0.75, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * Cylinder(1.4, 1.5)
        )  # spring locator
    return bar


def _pressed(shape, fraction: float):
    """Rotate about the pin by `fraction` of full travel (top end moves toward -X)."""
    pivot = Pos(PIVOT_X, SWITCH_Y, PIVOT_Z)
    return pivot * Rot(0, -PRESS_DEG * fraction, 0) * Pos(-PIVOT_X, -SWITCH_Y, -PIVOT_Z) * shape


def _pressed_union():
    """Channel volume: the lever (plus clearance) at every 10% of its travel."""
    out = None
    for k in range(0, 11):
        s = _pressed(_bar(grow=CLEAR), k / 10)
        out = s if out is None else out + s
    return out


def _light_ring():
    """Frosted annulus behind the PCB over the LED circle (r = 22), board coordinates.

    Notches clear the BAT/SPK plugs and the RTC socket; three holes take the
    board's M2.5 screws, which clamp the ring to the PCB.
    """
    ring = extrude(Circle(RING_R1) - Circle(RING_R0), RING_Z0 - RING_Z1)
    ring = Pos(0, 0, RING_Z1) * ring
    ring -= bbox_solid(13.4, 40, -12.3, 11.3, -20, 5)  # BAT + SPK plugs and their leads
    ring -= bbox_solid(-40, -13.6, -9.8, -2.2, -20, 5)  # RTC socket
    for hx, hy in hw.HOLES:
        ring -= Pos(hx, hy, 0) * Cylinder(hw.HOLE_D / 2, 30)
        ring -= Pos(hx, hy, RING_Z1 + 0.6) * Cylinder(2.6, 1.3)  # screw-head counterbore
    return ring


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
        body -= Pos(x, Y_FRONT + 0.2, z) * Rot(90, 0, 0) * (Cylinder(3.4, 0.8) - Cylinder(2.7, 1))
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
    # then the lever's swept volume (plus clearance) is cut out of both. The
    # tail below the pin rests on the wall: that is the lever's rest stop.
    cell_edge = X0 + CELL_X + hw.CELL_BAY_D / 2
    backing = bbox_solid(
        cell_edge + 0.2,
        X_OUT - T + 0.1,
        SWITCH_Y - 11.0,
        SWITCH_Y + 11.0,
        PIVOT_Z - 5.0,
        CAP_Z - 0.4,
    )
    body += backing
    channel = None
    for k in range(0, 11):
        s = _pressed(_bar(grow=CLEAR), k / 10)
        channel = s if channel is None else channel + s
    body -= channel
    pin = (
        Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.0, BAR_W - 3.0)
    )  # M2 x 16 screw as the pin
    bar = _bar() - Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.1, 40)

    # 12 x 12 tactile switch in a cradle above the right cell. The lever
    # compresses the series spring, the spring pushes the plunger onto the
    # stem; a post stops the lever at STOP, after the click.
    ts_parts = hw.place(hw.tact_switch(), TS_LOC)
    ts_env = ts_parts[0].shape + ts_parts[2].shape
    half = hw.TACT_W / 2
    cradle = TS_LOC * (
        bbox_solid(-half - 1.2, half + 1.2, -half - 1.2, half + 1.2, -1.5, hw.TACT_BODY)
        - bbox_solid(-half - 0.1, half + 0.1, -half - 0.1, half + 0.1, 0, 10)
    )
    for sx in (-1, 1):
        for sy in (-1, 1):
            cradle -= TS_LOC * Pos(sx * 6.25, sy * 2.5, 0) * Box(1.4, 1.6, 5)  # leg holes
    tube_x0 = TS_BASE_X + hw.TACT_BODY
    tube = (
        Pos((tube_x0 + X_OUT - T + 0.1) / 2, SWITCH_Y, TS_Z)
        * Rot(0, 90, 0)
        * Cylinder(3.6, X_OUT - T + 0.1 - tube_x0)
    )
    tube -= (
        Pos(FLANGE_X - 1.2, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * Cylinder(3.3, 2 * 30)
    )  # flange+spring
    tube -= (
        Pos(PLUNGER_TIP_X, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * Cylinder(2.4, 2 * 4.0)
    )  # shaft bore
    ts_cradle = ((cradle + tube) & inner) - _pressed_union()
    body -= (
        Pos((tube_x0 + X_OUT) / 2, SWITCH_Y, TS_Z)
        * Rot(0, 90, 0)
        * Cylinder(3.65, X_OUT - tube_x0)
    )
    # The lever travels slightly further at the post's top edge than at its axis.
    stop_x = X_OUT - STOP * (TS_Z + 1.4 - PIVOT_Z) / (TS_Z - PIVOT_Z) - 0.05
    stop_post = (
        Pos((tube_x0 + 4 + stop_x) / 2, SWITCH_Y + 7.0, TS_Z)
        * Rot(0, 90, 0)
        * Cylinder(1.4, stop_x - tube_x0 - 4)
    )
    body += stop_post
    plunger = Pos(FLANGE_X - 0.5, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * Cylinder(3.0, 1.0)
    plunger += Pos(PLUNGER_TIP_X + 1.25, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * Cylinder(2.2, 2.5)
    plunger += Pos(FLANGE_X + 0.75, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * Cylinder(1.4, 1.5)
    spring = hw.spring(SPRING_REST)
    spring = Part("spring", Pos(FLANGE_X, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * spring.shape, "metal")

    # Frosted light ring on the LEDs (clamped by the board screws) + frosted cap.
    ring = BOARD_LOC * _light_ring()

    body -= on_front(label("huxley", 2.4), 6.0, Y_FRONT, CAP_Z - 4.2)
    body -= on_front(label("HX-1", 3.2), -20.0, Y_FRONT, CAP_Z - 4.4)

    # KCD1-101 on the left side: a mount inside the wall gives it a 1.5 mm
    # panel at the bottom of a 6 mm recess, so the rocker sits below the surface.
    panel_x = -W / 2 + ROCKER_RECESS
    by, bz = hw.ROCKER_BEZEL[0] / 2, hw.ROCKER_BEZEL[1] / 2  # long side along Y
    mount = (
        bbox_solid(
            -W / 2 + T - 0.1,
            panel_x + 1.5,
            max(ROCKER_Y - by - 1.7, PCB_BOTTOM_Y + CELL_GAP + 0.1),  # stay behind the board
            ROCKER_Y + by + 1.7,
            ROCKER_Z - bz - 1.7,
            ROCKER_Z + bz + 1.7,
        )
        & inner
    )
    body += mount
    body -= bbox_solid(
        -W / 2 - 1,
        panel_x,
        ROCKER_Y - by - 0.5,
        ROCKER_Y + by + 0.5,
        ROCKER_Z - bz - 0.5,
        ROCKER_Z + bz + 0.5,
    )
    cy_, cz = hw.ROCKER_BODY[0] / 2, hw.ROCKER_BODY[1] / 2
    body -= bbox_solid(
        panel_x - 0.1,
        panel_x + 1.6,
        ROCKER_Y - cy_ - 0.1,
        ROCKER_Y + cy_ + 0.1,
        ROCKER_Z - cz - 0.1,
        ROCKER_Z + cz + 0.1,
    )
    # local +z (outward) -> device -X, local x (long side) -> device Y
    rocker_loc = Location((panel_x, ROCKER_Y, ROCKER_Z)) * Rot(0, -90, 0) * Rot(0, 0, 90)
    rocker = hw.place(hw.rocker(), rocker_loc)

    # Shells: grey front; back grey below BACK_FROST_Z; frosted upper back + top cap.
    seam_y = PCB_BOTTOM_Y + 1.0
    lower = body & bbox_solid(-60, 60, -60, 60, -10, CAP_Z - 0.15)
    cap = body & bbox_solid(-60, 60, -60, 60, CAP_Z + 0.15, 200)
    front = lower & bbox_solid(-60, 60, -60, seam_y - 0.15, -10, 200)
    back_all = lower & bbox_solid(-60, 60, seam_y + 0.15, 60, -10, 200)
    back = back_all & bbox_solid(-60, 60, -60, 60, -10, BACK_FROST_Z - 0.15)
    frosted = cap + (back_all & bbox_solid(-60, 60, -60, 60, BACK_FROST_Z + 0.15, 200))

    # Hardware.
    board = hw.place(hw.board_parts(), BOARD_LOC, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    tab = Location((X0, SPK_FRONT_Y + SPK_TAB_DEPTH, SPK_Z + SPK_TAB_R)) * Box(3.0, 1.0, 2.0)
    cells, bays = [], []
    for i, x in enumerate((X0 - CELL_X, X0 + CELL_X)):
        zc = CELL_Z0 + hw.CELL_BAY_L / 2
        cells += [
            Part(f"{p.name}_{i}", p.shape, p.kind)
            for p in hw.place(hw.cell(), Location((x, CELL_Y, zc)))
        ]
        cells.append(
            Part(f"cell_spring_{i}", Pos(x, CELL_Y, CELL_Z0 + 1.0) * Cylinder(4.0, 2.0), "metal")
        )
        cells.append(
            Part(f"cell_plate_{i}", Pos(x, CELL_Y, CELL_TOP - 0.7) * Cylinder(5.0, 0.4), "metal")
        )
        bays.append(Pos(x, CELL_Y, zc) * Cylinder(hw.CELL_BAY_D / 2, hw.CELL_BAY_L))
    # Parallel (1S2P): + plates bridged on top, - springs bridged underneath.
    bridge_top = bbox_solid(
        X0 - CELL_X, X0 + CELL_X, CELL_Y - 3, CELL_Y + 3, CELL_TOP + 0.1, CELL_TOP + 0.4
    )
    bridge_bot = bbox_solid(
        X0 - CELL_X, X0 + CELL_X, CELL_Y - 3, CELL_Y + 3, CELL_Z0 - 0.4, CELL_Z0 - 0.1
    )

    # Cable runs, all in the channel between the cells.
    bat_x, spk_x = to_device(17.81, -6.04)[0], to_device(17.81, 5.19)[0]
    plug_z = to_device(17.81, 0)[1]
    plug_y = PCB_BOTTOM_Y + hw.BOTTOM_CONN
    rocker_tab_x = panel_x + hw.ROCKER_BODY[2] + hw.ROCKER_TABS - 0.5
    prot = bbox_solid(
        X0 + PROT_X - hw.PROTECTION_PCB[1] / 2,
        X0 + PROT_X + hw.PROTECTION_PCB[1] / 2,
        PROT_Y - hw.PROTECTION_PCB[2] / 2,
        PROT_Y + hw.PROTECTION_PCB[2] / 2,
        PROT_Z - hw.PROTECTION_PCB[0] / 2,
        PROT_Z + hw.PROTECTION_PCB[0] / 2,
    )
    tab_y = SPK_FRONT_Y + SPK_TAB_DEPTH + 0.6
    cables = {
        "cable_spk": hw.cable(
            [
                (spk_x, plug_y, plug_z),
                (spk_x, plug_y + 3.0, plug_z - 1.0),
                (X0 + 2.5, 3.5, plug_z - 8.0),
                (X0 + 0.5, 1.5, SPK_Z + SPK_TAB_R + 3.0),
                (X0, tab_y, SPK_Z + SPK_TAB_R + 1.0),
            ]
        ),
        # + : plug -> rocker -> top bridge, hugging the board side of the channel.
        "cable_bat_pos": hw.cable(
            [
                (bat_x, plug_y, plug_z),
                (bat_x, plug_y + 3.0, plug_z + 1.5),
                (X0 - 7.0, 0.8, plug_z + 15.0),
                (rocker_tab_x + 1.5, 1.6, CELL_TOP + 1.5),
                (rocker_tab_x + 0.5, ROCKER_Y - hw.ROCKER_TAB_PITCH / 2, ROCKER_Z - 1.5),
                (rocker_tab_x, ROCKER_Y - hw.ROCKER_TAB_PITCH / 2, ROCKER_Z),
            ],
            d=1.2,
        ),
        "cable_bat_sw": hw.cable(
            [
                (rocker_tab_x, ROCKER_Y + hw.ROCKER_TAB_PITCH / 2, ROCKER_Z),
                (rocker_tab_x + 0.5, ROCKER_Y + hw.ROCKER_TAB_PITCH / 2, ROCKER_Z - 3.0),
                (rocker_tab_x - 3.0, CELL_Y + 0.5, CELL_TOP + 2.2),
                (X0 - CELL_X + 4.0, CELL_Y, CELL_TOP + 0.9),
            ],
            d=1.2,
        ),
        # - : plug -> protection board (P-) ... (B-) -> bottom bridge, behind the speaker.
        "cable_bat_neg": hw.cable(
            [
                (bat_x - 1.2, plug_y, plug_z),
                (bat_x - 1.5, plug_y + 3.0, plug_z - 2.0),
                (X0 - 5.0, 1.5, BACK_FROST_Z + 6.0),
                (X0 + PROT_X - 1.0, PROT_Y - 3.0, PROT_Z + 15.0),
                (X0 + PROT_X, PROT_Y - 2.4, PROT_Z + hw.PROTECTION_PCB[0] / 2 - 1.0),
            ],
            d=1.2,
        ),
        "cable_bat_neg2": hw.cable(
            [
                (X0 + PROT_X, PROT_Y - 2.4, PROT_Z - hw.PROTECTION_PCB[0] / 2 + 1.0),
                (X0 + PROT_X - 2.0, PROT_Y - 2.0, CELL_Z0 + 2.5),
                (X0 + 2.0, CELL_Y + 1.0, CELL_Z0 - 0.4),
            ],
            d=1.2,
        ),
    }
    legs_x = TS_BASE_X - hw.TACT_LEGS
    legs_z = TS_Z - 6.25
    tail_y = PCB_BOTTOM_Y + 1.5
    for name, (leg_y, hx) in {
        "cable_ptt_a": (SWITCH_Y - 2.5, -7.62),
        "cable_ptt_b": (SWITCH_Y + 2.5, -10.16),
    }.items():
        tx, tz = to_device(hx, 13.82 if hx > -9 else 16.36)
        cables[name] = hw.cable(
            [
                (legs_x, leg_y, legs_z),
                (legs_x - 0.5, min(leg_y, 1.2) - 0.5, legs_z - 0.8),  # behind the cradle
                (legs_x + 1.4, -1.8, CELL_TOP - 0.6),  # under the cradle, beside the bridge
                (
                    TS_BASE_X + hw.TACT_BODY + 0.8,
                    -3.0,
                    CELL_TOP - 0.2,
                ),  # clear of the cradle floor
                (tx + 0.4, -3.6, tz + 0.8),  # stay behind the ring until inside its hole
                (tx, tail_y + 1.5, tz + 0.3),
                (tx, tail_y, tz),
            ],
            d=1.0,
        )

    parts = [
        Part("shell_front", front, "shell"),
        Part("shell_back", back, "shell"),
        Part("frosted_back", frosted, "translucent"),
        *rocker,
        Part("protection_pcb", prot, "pcb_green"),
        Part("light_ring", ring, "glow"),
        Part("face_panel", face_panel, "shell_dark"),
        Part("standoffs", standoffs, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("ts_cradle", ts_cradle, "shell_inner"),
        Part("plunger", plunger, "shell_inner"),
        Part("light_pipe", pipe, "clear"),
        Part("ptt_bar", bar, "accent"),
        Part("ptt_pin", pin, "screw"),
        spring,
        *board,
        *spk,
        Part("speaker_tab", tab, "metal"),
        *cells,
        Part("bridge_top", bridge_top, "metal"),
        Part("bridge_bottom", bridge_bot, "metal"),
        *ts_parts,
        Part("cable_spk", cables["cable_spk"], "wire_black"),
        Part("cable_bat_pos", cables["cable_bat_pos"], "wire_red"),
        Part("cable_bat_neg", cables["cable_bat_neg"], "wire_black"),
        Part("cable_bat_neg2", cables["cable_bat_neg2"], "wire_black"),
        Part("cable_bat_sw", cables["cable_bat_sw"], "wire_red"),
        Part("cable_ptt_a", cables["cable_ptt_a"], "wire_white"),
        Part("cable_ptt_b", cables["cable_ptt_b"], "wire_white"),
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
        "tact_switch": ts_env,
        "spring": spring.shape,
        "bar_sweep": sweep,
        "bridge_top": bridge_top,
        "bridge_bottom": bridge_bot,
        "rocker": rocker[2].shape + rocker[3].shape,
        "protection_pcb": prot,
        **cables,
    }
    shells = {
        "body": body,
        "standoffs": standoffs,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
        "light_pipe": pipe,
        "ts_cradle": ts_cradle,
        "light_ring": ring,
    }
    allowed = {
        ("board", "standoffs"),
        ("board", "mic_boots"),
        ("board", "light_pipe"),
        ("board", "light_ring"),  # sits on the LEDs, inside the generic 2.5 mm keep-out
        ("tact_switch", "ts_cradle"),
        ("spring", "ts_cradle"),  # guided in its tube
        ("spring", "body"),  # seated in its pocket
        ("spring", "bar_sweep"),  # it is what the lever compresses
        ("bar_sweep", "spring"),
        ("bridge_top", "cable_bat_sw"),
        ("bridge_bottom", "cable_bat_neg2"),
        ("rocker", "body"),  # clipped into its panel
        ("rocker", "cable_bat_pos"),  # soldered to its tabs
        ("rocker", "cable_bat_sw"),
        ("protection_pcb", "cable_bat_neg"),  # soldered to its pads
        ("protection_pcb", "cable_bat_neg2"),
        ("cable_bat_pos", "cable_bat_sw"),
        ("cable_spk", "board"),  # leads start in the plug zone
        ("cable_bat_pos", "board"),
        ("cable_bat_neg", "board"),
        ("cable_bat_pos", "cable_bat_neg"),  # same lead out of one plug
        ("cable_bat_neg", "cable_bat_neg2"),
        ("cable_ptt_a", "board"),  # soldered to the header tails
        ("cable_ptt_b", "board"),
        ("cable_ptt_a", "cable_ptt_b"),
        ("cable_spk", "speaker"),  # soldered to the tab
    }
    occupied = [envelopes["speaker"], spk_ring, *bays, boss, backing, standoffs, ring]
    occupied += [p.shape for p in board if p.name == "board_pcb"]
    back_cc = cavity_cc(inner, occupied)
    return Concept("hx1", "HX-1", parts, envelopes, shells, allowed, {"back_volume_cc": back_cc})
