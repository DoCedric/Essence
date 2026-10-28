"""Where the panels live: the stack in the N sidebar, the texture set's settings in the
Properties editor's Material tab."""

from types import SimpleNamespace

import bpy

from harness import check, make_plane


def test_panel_placement(addon):
    for key in ("layers", "props", "mask", "adjustment"):
        for tag, space in (("view3d", 'VIEW_3D'), ("image", 'IMAGE_EDITOR')):
            cls = getattr(bpy.types, f"ESSENCE_PT_{key}_{tag}", None)
            check(cls is not None and cls.bl_space_type == space and cls.bl_region_type == 'UI'
                  and cls.bl_category == "Essence", f"{key} in the {space} sidebar")
    for key, label in (("maps", "Essence Mesh Maps"), ("material", "Essence Channels"),
                       ("bake", "Essence Bake"), ("export", "Essence Export")):
        cls = getattr(bpy.types, f"ESSENCE_PT_{key}_material", None)
        check(cls is not None and cls.bl_space_type == 'PROPERTIES'
              and cls.bl_region_type == 'WINDOW' and cls.bl_context == "material"
              and cls.bl_label == label, f"{label} in the Material tab")
        for tag in ("view3d", "image"):
            check(not hasattr(bpy.types, f"ESSENCE_PT_{key}_{tag}"),
                  f"{label} is no longer in a sidebar")
    poll = bpy.types.ESSENCE_PT_bake_material.poll
    check(not poll(SimpleNamespace(active_object=None)), "no object: hidden")
    ob = make_plane()
    check(not poll(SimpleNamespace(active_object=ob)), "no material: hidden")
    bpy.ops.essence.add_layer()
    check(poll(SimpleNamespace(active_object=ob)), "a material: shown")
