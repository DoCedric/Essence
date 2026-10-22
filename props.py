import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import blends, nodes, sources
from .channels import BY_ID, SOURCE_BY_ID


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
                              ('MASK', "Mask", "")],
                       default='LAYER')
    uid: StringProperty()
    parent: StringProperty()   # uid of the owner (folder or layer), empty at top level
    node_name: StringProperty()
    mask_node_name: StringProperty()
    frozen: BoolProperty()  # baked into the frozen images, left out of the chain (freeze.py)
    sources: CollectionProperty(type=EssenceSource)  # layers only, one per channel
    source: PointerProperty(type=EssenceSource)      # masks only, see sources.py


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
    channels: CollectionProperty(type=EssenceChannelState)
    freeze_uid: StringProperty()       # top-level layer or folder the freeze reaches up to
    freeze_channels: StringProperty()  # channel ids the frozen images cover
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
