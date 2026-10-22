from types import SimpleNamespace

import bpy

from harness import check, close, make_plane, sample_socket


def _sc():
    sc = bpy.context.scene
    sc.essence_freeze_size = '512'
    sc.essence_bake_margin = 0
    return sc


def _stack(n, masks=True):
    """n layers with different values, a gradient procedural on roughness, and a half mask."""
    ob = make_plane()
    for i in range(n):
        bpy.ops.essence.add_layer()
    mat = ob.active_material
    layers = [e for e in mat.essence.layers if e.kind == 'LAYER']
    for i, e in enumerate(layers):
        node = mat.node_tree.nodes[e.node_name]
        node.inputs['Color B'].default_value = (0.1 * (i + 1), 0.5, 1.0 - 0.1 * i, 1)
        node.inputs['Roughness B'].default_value = 0.1 + 0.1 * i
        node.inputs['Metallic B'].default_value = 0.05 * i
        node.inputs['Bump B'].default_value = 0.1 * (i - 1)
        node.inputs['Roughness Opacity'].default_value = 0.8
    src = next(s for s in layers[-1].sources if s.channel == 'ROUGHNESS')
    src.kind = 'PROCEDURAL'
    src.proc_type = 'GRADIENT'
    if masks:
        bpy.context.scene.essence_bake_margin = 0
        mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[1].name)
        bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
        m = mat.essence.layers[mat.essence.active_index]
        mat.node_tree.nodes[m.mask_node_name].image.generated_color = (0.5, 0.5, 0.5, 1)
    # entries move around in the list when masks are added, so keep plain copies
    layers = [SimpleNamespace(name=e.name, uid=e.uid, node_name=e.node_name)
              for e in mat.essence.layers if e.kind == 'LAYER']
    return ob, mat, layers


def _results(ob, mat, entry_name):
    e = next(x for x in mat.essence.layers if x.name == entry_name)
    n = mat.node_tree.nodes[e.node_name]
    return {k: sample_socket(ob, mat, n.outputs[k])[:3] for k in
            ('Color', 'Roughness', 'Metallic', 'Bump')}


def _final(ob, mat, addon):
    """What the shader actually receives for each channel."""
    bsdf = addon.nodes.find_principled(mat.node_tree)
    out = {}
    for name, sock in (('Color', 'Base Color'), ('Roughness', 'Roughness'),
                       ('Metallic', 'Metallic')):
        out[name] = sample_socket(ob, mat, bsdf.inputs[sock].links[0].from_socket)[:3]
    bump = mat.node_tree.nodes[addon.nodes.BUMP_NODE_NAME]
    out['Bump'] = sample_socket(ob, mat, bump.inputs['Height'].links[0].from_socket)[:3]
    return out


def _same(a, b, tol, what):
    for k in a:
        for i in range(3):
            close(b[k][i], a[k][i], tol, f"{what} {k}[{i}]")


def _reachable(mat):
    tree = mat.node_tree
    out = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
    seen, todo = set(), [out]
    while todo:
        n = todo.pop()
        if n.name in seen:
            continue
        seen.add(n.name)
        for s in n.inputs:
            for l in s.links:
                todo.append(l.from_node)
    total = 0
    for name in seen:  # a group counts as what is inside it, which the shader inlines
        n = tree.nodes[name]
        total += len(n.node_tree.nodes) if n.bl_idname == 'ShaderNodeGroup' else 1
    return total


def test_freeze_keeps_the_look(addon):
    _sc()
    ob, mat, layers = _stack(4)
    before = _final(ob, mat, addon)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[-1].name)
    check(bpy.ops.essence.freeze() == {'FINISHED'}, "freeze failed")
    check(mat.essence.freeze_uid == layers[-1].uid, "freeze boundary not stored")
    check(all(e.frozen for e in mat.essence.layers), "all entries should be frozen")
    _same(before, _final(ob, mat, addon), 0.02, "after freeze")


def test_freeze_shrinks_the_shader(addon):
    _sc()
    ob, mat, layers = _stack(10)
    full = _reachable(mat)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[-1].name)
    bpy.ops.essence.freeze()
    frozen = _reachable(mat)
    check(frozen < full * 0.25, f"frozen shader should be far smaller: {full} -> {frozen}")


