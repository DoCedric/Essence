"""Fill modes (Mesh, UV Island, Face, Full): which faces a click covers, which face a ray
picks, and that a fill click puts the user's selection, masking and paint settings back.
The click itself (Blender's Fill brush through face masking) needs a window:
`tests/gui_fill_modes.py`."""

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from harness import check, make_plane


def _strip():
    """Four quads in a row along x (faces 0 to 3) and a loose quad (face 4) at x 6 to 7, flat
    at z 0. UV islands: faces 0 and 1, faces 2 and 3 (cut between 1 and 2 although the
    geometry is connected), face 4. Face 3 uses the second material."""
    me = bpy.data.meshes.new("Strip")
    bm = bmesh.new()
    row = [(bm.verts.new((x, 0, 0)), bm.verts.new((x, 1, 0))) for x in range(5)]
    for i in range(4):
        bm.faces.new((row[i][0], row[i + 1][0], row[i + 1][1], row[i][1]))
    loose = [bm.verts.new(c) for c in ((6, 0, 0), (7, 0, 0), (7, 1, 0), (6, 1, 0))]
    bm.faces.new(loose)
    uv = bm.loops.layers.uv.new("UVMap")
    for i, f in enumerate(bm.faces):
        offset = (0.0, 0.0, 0.5, 0.5, 0.8)[i]
        for loop in f.loops:
            co = loop.vert.co
            loop[uv].uv = (offset + (co.x % 6) * 0.05, co.y * 0.1)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Strip", me)
    bpy.context.scene.collection.objects.link(ob)
    for name in ("StripA", "StripB"):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        me.materials.append(mat)
    me.polygons[3].material_index = 1
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def _faces(addon, ob, face, mode):
    return set(np.flatnonzero(addon.fill.faces_for_mode(ob.data, face, mode)).tolist())


def test_each_mode_covers_its_faces(addon):
    ob = _strip()
    check(_faces(addon, ob, 0, 'FACE') == {0}, "Face: only the clicked face")
    check(_faces(addon, ob, 0, 'MESH') == {0, 1, 2},
          f"Mesh: the connected piece of that material, got {_faces(addon, ob, 0, 'MESH')}")
    check(_faces(addon, ob, 0, 'UV_ISLAND') == {0, 1},
          f"UV Island: stops at the UV cut, got {_faces(addon, ob, 0, 'UV_ISLAND')}")
    check(_faces(addon, ob, 2, 'UV_ISLAND') == {2}, "the island's face of another material is left out")
    check(_faces(addon, ob, 0, 'FULL') == {0, 1, 2, 4}, "Full: every face of the material")
    check(_faces(addon, ob, 3, 'FULL') == {3} and _faces(addon, ob, 3, 'MESH') == {3},
          "a click on the other material fills only that material's faces")


def test_hidden_faces_are_not_filled(addon):
    ob = _strip()
    ob.data.polygons[1].hide = True
    check(_faces(addon, ob, 0, 'MESH') == {0, 2}, "a hidden face is skipped, the piece still connects")
    check(_faces(addon, ob, 0, 'FULL') == {0, 2, 4}, "and Full skips it too")


def _down(x, y=0.5):
    return Vector((x, y, 5.0)), Vector((0, 0, -1))


def test_a_ray_picks_the_face_of_the_mesh(addon):
    ob = _strip()
    ob.location = (0, 0, 0.3)
    bpy.context.view_layer.update()
    ctx = bpy.context
    check(addon.fill.pick_face(ctx, ob, *_down(2.5)) == 2, "the face under the ray")
    check(addon.fill.pick_face(ctx, ob, *_down(6.5)) == 4, "the loose face")
    check(addon.fill.pick_face(ctx, ob, *_down(5.0)) is None, "a miss")
    # Topology changed by a modifier: the face is found on the mesh itself.
    ob.modifiers.new("Subd", 'SUBSURF').levels = 2
    bpy.context.view_layer.update()
    check(addon.fill.pick_face(ctx, ob, *_down(2.5)) == 2, "through a Subdivision modifier")
    check(addon.fill.pick_face(ctx, ob, *_down(0.5)) == 0, "and another face")
    # Something else in front.
    cover = make_plane("Cover", size=4.0, location=(2.0, 0.5, 2.0))
    bpy.context.view_layer.objects.active = ob
    check(addon.fill.pick_face(ctx, ob, *_down(2.5)) is None, "an object in front blocks the fill")
    check(cover.name == "Cover", "the cover exists")


