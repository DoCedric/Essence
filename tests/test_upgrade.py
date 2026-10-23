import bpy

from harness import check, close, make_plane


def test_group_upgrade_keeps_values(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    layer = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    layer.inputs["Color B"].default_value = (0.1, 0.2, 0.3, 1.0)
    layer.inputs["Roughness B"].default_value = 0.37
    layer.inputs["Roughness Enable"].default_value = False
    ng = bpy.data.node_groups[addon.nodes.GROUP_NAME]
    ng[addon.nodes.VERSION_KEY] = addon.nodes.GROUP_VERSION - 1  # pretend it is outdated
    check(bpy.ops.essence.rebuild() == {'FINISHED'}, "rebuild failed")
    ng = bpy.data.node_groups[addon.nodes.GROUP_NAME]
    check(ng[addon.nodes.VERSION_KEY] == addon.nodes.GROUP_VERSION, "group not upgraded")
    layer = mat.node_tree.nodes[mat.essence.layers[-1].node_name]
    close(layer.inputs["Roughness B"].default_value, 0.37, 1e-4, "roughness kept")
    check(layer.inputs["Roughness Enable"].default_value is False, "enable kept")
    for got, want in zip(layer.inputs["Color B"].default_value, (0.1, 0.2, 0.3, 1.0)):
        close(got, want, 1e-4, "color kept")
    check(layer.outputs["Color"].is_linked, "layer output unlinked after upgrade")
