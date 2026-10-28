"""Duplicate, copy and paste of layers and folders, and the menu of a stack row."""

import os
import shutil
import tempfile
from types import SimpleNamespace

import bpy

from harness import check, close, make_plane, sample_socket
from test_blend import _mask, _set


def _entry(mat, name):
    return next(e for e in mat.essence.layers if e.name == name)


def _select(mat, name):
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(name)


def _node(mat, e):
    return mat.node_tree.nodes[e.node_name]


def _top_value(ob, mat, socket="Roughness"):
    top = next(_node(mat, e) for e in reversed(list(mat.essence.layers))
               if e.kind == 'LAYER' and not e.parent)
    return sample_socket(ob, mat, top.outputs[socket])


def _rich_layer():
    """A base layer and above it a layer with a paint mask holding a Fill and an Invert, a
    Levels adjustment on its roughness and a procedural color with a ramp."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    _set(_node(mat, mat.essence.layers[-1]), (1, 0, 0, 1), 0.2, 0.0, 0.0)
    bpy.ops.essence.add_layer()
    layer = mat.essence.layers[mat.essence.active_index]
    layer.name = "Rust"
    _set(_node(mat, layer), (0, 0, 1, 1), 0.8, 1.0, 0.0)
    _mask(mat, 0.25, invert=True)                         # 0.75
    _select(mat, "Rust")
    bpy.ops.essence.add_adjustment(adj_type='LEVELS')
    rust = _entry(mat, "Rust")
    src = next(s for s in rust.sources if s.channel == 'COLOR')
    src.kind = 'PROCEDURAL'
    src.proc_type = 'GRADIENT'
    return ob, mat


def _children(mat, uid):
    return sorted((e.kind, e.name) for e in mat.essence.layers if e.parent == uid)


def test_duplicate_copies_the_layer_and_everything_it_owns(addon):
    ob, mat = _rich_layer()
    before = _top_value(ob, mat)
    n_before = len(mat.essence.layers)
    rust = _entry(mat, "Rust")
    orig_uid = rust.uid
    orig_kids = _children(mat, orig_uid)
    _select(mat, "Rust")
    check(bpy.ops.essence.duplicate() == {'FINISHED'}, "duplicate failed")
    s = mat.essence
    copy = s.layers[s.active_index]
    check(copy.name == "Rust.001", f"the copy's name: {copy.name}")
    check(len(s.layers) == 2 * n_before - 1, f"{n_before} entries -> {len(s.layers)}")
    check(copy.uid != orig_uid and copy.parent == "", "a new uid, same level")
    check(_children(mat, copy.uid) == orig_kids, f"{_children(mat, copy.uid)} vs {orig_kids}")
    # It sits right above the original, and the mask's items came along under the new mask.
    order = [e.uid for e in s.layers if e.kind == 'LAYER' and not e.parent]
    check(order.index(copy.uid) == order.index(orig_uid) + 1, "right above the original")
    new_mask = next(e for e in s.layers if e.kind == 'MASK' and e.parent == copy.uid)
    old_mask = next(e for e in s.layers if e.kind == 'MASK' and e.parent == orig_uid)
    check(_children(mat, new_mask.uid) == _children(mat, old_mask.uid), "the mask's items")
    # Its own nodes, with the same values, and its own painted image.
    check(copy.node_name != rust.node_name, "its own group node")
    c, o = _node(mat, copy), _node(mat, _entry(mat, "Rust"))
    for sock in ("Roughness B", "Metallic B"):
        close(c.inputs[sock].default_value, o.inputs[sock].default_value, 1e-6, sock)
    img_new = mat.node_tree.nodes[new_mask.mask_node_name].image
    img_old = mat.node_tree.nodes[old_mask.mask_node_name].image
    check(img_new is not img_old, "the painted mask image is copied, not shared")
    close(img_new.pixels[0], img_old.pixels[0], 1e-6, "same pixels")
    check(img_new.packed_file is not None, "the copy is packed")
    # Every source node of the copy is tagged with its uids; none still points at the original.
    keys = {str(n.get(addon.sources.SRC_KEY, "")).split(":")[0] for n in mat.node_tree.nodes}
    for e in s.layers:
        if e.kind in ('LAYER', 'MASK', 'ADJUSTMENT', 'FILL'):
            check(e.uid in keys, f"{e.kind} {e.name} has nodes") if e.kind != 'LAYER' else None
    # The copy blends again over the original by its own 0.75 mask: 0.65 -> 0.7625.
    close(_top_value(ob, mat)[0], before[0] + (0.8 - before[0]) * 0.75, 0.01,
          "the copy blends over the original")
    # Painting the copy's mask white hides the copy (its Invert makes it 0): the original
    # shows again.
    img_new.pixels.foreach_set([1.0, 1.0, 1.0, 1.0] * (img_new.size[0] * img_new.size[1]))
    img_new.update()
    close(img_old.pixels[0], 0.25, 1e-3, "the original image is untouched")
    close(_top_value(ob, mat)[0], before[0], 0.01, "the original still shows its own mask")


def test_duplicate_keeps_procedural_and_ramp_settings(addon):
    ob, mat = _rich_layer()
    rust = _entry(mat, "Rust")
    nodes = addon.sources.source_nodes(mat.node_tree, rust.uid, 'COLOR')
    ramp = nodes['RAMP'].color_ramp
    ramp.elements[0].color = (0.1, 0.2, 0.3, 1.0)
    ramp.elements.new(0.4).color = (0.9, 0.8, 0.7, 1.0)
    nodes['MAPPING'].inputs['Location'].default_value = (0.2, 0.0, 0.0)
    _select(mat, "Rust")
    bpy.ops.essence.duplicate()
    copy = mat.essence.layers[mat.essence.active_index]
    cn = addon.sources.source_nodes(mat.node_tree, copy.uid, 'COLOR')
    check(set(cn) == set(nodes), f"the same source nodes: {set(cn)} vs {set(nodes)}")
    check(cn['TEXTURE'].node_tree == nodes['TEXTURE'].node_tree, "the same procedural")
    els = cn['RAMP'].color_ramp.elements
    check(len(els) == 3, f"three ramp stops: {len(els)}")
    close(els[1].position, 0.4, 1e-6, "the added stop")
    close(els[0].color[1], 0.2, 1e-6, "the first stop's color")
    close(cn['MAPPING'].inputs['Location'].default_value[0], 0.2, 1e-6, "the mapping")
    check(cn['MAPPING'] is not nodes['MAPPING'], "its own mapping node")
    # Wired: what feeds the copy's Color B reads the same as the original's.
    a = sample_socket(ob, mat, _node(mat, copy).inputs['Color B'].links[0].from_socket)
    b = sample_socket(ob, mat, _node(mat, _entry(mat, "Rust")).inputs['Color B']
                      .links[0].from_socket)
    for i in range(3):
        close(a[i], b[i], 0.01, f"color channel {i}")


def test_duplicate_a_nested_folder(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    bpy.ops.essence.add_folder()
    _entry(mat, "Folder 1").name = "Outer"
    bpy.ops.essence.add_folder()                       # inside Outer
    _entry(mat, "Folder 2").name = "Inner"
    bpy.ops.essence.add_layer()                        # inside Inner
    _entry(mat, "Layer 2").name = "Deep"
    _select(mat, "Outer")
    _mask(mat, 0.5)
    _select(mat, "Outer")
    bpy.ops.essence.duplicate()
    s = mat.essence
    copy = s.layers[s.active_index]
    check(copy.name == "Outer.001" and copy.kind == 'FOLDER', copy.name)
    inner = next(e for e in s.layers if e.parent == copy.uid and e.kind == 'FOLDER')
    deep = next(e for e in s.layers if e.parent == inner.uid)
    check((inner.name, deep.name) == ("Inner", "Deep"), "the nesting came along")
    check(any(e.kind == 'MASK' and e.parent == copy.uid for e in s.layers), "the folder mask")
    check(copy.folder_mode == _entry(mat, "Outer").folder_mode, "the folder mode")
    check(sum(1 for e in s.layers if e.name == "Deep") == 2, "two Deep layers")


def test_copy_and_paste_into_another_material(addon):
    ob, mat = _rich_layer()
    want = _top_value(ob, mat)
    _select(mat, "Rust")
    check(bpy.ops.essence.copy() == {'FINISHED'}, "copy failed")
    other = make_plane("Other", location=(5, 0, 0))
    bpy.context.view_layer.objects.active = other
    check(bpy.ops.essence.paste() == {'FINISHED'}, "paste into an object without material")
    mat2 = other.active_material
    check(mat2 is not None and mat2 is not mat, "a new material")
    pasted = mat2.essence.layers[mat2.essence.active_index]
    check(pasted.name == "Rust", "the name")
    check(_children(mat2, pasted.uid) == _children(mat, _entry(mat, "Rust").uid), "its items")
    # Over the same base as in the first material, it looks the same.
    bpy.ops.essence.add_layer()
    base = mat2.essence.layers[mat2.essence.active_index]
    base_name = base.name
    _set(_node(mat2, base), (1, 0, 0, 1), 0.2, 0.0, 0.0)
    _select(mat2, "Rust")
    bpy.ops.essence.move_layer(direction='UP')
    order = [e.name for e in mat2.essence.layers if e.kind == 'LAYER']
    check(order == [base_name, "Rust"], f"base under the pasted layer: {order}")
    close(_top_value(other, mat2)[0], want[0], 0.01, "the same roughness in the other material")
    # The mask image is a copy there too, and nothing points into the first material.
    m2 = next(e for e in mat2.essence.layers if e.kind == 'MASK')
    m1 = next(e for e in mat.essence.layers if e.kind == 'MASK')
    check(mat2.node_tree.nodes[m2.mask_node_name].image is not
          mat.node_tree.nodes[m1.mask_node_name].image, "its own mask image")
    # Pasting with a folder selected puts it inside.
    bpy.ops.essence.add_folder()
    folder = mat2.essence.layers[mat2.essence.active_index].uid
    bpy.ops.essence.paste()
    pasted = mat2.essence.layers[mat2.essence.active_index]
    check(pasted.parent == folder, "pasted inside the selected folder")


def test_paste_without_a_copy_and_after_the_source_is_gone(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    addon.clipboard._clip = None
    check(not bpy.ops.essence.paste.poll(), "nothing copied: Paste is off")
    bpy.ops.essence.copy()
    check(bpy.ops.essence.paste.poll(), "copied: Paste is on")
    bpy.ops.essence.remove_layer()
    check(not bpy.ops.essence.paste.poll(), "the copied layer is gone: Paste is off")


def test_duplicate_a_filter_copies_its_group_and_image(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    _mask(mat, 0.5)
    bpy.ops.essence.add_filter(filter_type='BLUR')
    f = mat.essence.layers[mat.essence.active_index]
    fuid = f.uid
    img = bpy.data.images.new(addon.filters.image_name(fuid), 4, 4)
    _select(mat, "Layer 1")
    bpy.ops.essence.duplicate()
    copy = mat.essence.layers[mat.essence.active_index]
    mask = next(e for e in mat.essence.layers if e.kind == 'MASK' and e.parent == copy.uid)
    nf = next(e for e in mat.essence.layers if e.kind == 'FILTER' and e.parent == mask.uid)
    check(bpy.data.node_groups.get(addon.filters.group_name(nf.uid)) is not None,
          "the filter's compositor group is copied")
    check(addon.filters.result_image(nf.uid) is not None, "and its result")
    check(addon.filters.result_image(fuid) is img, "the original keeps its own")
    addon.filters.cleanup()
    check(bpy.data.node_groups.get(addon.filters.group_name(nf.uid)) is not None,
          "cleanup keeps the copy's group")


def test_duplicate_survives_save_and_reload(addon):
    ob, mat = _rich_layer()
    _select(mat, "Rust")
    bpy.ops.essence.duplicate()
    want = _top_value(ob, mat)
    folder = tempfile.mkdtemp(prefix="essence_clip_")
    path = os.path.join(folder, "dup.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        ob = bpy.data.objects["Plane"]
        mat = ob.active_material
        check(sum(1 for e in mat.essence.layers if e.name.startswith("Rust")) == 2, "both")
        close(_top_value(ob, mat)[0], want[0], 0.01, "same look after reload")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_remove_entry_by_uid(addon):
    ob, mat = _rich_layer()
    uid = _entry(mat, "Rust").uid
    _select(mat, "Layer 1")
    check(bpy.ops.essence.remove_entry(uid=uid) == {'FINISHED'}, "remove failed")
    check(all(e.uid != uid for e in mat.essence.layers), "gone")
    check(bpy.ops.essence.remove_entry(uid="nope") == {'CANCELLED'}, "unknown uid")


def test_row_menu_adds_a_mask(addon):
    from test_ui import Rec
    ob, mat = _rich_layer()
    base = next(e for e in mat.essence.layers if e.kind == 'LAYER' and e.name != "Rust")
    uid = base.uid
    log = []
    addon.clipboard.draw_entry_menu(Rec(log), mat, base)
    check(any(c[0] == "operator" and c[1][0] == "essence.add_mask_to" for c in log),
          "a layer without a mask offers Add Mask")
    log = []
    addon.clipboard.draw_entry_menu(Rec(log), mat, _entry(mat, "Rust"))
    check(not any(c[0] == "operator" and c[1][0] == "essence.add_mask_to" for c in log),
          "a layer with a mask does not")
    _select(mat, "Rust")                # another row is active: the menu's row gets the mask
    check(bpy.ops.essence.add_mask_to(uid=uid, fill='BLACK') == {'FINISHED'}, "add failed")
    masks = [e for e in mat.essence.layers if e.kind == 'MASK' and e.parent == uid]
    check(len(masks) == 1, f"one mask on the base layer: {len(masks)}")
    check(mat.essence.layers[mat.essence.active_index].uid == masks[0].uid, "the mask is selected")
    check(bpy.ops.essence.add_mask_to(uid=uid) == {'CANCELLED'}, "no second mask")
    check(bpy.ops.essence.add_mask_to(uid="nope") == {'CANCELLED'}, "unknown uid")


def test_row_menus_draw(addon):
    from test_ui import Rec, _ctx
    ob, mat = _rich_layer()
    clip = addon.clipboard
    for e in list(mat.essence.layers):
        log = []
        clip.draw_entry_menu(Rec(log), mat, e)
        ops = [c[1][0] for c in log if c[0] == "operator"]
        check("essence.rename_entry" in ops and "essence.remove_entry" in ops, f"{e.kind}: {ops}")
        check(("essence.duplicate" in ops) == (e.kind in ('LAYER', 'FOLDER')),
              f"{e.kind}: duplicate only on layers and folders")
    # Blender's right-click menu: only on a stack row's button, for that row.
    rust = _entry(mat, "Rust")
    for op, want in ((SimpleNamespace(bl_rna=SimpleNamespace(identifier="ESSENCE_OT_select_entry"),
                                      uid=rust.uid), True),
                     (SimpleNamespace(bl_rna=SimpleNamespace(identifier="OBJECT_OT_delete"),
                                      uid=""), False), (None, False)):
        log = []
        ctx = SimpleNamespace(active_object=ob, button_operator=op)
        clip._button_menu(SimpleNamespace(layout=Rec(log)), ctx)
        uids = [c for c in log if c[0] == "operator"]
        check(bool(uids) == want, f"{op}: menu shown {bool(uids)}")
    # The menu of a Shift+click works on the active entry.
    _select(mat, "Rust")
    log = []
    clip.ESSENCE_MT_entry.draw(SimpleNamespace(layout=Rec(log)), _ctx(ob))
    check(any(c[0] == "operator" and c[1][0] == "essence.duplicate" for c in log), "Shift menu")
    # The bar has the three buttons.
    from essence import ui
    from test_ui import _draw
    log = _draw(ui._stack, ob)
    bar = {c[1][0] for c in log if c[0] == "operator"}
    check({"essence.duplicate", "essence.copy", "essence.paste"} <= bar, "bar buttons")
