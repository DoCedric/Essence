import uuid

import bpy
from bpy.props import EnumProperty, IntProperty

from . import nodes


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


class _Base:
    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'


class ESSENCE_OT_add_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.add_layer"
    bl_label = "Add Layer"
    bl_description = "Add a layer on top of the stack"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context, create=True)
        s = mat.essence
        nodes.ensure_tree(mat)
        name = f"Layer {len(s.layers) + 1}"
        node = nodes.add_layer_node(mat, name)
        # Insert above the active entry. With a folder active, the layer goes inside it
        # as its top child; with a child active, it joins that child's folder.
        n = len(s.layers)
        parent = ""
        target = 0
        if n:
            cur = s.layers[s.active_index]
            if cur.kind == 'FOLDER':
                parent = cur.uid
                target = s.active_index  # just below the folder entry
            else:
                parent = cur.parent
                target = s.active_index + 1
        e = s.layers.add()
        e.node_name = node.name
        e.name = name
        e.parent = parent
        s.layers.move(n, target)
        nodes.rebuild_stack(mat)
        s.active_index = target
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
        node = nodes.add_layer_node(mat, "", folder=True)
        n = len(s.layers)
        target = 0
        if n:
            cur = s.layers[s.active_index]
            target = s.active_index + 1
            if cur.parent:  # above the folder holding the active layer
                target = _unit_end(s.layers, s.active_index) + 1
        name = f"Folder {sum(1 for x in s.layers if x.kind == 'FOLDER') + 1}"
        node.label = name
        e = s.layers.add()
        e.kind = 'FOLDER'
        e.uid = uuid.uuid4().hex[:8]
        e.node_name = node.name
        e.name = name
        s.layers.move(n, target)
        nodes.rebuild_stack(mat)
        s.active_index = target
        return {'FINISHED'}


class ESSENCE_OT_remove_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.remove_layer"
    bl_label = "Remove Layer"
    bl_description = "Remove the active layer, or ungroup and remove the active folder"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and len(mat.essence.layers) > 0

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        i = s.active_index
        e = s.layers[i]
        tree = mat.node_tree
        if e.kind == 'FOLDER':  # removing a folder ungroups its layers
            for c in s.layers:
                if c.parent == e.uid:
                    c.parent = ""
        for n in (e.node_name, e.mask_node_name):
            node = tree.nodes.get(n) if n else None
            if node is not None:
                tree.nodes.remove(node)
        s.layers.remove(i)
        s.active_index = max(0, min(i, len(s.layers) - 1))
        nodes.rebuild_stack(mat)
        return {'FINISHED'}


def _block_start(layers, h):
    """Index of the first child of the folder whose entry is at `h` (`h` if empty)."""
    s = h
    while s > 0 and layers[s - 1].parent == layers[h].uid:
        s -= 1
    return s


def _unit_end(layers, i):
    """Index of the topmost entry of the top-level unit whose lowest entry is `i`: the
    folder entry if `i` is a child, otherwise `i` itself."""
    e = layers[i]
    if not e.parent:
        return i
    j = i
    while layers[j].kind != 'FOLDER' or layers[j].uid != e.parent:
        j += 1
    return j


def _unit_size(layers, i):
    """Size of the top-level unit whose topmost entry is `i`."""
    return i - _block_start(layers, i) + 1 if layers[i].kind == 'FOLDER' else 1


def _move(layers, i, up):
    """Move entry `i` one step up or down. Layers cross folder borders: moving up into
    a folder joins it as its bottom child, moving down into one as its top child, and
    moving past the end leaves it. Folders move as whole blocks. Returns the entry's new
    index, or None if it cannot move."""
    n = len(layers)
    e = layers[i]
    if e.kind == 'FOLDER':
        start = _block_start(layers, i)
        if up:
            if i + 1 >= n:
                return None
            m = _unit_end(layers, i + 1) - i
            for k in range(m):
                layers.move(i + 1 + k, start + k)
            return i + m
        if start == 0:
            return None
        m = _unit_size(layers, start - 1)
        for _ in range(m):
            layers.move(start - m, i)
        return i - m
    if up:
        if i + 1 >= n:
            return None
        nxt = layers[i + 1]
        if e.parent:
            if nxt.parent != e.parent:  # next is the folder entry: step out above it
                e.parent = ""
            layers.move(i, i + 1)
            return i + 1
        if nxt.kind == 'FOLDER' or nxt.parent:  # step into the folder above
            e.parent = nxt.uid if nxt.kind == 'FOLDER' else nxt.parent
            return i
        layers.move(i, i + 1)
        return i + 1
    if i == 0:
        return None
    below = layers[i - 1]
    if e.parent:
        if below.parent == e.parent:
            layers.move(i, i - 1)
            return i - 1
        e.parent = ""  # bottom child steps out below the folder
        return i
    folder_uid = below.uid if below.kind == 'FOLDER' else ""  # read before the move
    layers.move(i, i - 1)
    if folder_uid:  # step into the folder below as its top child
        layers[i - 1].parent = folder_uid
    return i - 1


