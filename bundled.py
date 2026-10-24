"""Premade texture masks that ship with the addon (assets/masks, ambientCG, CC0).

They are loaded into the open file as ordinary image datablocks, so they show up in every
image picker and mask grid and need nothing from Essence to work. Loading is idempotent: an
image remembers the file it came from (`essence_bundled`), so a mask that is already in the
file is never loaded again, even if the user renamed it.

Loaded in three situations, because Blender is awkward about each: after every file load
(`load_post`), when the addon is enabled while a file is open, and when Blender starts with
the addon enabled. In that last case `bpy.data` is blocked while addons register and the
startup file does not trigger `load_post`, so the load is deferred with a one-shot call that
runs once and is not repeated (it is not a timer loop).

Each image gets a fake user so it survives a save, is Non-Color (masks are data) and stays a
reference to the file in the addon folder, not a copy packed into the .blend.
"""

import os

import bpy
from bpy.app.handlers import persistent

MASK_DIR = os.path.join(os.path.dirname(__file__), "assets", "masks")
EXTENSIONS = {".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff"}
KEY = "essence_bundled"     # custom property on the image: the file name it was loaded from
PREFIX = "Essence Mask "

# The test harness switches this off so a scratch scene starts without 30 extra images.
AUTOLOAD = True


def files():
    """File names of the bundled masks, sorted."""
    if not os.path.isdir(MASK_DIR):
        return []
    return sorted(f for f in os.listdir(MASK_DIR)
                  if os.path.splitext(f)[1].lower() in EXTENSIONS)


def display_name(filename):
    """'Asphalt026C_1K-JPG_Roughness.jpg' -> 'Essence Mask Asphalt026C'."""
    return PREFIX + os.path.splitext(filename)[0].split("_")[0]


def load_masks():
    """Load the bundled masks the file does not have yet. Returns how many were loaded."""
    have = {i[KEY]: i for i in bpy.data.images if KEY in i}
    loaded = 0
    for f in files():
        path = os.path.join(MASK_DIR, f)
        img = have.get(f)
        if img is not None:
            # The addon folder moved (an update, another machine): point back at the file.
            if (img.packed_file is None and os.path.exists(path)
                    and os.path.normcase(bpy.path.abspath(img.filepath)) != os.path.normcase(path)):
                img.filepath = path
                img.reload()
            continue
        img = bpy.data.images.load(path)
        img.name = display_name(f)
        img[KEY] = f
        img.colorspace_settings.name = 'Non-Color'
        img.use_fake_user = True
        loaded += 1
    return loaded


def _load_quietly():
    """Never let a problem here get in the way of opening a file or enabling the addon."""
    if not AUTOLOAD:
        return
    try:
        load_masks()
    except Exception as e:  # noqa: BLE001
        print(f"Essence: could not load the bundled masks: {e}")


@persistent
def _on_load_post(*_args):
    _load_quietly()


def _deferred():
    _load_quietly()
    return None         # one shot


def register():
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    try:
        bpy.data.images
    except AttributeError:      # Blender is starting: data is blocked until it has started
        if AUTOLOAD:
            bpy.app.timers.register(_deferred, first_interval=0.5, persistent=True)
        return
    _load_quietly()             # the addon was enabled while a file is open


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_deferred):
        bpy.app.timers.unregister(_deferred)
