"""Renaming rows of the stack: the rename popup operator and the Ctrl+click on a row."""

from types import SimpleNamespace

import bpy

from harness import check, make_plane


def _click(ctrl=False, shift=False):
    return SimpleNamespace(ctrl=ctrl, shift=shift)


def _entry(mat, uid):
    return next(e for e in mat.essence.layers if e.uid == uid)


def _setup():
    ob = make_plane("Plane", size=2.0)
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    layer = mat.essence.layers[mat.essence.active_index].uid
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=16)
    mask = mat.essence.layers[mat.essence.active_index].uid
    bpy.ops.essence.add_adjustment(adj_type='INVERT')
    adj = mat.essence.layers[mat.essence.active_index].uid
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == mask)
    bpy.ops.essence.add_fill()
    fill = mat.essence.layers[mat.essence.active_index].uid
    folder = next(e.uid for e in mat.essence.layers if e.kind == 'FOLDER')
    return ob, mat, folder, layer, mask, adj, fill


def test_rename_any_row(addon):
    ob, mat, folder, layer, mask, adj, fill = _setup()
    for uid, name in ((folder, "Chrome"), (layer, "Scratches"), (mask, "Edge wear mask"),
                      (adj, "Contrast boost"), (fill, "Dirt fill")):
        check(bpy.ops.essence.rename_entry(uid=uid, name=name) == {'FINISHED'}, f"{name}: rename failed")
        check(_entry(mat, uid).name == name, f"{name}: the name was not set")
    check(mat.node_tree.nodes[_entry(mat, layer).node_name].label == "Scratches",
          "the layer's node shows the new name in the node editor")
    check(mat.node_tree.nodes[_entry(mat, mask).node_name].label == "Edge wear mask",
          "and the mask's")
    check(mat.node_tree.nodes[_entry(mat, folder).node_name].label == "Chrome", "and the folder's")


def test_a_name_is_trimmed_and_an_empty_one_is_ignored(addon):
    ob, mat, folder, layer, mask, adj, fill = _setup()
    bpy.ops.essence.rename_entry(uid=layer, name="  Base paint  ")
    check(_entry(mat, layer).name == "Base paint", "spaces around it are dropped")
    check(bpy.ops.essence.rename_entry(uid=layer, name="   ") == {'CANCELLED'}, "an empty name is refused")
    check(_entry(mat, layer).name == "Base paint", "and the name is as it was")
    check(bpy.ops.essence.rename_entry(uid="nope", name="x") == {'CANCELLED'}, "no such row")


def test_a_renamed_row_keeps_its_name_when_the_stack_changes(addon):
    ob, mat, folder, layer, mask, adj, fill = _setup()
    bpy.ops.essence.rename_entry(uid=folder, name="Chrome")
    bpy.ops.essence.rebuild()
    bpy.ops.essence.add_layer()
    check(_entry(mat, folder).name == "Chrome", "a rebuild and a new layer do not rename it")
    check(mat.node_tree.nodes[_entry(mat, folder).node_name].label == "Chrome", "nor its node")


def test_ctrl_click_on_a_row_selects_it_and_asks_for_a_name(addon):
    from essence import ops
    ob, mat, folder, layer, mask, adj, fill = _setup()
    asked = []
    original = bpy.ops.essence.rename_entry

    class Stub:
        def __init__(self, uid):
            self.uid = uid
            self.execute = lambda ctx: ops.ESSENCE_OT_select_entry.execute(self, ctx)

    # a plain click only selects
    mat.essence.active_index = 0
    r = ops.ESSENCE_OT_select_entry.invoke(Stub(layer), bpy.context, _click(ctrl=False))
    check(r == {'FINISHED'} and mat.essence.layers[mat.essence.active_index].uid == layer,
          "a plain click selects the row")
    # ctrl-click selects too, and then opens the rename popup (there is no window in the tests:
    # the popup operator is allowed to refuse, the selection must have happened)
    mat.essence.active_index = 0
    try:
        r = ops.ESSENCE_OT_select_entry.invoke(Stub(mask), bpy.context, _click(ctrl=True))
    except RuntimeError:
        r = {'FINISHED'}
    check(mat.essence.layers[mat.essence.active_index].uid == mask, "ctrl-click selects the row")
    # a row that is gone is not renamed
    r = ops.ESSENCE_OT_select_entry.invoke(Stub("nope"), bpy.context, _click(ctrl=True))
    check(r == {'CANCELLED'}, "no such row, nothing happens")
    check("Ctrl" in ops.ESSENCE_OT_select_entry.bl_description, "the tooltip tells about it")
    r = ops.ESSENCE_OT_rename_entry.invoke(SimpleNamespace(uid="nope", name=""), bpy.context,
                                           _click())
    check(r == {'CANCELLED'}, "the popup does not open for a row that is gone")


def test_rename_survives_save_and_reload(addon):
    import os
    import shutil
    import tempfile
    ob, mat, folder, layer, mask, adj, fill = _setup()
    bpy.ops.essence.rename_entry(uid=folder, name="Chrome")
    name = ob.name
    path_dir = tempfile.mkdtemp(prefix="essence_rename_")
    path = os.path.join(path_dir, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        check(_entry(mat, folder).name == "Chrome", "the name is in the file")
    finally:
        shutil.rmtree(path_dir, ignore_errors=True)


def test_the_rename_popup_is_ready_to_type_into(addon):
    """Blender's properties dialog does not put the cursor in the field by itself: the field is
    drawn with activate_init, which activates it when the popup opens."""
    from essence import ops

    class Layout:
        def __init__(self):
            self.drawn = []
            self.activate_init = False

        def row(self, **kw):
            sub = Layout()
            self.drawn.append(("row", sub))
            return sub

        def prop(self, data, name, **kw):
            self.drawn.append(("prop", name, kw))

    layout = Layout()
    ops.ESSENCE_OT_rename_entry.draw(SimpleNamespace(layout=layout, name="x"), bpy.context)
    kind, row = layout.drawn[0]
    check(kind == "row" and row.activate_init is True, "the row has activate_init set")
    check(row.drawn and row.drawn[0][0] == "prop" and row.drawn[0][1] == "name"
          and row.drawn[0][2].get("text") == "", "and holds the name field with no label")
    check('activate_init' in bpy.types.UILayout.bl_rna.properties,
          "this Blender has the flag (a misspelled attribute would not fail in the stub)")
