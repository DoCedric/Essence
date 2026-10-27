"""Smart materials: the Essence asset library, saving a layer or folder and adding it back."""

import os
import shutil
import tempfile
from types import SimpleNamespace

import bpy

from harness import check, close, make_plane
from test_blend import _set
from test_clipboard import _children, _entry, _node, _rich_layer, _select, _top_value


def _library(addon):
    """A fresh library folder in temp, and no Essence entry in the preferences yet."""
    libs = bpy.context.preferences.filepaths.asset_libraries
    for lib in [x for x in libs if x.name == addon.smart.LIBRARY_NAME]:
        libs.remove(lib)
    root = tempfile.mkdtemp(prefix="essence_lib_")
    addon.smart.DEFAULT_DIR = root
    return root


def _counts():
    return (len(bpy.data.materials), len(bpy.data.images), len(bpy.data.node_groups))


def test_library_and_catalog(addon):
    smart = addon.smart
    root = _library(addon)
    try:
        check(smart.saved() == [], "nothing saved, and listing does not create the folder")
        check(not os.path.exists(smart.folder()), "no folder made by listing")
        cid = smart.ensure_library()
        lib = smart.library()
        check(lib is not None and os.path.normcase(bpy.path.abspath(lib.path))
              == os.path.normcase(root), f"the Essence library points at {lib and lib.path}")
        check(os.path.isdir(os.path.join(root, "Smart Materials")), "the Smart Materials folder")
        check(lib.import_method == 'APPEND', f"dropped assets are appended: {lib.import_method}")
        cats = open(os.path.join(root, smart.CATS_FILE), encoding="utf-8").read()
        check("VERSION 1" in cats and f"{cid}:Smart Materials:Smart Materials" in cats, cats)
        smart.ensure_library()
        libs = bpy.context.preferences.filepaths.asset_libraries
        check(sum(1 for x in libs if x.name == "Essence") == 1, "one library entry")
        check(open(os.path.join(root, smart.CATS_FILE), encoding="utf-8").read().count(
            "Smart Materials:") == 1, "the catalog once")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    # A catalog file the user made already, with a Smart Materials catalog of its own.
    root = _library(addon)
    try:
        with open(os.path.join(root, smart.CATS_FILE), "w", encoding="utf-8") as f:
            f.write("VERSION 1\n\naaaa-bbbb:Grunge:Grunge\ncccc-dddd:Smart Materials:Mine\n")
        check(smart.ensure_library() == "cccc-dddd", "the user's catalog keeps its id")
        cats = open(os.path.join(root, smart.CATS_FILE), encoding="utf-8").read()
        check("Grunge" in cats and cats.count("Smart Materials") == 1, cats)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_writes_an_asset_and_leaves_nothing_behind(addon):
    smart = addon.smart
    root = _library(addon)
    try:
        ob, mat = _rich_layer()
        other = bpy.data.materials.new("Rust")          # a material named like the asset
        before = _counts()
        names = sorted(m.name for m in bpy.data.materials)
        path = smart.save(mat, _entry(mat, "Rust").uid, "Rust")
        check(os.path.exists(path) and path.endswith(os.path.join("Smart Materials",
                                                                  "Rust.blend")), path)
        check(_counts() == before, f"nothing left in the open file: {before} -> {_counts()}")
        check(sorted(m.name for m in bpy.data.materials) == names and other.name == "Rust",
              "the open file's material names are as they were")
        check(smart.saved() == ["Rust"], f"listed: {smart.saved()}")
        # In the file: a material asset in the catalog, holding the layer.
        with bpy.data.libraries.load(path) as (data_from, data_to):
            check(data_from.materials == ["Rust"], f"materials {data_from.materials}")
        with bpy.data.libraries.load(path, link=True, assets_only=True) as (data_from, data_to):
            check(data_from.materials == ["Rust"], "marked as an asset")
            data_to.materials = ["Rust"]
        linked = data_to.materials[0]
        check(linked.asset_data is not None
              and linked.asset_data.catalog_id == smart.CATALOG_ID, "in the catalog")
        check("Essence" in [t.name for t in linked.asset_data.tags], "tagged Essence")
        root_e = next(e for e in linked.essence.layers if e.uid == linked[smart.KEY])
        check(root_e.name == "Rust" and root_e.kind == 'LAYER', "the saved layer")
        check(len(linked.essence.layers) == len(mat.essence.layers) - 1,
              "the layer with its mask and items, not the base layer")
        bpy.data.materials.remove(linked)
        bpy.data.libraries.remove(bpy.data.libraries[-1])
        # Saving again under that name is refused unless replacing.
        try:
            smart.save(mat, _entry(mat, "Rust").uid, "Rust")
            check(False, "an existing smart material was overwritten")
        except FileExistsError:
            pass
        smart.save(mat, _entry(mat, "Rust").uid, "Rust", overwrite=True)
        check(smart.file_path('a/b:c?').endswith("a_b_c_.blend"), "file name cleaned")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_add_smart_material_to_another_object(addon):
    smart = addon.smart
    root = _library(addon)
    try:
        ob, mat = _rich_layer()
        want = _top_value(ob, mat)
        smart.save(mat, _entry(mat, "Rust").uid, "Rusty")
        kids = _children(mat, _entry(mat, "Rust").uid)
        other = make_plane("Other", location=(5, 0, 0))
        bpy.context.view_layer.objects.active = other
        bpy.ops.essence.add_layer()
        mat2 = other.active_material
        base = mat2.essence.layers[mat2.essence.active_index]
        _set(_node(mat2, base), (1, 0, 0, 1), 0.2, 0.0, 0.0)
        groups = {g.name for g in bpy.data.node_groups}
        mats = len(bpy.data.materials)
        check(bpy.ops.essence.add_smart_material(name="Rusty") == {'FINISHED'}, "add failed")
        added = mat2.essence.layers[mat2.essence.active_index]
        check(added.name == "Rust" and added.kind == 'LAYER', f"added {added.name}")
        check(_children(mat2, added.uid) == kids, "its mask and items")
        order = [e.name for e in mat2.essence.layers if e.kind == 'LAYER']
        check(order[-1] == "Rust", f"above the selected layer: {order}")
        close(_top_value(other, mat2)[0], want[0], 0.01, "looks the same as where it was saved")
        check(len(bpy.data.materials) == mats, "the appended material is gone again")
        extra = {g.name for g in bpy.data.node_groups} - groups
        check(not extra, f"no duplicated shared groups: {sorted(extra)}")
        m2 = next(e for e in mat2.essence.layers if e.kind == 'MASK')
        img = mat2.node_tree.nodes[m2.mask_node_name].image
        check(img is not None and img.packed_file is not None, "its own packed mask image")
        close(img.pixels[0], 0.25, 1e-3, "with the saved pixels")
        check(img.users >= 1, "used by the new mask")
        # A second add gets its own image, and into a selected folder it goes inside.
        bpy.ops.essence.add_folder()
        folder = mat2.essence.layers[mat2.essence.active_index].uid
        bpy.ops.essence.add_smart_material(name="Rusty")
        again = mat2.essence.layers[mat2.essence.active_index]
        check(again.parent == folder, "inside the selected folder")
        m3 = next(e for e in mat2.essence.layers if e.kind == 'MASK' and e.parent == again.uid)
        check(mat2.node_tree.nodes[m3.mask_node_name].image is not img, "its own image")
        try:
            bpy.ops.essence.add_smart_material(name="Missing")
            check(False, "a missing smart material was added")
        except RuntimeError as err:
            check("No smart material" in str(err), str(err))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_a_folder_and_into_an_object_without_material(addon):
    smart = addon.smart
    root = _library(addon)
    try:
        ob = make_plane()
        bpy.ops.essence.add_folder()
        mat = ob.active_material
        _entry(mat, "Folder 1").name = "Kit"
        bpy.ops.essence.add_layer()                        # inside Kit
        bpy.ops.essence.add_mask(source='PAINT', fill='BLACK', size=16)
        smart.save(mat, _entry(mat, "Kit").uid, "Kit")
        other = make_plane("Bare", location=(5, 0, 0))
        bpy.context.view_layer.objects.active = other
        check(bpy.ops.essence.add_smart_material(name="Kit") == {'FINISHED'}, "add failed")
        mat2 = other.active_material
        check(mat2 is not None, "a material made for the object")
        kit = _entry(mat2, "Kit")
        check(kit.kind == 'FOLDER', "the folder")
        inside = [e for e in mat2.essence.layers if e.parent == kit.uid]
        check([e.kind for e in inside] == ['LAYER'], f"its layer: {[e.kind for e in inside]}")
        check(any(e.kind == 'MASK' and e.parent == inside[0].uid for e in mat2.essence.layers),
              "the layer's mask")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_smart_menus_draw(addon):
    from test_ui import Rec, _ctx
    from essence import ops
    smart = addon.smart
    root = _library(addon)
    try:
        ob, mat = _rich_layer()

        def draw(menu):
            log = []
            menu.draw(SimpleNamespace(layout=Rec(log)), _ctx(ob))
            return log

        log = draw(ops.ESSENCE_MT_add)
        check(any(c[0] == "menu" and c[1][0] == "ESSENCE_MT_add_smart" for c in log),
              "Add has Smart Material")
        log = draw(smart.ESSENCE_MT_add_smart)
        check(any(c[0] == "label" and "None saved" in c[2].get("text", "") for c in log),
              "empty library")
        smart.save(mat, _entry(mat, "Rust").uid, "Rusty")
        log = draw(smart.ESSENCE_MT_add_smart)
        items = [c[2].get("text") for c in log if c[0] == "operator"
                 and c[1][0] == "essence.add_smart_material"]
        check(items == ["Rusty"], f"listed {items}")
        log = []
        addon.clipboard.draw_entry_menu(Rec(log), mat, _entry(mat, "Rust"))
        check(any(c[0] == "operator" and c[1][0] == "essence.save_smart_material" for c in log),
              "the row menu can save it")
        _select(mat, "Rust")
        check(bpy.ops.essence.save_smart_material.poll(), "save is available on a layer")
    finally:
        shutil.rmtree(root, ignore_errors=True)
