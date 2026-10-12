"""HX-1's face art (huxcase.face_art): the dots say "hola" in braille, sit
on the face's own lines clear of every port, and the stencil puts them and
the badge there."""

from __future__ import annotations

import math

import pytest
from build123d import Cylinder, Pos, Rot
from huxcase import bumper
from huxcase import face_art as fa
from huxcase import hardware as hw
from huxcase import printability as pr
from huxcase.concepts import hx1 as m

HALO = 1.6  # clear face round every port, ring and screw head


@pytest.fixture(scope="module")
def parts():
    return {p.name: p.shape for p in m.build().parts if p.name != "ribbon_open"}


def test_the_dots_spell_hola():
    """Read the dots back the way braille is read: cells left to right, dots
    1-2-3 down the left column and 4-5-6 down the right."""
    pts = fa.dot_centres()
    xs = sorted({round(x, 3) for x, _, _ in pts})
    left = round(fa.X_FIRST, 3)
    rows = [fa.Z_MID + fa.DOT_PITCH, fa.Z_MID, fa.Z_MID - fa.DOT_PITCH]
    # Braille's own codes, written out here so a wrong code in face_art fails
    reverse = {"125": "h", "135": "o", "123": "l", "1": "a"}
    word = ""
    for i in range(len(fa.WORD)):
        x0 = left + i * fa.CELL_PITCH
        dots = ""
        for n in range(6):
            col, row = divmod(n, 3)
            x, z = x0 + col * fa.DOT_PITCH, rows[row]
            if any(math.hypot(x - px, z - pz) < 0.01 for px, pz, _ in pts):
                dots += str(n + 1)
        word += reverse[dots]
    assert word == "hola"
    assert len(xs) == 6  # h and o use both columns, l and a only their left


def test_the_word_and_badge_sit_on_the_faces_lines():
    pts = fa.dot_centres()
    r = fa.DOT_D / 2
    edge = -m.PANEL_W / 2 - fa.OVERSHOOT
    assert min(x for x, _, _ in pts) - r == pytest.approx(edge)  # the grille's left edge
    assert max(x for x, _, _ in pts) == pytest.approx(fa.MIC_X)  # the "a" on the mics' axis
    mids = [z for _, z, _ in pts]
    assert (max(mids) + min(mids)) / 2 == pytest.approx(
        (fa.ROW_LO + fa.ROW_HI) / 2, abs=fa.DOT_PITCH / 2 + 0.01
    )
    body, letters = fa.badge()
    bb = (body + letters).bounding_box()
    assert pytest.approx(edge, abs=0.01) == bb.min.X
    assert pytest.approx(fa.ROW_LO, abs=0.01) == bb.center().Z  # on the lower mic's row
    assert pytest.approx(2 * fa.MIC_RING_R, abs=0.01) == bb.size.Z  # as tall as a ring


def _features():
    """(x, z, radius) of every round thing on the face that the art must clear."""
    out = [(*m.to_device(*p), fa.MIC_RING_R) for p in hw.MICS]
    out.append((*m.to_device(*hw.CHG_LED), 0.8))
    out += [(x, z, m.CBORE_D / 2) for x, z in m.SCREWS]
    return out


def test_dots_and_badge_keep_clear_of_ports_rings_and_screws():
    r = fa.DOT_D / 2
    for x, z, _ in fa.dot_centres():
        for fx, fz, fr in _features():
            assert math.hypot(x - fx, z - fz) - r - fr >= HALO, (x, z, fx, fz)
        assert z - r >= m.SPK_Z + 0.5 + m.PANEL_H / 2 + HALO  # over the grille
    body, letters = fa.badge()
    pill = body + letters
    for fx, fz, fr in _features():
        disc = Pos(fx, m.Y_FRONT - 0.5, fz) * Rot(90, 0, 0) * Cylinder(fr, 1.0)
        assert pill.distance_to(disc) >= HALO - 1e-6, (fx, fz)


def test_nothing_on_the_face_runs_into_the_case_or_the_bumper(parts):
    on_face = [*fa.dots_on_face(), *fa.badge()]
    others = {**parts, "bumper": bumper.bumper()}
    for name, shape in others.items():
        for i, art in enumerate(on_face):
            assert (art & shape).volume < 1e-6, (name, i)
    face = parts["faceplate"]
    for art in on_face[:-1]:  # the letters sit in the badge's front, not on the face
        assert art.distance_to(face) < 1e-6  # glued: down on the face


def test_the_stencil_seats_on_the_case_and_every_piece_drops_through(parts):
    st = fa.stencil()
    assert len(st.solids()) == 1
    for name, shape in parts.items():
        assert (st & shape).volume < 1e-6, name
    assert st.distance_to(parts["faceplate"]) < 1e-6  # lies on the face
    lip_gap = min(st.distance_to(parts[n]) for n in ("tub_grey", "tub_frosted"))
    assert lip_gap == pytest.approx(fa.LIP_CLEAR, abs=0.02)
    for i, d in enumerate(fa.dots_on_face()):
        assert d.distance_to(st) >= fa.HOLE_CLEAR - 0.02, i
        # and the hole grips the dot: moved a little past the clearance, it hits
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shift = fa.HOLE_CLEAR + 0.05
            assert (Pos(dx * shift, 0, dz * shift) * d & st).volume > 1e-4, (i, dx, dz)
    body, letters = fa.badge()
    assert (body + letters).distance_to(st) >= fa.HOLE_CLEAR - 0.02


@pytest.mark.parametrize("piece", ["face_badge", "face_dot", "face_stencil"])
def test_face_pieces_print_without_support(piece):
    pieces = pr.printed_pieces()
    pose = next(p for p in pr.POSES if p.piece == piece)
    shape = pr.posed(pieces[piece], pose)
    assert len(pieces[piece].solids()) == 1
    assert pr.needs_support(pr.overhangs(shape)) == []
    assert pr.islands(shape) == []
