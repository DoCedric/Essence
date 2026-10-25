"""Baking: AO and World Gradient, ported from the Artistant addon's bake pipeline.

Each selected mesh object gets its own image ("<object>_<Map>"), created (or reused and
overwritten) and left on an unconnected Image Texture node in the object's material(s).
Re-baking reuses the image, so anything that references it (e.g. an Essence image mask)
keeps working. Adding a map type means adding an entry to MAP_TYPES and a branch in
_emission_builder (or a native bake pass).
"""

import colorsys
import contextlib
import hashlib
import os
import uuid

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Vector

from . import nodes as essence_nodes
from . import sources

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
    ('POSITION', "Position",
     "Where each texel is, 0 to 1 across the low-poly's bounding box (what the live Gradient "
     "generator reads). Baked from a high-poly it is the position of the high-poly surface",
     'EMIT'),
    ('ID', "ID",
     "A flat color per part: per object, per material, per mesh island or per UV island "
     "(choose below). The color is stable, so a mask can pick one", 'EMIT'),
)
SUFFIX = {'AO': "AO", 'WORLD_GRADIENT': "WorldGradient",
          'NORMAL': "Normal", 'NORMAL_WORLD': "NormalWorld", 'POSITION': "Position",
          'ID': "ID"}

# What an ID map tells apart. All but the UV island work from a high poly too: the color is
# that of the part the ray hits.
ID_MODES = (
    ('OBJECT', "Object", "One color per object: with a high poly, per high-poly part"),
    ('MATERIAL', "Material", "One color per material"),
    ('MESH_ISLAND', "Mesh Island",
     "One color per connected piece of geometry (of the high poly when there is one)"),
    ('UV_ISLAND', "UV Island",
     "One color per UV island of the low poly itself (a High Poly collection is ignored)"),
)
UV_ISLAND_ATTRIBUTE = "essence_uv_island"
_GOLDEN = 0.6180339887498949

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


_AA = (('1', "Off", "Bake at the final size"),
       ('2', "2x", "Bake at twice the size and average down: smoother edges, 4x the pixels"),
       ('4', "4x", "Bake at four times the size and average down: 16x the pixels"))
_MAX_BAKE_SIZE = 8192


def _bake_node_name(map_type):
    return f"Essence_Bake_{map_type}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def set_if_changed(obj, name, value):
    """Assign a property only when it differs. Assigning an RNA property runs its update even
    for the same value, and for the scene's render engine that update resets the engine and the
    GPU data of every viewport: with a Material Preview viewport and a heavy material that
    deadlocked Blender (the log stopped at that very assignment, with the scene already on
    Cycles). So settings a bake needs are only touched when they are not already right, and
    only put back when they were changed."""
    if getattr(obj, name) != value:
        setattr(obj, name, value)
        return True
    return False


_DEVICES = (
    ('AUTO', "Auto (GPU when available)",
     "The GPU when Cycles has one switched on in Preferences > System, else the CPU"),
    ('CPU', "CPU", "Always the CPU"),
)


def gpu_device_name():
    """The name of the GPU Cycles would use, or None when it has none: no compute device type
    chosen in Preferences > System, or no device of that type switched on. This is Cycles' own
    test (the same one that enables "GPU Compute" in the render settings)."""
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        if not prefs.has_active_device():
            return None
        names = sorted({d.name for d in prefs.get_devices_for_type(prefs.compute_device_type)
                        if d.use and d.type != 'CPU'})
    except (KeyError, AttributeError, RuntimeError):
        return None
    return ", ".join(names) or None


def resolve_device(scene):
    """('GPU' or 'CPU', what to tell the user) for the Device setting of the Bake panel."""
    if scene.essence_bake_device == 'CPU':
        return 'CPU', "the CPU, as chosen"
    gpu = gpu_device_name()
    if gpu is not None:
        return 'GPU', f"the GPU: {gpu}"
    return 'CPU', "the CPU: no GPU is enabled in Preferences > System"


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


def _bake_image(base, size, map_type, float_buffer=True):
    name = f"{base}_{SUFFIX[map_type]}"
    img = bpy.data.images.get(name)
    if img is not None and (img.is_float != float_buffer or tuple(img.size) != (size, size)):
        # Neither the buffer type nor (for a packed image) the size can change in place:
        # Image.scale is silently ignored once the image is packed. Make a new image and
        # move the users over, so references such as mask slots survive.
        new = bpy.data.images.new(f"{name}.tmp", size, size, alpha=False,
                                  float_buffer=float_buffer)
        img.user_remap(new)
        bpy.data.images.remove(img)
        new.name = name
        img = new
    if img is None:
        img = bpy.data.images.new(name, size, size, alpha=False,
                                  float_buffer=float_buffer)
    img.colorspace_settings.name = 'Non-Color'
    return img


