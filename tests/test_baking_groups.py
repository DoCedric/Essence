"""Baking groups: each low-poly object bakes from its own high polys (and floaters), through one
merged mesh, with only the bake's own geometry rendering."""

import math
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


def _collection(name, *objects):
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    for o in objects:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    return coll


def _tilted(name, location, tilt, size=1.2):
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    o = bpy.context.active_object
    o.name = name
    o.rotation_euler = (math.radians(tilt), 0, 0)
    return o


def _half(ob, u0):
    for l in ob.data.uv_layers.active.data:
        u, v = l.uv
        l.uv = (u0 + u * 0.4, 0.1 + v * 0.8)


def _two_parts():
    """Two low planes sharing a material (A left in UV, B right). A's group is a plane tilted
    +15 degrees; B's group has its own plane tilted -15 and a decoy right on top of A, closer
    to A's surface than A's own high poly."""
    a = make_plane("A_Low", size=1.0, location=(-2, 0, 0))
    _half(a, 0.05)
    b = make_plane("B_Low", size=1.0, location=(2, 0, 0))
    _half(b, 0.55)
    mat = bpy.data.materials.new("Shared")
    mat.use_nodes = True
    a.data.materials.append(mat)
    b.data.materials.append(mat)
    ga = _collection("A", _tilted("A_High", (-2, 0, -0.01), 15))
    gb = _collection("B", _tilted("B_High", (2, 0, -0.01), -15),
                     _tilted("B_Decoy", (-2, 0, 0.005), -15))
    a.essence_bake_high, b.essence_bake_high = ga, gb
    mat.essence.bake_extrusion = 0.1
    texset(a).bake_size = "128"
    return a, b, mat


def _tilt_sign(px):
    """World normal remapped 0..1: a plane tilted about X leans its normal along -Y or +Y."""
    return -1 if px[1] > 0.55 else (1 if px[1] < 0.45 else 0)


