"""HX-1: the walkie, two 18650s in holders, tact-switch PTT, frosted back.

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
- PTT: an orange lever on the right side (right thumb) turning on a printed
  pin. Its top end compresses a spring that pushes a printed plunger onto a
  12 x 12 tactile switch above the right cell (spring in series: progressive
  press, then the click, and the click point does not depend on print
  accuracy).
Assembly (rev E): two printed shells. The grey faceplate carries the board,
the speaker (in a black grille plug) and the mic and light ports; the tub
carries everything else and prints upright, grey to BACK_FROST_Z and frosted
above, so the board's rear LEDs glow through the top and the upper back. Four
M3 x 8 screws (two through the face, two up through the floor) close it into
heat-set inserts; closing it also captures the lever pin and clamps the
speaker. No glue anywhere.
The internal assembly sits 1.2 mm left of centre (X0) so the right wall has
room for the lever channel and the left cell still hugs its wall.
Device frame: X right, Y back (front face at -Y), Z up.
"""

from __future__ import annotations

import math

from build123d import (
    Axis,
    Box,
    Cylinder,
    Location,
    Polyline,
    Pos,
    Rectangle,
    Rot,
    chamfer,
    extrude,
    fillet,
    make_face,
)

from huxcase import fits
from huxcase import hardware as hw
from huxcase.concept import Concept, cavity_cc
from huxcase.hardware import Part
from huxcase.shapes import bbox_solid, hex_holes, hole_field, label, on_front, rounded_box

W = 70.0  # +2 mm over the bare-cell layout: Mario's 18650 holders are 21 mm wide
T = 2.4
X0 = -1.2  # internal assembly offset; frees room for the PTT channel on the right
R_FACE = 8.0  # front silhouette corners; tight so the holders reach the corners
Y_FRONT = -18.0
BOARD_TOP_Y = Y_FRONT + T + 6.0
PCB_BOTTOM_Y = BOARD_TOP_Y + hw.PCB_T
CELL_GAP = 2.5  # PCB underside to cell can, clears LEDs and chips
CELL_X = 20.7  # from the assembly centre; the board's plug zone fits between the cells
# Binding constraint: the speaker basket's back rim (r 15.8 at 12.3 mm deep)
# grazing the cells' inner flanks, not the board.
CELL_Y = 5.9
HOLDER_FRONT_Y = CELL_Y + hw.CELL_D / 2 + hw.HOLDER_FLOOR - hw.HOLDER_H  # end caps' front face
assert HOLDER_FRONT_Y >= PCB_BOTTOM_Y + CELL_GAP
CELL_Z0 = 6.7  # holder bottom: clears the 6.6 mm inner corner radius, leads join below
CELL_TOP = CELL_Z0 + hw.HOLDER_L
HOLDER_Z = (CELL_Z0 + CELL_TOP) / 2
Y_BACK = CELL_Y + hw.CELL_D / 2 + hw.HOLDER_FLOOR + 0.2 + T  # holder floor at the back
D = Y_BACK - Y_FRONT
assert X0 - CELL_X - hw.HOLDER_W / 2 >= -W / 2 + T + 0.15  # left holder clears its wall

SPK_Z = T + 0.4 + hw.SPK_D / 2
SPK_FRONT_Y = Y_FRONT + T + 0.5
SPK_LOC = Location((X0, SPK_FRONT_Y, SPK_Z), (-90, 0, 0))  # local -y -> device +Z
SPK_TAB_R, SPK_TAB_DEPTH = 15.3, 12.2  # solder tabs on the basket rim (STEP)
SPK_TAB_DEG = 60.0  # turn the speaker so its tabs sit upper right, clear of the seam wires
# Mario's lead is 97 mm; the route is ~20 mm, and loose wire buzzes in the back
# box. Cut it to this (plug to tab) and re-solder at the speaker: board and
# speaker both sit on the faceplate, so a little slack to unplug it is enough.
SPK_LEAD_CUT = 26.0
BOARD_Z = SPK_Z + hw.SPK_D / 2 + 0.3 + hw.PCB_R + 0.3 + 0.2
H = BOARD_Z + hw.PCB_R + 0.3 + 0.1 + T
# Board +x -> device -Z, board +y -> device +X, board +z (top face) -> device -Y.
BOARD_LOC = Location((X0, BOARD_TOP_Y, BOARD_Z)) * Rot(90, 0, 0) * Rot(0, 0, -90)
USB_Y, USB_FACE = (
    BOARD_TOP_Y - hw.USB_C_CENTER_Z,
    X0 + hw.USB_C_FACE_Y,
)  # the plug's axis, its face
BACK_FROST_Z = 36.0  # the tub prints grey up to here, then frosted (one filament swap)
LABEL_Z = 92.8  # "huxley" and "HX-1" on the face

# The two shells meet at the inside of the face. The faceplate's lip seats in
# the tub's rim; four M3 x 8 button heads close it into 5 mm heat-set inserts.
FACE_Y = Y_FRONT + T
LIP_D, LIP_W = 2.0, 1.6
ROOF_RELIEF = 0.3  # extra room over the lip under the roof (printed over support)
# Two through the face at the top (above the holders), two up through the
# floor at the bottom into bosses on the faceplate: nothing of the tub stands
# in front of the holders, so they and the cells can go in from the front.
SCREWS = ((-27.5, 96.5), (27.5, 96.5))
BOTTOM_SCREWS = ((-27.0, -11.5), (27.0, -11.5))  # (x, y), vertical, heads under the floor
FLOOR_PAD = 2.0  # tub floor thickened round each bottom screw
SCREW_L, HEAD_D, HEAD_H = 8.0, 5.7, 1.65  # M3 x 8 button head
BOARD_SCREW_L, BOARD_WASHER = 8.0, 0.4  # M2 x 8 + flat washer (Mario's kit has no M2 x 6)
CBORE_D, CBORE_H = 6.2, 1.8  # head sits flush, nothing to snag
PAD = 2.0  # faceplate thickened behind each screw
PAD_R = 4.0
INSERT_L, INSERT_OD = 5.0, 4.4  # Mario's M3 inserts
BLOCK_X_IN, BLOCK_Y1, BLOCK_HZ = 23.25, -5.0, 4.6

# Holder retention: one screwed bridge pressing on both cells, tabs between
# the holders, crush-ribbed pedestals below and pads above.
BRIDGE_Z = (64.5, 71.5)
BRIDGE_T = 1.6
CELL_BAR_T = 2.0  # the bridge's front bar, across both cells
BRIDGE_SCREW_L = 6.0  # M3 x 6 into a 5 mm insert in a boss on the back wall
TAB_TOP = CELL_TOP - 1.0  # the bridge's tabs reach 2 mm up the + end caps
LUG_Y, LUG_H = (CELL_Y, CELL_Y + 6.0), 7.0  # floor lugs beside the - end caps

# Wiring: the parallel joints and the long - run (see _wire_keepers).
J_POS = (X0 - 4.0, CELL_Y + 7.0, CELL_TOP + 1.5)  # + joint: over the channel, under the rocker
J_NEG = (X0 + 2.0, CELL_Y + 8.0, T + 1.8)  # - joint: at the channel's foot
# Where the + lead and the talk pair cross the bridge, inside its U. They lie
# loose (Mario, 2026-10-11); only the - lead has clips, on the back wall.
POS_RUN_X, RUN_Y = X0 - 7.0, 0.8
PTT_RUN_X = 5.5

# Black grille: a plug that drops into a window in the face from inside. Its
# flange sits in a recess on the inside of the face (top and sides), and the
# speaker rim clamps it there; a post on the tub's back wall presses the
# speaker's magnet forward when the case closes.
PANEL_W, PANEL_H, PANEL_R = 44.0, 41.0, 9.0
FLANGE_W, FLANGE_T = 1.5, 0.6
# Locating tabs on the plug, degrees round the speaker: six, so it cannot shift
# more than ~0.35 mm any way before one stops it; all clear of the board.
SPK_TABS = (0.0, 60.0, 120.0, 180.0, 240.0, 300.0)
# KCD1-101 rocker on the left side, above the left cell, fully recessed: the
# back had only 20 mm of depth behind it before the light ring.
ROCKER_Y, ROCKER_Z, ROCKER_RECESS = 5.3, 93.1, 7.2  # rocker top 0.2 below the surface
# Its clips' catch lies somewhere in this range under the frame (centre of
# the clip ~3 mm, Mario); the panel's chamfer spans it.
ROCKER_CATCH = (1.2, 2.8)
ROCKER_PANEL = ROCKER_CATCH[1]
# The hole's top edge prints as a bare 19 mm bridge of the 1.2 mm land (the
# slicer sees nothing to support there): 0.3 above and below the body, not
# 0.1, so a little sag still lets the switch in. Its clips still land on the
# chamfer, 0.2 deeper.
ROCKER_HOLE_Z_CLEAR = 0.3
SPLICE_D = 3.5  # a parallel joint in heat-shrink
# Mario's cells are protected 18650s (2026-10-10), each with its own circuit,
# so no separate protection board. The battery - lead runs from the - joint
# up the back wall to the plug, in two clips beside the speaker post.
NEG_RUN_X, NEG_CLIPS_Z = X0 + 6.0, (14.0, 28.0)

# Wires between the halves: six conductors of Mario's rainbow ribbon (two each
# for + and -, talk, talk ground) from the board to a junction in the channel
# where the tub's wires meet them, spliced in heat-shrink. Loose with slack on
# purpose (Mario, 2026-10-11: the heavy parts are held, the wires need no
# place), so the faceplate swings open like a book on them.
SEAM = (X0, 12.6, 46.0)  # the junction, behind the speaker's magnet
# Opening like a book: pull the faceplate straight out BOOK_PULL (clears the
# lip, the side fins and the lever-pin tooth), then swing it open about its
# left front edge.
HINGE, BOOK_PULL = (-W / 2, Y_FRONT), 10.0


