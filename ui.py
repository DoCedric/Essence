import bpy

from .channels import CHANNELS
from . import nodes
from .bake import HIGH_MAPS

SPACES = (('VIEW_3D', 'VIEW3D'), ('IMAGE_EDITOR', 'IMAGE'))


def _mat(context):
    ob = context.active_object
    if ob is None or ob.type != 'MESH':
        return None
    return ob.active_material


def _active(mat):
    s = mat.essence
    if not 0 <= s.active_index < len(s.layers):
        return None, None
    e = s.layers[s.active_index]
    node = mat.node_tree.nodes.get(e.node_name) if mat.node_tree else None
    return e, node


class ESSENCE_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        if item.parent:
            row.separator(factor=2.0)
        row.prop(item, "name", text="", emboss=False,
                 icon='FILE_FOLDER' if item.kind == 'FOLDER' else 'NODETREE')
        node = None
        tree = item.id_data.node_tree
        if item.mask_node_name and tree is not None:
            node = tree.nodes.get(item.mask_node_name)
        img = getattr(node, "image", None)
        if img is not None:
            try:
                img.preview_ensure()
                row.label(text="", icon_value=img.preview.icon_id)
                return
            except Exception:  # noqa: BLE001
                pass
        row.label(text="", icon='MOD_MASK' if item.mask_node_name else 'BLANK1')


def _stack(self, context):
    layout = self.layout
    ob = context.active_object
    if ob is None or ob.type != 'MESH':
        layout.label(text="Select a mesh object")
        return
    mat = ob.active_material
    if mat is None or not mat.essence.layers:
        col = layout.column(align=True)
        col.operator("essence.add_layer", icon='ADD')
        col.operator("essence.add_folder", icon='NEWFOLDER')
        return
    s = mat.essence
    row = layout.row()
    row.template_list("ESSENCE_UL_layers", "", s, "layers", s, "active_index",
                      rows=5, sort_reverse=True, sort_lock=True)
    col = row.column(align=True)
    col.operator("essence.add_layer", icon='ADD', text="")
    col.operator("essence.add_folder", icon='NEWFOLDER', text="")
    col.operator("essence.remove_layer", icon='REMOVE', text="")
    col.separator()
    col.operator("essence.move_layer", icon='TRIA_UP', text="").direction = 'UP'
    col.operator("essence.move_layer", icon='TRIA_DOWN', text="").direction = 'DOWN'
    col.separator()
    col.operator("essence.rebuild", icon='FILE_REFRESH', text="")


def _props(self, context):
    layout = self.layout
    mat = _mat(context)
    if mat is None or not mat.essence.layers:
        return
    e, node = _active(mat)
    if e is None:
        return
    if node is None:
        layout.label(text="Layer node missing from graph", icon='ERROR')
        return
    layout.prop(e, "name", text="Name")
    if e.kind == 'FOLDER':
        layout.label(text="Folder: the mask limits all layers inside", icon='INFO')
        return
    col = layout.column(align=True)
    for ch in CHANNELS:
        enable = node.inputs[f"{ch.label} Enable"]
        row = col.row(align=True)
        row.prop(enable, "default_value", text="",
                 icon='CHECKBOX_HLT' if enable.default_value else 'CHECKBOX_DEHLT',
                 emboss=False)
        sub = row.row(align=True)
        sub.active = enable.default_value
        sub.prop(node.inputs[f"{ch.label} B"], "default_value", text=ch.label)


