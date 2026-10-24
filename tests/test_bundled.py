"""The premade masks in assets/masks load into the file once."""

import os
import shutil
import tempfile
import time

import bpy

from harness import check


def _bundled():
    return [i for i in bpy.data.images if "essence_bundled" in i]


def test_masks_load_once_and_are_set_up_as_masks(addon):
    b = addon.bundled
    files = b.files()
    check(len(files) > 0, "no masks found in assets/masks")
    check(not _bundled(), "a scratch scene starts without them")
    t = time.perf_counter()
    check(b.load_masks() == len(files), "first load should load every mask")
    took = time.perf_counter() - t
    check(took < 10, f"loading took {took:.1f} s")
    imgs = _bundled()
    check(len(imgs) == len(files), f"{len(imgs)} images for {len(files)} files")
    check(all(i.colorspace_settings.name == 'Non-Color' for i in imgs), "masks are Non-Color")
    check(all(i.use_fake_user for i in imgs), "a mask without a fake user is lost on save")
    check(all(i.packed_file is None for i in imgs), "they should reference the files, not pack")
    check(all(i.name.startswith("Essence Mask ") and not i.name.startswith(".") for i in imgs),
          "names should be readable and not hidden from the pickers")
    check(all(i.size[0] > 0 for i in imgs), "images should have real pixels")
    check(b.load_masks() == 0, "a second call must not load anything again")
    check(len(_bundled()) == len(files), "no duplicates after a second call")


def test_renamed_and_missing_masks(addon):
    b = addon.bundled
    b.load_masks()
    first = _bundled()[0]
    first.name = "my own name"
    check(b.load_masks() == 0, "a renamed mask must not be loaded again")
    check(first.name == "my own name", "the user's name is kept")
    bpy.data.images.remove(first)
    check(b.load_masks() == 1, "a mask that was deleted comes back")
    check(len(_bundled()) == len(b.files()), "and only that one")


def test_a_moved_addon_folder_is_found_again(addon):
    b = addon.bundled
    b.load_masks()
    img = _bundled()[0]
    img.filepath = os.path.join(tempfile.gettempdir(), "gone", "x.jpg")
    b.load_masks()
    check(os.path.normcase(bpy.path.abspath(img.filepath)) ==
          os.path.normcase(os.path.join(b.MASK_DIR, img["essence_bundled"])),
          f"path not repaired: {img.filepath}")
    check(img.size[0] > 0, "the image should have pixels again")


def test_loads_after_the_file_is_replaced(addon):
    """Blender replaces the data with the startup file after addons are enabled, and every
    opened file is a new bpy.data: the load handler has to do it, not register()."""
    b = addon.bundled
    b.AUTOLOAD = True
    check(b._on_load_post in bpy.app.handlers.load_post, "handler not registered")
    bpy.ops.wm.read_homefile(use_empty=True)
    check(len(_bundled()) == len(b.files()), f"after a new file: {len(_bundled())} masks")
    b.AUTOLOAD = False
    bpy.ops.wm.read_homefile(use_empty=True)
    check(not _bundled(), "AUTOLOAD off must load nothing")


def test_save_and_reopen_keeps_one_copy(addon):
    b = addon.bundled
    b.AUTOLOAD = True
    b.load_masks()
    n = len(_bundled())
    folder = tempfile.mkdtemp(prefix="essence_bundled_")
    path = os.path.join(folder, "t.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.ops.wm.open_mainfile(filepath=path)
        check(len(_bundled()) == n, f"reopened file has {len(_bundled())} masks, not {n}")
        check(all(i.use_fake_user for i in _bundled()), "fake users lost in the save")
        check(len([i for i in bpy.data.images if i.name.startswith("Essence Mask ")]) == n,
              "duplicates such as '.001' after reopening")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_handler_is_removed_on_unregister(addon):
    b = addon.bundled
    h = b._on_load_post
    addon.unregister()
    check(h not in bpy.app.handlers.load_post, "handler left behind after unregister")
    addon.register()
    check(addon.bundled._on_load_post in bpy.app.handlers.load_post, "handler back on register")


def test_a_bundled_mask_works_as_a_mask_source(addon):
    from harness import make_plane
    b = addon.bundled
    b.load_masks()
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=32)
    mat = ob.active_material
    mask = mat.essence.layers[mat.essence.active_index]
    mask.source.kind = 'IMAGE'
    node = addon.sources.source_nodes(mat.node_tree, mask.uid, 'MASK')['TEXTURE']
    node.image = _bundled()[0]
    check(node.image.colorspace_settings.name == 'Non-Color', "mask image should be Non-Color")
