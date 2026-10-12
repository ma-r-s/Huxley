"""HX-1's face art (Mario's design, 2026-10-12): "hola" in braille as ten
raised dots about twice braille size, and a small black HX-1 badge with
orange letters. Both are printed separately and glued on with CA. A printed
stencil that hooks over the top of the case puts each one in place.

Layout, on lines the face already has:
- the grille's left edge (x -22) starts the word and the badge; round shapes
  sit OVERSHOOT past it, which reads as flush;
- the mic rings' axis (x 19.35) ends the word: the final "a" is one dot on
  that axis, so the right side reads ring, dot, ring;
- the word sits midway between the two mic rings; the badge sits on the
  lower ring's row, as tall as a ring, a nameplate over the speaker.

Letters alternate between two dot colours (h and l in the first, o and a in
the second), so each braille cell shows as one colour.

Device frame as in hx1: x right, y back (the face at hx1.Y_FRONT), z up.
"""

from __future__ import annotations

import math

from build123d import (
    Axis,
    Cylinder,
    FontStyle,
    Line,
    Location,
    Pos,
    Rot,
    Text,
    ThreePointArc,
    Wire,
    extrude,
    fillet,
    make_face,
    revolve,
)

from huxcase import fits
from huxcase import hardware as hw
from huxcase.concepts import hx1 as m
from huxcase.shapes import bbox_solid, rounded_box

WORD = "hola"
LETTERS = {"h": "125", "o": "135", "l": "123", "a": "1"}  # dots 1-2-3 down the left, 4-5-6 right

GRILLE_X = m.PANEL_W / 2  # the grille's half width: the column everything sits in
MIC_X = sum(m.to_device(*p)[0] for p in hw.MICS) / len(hw.MICS)
ROW_LO = min(m.to_device(*p)[1] for p in hw.MICS)  # the lower mic ring's centre line
ROW_HI = max(m.to_device(*p)[1] for p in hw.MICS)
MIC_RING_R = 3.4  # outer edge of the "speak here" ring round each mic port (hx1._faceplate)
OVERSHOOT = 0.3  # a round edge this far past a straight one reads as flush with it

# The dots: braille's own proportions (dot 1.5, pitch 2.5) at about 2.1 times.
DOT_D = 3.2
DOT_H = 1.0  # stands this proud of the face
DOT_FOOT = 0.36  # straight under the dome (0.2 + two 0.08 layers), taller than the
# stencil's relief pocket, so the hole grips the dot at its full width
DOT_PITCH = 5.3  # dot to dot inside a cell
X_FIRST = -GRILLE_X - OVERSHOOT + DOT_D / 2  # the "h"'s left column
CELL_PITCH = (MIC_X - X_FIRST) / (len(WORD) - 1)  # puts the final "a" on the mic axis
Z_MID = (ROW_LO + ROW_HI) / 2  # the word's middle row

# The badge: a black pill, "HX-1" set into its face in orange.
BADGE_W, BADGE_H, BADGE_T = 17.0, 2 * MIC_RING_R, 1.0  # the black pill
BADGE_X = -GRILLE_X - OVERSHOOT + BADGE_W / 2
BADGE_Z = ROW_LO
# The letters stand RAISED on the pill's front (Mario, 2026-10-12): black
# below, orange above, so the filament changes once at one height and never
# inside a layer.
TEXT, TEXT_SIZE, RAISED = "HX-1", 5.0, 0.6
TEXT_TRACK = 0.4  # extra space between letters: 0.34 between H and X at no tracking

# The stencil: a 1 mm plate on the face from just over the grille to the top,
# a lip round the top and both sides that slides over the case.
STENCIL_T = 1.0
LIP_T, LIP_D = 1.6, 6.0  # lip thickness; how far it reaches back along the case
LIP_CLEAR = fits.SLIDE + 0.1  # over the case's walls: goes on and off by hand
STENCIL_Z0 = m.PANEL_H / 2 + m.SPK_Z + 0.5 + 3.2  # 3.2 over the grille's top edge
HOLE_CLEAR = 0.2  # round each dot and the badge, per side: drops in, lifts off
RELIEF, RELIEF_D = 0.6, 0.2  # pocket round each hole on the face side: CA squeeze-out
# stays off the stencil


def dot_centres() -> list[tuple[float, float, int]]:
    """(x, z, letter index) of every dot, in reading order."""
    out = []
    for i, ch in enumerate(WORD):
        for d in LETTERS[ch]:
            col, row = divmod(int(d) - 1, 3)
            x = X_FIRST + i * CELL_PITCH + col * DOT_PITCH
            out.append((x, Z_MID + DOT_PITCH - row * DOT_PITCH, i))
    return out


