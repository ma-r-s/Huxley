"""Every HX-1 piece prints in its stated orientation with support only where
the print guide says (the tub's roof, switch cradle and rocker mount), and
nothing starts in mid-air."""

from __future__ import annotations

import pytest
from huxcase import printability as pr


@pytest.fixture(scope="module")
def pieces():
    return pr.printed_pieces()


@pytest.fixture(scope="module")
def report(pieces):
    return {
        p.piece: pr.needs_support(pr.overhangs(pr.posed(pieces[p.piece], p))) for p in pr.POSES
    }


def test_every_piece_has_a_pose_and_is_one_solid(pieces):
    assert {p.piece for p in pr.POSES} == set(pieces)
    for name, shape in pieces.items():
        assert len(shape.solids()) == 1, name


@pytest.mark.parametrize("piece", ["ptt_pin", "plunger", "plunger_long05", "plunger_long10"])
def test_small_parts_need_no_support(report, piece):
    assert report[piece] == []


def test_island_check_finds_a_part_that_starts_in_mid_air():
    from build123d import Box, Pos

    base = Pos(0, 0, 1) * Box(10, 10, 2) + Pos(4, 0, 5) * Box(2, 10, 10)
    assert pr.islands(base + Pos(0, 0, 8) * Box(1, 6, 2)) != []  # a prong off nothing
    assert pr.islands(base + Pos(2.0, 0, 9) * Box(4, 4, 1)) == []  # a ledge off the wall


SUPPORTED = ("tub_base", "tub_top", "test_base", "test_top")


@pytest.mark.parametrize(
    "pose", [p for p in pr.POSES if p.piece not in SUPPORTED], ids=lambda p: p.piece
)
def test_nothing_starts_in_mid_air(pieces, pose):
    """The pieces printed without support have no layer that starts on
    nothing (the overhang check reads a narrow one as a short bridge). The
    tub's base and top print with supports, which catch their few."""
    assert pr.islands(pr.posed(pieces[pose.piece], pose)) == []


@pytest.mark.parametrize(
    ("piece", "budget"),
    [
        ("ptt_bar", 60),  # the pin bore's roof and the dimple's floor: tiny bridges
        ("bridge", 130),  # the back bar bridges 17 mm between its webs
        ("grille_plug", 200),  # its flange: 1.5 mm out
        ("faceplate", 450),  # the face's edge round-over and the counterbore steps
        ("tub_base", 450),  # the joint shelves, the pedestal ribs: tree supports
        (
            "tub_top",
            450,
        ),  # on its back: the wall over the lever's slot, the rocker's recess, the back's edge
    ],
)
def test_overhangs_stay_within_budget(report, piece, budget):
    """A budget, not zero: these areas are known and listed in the print guide.
    A new feature that needs support pushes a piece over and fails here."""
    assert sum(k["area"] for k in report[piece]) <= budget


def test_tub_ledges_print_without_support(report):
    """In the base (standing on its floor): the bridge boss and the speaker
    post have 45 deg undersides. The top prints on its back (see below)."""
    for piece, seam in (("tub_base", 0.0),):
        for k in report[piece]:
            x0, y0, z0, x1, y1, z1 = k["bbox"]
            z = z0 + seam  # posed -> device
            if 20.0 < z < 80.0 and k["narrow"] > 6.0:  # between the floor and the cradle zone
                assert k["area"] < 10.0, (piece, k)


def test_tub_top_on_its_back_leaves_the_close_fits_clean(pieces):
    """Printed on its back, nothing that needs support lies inside the top's
    close fits (support_keep_outs): the lever's slot, the plunger's tube, the
    pin seat, the rocker's hole, the USB plug's path. Standing on its seam,
    trees grew through the slot and the tube (Mario, 2026-10-11)."""
    pose = next(p for p in pr.POSES if p.piece == "tub_top")
    hits = [(p, pr.in_keep_out(p)) for p in pr.support_faces_device(pieces["tub_top"], pose)]
    assert [h for h in hits if h[1]] == []


