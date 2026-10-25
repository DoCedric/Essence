"""The Thickness mesh map: inside ambient occlusion, bright where thick and dark where thin."""

import bpy

from harness import check, make_plane
from test_bake_quality import _bake, _select


def _box(name, sx, sy, sz, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = (sx, sy, sz)
    bpy.ops.object.transform_apply(scale=True)
    return ob


def _mean(img):
    px = list(img.pixels)[0::4]
    return sum(px) / len(px)


def _bake_thickness(ob, distance=0.0):
    sc = bpy.context.scene
    sc.essence_bake_thickness = distance
    _select(ob)
    return _bake('THICKNESS')


def test_a_thick_box_is_bright_and_a_thin_slab_is_dark(addon):
    thick = _box("Thick", 2.0, 2.0, 2.0)
    check(_bake_thickness(thick, 0.5) == {'FINISHED'}, "bake failed")
    thick_mean = _mean(bpy.data.images["Thick_Thickness"])
    thin = _box("Thin", 2.0, 2.0, 0.1, location=(5, 0, 0))
    check(_bake_thickness(thin, 0.5) == {'FINISHED'}, "bake failed")
    thin_mean = _mean(bpy.data.images["Thin_Thickness"])
    # (the UV layout does not cover the whole image, so the mean cannot be compared with a
    # fixed number: the brightest texels and the two means against each other)
    brightest = max(list(bpy.data.images["Thick_Thickness"].pixels)[0::4])
    check(brightest > 0.9, f"a 2 unit box seen within 0.5 should reach white: {brightest}")
    check(thin_mean < thick_mean * 0.6, f"a 0.1 slab should be darker: {thin_mean} against {thick_mean}")
    img = bpy.data.images["Thin_Thickness"]
    check(img.is_float and img.colorspace_settings.name == 'Non-Color', "float, Non-Color")
    slot = thin.active_material.node_tree.nodes[addon.nodes.map_slot_name('THICKNESS')]
    check(slot.image is img, "the Thickness slot has it")


def test_the_distance_decides_what_counts_as_thin(addon):
    box = _box("Box", 2.0, 2.0, 0.5)
    _bake_thickness(box, 0.2)           # a ray looks 0.2 far: nothing is thin
    far = _mean(bpy.data.images["Box_Thickness"])
    _bake_thickness(box, 2.0)           # looks 2 far: the 0.5 slab shows as thin
    near = _mean(bpy.data.images["Box_Thickness"])
    check(far > near + 0.1, f"a longer look should find the other side: {far} then {near}")


def test_the_automatic_distance_follows_the_size(addon):
    small = _box("Small", 0.1, 0.1, 0.02)
    check(_bake_thickness(small, 0.0) == {'FINISHED'}, "automatic distance failed")
    big = _box("Big", 20.0, 20.0, 4.0, location=(50, 0, 0))
    check(_bake_thickness(big, 0.0) == {'FINISHED'}, "automatic distance failed")
    # the same shape at any size gives the same map
    a, b = _mean(bpy.data.images["Small_Thickness"]), _mean(bpy.data.images["Big_Thickness"])
    check(abs(a - b) < 0.1, f"automatic distance should scale with the object: {a} and {b}")


def test_thickness_from_a_high_poly(addon):
    low = make_plane("Low", size=2.0)
    slab = _box("Slab", 2.0, 2.0, 0.1, location=(0, 0, 0.02))
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    for c in list(slab.users_collection):
        c.objects.unlink(slab)
    coll.objects.link(slab)
    sc = bpy.context.scene
    sc.essence_bake_high = coll
    sc.essence_bake_extrusion = 0.3
    check(_bake_thickness(low, 0.5) == {'FINISHED'}, "bake failed")
    thin = _mean(bpy.data.images["Low_Thickness"])
    check(thin < 0.5, f"the thin high-poly slab should read dark: {thin}")
    sc.essence_bake_high = None
    sc.essence_bake_thickness = 0.5
    solid = _box("Solid", 2.0, 2.0, 2.0, location=(0, 0, -1.0))
    for c in list(solid.users_collection):
        c.objects.unlink(solid)
    coll.objects.unlink(slab)
    coll.objects.link(solid)
    sc.essence_bake_high = coll
    _select(low)
    _bake('THICKNESS')
    check(_mean(bpy.data.images["Low_Thickness"]) > thin + 0.2,
          "a thick high poly should read brighter than a thin one")


def test_thickness_panel_and_thumbnail(addon):
    from essence import bake, ui
    from test_ui import _draw
    ob = make_plane("Panel", size=2.0)
    bpy.context.scene.essence_bake_map = 'THICKNESS'
    log = _draw(ui._bake, ob)
    check(any(c[0] == "prop" and c[1][1] == "essence_bake_thickness" for c in log),
          "no Distance option for the thickness")
    check(any(c[0] == "prop" and c[1][1] == "essence_bake_high" for c in log),
          "a thickness bake can use a high poly")
    check(bake._previews.get('THICKNESS') is not None, "the Thickness thumbnail did not load")
    bpy.context.scene.essence_bake_map = 'AO'
    log = _draw(ui._bake, ob)
    check(not any(c[0] == "prop" and c[1][1] == "essence_bake_thickness" for c in log),
          "the thickness distance only shows for the thickness map")
