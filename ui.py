import bpy

from .channels import BY_ID, CHANNELS, MASK_SOURCE, active_channels
from . import adjustments, curvature, filters, generators, gizmo, nodes, sources
from .bake import HIGH_MAPS, resolve_device

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
    if item.kind in ('MASK', 'ADJUSTMENT', 'FILL', 'FILTER'):
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

        if item.kind == 'FILTER':
            row.label(text="", icon=_filter_icon(item.id_data, item))
            row.prop(item, "name", text="", emboss=False)
            row.prop(item, "adj_enabled", text="", emboss=False,
                     icon='HIDE_OFF' if item.adj_enabled else 'HIDE_ON')
            return

        if item.kind == 'FILL':
            icon_id = _thumb(tree, item)
            if icon_id:
                row.label(text="", icon_value=icon_id)
            else:
                row.label(text="", icon='BRUSH_DATA' if item.fill_paint else 'IMAGE_DATA')
            row.prop(item, "name", text="", emboss=False)
            row.prop(item, "adj_enabled", text="", emboss=False,
                     icon='HIDE_OFF' if item.adj_enabled else 'HIDE_ON')
            return

        if item.kind == 'ADJUSTMENT':
            row.label(text="", icon='MODIFIER')
            row.prop(item, "name", text="", emboss=False)
            row.prop(item, "adj_enabled", text="", emboss=False,
                     icon='HIDE_OFF' if item.adj_enabled else 'HIDE_ON')
            return

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


def _thumb(tree, item):
    """Icon id of the preview of a mask's or fill's image, 0 if it has none. A paint item has
    none on purpose: its image is mostly transparent, so its preview is an empty speck, and the
    brush icon says more."""
    if item.kind == 'FILL' and item.fill_paint:
        return 0
    node = tree.nodes.get(item.mask_node_name) if tree is not None and item.mask_node_name else None
    img = getattr(node, "image", None)
    if img is None:
        return 0
    try:
        img.preview_ensure()
        return img.preview.icon_id
    except Exception:  # noqa: BLE001
        return 0


def _filter_icon(mat, item):
    """Red dot: nothing baked yet or out of date. Green dot: the baked result is current."""
    if filters.is_baked(item) and not filters.is_stale(mat, item):
        return 'STRIP_COLOR_04'
    return 'STRIP_COLOR_01'


def _entry_row(layout, mat, item, active, add_mask=False, foldable=False):
    """One entry of the tree view: a fold triangle when something hangs under it, its icon, a
    button that selects it (pressed while it is the active one) and its eye toggle."""
    tree = mat.node_tree
    node = tree.nodes.get(item.node_name) if tree is not None else None
    row = layout.row(align=True)
    if foldable:
        row.prop(item, "collapsed", text="", emboss=False,
                 icon='TRIA_RIGHT' if item.collapsed else 'TRIA_DOWN')
    toggle = None
    if item.kind == 'LAYER':
        color = node.inputs.get("Color B") if node is not None else None
        if color is not None and not color.is_linked:
            sub = row.row(align=True)
            sub.ui_units_x = 1.3
            sub.prop(color, "default_value", text="")
        else:
            row.label(text="", icon='NODETREE')
        toggle = node.inputs.get(nodes.VISIBLE) if node is not None else None
    elif item.kind == 'FOLDER':
        row.label(text="", icon='FILE_FOLDER')
        toggle = node.inputs.get(nodes.VISIBLE) if node is not None else None
    elif item.kind in ('MASK', 'FILL'):
        icon_id = _thumb(tree, item)
        if icon_id:
            row.label(text="", icon_value=icon_id)
        else:
            row.label(text="", icon='MOD_MASK' if item.kind == 'MASK'
                      else 'BRUSH_DATA' if item.fill_paint else 'IMAGE_DATA')
        if item.kind == 'MASK':
            toggle = node.inputs.get(nodes.MASK_ENABLE) if node is not None else None
    elif item.kind == 'FILTER':
        row.label(text="", icon=_filter_icon(mat, item))
    else:
        row.label(text="", icon='MODIFIER')
    row.operator("essence.select_entry", text=item.name, depress=active).uid = item.uid
    if item.kind in ('ADJUSTMENT', 'FILL', 'FILTER'):
        row.prop(item, "adj_enabled", text="", emboss=False,
                 icon='HIDE_OFF' if item.adj_enabled else 'HIDE_ON')
    elif toggle is not None:
        row.prop(toggle, "default_value", text="", emboss=False,
                 icon='HIDE_OFF' if toggle.default_value else 'HIDE_ON')
    if add_mask:  # a layer or folder without a mask: a button for a white or black one
        row.operator("essence.add_mask_menu", text="", icon='MOD_MASK').uid = item.uid


