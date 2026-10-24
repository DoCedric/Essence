"""Curvature, derived from the Normal slot with the compositor.

Curvature is how fast the surface direction changes. In a tangent space normal map that is the
divergence of the normal: d(nx)/du + d(ny)/dv. Where the normal turns outward the surface is
convex (an edge, bright), where it turns inward it is concave (a crevice, dark), and flat
areas stay at 0.5. It needs no bake of its own, works on any normal image the user picks, and
keeps the high-poly detail because the normal carries it (tested against Cycles' Pointiness,
which is nearly flat on hard surface, and Bevel, which is unsigned and slow).

Like a baked filter it is an explicit step: **Derive** reads the Normal slot, runs a
compositor group on it in a throwaway scene (see `filters.run_compositor_group`) and puts the
result in the Curvature slot. The group "Essence Curvature <uid>" can be edited by hand.
The button is red while the result is missing or out of date (`signature`).

    Normal -> Blur(Width) -> shifted copies one pixel left, right, up, down
           -> (nx(x+1) - nx(x-1)) + (ny(y+1) - ny(y-1)) -> * Strength * size / 1024 -> + 0.5

**Island borders.** Where two UV islands are close, their margins meet and the normal jumps
from one island's tangent frame to the other's; the derivative reads that jump as a sharp
edge, and it reaches the island's own border pixels through the blur and the one pixel
neighbour lookup. So Derive also bakes the island coverage (the same bake with margin 0, a
fraction of a second), and the group keeps the curvature only inside the islands, shrunk by
the reach of the filter, and refills everything else (the padding and the contaminated border
band) from the nearest island interior with Blender's Inpaint node, flat grey beyond.
"""

import hashlib
import time

import bpy

from . import filters, nodes

GROUP_PREFIX = "Essence Curvature "
RESULT_SUFFIX = "Curvature"
REFERENCE_SIZE = 1024.0     # the Strength is for a 1024 px map; bigger maps scale it down
FILL_DISTANCE = 32          # how far the island interior is continued into the padding


def group_name(mat):
    return f"{GROUP_PREFIX}{mat.essence.curvature_uid}"


def ensure_uid(mat):
    if not mat.essence.curvature_uid:
        import uuid
        mat.essence.curvature_uid = uuid.uuid4().hex[:8]
    return mat.essence.curvature_uid


def _node(ng, idname, name, x, y):
    n = ng.nodes.get(name)
    if n is None or n.bl_idname != idname:
        if n is not None:
            ng.nodes.remove(n)
        n = ng.nodes.new(idname)
        n.name = n.label = name
    n.location = (x, y)
    return n


def _link(ng, out, inp):
    if not (inp.is_linked and inp.links[0].from_socket == out):
        for l in list(inp.links):
            ng.links.remove(l)
        ng.links.new(out, inp)


def _math(ng, name, op, x, y):
    n = _node(ng, 'ShaderNodeMath', name, x, y)
    n.operation = op
    return n


