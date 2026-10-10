"""Concept container + the packaging fit check every concept must pass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from build123d import Solid

    from huxcase.hardware import Part


@dataclass
class Concept:
    id: str
    name: str
    parts: list[Part]
    # Packaging volumes that must not touch each other or any shell part:
    # board keepout, speaker, cell bay, PTT switch stack...
    envelopes: dict[str, Solid]
    # Shell parts (anything printed). Envelopes may not intersect these
    # unless the pair is listed in `allowed`.
    shells: dict[str, Solid]
    allowed: set[tuple[str, str]] = field(default_factory=set)
    notes: dict[str, str] = field(default_factory=dict)

    def size(self) -> tuple[float, float, float]:
        bb = None
        for p in self.parts:
            b = p.shape.bounding_box()
            bb = b if bb is None else bb.add(b)
        assert bb is not None
        return (bb.size.X, bb.size.Y, bb.size.Z)


def _bb_overlap(a: Solid, b: Solid) -> bool:
    ba, bb = a.bounding_box(), b.bounding_box()
    return (
        ba.min.X < bb.max.X
        and bb.min.X < ba.max.X
        and ba.min.Y < bb.max.Y
        and bb.min.Y < ba.max.Y
        and ba.min.Z < bb.max.Z
        and bb.min.Z < ba.max.Z
    )


def collisions(c: Concept, tol: float = 0.05) -> list[tuple[str, str, float]]:
    """Every intersecting (envelope, envelope|shell) pair with its volume in mm^3."""
    hits = []
    env = list(c.envelopes.items())
    pairs = [(a, b) for i, a in enumerate(env) for b in env[i + 1 :]]
    pairs += [(a, s) for a in env for s in c.shells.items()]
    for (na, sa), (nb, sb) in pairs:
        if (na, nb) in c.allowed or (nb, na) in c.allowed:
            continue
        if not _bb_overlap(sa, sb):
            continue
        v = (sa & sb).volume
        if v > tol:
            hits.append((na, nb, round(v, 2)))
    return hits


def cavity_cc(region: Solid, occupied: list[Solid]) -> float:
    """Air left in `region` after removing `occupied` solids, in cc."""
    for o in occupied:
        region = region - o
    return round(region.volume / 1000, 1)
