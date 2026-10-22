"""Freeze: bake the bottom of the layer stack into images so the live shader stays small.

Blender recompiles the whole material shader whenever its node structure changes, and the Bump
node evaluates everything feeding its height three times, so a deep stack gets slow to edit.
Freezing a layer or folder bakes the result of everything up to it, one image per channel, and
the layers above read those images instead. The frozen layers keep their nodes (so Unfreeze
brings them back exactly) but are left out of the chain, and Blender skips nodes that do not
reach the output.

The images are sampled with the object's UV map, like a baked mesh map, so freezing is per
object: a material shared by objects with different UVs cannot be frozen meaningfully.
"""

import bpy
from bpy.props import EnumProperty

from . import nodes
from .bake import _BAKE_ATTRS, _emission_override, _ensure_materials
from .channels import active_channels
from .export import _temp_target

FROZEN_KEY = nodes.FROZEN_KEY  # on the nodes made here: the channel id, or "COORD"
_SIZES = tuple((str(s), f"{s} x {s}", "") for s in (512, 1024, 2048, 4096))


def frozen_nodes(tree):
    """{channel id or 'COORD': node} of the frozen image nodes in a tree."""
    return {n[FROZEN_KEY]: n for n in tree.nodes if FROZEN_KEY in n}


def is_frozen(mat):
    return bool(mat.essence.freeze_uid)


def _remove_frozen(mat):
    tree = mat.node_tree
    for n in list(tree.nodes):
        if FROZEN_KEY not in n:
            continue
        img = getattr(n, "image", None)
        tree.nodes.remove(n)
        if img is not None and img.users == 0:
            bpy.data.images.remove(img)


def _unit_of(layers, entry):
    """The top-level layer or folder an entry belongs to."""
    if entry.kind == 'MASK':
        entry = next((x for x in layers if x.uid == entry.parent), entry)
    if entry.kind == 'LAYER' and entry.parent:
        entry = next((x for x in layers if x.uid == entry.parent), entry)
    return entry


def _active_entry(mat):
    s = mat.essence
    return s.layers[s.active_index] if 0 <= s.active_index < len(s.layers) else None


class ESSENCE_OT_freeze(bpy.types.Operator):
    bl_idname = "essence.freeze"
    bl_label = "Freeze"
    bl_description = ("Bake the stack up to and including the active layer or folder into "
                      "images. Everything frozen stops costing shader time; Unfreeze brings "
                      "it back")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob is None or ob.type != 'MESH' or ob.active_material is None:
            return False
        return len(ob.active_material.essence.layers) > 0

    def _bake(self, obj, mat, image, socket):
        teardowns = []

        def build(nt_, x, y):
            return socket, []
        try:
            for m in _ensure_materials(obj):
                if not m.use_nodes:
                    m.use_nodes = True
                teardowns.append(_temp_target(m, image))
            teardowns.append(_emission_override(mat, build))
            if 'FINISHED' not in bpy.ops.object.bake(type='EMIT', use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
        finally:
            for t in reversed(teardowns):
                t()

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        mat = obj.active_material
        s = mat.essence
        entry = _active_entry(mat)
        if entry is None:
            return {'CANCELLED'}
        if not obj.data.uv_layers:
            self.report({'ERROR'}, "Object has no UV map")
            return {'CANCELLED'}
        unit = _unit_of(s.layers, entry)
        node = mat.node_tree.nodes.get(unit.node_name)
        if node is None:
            self.report({'ERROR'}, "The layer's node is missing from the shader graph")
            return {'CANCELLED'}
        nodes.rebuild_stack(mat)  # chain, including any older frozen base, is up to date
        size = int(scene.essence_freeze_size)
        channels = [c for c in active_channels(mat) if c.label in node.outputs]

        start_mode = obj.mode
        if start_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        orig_selection = list(context.selected_objects)
        orig_active = context.view_layer.objects.active
        orig_engine = scene.render.engine
        orig_samples = scene.cycles.samples
        bake = scene.render.bake
        saved = {a: getattr(bake, a) for a in _BAKE_ATTRS}

        images = {}
        try:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj
            scene.render.engine = 'CYCLES'
            scene.cycles.samples = 1
            bake.target = 'IMAGE_TEXTURES'
            bake.use_selected_to_active = False
            bake.margin = scene.essence_bake_margin
            for ch in channels:
                img = bpy.data.images.new(f"{mat.name}_Frozen_{ch.label}", size, size,
                                          alpha=False, float_buffer=True)
                img.use_half_precision = True
                img.colorspace_settings.name = 'Non-Color'
                images[ch.id] = img
                self._bake(obj, mat, img, node.outputs[ch.label])
        except Exception as e:  # noqa: BLE001
            for img in images.values():
                bpy.data.images.remove(img)
            self.report({'ERROR'}, f"Freeze failed: {e}")
            return {'CANCELLED'}
        finally:
            scene.render.engine = orig_engine
            scene.cycles.samples = orig_samples
            for a, v in saved.items():
                setattr(bake, a, v)
            bpy.ops.object.select_all(action='DESELECT')
            for o in orig_selection:
                try:
                    o.select_set(True)
                except Exception:  # noqa: BLE001
                    pass
            if orig_active is not None:
                context.view_layer.objects.active = orig_active
            if start_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=start_mode)

        # Swap the new images in for any older freeze, then mark what they replace.
        _remove_frozen(mat)
        tree = mat.node_tree
        coord = tree.nodes.new('ShaderNodeTexCoord')
        coord.name = coord.label = "Essence Frozen UV"
        coord[FROZEN_KEY] = "COORD"
        for i, ch in enumerate(channels):
            img = images[ch.id]
            img.pack()
            tex = tree.nodes.new('ShaderNodeTexImage')
            tex.name = tex.label = f"Essence Frozen {ch.label}"
            tex.image = img
            tex.interpolation = 'Linear'
            tex.extension = 'EXTEND'
            tex[FROZEN_KEY] = ch.id
            tex.location = (-900, 500 - i * 300)
            tree.links.new(coord.outputs['UV'], tex.inputs['Vector'])
        coord.location = (-1150, 400)

        last = max(i for i, e in enumerate(s.layers) if e.uid == unit.uid)
        for i, e in enumerate(s.layers):
            e.frozen = i <= last
        s.freeze_uid = unit.uid
        s.freeze_channels = ",".join(c.id for c in channels)
        nodes.rebuild_stack(mat)
        self.report({'INFO'}, f"Froze up to '{unit.name}' at {size} x {size}")
        return {'FINISHED'}


class ESSENCE_OT_unfreeze(bpy.types.Operator):
    bl_idname = "essence.unfreeze"
    bl_label = "Unfreeze"
    bl_description = "Bring the frozen layers back and drop the baked images"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return (ob is not None and ob.type == 'MESH' and ob.active_material is not None
                and is_frozen(ob.active_material))

    def execute(self, context):
        mat = context.active_object.active_material
        for e in mat.essence.layers:
            e.frozen = False
        mat.essence.freeze_uid = ""
        mat.essence.freeze_channels = ""
        _remove_frozen(mat)
        nodes.rebuild_stack(mat)
        return {'FINISHED'}


classes = (ESSENCE_OT_freeze, ESSENCE_OT_unfreeze)


def register():
    bpy.types.Scene.essence_freeze_size = EnumProperty(
        name="Freeze Size", items=_SIZES, default="1024",
        description="Resolution of the images a freeze bakes")
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.essence_freeze_size
