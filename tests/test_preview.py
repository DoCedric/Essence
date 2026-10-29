"""Preview (solo): an Emission node wired into the Material Output."""

import os
import shutil
import tempfile
from types import SimpleNamespace

import bpy

from harness import check, close, make_plane, setup_cycles


def _surface(ob, mat, uv=(0.5, 0.5), size=16):
    """What the material's own surface gives at `uv`: bake the emission of the Material Output
    as it is, with nothing overridden."""
    tree = mat.node_tree
    setup_cycles()
    img = bpy.data.images.new("_surface", size, size, float_buffer=True)
    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tree.nodes.active = tex
    for o in bpy.context.view_layer.objects:
        o.select_set(o is ob)
    bpy.context.view_layer.objects.active = ob
    bpy.context.scene.render.bake.margin = 0
    bpy.ops.object.bake(type='EMIT')
    px = list(img.pixels)
    cx, cy = round(uv[0] * size), round(uv[1] * size)
    block = [px[((y * size) + x) * 4:((y * size) + x) * 4 + 3]
             for y in (cy - 1, cy) for x in (cx - 1, cx)]
    tree.nodes.remove(tex)
    bpy.data.images.remove(img)
    return tuple(sum(b[i] for b in block) / 4 for i in range(3))


def _evaluate():
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()


def _setup():
    """A plane with a bottom layer (roughness 0.2) and a top layer: color (0.9, 0.1, 0.1),
    roughness 0.8, a mask of 0 (hides the top layer completely)."""
    ob = make_plane("Plane", size=2.0)
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    bottom = mat.essence.layers[mat.essence.active_index]
    bottom_uid = bottom.uid     # entries are index based: take what is needed before the list moves
    mat.node_tree.nodes[bottom.node_name].inputs['Roughness B'].default_value = 0.2
    bpy.ops.essence.add_layer()
    top = mat.essence.layers[mat.essence.active_index]
    top_uid = top.uid
    node = mat.node_tree.nodes[top.node_name]
    node.inputs['Color B'].default_value = (0.9, 0.1, 0.1, 1.0)
    node.inputs['Roughness B'].default_value = 0.8
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=16)
    mask = mat.essence.layers[mat.essence.active_index]
    mask_uid = mask.uid
    mask.source.kind = 'VALUE'
    mat.node_tree.nodes[mask.node_name].inputs['Value'].default_value = 0.0
    return ob, mat, bottom_uid, top_uid, mask_uid


def _surface_link(mat):
    tree = mat.node_tree
    out = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    l = out.inputs['Surface'].links
    return (l[0].from_node.name, l[0].from_socket.identifier) if l else None


def _run(kind, ident):
    return bpy.ops.essence.preview(kind=kind, ident=ident)


def test_a_mask_shows_as_grey_and_ending_restores_the_surface(addon):
    ob, mat, bottom, top, mask = _setup()
    before = _surface_link(mat)
    check(before is not None and before[0].startswith("Principled"), f"the shader feeds it: {before}")
    check(_run('MASK', mask) == {'FINISHED'}, "preview failed")
    check(addon.preview.active(mat) == ('MASK', mask), "the mask is previewed")
    check(_surface_link(mat)[0] == addon.preview.NODE, "the Emission node feeds the output")
    mat.node_tree.nodes[next(e for e in mat.essence.layers if e.uid == mask).node_name] \
        .inputs['Value'].default_value = 0.4
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.4) < 0.02 and abs(g - 0.4) < 0.02 and abs(b - 0.4) < 0.02,
          f"flat grey 0.4: {(r, g, b)}")
    check(_run('MASK', mask) == {'FINISHED'}, "pressing again ends it")
    check(addon.preview.find(mat) is None, "the preview node is gone")
    check(_surface_link(mat) == before, f"the original link is back exactly: {_surface_link(mat)}")
    check(not [n for n in mat.node_tree.nodes if n.get(addon.preview.HELPER_KEY)], "no helpers left")


