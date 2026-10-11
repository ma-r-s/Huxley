# ruff: noqa: RUF001  (page copy is user-facing: real multiplication signs)
"""HX-1 build page: one glTF node per part plus the assembly manifest.

Writes out/site/: hx1.gltf.json (glTF with the buffer inlined as base64; the
artifact host does not serve .glb), assembly.json (steps, labels, specs,
parts list), index.html (site/index.html with the manifest inlined) and preview.html (the
same page with a charset, for a local static server).

    cd clients/firmware/enclosure && uv run python -m huxcase.site           # everything
    cd clients/firmware/enclosure && uv run python -m huxcase.site --page    # page edits only
"""

from __future__ import annotations

import base64
import fnmatch
import json
import struct
from pathlib import Path

from build123d import Compound, export_gltf

from huxcase.concepts import hx1
from huxcase.export import CELL_G, HARDWARE_G, OUT, PLA_G_PER_CC, PRINTED

SITE_SRC = Path(__file__).resolve().parent.parent / "site"
SITE_OUT = OUT / "site"

# Assembly order, as it is built on the bench (rev D): the tub lies on its
# back and its parts go in from the open front; the faceplate side (grille,
# speaker, board) is built face down on the faceplate and closes over it last.
# `enter` is where a step starts when it flies in; `explode` is where it sits
# in the exploded view; `explode_parts` spreads parts of one step further
# apart in that view. All in device mm.
STEPS = [
    {
        "id": "tub",
        "title": "Tub",
        "text": "One print, standing up: grey to 36 mm, then a filament swap to frosted "
        "for the rest, so the light shows through the top and upper back.",
        "parts": ["tub_*"],
        "enter": (0, -130, 0),
        "explode": (0, 0, 0),
    },
    {
        "id": "inserts",
        "title": "Inserts",
        "text": "Three M3 × 5 heat-set inserts with the insert tool: two in the top "
        "corner blocks, one in the boss on the back wall. Two more go in the faceplate.",
        "parts": ["inserts", "bridge_insert"],
        "enter": (0, -50, 0),
        "explode": (0, -24, 0),
    },
    {
        "id": "rocker",
        "title": "Power switch",
        "text": "The KCD1-101 snaps into its recess in the left side from outside. "
        "Solder its two leads before the holders go in.",
        "parts": ["rocker_*"],
        "enter": (-70, 0, 0),
        "explode": (-40, 0, 0),
    },
    {
        "id": "switch",
        "title": "Talk switch",
        "text": "Solder two wires to one pair of legs, then slide the 12 × 12 button "
        "into its cradle from the front. Crush ribs hold it with its top on the tube.",
        "parts": ["tact_*"],
        "enter": (0, -60, 0),
        "explode": (0, -30, 18),
    },
    {
        "id": "plunger",
        "title": "Plunger and spring",
        "text": "From outside, the printed plunger goes into the tube, then the "
        "0.4 × 4 × 15 spring on top of it.",
        "parts": ["plunger", "spring"],
        "enter": (60, 0, 0),
        "explode": (30, 0, 18),
    },
    {
        "id": "lever",
        "title": "Lever",
        "text": "Push the printed pin through the lever, then slide it in from the "
        "front along its channel until the pin drops into its seat at the back.",
        "parts": ["ptt_bar", "ptt_pin"],
        "enter": (70, 0, 0),
        "explode": (45, 0, 0),
    },
    {
        "id": "cells",
        "title": "Batteries",
        "text": "Each holder goes in 1 mm toward the middle, then slides out under "
        "its two lips. Crush ribs underneath and above keep it from moving.",
        "parts": ["holder_*", "cell_*"],
        "enter": (0, -100, 0),
        "explode": (0, -46, 0),
    },
    {
        "id": "bridge",
        "title": "Holder bridge",
        "text": "A printed bridge across both holders, one M3 × 6 into the boss. Its "
        "ribs press the holders onto the back wall; its front bar keeps the cells in.",
        "parts": ["bridge", "bridge_screw"],
        "enter": (0, -80, 0),
        "explode": (0, -64, 0),
    },
    {
        "id": "wiring",
        "title": "Wiring",
        "text": "Before closing: the holder leads join in pairs, the joints in "
        "heat-shrink go into their pockets, the long leads into their clips.",
        "parts": ["cable_*", "lead_*"],
        "enter": (0, -40, 0),
        "explode": (0, -56, 0),
    },
    {
        "id": "front",
        "title": "Faceplate side",
        "text": "Built face down: two inserts in its bottom bosses, the grille plug "
        "into its window, the speaker on its six tabs, the board on three M2 × 8.",
        "parts": [
            "faceplate",
            "face_inserts",
            "grille_plug",
            "speaker_*",
            "board_*",
            "board_screws",
        ],
        "enter": (0, -150, 0),
        "explode": (0, -108, 0),
        "explode_parts": {
            "speaker_*": (0, -72, 0),
            "board_*": (0, -86, 0),
            "board_screws": (0, -80, 0),
            "grille_plug": (0, -126, 0),
        },
    },
    {
        "id": "screws",
        "title": "Close it",
        "text": "Two M3 × 8 through the face at the top, two up through the floor. "
        "Closing it also captures the lever pin and clamps the speaker.",
        "parts": ["case_screws"],
        "enter": (0, -60, 0),
        "explode": (0, -142, 0),
    },
]

