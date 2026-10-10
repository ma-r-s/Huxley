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
  end inside a tilted wall channel. Its top end carries an M2 adjuster screw
  that clicks a mouse microswitch housed above the right cell; a cut-down
  ballpoint-pen spring gives the travel and pushes it back.
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

# PTT lever on the right side.
X_OUT = W / 2
PIVOT_Z, MS_Z, SPRING_Z, SWITCH_Y = 50.0, 86.0, 92.8, 2.5
PIVOT_X = X_OUT - 1.0  # pin sits inside the wall, between the channel's side walls
STOP = 3.3  # lever travel at the switch before it lands on the stop post
PRESS_DEG = math.degrees(math.atan(STOP / (MS_Z - PIVOT_Z)))
BAR_T, BAR_W = 3.0, 18.0
BAR_Z0, BAR_Z1 = PIVOT_Z - 7.0, 98.0  # tail below the pin is the rest stop
MS_BUTTON_X = 30.6  # microswitch button tip at rest
MS_TOP_X = MS_BUTTON_X - hw.MS_BUTTON[2]
SCREW_PROUD = 0.7  # adjuster screw tip past the lever's inner face: click at ~2.9 mm
SPRING_REST = 11.0
CLEAR = 0.4
# Microswitch: button +X, length along Y, width along Z.
MS_LOC = Location((MS_TOP_X, SWITCH_Y, MS_Z)) * Rot(0, 90, 0) * Rot(0, 0, 90)

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
        bar += Pos(X_OUT + BAR_T, SWITCH_Y, 74.0) * Rot(0, 90, 0) * Cylinder(1.4, 1.6)  # thumb dot
        bar -= Pos(X_OUT + 0.5, SWITCH_Y, SPRING_Z) * Rot(0, 90, 0) * Cylinder(2.4, 1.2)  # seat
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
    pin = Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.0, BAR_W + 3.0)
    bar = _bar() - Pos(PIVOT_X, SWITCH_Y, PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.1, 40)

    # Mouse microswitch in a sleeve above the right cell; the lever's M2
    # adjuster screw clicks it. Pen spring in a pocket above it; a post stops
    # the lever at STOP so the switch never takes more than its overtravel.
    ms_parts = hw.place(hw.microswitch(), MS_LOC)
    ms_env = ms_parts[0].shape + ms_parts[1].shape
    sleeve = MS_LOC * (
        bbox_solid(
            -hw.MS_L / 2 - 1.2,
            hw.MS_L / 2 + 1.2,
            -hw.MS_W / 2 - 1.2,
            hw.MS_W / 2 + 1.2,
            -hw.MS_H,
            0,
        )
        - bbox_solid(-hw.MS_L / 2, hw.MS_L / 2, -hw.MS_W / 2, hw.MS_W / 2, -10, 1)
    )
    tie = bbox_solid(
        MS_TOP_X - 4.0,
        X_OUT - T + 0.1,
        SWITCH_Y + hw.MS_L / 2 + 0.1,
        SWITCH_Y + hw.MS_L / 2 + 1.2,
        MS_Z - 4.1,
        MS_Z + 4.1,
    )
    ms_cradle = ((sleeve + tie) & inner) - _pressed_union()
    spring_x0 = X_OUT - SPRING_REST
    spring_boss = (
        Pos((spring_x0 - 1.0 + X_OUT - T + 0.1) / 2, SWITCH_Y, SPRING_Z)
        * Rot(0, 90, 0)
        * (Cylinder(3.4, X_OUT - T + 0.1 - spring_x0 + 1.0))
    )
    spring_boss -= (
        Pos(X_OUT - 5.0, SWITCH_Y, SPRING_Z)
        * Rot(0, 90, 0)
        * Cylinder(hw.SPRING_D / 2 + 0.3, 2 * (X_OUT - 5.0 - spring_x0))
    )
    stop_x = X_OUT - STOP * (SPRING_Z - PIVOT_Z) / (MS_Z - PIVOT_Z) - 0.05
    stop_post = (
        Pos((spring_x0 + stop_x) / 2, SWITCH_Y + 6.0, SPRING_Z)
        * Rot(0, 90, 0)
        * Cylinder(1.4, stop_x - spring_x0)
    )
    body = body + spring_boss - channel + stop_post
    body -= MS_LOC * bbox_solid(  # pocket for the switch body and button in the backing
        -hw.MS_L / 2 - 0.2,
        hw.MS_L / 2 + 0.2,
        -hw.MS_W / 2 - 0.2,
        hw.MS_W / 2 + 0.2,
        -hw.MS_H - 0.2,
        2.0,
    )
    spring = hw.spring(SPRING_REST)
    spring = Part(
        "spring", Pos(spring_x0, SWITCH_Y, SPRING_Z) * Rot(0, 90, 0) * spring.shape, "metal"
    )
    screw = (
        Pos(X_OUT - SCREW_PROUD, SWITCH_Y, MS_Z)
        * Rot(0, 90, 0)
        * (
            Pos(0, 0, (BAR_T + 0.6 + SCREW_PROUD) / 2) * Cylinder(1.0, BAR_T + 0.6 + SCREW_PROUD)
            + Pos(0, 0, BAR_T + SCREW_PROUD + 0.6 + 0.65) * Cylinder(1.9, 1.3)
        )
    )
    bar -= Pos(X_OUT + BAR_T / 2, SWITCH_Y, MS_Z) * Rot(0, 90, 0) * Cylinder(0.8, 10)

    # Frosted light ring on the LEDs (clamped by the board screws) + frosted cap.
    ring = BOARD_LOC * _light_ring()

    body -= on_front(label("huxley", 2.4), 6.0, Y_FRONT, CAP_Z - 4.2)
    body -= on_front(label("HX-1", 3.2), -20.0, Y_FRONT, CAP_Z - 4.4)

    seam_y = PCB_BOTTOM_Y + 1.0
    lower = body & bbox_solid(-60, 60, -60, 60, -10, CAP_Z - 0.15)
    cap = body & bbox_solid(-60, 60, -60, 60, CAP_Z + 0.15, 200)
    front = lower & bbox_solid(-60, 60, -60, seam_y - 0.15, -10, 200)
    back = lower & bbox_solid(-60, 60, seam_y + 0.15, 60, -10, 200)

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
        "cable_bat_pos": hw.cable(
            [
                (bat_x, plug_y, plug_z),
                (bat_x, plug_y + 3.5, plug_z + 1.0),
                (X0 - 3.0, 9.0, plug_z + 12.0),
                (X0, 9.5, CELL_TOP - 3.0),
                (X0, CELL_Y + 2.0, CELL_TOP + 0.4),
            ],
            d=1.2,
        ),
        "cable_bat_neg": hw.cable(
            [
                (bat_x - 1.2, plug_y, plug_z),
                (bat_x - 1.5, plug_y + 3.5, plug_z - 2.0),
                (X0 - 4.0, 10.0, plug_z - 14.0),
                (X0 - 2.0, 10.5, SPK_Z),
                (X0, 9.0, CELL_Z0 + 3.0),
                (X0, CELL_Y + 2.0, CELL_Z0 - 0.4),
            ],
            d=1.2,
        ),
    }
    ms_pins_x = MS_TOP_X - hw.MS_H - hw.MS_PINS
    tail_y = PCB_BOTTOM_Y + 1.5
    for name, (pin_dy, hx) in {"cable_ptt_a": (-5.0, -7.62), "cable_ptt_b": (0.0, -10.16)}.items():
        tx, tz = to_device(hx, 13.82 if hx > -9 else 16.36)
        cables[name] = hw.cable(
            [
                (ms_pins_x, SWITCH_Y + pin_dy, MS_Z),
                (ms_pins_x - 3.0, SWITCH_Y + pin_dy * 0.6, MS_Z),
                (tx + 3.0, -2.0, tz + 3.0),
                (tx + 0.4, -3.6, tz + 0.8),  # stay behind the ring until inside its hole
                (tx, tail_y + 1.5, tz + 0.3),
                (tx, tail_y, tz),
            ],
            d=1.0,
        )

    parts = [
        Part("shell_front", front, "shell"),
        Part("shell_back", back, "shell"),
        Part("top_cap", cap, "translucent"),
        Part("light_ring", ring, "glow"),
        Part("face_panel", face_panel, "shell_dark"),
        Part("standoffs", standoffs, "shell_inner"),
        Part("speaker_ring", spk_ring, "shell_inner"),
        Part("mic_boots", boots, "shell_inner"),
        Part("ms_cradle", ms_cradle, "shell_inner"),
        Part("light_pipe", pipe, "clear"),
        Part("ptt_bar", bar, "accent"),
        Part("ptt_pin", pin, "screw"),
        Part("ptt_screw", screw, "screw"),
        spring,
        *board,
        *spk,
        Part("speaker_tab", tab, "metal"),
        *cells,
        Part("bridge_top", bridge_top, "metal"),
        Part("bridge_bottom", bridge_bot, "metal"),
        *ms_parts,
        Part("cable_spk", cables["cable_spk"], "wire_black"),
        Part("cable_bat_pos", cables["cable_bat_pos"], "wire_red"),
        Part("cable_bat_neg", cables["cable_bat_neg"], "wire_black"),
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
        "microswitch": ms_env,
        "spring": spring.shape,
        "bar_sweep": sweep,
        "bridge_top": bridge_top,
        "bridge_bottom": bridge_bot,
        **cables,
    }
    shells = {
        "body": body,
        "standoffs": standoffs,
        "speaker_ring": spk_ring,
        "mic_boots": boots,
        "light_pipe": pipe,
        "ms_cradle": ms_cradle,
        "light_ring": ring,
    }
    allowed = {
        ("board", "standoffs"),
        ("board", "mic_boots"),
        ("board", "light_pipe"),
        ("board", "light_ring"),  # sits on the LEDs, inside the generic 2.5 mm keep-out
        ("microswitch", "ms_cradle"),
        ("spring", "body"),  # seated in its pocket
        ("spring", "bar_sweep"),  # it is what the lever compresses
        ("bar_sweep", "spring"),
        ("bridge_top", "cable_bat_pos"),
        ("bridge_bottom", "cable_bat_neg"),
        ("cable_spk", "board"),  # leads start in the plug zone
        ("cable_bat_pos", "board"),
        ("cable_bat_neg", "board"),
        ("cable_bat_pos", "cable_bat_neg"),  # same lead out of one plug
        ("cable_ptt_a", "board"),  # soldered to the header tails
        ("cable_ptt_b", "board"),
        ("cable_ptt_a", "cable_ptt_b"),
        ("cable_spk", "speaker"),  # soldered to the tab
    }
    occupied = [envelopes["speaker"], spk_ring, *bays, boss, backing, standoffs, ring]
    occupied += [p.shape for p in board if p.name == "board_pcb"]
    back_cc = cavity_cc(inner, occupied)
    return Concept("hx1", "HX-1", parts, envelopes, shells, allowed, {"back_volume_cc": back_cc})
