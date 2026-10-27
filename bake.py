"""Baking mesh maps, ported from the Artistant addon's bake pipeline.

A map belongs to a **texture set**: a material and every mesh object in the view layer that
uses it. The selected objects decide which texture sets are baked; all objects of a set bake
into one image ("<material>_<Map>", so their UVs must not overlap), left on the material's
unconnected Image Texture node `Essence_Bake_<MAP>`. Re-baking reuses the image, so anything
that references it (e.g. an Essence image mask) keeps working. High Poly, Extrusion and Size
are settings of the material; a cage belongs to one low-poly object.

Cycles' margin fills every pixel a bake did not write, including the islands of the other
objects of the set (tested: one call with several objects selected, and one call per object
without clearing, both overwrite). So a set of several objects is baked with margin 0 and the
margin is added afterwards by the compositor (`filters.add_margin`). Adding a map type means
adding an entry to MAP_TYPES and a builder (or a native bake pass).
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
    ('THICKNESS', "Thickness",
     "How thick the mesh is under each texel: bright where thick, dark where thin (ambient "
     "occlusion from the inside, within a distance). Baked from a high-poly it is the "
     "thickness of the high-poly", 'EMIT'),
)
SUFFIX = {'AO': "AO", 'WORLD_GRADIENT': "WorldGradient",
          'NORMAL': "Normal", 'NORMAL_WORLD': "NormalWorld", 'POSITION': "Position",
          'ID': "ID", 'THICKNESS': "Thickness"}

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
# The face attribute an ID bake reads its hue from (objects of a merged high poly, materials,
# UV islands); written for the bake and removed again.
ID_ATTRIBUTE = "essence_id"
UV_ISLAND_ATTRIBUTE = ID_ATTRIBUTE
# Object types with a surface a bake ray can hit (a curve with a bevel, text, ...).
SURFACE_TYPES = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}
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


def texture_set(context, mat):
    """The mesh objects of the view layer that use `mat`, minus any high poly or floater: the
    objects that bake into the material's maps."""
    pool = high_poly_pool()
    return [o for o in context.view_layer.objects
            if o is not None and o.type == 'MESH' and o not in pool
            and any(s.material == mat for s in o.material_slots)]


def temp_target(mat, image):
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


def other_targets(objects, mat):
    """Cycles bakes every face into the active image of the face's material. Faces of the
    objects' other materials go to a throwaway image, so only `mat`'s image is written.
    Returns a teardown."""
    others = {s.material for o in objects for s in o.material_slots
              if s.material is not None and s.material != mat}
    if not others:
        return lambda: None
    junk = bpy.data.images.new(".Essence Bake Other", 8, 8)
    teardowns = []
    for m in others:
        if not m.use_nodes:
            m.use_nodes = True
        teardowns.append(temp_target(m, junk))

    def restore():
        for t in reversed(teardowns):
            t()
        bpy.data.images.remove(junk)
    return restore


def _set_image(mat, objects, size, map_type, float_buffer):
    """The texture set's image for a map. A file baked before texture sets has it under an
    object's name: that image is renamed, so whatever uses it (a mask, a source) follows."""
    name = f"{mat.name}_{SUFFIX[map_type]}"
    if bpy.data.images.get(name) is None:
        node = mat.node_tree.nodes.get(_bake_node_name(map_type))
        old = getattr(node, "image", None)
        if old is not None and old.name in {f"{o.name}_{SUFFIX[map_type]}" for o in objects}:
            old.name = name
    return _bake_image(name, size, map_type, float_buffer)


def _bake_image(name, size, map_type, float_buffer=True):
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


def name_hue(name):
    """A stable hue for a name (an object's or a material's ID color)."""
    return int(hashlib.md5(name.encode()).hexdigest()[:8], 16) / 0xffffffff


def name_color(name):
    """A stable, saturated color for a name (an object's or a material's ID color)."""
    return (*colorsys.hsv_to_rgb(name_hue(name), 0.8, 0.9), 1.0)


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


