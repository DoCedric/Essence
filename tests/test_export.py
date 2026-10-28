"""Export presets, bit depths and formats, dilation, and the flattened Blender material."""

import os
import shutil
import tempfile

import bpy
import numpy as np

from harness import check, close, make_plane
from test_channels import _state


def _setup(color=(0.2, 0.4, 0.6, 1.0), rough=0.3, metal=0.8):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    node = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    node.inputs["Color B"].default_value = color
    node.inputs["Roughness B"].default_value = rough
    node.inputs["Metallic B"].default_value = metal
    return ob, mat, node


def _export(folder, preset, fmt='PNG', depth='8', dilation='INFINITE', padding=4,
            overwrite=True):
    sc = bpy.context.scene
    sc.essence_export_overwrite = overwrite
    sc.essence_export_preset = preset
    sc.essence_export_format = fmt
    sc.essence_export_depth = depth
    sc.essence_export_size = '512'
    sc.essence_export_dilation = dilation
    sc.essence_export_padding = padding
    sc.essence_export_dir = folder
    return bpy.ops.essence.export()


def _load(path):
    img = bpy.data.images.load(path)
    img.colorspace_settings.name = 'Non-Color'
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)


def _png_header(path):
    """(bit depth, color type: 0 grey, 2 RGB, 6 RGBA)."""
    with open(path, "rb") as f:
        head = f.read(26)
    return head[24], head[25]


