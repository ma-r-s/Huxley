"""Slice the HX-1 print set into ready-to-print Bambu Studio projects for
Mario's P2S (0.4 nozzle, PLA): one project per filament colour, each a
.gcode.3mf to send to the printer as it is. The tub's project pauses at the
frosted layer for the filament swap.

    cd clients/firmware/enclosure && uv run python -m huxcase.slice   # out/print/sliced/

Uses Bambu Studio's command line (the macOS app) and its own P2S presets.
The command line wants complete preset files, so the presets' `inherits`
chains are flattened here first. Run `python -m huxcase.printability --stl`
before this: it slices the posed STLs in out/print.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from huxcase.concepts import hx1
from huxcase.export import OUT

APP = Path("/Applications/BambuStudio.app/Contents")
PROFILES = APP / "Resources/profiles/BBL"
MACHINE, PROCESS, FILAMENT = (
    "Bambu Lab P2S 0.4 nozzle",
    "0.20mm Standard @BBL P2S",
    "Bambu PLA Basic @BBL P2S",
)
TREE_SUPPORTS = {
    "enable_support": "1",
    "support_type": "tree(auto)",
    "support_on_build_plate_only": "0",  # "Everywhere": they grow from the tub's own floor
}
LAYER = 0.2
# The first frosted layer: the printer pauses before printing it.
PAUSE_Z = round(hx1.BACK_FROST_Z + LAYER, 2)

GREY, BLACK, ORANGE = "#8E9089", "#161616", "#FF6A13"
# (project, STLs from out/print, process overrides and colour, pause height or None)
PLATES = [
    (
        "1_grey_faceplate_bridge_pin_plunger",
        ["hx1_01_faceplate", "hx1_07_bridge", "hx1_05_ptt_pin", "hx1_06_plunger"],
        {"filament_colour": [GREY]},
        None,
    ),
    ("2_black_grille", ["hx1_02_grille_plug"], {"filament_colour": [BLACK]}, None),
    ("3_orange_lever", ["hx1_04_ptt_bar"], {"filament_colour": [ORANGE]}, None),
    (
        "4_tub_grey_then_frosted",
        ["hx1_03_tub"],
        TREE_SUPPORTS | {"filament_colour": [GREY]},
        PAUSE_Z,
    ),
]


BED = "Textured PEI Plate"  # the plate the P2S ships with; pick yours in Bambu Studio if not
PAUSE_GCODE = "M400 U1"  # Bambu's "wait for the user" (the P2S preset leaves its pause empty)


def _pause_xml(z: float) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n<plate>\n'
        '<plate_info id="1"/>\n'
        f'<layer top_z="{z}" type="1" extruder="1" color="" extra="Swap to frosted" '
        f'gcode="{PAUSE_GCODE}"/>\n<mode value="SingleExtruder"/>\n</plate>\n'
        "</custom_gcodes_per_layer>\n"
    )


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


def slice_plate(name, stls, overrides, pause_z, print_dir: Path, out_dir: Path) -> Path:
    """Lay the plate out with Bambu's own P2S presets, set the plate's own
    settings (supports, bed, pause) in the project, then slice it."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "data").mkdir()
        presets = [
            "--load-settings",
            f"{PROFILES / 'machine' / MACHINE}.json;{PROFILES / 'process' / PROCESS}.json",
            "--load-filaments",
            f"{PROFILES / 'filament' / FILAMENT}.json",
        ]
        files = [str(print_dir / f"{s}.stl") for s in stls]
        _run(
            [
                *presets,
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
        project = work / "project.3mf"
        with (
            zipfile.ZipFile(work / "draft.3mf") as src,
            zipfile.ZipFile(project, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "Metadata/project_settings.config":
                    cfg = json.loads(data)
                    cfg.update(overrides)
                    cfg["curr_bed_type"] = BED
                    cfg["machine_pause_gcode"] = PAUSE_GCODE
                    data = json.dumps(cfg, indent=4).encode()
                if item.filename != "Metadata/custom_gcode_per_layer.xml":
                    dst.writestr(item, data)
            if pause_z is not None:
                dst.writestr("Metadata/custom_gcode_per_layer.xml", _pause_xml(pause_z))
        out = out_dir / f"{name}.gcode.3mf"
        _run(
            ["--slice", "0", "--outputdir", str(out_dir), "--export-3mf", out.name, str(project)],
            work,
        )
        return out


def gcode_of(project: Path) -> str:
    with zipfile.ZipFile(project) as z:
        name = next(n for n in z.namelist() if n.endswith(".gcode"))
        return z.read(name).decode("utf-8", "replace")


def check(project: Path, pause_z: float | None) -> str:
    """The sliced G-code pauses at the frosted layer and nowhere else."""
    lines = gcode_of(project).splitlines()
    pauses = [i for i, ln in enumerate(lines) if ln.strip() == PAUSE_GCODE]
    if pause_z is None:
        assert not pauses, f"{project.name}: unexpected pause"
        return "no pause"
    assert len(pauses) == 1, f"{project.name}: {len(pauses)} pauses"
    before = [ln for ln in lines[: pauses[0]] if ln.startswith("; Z_HEIGHT:")][-1]
    assert abs(float(before.split(":")[1]) - pause_z) < 1e-6, before
    return f"pauses before the layer at {pause_z} mm"


def main() -> int:
    print_dir = OUT / "print"
    out_dir = print_dir / "sliced"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for stray in out_dir.glob("*.gcode"):
        stray.unlink()
    for name, stls, overrides, pause_z in PLATES:
        out = slice_plate(name, stls, overrides, pause_z, print_dir, out_dir)
        print(f"{out.name}: {check(out, pause_z)}")
    for stray in out_dir.glob("*.gcode"):  # the command line also leaves the bare G-code
        stray.unlink()
    for stray in out_dir.glob("result.json"):
        stray.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
