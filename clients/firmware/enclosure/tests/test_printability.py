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


SUPPORTED = ("tub_base", "tub_top", "test_base", "test_top", "tub")


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
        ("tub_top", 3300),  # roof, switch cradle and tube, rocker mount: tree supports
        ("tub", 3750),  # the two halves' together, in one piece
    ],
)
def test_overhangs_stay_within_budget(report, piece, budget):
    """A budget, not zero: these areas are known and listed in the print guide.
    A new feature that needs support pushes a piece over and fails here."""
    assert sum(k["area"] for k in report[piece]) <= budget


def test_tub_ledges_print_without_support(report):
    """Insert blocks, the bridge boss and the speaker post have 45 deg undersides.
    Horizontal holes up to 6 mm (the lever pivot) print fine as they are."""
    from huxcase.concepts import hx1

    for piece, seam in (("tub_base", 0.0), ("tub_top", hx1.BACK_FROST_Z), ("tub", 0.0)):
        for k in report[piece]:
            x0, y0, z0, x1, y1, z1 = k["bbox"]
            z = z0 + seam  # posed -> device
            if 20.0 < z < 80.0 and k["narrow"] > 6.0:  # between the floor and the cradle zone
                assert k["area"] < 10.0, (piece, k)


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