LABELS = [
    ("tub_*", "Tub"),
    ("inserts", "M3 × 5 heat-set insert"),
    ("bridge_insert", "M3 × 5 heat-set insert"),
    ("face_inserts", "M3 × 5 heat-set insert"),
    ("faceplate", "Faceplate"),
    ("grille_plug", "Grille plug"),
    ("speaker_*", "Speaker, 40 mm 5 W"),
    ("board_screws", "M2 × 8 screw and washer"),
    ("board_*", "ESP32-S3 audio board"),
    ("tact_*", "12 × 12 tact switch"),
    ("plunger", "Plunger"),
    ("spring", "Spring 0.4 × 4 × 15"),
    ("ptt_bar", "Talk lever"),
    ("ptt_pin", "Lever pin, printed"),
    ("rocker_*", "Power switch KCD1-101"),
    ("holder_*", "18650 holder"),
    ("cell_*", "18650 cell"),
    ("bridge_screw", "M3 × 6 screw"),
    ("bridge", "Holder bridge"),
    ("cable_spk", "Speaker lead"),
    ("cable_bat_*", "Battery lead"),
    ("cable_ptt_*", "Talk switch wires"),
    ("lead_*", "Holder leads"),
    ("case_screws", "M3 × 8 screw"),
]

PRINT_MATERIAL = {
    "shell": "Grey PLA",
    "shell_dark": "Black PLA",
    "shell_inner": "Grey PLA",
    "accent": "Orange PLA",
    "translucent": "Frosted PLA",
}
# One print, two filaments.
PRINT_MATERIAL_BY_LABEL = {"Tub": "Grey, then frosted PLA"}

BOUGHT = [
    ("ESP32-S3 audio board (Waveshare)", 1),
    ("Speaker 40 mm 5 W, with the board", 1),
    ("18650 cell", 2),
    ("Single-18650 holder with leads", 2),
    ("12 × 12 tact switch", 1),
    ("Spring 0.4 × 4 × 15 (SciWil kit)", 1),
    ("KCD1-101 rocker switch", 1),
    ("M3 × 8 button head, case", 4),
    ("M3 × 6 button head, holder bridge", 1),
    ("M3 × 5 heat-set insert", 5),
    ("M2 × 8 button head + flat washer, board", 3),
    ("Ribbon cable, for every run", 1),
]