def test_a_layer_shows_its_own_color_whatever_its_mask(addon):
    ob, mat, bottom, top, mask = _setup()
    _run('LAYER', top)
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.9) < 0.02 and abs(g - 0.1) < 0.02 and abs(b - 0.1) < 0.02,
          f"the top layer's own color, although its mask hides it: {(r, g, b)}")
    # a plain color is followed live
    node = mat.node_tree.nodes[next(e for e in mat.essence.layers if e.uid == top).node_name]
    node.inputs['Color B'].default_value = (0.1, 0.8, 0.2, 1.0)
    _evaluate()
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.1) < 0.02 and abs(g - 0.8) < 0.02 and abs(b - 0.2) < 0.02,
          f"changing the color while previewing is followed: {(r, g, b)}")
    addon_preview = addon.preview
    addon_preview.stop(mat)
    check(not (mat.node_tree.animation_data and any(
        fc.data_path.startswith('nodes["Essence Preview') for fc in mat.node_tree.animation_data.drivers)),
        "the drivers of the helper are removed")


def test_a_layer_preview_includes_its_adjustments(addon):
    ob, mat, bottom, top, mask = _setup()
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == top)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')
    _run('LAYER', top)
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.1) < 0.03 and abs(g - 0.9) < 0.03, f"the inverted color: {(r, g, b)}")


def test_a_channel_shows_the_result_of_the_whole_stack(addon):
    ob, mat, bottom, top, mask = _setup()
    # the top layer's mask is 0, so the stack's roughness is the bottom layer's 0.2
    _run('CHANNEL', 'ROUGHNESS')
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.2) < 0.02, f"roughness of the stack: {(r, g, b)}")
    mat.node_tree.nodes[next(e for e in mat.essence.layers if e.uid == mask).node_name] \
        .inputs['Value'].default_value = 1.0
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.8) < 0.02, f"with the mask open the top layer's 0.8: {(r, g, b)}")
    _run('CHANNEL', 'COLOR')
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.9) < 0.02 and abs(g - 0.1) < 0.02, f"color of the stack: {(r, g, b)}")
    bump = mat.node_tree.nodes[next(e for e in mat.essence.layers if e.uid == top).node_name]
    bump.inputs['Bump B'].default_value = 0.5
    _run('CHANNEL', 'BUMP')
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.75) < 0.03, f"a height of 0.5 shows as 0.75 (grey is flat): {(r, g, b)}")


def test_one_preview_at_a_time_and_the_original_is_remembered_once(addon):
    ob, mat, bottom, top, mask = _setup()
    before = _surface_link(mat)
    _run('MASK', mask)
    _run('LAYER', top)
    check(addon.preview.active(mat) == ('LAYER', top), "the second replaces the first")
    check(len([n for n in mat.node_tree.nodes if addon.preview.KEY in n]) == 1,
          "one emission node (the helper color node is not one)")
    _run('CHANNEL', 'ROUGHNESS')
    check(addon.preview.active(mat) == ('CHANNEL', 'ROUGHNESS'), "and the third")
    bpy.ops.essence.preview_stop()
    check(_surface_link(mat) == before, f"stopping restores the real shader, not a preview: {_surface_link(mat)}")
    check(not bpy.ops.essence.preview_stop.poll(), "nothing to stop")


def test_a_rebuild_keeps_it_and_removing_the_entry_ends_it(addon):
    ob, mat, bottom, top, mask = _setup()
    before = _surface_link(mat)
    _run('LAYER', top)
    bpy.ops.essence.rebuild()
    check(addon.preview.active(mat) == ('LAYER', top), "a rebuild keeps the preview")
    bpy.ops.essence.add_layer()
    check(addon.preview.active(mat) == ('LAYER', top), "adding a layer keeps it")
    _run('CHANNEL', 'ROUGHNESS')
    bpy.ops.essence.add_layer()
    check(addon.preview.active(mat) == ('CHANNEL', 'ROUGHNESS'), "a channel preview keeps going too")
    _run('MASK', mask)
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == mask)
    bpy.ops.essence.remove_layer()
    check(addon.preview.find(mat) is None, "removing the previewed mask ends the preview")
    check(_surface_link(mat) == before, f"and the shader is back: {_surface_link(mat)}")
    _run('LAYER', top)
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == top)
    bpy.ops.essence.remove_layer()
    check(addon.preview.find(mat) is None, "removing the previewed layer ends it")


