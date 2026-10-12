"""The simplified board model must match Waveshare's STEP where it matters.

Needs reference/ (run ./fetch_reference.sh); skipped without it.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from huxcase import hardware as hw

STEP = Path(__file__).resolve().parent.parent / "reference/drawing/ESP32-S3-AUDIO-Board.stp"
Z_SHIFT = 7.6  # z_board = z_step + 7.6


def _find(node, name):
    if node.label == name:
        yield node
    for c in node.children:
        yield from _find(c, name)


class Placed:
    """A STEP node plus the transform to board coordinates.

    build123d's Shape.moved() deep-copies the whole assembly tree (seconds per
    call on this 26 MB STEP), so points and boxes are transformed by hand.
    """

    def __init__(self, shape, trsf):
        self.shape, self.trsf, self.label = shape, trsf, shape.label

    def point(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        from OCP.gp import gp_Pnt

        q = gp_Pnt(x, y, z).Transformed(self.trsf)
        return q.X(), q.Y(), q.Z()

    def verts(self) -> list[tuple[float, float, float]]:
        return [self.point(v.X, v.Y, v.Z) for v in self.shape.vertices()]

    def bbox(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        b = self.shape.bounding_box()
        pts = [
            self.point(x, y, z)
            for x in (b.min.X, b.max.X)
            for y in (b.min.Y, b.max.Y)
            for z in (b.min.Z, b.max.Z)
        ]
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi


@pytest.fixture(scope="module")
def step():
    if not STEP.exists():
        pytest.skip("reference STEP missing: run ./fetch_reference.sh")
    from build123d import import_step
    from OCP.gp import gp_Trsf

    asm = import_step(str(STEP))
    board = next(_find(asm, "ESP32-S3-AUDIO-BOARD_ASM"))
    trsf = board.location.wrapped.Transformation()
    parts = {c.label: Placed(c, trsf) for c in board.children[0].children}
    # Connectors, header, USB-C and mics also sit one level up, in global coords.
    pcba = next(_find(asm, "ESP32-S3-AUDIO-PCBA_ASM"))
    everything = list(parts.values()) + [
        Placed(c, gp_Trsf()) for c in pcba.children if c is not board
    ]
    return asm, parts, everything


def test_pcb_outline_and_thickness(step):
    _, parts, _ = step
    lo, hi = parts["BOARD"].bbox()
    assert hi[0] == pytest.approx(hw.PCB_R, abs=0.05)
    assert hi[1] == pytest.approx(hw.PCB_FLAT_POS_Y, abs=0.05)
    assert lo[1] == pytest.approx(hw.PCB_FLAT_NEG_Y, abs=0.05)
    assert hi[2] - lo[2] == pytest.approx(hw.PCB_T, abs=0.01)


def test_mounting_holes(step):
    from build123d import GeomType

    _, parts, _ = step
    pcb = parts["BOARD"]
    found = {
        pcb.point(e.arc_center.X, e.arc_center.Y, e.arc_center.Z)[:2]
        for e in pcb.shape.edges()
        if e.geom_type == GeomType.CIRCLE and e.radius > 1.3
    }
    for x, y in hw.HOLES:
        assert any(math.hypot(x - fx, y - fy) < 0.05 for fx, fy in found), (x, y)


def test_side_buttons_and_switch(step):
    _, parts, _ = step
    for name, deg in hw.KEYS.items():
        label = {"RESET": "KEY5_ASM", "BOOT": "KEY4_ASM"}.get(name, name + "_ASM")
        vs = parts[label].verts()
        assert max(math.hypot(x, y) for x, y, _ in vs) == pytest.approx(hw.KEY_TIP_R, abs=0.1)
        cx = sum(v[0] for v in vs) / len(vs)
        cy = sum(v[1] for v in vs) / len(vs)
        assert math.degrees(math.atan2(cy, cx)) == pytest.approx(deg, abs=1.0)
    vs = parts["SW1_ASM"].verts()
    assert max(math.hypot(x, y) for x, y, _ in vs) == pytest.approx(hw.SLIDE_SWITCH_TIP_R, abs=0.1)


def test_mics(step):
    _, parts, _ = step
    centres = []
    for label in ("MIC1_ASM", "MIC2_ASM"):
        lo, hi = parts[label].bbox()
        centres.append(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2))
    for x, y in hw.MICS:
        assert any(math.hypot(x - cx, y - cy) < 0.1 for cx, cy in centres)


def test_speaker(step):
    asm, _, _ = step
    spk = next(_find(asm, "SPK-4020-5W")).bounding_box()
    assert pytest.approx(hw.SPK_D, abs=0.05) == spk.size.X
    assert pytest.approx(hw.SPK_DEPTH, abs=0.05) == spk.size.Z


def test_model_envelopes_cover_step_parts(step):
    """The simplified top/bottom keep-out must contain every real component."""
    _, _, everything = step
    comps = [p for p in everything if p.label != "BOARD"]
    boxes = [p.bbox() for p in comps]
    top = max(hi[2] for _, hi in boxes) + Z_SHIFT
    assert top <= 5.9  # board_keepout(top=5.9) in every concept
    bottom = -(min(lo[2] for lo, _ in boxes) + Z_SHIFT)
    assert bottom - hw.PCB_T <= 9.0  # plug zone depth under the connectors
