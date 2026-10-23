import bpy

from harness import check, close, make_plane, sample_socket


def _setup(base_value=None):
    """Two layers (rough 0.2 below, 0.8 on top) and a mask on the top one. Returns
    (ob, mat, top node, top uid, mask uid). With `base_value` the mask is a plain value."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.node_tree.nodes[mat.essence.layers[-1].node_name].inputs['Roughness B'].default_value = 0.2
    bpy.ops.essence.add_layer()
    e = mat.essence.layers[mat.essence.active_index]
    top_uid = e.uid
    top = mat.node_tree.nodes[e.node_name]
    top.inputs['Roughness B'].default_value = 0.8
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    mask = mat.essence.layers[mat.essence.active_index]
    mask_uid = mask.uid
    if base_value is not None:
        mask.source.kind = 'VALUE'
        mat.node_tree.nodes[mask.node_name].inputs['Value'].default_value = base_value
    return ob, mat, top, top_uid, mask_uid


def _entry(mat, uid):
    return next(e for e in mat.essence.layers if e.uid == uid)


def _rough(ob, mat, top):
    return sample_socket(ob, mat, top.outputs['Roughness'])[0]


def _add_fill(mat, mask_uid, value=None, blend='MULTIPLY'):
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    check(bpy.ops.essence.add_fill() == {'FINISHED'}, "add fill failed")
    e = mat.essence.layers[mat.essence.active_index]
    check(e.kind == 'FILL', "the new fill should be active")
    uid = e.uid
    if blend != 'MULTIPLY':
        e.fill_blend = blend
    if value is not None:
        _blend_node(mat, uid).inputs[1].default_value = value
    return uid


def _blend_node(mat, fill_uid):
    return bpy.data.materials[mat.name].node_tree.nodes[_entry(mat, fill_uid).node_name]


def test_fill_entries_order_and_removal(addon):
    ob, mat, top, top_uid, mask_uid = _setup()
    f = _add_fill(mat, mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')     # on top of the fill (active is the fill)
    kinds = [(e.kind) for e in mat.essence.layers]
    check(kinds == ['LAYER', 'FILL', 'ADJUSTMENT', 'MASK', 'LAYER'], f"order {kinds}")
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == top_uid)
    bpy.ops.essence.remove_layer()
    left = [n.name for n in mat.node_tree.nodes if n.get(addon.sources.SRC_KEY)]
    check(not left, f"nodes left behind after removing the layer: {left}")
    check(f not in [e.uid for e in mat.essence.layers], "fill should be gone with its layer")


def test_fill_multiply_reveals_and_hides(addon):
    ob, mat, top, top_uid, mask_uid = _setup()
    close(_rough(ob, mat, top), 0.8, 0.02, "white mask reveals")
    f = _add_fill(mat, mask_uid)                        # a white value fill: no change
    close(_rough(ob, mat, top), 0.8, 0.02, "white fill multiplies by 1")
    _blend_node(mat, f).inputs[1].default_value = 0.0
    close(_rough(ob, mat, top), 0.2, 0.02, "black fill hides")


def test_fill_blend_modes(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.5)
    close(_rough(ob, mat, top), 0.5, 0.02, "base mask 0.5")      # 0.2 + 0.6 * 0.5
    f = _add_fill(mat, mask_uid, value=0.4)
    for mode, mask_value in (('MULTIPLY', 0.2), ('ADD', 0.9), ('SUBTRACT', 0.1),
                             ('MINIMUM', 0.4), ('MAXIMUM', 0.5)):
        _entry(mat, f).fill_blend = mode
        close(_rough(ob, mat, top), 0.2 + 0.6 * mask_value, 0.02, f"blend {mode}")


def test_fill_opacity_and_eye(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.5)
    f = _add_fill(mat, mask_uid, value=0.0)             # multiply by 0 -> mask 0
    close(_rough(ob, mat, top), 0.2, 0.02, "fill hides")
    mix = addon.sources.source_nodes(mat.node_tree, f, 'ADJ')['MIX']
    addon.adjustments.mix_socket(mix, "Factor_Float").default_value = 0.5   # mask 0.25
    close(_rough(ob, mat, top), 0.2 + 0.6 * 0.25, 0.02, "fill at half opacity")
    _entry(mat, f).adj_enabled = False
    close(_rough(ob, mat, top), 0.5, 0.02, "disabled fill does nothing")


def test_fill_image_and_procedural_sources(addon):
    ob, mat, top, top_uid, mask_uid = _setup()
    f = _add_fill(mat, mask_uid)
    img = bpy.data.images.new("half", 8, 8, float_buffer=True)
    img.generated_color = (0.5, 0.5, 0.5, 1.0)
    img.colorspace_settings.name = 'Non-Color'
    _entry(mat, f).source.kind = 'IMAGE'
    nodes = addon.sources.source_nodes(mat.node_tree, f, 'MASK')
    check(set(nodes) == {'COORD', 'MAPPING', 'TEXTURE', 'REMAP'}, f"fill roles {sorted(nodes)}")
    nodes['TEXTURE'].image = img
    close(_rough(ob, mat, top), 0.5, 0.02, "image fill 0.5 multiplies the white mask")
    _entry(mat, f).source.kind = 'PROCEDURAL'
    _entry(mat, f).source.proc_type = 'GRADIENT'
    check(addon.sources.source_nodes(mat.node_tree, f, 'MASK')['TEXTURE'].bl_idname
          == 'ShaderNodeGroup', "procedural fill node missing")
    close(_rough(ob, mat, top), 0.5, 0.05, "gradient fill is 0.5 at the center")
    _entry(mat, f).source.kind = 'VALUE'
    close(_rough(ob, mat, top), 0.8, 0.02, "value fill 1 again")
    check(not addon.sources.source_nodes(mat.node_tree, f, 'MASK'), "source nodes left over")


def test_items_apply_in_order(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.5)
    f = _add_fill(mat, mask_uid, value=0.4)             # 0.5 * 0.4 = 0.2
    close(_rough(ob, mat, top), 0.2 + 0.6 * 0.2, 0.02, "fill")
    bpy.ops.essence.add_adjustment(adj_type='INVERT')   # above the fill: 1 - 0.2 = 0.8
    close(_rough(ob, mat, top), 0.2 + 0.6 * 0.8, 0.02, "invert after the fill")
    bpy.ops.essence.move_layer(direction='DOWN')        # invert first: (1 - 0.5) * 0.4 = 0.2
    close(_rough(ob, mat, top), 0.2 + 0.6 * 0.2, 0.02, "invert before the fill")
    items = [e.kind for e in mat.essence.layers if e.kind in ('FILL', 'ADJUSTMENT')]
    check(items == ['ADJUSTMENT', 'FILL'], f"order after move: {items}")


def test_fill_removal_restores_the_mask(addon):
    ob, mat, top, top_uid, mask_uid = _setup()
    f = _add_fill(mat, mask_uid, value=0.0)
    close(_rough(ob, mat, top), 0.2, 0.02, "fill hides")
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == f)
    bpy.ops.essence.remove_layer()
    close(_rough(ob, mat, top), 0.8, 0.02, "mask back to white after removing the fill")
    check(not [n for n in mat.node_tree.nodes if (n.get(addon.sources.SRC_KEY) or "").startswith(f)],
          "fill nodes left behind")


def test_add_fill_only_on_masks(addon):
    ob, mat, top, top_uid, mask_uid = _setup()
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == top_uid)
    check(not bpy.ops.essence.add_fill.poll(), "a layer cannot take a fill")
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    check(bpy.ops.essence.add_fill.poll(), "a mask can take a fill")
