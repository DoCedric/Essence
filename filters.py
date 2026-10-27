"""Baked filters: neighbour-based image filters (Kuwahara, blur, sharpen, dilate/erode).

A shader sees one pixel at a time, so these cannot be live nodes. A filter is an opt-in item
(entry kind FILTER) on a mask, or on one channel of a layer (the layer's own fill of that
channel, before it is masked and blended). Apply does three things:

    1. bake the value built so far (everything below the filter in the same mask or channel)
       into a float image, in UV space, with the same emission trick the export uses;
    2. run Blender's compositor on it, in a throwaway scene, through a compositor node group
       that belongs to the item and can be edited by hand;
    3. keep the result in one image per item (".Essence Filter <uid>", the leading dot hides
       it from image pickers), which the chain samples with the UV map.

Until the first Apply the item passes the value through unchanged. Changing the items
below a filter needs another Apply: the filter works on a cache, by design. The button is
red while the cache is missing or out of date (see `signature`) and shows a green dot
when it is current.
"""

import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Tuple

import bpy
import numpy as np

from . import sources
from .bake import (_BAKE_ATTRS, _ForceSelectable, _emission_override, other_targets,
                   set_if_changed, temp_target, texture_set)

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
    single_value: bool = False   # the node takes one value (a color is reduced to its brightness)


TYPES = (
    FilterType('KUWAHARA', "Kuwahara", "Painterly smoothing that keeps edges",
               'CompositorNodeKuwahara', 'Image', (("Size", 6),)),
    FilterType('BLUR', "Blur", "Blur the value", 'CompositorNodeBlur', 'Image',
               (("Size", (10.0, 10.0)),)),
    FilterType('SHARPEN', "Sharpen", "Sharpen the value", 'CompositorNodeFilter', 'Image'),
    FilterType('DILATE_ERODE', "Dilate / Erode", "Grow or shrink the bright areas",
               'CompositorNodeDilateErode', 'Mask', single_value=True),
)
BY_ID = {t.id: t for t in TYPES}


LOG_PATH = os.path.join(tempfile.gettempdir(), "essence_filter.log")


