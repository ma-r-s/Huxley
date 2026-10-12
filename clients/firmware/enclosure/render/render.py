"""Studio renders of the enclosure concepts (Blender + Cycles).

    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python render/render.py -- out/ out/renders/ [hx1 hx2 ...] [--quick]

Reads out/<id>.glb (part names are "<kind>__<name>"), assigns a material per
kind, renders hero / back / x-ray views per concept plus a family shot.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
QUICK = "--quick" in argv
FAMILY_ONLY = "--family-only" in argv  # skip per-concept views, render the group shot
argv = [a for a in argv if not a.startswith("--")]
SRC = Path(argv[0]) if argv else Path("out")
DST = Path(argv[1]) if len(argv) > 1 else SRC / "renders"
IDS = argv[2:] or ["hx1", "hx2", "hx3", "hx4"]
DST.mkdir(parents=True, exist_ok=True)

SHELL_KINDS = {
    "shell",
    "shell_dark",
    "shell_inner",
    "accent_dark",
    "translucent",
    "rubber",
    "clear",
}

# Section views: the orthographic camera's near clip plane slices the model
# so the mechanism shows in profile. name -> (look, clip_y_mm, centre_xz_mm,
# height_mm, (res_x, res_y)). look "front" = camera at -Y looking +Y.
EXTRA_VIEWS = {
    "hx1": {
        "switch": ("front", 2.5, (26.0, 72.0), 66.0, (1280, 1600)),
        "open_back": ("back", 15.6, (1.5, 52.0), 112.0, (1280, 1600)),
    },
}

FROSTED_BACK_PREVIEW: set[str] = set()  # HX-1 now models its frosted back

# Per-concept camera azimuth for the hero shot (deg, 0 = straight at the front).
HERO_AZ = {"hx1a": -38, "hx1": 36, "hx2": -32, "hx3": -28, "hx4": -40}


def mat(
    name,
    base,
    rough=0.5,
    metal=0.0,
    transmission=0.0,
    emission=None,
    strength=0.0,
    alpha=1.0,
    coat=0.0,
):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*base, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    b.inputs["Transmission Weight"].default_value = transmission
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Alpha"].default_value = alpha
    if emission:
        b.inputs["Emission Color"].default_value = (*emission, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


MATS = {}


def build_materials():
    MATS.update(
        shell=mat("TE grey", (0.42, 0.43, 0.43), 0.5, coat=0.05),
        shell_dark=mat("TE black", (0.025, 0.025, 0.027), 0.45),
        shell_inner=mat("inner grey", (0.3, 0.3, 0.3), 0.6),
        accent=mat("TE orange", (0.95, 0.12, 0.004), 0.4, coat=0.1),
        accent_dark=mat("dark", (0.02, 0.02, 0.02), 0.5),
        translucent=mat("frosted", (0.95, 0.97, 1.0), 0.32, transmission=1.0),
        glow=mat(
            "light ring",
            (0.9, 0.96, 1.0),
            0.35,
            transmission=0.6,
            emission=(0.45, 0.82, 1.0),
            strength=12.0,
        ),
        clear=mat("clear", (1, 1, 1), 0.05, transmission=1.0),
        rubber=mat("rubber", (0.015, 0.015, 0.015), 0.9),
        screw=mat("steel", (0.6, 0.6, 0.62), 0.3, metal=1.0),
        pcb=mat("pcb", (0.02, 0.1, 0.32), 0.35),
        pcb_green=mat("pcb green", (0.02, 0.22, 0.06), 0.35),
        metal=mat("nickel", (0.7, 0.7, 0.72), 0.25, metal=1.0),
        black=mat("plastic black", (0.02, 0.02, 0.02), 0.4),
        white=mat("led", (0.9, 0.9, 0.85), 0.3),
        led=mat("led glow", (0.9, 0.95, 1.0), 0.3, emission=(0.45, 0.8, 1.0), strength=90.0),
        speaker=mat("speaker", (0.06, 0.06, 0.065), 0.45, metal=0.3),
        cone=mat("cone", (0.015, 0.015, 0.015), 0.85),
        cell=mat("cell", (0.12, 0.36, 0.3), 0.35, coat=0.6),
        switch=mat("switch", (0.08, 0.08, 0.09), 0.4),
        stem=mat("stem", (0.85, 0.5, 0.15), 0.4),
        wire_black=mat("wire black", (0.02, 0.02, 0.02), 0.5),
        wire_red=mat("wire red", (0.55, 0.02, 0.02), 0.45),
        wire_white=mat("wire white", (0.8, 0.8, 0.75), 0.5),
        ghost=mat("ghost", (0.8, 0.84, 0.9), 0.25, alpha=0.07),
    )


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build_materials()
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    sc.cycles.device = "GPU"
    sc.cycles.samples = 48 if QUICK else 110
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = (1000, 800) if QUICK else (1600, 1280)
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    sc.view_settings.exposure = -1.0
    world = bpy.data.worlds.new("w")
    sc.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.82, 0.82, 0.8, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.06


def studio(size: float):
    """Seamless sweep (floor curving up into a back wall) + area lights."""
    import bmesh

    s = size
    floor_len, cove_r, wall_h, width = 30 * s, 6 * s, 30 * s, 60 * s
    # Floor to y = 4s, quarter-circle cove, then the back wall.
    arc = [math.radians(-90 + 90 * i / 16) for i in range(17)]
    prof = [(-floor_len, 0.0)]
    prof += [(4 * s + cove_r * math.cos(a), cove_r + cove_r * math.sin(a)) for a in arc]
    prof.append((4 * s + cove_r, wall_h))
    me = bpy.data.meshes.new("sweep")
    bm = bmesh.new()
    rows = []
    for x in (-width / 2, width / 2):
        rows.append([bm.verts.new((x, y, z)) for y, z in prof])
    for i in range(len(prof) - 1):
        bm.faces.new((rows[0][i], rows[0][i + 1], rows[1][i + 1], rows[1][i]))
    bm.to_mesh(me)
    bm.free()
    sweep = bpy.data.objects.new("sweep", me)
    bpy.context.scene.collection.objects.link(sweep)
    for poly in me.polygons:
        poly.use_smooth = True
    me.materials.append(mat("cyc", (0.74, 0.74, 0.72), 0.9))

    def area(name, loc, energy, size_, color=(1, 1, 1)):
        bpy.ops.object.light_add(type="AREA", location=loc)
        lamp = bpy.context.object
        lamp.name = name
        lamp.data.energy = energy
        lamp.data.size = size_
        lamp.data.color = color
        direction = Vector((0, 0, 0.4 * s)) - Vector(loc)
        lamp.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        return lamp

    k = s * s  # energy falls off with distance^2, and distances scale with s
    area("key", (-2.2 * s, -2.6 * s, 3.0 * s), 520 * k, 2.4 * s, (1.0, 0.97, 0.92))
    area("fill", (2.8 * s, -1.6 * s, 1.4 * s), 110 * k, 3.0 * s, (0.9, 0.95, 1.0))
    area("rim", (1.2 * s, 2.6 * s, 2.8 * s), 380 * k, 1.6 * s)
    area("top", (0, 0, 4.0 * s), 90 * k, 3.0 * s)


def import_concept(cid: str, x_offset: float = 0.0):
    """Import out/<cid>.glb standing on z = 0, its left edge at x_offset if given."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(SRC / f"{cid}.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    objs = [o for o in new if o.type == "MESH"]
    roots = [o for o in new if o.parent is None]
    bpy.context.view_layer.update()
    lo, hi = bounds(objs)
    dx = (x_offset - lo.x) if x_offset is not None else 0.0
    for r in roots:
        r.location.z -= lo.z
        r.location.x += dx
    bpy.context.view_layer.update()
    for o in objs:
        kind, _, rest = o.name.partition("__")
        m = MATS["led"] if rest.startswith("board_led_") else MATS.get(kind, MATS["shell"])
        o.data.materials.clear()
        o.data.materials.append(m)
        o["kind"] = kind
        for p in o.data.polygons:
            p.use_smooth = True
    return objs, roots


