"""Duplicate, copy and paste of layers and folders, and the menu of a stack row.

A copy takes the entry with everything it owns (masks, fills, adjustments, filters, and a
folder's layers and folders) and their nodes. Entries are copied at the ID property level so
no update callback fires halfway; nodes are copied by hand (there is no node duplicate outside
a node editor) and keep their values, then the usual `sources.apply` and rebuild wire them.

Images Essence made to be painted on, and filter results, belong to their entry and are
copied, so painting the copy leaves the original alone; images the user picked (an image
source) are shared. Links from user nodes outside the copied set are kept within one material
and dropped when pasting into another.

The clipboard holds a reference (material and entry), not a snapshot: Paste copies the entry
as it is at that moment.
"""

import bpy
from bpy.props import EnumProperty, StringProperty

from . import filters, nodes, sources, stack
from .persist import PAINT_KEY
from .sources import SRC_KEY

OWNER_KINDS = ('LAYER', 'FOLDER')

# The copied entry: (material session uid, entry uid).
_clip = None


def _subtree(layers, uid):
    """`uid` and every entry under it, in stored (bottom-to-top) order."""
    parent = {e.uid: e.parent for e in layers}

    def under(u):
        seen = set()
        while u and u not in seen:
            if u == uid:
                return True
            seen.add(u)
            u = parent.get(u, "")
        return False

    return [e.uid for e in layers if under(e.uid)]


