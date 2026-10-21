"""Baking: AO and World Gradient, ported from the Artistant addon's bake pipeline.

Each selected mesh object gets its own image ("<object>_<Map>"), created (or reused and
overwritten) and left on an unconnected Image Texture node in the object's material(s).
Re-baking reuses the image, so anything that references it (e.g. an Essence image mask)
keeps working. Adding a map type means adding an entry to MAP_TYPES and a branch in
_emission_builder (or a native bake pass).
"""

import os

import bpy
import bpy.utils.previews
from bpy.props import EnumProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Vector

from . import nodes as essence_nodes

# id, label, description, Cycles bake pass
MAP_TYPES = (
    ('AO', "Ambient Occlusion", "Bake ambient occlusion into a texture", 'AO'),
    ('WORLD_GRADIENT', "World Gradient",
     "Black (lowest point) to white (highest point) along world Z, "
     "spanning the bounding box of all selected objects", 'EMIT'),
    ('NORMAL', "Normal (Tangent)",
     "Tangent-space normal map. Flat on a single object; carries detail once "
     "baked from a high-poly. Is wired into the material's Normal after baking", 'NORMAL'),
    ('NORMAL_WORLD', "Normal (World)",
     "World-space normal of the mesh, remapped to 0-1 (ignores the material's bump)",
     'EMIT'),
)
SUFFIX = {'AO': "AO", 'WORLD_GRADIENT': "WorldGradient",
          'NORMAL': "Normal", 'NORMAL_WORLD': "NormalWorld"}

_THUMB_DIR = os.path.join(os.path.dirname(__file__), "assets", "thumbnails")
_previews = None
_items_cache = []  # Blender needs the enum item strings to stay alive


def _map_items(self, context):
    _items_cache.clear()
    for i, (mid, label, desc, _) in enumerate(MAP_TYPES):
        p = _previews.get(mid) if _previews else None
        _items_cache.append((mid, label, desc, p.icon_id if p else 0, i))
    return _items_cache


_SIZES = tuple((str(s), f"{s} x {s}", "") for s in (128, 256, 512, 1024, 2048, 4096))


def _bake_node_name(map_type):
    return f"Essence_Bake_{map_type}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def world_bounds(context, objects):
    """World-space (min, max) corners of the combined bounding box of `objects`."""
    dg = context.evaluated_depsgraph_get()
    lo = Vector((float('inf'),) * 3)
    hi = Vector((float('-inf'),) * 3)
    for obj in objects:
        ev = obj.evaluated_get(dg)
        for corner in ev.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def _ensure_materials(obj):
    mats = [m for m in obj.data.materials if m is not None]
    if mats:
        return mats
    mat = bpy.data.materials.new(f"{obj.name}_Mat")
    mat.use_nodes = True
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat
    return [mat]


def _bake_image(base, size, map_type):
    name = f"{base}_{SUFFIX[map_type]}"
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, size, size, alpha=False)
    elif tuple(img.size) != (size, size):
        img.scale(size, size)
    img.colorspace_settings.name = 'Non-Color'
    return img


def _activate_target(mat, image, map_type):
    """Put `image` on the material's dedicated bake node and make it active.
    Returns a callable restoring the previous active/selected nodes."""
    nt = mat.node_tree
    prev_active = nt.nodes.active
    prev_selected = [n.name for n in nt.nodes if n.select]

    name = _bake_node_name(map_type)
    node = nt.nodes.get(name)
    if node is None or node.type != 'TEX_IMAGE':
        node = nt.nodes.new('ShaderNodeTexImage')
        node.name = name
        node.label = name
        node.location = (-400, 300)
    node.image = image
    for n in nt.nodes:
        n.select = (n.name == node.name)
    nt.nodes.active = node

    def restore():
        for n in nt.nodes:
            n.select = n.name in prev_selected
        if prev_active is not None and nt.nodes.get(prev_active.name) is not None:
            nt.nodes.active = prev_active
    return restore


