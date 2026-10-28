import math
import os
import shutil
import tempfile

import bpy

from harness import baked, check, close, make_plane, setup_cycles, texset


def _pixels(img):
    return list(img.pixels)


def _center(img):
    w, h = img.size
    i = ((h // 2) * w + w // 2) * 4
    return _pixels(img)[i:i + 4]


def _bake(map_type, size="128"):
    sc = bpy.context.scene
    sc.essence_bake_map = map_type
    for o in bpy.context.selected_objects:
        texset(o).bake_size = size
    sc.essence_bake_samples = 4
    sc.essence_bake_margin = 0
    return bpy.ops.essence.bake()


def test_bake_ao_flat_plane(addon):
    ob = make_plane()
    check(_bake('AO') == {'FINISHED'}, "AO bake did not finish")
    img = baked("Plane", "AO")
    check(img is not None, "AO image not created")
    close(_center(img)[0], 1.0, 0.05, "open plane AO")


def test_bake_restores_settings(addon):
    ob = make_plane()
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    margin, selected = sc.render.bake.margin, sc.render.bake.use_selected_to_active
    samples = sc.cycles.samples
    _bake('AO')
    check(sc.render.engine == 'BLENDER_EEVEE', f"engine not restored: {sc.render.engine}")
    check(sc.cycles.samples == samples, "samples not restored")
    check(sc.render.bake.margin == margin, "margin not restored")
    check(sc.render.bake.use_selected_to_active == selected, "selected-to-active not restored")
    check(ob.select_get() and bpy.context.view_layer.objects.active is ob, "selection lost")


def test_bake_world_gradient_cube(addon):
    bpy.ops.mesh.primitive_cube_add(size=2)
    ob = bpy.context.active_object
    ob.name = "Cube"
    check(_bake('WORLD_GRADIENT') == {'FINISHED'}, "gradient bake failed")
    img = baked("Cube", "WorldGradient")
    r = _pixels(img)[0::4]
    check(min(r) < 0.1 and max(r) > 0.9, f"gradient range {min(r):.2f}..{max(r):.2f}")


def test_bake_world_gradient_flat_plane(addon):
    """Zero-height bounds must not crash or produce NaN."""
    make_plane()
    check(_bake('WORLD_GRADIENT') == {'FINISHED'}, "flat gradient bake failed")
    r = _pixels(baked("Plane", "WorldGradient"))[0::4]
    check(all(math.isfinite(v) for v in r), "NaN in flat gradient")


def test_bake_normal_world(addon):
    make_plane()
    check(_bake('NORMAL_WORLD') == {'FINISHED'}, "world normal bake failed")
    px = _center(baked("Plane", "NormalWorld"))
    close(px[0], 0.5, 0.05, "world normal x")
    close(px[1], 0.5, 0.05, "world normal y")
    close(px[2], 1.0, 0.05, "world normal z")


def test_bake_normal_flat_and_wiring(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    check(_bake('NORMAL') == {'FINISHED'}, "normal bake failed")
    px = _center(baked("Plane", "Normal"))
    close(px[0], 0.5, 0.05, "tangent x")
    close(px[1], 0.5, 0.05, "tangent y")
    close(px[2], 1.0, 0.05, "tangent z")
    tree = ob.active_material.node_tree
    nm = tree.nodes.get(addon.nodes.NORMAL_NODE_NAME)
    bump = tree.nodes.get(addon.nodes.BUMP_NODE_NAME)
    check(nm is not None and nm.inputs['Color'].is_linked, "normal map not fed by the bake")
    check(bump.inputs['Normal'].is_linked and
          bump.inputs['Normal'].links[0].from_node.name == nm.name, "normal map not into bump")
    check(bump.outputs['Normal'].links[0].to_socket.name == 'Normal', "bump not into BSDF")


def test_bake_high_to_low(addon):
    low = make_plane("Low", size=2.0)
    bpy.ops.mesh.primitive_plane_add(size=4.0)
    high = bpy.context.active_object
    high.name = "High"
    high.rotation_euler = (math.radians(20), 0, 0)
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(high)
    bpy.context.scene.collection.objects.unlink(high)
    for o in bpy.context.view_layer.objects:
        o.select_set(o is low)
    bpy.context.view_layer.objects.active = low
    texset(low).bake_high = coll
    texset(low).bake_extrusion = 0.5
    check(_bake('NORMAL') == {'FINISHED'}, "high to low bake failed")
    check(baked("High", "Normal") is None, "high-poly was baked as a target")
    px = _center(baked("Low", "Normal"))
    check(abs(px[1] - 0.5) > 0.1, f"tilted high-poly left no mark in tangent normal: {px}")
    check(px[2] < 0.99, f"normal z should drop below 1: {px}")


def test_export_unity_exr(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    layer = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    layer.inputs["Color B"].default_value = (0.2, 0.4, 0.6, 1.0)
    layer.inputs["Roughness B"].default_value = 0.3
    layer.inputs["Metallic B"].default_value = 0.8
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc = bpy.context.scene
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        sc.essence_bake_margin = 0
        check(bpy.ops.essence.export() == {'FINISHED'}, "export did not finish")
        files = sorted(os.listdir(folder))
        check(len(files) == 3, f"expected 3 files, got {files}")

        def load(suffix):
            p = os.path.join(folder, f"{bpy.path.clean_name(mat.name)}_{suffix}.exr")
            img = bpy.data.images.load(p)
            img.colorspace_settings.name = 'Non-Color'
            return _center(img)

        base = load("BaseMap")
        for got, want, n in zip(base[:3], (0.2, 0.4, 0.6), "rgb"):
            close(got, want, 0.02, f"BaseMap {n}")
        m = load("MaskMap")
        close(m[0], 0.8, 0.02, "MaskMap R metallic")
        close(m[1], 1.0, 0.02, "MaskMap G AO (white without a bake)")
        close(m[2], 1.0, 0.02, "MaskMap B detail")
        close(m[3], 0.7, 0.02, "MaskMap A smoothness")
        n = load("Normal")
        close(n[2], 1.0, 0.05, "Normal z")
        leftovers = [i.name for i in bpy.data.images if i.name.startswith("__essence_")]
        check(not leftovers, f"temp images left behind: {leftovers}")
        nodes = [n_.name for n_ in mat.node_tree.nodes if n_.bl_idname == 'ShaderNodeTexImage'
             and not n_.name.startswith("Essence_Bake_")]
        check(not nodes, f"temp nodes left behind: {nodes}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_export_normal_keeps_baked_normal(addon):
    """A layer with zero height (unconnected-equivalent Bump) must not alter the baked normal."""
    low = make_plane("Low", size=2.0)
    bpy.ops.mesh.primitive_plane_add(size=4.0)
    high = bpy.context.active_object
    high.name = "High"
    high.rotation_euler = (math.radians(20), 0, 0)
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(high)
    bpy.context.scene.collection.objects.unlink(high)
    for o in bpy.context.view_layer.objects:
        o.select_set(o is low)
    bpy.context.view_layer.objects.active = low
    bpy.ops.essence.add_layer()
    sc = bpy.context.scene
    texset(low).bake_high = coll
    texset(low).bake_extrusion = 0.5
    _bake('NORMAL')
    baked_px = _center(baked("Low", "Normal"))
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        sc.essence_bake_margin = 0
        for o in bpy.context.view_layer.objects:
            o.select_set(o is low)
        bpy.context.view_layer.objects.active = low
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed")
        mat = low.active_material
        p = os.path.join(folder, f"{bpy.path.clean_name(mat.name)}_Normal.exr")
        img = bpy.data.images.load(p)
        img.colorspace_settings.name = 'Non-Color'
        exported = _center(img)
        for i, n in enumerate("xyz"):
            close(exported[i], baked_px[i], 0.05, f"exported normal {n} differs from the baked one")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