def test_paint_state_is_put_back(addon):
    ob = _strip()
    me = ob.data
    for p in me.polygons:
        p.select = p.index in (1, 4)
    me.use_paint_mask_vertex = True
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    paint = bpy.context.tool_settings.image_paint
    ups = paint.unified_paint_settings
    ups.use_unified_color = False
    ups.color = (0.2, 0.4, 0.6)
    ups.strength = 0.35
    state = addon.fill.PaintState(ob, paint)
    me.polygons.foreach_set("select", [True] * 5)
    me.use_paint_mask = True
    ups.use_unified_color, ups.color, ups.strength = True, (1, 1, 1), 1.0
    state.restore(ob, paint)
    check([p.select for p in me.polygons] == [False, True, False, False, True], "face selection back")
    check(me.use_paint_mask_vertex and not me.use_paint_mask, "masking switches back")
    check(not ups.use_unified_color and abs(ups.strength - 0.35) < 1e-6
          and abs(ups.color[1] - 0.4) < 1e-6, "unified color and strength back")


def test_a_failed_fill_still_puts_everything_back(addon):
    ob = _strip()
    me = ob.data
    for p in me.polygons:
        p.select = p.index == 3
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    faces = addon.fill.faces_for_mode(me, 0, 'MESH')
    try:
        addon.fill.fill(bpy.context, ob, faces, (10, 10), (1, 0, 0))
    except RuntimeError:
        pass                            # no window headless: the paint operator cannot run
    check([p.select for p in me.polygons] == [False, False, False, True, False],
          "the user's selection is back")
    check(not me.use_paint_mask, "face masking is off again")


def test_fill_needs_texture_paint_and_uvs(addon):
    ob = _strip()
    check(not bpy.ops.essence.fill.poll(), "not outside Texture Paint")
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    check(bpy.ops.essence.fill.poll(), "available in Texture Paint")
    bpy.ops.object.mode_set(mode='OBJECT')
    ob.data.uv_layers.remove(ob.data.uv_layers[0])
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    check(not bpy.ops.essence.fill.poll(), "not without a UV map")


def test_fill_controls_draw_while_painting(addon):
    from essence import ui
    from test_ui import _draw
    from test_paint_items import _add_paint, _setup
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=16)
    log = _draw(ui._mask, ob)
    check(not any(c[0] == "operator" and c[1][0] == "essence.fill" for c in log),
          "no Fill outside Texture Paint")
    bpy.ops.essence.paint_mask()
    log = _draw(ui._mask, ob)
    check(any(c[0] == "operator" and c[1][0] == "essence.fill" for c in log), "Fill on a mask")
    check(sum(1 for c in log if c[0] == "operator" and c[1][0] == "essence.fill") == 4,
          "a button per mode, each starts filling")
    check(not any("Fill tool fills everything" in c[2].get("text", "") for c in log),
          "no warning with an ordinary brush")
    bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS', asset_library_identifier="",
                                 relative_asset_identifier=addon.fill.FILL_BRUSH[2])
    log = _draw(ui._mask, ob)
    check(any("Fill tool fills everything" in c[2].get("text", "") for c in log),
          "a warning when Blender's own Fill brush is active (it ignores the modes)")
    bpy.ops.essence.exit_paint()
    bpy.data.objects.remove(ob)
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    _add_paint(mat, mask_uid)
    bpy.ops.essence.paint_mask()
    log = _draw(ui._adjustment, ob)
    check(any(c[0] == "operator" and c[1][0] == "essence.fill" for c in log), "Fill on a paint item")
    check(any(c[0] == "prop" and c[1][1] == "essence_fill_color" for c in log), "and the color")