def _id_hues(obj, mode, first=0, name=None):
    """The ID hue of every face of `obj` for an "ID by" mode, and how many UV islands were
    numbered. A shader cannot tell objects of a merged high poly, materials or UV islands
    apart, so all of them reach it as a face attribute. Mesh islands need none (None).
    `name` is the object's name for its ID (the original's, for a copy)."""
    mesh = obj.data
    if mode == 'OBJECT':
        return [name_hue(name or obj.name)] * len(mesh.polygons), 0
    if mode == 'MATERIAL':
        names = [s.material.name if s.material is not None else "" for s in obj.material_slots]
        hues = [name_hue(n) for n in names] or [name_hue("")]
        idx = [0] * len(mesh.polygons)
        mesh.polygons.foreach_get("material_index", idx)
        return [hues[min(i, len(hues) - 1)] for i in idx], 0
    if mode == 'UV_ISLAND':
        islands = uv_island_indices(mesh)
        return [((first + i) * _GOLDEN) % 1.0 for i in islands], len(set(islands))
    return None, 0


def _write_id(mesh, hues):
    if ID_ATTRIBUTE in mesh.attributes:
        mesh.attributes.remove(mesh.attributes[ID_ATTRIBUTE])
    attr = mesh.attributes.new(ID_ATTRIBUTE, 'FLOAT', 'FACE')
    attr.data.foreach_set("value", hues)


@contextlib.contextmanager
def _id_attribute(obj, mode, first=0):
    """Give the low poly's mesh its ID hues as a face attribute while it is baked, and take it
    away again, whatever happens. UV islands are numbered from `first`, so the objects of one
    texture set get different colors. Yields the number of UV islands."""
    hues, count = _id_hues(obj, mode, first)
    if hues is None:
        yield 0
        return
    _write_id(obj.data, hues)
    try:
        yield count
    finally:
        if ID_ATTRIBUTE in obj.data.attributes:
            obj.data.attributes.remove(obj.data.attributes[ID_ATTRIBUTE])


def _uv_island_attribute(obj, first=0):
    return _id_attribute(obj, 'UV_ISLAND', first)


def _id_builder(mode):
    """A flat color per part: a hue the shader reads (the ID attribute, or Geometry Random Per
    Island for mesh islands), at a fixed saturation and value."""
    def build(nt, x, y):
        if mode == 'MESH_ISLAND':
            src = nt.nodes.new('ShaderNodeNewGeometry')
            hue = src.outputs['Random Per Island']
        else:
            src = nt.nodes.new('ShaderNodeAttribute')
            src.attribute_type = 'GEOMETRY'
            src.attribute_name = ID_ATTRIBUTE
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


def _thickness_builder(distance):
    """Ambient occlusion from the inside: rays go into the mesh, and the fewer of them reach the
    other side within `distance`, the thicker it is (bright), the more, the thinner (dark)."""
    def build(nt, x, y):
        ao = nt.nodes.new('ShaderNodeAmbientOcclusion')
        ao.inside = True
        ao.only_local = False
        ao.samples = 16
        ao.inputs['Distance'].default_value = distance
        ao.location = (x, y)
        return ao.outputs['AO'], [ao]
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
    """Objects with a surface in `collection` (and its children), minus `exclude`."""
    skip = set(exclude)
    return [o for o in collection.all_objects if o.type in SURFACE_TYPES and o not in skip]


def bake_group(obj, mat):
    """The baking group of a low-poly object: (High Poly collection, Floaters collection).
    Its own High Poly, else the material's; floaters are the object's own only."""
    return obj.essence_bake_high or mat.essence.bake_high, obj.essence_bake_floaters


def high_poly_pool():
    """Every object in a High Poly or Floaters collection that some object or material names:
    those are sources, never bake targets."""
    colls = {m.essence.bake_high for m in bpy.data.materials if m.essence.bake_high}
    for o in bpy.data.objects:
        colls |= {c for c in (getattr(o, "essence_bake_high", None),
                              getattr(o, "essence_bake_floaters", None)) if c is not None}
    return {h for c in colls for h in high_poly_objects(c)}


def auto_extrusion(obj):
    """Ray start offset: 2% of the object's bounding-box diagonal."""
    return obj.dimensions.length * 0.02


