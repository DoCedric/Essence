"""The Specular, Specular Tint and Ambient Occlusion channels."""

import os
import shutil
import tempfile

import bpy
import numpy as np

from harness import check, close, make_plane, sample_socket
from test_channels import _state
from test_mesh_maps import _grey_ao, _slot


def _setup():
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    return ob, mat


def _top(mat):
    return mat.node_tree.nodes[mat.essence.layers[-1].node_name]


def _bsdf(addon, mat):
    return addon.nodes.find_principled(mat.node_tree)


def _fed(ob, mat, sock):
    """What a BSDF input receives (baked)."""
    check(sock.is_linked, f"{sock.name} is linked")
    return sample_socket(ob, mat, sock.links[0].from_socket)


def test_new_channels_are_off_by_default(addon):
    ob, mat = _setup()
    active = [c.id for c in addon.channels.active_channels(mat)]
    for ch in ('SPECULAR', 'SPECULAR_TINT', 'AO'):
        check(ch not in active, f"{ch} is off by default")
        check(_top(mat).inputs.get(f"{addon.channels.BY_ID[ch].label} B") is None,
              f"{ch}: no sockets while off")
    check(_top(mat).node_tree.name == addon.nodes.GROUP_NAME, "old layers keep their group")


def test_specular_channels_feed_the_bsdf(addon):
    ob, mat = _setup()
    _state(mat, 'SPECULAR').enabled = True
    _state(mat, 'SPECULAR_TINT').enabled = True
    top = _top(mat)
    top.inputs["Specular B"].default_value = 0.8
    top.inputs["Specular Tint B"].default_value = (1.0, 0.5, 0.25, 1.0)
    bsdf = _bsdf(addon, mat)
    close(_fed(ob, mat, bsdf.inputs['Specular IOR Level'])[0], 0.8, 0.01, "specular level")
    tint = _fed(ob, mat, bsdf.inputs['Specular Tint'])
    for got, want in zip(tint[:3], (1.0, 0.5, 0.25)):
        close(got, want, 0.01, "specular tint")
    # The AO overlay still lowers the specular, now the stack's.
    _slot(addon, mat, 'AO').image = _grey_ao(0.5)
    bpy.ops.essence.rebuild()
    close(_fed(ob, mat, bsdf.inputs['Specular IOR Level'])[0], 0.4, 0.01,
          "the stack's specular times the AO")
    _state(mat, 'SPECULAR').enabled = False
    close(_fed(ob, mat, bsdf.inputs['Specular IOR Level'])[0], 0.25, 0.01,
          "off: the BSDF's own 0.5 times the AO")


def _ao_rgb(addon, ob, mat):
    color = mat.node_tree.nodes[addon.nodes.AO_COLOR_NODE]
    return sample_socket(ob, mat, next(o for o in color.outputs if o.identifier == "Result_Color"))


def test_ao_channel_drives_the_overlay(addon):
    ob, mat = _setup()
    _top(mat).inputs["Color B"].default_value = (0.8, 0.8, 0.8, 1.0)
    bsdf = _bsdf(addon, mat)
    _state(mat, 'AO').enabled = True
    _top(mat).inputs["Ambient Occlusion B"].default_value = 0.5
    bpy.ops.essence.rebuild()
    check(bsdf.inputs['Base Color'].links[0].from_node.name == addon.nodes.AO_COLOR_NODE,
          "the channel alone turns the overlay on (no baked AO)")
    close(_ao_rgb(addon, ob, mat)[0], 0.4, 0.01, "0.8 * AO channel 0.5")
    _slot(addon, mat, 'AO').image = _grey_ao(0.5)
    bpy.ops.essence.rebuild()
    close(_ao_rgb(addon, ob, mat)[0], 0.2, 0.01, "0.8 * baked 0.5 * channel 0.5")
    # Preview of the channel shows the stack's AO.
    sock = addon.preview._resolve(mat, 'CHANNEL', 'AO')
    check(sock is not None, "the AO channel can be previewed")
    close(sample_socket(ob, mat, sock)[0], 0.5, 0.01, "the preview shows the channel")
    _state(mat, 'AO').enabled = False
    check(mat.node_tree.nodes.get(addon.nodes.AO_COMBINE_NODE) is None, "combine node gone")
    close(_ao_rgb(addon, ob, mat)[0], 0.4, 0.01, "off: the baked AO alone")
    _slot(addon, mat, 'AO').image = None
    bpy.ops.essence.rebuild()
    check(not any(l.from_node.name == addon.nodes.AO_COLOR_NODE
                  for l in bsdf.inputs['Base Color'].links), "no AO at all: overlay off")


def _load(path):
    img = bpy.data.images.load(path)
    img.colorspace_settings.name = 'Non-Color'
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[h // 2, w // 2]


def test_export_writes_the_new_channels(addon):
    ob, mat = _setup()
    for ch in ('SPECULAR', 'SPECULAR_TINT', 'AO'):
        _state(mat, ch).enabled = True
    top = _top(mat)
    top.inputs["Specular B"].default_value = 0.8
    top.inputs["Specular Tint B"].default_value = (1.0, 0.5, 0.25, 1.0)
    top.inputs["Ambient Occlusion B"].default_value = 0.5
    _slot(addon, mat, 'AO').image = _grey_ao(0.6)
    bpy.ops.essence.rebuild()
    stem = bpy.path.clean_name(mat.name)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    sc = bpy.context.scene
    try:
        sc.essence_export_preset = 'PBR'
        sc.essence_export_format = 'OPEN_EXR'
        sc.essence_export_depth = '32'
        sc.essence_export_size = '512'
        sc.essence_export_dir = folder
        sc.essence_export_overwrite = True
        check(bpy.ops.essence.export() == {'FINISHED'}, "export failed")
        close(_load(os.path.join(folder, f"{stem}_Specular.exr"))[0], 0.8, 0.002, "specular")
        tint = _load(os.path.join(folder, f"{stem}_SpecularTint.exr"))
        close(tint[1], 0.5, 0.002, "specular tint green (linear in EXR)")
        close(_load(os.path.join(folder, f"{stem}_AO.exr"))[0], 0.3, 0.002,
              "AO: baked 0.6 * channel 0.5")
        sc.essence_export_preset = 'UNITY_HDRP'
        bpy.ops.essence.export()
        close(_load(os.path.join(folder, f"{stem}_MaskMap.exr"))[1], 0.3, 0.002,
              "MaskMap green is the same AO")
        # The channel alone, no baked AO.
        _slot(addon, mat, 'AO').image = None
        bpy.data.images.remove(bpy.data.images["ao_0.6"])
        bpy.ops.essence.rebuild()
        sc.essence_export_preset = 'PBR'
        bpy.ops.essence.export()
        close(_load(os.path.join(folder, f"{stem}_AO.exr"))[0], 0.5, 0.002,
              "AO from the channel alone")
        # The baked material takes the specular channels.
        bpy.ops.essence.bake_material()
        flat = bpy.data.materials[f"{mat.name} Baked"]
        bsdf = next(n for n in flat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        check(bsdf.inputs['Specular IOR Level'].is_linked and bsdf.inputs['Specular Tint'].is_linked,
              "the baked material has specular and tint")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
