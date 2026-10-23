import bpy

from .channels import BY_ID, CHANNELS, MASK_SOURCE, active_channels
from . import nodes, sources
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


def _depth(layers, item):
    """Indent level of a list row: folders 0, their layers 1, masks one more than the
    owner."""
    if item.kind == 'MASK':
        owner = next((e for e in layers if e.uid == item.parent), None)
        return (_depth(layers, owner) if owner is not None else 0) + 1
    return 1 if item.parent else 0


class ESSENCE_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        tree = item.id_data.node_tree
        row = layout.row(align=True)
        depth = _depth(data.layers, item)
        if depth:
            row.separator(factor=2.0 * depth)
        node = tree.nodes.get(item.node_name) if tree is not None else None

        if item.kind == 'MASK':
            img_node = tree.nodes.get(item.mask_node_name) if tree is not None else None
            img = getattr(img_node, "image", None)
            icon_id = 0
            if img is not None:
                try:
                    img.preview_ensure()
                    icon_id = img.preview.icon_id
                except Exception:  # noqa: BLE001
                    pass
            if icon_id:
                row.label(text="", icon_value=icon_id)
            else:
                row.label(text="", icon='MOD_MASK')
            row.prop(item, "name", text="", emboss=False)
            enable = node.inputs.get(nodes.MASK_ENABLE) if node is not None else None
            if enable is not None:
                row.prop(enable, "default_value", text="", emboss=False,
                         icon='HIDE_OFF' if enable.default_value else 'HIDE_ON')
            return

        vis = node.inputs.get(nodes.VISIBLE) if node is not None else None

        def eye():
            if vis is not None:
                row.prop(vis, "default_value", text="", emboss=False,
                         icon='HIDE_OFF' if vis.default_value else 'HIDE_ON')

        if item.kind == 'FOLDER':
            row.prop(item, "name", text="", emboss=False, icon='FILE_FOLDER')
            eye()
            return

        # Layer: a swatch of its color, then the name.
        color = node.inputs.get("Color B") if node is not None else None
        if color is not None and not color.is_linked:
            sub = row.row(align=True)
            sub.ui_units_x = 1.3
            sub.prop(color, "default_value", text="")
        else:
            row.label(text="", icon='NODETREE')
        row.prop(item, "name", text="", emboss=False)
        eye()


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
    if nodes.needs_migration(mat):
        box = layout.box()
        box.label(text="Made with an older Essence", icon='INFO')
        box.operator("essence.rebuild", text="Upgrade Layers", icon='FILE_REFRESH')
    row = layout.row()
    row.template_list("ESSENCE_UL_layers", "", s, "layers", s, "active_index",
                      rows=8, sort_reverse=True, sort_lock=True)
    col = row.column(align=True)
    col.operator("essence.add_layer", icon='ADD', text="")
    col.operator("essence.add_folder", icon='NEWFOLDER', text="")
    col.menu("ESSENCE_MT_add_mask", icon='MOD_MASK', text="")
    col.operator("essence.remove_layer", icon='REMOVE', text="")
    col.separator()
    col.operator("essence.move_layer", icon='TRIA_UP', text="").direction = 'UP'
    col.operator("essence.move_layer", icon='TRIA_DOWN', text="").direction = 'DOWN'
    col.separator()
    col.operator("essence.rebuild", icon='FILE_REFRESH', text="")


def _props_poll(cls, context):
    mat = _mat(context)
    if mat is None or not mat.essence.layers:
        return False
    e, _ = _active(mat)
    return e is not None and e.kind in ('LAYER', 'FOLDER')


def _mask_poll(cls, context):
    mat = _mat(context)
    if mat is None or not mat.essence.layers:
        return False
    e, _ = _active(mat)
    return e is not None and e.kind == 'MASK'


def _mapping(layout, node):
    col = layout.column(align=True)
    col.prop(node.inputs['Scale'], "default_value", text="Tiling")
    col.prop(node.inputs['Location'], "default_value", text="Offset")
    col.prop(node.inputs['Rotation'], "default_value", text="Rotation")


def _channel(layout, mat, e, node, ch):
    """One channel of a layer: enable, source kind, then the controls of that source."""
    src = sources.get_source(e, ch.id)
    box = layout.box()
    enable = node.inputs[f"{ch.label} Enable"]
    row = box.row(align=True)
    if src is not None:
        row.prop(src, "expanded", text="", emboss=False,
                 icon='TRIA_DOWN' if src.expanded else 'TRIA_RIGHT')
    row.prop(enable, "default_value", text="", emboss=False,
             icon='CHECKBOX_HLT' if enable.default_value else 'CHECKBOX_DEHLT')
    row.label(text=ch.label)
    if src is None:
        row.operator("essence.rebuild", text="Upgrade", icon='FILE_REFRESH')
        return
    row.prop(src, "kind", text="")
    # A disabled channel has no effect, so its settings stay out of the way.
    if not src.expanded or not enable.default_value:
        return

    col = box.column(align=True)
    col.active = enable.default_value
    col.prop(src, "blend", text="Blend")
    col.prop(node.inputs[f"{ch.label} Opacity"], "default_value", text="Opacity", slider=True)
    _source_controls(col, mat, e, src, ch, node.inputs[f"{ch.label} B"])


