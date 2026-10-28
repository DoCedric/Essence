"""Paint layers: layer channels with a Paint source, transparent until painted."""

import os
import shutil
import tempfile

import bpy

from harness import check, close, make_plane, sample_socket
from test_blend import _set


def _layers(mat):
    return [e for e in mat.essence.layers if e.kind == 'LAYER']


def _node(mat, e):
    return mat.node_tree.nodes[e.node_name]


def _top(ob, mat, socket):
    return sample_socket(ob, mat, _node(mat, _layers(mat)[-1]).outputs[socket])


def _base_and_paint_layer():
    """A base layer (red, roughness 0.2) and a paint layer above it."""
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    mat.essence.bake_size = '128'
    _set(_node(mat, _layers(mat)[0]), (1, 0, 0, 1), 0.2, 0.0, 0.0)
    check(bpy.ops.essence.add_paint_layer() == {'FINISHED'}, "add paint layer failed")
    return ob, mat


def _paint(mat, layer, ch, rgba):
    """Fill the channel's paint image with `rgba` (as the brush stores it: premultiplied)."""
    tex = __import__("essence").sources.source_nodes(mat.node_tree, layer.uid, ch)['TEXTURE']
    img = tex.image
    img.pixels.foreach_set(list(rgba) * (img.size[0] * img.size[1]))
    img.update()
    return img


def test_a_new_paint_layer_changes_nothing(addon):
    ob, mat = _base_and_paint_layer()
    layer = _layers(mat)[-1]
    check(layer.name == "Paint Layer 1", f"name {layer.name}")
    check(all(s.kind == 'PAINT' for s in layer.sources), "every channel is a Paint channel")
    node = _node(mat, layer)
    for ch in addon.channels.active_channels(mat):
        sock = node.inputs.get(f"{ch.label} Alpha")
        check(sock is not None, f"{ch.label} has a paint alpha input")
        check(sock.default_value == 0.0 and not sock.is_linked, f"{ch.label}: alpha 0, unpainted")
        check(not addon.sources.source_nodes(mat.node_tree, layer.uid, ch.id),
              f"{ch.label}: no nodes before the first paint")
    check(node.node_tree.name == "Essence Layer Paint", f"its group: {node.node_tree.name}")
    base_group = _node(mat, _layers(mat)[0]).node_tree
    check(not any(s.name.endswith(" Alpha") and s.name != "Alpha Alpha" for s in
                  base_group.interface.items_tree if getattr(s, "in_out", "") == 'INPUT'),
          "an ordinary layer keeps the group without paint inputs")
    close(_top(ob, mat, "Roughness")[0], 0.2, 0.01, "the base roughness shows through")
    c = _top(ob, mat, "Color")
    close(c[0], 1.0, 0.01, "the base color shows through (red)")
    close(c[2], 0.0, 0.01, "no blue")
    check(not addon.sources.MASK_KINDS or all(k[0] != 'PAINT' for k in addon.sources.MASK_KINDS),
          "masks have no Paint source")


def test_painting_a_channel(addon):
    ob, mat = _base_and_paint_layer()
    layer = _layers(mat)[-1]
    uid = layer.uid
    n_images = len(bpy.data.images)
    check(bpy.ops.essence.paint_channel(channel='ROUGHNESS') == {'FINISHED'}, "paint failed")
    check(ob.mode == 'TEXTURE_PAINT', "in Texture Paint")
    layer = next(e for e in mat.essence.layers if e.uid == uid)
    check(layer.paint_channel == 'ROUGHNESS', "remembers the channel")
    check(len(bpy.data.images) == n_images + 1, "one image, for that channel only")
    found = addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS')
    tex = found['TEXTURE']
    img = tex.image
    check(mat.node_tree.nodes.active == tex, "its image node is the paint target")
    check(tuple(img.size) == (128, 128), f"the texture set's size: {tuple(img.size)}")
    check(img.colorspace_settings.name == 'Non-Color', img.colorspace_settings.name)
    check(img.packed_file is not None, "packed")
    close(img.pixels[3], 0.0, 1e-6, "transparent")
    close(_top(ob, mat, "Roughness")[0], 0.2, 0.01, "still nothing painted")
    _paint(mat, layer, 'ROUGHNESS', (0.9, 0.9, 0.9, 1.0))
    close(_top(ob, mat, "Roughness")[0], 0.9, 0.01, "painted roughness")
    # A half-covered texel holds color * alpha (the brush's premultiplied maths).
    _paint(mat, layer, 'ROUGHNESS', (0.45, 0.45, 0.45, 0.5))
    close(_top(ob, mat, "Roughness")[0], 0.55, 0.01, "lerp(0.2, 0.9, 0.5)")
    close(_top(ob, mat, "Color")[0], 1.0, 0.01, "the unpainted color still shows the base")
    # The color channel: a color image, scene linear.
    bpy.ops.essence.paint_channel(channel='COLOR')
    layer = next(e for e in mat.essence.layers if e.uid == uid)
    ctex = addon.sources.source_nodes(mat.node_tree, uid, 'COLOR')['TEXTURE']
    check(ctex.image.colorspace_settings.name == 'Linear Rec.709',
          ctex.image.colorspace_settings.name)
    check(mat.node_tree.nodes.active == ctex, "switched target")
    _paint(mat, layer, 'COLOR', (0.0, 0.0, 1.0, 1.0))
    c = _top(ob, mat, "Color")
    close(c[0], 0.0, 0.01, "painted blue: no red")
    close(c[2], 1.0, 0.01, "blue")
    # Multiply on a painted channel: unpainted texels still change nothing.
    src = next(s for s in layer.sources if s.channel == 'METALLIC')
    check(src.kind == 'PAINT', "metallic is a paint channel")
    bpy.ops.object.mode_set(mode='OBJECT')


