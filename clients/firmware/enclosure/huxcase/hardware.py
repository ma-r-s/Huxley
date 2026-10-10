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
SPK_CABLE_L = 97.0  # measured by Mario (2026-10-10): speaker to the end of its plug
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


# Spring drawing helper (the series spring's OD).
SPRING_D = 4.0


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


# --- 12 x 12 tactile switch PTT (Mario's, from his dev-kit drawer) ------------
# Measured by Mario (2026-10-10): 7.3 mm from the base to the top of the stem,
# stem a 3.8 mm square seen from above. Body 12 x 12 x 4.0 and 4 legs assumed
# (generic). Actuation ~1.6-2.6 N, 0.25 mm.
TACT_W, TACT_BODY, TACT_STEM_W, TACT_STEM_H = 12.0, 4.0, 3.8, 3.3
TACT_TRAVEL, TACT_FORCE = 0.25, 2.0
TACT_LEGS = 3.5

# Series spring from the SciWil kit: 0.4 wire x 4 OD x 15 free, 10 coils.
SERIES_SPRING_D, SERIES_SPRING_FREE, SERIES_SPRING_K = 4.0, 15.0, 0.59  # N/mm (304 SS, 8 active)


def tact_switch() -> list[Part]:
    """Local frame: body base at z = 0, stem up +z (stem top at TACT_BODY + TACT_STEM_H)."""
    body = Pos(0, 0, TACT_BODY / 2) * Box(TACT_W, TACT_W, TACT_BODY)
    plate = Pos(0, 0, TACT_BODY - 0.15) * Box(TACT_W - 0.6, TACT_W - 0.6, 0.3)
    stem = Pos(0, 0, TACT_BODY + TACT_STEM_H / 2) * Box(TACT_STEM_W, TACT_STEM_W, TACT_STEM_H)
    legs = None
    for sx in (-1, 1):
        for sy in (-1, 1):
            leg = Pos(sx * 6.25, sy * 2.5, -TACT_LEGS / 2) * Box(0.7, 1.0, TACT_LEGS)
            legs = leg if legs is None else legs + leg
    return [
        Part("tact_body", body, "black"),
        Part("tact_plate", plate, "metal"),
        Part("tact_stem", stem, "black"),
        Part("tact_legs", legs, "metal"),
    ]


# --- KCD1-101 rocker (Mario's on/off) ------------------------------------------
# Measured by Mario (2026-10-10): 18.6 mm from the underside of the frame to
# the tips of the solder tabs. Assumed (generic): bezel 21 x 15 x 2 outside the
# panel, snap body 19 x 13 x 11.5 behind it (so the tabs stand 7.1), tabs 6 mm
# apart, rocker ~5 mm above the bezel.
ROCKER_BEZEL = (21.0, 15.0, 2.0)
ROCKER_BODY = (19.0, 13.0, 11.5)
ROCKER_DEPTH = 18.6  # measured: frame underside to tab tips
ROCKER_TABS, ROCKER_TAB_PITCH, ROCKER_CAP = ROCKER_DEPTH - ROCKER_BODY[2], 6.0, 5.0

# 1S Li-ion protection board (DW01 + 8205 class): reserved, not fitted.
PROTECTION_PCB = (26.0, 5.0, 3.5)


def rocker() -> list[Part]:
    """Local frame: panel (outer face of the wall it clips into) at z = 0,
    bezel and rocker outward (+z), body and tabs inward (-z); long side along x."""
    bx, by, bz = ROCKER_BEZEL
    cx, cy, cz = ROCKER_BODY
    bezel = Pos(0, 0, bz / 2) * Box(bx, by, bz)
    cap = Pos(0, 0, bz + ROCKER_CAP / 2) * Box(bx - 4.0, by - 4.0, ROCKER_CAP)
    body = Pos(0, 0, -cz / 2) * Box(cx, cy, cz)
    tabs = None
    for sx in (-1, 1):
        t = Pos(sx * ROCKER_TAB_PITCH / 2, 0, -cz - ROCKER_TABS / 2) * Box(0.8, 4.8, ROCKER_TABS)
        tabs = t if tabs is None else tabs + t
    return [
        Part("rocker_bezel", bezel, "black"),
        Part("rocker_cap", cap, "black"),
        Part("rocker_body", body, "black"),
        Part("rocker_tabs", tabs, "metal"),
    ]


# --- Single-18650 holder with leads (Mario's) -----------------------------------
# Measured by Mario (2026-10-10): 76 x 21 x 20.5 outside. Assumed: open frame,
# floor on one side, side walls up to the cell's centre plane, full-height end
# caps with the spring (-) at one end and the flat contact (+) at the other;
# one lead out of each end cap near the floor. Holds a 65 mm flat-top cell.
HOLDER_L, HOLDER_W, HOLDER_H = 76.0, 21.0, 20.5
HOLDER_FLOOR, HOLDER_WALL, HOLDER_CAP = 1.2, 1.2, 3.0
HOLDER_SPRING = 4.5  # compressed spring between the - cap and the cell
HOLDER_CELL_L = 65.0


def holder() -> list[Part]:
    """Local frame: cell axis along z centred at the origin, floor toward +y."""
    r = CELL_D / 2
    back = r + HOLDER_FLOOR
    floor = Pos(0, r + HOLDER_FLOOR / 2, 0) * Box(HOLDER_W, HOLDER_FLOOR, HOLDER_L)
    frame = floor
    for sx in (-1, 1):
        wall = Pos(sx * (HOLDER_W - HOLDER_WALL) / 2, back / 2, 0) * Box(
            HOLDER_WALL, back, HOLDER_L
        )
        frame += wall
    for sz in (-1, 1):
        cap = Pos(0, back - HOLDER_H / 2, sz * (HOLDER_L - HOLDER_CAP) / 2) * Box(
            HOLDER_W, HOLDER_H, HOLDER_CAP
        )
        frame += cap
    inner = HOLDER_L / 2 - HOLDER_CAP
    spring = Pos(0, 0, -inner + HOLDER_SPRING / 2) * (
        Cylinder(4.5, HOLDER_SPRING) - Cylinder(3.9, HOLDER_SPRING + 1)
    )
    plate = Pos(0, 0, inner - 0.2) * Cylinder(5.0, 0.4)
    cell = Pos(0, 0, -inner + HOLDER_SPRING + HOLDER_CELL_L / 2) * Cylinder(r, HOLDER_CELL_L)
    assert -inner + HOLDER_SPRING + HOLDER_CELL_L <= inner - 0.4  # cell ends on the + plate
    return [
        Part("holder", frame, "black"),
        Part("holder_spring", spring, "metal"),
        Part("holder_plate", plate, "metal"),
        Part("cell", cell, "cell"),
    ]


def holder_envelope() -> Solid:
    """Frame + cell, for the fit check (same local frame as holder())."""
    parts = holder()
    out = parts[0].shape
    for p in parts[1:]:
        out += p.shape
    return out
