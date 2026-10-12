"""HX-1's TPU bumper: a band round the case's four narrow sides with lips
over the front and back, and a strap loop on top (2026-10-12, Mario's
mother's idea: protect it from falls, and let his grandfather hang it from
his neck or his wrist).

It prints on its front, the front lip flat on the bed, so every wall
stands vertical and nothing needs support. The front lip is the seat: it
hugs the case's rounded edge and is never stretched. The back lip is the
way in: narrow, its underside a 45 deg cone that rests on the case's
rounded edge, and broken where the lever's window opens to the back (a U),
so the case goes in front first and the lever slides along the U.

Device frame as in hx1: x right, y back (the face at hx1.Y_FRONT), z up.
"""

from __future__ import annotations

import math

from build123d import (
    Axis,
    Box,
    Cylinder,
    Plane,
    Polygon,
    Pos,
    RectangleRounded,
    Rot,
    chamfer,
    extrude,
    fillet,
    loft,
)

from huxcase.concepts import hx1 as m
from huxcase.shapes import rounded_box

WALL = 1.5  # the flat sides: thin, the walkie stays small in the hand (Mario, 2026-10-12)
R_OUT = (
    3.0  # outer corners tighter than the case's R8: the TPU fills the round, 4.2 mm on each corner
)
LIP_T = 1.5  # both lips stand this proud of their faces
EDGE_R = 2.0  # the back's outer edges
BED_CHAMFER = 0.8  # the front's outer edge, on the bed
CLAMP = 0.1  # each lip presses its face by this; round the band the fit is nominal
# The front lip, measured in from the case's outline: wide along the sides,
# narrow at the top (face screws) and the bottom (the grille starts at z 2.8).
FRONT_LIP_SIDE = 5.0
FRONT_LIP_END = 2.5
FRONT_LIP_WIDE_TO_Z = 90.0  # the face screws' heads reach down to z 93.4
# The back lip: reaches 2.0 in at its top, its edge vertical down to the
# 45 deg cone, which touches the case's R3 edge where that edge is at 45 deg.
BACK_LIP = 2.0
CASE_EDGE_R = 3.0
WINDOW_R = 2.0  # window corners: TPU tears from a sharp one

# The lever's bar: 18 wide in y round SWITCH_Y, z 43..99, out to x 38.
LEVER_Y0 = m.SWITCH_Y - m.BAR_W / 2  # its front face, y -6.5
LEVER_FRONT_CLEAR = 1.5
LEVER_Z = (42.0, 100.0)  # the bar plus 1 mm each end
# USB-C: the tub's opening runs from the faceplate seam to the pocket's back.
USB_Y = (m.Y_FRONT + m.T, m.USB_Y + 3.8)
USB_Z = (m.BOARD_Z - 6.8, m.BOARD_Z + 6.8)
USB_CLEAR, USB_FUNNEL = 1.0, 3.0  # per side, at the case and at the TPU's face (z only)
# The rocker's recess opening plus 0.3, its back corners filled at 45 deg so
# the bridge over it (printed on its front) is short.
ROCKER_Y = (-6.0, 16.6)
ROCKER_Z = (84.8, 101.4)
ROCKER_FILL = 2.6
# Strap loop: on top, near the centre of mass (the cells sit behind centre),
# its hole along y; its front face at 45 deg so it prints standing on its front.
LOOP_X, LOOP_Y = -1.0, 1.5
LOOP_BASE_W = 25.0
LOOP_HOLE = 5.0
LOOP_RIM = 3.0  # TPU round the hole
LOOP_LIFT = 1.5  # the hole's bottom above the band, for the cord
LOOP_D = 14.0  # along y, at its base


def _cy() -> float:
    return (m.Y_FRONT + m.Y_BACK) / 2


def _top() -> float:
    return m.H + WALL


def case_outside():
    """The case's outer surface, as hx1.build() makes it."""
    return Pos(0, _cy(), m.H / 2) * rounded_box(m.W, m.D, m.H, m.R_FACE, CASE_EDGE_R)