def build_group(mat):
    """(Re)build the compositor group of the material's curvature, keeping the nodes (and so
    hand edits and the settings) that are already there."""
    uid = ensure_uid(mat)
    name = group_name(mat)
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'CompositorNodeTree')
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.use_fake_user = True     # only the throwaway scene uses it (see filters.build_group)
    out = next((n for n in ng.nodes if n.bl_idname == 'NodeGroupOutput'), None)
    if out is None:
        out = ng.nodes.new('NodeGroupOutput')
    out.location = (2400, 0)
    src = _node(ng, 'CompositorNodeImage', "Source", -900, 0)
    blur = _node(ng, 'CompositorNodeBlur', "Width", -650, 0)
    _link(ng, src.outputs['Image'], blur.inputs['Image'])
    sep = {}
    for key, (axis, shift) in {"xm": ('X', -1.0), "xp": ('X', 1.0),
                               "ym": ('Y', -1.0), "yp": ('Y', 1.0)}.items():
        # Translate by -1 gives the pixel to the right (or above): the neighbour at +1.
        t = _node(ng, 'CompositorNodeTranslate', f"Shift {key}", -350, {"xm": 450, "xp": 250,
                                                                     "ym": -250, "yp": -450}[key])
        _link(ng, blur.outputs['Image'], t.inputs['Image'])
        t.inputs[axis].default_value = shift
        for ext in ('Extension X', 'Extension Y'):
            try:
                t.inputs[ext].default_value = 'Extend'   # the map edge repeats, not black
            except (TypeError, ValueError):
                pass
        s = _node(ng, 'CompositorNodeSeparateColor', f"Split {key}", -100, t.location.y)
        _link(ng, t.outputs['Image'], s.inputs['Image'])
        sep[key] = s
    dx = _math(ng, "d normal x", 'SUBTRACT', 150, 350)
    # xm is shifted by -1 (it shows the pixel at x+1), so xm - xp is nx(x+1) - nx(x-1).
    _link(ng, sep["xm"].outputs['Red'], dx.inputs[0])
    _link(ng, sep["xp"].outputs['Red'], dx.inputs[1])
    dy = _math(ng, "d normal y", 'SUBTRACT', 150, -350)
    _link(ng, sep["ym"].outputs['Green'], dy.inputs[0])
    _link(ng, sep["yp"].outputs['Green'], dy.inputs[1])
    total = _math(ng, "Divergence", 'ADD', 400, 0)
    _link(ng, dx.outputs[0], total.inputs[0])
    _link(ng, dy.outputs[0], total.inputs[1])
    strength = _math(ng, "Strength", 'MULTIPLY', 650, 0)
    _link(ng, total.outputs[0], strength.inputs[0])
    centered = _math(ng, "Around grey", 'ADD', 900, 0)
    centered.use_clamp = True
    _link(ng, strength.outputs[0], centered.inputs[0])
    centered.inputs[1].default_value = 0.5
    comb = _node(ng, 'CompositorNodeCombineColor', "Grey", 1150, 0)
    for ch in ('Red', 'Green', 'Blue'):
        _link(ng, centered.outputs[0], comb.inputs[ch])
    comb.inputs['Alpha'].default_value = 1.0
    # Keep the curvature inside the islands (shrunk), refill the rest from the interior.
    cov = _node(ng, 'CompositorNodeImage', "Coverage", -900, -800)
    border = _node(ng, 'CompositorNodeDilateErode', "Border", -650, -800)
    _link(ng, cov.outputs['Image'], border.inputs['Mask'])
    try:
        border.inputs['Type'].default_value = 'Distance'
    except (TypeError, ValueError):
        pass
    keep = _node(ng, 'CompositorNodeSetAlpha', "Inside the islands", 1450, 0)
    _link(ng, comb.outputs['Image'], keep.inputs['Image'])
    _link(ng, border.outputs['Mask'], keep.inputs['Alpha'])
    try:
        keep.inputs['Type'].default_value = 'Replace Alpha'
    except (TypeError, ValueError):
        pass
    fill = _node(ng, 'CompositorNodeInpaint', "Fill", 1700, 0)
    _link(ng, keep.outputs['Image'], fill.inputs['Image'])
    grey = _node(ng, 'CompositorNodeRGB', "Flat", 1700, -300)
    grey.outputs[0].default_value = (0.5, 0.5, 0.5, 1.0)
    over = _node(ng, 'CompositorNodeAlphaOver', "Flat beyond the fill", 1950, 0)
    _link(ng, grey.outputs['Color'], over.inputs['Background'])
    _link(ng, fill.outputs['Image'], over.inputs['Foreground'])
    over.inputs['Factor'].default_value = 1.0
    _link(ng, over.outputs['Image'], out.inputs[0])
    return ng


