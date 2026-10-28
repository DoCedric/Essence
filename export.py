"""Export of the selected material's textures through presets, and the flattened Blender
material.

A preset is data: a list of files, each with a name suffix and what goes in its channels
(an RGB source plus an optional alpha, or one grey source per channel). Every source is
baked once from the top Essence layer's outputs with Cycles' emit pass (exact and unlit) into
a float Non-Color image, the normal is a real tangent-space bake of the whole material (baked
normal map + layer bump combined) and AO comes from the AO slot. All of them are baked
without a margin and dilated afterwards from one coverage bake, then the files are put
together in numpy (sRGB encoding, smoothness, DirectX green) and saved at the chosen depth.
"""

import os
from collections import namedtuple

import bpy
import numpy as np
from bpy.props import EnumProperty, IntProperty, StringProperty

from .bake import _emission_override, other_targets, set_if_changed, temp_target, texture_set
from . import filters, nodes
from .channels import BY_ID, active_channels

FORMATS = (
    ('PNG', "PNG", "PNG, 8 or 16 bits", 'png', ('8', '16')),
    ('TARGA', "TGA", "Targa, 8 bits", 'tga', ('8',)),
    ('TIFF', "TIFF", "TIFF, 8 or 16 bits", 'tif', ('8', '16')),
    ('JPEG', "JPEG", "JPEG, 8 bits, no alpha", 'jpg', ('8',)),
    ('OPEN_EXR', "EXR", "OpenEXR, 16 (half) or 32 bits float, linear", 'exr', ('16', '32')),
)
DEPTHS = (('8', "8-bit", ""), ('16', "16-bit", ""), ('32', "32-bit float", ""))
_SIZES = tuple((str(s), f"{s} x {s}", "") for s in (512, 1024, 2048, 4096, 8192))

# One exported file. `rgb` is a color source (or None) with `a` an optional grey source in
# its alpha; else `r`, `g`, `b`, `a` are grey sources (a grey file when only `r` is set).
# `srgb`: the RGB is color, sRGB encoded except in EXR. `needs`: the file is only written
# when that channel is switched on ('AO': when the material has an AO image).
File = namedtuple("File", "suffix rgb r g b a srgb needs", defaults=(None,) * 6 + (False, None))

# Sources: a channel id (the stack's output, or its default when switched off), SMOOTHNESS
# (1 - roughness), AO (the AO slot, white without one), NORMAL_GL / NORMAL_DX (tangent space,
# +Y and -Y), WHITE. A channel id in an alpha that is switched off drops the alpha.
Preset = namedtuple("Preset", "id name description files")

PRESETS = (
    Preset('PBR', "PBR Maps", "Every channel in its own file", (
        File("Color", rgb='COLOR', srgb=True),
        File("Roughness", r='ROUGHNESS'),
        File("Metallic", r='METALLIC'),
        File("Normal", rgb='NORMAL_GL'),
        File("Height", r='BUMP', needs='BUMP'),
        File("Opacity", r='ALPHA', needs='ALPHA'),
        File("Emission", rgb='EMISSION', srgb=True, needs='EMISSION'),
        File("AO", r='AO', needs='AO'),
    )),
    Preset('UNITY_HDRP', "Unity HDRP", "BaseMap, MaskMap (R metal, G AO, B detail, A smooth), "
           "Normal (OpenGL)", (
        File("BaseMap", rgb='COLOR', a='ALPHA', srgb=True),
        File("MaskMap", r='METALLIC', g='AO', b='WHITE', a='SMOOTHNESS'),
        File("Normal", rgb='NORMAL_GL'),
        File("Emission", rgb='EMISSION', srgb=True, needs='EMISSION'),
    )),
    Preset('UNITY_URP', "Unity URP", "BaseMap, MetallicSmoothness (RGB metal, A smooth), "
           "Normal (OpenGL), Occlusion", (
        File("BaseMap", rgb='COLOR', a='ALPHA', srgb=True),
        File("MetallicSmoothness", r='METALLIC', g='METALLIC', b='METALLIC', a='SMOOTHNESS'),
        File("Normal", rgb='NORMAL_GL'),
        File("Occlusion", r='AO', needs='AO'),
        File("Emission", rgb='EMISSION', srgb=True, needs='EMISSION'),
    )),
    Preset('UNREAL', "Unreal Engine", "BaseColor, OcclusionRoughnessMetallic (ORM), "
           "Normal (DirectX)", (
        File("BaseColor", rgb='COLOR', a='ALPHA', srgb=True),
        File("OcclusionRoughnessMetallic", r='AO', g='ROUGHNESS', b='METALLIC'),
        File("Normal", rgb='NORMAL_DX'),
        File("Emissive", rgb='EMISSION', srgb=True, needs='EMISSION'),
    )),
    Preset('GLTF', "glTF", "BaseColor, OcclusionRoughnessMetallic (ORM), Normal (OpenGL)", (
        File("BaseColor", rgb='COLOR', a='ALPHA', srgb=True),
        File("OcclusionRoughnessMetallic", r='AO', g='ROUGHNESS', b='METALLIC'),
        File("Normal", rgb='NORMAL_GL'),
        File("Emissive", rgb='EMISSION', srgb=True, needs='EMISSION'),
    )),
)
BY_PRESET = {p.id: p for p in PRESETS}