def book_open(p: tuple[float, float, float]) -> tuple[float, float, float]:
    """Where a faceplate-side point lies with the faceplate opened flat beside the tub."""
    x, y, z = p
    return (2 * HINGE[0] - x, 2 * HINGE[1] - BOOK_PULL - y, z)


# Fins on the faceplate along the inside of both side walls, below the
# holders' fronts: they line the faceplate up sideways as it closes.
GUIDE_Z, GUIDE_Y1, GUIDE_T = (12.0, 38.0), -8.8, 1.6

# PTT lever on the right side.
X_OUT = W / 2
PIVOT_Z, TS_Z, SWITCH_Y = 50.0, 91.1, 2.5
# The lever turns on a printed Ø2.4 pin: a blind seat in the tub behind the
# channel, a blind seat in a faceplate tooth in front of it. Closing the case
# captures it; no screw, no metal pin.
PIVOT_X = 32.7  # seat walls: 1.0 to the holder side, 0.8 to the outside
PIN_D = 2.4
PIN_BORE = PIN_D + 2 * fits.SLIDE_HBORE  # seats and lever bore lie horizontal in print
KNUCKLE_R = (
    PIN_BORE / 2 + fits.MIN_WALL + 0.1
)  # +0.1: overlaps the slab (a tangent seam meshes open)
TOOTH_Z = (46.0, 54.0)
TOOTH_X0 = 30.0  # in front of the right holder, so it can reach in this far
NOTCH_CLEAR = 0.4  # the tub's notch round the tooth, above and below
CHANNEL_TOP_RELIEF = 0.4  # over the lever's top end (the channel's top prints on support)
BOARD_CENTRING = (35.0, 150.0, 330.0)  # board-frame angles of the centring posts
LATCH_ROOT_Y, LATCH_CATCH = 5.0, 0.5  # switch latch: finger root (cradle y), lip overlap
STOP_X0 = 26.0  # inner end of the lever's stop block
STOP = 3.8  # lever travel at the switch axis before it lands on the stop post
PRESS_DEG = math.degrees(math.atan(STOP / (TS_Z - PIVOT_Z)))
BAR_T, BAR_W = 3.0, 18.0
BAR_Z0, BAR_Z1 = PIVOT_Z - 7.0, 99.0  # tail below the pin is the rest stop
CLEAR = fits.PIVOT  # the lever swings in its channel: printed against printed
CHAN_Y0 = SWITCH_Y - BAR_W / 2 - CLEAR  # channel's front wall (the tooth's back face)
PIN_Y0, PIN_Y1 = SWITCH_Y - BAR_W / 2 - 2.5, SWITCH_Y + BAR_W / 2 + 2.5  # 2.5 into each seat
# Series spring between the lever's inner face and the plunger flange.
# 1.5 mm of preload (0.9 N) holds the lever firmly on its rest stop: no rattle.
SPRING_REST = hw.SERIES_SPRING_FREE - 1.5
SPRING_CBORE = 1.0  # the spring's end sits in a counterbore in the lever (nothing sticks out)
FLANGE_X = X_OUT + SPRING_CBORE - SPRING_REST  # plunger flange face (spring seat)
PLUNGER_TIP_X = FLANGE_X - 3.5  # plunger is a 3.5 mm piston, the length guides it
PLUNGER_R = 3.0
# Printed plungers, extra length (mm): the spring and switch are not measured, so
# a longer one adds preload if the click comes late. Try 0.0 first.
PLUNGER_VARIANTS = (0.0, 0.5, 1.0)
# Plunger tube, part of the tub. The tub prints upright, so the bore lies
# horizontal and gets the horizontal-bore fit.
TUBE_BORE = PLUNGER_R + fits.SLIDE_HBORE
TUBE_R = TUBE_BORE + 1.2  # three perimeters
# Tactile switch: stem +X, base toward the board's centre line.
TS_BASE_X = PLUNGER_TIP_X - hw.TACT_BODY - hw.TACT_STEM_H
TS_LOC = Location((TS_BASE_X, SWITCH_Y, TS_Z)) * Rot(0, 90, 0)


def click_travel(force: float = hw.TACT_FORCE, longer: float = 0.0) -> float:
    """Lever travel at the switch axis until the switch clicks: the spring
    compresses until it pushes with the switch's actuation force, and the stem
    moves its own TACT_TRAVEL. `longer`: a plunger printed that much longer
    (more preload, an earlier click)."""
    preload = (hw.SERIES_SPRING_FREE - SPRING_REST + longer) * hw.SERIES_SPRING_K
    return max(0.0, (force - preload) / hw.SERIES_SPRING_K) + hw.TACT_TRAVEL


def plunger_shape(longer: float = 0.0):
    """The printed plunger: a piston from the switch's stem to the spring, and
    a locator inside the spring. It prints standing on its tip, so the tip's
    edge is chamfered (no elephant's foot to catch in the bore). `longer`: a
    variant that much longer at the spring end, for more preload."""
    end = FLANGE_X + longer
    p = _along_x(SWITCH_Y, TS_Z, PLUNGER_R, PLUNGER_TIP_X, end)
    p = chamfer(p.edges().group_by(Axis.X)[0], 0.4)
    return p + _along_x(SWITCH_Y, TS_Z, 1.4, end - 0.01, end + 1.5)  # spring locator


def to_device(x: float, y: float) -> tuple[float, float]:
    """Board (x, y) -> device (x, z) on the board plane."""
    return X0 + y, BOARD_Z - x


def _bar(grow: float = 0.0):
    """The lever at rest: inner face on the wall plane, 3 mm proud, knuckle round the pin.

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
        * Cylinder(KNUCKLE_R + g, BAR_W + 2 * g)  # full width: the channel walls locate it
    )
    web = bbox_solid(
        PIVOT_X - g,
        X_OUT + g,
        SWITCH_Y - BAR_W / 2 - g,
        SWITCH_Y + BAR_W / 2 + g,
        PIVOT_Z - 2.0 - g,
        PIVOT_Z + 4.0 + g,
    )
    bar = slab + knuckle + web
    if g == 0:
        # A dimple to find by touch (the outer face is the one printed on the bed).
        bar -= Pos(X_OUT + BAR_T, SWITCH_Y, 74.0) * Rot(0, 90, 0) * Cylinder(1.5, 1.2)
        # Spring seat: a counterbore, so nothing on the lever's inner face stops it
        # sliding in from the front.
        bar -= (
            Pos(X_OUT + SPRING_CBORE / 2, SWITCH_Y, TS_Z)
            * Rot(0, 90, 0)
            * Cylinder(hw.SERIES_SPRING_D / 2 + 0.2, SPRING_CBORE + 0.02)
        )
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


def _knuckle_roof():
    """A 45 deg roof over the channel round the lever's knuckle: from where the
    knuckle's clearance turns flatter than 45 deg, up to the slot the lever's
    blade swings in. The channel's front and back walls, which locate the
    knuckle, stay where they are."""
    rk = KNUCKLE_R + CLEAR
    xt, zt = PIVOT_X - rk / math.sqrt(2), PIVOT_Z + rk / math.sqrt(2)
    xe = X_OUT - CLEAR - 0.15  # inside the blade's slot, whatever the press
    y0, y1 = SWITCH_Y - BAR_W / 2 - CLEAR, SWITCH_Y + BAR_W / 2 + CLEAR
    prof = make_face(
        Polyline(
            (xt, y0, PIVOT_Z), (xe, y0, PIVOT_Z), (xe, y0, zt + xe - xt), (xt, y0, zt), close=True
        )
    )
    roof = extrude(prof, y1 - y0, dir=(0, 1, 0))
    # The web's swept top stands above that line for its first ~1 mm (a flat
    # strip the slicer supported, its support sitting where the lever swings):
    # a second 45 deg roof from the web's top inner corner, fully pressed.
    a = math.radians(PRESS_DEG)
    cx, cz = -CLEAR, 4.0 + CLEAR  # web top inner corner (grown), from the pivot
    xs = PIVOT_X + cx * math.cos(a) - cz * math.sin(a) - 0.05
    zs = PIVOT_Z + cz
    prof = make_face(
        Polyline(
            (xs, y0, zs - 0.05),
            (xe, y0, zs - 0.05),
            (xe, y0, zs + xe - xs),
            (xs, y0, zs),
            close=True,
        )
    )
    return roof + extrude(prof, y1 - y0, dir=(0, 1, 0))


def _profile(grow: float, depth: float, y0: float):
    """The inside of the case seen from the front, grown by `grow`, as a slab
    `depth` thick starting at y0 (for the faceplate lip)."""
    w, h = W - 2 * T + 2 * grow, H - 2 * T + 2 * grow
    r = max(0.5, R_FACE - T + grow)
    return Pos(0, y0 + depth / 2, H / 2) * rounded_box(w, depth, h, r, 0.0)


def _panel(grow: float, y0: float, y1: float):
    """The grille plug's outline (front view), grown by `grow`, from y0 to y1."""
    b = Pos(0, (y0 + y1) / 2, SPK_Z + 0.5) * Box(PANEL_W + 2 * grow, y1 - y0, PANEL_H + 2 * grow)
    return fillet(b.edges().filter_by(Axis.Y), PANEL_R + grow)


def _along_y(x: float, z: float, r: float, y0: float, y1: float):
    return Pos(x, (y0 + y1) / 2, z) * Rot(90, 0, 0) * Cylinder(r, y1 - y0)


def _along_x(y: float, z: float, r: float, x0: float, x1: float):
    return Pos((x0 + x1) / 2, y, z) * Rot(0, 90, 0) * Cylinder(r, x1 - x0)


