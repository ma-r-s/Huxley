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
    vol = lambda m: m.W * m.D * m.H  # noqa: E731
    assert sum(p.kind == "cell" for p in duo.parts) == 2 * sum(p.kind == "cell" for p in one.parts)
    assert vol(hx1) < vol(hx1_classic)
    assert duo.size()[2] < one.size()[2]


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
    """The lead is cut to SPK_LEAD_CUT: it must cover the route plus enough to
    unplug it, no more (loose wire buzzes), and Mario's 97 mm lead is long
    enough to cut."""
    from huxcase import hardware as hw
    from huxcase.concepts import hx1

    route = _route_length(hx1.build().envelopes["cable_spk"])
    assert route + 5.0 <= hx1.SPK_LEAD_CUT <= route + 10.0 <= hw.SPK_CABLE_L


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


@pytest.fixture(scope="module")
def hx1_concept():
    from huxcase.concepts import hx1

    return hx1.build()


@pytest.mark.parametrize(
    ("part", "ribs"),
    [
        ("holder_0", "tub_ribs"),
        ("holder_1", "tub_ribs"),
        ("holder_0", "bridge_ribs"),
        ("holder_1", "bridge_ribs"),
        ("tact_switch", "tub_ribs"),
        ("speaker", "plug_ribs"),
        ("speaker", "tub_ribs"),
    ],
)
def test_hx1_crush_ribs_grip_the_bought_parts(hx1_concept, part, ribs):
    """No glue: every bought part that would otherwise rattle is held by ribs
    that really reach into it (the fit check only proves nothing else does)."""
    c = hx1_concept
    assert (c.envelopes[part] & c.shells[ribs]).volume > 0.05


def test_hx1_lever_pin_is_captured_in_two_blind_seats(hx1_concept):
    """The printed pin sits in a seat in the tub behind the lever and one in
    the faceplate's tooth in front of it, so closing the case captures it."""
    from build123d import Cylinder, Pos, Rot
    from huxcase.concepts import hx1

    c = hx1_concept

    def collar(y0, y1):
        r0, r1 = hx1.PIN_BORE / 2 + 0.05, hx1.PIN_BORE / 2 + 0.6
        ring = Cylinder(r1, y1 - y0) - Cylinder(r0, y1 - y0 + 1)
        return Pos(hx1.PIVOT_X, (y0 + y1) / 2, hx1.PIVOT_Z) * Rot(90, 0, 0) * ring

    rear = collar(hx1.PIN_Y1 - 2.0, hx1.PIN_Y1)
    front = collar(hx1.PIN_Y0, hx1.PIN_Y0 + 2.0)
    # A seat wall all the way round each end (a collar 0.55 thick is mostly solid)...
    assert (rear & c.shells["tub"]).volume > 0.8 * rear.volume
    assert (front & c.shells["faceplate"]).volume > 0.8 * front.volume
    # ...and an end wall beyond each end, so it cannot slide out.
    cap = lambda y: Pos(hx1.PIVOT_X, y, hx1.PIVOT_Z) * Rot(90, 0, 0) * Cylinder(1.0, 0.2)  # noqa: E731
    assert (cap(hx1.PIN_Y1 + 0.5) & c.shells["tub"]).volume > 0
    assert (cap(hx1.PIN_Y0 - 0.5) & c.shells["faceplate"]).volume > 0
