import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import adjustments, blends, nodes, sources, stack
from .channels import BY_ID, CHANNELS, SOURCE_BY_ID


def _name_update(self, context):
    mat = self.id_data
    if mat.node_tree is None:
        return
    for n in (self.node_name, self.mask_node_name):
        node = mat.node_tree.nodes.get(n) if n else None
        if node is not None:
            node.label = self.name


def _kind_items(self, context):
    return sources.kind_items(self)


def _blend_items(self, context):
    ch = SOURCE_BY_ID.get(self.channel)
    return blends.items_for(ch) if ch is not None and ch.id in BY_ID else _NO_BLEND


_NO_BLEND = [('MIX', "Normal", "", 'NONE', 0)]


def _blend_update(self, context):
    """The blend mode of a channel changed: the layer needs another group variant."""
    mat = self.id_data
    if sources.SUSPEND or mat.node_tree is None or not self.blend_init:
        return
    entry = next((e for e in mat.essence.layers if e.uid == self.uid), None)
    if entry is not None:
        nodes.refresh_entry(mat, entry)
        nodes.rebuild_stack(mat)


def _channel_update(self, context):
    """A channel was switched on or off for the material."""
    mat = self.id_data
    if sources.SUSPEND or mat.node_tree is None:
        return
    nodes.refresh_all(mat, changed={self.id})


class EssenceChannelState(bpy.types.PropertyGroup):
    """Whether the material has a channel (see channels.py, default_enabled)."""
    id: StringProperty()
    enabled: BoolProperty(name="Enabled", default=True, update=_channel_update,
                          description="Use this channel in the layer stack")


def _source_update(self, context):
    """A source setting changed: rebuild that channel's source nodes."""
    mat = self.id_data
    if sources.SUSPEND or mat.node_tree is None or not self.channel:
        return
    entry = next((e for e in mat.essence.layers if e.uid == self.uid), None)
    if entry is not None:
        sources.apply(mat, entry, self.channel)
        if self.channel == 'MASK':
            nodes.rebuild_stack(mat)  # re-places the mask's source nodes
            _refresh_paint_slots(context, mat, entry)


def _refresh_paint_slots(context, mat, entry):
    """Texture Paint builds its list of paintable images when the mode is entered, so after the
    mask's image node was replaced it still points at the old, deleted node and painting does
    nothing until the mode is re-entered. Do that here, with the new image node active."""
    ob = context.active_object
    if ob is None or ob.mode != 'TEXTURE_PAINT' or ob.active_material is None:
        return
    if ob.active_material.name != mat.name:
        return
    tex = mat.node_tree.nodes.get(entry.mask_node_name) if entry.mask_node_name else None
    if tex is not None:
        mat.node_tree.nodes.active = tex
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    except RuntimeError:
        pass


def _adj_channel_items(self, context):
    return [(c.id, c.label, "") for c in CHANNELS]


def _adj_update(self, context):
    """An adjustment changed: rewire the chain it belongs to."""
    mat = self.id_data
    if sources.SUSPEND or mat.node_tree is None or self.kind not in stack.ITEM_KINDS:
        return
    owner = next((e for e in mat.essence.layers if e.uid == self.parent), None)
    if owner is None:
        return
    if (self.kind == 'ADJUSTMENT' and adjustments.BY_ID[self.adj_type].color_only
            and owner.kind == 'LAYER'
            and BY_ID[self.adj_channel].socket_type != 'NodeSocketColor'):
        sources.SUSPEND = True  # a color-only adjustment cannot act on a float channel
        try:
            self.adj_channel = next(c.id for c in CHANNELS if c.socket_type == 'NodeSocketColor')
        finally:
            sources.SUSPEND = False
    if self.kind == 'ADJUSTMENT' and self.name in {t.label for t in adjustments.TYPES}:
        self.name = adjustments.BY_ID[self.adj_type].label  # a default name follows the type
    sources.apply_owner(mat, owner)
    nodes.rebuild_stack(mat)