def merge_high(context, objects, id_mode=None):
    """One temporary mesh object made of `objects` with their modifiers applied, placed where
    they are. Blender's selected-to-active bake costs per high-poly object, not per triangle
    (on a real file 60 objects took 27 s, the same geometry as one mesh 1.1 s), so every
    high-to-low bake reads a merge. With an ID mode the parts' ID hues are written first, so ID
    by object and by material survive the merge. Faces without a material get a temporary one.
    Returns (object, teardown)."""
    temps, meshes = [], []
    # excluded collections are not evaluated
    with _ForceSelectable(context, objects), _render_settings(objects):
        dg = context.evaluated_depsgraph_get()
        dg.update()
        for o in objects:
            me = bpy.data.meshes.new_from_object(o.evaluated_get(dg),
                                                 preserve_all_data_layers=True, depsgraph=dg)
            if not me.polygons:
                bpy.data.meshes.remove(me)
                continue
            meshes.append(me.name)
            t = bpy.data.objects.new(o.name, me)
            t.matrix_world = o.matrix_world
            context.scene.collection.objects.link(t)
            if id_mode is not None:
                # the part's own name: the copy's is "<name>.001"
                hues, _ = _id_hues(t, id_mode, name=o.name)
                if hues is not None:
                    _write_id(me, hues)
            temps.append(t)
    if not temps:
        raise RuntimeError("the high-poly objects have no surface")
    context.view_layer.update()
    merged = temps[0]
    if len(temps) > 1:
        with context.temp_override(active_object=merged, object=merged,
                                   selected_objects=temps, selected_editable_objects=temps):
            bpy.ops.object.join()
    merged.name = ".Essence High"
    fill = None
    if not merged.material_slots:
        merged.data.materials.append(None)
    for slot in merged.material_slots:
        if slot.material is None:
            if fill is None:
                fill = bpy.data.materials.new(".Essence High Fill")
                fill.use_nodes = True
            slot.material = fill

    def teardown():
        bpy.data.objects.remove(merged)
        for name in meshes:     # the merge's own mesh and the ones the join emptied
            m = bpy.data.meshes.get(name)
            if m is not None and m.users == 0:
                bpy.data.meshes.remove(m)
        if fill is not None:
            bpy.data.materials.remove(fill)
        context.view_layer.update()     # else the view layer still lists the removed object
    return merged, teardown


@contextlib.contextmanager
def _render_settings(objects):
    """Evaluate `objects` as a render (and so a bake) does: modifiers switched on for render
    are on, Subdivision Surface and Multiresolution use their render levels. Python only sees
    the viewport evaluation, so these are set for the merge and put back afterwards."""
    saved = []
    for o in objects:
        for m in o.modifiers:
            saved.append((m, 'show_viewport', m.show_viewport))
            set_if_changed(m, 'show_viewport', m.show_render)
            if m.type in ('SUBSURF', 'MULTIRES'):
                saved.append((m, 'levels', m.levels))
                set_if_changed(m, 'levels', m.render_levels)
    try:
        yield
    finally:
        for m, attr, value in reversed(saved):
            set_if_changed(m, attr, value)


