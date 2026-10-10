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
    assert names & {"mx_housing", "ms_body", "tact_body"}, "every concept needs a talk switch"
    assert any(p.kind == "cell" for p in concept.parts)
    assert {"board", "speaker"} <= set(concept.envelopes)
    assert set(concept.envelopes) & {"mx", "microswitch", "tact_switch"}
    assert any(k.startswith(("cell", "holder")) for k in concept.envelopes)


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


def test_hx1_tact_switch_clicks_before_the_stop():
    """Spring in series: the lever compresses the spring until it reaches the
    switch's actuation force. That must happen before the stop post, with
    margin, for a nominal 12x12 switch; a stiff one (2.6 N) must still click
    with one 0.5 mm washer under the plunger flange."""
    from huxcase import hardware as hw
    from huxcase.concepts import hx1

    assert hx1.click_travel() < hx1.STOP - 0.5
    assert hx1.click_travel(force=2.6, shim=0.5) < hx1.STOP - 0.2
    solid = 10 * 0.4  # 10 coils of 0.4 mm wire
    assert solid + 1.0 < hx1.SPRING_REST - hx1.STOP + hw.TACT_TRAVEL


def test_hx1_light_ring_clears_the_holders():
    """The frosted ring behind the rear LEDs must stop short of the holders'
    front faces, or the cells would shadow the light."""
    from huxcase.concepts import hx1

    c = hx1.build()
    ring_back_y = hx1.BOARD_TOP_Y - hx1.RING_Z1
    holder_front_y = min(
        e.bounding_box().min.Y for k, e in c.envelopes.items() if k.startswith("holder")
    )
    assert ring_back_y < holder_front_y


def test_hx1_rocker_sits_below_the_side_surface():
    """The KCD1-101 is recessed: nothing of it stands past the left side."""
    from huxcase.concepts import hx1

    c = hx1.build()
    rocker = [p.shape for p in c.parts if p.name.startswith("rocker_")]
    assert min(r.bounding_box().min.X for r in rocker) >= -hx1.W / 2


def _route_length(tube, d: float = 1.4) -> float:
    import math

    return tube.volume / (math.pi * (d / 2) ** 2)


def test_hx1_speaker_lead_cut_reaches_with_slack():
    """The lead is cut to SPK_LEAD_CUT: it must cover the route plus room to
    lift the board out, and Mario's 97 mm lead must be long enough to cut."""
    from huxcase import hardware as hw
    from huxcase.concepts import hx1

    route = _route_length(hx1.build().envelopes["cable_spk"])
    assert route + 15.0 <= hx1.SPK_LEAD_CUT <= hw.SPK_CABLE_L


def test_hx1_plunger_tube_prints_and_clears_the_square_stem():
    """Mario measured a 3.8 mm square stem: its corners need more room than a
    round stem. The tube wall must be printable with a 0.4 mm nozzle."""
    import math

    from huxcase import fits
    from huxcase import hardware as hw
    from huxcase.concepts import hx1

    corner = hw.TACT_STEM_W / math.sqrt(2)
    assert corner + fits.PART <= hx1.TUBE_BORE
    assert hx1.TUBE_BORE - hx1.PLUNGER_R >= fits.SLIDE
    assert hx1.TUBE_R - hx1.TUBE_BORE >= fits.MIN_WALL


def test_hx1_bought_parts_measured():
    """Mario's caliper numbers (2026-10-10) are what the model uses."""
    from huxcase import hardware as hw

    assert (hw.HOLDER_L, hw.HOLDER_W, hw.HOLDER_H) == (76.0, 21.0, 20.5)
    assert hw.TACT_BODY + hw.TACT_STEM_H == 7.3
    assert hw.ROCKER_BODY[2] + hw.ROCKER_TABS == 18.6
    h = next(p.shape for p in hw.holder() if p.name == "holder").bounding_box().size
    assert (round(h.X, 2), round(h.Y, 2), round(h.Z, 2)) == (21.0, 20.5, 76.0)
