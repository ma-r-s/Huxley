"""Print orientation and overhang check for every printed HX-1 piece.

Each piece gets the orientation it is printed in on Mario's P2S. In that
pose, any surface that faces down more steeply than 45 deg from vertical and
is not on the bed needs support. `overhangs()` measures that area and groups
it into clusters, so a test can hold each piece to a budget and a person can
see what still needs support.

    cd clients/firmware/enclosure && uv run python -m huxcase.printability         # report
    cd clients/firmware/enclosure && uv run python -m huxcase.printability --stl   # out/print/*.stl
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from build123d import Pos, Rot

from huxcase import hardware as hw
from huxcase.concepts import hx1
from huxcase.shapes import bbox_solid

MAX_OVERHANG_DEG = 45.0  # from vertical; PLA on a P2S prints this unsupported
BED_TOL = 0.3  # faces this close to the bed are the first layer, not overhangs
BRIDGE_OK = 2.0  # mm: a downward face this narrow is a short bridge, prints fine


@dataclass(frozen=True)
class Pose:
    piece: str  # part name in hx1.build().parts
    how: str  # in words, for the print guide
    rotate: tuple[float, float, float]  # degrees, applied about the origin, then dropped to z = 0


POSES = (
    Pose("faceplate", "face down", (90, 0, 0)),
    Pose("grille_plug", "face down", (90, 0, 0)),
    Pose("tub_base", "standing up, the open front toward you", (0, 0, 0)),
    Pose("ptt_bar", "outer face down", (0, 90, 0)),
    Pose("ptt_pin", "lying flat", (0, 0, 0)),
    Pose("plunger", "standing on its tip", (0, -90, 0)),
    Pose("bridge", "front face down", (90, 0, 0)),
    # Spare plungers, 0.5 and 1.0 mm longer (more preload, an earlier click).
    Pose("plunger_long05", "standing on its tip", (0, -90, 0)),
    Pose("plunger_long10", "standing on its tip", (0, -90, 0)),
    # The tub's frosted top, glued onto the base (hx1.LAP_H): standing on its
    # seam face, the open front toward you.
    Pose("tub_top", "standing on its seam face, the open front toward you", (0, 0, 0)),
    # A corner of the top (roof, back and right walls, the backing), to
    # check the frosted look before the long print.
    Pose("frost_swatch", "standing up", (0, 0, 0)),
)


# The swatch: the top's back right top corner (x0, x1, y0, y1, z0, z1).
FROST_SWATCH = (14.0, 36.0, 8.0, 20.0, 84.0, 105.0)


def printed_pieces(c=None) -> dict[str, object]:
    """Separate prints of hx1 (the tub's two colours are one print)."""
    c = c or hx1.build()
    by_name = {p.name: p.shape for p in c.parts}
    out = {name: by_name[name] for name in ("faceplate", "grille_plug", "ptt_bar", "ptt_pin")}
    out["plunger"] = by_name["plunger"]
    out["bridge"] = by_name["bridge"] + hx1.bridge_skin()  # the screw pokes through it
    out["plunger_long05"] = hx1.plunger_shape(0.5)
    out["plunger_long10"] = hx1.plunger_shape(1.0)
    out["tub_base"] = by_name["tub_grey"]
    out["tub_top"] = by_name["tub_frosted"]
    out["frost_swatch"] = by_name["tub_frosted"] & bbox_solid(*FROST_SWATCH)
    return out


def pose_location(shape, pose: Pose):
    """Device frame -> bed: rotated, then dropped so its lowest point is z = 0."""
    bb = (Rot(*pose.rotate) * shape).bounding_box()
    return Pos(-bb.center().X, -bb.center().Y, -bb.min.Z) * Rot(*pose.rotate)


def posed(shape, pose: Pose):
    """The piece as it sits on the bed."""
    return pose_location(shape, pose) * shape


def overhangs(shape, tolerance: float = 0.1) -> list[dict]:
    """Clusters of support-needing faces: area (mm^2), bbox, lowest z."""
    import numpy as np
    import trimesh

    verts, tris = shape.tessellate(tolerance, 0.3)
    mesh = trimesh.Trimesh(
        vertices=np.array([(v.X, v.Y, v.Z) for v in verts]), faces=np.array(tris), process=True
    )
    nz = mesh.face_normals[:, 2]
    # A face is an overhang when it leans more than MAX_OVERHANG_DEG from vertical;
    # 1 deg of slack so an exact 45 deg chamfer passes.
    limit = -math.cos(math.radians(90.0 - MAX_OVERHANG_DEG - 1.0))
    zmin = mesh.triangles[:, :, 2].min(axis=1)
    bad = (nz < limit) & (zmin > BED_TOL)
    if not bad.any():
        return []
    # Group the bad faces into connected patches (union-find over shared edges).
    idx = np.nonzero(bad)[0]
    parent = {int(i): int(i) for i in idx}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in mesh.face_adjacency:
        if bad[a] and bad[b]:
            ra, rb = find(int(a)), find(int(b))
            if ra != rb:
                parent[ra] = rb
    patches: dict[int, list[int]] = {}
    for i in idx:
        patches.setdefault(find(int(i)), []).append(int(i))
    out = []
    for faces in patches.values():
        pts = mesh.triangles[faces].reshape(-1, 3)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        out.append(
            {
                "area": float(mesh.area_faces[faces].sum()),
                "narrow": float(min(hi[0] - lo[0], hi[1] - lo[1])),
                "z": float(lo[2]),
                "bbox": [round(float(v), 1) for v in (*lo, *hi)],
            }
        )
    return sorted(out, key=lambda d: -d["area"])


def islands(shape, pitch: float = 0.2) -> list[dict]:
    """Places where a layer starts in mid-air: a patch of a layer that touches
    nothing of the layer below. A narrow downward face can pass the overhang
    check (it looks like a short bridge) and still be one of these."""
    import numpy as np
    import trimesh
    from scipy import ndimage

    verts, tris = shape.tessellate(0.05, 0.2)
    mesh = trimesh.Trimesh(
        vertices=np.array([(v.X, v.Y, v.Z) for v in verts]), faces=np.array(tris), process=True
    )
    vox = mesh.voxelized(pitch).fill()
    mat, origin = vox.matrix, vox.translation
    out = []
    for k in range(1, mat.shape[2]):
        lab, n = ndimage.label(mat[:, :, k])
        if not n:
            continue
        below = ndimage.binary_dilation(mat[:, :, k - 1])
        for i in range(1, n + 1):
            comp = lab == i
            if (comp & below).any():
                continue
            xs, ys = np.nonzero(comp)
            out.append(
                {
                    "z": round(float(origin[2] + k * pitch), 2),
                    "area": round(float(comp.sum() * pitch * pitch), 2),
                    "bbox": [
                        round(float(origin[0] + xs.min() * pitch), 1),
                        round(float(origin[1] + ys.min() * pitch), 1),
                        round(float(origin[0] + xs.max() * pitch), 1),
                        round(float(origin[1] + ys.max() * pitch), 1),
                    ],
                }
            )
    return out


def needs_support(clusters: list[dict], min_area: float = 1.0) -> list[dict]:
    """Clusters wider than a short bridge and bigger than a speck."""
    return [k for k in clusters if k["narrow"] > BRIDGE_OK and k["area"] > min_area]


def support_keep_outs() -> dict[str, tuple[float, float, float, float, float, float]]:
    """Close-fitting cuts in the tub where the slicer must put no support
    (device frame: x0, x1, y0, y1, z0, z1), each a little inside its cut.
    Support in them could not be pulled out cleanly, and what it left would
    bind the part that moves there. slice.py checks the G-code against these."""
    m = hx1
    chan_y = (m.CHAN_Y0 + 0.4, m.SWITCH_Y + m.BAR_W / 2 + m.CLEAR - 0.4)
    panel_x = -m.W / 2 + m.ROCKER_RECESS
    cy, cz = hw.ROCKER_BODY[0] / 2, hw.ROCKER_BODY[1] / 2
    return {
        "plunger bore": (  # from past the stem's slot (support there comes out the front)
            m.PLUNGER_TIP_X + 0.8,
            m.X_OUT - 0.3,
            m.SWITCH_Y - m.TUBE_BORE + 0.4,
            m.SWITCH_Y + m.TUBE_BORE - 0.4,
            m.TS_Z - m.TUBE_BORE + 0.4,
            m.TS_Z + m.TUBE_BORE * 1.41 - 0.4,
        ),
        "lever channel": (
            m.PIVOT_X - m.KNUCKLE_R - m.CLEAR + 0.4,
            m.X_OUT - 0.3,
            *chan_y,
            m.PIVOT_Z - 2.0,
            m.BAR_Z1 - 0.5,
        ),
        "rear pin seat": (
            m.PIVOT_X - 1.0,
            m.PIVOT_X + 1.0,
            chan_y[1] + 0.8,
            m.PIN_Y1,
            m.PIVOT_Z - 1.0,
            m.PIVOT_Z + 1.6,
        ),
        "rocker hole": (
            panel_x + 0.2,
            panel_x + m.ROCKER_PANEL - 0.2,
            m.ROCKER_Y - cy + 0.3,
            m.ROCKER_Y + cy - 0.3,
            m.ROCKER_Z - cz + 0.3,
            m.ROCKER_Z + cz - 0.3,
        ),
        "USB plug": (  # where the plug's shell goes; the pocket round it is roomy
            -m.W / 2 + 0.3,
            m.USB_FACE,
            m.USB_Y - 1.6,
            m.USB_Y + 1.6,
            m.BOARD_Z - 4.5,
            m.BOARD_Z + 4.5,
        ),
    }


COLOUR = {
    "faceplate": "grey",
    "grille_plug": "black",
    "tub_base": "grey",
    "tub_top": "frosted (clarity settings)",
    "frost_swatch": "frosted (clarity settings)",
    "ptt_bar": "orange",
    "ptt_pin": "grey",
    "plunger": "grey",
    "bridge": "grey",
    "plunger_long05": "grey",
    "plunger_long10": "grey",
}


def export_stls(out_dir) -> list[str]:
    """One STL per printed piece, already in its print pose (lands on the bed
    the right way up), named in print order."""
    from pathlib import Path

    from build123d import export_stl

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("hx1_*.stl"):  # a piece that no longer exists must not linger
        stale.unlink()
    pieces = printed_pieces()
    written = []
    for i, pose in enumerate(POSES, 1):
        path = out / f"hx1_{i:02d}_{pose.piece}.stl"
        export_stl(
            posed(pieces[pose.piece], pose), str(path), tolerance=0.02, angular_tolerance=0.15
        )
        written.append(f"{path.name}: {COLOUR[pose.piece]}, {pose.how}")
    (out / "PRINT.txt").write_text(
        "HX-1 rev E print set (PLA, 0.4 nozzle, 0.20 mm layers). The tub is two pieces, "
        "glued with a lap joint: a grey base and a frosted top, on one plate printed by "
        "object (projects/4_tub_base_and_top.3mf). Both get tree supports, Everywhere, "
        "bridges included; everything else needs none. The frosted top and its swatch "
        "print with clarity settings (dry the filament first; print the swatch first). "
        "Projects to open and slice: projects/; the same sliced: sliced/.\n"
        + "\n".join(written)
        + "\n"
    )
    return written


def main() -> int:
    import sys

    if "--stl" in sys.argv:
        from huxcase.export import OUT

        for line in export_stls(OUT / "print"):
            print(line)
        return 0
    pieces = printed_pieces()
    for pose in POSES:
        clusters = needs_support(overhangs(posed(pieces[pose.piece], pose)))
        total = sum(k["area"] for k in clusters)
        print(f"{pose.piece:12s} {pose.how}: {len(clusters)} support areas, {total:.0f} mm^2")
        for k in clusters[:12]:
            print(f"    {k['area']:7.1f} mm^2  z {k['z']:5.1f}  bbox {k['bbox']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