def apply_settings(mat, ng, size):
    """Put the material's Width and Strength into the group's nodes."""
    s = mat.essence
    ng.nodes["Width"].inputs['Size'].default_value = (s.curvature_width, s.curvature_width)
    # What the filter reaches: the one pixel neighbours plus the blur. Curvature closer than
    # that to an island border is thrown away and refilled.
    ng.nodes["Border"].inputs['Size'].default_value = -(int(s.curvature_width + 0.999) + 2)
    ng.nodes["Fill"].inputs['Size'].default_value = FILL_DISTANCE
    # the derivative is per pixel, so a bigger map needs less of it for the same look
    ng.nodes["Strength"].inputs[1].default_value = s.curvature_strength * size / REFERENCE_SIZE


def slot(mat):
    return mat.node_tree.nodes.get(nodes.map_slot_name('CURVATURE')) if mat.node_tree else None


def normal_image(mat):
    node = mat.node_tree.nodes.get(nodes.map_slot_name('NORMAL')) if mat.node_tree else None
    return node.image if node is not None and node.bl_idname == 'ShaderNodeTexImage' else None


def result_image(mat):
    node = slot(mat)
    return node.image if node is not None else None


def signature(mat):
    """A digest of what the result depends on: the Normal slot's image (which image, its size
    and, for an Essence bake, the stamp it got when it was baked), the two settings and the
    group's nodes (so hand edits count). Painting over the normal by hand is not seen."""
    img = normal_image(mat)
    state = [img.name if img is not None else None,
             tuple(img.size) if img is not None else None,
             img.get("essence_stamp") if img is not None else None,
             img.packed_file.size if img is not None and img.packed_file is not None else None,
             round(mat.essence.curvature_width, 4), round(mat.essence.curvature_strength, 4)]
    ng = bpy.data.node_groups.get(group_name(mat)) if mat.essence.curvature_uid else None
    if ng is not None:
        for n in sorted(ng.nodes, key=lambda n: n.name):
            if n.name != "Source":
                state.append(filters._node_state(n))
    return hashlib.md5(repr(state).encode()).hexdigest()


def is_derived(mat):
    return result_image(mat) is not None and bool(mat.essence.curvature_sig)


def needs_derive(mat):
    return (normal_image(mat) is not None
            and (not is_derived(mat) or mat.essence.curvature_sig != signature(mat)))


def bake_coverage(context, ob, mat, size):
    """A float image, white where the object's UV islands are and black elsewhere: the same
    bake as any map, with a constant white emission and no margin."""
    tree = mat.node_tree
    white = tree.nodes.new('ShaderNodeRGB')
    white.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    try:
        return filters.bake_value(context, ob, mat, lambda t: white.outputs[0], size, margin=0)
    finally:
        tree.nodes.remove(white)


def derive(context, ob, mat):
    """Run the compositor on the Normal slot and put the result in the Curvature slot, as a
    packed float image called `<object>_Curvature` that replaces the previous one."""
    src_img = normal_image(mat)
    if src_img is None:
        raise RuntimeError("the Normal slot is empty: bake a normal or pick one first")
    if not ob.data.uv_layers:
        raise RuntimeError("the object has no UV map")
    name = ob.name
    nodes.ensure_map_slots(mat)     # a file from before this slot existed does not have it
    t0 = time.perf_counter()
    filters.log(f"CURVATURE '{mat.name}' from '{src_img.name}' {tuple(src_img.size)}")
    ng = build_group(mat)
    apply_settings(mat, ng, src_img.size[0])
    sig = signature(mat)
    coverage = bake_coverage(context, ob, mat, src_img.size[0])
    try:
        result = filters.run_compositor_group(ng, src_img, {"Coverage": coverage})
    finally:
        bpy.data.images.remove(coverage)
    node = slot(mat)
    img_name = f"{name}_{RESULT_SUFFIX}"
    old = bpy.data.images.get(img_name)
    if old is not None:
        old.user_remap(result)
        bpy.data.images.remove(old)
    result.name = img_name
    node.image = result
    mat.essence.curvature_sig = sig
    filters.log(f"CURVATURE finished in {time.perf_counter() - t0:.1f} s")
    return result
