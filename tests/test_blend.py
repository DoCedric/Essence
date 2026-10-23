"""Evaluate real pixel values of the generated stack against the documented formulas."""

from importlib import import_module

import bpy

from harness import close, make_plane, sample_socket


def _node(mat, e):
    return mat.node_tree.nodes[e.node_name]


def _set(node, color, rough, metal, bump):
    node.inputs["Color B"].default_value = color
    node.inputs["Roughness B"].default_value = rough
    node.inputs["Metallic B"].default_value = metal
    node.inputs["Bump B"].default_value = bump


def _mask(mat, value, invert=False):
    """Add a mask whose image is a flat `value` (with an Invert item on top if `invert`);
    returns its group node."""
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    e = mat.essence.layers[mat.essence.active_index]
    img = mat.node_tree.nodes[e.mask_node_name].image
    img.generated_color = (value, value, value, 1.0)
    node = mat.node_tree.nodes[e.node_name]
    if invert:
        bpy.ops.essence.add_adjustment(adj_type='INVERT')
    return node


def _select(mat, name):
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(name)


def _top(mat):
    return next(_node(mat, e) for e in reversed(list(mat.essence.layers)) if e.kind != 'MASK')


def _two_layers():
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    _set(_node(mat, mat.essence.layers[-1]), (1, 0, 0, 1), 0.2, 1.0, 0.1)
    bpy.ops.essence.add_layer()
    _set(_node(mat, mat.essence.layers[-1]), (0, 0, 1, 1), 0.8, 0.0, 0.3)
    return ob, mat


def test_full_mask_replaces(addon):
    ob, mat = _two_layers()
    top = _top(mat)
    r, g, bl, _ = sample_socket(ob, mat, top.outputs["Color"])
    close(r, 0, msg="red")
    close(bl, 1, msg="blue")
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.8, msg="roughness")
    close(sample_socket(ob, mat, top.outputs["Metallic"])[0], 0.0, msg="metallic")
    close(sample_socket(ob, mat, top.outputs["Bump"])[0], 0.4, msg="bump add")


def test_mask_blend(addon):
    ob, mat = _two_layers()
    _mask(mat, 0.25)
    top = _top(mat)
    r, g, bl, _ = sample_socket(ob, mat, top.outputs["Color"])
    close(r, 0.75, msg="red lerp")
    close(bl, 0.25, msg="blue lerp")
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.2 + 0.6 * 0.25, msg="rough lerp")
    close(sample_socket(ob, mat, top.outputs["Bump"])[0], 0.1 + 0.3 * 0.25, msg="bump masked add")


def test_mask_invert_and_stack(addon):
    ob, mat = _two_layers()
    _mask(mat, 0.25, invert=True)  # 0.75
    top = _top(mat)
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.2 + 0.6 * 0.75, msg="invert")
    # A second value is a Fill on the mask, applied after the Invert item: 0.75 * 0.5
    bpy.ops.essence.add_fill()
    fill = mat.essence.layers[mat.essence.active_index]
    mat.node_tree.nodes[fill.node_name].inputs[1].default_value = 0.5
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.2 + 0.6 * 0.375, msg="stack")


def test_mask_disable(addon):
    ob, mat = _two_layers()
    m = _mask(mat, 0.0)
    top = _top(mat)
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.2, msg="black mask hides")
    m.inputs["Enable"].default_value = False  # disabled mask counts as white
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.8, msg="disabled = white")


def test_channel_enable_leaks(addon):
    ob, mat = _two_layers()
    top = _top(mat)
    top.inputs["Roughness Enable"].default_value = False
    close(sample_socket(ob, mat, top.outputs["Roughness"])[0], 0.2, msg="roughness leak")
    close(sample_socket(ob, mat, top.outputs["Metallic"])[0], 0.0, msg="metallic unaffected")


def test_folder_lerp(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    _set(_node(mat, mat.essence.layers[-1]), (1, 0, 0, 1), 0.2, 0.0, 0.0)
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()  # into the folder
    _set(_node(mat, mat.essence.layers[mat.essence.active_index]), (0, 0, 1, 1), 0.8, 0.0, 0.5)
    _select(mat, "Folder 1")
    _mask(mat, 0.5)
    folder = next(mat.node_tree.nodes[e.node_name] for e in mat.essence.layers
                  if e.kind == 'FOLDER')
    close(sample_socket(ob, mat, folder.outputs["Roughness"])[0], 0.5, msg="folder lerp")
    close(sample_socket(ob, mat, folder.outputs["Bump"])[0], 0.25, msg="folder bump")


def test_mask_brightness_contrast_direction(addon):
    ob, mat = _two_layers()
    m = _mask(mat, 0.5)
    top = _top(mat)
    base = sample_socket(ob, mat, top.outputs["Roughness"])[0]
    close(base, 0.5, 0.02, "mask 0.5 baseline")
    bpy.ops.essence.add_adjustment(adj_type='BRIGHT_CONTRAST')
    adj = mat.essence.layers[mat.essence.active_index].uid
    bc = import_module("essence").sources.source_nodes(mat.node_tree, adj, 'ADJ')['MAIN']
    bc.inputs['Bright'].default_value = 50
    bright = sample_socket(ob, mat, top.outputs["Roughness"])[0]
    assert bright > base + 0.05, f"brightness did not brighten: {base} -> {bright}"
    bc.inputs['Bright'].default_value = -50
    dark = sample_socket(ob, mat, top.outputs["Roughness"])[0]
    assert dark < base - 0.05, f"negative brightness did not darken: {base} -> {dark}"
