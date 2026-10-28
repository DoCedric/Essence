"""Fill modes for Texture Paint: click a part of the model and Blender's own Fill tool fills
the whole connected piece (Mesh), the UV island, the single face, or every face of the
material (Full) with the brush color (its first color, the one in the header).

Essence never fills pixels itself. It selects the faces of the mode, turns on Blender's face
selection masking and runs one click of the native Fill brush at the mouse; the Fill tool in
the 3D view covers every selected face, including faces hidden from the view (checked in a
windowed session, `tests/gui_fill_modes.py`). The user's face selection, masking, brush and
unified color are put back after every click. A fill only covers faces of the material that
was clicked: in Material paint mode every face paints into its own material's active image.
"""

import bpy
import numpy as np
from bpy.props import EnumProperty
from mathutils.bvhtree import BVHTree

from . import bake

FILL_MODES = [
    ('MESH', "Mesh", "The connected piece of geometry under the cursor (like L in Edit Mode)"),
    ('UV_ISLAND', "UV Island", "The UV island under the cursor"),
    ('FACE', "Face", "The face under the cursor"),
    ('FULL', "Full", "Every face of the material under the cursor"),
]

FILL_BRUSH = ('ESSENTIALS', "", "brushes/essentials_brushes-mesh_texture.blend/Brush/Fill")


def faces_for_mode(mesh, face, mode):
    """Which polygons of `mesh` a fill from polygon `face` covers (a bool array): the faces of
    the mode that use the same material and are not hidden."""
    n = len(mesh.polygons)
    material = np.empty(n, dtype=np.int64)
    mesh.polygons.foreach_get("material_index", material)
    hidden = np.empty(n, dtype=bool)
    mesh.polygons.foreach_get("hide", hidden)
    if mode == 'FACE':
        sel = np.zeros(n, dtype=bool)
        sel[face] = True
    elif mode == 'FULL':
        sel = np.ones(n, dtype=bool)
    else:
        ids = np.array(bake.uv_island_indices(mesh) if mode == 'UV_ISLAND'
                       else bake.mesh_island_indices(mesh))
        sel = ids == ids[face]
    return sel & (material == material[face]) & ~hidden


def pick_face(context, ob, origin, direction):
    """The polygon of `ob`'s own mesh that a ray (world space) hits first, or None when it
    misses or something else is in front."""
    dg = context.evaluated_depsgraph_get()
    hit, _loc, _normal, index, obj, _matrix = context.scene.ray_cast(dg, origin, direction)
    if not hit or obj is None or obj.original is not ob:
        return None
    me = ob.data
    if len(ob.evaluated_get(dg).data.polygons) == len(me.polygons):
        return index
    # Modifiers changed the topology: find the face on the mesh itself, which is what the
    # face selection masking works on.
    inv = ob.matrix_world.inverted()
    tree = BVHTree.FromPolygons([v.co for v in me.vertices], [p.vertices for p in me.polygons])
    local_dir = inv.to_3x3() @ direction
    _co, _no, index, _dist = tree.ray_cast(inv @ origin, local_dir.normalized())
    return index


class PaintState:
    """What a fill click changes, so it can be put back exactly: the face selection, the
    masking switches, the active brush and the unified color and strength."""

    def __init__(self, ob, paint):
        me = ob.data
        self.select = np.empty(len(me.polygons), dtype=bool)
        me.polygons.foreach_get("select", self.select)
        self.mask = me.use_paint_mask
        self.mask_vertex = me.use_paint_mask_vertex
        ref = paint.brush_asset_reference
        self.brush = (None if ref is None else
                      (ref.asset_library_type, ref.asset_library_identifier,
                       ref.relative_asset_identifier))
        ups = paint.unified_paint_settings
        self.unified = (ups.use_unified_color, tuple(ups.color),
                        ups.use_unified_strength, ups.strength)

    def restore(self, ob, paint):
        me = ob.data
        me.polygons.foreach_set("select", self.select)
        me.use_paint_mask = self.mask
        me.use_paint_mask_vertex = self.mask_vertex
        me.update()
        ups = paint.unified_paint_settings
        ups.use_unified_color, ups.color, ups.use_unified_strength, ups.strength = self.unified
        if self.brush is not None and self.brush != FILL_BRUSH:
            activate_brush(*self.brush)


def activate_brush(library_type, library_id, relative):
    bpy.ops.brush.asset_activate(asset_library_type=library_type,
                                 asset_library_identifier=library_id,
                                 relative_asset_identifier=relative)


def paint_color(paint):
    """What holds the color the brush paints with (its first color): the unified color when
    it is on, else the active brush. Fill paints that color, so the two never disagree."""
    ups = paint.unified_paint_settings
    return ups if ups.use_unified_color or paint.brush is None else paint.brush