def _tree(layout, mat):
    """The stack as boxes, top of the stack first. A layer's box has the layer and its
    adjustments on the left and its masks, each with its fills and adjustments, on the right."""
    s = mat.essence
    active = s.layers[s.active_index].uid if 0 <= s.active_index < len(s.layers) else ""
    items, masks, kids, roots = {}, {}, {}, []
    for e in s.layers:  # stored bottom-to-top
        if e.kind in ('ADJUSTMENT', 'FILL', 'FILTER'):
            items.setdefault(e.parent, []).append(e)
        elif e.kind == 'MASK':
            masks.setdefault(e.parent, []).append(e)
        elif e.kind == 'LAYER' and e.parent:
            kids.setdefault(e.parent, []).append(e)
        else:
            roots.append(e)

    def draw_items(col, owner, hidden=False):
        if hidden:
            return
        for it in reversed(items.get(owner.uid, [])):
            row = col.row()
            row.separator(factor=1.5)
            _entry_row(row, mat, it, it.uid == active)

    def draw_masks(col, owner, hidden=False):
        for m in reversed(masks.get(owner.uid, [])):
            _entry_row(col, mat, m, m.uid == active)
            draw_items(col, m, hidden)

    def mask_items(owner):
        return any(items.get(m.uid) for m in masks.get(owner.uid, []))

    def foldable(e):
        """Something hangs under it that the triangle can hide."""
        if e.kind == 'FOLDER':
            return bool(kids.get(e.uid)) or mask_items(e)
        return bool(items.get(e.uid)) or mask_items(e)

    def draw_layer(col, e):
        box = col.box()
        if masks.get(e.uid):
            split = box.split(factor=0.5)
            left, right = split.column(align=True), split.column(align=True)
        else:
            left, right = box.column(align=True), None
        _entry_row(left, mat, e, e.uid == active, add_mask=right is None, foldable=foldable(e))
        draw_items(left, e, e.collapsed)
        if right is not None:
            draw_masks(right, e, e.collapsed)

    def draw_folder(col, e):
        box = col.box()
        if masks.get(e.uid):  # like a layer: the folder on the left, its masks on the right
            split = box.split(factor=0.5)
            left, right = split.column(align=True), split.column(align=True)
            draw_masks(right, e, e.collapsed)
        else:
            left = box.column(align=True)
        _entry_row(left, mat, e, e.uid == active, add_mask=not masks.get(e.uid),
                   foldable=foldable(e))
        for child in ([] if e.collapsed else reversed(kids.get(e.uid, []))):
            row = box.split(factor=0.04)
            row.label(text="")
            draw_layer(row.column(), child)

    for e in reversed(roots):
        (draw_folder if e.kind == 'FOLDER' else draw_layer)(layout, e)


