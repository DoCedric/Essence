"""Preview (solo): look at one mask, one layer's own color or the whole stack's result for a
channel, flat and unshaded, in the viewport.

The viewport can only show what the material shows, so a preview is an **Emission node wired
into the Material Output's Surface** (Workbench ignores the node graph, so Solid shading would
show nothing of a procedural, a generator or a mask stack). The state lives in the graph: the
Emission node carries what it shows (`essence_preview`) and the node and socket that fed the
Surface before, so the toggle's look, a save, a reload and a rebuild all follow from the nodes,
and ending the preview puts the link back exactly. One preview at a time.

    mask     the mask's output (after its fills, adjustments, filters and Enable), as grey
    layer    the layer's own Color fill: the Color input after its source and adjustments,
             before the mask, opacity and blend
    channel  the whole stack's result for a channel: what feeds the shader for it
    map      a Mesh Maps slot (ID, Curvature, AO, Position, Thickness, Normal) on the mesh,
             sampled with the UV map: for reading an ID color with the eyedropper, or just
             looking at a bake on the model

A **map** preview shows raw data, and the eyedropper reads the pixel as displayed and converts
it back to scene linear, which is exact only with the Standard view transform (AgX and Filmic
tone-map and cannot be undone exactly, exposure and gamma shift the values). So while a map
preview runs the scene's color management is set to Standard, no look, exposure 0 and gamma 1;
what it was is kept on the Emission node (`essence_prev_view`, so it survives a save) and put back
when the preview ends or changes to something that is not a map. Other previews leave it alone.

Helper nodes (`essence_preview_helper`) stand in where there is no socket to show: a color
node kept equal to a layer's plain Color value by drivers, and a remap of the signed height.
"""

import bpy

from .channels import BY_ID

NODE = "Essence Preview"
KEY = "essence_preview"
VIEW_KEY = "essence_prev_view"      # {scene, transform, look, exposure, gamma} to put back
HELPER_KEY = "essence_preview_helper"
HELPER_PREFIX = "Essence Preview "


def _output(tree):
    out = next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    return out or next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)


def find(mat):
    """The Emission node of a running preview, or None."""
    tree = mat.node_tree if mat is not None else None
    node = tree.nodes.get(NODE) if tree is not None else None
    return node if node is not None and KEY in node else None


def active(mat):
    """(kind, ident) of the running preview, or None."""
    node = find(mat)
    if node is None:
        return None
    kind, _, ident = str(node[KEY]).partition(":")
    return kind, ident


def view_override(mat):
    """Is the color management set to Standard by a running map preview?"""
    node = find(mat)
    return node is not None and VIEW_KEY in node


def _apply_view(node, exact):
    """Standard color management while a map preview runs (`exact`), the old one otherwise."""
    from .bake import set_if_changed
    stored = node.get(VIEW_KEY)
    if not exact:
        if stored is not None:
            _restore_view(node)
        return
    if stored is not None:
        return                              # already set by this preview: keep what it replaced
    scene = bpy.context.scene
    vs = scene.view_settings
    if (vs.view_transform == 'Standard' and vs.look == 'None'
            and abs(vs.exposure) < 1e-6 and abs(vs.gamma - 1.0) < 1e-6):
        return                              # nothing to change, nothing to put back
    # a dict: a custom property list cannot mix text and numbers
    node[VIEW_KEY] = {"scene": scene.name, "transform": vs.view_transform, "look": vs.look,
                      "exposure": vs.exposure, "gamma": vs.gamma}
    set_if_changed(vs, 'view_transform', 'Standard')
    set_if_changed(vs, 'look', 'None')
    set_if_changed(vs, 'exposure', 0.0)
    set_if_changed(vs, 'gamma', 1.0)


def _restore_view(node):
    from .bake import set_if_changed
    stored = node.get(VIEW_KEY)
    if stored is None:
        return
    scene = bpy.data.scenes.get(stored["scene"]) or bpy.context.scene
    vs = scene.view_settings
    try:
        set_if_changed(vs, 'view_transform', stored["transform"])   # first: it decides the looks
        set_if_changed(vs, 'look', stored["look"])
    except TypeError:
        pass                                                # a look that no longer exists
    set_if_changed(vs, 'exposure', stored["exposure"])
    set_if_changed(vs, 'gamma', stored["gamma"])
    del node[VIEW_KEY]


def _clear_helpers(tree):
    """Remove the helper nodes and the drivers that kept them up to date."""
    ad = tree.animation_data
    if ad is not None:
        for fc in [fc for fc in ad.drivers if fc.data_path.startswith(f'nodes["{HELPER_PREFIX}')]:
            ad.drivers.remove(fc)
    for n in [n for n in tree.nodes if n.get(HELPER_KEY)]:
        tree.nodes.remove(n)


def _color_helper(tree, layer_node, socket_name):
    """A color node that follows a plain (unlinked) color input of a layer, live, by drivers."""
    rgb = tree.nodes.new('ShaderNodeRGB')
    rgb.name = rgb.label = HELPER_PREFIX + "Color"
    rgb[HELPER_KEY] = True
    rgb.outputs[0].default_value = tuple(layer_node.inputs[socket_name].default_value)
    for i in range(4):
        fc = tree.driver_add(f'nodes["{rgb.name}"].outputs[0].default_value', i)
        d = fc.driver
        d.type = 'SCRIPTED'
        v = d.variables.new()
        v.name = "v"
        v.type = 'SINGLE_PROP'
        v.targets[0].id_type = 'NODETREE'
        v.targets[0].id = tree
        v.targets[0].data_path = f'nodes["{layer_node.name}"].inputs["{socket_name}"].default_value[{i}]'
        d.expression = "v"
    return rgb.outputs[0]