def bounds(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def camera(objs, az_deg, el_deg, lens=85, margin=1.15, focus=None):
    lo, hi = focus if focus else bounds(objs)
    c = (lo + hi) / 2
    radius = (hi - lo).length / 2
    sc = bpy.context.scene
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = lens
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    sensor = 36.0
    fov = (
        2 * math.atan(sensor / 2 / lens) * min(1, sc.render.resolution_y / sc.render.resolution_x)
    )
    dist = radius * margin / math.tan(fov / 2)
    az, el = math.radians(az_deg), math.radians(el_deg)
    cam.location = (
        c
        + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * dist
    )
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.dof.use_dof = True
    cam_data.dof.focus_distance = (c - cam.location).length
    cam_data.dof.aperture_fstop = 8.0
    sc.camera = cam
    return cam


def section_caps(on: bool):
    """Where a clip plane exposes a part's inside, show it as flat part colour."""
    for m in bpy.data.materials:
        if not m.use_nodes or "Principled BSDF" not in m.node_tree.nodes:
            continue
        nt = m.node_tree
        out = nt.nodes["Material Output"]
        bsdf = nt.nodes["Principled BSDF"]
        if on and "cap_mix" not in nt.nodes:
            geo = nt.nodes.new("ShaderNodeNewGeometry")
            emit = nt.nodes.new("ShaderNodeEmission")
            col = bsdf.inputs["Base Color"].default_value
            emit.inputs["Color"].default_value = (col[0] * 0.8, col[1] * 0.8, col[2] * 0.8, 1)
            emit.inputs["Strength"].default_value = 1.0
            mix = nt.nodes.new("ShaderNodeMixShader")
            mix.name = "cap_mix"
            nt.links.new(geo.outputs["Backfacing"], mix.inputs["Fac"])
            nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
            nt.links.new(emit.outputs["Emission"], mix.inputs[2])
            nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        elif not on and "cap_mix" in nt.nodes:
            nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])


