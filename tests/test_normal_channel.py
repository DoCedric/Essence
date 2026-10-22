import math

import bpy

from harness import check, close, make_plane, sample_socket


def _enc(v):
    return tuple(c * 0.5 + 0.5 for c in v) + (1.0,)


def _dec(c):
    return tuple(x * 2 - 1 for x in c[:3])


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _tilt(sx, sy):
    z = math.sqrt(max(0.0, 1 - sx * sx - sy * sy))
    return (sx, sy, z)


def _whiteout(a, b):
    return _norm((a[0] + b[0], a[1] + b[1], a[2] * b[2]))


def _rnm(a, b):
    t = (a[0], a[1], a[2] + 1)
    u = (-b[0], -b[1], b[2])
    d = sum(x * y for x, y in zip(t, u))
    return _norm(tuple(t[i] * d / t[2] - u[i] for i in range(3)))


def _setup(a_tilt, b_tilt):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    next(c for c in mat.essence.channels if c.id == 'NORMAL').enabled = True
    base = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    base.inputs['Normal B'].default_value = _enc(a_tilt)
    bpy.ops.essence.add_layer()
    top_e = mat.essence.layers[-1]
    top = mat.node_tree.nodes[top_e.node_name]
    top.inputs['Normal B'].default_value = _enc(b_tilt)
    return ob, mat, top_e


def _sample_normal(ob, mat, entry):
    c = sample_socket(ob, mat, mat.node_tree.nodes[entry.node_name].outputs['Normal'])
    return _dec(c)


def _mode(entry, mode):
    next(s for s in entry.sources if s.channel == 'NORMAL').blend = mode


def test_normal_wiring(addon):
    ob, mat, top = _setup(_tilt(0.2, 0), _tilt(0, 0.3))
    tree = mat.node_tree
    nm = tree.nodes[addon.nodes.NORMAL_NODE_NAME]
    bump = tree.nodes[addon.nodes.BUMP_NODE_NAME]
    layer = tree.nodes[top.node_name]
    check(nm.inputs['Color'].links[0].from_node.name == layer.name, "layer normal not into the Normal Map")
    check(nm.inputs['Color'].links[0].from_socket.name == 'Normal', "wrong layer output")
    check(bump.inputs['Normal'].links[0].from_node.name == nm.name, "Normal Map not into bump")
    check(addon.nodes.find_principled(tree).inputs['Normal'].is_linked, "bump not into BSDF")


def test_normal_replace(addon):
    a, b = _tilt(0.2, 0), _tilt(0, 0.3)
    ob, mat, top = _setup(a, b)
    got = _sample_normal(ob, mat, top)
    for i in range(3):
        close(got[i], b[i], 0.03, f"mix component {i}")


def test_normal_whiteout(addon):
    a, b = _tilt(0.3, 0.1), _tilt(0.2, -0.2)
    ob, mat, top = _setup(a, b)
    _mode(top, 'WHITEOUT')
    want = _whiteout(a, b)
    got = _sample_normal(ob, mat, top)
    for i in range(3):
        close(got[i], want[i], 0.03, f"whiteout component {i}")


def test_normal_detail_rnm(addon):
    a, b = _tilt(0.3, 0.1), _tilt(0.2, -0.2)
    ob, mat, top = _setup(a, b)
    _mode(top, 'DETAIL')
    want = _rnm(a, b)
    got = _sample_normal(ob, mat, top)
    for i in range(3):
        close(got[i], want[i], 0.03, f"rnm component {i}")


def test_normal_factor(addon):
    a, b = _tilt(0.3, 0), _tilt(0, 0)
    ob, mat, top = _setup(a, b)
    mat.node_tree.nodes[top.node_name].inputs['Normal Opacity'].default_value = 0.0
    got = _sample_normal(ob, mat, top)
    close(got[0], a[0], 0.03, "opacity 0 leaves the normal below")


def test_baked_normal_is_the_base(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    next(c for c in mat.essence.channels if c.id == 'NORMAL').enabled = True
    sc = bpy.context.scene
    sc.essence_bake_map = 'NORMAL'
    sc.essence_bake_size = '128'
    sc.essence_bake_samples = 4
    check(bpy.ops.essence.bake() == {'FINISHED'}, "bake failed")
    tree = mat.node_tree
    baked = tree.nodes[addon.nodes.BAKED_NORMAL_NODE]
    layer = tree.nodes[mat.essence.layers[-1].node_name]
    nm = tree.nodes[addon.nodes.NORMAL_NODE_NAME]
    check(layer.inputs['Normal A'].links[0].from_node.name == baked.name,
          "baked normal should feed the bottom layer")
    check(nm.inputs['Color'].links[0].from_node.name == layer.name,
          "stack result should feed the Normal Map")
    next(c for c in mat.essence.channels if c.id == 'NORMAL').enabled = False
    check(nm.inputs['Color'].links[0].from_node.name == baked.name,
          "without the normal channel the baked normal feeds the Normal Map directly")


def test_normal_image_source(addon):
    ob, mat, top = _setup(_tilt(0, 0), _tilt(0, 0))
    src = next(s for s in top.sources if s.channel == 'NORMAL')
    src.kind = 'IMAGE'
    n = addon.sources.source_nodes(mat.node_tree, top.uid, 'NORMAL')
    check('REMAP' not in n and 'RAMP' not in n, "a normal image needs no remap")
    img = bpy.data.images.new("n", 8, 8, float_buffer=True)
    img.generated_color = (*_enc(_tilt(0.4, 0))[:3], 1.0)
    img.colorspace_settings.name = 'Non-Color'
    n['TEXTURE'].image = img
    got = _sample_normal(ob, mat, top)
    close(got[0], 0.4, 0.03, "normal from image")
    kinds = [i[0] for i in src.bl_rna.properties['kind'].enum_items]
    check('PROCEDURAL' not in kinds, f"normal should not offer a procedural source: {kinds}")
