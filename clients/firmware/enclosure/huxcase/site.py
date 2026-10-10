# ruff: noqa: RUF001  (page copy is user-facing: real multiplication signs)
"""HX-1 build page: one glTF node per part plus the assembly manifest.

Writes out/site/: hx1.gltf.json (glTF with the buffer inlined as base64; the
artifact host does not serve .glb), assembly.json (steps, labels, specs,
parts list), index.html (site/index.html with the manifest inlined) and preview.html (the
same page with a charset, for a local static server).

    cd clients/firmware/enclosure && uv run python -m huxcase.site
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

# Assembly order, as it is built on the bench: the front shell lies face down
# and everything drops in from behind (device +Y). `enter` is where each step
# starts when it flies in; `explode` is where it sits in the exploded view
# (front to back in stacking order). Both in device mm. Parts not listed are not shown (the protection board
# pocket is reserved, not fitted).
STEPS = [
    {
        "id": "front",
        "title": "Front shell",
        "text": "Grey PLA with a black grille inlay. The two mic ports and the "
        "charge light's pipe go through the face.",
        "parts": [
            "shell_front",
            "face_panel",
            "standoffs",
            "speaker_ring",
            "mic_boots",
            "light_pipe",
        ],
        "enter": (0, 140, 0),
        "explode": (0, 0, 0),
    },
    {
        "id": "speaker",
        "title": "Speaker",
        "text": "40 mm, 5 W. Presses into its ring with the solder tabs up. "
        "Its 97 mm lead is cut to 45 mm first.",
        "parts": ["speaker_*"],
        "enter": (0, 80, 0),
        "explode": (0, 30, 0),
    },
    {
        "id": "board",
        "title": "Board",
        "text": "The ESP32-S3 audio board, mics toward the face, on three "
        "standoffs. The speaker plugs into its back.",
        "parts": ["board_*"],
        "enter": (0, 100, 0),
        "explode": (0, 45, 10),
    },
    {
        "id": "ring",
        "title": "Light ring",
        "text": "Frosted PLA over the seven LEDs on the board's back. It turns "
        "seven dots into one glow and feeds the frosted back.",
        "parts": ["light_ring"],
        "enter": (0, 110, 0),
        "explode": (0, 65, 10),
    },
    {
        "id": "switch",
        "title": "Talk switch",
        "text": "The 12 × 12 button drops into a pocket with crush ribs. A "
        "printed plunger and a 0.4 × 4 × 15 spring go in its tube.",
        "parts": ["ts_cradle", "tact_*", "plunger", "spring"],
        "enter": (0, 90, 0),
        "explode": (0, 58, 22),
    },
    {
        "id": "lever",
        "title": "Lever",
        "text": "Orange PLA, 56 mm, pinned at the bottom. Pressing it squeezes "
        "the spring until the button clicks.",
        "parts": ["ptt_bar", "ptt_pin"],
        "enter": (70, 0, 0),
        "explode": (40, 0, 0),
    },
    {
        "id": "rocker",
        "title": "Power switch",
        "text": "The KCD1-101 rocker snaps into a recess in the left side, "
        "below the surface, so it can't be knocked off.",
        "parts": ["rocker_*"],
        "enter": (-70, 0, 0),
        "explode": (-40, 0, 0),
    },
    {
        "id": "cells",
        "title": "Batteries",
        "text": "Two 18650s in their holders, wired in parallel. The board "
        "charges both over USB-C.",
        "parts": ["holder_*", "cell_*"],
        "enter": (0, 110, 0),
        "explode": (0, 95, 0),
    },
    {
        "id": "wiring",
        "title": "Wiring",
        "text": "Every wire runs in the channel between the holders: speaker, "
        "battery, and the two talk-switch wires to the header.",
        "parts": ["cable_*", "lead_*"],
        "enter": (0, 60, 0),
        "explode": (0, 78, 0),
    },
    {
        "id": "back",
        "title": "Back shell",
        "text": "Grey PLA, the lower third of the back. It closes over the "
        "speaker and the bottom of the holders.",
        "parts": ["shell_back"],
        "enter": (0, 120, 0),
        "explode": (0, 135, -10),
    },
    {
        "id": "frosted",
        "title": "Frosted back",
        "text": "Frosted PLA for the top and the upper back. The LEDs glow "
        "through it, softest where the holders sit.",
        "parts": ["frosted_back"],
        "enter": (0, 140, 0),
        "explode": (0, 150, 25),
    },
]

LABELS = [
    ("shell_front", "Front shell"),
    ("face_panel", "Grille inlay"),
    ("standoffs", "Board standoffs"),
    ("speaker_ring", "Speaker ring"),
    ("mic_boots", "Mic boots"),
    ("light_pipe", "Charge light pipe"),
    ("speaker_*", "Speaker, 40 mm 5 W"),
    ("board_*", "ESP32-S3 audio board"),
    ("light_ring", "Light ring"),
    ("ts_cradle", "Switch cradle"),
    ("tact_*", "12 × 12 tact switch"),
    ("plunger", "Plunger"),
    ("spring", "Spring 0.4 × 4 × 15"),
    ("ptt_bar", "Talk lever"),
    ("ptt_pin", "Lever pin"),
    ("rocker_*", "Power switch KCD1-101"),
    ("holder_*", "18650 holder"),
    ("cell_*", "18650 cell"),
    ("cable_spk", "Speaker lead"),
    ("cable_bat_*", "Battery lead"),
    ("cable_ptt_*", "Talk switch wires"),
    ("lead_*", "Holder leads"),
    ("shell_back", "Back shell"),
    ("frosted_back", "Frosted back and top"),
]

PRINT_MATERIAL = {
    "shell": "Grey PLA",
    "shell_dark": "Black PLA",
    "shell_inner": "Grey PLA",
    "accent": "Orange PLA",
    "translucent": "Frosted PLA",
    "glow": "Frosted PLA",
    "clear": "Frosted PLA",
}

BOUGHT = [
    ("ESP32-S3 audio board (Waveshare)", 1),
    ("Speaker 40 mm 5 W, with the board", 1),
    ("18650 cell", 2),
    ("Single-18650 holder with leads", 2),
    ("12 × 12 tact switch", 1),
    ("Spring 0.4 × 4 × 15 (SciWil kit)", 1),
    ("KCD1-101 rocker switch", 1),
    ("M2 × 8 screw + washer, board", 3),
    ("M2 heat-set insert, board", 3),
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

    out = trimesh.Scene()
    nodes = {}
    for i, ((step, kind, label), meshes) in enumerate(sorted(groups.items())):
        merged = trimesh.util.concatenate(meshes)
        merged.visual = trimesh.visual.ColorVisuals(merged)
        name = f"n{i:03d}"
        out.add_geometry(merged, node_name=name, geom_name=name)
        nodes[name] = {"step": step, "kind": kind, "label": label.split("|")[0]}
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
            entry = printed.setdefault(label, [PRINT_MATERIAL.get(p.kind, "PLA"), 0.0])
            entry[1] += p.shape.volume / 1000
    printed_cc = sum(v for _, v in printed.values())
    cells = sum(1 for p in c.parts if p.kind == "cell")
    w, d, h = c.size()
    manifest = {
        "name": c.name,
        "rev": "rev C",
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
    # The manifest is small: inline it so the spec sheet renders before the model loads.
    page = (SITE_SRC / "index.html").read_text()
    page = page.replace(
        "__MANIFEST__", json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    )
    (SITE_OUT / "index.html").write_text(page)
    # preview.html: the same page with the charset and viewport the publish
    # skeleton adds, for checking it on a local static server.
    (SITE_OUT / "preview.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
        'initial-scale=1, viewport-fit=cover">\n' + page
    )
    return manifest


def main() -> int:
    m = build_site()
    size = (SITE_OUT / "hx1.gltf.json").stat().st_size / 1e6
    print(
        f"wrote {SITE_OUT}: {len(m['nodes'])} nodes, {len(m['steps'])} steps, model {size:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
