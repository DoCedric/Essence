import bpy

from harness import baked, check, close, make_plane, sample_socket, texset


def _slot(addon, mat, map_id):
    return mat.node_tree.nodes[addon.nodes.map_slot_name(map_id)]


def test_slots_exist_and_are_unlinked_by_default(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    for map_id, _ in addon.nodes.MAP_SLOTS:
        node = _slot(addon, mat, map_id)
        check(node.bl_idname == 'ShaderNodeTexImage' and node.image is None, f"{map_id} slot")
    check(mat.node_tree.nodes.get(addon.nodes.NORMAL_NODE_NAME) is None,
          "no Normal Map node without a normal image")


def test_picked_normal_feeds_the_shader(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    img = bpy.data.images.new("my_normal", 16, 16)
    img.colorspace_settings.name = 'Non-Color'
    _slot(addon, mat, 'NORMAL').image = img
    bpy.ops.essence.rebuild()
    tree = mat.node_tree
    nm = tree.nodes[addon.nodes.NORMAL_NODE_NAME]
    check(nm.inputs['Color'].links[0].from_node.name == addon.nodes.map_slot_name('NORMAL'),
          "slot image should feed the Normal Map node")
    bump = tree.nodes[addon.nodes.BUMP_NODE_NAME]
    check(bump.inputs['Normal'].links[0].from_node.name == nm.name, "Normal Map into bump")
    _slot(addon, mat, 'NORMAL').image = None
    bpy.ops.essence.rebuild()
    check(not tree.nodes[addon.nodes.NORMAL_NODE_NAME].inputs['Color'].is_linked,
          "an empty slot should not feed the shader")


def test_bake_fills_the_slot(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    sc = bpy.context.scene
    sc.essence_bake_map = 'AO'
    texset(ob).bake_size = '128'
    sc.essence_bake_samples = 4
    check(bpy.ops.essence.bake() == {'FINISHED'}, "bake failed")
    img = _slot(addon, mat, 'AO').image
    check(img is not None and img.name == f"{mat.name}_AO", f"AO slot image: {img}")
    sc.essence_bake_map = 'NORMAL'
    check(bpy.ops.essence.bake() == {'FINISHED'}, "normal bake failed")
    check(_slot(addon, mat, 'NORMAL').image.name == f"{mat.name}_Normal", "normal slot image")


def test_export_reads_the_ao_slot(addon):
    import os
    import shutil
    import tempfile
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    img = bpy.data.images.new("grey_ao", 16, 16, float_buffer=True)
    img.generated_color = (0.25, 0.25, 0.25, 1.0)
    img.colorspace_settings.name = 'Non-Color'
    _slot(addon, mat, 'AO').image = img
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc = bpy.context.scene
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed")
        p = os.path.join(folder, f"{bpy.path.clean_name(mat.name)}_MaskMap.exr")
        out = bpy.data.images.load(p)
        out.colorspace_settings.name = 'Non-Color'
        w, h = out.size
        i = ((h // 2) * w + w // 2) * 4
        close(list(out.pixels)[i + 1], 0.25, 0.03, "MaskMap G is the slot's AO")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_removed_normal_channel_migrates(addon):
    """Files from the sprint when Normal was a layer channel still load and rebuild."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    state = mat.essence.channels.add()
    state.id = 'NORMAL'
    layer = mat.essence.layers[0]
    s = layer.sources.add()
    s.channel = 'NORMAL'
    s.uid = layer.uid
    check(bpy.ops.essence.rebuild() == {'FINISHED'}, "rebuild failed")
    check(all(c.id != 'NORMAL' for c in mat.essence.channels), "stale channel state kept")
    check(all(x.channel != 'NORMAL' for x in layer.sources), "stale source kept")


def _factor(node):
    return next(i for i in node.inputs if i.identifier == "Factor_Float")


def _grey_ao(value):
    img = bpy.data.images.new(f"ao_{value}", 16, 16, float_buffer=True)
    img.generated_color = (value, value, value, 1.0)
    img.colorspace_settings.name = 'Non-Color'
    return img


def _ao_setup(addon, ao_value=0.25):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    top = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    top.inputs["Color B"].default_value = (0.8, 0.4, 0.2, 1.0)
    _slot(addon, mat, 'AO').image = _grey_ao(ao_value)
    bpy.ops.essence.rebuild()
    return ob, mat


def test_ao_darkens_color_and_specular(addon):
    ob, mat = _ao_setup(addon, 0.25)
    tree = mat.node_tree
    bsdf = addon.nodes.find_principled(tree)
    color_mix = tree.nodes[addon.nodes.AO_COLOR_NODE]
    check(bsdf.inputs['Base Color'].links[0].from_node == color_mix, "AO not before Base Color")
    spec_mul = tree.nodes[addon.nodes.AO_SPEC_NODE]
    check(bsdf.inputs['Specular IOR Level'].links[0].from_node == spec_mul, "AO not on specular")
    strength = tree.nodes[addon.nodes.AO_STRENGTH_NODE]
    out = lambda sock: sample_socket(ob, mat, sock)  # noqa: E731
    result = next(o for o in color_mix.outputs if o.identifier == "Result_Color")
    full = out(result)
    close(full[0], 0.8 * 0.25, 0.02, "color red at strength 1")
    close(full[1], 0.4 * 0.25, 0.02, "color green at strength 1")
    close(out(spec_mul.outputs[0])[0], 0.5 * 0.25, 0.02, "specular at strength 1")
    _factor(strength).default_value = 0.5   # lerp(1, 0.25, 0.5) = 0.625
    half = out(result)
    close(half[0], 0.8 * 0.625, 0.02, "color red at strength 0.5")
    close(out(spec_mul.outputs[0])[0], 0.5 * 0.625, 0.02, "specular at strength 0.5")
    _factor(strength).default_value = 0.0
    close(out(result)[0], 0.8, 0.02, "strength 0 leaves the color alone")


def test_ao_rebuild_is_idempotent_and_removable(addon):
    ob, mat = _ao_setup(addon, 0.25)
    tree = mat.node_tree
    bsdf = addon.nodes.find_principled(tree)
    stack_out = tree.nodes[mat.essence.layers[-1].node_name]
    for _ in range(3):
        bpy.ops.essence.rebuild()
    color_mix = tree.nodes[addon.nodes.AO_COLOR_NODE]
    a = next(s for s in color_mix.inputs if s.identifier == "A_Color")
    check(a.links[0].from_node == stack_out, "layer stack should feed the AO color node once")
    check(len([n for n in tree.nodes if n.name.startswith("Essence AO Color")]) == 1,
          "duplicate AO nodes")
    spec_mul = tree.nodes[addon.nodes.AO_SPEC_NODE]
    close(spec_mul.inputs[1].default_value, 0.5, 0.001, "specular default carried over")
    _slot(addon, mat, 'AO').image = None
    bpy.ops.essence.rebuild()
    check(bsdf.inputs['Base Color'].links[0].from_node == stack_out,
          "an empty AO slot should hand Base Color back to the stack")
    check(not bsdf.inputs['Specular IOR Level'].is_linked, "specular should be unlinked again")
    close(_factor(tree.nodes[addon.nodes.AO_STRENGTH_NODE]).default_value, 1.0, 0.001, "strength kept")


def test_ao_bake_wires_the_shader(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    sc = bpy.context.scene
    sc.essence_bake_map = 'AO'
    texset(ob).bake_size = '128'
    sc.essence_bake_samples = 4
    check(bpy.ops.essence.bake() == {'FINISHED'}, "bake failed")
    bsdf = addon.nodes.find_principled(mat.node_tree)
    check(bsdf.inputs['Base Color'].links[0].from_node.name == addon.nodes.AO_COLOR_NODE,
          "a fresh AO bake should be wired into the shader")


def test_export_ignores_the_ao_overlay(addon):
    import os
    import shutil
    import tempfile
    ob, mat = _ao_setup(addon, 0.25)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc = bpy.context.scene
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed")
        out = bpy.data.images.load(os.path.join(folder, f"{bpy.path.clean_name(mat.name)}_BaseMap.exr"))
        w, h = out.size
        i = ((h // 2) * w + w // 2) * 4
        close(list(out.pixels)[i], 0.8, 0.03, "BaseMap red must not carry the AO darkening")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_ao_strength_slider_is_limited_to_0_1(addon):
    ob, mat = _ao_setup(addon, 0.25)
    node = mat.node_tree.nodes[addon.nodes.AO_STRENGTH_NODE]
    sock = _factor(node)
    check(sock.default_value == 1.0, "default strength should be 1")
    prop = sock.bl_rna.properties["default_value"]
    check(prop.hard_min == 0.0 and prop.hard_max == 1.0,
          f"slider range is {prop.hard_min}..{prop.hard_max}")
