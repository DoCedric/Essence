"""Smart materials: a layer or folder saved as a material asset in the Essence asset library,
and added back into any stack. The library is an ordinary Blender asset library named Essence
(Preferences > File Paths > Asset Libraries), with a Smart Materials folder and catalog."""

import os
import re

import bpy
from bpy.props import BoolProperty, StringProperty

from . import clipboard

LIBRARY_NAME = "Essence"
CATALOG = "Smart Materials"
CATALOG_ID = "5e0c7a3e-6d1b-4f0a-9c2e-8b4d7f1a2c36"   # fixed, so every saved file agrees
CATS_FILE = "blender_assets.cats.txt"
KEY = "essence_smart"     # on the asset material: the uid of the saved layer or folder
VERSION_KEY = "essence_version"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Blender", "Essence")


def library():
    """The Essence entry of the preferences' asset libraries, or None."""
    libs = bpy.context.preferences.filepaths.asset_libraries
    return next((lib for lib in libs if lib.name == LIBRARY_NAME), None)


def library_dir():
    """The library's folder: where the user pointed it in the preferences, else the default."""
    lib = library()
    return bpy.path.abspath(lib.path) if lib is not None and lib.path else DEFAULT_DIR


def folder():
    return os.path.join(library_dir(), CATALOG)


def ensure_library():
    """The Essence asset library in the preferences (saved with them when Blender saves its
    preferences), its Smart Materials folder and catalog. Returns the catalog id."""
    prefs = bpy.context.preferences
    if library() is None:
        prefs.filepaths.asset_libraries.new(name=LIBRARY_NAME, directory=DEFAULT_DIR)
        prefs.is_dirty = True
    root = library_dir()
    os.makedirs(os.path.join(root, CATALOG), exist_ok=True)
    return _ensure_catalog(root)


