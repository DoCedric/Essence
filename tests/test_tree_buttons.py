import bpy

from harness import check, make_plane
from test_ui import _draw


def _buttons(ob):
    from essence import ui
    log = _draw(ui._stack, ob)
    return [c[2] for c in log if c[0] == "operator" and c[1][0] == "essence.add_mask_menu"], log


def test_mask_button_only_on_layers_and_folders_without_a_mask(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    buttons, _ = _buttons(ob)
    layers_and_folders = [e for e in mat.essence.layers if e.kind in ('LAYER', 'FOLDER')]
    check(len(buttons) == len(layers_and_folders) == 3, f"{len(buttons)} buttons")
    # Give the inner layer a mask: its button goes away, the others stay.
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    buttons, _ = _buttons(ob)
    check(len(buttons) == 2, f"{len(buttons)} buttons after adding a mask")
    # A folder with a mask loses its button too.
    folder = next(i for i, e in enumerate(mat.essence.layers) if e.kind == 'FOLDER')
    mat.essence.active_index = folder
    bpy.ops.essence.add_mask(source='PAINT', fill='BLACK')
    buttons, _ = _buttons(ob)
    check(len(buttons) == 1, f"{len(buttons)} buttons after masking the folder")


def test_mask_button_selects_the_owner_before_the_menu(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    first = mat.essence.layers[0]
    uid = first.uid
    check(mat.essence.layers[mat.essence.active_index].uid != uid, "setup: other layer active")
    check(bpy.ops.essence.add_mask_menu(uid=uid) == {'FINISHED'}, "button operator failed")
    check(mat.essence.layers[mat.essence.active_index].uid == uid, "owner not selected")
    # The menu's operators then add the mask to that owner.
    bpy.ops.essence.add_mask(source='PAINT', fill='BLACK')
    masks = [e for e in mat.essence.layers if e.kind == 'MASK']
    check(len(masks) == 1 and masks[0].parent == uid, "mask went to the wrong layer")
