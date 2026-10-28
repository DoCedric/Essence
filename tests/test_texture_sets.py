"""Texture sets: every object using a material bakes into that material's maps."""

import os
import shutil
import tempfile

import bpy

from harness import check, close, make_plane, texset
from test_bake_quality import _bake, _select


def _px(img, u, v):
    w, h = img.size
    x, y = min(w - 1, int(u * w)), min(h - 1, int(v * h))
    i = (y * w + x) * 4
    return list(img.pixels)[i:i + 4]


def _half(ob, u0):
    """Squeeze the plane's UVs into u0..u0+0.4, v 0.1..0.9."""
    for l in ob.data.uv_layers.active.data:
        u, v = l.uv
        l.uv = (u0 + u * 0.4, 0.1 + v * 0.8)


def _pair(mat_name="Shared"):
    """Two planes sharing one material: A high up with its UVs in the left half, B low with its
    UVs in the right half. A world gradient bake gives A white and B black."""
    a = make_plane("A", size=1.0, location=(-2, 0, 1))
    _half(a, 0.05)
    b = make_plane("B", size=1.0, location=(2, 0, 0))
    _half(b, 0.55)
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    a.data.materials.append(mat)
    b.data.materials.append(mat)
    return a, b, mat


def _gradient(margin):
    sc = bpy.context.scene
    sc.essence_bake_map = 'WORLD_GRADIENT'
    sc.essence_bake_margin = margin
    return bpy.ops.essence.bake()


def test_objects_of_a_material_bake_into_one_image(addon):
    a, b, mat = _pair()
    texset(a).bake_size = "128"
    _select(a)                     # only A is selected: B belongs to the set all the same
    check(_gradient(4) == {'FINISHED'}, "bake failed")
    img = bpy.data.images.get("Shared_WorldGradient")
    check(img is not None, "one image named after the material")
    check(not [i for i in bpy.data.images if i.name in ("A_WorldGradient", "B_WorldGradient")],
          "no image per object")
    check(tuple(img.size) == (128, 128), "the material's size")
    slot = mat.node_tree.nodes["Essence_Bake_WORLD_GRADIENT"]
    check(slot.image is img, "on the material's bake node")
    close(_px(img, 0.25, 0.5)[0], 1.0, 0.02, "A's island")
    close(_px(img, 0.75, 0.5)[0], 0.0, 0.02, "B's island")


