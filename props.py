import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import nodes, sources


def _name_update(self, context):
    mat = self.id_data
    if mat.node_tree is None:
        return
    for n in (self.node_name, self.mask_node_name):
        node = mat.node_tree.nodes.get(n) if n else None
        if node is not None:
            node.label = self.name


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


class EssenceSource(bpy.types.PropertyGroup):
    """What drives one channel of a layer (see sources.py). The nodes carry the values."""
    channel: StringProperty()
    uid: StringProperty()  # uid of the layer entry that owns it
    kind: EnumProperty(name="Source", items=sources.KINDS, default='VALUE',
                       update=_source_update)
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
    active_index: IntProperty(update=_active_update)


classes = (EssenceSource, EssenceLayer, EssenceMaterial)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.essence = PointerProperty(type=EssenceMaterial)


def unregister():
    del bpy.types.Material.essence
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
