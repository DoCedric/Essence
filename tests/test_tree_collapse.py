import bpy

from harness import check, make_plane
from test_ui import _draw


def _build():
    """Folder (with a mask holding an Invert) containing a layer (an Invert item, a mask with a
    Gamma item) and a loose layer above the folder (no items, no mask)."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=64)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')           # on the folder's mask
    mat = ob.active_material
    folder = next(i for i, e in enumerate(mat.essence.layers) if e.kind == 'FOLDER')
    mat.essence.active_index = folder
    bpy.ops.essence.add_layer()                                  # into the folder
    bpy.ops.essence.add_adjustment(adj_type='INVERT')           # on the inner layer
    inner = next(e for e in mat.essence.layers if e.kind == 'LAYER' and e.parent)
    inner_uid = inner.uid
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == inner_uid)
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=64)
    bpy.ops.essence.add_adjustment(adj_type='GAMMA')            # on the inner layer's mask
    return ob, mat


def _rows(ob):
    from essence import ui
    log = _draw(ui._stack, ob)
    selects = [c for c in log if c[0] == "operator" and c[1][0] == "essence.select_entry"]
    folds = [c for c in log if c[0] == "prop" and c[1][1] == "collapsed"]
    return len(selects), folds


def _entry(mat, kind, parented=None):
    return next(e for e in mat.essence.layers if e.kind == kind
                and (parented is None or bool(e.parent) == parented))


def test_fold_triangles_only_where_something_hangs_under_the_entry(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    n, folds = _rows(ob)
    check(not folds, "a layer with nothing under it has no triangle")
    bpy.ops.essence.add_adjustment(adj_type='INVERT')
    n, folds = _rows(ob)
    check(len(folds) == 1, f"a layer with an item has one triangle, not {len(folds)}")


def test_collapsing_a_layer_hides_its_items_and_its_masks_items(addon):
    ob, mat = _build()
    total, folds = _rows(ob)
    inner = _entry(mat, 'LAYER', parented=True)
    inner.collapsed = True
    shown, _ = _rows(ob)
    check(shown == total - 2, f"the layer's Invert and its mask's Gamma should hide: {total} -> {shown}")
    inner = _entry(mat, 'LAYER', parented=True)
    check(inner.collapsed, "state kept")
    inner.collapsed = False
    check(_rows(ob)[0] == total, "expanding shows them again")


def test_collapsing_a_folder_hides_its_layers_and_the_masks_items(addon):
    ob, mat = _build()
    total, _ = _rows(ob)
    folder = _entry(mat, 'FOLDER')
    folder.collapsed = True
    shown, _ = _rows(ob)
    # Hidden: the inner layer, its item, its mask, that mask's item, and the folder mask's item.
    check(shown == total - 5, f"{total} -> {shown}")
    # The folder and its mask row stay.
    check(shown >= 2, "folder and its mask stay visible")
    _entry(mat, 'FOLDER').collapsed = False
    check(_rows(ob)[0] == total, "expanding shows everything again")


def test_a_selected_entry_inside_a_collapsed_folder_does_not_break_the_panels(addon):
    from essence import ui
    ob, mat = _build()
    inner = _entry(mat, 'LAYER', parented=True)
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == inner.uid)
    _entry(mat, 'FOLDER').collapsed = True
    for fn in (ui._stack, ui._props):
        _draw(fn, ob)