@contextlib.contextmanager
def _render_only(context, keep):
    """Only `keep` renders while a bake runs: every other object of the view layer with a
    surface is switched off for rendering and put back afterwards. Cycles lets everything
    visible occlude an AO bake (other low polys, props, a floor), and the bake target itself
    must be on (a bake refuses an object that is off for rendering)."""
    changed = []
    for o in context.view_layer.objects:
        if o is not None and o.type in SURFACE_TYPES:
            if set_if_changed(o, 'hide_render', o not in keep):
                changed.append(o)
    try:
        yield
    finally:
        for o in changed:
            try:
                o.hide_render = not o.hide_render
            except ReferenceError:
                pass


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
                raise RuntimeError(f"'{o.name}' is not in this view layer")
            self.saved.append((o, o.hide_get(), o.hide_viewport, o.hide_select))
            o.hide_set(False)
            o.hide_viewport = False
            o.hide_select = False
            if not o.visible_get(view_layer=self.vl):
                raise RuntimeError(f"'{o.name}' is still hidden")
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
    bl_description = ("Bake the selected map for the texture sets of the selected objects: "
                      "every object using one of their materials bakes into that material's "
                      "image, each from its own baking group")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def _bake_object(self, context, mat, obj, map_type, build_color, high, clear, keep):
        """One Cycles bake of `obj` into the active image of `mat` (set up by _bake_set), from
        the merged high poly in `high` (a list of at most one), with only `keep` rendering."""
        bake = context.scene.render.bake
        bpy.ops.object.select_all(action='DESELECT')
        bake.use_selected_to_active = bool(high)
        if high:
            cage = obj.essence_bake_cage
            if cage is not None:
                # Rays start on the cage and travel to the low surface; the cage replaces
                # the extrusion and the distance limit.
                bake.use_cage = True
                bake.cage_object = cage
                bake.max_ray_distance = 0.0
            else:
                extrusion = mat.essence.bake_extrusion or auto_extrusion(obj)
                bake.use_cage = False
                bake.cage_extrusion = extrusion
                # Rays start `extrusion` above the low surface; reach `extrusion` below.
                bake.max_ray_distance = extrusion * 2
            for h in high:
                h.select_set(True)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        teardowns = []
        try:
            if build_color is not None:
                teardowns.append(_emission_override(mat, build_color))
                for h in high:
                    teardowns.extend(_override_high_materials(h, build_color, {mat.name}))
            pass_type = next(t[3] for t in MAP_TYPES if t[0] == map_type)
            with _render_only(context, set(keep) | {obj} | set(high)):
                if 'FINISHED' not in bpy.ops.object.bake(type=pass_type, use_clear=clear):
                    raise RuntimeError("bake did not finish (see console)")
        finally:
            for t in reversed(teardowns):
                t()

    def _bake_set(self, context, mat, objects, map_type, build_color, image, margin, id_mode,
                  use_high):
        """Bake every object of a texture set into `image`, each from its own baking group.
        One object gets Cycles' own margin (an ID map never does); several are baked with none
        (Cycles' margin would cover the islands of the others) and get the margin from the
        compositor afterwards."""
        # An ID map always gets its margin from the compositor, which also refills the texels
        # whose rays missed the high poly (Cycles' margin leaves those black).
        by_compositor = len(objects) > 1 or map_type == 'ID'
        set_if_changed(context.scene.render.bake, 'margin', 0 if by_compositor else margin)
        # Floaters add their detail to every map but AO and Thickness, where something floating
        # above the surface would cast a false shadow or read as thin.
        floaters_too = map_type not in ('AO', 'THICKNESS')
        groups = {}
        for obj in objects:
            high, floaters = bake_group(obj, mat) if use_high else (None, None)
            colls = tuple(c for c in (high, floaters if floaters_too else None) if c is not None)
            groups[obj] = colls if high is not None else ()
        merges, teardowns = {}, []
        try:
            for colls in dict.fromkeys(g for g in groups.values() if g):
                sources = list(dict.fromkeys(o for c in colls for o in high_poly_objects(c)))
                if not sources:
                    raise RuntimeError(f"high-poly collection '{colls[0].name}' has no objects")
                merged, drop = merge_high(context, sources, id_mode)
                merges[colls] = merged
                teardowns.append(drop)
            teardowns.append(_activate_target(mat, image, map_type))
            if map_type == 'NORMAL':
                teardowns.append(_detach_normal_input(mat))
            teardowns.append(other_targets(objects, mat))
            first_island = 0
            for i, obj in enumerate(objects):
                build = _position_builder(context, obj) if map_type == 'POSITION' else build_color
                colls = groups[obj]
                high = [merges[colls]] if colls else []
                if map_type == 'AO':
                    # Every part's high poly occludes (the others' merges); without a high poly
                    # the low polys of the set are the surface and occlude each other.
                    keep = ([m for c, m in merges.items() if c != colls] if high
                            else list(objects))
                else:
                    keep = []
                with (_id_attribute(obj, id_mode, first_island) if id_mode and not high
                      else contextlib.nullcontext(0)) as count:
                    self._bake_object(context, mat, obj, map_type, build, high, i == 0, keep)
                first_island += count
        finally:
            for t in reversed(teardowns):
                t()
        if by_compositor and margin:
            from . import filters
            filters.add_margin(context, objects[0], mat, image, margin,
                               black_is_hole=map_type == 'ID')

    def execute(self, context):
        scene = context.scene
        map_type = scene.essence_bake_map
        aa = int(scene.essence_bake_aa)
        if map_type == 'ID':
            aa = 1      # averaging two colors makes a third one that is no part's ID
        uv_islands = map_type == 'ID' and scene.essence_bake_id == 'UV_ISLAND'
        use_high = map_type in HIGH_MAPS and not uv_islands

        # The texture sets: the materials of the selected objects. A high poly is a source,
        # never a target.
        pool = high_poly_pool()
        mats = []
        for o in context.selected_objects:
            if o.type != 'MESH' or o in pool:
                continue
            for m in _ensure_materials(o):
                if m not in mats:
                    mats.append(m)

        sets, failed, skipped = [], [], []
        for mat in mats:
            if not mat.use_nodes:
                mat.use_nodes = True
            objects = texture_set(context, mat)
            skipped += [o.name for o in objects if not o.data.uv_layers]
            objects = [o for o in objects if o.data.uv_layers]
            size = int(mat.essence.bake_size)
            if size * aa > _MAX_BAKE_SIZE:
                failed.append(f"{mat.name} ({size} px with {aa}x anti-aliasing bakes at "
                              f"{size * aa} px, above the {_MAX_BAKE_SIZE} px limit)")
            elif objects:
                sets.append((mat, objects, size))
        if not sets:
            self.report({'ERROR'}, "; ".join(failed) if failed else
                        "No selected mesh with a UV map to bake")
            return {'CANCELLED'}

        build_color = None
        if map_type == 'WORLD_GRADIENT':
            lo, hi = world_bounds(context, [o for _, objs, _ in sets for o in objs])
            build_color = _gradient_builder(lo.z, hi.z)
        elif map_type == 'NORMAL_WORLD':
            build_color = _world_normal_builder()
        elif map_type == 'ID':
            build_color = _id_builder(scene.essence_bake_id)
        id_mode = scene.essence_bake_id if map_type == 'ID' else None

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

        baked, last_image = [], None
        # Hidden objects of a set are shown for the bake, then hidden again (the high polys
        # are read through a merge, see merge_high).
        forced = _ForceSelectable(context, list(dict.fromkeys(
            o for _, objs, _ in sets for o in objs)))
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
            margin = scene.essence_bake_margin * aa

            for mat, objects, size in sets:
                build = build_color
                if map_type == 'THICKNESS':
                    # 0 is automatic: a tenth of the biggest object of the set
                    distance = (scene.essence_bake_thickness
                                or 0.1 * max(max(o.dimensions) for o in objects))
                    build = _thickness_builder(distance or 1.0)
                try:
                    image = _set_image(mat, objects, size * aa, map_type, scene.essence_bake_float)
                    args = (context, mat, objects, map_type, build, image, margin, id_mode,
                            use_high)
                    try:
                        self._bake_set(*args)
                    except Exception:  # noqa: BLE001
                        if device != 'GPU' or scene.essence_bake_device == 'CPU':
                            raise
                        # The GPU failed (out of memory, a driver): this and the rest of the
                        # sets go on the CPU.
                        device, fell_back = 'CPU', True
                        set_if_changed(scene.cycles, 'device', 'CPU')
                        self._bake_set(*args)
                    if aa > 1:
                        _downscale(image, size)
                    image.pack()
                    image["essence_stamp"] = uuid.uuid4().hex[:8]   # derived maps notice it
                    if map_type == 'NORMAL':
                        node = mat.node_tree.nodes.get(_bake_node_name(map_type))
                        if node is not None:
                            essence_nodes.apply_normal_map(mat, node)
                    elif map_type == 'AO':
                        essence_nodes.apply_ao(mat)
                    elif map_type == 'POSITION':
                        sources.wire_baked_inputs(mat.node_tree)   # a generator may read it now
                    last_image = image
                    baked.append(f"{mat.name} ({len(objects)} object"
                                 f"{'s' if len(objects) > 1 else ''})")
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{mat.name} ({e})")
        except RuntimeError as e:  # visibility problems
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
            if fell_back:
                self.report({'WARNING'}, f"Baked {', '.join(baked)}; the GPU failed, "
                                         "so the CPU was used")
            else:
                self.report({'INFO'}, f"Baked {', '.join(baked)} on the {device}")
        return {'FINISHED'} if baked else {'CANCELLED'}