def _detach_normal_input(mat):
    """Temporarily unlink the Principled BSDF's Normal input so a tangent-normal bake
    doesn't capture the material's own bump/normal map (which Essence applies on top
    afterwards). Returns a teardown callable restoring the link."""
    nt = mat.node_tree
    saved = []
    for n in nt.nodes:
        if n.type == 'BSDF_PRINCIPLED' and n.inputs['Normal'].is_linked:
            saved.append((n.inputs['Normal'], n.inputs['Normal'].links[0].from_socket))
            nt.links.remove(n.inputs['Normal'].links[0])

    def restore():
        for dst, src in saved:
            nt.links.new(src, dst)
    return restore


def _gradient_builder(z_min, z_max):
    if z_max - z_min < 1e-6:
        z_max = z_min + 1.0  # flat: avoid a zero-width range

    def build(nt, x, y):
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        geo.location = (x, y)
        sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        sep.location = (x + 200, y)
        nt.links.new(geo.outputs['Position'], sep.inputs['Vector'])
        mr = nt.nodes.new('ShaderNodeMapRange')
        mr.location = (x + 400, y)
        mr.clamp = True
        mr.inputs['From Min'].default_value = z_min
        mr.inputs['From Max'].default_value = z_max
        mr.inputs['To Min'].default_value = 0.0
        mr.inputs['To Max'].default_value = 1.0
        nt.links.new(sep.outputs['Z'], mr.inputs['Value'])
        return mr.outputs['Result'], [geo, sep, mr]
    return build


def _world_normal_builder():
    def build(nt, x, y):
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        geo.location = (x, y)
        remap = nt.nodes.new('ShaderNodeVectorMath')
        remap.operation = 'MULTIPLY_ADD'  # n * 0.5 + 0.5
        remap.location = (x + 250, y)
        remap.inputs[1].default_value = (0.5, 0.5, 0.5)
        remap.inputs[2].default_value = (0.5, 0.5, 0.5)
        nt.links.new(geo.outputs['Normal'], remap.inputs[0])
        return remap.outputs['Vector'], [geo, remap]
    return build


def _emission_override(mat, build_color):
    """Temporarily route a generated color into the surface output as flat emission.
    Returns a teardown callable restoring the material exactly as it was."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes
                if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    out = out or next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if out is None:
        out = nt.nodes.new('ShaderNodeOutputMaterial')

    surface = out.inputs['Surface']
    original = surface.links[0].from_socket if surface.is_linked else None

    color, created = build_color(nt, out.location.x - 800, out.location.y - 200)
    emit = nt.nodes.new('ShaderNodeEmission')
    emit.name = emit.label = "Essence_Bake_Emission"
    emit.location = (out.location.x - 200, out.location.y - 200)
    nt.links.new(color, emit.inputs['Color'])
    nt.links.new(emit.outputs['Emission'], surface)

    def teardown():
        for l in list(surface.links):
            nt.links.remove(l)
        if original is not None:
            nt.links.new(original, surface)
        nt.nodes.remove(emit)
        for n in created:
            nt.nodes.remove(n)
    return teardown


def _show_in_image_editors(context, image):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'IMAGE_EDITOR' and area.spaces.active.mode == 'VIEW':
                area.spaces.active.image = image


# --------------------------------------------------------------------------
# Operator
# --------------------------------------------------------------------------

# Maps that can be baked from a high-poly collection onto the selected low-poly objects.
HIGH_MAPS = {t[0] for t in MAP_TYPES}  # every map; a future map can opt out here


def _override_high_materials(obj, build_color, done):
    """Emission override for the materials of a high-poly object (selected-to-active
    reads the shader from the high-poly). `done` holds material names already
    overridden so a material shared by several objects is only handled once.
    Returns teardown callables."""
    teardowns = []
    mats = [m for m in obj.data.materials if m is not None]
    if not mats:
        mat = bpy.data.materials.new(f"{obj.name}_EssenceTemp")
        mat.use_nodes = True
        obj.data.materials.append(mat)

        def drop():
            idx = list(obj.data.materials).index(mat)
            obj.data.materials.pop(index=idx)
            bpy.data.materials.remove(mat)
        teardowns.append(drop)
        mats = [mat]
    for mat in mats:
        if mat.name in done:
            continue
        done.add(mat.name)
        if not mat.use_nodes:
            mat.use_nodes = True
        teardowns.append(_emission_override(mat, build_color))
    return teardowns

# Cycles bake settings Essence touches; saved before and restored after every bake.
_BAKE_ATTRS = ('margin', 'normal_space', 'normal_r', 'normal_g', 'normal_b',
               'use_selected_to_active', 'use_cage', 'cage_extrusion',
               'max_ray_distance', 'target')


def high_poly_objects(collection, exclude=()):
    """Mesh objects in `collection` (and its children), minus `exclude`."""
    skip = set(exclude)
    return [o for o in collection.all_objects if o.type == 'MESH' and o not in skip]


def auto_extrusion(obj):
    """Ray start offset: 2% of the object's bounding-box diagonal."""
    return obj.dimensions.length * 0.02


