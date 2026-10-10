"""Build every concept, refuse to export if anything collides, write
out/<id>.glb (viewer + Blender), out/<id>.step (CAD) and out/concepts.json.

    cd clients/firmware/enclosure && uv run python -m huxcase.export
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from build123d import Color, Compound, export_gltf, export_step

from huxcase.concept import Concept, collisions
from huxcase.concepts import hx1, hx1_classic, hx2_baton, hx3_radio, hx4_can

OUT = Path(__file__).resolve().parent.parent / "out"
BUILDERS = [hx1.build, hx1_classic.build, hx2_baton.build, hx3_radio.build, hx4_can.build]

# Approximate masses for the weight estimate (g).
PLA_G_PER_CC = 1.24
HARDWARE_G = {"board": 22.0, "speaker": 28.0, "switch": 6.0, "screws": 4.0}
CELL_G = 47.0

KIND_COLOR = {
    "shell": (0.85, 0.85, 0.83),
    "shell_dark": (0.12, 0.12, 0.12),
    "shell_inner": (0.7, 0.7, 0.68),
    "accent": (1.0, 0.36, 0.0),
    "accent_dark": (0.1, 0.1, 0.1),
    "translucent": (0.95, 0.95, 0.95),
    "glow": (0.85, 0.95, 1.0),
    "wire_black": (0.05, 0.05, 0.05),
    "wire_red": (0.7, 0.05, 0.05),
    "wire_white": (0.9, 0.9, 0.85),
    "clear": (0.95, 0.95, 1.0),
    "rubber": (0.08, 0.08, 0.08),
    "screw": (0.55, 0.55, 0.58),
    "pcb": (0.1, 0.28, 0.6),
    "pcb_green": (0.1, 0.45, 0.2),
    "metal": (0.75, 0.75, 0.78),
    "black": (0.05, 0.05, 0.05),
    "white": (0.95, 0.95, 0.92),
    "speaker": (0.15, 0.15, 0.16),
    "cone": (0.05, 0.05, 0.05),
    "cell": (0.2, 0.45, 0.35),
    "switch": (0.2, 0.2, 0.22),
    "stem": (0.8, 0.45, 0.1),
}
PRINTED = {"shell", "shell_dark", "shell_inner", "accent", "accent_dark", "translucent", "glow"}


def summarize(c: Concept, hits: list[tuple[str, str, float]]) -> dict:
    printed_cc = sum(p.shape.volume for p in c.parts if p.kind in PRINTED) / 1000
    cells = sum(1 for p in c.parts if p.kind == "cell")
    w, d, h = c.size()
    return {
        "id": c.id,
        "name": c.name,
        "size_mm": [round(w, 1), round(d, 1), round(h, 1)],
        "printed_cc": round(printed_cc, 1),
        "cells": cells,
        "weight_g": round(printed_cc * PLA_G_PER_CC + sum(HARDWARE_G.values()) + cells * CELL_G),
        "back_volume_cc": c.notes.get("back_volume_cc"),
        "collisions": hits,
        "parts": sorted({p.name for p in c.parts}),
    }


def export(c: Concept) -> None:
    children = []
    for p in c.parts:
        s = p.shape
        s.label = f"{p.kind}__{p.name}"
        s.color = Color(*KIND_COLOR.get(p.kind, (0.5, 0.5, 0.5)))
        children.append(s)
    asm = Compound(children=children, label=c.id)
    export_gltf(
        asm, str(OUT / f"{c.id}.glb"), binary=True, linear_deflection=0.04, angular_deflection=0.25
    )
    export_step(asm, str(OUT / f"{c.id}.step"))


def export_web(c: Concept) -> None:
    """Coarse, one-mesh-per-kind GLB for the browser viewer (out/web/<id>.glb)."""
    import trimesh

    tmp = OUT / "web" / f"{c.id}.raw.glb"
    children = []
    for p in c.parts:
        s = p.shape
        s.label = f"{p.kind}__{p.name}"
        children.append(s)
    export_gltf(
        Compound(children=children),
        str(tmp),
        binary=True,
        linear_deflection=0.1,
        angular_deflection=0.5,
    )
    scene = trimesh.load(str(tmp))
    by_kind: dict[str, list] = {}
    for node in scene.graph.nodes_geometry:
        tf, geom = scene.graph[node]
        kind = node.split("__")[0]
        if "__board_led_" in node:
            kind = "led"
        m = scene.geometry[geom].copy()
        m.apply_transform(tf)
        by_kind.setdefault(kind, []).append(m)
    out = trimesh.Scene()
    for kind, meshes in by_kind.items():
        merged = trimesh.util.concatenate(meshes)
        # Keep per-B-rep-face vertices (no merge) so edges stay crisp; drop UVs.
        merged.visual = trimesh.visual.ColorVisuals(merged)
        out.add_geometry(merged, node_name=kind, geom_name=kind)
    glb = trimesh.exchange.gltf.export_glb(out, include_normals=True)
    (OUT / "web" / f"{c.id}.glb").write_bytes(glb)
    tmp.unlink()


def main() -> int:
    (OUT / "web").mkdir(parents=True, exist_ok=True)
    summary = []
    failed = False
    for build in BUILDERS:
        t = time.time()
        c = build()
        hits = collisions(c)
        summary.append(summarize(c, hits))
        if hits:
            failed = True
            print(f"{c.name}: COLLISIONS {hits}", file=sys.stderr)
            continue
        export(c)
        export_web(c)
        print(
            f"{c.name}: ok {summary[-1]['size_mm']} mm, ~{summary[-1]['weight_g']} g ({time.time() - t:.1f}s)"
        )
    (OUT / "concepts.json").write_text(json.dumps(summary, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
