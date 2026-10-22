import bpy

from harness import check, make_plane


def test_registers(addon):
    check(hasattr(bpy.types.Material, "essence"), "Material.essence missing")
    check(hasattr(bpy.ops.essence, "add_layer"), "add_layer operator missing")


def test_groups_build(addon):
    nodes = addon.nodes
    for fn, name in ((nodes.ensure_layer_group, nodes.GROUP_NAME),
                     (nodes.ensure_folder_group, nodes.FOLDER_GROUP_NAME),
                     (nodes.ensure_mask_group, nodes.MASK_GROUP_NAME)):
        ng = fn()
        check(ng.name == name, f"{name} not created")
        check(len(ng.interface.items_tree) > 0, f"{name} has no interface")
    ng = nodes.ensure_layer_group()
    names = {s.name for s in ng.interface.items_tree}
    for ch in (c for c in addon.channels.CHANNELS if c.default_enabled):
        for n in (f"{ch.label} A", f"{ch.label} B", f"{ch.label} Enable", ch.label):
            check(n in names, f"layer group missing socket {n}")
    check("Mask" in names, "layer group missing Mask")


def test_add_layer(addon):
    ob = make_plane()
    check(bpy.ops.essence.add_layer() == {'FINISHED'}, "add_layer failed")
    mat = ob.active_material
    check(mat is not None and len(mat.essence.layers) == 1, "layer entry missing")
    tree = mat.node_tree
    bsdf = addon.nodes.find_principled(tree)
    check(bsdf.inputs['Base Color'].is_linked, "Base Color not linked")
    check(bsdf.inputs['Roughness'].is_linked, "Roughness not linked")
    check(bsdf.inputs['Metallic'].is_linked, "Metallic not linked")
    bump = tree.nodes.get(addon.nodes.BUMP_NODE_NAME)
    check(bump is not None and bump.inputs['Height'].is_linked, "Bump height not linked")
    check(bsdf.inputs['Normal'].is_linked, "BSDF normal not linked to bump")
