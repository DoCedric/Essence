"""Curvature derived from the Normal slot with the compositor (curvature.py)."""

import math
import os
import shutil
import tempfile

import bpy

from harness import check, close, make_plane
from test_bake_quality import _bake, _select


def _normal_image(name, size, profile, axis='x'):
    """A tangent normal map whose x (or y) component along `axis` follows `profile(t)`, t being
    the position 0..1 across the image: n = (profile, 0, sqrt(1 - profile^2))."""
    img = bpy.data.images.new(name, size, size, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    px = []
    for y in range(size):
        for x in range(size):
            t = (x if axis == 'x' else y) / (size - 1)
            n = profile(t)
            nx, ny = (n, 0.0) if axis == 'x' else (0.0, n)
            nz = math.sqrt(max(0.0, 1 - n * n))
            px += [nx * 0.5 + 0.5, ny * 0.5 + 0.5, nz * 0.5 + 0.5, 1.0]
    img.pixels.foreach_set(px)
    img.update()
    return img


def _ramp(sign):
    """Flat, then a ramp across the middle eighth of the image, then flat: the normal turns
    away from the center line (convex, sign +1) or towards it (concave, sign -1)."""
    def profile(t):
        return sign * 0.7 * max(-1.0, min(1.0, (t - 0.5) / 0.0625))
    return profile


def _setup(addon):
    ob = make_plane("Surface", size=2.0)
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.essence.curvature_width = 0.0
    return ob, mat


def _put_normal(addon, mat, img):
    mat.node_tree.nodes[addon.nodes.map_slot_name('NORMAL')].image = img


def _at(img, x, y):
    w, h = img.size
    return list(img.pixels)[(y * w + x) * 4:(y * w + x) * 4 + 3]


def _derive():
    return bpy.ops.essence.derive_curvature()


def test_convex_is_bright_concave_is_dark_flat_is_grey(addon):
    for axis in ('x', 'y'):
        for sign, name in ((1, "convex"), (-1, "concave")):
            ob, mat = _setup(addon)
            _put_normal(addon, mat, _normal_image(f"n_{axis}_{name}", 64, _ramp(sign), axis))
            check(_derive() == {'FINISHED'}, "derive failed")
            img = bpy.data.images[f"{ob.name}_Curvature"]
            mid = 32
            at_edge = _at(img, mid, 10)[0] if axis == 'x' else _at(img, 10, mid)[0]
            flat = _at(img, 8, 10)[0] if axis == 'x' else _at(img, 10, 8)[0]
            close(flat, 0.5, 0.005, f"{axis} {name}: flat areas stay grey")
            if sign > 0:
                check(at_edge > 0.505, f"{axis} {name}: should be brighter than grey: {at_edge}")
            else:
                check(at_edge < 0.495, f"{axis} {name}: should be darker than grey: {at_edge}")


def test_the_same_shape_gives_the_same_curvature_at_any_size(addon):
    values = []
    for size in (64, 128):
        ob, mat = _setup(addon)
        _put_normal(addon, mat, _normal_image(f"n{size}", size, _ramp(1)))
        check(_derive() == {'FINISHED'}, "derive failed")
        img = bpy.data.images[f"{ob.name}_Curvature"]
        check(tuple(img.size) == (size, size), "the result has the size of the normal map")
        values.append(_at(img, size // 2, 10)[0] - 0.5)
    check(values[0] > 0.004, f"too weak to compare: {values}")
    close(values[0], values[1], values[0] * 0.2, f"size dependent: {values}")


def test_width_and_strength(addon):
    ob, mat = _setup(addon)
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    _derive()
    base = _at(bpy.data.images["Surface_Curvature"], 32, 10)[0] - 0.5
    mat.essence.curvature_strength = 3.0
    _derive()
    doubled = _at(bpy.data.images["Surface_Curvature"], 32, 10)[0] - 0.5
    close(doubled, base * 2, base * 0.1, "twice the strength, twice the response")
    mat.essence.curvature_strength = 1.5
    mat.essence.curvature_width = 4.0
    _derive()
    soft = bpy.data.images["Surface_Curvature"]
    check(abs(_at(soft, 32, 10)[0] - 0.5) < base, "a wider blur lowers the peak of a sharp edge")


def test_result_image_and_slot(addon):
    ob, mat = _setup(addon)
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    check(_derive() == {'FINISHED'}, "derive failed")
    img = bpy.data.images["Surface_Curvature"]
    slot = mat.node_tree.nodes[addon.nodes.map_slot_name('CURVATURE')]
    check(slot.image is not None and slot.image.name == img.name, "the Curvature slot has it")
    check(img.is_float and img.packed_file is not None, "float and packed")
    check(img.colorspace_settings.name == 'Non-Color', "Non-Color")
    check(not img.name.startswith("."), "a visible image: it is a map, not a helper")
    _derive()
    check(len([i for i in bpy.data.images if i.name.startswith("Surface_Curvature")]) == 1,
          "deriving again replaces the image")
    check(mat.node_tree.nodes[addon.nodes.map_slot_name('CURVATURE')].image.name ==
          "Surface_Curvature", "and the slot follows")
    check(bpy.data.node_groups[addon.curvature.group_name(mat)].use_fake_user,
          "the compositor group needs a fake user or a save drops it")
    check(not [s for s in bpy.data.scenes if s.name.startswith("EssenceFilterTmp")],
          "the throwaway scene is gone")


def test_without_a_normal_it_says_so(addon):
    ob, mat = _setup(addon)
    try:
        _derive()
        msg = None
    except RuntimeError as e:
        msg = str(e)
    check(msg and "Normal" in msg, f"expected a message about the Normal slot: {msg}")
    check(addon.curvature.result_image(mat) is None, "nothing was made")
    check(not addon.curvature.needs_derive(mat), "no normal means nothing to derive")


def test_it_goes_stale_when_the_normal_or_the_settings_change(addon):
    c = addon.curvature
    ob, mat = _setup(addon)
    n1 = _normal_image("n1", 64, _ramp(1))
    _put_normal(addon, mat, n1)
    check(c.needs_derive(mat), "a normal but no result yet")
    _derive()
    check(not c.needs_derive(mat), "a fresh result is current")
    mat.essence.curvature_strength = 2.0
    check(c.needs_derive(mat), "the strength changed")
    _derive()
    check(not c.needs_derive(mat), "current again")
    _put_normal(addon, mat, _normal_image("n2", 64, _ramp(-1)))
    check(c.needs_derive(mat), "another normal image")
    _derive()
    check(not c.needs_derive(mat), "current again")
    c.slot(mat).location = (5, 5)
    ng = bpy.data.node_groups[c.group_name(mat)]
    ng.nodes["Strength"].inputs[1].default_value = 9.0
    check(c.needs_derive(mat), "a hand edit of the compositor group counts")
    _derive()      # puts the settings back into the nodes
    check(not c.needs_derive(mat), "and deriving applies the settings again")


def test_a_rebake_of_the_normal_makes_it_stale(addon):
    c = addon.curvature
    low = make_plane("Low", size=2.0)
    bpy.ops.essence.add_layer()
    mat = low.active_material
    mat.essence.curvature_width = 0.0
    _select(low)
    check(_bake('NORMAL') == {'FINISHED'}, "bake failed")
    check("essence_stamp" in bpy.data.images["Low_Normal"], "a bake leaves a stamp")
    check(_derive() == {'FINISHED'}, "derive failed")
    check(not c.needs_derive(mat), "current")
    _select(low)
    _bake('NORMAL')       # the same image name, new content
    check(c.needs_derive(mat), "a new bake of the normal makes the curvature stale")


def test_curvature_from_a_real_high_poly_bake(addon):
    low = make_plane("Low", size=2.0)
    bpy.ops.essence.add_layer()
    mat = low.active_material
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.05))
    cube = bpy.context.active_object
    cube.name = "High"
    bevel = cube.modifiers.new("b", 'BEVEL')
    bevel.width = 0.08
    bevel.segments = 4
    bpy.ops.object.modifier_apply(modifier="b")
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(cube)
    bpy.context.scene.collection.objects.unlink(cube)
    sc = bpy.context.scene
    sc.essence_bake_high = coll
    sc.essence_bake_extrusion = 0.6
    _select(low)
    sc.essence_bake_size = '256'
    check(_bake('NORMAL') == {'FINISHED'}, "normal bake failed")
    sc.essence_bake_size = '256'
    check(_derive() == {'FINISHED'}, "derive failed")
    img = bpy.data.images["Low_Curvature"]
    check(tuple(img.size) == tuple(bpy.data.images["Low_Normal"].size), "same size as the normal")
    px = list(img.pixels)[0::4]
    check(max(px) > 0.52 and min(px) < 0.48,
          f"a bevelled box should leave edges: min {min(px)} max {max(px)}")
    close(sorted(px)[len(px) // 2], 0.5, 0.01, "most of the map is flat grey")


def test_curvature_survives_save_and_reload(addon):
    ob, mat = _setup(addon)
    mat.essence.curvature_width = 2.5
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    img = bpy.data.images["n"]
    img.pack()
    _derive()
    name = mat.name
    folder = tempfile.mkdtemp(prefix="essence_curv_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.materials[name]
        c = addon.curvature
        close(mat.essence.curvature_width, 2.5, 1e-6, "setting lost")
        ng = bpy.data.node_groups.get(c.group_name(mat))
        check(ng is not None and "Width" in ng.nodes, "the compositor group was lost on save")
        check(c.result_image(mat) is not None, "the result was lost")
        check(not c.needs_derive(mat), "still current after a reload")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_curvature_panel_draws(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat = _setup(addon)
    log = _draw(ui._maps, ob)
    check(any(c[0] == "operator" and c[1][0] == "essence.derive_curvature" for c in log),
          "no Derive button")
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    _draw(ui._maps, ob)
    _derive()
    _draw(ui._maps, ob)


def test_a_file_from_before_the_slots_existed(addon):
    """Files made before the Position and Curvature slots have no slot nodes."""
    from essence import ui
    from test_ui import _draw
    ob, mat = _setup(addon)
    tree = mat.node_tree
    for map_id in ('POSITION', 'CURVATURE'):
        tree.nodes.remove(tree.nodes[addon.nodes.map_slot_name(map_id)])
    log = _draw(ui._maps, ob)
    check(any(c[0] == "operator" and c[1][0] == "essence.rebuild" for c in log),
          "the panel should offer to add the new slots")
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    check(_derive() == {'FINISHED'}, "derive should make the slot it needs")
    check(addon.curvature.result_image(mat) is not None, "and fill it")
    bpy.ops.essence.rebuild()
    check(all(tree.nodes.get(addon.nodes.map_slot_name(m)) is not None
              for m, _ in addon.nodes.MAP_SLOTS), "a rebuild adds every slot")


def _two_island_normal(size=128):
    """A tangent normal map for the two-island grid of test_mesh_id: the left island (u < 0.45)
    leans one way, the right island (u > 0.55) the other, so the normal jumps in the gap
    between them. The left island also has a real feature: a dip around u = 0.2."""
    img = bpy.data.images.new("two", size, size, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    px = []
    for y in range(size):
        for x in range(size):
            u = x / (size - 1)
            nx = 0.5 if u < 0.5 else -0.5
            if 0.18 < u < 0.22:
                nx -= 0.4 * (1 - abs(u - 0.2) / 0.02)
            nz = math.sqrt(max(0.0, 1 - nx * nx))
            px += [nx * 0.5 + 0.5, 0.5, nz * 0.5 + 0.5, 1.0]
    img.pixels.foreach_set(px)
    img.update()
    return img


def test_a_jump_between_close_islands_leaves_no_line(addon):
    from test_mesh_id import _grid, _two_islands
    low = _grid("Low", _two_islands)
    for o in bpy.context.view_layer.objects:
        o.select_set(o is low)
    bpy.context.view_layer.objects.active = low
    bpy.ops.essence.add_layer()
    mat = low.active_material
    mat.essence.curvature_width = 1.0
    img = _two_island_normal()
    _put_normal(addon, mat, img)
    # the test is only meaningful if the jump is really there and the plain derivative sees it
    px = list(img.pixels)
    w = img.size[0]
    row = [px[(10 * w + x) * 4] for x in range(w)]
    check(max(abs(row[x + 1] - row[x - 1]) for x in range(1, w - 1)) > 0.4,
          "the test normal should have a hard jump between the islands")
    check(_derive() == {'FINISHED'}, "derive failed")
    out = bpy.data.images["Low_Curvature"]
    c = [list(out.pixels)[(y * w + x) * 4] for y in range(w) for x in (range(w))]
    at = lambda u, v: c[int(v * (w - 1)) * w + int(u * (w - 1))]
    # across the gap and at the borders of both islands: flat grey
    for u in (0.40, 0.43, 0.45, 0.48, 0.50, 0.52, 0.55, 0.57, 0.60):
        for v in (0.2, 0.5, 0.8):
            close(at(u, v), 0.5, 0.02, f"u={u} v={v}: a line where the islands meet")
    # a real feature inside an island is still there
    dip = min(at(u / 100, 0.5) for u in range(16, 25))
    bright = max(at(u / 100, 0.5) for u in range(16, 25))
    check(dip < 0.495 or bright > 0.505, f"the real dip in the left island is gone: {dip} {bright}")


def test_the_border_reach_follows_the_width(addon):
    ob, mat = _setup(addon)
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    for width, border in ((0.0, -2), (1.0, -3), (1.5, -4), (4.0, -6)):
        mat.essence.curvature_width = width
        _derive()
        ng = bpy.data.node_groups[addon.curvature.group_name(mat)]
        check(ng.nodes["Border"].inputs['Size'].default_value == border,
              f"width {width}: border {ng.nodes['Border'].inputs['Size'].default_value}")


def test_derive_leaves_nothing_behind_and_needs_a_uv_map(addon):
    ob, mat = _setup(addon)
    sc = bpy.context.scene
    engine, samples, margin = sc.render.engine, sc.cycles.samples, sc.render.bake.margin
    _put_normal(addon, mat, _normal_image("n", 64, _ramp(1)))
    check(_derive() == {'FINISHED'}, "derive failed")
    check(sc.render.engine == engine and sc.cycles.samples == samples, "render settings changed")
    check(sc.render.bake.margin == margin, "the bake margin was left at 0")
    check(not [i for i in bpy.data.images if i.name.startswith(".Essence")],
          "a temporary image was left behind")
    check(not [n for n in mat.node_tree.nodes if n.bl_idname == 'ShaderNodeEmission'],
          "the bake override was left in the material")
    ob.data.uv_layers.remove(ob.data.uv_layers[0])
    try:
        _derive()
        msg = None
    except RuntimeError as e:
        msg = str(e)
    check(msg and "UV" in msg, f"expected a message about the UV map: {msg}")
