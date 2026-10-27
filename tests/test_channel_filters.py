"""Baked filters on a layer channel (the layer's own fill of one channel), not only on masks."""

import os
import shutil
import tempfile

import bpy

from harness import check, close, make_plane, sample_socket


def _edge_image(name, left, right, size=64):
    """Left of u = 0.45 and right of it: each a grey value or an (r, g, b) tuple. Non-Color."""
    img = bpy.data.images.new(name, size, size, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    lo = left if isinstance(left, tuple) else (left,) * 3
    hi = right if isinstance(right, tuple) else (right,) * 3
    cut = int(0.45 * size)
    px = []
    for _y in range(size):
        for x in range(size):
            px += list(lo if x < cut else hi) + [1.0]
    img.pixels.foreach_set(px)
    img.update()
    return img


def _layer_with_source(addon, channel, image):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    next(s for s in e.sources if s.channel == channel).kind = 'IMAGE'
    addon.sources.source_nodes(mat.node_tree, e.uid, channel)['TEXTURE'].image = image
    return ob, mat, e.uid


def _entry(mat, uid):
    return next(x for x in mat.essence.layers if x.uid == uid)


def _select(mat, uid):
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == uid)


def _add_filter(mat, owner_uid, filter_type='BLUR', channel=None, resolution='256'):
    _select(mat, owner_uid)
    check(bpy.ops.essence.add_filter(filter_type=filter_type) == {'FINISHED'}, "add failed")
    e = mat.essence.layers[mat.essence.active_index]
    check(e.kind == 'FILTER' and e.parent == owner_uid, "the new filter should be active")
    if channel is not None:
        e.adj_channel = channel
    e.filter_resolution = resolution
    return e.uid


def _out(ob, mat, layer_uid, socket):
    node = mat.node_tree.nodes[_entry(mat, layer_uid).node_name]
    return sample_socket(ob, mat, node.outputs[socket], size=64)


def _widen(addon, mat, filter_uid):
    addon.filters.filter_node(_entry(mat, filter_uid)).inputs['Size'].default_value = (30.0, 30.0)