def test_switching_kind_keeps_the_painted_image(addon):
    ob, mat = _base_and_paint_layer()
    uid = _layers(mat)[-1].uid
    bpy.ops.essence.paint_channel(channel='ROUGHNESS')
    bpy.ops.object.mode_set(mode='OBJECT')
    layer = next(e for e in mat.essence.layers if e.uid == uid)
    img = _paint(mat, layer, 'ROUGHNESS', (0.9, 0.9, 0.9, 1.0))
    src = next(s for s in layer.sources if s.channel == 'ROUGHNESS')
    src.kind = 'VALUE'
    node = _node(mat, layer)
    check(node.inputs.get("Roughness Alpha") is None, "no paint alpha for a Value channel")
    check(node.inputs.get("Color Alpha") is not None, "the other paint channels keep theirs")
    node.inputs["Roughness B"].default_value = 0.6
    close(_top(ob, mat, "Roughness")[0], 0.6, 0.01, "a value covers everything")
    src = next(s for s in next(e for e in mat.essence.layers if e.uid == uid).sources
               if s.channel == 'ROUGHNESS')
    src.kind = 'PAINT'
    tex = addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS').get('TEXTURE')
    check(tex is not None and tex.image is img, "back to Paint: the same image")
    close(_top(ob, mat, "Roughness")[0], 0.9, 0.01, "with its paint")
    # Any layer can paint one channel.
    bpy.ops.essence.add_layer()
    plain = mat.essence.layers[mat.essence.active_index]
    puid = plain.uid
    next(s for s in plain.sources if s.channel == 'METALLIC').kind = 'PAINT'
    plain = next(e for e in mat.essence.layers if e.uid == puid)
    check(_node(mat, plain).inputs.get("Metallic Alpha") is not None, "metallic paint input")
    check(_node(mat, plain).inputs.get("Color Alpha") is None, "color stays a value")
    check(bpy.ops.essence.paint_channel.poll(), "Paint works on that layer")


def test_paint_layer_survives_save_reload_and_duplicate(addon):
    ob, mat = _base_and_paint_layer()
    uid = _layers(mat)[-1].uid
    bpy.ops.essence.paint_channel(channel='ROUGHNESS')
    bpy.ops.object.mode_set(mode='OBJECT')
    layer = next(e for e in mat.essence.layers if e.uid == uid)
    _paint(mat, layer, 'ROUGHNESS', (0.45, 0.45, 0.45, 0.5))
    folder = tempfile.mkdtemp(prefix="essence_paintlayer_")
    path = os.path.join(folder, "paint.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        ob = bpy.data.objects["Plane"]
        mat = ob.active_material
        close(_top(ob, mat, "Roughness")[0], 0.55, 0.01, "the paint after a reload")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    uid = _layers(mat)[-1].uid      # a uid: the duplicate reallocates the collection
    mat.essence.active_index = [e.uid for e in mat.essence.layers].index(uid)
    bpy.ops.essence.duplicate()
    copy = mat.essence.layers[mat.essence.active_index]
    a = addon.sources.source_nodes(mat.node_tree, copy.uid, 'ROUGHNESS')['TEXTURE'].image
    b = addon.sources.source_nodes(mat.node_tree, uid, 'ROUGHNESS')['TEXTURE'].image
    check(a is not b, "the duplicate paints its own image")
    check(_node(mat, copy).inputs["Roughness Alpha"].is_linked, "and is wired")


def test_paint_layer_panels_draw(addon):
    from test_ui import Rec, _ctx, _draw
    from essence import ops, ui
    ob, mat = _base_and_paint_layer()
    log = _draw(ui._props, ob)
    ops_drawn = [c for c in log if c[0] == "operator" and c[1][0] == "essence.paint_channel"]
    check(len(ops_drawn) == len(addon.channels.active_channels(mat)),
          f"a Paint button per channel: {len(ops_drawn)}")
    check(any(c[0] == "label" and "Not painted yet" in c[2].get("text", "") for c in log),
          "an unpainted channel says so")
    log = _draw(ui._stack, ob)
    check(any(c[0] == "label" and c[2].get("icon") == 'BRUSH_DATA' for c in log),
          "a brush icon on the paint layer's row")
    check(any(c[0] == "operator" and c[1][0] == "essence.add_paint_layer" for c in log),
          "the bar has Paint Layer")