def fill(context, ob, faces, coord, color):
    """Fill `faces` (a bool array over the polygons) with `color` by one click of the native
    Fill brush at `coord` (pixels in the 3D view region of `context`), and put back what the
    click changed. Returns the paint operator's result."""
    paint = context.tool_settings.image_paint
    state = PaintState(ob, paint)
    try:
        me = ob.data
        me.polygons.foreach_set("select", faces)
        me.use_paint_mask_vertex = False
        me.use_paint_mask = True
        me.update()
        context.view_layer.update()
        activate_brush(*FILL_BRUSH)
        # The unified color and strength, so the Fill brush asset itself is never edited.
        ups = paint.unified_paint_settings
        ups.use_unified_color = True
        ups.color = color
        ups.use_unified_strength = True
        ups.strength = 1.0
        x, y = coord
        return bpy.ops.paint.image_paint(mode='NORMAL', stroke=[{
            "name": "", "location": (0, 0, 0), "mouse": (x, y), "mouse_event": (x, y),
            "is_start": True, "pressure": 1.0, "size": 1.0, "time": 0.0,
            "x_tilt": 0.0, "y_tilt": 0.0}])
    finally:
        state.restore(ob, paint)


def _view(context, event):
    """(area, region) of the 3D view under the mouse, or (None, None)."""
    for area in context.window.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for region in area.regions:
            if (region.type == 'WINDOW' and region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                return area, region
    return None, None


# The Fill operator that is running, so a second mode button switches its mode instead of
# starting another one, and the panel can show which mode is on.
_running = None


def running():
    """The running Fill operator, or None (also when Blender dropped it, for example on a file
    load, without calling its modal)."""
    global _running
    try:
        if _running is not None and _running.bl_idname:
            return _running
    except ReferenceError:
        pass
    _running = None
    return None


class ESSENCE_OT_fill(bpy.types.Operator):
    bl_idname = "essence.fill"
    bl_label = "Fill"
    bl_description = ("Click parts of the model to fill them with the brush color: the connected "
                      "mesh, the UV island, the face or every face (Face also by dragging). Press "
                      "the lit mode again, right-click or Esc to stop")
    # No UNDO: every click is a stroke of Blender's paint operator, which is its own undo step.
    bl_options = {'REGISTER'}

    mode: EnumProperty(name="Mode", items=FILL_MODES, default='MESH')

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return (ob is not None and ob.type == 'MESH' and ob.mode == 'TEXTURE_PAINT'
                and bool(ob.data.uv_layers))

    @classmethod
    def description(cls, context, props):
        return dict((m[0], m[2]) for m in FILL_MODES)[props.mode] + (
            ". Then click parts of the model to fill them" +
            (", or drag over faces" if props.mode == 'FACE' else "") +
            "; press it again, right-click or Esc to stop")

    def invoke(self, context, event):
        global _running
        op = running()
        if op is not None:
            # A mode button while filling: switch to it, or stop when it is the lit one.
            if context.scene.essence_fill_mode == self.mode:
                op.stop = True
            context.scene.essence_fill_mode = self.mode
            op.sync_wireframe(context)
            _redraw(context)
            return {'FINISHED'}
        context.scene.essence_fill_mode = self.mode
        self.stop = False
        self.wire = None            # [(3D view space, its Wireframe overlay before)] in Face mode
        self.drag = None            # (last mouse x, y, faces filled) while dragging in Face mode
        _running = self
        self.sync_wireframe(context)
        context.window.cursor_modal_set('PAINT_BRUSH')
        context.workspace.status_text_set("Click a part to fill it   |   Right-click or Esc: done")
        context.window_manager.modal_handler_add(self)
        _redraw(context)
        return {'RUNNING_MODAL'}

    def sync_wireframe(self, context):
        """Face mode shows the wireframe in every 3D view, so the faces can be seen; any other
        mode, or the end of filling, puts each view's own setting back."""
        on = _running is self and context.scene.essence_fill_mode == 'FACE'
        if on and self.wire is None:
            self.wire = []
            for area in context.window.screen.areas:
                if area.type == 'VIEW_3D':
                    overlay = area.spaces.active.overlay
                    self.wire.append((area.spaces.active, overlay.show_wireframes))
                    overlay.show_wireframes = True
        elif not on and self.wire is not None:
            for space, before in self.wire:
                try:
                    space.overlay.show_wireframes = before
                except ReferenceError:      # the view was closed meanwhile
                    pass
            self.wire = None

    def _finish(self, context):
        global _running
        _running = None
        self.sync_wireframe(context)
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        _redraw(context)

    def modal(self, context, event):
        if self.stop or (event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS'):
            self._finish(context)
            return {'FINISHED'}
        ob = context.active_object
        if ob is None or ob.mode != 'TEXTURE_PAINT':
            # Left Texture Paint (Exit Paint Mode, the mode menu): filling ends with it.
            self._finish(context)
            return {'CANCELLED'}
        self.sync_wireframe(context)
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.drag is not None:
            self.drag = None
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and self.drag is not None:
            return self._drag_to(context, ob, event)
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        area, region = _view(context, event)
        if region is None:
            return {'PASS_THROUGH'}         # the sidebar and the other editors keep working
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        face = _face_at(context, ob, area, region, coord)
        if face is None:
            self.report({'INFO'}, f"Nothing of {ob.name} under the cursor")
            return {'RUNNING_MODAL'}
        mode = context.scene.essence_fill_mode
        self._fill(context, ob, area, region, faces_for_mode(ob.data, face, mode), coord)
        if mode == 'FACE':
            self.drag = (event.mouse_x, event.mouse_y, {face})
        return {'RUNNING_MODAL'}

    def _drag_to(self, context, ob, event):
        """Face mode, mouse held: fill the faces passed since the last move. The path is
        sampled every few pixels so a fast drag skips no face, and the new faces of one
        material go in one click of the Fill brush (one undo step per move)."""
        x0, y0, done = self.drag
        self.drag = (event.mouse_x, event.mouse_y, done)
        area, region = _view(context, event)
        if region is None:
            return {'RUNNING_MODAL'}
        dx, dy = event.mouse_x - x0, event.mouse_y - y0
        steps = max(1, int(max(abs(dx), abs(dy)) / 3))
        hits = {}                   # face: where it was hit
        for i in range(1, steps + 1):
            coord = (x0 + dx * i / steps - region.x, y0 + dy * i / steps - region.y)
            face = _face_at(context, ob, area, region, coord)
            if face is not None and face not in done:
                hits.setdefault(face, coord)
        # One click paints into one material's image, so a click per material.
        by_material = {}
        for face, coord in hits.items():
            by_material.setdefault(ob.data.polygons[face].material_index, []).append((face, coord))
        for group in by_material.values():
            faces = np.zeros(len(ob.data.polygons), dtype=bool)
            for face, _coord in group:
                faces |= faces_for_mode(ob.data, face, 'FACE')
            self._fill(context, ob, area, region, faces, group[-1][1])
        done.update(hits)
        return {'RUNNING_MODAL'}

    def _fill(self, context, ob, area, region, faces, coord):
        if not faces.any():
            return
        with context.temp_override(area=area, region=region):
            fill(bpy.context, ob, faces, coord,
                 tuple(paint_color(context.scene.tool_settings.image_paint).color))


def _face_at(context, ob, area, region, coord):
    """The polygon of `ob` under `coord` (pixels in `region`), or None."""
    from bpy_extras import view3d_utils
    rv3d = area.spaces.active.region_3d
    return pick_face(context, ob, view3d_utils.region_2d_to_origin_3d(region, rv3d, coord),
                     view3d_utils.region_2d_to_vector_3d(region, rv3d, coord))


def _redraw(context):
    for area in context.window.screen.areas:
        if area.type in {'VIEW_3D', 'PROPERTIES', 'IMAGE_EDITOR'}:
            area.tag_redraw()


def draw(layout, context):
    """The fill controls, drawn under Exit Paint Mode while in Texture Paint. Each mode button
    starts filling (the lit one is on), so there is no separate Fill button to miss."""
    box = layout.box()
    on = running() is not None
    box.label(text="Fill: click a mode, then the model" if not on else
              "Filling: click parts of the model", icon='BRUSH_DATA')
    # The brush's own color, the one in the header: Fill paints with it.
    box.prop(paint_color(context.scene.tool_settings.image_paint), "color", text="Brush Color")
    row = box.row(align=True)
    for mode, name, _desc in FILL_MODES:
        row.operator("essence.fill", text=name,
                     depress=on and context.scene.essence_fill_mode == mode).mode = mode
    brush = context.scene.tool_settings.image_paint.brush
    if not on and brush is not None and getattr(brush, "image_brush_type", None) == 'FILL':
        box.label(text="Blender's Fill tool ignores these modes", icon='ERROR')


def register():
    S = bpy.types.Scene
    S.essence_fill_mode = EnumProperty(name="Fill Mode", items=FILL_MODES, default='MESH',
                                       description="What a click of Fill covers (the last mode used)")
    bpy.utils.register_class(ESSENCE_OT_fill)


def unregister():
    bpy.utils.unregister_class(ESSENCE_OT_fill)
    del bpy.types.Scene.essence_fill_mode
