import bpy

from harness import check, close, make_plane, sample_socket


def _layer(ob):
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    return mat, e, mat.node_tree.nodes[e.node_name]


def _src(e, ch):
    return next(s for s in e.sources if s.channel == ch)


def _flat_image(name, value, colorspace='Non-Color'):
    img = bpy.data.images.new(name, 16, 16, float_buffer=True)
    img.generated_color = (value, value, value, 1.0)
    img.colorspace_settings.name = colorspace
    return img


def _nodes(addon, mat, e, ch):
    return addon.sources.source_nodes(mat.node_tree, e.uid, ch)


def test_source_entries_exist(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    check([s.channel for s in e.sources] == [c.id for c in addon.channels.CHANNELS],
          "layer should have one source per channel")
    check(all(s.kind == 'VALUE' for s in e.sources), "default source should be Value")
    check(not _nodes(addon, mat, e, 'ROUGHNESS'), "Value must not create nodes")


def test_image_source_roughness(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    _src(e, 'ROUGHNESS').kind = 'IMAGE'
    n = _nodes(addon, mat, e, 'ROUGHNESS')
    check(set(n) == {'COORD', 'MAPPING', 'TEXTURE', 'REMAP'}, f"roles: {sorted(n)}")
    n['TEXTURE'].image = _flat_image("rough", 0.3)
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.3, 0.02, "image roughness")
    n['REMAP'].inputs['To Max'].default_value = 0.5   # 0.3 maps into 0..0.5
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.15, 0.02, "remap")


def test_image_source_height_remap(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    _src(e, 'BUMP').kind = 'IMAGE'
    n = _nodes(addon, mat, e, 'BUMP')
    n['TEXTURE'].image = _flat_image("height", 0.75)
    close(sample_socket(ob, mat, node.outputs['Bump'])[0], 0.5, 0.02, "0.75 grey = +0.5")
    n['TEXTURE'].image = _flat_image("height2", 0.5)
    close(sample_socket(ob, mat, node.outputs['Bump'])[0], 0.0, 0.02, "mid grey = 0")


def test_image_source_color_no_remap(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    _src(e, 'COLOR').kind = 'IMAGE'
    n = _nodes(addon, mat, e, 'COLOR')
    check('REMAP' not in n, "color should not get a Map Range")
    img = _flat_image("col", 0.5, 'Non-Color')
    n['TEXTURE'].image = img
    r, g, b, _ = sample_socket(ob, mat, node.outputs['Color'])
    close(r, 0.5, 0.02, "color passes straight through")


def test_procedural_gradient_and_mapping(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    s = _src(e, 'ROUGHNESS')
    s.kind = 'PROCEDURAL'
    s.proc_type = 'GRADIENT'
    n = _nodes(addon, mat, e, 'ROUGHNESS')
    check(n['TEXTURE'].node_tree.name == "Essence Proc Gradient", "wrong procedural group")
    # Linear gradient along u: the center of a UV-mapped plane is 0.5.
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.5, 0.03, "gradient center")
    n['MAPPING'].inputs['Location'].default_value = (0.2, 0, 0)
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.7, 0.03, "mapping offset")
    n['MAPPING'].inputs['Location'].default_value = (0, 0, 0)
    n['REMAP'].inputs['To Max'].default_value = 0.6
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.3, 0.03, "procedural range")


