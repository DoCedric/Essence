"""Fill modes with Blender's real Fill brush. Needs a window, so it is not part of run_all:

    blender --factory-startup --enable-event-simulate --python tests/gui_fill_modes.py

(without --enable-event-simulate the clicks through the modal operator are skipped)

Opens Blender, builds two cubes in one mesh with an Essence mask, fills through
`fill.fill` at real screen positions in every mode, prints PASS/FAIL lines, writes them to
the file in ESSENCE_GUI_OUT (default: the temp dir) and quits with a non-zero code on
failure. Each face has its own UV cell, so a face's texels are known.
"""
import os
import sys
import tempfile
import traceback

import bmesh
import bpy
import numpy as np
from bpy_extras import view3d_utils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402

OUT = os.environ.get("ESSENCE_GUI_OUT", os.path.join(tempfile.gettempdir(), "essence_gui_fill.txt"))
lines = []
failed = []


def check(cond, msg):
    lines.append(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failed.append(msg)


def build():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    me = bpy.data.meshes.new("Parts")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    b = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.translate(bm, verts=b, vec=(2.0, 0, 0))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Parts", me)
    bpy.context.scene.collection.objects.link(ob)
    uv = me.uv_layers.new(name="UVMap")
    for p in me.polygons:
        cx, cy = p.index % 4, p.index // 4
        for li, (u, v) in zip(p.loop_indices, ((0.05, 0.05), (0.95, 0.05), (0.95, 0.95), (0.05, 0.95))):
            uv.data[li].uv = ((cx + u) / 4, (cy + v) / 3)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def mask_image(ob):
    mat = ob.active_material
    e = mat.essence.layers[mat.essence.active_index]
    return mat.node_tree.nodes[e.mask_node_name].image


def face_values(img):
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    out = []
    for i in range(12):
        cx, cy = i % 4, i // 4
        x0, x1 = int((cx + 0.2) / 4 * w), int((cx + 0.8) / 4 * w)
        y0, y1 = int((cy + 0.2) / 3 * h), int((cy + 0.8) / 3 * h)
        out.append(float(px[y0:y1, x0:x1, 0].mean()))
    return np.array(out)


def reset(img):
    img.pixels.foreach_set(np.tile(np.array([0, 0, 0, 1], dtype=np.float32), img.size[0] * img.size[1]))
    img.update()


def top_of(ob, part):
    """The top face of part 0 or 1 and its screen position."""
    me = ob.data
    face = next(p.index for p in me.polygons if p.normal.z > 0.9 and (p.center.x > 1) == bool(part))
    return face, me.polygons[face].center


def run():
    try:
        addon = harness.load_addon()
        addon.bundled.AUTOLOAD = False
        addon.register()
        ob = build()
        bpy.ops.essence.add_layer()
        bpy.ops.essence.add_mask(source='PAINT', fill='BLACK', size=256)
        win = bpy.context.window_manager.windows[0]
        area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        rv3d = area.spaces.active.region_3d
        rv3d.view_rotation = (1, 0, 0, 0)          # from the top: the bottom faces are hidden
        rv3d.view_perspective = 'ORTHO'
        rv3d.view_location = (1.0, 0, 0)
        rv3d.view_distance = 6.0
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.essence.paint_mask()
        bpy.app.timers.register(lambda: cases(addon, ob, win, area, region), first_interval=1.0)
    except Exception:
        lines.append("FAIL setup\n" + traceback.format_exc())
        failed.append("setup")
        finish()


def cases(addon, ob, win, area, region):
    try:
        rv3d = area.spaces.active.region_3d
        img = mask_image(ob)
        me = ob.data
        paint = bpy.context.tool_settings.image_paint
        # The user's own state, which every click must leave as it was.
        for p in me.polygons:
            p.select = p.index in (7, 9)
        ups = paint.unified_paint_settings
        ups.use_unified_color = False
        ups.color = (0.1, 0.2, 0.3)
        ups.strength = 0.4
        brush_before = paint.brush.name if paint.brush else None
        check(brush_before is not None and brush_before != "Fill", f"a paint brush to begin with ({brush_before})")

        def click(part, mode, color=(1.0, 1.0, 1.0)):
            reset(img)
            face, center = top_of(ob, part)
            co = view3d_utils.location_3d_to_region_2d(region, rv3d, ob.matrix_world @ center)
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
            with bpy.context.temp_override(window=win, area=area, region=region):
                picked = addon.fill.pick_face(bpy.context, ob, origin, direction)
                check(picked == face, f"{mode}: the ray picks the top face ({picked} == {face})")
                faces = addon.fill.faces_for_mode(me, picked, mode)
                res = addon.fill.fill(bpy.context, ob, faces, (co.x, co.y), color)
            check(res == {'FINISHED'}, f"{mode}: the paint operator ran ({res})")
            img.update()
            values = face_values(img)
            check([p.select for p in me.polygons] == [p.index in (7, 9) for p in me.polygons],
                  f"{mode}: the face selection is back")
            check(not me.use_paint_mask, f"{mode}: face masking is off again")
            check((paint.brush.name if paint.brush else None) == brush_before,
                  f"{mode}: the brush is back ({paint.brush.name if paint.brush else None})")
            check(not ups.use_unified_color and abs(ups.strength - 0.4) < 1e-6
                  and abs(ups.color[2] - 0.3) < 1e-6, f"{mode}: unified color and strength are back")
            return faces, values

        faces, v = click(0, 'MESH')
        check(np.all(v[:6] > 0.99) and np.all(v[6:] < 0.01),
              f"MESH: all of part A (hidden bottom too), none of B: {np.round(v, 2).tolist()}")
        faces, v = click(1, 'FACE')
        top_b = top_of(ob, 1)[0]
        check(v[top_b] > 0.99 and np.all(np.delete(v, top_b) < 0.01),
              f"FACE: only B's top: {np.round(v, 2).tolist()}")
        faces, v = click(1, 'UV_ISLAND')
        check(v[top_b] > 0.99 and np.all(np.delete(v, top_b) < 0.01),
              f"UV_ISLAND: one cell per face, so only B's top: {np.round(v, 2).tolist()}")
        faces, v = click(0, 'FULL')
        check(np.all(v > 0.99), f"FULL: every face: {np.round(v, 2).tolist()}")
        faces, v = click(0, 'MESH', color=(0.5, 0.5, 0.5))
        lines.append(f"INFO grey 0.5 fill reads {v[0]:.4f} in the mask image "
                     f"({img.colorspace_settings.name}, float {img.is_float})")
        check(abs(v[0] - 0.5) < 0.01, f"a grey fill stores 0.5 in the Non-Color mask ({v[0]:.4f})")
        if bpy.app.use_event_simulate:
            bpy.app.timers.register(lambda: modal_case(ob, win, area, region), first_interval=0.5)
            return
        lines.append("INFO the clicks through the modal operator need --enable-event-simulate")
    except Exception:
        lines.append("FAIL cases\n" + traceback.format_exc())
        failed.append("cases")
    finish()


def modal_case(ob, win, area, region):
    """The way a user does it: press Fill, click the model with real (simulated) events."""
    try:
        img = mask_image(ob)
        reset(img)
        bpy.context.scene.essence_fill_color = (1.0, 1.0, 1.0)
        rv3d = area.spaces.active.region_3d
        face, center = top_of(ob, 1)
        co = view3d_utils.location_3d_to_region_2d(region, rv3d, ob.matrix_world @ center)
        x, y = int(region.x + co.x), int(region.y + co.y)
        sidebar = next((r for r in area.regions if r.type == 'UI'), region)
        fill = sys.modules["essence.fill"]
        with bpy.context.temp_override(window=win, area=area, region=sidebar):
            res = bpy.ops.essence.fill('INVOKE_DEFAULT', mode='MESH')
            check(res == {'RUNNING_MODAL'}, f"modal: the Mesh button starts filling ({res})")
            res = bpy.ops.essence.fill('INVOKE_DEFAULT', mode='FACE')
            check(res == {'FINISHED'} and fill.running() is not None
                  and bpy.context.scene.essence_fill_mode == 'FACE',
                  "modal: the Face button while filling switches the mode, no second operator")
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        win.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        bpy.app.timers.register(lambda: modal_check(ob, win, face, x, y), first_interval=0.5)
        return
    except Exception:
        lines.append("FAIL modal\n" + traceback.format_exc())
        failed.append("modal")
    finish()


def modal_check(ob, win, face, x, y):
    try:
        img = mask_image(ob)
        img.update()
        v = face_values(img)
        check(v[face] > 0.99 and np.all(np.delete(v, face) < 0.01),
              f"modal FACE click fills only that face: {np.round(v, 2).tolist()}")
        fill = sys.modules["essence.fill"]
        area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
        sidebar = next((r for r in area.regions if r.type == 'UI'), None)
        with bpy.context.temp_override(window=win, area=area, region=sidebar):
            bpy.ops.essence.fill('INVOKE_DEFAULT', mode='FACE')
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x + 1, y=y)
        bpy.app.timers.register(lambda: stopped_check(win, fill), first_interval=0.3)
        return
    except Exception:
        lines.append("FAIL modal check\n" + traceback.format_exc())
        failed.append("modal check")
    finish()


def stopped_check(win, fill):
    try:
        check(fill.running() is None, "modal: pressing the lit mode again stops filling")
    except Exception:
        lines.append("FAIL stop check\n" + traceback.format_exc())
        failed.append("stop check")
    finish()


def finish():
    text = "\n".join(lines) + f"\n{'FAILED' if failed else 'ALL PASSED'}\n"
    print(text)
    with open(OUT, "w") as f:
        f.write(text)
    sys.stdout.flush()
    os._exit(1 if failed else 0)


bpy.app.timers.register(run, first_interval=1.0)