def _match(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _label(name: str) -> str:
    return next(label for pattern, label in LABELS if fnmatch.fnmatchcase(name, pattern))


def _to_three(v: tuple[float, float, float]) -> list[float]:
    """Device mm (Z up, front at -Y) -> glTF metres (Y up, front at +Z)."""
    x, y, z = v
    return [round(x / 1000, 5), round(z / 1000, 5), round(-y / 1000, 5)]


def _glb_to_json(glb: bytes) -> dict:
    off, doc, blob = 12, None, b""
    while off < len(glb):
        n, kind = struct.unpack_from("<II", glb, off)
        chunk = glb[off + 8 : off + 8 + n]
        if kind == 0x4E4F534A:
            doc = json.loads(chunk)
        elif kind == 0x004E4942:
            blob = chunk
        off += 8 + n
    assert doc is not None
    uri = "data:application/octet-stream;base64," + base64.b64encode(blob).decode()
    doc["buffers"] = [{"byteLength": len(blob), "uri": uri}]
    return doc


# Parts the page leaves out on purpose.
HIDDEN = {"protection_pcb"}  # reserved pocket, not fitted


def assign_steps(names: list[str]) -> dict[str, str]:
    """Part name -> the first step whose patterns match it."""
    out: dict[str, str] = {}
    for step in STEPS:
        for name in names:
            if name not in out and name not in HIDDEN and _match(name, step["parts"]):
                out[name] = step["id"]
    return out


def build_site() -> dict:
    import trimesh

    c = hx1.build()
    step_of = assign_steps([p.name for p in c.parts])
    shown = [p for p in c.parts if p.name in step_of]

    SITE_OUT.mkdir(parents=True, exist_ok=True)
    raw = SITE_OUT / "hx1.raw.glb"
    children = []
    for p in shown:
        s = p.shape
        s.label = f"{p.kind}__{p.name}"
        children.append(s)
    export_gltf(
        Compound(children=children),
        str(raw),
        binary=True,
        linear_deflection=0.05,
        angular_deflection=0.3,
    )
    scene = trimesh.load(str(raw))
    raw.unlink()

    # One node per (step, kind, label): the board's 30 pieces become a few meshes,
    # everything else keeps its own node so hovering names it.
    groups: dict[tuple[str, str, str], list] = {}
    group_parts: dict[tuple[str, str, str], set[str]] = {}
    tags = {f"{p.kind}__{p.name}": p for p in shown}
    for node in scene.graph.nodes_geometry:
        tf, geom = scene.graph[node]
        # Exact label, or the label plus a suffix the loader added; longest wins
        # so "cable_bat_neg2" never lands on "cable_bat_neg".
        tag = (
            node
            if node in tags
            else max((t for t in tags if node.startswith(t)), key=len, default=None)
        )
        if tag is None:
            continue
        part, kind = tags[tag].name, tags[tag].kind
        if part.startswith("board_led_"):
            kind = "led"
        label = _label(part)
        if part.startswith(("holder_", "cell_")):
            label += "|" + part[-1]  # one node per side
        m = scene.geometry[geom].copy()
        m.apply_transform(tf)
        groups.setdefault((step_of[part], kind, label), []).append(m)
        group_parts.setdefault((step_of[part], kind, label), set()).add(part)

    out = trimesh.Scene()
    nodes = {}
    for i, ((step, kind, label), meshes) in enumerate(sorted(groups.items())):
        merged = trimesh.util.concatenate(meshes)
        merged.visual = trimesh.visual.ColorVisuals(merged)
        name = f"n{i:03d}"
        out.add_geometry(merged, node_name=name, geom_name=name)
        nodes[name] = {"step": step, "kind": kind, "label": label.split("|")[0]}
        step_def = next(st for st in STEPS if st["id"] == step)
        for pattern, vec in step_def.get("explode_parts", {}).items():
            if any(fnmatch.fnmatchcase(p, pattern) for p in group_parts[(step, kind, label)]):
                nodes[name]["explode"] = _to_three(vec)
    doc = _glb_to_json(trimesh.exchange.gltf.export_glb(out, include_normals=True))
    (SITE_OUT / "hx1.gltf.json").write_text(json.dumps(doc, separators=(",", ":")))

    steps = []
    for step in STEPS:
        parts = [p for p in shown if step_of[p.name] == step["id"]]
        boxes = [p.shape.bounding_box() for p in parts]
        lo = [min((bb.min.X, bb.min.Y, bb.min.Z)[k] for bb in boxes) for k in range(3)]
        hi = [max((bb.max.X, bb.max.Y, bb.max.Z)[k] for bb in boxes) for k in range(3)]
        mid = tuple((a + b) / 2 for a, b in zip(lo, hi, strict=True))
        steps.append(
            {
                "id": step["id"],
                "title": step["title"],
                "text": step["text"],
                "enter": _to_three(step["enter"]),
                "explode": _to_three(step["explode"]),
                "anchor": _to_three(mid),
            }
        )

    printed: dict[str, list] = {}
    for p in c.parts:
        if p.kind in PRINTED or p.kind in ("shell_inner", "clear"):
            label = _label(p.name) if p.name in step_of else p.name
            material = PRINT_MATERIAL_BY_LABEL.get(label, PRINT_MATERIAL.get(p.kind, "PLA"))
            entry = printed.setdefault(label, [material, 0.0])
            entry[1] += p.shape.volume / 1000
    printed_cc = sum(v for _, v in printed.values())
    cells = sum(1 for p in c.parts if p.kind == "cell")
    w, d, h = c.size()
    manifest = {
        "name": c.name,
        "rev": "rev D",
        # The board's rear LEDs: the page puts its glow light here.
        "light_at": _to_three((hx1.X0, hx1.PCB_BOTTOM_Y + 3.0, hx1.BOARD_Z)),
        "steps": steps,
        "nodes": nodes,
        "specs": {
            "size_mm": [round(hx1.W, 1), round(hx1.D, 1), round(hx1.H, 1)],
            "size_with_lever_mm": round(w, 1),
            "weight_g": round(
                printed_cc * PLA_G_PER_CC + sum(HARDWARE_G.values()) + cells * CELL_G
            ),
            "back_volume_cc": c.notes["back_volume_cc"],
            "cells": cells,
            "printed_cc": round(printed_cc, 1),
        },
        "printed": [
            {"part": k, "material": m, "cc": round(v, 1)}
            for k, (m, v) in sorted(printed.items(), key=lambda kv: -kv[1][1])
        ],
        "bought": [{"part": k, "count": n} for k, n in BOUGHT],
    }
    (SITE_OUT / "assembly.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))

    if not (SITE_SRC / "index.html").exists():
        return manifest
    write_pages(manifest)
    return manifest


def write_pages(manifest: dict) -> None:
    """index.html (manifest inlined, so the spec sheet renders before the model
    loads) and preview.html (the same with a charset, for a local server)."""
    if not (SITE_SRC / "index.html").exists():
        return
    page = (SITE_SRC / "index.html").read_text()
    page = page.replace(
        "__MANIFEST__", json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    )
    (SITE_OUT / "index.html").write_text(page)
    (SITE_OUT / "preview.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
        'initial-scale=1, viewport-fit=cover">\n' + page
    )


def main() -> int:
    import sys

    if "--page" in sys.argv:  # page edits only: reuse the exported model and manifest
        write_pages(json.loads((SITE_OUT / "assembly.json").read_text()))
        print(f"wrote {SITE_OUT}/index.html")
        return 0
    m = build_site()
    size = (SITE_OUT / "hx1.gltf.json").stat().st_size / 1e6
    print(
        f"wrote {SITE_OUT}: {len(m['nodes'])} nodes, {len(m['steps'])} steps, model {size:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
