import bpy

from harness import check, close, make_plane, sample_socket


def _layer(ob, color=(0.8, 0.4, 0.2, 1.0), rough=0.5):
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    node = mat.node_tree.nodes[e.node_name]
    node.inputs['Color B'].default_value = color
    node.inputs['Roughness B'].default_value = rough
    return mat, e.uid, node


def _entry(mat, uid):
    return next(e for e in mat.essence.layers if e.uid == uid)


def _select(mat, uid):
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == uid)


def _add(mat, owner_uid, adj_type, channel='COLOR'):
    _select(mat, owner_uid)
    check(bpy.ops.essence.add_adjustment(adj_type=adj_type) == {'FINISHED'}, "add failed")
    e = mat.essence.layers[mat.essence.active_index]
    check(e.kind == 'ADJUSTMENT', "new adjustment should be active")
    uid = e.uid
    if channel != 'COLOR':
        e.adj_channel = channel
    return uid


def _nodes(addon, mat, adj_uid):
    return addon.sources.source_nodes(mat.node_tree, adj_uid, 'ADJ')


def _color(ob, mat, node):
    return sample_socket(ob, mat, node.outputs['Color'])


def _rough(ob, mat, node):
    return sample_socket(ob, mat, node.outputs['Roughness'])[0]


def test_adjustment_entries_order_and_removal(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob)
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    mask_uid = mat.essence.layers[mat.essence.active_index].uid
    a1 = _add(mat, uid, 'INVERT')
    a2 = _add(mat, mask_uid, 'GAMMA')
    # Stored bottom-to-top: a mask's items sit under the mask, a layer's items above its masks.
    order = [e.uid for e in mat.essence.layers]
    check(order == [a2, mask_uid, a1, uid], f"order {order}")
    # Removing the layer takes its mask and every item with it, nodes included.
    _select(mat, uid)
    bpy.ops.essence.remove_layer()
    check(len(mat.essence.layers) == 0, f"left {[e.kind for e in mat.essence.layers]}")
    left = [n.name for n in mat.node_tree.nodes if n.get(addon.sources.SRC_KEY)]
    check(not left, f"source/adjustment nodes left behind: {left}")


def test_color_adjustments_on_a_value(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob, color=(0.8, 0.4, 0.2, 1.0))
    close(_color(ob, mat, node)[0], 0.8, 0.02, "untouched red")

    inv = _add(mat, uid, 'INVERT')
    c = _color(ob, mat, node)
    close(c[0], 0.2, 0.02, "invert r")
    close(c[1], 0.6, 0.02, "invert g")
    close(c[2], 0.8, 0.02, "invert b")
    # Opacity blends original and adjusted.
    mix = _nodes(addon, mat, inv)['MIX']
    addon.adjustments.mix_socket(mix, "Factor_Float").default_value = 0.5
    close(_color(ob, mat, node)[0], 0.5, 0.02, "invert at 50% opacity")
    # Disabling restores the original and returns the value to the layer's own socket.
    _entry(mat, inv).adj_enabled = False
    close(_color(ob, mat, node)[0], 0.8, 0.02, "disabled adjustment must not act")
    check(not addon.sources.source_nodes(mat.node_tree, uid, 'COLOR'),
          "constant node should be gone with nothing to adjust")
    close(node.inputs['Color B'].default_value[0], 0.8, 0.001, "socket value after disabling")


def test_gamma_levels_saturation(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob, color=(0.8, 0.4, 0.2, 1.0))
    g = _add(mat, uid, 'GAMMA')
    _nodes(addon, mat, g)['MAIN'].inputs['Gamma'].default_value = 2.0
    c = _color(ob, mat, node)
    close(c[0], 0.8 ** 2, 0.02, "gamma 2 red")      # Blender's Gamma node: value ** gamma
    close(c[1], 0.4 ** 2, 0.02, "gamma 2 green")
    _entry(mat, g).adj_enabled = False

    lv = _add(mat, uid, 'LEVELS')
    lvn = _nodes(addon, mat, lv)['MAIN']
    lvn.inputs['Input Black'].default_value = 0.2
    lvn.inputs['Input White'].default_value = 0.6
    c = _color(ob, mat, node)
    close(c[0], 1.0, 0.02, "levels clamps above white")
    close(c[1], 0.5, 0.02, "levels midpoint")
    close(c[2], 0.0, 0.02, "levels at black")
    lvn.inputs['Output Black'].default_value = 0.1
    lvn.inputs['Output White'].default_value = 0.5
    close(_color(ob, mat, node)[1], 0.3, 0.02, "levels output range")
    _entry(mat, lv).adj_enabled = False

    hs = _add(mat, uid, 'HUE_SAT')
    _nodes(addon, mat, hs)['MAIN'].inputs['Saturation'].default_value = 0.0
    c = _color(ob, mat, node)
    check(abs(c[0] - c[1]) < 0.02 and abs(c[1] - c[2]) < 0.02, f"saturation 0 is grey: {c}")


