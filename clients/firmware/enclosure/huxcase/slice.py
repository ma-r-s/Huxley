"""Bambu Studio projects for Mario's P2S (0.4 nozzle, PLA).

- `out/print/HX1_one_plate_AMS.3mf`: what is left after his first prints
  (tub, lever, bridge, spare plungers and pins) on one plate, printed object
  by object, filaments set for his four AMS slots; the tub changes from grey
  to frosted by itself at BACK_FROST_Z. Not sliced: he opens it, slices and
  prints. It is sliced here too, to check it (one plate, the tub last, the
  colour change, no support touching a close fit).
- `out/print/sliced/`: one sliced project per filament colour, for printing
  without the AMS; the tub's pauses at the frosted layer.

    cd clients/firmware/enclosure && uv run python -m huxcase.slice

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
from pathlib import Path

from huxcase import printability as pr
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
    # Bridges up to 20 mm print unsupported (the preset supports every bridge).
    # The close fits also get support blockers (pr.support_blockers): this
    # alone does not keep tree branches out of them.
    "max_bridge_length": "20",
}
LAYER = 0.2
# The first frosted layer: the printer pauses before printing it.
PAUSE_Z = round(hx1.BACK_FROST_Z + LAYER, 2)

GREY, BLACK, ORANGE = "#8E9089", "#161616", "#FF6A13"
# (project, STLs from out/print, process overrides and colour, pause height or
# None). The tub's project also gets the support blockers.
PLATES = [
    (
        "1_grey_faceplate_bridge_pin_plunger",
        [
            "hx1_01_faceplate",
            "hx1_07_bridge",
            *["hx1_05_ptt_pin"] * 3,  # two spares: it is thin and lies on a narrow base
            "hx1_06_plunger",
            "hx1_08_plunger_long05",
            "hx1_09_plunger_long10",
        ],
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


FROSTED = "#E4ECEE"
# Mario's AMS, one colour per slot (2026-10-11).
AMS = (("grey", GREY), ("black", BLACK), ("orange", ORANGE), ("frosted", FROSTED))
SLOT = {name: i + 1 for i, (name, _) in enumerate(AMS)}
AMS_PROJECT = "HX1_one_plate_AMS"
# Left to print after Mario's first prints (faceplate, grille, a pin and a
# plunger, 2026-10-11). The small grey pieces go side by side as one object.
GREY_GROUP = ("bridge", "plunger_long05", "plunger_long10", "ptt_pin", "ptt_pin")
# Object centres on the 256 x 256 bed. Printed object by object, the toolhead
# must clear what is already done: 72 mm between objects (the P2S's extruder
# clearance), and the tall tub, printed last, away from the rest.
AMS_LAYOUT = {"lever": (36.0, 40.0), "grey_parts": (190.0, 40.0), "tub": (128.0, 200.0)}

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


def _presets(work: Path, filaments: int = 1) -> list[str]:
    """Command-line arguments loading the complete P2S presets from `work`."""
    paths = {}
    for kind, name in (("machine", MACHINE), ("process", PROCESS), ("filament", FILAMENT)):
        paths[kind] = work / f"{kind}.json"
        paths[kind].write_text(json.dumps(_resolve(kind, name), indent=1))
    return [
        "--load-settings",
        f"{paths['machine']};{paths['process']}",
        "--load-filaments",
        ";".join([str(paths["filament"])] * filaments),
    ]


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


def _blocker_stls(work: Path) -> list[str]:
    """The tub's support blockers as STLs, on the bed where the tub STL is."""
    from build123d import export_stl

    from huxcase.shapes import bbox_solid

    pose = next(p for p in pr.POSES if p.piece == "tub")
    loc = pr.pose_location(pr.printed_pieces()["tub"], pose)
    files = []
    for name, box in pr.support_blockers().items():
        path = work / f"blocker_{name}.stl"
        export_stl(loc * bbox_solid(*box), str(path))
        files.append(str(path))
    return files


