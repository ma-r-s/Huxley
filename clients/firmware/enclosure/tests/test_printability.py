"""Every HX-1 piece prints in its stated orientation with support only where
the print guide says (the tub's roof, switch cradle and rocker mount)."""

from __future__ import annotations

import pytest
from huxcase import printability as pr


@pytest.fixture(scope="module")
def report():
    pieces = pr.printed_pieces()
    return {
        p.piece: pr.needs_support(pr.overhangs(pr.posed(pieces[p.piece], p))) for p in pr.POSES
    }


def test_every_piece_has_a_pose_and_is_one_solid():
    pieces = pr.printed_pieces()
    assert {p.piece for p in pr.POSES} == set(pieces)
    for name, shape in pieces.items():
        assert len(shape.solids()) == 1, name


@pytest.mark.parametrize("piece", ["ptt_pin", "plunger"])
def test_small_parts_need_no_support(report, piece):
    assert report[piece] == []


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
