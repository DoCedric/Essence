"""Open Question 8: can an addon run a compositor filter on one image and get an image back?"""
import os
import random
import shutil
import tempfile

import bpy

from harness import check


def _noisy_squares(size=128):
    random.seed(1)
    img = bpy.data.images.new("src", size, size, float_buffer=True)
    px = []
    for y in range(size):
        for x in range(size):
            v = (0.8 if ((x // 32 + y // 32) % 2) else 0.0) + random.random() * 0.2
            px += [v, v, v, 1.0]
    img.pixels.foreach_set(px)
    return img


def _run_filter(img, make_nodes):
    """Compositor group Image -> filter -> Group Output, rendered in a throwaway scene and
    read back through a temporary EXR. Returns the result image."""
    scene = bpy.data.scenes.new("EssenceFilterTmp")
    folder = tempfile.mkdtemp(prefix="essence_filter_")
    ng = bpy.data.node_groups.new("EssenceFilterTmp", 'CompositorNodeTree')
    try:
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
        src = ng.nodes.new('CompositorNodeImage')
        src.image = img
        out = ng.nodes.new('NodeGroupOutput')
        last = make_nodes(ng, src.outputs['Image'])
        ng.links.new(last, out.inputs[0])
        scene.compositing_node_group = ng
        w, h = img.size
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
        r.engine = 'BLENDER_WORKBENCH'
        r.image_settings.file_format = 'OPEN_EXR'
        r.image_settings.color_depth = '32'
        r.filepath = os.path.join(folder, "out.exr")
        cam = bpy.data.objects.new("c", bpy.data.cameras.new("c"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        check("FINISHED" in bpy.ops.render.render(write_still=True, scene=scene.name),
              "render did not finish")
        result = bpy.data.images.load(r.filepath)
        result.pack()           # outlives the temp folder
        return result
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        bpy.data.scenes.remove(scene)
        bpy.data.node_groups.remove(ng)


def _variance(img, x0=4, y0=4, n=24):
    w = img.size[0]
    px = list(img.pixels)
    vals = [px[((y0 + j) * w + x0 + i) * 4] for j in range(n) for i in range(n)]
    m = sum(vals) / len(vals)
    return sum((v - m) ** 2 for v in vals) / len(vals)


def test_kuwahara_through_a_temporary_scene(addon):
    img = _noisy_squares()
    user_scene = bpy.context.scene

    def kuwahara(ng, color):
        k = ng.nodes.new('CompositorNodeKuwahara')
        k.inputs['Size'].default_value = 6
        ng.links.new(color, k.inputs['Image'])
        return k.outputs['Image']

    out = _run_filter(img, kuwahara)
    check(tuple(out.size) == tuple(img.size), f"size changed: {tuple(out.size)}")
    before, after = _variance(img), _variance(out)
    check(after < before * 0.25, f"noise not smoothed: {before} -> {after}")
    w = img.size[0]
    row = [list(out.pixels)[(10 * w + x) * 4] for x in range(24, 40)]
    check(max(row[:6]) < 0.3 and min(row[-6:]) > 0.6, f"edge not kept: {row}")
    check(bpy.context.scene is user_scene and user_scene.compositing_node_group is None,
          "the user's scene must not be touched")
    check(bpy.data.scenes.get("EssenceFilterTmp") is None, "temp scene left behind")
    check(bpy.data.node_groups.get("EssenceFilterTmp") is None, "temp group left behind")
