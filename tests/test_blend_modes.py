import bpy

from harness import check, close, make_plane, sample_socket


def _two_layers(base_rough=0.5, top_rough=0.8):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    base = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    base.inputs['Roughness B'].default_value = base_rough
    base.inputs['Color B'].default_value = (0.5, 0.5, 0.5, 1)
    base.inputs['Bump B'].default_value = 0.2
    bpy.ops.essence.add_layer()
    top_entry = mat.essence.layers[-1]
    top = mat.node_tree.nodes[top_entry.node_name]
    top.inputs['Roughness B'].default_value = top_rough
    top.inputs['Color B'].default_value = (0.5, 0.25, 1.0, 1)
    top.inputs['Bump B'].default_value = 0.3
    return ob, mat, top_entry


def _mode(entry, channel, mode):
    next(s for s in entry.sources if s.channel == channel).blend = mode


def _top(mat, entry):
    return mat.node_tree.nodes[entry.node_name]


def _rough(ob, mat, entry):
    return sample_socket(ob, mat, _top(mat, entry).outputs['Roughness'])[0]


def test_scalar_modes(addon):
    ob, mat, e = _two_layers(0.5, 0.8)
    expect = {'MIX': 0.8, 'ADD': 1.3, 'MULTIPLY': 0.4, 'SUBTRACT': -0.3, 'DIVIDE': 0.5 / 0.8,
              'DIFFERENCE': 0.3, 'EXCLUSION': 0.5 + 0.8 - 2 * 0.5 * 0.8}
    for mode, want in expect.items():
        _mode(e, 'ROUGHNESS', mode)
        close(_rough(ob, mat, e), want, 0.02, f"roughness {mode}")


def test_scalar_mode_respects_factor(addon):
    ob, mat, e = _two_layers(0.5, 0.8)
    _mode(e, 'ROUGHNESS', 'MULTIPLY')
    _top(mat, e).inputs['Roughness Opacity'].default_value = 0.5
    close(_rough(ob, mat, e), 0.5 + (0.4 - 0.5) * 0.5, 0.02, "lerp toward multiply")


def test_color_modes(addon):
    ob, mat, e = _two_layers()
    _mode(e, 'COLOR', 'MULTIPLY')
    r, g, b, _ = sample_socket(ob, mat, _top(mat, e).outputs['Color'])
    close(r, 0.25, 0.02, "multiply r")
    close(g, 0.125, 0.02, "multiply g")
    close(b, 0.5, 0.02, "multiply b")
    _mode(e, 'COLOR', 'SUBTRACT')
    r, g, b, _ = sample_socket(ob, mat, _top(mat, e).outputs['Color'])
    close(r, 0.0, 0.02, "subtract r")
    close(b, 0.0, 0.02, "subtract b (0.5 - 1.0)")


def test_bump_default_add_and_mix(addon):
    ob, mat, e = _two_layers()
    bump = next(s for s in e.sources if s.channel == 'BUMP')
    check(bump.blend == 'ADD', f"bump should default to Add, got {bump.blend}")
    close(sample_socket(ob, mat, _top(mat, e).outputs['Bump'])[0], 0.5, 0.02, "bump add")
    bump.blend = 'MIX'
    close(sample_socket(ob, mat, _top(mat, e).outputs['Bump'])[0], 0.3, 0.02, "bump replace")


def test_variant_groups(addon):
    ob, mat, e = _two_layers()
    default = bpy.data.node_groups[addon.nodes.GROUP_NAME]
    check(_top(mat, e).node_tree.name == addon.nodes.GROUP_NAME, "default layer on the base group")
    _mode(e, 'ROUGHNESS', 'MULTIPLY')
    ng = _top(mat, e).node_tree
    check(ng.name != addon.nodes.GROUP_NAME and ng.name.startswith("Essence Layer"),
          f"variant name {ng.name}")
    check("Multiply" in ng.name, f"variant name should say the mode: {ng.name}")
    other = mat.essence.layers[0]
    _mode(other, 'ROUGHNESS', 'MULTIPLY')
    check(mat.node_tree.nodes[other.node_name].node_tree is ng, "same modes share one group")
    _mode(e, 'ROUGHNESS', 'MIX')
    check(_top(mat, e).node_tree is default or _top(mat, e).node_tree.name == default.name,
          "back to the default group")


def test_mode_switch_keeps_values_and_links(addon):
    ob, mat, e = _two_layers()
    src = next(s for s in e.sources if s.channel == 'ROUGHNESS')
    src.kind = 'PROCEDURAL'
    src.proc_type = 'GRADIENT'
    top = _top(mat, e)
    top.inputs['Metallic B'].default_value = 0.7
    top.inputs['Opacity'].default_value = 0.9
    before = sample_socket(ob, mat, top.outputs['Roughness'])[0]
    _mode(e, 'COLOR', 'MULTIPLY')
    top = _top(mat, e)
    close(top.inputs['Metallic B'].default_value, 0.7, 1e-4, "metallic value kept")
    close(top.inputs['Opacity'].default_value, 0.9, 1e-4, "opacity kept")
    check(top.inputs['Roughness B'].is_linked, "source link lost on mode switch")
    close(sample_socket(ob, mat, top.outputs['Roughness'])[0], before, 0.02, "roughness unchanged")


def test_modes_survive_save_reload(addon):
    import os
    import tempfile
    ob, mat, e = _two_layers()
    _mode(e, 'ROUGHNESS', 'MULTIPLY')
    path = os.path.join(tempfile.gettempdir(), "essence_modes_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    mat = bpy.data.materials[0]
    ob = bpy.data.objects[0]
    bpy.context.view_layer.objects.active = ob
    e = mat.essence.layers[-1]
    check(next(s for s in e.sources if s.channel == 'ROUGHNESS').blend == 'MULTIPLY', "mode lost")
    bpy.ops.essence.rebuild()
    top = mat.node_tree.nodes[e.node_name]
    close(sample_socket(ob, mat, top.outputs['Roughness'])[0], 0.4, 0.02, "multiply after reload")
    os.remove(path)


def test_folder_unaffected_by_modes(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_folder()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    inner = mat.essence.layers[mat.essence.active_index]
    _mode(inner, 'ROUGHNESS', 'MULTIPLY')
    folder = next(e for e in mat.essence.layers if e.kind == 'FOLDER')
    check(mat.node_tree.nodes[folder.node_name].node_tree.name == addon.nodes.FOLDER_GROUP_NAME,
          "folder keeps its base group")