def test_layer_channel_filter_blurs_the_edge(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge", 0.0, 1.0))
    close(_out(ob, mat, lid, 'Roughness')[0], 1.0, 0.02, "right of the edge")
    f = _add_filter(mat, lid, 'BLUR', 'ROUGHNESS')
    close(_out(ob, mat, lid, 'Roughness')[0], 1.0, 0.02, "an unbaked filter passes through")
    _widen(addon, mat, f)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    v = _out(ob, mat, lid, 'Roughness')[0]
    check(0.5 < v < 0.97, f"blur should soften the edge: {v}")
    _entry(mat, f).adj_enabled = False
    close(_out(ob, mat, lid, 'Roughness')[0], 1.0, 0.02, "disabled filter")


def test_layer_channel_filter_keeps_color(addon):
    img = _edge_image("color_edge", (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    ob, mat, lid = _layer_with_source(addon, 'COLOR', img)
    f = _add_filter(mat, lid, 'BLUR', 'COLOR')
    _widen(addon, mat, f)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    r, g, b, _a = _out(ob, mat, lid, 'Color')
    check(0.1 < r < 0.97 and 0.03 < b < 0.9, f"red and blue should mix at the edge: {r}, {b}")
    check(g < 0.05, f"green must stay out of it: {g}")
    result = bpy.data.images[addon.adjustments.FILTER_IMAGE_PREFIX + f]
    check(result.is_float and tuple(result.size) == (256, 256), "float result at the set size")


def test_channels_are_separate_chains(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge2", 0.0, 1.0))
    f_rough = _add_filter(mat, lid, 'BLUR', 'ROUGHNESS')
    f_color = _add_filter(mat, lid, 'BLUR', 'COLOR')
    check(bpy.ops.essence.filter_apply(uid=f_color) == {'FINISHED'}, "apply failed")
    check(addon.filters.is_baked(_entry(mat, f_color)), "the color filter should be baked")
    check(not addon.filters.is_baked(_entry(mat, f_rough)),
          "applying the Color filter must not apply the one on Roughness")
    check(not addon.filters.lower_filters(mat, _entry(mat, f_rough)),
          "a filter on another channel is not below this one")
    bpy.ops.essence.filter_apply(uid=f_rough)
    # A change in the other channel's chain leaves the roughness filter current.
    _select(mat, lid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')
    adj = mat.essence.layers[mat.essence.active_index]
    adj.adj_channel = 'COLOR'
    check(not addon.filters.is_stale(mat, _entry(mat, f_rough)),
          "an adjustment on Color made the Roughness filter stale")


def test_filter_goes_stale_with_its_channel(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge3", 0.0, 1.0))
    f = _add_filter(mat, lid, 'BLUR', 'ROUGHNESS')
    bpy.ops.essence.filter_apply(uid=f)
    flt = addon.filters
    check(not flt.needs_apply(mat, _entry(mat, f)), "a fresh result should be current")
    nodes = addon.sources.source_nodes(mat.node_tree, lid, 'ROUGHNESS')
    nodes['REMAP'].inputs['To Max'].default_value = 0.5
    check(flt.is_stale(mat, _entry(mat, f)), "changing the channel's source should go stale")
    bpy.ops.essence.filter_apply(uid=f)
    check(not flt.needs_apply(mat, _entry(mat, f)), "applying again makes it current")
    nodes['TEXTURE'].location = (123, 456)
    check(not flt.is_stale(mat, _entry(mat, f)), "moving a node must not go stale")
    # An adjustment added later sits above the filter: it is not part of what was baked.
    _select(mat, lid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')
    mat.essence.layers[mat.essence.active_index].adj_channel = 'ROUGHNESS'
    check(not flt.is_stale(mat, _entry(mat, f)), "an adjustment above the filter went stale")


def test_dependencies_and_apply_all_cover_layer_filters(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge4", 0.0, 1.0))
    low = _add_filter(mat, lid, 'BLUR', 'ROUGHNESS')
    high = _add_filter(mat, lid, 'KUWAHARA', 'ROUGHNESS')
    order = [e.uid for e in mat.essence.layers if e.uid in (low, high)]
    check(order == [low, high], f"stack order {order}")
    check([e.uid for e in addon.filters.lower_filters(mat, _entry(mat, high))] == [low],
          "the blur is below the Kuwahara")
    check(bpy.ops.essence.filter_apply(uid=high) == {'FINISHED'}, "apply failed")
    check(addon.filters.is_baked(_entry(mat, low)), "the lower filter is applied first")
    check(not addon.filters.any_unapplied(mat), "everything should be current")
    addon.filters.filter_node(_entry(mat, low)).inputs['Size'].default_value = (4.0, 4.0)
    check(addon.filters.needs_apply(mat, _entry(mat, low)), "the lower filter changed")
    check(bpy.ops.essence.filter_apply_all() == {'FINISHED'}, "apply all failed")
    check(not addon.filters.any_unapplied(mat), "apply all should leave nothing stale")


def test_new_filter_takes_the_selected_items_channel(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge5", 0.0, 1.0))
    f = _add_filter(mat, lid, 'BLUR')
    check(_entry(mat, f).adj_channel == 'COLOR', "a filter on a layer starts on Color")
    _entry(mat, f).adj_channel = 'ROUGHNESS'
    _select(mat, f)
    check(bpy.ops.essence.add_filter(filter_type='BLUR') == {'FINISHED'}, "add failed")
    g = mat.essence.layers[mat.essence.active_index]
    check(g.adj_channel == 'ROUGHNESS', f"added from a Roughness item, got {g.adj_channel}")
    d = _add_filter(mat, lid, 'DILATE_ERODE')
    ch = addon.channels.BY_ID[_entry(mat, d).adj_channel]
    check(ch.socket_type != 'NodeSocketColor', f"Dilate/Erode started on a color: {ch.id}")


def test_switched_off_channel_reports_instead_of_crashing(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge6", 0.0, 1.0))
    f = _add_filter(mat, lid, 'BLUR', 'EMISSION')   # Emission is off by default
    try:   # an operator that reports an error raises it as a RuntimeError in a script
        bpy.ops.essence.filter_apply(uid=f)
        refused = None
    except RuntimeError as e:
        refused = str(e)
    check(refused and "switched off" in refused, f"a switched-off channel should be refused: {refused}")
    check(not addon.filters.is_baked(_entry(mat, f)), "nothing should be baked")


def test_removing_the_layer_removes_its_filters(addon):
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge7", 0.0, 1.0))
    f = _add_filter(mat, lid, 'BLUR', 'ROUGHNESS')
    bpy.ops.essence.filter_apply(uid=f)
    _select(mat, lid)
    bpy.ops.essence.remove_layer()
    check(not [e for e in mat.essence.layers if e.kind == 'FILTER'], "filter entry left behind")
    check(not [i for i in bpy.data.images if i.name.startswith(".Essence Filter ")],
          "result image left behind")
    check(not [g for g in bpy.data.node_groups if g.name.startswith("Essence Filter ")],
          "compositor group left behind")


def test_layer_filter_survives_save_and_reload(addon):
    img = _edge_image("rough_edge8", 0.0, 1.0)
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', img)
    img.pack()
    f = _add_filter(mat, lid, 'BLUR', 'ROUGHNESS')
    addon.filters.filter_node(_entry(mat, f)).inputs['Size'].default_value = (7.0, 3.0)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    name = ob.name
    folder = tempfile.mkdtemp(prefix="essence_save_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        e = _entry(mat, f)
        check(e.adj_channel == 'ROUGHNESS' and e.parent == lid, "channel and owner kept")
        node = addon.filters.filter_node(e)
        check(node is not None and tuple(node.inputs['Size'].default_value) == (7.0, 3.0),
              "compositor settings lost")
        check(not addon.filters.is_stale(mat, e), "the reloaded filter should still be current")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_layer_filter_panel_and_rows_draw(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, lid = _layer_with_source(addon, 'ROUGHNESS', _edge_image("rough_edge9", 0.0, 1.0))
    for t in addon.filters.TYPES:
        _add_filter(mat, lid, t.id, 'COLOR')   # Dilate on a color draws its warning
        log = _draw(ui._adjustment, ob)
        check(any(c[0] == "operator" and c[1][0] == "essence.filter_apply" for c in log),
              f"{t.id}: no Apply button")
        _draw(ui._stack, ob)
    _draw(ui._stack, ob)
