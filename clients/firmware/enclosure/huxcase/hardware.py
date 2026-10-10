"""Measured hardware for the Huxley walkie: Waveshare ESP32-S3-AUDIO-Board,
its SPK-4020-5W speaker, an 18650 cell and an MX-style PTT switch.

Every number here comes from Waveshare's official STEP assembly
(reference/drawing/ESP32-S3-AUDIO-Board.stp, Creo export 2026-03-05) unless
the comment says otherwise. See MEASUREMENTS.md for the table and sources.

Board frame (all board builders use it):
  origin  = PCB centre, z = 0 on the PCB top (component) surface
  +x      = toward the TF card slot
  +y      = toward the mics and the 2x9 pin header
  -y      = toward the USB-C port
  +z      = out of the top face (mics, USB-C, side buttons are on top)
STEP z maps to board z as z_board = z_step + 7.6.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from build123d import (
    Box,
    Circle,
    Cone,
    Cylinder,
    Location,
    Pos,
    Rectangle,
    Rot,
    Solid,
    extrude,
)

# --- PCB ---------------------------------------------------------------------
PCB_R = 28.82  # outline radius (STEP: polygonised circle)
PCB_FLAT_POS_Y = 28.31  # flat on the mic/header side, |x| <= 5.5
PCB_FLAT_NEG_Y = -28.23  # flat on the USB-C side, |x| <= 5.5
PCB_T = 1.20  # thickness: 1.2 mm, not the usual 1.6
HOLE_D = 2.90  # M2.5 clearance (the hole at (19.18, -14) is 3.15)
HOLES = ((-23.75, 0.0), (19.18, 14.0), (19.18, -14.0))  # all on a 47.5 PCD

# Height envelopes measured from the PCB surfaces.
TOP_MAX = 5.68  # camera connector at the centre; header is 5.0, USB-C 3.25
BOTTOM_MAX = 1.75  # tallest generic part on the underside (flash IC)
BOTTOM_CONN = 5.8  # BAT / SPK connectors with mated plug

# Side-actuated tact switches: angle (deg, from +x), tips at r = 29.7, z = +0.6.
KEYS = {"RESET": -116.1, "BOOT": -129.1, "KEY3": -142.1, "KEY2": -155.0, "KEY1": -168.0}
KEY_TIP_R = 29.7
SLIDE_SWITCH_ANGLE = -55.2  # battery ON/OFF slide switch
SLIDE_SWITCH_TIP_R = 30.91

MICS = ((-17.28, 20.55), (17.20, 20.52))  # top face, top-ported (no PCB hole)
ANTENNA = (-25.7, 8.3)  # ceramic chip antenna, top face, keep metal away
LED_R = 22.0  # 7 x WS2812B on the underside, every 360/7 deg from +x
USB_C_CENTER_Z = 1.22  # receptacle centre above the PCB top
USB_C_FACE_Y = -28.40
CHG_LED = (9.36, -25.99)  # charge indicator, top face

# Bottom-side connectors (centres)
BAT_CONN = (17.81, -6.04)
SPK_CONN = (17.81, 5.19)
RTC_CONN = (-17.53, -6.0)

# --- Speaker: SPK-4020-5W (as shipped in the kit) ------------------------------
SPK_D = 40.0
SPK_DEPTH = 20.5
SPK_PROFILE = (  # (depth from front, radius) along the axis
    (0.0, 20.0),
    (2.6, 19.5),
    (12.3, 15.8),
    (12.7, 10.9),
    (19.0, 10.9),
    (19.0, 9.4),
    (20.5, 9.4),
)

# --- 18650 -------------------------------------------------------------------
# Not measured: worst-case protected button-top cell. Bare cells are 65 mm.
CELL_D = 18.6
CELL_L = 70.0
CELL_BAY_D = 19.4  # printed cradle bore
CELL_BAY_L = 74.0  # cell + spring contact at one end + flat contact at the other

# --- MX-style PTT switch (Cherry MX footprint) ---------------------------------
MX_CUTOUT = 14.0
MX_BELOW_PLATE = 5.0
MX_SOCKET = 1.8  # Kailh hot-swap socket under the switch
MX_ABOVE_PLATE = 6.6
MX_STEM_TOP = 10.2  # stem top above plate, unpressed
MX_TRAVEL = 4.0
STAB_SPACING = {2.0: 23.8, 3.0: 38.1}  # plate-mount stabiliser stem spacing by key width (u)


@dataclass(frozen=True)
class Part:
    """A named, coloured solid. `kind` drives the viewer/renderer material."""

    name: str
    shape: Solid
    kind: str


def _polar(r: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


def _bbox_solid(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Solid:
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def pcb() -> Solid:
    outline = Circle(PCB_R) & Pos(0, (PCB_FLAT_POS_Y + PCB_FLAT_NEG_Y) / 2) * Rectangle(
        2 * PCB_R + 2, PCB_FLAT_POS_Y - PCB_FLAT_NEG_Y
    )
    for x, y in HOLES:
        outline -= Pos(x, y) * Circle(HOLE_D / 2)
    return Pos(0, 0, -PCB_T) * extrude(outline, PCB_T)


def board_parts() -> list[Part]:
    """Simplified PCBA: the parts a case must clear, sized from the STEP bboxes."""
    parts = [Part("pcb", pcb(), "pcb")]
    top = [
        ("usb_c", (-4.47, 4.47, -28.40, -20.80, -0.81, 3.25), "metal"),
        ("header_2x9_ra", (-11.43, 11.43, 13.50, 26.50, 0.0, 5.0), "black"),
        ("camera_conn", (-8.85, 8.85, -2.60, 2.23, 0.0, TOP_MAX), "black"),
        ("lcd_fpc", (-6.61, 6.59, -10.16, -4.16, 0.0, 2.0), "black"),
        ("tf_socket", (10.21, 25.21, -7.53, 8.57, -0.6, 1.85), "metal"),
        ("antenna", (-27.71, -23.65, 4.71, 11.98, 0.0, 1.0), "white"),
        ("ipex", (-22.99, -19.51, 13.97, 17.28, 0.0, 1.25), "metal"),
        ("chg_led", (8.04, 10.68, -27.04, -24.94, 0.0, 1.0), "white"),
        ("esp32s3", (-16.67, -9.67, 2.41, 9.41, 0.0, 1.0), "black"),
    ]
    for i, (x, y) in enumerate(MICS):
        top.append((f"mic_{i}", (x - 2.14, x + 2.14, y - 2.09, y + 2.09, 0.0, 1.0), "metal"))
    bottom = [
        ("bat_conn", (15.71, 19.91, -9.86, -2.21, -PCB_T - BOTTOM_CONN, -PCB_T), "white"),
        ("spk_conn", (15.71, 19.91, 1.36, 9.01, -PCB_T - BOTTOM_CONN, -PCB_T), "white"),
        ("rtc_conn", (-19.55, -15.50, -8.18, -3.83, -PCB_T - 4.5, -PCB_T), "white"),
        ("flash", (-12.80, -7.80, -6.50, -0.30, -PCB_T - BOTTOM_MAX, -PCB_T), "black"),
    ]
    for name, (x0, x1, y0, y1, z0, z1), kind in top + bottom:
        parts.append(Part(name, _bbox_solid(x0, x1, y0, y1, z0, z1), kind))
    for i in range(7):
        x, y = _polar(LED_R, i * 360 / 7)
        led = Pos(x, y, -PCB_T - 0.75) * Box(3.2, 3.2, 1.5)
        parts.append(Part(f"led_{i}", led, "white"))
    for name, deg in KEYS.items():
        body = Pos(KEY_TIP_R - 0.9 - 1.6, 0, 0.7) * Box(3.2, 4.4, 2.2)
        nub = Pos(KEY_TIP_R - 0.45, 0, 0.6) * Box(0.9, 1.6, 1.0)
        parts.append(Part(f"key_{name.lower()}", Rot(0, 0, deg) * (body + nub), "white"))
    sw_body = Pos(27.1, 0, 0.65) * Box(3.6, 8.0, 2.7)
    sw_knob = Pos((28.9 + SLIDE_SWITCH_TIP_R) / 2, 0, 0.7) * Box(
        SLIDE_SWITCH_TIP_R - 28.9, 1.6, 1.4
    )
    parts.append(
        Part("slide_switch", Rot(0, 0, SLIDE_SWITCH_ANGLE) * (sw_body + sw_knob), "metal")
    )
    return parts


def board_keepout(top: float = 6.0, bottom: float = 2.5, conn: float = 9.0) -> Solid:
    """Volume nothing else may enter: board disc + clearances + edge protrusions.

    `conn` reserves room under the BAT/SPK connectors for the mated plugs and
    the cable bend.
    """
    z0, z1 = -PCB_T - bottom, top
    disc = Pos(0, 0, (z0 + z1) / 2) * Cylinder(PCB_R + 0.3, z1 - z0)
    # Respect the two flats (USB-C sits on the -y one).
    disc &= _bbox_solid(-40, 40, PCB_FLAT_NEG_Y - 0.3, PCB_FLAT_POS_Y + 0.3, z0 - 1, z1 + 1)
    disc += _bbox_solid(-4.6, 4.6, USB_C_FACE_Y - 0.1, -20.0, -0.9, 3.4)
    plugs = _bbox_solid(14.2, 23.5, -11.5, 10.5, -PCB_T - conn, -PCB_T)
    rtc = _bbox_solid(-20.5, -14.5, -9.0, -3.0, -PCB_T - 6.0, -PCB_T)
    edge = [p.shape for p in board_parts() if p.name.startswith(("key_", "slide_switch"))]
    out = disc + plugs + rtc
    for e in edge:
        out += e
    return out


def speaker() -> list[Part]:
    """SPK-4020-5W. Local frame: front (cone side) at z = 0, back toward +z."""
    body = None
    prof = SPK_PROFILE
    for (z0, r0), (z1, r1) in itertools.pairwise(prof):
        if z1 - z0 < 1e-6:
            continue
        seg = Pos(0, 0, (z0 + z1) / 2) * (
            Cone(r0, r1, z1 - z0) if abs(r0 - r1) > 1e-6 else Cylinder(r0, z1 - z0)
        )
        body = seg if body is None else body + seg
    cone = Pos(0, 0, 0.9) * Cone(17.6, 6.0, 1.8)
    body = body - cone
    dust_cap = Pos(0, 0, 1.4) * Cylinder(6.0, 0.6)
    return [
        Part("speaker_frame", body, "speaker"),
        Part("speaker_cone", Pos(0, 0, 1.75) * Cone(17.6, 6.0, 0.3) + dust_cap, "cone"),
    ]


def cell() -> list[Part]:
    """18650, axis along local z, centred at the origin."""
    can = Cylinder(CELL_D / 2, CELL_L - 1.0)
    button = Pos(0, 0, CELL_L / 2 - 0.25) * Cylinder(3.5, 1.0)
    return [Part("cell", can, "cell"), Part("cell_button", button, "metal")]


def mx_switch() -> list[Part]:
    """Cherry MX footprint. Local frame: plate top surface at z = 0, stem up +z."""
    below = Pos(0, 0, -MX_BELOW_PLATE / 2) * Box(MX_CUTOUT, MX_CUTOUT, MX_BELOW_PLATE)
    socket = Pos(0, 0, -MX_BELOW_PLATE - MX_SOCKET / 2) * Box(13.0, 10.0, MX_SOCKET)
    above = Pos(0, 0, MX_ABOVE_PLATE / 2) * Box(15.6, 15.6, MX_ABOVE_PLATE)
    stem = Pos(0, 0, (MX_ABOVE_PLATE + MX_STEM_TOP) / 2) * Box(
        5.5, 5.5, MX_STEM_TOP - MX_ABOVE_PLATE
    )
    return [
        Part("mx_housing", below + above, "switch"),
        Part("mx_socket", socket, "black"),
        Part("mx_stem", stem, "stem"),
    ]


def stabiliser_pair(spacing: float) -> Part:
    """Plate-mount stabiliser housings + wire, simplified, in the MX local frame."""
    out = None
    for s in (-spacing / 2, spacing / 2):
        h = Pos(s, 0, -3.5) * Box(6.6, 12.0, 7.0) + Pos(s, 0, 3.0) * Box(6.6, 8.0, 6.0)
        out = h if out is None else out + h
    wire = Pos(0, -8.6, 0.8) * Box(spacing, 1.2, 1.2)  # runs beside the switch
    return Part("stabiliser", out + wire, "switch")


def place(parts: list[Part], loc: Location, prefix: str = "") -> list[Part]:
    return [Part(prefix + p.name, loc * p.shape, p.kind) for p in parts]


# --- Mouse microswitch PTT (Omron D2F / D2FC class, from any old mouse) -------
# Not measured: the common mouse-switch envelope. Mario measures his before print.
MS_L, MS_W, MS_H = 12.8, 5.8, 6.5  # body length, width, height (button side up)
MS_BUTTON = (2.8, 1.6, 0.8)  # button footprint (along L, along W) and height above body
MS_PRETRAVEL, MS_OVERTRAVEL = 0.3, 0.5
MS_PINS = 3.5

# Return spring: a ballpoint-pen spring.
SPRING_D, SPRING_FREE = 4.0, 12.0


def microswitch() -> list[Part]:
    """Local frame: body bottom at z = -MS_H, top (button side) at z = 0, button up +z,
    length along x."""
    body = Pos(0, 0, -MS_H / 2) * Box(MS_L, MS_W, MS_H)
    bl, bw, bh = MS_BUTTON
    button = Pos(0, 0, bh / 2) * Box(bl, bw, bh)
    pins = None
    for x in (-5.0, 0.0, 5.0):
        p = Pos(x, 0, -MS_H - MS_PINS / 2) * Box(0.6, 3.0, MS_PINS)
        pins = p if pins is None else pins + p
    return [
        Part("ms_body", body, "black"),
        Part("ms_button", button, "white"),
        Part("ms_pins", pins, "metal"),
    ]


def spring(length: float) -> Part:
    """Compression spring along +z from z = 0, drawn as a ribbed tube."""
    coil = None
    turns = int(length / 0.9)
    for i in range(turns + 1):
        ring = Pos(0, 0, i * length / turns) * (
            Cylinder(SPRING_D / 2, 0.4) - Cylinder(SPRING_D / 2 - 0.4, 1.0)
        )
        coil = ring if coil is None else coil + ring
    return Part("spring", coil, "metal")


def cable(points: list[tuple[float, float, float]], d: float = 1.4):
    """A wire bundle swept along a smooth path through `points`."""
    from build123d import Plane, Spline, sweep

    path = Spline(*points)
    start = path.location_at(0)
    profile = Plane(origin=start.position, z_dir=path.tangent_at(0)) * Circle(d / 2)
    return sweep(profile, path=path)
