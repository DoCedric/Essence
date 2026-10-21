bl_info = {
    "name": "Essence",
    "author": "Cedric",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "location": "View3D / Image Editor > Sidebar > Essence",
    "description": "Substance Painter-style layered texturing built on Blender's shader graph",
    "category": "Material",
}

import importlib

# Dependency order: each module may import the ones before it.
_names = ("channels", "nodes", "props", "ops", "bake", "export", "ui")

# Blender's "Reload Scripts" only re-imports this file, not the submodules, so reload
# them here (this file is re-executed on reload, while `_loaded` still exists).
if "_loaded" in locals():
    for _n in _names:
        # A module added since the last load isn't in `_loaded`; it imports fresh below.
        if _n in _loaded:
            importlib.reload(_loaded[_n])

from . import channels, nodes, props, ops, bake, export, ui  # noqa: E402

_loaded = {"channels": channels, "nodes": nodes, "props": props,
           "ops": ops, "bake": bake, "export": export, "ui": ui}

# Registration order. channels and nodes only define data and helpers.
_modules = (props, ops, bake, export, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