def test_brightness_curves_gradient_threshold(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob, color=(0.4, 0.4, 0.4, 1.0), rough=0.5)
    bc = _add(mat, uid, 'BRIGHT_CONTRAST')
    _nodes(addon, mat, bc)['MAIN'].inputs['Bright'].default_value = 0.2
    check(_color(ob, mat, node)[0] > 0.55, "brightness should brighten")
    _entry(mat, bc).adj_enabled = False

    cv = _add(mat, uid, 'CURVES')            # default curve is the identity
    close(_color(ob, mat, node)[0], 0.4, 0.03, "identity curve")
    _entry(mat, cv).adj_enabled = False

    gm = _add(mat, uid, 'GRADIENT_MAP')      # black to white by value
    c = _color(ob, mat, node)
    check(abs(c[0] - c[1]) < 0.02 and 0.2 < c[0] < 0.6, f"gradient map of mid grey: {c}")
    ramp = _nodes(addon, mat, gm)['MAIN'].color_ramp
    ramp.elements[1].color = (1.0, 0.0, 0.0, 1.0)
    c = _color(ob, mat, node)
    check(c[0] > c[1] + 0.1, f"gradient end color should tint the result: {c}")
    _entry(mat, gm).adj_enabled = False

    th = _add(mat, uid, 'THRESHOLD', channel='ROUGHNESS')   # on a float channel
    n = _nodes(addon, mat, th)['MAIN']
    n.inputs['From Min'].default_value = 0.6
    n.inputs['From Max'].default_value = 0.7
    close(_rough(ob, mat, node), 0.0, 0.02, "below threshold")
    n.inputs['From Min'].default_value = 0.3
    n.inputs['From Max'].default_value = 0.4
    close(_rough(ob, mat, node), 1.0, 0.02, "above threshold")


def test_chain_order_and_move(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob, color=(0.8, 0.4, 0.2, 1.0))
    inv = _add(mat, uid, 'INVERT')
    g = _add(mat, uid, 'GAMMA')                  # applied after the invert (added on top)
    _nodes(addon, mat, g)['MAIN'].inputs['Gamma'].default_value = 2.0
    close(_color(ob, mat, node)[0], 0.2 ** 2, 0.02, "invert, then gamma")
    _select(mat, g)
    bpy.ops.essence.move_layer(direction='DOWN')
    close(_color(ob, mat, node)[0], 1 - 0.8 ** 2, 0.02, "gamma, then invert")
    order = [e.uid for e in mat.essence.layers if e.kind == 'ADJUSTMENT']
    check(order == [g, inv], f"order after move (bottom-to-top): {order}")


def test_image_source_and_float_channel(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob)
    img = bpy.data.images.new("rough", 8, 8, float_buffer=True)
    img.generated_color = (0.75, 0.75, 0.75, 1.0)
    img.colorspace_settings.name = 'Non-Color'
    e = _entry(mat, uid)
    next(s for s in e.sources if s.channel == 'ROUGHNESS').kind = 'IMAGE'
    addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS')['TEXTURE'].image = img
    close(_rough(ob, mat, node), 0.75, 0.02, "image roughness")
    a = _add(mat, uid, 'INVERT', channel='ROUGHNESS')
    close(_rough(ob, mat, node), 0.25, 0.02, "inverted image")
    # Switching the source to a value keeps the adjustment working on a constant.
    next(s for s in _entry(mat, uid).sources if s.channel == 'ROUGHNESS').kind = 'VALUE'
    close(_rough(ob, mat, node), 0.5, 0.02, "invert of value 0.5")
    _entry(mat, a).adj_enabled = False
    check(not addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS'), "constant left over")


def test_mask_adjustment(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    base = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    base.inputs['Roughness B'].default_value = 0.2
    mat, uid, top = _layer(ob, rough=0.8)
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    mask_uid = mat.essence.layers[mat.essence.active_index].uid
    close(_rough(ob, mat, top), 0.8, 0.02, "white mask reveals")
    a = _add(mat, mask_uid, 'INVERT')
    close(_rough(ob, mat, top), 0.2, 0.02, "inverted mask hides")
    # A Value mask works too (a constant is adjusted).
    _entry(mat, mask_uid).source.kind = 'VALUE'
    close(_rough(ob, mat, top), 0.2, 0.02, "inverted value mask")
    _entry(mat, a).adj_enabled = False
    close(_rough(ob, mat, top), 0.8, 0.02, "mask without adjustment")


def test_type_switch_and_hand_edits_survive_rebuild(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob)
    a = _add(mat, uid, 'LEVELS')
    _nodes(addon, mat, a)['MAIN'].inputs['Input Black'].default_value = 0.1
    bpy.ops.essence.rebuild()
    n = _nodes(addon, mat, a)['MAIN']
    close(n.inputs['Input Black'].default_value, 0.1, 0.001, "hand edit lost on rebuild")
    _entry(mat, a).adj_type = 'INVERT'
    mains = [x for x in mat.node_tree.nodes
             if x.get(addon.sources.SRC_KEY, "").startswith(a)
             and x.get(addon.sources.ROLE_KEY) == 'MAIN']
    check(len(mains) == 1 and mains[0].bl_idname == 'ShaderNodeInvert',
          f"type switch should replace the node: {[m.bl_idname for m in mains]}")
    close(_color(ob, mat, node)[0], 0.2, 0.02, "invert after switching type")


def test_color_only_type_moves_to_the_color_channel(addon):
    ob = make_plane()
    mat, uid, node = _layer(ob)
    a = _add(mat, uid, 'INVERT', channel='ROUGHNESS')
    _entry(mat, a).adj_type = 'HUE_SAT'
    check(_entry(mat, a).adj_channel == 'COLOR',
          "color-only adjustment must act on a color channel")
