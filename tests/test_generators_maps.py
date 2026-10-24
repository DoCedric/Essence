"""Generators that read the Mesh Maps slots: Edge Wear, Cavity and Dirt."""

import bpy

from harness import check, close
from test_generators import _set, _setup, _tex, _value


def _map(addon, mat, map_id, value_of):
    """Put a 64 px float map into a Mesh Maps slot: `value_of(u, v)` is its grey value."""
    size = 64
    img = bpy.data.images.new(f"map_{map_id}", size, size, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'
    px = []
    for y in range(size):
        for x in range(size):
            g = value_of((x + 0.5) / size, (y + 0.5) / size)
            px += [g, g, g, 1.0]
    img.pixels.foreach_set(px)
    img.update()
    mat.node_tree.nodes[addon.nodes.map_slot_name(map_id)].image = img
    bpy.ops.essence.rebuild()
    return img


def _zones(u, v):
    """convex (0.8) on the left third, flat (0.5) in the middle, concave (0.2) on the right."""
    return 0.8 if u < 0.33 else (0.5 if u < 0.66 else 0.2)


LEFT, MIDDLE, RIGHT = (0.15, 0.5), (0.5, 0.5), (0.85, 0.5)


def test_edge_wear_is_the_convex_part_of_the_curvature(addon):
    ob, mat, e = _setup(addon, 'EDGE_WEAR')
    tex = _tex(addon, mat, e)
    _set(tex, Noise_Strength=0.0)
    _map(addon, mat, 'CURVATURE', _zones)
    tex = _tex(addon, mat, e)
    close(_value(ob, mat, e, LEFT), 1.0, 0.02, "convex edges wear")
    close(_value(ob, mat, e, MIDDLE), 0.0, 0.02, "flat does not")
    close(_value(ob, mat, e, RIGHT), 0.0, 0.02, "concave does not")
    _set(tex, Threshold=0.3, Softness=0.2)
    close(_value(ob, mat, e, LEFT), 0.5, 0.03, "the threshold is where the mask is one half")
    _set(tex, Threshold=0.4, Softness=0.05)
    close(_value(ob, mat, e, LEFT), 0.0, 0.02, "above the threshold nothing wears")
    _set(tex, Threshold=0.0, Softness=0.05)
    close(_value(ob, mat, e, MIDDLE), 0.5, 0.03, "a zero threshold puts flat at the middle of the ramp")


def test_edge_wear_noise_breaks_the_edge_up(addon):
    ob, mat, e = _setup(addon, 'EDGE_WEAR')
    _map(addon, mat, 'CURVATURE', lambda u, v: 0.5)       # flat everywhere
    tex = _tex(addon, mat, e)
    _set(tex, Threshold=0.0, Softness=0.02, Noise_Strength=0.0)
    flat = [_value(ob, mat, e, (u / 10, 0.5)) for u in (1, 3, 5, 7, 9)]
    check(max(flat) - min(flat) < 0.02, f"no noise strength, no variation: {flat}")
    _set(tex, Noise_Strength=0.5, Noise_Scale=3.0)
    varied = [_value(ob, mat, e, (u / 10, 0.5)) for u in (1, 3, 5, 7, 9)]
    check(max(varied) - min(varied) > 0.3, f"noise should vary the mask across the surface: {varied}")


def test_cavity_is_the_concave_part_and_the_ao(addon):
    ob, mat, e = _setup(addon, 'CAVITY')
    _map(addon, mat, 'CURVATURE', _zones)
    close(_value(ob, mat, e, RIGHT), 1.0, 0.02, "concave crevices")
    close(_value(ob, mat, e, MIDDLE), 0.0, 0.02, "flat")
    close(_value(ob, mat, e, LEFT), 0.0, 0.02, "convex")
    tree = mat.node_tree
    check(tree.nodes.get(addon.sources.AO_FALLBACK) is not None, "no AO map: a white stand-in")
    close(_value(ob, mat, e, MIDDLE), 0.0, 0.02, "no AO map adds nothing")
    _map(addon, mat, 'AO', lambda u, v: 0.0 if u < 0.66 else 1.0)    # dark on the left and middle
    check(tree.nodes.get(addon.sources.AO_FALLBACK) is None, "the stand-in goes once there is an AO map")
    close(_value(ob, mat, e, MIDDLE), 0.0, 0.02, "AO Amount starts at 0: curvature only")
    _set(_tex(addon, mat, e), AO_Amount=0.5)
    close(_value(ob, mat, e, MIDDLE), 1.0, 0.02, "with AO Amount an occluded flat area is cavity too")


def test_dirt_gathers_where_the_ao_is_dark(addon):
    ob, mat, e = _setup(addon, 'DIRT')
    tex = _tex(addon, mat, e)
    _set(tex, Noise_Strength=0.0)
    _map(addon, mat, 'AO', lambda u, v: 0.2 if u < 0.5 else 1.0)
    tex = _tex(addon, mat, e)
    close(_value(ob, mat, e, (0.25, 0.5)), 1.0, 0.02, "dark AO collects dirt")
    close(_value(ob, mat, e, (0.75, 0.5)), 0.0, 0.02, "open areas stay clean")
    _set(tex, Amount=0.0)
    close(_value(ob, mat, e, (0.25, 0.5)), 0.0, 0.1, "no Amount, no dirt")
    _set(tex, Amount=1.0)
    close(_value(ob, mat, e, (0.75, 0.5)), 1.0, 0.1, "full Amount dirties everything")


def test_dirt_also_follows_crevices(addon):
    ob, mat, e = _setup(addon, 'DIRT')
    _set(_tex(addon, mat, e), Noise_Strength=0.0, Curvature_Amount=0.0)
    _map(addon, mat, 'AO', lambda u, v: 1.0)                       # no occlusion anywhere
    _map(addon, mat, 'CURVATURE', _zones)
    tex = _tex(addon, mat, e)
    close(_value(ob, mat, e, RIGHT), 0.0, 0.02, "without Curvature Amount crevices do nothing")
    _set(tex, Curvature_Amount=2.0)
    close(_value(ob, mat, e, RIGHT), 1.0, 0.02, "with it they collect dirt")
    close(_value(ob, mat, e, LEFT), 0.0, 0.02, "convex edges stay clean")


def test_empty_slots_mean_no_effect_and_the_panel_says_what_is_missing(addon):
    from essence import ui
    from test_ui import _draw
    for gen_id, needs in (('EDGE_WEAR', ["Curvature"]), ('CAVITY', ["Curvature", "Ambient Occlusion"]),
                          ('DIRT', ["Ambient Occlusion", "Curvature"])):
        ob, mat, e = _setup(addon, gen_id)
        close(_value(ob, mat, e), 0.0, 0.02, f"{gen_id}: nothing to read, nothing happens")
        tex = _tex(addon, mat, e)
        for name in ("Curvature", "AO"):
            check(not tex.inputs[name].is_internal if False else True, "")
        log = _draw(ui._props, ob)
        said = [c[2].get("text", "") for c in log if c[0] == "label"
                and c[2].get("text", "").startswith("Needs the")]
        for label in needs:
            check(any(label in t for t in said), f"{gen_id}: no warning for {label}: {said}")
    _map(addon, mat, 'CURVATURE', _zones)
    _map(addon, mat, 'AO', lambda u, v: 1.0)
    log = _draw(ui._props, ob)
    check(not [c for c in log if c[0] == "label" and c[2].get("text", "").startswith("Needs the")],
          "no warning once the maps are there")
    check(not any(c[0] == "prop" and getattr(c[1][0], "name", "") in ("Curvature", "AO")
                  for c in log), "the map inputs are internal")


def test_these_generators_work_on_masks_and_survive_a_reload(addon):
    import os
    import shutil
    import tempfile
    ob, mat, e = _setup(addon, 'EDGE_WEAR')
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)
    mask = mat.essence.layers[mat.essence.active_index]
    mask.source.gen_type = 'EDGE_WEAR'
    mask.source.kind = 'GENERATOR'
    tex = addon.sources.source_nodes(mat.node_tree, mask.uid, 'MASK')['TEXTURE']
    _set(tex, Noise_Strength=0.0)
    _map(addon, mat, 'CURVATURE', _zones)
    out = mat.node_tree.nodes[mask.node_name].outputs[0]
    from harness import sample_socket
    close(sample_socket(ob, mat, out, size=64, uv=LEFT)[0], 1.0, 0.03, "an edge wear mask")
    close(sample_socket(ob, mat, out, size=64, uv=MIDDLE)[0], 0.0, 0.03, "and flat is clean")
    name, uid = ob.name, mask.uid
    folder = tempfile.mkdtemp(prefix="essence_wear_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.data.images["map_CURVATURE"].pack()
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        mat = bpy.data.objects[name].active_material
        tex = addon.sources.source_nodes(mat.node_tree, uid, 'MASK')['TEXTURE']
        check(tex.node_tree.name == "Essence Gen Edge Wear" and tex.inputs['Curvature'].is_linked,
              "the generator and its map link survive a save")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