def _ensure_catalog(root):
    """The id of the Smart Materials catalog in the library's catalog file; added when it is
    not there (a catalog the user made with that path keeps its id)."""
    path = os.path.join(root, CATS_FILE)
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    for line in lines:
        if line and not line.startswith(("#", "VERSION")) and line.count(":") >= 2:
            cid, cpath, _ = line.split(":", 2)
            if cpath == CATALOG:
                return cid
    if not any(line.startswith("VERSION") for line in lines):
        lines = ["# This is an Asset Catalog Definition file for Blender.",
                 "#",
                 "# Empty lines and lines starting with `#` will be ignored.",
                 "# The first non-ignored line should be the version indicator.",
                 '# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"',
                 "",
                 "VERSION 1",
                 ""] + lines
    lines.append(f"{CATALOG_ID}:{CATALOG}:{CATALOG}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return CATALOG_ID


def file_path(name):
    """The file of smart material `name`: one .blend per smart material, named after it
    (characters a file name cannot hold become _)."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "Smart Material"
    return os.path.join(folder(), clean + ".blend")


def saved():
    """The names of the saved smart materials, from the files in the library folder."""
    try:
        files = os.listdir(folder())
    except OSError:
        return []
    return sorted((f[:-6] for f in files if f.lower().endswith(".blend")), key=str.lower)


def _ids():
    return {id.session_uid for coll in (bpy.data.materials, bpy.data.images,
                                        bpy.data.node_groups) for id in coll}


def _remove_unused(candidates):
    """Remove the materials among `candidates` (session uids) and then their images and node
    groups that nothing uses any more (a fake user does not count)."""
    for m in [m for m in bpy.data.materials if m.session_uid in candidates]:
        bpy.data.materials.remove(m)
    removed = True
    while removed:
        removed = False
        for coll in (bpy.data.node_groups, bpy.data.images):
            for id in list(coll):
                if id.session_uid in candidates and id.users - int(id.use_fake_user) <= 0:
                    coll.remove(id)
                    removed = True


def save(mat, uid, name, overwrite=False):
    """Save entry `uid` of `mat` (a layer or folder with what it owns) as smart material `name`:
    a material asset whose stack holds a copy of it, in its own file in the library. Nothing is
    left behind in the open file. Returns the file's path."""
    catalog = ensure_library()
    path = file_path(name)
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(path)
    before = _ids()
    # The asset is named `name`; a material of the open file with that name steps aside.
    other = bpy.data.materials.get(name)
    other_name = other.name if other is not None else None
    tmp = bpy.data.materials.new(name)
    try:
        if other is not None:
            other.name = f"{name} (saving)"
            tmp.name = name
        if "channels" in mat.essence.keys():     # the channel set comes along
            tmp.essence["channels"] = clipboard._plain(mat.essence["channels"])
        tmp[KEY] = clipboard.copy_entry(mat, uid, tmp, "")
        tmp.asset_mark()
        tmp.asset_data.catalog_id = catalog
        tmp.asset_data.description = "Essence smart material: Add > Smart Material in the stack"
        tmp.asset_data.tags.new("Essence", skip_if_exists=True)
        bpy.data.libraries.write(path, {tmp}, path_remap='ABSOLUTE', fake_user=True,
                                 compress=True)
    finally:
        _remove_unused(_ids() - before)
        if other is not None:
            other.name = other_name
    return path


def _merge_shared(new):
    """Appended copies of the addon's shared node groups and bundled masks (`Essence Layer.001`)
    are swapped for the ones already in the file."""
    for coll, key in ((bpy.data.node_groups, VERSION_KEY), (bpy.data.images, "essence_bundled")):
        for id in [x for x in coll if x.session_uid in new and x.get(key) is not None]:
            local = next((x for x in coll if x.session_uid not in new and x.get(key) is not None
                          and (x.name == re.sub(r"\.\d{3}$", "", id.name) if key == VERSION_KEY
                               else x.get(key) == id.get(key))), None)
            if local is not None:
                id.user_remap(local)


def add(path, dst_mat, parent="", anchor=None):
    """Copy the smart material in `path` into `dst_mat` (see `clipboard.copy_entry`). Returns
    the new entry's uid. What was appended to read it is removed again."""
    before = _ids()
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.materials = list(data_from.materials)
    new = _ids() - before
    try:
        src = next((m for m in data_to.materials if m is not None and m.get(KEY)), None)
        if src is None or not any(e.uid == src[KEY] for e in src.essence.layers):
            raise ValueError(f"{os.path.basename(path)} holds no Essence smart material")
        _merge_shared(new)
        return clipboard.copy_entry(src, src[KEY], dst_mat, parent, anchor)
    finally:
        _remove_unused(new)


def _mat(context):
    ob = context.active_object
    return ob.active_material if ob is not None and ob.type == 'MESH' else None


class ESSENCE_OT_save_smart_material(bpy.types.Operator):
    bl_idname = "essence.save_smart_material"
    bl_label = "Save as Smart Material"
    bl_description = ("Save this layer or folder with everything it owns as a smart material in "
                      "the Essence asset library, to add it to any stack from Add > Smart "
                      "Material")
    bl_options = {'REGISTER'}

    uid: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(name="Name")
    overwrite: BoolProperty(name="Replace the saved one", default=False,
                            options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        mat = _mat(context)
        return clipboard._owner(mat, clipboard._target(mat, "")) is not None if mat else False

    def _entry(self, context):
        mat = _mat(context)
        return mat, clipboard._owner(mat, clipboard._target(mat, self.uid))

    def invoke(self, context, event):
        mat, e = self._entry(context)
        if e is None:
            return {'CANCELLED'}
        self.uid, self.name = e.uid, e.name
        return context.window_manager.invoke_props_dialog(self, width=360,
                                                          confirm_text="Save")

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.activate_init = True
        row.prop(self, "name")
        if self.name.strip() and os.path.exists(file_path(self.name.strip())):
            layout.label(text="A smart material with this name exists", icon='ERROR')
            layout.prop(self, "overwrite")
        layout.label(text=f"In {folder()}")

    def execute(self, context):
        mat, e = self._entry(context)
        name = self.name.strip() or (e.name if e is not None else "")
        if e is None or not name:
            return {'CANCELLED'}
        try:
            path = save(mat, e.uid, name, self.overwrite)
        except FileExistsError:
            self.report({'ERROR'}, f"{name} exists; tick Replace or pick another name")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved {name} to {path}")
        return {'FINISHED'}


class ESSENCE_OT_add_smart_material(bpy.types.Operator):
    bl_idname = "essence.add_smart_material"
    bl_label = "Add Smart Material"
    bl_description = ("Add this smart material above the selected layer, or inside the "
                      "selected folder")
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        path = file_path(self.name)
        if not os.path.exists(path):
            self.report({'ERROR'}, f"No smart material {self.name} in {folder()}")
            return {'CANCELLED'}
        mat, parent, anchor = clipboard.paste_target(context)
        try:
            new = add(path, mat, parent, anchor)
        except (OSError, ValueError) as err:
            self.report({'ERROR'}, str(err))
            return {'CANCELLED'}
        s = mat.essence
        s.active_index = next(i for i, x in enumerate(s.layers) if x.uid == new)
        return {'FINISHED'}


class ESSENCE_MT_add_smart(bpy.types.Menu):
    bl_idname = "ESSENCE_MT_add_smart"
    bl_label = "Smart Material"

    def draw(self, context):
        layout = self.layout
        names = saved()
        for name in names:
            layout.operator("essence.add_smart_material", text=name,
                            icon='MATERIAL').name = name
        if not names:
            layout.label(text="None saved yet")
        layout.separator()
        layout.operator_context = 'INVOKE_DEFAULT'
        layout.operator("essence.save_smart_material", text="Save Selected as Smart Material",
                        icon='ASSET_MANAGER')
        layout.operator("wm.path_open", text="Open Library Folder",
                        icon='FILE_FOLDER').filepath = folder()


classes = (ESSENCE_OT_save_smart_material, ESSENCE_OT_add_smart_material, ESSENCE_MT_add_smart)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if not bpy.app.background:     # tests and scripts never touch the user's preferences or disk
        try:
            ensure_library()
        except Exception as err:  # noqa: BLE001
            print(f"Essence: could not set up the asset library: {err}")


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
