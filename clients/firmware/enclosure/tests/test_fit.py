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
    assert {"board_pcb", "speaker_frame", "mx_housing"} <= names
    assert any(p.kind == "cell" for p in concept.parts)
    assert {"board", "speaker", "mx"} <= set(concept.envelopes)
    assert any(k.startswith("cell") for k in concept.envelopes)


def test_hx1_duo_is_smaller_than_hx1_with_twice_the_cells():
    from huxcase.concepts import hx1_classic, hx1d_duo

    one, duo = hx1_classic.build(), hx1d_duo.build()
    body = lambda c: next(p.shape for p in c.parts if p.name == "shell_front").bounding_box()  # noqa: E731
    vol = lambda m: m.W * m.D * m.H  # noqa: E731
    assert sum(p.kind == "cell" for p in duo.parts) == 2 * sum(p.kind == "cell" for p in one.parts)
    assert vol(hx1d_duo) < vol(hx1_classic)
    assert body(duo).size.Z < body(one).size.Z


def test_speaker_gets_a_real_back_box(concept):
    # The original Waveshare case gives the driver roughly 40 to 60 cc.
    assert concept.notes["back_volume_cc"] >= 40.0
