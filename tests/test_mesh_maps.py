import bpy

from harness import check, close, make_plane, sample_socket


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
    sc.essence_bake_size = '128'
    sc.essence_bake_samples = 4
    check(bpy.ops.essence.bake() == {'FINISHED'}, "bake failed")
    img = _slot(addon, mat, 'AO').image
    check(img is not None and img.name == "Plane_AO", f"AO slot image: {img}")
    sc.essence_bake_map = 'NORMAL'
    check(bpy.ops.essence.bake() == {'FINISHED'}, "normal bake failed")
    check(_slot(addon, mat, 'NORMAL').image.name == "Plane_Normal", "normal slot image")


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
        check(bpy.ops.essence.export_unity() == {'FINISHED'}, "export failed")
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