def test_switch_latch_bends_well_inside_its_layers():
    """On its back the latch finger stands up in the print, so it bends across
    its layers: its strain at full catch (1.5 t catch / L^2, L from the root
    to the lip's catching face) stays under 0.4 %, against ~1 % that PLA's
    layer bond takes. And the printed finger is that thin: the cradle's top
    wall is cut down over it (the review caught it at 1.2, 2026-10-11)."""
    from build123d import Vector
    from huxcase.concepts import hx1

    assert hx1.latch_strain() < 0.004
    c = hx1.build()
    printed = c.shells["tub"] + c.shells["tub_latch"]
    finger = max(c.shells["tub_latch"].solids(), key=lambda s: s.bounding_box().size.Y)
    b = finger.bounding_box()
    x, y = (b.min.X + b.max.X) / 2, (b.min.Y + b.max.Y) / 2  # mid-length, clear of the lip
    # its thickness runs along z (the cradle's frame turned by TS_LOC): scan it
    zs = [b.min.Z - 1.0 + k * 0.02 for k in range(int((b.size.Z + 2.0) / 0.02))]
    solid = [z for z in zs if printed.is_inside(Vector(x, y, z))]
    assert max(solid) - min(solid) <= hx1.LATCH_T + 0.05, (min(solid), max(solid))


def test_tub_base_is_the_one_mario_printed(pieces):
    """Mario printed the base (white PLA, 2026-10-11); the top's redesign for
    printing on its back must not touch it. Its exact volume, pinned: twice
    a cut near the seam reached into the base's lip unnoticed."""
    assert pieces["tub_base"].volume == pytest.approx(20188.8795, abs=0.01)


def test_tub_halves_lap_and_rebuild_the_tub(pieces):
    """The glued halves are the one-piece tub the fit checks use, less only
    the glue gap; the base's lip stands inside the top's skirt, so the inside
    walls line up."""
    from huxcase.concepts import hx1

    c = hx1.build()
    whole = {p.name: p.shape for p in c.parts}
    base, top = pieces["tub_base"], pieces["tub_top"]
    assert (base & top).volume < 1e-6
    union = base + top
    gap = (whole["tub_grey"] + whole["tub_frosted"]).volume - union.volume
    assert gap < 1e-6  # the parts are these pieces
    sh = c.shells
    one_piece = (sh["tub"] + sh["tub_latch"] + sh["tub_ribs"]).volume
    assert 0 < one_piece - union.volume < 120.0  # just the lap's glue gap
    lip = base & hx1._lap(hx1.T, hx1.LAP_H + 1)
    # LAP_H straight, then the 45 deg top rises the lip's own depth
    lip_top = hx1.BACK_FROST_Z + hx1.LAP_H + hx1.T / 2 - hx1.LAP_GAP / 2
    assert pytest.approx(lip_top, abs=0.01) == lip.bounding_box().max.Z


def test_tub_top_clears_the_lip(pieces):
    """Above the shoulder the top stays LAP_GAP off the base's lip everywhere,
    what it carries inside included: the lever's backing once sat flush on
    the lip's inner face along 24 mm (review, 2026-10-11), and would bind."""
    from huxcase.concepts import hx1
    from huxcase.shapes import bbox_solid

    lip = pieces["tub_base"] & bbox_solid(-60, 60, -60, 60, hx1.BACK_FROST_Z + 0.01, 60)
    assert pieces["tub_top"].distance_to(lip) >= hx1.LAP_GAP - 0.01


def test_print_files_are_watertight(tmp_path):
    """The STLs Mario loads into Bambu Studio are closed meshes, flat on the bed."""
    import trimesh

    pr.export_stls(tmp_path)
    files = sorted(tmp_path.glob("*.stl"))
    assert len(files) == len(pr.POSES)
    for f in files:
        mesh = trimesh.load(f)
        assert mesh.is_watertight, f.name
        assert abs(mesh.bounds[0][2]) < 1e-3, f.name
