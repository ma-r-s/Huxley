"""The build page must show every part of HX-1, each in a step, each named."""

from __future__ import annotations

from huxcase import site
from huxcase.concepts import hx1


def test_every_part_is_in_a_build_step():
    names = [p.name for p in hx1.build().parts + site.face_art_parts()]
    assigned = site.assign_steps(names)
    missing = sorted(set(names) - set(assigned) - site.HIDDEN)
    assert missing == [], f"add these to a step in site.STEPS: {missing}"
    assert {s["id"] for s in site.STEPS} == set(assigned.values()), "a step has no parts"
    for name in assigned:
        site._label(name)  # raises StopIteration when a part has no display name


def test_step_copy_has_no_em_dashes():
    for s in site.STEPS:
        assert "—" not in s["title"] + s["text"]
