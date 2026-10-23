import bpy

from harness import check, close, make_plane, sample_socket


def _setup():
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    base = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    base.inputs['Roughness B'].default_value = 0.2
    bpy.ops.essence.add_layer()
    top = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    top.inputs['Roughness B'].default_value = 0.8
    return ob, mat, top


def _add_mask(mat):
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    return mat.essence.layers[mat.essence.active_index]


def _nodes(addon, mat, m):
    return addon.sources.source_nodes(mat.node_tree, m.uid, 'MASK')


def _rough(ob, mat, top):
    return sample_socket(ob, mat, top.outputs['Roughness'])[0]


def test_new_mask_has_image_source(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    check(m.source.channel == 'MASK' and m.source.kind == 'IMAGE', "mask source not set up")
    n = _nodes(addon, mat, m)
    check(set(n) == {'COORD', 'MAPPING', 'TEXTURE', 'REMAP'}, f"roles {sorted(n)}")
    check(m.mask_node_name == n['TEXTURE'].name, "mask_node_name should be the image node")
    check(n['TEXTURE'].image is not None, "painted mask lost its image")
    close(_rough(ob, mat, top), 0.8, 0.02, "white image mask reveals")


def test_procedural_mask(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    m.source.kind = 'PROCEDURAL'
    m.source.proc_type = 'GRADIENT'
    n = _nodes(addon, mat, m)
    check(n['TEXTURE'].bl_idname == 'ShaderNodeGroup', "procedural node missing")
    check(m.mask_node_name == "", "procedural mask should have no image node")
    close(_rough(ob, mat, top), 0.5, 0.03, "gradient mask 0.5 -> halfway")
    n['REMAP'].inputs['To Max'].default_value = 0.5   # center mask = 0.25
    close(_rough(ob, mat, top), 0.35, 0.03, "mask remap")


def test_value_mask(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    m.source.kind = 'VALUE'
    check(not _nodes(addon, mat, m), "value mask should have no source nodes")
    mat.node_tree.nodes[m.node_name].inputs['Value'].default_value = 0.25
    close(_rough(ob, mat, top), 0.35, 0.02, "constant mask 0.25")


def test_image_survives_kind_switch(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    img = _nodes(addon, mat, m)['TEXTURE'].image
    name = img.name
    m.source.kind = 'PROCEDURAL'
    m.source.kind = 'IMAGE'
    n = _nodes(addon, mat, m)
    check(n['TEXTURE'].image is not None and n['TEXTURE'].image.name == name,
          "image not restored after switching kinds")
    check(m.mask_node_name == n['TEXTURE'].name, "mask_node_name not restored")
    m.source.kind = 'VALUE'
    check(bpy.data.images.get(name) is not None and bpy.data.images[name].use_fake_user,
          "painted image would be lost")


def test_mask_projection_and_mapping(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    m.source.projection = 'TRIPLANAR'
    n = _nodes(addon, mat, m)
    check(n['TEXTURE'].projection == 'BOX', "triplanar mask not box projected")
    try:
        bpy.ops.essence.paint_mask()
        refused = False
    except RuntimeError:
        refused = True
    check(refused, "painting must be refused for a non-UV mask")
    m.source.projection = 'UV'
    close(_rough(ob, mat, top), 0.8, 0.02, "mask back on UV")


def test_legacy_mask_gets_a_source(addon):
    """A mask made before sources existed (image linked straight into the mask group)."""
    ob, mat, top = _setup()
    m = _add_mask(mat)
    uid = m.uid
    tree = mat.node_tree
    img_node = tree.nodes[m.mask_node_name]
    group = tree.nodes[m.node_name]
    for n in list(tree.nodes):
        if n.get(addon.sources.SRC_KEY) == f"{uid}:MASK" and n.name != img_node.name:
            tree.nodes.remove(n)
    del img_node[addon.sources.SRC_KEY]
    del img_node[addon.sources.ROLE_KEY]
    for l in list(group.inputs['Value'].links):
        tree.links.remove(l)
    tree.links.new(img_node.outputs['Color'], group.inputs['Value'])
    m.source.channel = ""
    m.source.kind = 'VALUE'
    check(bpy.ops.essence.rebuild() == {'FINISHED'}, "rebuild failed")
    m = next(e for e in mat.essence.layers if e.uid == uid)
    check(m.source.channel == 'MASK' and m.source.kind == 'IMAGE', "legacy mask not adopted")
    check(m.mask_node_name == img_node.name, "image node should stay the same node")
    close(_rough(ob, mat, top), 0.8, 0.02, "legacy mask still works")


def test_remove_mask_removes_source_nodes(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    m.source.kind = 'PROCEDURAL'
    bpy.ops.essence.remove_layer()
    left = [n.name for n in mat.node_tree.nodes if addon.sources.SRC_KEY in n]
    check(not left, f"source nodes left: {left}")


def test_second_value_stacks_as_a_fill(addon):
    ob, mat, top = _setup()
    _add_mask(mat)                       # white image
    bpy.ops.essence.add_fill()
    fill = mat.essence.layers[mat.essence.active_index]
    fill.fill_blend = 'MULTIPLY'
    mat.node_tree.nodes[fill.node_name].inputs[1].default_value = 0.5
    close(_rough(ob, mat, top), 0.5, 0.02, "mask 1.0 * fill 0.5")


def test_one_mask_per_layer(addon):
    ob, mat, top = _setup()
    _add_mask(mat)
    check(not bpy.ops.essence.add_mask.poll(), "a second mask must not be possible")
    uid = mat.essence.layers[-1].uid
    check(sum(1 for e in mat.essence.layers if e.kind == 'MASK') == 1, "one mask expected")
    # Another layer can still have its own.
    bpy.ops.essence.add_layer()
    check(bpy.ops.essence.add_mask.poll(), "a layer without a mask can take one")


def test_switching_source_in_paint_mode_keeps_painting(addon):
    ob, mat, top = _setup()
    m = _add_mask(mat)
    bpy.ops.essence.paint_mask()
    check(ob.mode == 'TEXTURE_PAINT', "not in paint mode")
    uid = m.uid
    m.source.kind = 'PROCEDURAL'
    m.source.kind = 'IMAGE'
    m = next(e for e in mat.essence.layers if e.uid == uid)
    check(ob.mode == 'TEXTURE_PAINT', f"paint mode lost: {ob.mode}")
    tex = mat.node_tree.nodes[m.mask_node_name]
    check(mat.node_tree.nodes.active.name == tex.name, "image node not active after switching back")
