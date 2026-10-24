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
_names = ("channels", "adjustments", "generators", "gizmo", "stack", "sources", "nodes", "bake",
          "filters", "curvature", "props", "ops", "export", "bundled", "ui")

# Blender's "Reload Scripts" only re-imports this file, not the submodules, so reload
# them here (this file is re-executed on reload, while `_loaded` still exists).
if "_loaded" in locals():
    for _n in _names:
        # A module added since the last load isn't in `_loaded`; it imports fresh below.
        if _n in _loaded:
            importlib.reload(_loaded[_n])

from . import (channels, adjustments, generators, gizmo, stack, sources, nodes,  # noqa: E402
               bake, filters, curvature, props, ops, export, bundled, ui)

_loaded = {"channels": channels, "adjustments": adjustments, "generators": generators,
           "gizmo": gizmo, "stack": stack,
           "sources": sources, "nodes": nodes, "bake": bake, "filters": filters,
           "curvature": curvature,
           "props": props, "ops": ops, "export": export, "bundled": bundled, "ui": ui}

# Registration order. channels, adjustments, stack, sources and nodes only define data and
# helpers.
_modules = (props, ops, bake, export, bundled, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