def log(message):
    """Append one line to a log file in the temp folder, flushed and closed at once, so after a
    hang the last line says which step it was in. Also printed to the system console."""
    line = f"{time.strftime('%H:%M:%S')} {message}"
    print(f"Essence: {message}", flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


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


def channel_of(mat, entry):
    """What a filter works on: 'MASK' for a mask's filter, else the layer channel id."""
    owner = next((e for e in mat.essence.layers if e.uid == entry.parent), None)
    return 'MASK' if owner is None or owner.kind == 'MASK' else entry.adj_channel


def chain_items(mat, entry):
    """The items of the filter's own mask or layer channel, bottom to top (the order they apply
    in). Other channels of the same layer are a separate chain."""
    return sources.adjustment_entries(mat, entry.parent, channel_of(mat, entry))


def build_group(entry):
    """(Re)build the compositor group of a filter item: Image -> filter -> Group Output. The
    filter node is kept when the type did not change, so hand edits survive."""
    t = BY_ID[entry.filter_type]
    name = group_name(entry.uid)
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'CompositorNodeTree')
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    # Only the throwaway scene ever uses this group, so without a fake user Blender would drop
    # it when the file is saved, and the filter would lose its settings.
    ng.use_fake_user = True
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
    """Cycles bake settings, selection and mode saved and restored around a bake of `objects`
    (all selected, the first active)."""

    def __init__(self, context, objects, margin=None):
        self.context, self.objects, self.margin = context, objects, margin

    def __enter__(self):
        c = self.context
        self.active = c.view_layer.objects.active
        self.mode = self.active.mode if self.active is not None else 'OBJECT'
        if self.mode != 'OBJECT':
            log(f"bake state: leaving {self.mode} for Object Mode")
            bpy.ops.object.mode_set(mode='OBJECT')
            log("bake state: now in Object Mode")
        self.selection = list(c.selected_objects)
        self.engine = c.scene.render.engine
        self.samples = c.scene.cycles.samples
        self.device = c.scene.cycles.device
        self.bake = c.scene.render.bake
        self.saved = {a: getattr(self.bake, a) for a in _BAKE_ATTRS}
        self.forced = _ForceSelectable(c, self.objects).__enter__()
        log(f"bake state: selecting {len(self.objects)} object(s)")
        bpy.ops.object.select_all(action='DESELECT')
        for o in self.objects:
            o.select_set(True)
        c.view_layer.objects.active = self.objects[0]
        # Only what differs is assigned (see set_if_changed): assigning the engine again resets the
        # viewports, which deadlocked Blender with a Material Preview viewport.
        if self.engine == 'CYCLES':
            log("bake state: the scene engine is Cycles already, left alone")
        else:
            log(f"bake state: switching the scene engine from {self.engine} to Cycles")
        set_if_changed(c.scene.render, 'engine', 'CYCLES')
        log("bake state: engine is Cycles")
        set_if_changed(c.scene.cycles, 'samples', 1)
        # CPU: one sample of an emission bake is quick, and it keeps the GPU (and whatever the
        # viewport is doing with it) out of this.
        set_if_changed(c.scene.cycles, 'device', 'CPU')
        set_if_changed(self.bake, 'target', 'IMAGE_TEXTURES')
        set_if_changed(self.bake, 'use_selected_to_active', False)
        set_if_changed(self.bake, 'margin', c.scene.essence_bake_margin if self.margin is None
                       else self.margin)
        return self

    def __exit__(self, *exc):
        c = self.context
        if c.scene.render.engine != self.engine:
            log(f"bake state: restoring the scene engine to {self.engine}")
        set_if_changed(c.scene.render, 'engine', self.engine)
        set_if_changed(c.scene.cycles, 'samples', self.samples)
        set_if_changed(c.scene.cycles, 'device', self.device)
        for a, v in self.saved.items():
            set_if_changed(self.bake, a, v)
        bpy.ops.object.select_all(action='DESELECT')
        for o in self.selection:
            try:
                o.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        self.forced.__exit__()
        if self.active is not None:
            c.view_layer.objects.active = self.active
        if self.mode != 'OBJECT':
            log(f"bake state: returning to {self.mode}")
            bpy.ops.object.mode_set(mode=self.mode)
            log(f"bake state: back in {self.mode}")
        return False


