"""Export for Unity (HDRP): BaseMap, MaskMap and Normal textures from the active material.

Values are baked from the top Essence layer's outputs (the final result of the stack) with
Cycles' emit pass, so they are exact and unlit. The normal is a real tangent-space bake of
the whole material (baked normal map + layer bump combined).

MaskMap layout (Unity HDRP): R = Metallic, G = Ambient Occlusion, B = Detail Mask,
A = Smoothness (1 - roughness).
"""

import os

import bpy
import numpy as np
from bpy.props import EnumProperty, StringProperty

from .bake import (_BAKE_ATTRS, _ForceSelectable, _emission_override, other_targets,
                   set_if_changed, temp_target, texture_set)
from . import filters, nodes
from .channels import BY_ID, active_channels

FORMATS = (
    ('PNG', "PNG", "8-bit PNG", 'png'),
    ('TARGA', "TGA", "Targa", 'tga'),
    ('OPEN_EXR', "EXR", "32-bit float OpenEXR", 'exr'),
)
_SIZES = tuple((str(s), f"{s} x {s}", "") for s in (512, 1024, 2048, 4096))


def _ext(fmt):
    return next(f[3] for f in FORMATS if f[0] == fmt)


def _pixels(img):
    a = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(a)
    return a


class ESSENCE_OT_export_unity(bpy.types.Operator):
    bl_idname = "essence.export_unity"
    bl_label = "Export for Unity"
    bl_description = ("Bake and save BaseMap, MaskMap (R metallic, G AO, B detail, "
                      "A smoothness) and Normal for the active material, from every object "
                      "that uses it")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob is None or ob.type != 'MESH' or ob.active_material is None:
            return False
        return len(ob.active_material.essence.layers) > 0

    def _bake(self, context, objects, mat, image, pass_type, build=None):
        """Bake the texture set (every object of the material, all selected) into `image`.
        Several objects bake without Cycles' margin and get it from the compositor."""
        margin = context.scene.essence_bake_margin
        several = len(objects) > 1
        set_if_changed(context.scene.render.bake, 'margin', 0 if several else margin)
        teardowns = []
        try:
            teardowns.append(temp_target(mat, image))
            teardowns.append(other_targets(objects, mat))
            if build is not None:
                teardowns.append(_emission_override(mat, build))
            if 'FINISHED' not in bpy.ops.object.bake(type=pass_type, use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
        finally:
            for t in reversed(teardowns):
                t()
        if several and margin:
            filters.add_margin(context, objects[0], mat, image, margin)

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        mat = obj.active_material
        objects = [o for o in texture_set(context, mat) if o.data.uv_layers]
        if obj not in objects:
            self.report({'ERROR'}, "Object has no UV map")
            return {'CANCELLED'}
        objects.remove(obj)
        objects.insert(0, obj)
        nt = mat.node_tree
        tail = nt.nodes.get(mat.essence.layers[-1].node_name)
        if tail is None:
            self.report({'ERROR'}, "Top layer node is missing from the shader graph")
            return {'CANCELLED'}

        fmt = scene.essence_export_format
        size = int(scene.essence_export_size)
        is_exr = fmt == 'OPEN_EXR'
        folder = bpy.path.abspath(scene.essence_export_dir)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Cannot create export folder: {e}")
            return {'CANCELLED'}
        stem = bpy.path.clean_name(mat.name)

        def path(suffix):
            return os.path.join(folder, f"{stem}_{suffix}.{_ext(fmt)}")

        def new_image(name, color=False, alpha=False):
            img = bpy.data.images.new(f"__essence_{name}", size, size, alpha=alpha,
                                      float_buffer=is_exr)
            img.colorspace_settings.name = 'sRGB' if (color and not is_exr) else 'Non-Color'
            return img

        def output(nt_, ch_id, x, y):
            """The stack's output for a channel, or its default if the material has no such
            channel (a switched-off channel is not in the layer node)."""
            ch = BY_ID[ch_id]
            if ch.label in tail.outputs:
                return tail.outputs[ch.label], []
            if ch.socket_type == 'NodeSocketColor':
                n = nt_.nodes.new('ShaderNodeRGB')
                n.outputs[0].default_value = ch.default
            else:
                n = nt_.nodes.new('ShaderNodeValue')
                n.outputs[0].default_value = ch.default
            n.location = (x, y)
            return n.outputs[0], [n]

        def channel(ch_id):
            def build(nt_, x, y):
                return output(nt_, ch_id, x, y)
            return build

        def smoothness(nt_, x, y):
            m = nt_.nodes.new('ShaderNodeMath')
            m.operation = 'SUBTRACT'
            m.inputs[0].default_value = 1.0
            rough, made = output(nt_, 'ROUGHNESS', x - 200, y)
            nt_.links.new(rough, m.inputs[1])
            m.location = (x, y)
            return m.outputs[0], [m] + made

        start_mode = context.active_object.mode if context.active_object else 'OBJECT'
        if start_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        orig_selection = list(context.selected_objects)
        orig_active = context.view_layer.objects.active
        orig_engine = scene.render.engine
        orig_samples = scene.cycles.samples
        bake = scene.render.bake
        saved = {a: getattr(bake, a) for a in _BAKE_ATTRS}

        temps, written, notes = [], [], []
        forced = _ForceSelectable(context, objects)
        try:
            forced.__enter__()
            bpy.ops.object.select_all(action='DESELECT')
            for o in objects:
                o.select_set(True)
            context.view_layer.objects.active = obj

            set_if_changed(scene.render, 'engine', 'CYCLES')    # see set_if_changed
            set_if_changed(scene.cycles, 'samples', 4)
            set_if_changed(bake, 'target', 'IMAGE_TEXTURES')
            set_if_changed(bake, 'use_selected_to_active', False)
            set_if_changed(bake, 'normal_space', 'TANGENT')
            for name, value in (('normal_r', 'POS_X'), ('normal_g', 'POS_Y'), ('normal_b', 'POS_Z')):
                set_if_changed(bake, name, value)

            active = {c.id for c in active_channels(mat)}
            base = new_image("base", color=True, alpha='ALPHA' in active)
            metal = new_image("metal")
            smooth = new_image("smooth")
            normal = new_image("normal")
            temps += [base, metal, smooth, normal]

            self._bake(context, objects, mat, base, 'EMIT', channel('COLOR'))
            if 'ALPHA' in active:
                alpha = new_image("alpha")
                temps.append(alpha)
                self._bake(context, objects, mat, alpha, 'EMIT', channel('ALPHA'))
                base.alpha_mode = 'CHANNEL_PACKED'
                px = _pixels(base)
                px[3::4] = _pixels(alpha)[0::4]
                base.pixels.foreach_set(px)
                base.update()
                notes.append("opacity is in the BaseMap alpha")
            extra = []
            if 'EMISSION' in active:
                emission = new_image("emission", color=True)
                temps.append(emission)
                self._bake(context, objects, mat, emission, 'EMIT', channel('EMISSION'))
                extra.append((emission, "Emission"))
            self._bake(context, objects, mat, metal, 'EMIT', channel('METALLIC'))
            self._bake(context, objects, mat, smooth, 'EMIT', smoothness)
            self._bake(context, objects, mat, normal, 'NORMAL')

            # Mask map: R metallic, G AO, B detail mask, A smoothness.
            ao_node = mat.node_tree.nodes.get(nodes.map_slot_name('AO'))
            ao_src = getattr(ao_node, "image", None) or bpy.data.images.get(f"{mat.name}_AO")
            if ao_src is not None:
                ao = ao_src.copy()
                temps.append(ao)
                if tuple(ao.size) != (size, size):
                    ao.scale(size, size)
                ao_px = _pixels(ao)[0::4]
            else:
                ao_px = np.ones(size * size, dtype=np.float32)
                notes.append("no baked AO found, MaskMap green is white")

            mask = new_image("mask", alpha=True)
            mask.alpha_mode = 'CHANNEL_PACKED'
            temps.append(mask)
            out = np.empty((size * size, 4), dtype=np.float32)
            out[:, 0] = _pixels(metal)[0::4]
            out[:, 1] = ao_px
            out[:, 2] = 1.0
            out[:, 3] = _pixels(smooth)[0::4]
            mask.pixels.foreach_set(out.ravel())
            mask.update()

            for img, suffix in [(base, "BaseMap"), (mask, "MaskMap"), (normal, "Normal")] + extra:
                p = path(suffix)
                img.filepath_raw = p
                img.file_format = fmt
                img.save()
                written.append(os.path.basename(p))
        except Exception as e:  # noqa: BLE001
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}
        finally:
            for img in temps:
                try:
                    bpy.data.images.remove(img)
                except (ReferenceError, RuntimeError):
                    pass
            forced.__exit__()
            set_if_changed(scene.render, 'engine', orig_engine)
            set_if_changed(scene.cycles, 'samples', orig_samples)
            for a, v in saved.items():
                set_if_changed(bake, a, v)
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

        msg = f"Exported {', '.join(written)} to {folder}"
        if notes:
            msg += f" ({'; '.join(notes)})"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def register():
    S = bpy.types.Scene
    S.essence_export_format = EnumProperty(
        name="Format", items=[f[:3] for f in FORMATS], default='PNG')
    S.essence_export_size = EnumProperty(name="Resolution", items=_SIZES, default="2048")
    S.essence_export_dir = StringProperty(
        name="Folder", subtype='DIR_PATH', default="//Export/",
        description="Where the textures are written. // is relative to the .blend file")
    bpy.utils.register_class(ESSENCE_OT_export_unity)


def unregister():
    bpy.utils.unregister_class(ESSENCE_OT_export_unity)
    S = bpy.types.Scene
    for p in ("essence_export_format", "essence_export_size", "essence_export_dir"):
        delattr(S, p)
