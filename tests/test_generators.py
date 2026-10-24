"""Live generators (generators.py): structure, values against baked pixels, and wiring."""

import math

import bpy

from harness import check, close, make_plane, sample_socket


def _setup(addon, gen_type, channel='ROUGHNESS'):
    """A plane with one layer whose `channel` is a generator. Returns (ob, mat, layer entry)."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    src = next(s for s in e.sources if s.channel == channel)
    src.gen_type = gen_type
    src.kind = 'GENERATOR'
    return ob, mat, e


def _tex(addon, mat, e, channel='ROUGHNESS'):
    return addon.sources.source_nodes(mat.node_tree, e.uid, channel)['TEXTURE']


def _value(ob, mat, e, uv=(0.5, 0.5)):
    node = mat.node_tree.nodes[e.node_name]
    return sample_socket(ob, mat, node.outputs['Roughness'], size=64, uv=uv)[0]


def _set(tex, **values):
    for name, v in values.items():
        tex.inputs[name.replace("_", " ")].default_value = v


def test_generator_source_has_no_coordinates(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    found = addon.sources.source_nodes(mat.node_tree, e.uid, 'ROUGHNESS')
    check(set(found) == {'TEXTURE', 'REMAP'}, f"roles: {sorted(found)}")
    ng = found['TEXTURE'].node_tree
    check(ng.name == "Essence Gen Directional Light", ng.name)
    check(ng.get("essence_version") == 1, "the group needs a version key")
    # Switching to a procedural brings coordinates back, and back again removes them.
    next(s for s in e.sources if s.channel == 'ROUGHNESS').kind = 'PROCEDURAL'
    check({'COORD', 'MAPPING', 'TEXTURE', 'REMAP'} == set(
        addon.sources.source_nodes(mat.node_tree, e.uid, 'ROUGHNESS')), "procedural roles")
    next(s for s in e.sources if s.channel == 'ROUGHNESS').kind = 'GENERATOR'
    check(set(addon.sources.source_nodes(mat.node_tree, e.uid, 'ROUGHNESS')) ==
          {'TEXTURE', 'REMAP'}, "generator roles again")


def test_generator_type_switch_resets_inputs(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    _set(_tex(addon, mat, e), Contrast=4.0)
    next(s for s in e.sources if s.channel == 'ROUGHNESS').gen_type = 'SLOPE'
    tex = _tex(addon, mat, e)
    check(tex.node_tree.name == "Essence Gen Slope", tex.node_tree.name)
    close(tex.inputs['Angle'].default_value, 45.0, 0.001, "slope defaults")


def test_directional_light_values(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    tex = _tex(addon, mat, e)
    s2 = math.sqrt(0.5)
    cases = (((0, 0, 1), 1.0, {}), ((0, 0, -1), 0.0, {}), ((1, 0, 0), 0.5, {}),
             ((0, s2, s2), 0.5 + 0.5 * s2, {}),
             ((0, s2, s2), 0.5 + 0.25 * s2, {"Contrast": 0.5}),
             ((1, 0, 0), 0.6, {"Balance": 0.1}),
             ((0, 0, 3), 1.0, {}))          # the direction need not be normalized
    for direction, expected, extra in cases:
        _set(tex, Contrast=1.0, Balance=0.0)
        tex.inputs['Direction'].default_value = direction
        _set(tex, **extra)
        close(_value(ob, mat, e), expected, 0.02, f"light {direction} {extra}")


def test_light_in_object_and_world_space(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    tex = _tex(addon, mat, e)
    ob.rotation_euler.x = math.pi / 2      # the plane's +Z now points along world -Y
    bpy.context.view_layer.update()
    tex.inputs['Direction'].default_value = (0, 0, 1)
    close(_value(ob, mat, e), 1.0, 0.02, "object space follows the object")
    tex.inputs['World Space'].default_value = True
    close(_value(ob, mat, e), 0.5, 0.02, "world up no longer faces the plane")
    tex.inputs['Direction'].default_value = (0, -1, 0)
    close(_value(ob, mat, e), 1.0, 0.02, "world -Y does")


def test_directional_light_can_use_the_detail_normal(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    tex = _tex(addon, mat, e)
    _set(tex, World_Space=True)
    tex.inputs['Direction'].default_value = (1, 0, 0)
    close(_value(ob, mat, e), 0.5, 0.02, "a flat plane does not face +X")
    # A tangent space normal map tilted 45 degrees towards the tangent (+X on this plane).
    img = bpy.data.images.new("tilt", 8, 8, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    img.generated_color = (0.5 + 0.5 * math.sqrt(0.5), 0.5, 0.5 + 0.5 * math.sqrt(0.5), 1.0)
    slot = mat.node_tree.nodes[addon.nodes.map_slot_name('NORMAL')]
    slot.image = img
    bpy.ops.essence.rebuild()
    tex = _tex(addon, mat, e)
    check(tex.inputs['Detail Normal'].is_linked, "the detail normal should be wired")
    check(tex.inputs['Detail Normal'].links[0].from_node.name == addon.nodes.NORMAL_NODE_NAME,
          "from the Essence Normal Map node")
    close(_value(ob, mat, e), 0.5, 0.02, "off by default: the detail is not used")
    tex.inputs['Use Detail Normal'].default_value = True
    close(_value(ob, mat, e), 0.5 + 0.5 * math.sqrt(0.5), 0.03, "with the detail normal")


def test_an_empty_normal_slot_means_the_surface_normal(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    tex = _tex(addon, mat, e)
    _set(tex, World_Space=True)
    tex.inputs['Use Detail Normal'].default_value = True
    tex.inputs['Direction'].default_value = (0, 0, 1)
    check(tex.inputs['Detail Normal'].is_linked, "the detail normal must never be left open")
    close(_value(ob, mat, e), 1.0, 0.02, "the plain normal of a flat plane faces up")
    tree = mat.node_tree
    check(tree.nodes.get(addon.sources.DETAIL_FALLBACK) is not None, "stand-in node expected")
    img = bpy.data.images.new("flat", 8, 8, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    img.generated_color = (0.5, 0.5, 1.0, 1.0)
    tree.nodes[addon.nodes.map_slot_name('NORMAL')].image = img
    bpy.ops.essence.rebuild()
    check(tree.nodes.get(addon.sources.DETAIL_FALLBACK) is None,
          "the stand-in goes once the Normal Map node exists")
    check(_tex(addon, mat, e).inputs['Detail Normal'].links[0].from_node.name ==
          addon.nodes.NORMAL_NODE_NAME, "now from the Normal Map node")
    next(s for s in e.sources if s.channel == 'ROUGHNESS').kind = 'VALUE'
    bpy.ops.essence.rebuild()
    check(tree.nodes.get(addon.sources.DETAIL_FALLBACK) is None, "nothing left over")


def test_slope(addon):
    ob, mat, e = _setup(addon, 'SLOPE')
    tex = _tex(addon, mat, e)
    close(_value(ob, mat, e), 1.0, 0.02, "flat ground faces up")
    tex.inputs['Axis'].default_value = (0, 0, -1)
    close(_value(ob, mat, e), 0.0, 0.02, "and not down")
    tex.inputs['Axis'].default_value = (0, 0, 1)
    _set(tex, World_Space=True)
    ob.rotation_euler.x = math.radians(45)
    bpy.context.view_layer.update()
    close(_value(ob, mat, e), 0.5, 0.03, "45 degrees is the middle of the ramp at Angle 45")
    _set(tex, Angle=60.0)
    close(_value(ob, mat, e), 1.0, 0.03, "inside a wider angle")
    _set(tex, Angle=30.0)
    close(_value(ob, mat, e), 0.0, 0.03, "outside a narrower one")
    _set(tex, Softness=0.0)
    close(_value(ob, mat, e), 0.0, 0.03, "no softness is a hard edge, not a crash")


def test_gradient(addon):
    ob, mat, e = _setup(addon, 'GRADIENT')
    tex = _tex(addon, mat, e)
    tex.inputs['Axis'].default_value = (1, 0, 0)
    for u in (0.1, 0.5, 0.9):
        close(_value(ob, mat, e, (u, 0.5)), u, 0.03, f"x gradient at u={u}")
    tex.inputs['Axis'].default_value = (-1, 0, 0)
    close(_value(ob, mat, e, (0.9, 0.5)), 0.1, 0.03, "reversed")
    tex.inputs['Axis'].default_value = (0, 1, 0)
    close(_value(ob, mat, e, (0.5, 0.25)), 0.25, 0.03, "y gradient")
    tex.inputs['Axis'].default_value = (1, 0, 0)
    _set(tex, Start=0.5, End=1.0)
    close(_value(ob, mat, e, (0.75, 0.5)), 0.5, 0.03, "range remap")
    close(_value(ob, mat, e, (0.25, 0.5)), 0.0, 0.03, "below the range")


def test_gradient_height_uses_the_object_bounding_box(addon):
    ob, mat, e = _setup(addon, 'GRADIENT')
    ob.rotation_euler.x = math.pi / 2     # the plane stands up: its local Y runs up in the world
    bpy.context.view_layer.update()
    tex = _tex(addon, mat, e)
    tex.inputs['Axis'].default_value = (0, 1, 0)
    close(_value(ob, mat, e, (0.5, 0.8)), 0.8, 0.03, "generated coordinates are object local")


def test_generator_on_a_color_channel_and_on_masks_and_fills(addon):
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT', channel='COLOR')
    found = addon.sources.source_nodes(mat.node_tree, e.uid, 'COLOR')
    check(set(found) == {'TEXTURE', 'RAMP'}, f"color roles: {sorted(found)}")
    node = mat.node_tree.nodes[e.node_name]
    r = sample_socket(ob, mat, node.outputs['Color'], size=16)[0]
    check(r > 0.5, f"a light facing up should give the channel color, got {r}")

    ob, mat, e = _setup(addon, 'GRADIENT')
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)
    mask = mat.essence.layers[mat.essence.active_index]
    mask.source.gen_type = 'GRADIENT'
    mask.source.kind = 'GENERATOR'
    tex = addon.sources.source_nodes(mat.node_tree, mask.uid, 'MASK')['TEXTURE']
    tex.inputs['Axis'].default_value = (1, 0, 0)
    out = mat.node_tree.nodes[mask.node_name].outputs[0]
    close(sample_socket(ob, mat, out, size=64, uv=(0.9, 0.5))[0], 0.9, 0.03, "generator mask")

    bpy.ops.essence.add_fill()
    fill = mat.essence.layers[mat.essence.active_index]
    check(fill.kind == 'FILL', "fill added")
    fill.fill_blend = 'NORMAL'
    fill.source.gen_type = 'GRADIENT'
    fill.source.kind = 'GENERATOR'
    ftex = addon.sources.source_nodes(mat.node_tree, fill.uid, 'MASK')['TEXTURE']
    ftex.inputs['Axis'].default_value = (0, 1, 0)
    out = mat.node_tree.nodes[mask.node_name].outputs[0]
    close(sample_socket(ob, mat, out, size=64, uv=(0.9, 0.25))[0], 0.25, 0.03,
          "a generator fill replaces the mask value (Normal blend)")


def test_group_upgrade_keeps_values_and_links(addon):
    ob, mat, e = _setup(addon, 'SLOPE')
    tex = _tex(addon, mat, e)
    _set(tex, Angle=33.0)
    tex.inputs['Axis'].default_value = (0, 1, 0)
    ng = tex.node_tree
    ng["essence_version"] = 0                        # an older file
    bpy.ops.essence.rebuild()
    tex = _tex(addon, mat, e)
    check(tex.node_tree is ng and ng["essence_version"] == 1, "the group should be rebuilt")
    close(tex.inputs['Angle'].default_value, 33.0, 0.001, "value kept")
    check(tuple(tex.inputs['Axis'].default_value) == (0.0, 1.0, 0.0), "vector kept")
    check(tex.outputs['Fac'].is_linked, "output link kept")
    check(tex.inputs['Detail Normal'].is_linked, "detail link kept")
    close(_value(ob, mat, e), 0.0, 0.03, "and it still evaluates (flat plane, axis Y)")


def test_generators_survive_save_and_reload(addon):
    import os
    import shutil
    import tempfile
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    _set(_tex(addon, mat, e), Contrast=3.0)
    name, uid = ob.name, e.uid
    folder = tempfile.mkdtemp(prefix="essence_gen_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        tex = addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS')['TEXTURE']
        close(tex.inputs['Contrast'].default_value, 3.0, 0.001, "value lost in the save")
        entry = next(x for x in mat.essence.layers if x.uid == uid)
        src = next(s for s in entry.sources if s.channel == 'ROUGHNESS')
        check(src.kind == 'GENERATOR' and src.gen_type == 'DIRECTIONAL_LIGHT', "source lost")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_generator_panels_draw(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, e = _setup(addon, 'DIRECTIONAL_LIGHT')
    for gen_id in addon.generators.GENERATORS:
        next(s for s in e.sources if s.channel == 'ROUGHNESS').gen_type = gen_id
        mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers)
                                        if x.uid == e.uid)
        log = _draw(ui._props, ob)
        check(any(c[0] == "prop" and c[1][1] == "gen_type" for c in log),
              f"{gen_id}: no generator dropdown")
        check(not any(c[0] == "prop" and c[1][1] == "projection" for c in log),
              f"{gen_id}: a generator has no projection")
        check(not any(c[0] == "prop" and c[1][1] == "default_value" and
                      getattr(c[1][0], "name", "") == "Detail Normal" for c in log),
              "the detail normal is internal")
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)
    mask = mat.essence.layers[mat.essence.active_index]
    mask.source.kind = 'GENERATOR'
    _draw(ui._mask, ob)
