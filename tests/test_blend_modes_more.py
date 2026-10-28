"""The Sprint 10 blend modes: values from baked pixels against Blender's Mix node formulas, on
a float channel and per component on a color, both branches of the split modes."""

import colorsys

import bpy

from harness import check, close, sample_socket
from test_blend_modes import _mode, _top, _two_layers


def _clamp(v):
    return min(max(v, 0.0), 1.0)


def _screen(a, b):
    return 1 - (1 - a) * (1 - b)


REF = {
    'DARKEN': min,
    'LIGHTEN': max,
    'SCREEN': _screen,
    'DODGE': lambda a, b: 0.0 if a == 0 else 1.0 if b >= 1 else min(a / (1 - b), 1.0),
    'BURN': lambda a, b: 0.0 if b <= 0 else _clamp(1 - (1 - a) / b),
    'LINEAR_BURN': lambda a, b: a + b - 1,
    'OVERLAY': lambda a, b: 2 * a * b if a < 0.5 else 1 - 2 * (1 - a) * (1 - b),
    'SOFT_LIGHT': lambda a, b: (1 - a) * b * a + a * _screen(a, b),
    'HARD_LIGHT': lambda a, b: 2 * a * b if b < 0.5 else 1 - 2 * (1 - a) * (1 - b),
    'LINEAR_LIGHT': lambda a, b: a + 2 * b - 1,
    'PIN_LIGHT': lambda a, b: min(a, 2 * b) if b < 0.5 else max(a, 2 * b - 1),
}


def _rough(ob, mat, e):
    return sample_socket(ob, mat, _top(mat, e).outputs['Roughness'])[0]


def test_new_scalar_modes(addon):
    for a, b in ((0.3, 0.7), (0.7, 0.3), (0.6, 0.55)):
        ob, mat, e = _two_layers(a, b)
        for mode, f in REF.items():
            _mode(e, 'ROUGHNESS', mode)
            close(_rough(ob, mat, e), f(a, b), 0.01, f"roughness {mode} ({a}, {b})")
        bpy.data.objects.remove(ob)


def test_new_scalar_mode_respects_factor(addon):
    ob, mat, e = _two_layers(0.3, 0.7)
    _mode(e, 'ROUGHNESS', 'SCREEN')
    _top(mat, e).inputs['Roughness Opacity'].default_value = 0.5
    close(_rough(ob, mat, e), 0.3 + (_screen(0.3, 0.7) - 0.3) * 0.5, 0.01,
          "lerp toward screen")


def _colors(a, b):
    ob, mat, e = _two_layers()
    base = mat.node_tree.nodes[mat.essence.layers[0].node_name]
    base.inputs['Color B'].default_value = (*a, 1.0)
    _top(mat, e).inputs['Color B'].default_value = (*b, 1.0)
    return ob, mat, e


def test_new_color_modes(addon):
    a, b = (0.3, 0.7, 0.6), (0.7, 0.3, 0.45)     # both branches of the split modes
    ob, mat, e = _colors(a, b)
    for mode, f in REF.items():
        _mode(e, 'COLOR', mode)
        got = sample_socket(ob, mat, _top(mat, e).outputs['Color'])
        for i, n in enumerate("rgb"):
            close(got[i], _clamp(f(a[i], b[i])), 0.01, f"color {mode} {n}")


def test_hsv_color_modes(addon):
    a, b = (0.6, 0.3, 0.2), (0.2, 0.4, 0.8)
    ob, mat, e = _colors(a, b)
    ha, sa, va = colorsys.rgb_to_hsv(*a)
    hb, sb, vb = colorsys.rgb_to_hsv(*b)
    want = {'HUE': (hb, sa, va), 'SATURATION': (ha, sb, va), 'COLOR': (hb, sb, va),
            'VALUE': (ha, sa, vb)}
    for mode, hsv in want.items():
        _mode(e, 'COLOR', mode)
        got = sample_socket(ob, mat, _top(mat, e).outputs['Color'])
        for i, (g, w) in enumerate(zip(got[:3], colorsys.hsv_to_rgb(*hsv))):
            close(g, w, 0.02, f"color {mode} component {i}")


def test_color_only_modes_are_not_offered_on_floats(addon):
    from essence.channels import BY_ID
    blends = addon.blends
    rough = [m for m in blends.modes_for(BY_ID['ROUGHNESS'])]
    color = [m for m in blends.modes_for(BY_ID['COLOR'])]
    check('HUE' not in rough and 'VALUE' not in rough, f"no HSV modes on roughness: {rough}")
    check('HUE' in color and 'PIN_LIGHT' in color and 'PIN_LIGHT' in rough, "the rest on both")
    numbers = [m[1] for m in blends.MODES.values()]
    check(len(set(numbers)) == len(numbers), "every mode has its own stored number")
    check(blends.MODES['EXCLUSION'][1] == 6 and blends.MODES['MIX'][1] == 0,
          "the old numbers are kept (files store them)")


def test_new_modes_survive_save_reload(addon):
    import os
    import tempfile
    ob, mat, e = _two_layers(0.3, 0.7)
    _mode(e, 'ROUGHNESS', 'PIN_LIGHT')
    _mode(e, 'COLOR', 'HUE')
    name = _top(mat, e).node_tree.name
    check("Pin Light" in name and "Hue" in name, f"readable variant name: {name}")
    path = os.path.join(tempfile.gettempdir(), "essence_modes_more.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    bpy.ops.wm.open_mainfile(filepath=path)
    mat = bpy.data.materials[0]
    ob = bpy.data.objects[0]
    e = mat.essence.layers[-1]
    check(next(s for s in e.sources if s.channel == 'ROUGHNESS').blend == 'PIN_LIGHT', "kept")
    check(next(s for s in e.sources if s.channel == 'COLOR').blend == 'HUE', "kept")
    close(_rough(ob, mat, e), REF['PIN_LIGHT'](0.3, 0.7), 0.01, "pin light after reload")
    os.remove(path)
