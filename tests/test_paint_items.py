"""Paint items: a fill whose image you paint, with its alpha as the factor."""

import os
import shutil
import tempfile

import bpy

from harness import check, close, sample_socket
from test_fills import _setup


def _mask(ob, mat, mask_uid):
    return next(e for e in mat.essence.layers if e.uid == mask_uid)


def _out(mat, mask_uid):
    return mat.node_tree.nodes[_mask(None, mat, mask_uid).node_name].outputs[0]


def _value(ob, mat, mask_uid, uv):
    return sample_socket(ob, mat, _out(mat, mask_uid), size=64, uv=uv)[0]


def _add_paint(mat, mask_uid, size=64):
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    check(bpy.ops.essence.add_paint(size=size) == {'FINISHED'}, "add paint failed")
    e = mat.essence.layers[mat.essence.active_index]
    check(e.kind == 'FILL' and e.fill_paint, "the new paint item should be active")
    return e.uid


def _paint(mat, uid, f):
    """Write what a brush would have left: f(u, v) -> (r, g, b, a) per pixel."""
    img = next(e for e in mat.essence.layers if e.uid == uid)
    node = mat.node_tree.nodes[img.mask_node_name]
    im = node.image
    w, h = im.size
    px = []
    for y in range(h):
        for x in range(w):
            px += list(f((x + 0.5) / w, (y + 0.5) / h))
    im.pixels.foreach_set(px)
    im.update()
    return im


def _stripes(u, v):
    """What the brush leaves: color times alpha (premultiplied), and alpha."""
    if u < 0.25:
        return (0.0, 0.0, 0.0, 0.0)     # unpainted
    if u < 0.5:
        return (1.0, 1.0, 1.0, 1.0)     # white
    if u < 0.75:
        return (0.0, 0.0, 0.0, 1.0)     # black
    return (0.5, 0.5, 0.5, 0.5)         # a soft edge of white: 1.0 * 0.5, alpha 0.5


def _brush(dst, color, a):
    """Blender's brush mix on a texel: the color is mixed with what was there by the stroke's
    alpha alone (dst * (1 - a) + color * a, premultiplied maths) and the alphas add up."""
    r = dst[0] * (1 - a) + color * a
    return (r, r, r, a + dst[3] * (1 - a))


A, B, C, D = (0.1, 0.5), (0.4, 0.5), (0.6, 0.5), (0.9, 0.5)


def test_add_paint_makes_a_transparent_image_and_the_nodes(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid, size=128)
    e = next(x for x in mat.essence.layers if x.uid == uid)
    check(e.parent == mask_uid and e.name == "Paint", "a paint item of the mask")
    node = mat.node_tree.nodes[e.mask_node_name]
    im = node.image
    check(im is not None and tuple(im.size) == (128, 128), "an image of the chosen size")
    check(im.is_float and im.packed_file is not None, "float, packed")
    check(im.colorspace_settings.name == 'Non-Color' and im.alpha_mode == 'CHANNEL_PACKED',
          f"Non-Color and raw (no conversion of the premultiplied pixels): {im.alpha_mode}")
    px = list(im.pixels)
    check(max(px[3::4]) == 0.0, "nothing painted yet: the alpha starts at 0")
    check(max(px) == 0.0, "and so does the color: transparent black, nothing to leak into an edge")
    found = addon.sources.source_nodes(mat.node_tree, uid, 'ADJ')
    check({'MAIN', 'ALPHA', 'MIX'} <= set(found), f"nodes: {sorted(found)}")
    factor = addon.adjustments.mix_socket(found['ALPHA'], "Factor_Float")
    check(factor.is_linked and factor.links[0].from_socket.name == 'Alpha', "the alpha is its factor")
    close(_value(ob, mat, mask_uid, B), 0.3, 0.02, "an unpainted paint item changes nothing")
    e2 = _add_paint(mat, mask_uid)
    check(next(x for x in mat.essence.layers if x.uid == e2).name == "Paint 2", "numbered")