def _wedge_under(x_wall: float, x_in: float, y0: float, y1: float, z_top: float):
    """45 deg support under a ledge that sticks out from a wall at x_wall to
    x_in, so the tub prints it standing up without support."""
    from build123d import Polyline, extrude, make_face

    depth = abs(x_in - x_wall)
    pts = [(x_wall, z_top), (x_in, z_top), (x_wall, z_top - depth)]
    prof = make_face(Polyline(*pts, close=True))
    # The profile lies in XY (x, z as x, y): extrude along +Z, then turn Z into Y.
    solid = extrude(prof, y1 - y0)
    return Location((0, y0, 0), (90, 0, 0)) * Location((0, 0, -(y1 - y0))) * solid


def _teardrop_y(x: float, z: float, r: float, y0: float, y1: float):
    """A cylinder along Y with a 45 deg point underneath: prints without support
    when its axis lies horizontal on the bed."""
    from build123d import Polyline, extrude, make_face

    c = r * math.cos(math.radians(45))
    tri = make_face(
        Polyline((x - c, z - c), (x + c, z - c), (x, z - r * math.sqrt(2)), close=True)
    )
    prism = Location((0, y0, 0), (90, 0, 0)) * Location((0, 0, -(y1 - y0))) * extrude(tri, y1 - y0)
    return _along_y(x, z, r, y0, y1) + prism


def _teardrop_x(y: float, z: float, r: float, x0: float, x1: float):
    """A bore along X with a 45 deg point on top: its roof prints without
    support when it lies horizontal."""
    c = r / math.sqrt(2)
    diamond = Pos((x0 + x1) / 2, y, z) * Rot(45, 0, 0) * Box(x1 - x0, 2 * r, 2 * r)
    point = diamond & bbox_solid(x0, x1, y - 2 * r, y + 2 * r, z + c - 0.01, z + 2 * r)
    return _along_x(y, z, r, x0, x1) + point


def _bore_y(x: float, z: float, r: float, y0: float, y1: float):
    """A bore along Y with a 45 deg point on top (see _teardrop_x)."""
    c = r / math.sqrt(2)
    diamond = Pos(x, (y0 + y1) / 2, z) * Rot(0, 45, 0) * Box(2 * r, y1 - y0, 2 * r)
    point = diamond & bbox_solid(x - 2 * r, x + 2 * r, y0, y1, z + c - 0.01, z + 2 * r)
    return _along_y(x, z, r, y0, y1) + point


def _ramped_rib(x_face: float, out: int, y0: float, y1: float, z0: float, z1: float):
    """A crush rib from a face at x_face toward `out` (+1 or -1), ramping up
    over its first mm from y0: a part sliding in along +y rides onto it."""
    h = fits.CRUSH_RIB + 0.01
    xa = x_face - out * 0.01
    prof = make_face(
        Polyline((xa, y0), (xa + out * h, y0 + 1.0), (xa + out * h, y1), (xa, y1), close=True)
    )
    return Pos(0, 0, z0) * extrude(prof, z1 - z0, dir=(0, 0, 1))


def _union(shapes):
    out = None
    for s in shapes:
        out = s if out is None else out + s
    return out


def _faceplate(outer, wall, boss_box, usb_boss, inner):
    """Grey faceplate: the face, its lip, the board standoffs, mic and light
    tubes, screw pads and the lever-pin tooth. Returns (shapes, hardware)."""
    face = wall & bbox_solid(-60, 60, -60, FACE_Y, -10, 200)
    lip = _profile(-fits.SNUG, LIP_D, FACE_Y)
    lip -= _profile(-fits.SNUG - LIP_W, LIP_D + 1, FACE_Y - 0.5)
    # The speaker sits 0.4 above the floor and the board 0.4 below the roof:
    # the lip steps round both.
    lip -= _along_y(X0, SPK_Z, hw.SPK_D / 2 + fits.PART + 2.5, FACE_Y - 1, FACE_Y + LIP_D + 1)
    lip -= _along_y(X0, BOARD_Z, hw.PCB_R + 0.3 + fits.PART, FACE_Y - 1, FACE_Y + LIP_D + 1)
    lip -= boss_box
    # The grille plug drops in from inside: clear its path through the lip,
    # flange included (top and sides; there is none below the plug).
    lip -= _panel(fits.SNUG + 0.1, FACE_Y - 1, FACE_Y + LIP_D + 1)
    flange_path = _panel(FLANGE_W + fits.SNUG + 0.1, FACE_Y - 1, FACE_Y + LIP_D + 1)
    lip -= flange_path - bbox_solid(-60, 60, -60, 60, -10, SPK_Z + 0.5 - PANEL_H / 2)
    face += lip
    face += usb_boss

    # Grille window; the plug's flange sits in a recess on the inside of the
    # face (top and sides: the speaker reaches the floor, no room below).
    face -= _panel(fits.SNUG, Y_FRONT - 1, FACE_Y + 1)
    recess = _panel(FLANGE_W + fits.SNUG, FACE_Y - FLANGE_T - fits.STACK, FACE_Y + 0.01)
    face -= recess - bbox_solid(-60, 60, -60, 60, -10, SPK_Z + 0.5 - PANEL_H / 2)
    # Crush ribs in the window: the plug stays put when the faceplate is turned over.
    window_ribs = []
    rib_reach = fits.SNUG + (fits.CRUSH_RIB - fits.PART)
    for sx in (-1, 1):
        for dz in (-8.0, 8.0):
            x0, x1 = sorted(
                (sx * (PANEL_W / 2 + fits.SNUG), sx * (PANEL_W / 2 + fits.SNUG - rib_reach))
            )
            window_ribs.append(
                bbox_solid(
                    x0,
                    x1,
                    Y_FRONT + 0.4,
                    FACE_Y - 0.1,
                    SPK_Z + 0.5 + dz - 0.4,
                    SPK_Z + 0.5 + dz + 0.4,
                )
            )

    # Mic ports with a tactile "speak here" ring, and tubes onto each mic.
    boots = []
    for mx, my in hw.MICS:
        x, z = to_device(mx, my)
        face -= Pos(x, Y_FRONT + 1.2, z) * Rot(90, 0, 0) * Cylinder(0.6, 4.0)
        ring = Cylinder(3.4, 0.8) - Cylinder(2.7, 1)
        face -= Pos(x, Y_FRONT + 0.2, z) * Rot(90, 0, 0) * ring
        tube = _along_y(x, z, 2.1, FACE_Y, BOARD_TOP_Y - 1.5)
        boots.append(tube - _along_y(x, z, 1.0, -30, 0))

    # Charge light: a hole through the face and a tube to the LED.
    lx, lz = to_device(*hw.CHG_LED)
    face -= Pos(lx, Y_FRONT + 1.2, lz) * Rot(90, 0, 0) * Cylinder(0.8, 4.0)
    light_tube = _along_y(lx, lz, 1.6, FACE_Y, BOARD_TOP_Y - 0.9)
    light_tube -= _along_y(lx, lz, 0.8, -30, 0)

    centering_ribs = []
    # Board standoffs: M2 x 8 + washer from behind, self-tapping into a 1.7 mm
    # pilot that runs on into the face wall (1.3 mm of face stays in front of the tip).
    standoffs, board_screws = [], []
    pilot_r = fits.SELF_TAP_PILOT["M2"] / 2
    for hx, hy in hw.HOLES:
        x, z = to_device(hx, hy)
        so = _along_y(x, z, 2.6, FACE_Y, BOARD_TOP_Y)
        standoffs.append(so - _along_y(x, z, pilot_r, Y_FRONT + 1.3, 0))
        y_head = PCB_BOTTOM_Y + BOARD_WASHER
        washer = _along_y(x, z, 2.5, PCB_BOTTOM_Y, y_head)
        head = _along_y(x, z, 1.9, y_head, y_head + 1.3)
        shank = _along_y(x, z, 1.0, y_head - BOARD_SCREW_L, y_head)
        board_screws.append(washer + head + shank)

    # Three posts round the board's edge centre it on its screws (Ø2.9 holes
    # let an M2 wander 0.45), so USB-C cannot catch on the tub when it closes.
    centering = []
    for deg in BOARD_CENTRING:
        a = math.radians(deg)
        rp = hw.PCB_R + fits.PART + 1.2
        x, z = to_device(rp * math.cos(a), rp * math.sin(a))
        centering.append(_along_y(x, z, 1.2, FACE_Y, PCB_BOTTOM_Y))
        rr = hw.PCB_R - (fits.CRUSH_RIB - fits.PART) + fits.CRUSH_RIB / 2
        x, z = to_device(rr * math.cos(a), rr * math.sin(a))
        rib = Pos(x, (BOARD_TOP_Y + PCB_BOTTOM_Y) / 2, z) * Rot(0, 90 - deg, 0)  # radial
        centering_ribs.append(rib * Box(fits.CRUSH_RIB + 0.3, hw.PCB_T + 0.4, fits.CRUSH_RIB_W))

    # Screw pads, clearance holes and flush counterbores; the screws and the
    # inserts they land in.
    case_screws, inserts, face_inserts = [], [], []
    for sx, sz in SCREWS:
        pad = _along_y(sx, sz, PAD_R, FACE_Y - FLANGE_T, FACE_Y + PAD)
        face += pad & _profile(-fits.SNUG, PAD + FLANGE_T, FACE_Y - FLANGE_T)
        face -= _along_y(sx, sz, fits.SCREW_CLEAR["M3"] / 2, Y_FRONT - 1, FACE_Y + PAD + 1)
        face -= _along_y(sx, sz, CBORE_D / 2, Y_FRONT - 1, Y_FRONT + CBORE_H)
        y_head = Y_FRONT + CBORE_H
        head = _along_y(sx, sz, HEAD_D / 2, y_head - HEAD_H, y_head)
        case_screws.append(head + _along_y(sx, sz, 1.5, y_head, y_head + SCREW_L))
        ins = _along_y(sx, sz, INSERT_OD / 2, FACE_Y + PAD, FACE_Y + PAD + INSERT_L)
        inserts.append(ins - _along_y(sx, sz, 1.5, -30, 30))

    face -= on_front(label("huxley", 2.4), 6.0, Y_FRONT, LABEL_Z)
    face -= on_front(label("HX-1", 3.2), -20.0, Y_FRONT, LABEL_Z - 0.2)

    # Lever pin, front seat: a tooth on the faceplate fills a notch in the tub
    # in front of the lever channel and holds the pin's front end.
    tooth = bbox_solid(TOOTH_X0, X_OUT + 1, FACE_Y - 0.5, CHAN_Y0, *TOOTH_Z) & outer
    front_seat = PIN_D / 2 + fits.SLIDE  # vertical in the face-down faceplate: tighter
    tooth -= _along_y(PIVOT_X, PIVOT_Z, front_seat, PIN_Y0 - 0.3, CHAN_Y0 + 1)
    tooth -= _along_y(PIVOT_X, PIVOT_Z, PIN_BORE / 2 + 0.5, CHAN_Y0 - 0.5, CHAN_Y0 + 1)  # lead-in
    face += tooth
    for sx in (-1, 1):  # side guide fins
        x0, x1 = sorted((sx * (W / 2 - T - fits.SNUG), sx * (W / 2 - T - fits.SNUG - GUIDE_T)))
        face += bbox_solid(x0, x1, FACE_Y - 0.1, GUIDE_Y1, *GUIDE_Z)
    # Bottom screws: bosses on the faceplate behind the face, inserts from below.
    for bx, by in BOTTOM_SCREWS:
        z0 = T + FLOOR_PAD + fits.SNUG
        bs = Pos(bx, by, z0 + 3.5) * Cylinder(3.5, 7.0)
        bs += bbox_solid(bx - 3.5, bx + 3.5, FACE_Y - 0.1, by, z0, z0 + 7.0)
        bs -= Pos(bx, by, z0 + (INSERT_L + 1.0) / 2 - 0.01) * Cylinder(
            fits.INSERT_BORE["M3"] / 2, INSERT_L + 1.0
        )
        face += bs & inner
        ins = Pos(bx, by, z0 + INSERT_L / 2) * (
            Cylinder(INSERT_OD / 2, INSERT_L) - Cylinder(1.5, 20)
        )
        face_inserts.append(ins)
        z_head = CBORE_H
        sc = Pos(bx, by, z_head - HEAD_H / 2) * Cylinder(HEAD_D / 2, HEAD_H)
        sc += Pos(bx, by, z_head + SCREW_L / 2) * Cylinder(1.5, SCREW_L)
        case_screws.append(sc)
    shapes = {
        "faceplate": face + _union(centering),
        "faceplate_ribs": _union(centering_ribs + window_ribs),
        "faceplate_standoffs": _union(standoffs),
        "faceplate_boots": _union(boots),
        "faceplate_light": light_tube,
    }
    hardware = {
        "board_screws": _union(board_screws),
        "case_screws": _union(case_screws),
        "inserts": _union(inserts),
        "face_inserts": _union(face_inserts),
    }
    return shapes, hardware


