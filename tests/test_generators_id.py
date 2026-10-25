"""ID Select: choose one part of the ID map by its color."""

import bpy

from harness import check, close
from test_generators import _set, _setup, _tex, _value
from test_generators_maps import _map

RED, GREEN, BLUE = (0.9, 0.2, 0.2), (0.2, 0.7, 0.3), (0.2, 0.4, 0.9)


def _id_map(addon, mat, size=64):
    """An ID map with four parts: red left, green and blue in the middle, a near red (0.82, 0.2,
    0.2) on the right edge, to tell a tolerance apart."""
    img = bpy.data.images.new("ids", size, size, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    px = []
    for y in range(size):
        for x in range(size):
            u = (x + 0.5) / size
            c = RED if u < 0.25 else GREEN if u < 0.5 else BLUE if u < 0.75 else (0.82, 0.2, 0.2)
            px += [*c, 1.0]
    img.pixels.foreach_set(px)
    img.update()
    mat.node_tree.nodes[addon.nodes.map_slot_name('ID')].image = img
    bpy.ops.essence.rebuild()
    return img


P1, P2, P3, P4 = (0.1, 0.5), (0.4, 0.5), (0.6, 0.5), (0.9, 0.5)


def _pick(tex, color):
    tex.inputs['ID Color'].default_value = (*color, 1.0)


def test_id_select_chooses_one_part(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _set(tex, Tolerance=0.05, Softness=0.01)
    for color, expected in ((RED, (1, 0, 0, 0)), (GREEN, (0, 1, 0, 0)), (BLUE, (0, 0, 1, 0))):
        _pick(tex, color)
        got = [_value(ob, mat, e, p) for p in (P1, P2, P3, P4)]
        for g, want in zip(got, expected):
            close(g, want, 0.03, f"picked {color}: {got}")


def test_the_tolerance_decides_what_counts_as_the_same_part(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _pick(tex, RED)
    _set(tex, Tolerance=0.02, Softness=0.01)
    close(_value(ob, mat, e, P4), 0.0, 0.03, "a near red is another part with a small tolerance")
    _set(tex, Tolerance=0.2)
    close(_value(ob, mat, e, P4), 1.0, 0.03, "and the same one with a wider tolerance")
    close(_value(ob, mat, e, P2), 0.0, 0.03, "while green is still apart")
    _set(tex, Tolerance=2.0)
    close(_value(ob, mat, e, P3), 1.0, 0.03, "a huge tolerance takes everything")


def test_softness_and_invert(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _pick(tex, RED)
    _set(tex, Tolerance=0.08, Softness=0.16)               # the near red is 0.08 away: the middle
    close(_value(ob, mat, e, P4), 0.5, 0.05, "on the edge of the tolerance: half")
    _set(tex, Softness=0.01, Tolerance=0.05)
    tex.inputs['Invert'].default_value = True
    close(_value(ob, mat, e, P1), 0.0, 0.03, "inverted: the part is out")
    close(_value(ob, mat, e, P2), 1.0, 0.03, "and everything else is in")


def test_an_empty_id_slot_selects_nothing_and_says_so(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, e = _setup(addon, 'ID_SELECT')
    tree = mat.node_tree
    tex = _tex(addon, mat, e)
    check(tex.inputs['ID'].is_linked, "the ID input is never left open")
    check(tree.nodes.get(addon.sources.ID_FALLBACK) is not None, "a black stand-in")
    _pick(tex, RED)
    close(_value(ob, mat, e), 0.0, 0.03, "no ID map, no part")
    mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers) if x.uid == e.uid)
    said = [c[2].get("text", "") for c in _draw(ui._props, ob) if c[0] == "label"]
    check(any(t.startswith("Needs the ID map") for t in said), f"no warning: {said}")
    _id_map(addon, mat)
    check(tree.nodes.get(addon.sources.ID_FALLBACK) is None, "the stand-in goes with a map")
    said = [c[2].get("text", "") for c in _draw(ui._props, ob) if c[0] == "label"]
    check(not any(t.startswith("Needs the ID map") for t in said), "no warning with a map")


def _select_face(ob, left):
    import bmesh
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    for f in bm.faces:
        cx = sum(v.co.x for v in f.verts) / len(f.verts)
        if (cx < 0) == left:
            f.select_set(True)
            bm.faces.active = f
            break
    bmesh.update_edit_mesh(me)


def test_pick_from_a_selected_face(addon):
    from test_mesh_id import _grid, _planar
    ob = _grid("Part", _planar)
    for o in bpy.context.view_layer.objects:
        o.select_set(o is ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    src = next(s for s in e.sources if s.channel == 'ROUGHNESS')
    src.gen_type = 'ID_SELECT'
    src.kind = 'GENERATOR'
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    # outside Edit Mode there is no face to pick
    try:
        bpy.ops.essence.pick_id(uid=e.uid, channel='ROUGHNESS')
        refused = None
    except RuntimeError as err:
        refused = str(err)
    check(refused and "Edit Mode" in refused, f"should ask for Edit Mode: {refused}")
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        _select_face(ob, left=True)         # u is 0..0.5 on the left: red then green
        check(bpy.ops.essence.pick_id(uid=e.uid, channel='ROUGHNESS') == {'FINISHED'}, "pick failed")
        got = tuple(tex.inputs['ID Color'].default_value)[:3]
        # the left faces are (0..0.5, 0..0.5) and (0..0.5, 0.5..1): their centers are u 0.25
        check(any(all(abs(a - b) < 0.02 for a, b in zip(got, c)) for c in (RED, GREEN)),
              f"a color of the left half: {got}")
        _select_face(ob, left=False)
        bpy.ops.essence.pick_id(uid=e.uid, channel='ROUGHNESS')
        got2 = tuple(tex.inputs['ID Color'].default_value)[:3]
        check(any(all(abs(a - b) < 0.02 for a, b in zip(got2, c)) for c in (BLUE, (0.82, 0.2, 0.2))),
              f"a color of the right half: {got2}")
        check(got != got2, "two different parts, two different colors")
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.essence.rebuild()
    mat.node_tree.nodes[addon.nodes.map_slot_name('ID')].image = None
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.essence.pick_id(uid=e.uid, channel='ROUGHNESS')
        said = None
    except RuntimeError as err:
        said = str(err)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    check(said and "ID slot is empty" in said, f"an empty ID slot is reported: {said}")


def test_show_map_and_the_panel_buttons(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers) if x.uid == e.uid)
    log = _draw(ui._props, ob)
    ops_drawn = [c[1][0] for c in log if c[0] == "operator"]
    check("essence.pick_id" in ops_drawn and "essence.show_map" in ops_drawn,
          f"the pick and show buttons: {ops_drawn}")
    check(bpy.ops.essence.show_map(map_id='ID') == {'FINISHED'}, "show map")
    check(not any(c[0] == "prop" and getattr(c[1][0], "name", "") in ("ID",) for c in log),
          "the ID map input is internal")


def test_id_select_survives_save_and_reload(addon):
    import os
    import shutil
    import tempfile
    ob, mat, e = _setup(addon, 'ID_SELECT')
    img = _id_map(addon, mat)
    img.pack()
    _pick(_tex(addon, mat, e), BLUE)
    name, uid = ob.name, e.uid
    folder = tempfile.mkdtemp(prefix="essence_idsel_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        tex = addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS')['TEXTURE']
        check(tex.node_tree.name == "Essence Gen ID Select" and tex.inputs['ID'].is_linked,
              "generator and its map link survive a save")
        got = tuple(tex.inputs['ID Color'].default_value)[:3]
        check(all(abs(a - b) < 1e-5 for a, b in zip(got, BLUE)), f"the picked color: {got}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