class _ForceSelectable:
    """Temporarily make objects visible and selectable (required for selected-to-active
    baking), then put their visibility flags back."""

    def __init__(self, context, objects):
        self.vl = context.view_layer
        self.objects = objects
        self.saved = []

    def __enter__(self):
        for o in self.objects:
            if o.name not in self.vl.objects:
                raise RuntimeError(f"high-poly '{o.name}' is not in this view layer "
                                   "(its collection is excluded)")
            self.saved.append((o, o.hide_get(), o.hide_viewport, o.hide_select))
            o.hide_set(False)
            o.hide_viewport = False
            o.hide_select = False
            if not o.visible_get(view_layer=self.vl):
                raise RuntimeError(f"high-poly '{o.name}' is hidden by its collection")
        return self

    def __exit__(self, *exc):
        for o, hidden, hide_vp, hide_sel in self.saved:
            try:
                o.hide_set(hidden)
                o.hide_viewport = hide_vp
                o.hide_select = hide_sel
            except ReferenceError:
                pass
        return False


class ESSENCE_OT_bake(bpy.types.Operator):
    bl_idname = "essence.bake"
    bl_label = "Bake"
    bl_description = "Bake the selected map for each selected mesh object"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def _bake_object(self, context, obj, map_type, size, build_color, high=()):
        bake = context.scene.render.bake
        bpy.ops.object.select_all(action='DESELECT')
        bake.use_selected_to_active = bool(high)
        if high:
            extrusion = context.scene.essence_bake_extrusion or auto_extrusion(obj)
            bake.use_cage = False
            bake.cage_extrusion = extrusion
            # Rays start `extrusion` above the low surface; reach `extrusion` below it.
            bake.max_ray_distance = extrusion * 2
            for h in high:
                h.select_set(True)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        image = _bake_image(obj.name, size, map_type)
        teardowns = []
        try:
            overridden = set()
            for mat in _ensure_materials(obj):
                if not mat.use_nodes:
                    mat.use_nodes = True
                teardowns.append(_activate_target(mat, image, map_type))
                if map_type == 'NORMAL':
                    teardowns.append(_detach_normal_input(mat))
                if build_color is not None:
                    teardowns.append(_emission_override(mat, build_color))
                    overridden.add(mat.name)
            if build_color is not None:
                for h in high:
                    teardowns.extend(_override_high_materials(h, build_color, overridden))
            pass_type = next(t[3] for t in MAP_TYPES if t[0] == map_type)
            if 'FINISHED' not in bpy.ops.object.bake(type=pass_type, use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
        finally:
            for t in reversed(teardowns):
                t()
        image.pack()
        if map_type == 'NORMAL':
            for mat in _ensure_materials(obj):
                node = mat.node_tree.nodes.get(_bake_node_name(map_type))
                if node is not None:
                    essence_nodes.apply_normal_map(mat, node)
        return image

    def execute(self, context):
        scene = context.scene
        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        map_type = scene.essence_bake_map
        size = int(scene.essence_bake_size)

        high = []
        collection = scene.essence_bake_high
        if collection is not None and map_type in HIGH_MAPS:
            high = high_poly_objects(collection)
            if not high:
                self.report({'ERROR'}, f"High-poly collection '{collection.name}' has no mesh objects")
                return {'CANCELLED'}
            # The high-poly objects are the source, never a bake target.
            meshes = [o for o in meshes if o not in set(high)]

        todo = [o for o in meshes if o.data.uv_layers]
        if not todo:
            self.report({'ERROR'}, "No low-poly mesh with a UV map selected"
                        if high else "Selected mesh object(s) have no UV map")
            return {'CANCELLED'}
        skipped = [o.name for o in meshes if not o.data.uv_layers]

        build_color = None
        if map_type == 'WORLD_GRADIENT':
            lo, hi = world_bounds(context, todo)
            build_color = _gradient_builder(lo.z, hi.z)
        elif map_type == 'NORMAL_WORLD':
            build_color = _world_normal_builder()

        start_mode = context.mode
        if start_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        orig_selection = list(context.selected_objects)
        orig_active = context.view_layer.objects.active
        orig_engine = scene.render.engine
        orig_samples = scene.cycles.samples
        bake_settings = scene.render.bake
        saved_bake = {a: getattr(bake_settings, a) for a in _BAKE_ATTRS}

        baked, failed, last_image = [], [], None
        forced = _ForceSelectable(context, high)
        try:
            forced.__enter__()
            scene.render.engine = 'CYCLES'
            bake_settings.target = 'IMAGE_TEXTURES'
            scene.render.bake.normal_space = 'TANGENT'
            scene.render.bake.normal_r = 'POS_X'
            scene.render.bake.normal_g = 'POS_Y'  # OpenGL (+Y), Blender's convention
            scene.render.bake.normal_b = 'POS_Z'
            scene.cycles.samples = scene.essence_bake_samples
            scene.render.bake.margin = scene.essence_bake_margin
            for obj in todo:
                try:
                    last_image = self._bake_object(context, obj, map_type, size,
                                                   build_color, high)
                    baked.append(obj.name)
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{obj.name} ({e})")
        except RuntimeError as e:  # high-poly visibility problems
            failed.append(str(e))
        finally:
            forced.__exit__()
            scene.render.engine = orig_engine
            scene.cycles.samples = orig_samples
            for a, v in saved_bake.items():
                setattr(bake_settings, a, v)
            bpy.ops.object.select_all(action='DESELECT')
            for o in orig_selection:
                try:
                    o.select_set(True)
                except Exception:  # noqa: BLE001
                    pass
            if orig_active is not None:
                context.view_layer.objects.active = orig_active
            if start_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='EDIT' if start_mode.startswith('EDIT_') else start_mode)

        if last_image is not None:
            _show_in_image_editors(context, last_image)
        if skipped:
            self.report({'WARNING'}, f"Skipped (no UV map): {', '.join(skipped)}")
        if failed:
            level = 'WARNING' if baked else 'ERROR'
            self.report({level}, f"Failed: {', '.join(failed)}")
        elif baked:
            src = f" from '{collection.name}'" if high else ""
            self.report({'INFO'}, f"Baked {len(baked)} object(s){src}")
        return {'FINISHED'} if baked else {'CANCELLED'}


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for mid, *_ in MAP_TYPES:
        path = os.path.join(_THUMB_DIR, f"bake_{mid.lower()}.png")
        if os.path.isfile(path):
            _previews.load(mid, path, 'IMAGE')

    S = bpy.types.Scene
    S.essence_bake_map = EnumProperty(name="Map", description="Which map to bake",
                                      items=_map_items)
    S.essence_bake_size = EnumProperty(name="Size", items=_SIZES, default="1024")
    S.essence_bake_samples = IntProperty(name="Samples", default=32, min=1, soft_max=512)
    S.essence_bake_margin = IntProperty(name="Margin", default=16, min=0, soft_max=64,
                                        description="Padding in pixels around UV islands")
    S.essence_bake_high = PointerProperty(
        name="High Poly", type=bpy.types.Collection,
        description="Collection of high-poly meshes to project onto the selected "
                    "low-poly objects. Leave empty to bake the low-poly alone")
    S.essence_bake_extrusion = FloatProperty(
        name="Extrusion", default=0.0, min=0.0, subtype='DISTANCE',
        description="How far above the low-poly surface rays start. "
                    "0 = automatic (2% of the object's size)")
    bpy.utils.register_class(ESSENCE_OT_bake)


def unregister():
    global _previews
    bpy.utils.unregister_class(ESSENCE_OT_bake)
    S = bpy.types.Scene
    for p in ("essence_bake_map", "essence_bake_size",
              "essence_bake_samples", "essence_bake_margin",
              "essence_bake_high", "essence_bake_extrusion"):
        delattr(S, p)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
