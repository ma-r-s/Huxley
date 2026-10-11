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

from huxcase.concepts import hx1

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
    Pose("tub", "standing up, the open front toward you", (0, 0, 0)),
    Pose("ptt_bar", "outer face down", (0, 90, 0)),
    Pose("ptt_pin", "lying flat", (0, 0, 0)),
    Pose("plunger", "standing on its flat end", (0, -90, 0)),
    Pose("bridge", "front face down", (90, 0, 0)),
)


def printed_pieces(c=None) -> dict[str, object]:
    """Separate prints of hx1 (the tub's two colours are one print)."""
    c = c or hx1.build()
    by_name = {p.name: p.shape for p in c.parts}
    out = {name: by_name[name] for name in ("faceplate", "grille_plug", "ptt_bar", "ptt_pin")}
    out["plunger"] = by_name["plunger"]
    out["bridge"] = by_name["bridge"]
    out["tub"] = by_name["tub_grey"] + by_name["tub_frosted"]
    return out


def posed(shape, pose: Pose):
    """The piece as it sits on the bed: rotated, then dropped so its lowest point is z = 0."""
    s = Rot(*pose.rotate) * shape
    bb = s.bounding_box()
    return Pos(-bb.center().X, -bb.center().Y, -bb.min.Z) * s


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


def needs_support(clusters: list[dict], min_area: float = 1.0) -> list[dict]:
    """Clusters wider than a short bridge and bigger than a speck."""
    return [k for k in clusters if k["narrow"] > BRIDGE_OK and k["area"] > min_area]


COLOUR = {
    "faceplate": "grey",
    "grille_plug": "black",
    "tub": "grey, then frosted from 36 mm (pause and swap at that layer)",
    "ptt_bar": "orange",
    "ptt_pin": "grey",
    "plunger": "grey",
    "bridge": "grey",
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
        "HX-1 rev D print set (PLA, 0.4 nozzle, 0.20 mm layers)\n" + "\n".join(written) + "\n"
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
