"""ID Select: choose parts of the ID map by color, from a palette."""

import bpy

from harness import check, close
from test_generators import _set, _setup, _tex, _value

RED, GREEN, BLUE = (0.9, 0.2, 0.2), (0.2, 0.7, 0.3), (0.2, 0.4, 0.9)
NEAR_RED = (0.82, 0.2, 0.2)


def _id_map(addon, mat, size=64):
    """An ID map with four parts: red on the left quarter, green, blue, and a near red (0.82, 0.2,
    0.2) on the right edge, to tell a tolerance apart."""
    img = bpy.data.images.new("ids", size, size, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    px = []
    for y in range(size):
        for x in range(size):
            u = (x + 0.5) / size
            c = RED if u < 0.25 else GREEN if u < 0.5 else BLUE if u < 0.75 else NEAR_RED
            px += [*c, 1.0]
    img.pixels.foreach_set(px)
    img.update()
    mat.node_tree.nodes[addon.nodes.map_slot_name('ID')].image = img
    bpy.ops.essence.rebuild()
    return img


P1, P2, P3, P4 = (0.1, 0.5), (0.4, 0.5), (0.6, 0.5), (0.9, 0.5)


def _pick(tex, color, i=1):
    tex.inputs[f'ID Color {i}'].default_value = (*color, 1.0)


def _palette(tex, *colors):
    for i, c in enumerate(colors, 1):
        _pick(tex, c, i)
    tex.inputs['Colors'].default_value = len(colors)


def _got(ob, mat, e):
    return [_value(ob, mat, e, p) for p in (P1, P2, P3, P4)]


def test_defaults(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    tex = _tex(addon, mat, e)
    close(tex.inputs['Tolerance'].default_value, 0.01, 1e-6, "the default tolerance")
    check(tex.inputs['Colors'].default_value == 1, "one color to begin with")
    check(addon.generators.GENERATORS['ID_SELECT'].version == 3, "version 3: white within the tolerance")


def test_an_exact_match_is_white_with_the_default_settings(addon):
    """Reported from the GUI: the picked part was a bright grey, not white. Version 2 centred
    the fade on the tolerance, so Tolerance 0.01 with Softness 0.03 gave 0.83 on an exact match."""
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    close(tex.inputs['Softness'].default_value, 0.03, 1e-6, "the default softness")
    _palette(tex, RED)
    for g, want in zip(_got(ob, mat, e), (1, 0, 0, 0)):
        close(g, want, 0.01, "defaults: the picked part is white, the rest black")


def test_id_select_chooses_one_part(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _set(tex, Tolerance=0.05, Softness=0.01)
    for color, expected in ((RED, (1, 0, 0, 0)), (GREEN, (0, 1, 0, 0)), (BLUE, (0, 0, 1, 0))):
        _palette(tex, color)
        for g, want in zip(_got(ob, mat, e), expected):
            close(g, want, 0.03, f"picked {color}")


def test_the_palette_selects_the_union_of_its_colors(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _set(tex, Tolerance=0.05, Softness=0.01)
    _palette(tex, RED, BLUE)
    for g, want in zip(_got(ob, mat, e), (1, 0, 1, 0)):
        close(g, want, 0.03, "red and blue are in, green is not")
    _palette(tex, RED, BLUE, GREEN)
    for g, want in zip(_got(ob, mat, e), (1, 1, 1, 0)):
        close(g, want, 0.03, "three colors")
    # a color past the count is stored but not used
    tex.inputs['Colors'].default_value = 2
    for g, want in zip(_got(ob, mat, e), (1, 0, 1, 0)):
        close(g, want, 0.03, "the third color is switched off by the count")
    _palette(tex, GREEN, GREEN, GREEN)
    for g, want in zip(_got(ob, mat, e), (0, 1, 0, 0)):
        close(g, want, 0.03, "the same color twice is the same")


def test_the_tolerance_decides_what_counts_as_the_same_part(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _palette(tex, RED)
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
    _palette(tex, RED)
    _set(tex, Tolerance=0.08, Softness=0.16)               # the near red is 0.08 away
    close(_value(ob, mat, e, P4), 1.0, 0.05, "on the edge of the tolerance: still in")
    _set(tex, Tolerance=0.04, Softness=0.08)
    close(_value(ob, mat, e, P4), 0.5, 0.05, "halfway through the softness: half")
    _set(tex, Tolerance=0.0, Softness=0.08)
    close(_value(ob, mat, e, P4), 0.0, 0.05, "past tolerance + softness: out")
    close(_value(ob, mat, e, P1), 1.0, 0.03, "an exact match is in even with no tolerance")
    _set(tex, Softness=0.01, Tolerance=0.05)
    tex.inputs['Invert'].default_value = True
    close(_value(ob, mat, e, P1), 0.0, 0.03, "inverted: the part is out")
    close(_value(ob, mat, e, P2), 1.0, 0.03, "and everything else is in")
    _palette(tex, RED, GREEN)
    close(_value(ob, mat, e, P2), 0.0, 0.03, "inverted palette: a color in the palette is out")
    close(_value(ob, mat, e, P3), 1.0, 0.03, "and blue, not in it, is in")


def _run_add(e, channel='ROUGHNESS'):
    return bpy.ops.essence.id_color_add(uid=e.uid, channel=channel)


def _run_remove(e, index, channel='ROUGHNESS'):
    return bpy.ops.essence.id_color_remove(uid=e.uid, channel=channel, index=index)


def test_adding_and_removing_colors(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _set(tex, Tolerance=0.05, Softness=0.01)
    _palette(tex, RED)
    check(_run_add(e) == {'FINISHED'}, "add failed")
    check(tex.inputs['Colors'].default_value == 2, "two colors")
    second = tuple(tex.inputs['ID Color 2'].default_value)[:3]
    check(second != RED, f"a new color is not the same as the first: {second}")
    _pick(tex, BLUE, 2)
    _run_add(e)
    _pick(tex, GREEN, 3)
    check(tex.inputs['Colors'].default_value == 3, "three colors")
    for g, want in zip(_got(ob, mat, e), (1, 1, 1, 0)):
        close(g, want, 0.03, "red, blue and green")
    check(_run_remove(e, 2) == {'FINISHED'}, "remove failed")        # blue goes
    check(tex.inputs['Colors'].default_value == 2, "two colors left")
    check(all(abs(a - b) < 1e-6 for a, b in zip(tex.inputs['ID Color 1'].default_value[:3], RED)),
          "the first color stays")
    check(all(abs(a - b) < 1e-6 for a, b in zip(tex.inputs['ID Color 2'].default_value[:3], GREEN)),
          "the colors below move up one place")
    for g, want in zip(_got(ob, mat, e), (1, 1, 0, 0)):
        close(g, want, 0.03, "red and green now, blue is gone")
    check(_run_remove(e, 1) == {'FINISHED'}, "remove the first")
    check(all(abs(a - b) < 1e-6 for a, b in zip(tex.inputs['ID Color 1'].default_value[:3], GREEN)),
          "green is the first now")
    try:
        _run_remove(e, 1)
        refused = False
    except RuntimeError:
        refused = True
    check(refused or tex.inputs['Colors'].default_value == 1, "the last color cannot be removed")
    check(tex.inputs['Colors'].default_value == 1, "at least one color stays")


def test_the_palette_holds_sixteen(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    for _ in range(addon.generators.MAX_ID_COLORS - 1):
        _run_add(e)
    tex = _tex(addon, mat, e)
    check(tex.inputs['Colors'].default_value == 16, "sixteen")
    try:
        r = _run_add(e)
    except RuntimeError:
        r = None
    check(r != {'FINISHED'} and tex.inputs['Colors'].default_value == 16, "no seventeenth")
    colors = {tuple(round(v, 3) for v in tex.inputs[f'ID Color {i}'].default_value[:3])
              for i in range(1, 17)}
    check(len(colors) >= 15, f"new colors are easy to tell apart: {len(colors)} different")


def test_an_older_id_select_with_one_color_becomes_a_palette(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    ng = tex.node_tree
    ng["essence_version"] = 1                       # a file from before the palette
    ng.interface.clear()
    ng.interface.new_socket("Fac", in_out='OUTPUT', socket_type='NodeSocketFloat')
    for name, kind in (("ID Color", 'NodeSocketColor'), ("Tolerance", 'NodeSocketFloat'),
                       ("Softness", 'NodeSocketFloat'), ("Invert", 'NodeSocketBool'),
                       ("ID", 'NodeSocketColor')):
        ng.interface.new_socket(name, in_out='INPUT', socket_type=kind)
    tex.node_tree = ng
    tex.inputs['ID Color'].default_value = (*BLUE, 1.0)
    tex.inputs['Tolerance'].default_value = 0.07
    bpy.ops.essence.rebuild()
    tex = _tex(addon, mat, e)
    check(ng["essence_version"] == 3 and 'ID Color' not in tex.inputs, "rebuilt to the palette")
    check(all(abs(a - b) < 1e-5 for a, b in zip(tex.inputs['ID Color 1'].default_value[:3], BLUE)),
          "the old color is the first of the palette")
    check(tex.inputs['Colors'].default_value == 1, "and the only one")
    close(tex.inputs['Tolerance'].default_value, 0.07, 1e-6, "the old tolerance is kept")
    check(tex.outputs['Fac'].is_linked and tex.inputs['ID'].is_linked, "links kept")
    _set(tex, Softness=0.01)
    for g, want in zip(_got(ob, mat, e), (0, 0, 1, 0)):
        close(g, want, 0.03, "it still selects blue")


def test_an_empty_id_slot_selects_nothing_and_says_so(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, e = _setup(addon, 'ID_SELECT')
    tree = mat.node_tree
    tex = _tex(addon, mat, e)
    check(tex.inputs['ID'].is_linked, "the ID input is never left open")
    check(tree.nodes.get(addon.sources.ID_FALLBACK) is not None, "a black stand-in")
    _palette(tex, RED)
    close(_value(ob, mat, e), 0.0, 0.03, "no ID map, no part")
    mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers) if x.uid == e.uid)
    said = [c[2].get("text", "") for c in _draw(ui._props, ob) if c[0] == "label"]
    check(any(t.startswith("Needs the ID map") for t in said), f"no warning: {said}")
    _id_map(addon, mat)
    check(tree.nodes.get(addon.sources.ID_FALLBACK) is None, "the stand-in goes with a map")
    said = [c[2].get("text", "") for c in _draw(ui._props, ob) if c[0] == "label"]
    check(not any(t.startswith("Needs the ID map") for t in said), "no warning with a map")


def test_the_palette_panel(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers) if x.uid == e.uid)
    _run_add(e)
    _run_add(e)
    log = _draw(ui._props, ob)
    swatches = [c for c in log if c[0] == "prop" and str(getattr(c[1][0], "name", "")).startswith("ID Color ")]
    check(len(swatches) == 3, f"a swatch for each color: {len(swatches)}")
    ops_drawn = [c[1][0] for c in log if c[0] == "operator"]
    check(ops_drawn.count("essence.id_color_remove") == 3, "a remove button on each")
    check("essence.id_color_add" in ops_drawn and "essence.show_map" in ops_drawn,
          f"add and show buttons: {ops_drawn}")
    texts = [c[2].get("text", "") for c in log if c[0] == "operator"]
    check("Show ID on Mesh" in texts, "the toggle to see the ID on the mesh")
    labels = [c[2].get("text", "") for c in log if c[0] == "label"]
    check(any("press E" in t for t in labels), f"the eyedropper hint: {labels}")
    check(not any(c[0] == "prop" and getattr(c[1][0], "name", "") in ("ID", "Colors") for c in log),
          "the count and the map input are not drawn as properties")
    check("essence.pick_id" not in ops_drawn, "the pick from a face button is gone")
    check(bpy.ops.essence.show_map(map_id='ID') == {'FINISHED'}, "show map")


def test_the_palette_survives_save_and_reload(addon):
    import os
    import shutil
    import tempfile
    ob, mat, e = _setup(addon, 'ID_SELECT')
    img = _id_map(addon, mat)
    img.pack()
    tex = _tex(addon, mat, e)
    _palette(tex, BLUE, RED)
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
        check(tex.inputs['Colors'].default_value == 2, "two colors")
        got = tuple(tex.inputs['ID Color 1'].default_value)[:3]
        check(all(abs(a - b) < 1e-5 for a, b in zip(got, BLUE)), f"the first color: {got}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_a_version_2_group_is_rebuilt_with_its_values(addon):
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _palette(tex, RED, BLUE)
    _set(tex, Tolerance=0.01, Softness=0.03)
    tex.node_tree["essence_version"] = 2
    bpy.ops.essence.rebuild()
    tex = _tex(addon, mat, e)
    check(tex.node_tree["essence_version"] == 3, "rebuilt")
    check(tex.inputs['Colors'].default_value == 2, "the palette is kept")
    close(tex.inputs['Softness'].default_value, 0.03, 1e-6, "the softness is kept")
    for g, want in zip(_got(ob, mat, e), (1, 0, 1, 0)):
        close(g, want, 0.01, "and red and blue are white now")


def test_an_exact_match_needs_no_tolerance_and_no_softness(addon):
    """The mask is an exact comparison of the ID map's RGB with the picked color: Tolerance and
    Softness at 0 still select the part (float noise must not matter), and a color that is a
    little off is not selected."""
    ob, mat, e = _setup(addon, 'ID_SELECT')
    _id_map(addon, mat)
    tex = _tex(addon, mat, e)
    _set(tex, Tolerance=0.0, Softness=0.0)
    for color, expected in ((RED, (1, 0, 0, 0)), (GREEN, (0, 1, 0, 0)), (BLUE, (0, 0, 1, 0))):
        _palette(tex, color)
        for g, want in zip(_got(ob, mat, e), expected):
            close(g, want, 0.01, f"exactly {color}, no tolerance, no softness")
    _palette(tex, (RED[0] - 0.02, RED[1], RED[2]))
    close(_got(ob, mat, e)[0], 0.0, 0.01, "a color 0.02 off is not the part")
