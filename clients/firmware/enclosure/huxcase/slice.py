"""Bambu Studio projects for Mario's P2S (0.4 nozzle, PLA), one per filament
colour, and the tub's two halves on one plate printed by object (frosted top,
then the red base, Mario's AMS slots): `out/print/projects/<n>_<what>.3mf`, not
sliced (he opens one, slices, prints), and the same sliced in
`out/print/sliced/`, which is what this checks: one plate, the P2S's own
start sequence, no slicer warnings, objects in order and in their filaments,
and for the tub's top no support touching a close fit.

    cd clients/firmware/enclosure && uv run python -m huxcase.slice

The tub is two pieces, glued (hx1.LAP_H): a grey base, and a frosted top
printed for clarity (CLARITY_*). The filament changes between the pieces,
never inside one: a change mid-print left the weak layer, where supports
snapped and the case parted.

Uses Bambu Studio's command line (the macOS app). Given a system preset file,
it neither follows the preset's `inherits` chain all the way nor reads its
`include`d templates (the P2S keeps its start, end and filament-change
G-code there), and silently fills the gaps with generic defaults: a 200 mm
bed, `G28` for a start sequence. So the presets are flattened here first,
with their includes, and loaded complete. Run `python -m huxcase.printability
--stl` before this: it slices the posed STLs in out/print.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from build123d import Pos

from huxcase import printability as pr
from huxcase.export import OUT

APP = Path("/Applications/BambuStudio.app/Contents")
PROFILES = APP / "Resources/profiles/BBL"
MACHINE, PROCESS = "Bambu Lab P2S 0.4 nozzle", "0.20mm Standard @BBL P2S"
# Mario's frosted spool is transparent PETG (2026-10-11); the rest are PLA.
# Bambu's own preset for translucent PETG on the P2S is the starting point.
PLA, PETG = "Bambu PLA Basic @BBL P2S", "Bambu PETG Translucent @BBL P2S 0.4 nozzle"
# The bumper's TPU runs from the external spool (the AMS can't feed it).
# Mario's is Overture TPU 95A (2026-10-12): standard flow, so Bambu's 95A
# preset (3.6 mm3/s, 230 C), not the HF one (12 mm3/s would starve it).
TPU = "Bambu TPU 95A @BBL P2S"
TREE_SUPPORTS = {
    "enable_support": "1",
    "support_type": "tree(auto)",
    "support_on_build_plate_only": "0",  # "Everywhere": they grow from the part's own floor
    # Every bridge gets support too (the preset's max_bridge_length 0): left
    # bare under support blockers, overhangs in the first tub printed in
    # mid-air and failed (Mario, 2026-10-11). The top prints on its back and
    # keeps support out of its close fits by its shape
    # (printability.support_keep_outs, checked in the G-code).
    # Organic branches, no walls of their own: 25 min and 7 g less on the
    # tub's plate than hybrid ones with two walls (sliced, 2026-10-11). The
    # walls were for branches that snapped at an AMS change, now gone.
    "support_style": "tree_organic",
    "tree_support_wall_count": "0",
    # PETG welds to its supports at the standard 0.2 gap; 0.25 lets the
    # top's come away (the grey base's too, no harm).
    "support_top_z_distance": "0.25",
    "support_bottom_z_distance": "0.25",
}
# No brim: the top lies on its whole back wall and the base on its floor.
# (Standing on its seam the top had a 1.1 mm foot, and an "outer only" brim
# still ran inside its open U, along the edge that slides over the lip.)
NO_BRIM = {"brim_type": "no_brim"}
# The frosted top, for an even glow as clear as its PETG gets, after Bambu's
# translucent PLA/PETG guide (wiki: knowledge-sharing/transparent-petg, its
# PETG settings read off its screenshots, 2026-10-11): dry filament first;
# one wall and 100 % rectilinear infill, no top or bottom shells (no sparse
# grid to show in the glow, fewer line boundaries); wide infill lines; slow.
# Its 270 C, flow 1.01 and fans off strung the first clear top (damp, at
# 40 mm/s), so the heat, flow and fan went back toward the spool and
# Bambu's preset (2026-10-12): Mario asked for "even, not glass" and above
# all a print that succeeds without strings. Kept from the standard
# profile: 0.2 layers (the fits are tuned there; the guide says 0.1, about
# twice the time) and the walls' 0.42 lines (fine features keep printing).
#
# The base and the top share one plate (Mario, 2026-10-11), so the process
# is the plate's: the base prints solid too (stronger, a little slower).
# Everything that must differ goes with the frosted filament instead, which
# only the top uses: heat, flow, fan, retraction and speed (a volumetric cap
# of 0.5 x 0.2 x CLARITY_SPEED). Not the
# objects' own settings: Bambu Studio did not apply those for Mario.
CLARITY_SPEED = 25  # forum and makers' guides for clear PETG: 20 to 30 mm/s (2026-10-12)
CLARITY_PROCESS = {
    "wall_loops": "1",
    "top_shell_layers": "0",
    "bottom_shell_layers": "0",
    "sparse_infill_density": "100%",
    # Every layer the same way, 0 deg. Bambu's "Rectilinear" (the guide's
    # pick) turns 90 deg each layer, and through clear PETG the stack showed
    # as a crosshatch in Mario's test corner (2026-10-11).
    "sparse_infill_pattern": "alignedrectilinear",
    "internal_solid_infill_pattern": "alignedrectilinear",
    "infill_direction": "0",
    # Travel stays inside the part, so PETG's strings land inside, not across
    # the outside faces.
    "reduce_crossing_wall": "1",
    **{
        k: "0.5"
        for k in (
            "sparse_infill_line_width",
            "internal_solid_infill_line_width",
            "top_surface_line_width",
            "skin_infill_line_width",
            "skeleton_infill_line_width",
        )
    },
}
CLARITY_FILAMENT = {  # on the PETG preset
    # Mario's spool, ElitePETG Transparent, says 230-240 C at 50-100 mm/s
    # (its tag, 2026-10-11); the guide's 270 strung badly, damp, at 40 mm/s.
    # 240, the tag's top: hot enough at 25 mm/s (2.5 mm3/s is little to
    # melt), and every degree over it is more ooze. The first layer 5 over,
    # for the bed.
    "nozzle_temperature": "240",
    "nozzle_temperature_initial_layer": "245",  # preset 250
    # About 3 % over the preset's calibrated 0.95, where clear-PETG makers
    # put it so the lines fuse. Not the guide's 1.01: 6 % over, solid for
    # every layer, piles up and drags the nozzle (a maker's corner failed at
    # 1.01 and printed at 0.99), and it fattens the edge that slides over
    # the base's lip.
    "filament_flow_ratio": "0.98",  # preset 0.95
    # The preset's gentle fan, 10 % rising to 30 % on layers under 30 s, not
    # the guide's off: a cooled string snaps instead of drawing out, and the
    # top's small features hold their shape. A frosted glow hides the little
    # clarity it costs.
    "fan_min_speed": "10",  # preset 10
    "fan_max_speed": "30",  # preset 30
    "additional_cooling_fan_speed": "0",  # the side fan, already off in the preset
    # The P2S's own 0.8, not the preset's 0.3: PETG oozes, and 0.3 barely
    # pulls the melt back before each of the ~3000 travels.
    "filament_retraction_length": "0.8",  # preset 0.3
    # Bambu counts a line's rounded ends (0.5 x 0.2 is 0.091 mm2, not 0.1), so
    # this cap runs the infill at 27 mm/s and the 0.42 outer wall at 33
    # (measured in the G-code, 2026-10-12).
    "filament_max_volumetric_speed": f"{0.5 * 0.2 * CLARITY_SPEED:g}",
}
# No variable layer heights (Mario asked, 2026-10-11): Bambu Studio's layer
# height profile file, tried here, landed on the wrong object of a two-object
# plate (one got a mangled profile, the other none). Where it would help is
# the base's bottom 3 mm: its 8 mm corners (R_FACE) leave the bed almost
# flat, and their lowest ~1 mm needs support whatever the layers (the rough
# strip he saw); thin layers there only make it smaller. That is for the GUI
# (Variable layer height on the base). Elsewhere 0.2 suits: the fits are
# tuned there and the lap is counted in layers.

GREY, BLACK, ORANGE, FROSTED = "#8E9089", "#161616", "#FF6A13", "#E4ECEE"
WHITE, RED = "#F4F4F2", "#C12E1F"
# The final colours (Mario, 2026-10-12): a white faceplate, a red base, the
# clear PETG top, the black grille, the orange lever. His AMS slots 1 to 4:
# A1 the clear PETG, A2 black, A3 orange, A4 red (Bambu Studio, 2026-10-12).
AMS = (FROSTED, BLACK, ORANGE, RED)
TPU_BLACK = "#101010"  # the external spool
FILAMENT = {FROSTED: (PETG, "PETG"), TPU_BLACK: (TPU, "TPU")}  # by colour; the rest PLA


def _filament(colour: str) -> tuple[str, str]:
    """(preset, filament type) for a plate's colour."""
    return FILAMENT.get(colour, (PLA, "PLA"))


