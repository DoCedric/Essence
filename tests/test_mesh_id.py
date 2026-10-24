"""The ID mesh map: a flat color per object, material, mesh island or UV island."""

import bpy

from harness import check, close, make_plane
from test_bake_quality import _bake, _select


def _at(img, u, v):
    w, h = img.size
    x, y = min(w - 1, int(u * w)), min(h - 1, int(v * h))
    px = list(img.pixels)
    i = (y * w + x) * 4
    return tuple(px[i:i + 3])


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _differ(a, b, msg):
    check(_distance(a, b) > 0.15, f"{msg}: {a} and {b} should be different colors")


def _same(a, b, msg):
    check(_distance(a, b) < 0.03, f"{msg}: {a} and {b} should be the same color")


def _grid(name, uv_for):
    """A 2 x 2 grid of quads over x, y in -1..1 (faces 0 to 3), with UVs from
    `uv_for(x, y, face center x)`."""
    mesh = bpy.data.meshes.new(name)
    verts = [(x, y, 0.0) for y in (-1, 0, 1) for x in (-1, 0, 1)]
    faces = [(0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 7, 6), (4, 5, 8, 7)]
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new(name="UVMap")
    for p in mesh.polygons:
        cx = sum(mesh.vertices[mesh.loops[li].vertex_index].co.x for li in p.loop_indices) / 4
        for li in p.loop_indices:
            co = mesh.vertices[mesh.loops[li].vertex_index].co
            uv.uv[li].vector = uv_for(co.x, co.y, cx)
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob


def _planar(x, y, cx):
    return ((x + 1) / 2, (y + 1) / 2)


def _two_islands(x, y, cx):
    """The left half in u 0 to 0.45, the right half in u 0.55 to 1: two UV islands."""
    u = (x + 1) * 0.45 if cx < 0 else 0.55 + x * 0.45
    return (u, (y + 1) / 2)


def _high_collection(*objects):
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    for o in objects:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    sc = bpy.context.scene
    sc.essence_bake_high = coll
    sc.essence_bake_extrusion = 0.5
    return coll


def _half_planes():
    """Two high-poly planes, each covering one half (x < 0 and x > 0) of a 2 x 2 low plane."""
    halves = []
    for name, x in (("HighLeft", -0.5), ("HighRight", 0.5)):
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, 0, 0.02))
        o = bpy.context.active_object
        o.name = name
        o.scale = (1.0, 2.0, 1.0)
        halves.append(o)
    bpy.context.view_layer.update()
    return halves


def _bake_id(mode, low, size="128"):
    sc = bpy.context.scene
    sc.essence_bake_id = mode
    _select(low)
    sc.essence_bake_size = size
    return _bake('ID')