def _apply_all_button(layout, mat):
    """Apply All under the stack when the material has filters: red while any filter needs
    applying, a green dot when all are current."""
    if not any(e.kind == 'FILTER' for e in mat.essence.layers):
        return
    dirty = filters.any_unapplied(mat)
    row = layout.row(align=True)
    row.alert = dirty
    row.operator("essence.filter_apply_all", text="Apply All Filters",
                 icon='STRIP_COLOR_01' if dirty else 'STRIP_COLOR_04')


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
    if s.use_tree:
        bar = layout.row(align=True)
        bar.operator("essence.add_layer", icon='ADD', text="")
        bar.operator("essence.add_folder", icon='NEWFOLDER', text="")
        bar.menu("ESSENCE_MT_add_mask", icon='MOD_MASK', text="")
        bar.menu("ESSENCE_MT_add_adjustment", icon='MODIFIER', text="")
        bar.operator("essence.remove_layer", icon='REMOVE', text="")
        bar.separator()
        bar.operator("essence.move_layer", icon='TRIA_UP', text="").direction = 'UP'
        bar.operator("essence.move_layer", icon='TRIA_DOWN', text="").direction = 'DOWN'
        bar.separator()
        bar.operator("essence.rebuild", icon='FILE_REFRESH', text="")
        bar.prop(s, "use_tree", text="", icon='OUTLINER')
        _tree(layout, mat)
        _apply_all_button(layout, mat)
        return
    row = layout.row()
    row.template_list("ESSENCE_UL_layers", "", s, "layers", s, "active_index",
                      rows=8, sort_reverse=True, sort_lock=True)
    col = row.column(align=True)
    col.operator("essence.add_layer", icon='ADD', text="")
    col.operator("essence.add_folder", icon='NEWFOLDER', text="")
    col.menu("ESSENCE_MT_add_mask", icon='MOD_MASK', text="")
    col.menu("ESSENCE_MT_add_adjustment", icon='MODIFIER', text="")
    col.operator("essence.remove_layer", icon='REMOVE', text="")
    col.separator()
    col.operator("essence.move_layer", icon='TRIA_UP', text="").direction = 'UP'
    col.operator("essence.move_layer", icon='TRIA_DOWN', text="").direction = 'DOWN'
    col.separator()
    col.operator("essence.rebuild", icon='FILE_REFRESH', text="")
    col.prop(s, "use_tree", text="", icon='OUTLINER')
    _apply_all_button(layout, mat)


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


def _adjustment_poll(cls, context):
    mat = _mat(context)
    if mat is None or not mat.essence.layers:
        return False
    e, _ = _active(mat)
    return e is not None and e.kind in ('ADJUSTMENT', 'FILL', 'FILTER')


def _filter(layout, mat, e, owner):
    """A baked filter: its type, the compositor node's settings, resolution and Apply (red when
    the baked result is missing or out of date, a green dot when it is current)."""
    if owner is not None:
        layout.label(text=f"Filters {owner.name} (baked)", icon='FILTER')
    layout.prop(e, "filter_type", text="Filter")
    on_layer = owner is not None and owner.kind == 'LAYER'
    if on_layer:
        layout.prop(e, "adj_channel", text="Channel")
    layout.prop(e, "adj_enabled", text="Enabled")
    node = filters.filter_node(e)
    if node is not None:
        t = filters.BY_ID[e.filter_type]
        col = layout.column(align=True)
        for sock in node.inputs:
            if sock.name != t.input and hasattr(sock, "default_value"):
                col.prop(sock, "default_value", text=sock.name)
    layout.prop(e, "filter_resolution", text="Resolution")
    if on_layer:
        layout.label(text="Baked in UV space: tiling and projection are baked in", icon='INFO')
        if (filters.BY_ID[e.filter_type].single_value
                and BY_ID[e.adj_channel].socket_type == 'NodeSocketColor'):
            layout.label(text="Dilate / Erode filters the brightness of a color", icon='ERROR')
    baked = filters.is_baked(e)
    current = baked and not filters.is_stale(mat, e)
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.alert = not current   # Blender's own red: something to apply
    row.operator("essence.filter_apply", text="Apply",
                 icon='STRIP_COLOR_04' if current else 'STRIP_COLOR_01').uid = e.uid
    if not baked:
        layout.label(text="Not applied yet: passes the value through", icon='INFO')


def _paint_item(layout, mat, e, src):
    """A paint item: its image (the mask value you paint, with alpha) and the Paint button."""
    layout.label(text="Paints onto the mask at this point of its stack", icon='BRUSH_DATA')
    tex = sources.source_nodes(mat.node_tree, e.uid, 'MASK').get('TEXTURE')
    if tex is None or tex.bl_idname != 'ShaderNodeTexImage':
        layout.operator("essence.rebuild", text="Paint image missing, rebuild", icon='FILE_REFRESH')
        return
    layout.template_ID(tex, "image", open="image.open")
    if bpy.context.active_object is not None and bpy.context.active_object.mode == 'TEXTURE_PAINT':
        layout.operator("essence.exit_paint", icon='OBJECT_DATAMODE')
    else:
        row = layout.row()
        row.enabled = tex.image is not None and src.projection == 'UV'
        row.operator("essence.paint_mask", text="Paint", icon='BRUSH_DATA')
    layout.label(text="White reveals, black hides, unpainted does nothing", icon='INFO')


