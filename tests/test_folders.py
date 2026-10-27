"""Folders: Passthrough and Normal modes, folder opacity, nested folders (values from baked
pixels), moving in and out of nested folders, the version 4 upgrade, save and reload."""

import os
import shutil
import tempfile

import bpy

from harness import check, close, make_plane, sample_socket


def _entry(mat, name):
    return next(e for e in mat.essence.layers if e.name == name)


def _node(mat, name):
    return mat.node_tree.nodes[_entry(mat, name).node_name]


def _select(mat, name):
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(name)


def _rough(ob, mat):
    """The Roughness the stack feeds the Principled BSDF."""
    bsdf = next(n for n in mat.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    links = bsdf.inputs['Roughness'].links
    check(links, "the stack feeds Roughness")
    return sample_socket(ob, mat, links[0].from_socket)[0]


def _layer(mat, rough, name=None):
    bpy.ops.essence.add_layer()
    e = mat.essence.layers[mat.essence.active_index]
    if name:
        e.name = name
    mat.node_tree.nodes[e.node_name].inputs['Roughness B'].default_value = rough
    return e


def _mask(mat, value):
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=16)
    e = mat.essence.layers[mat.essence.active_index]
    img = mat.node_tree.nodes[e.mask_node_name].image
    img.pixels.foreach_set([value, value, value, 1.0] * (img.size[0] * img.size[1]))
    img.update()


