import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from . import (adjustments, curvature, filters, generators, gizmo, nodes, preview, sources,
               stack)
from .channels import SOURCE_BY_ID


def _material(context, create=False):
    ob = context.active_object
    if ob is None or ob.type != 'MESH':
        return None
    mat = ob.active_material
    if mat is None and create:
        mat = bpy.data.materials.new(f"{ob.name} Essence")
        ob.data.materials.append(mat)
        ob.active_material = mat
    return mat


def _active(mat):
    """The active stack entry, or None."""
    s = mat.essence
    return s.layers[s.active_index] if 0 <= s.active_index < len(s.layers) else None


def _active_mask(context):
    """The active entry when it has an image to paint: a mask, or a paint item."""
    mat = _material(context)
    e = _active(mat) if mat is not None else None
    if e is None:
        return None
    return e if e.kind == 'MASK' or (e.kind == 'FILL' and e.fill_paint) else None


def _owner(mat, e):
    """The layer or folder entry that `e` belongs to (`e` itself if it is not a mask or an
    adjustment; an adjustment of a mask belongs to that mask's layer)."""
    while e is not None and (e.kind == 'MASK' or e.kind in stack.ITEM_KINDS):
        e = next((x for x in mat.essence.layers if x.uid == e.parent), None)
    return e


def _adjustment_owner(mat, e):
    """The layer or mask an adjustment is added to for the active entry `e`: the entry itself
    when it is a layer or mask, the owner when it is an adjustment, None for a folder."""
    if e is None:
        return None
    if e.kind in stack.ITEM_KINDS:
        e = next((x for x in mat.essence.layers if x.uid == e.parent), None)
    return e if e is not None and e.kind in ('LAYER', 'MASK') else None


def _reapply(mat, owner_uid):
    """Re-wire the sources of a layer or mask after its adjustments changed."""
    owner = next((x for x in mat.essence.layers if x.uid == owner_uid), None)
    if owner is not None:
        sources.apply_owner(mat, owner)
    nodes.rebuild_stack(mat)


class _Base:
    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'


