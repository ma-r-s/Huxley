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
from functools import cache

from build123d import Compound, Pos, Rot

from huxcase import bumper, face_art, fits
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
    # Spare plungers, 0.25 mm shorter and longer (hx1.PLUNGER_VARIANTS): less
    # rest force if the switch stays on, more if the click comes late.
    Pose("plunger_short", "standing on its tip", (0, -90, 0)),
    Pose("plunger_long", "standing on its tip", (0, -90, 0)),
    # The tub's top, glued onto the base (hx1.LAP_H): on its back, the open
    # front up (hx1.TOP_PRINT_UP, the device's -y up).
    Pose("tub_top", "on its back, the open front up", (-90, 0, 0)),
    # The test corner: the back right corner of both halves round the seam,
    # the lever's backing included (where the lip once bound). Push them
    # together dry and hold the frosted one to a light before the long print.
    Pose("test_base", "standing on its floor cut", (0, 0, 0)),
    Pose("test_top", "on its back, the open front up", (-90, 0, 0)),
    # The TPU bumper (huxcase.bumper) on its front, the front lip on the bed:
    # every wall vertical, two short bridges (over USB-C and the rocker). Its
    # front 8 mm first, as a fit test.
    Pose("bumper", "on its front, the front lip on the bed", (90, 0, 0)),
    Pose("bumper_ring", "on its front, the front lip on the bed", (90, 0, 0)),
    # The face art (huxcase.face_art), glued on: the badge on its back, its
    # raised letters on top (the filament changes once, at their height), the
    # dots dome up, the stencil with its face side up and the lip standing on it.
    Pose("face_badge", "back down, the letters up", (-90, 0, 0)),
    Pose("face_dot", "dome up", (-90, 0, 0)),
    Pose("face_stencil", "front down, the lip up", (90, 0, 0)),
    # The shortest plunger, 0.5 mm under the normal one, for a spring that came
    # long (added last so the other STLs keep their numbers).
    Pose("plunger_shortest", "standing on its tip", (0, -90, 0)),
)


# The test corner (x0, x1, y0, y1), and each half's slice of it in z.
TEST_CORNER = (10.0, 36.0, -10.0, 20.0)
TEST_BASE_Z, TEST_TOP_Z = (28.0, 40.0), (36.0, 52.0)


def printed_pieces(c=None) -> dict[str, object]:
    """Separate prints of hx1 (the tub's two colours are one print)."""
    c = c or hx1.build()
    by_name = {p.name: p.shape for p in c.parts}
    out = {name: by_name[name] for name in ("faceplate", "grille_plug", "ptt_bar", "ptt_pin")}
    out["plunger"] = by_name["plunger"]
    out["bridge"] = by_name["bridge"] + hx1.bridge_skin()  # the screw pokes through it
    out["plunger_short"] = hx1.plunger_shape(-0.25)
    out["plunger_long"] = hx1.plunger_shape(0.25)
    out["plunger_shortest"] = hx1.plunger_shape(-0.5)
    out["tub_base"] = by_name["tub_grey"]
    out["tub_top"] = by_name["tub_frosted"]
    out["test_base"] = by_name["tub_grey"] & bbox_solid(*TEST_CORNER, *TEST_BASE_Z)
    out["test_top"] = by_name["tub_frosted"] & bbox_solid(*TEST_CORNER, *TEST_TOP_Z)
    out["bumper"] = bumper.bumper()
    out["bumper_ring"] = bumper.front_ring()
    body, letters = face_art.badge()
    out["face_badge"] = body + letters
    out["face_dot"] = face_art.dots_on_face()[0]
    out["face_stencil"] = face_art.stencil()
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
                "centres": mesh.triangles_center[faces].tolist(),
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
    # trimesh halves edges 10 times by default: not enough for the bumper's 116 mm ones
    vox = mesh.voxelized(pitch, max_iter=14).fill()
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


def support_faces_device(shape, pose: Pose, tolerance: float = 0.1) -> list[tuple]:
    """Centres (device frame) of the faces that need support (needs_support)
    with `shape` printed in `pose`: what support_keep_outs must stay clear of."""
    loc = pose_location(shape, pose)
    inv = loc.inverse()
    out = []
    for k in needs_support(overhangs(loc * shape, tolerance)):
        for c in k["centres"]:
            v = (inv * Pos(*c)).position
            out.append((v.X, v.Y, v.Z))
    return out


def needs_support(clusters: list[dict], min_area: float = 1.0) -> list[dict]:
    """Clusters wider than a short bridge and bigger than a speck."""
    return [k for k in clusters if k["narrow"] > BRIDGE_OK and k["area"] > min_area]