class EssenceSource(bpy.types.PropertyGroup):
    """What drives one channel of a layer (see sources.py). The nodes carry the values."""
    channel: StringProperty()
    uid: StringProperty()  # uid of the layer entry that owns it
    kind: EnumProperty(name="Source", items=_kind_items, update=_source_update)
    blend: EnumProperty(name="Blend", items=_blend_items, update=_blend_update,
                        description="How this channel of the layer combines with what is below")
    blend_init: BoolProperty()  # blend has been set to the channel's default
    show_transform: BoolProperty(name="Transform", default=False,
                                 description="Show tiling, offset and rotation")
    projection: EnumProperty(name="Projection", items=sources.PROJECTIONS, default='UV',
                             update=_source_update)
    proc_type: EnumProperty(name="Type", items=sources.PROC_ITEMS, default='NOISE',
                            update=_source_update)
    expanded: BoolProperty(name="Expanded", default=True,
                           description="Show this channel's settings")
    # The image of an Image source that was switched away, restored when switching back.
    image_memory: PointerProperty(type=bpy.types.Image)


class EssenceLayer(bpy.types.PropertyGroup):
    """Thin index into the node tree. Values live on the group node's sockets.

    The list is stored bottom-to-top. A folder entry sits directly above its children,
    which point at it through `parent` (the folder's `uid`). Folders do not nest.
    Masks (kind MASK) point at the layer or folder that owns them; their `node_name` is the
    mask group node and `mask_node_name` the image node. See stack.py."""
    name: StringProperty(name="Name", update=_name_update)
    kind: EnumProperty(items=[('LAYER', "Layer", ""), ('FOLDER', "Folder", ""),
                              ('MASK', "Mask", ""), ('ADJUSTMENT', "Adjustment", ""),
                              ('FILL', "Fill", "")],
                       default='LAYER')
    uid: StringProperty()
    parent: StringProperty()   # uid of the owner (folder or layer), empty at top level
    node_name: StringProperty()
    mask_node_name: StringProperty()
    sources: CollectionProperty(type=EssenceSource)  # layers only, one per channel
    source: PointerProperty(type=EssenceSource)      # masks only, see sources.py
    # Adjustments only (see adjustments.py): what it does, the channel of its layer it acts on
    # (masks: the mask value), and whether it is applied.
    adj_type: EnumProperty(name="Type", items=adjustments.TYPE_ITEMS, update=_adj_update)
    adj_channel: EnumProperty(name="Channel", items=_adj_channel_items, update=_adj_update)
    adj_enabled: BoolProperty(name="Enabled", default=True, update=_adj_update,
                              description="Apply this adjustment or fill")
    # Fills only (a mask's item with its own `source`): how it combines with the value below.
    fill_blend: EnumProperty(name="Blend", items=adjustments.FILL_BLENDS, update=_adj_update,
                             description="How this fill combines with the mask value below it")


def _active_update(self, context):
    """Make the selected layer's mask the active image node (the paint target)."""
    mat = self.id_data
    tree = mat.node_tree
    if tree is None or not (0 <= self.active_index < len(self.layers)):
        return
    mask = tree.nodes.get(self.layers[self.active_index].mask_node_name)
    if mask is not None:
        tree.nodes.active = mask


class EssenceMaterial(bpy.types.PropertyGroup):
    layers: CollectionProperty(type=EssenceLayer)
    use_tree: BoolProperty(name="Tree View", default=True,
                           description="Show the stack as boxes with each layer's masks beside "
                                       "it, instead of a flat list")
    channels: CollectionProperty(type=EssenceChannelState)
    active_index: IntProperty(update=_active_update)


classes = (EssenceChannelState, EssenceSource, EssenceLayer, EssenceMaterial)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.essence = PointerProperty(type=EssenceMaterial)


def unregister():
    del bpy.types.Material.essence
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
