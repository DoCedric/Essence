"""Keep what is painted on a mask or a paint item when the file is saved.

An image that is packed in the file keeps the pixels it had when it was packed: painting on it
changes the buffer, but a save does not pack that buffer again, and a generated blank (what
older versions of Essence made) is written as a blank again. So just before a save, the images
Essence paints on that were changed since they were packed are packed again. It runs when the
user saves and nothing else.
"""

import bpy
from bpy.app.handlers import persistent

PAINT_KEY = "essence_paint"     # set on the images Essence creates to be painted on


def paint_images():
    """The images Essence has as paint targets: the ones it made, and the image of every mask
    and paint item (a file from an older version has generated blanks there)."""
    images = {i for i in bpy.data.images if i.get(PAINT_KEY)}
    for mat in bpy.data.materials:
        if mat.node_tree is None or not hasattr(mat, "essence"):
            continue
        for e in mat.essence.layers:
            if e.kind in ('MASK', 'FILL') and e.mask_node_name:
                node = mat.node_tree.nodes.get(e.mask_node_name)
                img = getattr(node, "image", None)
                if img is not None:
                    images.add(img)
    return images


def repack_painted():
    """Pack again the paint images that were changed. Returns how many were."""
    done = 0
    for img in paint_images():
        if img.is_dirty and (img.packed_file is not None or img.source == 'GENERATED'):
            try:
                img.pack()
                done += 1
            except RuntimeError:
                pass
    return done


@persistent
def _on_save_pre(*_args):
    try:
        repack_painted()
    except Exception as e:  # noqa: BLE001 - never get in the way of a save
        print(f"Essence: could not pack the painted images: {e}")


def register():
    if _on_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_on_save_pre)


def unregister():
    if _on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save_pre)