def _plain(v):
    """An ID property value as plain data (groups to dicts, collections to lists of dicts). A
    pointer to a data-block (an image a source remembers) stays that data-block."""
    if isinstance(v, bpy.types.ID):
        return v
    if hasattr(v, "to_dict"):       # key by key: to_dict() would turn a pointer into a dict
        return {k: _plain(v[k]) for k in v.keys()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if hasattr(v, "to_list"):
        return v.to_list()
    return v


def _copy_ramp(src, dst):
    for attr in ("color_mode", "hue_interpolation", "interpolation"):
        setattr(dst, attr, getattr(src, attr))
    els = dst.elements
    while len(els) > len(src.elements):
        els.remove(els[-1])
    while len(els) < len(src.elements):
        els.new(0.0)
    for a, b in zip(src.elements, els):
        b.position = a.position
        b.color = a.color


def _copy_curves(src, dst):
    for attr in ("black_level", "white_level", "clip_min_x", "clip_min_y", "clip_max_x",
                 "clip_max_y", "use_clip", "extend"):
        if hasattr(src, attr):
            setattr(dst, attr, getattr(src, attr))
    for a, b in zip(src.curves, dst.curves):
        pts = b.points
        while len(pts) > len(a.points):
            pts.remove(pts[-1])
        while len(pts) < len(a.points):
            pts.new(0.0, 0.0)
        for p, q in zip(a.points, pts):
            q.location = p.location
            q.handle_type = p.handle_type
    dst.update()


_SKIP = {"rna_type", "name", "select", "location", "location_absolute", "parent", "dimensions",
         "type", "node_tree", "inputs", "outputs", "internal_links", "is_active_output"}


def _copy_node(src, tree, images):
    n = tree.nodes.new(src.bl_idname)
    if getattr(src, "node_tree", None) is not None:
        n.node_tree = src.node_tree
    # Enums such as a Map Range's data type change the sockets, so properties go first.
    for p in src.bl_rna.properties:
        if p.identifier in _SKIP or p.is_readonly or p.identifier.startswith("bl_"):
            continue
        try:
            setattr(n, p.identifier, getattr(src, p.identifier))
        except (AttributeError, TypeError, ValueError):
            pass
    if getattr(src, "color_ramp", None) is not None:
        _copy_ramp(src.color_ramp, n.color_ramp)
    if getattr(src, "mapping", None) is not None and hasattr(src.mapping, "curves"):
        _copy_curves(src.mapping, n.mapping)
    if getattr(src, "image", None) is not None:
        n.image = images.get(src.image, src.image)
    for socks, new in ((src.inputs, n.inputs), (src.outputs, n.outputs)):
        for a, b in zip(socks, new):
            b.hide = a.hide
            if hasattr(a, "default_value"):
                try:
                    b.default_value = a.default_value
                except (TypeError, ValueError):
                    pass
    for k in src.keys():
        n[k] = _plain(src[k])
    n.location = src.location
    n.hide = src.hide
    return n


def _copy_image(img):
    """A copy with the pixels as they are now: a copy of a packed image would start from the
    packed pixels and lose what was painted since."""
    import numpy as np
    new = img.copy()
    if img.has_data and tuple(img.size) == tuple(new.size) and img.size[0]:
        px = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(px)
        new.pixels.foreach_set(px)
        new.update()
    if img.packed_file is not None or img.source == 'GENERATED':
        new.pack()
    return new


def _unique_name(layers, name):
    taken = {e.name for e in layers}
    if name not in taken:
        return name
    base = name.rsplit(".", 1)[0] if name[-4:-3] == "." and name[-3:].isdigit() else name
    i = 1
    while f"{base}.{i:03d}" in taken:
        i += 1
    return f"{base}.{i:03d}"


def copy_entry(src_mat, uid, dst_mat, parent, anchor=None):
    """Copy entry `uid` of `src_mat` with what it owns into `dst_mat`, under `parent` (a folder
    uid or "" for the top level), just above `anchor` when it is a sibling (else on top).
    Returns the uid of the copy."""
    same = src_mat == dst_mat
    s_layers = src_mat.essence.layers
    uids = _subtree(s_layers, uid)
    umap = {u: stack.new_uid() for u in uids}
    by_uid = {e.uid: e for e in s_layers}
    data = {u: {k: _plain(by_uid[u][k]) for k in by_uid[u].keys()} for u in uids}
    kinds = {u: by_uid[u].kind for u in uids}
    root_name = by_uid[uid].name

    # The nodes that belong to the copied entries: their group and image nodes, and every
    # source, adjustment and fill node tagged with one of their uids.
    s_tree = src_mat.node_tree
    d_tree = nodes.ensure_tree(dst_mat)
    named = {data[u].get(k) for u in uids for k in ("node_name", "mask_node_name")} - {None, ""}
    owned = [n for n in s_tree.nodes
             if n.name in named or str(n.get(SRC_KEY, "")).split(":")[0] in umap]
    # Paint targets: the images Essence made, and any mask or paint item image (older files).
    painted = {data[u].get("mask_node_name") for u in uids
               if kinds[u] in ('MASK', 'FILL')} - {None, ""}
    images = {}
    for n in owned:
        img = getattr(n, "image", None)
        if img is not None and img not in images and (img.get(PAINT_KEY) or n.name in painted):
            images[img] = _copy_image(img)
    for u in uids:
        if kinds[u] == 'FILTER':
            old = filters.result_image(u)
            if old is not None:
                images[old] = _copy_image(old)
                images[old].name = filters.image_name(umap[u])
            ng = bpy.data.node_groups.get(filters.group_name(u))
            if ng is not None:
                ng.copy().name = filters.group_name(umap[u])

    nmap = {}
    for n in owned:
        c = _copy_node(n, d_tree, images)
        key = str(n.get(SRC_KEY, ""))
        if key:
            u, _, rest = key.partition(":")
            c[SRC_KEY] = f"{umap[u]}:{rest}"
        nmap[n] = c
    for link in list(s_tree.links):
        a, b = nmap.get(link.from_node), nmap.get(link.to_node)
        if b is None:
            continue
        dst = b.inputs[list(link.to_node.inputs).index(link.to_socket)]
        if a is not None:
            d_tree.links.new(a.outputs[list(link.from_node.outputs).index(link.from_socket)],
                             dst)
        elif same:   # a hand-made feed from outside the copied set
            d_tree.links.new(link.from_socket, dst)
    names = {n.name: c.name for n, c in nmap.items()}

    # The entries, with new uids and the copied nodes' names.
    d_layers = dst_mat.essence.layers
    new_name = _unique_name(d_layers, root_name) if same else root_name
    for u in uids:
        d = data[u]
        d["uid"] = umap[u]
        d["parent"] = parent if u == uid else umap.get(d.get("parent", ""), parent)
        for k in ("node_name", "mask_node_name"):
            if d.get(k):
                d[k] = names.get(d[k], "")
        for src in d.get("sources", []) + ([d["source"]] if "source" in d else []):
            if src.get("uid") in umap:
                src["uid"] = umap[src["uid"]]
            mem = src.get("image_memory")
            if isinstance(mem, bpy.types.ID) and mem in images:  # an unset one reads as {}
                src["image_memory"] = images[mem]
        if u == uid:
            d["name"] = new_name
        e = d_layers.add()
        for k, v in d.items():
            e[k] = v
    root_node = d_tree.nodes.get(names.get(data[uid].get("node_name", ""), ""))
    if root_node is not None:
        root_node.label = new_name

    tree = stack.Tree(d_layers)
    new = umap[uid]
    if anchor and tree.parent.get(anchor) == tree.parent.get(new):
        tree.insert_after(new, anchor)
    tree.apply()
    for u in uids:
        e = d_layers[tree.index(umap[u])]
        if e.kind == 'LAYER':
            sources.ensure_entries(e)
    for u in uids:
        e = d_layers[tree.index(umap[u])]
        if e.kind in ('LAYER', 'MASK'):
            sources.apply_owner(dst_mat, e)
    nodes.rebuild_stack(dst_mat)
    return new


def _material(context):
    ob = context.active_object
    return ob.active_material if ob is not None and ob.type == 'MESH' else None


def _entry(mat, uid):
    return next((e for e in mat.essence.layers if e.uid == uid), None) if mat else None


def _target(mat, uid):
    """The entry an operator acts on: `uid` when given, else the active entry."""
    if uid:
        return _entry(mat, uid)
    s = mat.essence if mat else None
    return s.layers[s.active_index] if s and 0 <= s.active_index < len(s.layers) else None


def _clip_source():
    if _clip is None:
        return None, None
    mat = next((m for m in bpy.data.materials if m.session_uid == _clip[0]), None)
    return mat, _entry(mat, _clip[1])


def _owner(mat, e):
    """The layer or folder a mask or item belongs to (the entry itself for those)."""
    while e is not None and e.kind not in OWNER_KINDS:
        e = _entry(mat, e.parent)
    return e


class _EntryOp:
    uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _material(context) is not None


class ESSENCE_OT_duplicate(_EntryOp, bpy.types.Operator):
    bl_idname = "essence.duplicate"
    bl_label = "Duplicate"
    bl_description = ("Duplicate the layer or folder with its masks, fills, adjustments and "
                      "contents; painted images are copied too")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context)
        e = _owner(mat, _target(mat, self.uid))
        if e is None:
            return {'CANCELLED'}
        new = copy_entry(mat, e.uid, mat, e.parent, anchor=e.uid)
        s = mat.essence
        s.active_index = next(i for i, x in enumerate(s.layers) if x.uid == new)
        return {'FINISHED'}