def bake_value(context, obj, mat, socket_of, size, margin=None):
    """Bake what a shader socket outputs into a new float image (UV space, Non-Color).
    `socket_of(tree)` returns the socket; nothing is left behind in the materials. Every object
    of the material's texture set is baked (`obj` alone if it is not in one). `margin`
    overrides the scene's bake margin (0 gives the plain island coverage)."""
    objects = [o for o in texture_set(context, mat) if o.data.uv_layers] or [obj]
    if margin is None:
        margin = context.scene.essence_bake_margin
    several = len(objects) > 1
    image = bpy.data.images.new(".Essence Filter Input", size, size, alpha=False,
                                float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'

    def build(tree, x, y):
        return socket_of(tree), []

    teardowns = []
    try:
        log("bake: entering bake state (mode, selection, Cycles settings)")
        # Several objects get no margin from Cycles: it would cover the others' islands.
        with _BakeState(context, objects, 0 if several else margin):
            log("bake: adding the temporary target and emission nodes")
            if not mat.use_nodes:
                mat.use_nodes = True
            teardowns.append(temp_target(mat, image))
            teardowns.append(other_targets(objects, mat))
            teardowns.append(_emission_override(mat, build))
            log("bake: calling bpy.ops.object.bake")
            if 'FINISHED' not in bpy.ops.object.bake(type='EMIT', use_clear=True):
                raise RuntimeError("bake did not finish (see console)")
            log("bake: bpy.ops.object.bake returned")
        log("bake: bake state restored")
    except Exception as e:
        log(f"bake: FAILED {type(e).__name__}: {e}")
        bpy.data.images.remove(image)
        raise
    finally:
        for t in reversed(teardowns):
            t()
        log("bake: temporary nodes removed")
    if several and margin:
        add_margin(context, obj, mat, image, margin)
    return image


def bake_coverage(context, obj, mat, size):
    """A float image, white where the UV islands of the material's texture set are and black
    elsewhere: the same bake as any map, with a constant white emission and no margin."""
    tree = mat.node_tree
    white = tree.nodes.new('ShaderNodeRGB')
    white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    try:
        return bake_value(context, obj, mat, lambda t: white.outputs[0], size, margin=0)
    finally:
        tree.nodes.remove(white)


def _margin_group(margin):
    """A throwaway compositor group: keep the image inside the islands (the coverage) and
    continue the island borders outwards by `margin` pixels with the Inpaint node, black
    beyond, like a Cycles margin."""
    ng = bpy.data.node_groups.new(".Essence Margin", 'CompositorNodeTree')
    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    out = ng.nodes.new('NodeGroupOutput')
    src = ng.nodes.new('CompositorNodeImage')
    src.name = "Source"
    cov = ng.nodes.new('CompositorNodeImage')
    cov.name = "Coverage"
    keep = ng.nodes.new('CompositorNodeSetAlpha')
    ng.links.new(src.outputs['Image'], keep.inputs['Image'])
    ng.links.new(cov.outputs['Image'], keep.inputs['Alpha'])
    try:
        keep.inputs['Type'].default_value = 'Replace Alpha'
    except (TypeError, ValueError):
        pass
    fill = ng.nodes.new('CompositorNodeInpaint')
    fill.inputs['Size'].default_value = margin
    ng.links.new(keep.outputs['Image'], fill.inputs['Image'])
    black = ng.nodes.new('CompositorNodeRGB')
    black.outputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
    over = ng.nodes.new('CompositorNodeAlphaOver')
    ng.links.new(black.outputs['Color'], over.inputs['Background'])
    ng.links.new(fill.outputs['Image'], over.inputs['Foreground'])
    over.inputs['Factor'].default_value = 1.0
    ng.links.new(over.outputs['Image'], out.inputs[0])
    return ng


def dilate_colors(px, valid, steps):
    """Continue the colors of `px` (height, width, 4) into the texels that are not `valid` (a
    boolean grid), one texel per step, copying the neighbour's color unchanged. Unlike the
    Inpaint node this never blends two colors, which an ID map cannot have (a mix of two IDs
    is a third color). Returns the filled copy; unreached texels stay as they were."""
    px = px.copy()
    valid = valid.copy()
    for _ in range(steps):
        grown = valid.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            src = np.zeros_like(valid)
            col = np.zeros_like(px)
            ys, yd = (slice(None, -1), slice(1, None)) if dy == 1 else                      (slice(1, None), slice(None, -1)) if dy == -1 else (slice(None), slice(None))
            xs, xd = (slice(None, -1), slice(1, None)) if dx == 1 else                      (slice(1, None), slice(None, -1)) if dx == -1 else (slice(None), slice(None))
            src[yd, xd] = valid[ys, xs]
            col[yd, xd] = px[ys, xs]
            take = src & ~grown
            px[take] = col[take]
            grown |= take
        if (grown == valid).all():
            break
        valid = grown
    return px


def add_margin(context, obj, mat, image, margin, black_is_hole=False):
    """Give `image`, baked without a margin, its margin afterwards: the islands of the texture
    set (a coverage bake) are continued outwards by `margin` pixels in the compositor, and the
    result is written back into `image` itself, so it stays the same datablock with the same
    users. `black_is_hole` is for ID maps: black texels inside the islands (rays that missed
    the high poly; an ID is never black) are refilled too, and the colors are continued by
    copying the nearest one instead of blending (see `dilate_colors`)."""
    log(f"margin: {margin} px on '{image.name}'")
    coverage = bake_coverage(context, obj, mat, image.size[0])
    if black_is_hole:
        try:
            w, h = image.size
            cov = np.empty(len(coverage.pixels), dtype=np.float32)
            img = np.empty(len(image.pixels), dtype=np.float32)
            coverage.pixels.foreach_get(cov)
            image.pixels.foreach_get(img)
            img = img.reshape(h, w, 4)
            valid = (cov.reshape(h, w, 4)[..., 0] > 0.5) & (img[..., :3].max(axis=2) > 1e-4)
            img[~valid] = (0.0, 0.0, 0.0, 1.0)
            image.pixels.foreach_set(dilate_colors(img, valid, margin).reshape(-1))
            image.update()
        finally:
            bpy.data.images.remove(coverage)
        return
    ng = _margin_group(margin)
    try:
        result = run_compositor_group(ng, image, {"Coverage": coverage})
    finally:
        bpy.data.images.remove(coverage)
        bpy.data.node_groups.remove(ng)
    try:
        px = np.empty(len(result.pixels), dtype=np.float32)
        result.pixels.foreach_get(px)
        image.pixels.foreach_set(px)
        image.update()
    finally:
        bpy.data.images.remove(result)


def run_compositor(entry, image):
    """Run the item's compositor group on `image` in a throwaway scene and return the
    result as a new, packed, float image."""
    log("compositor: building the group and the throwaway scene")
    ng, node = build_group(entry)
    return run_compositor_group(ng, image)


def run_compositor_group(ng, image, extra=None):
    """Run a compositor group (an Image node named "Source", one Image output) on `image` in
    a throwaway scene at the image's size, and return the result as a new, packed, float,
    Non-Color image. `extra` maps the names of further Image nodes of the group to their
    images. Shared by the baked filters and the curvature."""
    extra = extra or {}
    src = ng.nodes["Source"]
    for name, img in extra.items():
        ng.nodes[name].image = img
    scene = bpy.data.scenes.new("EssenceFilterTmp")
    folder = tempfile.mkdtemp(prefix="essence_filter_")
    try:
        src.image = image
        scene.compositing_node_group = ng
        w, h = image.size
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
        # The compositor is the only thing that matters in this scene. No GPU anywhere: a
        # Workbench render and the GPU compositor hung Blender in the GUI once.
        r.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        scene.cycles.samples = 1
        r.compositor_device = 'CPU'
        r.compositor_precision = 'FULL'
        r.image_settings.file_format = 'OPEN_EXR'
        r.image_settings.color_depth = '32'
        r.filepath = os.path.join(folder, "out.exr")
        cam = bpy.data.objects.new("EssenceFilterCam", bpy.data.cameras.new("EssenceFilterCam"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        log("compositor: calling bpy.ops.render.render")
        if 'FINISHED' not in bpy.ops.render.render(write_still=True, scene=scene.name):
            raise RuntimeError("the compositor render did not finish")
        log("compositor: render returned, loading the result")
        result = bpy.data.images.load(r.filepath)
        result.colorspace_settings.name = 'Non-Color'
        result.pack()           # outlives the temporary folder
        log("compositor: result loaded and packed")
        return result
    finally:
        src.image = None
        for name in extra:
            ng.nodes[name].image = None
        shutil.rmtree(folder, ignore_errors=True)
        log("compositor: removing the throwaway scene")
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


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------

# Node properties that do not change the value a node produces.
_SKIP = {'rna_type', 'name', 'label', 'location', 'location_absolute', 'width', 'height',
         'dimensions', 'select', 'hide', 'show_options', 'show_preview', 'show_texture',
         'parent', 'color', 'use_custom_color', 'inputs', 'outputs', 'internal_links',
         'warning_propagation', 'is_active_output', 'panel_states', 'color_tag',
         'bl_idname', 'bl_label', 'bl_description', 'bl_icon', 'bl_static_type',
         'bl_width_default', 'bl_width_min', 'bl_width_max', 'bl_height_default',
         'bl_height_min', 'bl_height_max', 'type', 'node_tree', 'image', 'color_ramp',
         'mapping', 'image_user', 'text', 'script', 'object', 'material', 'node_tree'}
_SCALARS = ('BOOLEAN', 'INT', 'FLOAT', 'ENUM', 'STRING')


def _num(v):
    try:
        return tuple(round(x, 5) for x in v)
    except TypeError:
        return round(v, 5) if isinstance(v, float) else v


def _node_state(node):
    state = [node.bl_idname]
    for p in node.bl_rna.properties:
        if p.identifier in _SKIP or p.type not in _SCALARS:
            continue
        v = getattr(node, p.identifier, None)
        state.append((p.identifier, _num(v) if not isinstance(v, set) else sorted(v)))
    for s in node.inputs:
        if hasattr(s, "default_value") and not s.is_linked:
            state.append((s.identifier, _num(s.default_value)))
    img = getattr(node, "image", None)
    if img is not None:
        state.append(("image", img.name, tuple(img.size), img.is_float))
    tree = getattr(node, "node_tree", None)
    if tree is not None:
        state.append(("group", tree.name))
    ramp = getattr(node, "color_ramp", None)
    if ramp is not None:
        state.append(("ramp", ramp.interpolation,
                      [(round(e.position, 5), _num(e.color)) for e in ramp.elements]))
    mapping = getattr(node, "mapping", None)
    if mapping is not None and hasattr(mapping, "curves"):
        state.append(("curves", [[(round(p.location[0], 5), round(p.location[1], 5))
                                  for p in c.points] for c in mapping.curves]))
    return state


def _source_state(src):
    return (src.kind, src.projection, src.proc_type, src.gen_type) if src is not None else ()


def signature(mat, entry):
    """A digest of everything that decides what a filter would bake: the mask's (or the layer
    channel's) own source, every item below the filter (their nodes, order and switches; a filter below counts by the
    signature it was baked with) and the filter's own settings. Moving nodes or renaming
    does not change it."""
    tree = mat.node_tree
    layers = mat.essence.layers
    state = []
    owner = next((e for e in layers if e.uid == entry.parent), None)
    ch_id = channel_of(mat, entry)
    if owner is not None:
        state.append(_source_state(sources.get_source(owner, ch_id)))
        for n in sorted(sources.source_nodes(tree, owner.uid, ch_id).values(),
                        key=lambda n: n.name):
            state.append(_node_state(n))
    for item in chain_items(mat, entry):
        if item.uid == entry.uid:
            break
        state.append((item.kind, item.adj_enabled, item.adj_type, item.fill_blend,
                      item.filter_type, item.get("essence_sig", "")))
        nodes = dict(sources.source_nodes(tree, item.uid, 'ADJ'))
        nodes.pop('OUT', None)   # a filter's result is covered by its baked signature
        nodes.pop('IN', None)
        if item.kind == 'FILL':
            nodes.update({"src_" + r: n for r, n in
                          sources.source_nodes(tree, item.uid, 'MASK').items()})
            state.append(_source_state(item.source))
        for key in sorted(nodes):
            state.append((key, _node_state(nodes[key])))
    state.append((entry.filter_type, entry.filter_resolution, entry.adj_enabled))
    ng = bpy.data.node_groups.get(group_name(entry.uid))
    if ng is not None:
        for n in sorted(ng.nodes, key=lambda n: n.name):
            if n.name != "Source":
                state.append(_node_state(n))
    return hashlib.md5(repr(state).encode()).hexdigest()


def is_baked(entry):
    return result_image(entry.uid) is not None


def is_stale(mat, entry):
    """A result exists but what it was baked from has changed since."""
    return is_baked(entry) and entry.get("essence_sig", "") != signature(mat, entry)


def lower_filters(mat, entry):
    """The filters below `entry` in the same mask or channel, bottom to top. They are what it
    bakes."""
    out = []
    for item in chain_items(mat, entry):
        if item.uid == entry.uid:
            break
        if item.kind == 'FILTER':
            out.append(item)
    return out


def needs_apply(mat, entry):
    return not is_baked(entry) or is_stale(mat, entry)


def apply_with_dependencies(context, ob, mat, entry):
    """Apply `entry` after any filter below it that is not applied or out of date, in order:
    its bake would otherwise be made from a stale result. Returns the names applied."""
    uid = entry.uid
    todo = [e.uid for e in lower_filters(mat, entry) if needs_apply(mat, e)] + [uid]
    done = []
    for u in todo:
        e = next(x for x in mat.essence.layers if x.uid == u)
        # A lower filter applied just now can leave the next one needing it again.
        if u == uid or needs_apply(mat, e):
            apply_entry(context, ob, mat, e)
            done.append(next(x for x in mat.essence.layers if x.uid == u).name)
    return done


def apply_all(context, ob, mat):
    """Apply every filter of the material that is not applied or out of date, chain by chain
    (a mask, or one channel of a layer) and bottom to top: applying one makes the baked filters
    above it out of date, and they come up for re-application later in the same pass. Returns
    the names applied."""
    chains = {}
    for e in mat.essence.layers:
        if e.kind == 'FILTER':
            chains.setdefault((e.parent, channel_of(mat, e)), []).append(e.uid)
    done = []
    for uids in chains.values():
        for f in uids:
            e = next(x for x in mat.essence.layers if x.uid == f)
            if needs_apply(mat, e):
                apply_entry(context, ob, mat, e)
                done.append(next(x for x in mat.essence.layers if x.uid == f).name)
    return done


def any_unapplied(mat):
    return any(e.kind == 'FILTER' and needs_apply(mat, e) for e in mat.essence.layers)


def apply_entry(context, ob, mat, entry):
    """Bake, filter and store the result of one filter item. Returns the size baked at.
    Raises on failure; nothing is left behind in the scene either way."""
    tree = mat.node_tree
    ch_id = channel_of(mat, entry)
    if ch_id != 'MASK' and ch_id not in {c.id for c in sources.active_channels(mat)}:
        # Checked first: the item's nodes from before the channel was switched are stale.
        raise RuntimeError(f"the {sources.SOURCE_BY_ID[ch_id].label} channel is switched off "
                           "in the Material panel")
    node_in = sources.source_nodes(tree, entry.uid, "ADJ").get('IN')
    if node_in is None:
        raise RuntimeError("filter nodes missing, press Rebuild Links")
    if not ob.data.uv_layers:
        raise RuntimeError("the object has no UV map")
    size = int(entry.filter_resolution)
    uid, in_name = entry.uid, node_in.name
    sig = signature(mat, entry)   # of the state that is about to be baked
    # Every step is logged to a file (LOG_PATH): after a hang the last line says where.
    t0 = time.perf_counter()
    spaces = [sp.shading.type for w in bpy.context.window_manager.windows
              for ar in w.screen.areas if ar.type == 'VIEW_3D' for sp in ar.spaces
              if sp.type == 'VIEW_3D']
    log(f"APPLY '{entry.name}' {entry.filter_type} uid={uid} {size}px, object={ob.name} "
        f"mode={ob.mode}, viewport shading={spaces}, nodes in material={len(tree.nodes)}")
    owner_uid = entry.parent
    image = bake_value(context, ob, mat, lambda t: t.nodes[in_name].outputs[0], size)
    log(f"baked in {time.perf_counter() - t0:.1f} s, running the compositor")
    try:
        result = run_compositor(entry, image)
    finally:
        bpy.data.images.remove(image)
    log(f"compositor done at {time.perf_counter() - t0:.1f} s, storing the result")
    entry = next(e for e in mat.essence.layers if e.uid == uid)
    store_result(entry, result)
    entry["essence_sig"] = sig
    # Put the new result into the chain now: a filter above bakes from it.
    from . import nodes
    owner = next((x for x in mat.essence.layers if x.uid == owner_uid), None)
    if owner is not None:
        sources.apply_owner(mat, owner)
    nodes.rebuild_stack(mat)
    log(f"APPLY finished in {time.perf_counter() - t0:.1f} s")
    return size