def test_nothing_to_preview_leaves_the_material_alone(addon):
    ob, mat, bottom, top, mask = _setup()
    before = _surface_link(mat)
    _run('CHANNEL', 'ALPHA')                 # switched off by default: nothing feeds it
    check(addon.preview.find(mat) is None and _surface_link(mat) == before,
          "a channel that is off has nothing to show")
    _run('LAYER', "nope")
    check(addon.preview.find(mat) is None and _surface_link(mat) == before, "no such layer")


def test_a_bake_during_a_preview_keeps_the_preview(addon):
    from test_bake_quality import _bake
    ob, mat, bottom, top, mask = _setup()
    _run('LAYER', top)
    check(_bake('AO') == {'FINISHED'}, "bake failed")
    check(addon.preview.active(mat) == ('LAYER', top) and _surface_link(mat)[0] == addon.preview.NODE,
          "the bake put the preview link back as it found it")


def test_a_preview_survives_save_and_reload(addon):
    ob, mat, bottom, top, mask = _setup()
    before = _surface_link(mat)
    _run('MASK', mask)
    name = ob.name
    folder = tempfile.mkdtemp(prefix="essence_prev_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        check(addon.preview.active(mat) == ('MASK', mask), "the preview is still on")
        addon.preview.stop(mat)
        check(_surface_link(mat) == before, "and ending it restores the shader")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_preview_buttons_and_notice_draw(addon):
    from essence import ui
    from test_ui import Rec, _draw
    ob, mat, bottom, top, mask = _setup()
    log = _draw(ui._stack, ob)
    kinds = [(c[2].get("icon"), c[1][0]) for c in log if c[0] == "operator"
             and c[1][0] == "essence.preview" and c[2].get("icon")]
    check(len(kinds) == 1, f"one preview toggle, on the stack bar: {len(kinds)}")
    check(kinds[0][0] == 'SHADING_TEXTURE', "the icon")
    channels = [c[2].get("text") for c in log if c[0] == "operator"
                and c[1][0] == "essence.preview" and not c[2].get("icon")]
    check(channels == ["Col", "Rgh", "Met", "Bmp"],
          f"the whole-stack previews above the stack, short: {channels}")
    check(not any(c[0] == "operator" and c[1][0] == "essence.preview"
                  for c in _draw(ui._material, ob)), "no longer in Essence Channels")
    _run('MASK', mask)
    # the bar's toggle follows the selection: pressed on the previewed mask, not on a layer
    pressed = lambda: [c[2].get("depress") for c in _draw(ui._stack, ob) if c[0] == "operator"
                       and c[1][0] == "essence.preview" and c[2].get("icon")][0]
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == mask)
    check(pressed(), "the selected mask is the one previewed: pressed")
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == top)
    check(not pressed(), "another layer selected: not pressed")
    # No notice above the stack while previewing (the user's request, 2026-10-29): it pushed
    # the buttons down; the pressed button is enough, and pressing it again stops.
    log = _draw(ui._stack, ob)
    check(not any(c[0] == "label" and c[2].get("text", "").startswith("Previewing")
                  for c in log), "no Previewing notice")
    check(not any(c[0] == "operator" and c[1][0] == "essence.preview_stop" for c in log),
          "no stop button")
    _run('CHANNEL', 'ROUGHNESS')
    rgh = [c[2].get("depress") for c in _draw(ui._stack, ob) if c[0] == "operator"
           and c[1][0] == "essence.preview" and c[2].get("text") == "Rgh"]
    check(rgh == [True], f"the Rgh button is pressed: {rgh}")