def _source_controls(col, mat, e, src, ch, value_socket, preview=False):
    """The settings of a source (channel or mask): its value, image or procedural, the
    remap or color ramp, then projection and mapping. `preview` draws an image as a
    thumbnail grid, like the mask picker."""
    found = sources.source_nodes(mat.node_tree, e.uid, ch.id)
    if src.kind == 'VALUE':
        if value_socket.is_linked:
            col.label(text="Driven by nodes in the node editor", icon='NODETREE')
        else:
            col.prop(value_socket, "default_value", text="Value")
        return

    tex = found.get('TEXTURE')
    if tex is None or 'MAPPING' not in found:
        col.operator("essence.rebuild", text="Source nodes missing, rebuild",
                     icon='FILE_REFRESH')
        return
    if src.kind == 'IMAGE':
        if preview:
            col.template_ID_preview(tex, "image", open="image.open", rows=3, cols=5)
        else:
            col.template_ID(tex, "image", open="image.open")
        if tex.image is None:
            if preview:
                col.label(text="Pick an image above", icon='INFO')
        elif tex.image.colorspace_settings.name != ch.colorspace:
            sub = col.box()
            sub.label(text=f"This {'mask' if preview else 'channel'} expects {ch.colorspace}",
                      icon='ERROR')
            sub.operator("essence.source_colorspace",
                         text=f"Set {ch.colorspace}").channel = ch.id
    else:
        col.prop(src, "proc_type", text="Type")
        for s in tex.inputs:
            if s.name != "Vector" and not s.is_linked:
                col.prop(s, "default_value", text=s.name)
    remap = found.get('REMAP')
    if remap is not None:
        sub = col.column(align=True)
        sub.prop(remap.inputs['To Min'], "default_value", text="Min")
        sub.prop(remap.inputs['To Max'], "default_value", text="Max")
    ramp = found.get('RAMP')
    if ramp is not None:
        col.template_color_ramp(ramp, "color_ramp")
    col.separator()
    col.prop(src, "projection", expand=True)
    col.prop(src, "show_transform", emboss=False,
             icon='TRIA_DOWN' if src.show_transform else 'TRIA_RIGHT')
    if src.show_transform:
        _mapping(col, found['MAPPING'])


def _props(self, context):
    layout = self.layout
    mat = _mat(context)
    e, node = _active(mat)
    if node is None:
        layout.label(text="Layer node missing from graph", icon='ERROR')
        return
    layout.prop(e, "name", text="Name")
    if e.kind == 'FOLDER':
        layout.label(text="The folder's masks limit all layers inside", icon='INFO')
    elif nodes.OPACITY not in node.inputs:
        layout.operator("essence.rebuild", text="Upgrade Layers", icon='FILE_REFRESH')
    else:
        layout.prop(node.inputs[nodes.OPACITY], "default_value", text="Opacity", slider=True)
        for ch in active_channels(mat):
            _channel(layout, mat, e, node, ch)
    n = sum(1 for m in mat.essence.layers if m.kind == 'MASK' and m.parent == e.uid)
    layout.menu("ESSENCE_MT_add_mask", icon='MOD_MASK',
                text="Add Mask" if n == 0 else f"Add Mask ({n})")


def _mask(self, context):
    layout = self.layout
    mat = _mat(context)
    e, node = _active(mat)
    tree = mat.node_tree
    owner = next((x for x in mat.essence.layers if x.uid == e.parent), None)
    if node is None:
        layout.label(text="Mask node missing from graph", icon='ERROR')
        layout.operator("essence.remove_layer", icon='X')
        return
    layout.prop(e, "name", text="Name")
    if owner is not None:
        layout.label(text=f"Masks {owner.name}", icon='MOD_MASK')
    src = sources.get_source(e, 'MASK')
    if src is None:
        layout.operator("essence.rebuild", text="Upgrade Layers", icon='FILE_REFRESH')
        return
    layout.prop(src, "kind", text="Source")
    # An image source shows a thumbnail grid of every image in the file, with Open and unlink.
    col = layout.column()
    _source_controls(col, mat, e, src, MASK_SOURCE, node.inputs[nodes.MASK_VALUE],
                     preview=True)
    img = getattr(tree.nodes.get(e.mask_node_name), "image", None) if tree else None
    if nodes.MASK_INVERT in node.inputs:
        col = layout.column(align=True)
        col.prop(node.inputs[nodes.MASK_INVERT], "default_value", text="Invert", toggle=True)
        col.prop(node.inputs[nodes.MASK_BRIGHTNESS], "default_value", text="Brightness")
        col.prop(node.inputs[nodes.MASK_CONTRAST], "default_value", text="Contrast")
    else:
        layout.operator("essence.rebuild", text="Upgrade Layers", icon='FILE_REFRESH')
    if context.active_object.mode == 'TEXTURE_PAINT':
        layout.operator("essence.exit_paint", icon='OBJECT_DATAMODE')
    else:
        row = layout.row()
        row.enabled = img is not None and src.kind == 'IMAGE' and src.projection == 'UV'
        row.operator("essence.paint_mask", icon='BRUSH_DATA')
        if src.kind == 'IMAGE' and src.projection != 'UV':
            layout.label(text="Painting needs the UV projection", icon='INFO')
    layout.operator("essence.remove_layer", text="Remove Mask", icon='X')


