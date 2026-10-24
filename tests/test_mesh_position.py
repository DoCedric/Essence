"""The Position mesh map: baked Generated coordinates, and the Gradient generator reading it."""

import math

import bpy

from harness import check, close, make_plane, sample_socket
from test_bake_quality import _bake, _select
from test_generators import _set, _setup, _tex, _value


def _at(img, u, v):
    w, h = img.size
    x, y = min(w - 1, int(u * w)), min(h - 1, int(v * h))
    px = list(img.pixels)
    i = (y * w + x) * 4
    return px[i:i + 3]


def _live_generated(ob, mat, uv):
    tree = mat.node_tree
    tc = tree.nodes.new('ShaderNodeTexCoord')
    v = sample_socket(ob, mat, tc.outputs['Generated'], size=64, uv=uv)[:3]
    tree.nodes.remove(tc)
    return v


def _with_high(low, z=0.2):
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 0, z))
    high = bpy.context.active_object
    high.name = "High"
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(high)
    bpy.context.scene.collection.objects.unlink(high)
    sc = bpy.context.scene
    sc.essence_bake_high = coll
    sc.essence_bake_extrusion = 0.5
    _select(low)
    return high


def test_baked_position_is_the_generated_coordinates_of_a_moved_object(addon):
    ob = make_plane("Low", size=2.0)
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    ob.scale = (2.0, 0.5, 1.0)
    ob.rotation_euler = (0.0, 0.0, math.radians(30))
    ob.location = (1.0, -2.0, 0.5)
    bpy.context.view_layer.update()
    check(_bake('POSITION') == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_Position"]
    check(img.is_float and img.colorspace_settings.name == 'Non-Color', "float, Non-Color")
    slot = mat.node_tree.nodes[addon.nodes.map_slot_name('POSITION')]
    check(slot.image is img, "the bake fills the Position slot")
    for uv in ((0.5, 0.5), (0.25, 0.8), (0.9, 0.1)):
        live = _live_generated(ob, mat, uv)
        got = _at(img, *uv)
        for c, (a, b) in enumerate(zip(got, live)):
            close(a, b, 0.03, f"channel {'XYZ'[c]} at uv {uv}: baked {got} live {live}")
    close(_at(img, 0.9, 0.1)[0], 0.9, 0.03, "x runs along u on this plane")


def test_baked_position_from_a_high_poly_is_the_high_polys_surface(addon):
    low = make_plane("Low", size=2.0)
    bpy.ops.essence.add_layer()
    _with_high(low, z=0.2)
    check(_bake('POSITION') == {'FINISHED'}, "bake failed")
    r, g, b = _at(bpy.data.images["Low_Position"], 0.5, 0.5)
    close(r, 0.5, 0.03, "x at the center")
    close(g, 0.5, 0.03, "y at the center")
    # The low plane has no height, so one unit of z is the whole range: 0.2 above it reads 0.7.
    close(b, 0.7, 0.03, "z of the high-poly surface")


def test_gradient_can_read_the_baked_position(addon):
    ob, mat, e = _setup(addon, 'GRADIENT')
    tree = mat.node_tree
    tex = _tex(addon, mat, e)
    sources = addon.sources
    check(tex.inputs['Baked Position'].is_linked, "the input is never left open")
    check(tree.nodes.get(sources.POSITION_FALLBACK) is not None, "stand-in with an empty slot")
    tex.inputs['Use Baked Position'].default_value = True
    close(_value(ob, mat, e), 0.5, 0.02, "an empty slot reads the live coordinates")
    _with_high(ob, z=0.2)
    check(_bake('POSITION') == {'FINISHED'}, "bake failed")
    tex = _tex(addon, mat, e)
    check(tree.nodes.get(sources.POSITION_FALLBACK) is None, "stand-in gone once baked")
    slot = tree.nodes[addon.nodes.map_slot_name('POSITION')]
    check(tex.inputs["Baked Position"].links[0].from_node.name == slot.name,
          "wired from the slot")
    _select(ob)
    close(_value(ob, mat, e), 0.7, 0.03, "the gradient follows the high poly")
    tex.inputs['Use Baked Position'].default_value = False
    close(_value(ob, mat, e), 0.5, 0.02, "and the switch goes back to live")


def test_a_picked_position_image_works_too(addon):
    ob, mat, e = _setup(addon, 'GRADIENT')
    img = bpy.data.images.new("pos", 8, 8, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    img.generated_color = (0.5, 0.5, 0.9, 1.0)
    mat.node_tree.nodes[addon.nodes.map_slot_name('POSITION')].image = img
    bpy.ops.essence.rebuild()
    tex = _tex(addon, mat, e)
    tex.inputs['Use Baked Position'].default_value = True
    close(_value(ob, mat, e), 0.9, 0.03, "any image in the slot is read")


def test_old_gradient_group_gets_the_new_inputs(addon):
    ob, mat, e = _setup(addon, 'GRADIENT')
    tex = _tex(addon, mat, e)
    _set(tex, Start=0.25)
    ng = tex.node_tree
    ng["essence_version"] = 2                        # a file from before the baked position
    bpy.ops.essence.rebuild()
    tex = _tex(addon, mat, e)
    check(ng["essence_version"] == 3 and 'Use Baked Position' in tex.inputs, "rebuilt")
    close(tex.inputs['Start'].default_value, 0.25, 0.001, "value kept")
    check(tex.inputs['Baked Position'].is_linked, "and wired")


def test_position_slot_and_panels_draw(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, e = _setup(addon, 'GRADIENT')
    log = _draw(ui._maps, ob)
    check(any(c[0] == "template_ID" and getattr(c[1][0], "name", "") ==
              addon.nodes.map_slot_name('POSITION') for c in log), "no Position slot drawn")
    log = _draw(ui._props, ob)
    check(any(c[0] == "prop" and getattr(c[1][0], "name", "") == "Use Baked Position"
              for c in log), "no Use Baked Position switch")
    check(not any(c[0] == "prop" and getattr(c[1][0], "name", "") == "Baked Position"
                  for c in log), "the baked input is internal")
    _draw(ui._bake, ob)