def _fill(layout, mat, e, owner):
    """A mask's fill: a source of its own, blended onto the mask value below it."""
    tree = mat.node_tree
    if owner is not None:
        layout.label(text=f"Fills {owner.name}", icon='IMAGE_DATA')
    layout.prop(e, "fill_blend", text="Blend")
    layout.prop(e, "adj_enabled", text="Enabled")
    found = sources.source_nodes(tree, e.uid, adjustments.ADJ_KEY) if tree else {}
    main, mix = found.get('MAIN'), found.get('MIX')
    if main is None or mix is None:
        layout.operator("essence.rebuild", text="Fill nodes missing, rebuild", icon='FILE_REFRESH')
        return
    layout.prop(adjustments.mix_socket(mix, "Factor_Float"), "default_value",
                text="Opacity", slider=True)
    src = sources.get_source(e, 'MASK')
    if src is None:
        return
    if e.fill_paint:
        _paint_item(layout, mat, e, src)
        return
    layout.prop(src, "kind", text="Source")
    col = layout.column()
    _source_controls(col, mat, e, src, MASK_SOURCE, main.inputs[1], preview=True)


def _adjustment(self, context):
    layout = self.layout
    mat = _mat(context)
    e, _ = _active(mat)
    tree = mat.node_tree
    owner = next((x for x in mat.essence.layers if x.uid == e.parent), None)
    layout.prop(e, "name", text="Name")
    if e.kind == 'FILL':
        _fill(layout, mat, e, owner)
        return
    if e.kind == 'FILTER':
        _filter(layout, mat, e, owner)
        return
    if owner is not None:
        layout.label(text=f"Adjusts {owner.name}", icon='MODIFIER')
    layout.prop(e, "adj_type", text="Type")
    if owner is not None and owner.kind == 'LAYER':
        layout.prop(e, "adj_channel", text="Channel")
    layout.prop(e, "adj_enabled", text="Enabled")
    found = sources.source_nodes(tree, e.uid, adjustments.ADJ_KEY) if tree else {}
    main, mix = found.get('MAIN'), found.get('MIX')
    if main is None or mix is None:
        layout.operator("essence.rebuild", text="Adjustment nodes missing, rebuild",
                        icon='FILE_REFRESH')
        return
    t = adjustments.BY_ID[e.adj_type]
    layout.prop(adjustments.mix_socket(mix, "Factor_Float"), "default_value",
                text="Opacity", slider=True)
    col = layout.column(align=True)
    for ident, label in t.params:
        # Node sockets match by identifier; a group node's identifiers are generated, so by name.
        sock = next((s for s in main.inputs if s.identifier == ident or s.name == ident), None)
        if sock is not None:
            col.prop(sock, "default_value", text=label)
    if t.widget == 'curve':
        layout.template_curve_mapping(main, "mapping", type='COLOR')
    elif t.widget == 'ramp':
        layout.template_color_ramp(main, "color_ramp")


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
    generator = src.kind == 'GENERATOR'
    if tex is None or ('MAPPING' not in found and not generator):
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
    elif generator:
        col.prop(src, "gen_type", text="Generator")
        arrow = gizmo.is_active(tex)
        for map_id in generators.GENERATORS[src.gen_type].needs:
            slot = mat.node_tree.nodes.get(nodes.map_slot_name(map_id))
            if slot is None or slot.bl_idname != 'ShaderNodeTexImage' or slot.image is None:
                label = dict(nodes.MAP_SLOTS)[map_id]
                col.label(text=f"Needs the {label} map (see Mesh Maps)", icon='ERROR')
        for s in tex.inputs:
            if s.name in generators.INTERNAL_INPUTS or s.is_linked:
                continue
            if s.name == generators.ROTATION_INPUT:
                col.prop(s, "default_value", text="Rotation", slider=True)
                op = col.operator("essence.light_gizmo",
                                  text="Remove Gizmo" if arrow else "Show Gizmo",
                                  icon='EMPTY_ARROWS', depress=arrow)
                op.uid, op.channel = e.uid, ch.id
            elif s.name == generators.PICK_INPUT:
                col.prop(s, "default_value", text="ID Color")
                op = col.operator("essence.pick_id", icon='EYEDROPPER')
                op.uid, op.channel = e.uid, ch.id
                col.operator("essence.show_map", icon='IMAGE_DATA').map_id = 'ID'
            elif s.name == "World Space" and arrow:
                sub = col.row()
                sub.enabled = False     # the arrow is parented for one space: remove it to switch
                sub.prop(s, "default_value", text=s.name)
            else:
                col.prop(s, "default_value", text=s.name)
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
    if generator:
        return       # it reads the mesh: no projection or mapping
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
    else:
        layout.prop(node.inputs[nodes.OPACITY], "default_value", text="Opacity", slider=True)
        for ch in active_channels(mat):
            _channel(layout, mat, e, node, ch)


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
        return
    layout.prop(src, "kind", text="Source")
    # An image source shows a thumbnail grid of every image in the file, with Open and unlink.
    col = layout.column()
    _source_controls(col, mat, e, src, MASK_SOURCE, node.inputs[nodes.MASK_VALUE],
                     preview=True)
    img = getattr(tree.nodes.get(e.mask_node_name), "image", None) if tree else None
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
    missing = False
    for map_id, label in nodes.MAP_SLOTS:
        node = tree.nodes.get(nodes.map_slot_name(map_id))
        if node is None:
            missing = True
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
        elif map_id == 'CURVATURE':
            _curvature(box, mat)
    if not found:
        layout.label(text="Add a layer first")
    elif missing:    # a file from before some slots existed: a rebuild adds them
        layout.operator("essence.rebuild", text="Add the new map slots", icon='FILE_REFRESH')