def test_painted_values_blend_by_their_alpha(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    _paint(mat, uid, _stripes)
    close(_value(ob, mat, mask_uid, A), 0.3, 0.02, "unpainted: the value below")
    close(_value(ob, mat, mask_uid, B), 1.0, 0.02, "painted white reveals")
    close(_value(ob, mat, mask_uid, C), 0.0, 0.02, "painted black hides")
    close(_value(ob, mat, mask_uid, D), 0.65, 0.03, "half alpha white: halfway to white")


def test_blend_and_opacity(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    _paint(mat, uid, lambda u, v: (0.2, 0.2, 0.2, 1.0))
    e = next(x for x in mat.essence.layers if x.uid == uid)
    close(_value(ob, mat, mask_uid, B), 0.2, 0.02, "Normal: the painted value")
    e.fill_blend = 'ADD'
    close(_value(ob, mat, mask_uid, B), 0.5, 0.02, "Add paints relative to what is below")
    e.fill_blend = 'SUBTRACT'
    close(_value(ob, mat, mask_uid, B), 0.1, 0.02, "Subtract")
    e.fill_blend = 'NORMAL'
    mix = addon.sources.source_nodes(mat.node_tree, uid, 'ADJ')['MIX']
    addon.adjustments.mix_socket(mix, "Factor_Float").default_value = 0.5
    close(_value(ob, mat, mask_uid, B), 0.25, 0.02, "Opacity 0.5 halves it")


def test_the_paint_sits_at_its_place_in_the_stack(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')        # below the paint: 0.3 becomes 0.7
    uid = _add_paint(mat, mask_uid)
    _paint(mat, uid, lambda u, v: (1.0, 1.0, 1.0, 1.0) if u > 0.5 else (0.0, 0.0, 0.0, 0.0))
    close(_value(ob, mat, mask_uid, A), 0.7, 0.02, "unpainted: what the stack below made")
    close(_value(ob, mat, mask_uid, D), 1.0, 0.02, "painted: white")
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')        # above: it treats the paint like the rest
    close(_value(ob, mat, mask_uid, A), 0.3, 0.02, "an adjustment above inverts the unpainted part")
    close(_value(ob, mat, mask_uid, D), 0.0, 0.02, "and the paint: painted white is now black")


def test_eye_and_removal(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    _paint(mat, uid, lambda u, v: (1.0, 1.0, 1.0, 1.0))
    close(_value(ob, mat, mask_uid, B), 1.0, 0.02, "painted")
    e = next(x for x in mat.essence.layers if x.uid == uid)
    e.adj_enabled = False
    close(_value(ob, mat, mask_uid, B), 0.3, 0.02, "the eye hides the paint")
    e.adj_enabled = True
    close(_value(ob, mat, mask_uid, B), 1.0, 0.02, "and shows it again")
    mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers) if x.uid == uid)
    bpy.ops.essence.remove_layer()
    check(not addon.sources.source_nodes(mat.node_tree, uid, 'ADJ'), "the paint item's nodes are gone")
    check(not addon.sources.source_nodes(mat.node_tree, uid, 'MASK'), "and its image node")
    close(_value(ob, mat, mask_uid, B), 0.3, 0.02, "the mask is as before")


def test_switching_the_source_away_drops_the_alpha_node(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    e = next(x for x in mat.essence.layers if x.uid == uid)
    e.source.kind = 'VALUE'
    check('ALPHA' not in addon.sources.source_nodes(mat.node_tree, uid, 'ADJ'),
          "no image, no alpha node")
    e.source.kind = 'IMAGE'
    check('ALPHA' in addon.sources.source_nodes(mat.node_tree, uid, 'ADJ'), "and back again")
    check(mat.node_tree.nodes[e.mask_node_name].image is not None,
          "the painted image is remembered, not lost")


def test_paint_button_targets_the_paint_item(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    e = next(x for x in mat.essence.layers if x.uid == uid)
    check(bpy.ops.essence.paint_mask.poll(), "Paint should be available on a paint item")
    check(mat.node_tree.nodes.active.name == e.mask_node_name,
          "selecting the paint item makes its image the active node")
    check(bpy.ops.essence.paint_mask() == {'FINISHED'}, "paint failed")
    check(ob.mode == 'TEXTURE_PAINT', "not in Texture Paint")
    bpy.ops.essence.exit_paint()
    # an ordinary fill has nothing to paint
    mat.essence.active_index = next(i for i, x in enumerate(mat.essence.layers)
                                    if x.uid == mask_uid)
    bpy.ops.essence.add_fill()
    check(not bpy.ops.essence.paint_mask.poll(), "an ordinary fill is not paintable")


def test_paint_survives_save_and_reload(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    _paint(mat, uid, _stripes)
    name = ob.name
    folder = tempfile.mkdtemp(prefix="essence_paint_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.context.view_layer.update()
        bpy.data.images[next(e for e in mat.essence.layers if e.uid == uid).source.image_memory.name].pack()
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        ob = bpy.data.objects[name]
        mat = ob.active_material
        e = next(x for x in mat.essence.layers if x.uid == uid)
        check(e.fill_paint and 'ALPHA' in addon.sources.source_nodes(mat.node_tree, uid, 'ADJ'),
              "the paint item was lost")
        close(_value(ob, mat, mask_uid, B), 1.0, 0.02, "painted white survived")
        close(_value(ob, mat, mask_uid, A), 0.3, 0.02, "and the unpainted part")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_paint_item_panels_draw(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    for fn in (ui._adjustment, ui._stack):
        log = _draw(fn, ob)
    log = _draw(ui._adjustment, ob)
    check(any(c[0] == "operator" and c[1][0] == "essence.paint_mask" for c in log),
          "no Paint button on a paint item")
    check(not any(c[0] == "prop" and c[1][1] == "kind" for c in log),
          "a paint item has no source dropdown")
    for tree_view in (True, False):
        mat.essence.use_tree = tree_view
        _draw(ui._stack, ob)
    bpy.ops.essence.paint_mask()
    log = _draw(ui._adjustment, ob)
    check(any(c[0] == "operator" and c[1][0] == "essence.exit_paint" for c in log),
          "an Exit button while painting")
    bpy.ops.essence.exit_paint()


def test_painted_pixels_survive_a_save_without_any_manual_packing(addon):
    """Image.pack() does nothing on a generated blank, so a new paint image used to depend on
    Blender's unsaved images prompt. New masks and paint items are packed for real."""
    from test_mask_sources import _add_mask
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    paint_img = mat.node_tree.nodes[next(e for e in mat.essence.layers if e.uid == uid).mask_node_name].image
    check(paint_img.packed_file is not None and paint_img.source == 'FILE',
          f"a new paint image should be packed: {paint_img.source}")
    ob2, mat2 = ob, mat
    bpy.ops.essence.add_layer()
    m = _add_mask(mat2)
    mask_img = mat2.node_tree.nodes[m.mask_node_name].image
    check(mask_img.packed_file is not None and mask_img.source == 'FILE',
          f"a new mask image should be packed: {mask_img.source}")
    # "paint": write pixels, as a brush does (the image becomes dirty)
    w = paint_img.size[0]
    px = list(paint_img.pixels)
    px[0:4] = [1.0, 1.0, 1.0, 1.0]
    paint_img.pixels.foreach_set(px)
    paint_img.update()
    mpx = list(mask_img.pixels)
    mpx[4:8] = [0.25, 0.25, 0.25, 1.0]
    mask_img.pixels.foreach_set(mpx)
    mask_img.update()
    names = (paint_img.name, mask_img.name)
    folder = tempfile.mkdtemp(prefix="essence_pack_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        p = list(bpy.data.images[names[0]].pixels)
        q = list(bpy.data.images[names[1]].pixels)
        check(p[0:4] == [1.0, 1.0, 1.0, 1.0], f"painted paint-item pixel lost: {p[0:4]}")
        check(abs(q[4] - 0.25) < 1e-6 and q[0] == 1.0, f"painted mask pixel lost: {q[0:8]}")
        check(p[4 + 3] == 0.0, "the unpainted part of the paint item is still transparent")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_a_legacy_generated_mask_is_packed_before_a_save(addon):
    """A mask from an older version has a generated blank, not a packed image."""
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    m = _mask(ob, mat, mask_uid)
    m.source.kind = 'IMAGE'
    node = mat.node_tree.nodes[m.mask_node_name]
    old = bpy.data.images.new("legacy_mask", 16, 16, alpha=False, float_buffer=True)
    old.use_fake_user = True
    node.image = old
    check(old.source == 'GENERATED', "set up a generated blank")
    px = list(old.pixels)
    px[0:4] = [0.25, 0.5, 0.75, 1.0]                  # painted
    old.pixels.foreach_set(px)
    old.update()
    check(old.is_dirty, "painting leaves the image dirty")
    folder = tempfile.mkdtemp(prefix="essence_legacy_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        got = list(bpy.data.images["legacy_mask"].pixels)[0:3]
        check(got == [0.25, 0.5, 0.75], f"the painted pixel was lost on save: {got}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_the_save_handler_only_touches_what_was_painted(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    other = bpy.data.images.new("not_ours", 8, 8, float_buffer=True)
    other.use_fake_user = True
    other.pixels[0] = 0.9
    other.update()
    check(other.is_dirty, "an unrelated dirty image")
    n = addon.persist.repack_painted()
    check(n == 0, f"nothing of Essence's was painted: {n}")
    check(other.is_dirty, "an image that is not a mask or a paint item is left alone")
    check(addon.persist._on_save_pre in bpy.app.handlers.save_pre, "the handler is registered")
    addon.unregister()
    check(addon.persist._on_save_pre not in bpy.app.handlers.save_pre, "and removed again")
    addon.register()


def test_a_soft_brush_edge_has_no_fringe(addon):
    """The white line round a black stroke: the brush leaves color * alpha where a texel is only
    partly covered, and a color left over from before must not show in the edge."""
    for base in (1.0, 0.0, 0.3):
        ob, mat, top, top_uid, mask_uid = _setup(base_value=base)
        uid = _add_paint(mat, mask_uid)
        for color in (0.0, 1.0, 0.5):
            for a in (0.1, 0.3, 0.5, 0.8, 1.0):
                texel = _brush((0.0, 0.0, 0.0, 0.0), color, a)
                _paint(mat, uid, lambda u, v, t=texel: t)
                want = base * (1 - a) + color * a
                got = _value(ob, mat, mask_uid, (0.5, 0.5))
                close(got, want, 0.02, f"stroke of {color} at alpha {a} over {base}")
        # two strokes on top of each other: black, then a softer white
        first = _brush((0.0, 0.0, 0.0, 0.0), 0.0, 0.6)
        second = _brush(first, 1.0, 0.4)
        _paint(mat, uid, lambda u, v: second)
        after_first = base * (1 - 0.6)
        close(_value(ob, mat, mask_uid, (0.5, 0.5)), after_first * (1 - 0.4) + 0.4, 0.02,
              "a second stroke over the first reads as two strokes one after the other")


def test_an_image_from_before_the_pixels_were_kept_raw_is_converted(addon):
    ob, mat, top, top_uid, mask_uid = _setup(base_value=0.3)
    uid = _add_paint(mat, mask_uid)
    e = next(x for x in mat.essence.layers if x.uid == uid)
    im = mat.node_tree.nodes[e.mask_node_name].image
    im.alpha_mode = 'STRAIGHT'                       # what the previous version made
    bpy.ops.essence.rebuild()
    check(im.alpha_mode == 'CHANNEL_PACKED', "a rebuild puts our own paint images right")
    other = bpy.data.images.new("theirs", 8, 8, alpha=True, float_buffer=True)   # picked by the user
    other.alpha_mode = 'STRAIGHT'
    mat.node_tree.nodes[e.mask_node_name].image = other
    bpy.ops.essence.rebuild()
    check(other.alpha_mode == 'STRAIGHT', "an image we did not make is left alone")
