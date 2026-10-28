import os
import tempfile

import bpy

from harness import check, make_plane


def _kinds(mat):
    return [e.kind for e in mat.essence.layers]


def _validate(addon, mat):
    """Flat list is a valid post-order and every entry's nodes exist."""
    layers = mat.essence.layers
    tree = mat.node_tree
    uids = [e.uid for e in layers]
    check(len(set(uids)) == len(uids), "duplicate uids")
    for e in layers:
        check(tree.nodes.get(e.node_name) is not None, f"node missing for {e.name}")
        if e.kind == 'MASK':
            check(tree.nodes.get(e.mask_node_name) is not None, f"image node missing {e.name}")
            check(uids.index(e.parent) > uids.index(e.uid), f"mask {e.name} not below its owner")
    t = addon.stack.Tree(layers)
    check(t.order() == uids, "list is not in canonical order")


def _select(mat, name):
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(name)


def test_layers_and_masks(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    check(_kinds(mat) == ['LAYER', 'LAYER'], _kinds(mat))
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    check(_kinds(mat).count('MASK') == 1, "mask not added")
    check(not bpy.ops.essence.add_mask.poll(), "a layer takes one mask only")
    _validate(addon, mat)
    top = mat.node_tree.nodes.get(mat.essence.layers[-1].node_name)
    check(top.inputs['Mask'].is_linked, "top layer mask not linked")
    n = len(mat.essence.layers)
    bpy.ops.essence.remove_layer()
    check(len(mat.essence.layers) == n - 1, "mask not removed")
    _validate(addon, mat)


def test_folder_and_moves(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()      # Layer 1
    bpy.ops.essence.add_folder()     # Folder 1 above
    bpy.ops.essence.add_layer()      # Layer 2 into the folder (folder active)
    mat = ob.active_material
    layers = mat.essence.layers
    l2 = next(e for e in layers if e.name == "Layer 2")
    f1 = next(e for e in layers if e.name == "Folder 1")
    check(l2.parent == f1.uid, "layer not added into the active folder")
    _validate(addon, mat)
    _select(mat, "Layer 2")
    bpy.ops.essence.move_layer(direction='DOWN')
    _validate(addon, mat)
    l2 = next(e for e in layers if e.name == "Layer 2")
    check(l2.parent == "", f"layer should have left the folder, parent={l2.parent}")
    _select(mat, "Layer 2")
    bpy.ops.essence.move_layer(direction='UP')
    _validate(addon, mat)
    _select(mat, "Folder 1")
    bpy.ops.essence.remove_layer(ungroup=True)
    _validate(addon, mat)
    check(all(e.parent == "" for e in layers), "layers not ungrouped")
    check(sum(1 for e in layers if e.kind == 'FOLDER') == 0, "folder not removed")


def test_save_reload(addon):
    make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    path = os.path.join(tempfile.gettempdir(), "essence_reload_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    mat = bpy.data.materials[0]
    check(len(mat.essence.layers) == 2, "stack lost on reload")
    _validate(addon, mat)
    os.remove(path)
