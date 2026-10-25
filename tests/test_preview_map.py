"""Previewing a Mesh Maps slot on the mesh, and the color management it sets while it runs."""

import os
import shutil
import tempfile
from types import SimpleNamespace

import bpy

from harness import check
from test_preview import _evaluate, _run, _setup, _surface, _surface_link

REDDISH = (0.9, 0.2, 0.2)


def _map_image(addon, mat, map_id, color, name=None):
    img = bpy.data.images.new(name or f"m_{map_id}", 16, 16, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    img.pixels.foreach_set([*color, 1.0] * 256)
    img.update()
    mat.node_tree.nodes[addon.nodes.map_slot_name(map_id)].image = img
    return img


def _vs():
    return bpy.context.scene.view_settings


def _make_agx():
    """The user's usual look: AgX with exposure and gamma off the defaults (and a look if one
    exists). Returns the values to compare with after."""
    vs = _vs()
    vs.view_transform = 'AgX'
    looks = [i.identifier for i in vs.bl_rna.properties['look'].enum_items
             if i.identifier not in ('None', 'NONE')]
    if looks:
        vs.look = looks[0]
    vs.exposure = 0.7
    vs.gamma = 1.2
    return (vs.view_transform, vs.look, round(vs.exposure, 4), round(vs.gamma, 4))


def _state():
    vs = _vs()
    return (vs.view_transform, vs.look, round(vs.exposure, 4), round(vs.gamma, 4))


def test_a_map_is_shown_on_the_mesh_through_its_uv_map(addon):
    ob, mat, bottom, top, mask = _setup()
    _map_image(addon, mat, 'ID', REDDISH)
    bpy.ops.essence.rebuild()
    _run('MAP', 'ID')
    check(addon.preview.active(mat) == ('MAP', 'ID'), "the ID map is previewed")
    r, g, b = _surface(ob, mat)
    check(abs(r - 0.9) < 0.02 and abs(g - 0.2) < 0.02 and abs(b - 0.2) < 0.02,
          f"the map's color flat on the mesh: {(r, g, b)}")
    check(_run('MAP', 'ID') == {'FINISHED'} and addon.preview.find(mat) is None, "again ends it")
    before = _surface_link(mat)
    _run('MAP', 'CURVATURE')                          # no image in that slot
    check(addon.preview.find(mat) is None and _surface_link(mat) == before,
          "an empty slot has nothing to show")


def test_color_management_is_standard_only_while_a_map_preview_runs(addon):
    ob, mat, bottom, top, mask = _setup()
    _map_image(addon, mat, 'ID', REDDISH)
    _map_image(addon, mat, 'AO', (0.5, 0.5, 0.5))
    bpy.ops.essence.rebuild()
    was = _make_agx()
    _run('MAP', 'ID')
    vs = _vs()
    check((vs.view_transform, vs.look, vs.exposure, vs.gamma) == ('Standard', 'None', 0.0, 1.0),
          f"Standard while it runs: {_state()}")
    check(addon.preview.view_override(mat), "the preview knows it changed it")
    # another map keeps what the first replaced
    _run('MAP', 'AO')
    check(_state()[0] == 'Standard' and addon.preview.active(mat) == ('MAP', 'AO'), "still Standard")
    bpy.ops.essence.preview_stop()
    check(_state() == was, f"put back exactly: {_state()} against {was}")
    check(not addon.preview.view_override(mat), "nothing left to put back")


def test_leaving_the_map_preview_for_another_kind_puts_it_back(addon):
    ob, mat, bottom, top, mask = _setup()
    _map_image(addon, mat, 'ID', REDDISH)
    bpy.ops.essence.rebuild()
    was = _make_agx()
    _run('MAP', 'ID')
    check(_state()[0] == 'Standard', "Standard")
    _run('LAYER', top)
    check(_state() == was, f"a layer preview shows the real color management: {_state()}")
    _run('MAP', 'ID')
    check(_state()[0] == 'Standard', "and a map sets it again")
    _run('CHANNEL', 'ROUGHNESS')
    check(_state() == was, "same for a channel")
    bpy.ops.essence.preview_stop()


def test_other_previews_leave_the_color_management_alone(addon):
    ob, mat, bottom, top, mask = _setup()
    was = _make_agx()
    for kind, ident in (('MASK', mask), ('LAYER', top), ('CHANNEL', 'ROUGHNESS')):
        _run(kind, ident)
        check(_state() == was and not addon.preview.view_override(mat),
              f"{kind} must not change it: {_state()}")
        bpy.ops.essence.preview_stop()
    check(_state() == was, "and ending leaves it")


def test_nothing_to_change_nothing_to_put_back(addon):
    ob, mat, bottom, top, mask = _setup()
    _map_image(addon, mat, 'ID', REDDISH)
    bpy.ops.essence.rebuild()
    vs = _vs()
    vs.view_transform, vs.exposure, vs.gamma = 'Standard', 0.0, 1.0
    vs.look = 'None'
    _run('MAP', 'ID')
    check(not addon.preview.view_override(mat), "already Standard: nothing was changed")
    vs.exposure = 0.3                                   # the user's own change while it runs
    bpy.ops.essence.preview_stop()
    check(abs(vs.exposure - 0.3) < 1e-6, "which ending does not undo")


def test_a_rebuild_and_a_reload_keep_what_to_put_back(addon):
    ob, mat, bottom, top, mask = _setup()
    _map_image(addon, mat, 'ID', REDDISH).pack()
    bpy.ops.essence.rebuild()
    was = _make_agx()
    _run('MAP', 'ID')
    bpy.ops.essence.rebuild()
    bpy.ops.essence.add_layer()
    check(_state()[0] == 'Standard' and addon.preview.view_override(mat),
          "a rebuild keeps both the preview and the override")
    name = ob.name
    folder = tempfile.mkdtemp(prefix="essence_view_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        check(addon.preview.active(mat) == ('MAP', 'ID') and addon.preview.view_override(mat),
              "the preview and what to put back are in the file")
        check(_state()[0] == 'Standard', "as saved")
        addon.preview.stop(mat)
        check(_state() == was, f"ending after a reload restores the original: {_state()} against {was}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_map_preview_buttons_and_notices_draw(addon):
    from essence import ui
    from test_ui import Rec, _draw
    ob, mat, bottom, top, mask = _setup()
    img = _map_image(addon, mat, 'ID', REDDISH)
    bpy.ops.essence.rebuild()
    log = _draw(ui._maps, ob)
    shows = [c for c in log if c[0] == "operator" and c[1][0] == "essence.preview"]
    check(len(shows) == 1, f"a Show on Mesh button for the slot that has an image: {len(shows)}")
    _make_agx()
    _run('MAP', 'ID')
    ctx = SimpleNamespace(active_object=ob, scene=bpy.context.scene)
    panel = SimpleNamespace(layout=Rec())
    ui._stack(panel, ctx)
    labels = [c[2].get("text", "") for c in panel.layout.log if c[0] == "label"]
    check(any("the ID map" in t for t in labels), f"the notice names the map: {labels}")
    check(any("Color management is Standard" in t for t in labels), "and the color management")
    bpy.ops.essence.preview_stop()
    # the ID Select panel
    from test_generators import _setup as _gen_setup
    ob2, mat2, e2 = _gen_setup(addon, 'ID_SELECT')
    _map_image(addon, mat2, 'ID', REDDISH)
    bpy.ops.essence.rebuild()
    mat2.essence.active_index = next(i for i, x in enumerate(mat2.essence.layers) if x.uid == e2.uid)
    log = _draw(ui._props, ob2)
    texts = [c[2].get("text", "") for c in log if c[0] == "operator"]
    check("Show ID on Mesh" in texts, f"the toggle: {texts}")
    _run('MAP', 'ID')
    labels = [c[2].get("text", "") for c in _draw(ui._props, ob2) if c[0] == "label"]
    check(any("eyedropper" in t for t in labels), f"the hint to use the eyedropper: {labels}")
    bpy.ops.essence.preview_stop()


def test_an_eyedropped_color_matches_the_id_map_in_standard(addon):
    """What the eyedropper does (the displayed 8-bit color converted back to linear) with the
    Standard view transform, for the colors an ID bake makes: within the default tolerance."""
    from essence import bake

    def to_display(v):
        return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055

    def to_linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    import colorsys
    colors = [bake.name_color(f"Material {i}")[:3] for i in range(40)]
    colors += [colorsys.hsv_to_rgb((i * 0.6180339887) % 1.0, 0.8, 0.9) for i in range(40)]
    worst = 0.0
    for c in colors:
        back = [to_linear(round(to_display(v) * 255) / 255) for v in c]
        worst = max(worst, sum((a - b) ** 2 for a, b in zip(c, back)) ** 0.5)
    check(worst < 0.02, f"an 8-bit round trip stays well inside the default tolerance (0.05): {worst}")