@dataclass(frozen=True)
class Plate:
    """A project: STLs from out/print, the project settings over the presets,
    its filaments, and per STL its filament slot (1-based; default 1) and
    its place on the bed (default: Bambu's arrange)."""

    name: str
    stls: tuple[str, ...]
    settings: dict
    colours: tuple[str, ...] = (GREY,)
    slots: dict = field(default_factory=dict)
    place: dict = field(default_factory=dict)
    clear: int = 0  # the filament slot printed for clarity, 0: none
    # STLs (of `stls`) assembled into one object, each part in its own
    # filament slot: a piece printed in two colours at once (the badge).
    parts: dict = field(default_factory=dict)


TOP, BASE = "hx1_10_tub_top", "hx1_03_tub_base"
TEST_BASE, TEST_TOP = "hx1_11_test_base", "hx1_12_test_top"
PLATES = [
    Plate(
        "1_white_faceplate_bridge_pin",
        (
            "hx1_01_faceplate",
            "hx1_07_bridge",
            *["hx1_05_ptt_pin"] * 3,  # two spares: it is thin and lies on a narrow base
        ),
        {},
        (WHITE,),
    ),
    Plate("2_black_grille", ("hx1_02_grille_plug",), {}, (BLACK,)),
    # The lever and its plungers, all that the talk switch's fix changed
    # (2026-10-13): one small plate, nothing else to reprint.
    Plate(
        "3_orange_lever_plungers",
        (
            "hx1_04_ptt_bar",
            "hx1_06_plunger",
            "hx1_08_plunger_short",
            "hx1_09_plunger_long",
            "hx1_18_plunger_shortest",
        ),
        {},
        (ORANGE,),
    ),
    # The tub, both halves, by object: the frosted top first (its first
    # layers come out clean, no grey left in the nozzle), then the base.
    # 94 mm apart in y (the top lies on its back, 68 mm deep): the toolhead
    # (72 mm round) and the gantry, 32.5 mm over the nozzle and lower than
    # either half, never pass over a printed one (Bambu Studio checks both).
    Plate(
        "4_tub_base_and_top",
        (TOP, BASE),
        TREE_SUPPORTS
        | NO_BRIM
        | CLARITY_PROCESS
        # No prime tower: by object, the AMS purges into the chute (the
        # preset's tower showed in Mario's Bambu Studio).
        | {"print_sequence": "by object", "enable_prime_tower": "0"},
        AMS,
        slots={TOP: 1, BASE: 4},
        place={TOP: (128.0, 195.0), BASE: (128.0, 50.0)},
        clear=1,
    ),
    # Both halves' back right corner round the seam (printability
    # TEST_CORNER), the same way and settings, ~20 min: push them together
    # dry, and hold the frosted one to a light, before the long print.
    Plate(
        "5_test_corner",
        (TEST_TOP, TEST_BASE),
        TREE_SUPPORTS
        | NO_BRIM
        | CLARITY_PROCESS
        | {"print_sequence": "by object", "enable_prime_tower": "0"},
        AMS,
        slots={TEST_TOP: 1, TEST_BASE: 4},
        place={TEST_TOP: (128.0, 190.0), TEST_BASE: (128.0, 60.0)},
        clear=1,
    ),
]
PLATES.append(
    # The top alone, clear: the frosted PETG with the clarity settings, on its
    # back, for the white base and red top Mario has (2026-10-12).
    Plate(
        "8_tub_top_clear",
        (TOP,),
        TREE_SUPPORTS | NO_BRIM | CLARITY_PROCESS,
        (FROSTED,),
        clear=1,
    )
)
PLATES.append(
    # The top alone in red PLA, standard settings, to go on the white base
    # Mario has (2026-10-11, after the clear PETG top strung).
    Plate("7_tub_top_pla", (TOP,), TREE_SUPPORTS | NO_BRIM, (RED,))
)
PLATES.append(
    # The base alone in red PLA, standing, for the final colours with a top
    # already printed (2026-10-12).
    Plate("11_tub_base_red", (BASE,), TREE_SUPPORTS | NO_BRIM, (RED,))
)
# The TPU bumper (huxcase.bumper): on its front, no supports (every wall
# vertical; the windows over USB-C and the rocker are short bridges). Solid:
# four walls make the 1.5 mm sides all perimeter and the corners fill.
BUMPER, BUMPER_RING = "hx1_13_bumper", "hx1_14_bumper_ring"
TPU_PROCESS = {
    "wall_loops": "4",
    "sparse_infill_density": "100%",
    "sparse_infill_pattern": "zig-zag",  # the preset's grid isn't allowed at 100 %
}
PLATES.append(Plate("9_bumper_tpu", (BUMPER,), NO_BRIM | TPU_PROCESS, (TPU_BLACK,)))
# Its front 8 mm, to check the outline and the front lip's seat first.
PLATES.append(Plate("10_bumper_test_ring", (BUMPER_RING,), NO_BRIM | TPU_PROCESS, (TPU_BLACK,)))
# The face art (huxcase.face_art), glued on: one plate, printed by object
# (Mario, 2026-10-12), in an order that changes filament twice: the badge
# (black, then its raised orange letters above it: one change, at one
# height), the orange dots, the blue dots, then the stencil in that blue.
# Bambu Studio keeps every object the toolhead's 72 mm from the others, low
# or not: the three small ones across the back, about 80 mm apart, the
# stencil (7 mm, the tallest) at the front and last. By object there is
# no prime tower: each change purges into the chute. 0.08 mm layers: the
# dots' domes get ten, and the letters start on a layer line (1.0 mm).
SKY = "#3FA9E5"
BADGE_BODY, BADGE_LETTERS = "hx1_15_face_badge_body", "hx1_15_face_badge_letters"
DOTS_HL, DOTS_OA, STENCIL = "hx1_16_face_dot_h_l", "hx1_16_face_dot_o_a", "hx1_17_face_stencil"
PLATES.append(
    Plate(
        "12_face_art",
        (BADGE_BODY, BADGE_LETTERS, DOTS_HL, DOTS_OA, STENCIL),
        {
            "print_sequence": "by object",
            "enable_prime_tower": "0",
            "layer_height": "0.08",
            # the dots' 3.2 mm feet stay 3.2 (the preset's 0.15 shrinks them)
            "elefant_foot_compensation": "0",
            # the letters' 0.7 mm strokes print as lines, not collapsed loops
            "wall_generator": "arachne",
        },
        # Mario's AMS (2026-10-12): A2 black, A3 orange, A4 blue.
        (BLACK, ORANGE, SKY),
        slots={DOTS_HL: 2, DOTS_OA: 3, STENCIL: 3},
        place={
            BADGE_BODY: (30.0, 220.0),
            DOTS_HL: (128.0, 220.0),
            DOTS_OA: (226.0, 220.0),
            STENCIL: (128.0, 60.0),
        },
        parts={BADGE_BODY: 1, BADGE_LETTERS: 2},
    )
)
# Plates whose close fits no support may enter: STL, its printed piece.
KEEP_OUT = {
    "4_tub_base_and_top": (TOP, "tub_top"),
    "5_test_corner": (TEST_TOP, "test_top"),
    "7_tub_top_pla": (TOP, "tub_top"),
    "8_tub_top_clear": (TOP, "tub_top"),
}