def _fmt(fmt):
    return next(f for f in FORMATS if f[0] == fmt)


def file_depth(fmt, want):
    """The bit depth a file gets: the wanted one when the format has it, else the nearest."""
    depths = _fmt(fmt)[4]
    if want in depths:
        return want
    return depths[-1] if int(want) > int(depths[-1]) else depths[0]


def to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _pixels(img):
    a = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(-1, 4)


def ao_image(mat):
    """The material's AO: the AO slot's image, else an older `<material>_AO` bake, or None."""
    node = mat.node_tree.nodes.get(nodes.map_slot_name('AO'))
    return getattr(node, "image", None) or bpy.data.images.get(f"{mat.name}_AO")


def plan(preset, mat):
    """The files of `preset` this material writes, each as (File, alpha source or None)."""
    active = {c.id for c in active_channels(mat)}
    has_ao = ao_image(mat) is not None
    out = []
    for f in preset.files:
        if f.needs == 'AO' and not has_ao or f.needs not in (None, 'AO') and f.needs not in active:
            continue
        alpha = f.a
        if f.rgb is not None and alpha in BY_ID and alpha not in active:
            alpha = None
        out.append((f, alpha))
    return out


def _sources(files):
    """The baked sources the files need (derived ones map to what they are made from)."""
    need = set()
    for f, alpha in files:
        for s in (f.rgb, f.r, f.g, f.b, alpha):
            if s == 'SMOOTHNESS':
                need.add('ROUGHNESS')
            elif s in ('NORMAL_GL', 'NORMAL_DX'):
                need.add('NORMAL')
            elif s not in (None, 'WHITE'):
                need.add(s)
    return need


