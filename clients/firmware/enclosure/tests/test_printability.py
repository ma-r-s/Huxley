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


@pytest.mark.parametrize("pose", [p for p in pr.POSES if p.piece != "tub"], ids=lambda p: p.piece)
def test_nothing_starts_in_mid_air(pieces, pose):
    """The pieces printed without support have no layer that starts on
    nothing (the overhang check reads a narrow one as a short bridge). The
    tub prints with supports, which catch its few."""
    assert pr.islands(pr.posed(pieces[pose.piece], pose)) == []


@pytest.mark.parametrize(
    ("piece", "budget"),
    [
        ("ptt_bar", 60),  # the pin bore's roof and the dimple's floor: tiny bridges
        ("bridge", 130),  # the back bar bridges 17 mm between its webs
        ("grille_plug", 200),  # its flange: 1.5 mm out
        ("faceplate", 450),  # the face's edge round-over and the counterbore steps
        ("tub", 3300),  # roof, switch cradle and tube, rocker mount: tree supports
    ],
)
def test_overhangs_stay_within_budget(report, piece, budget):
    """A budget, not zero: these areas are known and listed in the print guide.
    A new feature that needs support pushes a piece over and fails here."""
    assert sum(k["area"] for k in report[piece]) <= budget


def test_tub_ledges_print_without_support(report):
    """Insert blocks, the bridge boss and the speaker post have 45 deg undersides.
    Horizontal holes up to 6 mm (the lever pivot) print fine as they are."""
    for k in report["tub"]:
        x0, y0, z0, x1, y1, z1 = k["bbox"]
        if 20.0 < z0 < 80.0 and k["narrow"] > 6.0:  # between the floor and the cradle zone
            assert k["area"] < 10.0, k


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