BED = "Textured PEI Plate"  # the plate the P2S ships with; pick yours in Bambu Studio if not


def _clarity(cfg: dict, slot: int) -> None:
    """CLARITY_FILAMENT on one filament slot (1-based) of a project config.
    Some filament settings hold one entry per filament, some one per
    filament and nozzle variant (Standard, High Flow, ...), mapped to their
    filament by `filament_self_index`."""
    owner = cfg["filament_self_index"]
    for key, value in CLARITY_FILAMENT.items():
        if len(cfg[key]) == len(owner):
            cfg[key] = [
                value if o == str(slot) else v for v, o in zip(cfg[key], owner, strict=True)
            ]
        else:
            cfg[key][slot - 1] = value


def _resolve(kind: str, name: str) -> dict:
    """A system preset with its whole `inherits` chain and its `include`s."""
    d = json.loads((PROFILES / kind / f"{name}.json").read_text())
    out = _resolve(kind, d["inherits"]) if d.get("inherits") else {}
    for inc in d.get("include", []):
        tpl = json.loads((PROFILES / kind / f"{inc}.json").read_text())
        out.update(
            {k: v for k, v in tpl.items() if k not in ("name", "type", "from", "instantiation")}
        )
    out.update(d)
    out.pop("include", None)
    out["inherits"] = ""  # complete: nothing left to inherit
    return out