def _resolve(mat, kind, ident):
    """The socket a preview of (kind, ident) shows, making helper nodes where needed; None when
    there is nothing to show (the entry or the channel is gone)."""
    from . import nodes
    tree = mat.node_tree
    if kind in ('MASK', 'LAYER'):
        e = next((x for x in mat.essence.layers if x.uid == ident and x.kind == kind), None)
        node = tree.nodes.get(e.node_name) if e is not None else None
        if node is None:
            return None
        if kind == 'MASK':
            return node.outputs[0]
        sock = node.inputs.get("Color B")
        if sock is None:
            return None
        if sock.is_linked:
            return sock.links[0].from_socket
        return _color_helper(tree, node, "Color B")
    if kind == 'MAP':
        slot = tree.nodes.get(nodes.map_slot_name(ident))
        if slot is None or slot.bl_idname != 'ShaderNodeTexImage' or slot.image is None:
            return None
        return slot.outputs['Color']
    if kind == 'CHANNEL':
        ch = BY_ID.get(ident)
        if ch is None:
            return None
        where, name = ch.target
        owner = (nodes.find_principled(tree) if where == 'BSDF'
                 else tree.nodes.get(nodes.BUMP_NODE_NAME))
        sock = owner.inputs.get(name) if owner is not None else None
        if sock is None or not sock.is_linked:
            return None
        src = sock.links[0].from_socket
        if where != 'BUMP':
            return src
        # a signed height: -1 to 1 shown as 0 to 1 (grey is flat)
        remap = tree.nodes.new('ShaderNodeMath')
        remap.name = remap.label = HELPER_PREFIX + "Height"
        remap[HELPER_KEY] = True
        remap.operation = 'MULTIPLY_ADD'
        remap.inputs[1].default_value = 0.5
        remap.inputs[2].default_value = 0.5
        tree.links.new(src, remap.inputs[0])
        return remap.outputs[0]
    return None


def start(mat, kind, ident):
    """Show (kind, ident), replacing any preview that is running. Returns False if there is
    nothing to show."""
    tree = mat.node_tree
    out = _output(tree)
    if tree is None or out is None:
        return False
    surface = out.inputs['Surface']
    node = find(mat)
    created = node is None
    if created:
        node = tree.nodes.get(NODE)
        if node is not None:             # a stray node of that name from a broken state
            tree.nodes.remove(node)
        orig = surface.links[0].from_socket if surface.is_linked else None
        node = tree.nodes.new('ShaderNodeEmission')
        node.name = node.label = NODE
        node.location = (out.location.x - 200, out.location.y - 250)
        node["essence_prev_node"] = orig.node.name if orig is not None else ""
        node["essence_prev_socket"] = orig.identifier if orig is not None else ""
        node.inputs['Strength'].default_value = 1.0
    else:
        _clear_helpers(tree)
    src = _resolve(mat, kind, ident)
    if src is None:
        if created:
            tree.nodes.remove(node)
        else:
            stop(mat)
        return False
    node[KEY] = f"{kind}:{ident}"
    _apply_view(node, kind == 'MAP')
    color = node.inputs['Color']
    for l in list(color.links):
        tree.links.remove(l)
    tree.links.new(src, color)
    if not (surface.is_linked and surface.links[0].from_node == node):
        for l in list(surface.links):
            tree.links.remove(l)
        tree.links.new(node.outputs['Emission'], surface)
    return True


def stop(mat):
    """End the preview and put the Surface link back as it was. Returns False if none ran."""
    from . import nodes
    tree = mat.node_tree if mat is not None else None
    node = tree.nodes.get(NODE) if tree is not None else None
    if node is None or KEY not in node:
        return False
    out = _output(tree)
    prev_node = tree.nodes.get(node.get("essence_prev_node", ""))
    prev_sock = (next((s for s in prev_node.outputs if s.identifier == node.get("essence_prev_socket")),
                      None) if prev_node is not None else None)
    _clear_helpers(tree)
    _restore_view(node)
    tree.nodes.remove(node)
    if out is not None:
        surface = out.inputs['Surface']
        if prev_sock is None:                # the original is gone: the shader, if it still exists
            bsdf = nodes.find_principled(tree)
            prev_sock = bsdf.outputs[0] if bsdf is not None else None
        if prev_sock is not None and not surface.is_linked:
            tree.links.new(prev_sock, surface)
    return True


def refresh(mat):
    """After a rebuild: point a running preview at the current nodes, or end it when what it
    showed is gone."""
    node = find(mat)
    if node is None:
        return
    kind, ident = active(mat)
    start(mat, kind, ident) or stop(mat)


def toggle(mat, kind, ident):
    """Start the preview of (kind, ident), or end it if that is the one running."""
    if active(mat) == (kind, ident):
        stop(mat)
        return False
    return start(mat, kind, ident)