def _xz_face(y: float, w: float, h: float, r: float, zc: float = m.H / 2):
    """A rounded rectangle in the x-z plane at y, centred on x = 0, z = zc."""
    pl = Plane(origin=(0, y, zc), x_dir=(1, 0, 0), z_dir=(0, 1, 0))
    return (pl * RectangleRounded(w, h, r)).face()


def _yz_face(x: float, y0: float, y1: float, z0: float, z1: float, r: float):
    """A rounded rectangle in the y-z plane at x."""
    pl = Plane(origin=(x, (y0 + y1) / 2, (z0 + z1) / 2), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    return (pl * RectangleRounded(y1 - y0, z1 - z0, r)).face()


def _at(edges, axis: Axis, v: float):
    return edges.filter_by_position(axis, v - 0.01, v + 0.01)


def _outer():
    y0, y1 = m.Y_FRONT - LIP_T, m.Y_BACK + LIP_T
    b = Pos(0, (y0 + y1) / 2, m.H / 2) * Box(m.W + 2 * WALL, y1 - y0, m.H + 2 * WALL)
    b = fillet(b.edges().filter_by(Axis.Y), R_OUT)
    b = fillet(_at(b.edges().filter_by(Axis.Y, reverse=True), Axis.Y, y1), EDGE_R)
    return chamfer(_at(b.edges().filter_by(Axis.Y, reverse=True), Axis.Y, y0), BED_CHAMFER)


def _inner():
    """The case, pressed on by each lip by CLAMP; nominal round the band."""
    return Pos(0, _cy(), m.H / 2) * rounded_box(m.W, m.D - 2 * CLAMP, m.H, m.R_FACE, CASE_EDGE_R)


def _front_window():
    y0, y1 = m.Y_FRONT - LIP_T - 1, m.Y_FRONT + 1
    z0, z1 = FRONT_LIP_END, m.H - FRONT_LIP_END
    wide = extrude(
        _xz_face(y0, m.W - 2 * FRONT_LIP_SIDE, z1 - z0, CASE_EDGE_R, (z0 + z1) / 2), y1 - y0
    )
    zt = FRONT_LIP_WIDE_TO_Z
    top = Pos(0, (y0 + y1) / 2, (zt + z1) / 2) * Box(m.W - 2 * FRONT_LIP_END, y1 - y0, z1 - zt)
    top = fillet(_at(top.edges().filter_by(Axis.Y), Axis.Z, z1), m.R_FACE - FRONT_LIP_END)
    step = (FRONT_LIP_SIDE - FRONT_LIP_END) / 2
    top = fillet(_at(top.edges().filter_by(Axis.Y), Axis.Z, zt), step)
    return wide + top


def _back_window():
    """The back lip's opening: a 45 deg cone touching the case's rounded edge
    where that edge is at 45 deg, then straight up from BACK_LIP in."""
    yb = m.Y_BACK - CLAMP  # the case's back face, as pressed
    d45 = CASE_EDGE_R * (
        1 - math.cos(math.radians(45))
    )  # the edge at 45 deg: this far in, this deep
    y_cone0 = yb - 2 * d45  # where the cone meets the outline
    y_cone1 = y_cone0 + BACK_LIP  # where it reaches BACK_LIP in
    f0 = _xz_face(y_cone0, m.W, m.H, m.R_FACE)
    f1 = _xz_face(y_cone1, m.W - 2 * BACK_LIP, m.H - 2 * BACK_LIP, m.R_FACE - BACK_LIP)
    return loft([f0, f1]) + extrude(f1, m.Y_BACK + LIP_T + 2 - y_cone1)


def _lever_u():
    """Open to the back: the lever slides along it as the case goes in."""
    y0, y1 = LEVER_Y0 - LEVER_FRONT_CLEAR, m.Y_BACK + LIP_T + 2
    z0, z1 = LEVER_Z
    b = Pos(m.X_OUT + 3, (y0 + y1) / 2, (z0 + z1) / 2) * Box(8, y1 - y0, z1 - z0)
    return fillet(_at(b.edges().filter_by(Axis.X), Axis.Y, y0), WINDOW_R)


def _usb_window():
    """A funnel in z (the plug's long side), so a plug aimed by feel finds it."""
    y0, y1 = USB_Y[0] - USB_CLEAR, USB_Y[1] + USB_CLEAR
    z0, z1 = USB_Z
    f_in = _yz_face(-m.X_OUT + 0.01, y0, y1, z0 - USB_CLEAR, z1 + USB_CLEAR, WINDOW_R)
    f_out = _yz_face(-m.X_OUT - WALL - 0.01, y0, y1, z0 - USB_FUNNEL, z1 + USB_FUNNEL, WINDOW_R)
    straight = Pos(-m.X_OUT + 1, (y0 + y1) / 2, (z0 + z1) / 2) * Box(
        2.2, y1 - y0, z1 - z0 + 2 * USB_CLEAR
    )
    return loft([f_in, f_out]) + straight


def _rocker_window():
    y0, y1 = ROCKER_Y
    z0, z1 = ROCKER_Z
    f = ROCKER_FILL
    # y-z outline, the two back corners cut at 45 deg (the bridge's ends)
    prof = Polygon(
        (y0, z0), (y1 - f, z0), (y1, z0 + f), (y1, z1 - f), (y1 - f, z1), (y0, z1), align=None
    )
    pl = Plane(origin=(-m.X_OUT - WALL - 1, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    w = extrude(pl * prof, WALL + 2)
    return fillet(_at(w.edges().filter_by(Axis.X), Axis.Y, y0), WINDOW_R)


def _loop():
    top = _top()
    hz = top + LOOP_LIFT + LOOP_HOLE / 2
    ro = LOOP_HOLE / 2 + LOOP_RIM
    y0, y1 = LOOP_Y - LOOP_D / 2, LOOP_Y + LOOP_D / 2
    # front view (x-z): a wide base narrowing to a round top over the hole
    side = Polygon(
        (LOOP_X - LOOP_BASE_W / 2, top - 1),
        (LOOP_X + LOOP_BASE_W / 2, top - 1),
        (LOOP_X + LOOP_BASE_W / 2, top),
        (LOOP_X + ro, hz),
        (LOOP_X - ro, hz),
        (LOOP_X - LOOP_BASE_W / 2, top),
        align=None,
    )
    # x_dir x, normal -y: the plane's own y is device z
    pl_xz = Plane(origin=(0, y1, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    body = extrude(pl_xz * side, y1 - y0)
    body = body + Pos(LOOP_X, (y0 + y1) / 2, hz) * Rot(90, 0, 0) * Cylinder(ro, y1 - y0)
    # side view (y-z): the front face at 45 deg, so it prints standing on its front
    ztop = hz + ro
    prof = Polygon(
        (y0, top - 1),
        (y1, top - 1),
        (y1, ztop + 1),
        (y0 + ztop + 1 - top, ztop + 1),
        (y0, top),
        align=None,
    )
    pl_yz = Plane(origin=(LOOP_X - LOOP_BASE_W, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    body = body & extrude(pl_yz * prof, 2 * LOOP_BASE_W)
    hole = Pos(LOOP_X, (y0 + y1) / 2, hz) * Rot(90, 0, 0) * Cylinder(LOOP_HOLE / 2, y1 - y0 + 4)
    return body - hole


def bumper():
    inner = _inner()
    band = _outer() - inner - _front_window() - _back_window()
    band = band - _lever_u() - _usb_window() - _rocker_window()
    # The floor screws' heads sit where the floor starts curving into its R8
    # corners, a hair proud of the curve: a pocket under each.
    for x, y in m.BOTTOM_SCREWS:
        band = band - Pos(x, y, 0) * Cylinder(m.CBORE_D / 2 + 0.4, 2.0)
    return band + (_loop() - inner)


def front_ring(depth: float = 8.0):
    """The bumper's front, `depth` deep from its bed face: a quick fit test
    of the outline and the front lip's seat."""
    y0 = m.Y_FRONT - LIP_T
    return bumper() & (Pos(0, y0 + depth / 2 - 1, m.H / 2) * Box(200, depth + 2, 200))