def _luminance(colour: str) -> float:
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _flush(colours: tuple[str, ...]) -> list[str]:
    """Purge volumes (mm3) from each filament to each other, row = from: the
    base 140, more going light after dark (black to orange about 480), a
    little going dark. The presets carry a 4 x 4 matrix, and a plate with
    another filament count fails to slice ("Flush volumes matrix do not match
    to the correct size")."""
    out = []
    for a in colours:
        for b in colours:
            d = _luminance(b) - _luminance(a)
            out.append("0" if a == b else str(round(140 + 800 * max(d, 0) + 60 * max(-d, 0))))
    return out


def _presets(work: Path, colours: tuple[str, ...] = ("",)) -> list[str]:
    """Command-line arguments loading the complete P2S presets from `work`,
    one filament per colour (_filament)."""
    paths = {}
    wanted = [("machine", MACHINE), ("process", PROCESS)]
    wanted += [("filament", f) for f in (PLA, PETG, TPU)]
    for kind, name in wanted:
        paths[name] = work / f"{kind}_{len(paths)}.json"
        paths[name].write_text(json.dumps(_resolve(kind, name), indent=1))
    return [
        "--load-settings",
        f"{paths[MACHINE]};{paths[PROCESS]}",
        "--load-filaments",
        ";".join(str(paths[_filament(c)[0]]) for c in colours),
    ]