def _mask(self, context):
    layout = self.layout
    mat = _mat(context)
    if mat is None or not mat.essence.layers:
        return
    e, node = _active(mat)
    if e is None or node is None:
        return
    if not e.mask_node_name:
        col = layout.column(align=True)
        col.operator("essence.add_mask", text="Paint Mask", icon='BRUSH_DATA').source = 'PAINT'
        col.operator("essence.add_mask", text="Image Mask", icon='IMAGE_DATA').source = 'IMAGE'
        return
    mask_node = mat.node_tree.nodes.get(e.mask_node_name)
    if mask_node is None:
        layout.label(text="Mask node missing from graph", icon='ERROR')
        return
    # Thumbnail grid of every image in the file, with Open (load from disk) and unlink.
    layout.template_ID_preview(mask_node, "image", open="image.open", rows=3, cols=5)
    img = mask_node.image
    if img is None:
        layout.label(text="Pick an image above", icon='INFO')
    elif img.colorspace_settings.name != 'Non-Color':
        box = layout.box()
        box.label(text="Masks should be Non-Color", icon='ERROR')
        box.operator("essence.mask_non_color")
    if nodes.MASK_INVERT in node.inputs:
        col = layout.column(align=True)
        col.prop(node.inputs[nodes.MASK_INVERT], "default_value", text="Invert", toggle=True)
        col.prop(node.inputs[nodes.MASK_BRIGHTNESS], "default_value", text="Brightness")
        col.prop(node.inputs[nodes.MASK_CONTRAST], "default_value", text="Contrast")
    else:
        layout.operator("essence.rebuild", text="Upgrade Layer Group", icon='FILE_REFRESH')
    if context.active_object.mode == 'TEXTURE_PAINT':
        layout.operator("essence.exit_paint", icon='OBJECT_DATAMODE')
    else:
        row = layout.row()
        row.enabled = img is not None
        row.operator("essence.paint_mask", icon='BRUSH_DATA')
    layout.operator("essence.remove_mask", icon='X')


def _material(self, context):
    layout = self.layout
    mat = _mat(context)
    if mat is None or mat.node_tree is None:
        return
    tree = mat.node_tree
    normal = tree.nodes.get(nodes.NORMAL_NODE_NAME)
    bump = tree.nodes.get(nodes.BUMP_NODE_NAME)
    if normal is None and bump is None:
        layout.label(text="Add a layer or bake a normal map first")
        return
    col = layout.column(align=True)
    if normal is not None:
        col.prop(normal.inputs['Strength'], "default_value", text="Normal Map Strength")
    if bump is not None:
        col.prop(bump.inputs['Strength'], "default_value", text="Bump Strength")
        col.prop(bump.inputs['Distance'], "default_value", text="Bump Distance")


def _bake(self, context):
    layout = self.layout
    scene = context.scene
    layout.template_icon_view(scene, "essence_bake_map", show_labels=True,
                              scale=6, scale_popup=5)
    if scene.essence_bake_map in HIGH_MAPS:
        box = layout.box()
        box.prop(scene, "essence_bake_high", text="High Poly")
        if scene.essence_bake_high is not None:
            box.prop(scene, "essence_bake_extrusion", text="Extrusion")
            box.label(text="Bakes selected low-poly objects", icon='INFO')
    col = layout.column(align=True)
    col.prop(scene, "essence_bake_size", text="Size")
    col.prop(scene, "essence_bake_samples", text="Samples")
    col.prop(scene, "essence_bake_margin", text="Margin")
    layout.operator("essence.bake", icon='RENDER_STILL')


def _export(self, context):
    layout = self.layout
    scene = context.scene
    col = layout.column(align=True)
    col.prop(scene, "essence_export_format", text="Format")
    col.prop(scene, "essence_export_size", text="Resolution")
    layout.prop(scene, "essence_export_dir", text="Folder")
    box = layout.box()
    col = box.column(align=True)
    col.label(text="Unity (HDRP)")
    col.label(text="BaseMap, MaskMap, Normal")
    col.label(text="MaskMap: R Metal, G AO, A Smooth")
    layout.operator("essence.export_unity", icon='EXPORT')


_PANELS = (
    ("LAYERS", "Layers", _stack, None),
    ("PROPS", "Layer Properties", _props, None),
    ("MASK", "Mask", _mask, None),
    ("MATERIAL", "Material", _material, None),
    ("BAKE", "Bake", _bake, None),
    ("EXPORT", "Export", _export, None),
)

classes = [ESSENCE_UL_layers]

for space, tag in SPACES:
    for key, label, draw, _ in _PANELS:
        cls = type(
            f"ESSENCE_PT_{key.lower()}_{tag.lower()}",
            (bpy.types.Panel,),
            {
                "bl_idname": f"ESSENCE_PT_{key.lower()}_{tag.lower()}",
                "bl_label": label,
                "bl_space_type": space,
                "bl_region_type": 'UI',
                "bl_category": "Essence",
                "draw": draw,
            },
        )
        classes.append(cls)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