def test_each_object_bakes_from_its_own_group(addon):
    a, b, mat = _two_parts()
    _select(a)
    check(_bake('NORMAL_WORLD') == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Shared_NormalWorld"]
    pa, pb = _px(img, 0.25, 0.5), _px(img, 0.75, 0.5)
    check(_tilt_sign(pa) == 1, f"A should see its own +15 plane, not B's decoy: {pa}")
    check(_tilt_sign(pb) == -1, f"B should see its own -15 plane: {pb}")


def test_the_material_high_poly_is_the_default(addon):
    a, b, mat = _two_parts()
    b.essence_bake_high = None
    mat.essence.bake_high = bpy.data.collections["A"]
    check(addon.bake.bake_group(b, mat)[0] is mat.essence.bake_high, "falls back")
    check(addon.bake.bake_group(a, mat)[0].name == "A", "an own group wins")


def test_a_bake_reads_one_merged_high_poly_and_cleans_up(addon):
    a, b, mat = _two_parts()
    seen = []
    original = addon.bake.ESSENCE_OT_bake._bake_object

    def spy(self, context, mat_, obj, map_type, build, high, *args):
        seen.append((obj.name, [h.name for h in high], len(high[0].data.polygons) if high else 0))
        return original(self, context, mat_, obj, map_type, build, high, *args)

    addon.bake.ESSENCE_OT_bake._bake_object = spy
    try:
        _select(a)
        check(_bake('NORMAL_WORLD') == {'FINISHED'}, "bake failed")
    finally:
        addon.bake.ESSENCE_OT_bake._bake_object = original
    check(all(len(s[1]) == 1 and s[1][0].startswith(".Essence High") for s in seen),
          f"one merged high poly each: {seen}")
    check(dict((s[0], s[2]) for s in seen) == {"A_Low": 1, "B_Low": 2},
          f"A's merge has its one face, B's its two: {seen}")
    check(not [o for o in bpy.data.objects if o.name.startswith(".Essence High")],
          "merged objects are removed")
    check(not [m for m in bpy.data.meshes if m.users == 0], "no orphan meshes left")
    check(not [m for m in bpy.data.materials if m.name.startswith(".Essence High")],
          "the fill material is removed")


def test_the_merge_gives_the_same_result_as_separate_objects(addon):
    """Modifiers applied and custom normals kept: a bevelled, weighted-normal box and a
    subdivided plane, baked through the merge and by Blender from the separate objects."""
    low = make_plane("Low", size=2.0)
    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(-0.4, 0, -0.2))
    box = bpy.context.active_object
    box.modifiers.new("b", 'BEVEL').width = 0.1
    box.modifiers.new("w", 'WEIGHTED_NORMAL')
    plane = _tilted("Sub", (0.5, 0, -0.05), 10, size=0.8)
    plane.modifiers.new("s", 'SUBSURF')
    coll = _collection("High", box, plane)
    low.essence_bake_high = coll
    texset(low).bake_extrusion = 0.4
    _select(low)
    check(_bake('NORMAL') == {'FINISHED'}, "bake failed")
    merged_px = list(bpy.data.images["Low_Mat_Normal"].pixels)
    # Blender alone, separate objects, same settings
    sc = bpy.context.scene
    img = bpy.data.images.new("plain", 128, 128, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    tree = low.active_material.node_tree
    node = tree.nodes.new('ShaderNodeTexImage')
    node.image = img
    tree.nodes.active = node
    bsdf = addon.nodes.find_principled(tree)
    for l in list(bsdf.inputs['Normal'].links):
        tree.links.remove(l)
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 4
    bk = sc.render.bake
    bk.use_selected_to_active, bk.use_cage = True, False
    bk.cage_extrusion, bk.max_ray_distance, bk.margin = 0.4, 0.8, 0
    with addon.bake._ForceSelectable(bpy.context, [box, plane]):
        for o in bpy.context.view_layer.objects:
            o.select_set(o in (low, box, plane))
        bpy.context.view_layer.objects.active = low
        bpy.ops.object.bake(type='NORMAL')
    plain_px = list(img.pixels)
    diff = max(abs(x - y) for x, y in zip(merged_px, plain_px))
    check(diff < 0.005, f"the merge changed the bake: max difference {diff}")


def test_floaters_add_to_the_normal_but_not_to_ao(addon):
    low = make_plane("Low", size=2.0)
    flat = _tilted("Flat", (0, 0, -0.01), 0, size=2.4)
    floater = _tilted("Floater", (0, 0, 0.03), 25, size=0.6)
    low.essence_bake_high = _collection("High", flat)
    low.essence_bake_floaters = _collection("High_FL", floater)
    texset(low).bake_extrusion = 0.1
    _select(low)
    check(_bake('NORMAL_WORLD') == {'FINISHED'}, "normal bake failed")
    check(_tilt_sign(_px(bpy.data.images["Low_Mat_NormalWorld"], 0.5, 0.5)) != 0,
          "the floater's tilt is in the normal")
    _select(low)
    check(_bake('AO') == {'FINISHED'}, "AO bake failed")
    close(_px(bpy.data.images["Low_Mat_AO"], 0.5, 0.5)[0], 1.0, 0.05,
          "the floater casts no shadow in the AO")


def _ao_center(low):
    _select(low)
    check(_bake('AO') == {'FINISHED'}, "AO bake failed")
    return _px(bpy.data.images[f"{low.active_material.name}_AO"], 0.25, 0.5)[0]


def test_ao_sees_every_groups_high_poly_but_no_low_poly(addon):
    a = make_plane("A_Low", size=1.0, location=(-2, 0, 0))
    _half(a, 0.05)
    b = make_plane("B_Low", size=1.0, location=(2, 0, 0))
    _half(b, 0.55)
    mat = bpy.data.materials.new("Shared")
    mat.use_nodes = True
    a.data.materials.append(mat)
    b.data.materials.append(mat)
    a.essence_bake_high = _collection("A", _tilted("A_High", (-2, 0, -0.01), 0))
    roof = _tilted("B_Roof", (-2, 0, 0.3), 0, size=3.0)        # part of B, above A
    b.essence_bake_high = _collection("B", _tilted("B_High", (2, 0, -0.01), 0), roof)
    mat.essence.bake_extrusion = 0.05
    texset(a).bake_size = "128"
    check(_ao_center(a) < 0.3, "B's high poly over A occludes A")
    roof.location.z = -5                                         # away again
    b.location = (-2, 0, 0.3)                                    # B's low over A instead
    bpy.context.view_layer.update()
    close(_ao_center(a), 1.0, 0.05, "a low poly never occludes")


def test_unrelated_objects_do_not_occlude_and_are_put_back(addon):
    low = make_plane("Low", size=2.0)
    low.essence_bake_high = _collection("High", _tilted("H", (0, 0, -0.01), 0, size=2.4))
    texset(low).bake_extrusion = 0.05
    prop = _tilted("Prop", (0, 0, 0.3), 0, size=4.0)
    hidden = _tilted("AlreadyOff", (5, 0, 0), 0)
    hidden.hide_render = True
    close(_ao_center(low), 1.0, 0.05, "a prop above the part casts no AO")
    check(prop.hide_render is False and hidden.hide_render is True,
          "render switches are put back as they were")


def test_without_a_high_poly_the_parts_occlude_each_other(addon):
    a = make_plane("A", size=1.0, location=(0, 0, 0))
    _half(a, 0.05)
    b = make_plane("B", size=3.0, location=(0, 0, 0.3))
    _half(b, 0.55)
    mat = bpy.data.materials.new("Shared")
    mat.use_nodes = True
    a.data.materials.append(mat)
    b.data.materials.append(mat)
    texset(a).bake_size = "128"
    check(_ao_center(a) < 0.3, "low polys baked alone are the surface: B shades A")


def test_curves_count_as_high_poly(addon):
    bpy.ops.curve.primitive_bezier_circle_add(radius=0.5, location=(0, 0, 0.05))
    curve = bpy.context.active_object
    curve.data.bevel_depth = 0.05
    check(curve in addon.bake.high_poly_objects(_collection("H", curve)), "a curve is a source")
    merged, drop = addon.bake.merge_high(bpy.context, [curve])
    try:
        check(len(merged.data.polygons) > 0, "the curve's surface is in the merge")
    finally:
        drop()


def test_object_ids_from_a_merge_follow_the_part_names(addon):
    low = make_plane("Low", size=2.0)
    left = _tilted("PartLeft", (-0.5, 0, 0.02), 0, size=1.0)
    right = _tilted("PartRight", (0.5, 0, 0.02), 0, size=1.0)
    low.essence_bake_high = _collection("High", left, right)
    texset(low).bake_extrusion = 0.1
    bpy.context.scene.essence_bake_id = 'OBJECT'
    _select(low)
    check(_bake('ID') == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_Mat_ID"]
    for (u, name) in ((0.25, "PartLeft"), (0.75, "PartRight")):
        want = addon.bake.name_color(name)[:3]
        got = _px(img, u, 0.5)[:3]
        check(sum((x - y) ** 2 for x, y in zip(got, want)) ** 0.5 < 0.03,
              f"{name}: {got} should be its name color {want}")


def test_pair_by_name(addon):
    lows = []
    for n in ("BG1", "BG2", "Odd"):
        o = make_plane(f"{n}_Low", size=1.0)
        lows.append(o)
    mat = bpy.data.materials.new("Shared")
    mat.use_nodes = True
    for o in lows:
        o.data.materials.append(mat)
    bg1 = _collection("BG1", _tilted("BG1_High_01", (0, 0, 0), 0))
    fl1 = _collection("BG1_FL", _tilted("BG1_High_02", (0, 0, 0), 0))
    bg2 = _collection("BG2_High", _tilted("BG2_High_01", (0, 0, 0), 0))
    _select(lows[0])
    check(bpy.ops.essence.pair_by_name() == {'FINISHED'}, "pairing failed")
    check(lows[0].essence_bake_high == bg1 and lows[0].essence_bake_floaters == fl1, "BG1")
    check(lows[1].essence_bake_high == bg2 and lows[1].essence_bake_floaters is None, "BG2")
    check(lows[2].essence_bake_high is None, "no match: left alone")
    check(addon.bake.texture_set(bpy.context, mat) == lows,
          "the high polys are not part of the texture set")


def test_groups_survive_save_and_reload(addon):
    low = make_plane("Low", size=1.0)
    low.essence_bake_high = _collection("H", _tilted("Hi", (0, 0, 0), 0))
    low.essence_bake_floaters = _collection("F", _tilted("Fl", (0, 0, 0), 0))
    folder = tempfile.mkdtemp(prefix="essence_groups_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        low = bpy.data.objects["Low"]
        check(low.essence_bake_high.name == "H" and low.essence_bake_floaters.name == "F",
              "the group was lost")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_the_bake_panel_shows_the_groups(addon):
    from essence import ui
    from test_ui import _draw
    a, b, mat = _two_parts()
    _select(a)
    log = _draw(ui._bake, a)
    said = [c[2].get("text", "") for c in log if c[0] == "label"]
    check("Baking group of A_Low" in said, f"no group box: {said}")
    check("A_Low: A" in said and "B_Low: B" in said, f"no per-object lines: {said}")
    props = [c[1][1] for c in log if c[0] == "prop"]
    check({"essence_bake_high", "essence_bake_floaters", "essence_bake_cage"} <= set(props),
          f"group fields: {props}")
    check(any(c[0] == "operator" and c[1][0] == "essence.pair_by_name" for c in log),
          "no Pair by Name")
