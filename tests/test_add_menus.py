"""The two Add menus of the stack bar, and adding a generator from them."""

from types import SimpleNamespace

import bpy

from harness import check, close, make_plane, sample_socket
from test_generators import _aim


def _active(mat):
    return mat.essence.layers[mat.essence.active_index]


def _mask_of(mat, owner_uid):
    return next((x for x in mat.essence.layers if x.kind == 'MASK' and x.parent == owner_uid),
                None)


def _mask_value(addon, ob, mat, mask, uv):
    out = mat.node_tree.nodes[mask.node_name].outputs[0]
    return sample_socket(ob, mat, out, size=64, uv=uv)[0]


def test_generator_on_a_layer_without_a_mask_makes_the_mask(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    layer = _active(mat).uid
    images = len(bpy.data.images)
    check(bpy.ops.essence.add_generator(generator='GRADIENT') == {'FINISHED'}, "add failed")
    mask = _mask_of(mat, layer)
    check(mask is not None, "the layer got a mask")
    check(_active(mat).uid == mask.uid, "the new mask is selected")
    check(mask.name == "Gradient", f"named after the generator: {mask.name}")
    check(mask.source.kind == 'GENERATOR' and mask.source.gen_type == 'GRADIENT',
          f"source {mask.source.kind} {mask.source.gen_type}")
    check(len(bpy.data.images) == images, "no paint image made for a generator mask")
    tex = addon.sources.source_nodes(mat.node_tree, mask.uid, 'MASK')['TEXTURE']
    _aim(tex, (1, 0, 0))
    close(_mask_value(addon, ob, mat, mask, (0.9, 0.5)), 0.9, 0.03, "the mask is the gradient")


def test_generator_goes_onto_an_existing_mask(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    layer = _active(mat).uid
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)
    mask = _active(mat).uid
    # With the layer selected: on top of its mask.
    mat.essence.active_index = [e.uid for e in mat.essence.layers].index(layer)
    check(bpy.ops.essence.add_generator(generator='GRADIENT') == {'FINISHED'}, "add failed")
    fill = _active(mat)
    check(fill.kind == 'FILL' and fill.parent == mask, f"{fill.kind} under {fill.parent}")
    check(fill.name == "Gradient", f"named after the generator: {fill.name}")
    check(fill.source.kind == 'GENERATOR' and fill.source.gen_type == 'GRADIENT', "source")
    tex = addon.sources.source_nodes(mat.node_tree, fill.uid, 'MASK')['TEXTURE']
    _aim(tex, (0, 1, 0))
    close(_mask_value(addon, ob, mat, _mask_of(mat, layer), (0.9, 0.25)), 0.25, 0.03,
          "the fill replaces the white mask value")
    # With the mask or one of its items selected: on top of that mask too.
    for uid in (mask, fill.uid):
        mat.essence.active_index = [e.uid for e in mat.essence.layers].index(uid)
        check(bpy.ops.essence.add_generator(generator='EDGE_WEAR') == {'FINISHED'}, "add")
        check(_active(mat).parent == mask, "on the same mask")
    check(sum(1 for e in mat.essence.layers if e.kind == 'MASK') == 1, "still one mask")
    check(bpy.ops.essence.add_generator(generator='NOPE') == {'CANCELLED'}, "unknown generator")


def test_generator_on_a_folder(addon):
    ob = make_plane()
    bpy.ops.essence.add_folder()
    mat = ob.active_material
    folder = _active(mat).uid
    bpy.ops.essence.add_generator(generator='SLOPE')
    mask = _mask_of(mat, folder)
    check(mask is not None and mask.source.gen_type == 'SLOPE', "the folder got a slope mask")


def test_plain_fill_and_mask_unchanged(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    images = len(bpy.data.images)
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)
    check(_active(mat).name == "Mask" and len(bpy.data.images) == images + 1, "paint mask")
    bpy.ops.essence.add_fill()
    check(_active(mat).name == "Fill" and _active(mat).source.kind == 'VALUE', "plain fill")


def test_menus_draw(addon):
    from test_ui import Rec, _ctx, _draw
    from essence import ops, ui
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)

    def draw(menu):
        log = []
        menu.draw(SimpleNamespace(layout=Rec(log)), _ctx(ob))
        return log

    log = draw(ops.ESSENCE_MT_add)
    check([c[1][0] for c in log if c[0] == "operator"] == [
        "essence.add_layer", "essence.add_paint_layer", "essence.add_folder"], "Add")
    log = draw(ops.ESSENCE_MT_add_to)
    check([c[1][0] for c in log if c[0] == "menu"] == [
        "ESSENCE_MT_add_mask", "ESSENCE_MT_add_generator", "ESSENCE_MT_add_filter",
        "ESSENCE_MT_add_adjustment"], f"submenus {[c[1] for c in log if c[0] == 'menu']}")
    check({"essence.add_fill", "essence.add_paint"} <= {c[1][0] for c in log
                                                         if c[0] == "operator"}, "fill, paint")
    for menu, op, count in ((ops.ESSENCE_MT_add_generator, "essence.add_generator",
                             len(addon.generators.GENERATORS)),
                            (ops.ESSENCE_MT_add_filter, "essence.add_filter",
                             len(addon.filters.TYPES)),
                            (ops.ESSENCE_MT_add_adjustment, "essence.add_adjustment",
                             len(addon.adjustments.TYPES))):
        n = sum(1 for c in draw(menu) if c[0] == "operator" and c[1][0] == op)
        check(n == count, f"{menu.bl_idname}: {n} of {count}")
    for idname in ("ESSENCE_MT_add", "ESSENCE_MT_add_to", "ESSENCE_MT_add_generator",
                   "ESSENCE_MT_add_filter", "ESSENCE_MT_add_adjustment", "ESSENCE_MT_add_mask"):
        check(hasattr(bpy.types, idname), f"{idname} registered")
    bar = _draw(ui._stack, ob)
    menus = [c[1][0] for c in bar if c[0] == "menu"]
    check(menus == ["ESSENCE_MT_add", "ESSENCE_MT_add_to"], f"bar menus {menus}")
    check(not {"essence.add_layer", "essence.add_folder"} & {c[1][0] for c in bar
                                                              if c[0] == "operator"},
          "no separate add buttons on the bar")