class ESSENCE_OT_add_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.add_layer"
    bl_label = "Add Layer"
    bl_description = ("Add a layer above the active one. With a folder active, the layer "
                      "goes inside it")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context, create=True)
        s = mat.essence
        nodes.ensure_tree(mat)
        name = f"Layer {sum(1 for x in s.layers if x.kind == 'LAYER') + 1}"
        node = nodes.add_layer_node(mat, name)
        cur = _active(mat)
        owner = _owner(mat, cur) if cur is not None else None
        e = s.layers.add()
        e.uid = uid = stack.new_uid()
        e.node_name = node.name
        e.name = name
        e.parent = owner.uid if owner is not None and owner.kind == 'FOLDER' else (
            owner.parent if owner is not None else "")
        tree = stack.Tree(s.layers)
        if owner is not None and owner.kind == 'LAYER':
            tree.insert_after(uid, owner.uid)
        tree.apply()
        nodes.rebuild_stack(mat)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_add_folder(_Base, bpy.types.Operator):
    bl_idname = "essence.add_folder"
    bl_label = "Add Folder"
    bl_description = ("Add an empty folder above the active entry. Move layers into it with "
                      "the up/down arrows. The folder's mask limits everything inside")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context, create=True)
        s = mat.essence
        nodes.ensure_tree(mat)
        name = f"Folder {sum(1 for x in s.layers if x.kind == 'FOLDER') + 1}"
        node = nodes.add_layer_node(mat, name, folder=True)
        cur = _active(mat)
        owner = _owner(mat, cur) if cur is not None else None
        e = s.layers.add()
        e.kind = 'FOLDER'
        e.uid = uid = stack.new_uid()
        e.node_name = node.name
        e.name = name
        tree = stack.Tree(s.layers)
        if owner is not None:
            tree.insert_after(uid, owner.parent or owner.uid)  # above the whole folder
        tree.apply()
        nodes.rebuild_stack(mat)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_remove_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.remove_layer"
    bl_label = "Remove"
    bl_description = ("Remove the active layer or mask. Removing a folder ungroups its "
                      "layers. A layer or folder takes its masks with it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and len(mat.essence.layers) > 0

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        i = s.active_index
        e = _active(mat)
        if e is None:
            return {'CANCELLED'}
        tree = mat.node_tree
        adjusted = e.parent if e.kind in stack.ITEM_KINDS else ""
        for names in stack.remove_entries(s.layers, e.uid):
            for n in names:
                node = tree.nodes.get(n) if n else None
                if node is not None:
                    tree.nodes.remove(node)
        s.active_index = max(0, min(i, len(s.layers) - 1))
        nodes.rebuild_stack(mat)  # also deletes the removed adjustment's nodes
        filters.cleanup()
        if adjusted:
            _reapply(mat, adjusted)
        return {'FINISHED'}


class ESSENCE_OT_move_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.move_layer"
    bl_label = "Move"
    bl_description = ("Move the active entry. Layers move in and out of folders, masks "
                      "reorder within their layer")
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and len(mat.essence.layers) > 1

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        e = _active(mat)
        if e is None:
            return {'CANCELLED'}
        uid = e.uid
        tree = stack.Tree(s.layers)
        # The list is stored bottom-to-top; "up" in the UI means a higher index.
        if not tree.move(uid, self.direction == 'UP'):
            return {'CANCELLED'}
        tree.apply()
        nodes.rebuild_stack(mat)
        if e.kind in stack.ITEM_KINDS:
            _reapply(mat, e.parent)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_add_mask(_Base, bpy.types.Operator):
    bl_idname = "essence.add_mask"
    bl_label = "Add Mask"
    bl_description = ("Add a mask to the active layer or folder. A layer or folder has one "
                      "mask; stack more onto it with Fill items")
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(
        name="Source",
        items=[('PAINT', "Paint", "New blank image to paint on"),
               ('IMAGE', "Image", "Empty slot, pick an existing image afterwards")],
        default='PAINT', options={'HIDDEN'})
    fill: EnumProperty(
        name="Fill",
        items=[('BLACK', "Black (hides)", "Hides the layer until painted"),
               ('WHITE', "White (reveals)", "Layer is fully visible until erased")],
        default='WHITE')
    size: IntProperty(name="Size", default=1024, min=64, max=8192)

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        cur = _active(mat) if mat is not None else None
        owner = _owner(mat, cur) if cur is not None else None
        return owner is not None and not any(
            x.kind == 'MASK' and x.parent == owner.uid for x in mat.essence.layers)

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        cur = _active(mat)
        owner = _owner(mat, cur) if cur is not None else None
        if owner is None or any(x.kind == 'MASK' and x.parent == owner.uid for x in s.layers):
            return {'CANCELLED'}
        name = "Mask"
        img = None
        if self.source == 'PAINT':
            v = 0.0 if self.fill == 'BLACK' else 1.0
            img = nodes.new_paint_image(f"{mat.name}_{owner.name}_{name}", self.size,
                                        (v, v, v, 1.0))
        node, img_node = nodes.add_mask_node(mat, name, img)
        owner_uid = owner.uid
        e = s.layers.add()
        e.kind = 'MASK'
        e.uid = uid = stack.new_uid()
        e.parent = owner_uid
        e.name = name
        e.node_name = node.name
        e.mask_node_name = img_node.name
        tree = stack.Tree(s.layers)
        tree.apply()
        nodes.rebuild_stack(mat)
        s.active_index = tree.index(uid)  # selects the mask, which shows its options
        return {'FINISHED'}


class ESSENCE_OT_select_entry(_Base, bpy.types.Operator):
    bl_idname = "essence.select_entry"
    bl_label = "Select"
    bl_description = "Select this entry. Ctrl-click to rename it"
    bl_options = {'INTERNAL'}

    uid: StringProperty()

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        for i, e in enumerate(s.layers):
            if e.uid == self.uid:
                s.active_index = i
                return {'FINISHED'}
        return {'CANCELLED'}

    def invoke(self, context, event):
        result = self.execute(context)
        if event.ctrl and result == {'FINISHED'}:
            bpy.ops.essence.rename_entry('INVOKE_DEFAULT', uid=self.uid)
        return result


class ESSENCE_OT_rename_entry(_Base, bpy.types.Operator):
    bl_idname = "essence.rename_entry"
    bl_label = "Rename"
    bl_description = "Give this layer, folder, mask or item another name"
    bl_options = {'REGISTER', 'UNDO'}

    uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(name="Name")

    def invoke(self, context, event):
        mat = _material(context)
        e = next((x for x in mat.essence.layers if x.uid == self.uid), None) if mat else None
        if e is None:
            return {'CANCELLED'}
        self.name = e.name
        return context.window_manager.invoke_props_dialog(self, width=320, title="Rename",
                                                          confirm_text="Rename")

    def draw(self, context):
        row = self.layout.row()
        row.activate_init = True    # the field is ready to type into, with the name selected
        row.prop(self, "name", text="")

    def execute(self, context):
        mat = _material(context)
        e = next((x for x in mat.essence.layers if x.uid == self.uid), None) if mat else None
        name = self.name.strip()
        if e is None or not name:
            return {'CANCELLED'}
        e.name = name       # its update puts the name on the entry's nodes too
        return {'FINISHED'}


class ESSENCE_OT_add_mask_menu(_Base, bpy.types.Operator):
    bl_idname = "essence.add_mask_menu"
    bl_label = "Add Mask"
    bl_description = "Add a white or black mask to this layer or folder"
    bl_options = {'INTERNAL'}

    uid: StringProperty()

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        for i, e in enumerate(s.layers):
            if e.uid == self.uid:
                s.active_index = i  # the Add Mask menu works on the active entry
                return {'FINISHED'}
        return {'CANCELLED'}

    def invoke(self, context, event):
        if self.execute(context) == {'CANCELLED'}:
            return {'CANCELLED'}
        bpy.ops.wm.call_menu(name="ESSENCE_MT_add_mask")
        return {'FINISHED'}


class ESSENCE_OT_add_adjustment(_Base, bpy.types.Operator):
    bl_idname = "essence.add_adjustment"
    bl_label = "Add Adjustment"
    bl_description = ("Add an adjustment on top of the active layer's or mask's adjustments. "
                      "It transforms that layer channel's input (or the mask value) before "
                      "it is blended")
    bl_options = {'REGISTER', 'UNDO'}

    adj_type: EnumProperty(name="Type", items=adjustments.TYPE_ITEMS)

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and _adjustment_owner(mat, _active(mat)) is not None

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        owner = _adjustment_owner(mat, _active(mat))
        t = adjustments.BY_ID[self.adj_type]
        owner_uid = owner.uid
        e = s.layers.add()
        e.kind = 'ADJUSTMENT'
        e.uid = uid = stack.new_uid()
        e.parent = owner_uid
        e.name = t.label
        sources.SUSPEND = True  # set up without triggering the update callbacks
        try:
            e.adj_type = self.adj_type
            e.adj_enabled = True
            e.adj_channel = 'COLOR'
        finally:
            sources.SUSPEND = False
        tree = stack.Tree(s.layers)
        tree.apply()
        _reapply(mat, owner_uid)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_add_paint(_Base, bpy.types.Operator):
    bl_idname = "essence.add_paint"
    bl_label = "Add Paint"
    bl_description = ("Add a paint item on top of the active mask's items: a transparent image "
                      "you paint, blended onto the mask value built so far. Paint white to "
                      "reveal and black to hide; unpainted areas change nothing")
    bl_options = {'REGISTER', 'UNDO'}

    size: IntProperty(name="Size", default=1024, min=64, max=8192)

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        owner = _adjustment_owner(mat, _active(mat)) if mat is not None else None
        return owner is not None and owner.kind == 'MASK'

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        owner = _adjustment_owner(mat, _active(mat))
        owner_uid = owner.uid
        n = sum(1 for x in s.layers if x.kind == 'FILL' and x.fill_paint and x.parent == owner_uid)
        # Float, with an alpha channel that starts at 0: nothing painted yet.
        # Transparent black: a color under no alpha must not leak into a stroke's soft edge.
        img = nodes.new_paint_image(f"{mat.name}_Paint", self.size, (0.0, 0.0, 0.0, 0.0),
                                    alpha=True)
        e = s.layers.add()
        e.kind = 'FILL'
        e.uid = uid = stack.new_uid()
        e.parent = owner_uid
        e.name = "Paint" if n == 0 else f"Paint {n + 1}"
        sources.SUSPEND = True
        try:
            e.adj_enabled = True
            e.fill_blend = 'NORMAL'
            e.fill_paint = True
            e.source.channel = 'MASK'
            e.source.uid = uid
            e.source.kind = 'IMAGE'
            e.source.image_memory = img     # the image node made for the source takes it
        finally:
            sources.SUSPEND = False
        tree = stack.Tree(s.layers)
        tree.apply()
        _reapply(mat, owner_uid)
        # A fill gets its own node on the first pass and its image node on the second.
        _reapply(mat, owner_uid)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_add_fill(_Base, bpy.types.Operator):
    bl_idname = "essence.add_fill"
    bl_label = "Add Fill"
    bl_description = ("Add a fill on top of the active mask: another source (image, "
                      "procedural or value) blended onto the mask value built so far")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        owner = _adjustment_owner(mat, _active(mat)) if mat is not None else None
        return owner is not None and owner.kind == 'MASK'

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        owner_uid = _adjustment_owner(mat, _active(mat)).uid
        n = sum(1 for x in s.layers if x.kind == 'FILL' and x.parent == owner_uid)
        e = s.layers.add()
        e.kind = 'FILL'
        e.uid = uid = stack.new_uid()
        e.parent = owner_uid
        e.name = "Fill" if n == 0 else f"Fill {n + 1}"
        sources.SUSPEND = True
        try:
            e.adj_enabled = True
            e.fill_blend = 'NORMAL'
            e.source.channel = 'MASK'
            e.source.uid = uid
            e.source.kind = 'VALUE'  # white: neutral for Multiply; pick a source in the panel
        finally:
            sources.SUSPEND = False
        tree = stack.Tree(s.layers)
        tree.apply()
        _reapply(mat, owner_uid)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_add_filter(_Base, bpy.types.Operator):
    bl_idname = "essence.add_filter"
    bl_label = "Add Baked Filter"
    bl_description = ("Add a baked filter on top of the active mask's or layer's items. It "
                      "filters the value built so far (a mask value, or one layer channel's "
                      "input) into an image; press Apply to bake it")
    bl_options = {'REGISTER', 'UNDO'}

    filter_type: EnumProperty(name="Filter", items=[(t.id, t.label, t.description)
                                                    for t in filters.TYPES])

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and _adjustment_owner(mat, _active(mat)) is not None

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        active = _active(mat)
        owner = _adjustment_owner(mat, active)
        owner_uid = owner.uid
        t = filters.BY_ID[self.filter_type]
        channel = 'COLOR'
        if owner.kind == 'LAYER':
            # The channel of the item that is selected, else Color; Dilate/Erode works on one
            # value, so it starts on the first channel that is not a color.
            if active.kind in stack.ITEM_KINDS and active.adj_channel in SOURCE_BY_ID:
                channel = active.adj_channel
            if t.single_value and SOURCE_BY_ID[channel].socket_type == 'NodeSocketColor':
                channel = next((c.id for c in sources.active_channels(mat)
                                if c.socket_type != 'NodeSocketColor'), channel)
        e = s.layers.add()
        e.kind = 'FILTER'
        e.uid = uid = stack.new_uid()
        e.parent = owner_uid
        e.name = t.label
        sources.SUSPEND = True
        try:
            e.adj_enabled = True
            e.adj_channel = channel
            e.filter_type = self.filter_type
        finally:
            sources.SUSPEND = False
        filters.build_group(e)
        tree = stack.Tree(s.layers)
        tree.apply()
        _reapply(mat, owner_uid)
        s.active_index = tree.index(uid)
        return {'FINISHED'}


class ESSENCE_OT_preview(_Base, bpy.types.Operator):
    bl_idname = "essence.preview"
    bl_label = "Preview"
    bl_description = ("Show this on its own in the viewport, flat and unshaded (needs Material "
                      "Preview or Rendered shading). A mask shows as grey, a layer as its own "
                      "color, a channel as the result of the whole stack. Press again to end")
    bl_options = {'REGISTER', 'UNDO'}

    kind: StringProperty()      # 'LAYER', 'MASK' or 'CHANNEL'
    ident: StringProperty()     # the entry's uid, or the channel's id

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and mat.node_tree is not None

    def execute(self, context):
        mat = _material(context)
        ending = preview.active(mat) == (self.kind, self.ident)
        if not preview.toggle(mat, self.kind, self.ident) and not ending:
            self.report({'WARNING'}, "Nothing to preview here: the channel or the layer's "
                                     "Color channel is switched off, or nothing feeds it")
        return {'FINISHED'}


class ESSENCE_OT_preview_stop(_Base, bpy.types.Operator):
    bl_idname = "essence.preview_stop"
    bl_label = "Stop Preview"
    bl_description = "End the preview and show the material again"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and preview.find(mat) is not None

    def execute(self, context):
        preview.stop(_material(context))
        return {'FINISHED'}


def _id_select_node(context, uid, channel):
    """The ID Select generator node of a source of the active material, or None."""
    ob = context.active_object
    mat = ob.active_material if ob is not None else None
    if mat is None:
        return None
    tex = sources.source_nodes(mat.node_tree, uid, channel).get('TEXTURE')
    return tex if tex is not None and generators.COUNT_INPUT in tex.inputs else None


class ESSENCE_OT_id_color_add(_Base, bpy.types.Operator):
    bl_idname = "essence.id_color_add"
    bl_label = "Add Color"
    bl_description = ("Add another color to the palette: the generator selects every part "
                      "whose ID color is close to any of them")
    bl_options = {'REGISTER', 'UNDO'}

    uid: StringProperty()
    channel: StringProperty()

    def execute(self, context):
        import colorsys
        tex = _id_select_node(context, self.uid, self.channel)
        if tex is None:
            return {'CANCELLED'}
        count = int(tex.inputs[generators.COUNT_INPUT].default_value)
        if count >= generators.MAX_ID_COLORS:
            self.report({'WARNING'}, f"The palette holds {generators.MAX_ID_COLORS} colors")
            return {'CANCELLED'}
        # a color that is easy to tell from the last ones, to be replaced by the eyedropper
        r, g, b = colorsys.hsv_to_rgb((count * 0.618034) % 1.0, 0.8, 0.9)
        tex.inputs[generators.id_color_name(count + 1)].default_value = (r, g, b, 1.0)
        tex.inputs[generators.COUNT_INPUT].default_value = count + 1
        return {'FINISHED'}


def _barycentric(p, a, b, c):
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
    d20, d21 = v2.dot(v0), v2.dot(v1)
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-20:
        return (1.0, 0.0, 0.0)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return (1.0 - v - w, v, w)


def surface_uv(context, origin, direction):
    """What a ray from `origin` along `direction` (world space) hits first: (object, material,
    uv) of the surface point, or None for a miss or a mesh without a UV map."""
    dg = context.evaluated_depsgraph_get()
    hit, loc, _normal, index, obj, matrix = context.scene.ray_cast(dg, origin, direction)
    if not hit or obj is None or obj.type != 'MESH':
        return None
    ev = obj.evaluated_get(dg)
    me = ev.data
    layer = me.uv_layers.active
    if layer is None or not 0 <= index < len(me.polygons):
        return None
    p = matrix.inverted() @ loc
    best, best_score = None, None
    for t in me.loop_triangles:
        if t.polygon_index != index:
            continue
        a, b, c = (me.vertices[i].co for i in t.vertices)
        w = _barycentric(p, a, b, c)
        score = min(w)                  # the triangle that contains the point scores highest
        if best_score is None or score > best_score:
            best, best_score = (t, w), score
    if best is None:
        return None
    t, w = best
    uvs = [layer.uv[li].vector for li in t.loops]
    uv = (sum(wi * u.x for wi, u in zip(w, uvs)), sum(wi * u.y for wi, u in zip(w, uvs)))
    slot = me.polygons[index].material_index
    mat = ev.material_slots[slot].material if slot < len(ev.material_slots) else None
    return obj, (mat.original if mat is not None else None), uv


def texel_at(image, uv):
    """The RGB of the texel under `uv` as the shader reads it with Closest interpolation and
    Repeat extension."""
    w, h = image.size
    x, y = int(uv[0] * w // 1) % w, int(uv[1] * h // 1) % h
    i = (y * w + x) * image.channels
    return tuple(image.pixels[i:i + 3])


class ESSENCE_OT_id_color_sample(_Base, bpy.types.Operator):
    bl_idname = "essence.id_color_sample"
    bl_label = "Sample"
    bl_description = ("Click parts of the model to add their ID colors to the palette (the color "
                      "is read from the ID map itself). Right-click or Esc to finish")
    bl_options = {'REGISTER', 'UNDO'}

    uid: StringProperty()
    channel: StringProperty()

    def _view(self, context, event):
        """(region, region_3d) of the 3D view under the mouse, or (None, None)."""
        for area in context.window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if (region.type == 'WINDOW' and region.x <= event.mouse_x < region.x + region.width
                        and region.y <= event.mouse_y < region.y + region.height):
                    return region, area.spaces.active.region_3d
        return None, None

    def invoke(self, context, event):
        mat = _material(context)
        node = mat.node_tree.nodes.get(nodes.map_slot_name('ID')) if mat is not None else None
        if node is None or node.image is None:
            self.report({'WARNING'}, "Bake or pick an ID map first (Mesh Maps)")
            return {'CANCELLED'}
        if _id_select_node(context, self.uid, self.channel) is None:
            return {'CANCELLED'}
        context.window.cursor_modal_set('EYEDROPPER')
        context.workspace.status_text_set("Click a part to add its color   |   "
                                          "Right-click or Esc: done")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        region, rv3d = self._view(context, event)
        if region is None:
            return {'PASS_THROUGH'}         # the sidebar and the other editors keep working
        from bpy_extras import view3d_utils
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        hit = surface_uv(context, view3d_utils.region_2d_to_origin_3d(region, rv3d, coord),
                         view3d_utils.region_2d_to_vector_3d(region, rv3d, coord))
        mat = _material(context)
        tex = _id_select_node(context, self.uid, self.channel)
        node = mat.node_tree.nodes.get(nodes.map_slot_name('ID')) if mat is not None else None
        if tex is None or node is None or node.image is None:
            self._finish(context)
            return {'CANCELLED'}
        if hit is None:
            self.report({'INFO'}, "Nothing with a UV map under the cursor")
        elif hit[1] is not mat:
            self.report({'WARNING'}, "That part does not use this material")
        else:
            result = generators.add_palette_color(tex, texel_at(node.image, hit[2]))
            if result == 'FULL':
                self.report({'WARNING'}, f"The palette holds {generators.MAX_ID_COLORS} colors")
            elif result == 'DUPLICATE':
                self.report({'INFO'}, "That color is already in the palette")
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class ESSENCE_OT_id_color_remove(_Base, bpy.types.Operator):
    bl_idname = "essence.id_color_remove"
    bl_label = "Remove Color"
    bl_description = "Remove this color from the palette (the generator keeps at least one)"
    bl_options = {'REGISTER', 'UNDO'}

    uid: StringProperty()
    channel: StringProperty()
    index: IntProperty(min=1, max=16)

    def execute(self, context):
        tex = _id_select_node(context, self.uid, self.channel)
        if tex is None:
            return {'CANCELLED'}
        count = int(tex.inputs[generators.COUNT_INPUT].default_value)
        if count <= 1 or not 1 <= self.index <= count:
            return {'CANCELLED'}
        for j in range(self.index, count):          # the colors below move up one place
            tex.inputs[generators.id_color_name(j)].default_value = \
                tuple(tex.inputs[generators.id_color_name(j + 1)].default_value)
        tex.inputs[generators.COUNT_INPUT].default_value = count - 1
        return {'FINISHED'}


class ESSENCE_OT_show_map(_Base, bpy.types.Operator):
    bl_idname = "essence.show_map"
    bl_label = "Show in Image Editor"
    bl_description = "Show this mesh map in the Image Editors that are open"
    bl_options = {'INTERNAL'}

    map_id: StringProperty()

    def execute(self, context):
        mat = _material(context)
        node = mat.node_tree.nodes.get(nodes.map_slot_name(self.map_id)) if mat else None
        if node is None or node.image is None:
            return {'CANCELLED'}
        shown = False
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = node.image
                    shown = True
        if not shown:
            self.report({'INFO'}, "Open an Image Editor to see it")
        return {'FINISHED'}


class ESSENCE_OT_light_gizmo(_Base, bpy.types.Operator):
    bl_idname = "essence.light_gizmo"
    bl_label = "Light Gizmo"
    bl_description = ("Show a temporary arrow (an Empty) in the 3D view that sets this "
                      "generator's rotation: rotate it with Blender's own tools. Press again to "
                      "remove it; the rotation stays")
    bl_options = {'REGISTER', 'UNDO'}

    uid: StringProperty()
    channel: StringProperty()

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and ob.active_material is not None

    def execute(self, context):
        ob = context.active_object
        mat = ob.active_material
        entry = next((e for e in mat.essence.layers if e.uid == self.uid), None)
        tex = (sources.source_nodes(mat.node_tree, self.uid, self.channel).get('TEXTURE')
               if entry is not None else None)
        if not gizmo.supports(tex):
            return {'CANCELLED'}
        if gizmo.is_active(tex):
            gizmo.remove(tex)
            self.report({'INFO'}, "Gizmo removed, the rotation stays as it was")
            return {'FINISHED'}
        gizmo.add(tex, ob, entry.name)
        self.report({'INFO'}, "Select the arrow in the 3D view and rotate it (R): it points "
                              "where the light comes from")
        return {'FINISHED'}


class ESSENCE_OT_derive_curvature(_Base, bpy.types.Operator):
    bl_idname = "essence.derive_curvature"
    bl_label = "Derive Curvature"
    bl_description = ("Derive the curvature from the Normal slot: convex edges bright, concave "
                      "crevices dark, flat 0.5. Needed again after the normal or the settings "
                      "change")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and ob.active_material is not None

    def execute(self, context):
        ob = context.active_object
        mat = ob.active_material
        try:
            curvature.derive(context, ob, mat)
        except Exception as e:  # noqa: BLE001
            self.report({'ERROR'}, f"Curvature failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, "Curvature derived")
        return {'FINISHED'}


class ESSENCE_OT_filter_apply(_Base, bpy.types.Operator):
    bl_idname = "essence.filter_apply"
    bl_label = "Apply"
    bl_description = ("Bake the mask value below this filter, run the filter on it and store "
                      "the result. Needed again after the items below change")
    bl_options = {'REGISTER', 'UNDO'}

    uid: StringProperty()

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and ob.active_material is not None

    def execute(self, context):
        ob = context.active_object
        mat = ob.active_material
        entry = next((e for e in mat.essence.layers if e.uid == self.uid), None)
        if entry is None or entry.kind != 'FILTER':
            return {'CANCELLED'}
        if not ob.data.uv_layers:
            self.report({'ERROR'}, "The object has no UV map")
            return {'CANCELLED'}
        try:
            done = filters.apply_with_dependencies(context, ob, mat, entry)
        except Exception as e:  # noqa: BLE001
            self.report({'ERROR'}, f"Apply failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Applied {', '.join(done)}")
        return {'FINISHED'}


class ESSENCE_OT_filter_apply_all(_Base, bpy.types.Operator):
    bl_idname = "essence.filter_apply_all"
    bl_label = "Apply All"
    bl_description = ("Apply every filter that is not applied yet or out of date, in order: "
                      "lower filters first, and the ones above them again when they change")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return (ob is not None and ob.type == 'MESH' and ob.active_material is not None
                and any(e.kind == 'FILTER' for e in ob.active_material.essence.layers))

    def execute(self, context):
        ob = context.active_object
        mat = ob.active_material
        if not ob.data.uv_layers:
            self.report({'ERROR'}, "The object has no UV map")
            return {'CANCELLED'}
        try:
            done = filters.apply_all(context, ob, mat)
        except Exception as e:  # noqa: BLE001
            self.report({'ERROR'}, f"Apply failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Applied {', '.join(done)}" if done else "Everything is current")
        return {'FINISHED'}


class ESSENCE_MT_add_adjustment(bpy.types.Menu):
    bl_idname = "ESSENCE_MT_add_adjustment"
    bl_label = "Add Adjustment"

    def draw(self, context):
        mat = _material(context)
        owner = _adjustment_owner(mat, _active(mat)) if mat is not None else None
        if owner is not None:
            if owner.kind == 'MASK':
                self.layout.operator("essence.add_paint", text="Paint", icon='BRUSH_DATA')
                self.layout.operator("essence.add_fill", text="Fill", icon='IMAGE_DATA')
                self.layout.separator()
            self.layout.label(text="Baked filters (press Apply)")
            for ft in filters.TYPES:
                self.layout.operator("essence.add_filter", text=ft.label,
                                     icon='FILTER').filter_type = ft.id
            self.layout.separator()
        for t in adjustments.TYPES:
            op = self.layout.operator("essence.add_adjustment", text=t.label)
            op.adj_type = t.id


class ESSENCE_OT_mask_non_color(_Base, bpy.types.Operator):
    bl_idname = "essence.mask_non_color"
    bl_label = "Set Non-Color"
    bl_description = ("Set the mask image's color space to Non-Color. "
                      "This changes the image itself, so other users of it are affected")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_mask(context) is not None

    def execute(self, context):
        mat = _material(context)
        node = mat.node_tree.nodes.get(_active_mask(context).mask_node_name)
        if node is None or node.image is None:
            return {'CANCELLED'}
        node.image.colorspace_settings.name = 'Non-Color'
        return {'FINISHED'}


class ESSENCE_OT_source_colorspace(_Base, bpy.types.Operator):
    bl_idname = "essence.source_colorspace"
    bl_label = "Fix Color Space"
    bl_description = ("Set the source image's color space to what this channel expects "
                      "(sRGB for color, Non-Color for data). This changes the image itself, "
                      "so other users of it are affected")
    bl_options = {'REGISTER', 'UNDO'}

    channel: StringProperty()

    def execute(self, context):
        mat = _material(context)
        e = _active(mat) if mat is not None else None
        ch = SOURCE_BY_ID.get(self.channel)
        if e is None or ch is None:
            return {'CANCELLED'}
        tex = sources.source_nodes(mat.node_tree, e.uid, ch.id).get('TEXTURE')
        if tex is None or tex.bl_idname != 'ShaderNodeTexImage' or tex.image is None:
            return {'CANCELLED'}
        tex.image.colorspace_settings.name = ch.colorspace
        return {'FINISHED'}


class ESSENCE_OT_map_non_color(_Base, bpy.types.Operator):
    bl_idname = "essence.map_non_color"
    bl_label = "Set Non-Color"
    bl_description = ("Set the map image's color space to Non-Color. This changes the image "
                      "itself, so other users of it are affected")
    bl_options = {'REGISTER', 'UNDO'}

    map_id: StringProperty()

    def execute(self, context):
        mat = _material(context)
        node = mat.node_tree.nodes.get(nodes.map_slot_name(self.map_id)) if mat else None
        if node is None or node.image is None:
            return {'CANCELLED'}
        node.image.colorspace_settings.name = 'Non-Color'
        return {'FINISHED'}


class ESSENCE_OT_paint_mask(_Base, bpy.types.Operator):
    bl_idname = "essence.paint_mask"
    bl_label = "Paint"
    bl_description = "Enter Texture Paint mode targeting the active mask or paint item"

    @classmethod
    def poll(cls, context):
        return _active_mask(context) is not None

    def execute(self, context):
        ob = context.active_object
        mat = _material(context)
        e = _active_mask(context)
        node = mat.node_tree.nodes.get(e.mask_node_name)
        if node is None or node.image is None:
            self.report({'ERROR'}, "Mask image node is missing")
            return {'CANCELLED'}
        if e.source.projection != 'UV':
            self.report({'ERROR'}, "Painting needs the mask's UV projection")
            return {'CANCELLED'}
        if not ob.data.uv_layers:
            self.report({'ERROR'}, "Object has no UV map; unwrap it to paint")
            return {'CANCELLED'}
        mat.node_tree.nodes.active = node
        slot = ob.active_material_index
        if 0 <= slot < len(ob.material_slots):
            ob.active_material_index = slot
        context.tool_settings.image_paint.mode = 'MATERIAL'
        if ob.mode != 'TEXTURE_PAINT':
            bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        return {'FINISHED'}


class ESSENCE_OT_exit_paint(_Base, bpy.types.Operator):
    bl_idname = "essence.exit_paint"
    bl_label = "Exit Paint Mode"

    def execute(self, context):
        if context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}


class ESSENCE_OT_rebuild(_Base, bpy.types.Operator):
    bl_idname = "essence.rebuild"
    bl_label = "Rebuild Links"
    bl_description = "Re-create the links of the stack and refresh the node groups"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context)
        if mat is None or mat.node_tree is None:
            return {'CANCELLED'}
        for e in mat.essence.layers:
            if e.kind not in ('LAYER', 'FOLDER', 'MASK'):  # items have no group node
                continue
            n = mat.node_tree.nodes.get(e.node_name)
            group = nodes.group_for(mat, e)
            if n is not None and n.node_tree is not group:
                n.node_tree = group
        nodes.rebuild_stack(mat)
        for e in list(mat.essence.layers):   # re-wire the sources, adjustments, fills, filters
            if e.kind == 'FILTER':
                filters.build_group(e)       # the compositor group, if a file lost it
            elif e.kind in ('LAYER', 'MASK'):
                sources.apply_owner(mat, e)
        nodes.rebuild_stack(mat)
        return {'FINISHED'}


class ESSENCE_MT_add_mask(bpy.types.Menu):
    bl_idname = "ESSENCE_MT_add_mask"
    bl_label = "Add Mask"

    def draw(self, context):
        layout = self.layout
        op = layout.operator("essence.add_mask", text="White Mask (reveals all)",
                             icon='IMAGE_ALPHA')
        op.source, op.fill = 'PAINT', 'WHITE'
        op = layout.operator("essence.add_mask", text="Black Mask (hides all)",
                             icon='MOD_MASK')
        op.source, op.fill = 'PAINT', 'BLACK'
        layout.separator()
        layout.operator("essence.add_mask", text="Image Mask", icon='IMAGE_DATA').source = 'IMAGE'


classes = (
    ESSENCE_OT_add_layer, ESSENCE_OT_add_folder, ESSENCE_OT_remove_layer,
    ESSENCE_OT_move_layer, ESSENCE_OT_add_mask, ESSENCE_OT_add_adjustment, ESSENCE_OT_add_fill,
    ESSENCE_OT_select_entry, ESSENCE_OT_add_mask_menu, ESSENCE_OT_add_filter,
    ESSENCE_OT_rename_entry, ESSENCE_OT_add_paint, ESSENCE_OT_preview, ESSENCE_OT_preview_stop,
    ESSENCE_OT_id_color_add, ESSENCE_OT_id_color_sample, ESSENCE_OT_id_color_remove,
    ESSENCE_OT_show_map, ESSENCE_OT_light_gizmo,
    ESSENCE_OT_derive_curvature,
    ESSENCE_OT_filter_apply,
    ESSENCE_OT_filter_apply_all,
    ESSENCE_MT_add_adjustment,
    ESSENCE_OT_mask_non_color, ESSENCE_OT_map_non_color, ESSENCE_OT_source_colorspace, ESSENCE_OT_paint_mask,
    ESSENCE_OT_exit_paint,
    ESSENCE_OT_rebuild, ESSENCE_MT_add_mask,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