def section_camera(look, clip_y, centre, height, res):
    """Orthographic camera whose near clip plane is y = clip_y (CAD mm)."""
    sc = bpy.context.scene
    scale = 0.6 if QUICK else 1.0
    sc.render.resolution_x, sc.render.resolution_y = int(res[0] * scale), int(res[1] * scale)
    data = bpy.data.cameras.new("section")
    data.type = "ORTHO"
    data.ortho_scale = height / 1000
    cam = bpy.data.objects.new("section", data)
    sc.collection.objects.link(cam)
    sign = -1 if look == "front" else 1
    dist = 0.5
    cam.location = Vector((centre[0] / 1000, clip_y / 1000 + sign * dist, centre[1] / 1000))
    # Camera looks down its -Z; (90, 0, 0) looks along +Y with +Z up.
    cam.rotation_euler = (math.radians(90), 0, 0 if look == "front" else math.radians(180))
    sc.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
    data.clip_start = dist
    data.clip_end = dist + 0.2
    sc.camera = cam
    return cam


def render(path: Path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    print("wrote", path, flush=True)


def ghost(objs, on: bool):
    for o in objs:
        if o.get("kind") in SHELL_KINDS:
            o.data.materials[0] = MATS["ghost"] if on else MATS[o["kind"]]


def concept_views(cid: str):
    reset()
    objs, _ = import_concept(cid, x_offset=None)
    lo, hi = bounds(objs)
    studio(max(hi - lo))
    az = HERO_AZ.get(cid, -35)
    cam = camera(objs, az, 18)
    render(DST / f"{cid}_hero.png")
    bpy.data.objects.remove(cam)
    cam = camera(objs, 180 - az * 0.9, 22)
    render(DST / f"{cid}_back.png")
    bpy.data.objects.remove(cam)
    ghost(objs, True)
    cam = camera(objs, az * 0.6, 14)
    render(DST / f"{cid}_xray.png")
    ghost(objs, False)
    section_caps(True)
    for name, view in EXTRA_VIEWS.get(cid, {}).items():
        bpy.data.objects.remove(cam)
        cam = section_camera(*view)
        render(DST / f"{cid}_{name}.png")
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = (1000, 800) if QUICK else (1600, 1280)
    if cid in FROSTED_BACK_PREVIEW:
        # Option preview: the same model with the back shell printed in frosted PLA.
        section_caps(False)
        sc.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.06
        for o in objs:
            if o.name.startswith("shell__shell_back"):
                o.data.materials[0] = MATS["translucent"]
        bpy.data.objects.remove(cam)
        cam = camera(objs, 180 - az * 0.9, 22)
        render(DST / f"{cid}_back_frosted.png")


def family():
    reset()
    allobjs, allroots = [], []
    x = 0.0
    for cid in IDS:
        objs, roots = import_concept(cid, x_offset=x)
        lo, hi = bounds(objs)
        x = hi.x + 0.035
        allobjs += objs
        allroots += roots
    lo, hi = bounds(allobjs)
    for r in allroots:
        r.location.x -= (lo.x + hi.x) / 2
    bpy.context.view_layer.update()
    studio(max(hi - lo) * 0.6)
    camera(allobjs, -16 if len(IDS) > 2 else 8, 12, lens=70, margin=1.1)
    name = "family.png" if len(IDS) > 2 else f"compare_{'_'.join(IDS)}.png"
    render(DST / name)


if __name__ == "__main__":
    for cid in IDS if not FAMILY_ONLY else []:
        concept_views(cid)
    if len(IDS) > 1:
        family()