def test_object_ids_per_high_poly_part(addon):
    low = make_plane("Low", size=2.0)
    _high_collection(*_half_planes())
    check(_bake_id('OBJECT', low) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_ID"]
    left, right = _at(img, 0.25, 0.5), _at(img, 0.75, 0.5)
    _differ(left, right, "two high-poly objects")
    _same(left, _at(img, 0.2, 0.2), "inside one part")
    _same(right, _at(img, 0.8, 0.85), "inside the other part")
    check(img.is_float and img.colorspace_settings.name == 'Non-Color', "float, Non-Color")
    check("essence_stamp" in img, "stamped like every bake")
    slot = low.active_material.node_tree.nodes[addon.nodes.map_slot_name('ID')]
    check(slot.image is img, "the ID slot has it")
    # The colors are stable: a second bake gives the same ones.
    first = (left, right)
    _bake_id('OBJECT', low)
    img = bpy.data.images["Low_ID"]
    _same(first[0], _at(img, 0.25, 0.5), "a rebake keeps the colors")
    _same(first[1], _at(img, 0.75, 0.5), "a rebake keeps the colors")


def test_object_ids_of_the_low_poly_itself(addon):
    low = make_plane("Low", size=2.0)
    check(_bake_id('OBJECT', low) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_ID"]
    _same(_at(img, 0.2, 0.2), _at(img, 0.8, 0.8), "one object is one color")


def test_material_ids(addon):
    low = _grid("Low", _planar)
    mats = []
    for name in ("Paint", "Metal"):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        low.data.materials.append(m)
        mats.append(m)
    for p in low.data.polygons:
        cx = sum(low.data.vertices[low.data.loops[li].vertex_index].co.x
                 for li in p.loop_indices) / 4
        p.material_index = 0 if cx < 0 else 1
    check(_bake_id('MATERIAL', low) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_ID"]
    for (u, v), m in (((0.25, 0.3), mats[0]), ((0.75, 0.7), mats[1])):
        expected = addon.bake.name_color(m.name)[:3]
        check(_distance(_at(img, u, v), expected) < 0.03,
              f"{m.name}: {_at(img, u, v)} should be its name color {expected}")
    _differ(_at(img, 0.25, 0.5), _at(img, 0.75, 0.5), "two materials")


def test_material_ids_from_a_high_poly(addon):
    low = make_plane("Low", size=2.0)
    halves = _half_planes()
    for o, name in zip(halves, ("PartA", "PartB")):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        o.data.materials.append(m)
    _high_collection(*halves)
    check(_bake_id('MATERIAL', low) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_ID"]
    check(_distance(_at(img, 0.25, 0.5), addon.bake.name_color("PartA")[:3]) < 0.03,
          "the color of the high-poly material that was hit")
    check(_distance(_at(img, 0.75, 0.5), addon.bake.name_color("PartB")[:3]) < 0.03,
          "and of the other one")


def test_mesh_island_ids_from_one_high_poly_mesh(addon):
    low = make_plane("Low", size=2.0)
    mesh = bpy.data.meshes.new("Pieces")
    verts = [(-1, -1, .02), (0, -1, .02), (0, 1, .02), (-1, 1, .02),
             (0.0, -1, .02), (1, -1, .02), (1, 1, .02), (0.0, 1, .02)]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3), (4, 5, 6, 7)])      # two loose pieces
    pieces = bpy.data.objects.new("Pieces", mesh)
    bpy.context.scene.collection.objects.link(pieces)
    _high_collection(pieces)
    check(_bake_id('MESH_ISLAND', low) == {'FINISHED'}, "bake failed")
    img = bpy.data.images["Low_ID"]
    left, right = _at(img, 0.25, 0.5), _at(img, 0.75, 0.5)
    _differ(left, right, "two loose pieces of one mesh")
    _same(left, _at(img, 0.2, 0.8), "inside one piece")


def test_uv_island_indices(addon):
    one = _grid("One", _planar)
    check(addon.bake.uv_island_indices(one.data) == [0, 0, 0, 0], "a continuous UV is one island")
    two = _grid("Two", _two_islands)
    idx = addon.bake.uv_island_indices(two.data)
    left = [p.index for p in two.data.polygons
            if sum(two.data.vertices[two.data.loops[li].vertex_index].co.x
                   for li in p.loop_indices) < 0]
    right = [i for i in range(4) if i not in left]
    check(len({idx[i] for i in left}) == 1 and len({idx[i] for i in right}) == 1
          and idx[left[0]] != idx[right[0]], f"two islands: {idx}")


def test_uv_island_ids(addon):
    low = _grid("Low", _two_islands)
    # A High Poly collection is set, and must not matter: a UV island belongs to the low poly.
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 0, 0.02))
    plane = bpy.context.active_object
    plane.name = "Decoy"
    _high_collection(plane)
    check(_bake_id('UV_ISLAND', low) == {'FINISHED'}, "bake failed")
    check(bpy.data.images.get("Decoy_ID") is None, "the high poly was baked as a target")
    img = bpy.data.images["Low_ID"]
    left, right = _at(img, 0.2, 0.5), _at(img, 0.8, 0.5)
    _differ(left, right, "two UV islands")
    _same(left, _at(img, 0.3, 0.8), "inside one island")
    _same(right, _at(img, 0.7, 0.2), "inside the other")
    check(addon.bake.UV_ISLAND_ATTRIBUTE not in low.data.attributes,
          "the temporary attribute was left on the mesh")


def test_the_island_attribute_is_removed_even_when_the_bake_fails(addon):
    low = _grid("Low", _two_islands)
    try:
        with addon.bake._uv_island_attribute(low) as count:
            check(count == 2, f"two islands expected: {count}")
            check(addon.bake.UV_ISLAND_ATTRIBUTE in low.data.attributes, "attribute during")
            raise ValueError("boom")
    except ValueError:
        pass
    check(addon.bake.UV_ISLAND_ATTRIBUTE not in low.data.attributes, "attribute left behind")


def test_id_panels_draw(addon):
    from essence import ui
    from test_ui import _draw
    low = make_plane("Low", size=2.0)
    bpy.ops.essence.add_layer()
    sc = bpy.context.scene
    sc.essence_bake_map = 'ID'
    for mode, _label, _desc in addon.bake.ID_MODES:
        sc.essence_bake_id = mode
        for aa in ('1', '2'):
            sc.essence_bake_aa = aa
            log = _draw(ui._bake, low)
            check(any(c[0] == "prop" and c[1][1] == "essence_bake_id" for c in log),
                  f"{mode}: no ID by dropdown")
    sc.essence_bake_id = 'UV_ISLAND'
    log = _draw(ui._bake, low)
    check(not any(c[0] == "prop" and c[1][1] == "essence_bake_high" for c in log),
          "UV island IDs ignore the High Poly collection, so it is not offered")
    log = _draw(ui._maps, low)
    check(any(c[0] == "template_ID" and getattr(c[1][0], "name", "") ==
              addon.nodes.map_slot_name('ID') for c in log), "no ID slot drawn")
    check(addon.bake._previews.get('ID') is not None, "the ID thumbnail did not load")
    check(addon.bake._previews.get('POSITION') is not None, "the Position thumbnail did not load")