def _material(self, context):
    layout = self.layout
    mat = _mat(context)
    if mat is None or mat.node_tree is None:
        return
    tree = mat.node_tree
    if mat.essence.channels:
        box = layout.box()
        box.label(text="Channels")
        grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for state in mat.essence.channels:
            if state.id in BY_ID:  # a file from an older Essence may list a removed channel
                grid.prop(state, "enabled", text=BY_ID[state.id].label)
        on = {c.id for c in active_channels(mat)}
        bsdf = next((n for n in tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled'), None)
        if 'EMISSION' in on and bsdf is not None:
            box.prop(bsdf.inputs['Emission Strength'], "default_value",
                     text="Emission Strength")
    bump = tree.nodes.get(nodes.BUMP_NODE_NAME)
    if bump is None:
        layout.label(text="Add a layer first")
        return
    col = layout.column(align=True)
    if bump is not None:
        col.prop(bump.inputs['Strength'], "default_value", text="Bump Strength")
        col.prop(bump.inputs['Distance'], "default_value", text="Bump Distance")


def _maps(self, context):
    """The mesh map slots: baked here or picked from any image."""
    layout = self.layout
    mat = _mat(context)
    if mat is None or mat.node_tree is None:
        return
    tree = mat.node_tree
    found = False
    for map_id, label in nodes.MAP_SLOTS:
        node = tree.nodes.get(nodes.map_slot_name(map_id))
        if node is None:
            continue
        found = True
        box = layout.box()
        box.label(text=label)
        box.template_ID(node, "image", open="image.open")
        if node.image is not None and node.image.colorspace_settings.name != 'Non-Color':
            box.label(text="Maps should be Non-Color", icon='ERROR')
            box.operator("essence.map_non_color").map_id = map_id
        if map_id == 'NORMAL':
            nm = tree.nodes.get(nodes.NORMAL_NODE_NAME)
            if nm is not None:
                box.prop(nm.inputs['Strength'], "default_value", text="Strength")
        elif map_id == 'AO':
            ao = tree.nodes.get(nodes.AO_STRENGTH_NODE)
            if ao is not None:
                factor = next(i for i in ao.inputs if i.identifier == "Factor_Float")
                box.prop(factor, "default_value", text="Strength", slider=True)
    if not found:
        layout.label(text="Add a layer first")


def _bake(self, context):
    layout = self.layout
    scene = context.scene
    layout.template_icon_view(scene, "essence_bake_map", show_labels=True,
                              scale=6, scale_popup=5)
    if scene.essence_bake_map in HIGH_MAPS:
        box = layout.box()
        box.prop(scene, "essence_bake_high", text="High Poly")
        if scene.essence_bake_high is not None:
            box.prop(scene, "essence_bake_cage", text="Cage")
            if scene.essence_bake_cage is None:
                box.prop(scene, "essence_bake_extrusion", text="Extrusion")
            box.label(text="Bakes selected low-poly objects", icon='INFO')
    col = layout.column(align=True)
    col.prop(scene, "essence_bake_size", text="Size")
    col.prop(scene, "essence_bake_samples", text="Samples")
    col.prop(scene, "essence_bake_margin", text="Margin")
    col.prop(scene, "essence_bake_aa", text="Anti-aliasing")
    col.prop(scene, "essence_bake_float")
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
    ("PROPS", "Layer Properties", _props, _props_poll),
    ("MASK", "Mask Properties", _mask, _mask_poll),
    ("MAPS", "Mesh Maps", _maps, None),
    ("MATERIAL", "Material", _material, None),
    ("BAKE", "Bake", _bake, None),
    ("EXPORT", "Export", _export, None),
)

classes = [ESSENCE_UL_layers]

for space, tag in SPACES:
    for key, label, draw, poll in _PANELS:
        attrs = {
            "bl_idname": f"ESSENCE_PT_{key.lower()}_{tag.lower()}",
            "bl_label": label,
            "bl_space_type": space,
            "bl_region_type": 'UI',
            "bl_category": "Essence",
            "draw": draw,
        }
        if poll is not None:
            attrs["poll"] = classmethod(poll)
        cls = type(f"ESSENCE_PT_{key.lower()}_{tag.lower()}", (bpy.types.Panel,), attrs)
        classes.append(cls)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