def dot():
    """One dot standing on z = 0: a straight foot, then a spherical cap with a
    0.6 mm flat on top, turned from one profile. (The cap's pole left a sliver
    triangle in the STL, and a foot unioned to a cap met at a seam.) The flat
    is what the last 0.08 mm layer prints anyway."""
    r = DOT_D / 2
    cap_h = DOT_H - DOT_FOOT
    big = (r * r + cap_h * cap_h) / (2 * cap_h)  # sphere through the cap's rim and top
    zc = DOT_H - big
    top_r = 0.3
    z_top = zc + math.sqrt(big * big - top_r * top_r)
    xm = (r + top_r) / 2
    profile = Wire(
        [
            Line((0, 0, 0), (r, 0, 0)),
            Line((r, 0, 0), (r, 0, DOT_FOOT)),
            ThreePointArc(
                (r, 0, DOT_FOOT), (xm, 0, zc + math.sqrt(big * big - xm * xm)), (top_r, 0, z_top)
            ),
            Line((top_r, 0, z_top), (0, 0, z_top)),
            Line((0, 0, z_top), (0, 0, 0)),
        ]
    )
    return revolve(make_face(profile), Axis.Z, 360)


def dot_group(n: int, pitch: float = 6.0):
    """n dots in two rows, `pitch` apart, printed as one object: in the device
    frame like the word's first dot (domes toward -y) and centred on it, so
    the print pose of that one dot (printability) centres the group too."""
    one = dot()
    cols = (n + 1) // 2
    rows = (n + cols - 1) // cols
    x0, z0, _ = dot_centres()[0]
    return [
        Location(
            (
                x0 + ((i % cols) - (cols - 1) / 2) * pitch,
                m.Y_FRONT,
                z0 + ((i // cols) - (rows - 1) / 2) * pitch,
            ),
            (90, 0, 0),
        )
        * one
        for i in range(n)
    ]


def dots_on_face():
    """Every dot where it sits: its base on the face, its dome toward the viewer."""
    one = dot()
    return [Location((x, m.Y_FRONT, z), (90, 0, 0)) * one for x, z, _ in dot_centres()]


def _pill(w: float, h: float, y0: float, y1: float):
    """The badge's outline (front view), w x h, fully rounded ends, from y0 to y1."""
    slab = bbox_solid(-w / 2, w / 2, y0, y1, -h / 2, h / 2)
    slab = fillet(slab.edges().filter_by(Axis.Y), h / 2 - 0.001)
    return Pos(BADGE_X, 0, BADGE_Z) * slab


def badge():
    """(body, letters) where they sit: the pill's back on the face, the
    letters standing RAISED on its front."""
    y_front = m.Y_FRONT - BADGE_T
    pill = _pill(BADGE_W, BADGE_H, y_front, m.Y_FRONT)
    # Menlo is monospaced (0.602 em per letter): set one letter at a time,
    # TEXT_TRACK apart, so the strokes keep two lines of black between them.
    step = 0.602 * TEXT_SIZE + TEXT_TRACK
    text = None
    for i, ch in enumerate(TEXT):
        g = Text(ch, font_size=TEXT_SIZE, font="Menlo", font_style=FontStyle.BOLD)
        g = Pos((i - (len(TEXT) - 1) / 2) * step, 0, 0) * g
        text = g if text is None else text + g
    letters = Location((BADGE_X, y_front, BADGE_Z), (90, 0, 0)) * extrude(text, RAISED)
    return pill, letters


def _outline(grow: float, y0: float, y1: float):
    """The case's front silhouette (W x H, R_FACE corners) grown by `grow`."""
    w, h = m.W + 2 * grow, m.H + 2 * grow
    return Pos(0, (y0 + y1) / 2, m.H / 2) * rounded_box(w, y1 - y0, h, m.R_FACE + grow, 0.0)


def stencil():
    """The gluing stencil where it sits on the case."""
    y_out = m.Y_FRONT - STENCIL_T
    above = bbox_solid(-60, 60, -30, 30, STENCIL_Z0, 200)
    outer = LIP_CLEAR + LIP_T
    plate = _outline(outer, y_out, m.Y_FRONT) & above
    lip = _outline(outer, y_out, m.Y_FRONT + LIP_D) - _outline(
        LIP_CLEAR, y_out - 1, m.Y_FRONT + LIP_D + 1
    )
    s = plate + (lip & above)
    for x, z, _ in dot_centres():
        hole = Pos(x, m.Y_FRONT - STENCIL_T / 2, z) * Rot(90, 0, 0)
        s -= hole * Cylinder(DOT_D / 2 + HOLE_CLEAR, STENCIL_T + 1)
        pocket = Pos(x, m.Y_FRONT - RELIEF_D / 2 + 0.01, z) * Rot(90, 0, 0)
        s -= pocket * Cylinder(DOT_D / 2 + HOLE_CLEAR + RELIEF, RELIEF_D)
    g = HOLE_CLEAR
    s -= _pill(BADGE_W + 2 * g, BADGE_H + 2 * g, y_out - 1, m.Y_FRONT + 1)
    g += RELIEF
    s -= _pill(BADGE_W + 2 * g, BADGE_H + 2 * g, m.Y_FRONT - RELIEF_D, m.Y_FRONT + 1)
    return s
