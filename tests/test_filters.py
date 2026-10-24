import bpy

from harness import check, close, make_plane, sample_socket


def _setup():
    """Rough 0.2 below, 0.8 on top with an image mask: black left of u = 0.45, white right of
    it. Returns (ob, mat, top node, mask uid, mask image)."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.node_tree.nodes[mat.essence.layers[-1].node_name].inputs['Roughness B'].default_value = 0.2
    bpy.ops.essence.add_layer()
    e = mat.essence.layers[mat.essence.active_index]
    top = mat.node_tree.nodes[e.node_name]
    top.inputs['Roughness B'].default_value = 0.8
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=64)
    mask = mat.essence.layers[mat.essence.active_index]
    mask_uid = mask.uid
    img = mat.node_tree.nodes[mask.mask_node_name].image
    size = 64
    px = []
    for y in range(size):
        for x in range(size):
            v = 0.0 if x < int(0.45 * size) else 1.0
            px += [v, v, v, 1.0]
    img.pixels.foreach_set(px)
    img.update()
    return ob, mat, top, mask_uid, img


def _rough(ob, mat, top):
    # A fine bake: the edge of the mask is only 0.05 UV away from the sampled center.
    return sample_socket(ob, mat, top.outputs['Roughness'], size=64)[0]


def _add_filter(mat, mask_uid, filter_type, resolution='256'):
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    check(bpy.ops.essence.add_filter(filter_type=filter_type) == {'FINISHED'}, "add failed")
    e = mat.essence.layers[mat.essence.active_index]
    check(e.kind == 'FILTER', "the new filter should be active")
    e.filter_resolution = resolution
    return e.uid


def _entry(mat, uid):
    return next(e for e in mat.essence.layers if e.uid == uid)


def test_filter_passes_through_until_refreshed(addon):
    ob, mat, top, mask_uid, img = _setup()
    close(_rough(ob, mat, top), 0.8, 0.02, "right of the edge: mask is white")
    f = _add_filter(mat, mask_uid, 'BLUR')
    close(_rough(ob, mat, top), 0.8, 0.02, "an unbaked filter must not change the mask")
    check(bpy.data.node_groups.get(addon.adjustments.FILTER_GROUP_PREFIX + f) is not None,
          "the item's compositor group is missing")
    check(bpy.data.images.get(addon.adjustments.FILTER_IMAGE_PREFIX + f) is None,
          "no result image before the first refresh")


def test_blur_filter_softens_the_edge(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _add_filter(mat, mask_uid, 'BLUR')
    node = addon.filters.filter_node(_entry(mat, f))
    node.inputs['Size'].default_value = (30.0, 30.0)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "refresh failed")
    result = bpy.data.images.get(addon.adjustments.FILTER_IMAGE_PREFIX + f)
    check(result is not None and result.name.startswith("."), "result image missing or visible")
    check(result.is_float and result.packed_file is not None, "result should be a packed float")
    check(tuple(result.size) == (256, 256), f"result size {tuple(result.size)}")
    rough = _rough(ob, mat, top)
    check(0.5 < rough < 0.79, f"blur should lower the mask near the edge: roughness {rough}")
    # Disabled filters pass the unfiltered value again.
    _entry(mat, f).adj_enabled = False
    close(_rough(ob, mat, top), 0.8, 0.02, "disabled filter")
    _entry(mat, f).adj_enabled = True
    check(0.5 < _rough(ob, mat, top) < 0.79, "enabled again")


def test_every_filter_type_refreshes(addon):
    ob, mat, top, mask_uid, img = _setup()
    for t in addon.filters.TYPES:
        f = _add_filter(mat, mask_uid, t.id, resolution='256')
        check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, f"{t.id} refresh failed")
        check(bpy.data.images.get(addon.adjustments.FILTER_IMAGE_PREFIX + f) is not None,
              f"{t.id}: no result")


def test_refresh_leaves_the_scene_as_it_was(addon):
    ob, mat, top, mask_uid, img = _setup()
    sc = bpy.context.scene
    engine, samples = sc.render.engine, sc.cycles.samples
    scenes = len(bpy.data.scenes)
    f = _add_filter(mat, mask_uid, 'KUWAHARA')
    bpy.ops.essence.filter_apply(uid=f)
    check(sc.render.engine == engine and sc.cycles.samples == samples, "render settings changed")
    check(len(bpy.data.scenes) == scenes, "temp scene left behind")
    check(sc.compositing_node_group is None, "the user's compositor was touched")
    check(not [i for i in bpy.data.images if i.name.startswith(".Essence Filter Input")],
          "bake input image left behind")
    left = [n.name for n in mat.node_tree.nodes if n.bl_idname == 'ShaderNodeEmission']
    check(not left, f"bake emission override left in the material: {left}")
    check(ob.select_get() and bpy.context.view_layer.objects.active == ob, "selection changed")


def test_refresh_again_replaces_the_image_and_removal_cleans_up(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _add_filter(mat, mask_uid, 'BLUR')
    bpy.ops.essence.filter_apply(uid=f)
    bpy.ops.essence.filter_apply(uid=f)
    results = [i for i in bpy.data.images if i.name.startswith(".Essence Filter ")]
    check(len(results) == 1, f"one image per filter expected: {[i.name for i in results]}")
    tex = addon.sources.source_nodes(mat.node_tree, f, 'ADJ')['OUT']
    check(tex.image is results[0], "the chain should sample the stored result")
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == f)
    bpy.ops.essence.remove_layer()
    check(not [i for i in bpy.data.images if i.name.startswith(".Essence Filter ")],
          "result image left behind after removing the filter")
    check(not [g for g in bpy.data.node_groups if g.name.startswith("Essence Filter ")],
          "compositor group left behind")
    close(_rough(ob, mat, top), 0.8, 0.02, "mask back to unfiltered")


def test_filter_not_on_folders(addon):
    ob, mat, top, mask_uid, img = _setup()
    bpy.ops.essence.add_folder()
    check(mat.essence.layers[mat.essence.active_index].kind == 'FOLDER', "folder not active")
    check(not bpy.ops.essence.add_filter.poll(), "a folder cannot take a filter")


def test_filter_panel_and_rows_draw(addon):
    from types import SimpleNamespace

    from essence import ui
    from test_ui import Rec, _draw
    ob, mat, top, mask_uid, img = _setup()
    for t in addon.filters.TYPES:
        f = _add_filter(mat, mask_uid, t.id)
        log = _draw(ui._adjustment, ob)
        check(any(c[0] == "operator" and c[1][0] == "essence.filter_apply" for c in log),
              f"{t.id}: no Apply button")
        _draw(ui._stack, ob)


def test_filter_result_repeats_across_uv_tiles(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _add_filter(mat, mask_uid, 'BLUR')
    tex = addon.sources.source_nodes(mat.node_tree, f, 'ADJ')['OUT']
    check(tex.extension == 'REPEAT', f"the filter result must repeat, not {tex.extension}")
    tex.extension = 'EXTEND'                       # a stale node from an earlier version
    bpy.ops.essence.rebuild()
    check(addon.sources.source_nodes(mat.node_tree, f, 'ADJ')['OUT'].extension == 'REPEAT',
          "a rebuild must put it back to repeat")


def test_filter_settings_survive_save_and_reload(addon):
    import os
    import tempfile
    ob, mat, top, mask_uid, img = _setup()
    f = _add_filter(mat, mask_uid, 'BLUR')
    addon.filters.filter_node(_entry(mat, f)).inputs['Size'].default_value = (7.0, 3.0)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    check(bpy.data.node_groups[addon.adjustments.FILTER_GROUP_PREFIX + f].use_fake_user,
          "the compositor group needs a fake user or a save drops it")
    ob_name = ob.name   # pointers die when the file is reopened
    folder = tempfile.mkdtemp(prefix="essence_save_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[ob_name].active_material
        e = _entry(mat, f)
        node = addon.filters.filter_node(e)
        check(node is not None, "the compositor group was lost on save")
        size = tuple(node.inputs['Size'].default_value)
        check(abs(size[0] - 7.0) < 1e-4 and abs(size[1] - 3.0) < 1e-4, f"settings lost: {size}")
        check(not addon.filters.is_stale(mat, e), "a saved and reloaded filter must still be current")
        check(addon.filters.result_image(f) is not None, "the result image was lost")
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


def test_filter_over_a_procedural_fill_and_the_log_trail(addon):
    """A procedural checker fill (3D coordinates, a texture node group) under a blur."""
    ob, mat, top, top_uid, mask_uid = _setup_fill_mask()
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    bpy.ops.essence.add_fill()
    fill = mat.essence.layers[mat.essence.active_index]
    fill.source.kind = 'PROCEDURAL'
    fill.source.proc_type = 'CHECKER'
    f = _add_filter(mat, mask_uid, 'BLUR')
    import os
    if os.path.exists(addon.filters.LOG_PATH):
        os.remove(addon.filters.LOG_PATH)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply over a procedural failed")
    check(addon.filters.is_baked(_entry(mat, f)), "no result")
    log = open(addon.filters.LOG_PATH, encoding="utf-8").read()
    for step in ("APPLY", "bake: calling bpy.ops.object.bake", "bake: bake state restored",
                 "compositor: calling bpy.ops.render.render", "APPLY finished"):
        check(step in log, f"the log should show '{step}':\n{log}")


def _setup_fill_mask():
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    bpy.ops.essence.add_layer()
    e = mat.essence.layers[mat.essence.active_index]
    top, top_uid = mat.node_tree.nodes[e.node_name], e.uid
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=64)
    return ob, mat, top, top_uid, mat.essence.layers[mat.essence.active_index].uid