def _curvature(box, mat):
    """The Derive button of the Curvature slot: red while the result is missing or out of date,
    a green dot when it is current."""
    s = mat.essence
    col = box.column(align=True)
    col.prop(s, "curvature_width")
    col.prop(s, "curvature_strength")
    has_normal = curvature.normal_image(mat) is not None
    current = has_normal and curvature.is_derived(mat) and not curvature.needs_derive(mat)
    row = box.row(align=True)
    row.scale_y = 1.3
    row.enabled = has_normal
    row.alert = has_normal and not current
    row.operator("essence.derive_curvature", text="Derive from Normal",
                 icon='STRIP_COLOR_04' if current else 'STRIP_COLOR_01')
    if not has_normal:
        box.label(text="Needs the Normal map: bake or pick one above", icon='INFO')
    elif not curvature.is_derived(mat):
        box.label(text="Not derived yet", icon='INFO')


def _bake(self, context):
    layout = self.layout
    scene = context.scene
    layout.template_icon_view(scene, "essence_bake_map", show_labels=True,
                              scale=6, scale_popup=5)
    uv_islands = scene.essence_bake_map == 'ID' and scene.essence_bake_id == 'UV_ISLAND'
    if scene.essence_bake_map == 'THICKNESS':
        layout.prop(scene, "essence_bake_thickness")
    if scene.essence_bake_map == 'ID':
        layout.prop(scene, "essence_bake_id")
        if uv_islands:
            layout.label(text="The islands of the low poly itself", icon='INFO')
        if scene.essence_bake_aa != '1':
            layout.label(text="Anti-aliasing blends IDs at edges", icon='ERROR')
    if scene.essence_bake_map in HIGH_MAPS and not uv_islands:
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
    col.prop(scene, "essence_bake_device")
    device, note = resolve_device(scene)
    layout.label(text=f"Bakes on {note}", icon='MEMORY' if device == 'GPU' else 'INFO')
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
    ("ADJUSTMENT", "Adjustment Properties", _adjustment, _adjustment_poll),
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