def _base():
    """A plane with a base layer of roughness 0.2."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.node_tree.nodes[mat.essence.layers[-1].node_name].inputs['Roughness B'].default_value = 0.2
    mat.essence.layers[-1].name = "Base"
    return ob, mat


def _folder(mat, name, inside=None):
    if inside is not None:
        _select(mat, inside)
    bpy.ops.essence.add_folder()
    e = mat.essence.layers[mat.essence.active_index]
    e.name = name
    return e


def _two_inside(ob, mat):
    """Base 0.2, a folder with a 0.5 mask holding 0.8 and above it 0.4."""
    _folder(mat, "F")
    _layer(mat, 0.8, "L1")
    _select(mat, "L1")
    _layer(mat, 0.4, "L2")
    _select(mat, "F")
    _mask(mat, 0.5)


def test_a_new_folder_is_passthrough_and_old_ones_read_normal(addon):
    ob, mat = _base()
    f = _folder(mat, "F")
    check(f.folder_mode == 'PASSTHROUGH', "a new folder is Passthrough")
    old = mat.essence.layers.add()      # what a file from before modes existed holds
    old.kind = 'FOLDER'
    check(old.folder_mode == 'NORMAL', "a folder without a mode reads as Normal")
    mat.essence.layers.remove(len(mat.essence.layers) - 1)


def test_passthrough_blends_each_layer_with_the_folder_mask(addon):
    ob, mat = _base()
    _two_inside(ob, mat)
    check(_entry(mat, "L1").parent == _entry(mat, "F").uid
          and _entry(mat, "L2").parent == _entry(mat, "F").uid, "both layers are in the folder")
    # Each layer blends onto the stack with mask * 0.5: lerp(lerp(0.2, 0.8, .5), 0.4, .5)
    close(_rough(ob, mat), 0.45, 0.02, "passthrough")
    _entry(mat, "F").folder_mode = 'NORMAL'
    # The layers are combined first (0.4 on top of 0.8), then mixed by 0.5: lerp(0.2, 0.4, .5)
    close(_rough(ob, mat), 0.3, 0.02, "normal")
    check(not any(n.name.startswith(addon.nodes.FOLDER_FACTOR_PREFIX) for n in mat.node_tree.nodes),
          "a Normal folder leaves no factor nodes behind")
    _entry(mat, "F").folder_mode = 'PASSTHROUGH'
    close(_rough(ob, mat), 0.45, 0.02, "and back")


def test_passthrough_multiplies_the_layer_mask(addon):
    ob, mat = _base()
    _folder(mat, "F")
    _mask(mat, 0.5)
    _select(mat, "F")
    _layer(mat, 0.8, "L1")
    _mask(mat, 0.5)
    close(_rough(ob, mat), 0.2 + 0.6 * 0.25, 0.02, "layer mask times folder mask")


def test_opacity_and_eye(addon):
    ob, mat = _base()
    _folder(mat, "F")
    _layer(mat, 0.8, "L1")
    folder = _node(mat, "F")
    folder.inputs['Opacity'].default_value = 0.5
    close(_rough(ob, mat), 0.5, 0.02, "passthrough opacity")
    _entry(mat, "F").folder_mode = 'NORMAL'
    close(_rough(ob, mat), 0.5, 0.02, "normal opacity")
    folder.inputs['Visible'].default_value = False
    close(_rough(ob, mat), 0.2, 0.02, "normal hidden")
    _entry(mat, "F").folder_mode = 'PASSTHROUGH'
    close(_rough(ob, mat), 0.2, 0.02, "passthrough hidden")


def test_nested_folders_compound(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.essence.layers[-1].name = "Base"
    _node(mat, "Base").inputs['Roughness B'].default_value = 0.0
    _folder(mat, "Outer")
    _folder(mat, "Inner", inside="Outer")
    # (entries are looked up by name: a reference into the collection moves with a reorder)
    check(_entry(mat, "Inner").parent == _entry(mat, "Outer").uid,
          "Add Folder with a folder active nests it")
    _layer(mat, 1.0, "L")
    check(_entry(mat, "L").parent == _entry(mat, "Inner").uid, "the layer is in the inner folder")
    _node(mat, "Outer").inputs['Opacity'].default_value = 0.5
    _node(mat, "Inner").inputs['Opacity'].default_value = 0.5
    close(_rough(ob, mat), 0.25, 0.02, "passthrough in passthrough: 0.5 * 0.5")
    _entry(mat, "Inner").folder_mode = 'NORMAL'
    close(_rough(ob, mat), 0.25, 0.02, "normal in passthrough")
    _entry(mat, "Outer").folder_mode = 'NORMAL'
    close(_rough(ob, mat), 0.25, 0.02, "normal in normal")
    _entry(mat, "Inner").folder_mode = 'PASSTHROUGH'
    close(_rough(ob, mat), 0.25, 0.02, "passthrough in normal")
    _node(mat, "Inner").inputs['Visible'].default_value = False
    close(_rough(ob, mat), 0.0, 0.02, "the inner eye hides it")


def _order(mat):
    return [(e.name, next((p.name for p in mat.essence.layers if p.uid == e.parent), ""))
            for e in mat.essence.layers if e.kind in ('LAYER', 'FOLDER')]


def test_folders_move_in_and_out_of_folders(addon):
    ob, mat = _base()
    _folder(mat, "A")
    _select(mat, "Base")
    _folder(mat, "B")                    # above Base, below A
    check(_order(mat) == [("Base", ""), ("B", ""), ("A", "")], f"start: {_order(mat)}")
    bpy.ops.essence.move_layer(direction='UP')
    check(_entry(mat, "B").parent == _entry(mat, "A").uid, f"B moved into A: {_order(mat)}")
    bpy.ops.essence.move_layer(direction='UP')
    check(_order(mat) == [("Base", ""), ("A", ""), ("B", "")], f"and out above it: {_order(mat)}")
    bpy.ops.essence.move_layer(direction='DOWN')
    bpy.ops.essence.move_layer(direction='DOWN')
    check(_order(mat) == [("Base", ""), ("B", ""), ("A", "")], f"down through A: {_order(mat)}")
    # A layer walks through two levels.
    _select(mat, "B")
    bpy.ops.essence.move_layer(direction='UP')          # B into A
    _select(mat, "Base")
    for _ in range(2):
        bpy.ops.essence.move_layer(direction='UP')      # into A, then into B
    check(_entry(mat, "Base").parent == _entry(mat, "B").uid, f"two levels deep: {_order(mat)}")
    for _ in range(2):
        bpy.ops.essence.move_layer(direction='DOWN')
    check(_entry(mat, "Base").parent == "", f"and back out: {_order(mat)}")


def test_removing_a_nested_folder_keeps_its_content_in_the_parent(addon):
    ob, mat = _base()
    _folder(mat, "Outer")
    _folder(mat, "Inner", inside="Outer")
    _layer(mat, 0.8, "L")
    _select(mat, "Inner")
    bpy.ops.essence.remove_layer()
    check(_entry(mat, "L").parent == _entry(mat, "Outer").uid, f"L is in Outer: {_order(mat)}")
    close(_rough(ob, mat), 0.8, 0.02, "and still shows")


def test_broken_folder_cycles_are_recovered(addon):
    ob, mat = _base()
    _folder(mat, "A")
    _select(mat, "Base")
    _folder(mat, "B")
    a, b = _entry(mat, "A"), _entry(mat, "B")
    a_uid, b_uid = a.uid, b.uid
    a.parent = b_uid                        # broken data: each inside the other
    _entry(mat, "B").parent = a_uid
    check(_entry(mat, "A").parent == b_uid and _entry(mat, "B").parent == a_uid, "the cycle is set")
    t = addon.stack.Tree(mat.essence.layers)
    check(set(t.order()) == {e.uid for e in mat.essence.layers}, "nothing is lost")
    t.apply()
    check(bpy.ops.essence.rebuild() == {'FINISHED'}, "a rebuild still works")


def test_folder_group_upgrade_keeps_values(addon):
    ob, mat = _base()
    _folder(mat, "F")
    _layer(mat, 0.8, "L1")
    folder = _node(mat, "F")
    folder.inputs['Visible'].default_value = False
    ng = folder.node_tree
    ng[addon.nodes.VERSION_KEY] = 3
    ng.interface.remove(ng.interface.items_tree['Opacity'])     # what version 3 had
    check(bpy.ops.essence.rebuild() == {'FINISHED'}, "rebuild failed")
    folder = _node(mat, "F")
    check(folder.node_tree[addon.nodes.VERSION_KEY] == addon.nodes.FOLDER_GROUP_VERSION, "upgraded")
    check(folder.inputs['Visible'].default_value is False, "the eye is kept")
    check(abs(folder.inputs['Opacity'].default_value - 1.0) < 1e-6, "the new opacity starts at 1")
    check('Factor' in folder.outputs, "and the Factor output exists")
    close(_rough(ob, mat), 0.2, 0.02, "hidden folder after the upgrade")


def test_nested_folders_survive_save_and_reload(addon):
    ob, mat = _base()
    _folder(mat, "Outer")
    _folder(mat, "Inner", inside="Outer")
    _layer(mat, 1.0, "L")
    _node(mat, "Outer").inputs['Opacity'].default_value = 0.5
    _entry(mat, "Inner").folder_mode = 'NORMAL'
    want = _rough(ob, mat)
    folder = tempfile.mkdtemp(prefix="essence_folders_")
    path = os.path.join(folder, "t.blend")
    try:
        name = ob.name
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        ob = bpy.data.objects[name]
        mat = ob.active_material
        check(_entry(mat, "Inner").parent == _entry(mat, "Outer").uid, "nesting kept")
        check(_entry(mat, "Inner").folder_mode == 'NORMAL'
              and _entry(mat, "Outer").folder_mode == 'PASSTHROUGH', "modes kept")
        close(_rough(ob, mat), want, 0.02, "same value")
        check(bpy.ops.essence.rebuild() == {'FINISHED'}, "rebuild after reload")
        close(_rough(ob, mat), want, 0.02, "same value after a rebuild")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_folder_panels_draw(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat = _base()
    _folder(mat, "Outer")
    _folder(mat, "Inner", inside="Outer")
    _layer(mat, 1.0, "L")
    _select(mat, "Inner")
    log = _draw(ui._props, ob)
    check(any(c[0] == "prop" and c[1][1] == "folder_mode" for c in log), "the folder mode")
    check(any(c[0] == "prop" and c[2].get("text") == "Opacity" for c in log), "the folder opacity")
    for tree_view in (True, False):
        mat.essence.use_tree = tree_view
        log = _draw(ui._stack, ob)
    mat.essence.use_tree = True
    log = _draw(ui._stack, ob)
    names = [c[2].get("text") for c in log if c[0] == "operator" and c[1][0] == "essence.select_entry"]
    check(names[:4] == ["Outer", "Inner", "L", "Base"], f"the tree view shows the nesting: {names}")
    check(ui._depth(mat.essence.layers, _entry(mat, "L")) == 2, "the list indents two levels")