@cache  # the bore's mouth is a boolean; in_keep_out runs per support sample
def support_keep_outs() -> dict[str, tuple[float, float, float, float, float, float]]:
    """Close fits in the tub's top where no support may go, printed on its
    back (hx1.TOP_PRINT_UP), as boxes in the device frame (x0, x1, y0, y1,
    z0, z1), each a little inside its cut. Support or what it leaves there
    would bind a moving part or stop one going in. slice.py counts any
    support in them in the G-code; the faces that need support stay out
    (tests). One roof prints on support from outside the case, clear of these:
    the wall in front of the lever's slot (relieved, hx1.SLOT_RELIEF), its
    trees crossing the open slot. The rocker hole's front edge prints as a
    bare 12 mm bridge (0.3 mm round the body for its sag); the clips catch on
    its long sides."""
    m = hx1
    chan_x0 = m.PIVOT_X - m.KNUCKLE_R - m.CLEAR  # the slot's inner end, at the pivot
    # where the plunger's bore opens into the slot: the slot's inner edge there
    mouth = (
        (m._pressed_union() & bbox_solid(-60, 60, -60, 60, m.TS_Z - 0.5, m.TS_Z + 0.5))
        .bounding_box()
        .min.X
    )
    chan_y0, chan_y1 = m.CHAN_Y0, m.SWITCH_Y + m.BAR_W / 2 + m.CLEAR
    stop_x = m.X_OUT - m.STOP * (m.TS_Z + 2.0 - m.PIVOT_Z) / (m.TS_Z - m.PIVOT_Z) - 0.05
    panel_x = -m.W / 2 + m.ROCKER_RECESS
    cz = hw.ROCKER_BODY[1] / 2
    flare = cz + m.ROCKER_HOLE_CLEAR_Z + (m.ROCKER_PANEL - m.ROCKER_CATCH[0])
    pocket = hw.TACT_W / 2 + fits.PART
    return {
        "plunger bore": (  # from past the stem's slot to where it opens into the lever's slot
            m.PLUNGER_TIP_X + 0.8,
            mouth - 0.3,
            m.SWITCH_Y - m.TUBE_BORE * 1.41 + 0.4,
            m.SWITCH_Y + m.TUBE_BORE - 0.4,
            m.TS_Z - m.TUBE_BORE + 0.4,
            m.TS_Z + m.TUBE_BORE - 0.4,
        ),
        "tooth's notch": (  # where the faceplate's tooth goes, in front of the pivot
            m.TOOTH_X0 + 0.3,
            m.X_OUT - 0.3,
            m.FACE_Y + 0.3,
            chan_y0 - 0.3,
            m.TOOTH_Z[0] + 0.3,
            m.TOOTH_Z[1] - 0.3,
        ),
        "lever slot's back face": (  # the blade runs a fit's width from it; the knuckle bears on it
            chan_x0 + 0.4,
            m.X_OUT - 0.3,
            chan_y1 - 1.2,
            chan_y1 - 0.2,
            m.PIVOT_Z - m.KNUCKLE_R - m.CLEAR + 0.3,
            m.BAR_Z1 - 0.5,
        ),
        "lever's stop": (  # the face the lever lands on after the click
            stop_x - 0.1,
            stop_x + 0.6,
            m.SWITCH_Y + 5.9,
            chan_y1 - 0.4,
            m.TS_Z - 1.7,
            m.TS_Z + 1.7,
        ),
        "rear pin seat": (
            m.PIVOT_X - 1.0,
            m.PIVOT_X + 1.0,
            chan_y1 + 0.4,
            m.PIN_Y1,
            m.PIVOT_Z - 1.0,
            m.PIVOT_Z + 1.0,
        ),
        "rocker clips": (  # mid-length on the long sides, out to the chamfer the clips catch on
            panel_x + 0.2,
            panel_x + m.ROCKER_PANEL - 0.05,
            m.ROCKER_Y - 5.0,
            m.ROCKER_Y + 5.0,
            m.ROCKER_Z - flare + 0.05,
            m.ROCKER_Z + flare - 0.05,
        ),
        "switch cradle": (  # the pocket the talk switch slides into
            m.TS_BASE_X + 0.3,
            m.TS_BASE_X + hw.TACT_BODY - 0.3,
            m.SWITCH_Y - pocket + 0.3,
            m.SWITCH_Y + pocket - 0.3,
            m.TS_Z - pocket + 0.3,
            m.TS_Z + pocket - 0.3,
        ),
        "glue pocket, back": (  # where the base's lip goes
            -m.W / 2 + m.T + 0.3,
            m.W / 2 - m.T - 0.3,
            m.Y_BACK - m.T - m.LAP_GAP + 0.05,
            m.Y_BACK - m.T / 2 - 0.05,
            m.BACK_FROST_Z + 0.1,
            m.BACK_FROST_Z + m.LAP_H,
        ),
        "glue pocket, left": (
            -m.W / 2 + m.T / 2 + 0.05,
            -m.W / 2 + m.T + m.LAP_GAP - 0.05,
            m.FACE_Y + m.LIP_D + 0.3,
            m.Y_BACK - m.T - 0.3,
            m.BACK_FROST_Z + 0.1,
            m.BACK_FROST_Z + m.LAP_H,
        ),
        "glue pocket, right": (
            m.W / 2 - m.T - m.LAP_GAP + 0.05,
            m.W / 2 - m.T / 2 - 0.05,
            m.FACE_Y + m.LIP_D + 0.3,
            m.Y_BACK - m.T - 0.3,
            m.BACK_FROST_Z + 0.1,
            m.BACK_FROST_Z + m.LAP_H,
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


@cache
def support_keep_out_rounds() -> dict[str, tuple[float, float, float, float, float]]:
    """Round close fits in the top where no support may go (see
    support_keep_outs): the lever's pivot, the knuckle's clearance round the
    pin axis (along y): (x, z, radius, y0, y1), device frame."""
    m = hx1
    rk = m.KNUCKLE_R + m.CLEAR
    return {
        "lever pivot": (
            m.PIVOT_X,
            m.PIVOT_Z,
            rk - 0.2,
            m.CHAN_Y0 + 0.4,
            m.SWITCH_Y + m.BAR_W / 2 + m.CLEAR - 0.4,
        )
    }


def in_keep_out(p) -> list[str]:
    """The keep-outs (boxes and rounds) a device-frame point lies in."""
    hit = [
        k
        for k, (x0, x1, y0, y1, z0, z1) in support_keep_outs().items()
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and z0 <= p[2] <= z1
    ]
    for k, (x, z, r, y0, y1) in support_keep_out_rounds().items():
        if y0 <= p[1] <= y1 and (p[0] - x) ** 2 + (p[2] - z) ** 2 <= r * r:
            hit.append(k)
    return hit


COLOUR = {
    "faceplate": "white",
    "grille_plug": "black",
    "tub_base": "red",
    "tub_top": "clear PETG (clarity settings)",
    "test_base": "red",
    "test_top": "clear PETG (clarity settings)",
    "ptt_bar": "orange",
    "ptt_pin": "white",
    "plunger": "orange",
    "bridge": "white",
    "plunger_short": "orange",
    "plunger_long": "orange",
    "plunger_shortest": "orange",
    "bumper": "TPU, from the external spool",
    "bumper_ring": "TPU, from the external spool",
    "face_badge": "black, its raised letters orange (one object, two filaments)",
    "face_dot": "the dot colours: h and l in the first, o and a in the second",
    "face_stencil": "any colour",
}
# More STLs from a piece, written next to its own as <stl>_<name>.stl in its
# pose: the parts of a piece printed in two filaments (the badge), and the
# dots in two groups, one object each (piece -> {name: shape}).
EXTRA = {
    "face_badge": lambda: dict(zip(("body", "letters"), face_art.badge(), strict=True)),
    "face_dot": lambda: {
        "h_l": Compound(face_art.dot_group(8)),
        "o_a": Compound(face_art.dot_group(6)),
    },
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
        if pose.piece in EXTRA:
            loc = pose_location(pieces[pose.piece], pose)
            for part, shape in EXTRA[pose.piece]().items():
                export_stl(
                    loc * shape,
                    str(path.with_name(f"{path.stem}_{part}.stl")),
                    tolerance=0.01,
                    angular_tolerance=0.1,
                )
    (out / "PRINT.txt").write_text(
        "HX-1 rev E print set (PLA, the top in clear PETG; 0.4 nozzle, 0.20 mm layers). "
        "The final colours: a white faceplate, a red base, the clear top, the black grille, "
        "the orange lever. The tub is two pieces, "
        "glued with a lap joint: the red base and the clear top, on one plate printed by "
        "object (projects/4_tub_base_and_top.3mf), or each alone (11 and 8). Both get tree "
        "supports, Everywhere, bridges included; everything else needs none. The clear top prints with "
        "clarity settings (dry the filament first). Print the test corner first "
        "(projects/5_test_corner.3mf): the two halves' corner at the seam, to push "
        "together dry and to hold to a light. "
        "The TPU bumper (projects/9_bumper_tpu.3mf) prints from the external spool, on "
        "its front, no supports; its front 8 mm (projects/10_bumper_test_ring.3mf) "
        "checks the fit first. "
        "The face art (huxcase.face_art) is glued on with CA, all on one plate "
        "printed by object (projects/12_face_art.3mf): the HX-1 badge (black, its "
        "raised letters orange), ten braille dots in two colours (two spares of "
        "each) and the stencil that places them. "
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
