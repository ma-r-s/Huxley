"""Every concept must pack the measured hardware without collisions."""

from __future__ import annotations

import pytest
from huxcase.concept import collisions
from huxcase.export import BUILDERS


@pytest.fixture(scope="module", params=BUILDERS, ids=lambda b: b.__module__.rsplit(".", 1)[-1])
def concept(request):
    return request.param()


def test_no_collisions(concept):
    assert collisions(concept) == []


def test_has_core_hardware(concept):
    names = {p.name for p in concept.parts}
    assert {"board_pcb", "speaker_frame"} <= names
    assert names & {"mx_housing", "ms_body"}, "every concept needs a talk switch"
    assert any(p.kind == "cell" for p in concept.parts)
    assert {"board", "speaker"} <= set(concept.envelopes)
    assert set(concept.envelopes) & {"mx", "microswitch"}
    assert any(k.startswith("cell") for k in concept.envelopes)


def test_hx1_is_smaller_than_rev_a_with_twice_the_cells():
    from huxcase.concepts import hx1, hx1_classic

    one, duo = hx1_classic.build(), hx1.build()
    body = lambda c: next(p.shape for p in c.parts if p.name == "shell_front").bounding_box()  # noqa: E731
    vol = lambda m: m.W * m.D * m.H  # noqa: E731
    assert sum(p.kind == "cell" for p in duo.parts) == 2 * sum(p.kind == "cell" for p in one.parts)
    assert vol(hx1) < vol(hx1_classic)
    assert body(duo).size.Z < body(one).size.Z


def test_speaker_gets_a_real_back_box(concept):
    # The original Waveshare case gives the driver roughly 40 to 60 cc.
    assert concept.notes["back_volume_cc"] >= 40.0


def test_hx1_switch_never_overtravels():
    """At the lever's stop the mouse switch is pressed past its click, but by
    less than its overtravel."""
    from huxcase import hardware as hw
    from huxcase.concepts import hx1

    screw_tip_at_stop = hx1.X_OUT - hx1.SCREW_PROUD - hx1.STOP
    pressed = hx1.MS_BUTTON_X - screw_tip_at_stop
    assert hw.MS_PRETRAVEL < pressed < hw.MS_PRETRAVEL + hw.MS_OVERTRAVEL


def test_hx1_light_ring_clears_cells():
    from huxcase.concepts import hx1

    ring_back_y = hx1.BOARD_TOP_Y - hx1.RING_Z1
    assert ring_back_y < hx1.CELL_Y - hx1.hw.CELL_BAY_D / 2