class ESSENCE_OT_copy(_EntryOp, bpy.types.Operator):
    bl_idname = "essence.copy"
    bl_label = "Copy"
    bl_description = "Copy the layer or folder, to paste it here or into another material"
    bl_options = {'REGISTER'}

    def execute(self, context):
        global _clip
        mat = _material(context)
        e = _owner(mat, _target(mat, self.uid))
        if e is None:
            return {'CANCELLED'}
        _clip = (mat.session_uid, e.uid)
        self.report({'INFO'}, f"Copied {e.name}")
        return {'FINISHED'}


class ESSENCE_OT_paste(_EntryOp, bpy.types.Operator):
    bl_idname = "essence.paste"
    bl_label = "Paste"
    bl_description = ("Paste the copied layer or folder above the selected layer, or inside "
                      "the selected folder")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and _clip_source()[1] is not None

    def execute(self, context):
        src_mat, src = _clip_source()
        if src is None:
            self.report({'WARNING'}, "Nothing to paste: the copied layer is gone")
            return {'CANCELLED'}
        mat, parent, anchor = paste_target(context, self.uid)
        new = copy_entry(src_mat, src.uid, mat, parent, anchor)
        s = mat.essence
        s.active_index = next(i for i, x in enumerate(s.layers) if x.uid == new)
        return {'FINISHED'}