def test_freeze_middle_then_edit_above(addon):
    _sc()
    ob, mat, layers = _stack(4, masks=False)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[1].name)
    bpy.ops.essence.freeze()
    flags = {e.name: e.frozen for e in mat.essence.layers}
    check(flags[layers[0].name] and flags[layers[1].name], "lower layers should be frozen")
    check(not flags[layers[2].name] and not flags[layers[3].name], "upper layers stay live")
    mid = mat.node_tree.nodes[layers[2].node_name]
    mid.inputs['Roughness B'].default_value = 0.95
    mid.inputs['Roughness Opacity'].default_value = 1.0
    mat.node_tree.nodes[layers[3].node_name].inputs['Roughness Opacity'].default_value = 0.0
    after = _final(ob, mat, addon)
    check(abs(after['Roughness'][0] - 0.95) < 0.03, f"live layer edit: {after['Roughness']}")
    # the live layers feed from the frozen images
    first_live = mat.node_tree.nodes[layers[2].node_name]
    src = first_live.inputs['Color A'].links[0].from_node
    check(src.get("essence_frozen") == 'COLOR', "first live layer should read the frozen color")


def test_unfreeze_restores(addon):
    _sc()
    ob, mat, layers = _stack(3)
    before = _final(ob, mat, addon)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[-1].name)
    bpy.ops.essence.freeze()
    check(bpy.ops.essence.unfreeze() == {'FINISHED'}, "unfreeze failed")
    check(not mat.essence.freeze_uid and not any(e.frozen for e in mat.essence.layers),
          "flags should be cleared")
    left = [n.name for n in mat.node_tree.nodes if "essence_frozen" in n]
    check(not left, f"frozen nodes left: {left}")
    check(not [i.name for i in bpy.data.images if "_Frozen_" in i.name], "frozen images left")
    _same(before, _final(ob, mat, addon), 0.02, "after unfreeze")


def test_refreeze_higher_replaces_images(addon):
    _sc()
    ob, mat, layers = _stack(4, masks=False)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[0].name)
    bpy.ops.essence.freeze()
    before = _final(ob, mat, addon)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[2].name)
    bpy.ops.essence.freeze()
    imgs = [i for i in bpy.data.images if "_Frozen_" in i.name]
    check(len(imgs) == 4, f"old frozen images should be replaced, found {len(imgs)}")
    check(sum(e.frozen for e in mat.essence.layers) == 3, "three layers frozen")
    _same(before, _final(ob, mat, addon), 0.02, "refreeze")


def test_frozen_entries_are_protected(addon):
    _sc()
    ob, mat, layers = _stack(2, masks=False)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[0].name)
    bpy.ops.essence.freeze()
    for op in (lambda: bpy.ops.essence.remove_layer(),
               lambda: bpy.ops.essence.add_layer(),
               lambda: bpy.ops.essence.add_folder(),
               lambda: bpy.ops.essence.move_layer(direction='UP'),
               lambda: bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')):
        try:
            op()
            refused = False
        except RuntimeError:
            refused = True
        check(refused, "an edit of a frozen layer should be refused")
    check(len(mat.essence.layers) == 2, "layers changed while frozen")


def test_new_layer_above_the_freeze(addon):
    _sc()
    ob, mat, layers = _stack(2, masks=False)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[1].name)
    bpy.ops.essence.freeze()
    mat.essence.active_index = 0
    check(len(mat.essence.layers) == 2, "setup")
    # select nothing frozen: adding needs an unfrozen active entry, so unfreeze the top first
    bpy.ops.essence.unfreeze()
    bpy.ops.essence.add_layer()
    check(len(mat.essence.layers) == 3, "add after unfreeze")


def test_freeze_folder_unit(addon):
    _sc()
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    inner = next(e for e in mat.essence.layers if e.name == "Layer 2")
    mat.node_tree.nodes[inner.node_name].inputs['Roughness B'].default_value = 0.9
    mat.essence.active_index = [e.name for e in mat.essence.layers].index("Layer 2")
    before = _final(ob, mat, addon)
    bpy.ops.essence.freeze()
    folder = next(e for e in mat.essence.layers if e.kind == 'FOLDER')
    check(mat.essence.freeze_uid == folder.uid, "freezing a layer in a folder freezes the folder")
    check(all(e.frozen for e in mat.essence.layers), "everything is below the folder")
    _same(before, _final(ob, mat, addon), 0.02, "folder freeze")


def test_freeze_survives_save_reload(addon):
    import os
    import tempfile
    _sc()
    ob, mat, layers = _stack(3)
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layers[-1].name)
    bpy.ops.essence.freeze()
    before = _final(ob, mat, addon)
    path = os.path.join(tempfile.gettempdir(), "essence_freeze_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    mat = bpy.data.materials[0]
    ob = bpy.data.objects[0]
    bpy.context.view_layer.objects.active = ob
    check(mat.essence.freeze_uid != "", "freeze lost on reload")
    _same(before, _final(ob, mat, addon), 0.02, "after reload")
    os.remove(path)
