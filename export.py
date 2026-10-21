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

from .bake import _BAKE_ATTRS, _emission_override, _ensure_materials

FORMATS = (
    ('PNG', "PNG", "8-bit PNG", 'png'),
    ('TARGA', "TGA", "Targa", 'tga'),
    ('OPEN_EXR', "EXR", "32-bit float OpenEXR", 'exr'),
)
_SIZES = tuple((str(s), f"{s} x {s}", "") for s in (512, 1024, 2048, 4096))


def _ext(fmt):
    return next(f[3] for f in FORMATS if f[0] == fmt)


def _temp_target(mat, image):
    """Temporary active Image Texture node as the bake target; removed on teardown."""
    nt = mat.node_tree
    prev_active = nt.nodes.active
    prev_selected = [n.name for n in nt.nodes if n.select]
    node = nt.nodes.new('ShaderNodeTexImage')
    node.image = image
    for n in nt.nodes:
        n.select = False
    node.select = True
    nt.nodes.active = node

    def restore():
        nt.nodes.remove(node)
        for n in nt.nodes:
            n.select = n.name in prev_selected
        if prev_active is not None and nt.nodes.get(prev_active.name) is not None:
            nt.nodes.active = prev_active
    return restore


def _pixels(img):
    a = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(a)
    return a


class ESSENCE_OT_export_unity(bpy.types.Operator):
    bl_idname = "essence.export_unity"
    bl_label = "Export for Unity"
    bl_description = ("Bake and save BaseMap, MaskMap (R metallic, G AO, B detail, "
                      "A smoothness) and Normal for the active material")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob is None or ob.type != 'MESH' or ob.active_material is None:
            return False
        return len(ob.active_material.essence.layers) > 0

    def _bake(self, obj, chain_mat, image, pass_type, build=None):
        teardowns = []
        try:
            for m in _ensure_materials(obj):
                if not m.use_nodes:
                    m.use_nodes = True
                teardowns.append(_temp_target(m, image))
            if build is not None:
                teardowns.append(_emission_override(chain_mat, build))
            if 'FINISHED' not in bpy.ops.object.bake(type=pass_type, use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
        finally:
            for t in reversed(teardowns):
                t()

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        mat = obj.active_material
        if not obj.data.uv_layers:
            self.report({'ERROR'}, "Object has no UV map")
            return {'CANCELLED'}
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

        def channel(name):
            def build(nt_, x, y):
                return tail.outputs[name], []
            return build

        def smoothness(nt_, x, y):
            m = nt_.nodes.new('ShaderNodeMath')
            m.operation = 'SUBTRACT'
            m.inputs[0].default_value = 1.0
            nt_.links.new(tail.outputs['Roughness'], m.inputs[1])
            m.location = (x, y)
            return m.outputs[0], [m]

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
        try:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            scene.render.engine = 'CYCLES'
            scene.cycles.samples = 4
            bake.target = 'IMAGE_TEXTURES'
            bake.use_selected_to_active = False
            bake.margin = scene.essence_bake_margin
            bake.normal_space = 'TANGENT'
            bake.normal_r, bake.normal_g, bake.normal_b = 'POS_X', 'POS_Y', 'POS_Z'

            base = new_image("base", color=True)
            metal = new_image("metal")
            smooth = new_image("smooth")
            normal = new_image("normal")
            temps += [base, metal, smooth, normal]

            self._bake(obj, mat, base, 'EMIT', channel('Color'))
            self._bake(obj, mat, metal, 'EMIT', channel('Metallic'))
            self._bake(obj, mat, smooth, 'EMIT', smoothness)
            self._bake(obj, mat, normal, 'NORMAL')

            # Mask map: R metallic, G AO, B detail mask, A smoothness.
            ao_src = bpy.data.images.get(f"{obj.name}_AO")
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

            for img, suffix in ((base, "BaseMap"), (mask, "MaskMap"), (normal, "Normal")):
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
