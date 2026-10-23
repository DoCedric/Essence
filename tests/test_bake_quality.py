import math

import bpy

from harness import check, make_plane


def _bake(map_type="NORMAL_WORLD"):
    sc = bpy.context.scene
    sc.essence_bake_map = map_type
    sc.essence_bake_size = "128"
    sc.essence_bake_samples = 4
    sc.essence_bake_margin = 0
    return bpy.ops.essence.bake()


def _center(img):
    w, h = img.size
    i = ((h // 2) * w + w // 2) * 4
    return list(img.pixels)[i:i + 4]


def _high_collection(z, tilt):
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 0, z))
    high = bpy.context.active_object
    high.name = "High"
    high.rotation_euler = (math.radians(tilt), 0, 0)
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(high)
    bpy.context.scene.collection.objects.unlink(high)
    return coll


def _select(ob):
    for o in bpy.context.view_layer.objects:
        o.select_set(o is ob)
    bpy.context.view_layer.objects.active = ob


def test_cage_reaches_distant_high_poly(addon):
    low = make_plane("Low", size=2.0)
    sc = bpy.context.scene
    sc.essence_bake_high = _high_collection(z=0.5, tilt=20)
    _select(low)
    # Auto extrusion is ~0.06, so the tilted plane 0.5 above is out of reach: no cage, no hit.
    check(_bake() == {'FINISHED'}, "bake without cage failed")
    miss = _center(bpy.data.images["Low_NormalWorld"])
    check(max(miss[:3]) < 0.01, f"expected a miss (untouched black), got {miss}")

    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0.8))
    cage = bpy.context.active_object
    cage.name = "Cage"
    _select(low)
    sc.essence_bake_cage = cage
    check(_bake() == {'FINISHED'}, "bake with cage failed")
    hit = _center(bpy.data.images["Low_NormalWorld"])
    # World normal of a plane tilted 20 degrees about X: (0, -sin20, cos20) -> 0-1 remap.
    check(abs(hit[1] - (0.5 - math.sin(math.radians(20)) / 2)) < 0.05 or
          abs(hit[1] - (0.5 + math.sin(math.radians(20)) / 2)) < 0.05,
          f"cage bake did not capture the tilted high-poly normal: {hit}")
    check(abs(hit[2] - (0.5 + math.cos(math.radians(20)) / 2)) < 0.05, f"wrong z: {hit}")


def test_cage_settings_restored(addon):
    low = make_plane("Low", size=2.0)
    sc = bpy.context.scene
    sc.essence_bake_high = _high_collection(z=0.0, tilt=20)
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0.3))
    sc.essence_bake_cage = bpy.context.active_object
    _select(low)
    bake = sc.render.bake
    before = (bake.use_cage, bake.cage_object, bake.max_ray_distance)
    check(_bake() == {'FINISHED'}, "bake failed")
    after = (bake.use_cage, bake.cage_object, bake.max_ray_distance)
    check(before == after, f"bake settings not restored: {before} -> {after}")


def test_bake_image_is_float_and_switchable(addon):
    make_plane("P", size=2.0)
    sc = bpy.context.scene
    check(sc.essence_bake_float, "float should default on")
    check(_bake("AO") == {'FINISHED'}, "bake failed")
    img = bpy.data.images["P_AO"]
    check(img.is_float, "baked image is not float")
    check(img.packed_file is not None, "baked image not packed")
    # Switching to 8-bit keeps the image name and its users.
    holder = bpy.data.materials.new("Holder")
    holder.use_nodes = True
    n = holder.node_tree.nodes.new('ShaderNodeTexImage')
    n.image = img
    sc.essence_bake_float = False
    check(_bake("AO") == {'FINISHED'}, "8-bit rebake failed")
    img2 = bpy.data.images["P_AO"]
    check(not img2.is_float, "image should now be 8-bit")
    check(n.image is img2, "reference to the baked image was lost")


def test_new_paint_mask_is_float(addon):
    from harness import make_plane  # noqa: F811
    ob = make_plane("P", size=2.0)
    mat = bpy.data.materials.new("M")
    mat.use_nodes = True
    ob.data.materials.append(mat)
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    imgs = [i for i in bpy.data.images if "Mask" in i.name]
    check(imgs, "no mask image created")
    check(all(i.is_float for i in imgs), "paint mask is not a float image")
    check(imgs[0].colorspace_settings.name == 'Non-Color', "mask not Non-Color")