class Baker:
    """Bakes the sources of a material's texture set at one size into float Non-Color
    images, dilated, and hands them out as (pixels, 4) arrays. `remove()` frees them."""

    def __init__(self, context, mat, size, dilation):
        self.context, self.mat, self.size, self.dilation = context, mat, size, dilation
        self.images = {}

    def _new(self, name):
        img = bpy.data.images.new(f"__essence_{name}", self.size, self.size, alpha=True,
                                  float_buffer=True)
        img.colorspace_settings.name = 'Non-Color'
        return img

    def _bake(self, objects, image, pass_type, build=None):
        teardowns = []
        try:
            teardowns.append(temp_target(self.mat, image))
            teardowns.append(other_targets(objects, self.mat))
            if build is not None:
                teardowns.append(_emission_override(self.mat, build))
            if 'FINISHED' not in bpy.ops.object.bake(type=pass_type, use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
        finally:
            for t in reversed(teardowns):
                t()

    def bake(self, objects, sources):
        """Bake `sources` (channel ids, NORMAL, AO) from `objects`, the first one active."""
        mat, tail = self.mat, self.mat.node_tree.nodes[self.mat.essence.layers[-1].node_name]
        active = {c.id for c in active_channels(mat)}

        def emit(socket_of):
            def build(nt, x, y):
                return socket_of(nt), []
            return build

        white = None
        scene = self.context.scene
        bake = scene.render.bake
        with filters._BakeState(self.context, objects, 0):
            set_if_changed(scene.cycles, 'samples', 4)      # anti-aliased procedurals
            set_if_changed(bake, 'normal_space', 'TANGENT')
            for name, value in (('normal_r', 'POS_X'), ('normal_g', 'POS_Y'),
                                ('normal_b', 'POS_Z')):
                set_if_changed(bake, name, value)
            cov = self.images['COVERAGE'] = self._new("coverage")
            white = mat.node_tree.nodes.new('ShaderNodeRGB')
            white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
            try:
                self._bake(objects, cov, 'EMIT', emit(lambda nt: white.outputs[0]))
            finally:
                mat.node_tree.nodes.remove(white)
            for s in sorted(sources):
                if s in BY_ID and s in active:
                    label = BY_ID[s].label
                    img = self.images[s] = self._new(s.lower())
                    self._bake(objects, img, 'EMIT', emit(lambda nt, k=label: tail.outputs[k]))
                elif s == 'NORMAL':
                    img = self.images[s] = self._new("normal")
                    self._bake(objects, img, 'NORMAL')
        src = ao_image(mat) if 'AO' in sources else None
        if src is not None:
            ao = self.images['AO'] = src.copy()
            ao.name = "__essence_ao"
            ao.colorspace_settings.name = 'Non-Color'
            if tuple(ao.size) != (self.size, self.size):
                ao.scale(self.size, self.size)
        margin = self.size if self.dilation == 'INFINITE' else self.dilation
        for key, img in self.images.items():
            if key != 'COVERAGE' and margin:
                filters.dilate(img, cov, margin)

    def source(self, s):
        """(pixels, 3) for an RGB source, (pixels,) for a grey one."""
        n = self.size * self.size
        if s == 'WHITE':
            return np.ones(n, dtype=np.float32)
        if s == 'SMOOTHNESS':
            return 1.0 - self.source('ROUGHNESS')
        if s in ('NORMAL_GL', 'NORMAL_DX'):
            px = _pixels(self.images['NORMAL'])[:, :3].copy()
            if s == 'NORMAL_DX':
                px[:, 1] = 1.0 - px[:, 1]
            return px
        img = self.images.get(s)
        if s in BY_ID and BY_ID[s].socket_type == 'NodeSocketColor':
            if img is None:
                return np.tile(np.array(BY_ID[s].default[:3], dtype=np.float32), (n, 1))
            return _pixels(img)[:, :3].copy()
        if img is None:
            return np.full(n, 1.0 if s == 'AO' else BY_ID[s].default, dtype=np.float32)
        return _pixels(img)[:, 0].copy()

    def remove(self):
        for img in self.images.values():
            try:
                bpy.data.images.remove(img)
            except (ReferenceError, RuntimeError):
                pass
        self.images = {}


def compose(baker, f, alpha, encode_srgb):
    """The pixels of one file as (pixels, 4) floats and its color mode."""
    n = baker.size * baker.size
    out = np.ones((n, 4), dtype=np.float32)
    if f.rgb is not None:
        rgb = baker.source(f.rgb)
        out[:, :3] = to_srgb(rgb) if f.srgb and encode_srgb else rgb
        mode = 'RGB'
        if alpha is not None:
            out[:, 3] = baker.source(alpha)
            mode = 'RGBA'
    elif f.g is None and f.b is None and f.a is None:
        out[:, :3] = baker.source(f.r)[:, None]
        mode = 'BW'
    else:
        for i, s in enumerate((f.r, f.g, f.b)):
            out[:, i] = baker.source(s) if s is not None else 0.0
        mode = 'RGB'
        if f.a is not None:
            out[:, 3] = baker.source(f.a)
            mode = 'RGBA'
    return out, mode


def save(scene, px, size, path, fmt, depth, mode):
    """Write `px` ((pixels, 4) floats, written as they are) to `path` through a scene's
    output settings, so the format, bit depth and color mode are exact."""
    # 8 bits go through a byte image, which Blender writes as it is. A float image written
    # to PNG is taken as premultiplied and divided by its alpha (where it is not 0), which
    # would break packed channels, so a 16-bit PNG is multiplied by its alpha first.
    is_float = depth != '8'
    img = bpy.data.images.new("__essence_out", size, size, alpha=True, float_buffer=is_float)
    try:
        img.colorspace_settings.name = 'Non-Color'
        img.alpha_mode = 'CHANNEL_PACKED'
        if fmt != 'OPEN_EXR':
            px = np.clip(px, 0.0, 1.0)
        if is_float and fmt == 'PNG' and mode == 'RGBA':
            px = px.copy()
            a = px[:, 3:4]
            px[:, :3] = np.where(a > 0.0, px[:, :3] * a, px[:, :3])
        img.pixels.foreach_set(px.ravel())
        img.update()
        s = scene.render.image_settings
        s.file_format = fmt
        s.color_mode = 'RGB' if fmt == 'JPEG' and mode == 'RGBA' else mode
        s.color_depth = depth
        if fmt == 'JPEG':
            s.quality = 95
        img.save_render(path, scene=scene)
    finally:
        bpy.data.images.remove(img)


def output_scene():
    """A throwaway scene whose output settings save pixels unchanged: no view transform,
    no look, no dithering."""
    scene = bpy.data.scenes.new("__essence_export")
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.render.dither_intensity = 0.0
    s = scene.render.image_settings
    s.color_management = 'OVERRIDE'
    s.view_settings.view_transform = 'Standard'
    s.view_settings.look = 'None'
    return scene


def _objects(context, mat):
    """The texture set's objects with a UV map, the active object first, or an error."""
    obj = context.active_object
    objects = [o for o in texture_set(context, mat) if o.data.uv_layers]
    if obj not in objects:
        return None, "Object has no UV map"
    objects.remove(obj)
    return [obj] + objects, None


def _poll(context):
    ob = context.active_object
    return (ob is not None and ob.type == 'MESH' and ob.active_material is not None
            and len(ob.active_material.essence.layers) > 0)


class ESSENCE_OT_export(bpy.types.Operator):
    bl_idname = "essence.export"
    bl_label = "Export Textures"
    bl_description = ("Bake and save the selected material's textures with the export preset, "
                      "from every object that uses it")

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        scene = context.scene
        mat = context.active_object.active_material
        if mat.node_tree.nodes.get(mat.essence.layers[-1].node_name) is None:
            self.report({'ERROR'}, "Top layer node is missing from the shader graph")
            return {'CANCELLED'}
        objects, err = _objects(context, mat)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        preset = BY_PRESET[scene.essence_export_preset]
        fmt = scene.essence_export_format
        depth = file_depth(fmt, scene.essence_export_depth)
        size = int(scene.essence_export_size)
        folder = bpy.path.abspath(scene.essence_export_dir)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Cannot create export folder: {e}")
            return {'CANCELLED'}
        stem = bpy.path.clean_name(mat.name)
        files = plan(preset, mat)
        baker = Baker(context, mat, size, _dilation(scene))
        out_scene = None
        written = []
        try:
            baker.bake(objects, _sources(files))
            out_scene = output_scene()
            for f, alpha in files:
                px, mode = compose(baker, f, alpha, encode_srgb=fmt != 'OPEN_EXR')
                path = os.path.join(folder, f"{stem}_{f.suffix}.{_fmt(fmt)[3]}")
                save(out_scene, px, size, path, fmt, depth, mode)
                written.append(os.path.basename(path))
        except Exception as e:  # noqa: BLE001
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}
        finally:
            baker.remove()
            if out_scene is not None:
                bpy.data.scenes.remove(out_scene)
        notes = []
        if 'AO' in {s for f, _a in files for s in (f.r, f.g)} and ao_image(mat) is None:
            notes.append("no AO in the Mesh Maps, AO is white")
        msg = f"Exported {', '.join(written)} to {folder}"
        if notes:
            msg += f" ({'; '.join(notes)})"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# The flattened material: the channels a Principled BSDF takes, baked into packed images.
