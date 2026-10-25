import bpy

from harness import baked, check, make_plane, texset
from test_bake_quality import _bake, _center, _select


def _scene(nested=False):
    """A low plane and a high plane in a High Poly collection (inside a parent when
    `nested`)."""
    low = make_plane("Low", size=2.0)
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 0, 0.02))
    high = bpy.context.active_object
    high.name = "High"
    coll = bpy.data.collections.new("HighPoly")
    parent = bpy.data.collections.new("Parent") if nested else None
    if parent is not None:
        bpy.context.scene.collection.children.link(parent)
        parent.children.link(coll)
    else:
        bpy.context.scene.collection.children.link(coll)
    coll.objects.link(high)
    bpy.context.scene.collection.objects.unlink(high)
    texset(low).bake_high = coll
    _select(low)
    return low, coll, parent


def _layer_collection(name):
    def walk(lc):
        if lc.name == name:
            return lc
        for c in lc.children:
            found = walk(c)
            if found is not None:
                return found
    return walk(bpy.context.view_layer.layer_collection)


def test_bake_with_an_excluded_collection(addon):
    low, coll, _ = _scene()
    lc = _layer_collection("HighPoly")
    lc.exclude = True
    bpy.context.view_layer.update()
    check("High" not in bpy.context.view_layer.objects, "setup: high should be excluded")
    check(_bake() == {'FINISHED'}, "bake with an excluded collection failed")
    px = _center(baked("Low", "NormalWorld"))
    check(max(px[:3]) > 0.1, f"the excluded high-poly left no mark: {px}")
    check(_layer_collection("HighPoly").exclude is True,
          "the collection must be excluded again afterwards")
    check("High" not in bpy.context.view_layer.objects, "high-poly left in the view layer")


def test_bake_with_a_hidden_nested_collection(addon):
    low, coll, parent = _scene(nested=True)
    parent_lc, lc = _layer_collection("Parent"), _layer_collection("HighPoly")
    parent_lc.exclude = True
    bpy.context.view_layer.update()
    check(_bake() == {'FINISHED'}, "bake with an excluded parent failed")
    check(_layer_collection("Parent").exclude is True, "parent must be excluded again")
    _layer_collection("Parent").exclude = False
    lc = _layer_collection("HighPoly")
    lc.hide_viewport = True
    coll.hide_viewport = True
    coll.hide_select = True
    check(_bake() == {'FINISHED'}, "bake with a hidden collection failed")
    check(_layer_collection("HighPoly").hide_viewport is True and coll.hide_viewport is True
          and coll.hide_select is True, "hidden/unselectable flags must be restored")
    check(_layer_collection("Parent").exclude is False, "visible parent stays visible")
    # Nothing changes when everything was visible.
    _layer_collection("HighPoly").hide_viewport = False
    coll.hide_viewport = False
    coll.hide_select = False
    check(_bake() == {'FINISHED'}, "plain bake failed")
    lc = _layer_collection("HighPoly")
    check(lc.exclude is False and lc.hide_viewport is False, "visible collection touched")