def _plug():
    """Black grille plug, with the locating tabs and clamp ring for the speaker.
    Returns (plug, crush ribs)."""
    plug = _panel(0.0, Y_FRONT, FACE_Y)
    flange = _panel(FLANGE_W, FACE_Y - FLANGE_T, FACE_Y)  # 3 layers; the recess is one deeper
    flange -= bbox_solid(-60, 60, -60, 60, -10, SPK_Z + 0.5 - PANEL_H / 2 + 0.01)
    for sx, sz in SCREWS:
        flange -= _along_y(sx, sz, PAD_R + fits.SNUG, FACE_Y - 2, FACE_Y + 1)
    plug += flange
    ribs = []
    tab_r0 = hw.SPK_D / 2 + fits.PART  # tabs stand off the speaker rim by PART
    rib_r0 = hw.SPK_D / 2 - (fits.CRUSH_RIB - fits.PART)  # ribs reach 0.15 into it
    for deg in SPK_TABS:
        a = math.radians(deg)
        c, sn = math.cos(a), math.sin(a)
        at = Pos(X0 + c * (tab_r0 + 0.8), FACE_Y + 1.25, SPK_Z + sn * (tab_r0 + 0.8))
        plug += at * Rot(0, -deg, 0) * Box(1.6, 2.5, 4.0)
        rm = (rib_r0 + tab_r0 + 0.05) / 2
        rib = Pos(X0 + c * rm, FACE_Y + 0.6, SPK_Z + sn * rm) * Rot(0, -deg, 0)
        ribs.append(rib * Box(tab_r0 + 0.05 - rib_r0, 1.2, fits.CRUSH_RIB_W))
    # The speaker's rim lands on this ring and crushes it 0.15; a solid ring
    # 0.8 wide, it is also the hard stop toward the face.
    clamp = _along_y(X0, SPK_Z, hw.SPK_D / 2, FACE_Y, SPK_FRONT_Y + 0.15)
    plug += clamp - _along_y(X0, SPK_Z, hw.SPK_D / 2 - 0.8, -30, 30)
    holes = Location((X0, Y_FRONT + 1.2, SPK_Z), (90, 0, 0)) * hole_field(
        hex_holes(18.0, 2.8, 1.5, 4.0), 1.5, 4.0
    )
    return plug - holes, _union(ribs)


def _wire_keepers():
    """Pockets for the two parallel joints and clips for the battery - lead,
    all on the back wall: no joint or wire is left free to buzz in the back
    box. Returns (rigid features, snap lips, crush ribs).

    The joints (in heat-shrink) press in between two shelves from the front,
    past a lip; the pockets are 14 and 11 mm long so a lead cut a few mm off
    just moves its joint along. The lead snaps in between two prongs, past
    their lips.
    """
    back = Y_BACK - T + 0.1
    gap = SPLICE_D / 2 + fits.PART
    jx, jy, jz = J_POS
    keep, snaps, ribs = [], [], []
    for z0, z1, lz0, lz1 in (
        (jz - gap - 1.0, jz - gap, jz - gap, jz - gap + 0.3),
        (jz + gap, jz + gap + 1.0, jz + gap - 0.3, jz + gap),
    ):
        keep.append(bbox_solid(jx - 5.0, jx + 9.0, jy - 2.6, back, z0, z1))
        snaps.append(bbox_solid(jx - 5.0, jx + 9.0, jy - 2.6, jy - 1.8, lz0, lz1))  # front lip
    nx, ny, nz = J_NEG
    s0 = T + SPLICE_D + 2 * fits.PART  # the floor is the bottom of this pocket
    keep.append(bbox_solid(nx - 8.0, nx + 3.0, ny - 2.6, back, s0, s0 + 1.0))
    snaps.append(bbox_solid(nx - 8.0, nx + 3.0, ny - 2.6, ny - 1.8, s0 - 0.3, s0))
    for cz in NEG_CLIPS_Z:
        for side in (-1, 1):
            x0, x1 = sorted((NEG_RUN_X + side * 0.7, NEG_RUN_X + side * 1.7))
            keep.append(bbox_solid(x0, x1, back - 2.6, back, cz - 2.0, cz + 2.0))
            lx0, lx1 = sorted((NEG_RUN_X + side * 0.4, NEG_RUN_X + side * 0.71))  # 0.3 bump
            snaps.append(bbox_solid(lx0, lx1, back - 2.2, back - 1.4, cz - 2.0, cz + 2.0))
    return _union(keep), _union(snaps), _union(ribs)


def bridge_skin():
    """A skin across the bridge's screw hole, on its front face: printed
    front face down, the plate is a 17 mm bridge, and with the hole open its
    lines would end in the air (Bambu Studio: floating cantilever). The
    screw pokes through it. Printed only (printability.printed_pieces); the
    assembled model has the hole open."""
    face0 = CELL_Y - fits.PART - BRIDGE_T
    bzm = (BRIDGE_Z[0] + BRIDGE_Z[1]) / 2
    # 0.5 thick: the plate's face sits off the layer grid (9.9 mm up in
    # print), so a single 0.2 layer would hang on a tie at a slicing plane.
    return _along_y(X0, bzm, fits.SCREW_CLEAR["M3"] / 2 + 0.05, face0, face0 + 0.5)