def _downscale(image, size):
    """Halve `image` until it is `size` square. Each halving averages 2x2 pixels exactly,
    which a single bilinear jump from 4x would not (it skips texels)."""
    while image.size[0] > size:
        half = max(size, image.size[0] // 2)
        image.scale(half, half)


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


def _position_builder(context, obj):
    """The low poly's Generated coordinates, evaluated wherever the ray hits: its object space
    position remapped to 0..1 across its bounding box (an axis with no extent reads 0.5, as
    Blender does). A shader sees the hit point in world space, and a high-poly hit belongs to
    another object, so the low poly's transform is applied by a Mapping node in Texture mode
    (the inverse of its location, rotation and scale)."""
    ev = obj.evaluated_get(context.evaluated_depsgraph_get())
    corners = [Vector(c) for c in ev.bound_box]
    lo = Vector(min(c[i] for c in corners) for i in range(3))
    hi = Vector(max(c[i] for c in corners) for i in range(3))
    center = (lo + hi) / 2
    extent = Vector(e if e > 1e-6 else 1.0 for e in (hi - lo))
    loc, rot, scale = obj.matrix_world.decompose()

    def build(nt, x, y):
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        geo.location = (x, y)
        to_obj = nt.nodes.new('ShaderNodeMapping')
        to_obj.vector_type = 'TEXTURE'      # the inverse of the object's transform
        to_obj.inputs['Location'].default_value = loc
        to_obj.inputs['Rotation'].default_value = rot.to_euler('XYZ')
        to_obj.inputs['Scale'].default_value = scale
        to_obj.location = (x + 200, y)
        nt.links.new(geo.outputs['Position'], to_obj.inputs['Vector'])
        sub = nt.nodes.new('ShaderNodeVectorMath')
        sub.operation = 'SUBTRACT'
        sub.inputs[1].default_value = center
        sub.location = (x + 400, y)
        nt.links.new(to_obj.outputs['Vector'], sub.inputs[0])
        div = nt.nodes.new('ShaderNodeVectorMath')
        div.operation = 'DIVIDE'
        div.inputs[1].default_value = extent
        div.location = (x + 600, y)
        nt.links.new(sub.outputs['Vector'], div.inputs[0])
        add = nt.nodes.new('ShaderNodeVectorMath')
        add.operation = 'ADD'
        add.inputs[1].default_value = (0.5, 0.5, 0.5)
        add.location = (x + 800, y)
        nt.links.new(div.outputs['Vector'], add.inputs[0])
        return add.outputs['Vector'], [geo, to_obj, sub, div, add]
    return build


def name_color(name):
    """A stable, saturated color for a name (a material's ID color)."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16) / 0xffffffff
    return (*colorsys.hsv_to_rgb(h, 0.8, 0.9), 1.0)


def uv_island_indices(mesh):
    """The UV island of every polygon of `mesh` (its active UV map), numbered in order of
    appearance. Faces that share a UV vertex (the same mesh vertex at the same UV position)
    are in one island."""
    uv = mesh.uv_layers.active
    n = len(mesh.polygons)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    coords = [0.0] * (2 * len(mesh.loops))
    uv.uv.foreach_get("vector", coords)
    verts = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", verts)
    seen = {}
    for p in mesh.polygons:
        for li in p.loop_indices:
            key = (verts[li], round(coords[2 * li], 5), round(coords[2 * li + 1], 5))
            other = seen.setdefault(key, p.index)
            if other != p.index:
                parent[find(p.index)] = find(other)
    numbers, out = {}, []
    for i in range(n):
        out.append(numbers.setdefault(find(i), len(numbers)))
    return out


@contextlib.contextmanager
def _uv_island_attribute(obj):
    """Give the mesh a face attribute with one value per UV island while it is baked (a shader
    cannot tell UV islands apart), and take it away again, whatever happens."""
    mesh = obj.data
    if UV_ISLAND_ATTRIBUTE in mesh.attributes:
        mesh.attributes.remove(mesh.attributes[UV_ISLAND_ATTRIBUTE])
    islands = uv_island_indices(mesh)
    attr = mesh.attributes.new(UV_ISLAND_ATTRIBUTE, 'FLOAT', 'FACE')
    attr.data.foreach_set("value", [(i * _GOLDEN) % 1.0 for i in islands])
    try:
        yield len(set(islands))
    finally:
        if UV_ISLAND_ATTRIBUTE in mesh.attributes:
            mesh.attributes.remove(mesh.attributes[UV_ISLAND_ATTRIBUTE])


def _material_of(node_tree):
    """The material a node tree belongs to (an embedded tree is its own datablock, so its
    id_data is the tree, not the material)."""
    return next((m for m in bpy.data.materials if m.node_tree is node_tree), None)


def _id_builder(mode):
    """A flat color per part. Object, mesh island and UV island give a hue from a number the
    shader can read (Object Info Random, Geometry Random Per Island, the island attribute);
    a material is a constant color from its name, since its shader knows which material it is."""
    def build(nt, x, y):
        if mode == 'MATERIAL':
            rgb = nt.nodes.new('ShaderNodeRGB')
            mat = _material_of(nt)
            rgb.outputs[0].default_value = name_color(mat.name if mat is not None else nt.name)
            rgb.location = (x, y)
            return rgb.outputs[0], [rgb]
        if mode == 'OBJECT':
            src = nt.nodes.new('ShaderNodeObjectInfo')
            hue = src.outputs['Random']
        elif mode == 'MESH_ISLAND':
            src = nt.nodes.new('ShaderNodeNewGeometry')
            hue = src.outputs['Random Per Island']
        else:
            src = nt.nodes.new('ShaderNodeAttribute')
            src.attribute_type = 'GEOMETRY'
            src.attribute_name = UV_ISLAND_ATTRIBUTE
            hue = src.outputs['Factor']
        src.location = (x, y)
        comb = nt.nodes.new('ShaderNodeCombineColor')
        comb.mode = 'HSV'
        comb.inputs['Green'].default_value = 0.8
        comb.inputs['Blue'].default_value = 0.9
        comb.location = (x + 250, y)
        nt.links.new(hue, comb.inputs['Red'])
        return comb.outputs['Color'], [src, comb]
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
               'use_selected_to_active', 'use_cage', 'cage_extrusion', 'cage_object',
               'max_ray_distance', 'target')


def high_poly_objects(collection, exclude=()):
    """Mesh objects in `collection` (and its children), minus `exclude`."""
    skip = set(exclude)
    return [o for o in collection.all_objects if o.type == 'MESH' and o not in skip]


def auto_extrusion(obj):
    """Ray start offset: 2% of the object's bounding-box diagonal."""
    return obj.dimensions.length * 0.02


def _layer_collection_path(layer_collection, collection):
    """The chain of LayerCollections from the scene root down to `collection`, or None."""
    if layer_collection.collection == collection:
        return [layer_collection]
    for child in layer_collection.children:
        path = _layer_collection_path(child, collection)
        if path is not None:
            return [layer_collection] + path
    return None


class _ForceSelectable:
    """Temporarily make objects visible and selectable (required for selected-to-active
    baking), then put their visibility flags back. Collections that are excluded or hidden
    are switched on for the bake too, and switched off again afterwards, so the user's
    outliner looks the same as before."""

    def __init__(self, context, objects):
        self.vl = context.view_layer
        self.objects = objects
        self.saved = []
        self.saved_collections = []   # (collection, exclude, hide_viewport of its layer collection, ...)
        self._seen = set()

    def _enable_collections(self, o):
        """Un-exclude and un-hide every collection that holds `o`, from the root down."""
        todo = []
        for coll in o.users_collection:
            # [1:]: the scene's root collection cannot be excluded or hidden, and assigning
            # to it (even the same value) resets the state of the collections below it.
            for lc in (_layer_collection_path(self.vl.layer_collection, coll) or ())[1:]:
                if lc.name not in self._seen:
                    self._seen.add(lc.name)
                    c = lc.collection
                    # Everything is read before anything is changed: changing a parent
                    # rebuilds the layer collections below it.
                    self.saved_collections.append(
                        (c, lc.exclude, lc.hide_viewport, c.hide_viewport, c.hide_select))
                    todo.append(c)
        for c in todo:
            path = _layer_collection_path(self.vl.layer_collection, c)
            if path:
                path[-1].exclude = False
                path[-1].hide_viewport = False
            c.hide_viewport = False
            c.hide_select = False

    def __enter__(self):
        for o in self.objects:
            if o.name not in self.vl.objects or not o.visible_get(view_layer=self.vl):
                self._enable_collections(o)
            if o.name not in self.vl.objects:
                raise RuntimeError(f"high-poly '{o.name}' is not in this view layer")
            self.saved.append((o, o.hide_get(), o.hide_viewport, o.hide_select))
            o.hide_set(False)
            o.hide_viewport = False
            o.hide_select = False
            if not o.visible_get(view_layer=self.vl):
                raise RuntimeError(f"high-poly '{o.name}' is still hidden")
        return self

    def __exit__(self, *exc):
        for o, hidden, hide_vp, hide_sel in self.saved:   # while the objects are still in
            try:                                         # the view layer
                o.hide_set(hidden)
                o.hide_viewport = hide_vp
                o.hide_select = hide_sel
            except (ReferenceError, RuntimeError):
                pass
        for c, exclude, hide_vp, c_hide_vp, c_hide_sel in reversed(self.saved_collections):
            try:
                c.hide_select = c_hide_sel
                c.hide_viewport = c_hide_vp
                # Layer collections are looked up again: toggling exclude rebuilds them, so a
                # reference kept from before would be stale.
                path = _layer_collection_path(self.vl.layer_collection, c)
                if path:
                    path[-1].hide_viewport = hide_vp
                    path[-1].exclude = exclude
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
            cage = context.scene.essence_bake_cage
            if cage is not None:
                # Rays start on the cage and travel to the low surface; the cage replaces
                # the extrusion and the distance limit.
                bake.use_cage = True
                bake.cage_object = cage
                bake.max_ray_distance = 0.0
            else:
                bake.use_cage = False
                bake.cage_extrusion = extrusion
                # Rays start `extrusion` above the low surface; reach `extrusion` below.
                bake.max_ray_distance = extrusion * 2
            for h in high:
                h.select_set(True)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        aa = int(context.scene.essence_bake_aa)
        image = _bake_image(obj.name, size * aa, map_type,
                            context.scene.essence_bake_float)
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
        if aa > 1:
            _downscale(image, size)
        image.pack()
        image["essence_stamp"] = uuid.uuid4().hex[:8]   # derived maps notice a new bake
        if map_type == 'NORMAL':
            for mat in _ensure_materials(obj):
                node = mat.node_tree.nodes.get(_bake_node_name(map_type))
                if node is not None:
                    essence_nodes.apply_normal_map(mat, node)
        elif map_type == 'AO':
            for mat in _ensure_materials(obj):
                essence_nodes.apply_ao(mat)
        elif map_type == 'POSITION':
            for mat in _ensure_materials(obj):
                sources.wire_baked_inputs(mat.node_tree)   # a generator may read it now
        return image

    def execute(self, context):
        scene = context.scene
        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        map_type = scene.essence_bake_map
        size = int(scene.essence_bake_size)

        high = []
        collection = scene.essence_bake_high
        uv_islands = map_type == 'ID' and scene.essence_bake_id == 'UV_ISLAND'
        if collection is not None and map_type in HIGH_MAPS and not uv_islands:
            high = high_poly_objects(collection)
            if not high:
                self.report({'ERROR'}, f"High-poly collection '{collection.name}' has no mesh objects")
                return {'CANCELLED'}
            # The high-poly objects are the source, never a bake target.
            meshes = [o for o in meshes if o not in set(high)]

        if size * int(scene.essence_bake_aa) > _MAX_BAKE_SIZE:
            self.report({'ERROR'}, f"{size} px with {scene.essence_bake_aa}x anti-aliasing "
                        f"bakes at {size * int(scene.essence_bake_aa)} px, above the "
                        f"{_MAX_BAKE_SIZE} px limit. Lower the size or anti-aliasing")
            return {'CANCELLED'}

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
        elif map_type == 'ID':
            build_color = _id_builder(scene.essence_bake_id)

        start_mode = context.active_object.mode if context.active_object else 'OBJECT'
        if start_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        orig_selection = list(context.selected_objects)
        orig_active = context.view_layer.objects.active
        orig_engine = scene.render.engine
        orig_samples = scene.cycles.samples
        orig_device = scene.cycles.device
        device, _device_note = resolve_device(scene)
        fell_back = False
        bake_settings = scene.render.bake
        saved_bake = {a: getattr(bake_settings, a) for a in _BAKE_ATTRS}

        baked, failed, last_image = [], [], None
        forced = _ForceSelectable(context, high)
        try:
            forced.__enter__()
            set_if_changed(scene.render, 'engine', 'CYCLES')
            set_if_changed(bake_settings, 'target', 'IMAGE_TEXTURES')
            set_if_changed(bake_settings, 'normal_space', 'TANGENT')
            set_if_changed(bake_settings, 'normal_r', 'POS_X')
            set_if_changed(bake_settings, 'normal_g', 'POS_Y')  # OpenGL (+Y), Blender's convention
            set_if_changed(bake_settings, 'normal_b', 'POS_Z')
            set_if_changed(scene.cycles, 'samples', scene.essence_bake_samples)
            set_if_changed(scene.cycles, 'device', device)
            set_if_changed(bake_settings, 'margin',
                           scene.essence_bake_margin * int(scene.essence_bake_aa))

            def bake_it(obj):
                return self._bake_object(
                    context, obj, map_type, size,
                    _position_builder(context, obj) if map_type == 'POSITION'
                    else build_color, high)

            for obj in todo:
                try:
                    with (_uv_island_attribute(obj) if uv_islands
                          else contextlib.nullcontext()):
                        try:
                            last_image = bake_it(obj)
                        except Exception:  # noqa: BLE001
                            if device != 'GPU' or scene.essence_bake_device == 'CPU':
                                raise
                            # The GPU failed (out of memory, a driver): this and the rest
                            # of the objects go on the CPU.
                            device, fell_back = 'CPU', True
                            set_if_changed(scene.cycles, 'device', 'CPU')
                            last_image = bake_it(obj)
                    baked.append(obj.name)
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{obj.name} ({e})")
        except RuntimeError as e:  # high-poly visibility problems
            failed.append(str(e))
        finally:
            forced.__exit__()
            set_if_changed(scene.render, 'engine', orig_engine)
            set_if_changed(scene.cycles, 'samples', orig_samples)
            set_if_changed(scene.cycles, 'device', orig_device)
            for a, v in saved_bake.items():
                set_if_changed(bake_settings, a, v)
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

        if last_image is not None:
            _show_in_image_editors(context, last_image)
        if skipped:
            self.report({'WARNING'}, f"Skipped (no UV map): {', '.join(skipped)}")
        if failed:
            level = 'WARNING' if baked else 'ERROR'
            self.report({level}, f"Failed: {', '.join(failed)}")
        elif baked:
            src = f" from '{collection.name}'" if high else ""
            if fell_back:
                self.report({'WARNING'}, f"Baked {len(baked)} object(s){src}; the GPU failed, "
                                         "so the CPU was used")
            else:
                self.report({'INFO'}, f"Baked {len(baked)} object(s){src} on the {device}")
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
    S.essence_bake_cage = PointerProperty(
        name="Cage", type=bpy.types.Object, poll=lambda self, o: o.type == 'MESH',
        description="Optional cage mesh: rays start on it instead of at the extrusion "
                    "offset. Needs the same topology as the low-poly, pushed outwards")
    S.essence_bake_aa = EnumProperty(
        name="Anti-aliasing", items=_AA, default='1',
        description="Supersampling: bake larger, then average down to the final size "
                    "(Cycles samples only smooth shading, not the hard edges between "
                    "hit and miss, or between UV islands and the background)")
    S.essence_bake_id = EnumProperty(
        name="ID by", items=ID_MODES, default='OBJECT',
        description="What the ID map tells apart")
    S.essence_bake_device = EnumProperty(
        name="Device", items=_DEVICES, default='AUTO',
        description="What the bake runs on. The small baked filters and the curvature always "
                    "use the CPU, which is quick for them and keeps the GPU out of the viewport")
    S.essence_bake_float = BoolProperty(
        name="Float", default=True,
        description="Bake into a 32-bit float image (no banding, four times the memory)")
    bpy.utils.register_class(ESSENCE_OT_bake)


def unregister():
    global _previews
    bpy.utils.unregister_class(ESSENCE_OT_bake)
    S = bpy.types.Scene
    for p in ("essence_bake_map", "essence_bake_size",
              "essence_bake_samples", "essence_bake_margin",
              "essence_bake_high", "essence_bake_extrusion",
              "essence_bake_cage", "essence_bake_float", "essence_bake_aa",
              "essence_bake_id", "essence_bake_device"):
        delattr(S, p)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