FLAT = (
    ('COLOR', "Color", "Base Color", True),
    ('ROUGHNESS', "Roughness", "Roughness", False),
    ('METALLIC', "Metallic", "Metallic", False),
    ('ALPHA', "Opacity", "Alpha", False),
    ('EMISSION', "Emission", "Emission Color", True),
)
FLAT_KEY = "essence_baked_from"


def flat_name(mat):
    return f"{mat.name} Baked"


def build_flat(mat, size, images, has_normal):
    """The baked material for `mat`: a Principled BSDF fed by `images` ({channel id: image}),
    reused (and its old images replaced) when it exists, so its assignments stay."""
    flat = bpy.data.materials.get(flat_name(mat))
    if flat is None or flat.get(FLAT_KEY) != mat.name:
        flat = bpy.data.materials.new(flat_name(mat))
    flat[FLAT_KEY] = mat.name
    flat.use_fake_user = True       # unassigned on purpose: kept on save
    flat.use_nodes = True
    nt = flat.node_tree
    old = {n.image for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image is not None}
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (400, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(bsdf.outputs[0], out.inputs['Surface'])
    y = 300
    for ch, _suffix, socket, _color in FLAT:
        img = images.get(ch)
        if img is None:
            continue
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        tex.location = (-500, y)
        y -= 280
        nt.links.new(tex.outputs['Color'], bsdf.inputs[socket])
        if ch == 'EMISSION':
            bsdf.inputs['Emission Strength'].default_value = 1.0
    if has_normal:
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = images['NORMAL']
        tex.location = (-500, y)
        nm = nt.nodes.new('ShaderNodeNormalMap')
        nm.location = (-200, y)
        nt.links.new(tex.outputs['Color'], nm.inputs['Color'])
        nt.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
    for img in old - set(images.values()):
        if img.users == 0:
            bpy.data.images.remove(img)
    return flat


class ESSENCE_OT_bake_material(bpy.types.Operator):
    bl_idname = "essence.bake_material"
    bl_label = "Create Baked Material"
    bl_description = ("Bake the selected material's stack into packed images and a plain "
                      "Principled material named '<material> Baked' (not assigned; it is "
                      "updated in place when baked again)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        scene = context.scene
        mat = context.active_object.active_material
        objects, err = _objects(context, mat)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        size = int(scene.essence_export_size)
        active = {c.id for c in active_channels(mat)}
        wanted = [c for c in FLAT if c[0] in active]
        baker = Baker(context, mat, size, _dilation(scene))
        images = {}
        try:
            baker.bake(objects, {c[0] for c in wanted} | {'NORMAL'})
            for ch, suffix, _socket, color in wanted + [('NORMAL', "Normal", None, False)]:
                if ch == 'NORMAL':
                    px = np.ones((size * size, 4), dtype=np.float32)
                    px[:, :3] = baker.source('NORMAL_GL')
                else:
                    f = File(suffix, rgb=ch, srgb=color) if color else File(suffix, r=ch)
                    px, _mode = compose(baker, f, None, encode_srgb=color)
                name = f"{flat_name(mat)}_{suffix}"
                old = bpy.data.images.get(name)
                if old is not None and old.users == 0:
                    bpy.data.images.remove(old)
                img = bpy.data.images.new(name, size, size, alpha=False)
                # 8-bit: color holds sRGB-encoded values, the rest raw data.
                img.colorspace_settings.name = 'sRGB' if color else 'Non-Color'
                img.pixels.foreach_set(np.clip(px, 0.0, 1.0).ravel())
                img.update()
                img.pack()
                images[ch] = img
        except Exception as e:  # noqa: BLE001
            for img in images.values():
                bpy.data.images.remove(img)
            self.report({'ERROR'}, f"Baking the material failed: {e}")
            return {'CANCELLED'}
        finally:
            baker.remove()
        flat = build_flat(mat, size, images, has_normal=True)
        self.report({'INFO'}, f"Baked into material '{flat.name}' (assign it to use it)")
        return {'FINISHED'}


def _dilation(scene):
    return 'INFINITE' if scene.essence_export_dilation == 'INFINITE' else \
        scene.essence_export_padding


classes = (ESSENCE_OT_export, ESSENCE_OT_bake_material)


def register():
    S = bpy.types.Scene
    S.essence_export_preset = EnumProperty(
        name="Preset", items=[(p.id, p.name, p.description) for p in PRESETS],
        default='UNITY_HDRP', description="Which files are written and what is in them")
    S.essence_export_format = EnumProperty(
        name="Format", items=[f[:3] for f in FORMATS], default='PNG')
    S.essence_export_depth = EnumProperty(
        name="Bit Depth", items=DEPTHS, default='8',
        description="Bits per channel; a format without it uses the nearest it has")
    S.essence_export_size = EnumProperty(name="Resolution", items=_SIZES, default="2048")
    S.essence_export_dilation = EnumProperty(
        name="Dilation", default='INFINITE',
        items=[('INFINITE', "Infinite", "Fill all the space between the UV islands with the "
                "islands' colors (no seams in mipmaps)"),
               ('PIXELS', "Pixels", "Continue the islands by a number of pixels, black beyond")],
        description="Padding around the UV islands")
    S.essence_export_padding = IntProperty(
        name="Pixels", default=16, min=1, max=256, subtype='PIXEL',
        description="How far the islands are continued")
    S.essence_export_dir = StringProperty(
        name="Folder", subtype='DIR_PATH', default="//Export/",
        description="Where the textures are written. // is relative to the .blend file")
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    S = bpy.types.Scene
    for p in ("essence_export_preset", "essence_export_format", "essence_export_depth",
              "essence_export_size", "essence_export_dilation", "essence_export_padding",
              "essence_export_dir"):
        delattr(S, p)