def _holder_retention(inner):
    """Bridge, boss, pedestals and pads that hold Mario's two holders.

    Their plastic wraps round the cell (Mario, 2026-10-10), so nothing grips
    the holders' side walls: the holders slide straight in from the front, and
    the bridge, screwed to a boss on the back wall, presses on the cells. Crush
    ribs on its front bar push each cell, and so its holder, onto the back
    wall; tabs between the holders keep them out against the side walls;
    pedestals and pads stop their ends. Returns (tub features, crush ribs,
    (bridge, bridge ribs, screw, insert)).
    """
    r = hw.CELL_D / 2
    back_gap = Y_BACK - T - (CELL_Y + r + hw.HOLDER_FLOOR)
    face1 = CELL_Y - fits.PART  # the bridge plate's back face, on the boss
    face0 = face1 - BRIDGE_T
    feats, ribs = [], []
    walls = {}
    for hx, out in ((X0 - CELL_X, -1), (X0 + CELL_X, 1)):
        hw_ = hw.HOLDER_W / 2
        walls[out] = (hx + out * (hw_ - hw.HOLDER_WALL), hx + out * hw_, hx - out * hw_)
        # Lug on the floor beside the - end cap, channel side: with the bridge's
        # tab at the + cap, each holder is stopped sideways at both end caps.
        # Behind the speaker's magnet, in front of the - leads. Its crush rib
        # ramps up at the front, so the holder rides onto it as it slides in.
        x_face = hx - out * hw_ - out * fits.PART
        l0, l1 = sorted((x_face, x_face - out * 1.5))
        feats.append(bbox_solid(l0, l1, LUG_Y[0], LUG_Y[1], T - 0.1, CELL_Z0 + LUG_H))
        ribs.append(
            _ramped_rib(
                x_face, out, LUG_Y[0] + 1.0, LUG_Y[1] - 0.5, CELL_Z0 + 0.5, CELL_Z0 + LUG_H - 0.5
            )
        )
        # Pedestals under the front half of each holder, ribs on top; pads above.
        px0, px1 = hx - hw_ + 1.5, hx + hw_ - 1.5
        feats.append(
            bbox_solid(px0, px1, HOLDER_FRONT_Y + 1.0, CELL_Y + 2.0, T - 0.1, CELL_Z0 - fits.PART)
        )
        for k in range(3):
            xr = px0 + (k + 0.5) * (px1 - px0) / 3
            ribs.append(
                bbox_solid(
                    xr - 0.4,
                    xr + 0.4,
                    HOLDER_FRONT_Y + 1.5,
                    CELL_Y + 1.5,
                    CELL_Z0 - fits.PART - 0.01,
                    CELL_Z0 - fits.PART + fits.CRUSH_RIB,
                )
            )
    # Pads on top: the 76 mm holders leave 1.2 mm under the rocker mount and
    # the switch cradle. Front half only: the + leads leave at the back.
    top0 = CELL_TOP + fits.PART
    pads = [
        (-W / 2 + T - 0.1, X0 - CELL_X - hw.HOLDER_W / 2 + 4.5, ROCKER_Z - 7.5 - 1.7),
        (X0 + CELL_X - hw.HOLDER_W / 2 + 0.3, TS_BASE_X + hw.TACT_BODY, TS_Z - 7.5),
    ]
    for x0, x1, z_top in pads:
        feats.append(bbox_solid(x0, x1, HOLDER_FRONT_Y + 1.0, CELL_Y + 3.0, top0, z_top + 0.01))
        xm = (x0 + x1) / 2
        ribs.append(
            bbox_solid(
                xm - 0.4,
                xm + 0.4,
                HOLDER_FRONT_Y + 1.5,
                CELL_Y + 2.5,
                top0 - (fits.CRUSH_RIB - fits.PART),
                top0 + 0.01,
            )
        )
    # Bridge plate between the holders (clear of their wrapped walls, whatever
    # their height), screwed to a boss on the back wall.
    lx_in = walls[-1][2] + fits.PART
    rx_in = walls[1][2] - fits.PART
    bz0, bz1 = BRIDGE_Z
    bzm = (bz0 + bz1) / 2
    bridge = bbox_solid(lx_in, rx_in, face0, face1, bz0, bz1)
    # Front bar across both cells, PART in front of them: crush ribs on it push
    # each cell back_gap + 0.15 (its holder onto the wall, then the crush), and
    # in a drop a cell lands on the bar instead of the board's LEDs.
    bar_y1 = CELL_Y - r - fits.PART
    bar = bbox_solid(
        X0 - CELL_X - 7.0, X0 + CELL_X + 7.0, bar_y1 - CELL_BAR_T, bar_y1, bz0, bz1
    )  # across both cells' fronts, clear of the USB boss
    bar += bbox_solid(X0 - 4.0, X0 + 4.0, bar_y1 - CELL_BAR_T, bar_y1, bzm - 5.0, bzm + 5.0)
    bar -= _along_y(
        X0, bzm, CBORE_D / 2, bar_y1 - CELL_BAR_T - 1, bar_y1 + 1
    )  # screw goes in through it
    bridge += bar
    bridge_ribs = []
    push = back_gap + (fits.CRUSH_RIB - fits.PART)
    for hx in (X0 - CELL_X, X0 + CELL_X):
        for dx in (-1.0, 1.0):
            surface = CELL_Y - math.sqrt(r * r - dx * dx)  # the cell's front at this x
            bridge_ribs.append(
                bbox_solid(
                    hx + dx - 0.4, hx + dx + 0.4, bar_y1 - 0.01, surface + push, bz0 + 1, bz1 - 1
                )
            )
    for out in (-1, 1):
        # Tab on the channel side of each holder keeps it out against its wall.
        # It reaches up beside the + end cap: the caps stand full height, so the
        # stop holds whatever height the holder's side walls are.
        w_in = walls[out][2]  # inner wall's channel-side face
        t0, t1 = sorted((w_in - out * fits.PART, w_in - out * (fits.PART + 1.2)))
        bridge += bbox_solid(t0, t1, face1 - 0.01, face1 + 4.0, bz0, bz1)
        bridge += bbox_solid(t0, t1, bar_y1 - 0.01, face0 + 0.01, bz0, bz1)  # web to the front bar
        # Above the plate, its front edge rises at 45 deg: the bridge prints
        # front face down, so the tab grows out of the web without support.
        rise = TAB_TOP - bz1
        prof = make_face(
            Polyline(
                (t0, bar_y1, bz1 - 0.01),
                (t0, face1 + 4.0, bz1 - 0.01),
                (t0, face1 + 4.0, TAB_TOP),
                (t0, bar_y1 + rise, TAB_TOP),
                close=True,
            )
        )
        bridge += extrude(prof, t1 - t0, dir=(1, 0, 0))
        rx0, rx1 = sorted((w_in - out * fits.PART, w_in + out * (fits.CRUSH_RIB - fits.PART)))
        bridge_ribs.append(bbox_solid(rx0, rx1, face1 + 1.0, face1 + 3.5, bz0 + 1, TAB_TOP - 0.5))
    bridge -= _along_y(X0, bzm, fits.SCREW_CLEAR["M3"] / 2, face0 - 1, face1 + 1)
    boss = _teardrop_y(X0, bzm, 4.0, face1, Y_BACK - T + 0.1)
    boss -= _along_y(X0, bzm, fits.INSERT_BORE["M3"] / 2, face1 - 0.01, face1 + INSERT_L + 1.0)
    feats.append(boss)
    screw = _along_y(X0, bzm, HEAD_D / 2, face0 - HEAD_H, face0)
    screw += _along_y(X0, bzm, 1.5, face0, face0 + BRIDGE_SCREW_L)
    insert = _along_y(X0, bzm, INSERT_OD / 2, face1, face1 + INSERT_L)
    insert -= _along_y(X0, bzm, 1.5, -30, 30)
    return (
        _union(f & inner for f in feats),
        _union(ribs),
        (bridge, _union(bridge_ribs), screw, insert),
    )