def paste_target(context, uid=""):
    """Where pasted entries go: the material (made when the object has none), the parent and
    the anchor for `copy_entry`: inside the selected folder, else above the selected layer."""
    ob = context.active_object
    mat = ob.active_material
    if mat is None:
        mat = bpy.data.materials.new(f"{ob.name} Essence")
        ob.data.materials.append(mat)
        ob.active_material = mat
    at = _owner(mat, _target(mat, uid))
    if at is not None and at.kind == 'FOLDER':
        return mat, at.uid, None
    return (mat, at.parent, at.uid) if at is not None else (mat, "", None)


class ESSENCE_OT_remove_entry(_EntryOp, bpy.types.Operator):
    bl_idname = "essence.remove_entry"
    bl_label = "Delete"
    bl_description = ("Remove this entry. Removing a folder ungroups what is inside it; a "
                      "layer or folder takes its masks with it")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _material(context)
        s = mat.essence
        i = next((i for i, e in enumerate(s.layers) if e.uid == self.uid), -1)
        if i < 0:
            return {'CANCELLED'}
        s.active_index = i
        return bpy.ops.essence.remove_layer()


class ESSENCE_OT_add_mask_to(_EntryOp, bpy.types.Operator):
    bl_idname = "essence.add_mask_to"
    bl_label = "Add Mask"
    bl_description = "Add a mask to this layer or folder"
    bl_options = {'REGISTER', 'UNDO'}

    fill: EnumProperty(
        name="Fill",
        items=[('WHITE', "White (reveals)", "Layer is fully visible until erased"),
               ('BLACK', "Black (hides)", "Hides the layer until painted")],
        default='WHITE')

    def execute(self, context):
        s = _material(context).essence
        i = next((i for i, e in enumerate(s.layers) if e.uid == self.uid), -1)
        if i < 0:
            return {'CANCELLED'}
        s.active_index = i
        if not bpy.ops.essence.add_mask.poll():
            return {'CANCELLED'}
        return bpy.ops.essence.add_mask(source='PAINT', fill=self.fill)


def draw_entry_menu(layout, mat, e):
    """What the right-click and Shift+click menu of a row offers for entry `e`."""
    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator("essence.rename_entry", icon='GREASEPENCIL').uid = e.uid
    if e.kind in OWNER_KINDS:
        layout.operator("essence.duplicate", icon='DUPLICATE').uid = e.uid
        layout.operator("essence.copy", icon='COPYDOWN').uid = e.uid
    layout.operator("essence.paste", icon='PASTEDOWN').uid = e.uid
    if e.kind in OWNER_KINDS and not any(
            x.kind == 'MASK' and x.parent == e.uid for x in mat.essence.layers):
        layout.separator()
        for fill, text, icon in (('WHITE', "Add White Mask", 'IMAGE_ALPHA'),
                                 ('BLACK', "Add Black Mask", 'MOD_MASK')):
            op = layout.operator("essence.add_mask_to", text=text, icon=icon)
            op.uid, op.fill = e.uid, fill
    if e.kind in OWNER_KINDS:
        layout.operator("essence.save_smart_material", icon='ASSET_MANAGER').uid = e.uid
    layout.separator()
    layout.operator("essence.remove_entry", icon='X').uid = e.uid


class ESSENCE_MT_entry(bpy.types.Menu):
    bl_label = "Entry"
    bl_idname = "ESSENCE_MT_entry"

    def draw(self, context):
        mat = _material(context)
        s = mat.essence if mat else None
        if s is None or not 0 <= s.active_index < len(s.layers):
            return
        draw_entry_menu(self.layout, mat, s.layers[s.active_index])


def _button_menu(self, context):
    """Blender's right-click menu of a button: the row's own options on a stack row."""
    op = getattr(context, "button_operator", None)
    if op is None or op.bl_rna.identifier != "ESSENCE_OT_select_entry":
        return
    mat = _material(context)
    e = _entry(mat, op.uid)
    if e is None:
        return
    self.layout.separator()
    draw_entry_menu(self.layout, mat, e)


classes = (ESSENCE_OT_duplicate, ESSENCE_OT_copy, ESSENCE_OT_paste, ESSENCE_OT_remove_entry,
           ESSENCE_OT_add_mask_to, ESSENCE_MT_entry)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.UI_MT_button_context_menu.append(_button_menu)


def unregister():
    global _clip
    _clip = None
    bpy.types.UI_MT_button_context_menu.remove(_button_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
