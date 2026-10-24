"""A temporary gizmo for a generator's Rotation input: an Empty shown as an arrow.

The arrow is an ordinary Blender object, so Blender's own rotate tool, gizmo and numeric input
work on it, and nothing is drawn by Essence. Its rotation drives the generator's Rotation
input through three drivers (simple expressions, so they need no Python at run time and the
material keeps working without the addon). The arrow points where the light comes from.

In object space (World Space off) the arrow is a child of the object, so the light turns with
the object like the mask does; in world space it is free. Removing the gizmo keeps the
rotation it had and deletes the arrow and the drivers.

The empty is remembered on the node (`essence_gizmo`). Deleting the node, or the source it
belongs to, deletes the empty too (`drop`).
"""

import bpy
from mathutils import Vector

from .generators import ROTATION_INPUT

KEY = "essence_gizmo"
# The Rotation socket is limited to 0..360 degrees, so an arrow turned to -90 reads as 270.
WRAP = "fmod(fmod(a, 2*pi) + 2*pi, 2*pi)"


def supports(node):
    return (node is not None and node.bl_idname == 'ShaderNodeGroup'
            and node.node_tree is not None and ROTATION_INPUT in node.inputs)


def empty_of(node):
    ob = node.get(KEY) if node is not None else None
    return ob if isinstance(ob, bpy.types.Object) else None


def is_active(node):
    return empty_of(node) is not None


def _path(node):
    index = list(node.inputs).index(node.inputs[ROTATION_INPUT])
    return f'nodes["{node.name}"].inputs[{index}].default_value'


def _remove_drivers(node):
    ad = node.id_data.animation_data
    if ad is None:
        return
    path = _path(node)
    for fc in [fc for fc in ad.drivers if fc.data_path == path]:
        ad.drivers.remove(fc)


def add(node, ob, name):
    """Create the arrow for `node` (a generator group node of a material of `ob`)."""
    if is_active(node):
        return empty_of(node)
    sock = node.inputs[ROTATION_INPUT]
    world = bool(node.inputs["World Space"].default_value) if "World Space" in node.inputs \
        else False
    _remove_drivers(node)       # left over from an arrow that was deleted by hand
    center = sum((Vector(c) for c in ob.bound_box), Vector()) / 8
    empty = bpy.data.objects.new(f"Essence Light {name}", None)
    collections = ob.users_collection or [bpy.context.scene.collection]
    collections[0].objects.link(empty)
    empty.empty_display_type = 'SINGLE_ARROW'
    empty.show_in_front = True
    size = max(ob.dimensions) * 0.6 or 1.0
    if world:
        empty.location = ob.matrix_world @ center
    else:
        empty.parent = ob       # no parent inverse: its rotation is relative to the object
        empty.location = center
        size /= max(abs(x) for x in ob.scale) or 1.0
    empty.empty_display_size = size
    empty.rotation_mode = 'XYZ'
    empty.rotation_euler = tuple(sock.default_value)    # the arrow starts at the current value
    tree, path = node.id_data, _path(node)
    for i in range(3):
        fc = tree.driver_add(path, i)
        d = fc.driver
        d.type = 'SCRIPTED'
        v = d.variables.new()
        v.name = "a"
        v.type = 'SINGLE_PROP'
        v.targets[0].id_type = 'OBJECT'
        v.targets[0].id = empty
        v.targets[0].data_path = f"rotation_euler[{i}]"
        d.expression = WRAP
    node[KEY] = empty
    return empty


def remove(node):
    """Take the arrow away and keep the rotation it had set."""
    sock = node.inputs[ROTATION_INPUT]
    value = tuple(sock.default_value)
    empty = empty_of(node)
    _remove_drivers(node)
    sock.default_value = value
    if empty is not None:
        bpy.data.objects.remove(empty)
    if KEY in node:
        del node[KEY]


def drop(node):
    """The node is going away: delete its arrow and drivers."""
    if node.bl_idname != 'ShaderNodeGroup' or KEY not in node:
        return
    empty = empty_of(node)
    if node.node_tree is not None and ROTATION_INPUT in node.inputs:
        _remove_drivers(node)
    if empty is not None:
        bpy.data.objects.remove(empty)