def build() -> Concept:
    cy = (Y_FRONT + Y_BACK) / 2
    outer = Pos(0, cy, H / 2) * rounded_box(W, D, H, R_FACE, 3.0)
    inner = Pos(0, cy, H / 2) * rounded_box(W - 2 * T, D - 2 * T, H - 2 * T, R_FACE - T, 0.0)
    wall = outer - inner
    tub = wall - bbox_solid(-60, 60, -60, FACE_Y, -10, 200)
    board_env = BOARD_LOC * hw.board_keepout(top=5.9)

    # USB-C exits the left side (board -y edge). Its face sits 2.2 mm inside
    # the wall, so a boss brings the wall to it and a deep pocket takes the plug.
    usb_y, usb_face = USB_Y, USB_FACE
    zb0, zb1 = BOARD_Z - 8.0, BOARD_Z + 8.0
    boss = bbox_solid(-W / 2 + T - 0.05, usb_face - 0.15, FACE_Y + LIP_D, usb_y + 5.0, zb0, zb1)
    boss_box = bbox_solid(
        -W / 2,
        usb_face + fits.SNUG,
        FACE_Y - 1,
        FACE_Y + LIP_D + 1,
        zb0 - fits.SNUG,
        zb1 + fits.SNUG,
    )
    usb_cut = bbox_solid(
        -W / 2 - 1,
        usb_face - 1.0,
        usb_y - 3.8,
        usb_y + 3.8,
        BOARD_Z - 6.8,
        BOARD_Z + 6.8,
    )
    usb_cut += bbox_solid(
        -W / 2 - 1,
        usb_face + 0.1,
        usb_y - 1.9,
        usb_y + 1.9,
        BOARD_Z - 4.8,
        BOARD_Z + 4.8,
    )
    usb_boss = bbox_solid(
        -W / 2 + T + fits.SNUG, usb_face - 0.15, FACE_Y - 0.1, usb_y + 5.0, zb0, zb1
    )
    face_shapes, fasteners = _faceplate(outer, wall, boss_box, usb_boss, inner)
    face_shapes["faceplate"] -= usb_cut
    plug, plug_ribs = _plug()

    # ---------------- tub (grey, frosted above BACK_FROST_Z; prints upright) ----------------
    # Floor pads for the two bottom screws: counterbore from below, clearance up.
    for bx, by in BOTTOM_SCREWS:
        pad = bbox_solid(
            bx - 3.5, bx + 3.5, FACE_Y + LIP_D + fits.SNUG, by + 3.5, T - 0.1, T + FLOOR_PAD
        )
        tub += pad & inner
        tub -= Pos(bx, by, 0) * Cylinder(fits.SCREW_CLEAR["M3"] / 2, 2 * (T + FLOOR_PAD) + 1)
        tub -= Pos(bx, by, 0) * Cylinder(CBORE_D / 2, 2 * CBORE_H)
    # Insert blocks for the two face screws, on the side walls.
    for sx, sz in SCREWS:
        side = 1 if sx > 0 else -1
        x0, x1 = sorted((side * BLOCK_X_IN, side * W / 2))
        z0 = (
            T - 0.1 if sz - BLOCK_HZ < 3 * T else sz - BLOCK_HZ
        )  # the bottom pair stands on the floor
        blk = bbox_solid(x0, x1, FACE_Y + LIP_D, BLOCK_Y1, z0, sz + BLOCK_HZ)
        if z0 > T:
            blk += _wedge_under(
                side * (W / 2 - T + 0.1), side * BLOCK_X_IN, FACE_Y + LIP_D, BLOCK_Y1, z0
            )
            for k in (0.0, 3.0, 6.0, 10.0):  # the board's path as the faceplate closes
                blk -= Pos(0, -k, 0) * board_env
        blk &= inner
        bore_y1 = FACE_Y + LIP_D + INSERT_L + 1.0
        tub += blk - _along_y(sx, sz, fits.INSERT_BORE["M3"] / 2, FACE_Y, bore_y1)

    # PTT channel: backing fills the gap between the right holder and the wall,
    # up to the roof; the lever's swept volume (plus clearance) is cut out.
    cell_edge = X0 + CELL_X + hw.HOLDER_W / 2
    backing = bbox_solid(
        cell_edge + fits.PART,
        X_OUT - T + 0.1,
        SWITCH_Y - 11.0,
        SWITCH_Y + 13.0,
        T - 0.1,
        H - T + 0.1,
    )
    tub += backing & inner

    holder_parts, holder_ribs, (bridge, bridge_ribs, bridge_screw, bridge_insert) = (
        _holder_retention(inner)
    )
    tub += holder_parts
    tub_ribs = [holder_ribs]

    # 12 x 12 switch in a cradle above the right holder, open to the front so
    # it slides in with its legs in two slots. Crush ribs on three walls hold
    # it; ribs on the floor press its top onto the end of the plunger tube.
    ts_parts = hw.place(hw.tact_switch(), TS_LOC)
    pocket = hw.TACT_W / 2 + fits.PART
    rib_in, rw = pocket - fits.CRUSH_RIB, fits.CRUSH_RIB_W / 2
    floor = -fits.PART
    cradle = bbox_solid(
        -pocket - 1.2, pocket + 1.2, -pocket, pocket + 1.2, floor - 1.5, hw.TACT_BODY
    )
    cradle -= bbox_solid(-pocket, pocket, -pocket - 5, pocket, floor, 10)
    for sx in (-1, 1):  # leg slots, open to the front
        cradle -= bbox_solid(sx * 6.25 - 0.7, sx * 6.25 + 0.7, -pocket - 5, 3.3, -10, 0.01)
    ribs = [bbox_solid(o - rw, o + rw, rib_in, pocket, 0.4, hw.TACT_BODY) for o in (-3.0, 3.0)]
    ribs += [
        bbox_solid(x0, x1, -rw, rw, 0.4, hw.TACT_BODY)
        for x0, x1 in ((-pocket, -rib_in), (rib_in, pocket))
    ]
    ribs += [bbox_solid(o - rw, o + rw, -1.5, 1.5, floor, floor + fits.CRUSH_RIB) for o in (-3, 3)]
    tub_ribs += [TS_LOC * r for r in ribs]
    # Latch: a finger cut free in the cradle's top wall, with a lip that snaps
    # over the switch's front face. The back ribs press the switch onto it.
    half = hw.TACT_W / 2
    lip_y = -half - (fits.CRUSH_RIB - fits.PART)  # where the back ribs push the front face
    fz0, fz1 = -0.3, 3.0
    cradle -= bbox_solid(-pocket - 2, -pocket + 0.01, -pocket - 2, LATCH_ROOT_Y, fz0 - 0.6, fz0)
    cradle -= bbox_solid(-pocket - 2, -pocket + 0.01, -pocket - 2, LATCH_ROOT_Y, fz1, fz1 + 0.6)
    finger = bbox_solid(-pocket - 1.2, -pocket, lip_y - 1.6, LATCH_ROOT_Y, fz0, fz1)
    lip = bbox_solid(-pocket, -half + LATCH_CATCH, lip_y - 1.6, lip_y, fz0, fz1)
    lip -= (
        Pos(-half + LATCH_CATCH, lip_y - 1.6, 0) * Rot(0, 0, 45) * Box(1.13, 1.13, 20)
    )  # 0.8 lead-in
    latch = TS_LOC * (finger + lip)
    tube_x0 = TS_BASE_X + hw.TACT_BODY
    tube = _along_x(SWITCH_Y, TS_Z, TUBE_R, tube_x0, X_OUT - T + 0.1)
    # The bore's 45 deg point stands above the tube's round: a ridge covers it.
    tube += bbox_solid(
        tube_x0,
        X_OUT - T + 0.1,
        SWITCH_Y - 2.2,
        SWITCH_Y + 2.2,
        TS_Z,
        TS_Z + TUBE_BORE * 1.42 + 0.9,
    )
    # End plate: joins the cradle's walls to the tube (they would not touch
    # otherwise) and gives the switch's top a flat face to seat on.
    cradle += bbox_solid(
        -pocket - 1.2, pocket + 1.2, -pocket, pocket + 1.2, hw.TACT_BODY, hw.TACT_BODY + 1.2
    )
    tub += (TS_LOC * cradle + tube) & inner
    latch &= inner
    # Web from the cradle to the roof: the cradle (and the holder pad under it)
    # no longer hangs on its tube alone.
    cx0, cx1 = TS_BASE_X - fits.PART - 1.5, TS_BASE_X + hw.TACT_BODY + 1.2
    tub += bbox_solid(
        cx0, cx1, SWITCH_Y + 5.2, SWITCH_Y + pocket + 1.2, TS_Z + pocket + 1.0, H - T + 0.1
    )
    # The stem goes in from the front too: a slot in the tube's front wall.
    stem_w = hw.TACT_STEM_W / 2 + fits.PART
    stem_slot = bbox_solid(
        tube_x0 - 0.01,
        PLUNGER_TIP_X + 0.3,
        SWITCH_Y - pocket - 2.0,  # out past the cradle's front edge
        SWITCH_Y,
        TS_Z - stem_w,
        TS_Z + stem_w,
    )
    plunger = plunger_shape()
    spring = hw.spring(SPRING_REST)
    spring = Part("spring", Pos(FLANGE_X, SWITCH_Y, TS_Z) * Rot(0, 90, 0) * spring.shape, "metal")

    # KCD1-101 on the left side: a mount inside the wall gives it a panel at
    # the bottom of a 7.2 mm recess, so the rocker sits below the surface. The
    # panel is ROCKER_PANEL thick with a 45 deg chamfer round the inside of the
    # hole: the switch's snap clips land on the chamfer wherever their catch
    # is (Mario measured them about 3 mm under the frame) and pull it tight.
    panel_x = -W / 2 + ROCKER_RECESS
    by, bz = hw.ROCKER_BEZEL[0] / 2, hw.ROCKER_BEZEL[1] / 2  # long side along Y
    mount = bbox_solid(
        -W / 2 + T - 0.1,
        panel_x + ROCKER_PANEL,
        max(ROCKER_Y - by - 1.7, PCB_BOTTOM_Y + CELL_GAP + 0.1),  # stay behind the board
        ROCKER_Y + by + 1.7,
        ROCKER_Z - bz - 1.7,
        ROCKER_Z + bz + 1.7,
    )
    tub += mount & inner
    cy_, cz = hw.ROCKER_BODY[0] / 2, hw.ROCKER_BODY[1] / 2
    rocker_cut = bbox_solid(
        -W / 2 - 1,
        panel_x,
        ROCKER_Y - by - 0.5,
        ROCKER_Y + by + 0.5,
        ROCKER_Z - bz - 0.5,
        ROCKER_Z + bz + 0.5,
    )
    rocker_cut += bbox_solid(
        panel_x - 0.1,
        panel_x + ROCKER_PANEL + 0.1,
        ROCKER_Y - cy_ - 0.1,
        ROCKER_Y + cy_ + 0.1,
        ROCKER_Z - cz - ROCKER_HOLE_Z_CLEAR,
        ROCKER_Z + cz + ROCKER_HOLE_Z_CLEAR,
    )
    chamfer = ROCKER_PANEL - ROCKER_CATCH[0]
    hole = Rectangle(
        2 * (cz + ROCKER_HOLE_Z_CLEAR), 2 * (cy_ + 0.1)
    )  # after the turn: x -> -Z, y -> Y
    taper = extrude(hole, chamfer + 0.01, taper=-45)  # widens 45 deg as it goes in
    rocker_cut += Location((panel_x + ROCKER_CATCH[0], ROCKER_Y, ROCKER_Z), (0, 90, 0)) * taper
    # local +z (outward) -> device -X, local x (long side) -> device Y
    rocker_loc = Location((panel_x, ROCKER_Y, ROCKER_Z)) * Rot(0, -90, 0) * Rot(0, 0, 90)
    rocker = hw.place(hw.rocker(), rocker_loc)

    # Speaker clamp: a post from the back wall onto the magnet, crush ribs on its tip.
    post_y0 = SPK_FRONT_Y + hw.SPK_DEPTH + fits.PART
    tub += _teardrop_y(X0, SPK_Z, 3.0, post_y0, Y_BACK - T + 0.1)
    for k in range(3):
        a = math.radians(90 + 120 * k)
        x, z = X0 + 1.8 * math.cos(a), SPK_Z + 1.8 * math.sin(a)
        tub_ribs.append(_along_y(x, z, 0.6, post_y0 - fits.CRUSH_RIB, post_y0 + 0.01))

    keepers, keeper_snaps, keeper_ribs = _wire_keepers()
    tub += keepers
    tub_ribs.append(keeper_ribs)
    latch += keeper_snaps  # snap lips: rigid stops that flex on the way in

    # Cuts: lever channel, plunger bore, tooth notch, rear pin seat, rocker, USB,
    # and the band the faceplate's lip goes into. The tub prints upright, so
    # every close-fitting cut that lies horizontal gets a 45 deg roof the
    # slicer leaves unsupported: support left in them would bind.
    tub -= _pressed_union()
    tub -= _knuckle_roof()
    # The channel's top prints over support: CHANNEL_TOP_RELIEF more room so
    # what that leaves can't reach the lever's top end swinging in under it.
    tub -= bbox_solid(
        PIVOT_X - 3.0,
        X_OUT + 1.0,
        SWITCH_Y - BAR_W / 2 - CLEAR,
        SWITCH_Y + BAR_W / 2 + CLEAR,
        BAR_Z1 + CLEAR - 0.01,
        BAR_Z1 + CLEAR + CHANNEL_TOP_RELIEF,
    )
    tub -= _teardrop_x(SWITCH_Y, TS_Z, TUBE_BORE, tube_x0 - 0.01, X_OUT + 1)
    tub -= stem_slot
    # Stop: a block of the backing that the lever's inner face lands on after
    # the click. Added after the channel cut, which would otherwise remove it.
    stop_z0, stop_z1 = TS_Z - 2.0, TS_Z + 2.0
    stop_x = X_OUT - STOP * (stop_z1 - PIVOT_Z) / (TS_Z - PIVOT_Z) - 0.05
    # It reaches in over the right holder's top (empty at this height) for strength.
    tub += bbox_solid(STOP_X0, stop_x, SWITCH_Y + 5.6, SWITCH_Y + 10.0, stop_z0, stop_z1)
    tub += bbox_solid(
        stop_x - 0.01, X_OUT - T + 0.1, SWITCH_Y + 9.3, SWITCH_Y + 10.0, stop_z0, stop_z1
    )  # into the backing
    # The tooth's notch: its roof hangs open to the front and the side, so it
    # prints over support (pulled out from there). NOTCH_CLEAR above and below
    # the tooth so what that leaves cannot stop the faceplate closing.
    tub -= bbox_solid(
        TOOTH_X0 - fits.SNUG,
        X_OUT + 2,
        FACE_Y - 1,
        CHAN_Y0 + fits.SNUG,
        TOOTH_Z[0] - NOTCH_CLEAR,
        TOOTH_Z[1] + NOTCH_CLEAR,
    )
    tub -= _bore_y(PIVOT_X, PIVOT_Z, PIN_BORE / 2, CHAN_Y0, PIN_Y1 + 0.3)
    tub -= rocker_cut
    tub -= usb_cut
    # KEY1 stands 0.17 under the roof: a groove so it cannot scrape on closing.
    tub -= bbox_solid(-10.0, -4.5, FACE_Y - 1, BOARD_TOP_Y + 3.0, H - T - 0.01, H - T + 1.2)
    band = _profile(0.0, LIP_D + 0.01, FACE_Y - 0.005)
    tub -= band - _profile(-2 * fits.SNUG - LIP_W, LIP_D + 1, FACE_Y - 0.5)
    # The roof prints over support, which leaves its underside rough: ROOF_RELIEF
    # more room over the lip where it goes in (the faceplate is unchanged).
    relief = _profile(ROOF_RELIEF, LIP_D + 0.5, FACE_Y - 0.01) - _profile(
        0.0, LIP_D + 1, FACE_Y - 1
    )
    tub -= relief & bbox_solid(-60, 60, FACE_Y - 1, FACE_Y + LIP_D + 0.5, H - T - 1.2, H)

    # ---------------- lever + pin ----------------
    bar = _bar() - _along_y(PIVOT_X, PIVOT_Z, PIN_BORE / 2, -40, 40)
    pin = _along_y(PIVOT_X, PIVOT_Z, PIN_D / 2, PIN_Y0, PIN_Y1)

    # ---------------- hardware ----------------
    board = hw.place(hw.board_parts(), BOARD_LOC, "board_")
    spk = hw.place(hw.speaker(), SPK_LOC)
    ta = math.radians(SPK_TAB_DEG)
    tab_x, tab_z = X0 + SPK_TAB_R * math.cos(ta), SPK_Z + SPK_TAB_R * math.sin(ta)
    tab_y = SPK_FRONT_Y + SPK_TAB_DEPTH
    tab = Pos(tab_x, tab_y, tab_z) * Rot(0, 90 - SPK_TAB_DEG, 0) * Box(3.0, 1.0, 2.0)
    # Mario's two single-18650 holders, + end up, floor toward the back. Their
    # own leads join in parallel: + above the holders (on to the rocker), -
    # below them (on to the plug).
    cells, bays = [], []
    for i, x in enumerate((X0 - CELL_X, X0 + CELL_X)):
        cells += [
            Part(f"{p.name}_{i}", p.shape, p.kind)
            for p in hw.place(hw.holder(), Location((x, CELL_Y, HOLDER_Z)))
        ]
        bays.append(Pos(x, CELL_Y, HOLDER_Z) * hw.holder_envelope())
    j_pos, j_neg = J_POS, J_NEG
    lead_y = CELL_Y + hw.CELL_D / 2 - 0.5
    leads = {}
    for i, x in enumerate((X0 - CELL_X, X0 + CELL_X)):
        leads[f"lead_pos_{i}"] = hw.cable(
            [
                (x, lead_y, CELL_TOP - 0.5),
                (x, lead_y + 0.5, CELL_TOP + 1.0),
                ((x + j_pos[0]) / 2, j_pos[1] + 1.0, CELL_TOP + 1.3),
                (
                    j_pos[0] + (5.5 if x > j_pos[0] else -5.5),
                    j_pos[1] + 0.2,
                    j_pos[2],
                ),  # level into the pocket
                j_pos,
            ],
            d=1.5,
        )
        leads[f"lead_neg_{i}"] = hw.cable(
            [
                (x, lead_y, CELL_Z0 + 0.5),
                (x, lead_y + 0.5, CELL_Z0 - 1.4),
                ((x + j_neg[0]) / 2, j_neg[1] + 0.5, j_neg[2] + 0.4),
                (
                    j_neg[0] + (1.6 if x > j_neg[0] else -5.5),
                    j_neg[1] + 0.1,
                    j_neg[2],
                ),  # level into the pocket
                j_neg,
            ],
            d=1.5,
        )
    # Cable runs (tub side). The ribbon from the board to the seam junction is
    # loose with slack and not modelled.
    bat_x = to_device(17.81, -6.04)[0]
    spk_x = to_device(17.81, 5.19)[0]
    plug_z = to_device(17.81, 0)[1]
    plug_y = PCB_BOTTOM_Y + hw.BOTTOM_CONN
    rocker_tab_x = panel_x + hw.ROCKER_BODY[2] + hw.ROCKER_TABS - 0.5
    seam_x = [SEAM[0] + k * 2.5 for k in (-1.5, -0.5, 0.5, 1.5)]  # +, -, talk GND, talk
    neg_y = Y_BACK - T - 0.65  # the - lead against the back wall, in its clips

    def to_seam(x, y_in=13.6):
        """Last points of a tub wire: down behind where its splice sits."""
        return [(x, y_in, SEAM[2] + 1.6), (x, SEAM[1], SEAM[2])]

    cables = {
        "cable_spk": hw.cable(
            [
                (spk_x, plug_y, plug_z),
                (spk_x, plug_y + 1.2, plug_z - 0.8),
                (tab_x + 0.9, -1.4, plug_z - 5.0),
                (tab_x + 1.0, -1.6, tab_z + 6.0),
                (tab_x + 0.6, tab_y + 0.6, tab_z + 1.0),
            ]
        ),
        # + : rocker -> across the bridge -> down the channel -> its splice at
        # the seam junction.
        "cable_bat_pos": hw.cable(
            [
                (rocker_tab_x, ROCKER_Y - hw.ROCKER_TAB_PITCH / 2, ROCKER_Z),
                (rocker_tab_x + 0.5, ROCKER_Y - hw.ROCKER_TAB_PITCH / 2, ROCKER_Z - 1.5),
                (rocker_tab_x + 1.5, 1.6, CELL_TOP + 1.5),
                (POS_RUN_X, RUN_Y, (BRIDGE_Z[0] + BRIDGE_Z[1]) / 2),
                (POS_RUN_X + 0.6, 3.5, 61.5),
                (-7.4, 9.5, 54.0),
                (seam_x[0] - 0.4, 13.6, 51.3),
                *to_seam(seam_x[0], 13.8),
            ],
            d=1.2,
        ),
        "cable_bat_sw": hw.cable(
            [
                (rocker_tab_x, ROCKER_Y + hw.ROCKER_TAB_PITCH / 2, ROCKER_Z),
                (rocker_tab_x + 0.5, ROCKER_Y + hw.ROCKER_TAB_PITCH / 2, ROCKER_Z - 3.0),
                (rocker_tab_x - 1.0, CELL_Y + 4.0, CELL_TOP + 3.5),
                j_pos,
            ],
            d=1.2,
        ),
        # - : the - joint -> up the back wall in two clips -> under and
        # behind it -> its splice at the seam junction.
        "cable_bat_neg": hw.cable(
            [
                j_neg,
                (NEG_RUN_X - 0.6, j_neg[1] + 0.9, j_neg[2] + 0.2),  # under the pocket's shelf
                (NEG_RUN_X, neg_y - 0.3, j_neg[2] + 4.0),
                (NEG_RUN_X, neg_y, NEG_CLIPS_Z[0]),
                (NEG_RUN_X, neg_y, NEG_CLIPS_Z[1]),
                (NEG_RUN_X - 1.2, neg_y - 0.6, 36.0),
                (seam_x[1] + 3.0, 14.6, 41.5),
                *to_seam(seam_x[1], 14.2),
            ],
            d=1.2,
        ),
    }
    # Talk pair: switch legs -> across the bridge -> down the channel -> the seam junction.
    legs_x = TS_BASE_X - hw.TACT_LEGS
    legs_z = TS_Z - 6.25
    for name, (leg_y, pin_i, run_y) in {
        "cable_ptt_a": (SWITCH_Y - 2.5, 2, RUN_Y - 0.1),  # GND
        "cable_ptt_b": (SWITCH_Y + 2.5, 3, RUN_Y + 1.0),  # talk
    }.items():
        cables[name] = hw.cable(
            [
                (legs_x, leg_y, legs_z),
                (legs_x - 1.2, (leg_y + run_y) / 2, legs_z - 1.5),
                (PTT_RUN_X + 0.9, run_y, 78.0),
                (PTT_RUN_X, run_y, (BRIDGE_Z[0] + BRIDGE_Z[1]) / 2),
                (PTT_RUN_X - 0.2, run_y + 1.6, 62.0),
                (seam_x[pin_i] + 1.4, 9.0 + run_y, 54.5),
                (seam_x[pin_i] + 0.4, 13.0 + run_y * 0.4, 51.0),
                *to_seam(seam_x[pin_i], 13.2 + run_y * 0.4),
            ],
            d=1.0,
        )

    screws4 = fasteners["case_screws"].solids()
    face_screws = [sc for sc in screws4 if sc.bounding_box().center().Z > H / 2]
    floor_screws = [sc for sc in screws4 if sc.bounding_box().center().Z <= H / 2]
    assert len(face_screws) == len(floor_screws) == 2
    # The ribbon from the board to the junction, for the build page only (it
    # lies loose, so the fit check leaves it out): closed, and with the
    # faceplate opened flat beside the tub while it is soldered.
    plug_top = (bat_x, plug_y, plug_z)
    ribbon_closed = hw.cable(
        [
            plug_top,
            (bat_x, plug_y + 1.2, plug_z - 1.0),
            (bat_x + 2.2, 2.0, plug_z - 4.6),
            (X0 - 1.8, 6.0, SEAM[2] + 1.0),
            (X0 + 2.5, 9.5, SEAM[2] - 4.0),
            (X0 + 4.0, 12.0, SEAM[2] - 2.0),
            (X0 + 2.0, 13.0, SEAM[2] + 0.5),
            SEAM,
        ],
        d=1.2,
    )
    ribbon_open = hw.cable(
        [
            SEAM,
            (X0 - 2.0, 4.0, SEAM[2]),
            (X0 - 5.0, -5.0, SEAM[2] - 0.5),
            (-20.0, -5.0, SEAM[2] - 0.5),
            (-30.0, -10.0, SEAM[2] - 0.5),
            (-34.0, -17.0, SEAM[2] - 0.5),
            (-38.0, -24.0, SEAM[2] + 1.0),
            (-50.0, -36.0, SEAM[2] + 5.0),
            book_open(plug_top),
        ],
        d=1.2,
    )
    tub_ribs_all = _union(tub_ribs)
    frost = bbox_solid(-60, 60, -60, 60, BACK_FROST_Z, 200)
    tub_all = tub + latch + tub_ribs_all
    faceplate_all = _union(face_shapes.values())
    parts = [
        Part("faceplate", faceplate_all, "shell"),
        Part("grille_plug", plug + plug_ribs, "shell_dark"),
        Part("tub_grey", tub_all - frost, "shell"),
        Part("tub_frosted", tub_all & frost, "translucent"),
        *rocker,
        Part("ptt_bar", bar, "accent"),
        Part("ptt_pin", pin, "shell_inner"),
        Part("plunger", plunger, "shell_inner"),
        Part("bridge", bridge + bridge_ribs, "shell_inner"),
        spring,
        Part("bridge_screw", bridge_screw, "screw"),
        Part("bridge_insert", bridge_insert, "metal"),
        Part("board_screws", fasteners["board_screws"], "screw"),
        # The case screws by the way they go in: through the face, up through the floor.
        Part("case_screws_face", _union(face_screws), "screw"),
        Part("case_screws_floor", _union(floor_screws), "screw"),
        Part("inserts", fasteners["inserts"], "metal"),
        Part("face_inserts", fasteners["face_inserts"], "metal"),
        *board,
        *spk,
        Part("speaker_tab", tab, "metal"),
        *cells,
        *[Part(n, c, "wire_red" if "pos" in n else "wire_black") for n, c in leads.items()],
        *ts_parts,
        *[
            Part(
                n,
                c,
                "wire_red"
                if "pos" in n
                else "wire_white"
                if "ptt" in n or "talk" in n
                else "wire_black",
            )
            for n, c in cables.items()
        ],
        Part("ribbon_closed", ribbon_closed, "wire_red"),
        Part("ribbon_open", ribbon_open, "wire_red"),
    ]

    sweep = _union(_pressed(_bar(), k / 10) for k in range(0, 11))
    envelopes = {
        "board": board_env,
        "speaker": SPK_LOC * hw.speaker()[0].shape,
        "holder_0": bays[0],
        "holder_1": bays[1],
        "tact_switch": ts_parts[0].shape,
        "tact_stem": ts_parts[2].shape,  # square: its corners must clear the bore
        "spring": spring.shape,
        "bar_sweep": sweep,
        **leads,
        "rocker": rocker[2].shape + rocker[3].shape,
        **fasteners,
        "bridge_screw": bridge_screw,
        "bridge_insert": bridge_insert,
        **cables,
    }
    # Every separate printed piece; with disjoint_shells they may not overlap
    # each other either. Features fused into one print are listed apart only so
    # the board may touch them without hiding anything else.
    shells = {
        **face_shapes,
        "grille_plug": plug,
        "plug_ribs": plug_ribs,
        "tub": tub,
        "tub_latch": latch,
        "tub_ribs": tub_ribs_all,
        "plunger": plunger,
        "ptt_pin": pin,
        "bridge": bridge,
        "bridge_ribs": bridge_ribs,
    }
    allowed = {
        # one print
        ("faceplate", "faceplate_standoffs"),
        ("faceplate", "faceplate_boots"),
        ("faceplate", "faceplate_light"),
        ("faceplate", "faceplate_ribs"),
        ("grille_plug", "plug_ribs"),
        ("tub", "tub_ribs"),
        ("bridge", "bridge_ribs"),
        # meant to touch
        ("board", "faceplate_standoffs"),
        ("board", "faceplate_boots"),
        ("board", "faceplate_light"),
        ("board", "faceplate_ribs"),  # centring ribs on the PCB edge
        ("board_screws", "board"),  # through its holes
        ("board_screws", "faceplate_standoffs"),  # self-tapping
        ("board_screws", "faceplate"),  # the pilot runs on into the face wall
        ("case_screws", "inserts"),
        ("inserts", "tub"),  # melted in
        ("face_inserts", "faceplate"),  # the bottom pair, in the faceplate bosses
        ("case_screws", "face_inserts"),
        ("grille_plug", "faceplate_ribs"),  # crush ribs in its window
        ("tub", "tub_latch"),
        ("tact_switch", "tub_ribs"),  # crush ribs
        ("holder_0", "tub_ribs"),
        ("holder_1", "tub_ribs"),
        ("holder_0", "bridge_ribs"),
        ("holder_1", "bridge_ribs"),
        ("bridge_screw", "bridge_insert"),
        ("bridge_insert", "tub"),
        ("speaker", "plug_ribs"),
        ("speaker", "grille_plug"),  # its rim lands on the clamp ring
        ("speaker", "tub_ribs"),
        ("tact_stem", "tact_switch"),  # one part
        ("plunger", "tact_stem"),  # rests on it
        ("plunger", "spring"),  # the spring sits on it
        ("spring", "bar_sweep"),  # it is what the lever compresses
        ("ptt_pin", "bar_sweep"),  # the lever turns on it
        ("lead_pos_0", "holder_0"),  # leads leave their holders
        ("lead_pos_1", "holder_1"),
        ("lead_neg_0", "holder_0"),
        ("lead_neg_1", "holder_1"),
        ("lead_pos_0", "lead_pos_1"),  # parallel junctions
        ("lead_neg_0", "lead_neg_1"),
        ("cable_bat_sw", "lead_pos_0"),
        ("cable_bat_sw", "lead_pos_1"),
        ("rocker", "cable_bat_pos"),  # soldered to its tabs
        ("rocker", "cable_bat_sw"),
        ("cable_bat_pos", "cable_bat_sw"),
        ("cable_spk", "board"),  # leads start in the plug zone
        ("cable_bat_neg", "lead_neg_0"),  # the - joint
        ("cable_bat_neg", "lead_neg_1"),
        ("cable_ptt_a", "cable_ptt_b"),  # a pair
        ("cable_ptt_a", "tact_switch"),  # soldered to its legs
        ("cable_ptt_b", "tact_switch"),
        ("cable_spk", "speaker"),  # soldered to the tab
    }
    occupied = [envelopes["speaker"], *bays, boss, backing, face_shapes["faceplate_standoffs"]]
    occupied += [p.shape for p in board if p.name == "board_pcb"]
    back_cc = cavity_cc(inner, occupied)
    return Concept(
        "hx1",
        "HX-1",
        parts,
        envelopes,
        shells,
        allowed,
        {"back_volume_cc": back_cc},
        disjoint_shells=True,
    )
