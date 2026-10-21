import bpy
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty


def _name_update(self, context):
    mat = self.id_data
    if mat.node_tree is None:
        return
    node = mat.node_tree.nodes.get(self.node_name)
    if node is not None:
        node.label = self.name
    mask = mat.node_tree.nodes.get(self.mask_node_name) if self.mask_node_name else None
    if mask is not None:
        mask.label = f"{self.name} Mask"


class EssenceLayer(bpy.types.PropertyGroup):
    """Thin index into the node tree. Values live on the group node's sockets."""
    name: StringProperty(name="Name", update=_name_update)
    node_name: StringProperty()
    mask_node_name: StringProperty()


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


classes = (EssenceLayer, EssenceMaterial)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.essence = PointerProperty(type=EssenceMaterial)


def unregister():
    del bpy.types.Material.essence
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