def _no_warnings(out_dir: Path) -> None:
    """The slicer's own warnings (what Bambu Studio shows in its window, such
    as a floating cantilever) come back empty."""
    result = json.loads((out_dir / "result.json").read_text())
    warnings = [p.get("warning_message", "") for p in result.get("sliced_plates", [])]
    assert not any(warnings), f"Bambu Studio warns: {warnings}"


def _run(args: list[str], work: Path) -> None:
    """Bambu Studio's command line, with its settings and temp files kept in `work`."""
    res = subprocess.run(
        [str(APP / "MacOS/BambuStudio"), "--datadir", str(work / "data"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=work,
    )
    if res.returncode != 0:
        tail = "\n".join((res.stdout + res.stderr).splitlines()[-25:])
        raise RuntimeError(f"BambuStudio failed ({res.returncode}):\n{tail}")


def _changed(plate: Plate) -> list[str]:
    """Bambu Studio's list of what a project changes from its system presets:
    the process, then each filament, then the printer, ";"-joined keys.
    The GUI loads the named system presets and applies only these keys on
    top; the command line uses the whole config. Left empty, Mario's Bambu
    Studio printed the defaults (2026-10-11: no supports, a prime tower, 15 %
    grid infill in the clear test corner) while every check here passed."""
    out = [";".join(sorted(plate.settings))] + [""] * len(plate.colours) + [""]
    if plate.clear:
        out[plate.clear] = ";".join(sorted(CLARITY_FILAMENT))
    return out


def gui_settings(project: Path, plate: Plate) -> None:
    """The project tells Bambu Studio's window about every setting it changes
    (_changed), and each holds the value the slice here used."""
    with zipfile.ZipFile(project) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    diff = cfg["different_settings_to_system"]
    assert diff == _changed(plate), f"{project.name}: changed {diff}"
    for key, value in plate.settings.items():
        assert cfg[key] == value, f"{project.name}: {key} {cfg[key]} != {value}"
    if plate.clear:
        owner = cfg["filament_self_index"]
        for key, value in CLARITY_FILAMENT.items():
            got = cfg[key]
            if len(got) == len(owner):  # one per filament and nozzle variant
                mine = [v for v, o in zip(got, owner, strict=True) if o == str(plate.clear)]
            else:
                mine = [got[plate.clear - 1]]
            assert set(mine) == {value}, f"{project.name}: {key} {got}"


def _objects(model_settings: str) -> list[tuple[str, str, str]]:
    """(object id, STL name, its whole <object> block) in model_settings.config."""
    blocks = re.findall(r'(<object id="(\d+)">.*?</object>)', model_settings, re.S)
    found = [re.search(r'key="name" value="([^"]+)"', b).group(1) for b, _ in blocks]
    return [
        (oid, name.removesuffix(".stl"), b) for (b, oid), name in zip(blocks, found, strict=True)
    ]


def slice_plate(plate: Plate, print_dir: Path, out_dir: Path, proj_dir: Path) -> Path:
    """Lay the plate out with the complete P2S presets, set the plate's own
    settings (supports, bed, colours, clarity, each object's filament and
    place) in the project, keep that project (proj_dir, not
    sliced), then slice it into out_dir."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "data").mkdir()
        files = [str(print_dir / f"{s}.stl") for s in plate.stls if s not in plate.parts]
        if plate.parts:
            # One object from the parts, made with the plate's own filaments
            # (a 3mf with another filament count crashes the next step).
            _run(
                [
                    *_presets(work, plate.colours),
                    "--assemble",
                    "--outputdir",
                    str(work),
                    "--export-3mf",
                    "parts.3mf",
                    *[str(print_dir / f"{s}.stl") for s in plate.parts],
                ],
                work,
            )
            files.insert(0, str(work / "parts.3mf"))
        _run(
            [
                *_presets(work, plate.colours),
                "--orient",
                "0",
                "--arrange",
                "1",
                "--outputdir",
                str(work),
                "--export-3mf",
                "draft.3mf",
                *files,
            ],
            work,
        )
        ids: dict[str, str] = {}

        def settings(t: str) -> str:
            cfg = json.loads(t)
            cfg.update(plate.settings)
            cfg["filament_colour"] = list(plate.colours)
            n = len(plate.colours)
            if n > 1 and len(cfg["flush_volumes_matrix"]) != n * n:
                cfg["flush_volumes_matrix"] = _flush(plate.colours)
                cfg["flush_volumes_vector"] = ["140"] * (2 * n)
            if plate.clear:
                _clarity(cfg, plate.clear)
            cfg["curr_bed_type"] = BED
            cfg["different_settings_to_system"] = _changed(plate)
            return json.dumps(cfg, indent=4)

        def model(t: str) -> str:
            if plate.parts:
                first = next(iter(plate.parts))
                t = t.replace('key="name" value="Assembly"', f'key="name" value="{first}.stl"', 1)
            for part, slot in plate.parts.items():
                t, n = re.subn(
                    rf'(<metadata key="name" value="{part}\.stl"/>.*?key="extruder" value=")\d+"',
                    rf'\g<1>{slot}"',
                    t,
                    count=1,
                    flags=re.S,
                )
                assert n == 1, f"{plate.name}: no part {part}"
            for oid, name, block in _objects(t):
                ids[name] = oid
                slot = plate.slots.get(name, 1)
                t = t.replace(
                    block,
                    block.replace('key="extruder" value="1"', f'key="extruder" value="{slot}"'),
                )
            return t

        def layout(t: str) -> str:
            for name, (x, y) in plate.place.items():
                item = re.search(rf'<item objectid="{ids[name]}"[^>]*transform="([^"]+)"', t)
                z = item.group(1).split()[-1]
                t = t.replace(
                    item.group(0),
                    item.group(0).replace(item.group(1), f"1 0 0 0 1 0 0 0 1 {x} {y} {z}"),
                )
            # By object, Bambu Studio prints in the build list's order: make it
            # the plate's (the parts, one object, under their first name).
            items = re.findall(r"<item [^>]*/>", t)
            first = next(iter(plate.parts), None)
            want = []
            for st in plate.stls:
                oid = ids.get(first if st in plate.parts else st)
                if oid and oid not in want:
                    want.append(oid)
            rank = {oid: i for i, oid in enumerate(want)}
            ordered = sorted(
                items, key=lambda it: rank.get(re.search(r'objectid="(\d+)"', it).group(1), 99)
            )
            if len(items) > 1:
                start, end = t.index(items[0]), t.index(items[-1]) + len(items[-1])
                sep = t[start + len(items[0]) : t.index(items[1])]
                t = t[:start] + sep.join(ordered) + t[end:]
            return t

        # model_settings first: it names the objects the layout places.
        _rewrite(work / "draft.3mf", work / "named.3mf", {"Metadata/model_settings.config": model})
        project = proj_dir / f"{plate.name}.3mf"
        _rewrite(
            work / "named.3mf",
            project,
            {"Metadata/project_settings.config": settings, "3D/3dmodel.model": layout},
        )
        out = out_dir / f"{plate.name}.gcode.3mf"
        _run(
            ["--slice", "0", "--outputdir", str(out_dir), "--export-3mf", out.name, str(project)],
            work,
        )
        _no_warnings(out_dir)
        return out


def _rewrite(src: Path, dst: Path, edits: dict) -> None:
    """Copy a 3mf, passing the named entries through edit functions (str -> str)."""
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename in edits:
                data = edits[item.filename](data.decode()).encode()
            zo.writestr(item, data)


def gcode_of(project: Path) -> str:
    with zipfile.ZipFile(project) as z:
        name = next(n for n in z.namelist() if n.endswith(".gcode"))
        return z.read(name).decode("utf-8", "replace")


MOVE = re.compile(r"^G[0-3] ")
AXIS = re.compile(r"([XYE])(-?[\d.]+)")


OBJECT = "; start printing object, unique label id:"


def extrusions(gcode: str):
    """(x, y, z, feature, object label) every 0.5 mm along each extruding move."""
    x = y = z = 0.0
    feature, label = "", None
    for ln in gcode.splitlines():
        if ln.startswith("; Z_HEIGHT:"):
            z = float(ln.split(":")[1])
        elif ln.startswith("; FEATURE:"):
            feature = ln.split(":", 1)[1].strip()
        elif ln.startswith(OBJECT):
            label = ln.split(":")[1].strip()
        elif ln.startswith("; stop printing object"):
            label = None
        elif MOVE.match(ln):
            ax = dict(AXIS.findall(ln.split(";")[0]))
            nx, ny = float(ax.get("X", x)), float(ax.get("Y", y))
            if float(ax.get("E", 0)) > 0 and (nx, ny) != (x, y):
                n = max(1, int(((nx - x) ** 2 + (ny - y) ** 2) ** 0.5 / 0.5))
                for k in range(n + 1):
                    yield x + (nx - x) * k / n, y + (ny - y) * k / n, z, feature, label
            x, y = nx, ny


def labels(project: Path) -> dict[str, str]:
    """STL name -> the object's label id in the G-code (slice_info.config)."""
    with zipfile.ZipFile(project) as z:
        info = z.read("Metadata/slice_info.config").decode()
    found = re.findall(r'<object identify_id="(\d+)" name="([^"]+)"', info)
    return {name.removesuffix(".stl"): oid for oid, name in found}


def support_in_keep_outs(
    project: Path, shape, pose: pr.Pose, label: str | None = None
) -> dict[str, int]:
    """How much support of any kind (branches passing through, contact
    layers) the slice puts inside each close fit (pr.support_keep_outs,
    device frame), in 0.5 mm samples. Only contact counted until 2026-10-11:
    then trees grew through the lever's slot and the plunger's tube, clear of
    their walls but there, and in PETG fused to them. The G-code frame goes
    back to the device frame through the piece's print pose (`shape` printed
    in `pose`), matched on its walls. On a plate of several objects, `label`
    picks the piece's own extrusions."""
    gcode = gcode_of(project)
    half = float(re.search(r"; outer_wall_line_width = ([\d.]+)", gcode).group(1)) / 2
    pts = [p[:4] for p in extrusions(gcode) if label is None or p[4] == label]
    model = [(x, y) for x, y, z, f in pts if "wall" in f.lower() and z > 1.0]
    loc = pr.pose_location(shape, pose)
    bb = (loc * shape).bounding_box()
    dx = bb.min.X - min(p[0] for p in model) + half  # walls run a half line inside
    dy = bb.min.Y - min(p[1] for p in model) + half
    inv = loc.inverse()
    sup = []
    for x, y, z, f in pts:
        if f.startswith("Support"):
            v = (inv * Pos(x + dx, y + dy, z)).position
            sup.append((v.X, v.Y, v.Z))
    out = {k: 0 for k in [*pr.support_keep_outs(), *pr.support_keep_out_rounds()]}
    for p in sup:
        for k in pr.in_keep_out(p):
            out[k] += 1
    return out


def check(project: Path, plate: Plate) -> str:
    """What every sliced plate must be: the P2S's own start sequence, no
    pause, one plate; its objects one after another in the plate's order,
    each in its own filament, no prime tower; every filament the type its
    colour asks for (PETG frosted, TPU for the bumper, else PLA), and the
    clarity settings on the frosted one alone. Returns its print time."""
    with zipfile.ZipFile(project) as z:
        plates = [n for n in z.namelist() if n.endswith(".gcode")]
    assert plates == ["Metadata/plate_1.gcode"], f"{project.name}: plates {plates}"
    gcode = gcode_of(project)
    assert "P2S start gcode==" in gcode, f"{project.name}: not the P2S start sequence"
    assert not any(ln.strip() == "M400 U1" for ln in gcode.splitlines()), f"{project.name}: pauses"
    if len(plate.slots) > 1 or plate.parts:
        ids = labels(project)
        first = next(iter(plate.parts), None)  # the parts are one object, by its name
        order = []
        for s in plate.stls:
            oid = ids[first if s in plate.parts else s]
            if oid not in order:
                order.append(oid)
        runs = []  # objects in print order, each once if printed whole in turn
        for ln in gcode.splitlines():
            if ln.startswith(OBJECT) and (not runs or runs[-1] != ln.split(":")[1].strip()):
                runs.append(ln.split(":")[1].strip())
        assert runs == order, f"{project.name}: order {runs}"
        want = [plate.parts.get(s) or plate.slots.get(s, 1) for s in plate.stls]
        want = [n - 1 for i, n in enumerate(want) if i == 0 or n != want[i - 1]]
        loads = [int(n) for n in re.findall(r"^\s*M620 S(\d+)A", gcode, re.M) if n != "65535"]
        changes = [n for i, n in enumerate(loads) if i == 0 or n != loads[i - 1]]
        assert changes == want, f"{project.name}: {loads}"
        assert "; enable_prime_tower = 0" in gcode, f"{project.name}: prime tower on"
    kinds = re.split(r"[;,]", re.search(r"^; filament_type = (.*)$", gcode, re.M).group(1))
    want = [_filament(c)[1] for c in plate.colours]
    assert kinds == want, f"{project.name}: filament types {kinds}"
    if plate.clear:
        for key in ("nozzle_temperature", "filament_max_volumetric_speed", "fan_max_speed"):
            got = re.search(rf"^; {key} = (.*)$", gcode, re.M).group(1).split(",")
            mine = [i + 1 for i, v in enumerate(got) if v == CLARITY_FILAMENT[key]]
            assert mine == [plate.clear], f"{project.name}: {key} {got}"
    return re.search(r"model printing time: ([^;\n]+)", gcode).group(1)


def main() -> int:
    """All projects afresh, or with plate names as arguments only those
    (the others left as they are)."""
    print_dir = OUT / "print"
    out_dir, proj_dir = print_dir / "sliced", print_dir / "projects"
    only = set(sys.argv[1:])
    unknown = only - {p.name for p in PLATES}
    assert not unknown, f"no such plates: {sorted(unknown)}"
    for d in (out_dir, proj_dir):
        if d.exists() and not only:
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    for stale in print_dir.glob("*.3mf"):  # the AMS projects from before the split
        stale.unlink()
    pieces = pr.printed_pieces()
    poses = {p.piece: p for p in pr.POSES}
    for plate in [p for p in PLATES if not only or p.name in only]:
        out = slice_plate(plate, print_dir, out_dir, proj_dir)
        gui_settings(proj_dir / f"{plate.name}.3mf", plate)
        print(f"{plate.name}: {check(out, plate)}")
        if plate.name in KEEP_OUT:
            stl, piece = KEEP_OUT[plate.name]
            label = labels(out)[stl] if len(plate.stls) > 1 else None  # one object: no labels
            found = support_in_keep_outs(out, pieces[piece], poses[piece], label)
            print(f"    support inside close fits: {found}")
            assert not any(found.values()), f"{out.name}: support inside {found}"
    for stray in [*out_dir.glob("*.gcode"), *out_dir.glob("result.json")]:
        stray.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
