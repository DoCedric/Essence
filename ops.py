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
        e = s.layers.add()
        e.node_name = node.name
        e.name = name
        # Insert above the active layer.
        target = min(s.active_index + 1, len(s.layers) - 1) if len(s.layers) > 1 else 0
        s.layers.move(len(s.layers) - 1, target)
        nodes.rebuild_stack(mat)
        s.active_index = target
        return {'FINISHED'}


class ESSENCE_OT_remove_layer(_Base, bpy.types.Operator):
    bl_idname = "essence.remove_layer"
    bl_label = "Remove Layer"
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
        for n in (e.node_name, e.mask_node_name):
            node = tree.nodes.get(n) if n else None
            if node is not None:
                tree.nodes.remove(node)
        s.layers.remove(i)
        s.active_index = max(0, min(i, len(s.layers) - 1))
        nodes.rebuild_stack(mat)
        return {'FINISHED'}


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
        j = i + 1 if self.direction == 'UP' else i - 1
        if not 0 <= j < len(s.layers):
            return {'CANCELLED'}
        s.layers.move(i, j)
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
        group = nodes.ensure_layer_group()
        for e in mat.essence.layers:
            n = mat.node_tree.nodes.get(e.node_name)
            if n is not None and n.node_tree is not group:
                n.node_tree = group
        nodes.rebuild_stack(mat)
        return {'FINISHED'}


classes = (
    ESSENCE_OT_add_layer, ESSENCE_OT_remove_layer, ESSENCE_OT_move_layer,
    ESSENCE_OT_add_mask, ESSENCE_OT_remove_mask,
    ESSENCE_OT_mask_non_color, ESSENCE_OT_paint_mask, ESSENCE_OT_exit_paint, ESSENCE_OT_rebuild,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
