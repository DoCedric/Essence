"""Where the panels live: the stack in the N sidebar, the texture set's settings in the
Properties editor's Material tab."""

from types import SimpleNamespace

import bpy

from harness import check, make_plane


def test_panel_placement(addon):
    for key in ("props", "mask", "adjustment"):
        for tag, space in (("view3d", 'VIEW_3D'), ("image", 'IMAGE_EDITOR')):
            cls = getattr(bpy.types, f"ESSENCE_PT_{key}_{tag}", None)
            check(cls is not None and cls.bl_space_type == space and cls.bl_region_type == 'UI'
                  and cls.bl_category == "Essence", f"{key} in the {space} sidebar")
    for key, label in (("layers", "Essence Layers"), ("maps", "Essence Mesh Maps"),
                       ("material", "Essence Channels"),
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
    names = [c.__name__ for c in addon.ui.classes if c.bl_space_type == 'PROPERTIES']
    check(names[0] == "ESSENCE_PT_layers_material", f"Layers first in the Material tab: {names}")
    layers_poll = bpy.types.ESSENCE_PT_layers_material.poll
    plain = make_plane("Plain")
    check(layers_poll(SimpleNamespace(active_object=plain)),
          "Layers shows on a mesh without a material (Add Layer makes one)")


def test_view_controls_above_the_stack(addon):
    from test_channels import _state
    from test_ui import Rec
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    for ch in ('ALPHA', 'SPECULAR', 'SPECULAR_TINT', 'EMISSION', 'AO'):
        _state(mat, ch).enabled = True
    shading = bpy.context.scene.display.shading      # a real View3DShading to draw from

    def draw(kind):
        shading.type = kind
        area = SimpleNamespace(type='VIEW_3D', spaces=SimpleNamespace(
            active=SimpleNamespace(shading=shading)))
        ctx = SimpleNamespace(active_object=ob, scene=bpy.context.scene,
                              view_layer=bpy.context.view_layer,
                              screen=SimpleNamespace(areas=[area]))
        log = []
        addon.ui._stack(SimpleNamespace(layout=Rec(log)), ctx)
        return log

    log = draw('MATERIAL')
    rot = [c for c in log if c[0] == "prop" and c[1][1] == "studiolight_rotate_z"]
    check(len(rot) == 1 and rot[0][2].get("text") == "World Rotation",
          "the HDRI rotation above the stack in Material Preview")
    first = next(i for i, c in enumerate(log) if c[0] == "operator")
    check(log.index(rot[0]) < first, "before the stack's buttons")
    texts = [c[2].get("text") for c in log if c[0] == "operator"
             and c[1][0] == "essence.preview" and not c[2].get("icon")]
    check(texts == ["Col", "Rgh", "Met", "Bmp", "AO", "Alp", "Spec", "SpCo", "Emi"],
          f"five per row, the common ones first: {texts}")
    check(sum(1 for c in log if c[0] == "label" and c[2].get("text") == "") >= 1,
          "the short second row is padded to the first row's widths")
    check(not any(c[0] == "prop" and c[1][1] == "studiolight_rotate_z" for c in draw('SOLID')),
          "no rotation in Solid shading (no HDRI)")
