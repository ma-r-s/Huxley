"""Small geometry helpers shared by the concepts."""

from __future__ import annotations

import math

from build123d import Axis, Box, Compound, Cylinder, Pos, Solid, fillet


def rounded_box(w: float, d: float, h: float, r_face: float, r_edge: float) -> Solid:
    """Box centred at the origin. `r_face` rounds the four Y-direction edges
    (the silhouette seen from the front), `r_edge` everything else."""
    b = Box(w, d, h)
    if r_face > 0:
        b = fillet(b.edges().filter_by(Axis.Y), r_face)
    if r_edge > 0:
        b = fillet(b.edges().filter_by(Axis.Y, reverse=True), r_edge)
    return b


def hex_holes(
    field_r: float, pitch: float, hole_d: float, depth: float
) -> list[tuple[float, float]]:
    """Centres of a hex grid of holes inside a circle of radius field_r."""
    pts = []
    row_h = pitch * math.sqrt(3) / 2
    rows = int(field_r / row_h) + 1
    for j in range(-rows, rows + 1):
        y = j * row_h
        off = pitch / 2 if j % 2 else 0.0
        cols = int(field_r / pitch) + 2
        for i in range(-cols, cols + 1):
            x = i * pitch + off
            if math.hypot(x, y) <= field_r - hole_d / 2:
                pts.append((x, y))
    return pts


def hole_field(pts: list[tuple[float, float]], hole_d: float, depth: float) -> Compound:
    """Cylinders along Z, centred on z = 0, one per point."""
    return Compound([Pos(x, y, 0) * Cylinder(hole_d / 2, depth) for x, y in pts])


def bbox_solid(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Solid:
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


FONT = "Menlo"


def label(text: str, size: float, depth: float = 0.6):
    """Flat text solid in the XY plane, extruded +Z by `depth`, centred."""
    from build123d import Text, extrude

    return extrude(Text(text, font_size=size, font=FONT), depth)


def on_front(shape, x: float, y_face: float, z: float, sink: float = 0.4):
    """Place a +Z-extruded label on a face whose outward normal is -Y."""
    from build123d import Location

    return Location((x, y_face + sink, z), (90, 0, 0)) * shape


def on_left(shape, x_face: float, y: float, z: float, sink: float = 0.4):
    """Place a label on a face whose outward normal is -X (reads front-ward)."""
    from build123d import Pos, Rot

    return Pos(x_face + sink, y, z) * Rot(0, 0, -90) * Rot(90, 0, 0) * shape


def on_back(shape, x: float, y_face: float, z: float, sink: float = 0.4):
    """Place a label on a face whose outward normal is +Y."""
    from build123d import Pos, Rot

    return Pos(x, y_face - sink, z) * Rot(0, 0, 180) * Rot(90, 0, 0) * shape
