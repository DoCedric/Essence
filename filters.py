"""Baked filters: neighbour-based image filters (Kuwahara, blur, sharpen, dilate/erode).

A shader sees one pixel at a time, so these cannot be live nodes. A filter is an opt-in item
(entry kind FILTER) on a mask. Refresh does three things:

    1. bake the mask value built so far (everything below the filter) into a float image,
       in UV space, with the same emission trick the export uses;
    2. run Blender's compositor on it, in a throwaway scene, through a compositor node group
       that belongs to the item and can be edited by hand;
    3. keep the result in one image per item (".Essence Filter <uid>", the leading dot hides
       it from image pickers), which the chain samples with the UV map.

Until the first Refresh the item passes the value through unchanged. Changing the items
below a filter needs another Refresh: the filter works on a cache, by design.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Tuple

import bpy

from .bake import _BAKE_ATTRS, _emission_override, _ensure_materials

from .adjustments import FILTER_GROUP_PREFIX as GROUP_PREFIX
from .adjustments import FILTER_IMAGE_PREFIX as IMAGE_PREFIX

RESOLUTIONS = tuple((str(s), f"{s} x {s}", "") for s in (256, 512, 1024, 2048, 4096))


@dataclass(frozen=True)
class FilterType:
    id: str
    label: str
    description: str
    idname: str                  # the compositor node
    input: str                   # its image input
    defaults: Tuple = ()         # (input name, value) set when the node is created


TYPES = (
    FilterType('KUWAHARA', "Kuwahara", "Painterly smoothing that keeps edges",
               'CompositorNodeKuwahara', 'Image', (("Size", 6),)),
    FilterType('BLUR', "Blur", "Blur the value", 'CompositorNodeBlur', 'Image',
               (("Size", (10.0, 10.0)),)),
    FilterType('SHARPEN', "Sharpen", "Sharpen the value", 'CompositorNodeFilter', 'Image'),
    FilterType('DILATE_ERODE', "Dilate / Erode", "Grow or shrink the bright areas",
               'CompositorNodeDilateErode', 'Mask'),
)
BY_ID = {t.id: t for t in TYPES}


def group_name(uid):
    return f"{GROUP_PREFIX}{uid}"


def image_name(uid):
    return f"{IMAGE_PREFIX}{uid}"


def result_image(uid):
    return bpy.data.images.get(image_name(uid))


def _set_default(sock, value):
    try:
        sock.default_value = value
    except (TypeError, ValueError):
        pass


def build_group(entry):
    """(Re)build the compositor group of a filter item: Image -> filter -> Group Output. The
    filter node is kept when the type did not change, so hand edits survive."""
    t = BY_ID[entry.filter_type]
    name = group_name(entry.uid)
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'CompositorNodeTree')
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    src = ng.nodes.get("Source")
    if src is None:
        src = ng.nodes.new('CompositorNodeImage')
        src.name = src.label = "Source"
        src.location = (-400, 0)
    out = next((n for n in ng.nodes if n.bl_idname == 'NodeGroupOutput'), None)
    if out is None:
        out = ng.nodes.new('NodeGroupOutput')
        out.location = (400, 0)
    node = ng.nodes.get("Filter")
    if node is not None and node.bl_idname != t.idname:
        ng.nodes.remove(node)
        node = None
    if node is None:
        node = ng.nodes.new(t.idname)
        node.name = node.label = "Filter"
        node.location = (0, 0)
        for sock_name, value in t.defaults:
            if sock_name in node.inputs:
                _set_default(node.inputs[sock_name], value)
    for link in list(node.inputs[t.input].links) + list(out.inputs[0].links):
        ng.links.remove(link)
    ng.links.new(src.outputs['Image'], node.inputs[t.input])
    ng.links.new(node.outputs[0], out.inputs[0])
    return ng, node


def filter_node(entry):
    ng = bpy.data.node_groups.get(group_name(entry.uid))
    return ng.nodes.get("Filter") if ng is not None else None


class _BakeState:
    """Cycles bake settings, selection and mode saved and restored around a bake."""

    def __init__(self, context, obj):
        self.context, self.obj = context, obj

    def __enter__(self):
        c = self.context
        self.mode = self.obj.mode
        if self.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.selection = list(c.selected_objects)
        self.active = c.view_layer.objects.active
        self.engine = c.scene.render.engine
        self.samples = c.scene.cycles.samples
        self.bake = c.scene.render.bake
        self.saved = {a: getattr(self.bake, a) for a in _BAKE_ATTRS}
        bpy.ops.object.select_all(action='DESELECT')
        self.obj.select_set(True)
        c.view_layer.objects.active = self.obj
        c.scene.render.engine = 'CYCLES'
        c.scene.cycles.samples = 1
        self.bake.target = 'IMAGE_TEXTURES'
        self.bake.use_selected_to_active = False
        self.bake.margin = c.scene.essence_bake_margin
        return self

    def __exit__(self, *exc):
        c = self.context
        c.scene.render.engine = self.engine
        c.scene.cycles.samples = self.samples
        for a, v in self.saved.items():
            setattr(self.bake, a, v)
        bpy.ops.object.select_all(action='DESELECT')
        for o in self.selection:
            try:
                o.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        if self.active is not None:
            c.view_layer.objects.active = self.active
        if self.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=self.mode)
        return False


def bake_value(context, obj, mat, socket_of, size):
    """Bake what a shader socket outputs into a new float image (UV space, Non-Color).
    `socket_of(tree)` returns the socket; nothing is left behind in the materials."""
    from .export import _temp_target

    image = bpy.data.images.new(".Essence Filter Input", size, size, alpha=False,
                                float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'

    def build(tree, x, y):
        return socket_of(tree), []

    teardowns = []
    try:
        with _BakeState(context, obj):
            for m in _ensure_materials(obj):
                if not m.use_nodes:
                    m.use_nodes = True
                teardowns.append(_temp_target(m, image))
            teardowns.append(_emission_override(mat, build))
            if 'FINISHED' not in bpy.ops.object.bake(type='EMIT', use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
    except Exception:
        bpy.data.images.remove(image)
        raise
    finally:
        for t in reversed(teardowns):
            t()
    return image


def run_compositor(entry, image):
    """Run the item's compositor group on `image` in a throwaway scene and return the
    result as a new, packed, float image."""
    ng, node = build_group(entry)
    src = ng.nodes["Source"]
    scene = bpy.data.scenes.new("EssenceFilterTmp")
    folder = tempfile.mkdtemp(prefix="essence_filter_")
    try:
        src.image = image
        scene.compositing_node_group = ng
        w, h = image.size
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
        r.engine = 'BLENDER_WORKBENCH'
        r.image_settings.file_format = 'OPEN_EXR'
        r.image_settings.color_depth = '32'
        r.filepath = os.path.join(folder, "out.exr")
        cam = bpy.data.objects.new("EssenceFilterCam", bpy.data.cameras.new("EssenceFilterCam"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        if 'FINISHED' not in bpy.ops.render.render(write_still=True, scene=scene.name):
            raise RuntimeError("the compositor render did not finish")
        result = bpy.data.images.load(r.filepath)
        result.colorspace_settings.name = 'Non-Color'
        result.pack()           # outlives the temporary folder
        return result
    finally:
        src.image = None
        shutil.rmtree(folder, ignore_errors=True)
        cam_data = scene.camera.data if scene.camera else None
        bpy.data.scenes.remove(scene)
        for o in [o for o in bpy.data.objects if o.name.startswith("EssenceFilterCam")]:
            bpy.data.objects.remove(o)
        if cam_data is not None:
            bpy.data.cameras.remove(cam_data)


def store_result(entry, result):
    """Make `result` the item's image: it replaces the previous one for everything using it."""
    name = image_name(entry.uid)
    old = bpy.data.images.get(name)
    if old is not None:
        old.user_remap(result)
        bpy.data.images.remove(old)
    result.name = name
    return result


def cleanup():
    """Delete the compositor groups and images of filter items that no longer exist."""
    live = {e.uid for m in bpy.data.materials for e in m.essence.layers if e.kind == 'FILTER'}
    for ng in [g for g in bpy.data.node_groups if g.name.startswith(GROUP_PREFIX)]:
        if ng.name[len(GROUP_PREFIX):] not in live:
            bpy.data.node_groups.remove(ng)
    for img in [i for i in bpy.data.images if i.name.startswith(IMAGE_PREFIX)]:
        if img.name[len(IMAGE_PREFIX):] not in live:
            bpy.data.images.remove(img)