class ESSENCE_OT_move_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.move_layer"
    bl_label = "Move Layer"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        return mat is not None and len(mat.essence.layers) > 1

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        i = s.active_index
        # The list is stored bottom-to-top; "up" in the UI means a higher index.
        j = _move(s.layers, i, self.direction == 'UP')
        if j is None:
            return {'CANCELLED'}
        nodes.rebuild_stack(mat)
        s.active_index = j
        return {'FINISHED'}


class ESSENCE_OT_add_mask(_Base, bpy.types.Operator):
    bl_idname = "essence.add_mask"
    bl_label = "Add Mask"
    bl_description = "Create an image mask for the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(
        name="Source",
        items=[('PAINT', "Paint", "New blank image to paint on"),
               ('IMAGE', "Image", "Empty slot, pick an existing image afterwards")],
        default='PAINT', options={'HIDDEN'})
    fill: EnumProperty(
        name="Fill",
        items=[('BLACK', "Black (hidden)", "Layer is hidden until painted"),
               ('WHITE', "White (visible)", "Layer is fully visible until erased")],
        default='BLACK')
    size: IntProperty(name="Size", default=1024, min=64, max=8192)

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        if mat is None or not mat.essence.layers:
            return False
        return not mat.essence.layers[mat.essence.active_index].mask_node_name

    def invoke(self, context, event):
        if self.source == 'IMAGE':
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "fill")
        self.layout.prop(self, "size")

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        e = s.layers[s.active_index]
        layer_node = mat.node_tree.nodes.get(e.node_name)
        if layer_node is None:
            self.report({'ERROR'}, "Layer node is missing from the shader graph")
            return {'CANCELLED'}
        img = None
        if self.source == 'PAINT':
            v = 0.0 if self.fill == 'BLACK' else 1.0
            img = bpy.data.images.new(f"{mat.name}_{e.name}_mask", self.size, self.size,
                                      alpha=False)
            img.generated_color = (v, v, v, 1.0)
            img.colorspace_settings.name = 'Non-Color'
            img.pack()
        mask_node = nodes.add_mask_node(mat, layer_node, img)
        e.mask_node_name = mask_node.name
        nodes.rebuild_stack(mat)
        mat.node_tree.nodes.active = mask_node
        return {'FINISHED'}


class ESSENCE_OT_remove_mask(_Base, bpy.types.Operator):
    bl_idname = "essence.remove_mask"
    bl_label = "Remove Mask"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mat = _material(context)
        if mat is None or not mat.essence.layers:
            return False
        return bool(mat.essence.layers[mat.essence.active_index].mask_node_name)

    def execute(self, context):
        mat = _material(context)
        e = mat.essence.layers[mat.essence.active_index]
        node = mat.node_tree.nodes.get(e.mask_node_name)
        if node is not None:
            mat.node_tree.nodes.remove(node)
        e.mask_node_name = ""
        return {'FINISHED'}


class ESSENCE_OT_mask_non_color(_Base, bpy.types.Operator):
    bl_idname = "essence.mask_non_color"
    bl_label = "Set Non-Color"
    bl_description = ("Set the mask image's color space to Non-Color. "
                      "This changes the image itself, so other users of it are affected")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ESSENCE_OT_remove_mask.poll(context)

    def execute(self, context):
        mat = _material(context)
        e = mat.essence.layers[mat.essence.active_index]
        node = mat.node_tree.nodes.get(e.mask_node_name)
        if node is None or node.image is None:
            return {'CANCELLED'}
        node.image.colorspace_settings.name = 'Non-Color'
        return {'FINISHED'}


class ESSENCE_OT_paint_mask(_Base, bpy.types.Operator):
    bl_idname = "essence.paint_mask"
    bl_label = "Paint Mask"
    bl_description = "Enter Texture Paint mode targeting the active layer's mask"

    @classmethod
    def poll(cls, context):
        return ESSENCE_OT_remove_mask.poll(context)

    def execute(self, context):
        ob = context.active_object
        mat = _material(context)
        e = mat.essence.layers[mat.essence.active_index]
        node = mat.node_tree.nodes.get(e.mask_node_name)
        if node is None or node.image is None:
            self.report({'ERROR'}, "Mask image node is missing")
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
    bl_description = "Re-create the layer chain links and refresh the layer node group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context)
        if mat is None or mat.node_tree is None:
            return {'CANCELLED'}
        for e in mat.essence.layers:
            n = mat.node_tree.nodes.get(e.node_name)
            group = nodes.group_for(e)
            if n is not None and n.node_tree is not group:
                n.node_tree = group
        nodes.rebuild_stack(mat)
        return {'FINISHED'}


classes = (
    ESSENCE_OT_add_layer, ESSENCE_OT_add_folder, ESSENCE_OT_remove_layer, ESSENCE_OT_move_layer,
    ESSENCE_OT_add_mask, ESSENCE_OT_remove_mask,
    ESSENCE_OT_mask_non_color, ESSENCE_OT_paint_mask, ESSENCE_OT_exit_paint, ESSENCE_OT_rebuild,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