def test_procedural_switch_type_and_kind(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    s = _src(e, 'ROUGHNESS')
    s.kind = 'PROCEDURAL'
    for t in ('NOISE', 'VORONOI', 'WAVE', 'CHECKER', 'GRADIENT'):
        s.proc_type = t
        n = _nodes(addon, mat, e, 'ROUGHNESS')
        check(n['TEXTURE'].node_tree.name.endswith(t.title()), f"group for {t}")
        v = sample_socket(ob, mat, node.outputs['Roughness'])[0]
        check(0.0 <= v <= 1.0, f"{t} out of range: {v}")
    remap = _nodes(addon, mat, e, 'ROUGHNESS')['REMAP']
    remap.inputs['To Max'].default_value = 0.4
    s.projection = 'OBJECT'   # settings on the nodes that stay are kept
    check(abs(_nodes(addon, mat, e, 'ROUGHNESS')['REMAP'].inputs['To Max']
              .default_value - 0.4) < 1e-6, "remap reset by a projection change")
    s.kind = 'IMAGE'
    n = _nodes(addon, mat, e, 'ROUGHNESS')
    check(n['TEXTURE'].bl_idname == 'ShaderNodeTexImage', "texture node not swapped")
    s.kind = 'VALUE'
    check(not _nodes(addon, mat, e, 'ROUGHNESS'), "value kind should remove the nodes")
    check(not node.inputs['Roughness B'].is_linked, "B still linked after Value")
    for name in (n_.name for n_ in mat.node_tree.nodes):
        check('Source' not in name, name)


def test_procedural_color_ramp(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    s = _src(e, 'COLOR')
    s.kind = 'PROCEDURAL'
    s.proc_type = 'GRADIENT'
    n = _nodes(addon, mat, e, 'COLOR')
    check('RAMP' in n and 'REMAP' not in n, "color procedural should use a Color Ramp")
    n['RAMP'].color_ramp.elements[0].color = (0, 0, 0, 1)
    n['RAMP'].color_ramp.elements[1].color = (1, 0, 0, 1)
    r = sample_socket(ob, mat, node.outputs['Color'])[0]
    close(r, 0.5, 0.05, "ramp at gradient center")


def test_triplanar_projection(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    s = _src(e, 'ROUGHNESS')
    s.kind = 'IMAGE'
    s.projection = 'TRIPLANAR'
    n = _nodes(addon, mat, e, 'ROUGHNESS')
    check(n['TEXTURE'].projection == 'BOX', "triplanar should use box projection")
    check(n['MAPPING'].inputs['Vector'].links[0].from_socket.name == 'Object',
          "triplanar should use object coordinates")
    n['TEXTURE'].image = _flat_image("tri", 0.4)
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.4, 0.03, "triplanar sample")
    s.projection = 'UV'
    check(n['TEXTURE'].projection == 'FLAT', "UV should use flat projection")


def test_opacity(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    base = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    base.inputs['Roughness B'].default_value = 0.2
    base.inputs['Bump B'].default_value = 0.1
    bpy.ops.essence.add_layer()
    top = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    top.inputs['Roughness B'].default_value = 0.8
    top.inputs['Bump B'].default_value = 0.4
    top.inputs['Opacity'].default_value = 0.5
    close(sample_socket(ob, mat, top.outputs['Roughness'])[0], 0.5, 0.02, "layer opacity")
    close(sample_socket(ob, mat, top.outputs['Bump'])[0], 0.3, 0.02, "layer opacity on bump")
    top.inputs['Roughness Opacity'].default_value = 0.5   # 0.5 * 0.5
    close(sample_socket(ob, mat, top.outputs['Roughness'])[0], 0.35, 0.02, "channel opacity")
    close(sample_socket(ob, mat, top.outputs['Bump'])[0], 0.3, 0.02, "bump unaffected by it")


def test_hand_wired_input_survives_rebuild(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    val = mat.node_tree.nodes.new('ShaderNodeValue')
    val.outputs[0].default_value = 0.9
    mat.node_tree.links.new(val.outputs[0], node.inputs['Roughness B'])
    check(bpy.ops.essence.rebuild() == {'FINISHED'}, "rebuild failed")
    check(node.inputs['Roughness B'].is_linked and
          node.inputs['Roughness B'].links[0].from_node.name == val.name,
          "hand-wired link lost on rebuild")
    # and survives a group upgrade, which rebuilds the interface
    ng = bpy.data.node_groups[addon.nodes.GROUP_NAME]
    ng[addon.nodes.VERSION_KEY] = addon.nodes.GROUP_VERSION - 1
    bpy.ops.essence.rebuild()
    node = mat.node_tree.nodes[e.node_name]
    check(node.inputs['Roughness B'].is_linked, "hand-wired link lost on group upgrade")
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.9, 0.02, "value through link")


def test_source_survives_group_upgrade(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    s = _src(e, 'ROUGHNESS')
    s.kind = 'PROCEDURAL'
    s.proc_type = 'GRADIENT'
    ng = bpy.data.node_groups[addon.nodes.GROUP_NAME]
    ng[addon.nodes.VERSION_KEY] = addon.nodes.GROUP_VERSION - 1
    bpy.ops.essence.rebuild()
    node = mat.node_tree.nodes[e.node_name]
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.5, 0.03, "source after upgrade")


def test_remove_layer_removes_source_nodes(addon):
    ob = make_plane()
    mat, e, node = _layer(ob)
    _src(e, 'COLOR').kind = 'IMAGE'
    _src(e, 'ROUGHNESS').kind = 'PROCEDURAL'
    check(len(_nodes(addon, mat, e, 'COLOR')) > 0, "setup")
    bpy.ops.essence.remove_layer()
    left = [n.name for n in mat.node_tree.nodes if addon.sources.SRC_KEY in n]
    check(not left, f"source nodes left behind: {left}")


def test_source_values_survive_move(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat, e, node = _layer(ob)
    s = _src(e, 'ROUGHNESS')
    s.kind = 'IMAGE'
    n = _nodes(addon, mat, e, 'ROUGHNESS')
    n['TEXTURE'].image = _flat_image("mv", 0.6)
    name = e.name
    bpy.ops.essence.move_layer(direction='DOWN')
    e = next(x for x in mat.essence.layers if x.name == name)
    node = mat.node_tree.nodes[e.node_name]
    close(sample_socket(ob, mat, node.outputs['Roughness'])[0], 0.6, 0.02, "after move")


def test_visibility_toggle(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    base = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    base.inputs['Roughness B'].default_value = 0.2
    bpy.ops.essence.add_layer()
    top = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    top.inputs['Roughness B'].default_value = 0.8
    top.inputs['Visible'].default_value = False
    close(sample_socket(ob, mat, top.outputs['Roughness'])[0], 0.2, 0.02, "hidden layer")
    top.inputs['Visible'].default_value = True
    close(sample_socket(ob, mat, top.outputs['Roughness'])[0], 0.8, 0.02, "visible layer")


def test_folder_visibility_toggle(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.node_tree.nodes[mat.essence.layers[-1].node_name].inputs['Roughness B'] \
        .default_value = 0.2
    bpy.ops.essence.add_folder()
    mat.essence.layers[mat.essence.active_index].folder_mode = 'NORMAL'   # reads the folder node
    bpy.ops.essence.add_layer()
    mat.node_tree.nodes[mat.essence.layers[mat.essence.active_index].node_name] \
        .inputs['Roughness B'].default_value = 0.8
    folder = next(mat.node_tree.nodes[e.node_name] for e in mat.essence.layers
                  if e.kind == 'FOLDER')
    close(sample_socket(ob, mat, folder.outputs['Roughness'])[0], 0.8, 0.02, "folder shown")
    folder.inputs['Visible'].default_value = False
    close(sample_socket(ob, mat, folder.outputs['Roughness'])[0], 0.2, 0.02, "folder hidden")
