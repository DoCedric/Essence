import bpy

from harness import check, close, make_plane, sample_socket


def _state(mat, ch_id):
    return next(c for c in mat.essence.channels if c.id == ch_id)


def _layer(ob):
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    return mat, e


def _node(mat, e):
    return mat.node_tree.nodes[e.node_name]


def _bsdf(addon, mat):
    return addon.nodes.find_principled(mat.node_tree)


def test_default_channels(addon):
    ob = make_plane()
    mat, e = _layer(ob)
    on = [c.id for c in mat.essence.channels if c.enabled]
    check(on == ['COLOR', 'ROUGHNESS', 'METALLIC', 'BUMP'], f"default channels {on}")
    check("Emission A" not in _node(mat, e).inputs, "emission should not be in the default group")


def test_emission_channel(addon):
    ob = make_plane()
    mat, e = _layer(ob)
    bsdf = _bsdf(addon, mat)
    _state(mat, 'EMISSION').enabled = True
    node = _node(mat, e)
    check("Emission B" in node.inputs, "layer node did not gain the emission inputs")
    check(bsdf.inputs['Emission Color'].is_linked, "emission not linked to the BSDF")
    close(bsdf.inputs['Emission Strength'].default_value, 1.0, 1e-4, "strength set on enable")
    node.inputs['Emission B'].default_value = (1.0, 0.5, 0.0, 1.0)
    r, g, b, _ = sample_socket(ob, mat, node.outputs['Emission'])
    close(r, 1.0, 0.02, "emission r")
    close(g, 0.5, 0.02, "emission g")
    _state(mat, 'EMISSION').enabled = False
    node = _node(mat, e)
    check("Emission B" not in node.inputs, "emission inputs should be gone")
    check(not bsdf.inputs['Emission Color'].is_linked, "emission link should be gone")
    close(bsdf.inputs['Emission Strength'].default_value, 0.0, 1e-4, "strength reset on disable")


def test_alpha_channel_blends(addon):
    ob = make_plane()
    mat, e = _layer(ob)
    _state(mat, 'ALPHA').enabled = True
    _node(mat, e).inputs['Alpha B'].default_value = 0.4
    bpy.ops.essence.add_layer()
    e2 = mat.essence.layers[mat.essence.active_index]
    top = _node(mat, e2)
    check("Alpha B" in top.inputs, "new layer should have the alpha channel")
    top.inputs['Alpha B'].default_value = 0.2
    top.inputs['Alpha Opacity'].default_value = 0.5
    close(sample_socket(ob, mat, top.outputs['Alpha'])[0], 0.3, 0.02, "alpha lerp")
    check(_bsdf(addon, mat).inputs['Alpha'].is_linked, "alpha not linked to the BSDF")


def test_disabling_channel_removes_sources_and_enabling_restores(addon):
    ob = make_plane()
    mat, e = _layer(ob)
    src = next(s for s in e.sources if s.channel == 'METALLIC')
    src.kind = 'PROCEDURAL'
    src.proc_type = 'GRADIENT'
    n_before = len(addon.sources.source_nodes(mat.node_tree, e.uid, 'METALLIC'))
    check(n_before > 0, "setup")
    _state(mat, 'METALLIC').enabled = False
    check("Metallic B" not in _node(mat, e).inputs, "metallic should be gone")
    check(not addon.sources.source_nodes(mat.node_tree, e.uid, 'METALLIC'),
          "source nodes of a disabled channel should be removed")
    check(not _bsdf(addon, mat).inputs['Metallic'].is_linked, "BSDF metallic should be unlinked")
    _state(mat, 'METALLIC').enabled = True
    node = _node(mat, e)
    check(node.inputs['Metallic B'].is_linked, "source should come back with the channel")
    close(sample_socket(ob, mat, node.outputs['Metallic'])[0], 0.5, 0.03, "gradient after re-enable")


def test_channel_set_survives_new_layers_and_folders(addon):
    ob = make_plane()
    mat, e = _layer(ob)
    _state(mat, 'EMISSION').enabled = True
    _state(mat, 'METALLIC').enabled = False
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()
    for entry in mat.essence.layers:
        if entry.kind == 'MASK':
            continue
        node = _node(mat, entry)
        check("Emission B" in node.inputs or "Emission A" in node.inputs,
              f"{entry.name} missing emission")
        check("Metallic A" not in node.inputs, f"{entry.name} still has metallic")
    inner = next(x for x in mat.essence.layers if x.name == "Layer 2")
    folder = next(x for x in mat.essence.layers if x.kind == 'FOLDER')
    check(inner.parent == folder.uid, "layer should be inside the folder")


def test_blend_mode_combined_with_channel_change(addon):
    ob = make_plane()
    mat, e = _layer(ob)
    next(s for s in e.sources if s.channel == 'ROUGHNESS').blend = 'MULTIPLY'
    _state(mat, 'EMISSION').enabled = True
    ng = _node(mat, e).node_tree
    check("Roughness:Multiply" in ng.name and "+Emission" in ng.name, ng.name)


def test_export_alpha_and_emission(addon):
    import os
    import shutil
    import tempfile
    ob = make_plane()
    mat, e = _layer(ob)
    _state(mat, 'ALPHA').enabled = True
    _state(mat, 'EMISSION').enabled = True
    node = _node(mat, e)
    node.inputs['Alpha B'].default_value = 0.4
    node.inputs['Emission B'].default_value = (0.2, 0.6, 1.0, 1.0)
    node.inputs['Color B'].default_value = (0.5, 0.5, 0.5, 1.0)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc = bpy.context.scene
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed")
        stem = bpy.path.clean_name(mat.name)

        def load(suffix):
            img = bpy.data.images.load(os.path.join(folder, f"{stem}_{suffix}.exr"))
            img.colorspace_settings.name = 'Non-Color'
            w, h = img.size
            i = ((h // 2) * w + w // 2) * 4
            return list(img.pixels)[i:i + 4]

        base = load("BaseMap")
        close(base[0], 0.5, 0.02, "base color")
        close(base[3], 0.4, 0.02, "BaseMap alpha is the opacity")
        em = load("Emission")
        for got, want in zip(em[:3], (0.2, 0.6, 1.0)):
            close(got, want, 0.02, "emission color")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_export_still_works_without_metallic(addon):
    import os
    import shutil
    import tempfile
    ob = make_plane()
    mat, e = _layer(ob)
    _state(mat, 'METALLIC').enabled = False
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        sc = bpy.context.scene
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed without metallic")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
