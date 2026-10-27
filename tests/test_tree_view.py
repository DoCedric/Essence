from types import SimpleNamespace

import bpy

from harness import check, make_plane
from test_ui import Rec, _draw


def _build(addon):
    """Folder with a layer (mask, fill, adjustments) and a loose layer above it."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()                    # into the folder
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    bpy.ops.essence.add_fill()
    bpy.ops.essence.add_adjustment(adj_type='INVERT')
    mat = ob.active_material
    layer = next(e.uid for e in mat.essence.layers if e.kind == 'LAYER' and e.parent)
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == layer)
    bpy.ops.essence.add_adjustment(adj_type='GAMMA')
    return ob, mat


def test_tree_view_draws_every_kind(addon):
    from essence import ui
    ob, mat = _build(addon)
    kinds = {e.kind for e in mat.essence.layers}
    check(kinds == {'LAYER', 'FOLDER', 'MASK', 'FILL', 'ADJUSTMENT'}, f"setup kinds {kinds}")
    log = _draw(ui._stack, ob)
    selects = [c for c in log if c[0] == "operator" and c[1][0] == "essence.select_entry"]
    check(len(selects) == len(mat.essence.layers), f"{len(selects)} rows for "
          f"{len(mat.essence.layers)} entries")
    pressed = [c for c in selects if c[2].get("depress")]
    check(len(pressed) == 1, "exactly the active entry is drawn pressed")


def test_select_entry_changes_the_active_entry(addon):
    ob, mat = _build(addon)
    target = mat.essence.layers[0]
    uid, name = target.uid, target.name
    check(bpy.ops.essence.select_entry(uid=uid) == {'FINISHED'}, "select failed")
    check(mat.essence.layers[mat.essence.active_index].uid == uid, f"active is not {name}")
    check(bpy.ops.essence.select_entry(uid="nope") == {'CANCELLED'}, "unknown uid")


def test_default_adjustment_name_follows_the_type(addon):
    ob, mat = _build(addon)
    bpy.ops.essence.add_adjustment(adj_type='LEVELS')
    e = mat.essence.layers[mat.essence.active_index]
    check(e.name == "Levels", e.name)
    e.adj_type = 'INVERT'
    check(mat.essence.layers[mat.essence.active_index].name == "Invert", "name should follow")
    mat.essence.layers[mat.essence.active_index].name = "My look"
    mat.essence.layers[mat.essence.active_index].adj_type = 'GAMMA'
    check(mat.essence.layers[mat.essence.active_index].name == "My look",
          "a name the user chose must stay")