def _strip_low(name):
    for suffix in ("_low", ".low", " low", "_lp", "_lowpoly"):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return name


class ESSENCE_OT_pair_by_name(bpy.types.Operator):
    bl_idname = "essence.pair_by_name"
    bl_label = "Pair by Name"
    bl_description = ("Fill in the High Poly and Floaters of every object of this texture set "
                      "from collection names: BG1_Low gets a collection called BG1 (or "
                      "BG1_High) and BG1_FL (or BG1_Floaters). Objects without a match are "
                      "left as they are")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and ob.active_material is not None

    def execute(self, context):
        mat = context.active_object.active_material
        by_name = {c.name.lower(): c for c in bpy.data.collections}
        paired, missed = [], []
        for obj in texture_set(context, mat):
            base = _strip_low(obj.name).lower()
            high = next((by_name[n] for n in (base, f"{base}_high", f"{base}.high", f"{base}_hp")
                         if n in by_name), None)
            floaters = next((by_name[n] for n in (f"{base}_fl", f"{base}_floaters",
                                                  f"{base}_floater") if n in by_name), None)
            if high is None and floaters is None:
                missed.append(obj.name)
                continue
            if high is not None:
                obj.essence_bake_high = high
            if floaters is not None:
                obj.essence_bake_floaters = floaters
            paired.append(f"{obj.name}: {high.name if high else '-'}"
                          f"{' + ' + floaters.name if floaters else ''}")
        if not paired:
            self.report({'WARNING'}, "No collection names match the objects of this texture set")
            return {'CANCELLED'}
        msg = "Paired " + "; ".join(paired)
        if missed:
            msg += f" (no match: {', '.join(missed)})"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


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
    S.essence_bake_samples = IntProperty(name="Samples", default=32, min=1, soft_max=512)
    S.essence_bake_margin = IntProperty(name="Margin", default=16, min=0, soft_max=64,
                                        description="Padding in pixels around UV islands")
    bpy.types.Object.essence_bake_high = PointerProperty(
        name="High Poly", type=bpy.types.Collection,
        description="This low-poly object's high polys (its baking group): rays from this "
                    "object only hit these. Empty: the material's High Poly")
    bpy.types.Object.essence_bake_floaters = PointerProperty(
        name="Floaters", type=bpy.types.Collection,
        description="High-poly details floating above this object's surface: they add to "
                    "every map but AO and Thickness, where they would cast false shadows")
    bpy.types.Object.essence_bake_cage = PointerProperty(
        name="Cage", type=bpy.types.Object, poll=lambda self, o: o.type == 'MESH',
        description="Optional cage mesh for baking this low-poly object from a high poly: rays "
                    "start on it instead of at the extrusion offset. Needs the same topology "
                    "as this object, pushed outwards")
    S.essence_bake_aa = EnumProperty(
        name="Anti-aliasing", items=_AA, default='1',
        description="Supersampling: bake larger, then average down to the final size "
                    "(Cycles samples only smooth shading, not the hard edges between "
                    "hit and miss, or between UV islands and the background). ID maps ignore it: "
                    "averaging two IDs gives a color that is neither")
    S.essence_bake_id = EnumProperty(
        name="ID by", items=ID_MODES, default='OBJECT',
        description="What the ID map tells apart")
    S.essence_bake_device = EnumProperty(
        name="Device", items=_DEVICES, default='AUTO',
        description="What the bake runs on. The small baked filters and the curvature always "
                    "use the CPU, which is quick for them and keeps the GPU out of the viewport")
    S.essence_bake_thickness = FloatProperty(
        name="Distance", default=0.0, min=0.0, subtype='DISTANCE',
        description="How far a ray looks for the other side of the mesh. Thicker than this "
                    "reads as fully thick. 0 = automatic (a tenth of the biggest object)")
    S.essence_bake_float = BoolProperty(
        name="Float", default=True,
        description="Bake into a 32-bit float image (no banding, four times the memory)")
    bpy.utils.register_class(ESSENCE_OT_bake)
    bpy.utils.register_class(ESSENCE_OT_pair_by_name)


def unregister():
    global _previews
    bpy.utils.unregister_class(ESSENCE_OT_pair_by_name)
    bpy.utils.unregister_class(ESSENCE_OT_bake)
    del bpy.types.Object.essence_bake_cage
    del bpy.types.Object.essence_bake_floaters
    del bpy.types.Object.essence_bake_high
    S = bpy.types.Scene
    for p in ("essence_bake_map",
              "essence_bake_samples", "essence_bake_margin",
              "essence_bake_float", "essence_bake_aa",
              "essence_bake_id", "essence_bake_device", "essence_bake_thickness"):
        delattr(S, p)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