def _as_blockers(model_settings: bytes) -> bytes:
    """The assembled parts named blocker_* become support blockers."""
    text = model_settings.decode()
    parts = text.split("<part ")
    for i in range(1, len(parts)):
        if 'value="blocker_' in parts[i].split("</part>")[0]:
            assert 'subtype="normal_part"' in parts[i]
            parts[i] = parts[i].replace('subtype="normal_part"', 'subtype="support_blocker"', 1)
    out = "<part ".join(parts)
    assert out.count('subtype="support_blocker"') == len(pr.support_blockers())
    return out.encode()


def slice_plate(name, stls, overrides, pause_z, print_dir: Path, out_dir: Path) -> Path:
    """Lay the plate out with Bambu's own P2S presets, set the plate's own
    settings (supports, bed, pause) in the project, then slice it. A plate
    with a pause (the tub) is assembled with its support blockers."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "data").mkdir()
        presets = _presets(work)
        files = [str(print_dir / f"{s}.stl") for s in stls]
        blockers = pause_z is not None
        if blockers:
            files += _blocker_stls(work)
        _run(
            [
                *presets,
                "--orient",
                "0",
                "--arrange",
                "1",
                *(["--assemble"] if blockers else []),
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
                if blockers and item.filename == "Metadata/model_settings.config":
                    data = _as_blockers(data)
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


def _ams_stls(work: Path) -> dict[str, Path]:
    """The AMS plate's pieces as posed STLs: the tub as two parts, grey below
    BACK_FROST_Z and frosted above, with its support blockers; the lever; the
    small grey pieces in a row."""
    from build123d import Compound, Pos, export_stl

    c = hx1.build()
    parts = {p.name: p.shape for p in c.parts}
    pieces = pr.printed_pieces(c)
    poses = {p.piece: p for p in pr.POSES}
    out: dict[str, Path] = {}

    def save(name, shape):
        out[name] = work / f"{name}.stl"
        export_stl(shape, str(out[name]), tolerance=0.02, angular_tolerance=0.15)

    loc = pr.pose_location(pieces["tub"], poses["tub"])
    save("tub_grey", loc * parts["tub_grey"])
    save("tub_frosted", loc * parts["tub_frosted"])
    for path in _blocker_stls(work):
        out[Path(path).stem] = Path(path)
    save("lever", pr.posed(pieces["ptt_bar"], poses["ptt_bar"]))
    x, row = 0.0, []
    for name in GREY_GROUP:
        shape = pr.posed(pieces[name], poses[name])
        bb = shape.bounding_box()
        row.append(Pos(x - bb.min.X, -bb.center().Y, 0) * shape)
        x += bb.size.X + 6.0
    group = Compound(row)
    save("grey_parts", Pos(-group.bounding_box().center().X, 0, 0) * group)
    return out


def _rewrite(src: Path, dst: Path, edits: dict) -> None:
    """Copy a 3mf, passing the named entries through edit functions (str -> str)."""
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename in edits:
                data = edits[item.filename](data.decode()).encode()
            zo.writestr(item, data)


def _objects(model_settings: str) -> list[tuple[str, str, str]]:
    """(object id, name, its whole <object> block) in model_settings.config."""
    blocks = re.findall(r'(<object id="(\d+)">.*?</object>)', model_settings, re.S)
    return [(oid, re.search(r'key="name" value="([^"]+)"', b).group(1), b) for b, oid in blocks]


def ams_project(out: Path, print_dir: Path) -> Path:
    """The one-plate AMS project (not sliced): see the module docstring."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "data").mkdir()
        presets = _presets(work, len(AMS))
        stl = _ams_stls(work)
        tub_parts = ["tub_grey", "tub_frosted", *(n for n in stl if n.startswith("blocker_"))]
        _run(
            [
                *presets,
                "--orient",
                "0",
                "--arrange",
                "1",
                "--assemble",
                "--outputdir",
                str(work),
                "--export-3mf",
                "tub_draft.3mf",
                *(str(stl[n]) for n in tub_parts),
            ],
            work,
        )

        def tub_settings(t: str) -> str:
            t = _as_blockers(t.encode()).decode()
            frosted = re.search(
                r'<part [^>]*>\s*<metadata key="name" value="tub_frosted.stl"/>.*?</part>', t, re.S
            )
            block = frosted.group(0)
            t = t.replace(
                block,
                block.replace(
                    'key="extruder" value="1"', f'key="extruder" value="{SLOT["frosted"]}"'
                ),
            )
            return t.replace(
                '<metadata key="name" value="Assembly"/>', '<metadata key="name" value="tub"/>', 1
            )

        _rewrite(
            work / "tub_draft.3mf",
            work / "tub.3mf",
            {"Metadata/model_settings.config": tub_settings},
        )
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
                "plate_draft.3mf",
                str(work / "tub.3mf"),
                str(stl["grey_parts"]),
                str(stl["lever"]),
            ],
            work,
        )
        ids: dict[str, str] = {}

        def project(t: str) -> str:
            cfg = json.loads(t)
            cfg.update(
                {
                    "filament_colour": [colour for _, colour in AMS],
                    "print_sequence": "by object",
                    "enable_prime_tower": "0",  # by object, the AMS flushes into the chute
                    "enable_support": "0",  # only the tub gets support, as its own setting
                    "curr_bed_type": BED,
                }
            )
            return json.dumps(cfg, indent=4)

        def model(t: str) -> str:
            for oid, name, block in _objects(t):
                key = name.removesuffix(".stl")
                ids[key] = oid
                new = block
                if key == "lever":
                    new = new.replace(
                        'key="extruder" value="1"', f'key="extruder" value="{SLOT["orange"]}"'
                    )
                if key == "tub":
                    own = "".join(
                        f'    <metadata key="{k}" value="{v}"/>\n'
                        for k, v in TREE_SUPPORTS.items()
                    )
                    new = new.replace(
                        '<metadata key="name" value="tub"/>\n',
                        '<metadata key="name" value="tub"/>\n' + own,
                        1,
                    )
                t = t.replace(block, new)
            assert set(ids) == set(AMS_LAYOUT), ids
            return t

        def layout(t: str) -> str:
            for key, (x, y) in AMS_LAYOUT.items():
                item = re.search(rf'<item objectid="{ids[key]}"[^>]*transform="([^"]+)"', t)
                z = item.group(1).split()[-1]
                t = t.replace(
                    item.group(0),
                    item.group(0).replace(item.group(1), f"1 0 0 0 1 0 0 0 1 {x} {y} {z}"),
                )
            return t

        # model_settings first: it names the objects the layout places.
        _rewrite(
            work / "plate_draft.3mf",
            work / "plate_named.3mf",
            {"Metadata/model_settings.config": model},
        )
        _rewrite(
            work / "plate_named.3mf",
            out,
            {"Metadata/project_settings.config": project, "3D/3dmodel.model": layout},
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


def support_in_keep_outs(project: Path, contact: bool = True, label=None) -> dict[str, int]:
    """How much support the slice puts inside each of the tub's close-fitting
    cuts (pr.support_keep_outs), in 0.5 mm samples. With `contact`, only
    where it touches the part: every support ends on the model in interface
    layers, top and bottom. A tree branch that passes through a hole, clear
    of its walls, touches nothing there and comes out with the rest. The
    G-code frame is put on the device frame by matching the model's walls to
    the tub's outline (the tub's object only, given its `label`; unrotated)."""
    pts = [p[:4] for p in extrusions(gcode_of(project)) if label is None or p[4] == label]
    model = [(x, y) for x, y, z, f in pts if "wall" in f.lower() and z > 1.0]
    gx0, gy0 = min(p[0] for p in model), min(p[1] for p in model)
    bb = (hx1.build().shells["tub"]).bounding_box()
    dx, dy = bb.min.X - gx0 + 0.21, bb.min.Y - gy0 + 0.21  # walls run a half line inside
    kind = "Support interface" if contact else "Support"
    sup = [(x + dx, y + dy, z) for x, y, z, f in pts if f.startswith(kind)]
    out = {}
    for name, (x0, x1, y0, y1, z0, z1) in pr.support_keep_outs().items():
        out[name] = sum(1 for x, y, z in sup if x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1)
    return out


def ams_check(project: Path) -> list[str]:
    """Slice the AMS project as Bambu Studio would, and check what it prints:
    one plate, the P2S's own start sequence, each object in its filament,
    the tub last and changing to frosted at its first frosted layer, support
    only on the tub and none touching its close fits."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "data").mkdir()
        sliced = work / "check.gcode.3mf"
        _run(
            ["--slice", "0", "--outputdir", str(work), "--export-3mf", sliced.name, str(project)],
            work,
        )
        with zipfile.ZipFile(sliced) as z:
            plates = [n for n in z.namelist() if n.endswith(".gcode")]
            info = z.read("Metadata/slice_info.config").decode()
        assert plates == ["Metadata/plate_1.gcode"], f"not one plate: {plates}"
        names = dict(re.findall(r'<object identify_id="(\d+)" name="([^"]+)"', info))
        gcode = gcode_of(sliced)
        assert "P2S start gcode==" in gcode, "not the P2S's start sequence"
        time = re.search(r"model printing time: ([^;\n]+)", gcode).group(1)
        slot = {v: k for k, v in SLOT.items()}
        order, used, supported = [], {}, set()  # used: object -> [(colour, z)]
        z, label, filament = 0.0, None, None
        for ln in gcode.splitlines():
            if ln.startswith("; Z_HEIGHT:"):
                z = float(ln.split(":")[1])
            elif ln.startswith(OBJECT):
                label = names[ln.split(":")[1].strip()].removesuffix(".stl")
                if not order or order[-1] != label:
                    order.append(label)
                colours = used.setdefault(label, [])
                if not colours or colours[-1][0] != slot[filament]:  # changed since its last layer
                    colours.append((slot[filament], round(z, 1)))
            elif ln.startswith("; stop printing object"):
                label = None
            elif ln.startswith("; FEATURE: Support"):
                supported.add(label)
            elif m := re.match(r"M620 S(\d+)A", ln.strip()):
                filament = int(m.group(1)) + 1
        assert order[-1] == "tub", order
        want = {"lever": ["orange"], "grey_parts": ["grey"], "tub": ["grey", "frosted"]}
        for obj, cols in want.items():
            got = [c for c, _ in used[obj]]
            assert got == cols, f"{obj} prints in {got}, not {cols}"
        frost_z = used["tub"][1][1]
        assert abs(frost_z - hx1.BACK_FROST_Z) <= LAYER + 1e-6, frost_z
        assert supported == {"tub"}, supported
        tub_id = next(k for k, v in names.items() if v == "tub")
        touching = support_in_keep_outs(sliced, True, tub_id)
        assert not any(touching.values()), f"support touches {touching}"
        return [
            f"one plate, {time}, objects in order {order}",
            f"filaments per object (colour, from z) {used}",
            f"support only on {sorted(supported)}, touching close fits: {touching}",
        ]


def main() -> int:
    print_dir = OUT / "print"
    out_dir = print_dir / "sliced"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for stray in out_dir.glob("*.gcode"):
        stray.unlink()
    ams = ams_project(print_dir / f"{AMS_PROJECT}.3mf", print_dir)
    print(f"{ams.name}: not sliced (Mario slices it); as sliced here:")
    for line in ams_check(ams):
        print(f"    {line}")
    for name, stls, overrides, pause_z in PLATES:
        out = slice_plate(name, stls, overrides, pause_z, print_dir, out_dir)
        print(f"{out.name}: {check(out, pause_z)}")
        if pause_z is not None:  # the tub: no support touches its close fits
            found = support_in_keep_outs(out)
            print(f"    support touching close fits: {found}")
            print(f"    branches passing through them: {support_in_keep_outs(out, False)}")
            assert not any(found.values()), f"{out.name}: support touches {found}"
    for stray in out_dir.glob("*.gcode"):  # the command line also leaves the bare G-code
        stray.unlink()
    for stray in out_dir.glob("result.json"):
        stray.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