def test_the_margin_does_not_cover_the_other_objects(addon):
    """Cycles' margin fills everything a bake did not write, the other objects' islands
    included; a set is baked without it and gets the margin from the compositor."""
    a, b, mat = _pair()
    texset(a).bake_size = "128"
    _select(a)
    check(_gradient(16) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Shared_WorldGradient"]
    for u in (0.06, 0.25, 0.44):
        close(_px(img, u, 0.5)[0], 1.0, 0.02, f"A's island at u={u}")
    for u in (0.56, 0.75, 0.94):
        close(_px(img, u, 0.5)[0], 0.0, 0.02, f"B's island at u={u} (A's margin over it?)")
    # the margin is there: just outside A's island the value continues
    close(_px(img, 0.25, 0.06)[0], 1.0, 0.02, "the margin below A's island")
    close(_px(img, 0.25, 0.94)[0], 1.0, 0.02, "the margin above A's island")
    # and stops: 16 px beyond the island it is black
    close(_px(img, 0.25, 0.0)[0], 1.0, 0.02, "still within 16 px (13 px away)")
    check(bpy.context.scene.render.bake.margin == 16, "the scene's bake margin is put back")
    check(not [n for n in bpy.data.node_groups if n.name.startswith(".Essence Margin")],
          "the margin group is removed")
    check(not [s for s in bpy.data.scenes if s.name.startswith("EssenceFilterTmp")],
          "the throwaway scene is removed")


def test_the_margin_ends_where_a_cycles_margin_would(addon):
    a, b, mat = _pair()
    texset(a).bake_size = "128"
    _select(a)
    check(_gradient(2) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Shared_WorldGradient"]
    close(_px(img, 0.25, 0.09)[0], 1.0, 0.02, "1 px below the island: margin")
    close(_px(img, 0.25, 0.0)[0], 0.0, 0.02, "far below: black")


def test_one_object_keeps_cycles_margin(addon):
    """A set of one object bakes exactly as before, with Cycles' own margin."""
    a = make_plane("Solo", size=1.0, location=(0, 0, 0))
    _half(a, 0.3)
    texset(a).bake_size = "128"
    _select(a)
    calls = []
    original = addon.filters.add_margin
    addon.filters.add_margin = lambda *args: calls.append(args)
    try:
        check(_bake('AO') == {'FINISHED'}, "bake failed")
    finally:
        addon.filters.add_margin = original
    check(not calls, "no compositor margin for a single object")


def test_other_materials_of_the_objects_are_left_alone(addon):
    a, b, mat = _pair()
    # A gets four faces: the left two stay Shared, the right two get another material
    _select(a)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide()
    bpy.ops.object.mode_set(mode='OBJECT')
    other = bpy.data.materials.new("Other")
    other.use_nodes = True
    a.data.materials.append(other)
    for p in a.data.polygons:
        p.material_index = 1 if p.center.x > 0 else 0
    keep = bpy.data.images.new("Keep", 16, 16)
    keep.generated_color = (0.3, 0.3, 0.3, 1.0)
    node = other.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = keep
    other.node_tree.nodes.active = node
    texset(a).bake_size = "128"
    _select(b)
    check(_bake('AO') == {'FINISHED'}, "bake failed")
    close(_px(keep, 0.5, 0.5)[0], 0.3, 0.01, "another material's active image is not written")
    check(other.node_tree.nodes.active == node, "and its active node is put back")
    check(bpy.data.images.get("Other_AO") is None, "Other is not a set of B's: only A has it")
    check(not [i for i in bpy.data.images if i.name.startswith(".Essence Bake Other")],
          "the throwaway image is removed")


def test_each_material_has_its_own_settings(addon):
    a = make_plane("A", size=1.0, location=(-2, 0, 0))
    b = make_plane("B", size=1.0, location=(2, 0, 0))
    texset(a).bake_size = "128"
    texset(b).bake_size = "256"
    for o in (a, b):
        o.select_set(True)
    sc = bpy.context.scene
    sc.essence_bake_map = 'AO'
    sc.essence_bake_samples = 2
    check(bpy.ops.essence.bake() == {'FINISHED'}, "bake failed")
    check(tuple(bpy.data.images["A_Mat_AO"].size) == (128, 128), "A's set is 128")
    check(tuple(bpy.data.images["B_Mat_AO"].size) == (256, 256), "B's set is 256")


def test_an_image_from_before_texture_sets_is_renamed_and_kept(addon):
    a = make_plane("Old", size=1.0)
    bpy.ops.essence.add_layer()
    mat = a.active_material
    old = bpy.data.images.new("Old_AO", 32, 32, float_buffer=True)
    old.colorspace_settings.name = 'Non-Color'
    mat.node_tree.nodes[addon.nodes.map_slot_name('AO')].image = old
    holder = bpy.data.materials.new("Holder")
    holder.use_nodes = True
    ref = holder.node_tree.nodes.new('ShaderNodeTexImage')
    ref.image = old
    _select(a)
    check(_bake("AO", size="128") == {'FINISHED'}, "bake failed")
    check(bpy.data.images.get("Old_AO") is None, "the object-named image is gone")
    img = bpy.data.images[f"{mat.name}_AO"]
    check(ref.image is img, "what used the old image now uses the texture set's image")


def test_uv_island_ids_differ_between_the_objects_of_a_set(addon):
    a, b, mat = _pair()
    texset(a).bake_size = "128"
    sc = bpy.context.scene
    sc.essence_bake_id = 'UV_ISLAND'
    _select(a)
    check(_bake('ID') == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Shared_ID"]
    ca, cb = _px(img, 0.25, 0.5)[:3], _px(img, 0.75, 0.5)[:3]
    check(sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5 > 0.15,
          f"each object's island 0 should get its own color: {ca} {cb}")


def test_hidden_objects_of_a_set_are_baked_and_hidden_again(addon):
    a, b, mat = _pair()
    texset(a).bake_size = "128"
    b.hide_set(True)
    _select(a)
    check(_gradient(0) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Shared_WorldGradient"]
    close(_px(img, 0.75, 0.5)[0], 0.0, 0.02, "B's island (B is low: black)")
    # A world gradient over A alone would make A black too; with B included A is white
    close(_px(img, 0.25, 0.5)[0], 1.0, 0.02, "B counted in the gradient")
    check(b.hide_get(), "B is hidden again")


def test_filters_and_coverage_see_the_whole_set(addon):
    a, b, mat = _pair()
    cov = addon.filters.bake_coverage(bpy.context, a, mat, 64)
    try:
        close(_px(cov, 0.25, 0.5)[0], 1.0, 0.01, "A's island")
        close(_px(cov, 0.75, 0.5)[0], 1.0, 0.01, "B's island too")
        close(_px(cov, 0.5, 0.5)[0], 0.0, 0.01, "nothing between them")
    finally:
        bpy.data.images.remove(cov)


def test_export_bakes_every_object_of_the_material(addon):
    a, b, mat = _pair()
    _select(a)
    bpy.ops.essence.add_layer()
    top = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    top.inputs["Roughness B"].default_value = 0.2
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc = bpy.context.scene
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed")
        out = bpy.data.images.load(os.path.join(folder, "Shared_MaskMap.exr"))
        out.colorspace_settings.name = 'Non-Color'
        close(_px(out, 0.25, 0.5)[3], 0.8, 0.03, "A's smoothness")
        close(_px(out, 0.75, 0.5)[3], 0.8, 0.03, "B's smoothness: B is in the export too")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_settings_survive_save_and_reload(addon):
    a = make_plane("A", size=1.0)
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    cage = make_plane("Cage", size=1.0, location=(0, 0, 0.2))
    s = texset(a)
    s.bake_high, s.bake_extrusion, s.bake_size = coll, 0.25, "512"
    a.essence_bake_cage = cage
    folder = tempfile.mkdtemp(prefix="essence_texset_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        a = bpy.data.objects["A"]
        s = a.active_material.essence
        check(s.bake_high is not None and s.bake_high.name == "HighPoly", "High Poly lost")
        close(s.bake_extrusion, 0.25, 1e-6, "Extrusion lost")
        check(s.bake_size == "512", "Size lost")
        check(a.essence_bake_cage is not None and a.essence_bake_cage.name == "Cage", "Cage lost")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_the_bake_panel_shows_the_texture_set(addon):
    from essence import ui
    from test_ui import _draw
    a, b, mat = _pair()
    _select(a)
    log = _draw(ui._bake, a)
    said = [c[2].get("text", "") for c in log if c[0] == "label"]
    check("Texture set: Shared" in said, f"no texture set line: {said}")
    check(not any("bake into one image" in t for t in said), f"the note is gone: {said}")
    check(any(c[0] == "prop" and c[1][1] == "bake_size" for c in log), "no Size")
    check(any(c[0] == "prop" and c[1][1] == "bake_high" for c in log), "no High Poly")
    texset(a).bake_high = bpy.data.collections.new("H")
    log = _draw(ui._bake, a)
    check(any(c[0] == "prop" and c[1][1] == "essence_bake_cage" for c in log), "no Cage")
    bare = make_plane("Bare", size=1.0)
    log = _draw(ui._bake, bare)
    check(any("No material yet" in c[2].get("text", "") for c in log if c[0] == "label"),
          "an object without a material says so")