def _srgb(x):
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def test_presets_write_their_files(addon):
    ob, mat, node = _setup()
    stem = bpy.path.clean_name(mat.name)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    want = {
        'PBR': ["AO", "Color", "Height", "Metallic", "Normal", "Roughness"],  # Bump is on
        'UNITY_HDRP': ["BaseMap", "MaskMap", "Normal"],
        'UNITY_URP': ["BaseMap", "MetallicSmoothness", "Normal", "Occlusion"],
        'UNREAL': ["BaseColor", "Normal", "OcclusionRoughnessMetallic"],
        'GLTF': ["BaseColor", "Normal", "OcclusionRoughnessMetallic"],
    }
    # An AO image in the slot, so the AO files are written.
    ao = bpy.data.images.new(f"{mat.name}_AO", 32, 32, float_buffer=True)
    ao.colorspace_settings.name = 'Non-Color'
    ao.pixels.foreach_set(np.tile(np.array([0.5, 0.5, 0.5, 1.0], np.float32), 32 * 32))
    try:
        for preset, files in want.items():
            sub = os.path.join(folder, preset)
            check(_export(sub, preset) == {'FINISHED'}, f"{preset}: export failed")
            got = sorted(f.rsplit(".", 1)[0][len(stem) + 1:] for f in os.listdir(sub))
            check(got == sorted(files), f"{preset}: {got}")
        bpy.data.images.remove(ao)
        sub = os.path.join(folder, "noao")
        _export(sub, 'PBR')
        check(not any(f.endswith("_AO.png") for f in os.listdir(sub)), "no AO file without AO")
        # Optional channels add their files.
        for ch in ('ALPHA', 'EMISSION', 'BUMP'):
            _state(mat, ch).enabled = True
        sub = os.path.join(folder, "all")
        _export(sub, 'PBR')
        got = sorted(f.rsplit(".", 1)[0][len(stem) + 1:] for f in os.listdir(sub))
        check(got == ["Color", "Emission", "Height", "Metallic", "Normal", "Opacity",
                      "Roughness"], f"PBR with every channel: {got}")
        leftovers = [i.name for i in bpy.data.images if i.name.startswith("__essence_")]
        check(not leftovers, f"temp images left behind: {leftovers}")
        check(not any(s.name.startswith("__essence") for s in bpy.data.scenes), "temp scene left")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_png_values_depths_and_color_modes(addon):
    ob, mat, node = _setup()
    stem = bpy.path.clean_name(mat.name)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        _export(folder, 'PBR', depth='8')
        color = os.path.join(folder, f"{stem}_Color.png")
        rough = os.path.join(folder, f"{stem}_Roughness.png")
        check(_png_header(color) == (8, 2), f"Color: 8-bit RGB {_png_header(color)}")
        check(_png_header(rough) == (8, 0), f"Roughness: 8-bit grey {_png_header(rough)}")
        c = _load(color)[256, 256]
        for got, lin, n in zip(c[:3], (0.2, 0.4, 0.6), "rgb"):
            close(got, round(_srgb(lin) * 255) / 255, 0.003, f"Color {n} is sRGB encoded")
        close(_load(rough)[256, 256, 0], 0.3, 0.003, "Roughness raw")
        _export(folder, 'PBR', depth='16')
        check(_png_header(color) == (16, 2), f"16-bit Color {_png_header(color)}")
        close(_load(rough)[256, 256, 0], 0.3, 0.0002, "Roughness at 16 bits")
        # Alpha goes into the base map only when the channel is on.
        _export(folder, 'UNITY_HDRP')
        base = os.path.join(folder, f"{stem}_BaseMap.png")
        check(_png_header(base)[1] == 2, "BaseMap RGB without Opacity")
        _state(mat, 'ALPHA').enabled = True
        node = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
        node.inputs["Alpha B"].default_value = 0.4
        _export(folder, 'UNITY_HDRP')
        check(_png_header(base)[1] == 6, "BaseMap RGBA with Opacity")
        close(_load(base)[256, 256, 3], 0.4, 0.003, "opacity in the alpha")
        m = _load(os.path.join(folder, f"{stem}_MaskMap.png"))[256, 256]
        for got, want, n in zip(m, (0.8, 1.0, 1.0, 0.7), ("metal", "AO", "detail", "smooth")):
            close(got, want, 0.003, f"MaskMap {n}")
        _export(folder, 'UNITY_HDRP', depth='16')
        m = _load(os.path.join(folder, f"{stem}_MaskMap.png"))[256, 256]
        for got, want, n in zip(m, (0.8, 1.0, 1.0, 0.7), ("metal", "AO", "detail", "smooth")):
            close(got, want, 0.0002, f"16-bit MaskMap {n} (alpha does not touch the color)")
        node.inputs["Roughness B"].default_value = 1.0
        _export(folder, 'UNITY_HDRP', depth='16')
        m = _load(os.path.join(folder, f"{stem}_MaskMap.png"))[256, 256]
        close(m[0], 0.8, 0.0002, "metal kept where smoothness is 0")
        node.inputs["Roughness B"].default_value = 0.3
        # Formats without the depth use the nearest they have.
        check(addon.export.file_depth('TARGA', '16') == '8', "TGA is 8-bit")
        check(addon.export.file_depth('OPEN_EXR', '8') == '16', "EXR is at least half")
        check(addon.export.file_depth('PNG', '32') == '16', "PNG tops out at 16")
        for fmt, ext in (('TARGA', 'tga'), ('JPEG', 'jpg'), ('TIFF', 'tif')):
            _export(folder, 'UNREAL', fmt=fmt, depth='16')
            p = os.path.join(folder, f"{stem}_OcclusionRoughnessMetallic.{ext}")
            orm = _load(p)[256, 256]
            tol = 0.02 if fmt == 'JPEG' else 0.003
            for got, want, n in zip(orm[:3], (1.0, 0.3, 0.8), "ORM"):
                close(got, want, tol, f"{fmt} ORM {n}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_exr_is_linear(addon):
    ob, mat, node = _setup()
    stem = bpy.path.clean_name(mat.name)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        _export(folder, 'GLTF', fmt='OPEN_EXR', depth='32')
        c = _load(os.path.join(folder, f"{stem}_BaseColor.exr"))[256, 256]
        for got, want, n in zip(c[:3], (0.2, 0.4, 0.6), "rgb"):
            close(got, want, 0.001, f"EXR color {n} stays linear")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_directx_normal_flips_green(addon):
    exp = addon.export
    baker = exp.Baker(bpy.context, None, 4, 'INFINITE')
    img = bpy.data.images.new("__essence_normal", 4, 4, float_buffer=True)
    img.pixels.foreach_set(np.tile(np.array([0.6, 0.3, 0.9, 1.0], np.float32), 16))
    baker.images['NORMAL'] = img
    try:
        gl, dx = baker.source('NORMAL_GL'), baker.source('NORMAL_DX')
        close(float(gl[0, 1]), 0.3, 1e-6, "OpenGL green as baked")
        close(float(dx[0, 1]), 0.7, 1e-6, "DirectX green flipped")
        close(float(dx[0, 0]), 0.6, 1e-6, "red unchanged")
        check(any(f.rgb == 'NORMAL_DX' for f in exp.BY_PRESET['UNREAL'].files), "Unreal is DirectX")
        check(all(f.rgb != 'NORMAL_DX' for p in exp.PRESETS if p.id != 'UNREAL'
                  for f in p.files), "the others are OpenGL")
    finally:
        baker.remove()


def _half_uvs(ob):
    """Squeeze the UVs into the lower left quarter: the rest of the texture is empty."""
    for loop in ob.data.uv_layers.active.data:
        loop.uv = (loop.uv[0] * 0.5, loop.uv[1] * 0.5)


def test_dilation(addon):
    ob, mat, node = _setup(color=(1.0, 0.0, 0.0, 1.0))
    _half_uvs(ob)
    stem = bpy.path.clean_name(mat.name)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        _export(folder, 'PBR', dilation='INFINITE')
        px = _load(os.path.join(folder, f"{stem}_Color.png"))
        close(px[-2, -2, 0], 1.0, 0.01, "infinite: the far corner has the island's color")
        close(float((px[..., 0] > 0.99).mean()), 1.0, 0.001, "every texel is filled")
        _export(folder, 'PBR', dilation='PIXELS', padding=4)
        px = _load(os.path.join(folder, f"{stem}_Color.png"))
        close(px[-2, -2, 0], 0.0, 0.01, "4 pixels: the far corner stays black")
        close(px[256 + 2, 100, 0], 1.0, 0.01, "2 pixels beyond the island is filled")
        close(px[256 + 12, 100, 0], 0.0, 0.01, "12 pixels beyond is not")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_baked_material(addon):
    ob, mat, node = _setup()
    bpy.context.scene.essence_export_size = '512'
    check(bpy.ops.essence.bake_material() == {'FINISHED'}, "bake material failed")
    flat = bpy.data.materials.get(f"{mat.name} Baked")
    check(flat is not None and flat.use_fake_user, "a kept material")
    check(ob.active_material == mat and all(s.material != flat for s in ob.material_slots),
          "not assigned: the Essence material stays")
    bsdf = next(n for n in flat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    for socket in ("Base Color", "Roughness", "Metallic", "Normal"):
        check(bsdf.inputs[socket].is_linked, f"{socket} is wired")
    color = bsdf.inputs["Base Color"].links[0].from_node.image
    check(color.packed_file is not None and color.colorspace_settings.name == 'sRGB',
          "color: packed, sRGB")
    w = color.size[0]
    i = ((w // 2) * w + w // 2) * 4
    close(color.pixels[i], round(_srgb(0.2) * 255) / 255, 0.003, "the color's value")
    rough = bsdf.inputs["Roughness"].links[0].from_node.image
    check(rough.colorspace_settings.name == 'Non-Color', "roughness is data")
    close(rough.pixels[i], 0.3, 0.003, "the roughness value")
    # Baking again updates it in place: no second material, no stray images.
    n_images = len(bpy.data.images)
    ob.material_slots[0].material = flat         # the user assigned it meanwhile
    ob.material_slots[0].material = mat
    bpy.ops.essence.bake_material()
    check(len([m for m in bpy.data.materials if m.name.startswith(f"{mat.name} Baked")]) == 1,
          "one baked material")
    check(len(bpy.data.images) == n_images, f"no extra images: {len(bpy.data.images)} vs {n_images}")


def test_overwrite(addon):
    from test_ui import _draw
    from essence import ui
    ob, mat, node = _setup()
    stem = bpy.path.clean_name(mat.name)
    folder = tempfile.mkdtemp(prefix="essence_export_")
    try:
        check(_export(folder, 'UNITY_HDRP', overwrite=False) == {'FINISHED'},
              "a first export needs no Overwrite")
        rough = os.path.join(folder, f"{stem}_MaskMap.png")
        stamp = os.path.getmtime(rough)
        node.inputs["Metallic B"].default_value = 0.1
        log = _draw(ui._export, ob)
        check(any(c[0] == "label" and c[2].get("icon") == 'ERROR' and "(exists)" in
                  c[2].get("text", "") for c in log), "the panel marks the files that exist")
        check(any(c[0] == "prop" and c[1][1] == "essence_export_overwrite" for c in log),
              "the Overwrite checkbox")
        try:
            res = _export(folder, 'UNITY_HDRP', overwrite=False)
        except RuntimeError as e:           # background mode raises on an ERROR report
            res = str(e)
        check(res != {'FINISHED'} and "Overwrite" in str(res), f"refused: {res}")
        check(os.path.getmtime(rough) == stamp, "the file is untouched")
        close(_load(rough)[256, 256, 0], 0.8, 0.003, "still the old metal")
        check(_export(folder, 'UNITY_HDRP', overwrite=True) == {'FINISHED'}, "overwrite")
        close(_load(rough)[256, 256, 0], 0.1, 0.003, "replaced: the new metal")
        log = _draw(ui._export, ob)
        check(not any(c[0] == "label" and c[2].get("icon") == 'ERROR' for c in log),
              "with Overwrite on, existing files are no warning")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_export_panel_draws(addon):
    from test_ui import _draw
    from essence import ui
    ob, mat, node = _setup()
    bpy.context.scene.essence_export_preset = 'UNREAL'
    bpy.context.scene.essence_export_format = 'TARGA'
    bpy.context.scene.essence_export_depth = '16'
    log = _draw(ui._export, ob)
    texts = [c[2].get("text", "") for c in log if c[0] == "label"]
    check(any(t.endswith("_OcclusionRoughnessMetallic") for t in texts), f"lists the files {texts}")
    check(any("8 bits" in t for t in texts), "says TGA is written at 8 bits")
    ops_ = [c[1][0] for c in log if c[0] == "operator"]
    check("essence.export" in ops_ and "essence.bake_material" in ops_, f"buttons {ops_}")
