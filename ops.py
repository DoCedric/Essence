import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from . import adjustments, filters, nodes, sources, stack
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
    mat = _material(context)
    e = _active(mat) if mat is not None else None
    return e if e is not None and e.kind == 'MASK' else None


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
            img = bpy.data.images.new(f"{mat.name}_{owner.name}_{name}", self.size,
                                      self.size, alpha=False, float_buffer=True)
            img.generated_color = (v, v, v, 1.0)
            img.colorspace_settings.name = 'Non-Color'
            img.pack()
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
    bl_description = "Select this entry"
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
    bl_description = ("Add a baked filter on top of the active mask's items. It filters the "
                      "value built so far into an image; press Apply to bake it")
    bl_options = {'REGISTER', 'UNDO'}

    filter_type: EnumProperty(name="Filter", items=[(t.id, t.label, t.description)
                                                    for t in filters.TYPES])

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        owner = _adjustment_owner(mat, _active(mat)) if mat is not None else None
        return owner is not None and owner.kind == 'MASK'

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        owner_uid = _adjustment_owner(mat, _active(mat)).uid
        t = filters.BY_ID[self.filter_type]
        e = s.layers.add()
        e.kind = 'FILTER'
        e.uid = uid = stack.new_uid()
        e.parent = owner_uid
        e.name = t.label
        sources.SUSPEND = True
        try:
            e.adj_enabled = True
            e.filter_type = self.filter_type
        finally:
            sources.SUSPEND = False
        filters.build_group(e)
        tree = stack.Tree(s.layers)
        tree.apply()
        _reapply(mat, owner_uid)
        s.active_index = tree.index(uid)
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
        owner_uid = entry.parent
        try:
            size = filters.apply_entry(context, ob, mat, entry)
        except Exception as e:  # noqa: BLE001
            self.report({'ERROR'}, f"Apply failed: {e}")
            return {'CANCELLED'}
        _reapply(mat, owner_uid)
        self.report({'INFO'}, f"Baked filter at {size} px")
        return {'FINISHED'}


class ESSENCE_MT_add_adjustment(bpy.types.Menu):
    bl_idname = "ESSENCE_MT_add_adjustment"
    bl_label = "Add Adjustment"

    def draw(self, context):
        mat = _material(context)
        owner = _adjustment_owner(mat, _active(mat)) if mat is not None else None
        if owner is not None and owner.kind == 'MASK':
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
    bl_label = "Paint Mask"
    bl_description = "Enter Texture Paint mode targeting the active mask"

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
            n = mat.node_tree.nodes.get(e.node_name)
            group = nodes.group_for(mat, e)
            if n is not None and n.node_tree is not group:
                n.node_tree = group
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
    ESSENCE_OT_filter_apply,
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
