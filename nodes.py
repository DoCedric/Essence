"""Builds the layer node group and wires layer stacks inside a material."""

import bpy

from .channels import CHANNELS, MASK_SOCKET

MASK_INVERT = "Mask Invert"
MASK_BRIGHTNESS = "Mask Brightness"
MASK_CONTRAST = "Mask Contrast"

GROUP_NAME = "Essence Layer"
GROUP_VERSION = 3  # 2: per-channel Enable inputs, 3: mask invert/brightness/contrast
VERSION_KEY = "essence_version"
BUMP_NODE_NAME = "Essence Bump"
NORMAL_NODE_NAME = "Essence Normal Map"


# --------------------------------------------------------------------------
# Layer node group
# --------------------------------------------------------------------------

def _by_ident(sockets, ident):
    for s in sockets:
        if s.identifier == ident:
            return s
    raise KeyError(ident)


def _new_socket(iface, name, in_out, ch):
    s = iface.new_socket(name, in_out=in_out, socket_type=ch.socket_type)
    if in_out == 'INPUT':
        s.default_value = ch.default
    if ch.min_value is not None and in_out == 'INPUT':
        s.min_value = ch.min_value
        s.max_value = ch.max_value
    if ch.socket_type == 'NodeSocketFloat' and in_out == 'INPUT':
        s.subtype = ch.subtype
    return s


def _build_group(ng):
    iface = ng.interface
    iface.clear()
    ng.nodes.clear()

    for ch in CHANNELS:
        _new_socket(iface, f"{ch.label} A", 'INPUT', ch)
        _new_socket(iface, f"{ch.label} B", 'INPUT', ch)
    mask = iface.new_socket(MASK_SOCKET, in_out='INPUT', socket_type='NodeSocketFloat')
    mask.default_value = 1.0
    mask.min_value = 0.0
    mask.max_value = 1.0
    mask.subtype = 'FACTOR'
    inv = iface.new_socket(MASK_INVERT, in_out='INPUT', socket_type='NodeSocketBool')
    inv.default_value = False
    for name in (MASK_BRIGHTNESS, MASK_CONTRAST):
        s = iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
        s.default_value = 0.0
        s.min_value = -100.0  # same range as Blender's Brightness/Contrast node
        s.max_value = 100.0
    for ch in CHANNELS:
        en = iface.new_socket(f"{ch.label} Enable", in_out='INPUT',
                              socket_type='NodeSocketBool')
        en.default_value = True
    for ch in CHANNELS:
        _new_socket(iface, ch.label, 'OUTPUT', ch)

    nodes, links = ng.nodes, ng.links
    n_in = nodes.new('NodeGroupInput')
    n_in.location = (-1300, 0)
    n_out = nodes.new('NodeGroupOutput')
    n_out.location = (400, 0)

    # Mask adjustments: invert -> brightness/contrast -> clamp.
    sub = nodes.new('ShaderNodeMath')
    sub.operation = 'SUBTRACT'
    sub.label = "Invert (|invert - mask|)"
    sub.location = (-1000, 300)
    links.new(n_in.outputs[MASK_INVERT], sub.inputs[0])
    links.new(n_in.outputs[MASK_SOCKET], sub.inputs[1])
    absn = nodes.new('ShaderNodeMath')
    absn.operation = 'ABSOLUTE'
    absn.location = (-800, 300)
    links.new(sub.outputs[0], absn.inputs[0])
    bc = nodes.new('ShaderNodeBrightContrast')
    bc.location = (-600, 300)
    links.new(absn.outputs[0], bc.inputs['Color'])
    links.new(n_in.outputs[MASK_BRIGHTNESS], bc.inputs['Bright'])
    links.new(n_in.outputs[MASK_CONTRAST], bc.inputs['Contrast'])
    clamp = nodes.new('ShaderNodeClamp')
    clamp.location = (-400, 300)
    links.new(bc.outputs['Color'], clamp.inputs['Value'])
    mask_out = clamp.outputs['Result']

    for i, ch in enumerate(CHANNELS):
        a = n_in.outputs[f"{ch.label} A"]
        b = n_in.outputs[f"{ch.label} B"]
        y = -i * 200
        # Effective factor = mask * enable. Disabled channel -> factor 0 -> A leaks through.
        fac = nodes.new('ShaderNodeMath')
        fac.operation = 'MULTIPLY'
        fac.location = (-200, y)
        fac.label = f"{ch.label} Factor"
        links.new(mask_out, fac.inputs[0])
        links.new(n_in.outputs[f"{ch.label} Enable"], fac.inputs[1])
        factor = fac.outputs[0]
        if ch.blend == 'MIX':
            node = nodes.new('ShaderNodeMix')
            is_color = ch.socket_type == 'NodeSocketColor'
            node.data_type = 'RGBA' if is_color else 'FLOAT'
            node.blend_type = 'MIX'
            suffix = 'Color' if is_color else 'Float'
            links.new(factor, _by_ident(node.inputs, "Factor_Float"))
            links.new(a, _by_ident(node.inputs, f"A_{suffix}"))
            links.new(b, _by_ident(node.inputs, f"B_{suffix}"))
            result = _by_ident(node.outputs, f"Result_{suffix}")
        elif ch.blend == 'ADD':
            # A + B * mask
            node = nodes.new('ShaderNodeMath')
            node.operation = 'MULTIPLY_ADD'
            links.new(b, node.inputs[0])
            links.new(factor, node.inputs[1])
            links.new(a, node.inputs[2])
            result = node.outputs[0]
        else:
            raise ValueError(f"Unknown blend method {ch.blend}")
        node.location = (0, y)
        node.label = f"{ch.label} ({ch.blend.title()})"
        links.new(result, n_out.inputs[ch.label])


def ensure_layer_group():
    """Return the shared layer node group, (re)building it if missing or outdated."""
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is not None and ng.get(VERSION_KEY) == GROUP_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP_NAME, 'ShaderNodeTree')
    saved = _snapshot_values(ng)
    _build_group(ng)
    ng[VERSION_KEY] = GROUP_VERSION
    _restore_values(saved)
    return ng


def _snapshot_values(ng):
    """Input values of every instance of `ng`, by (material, node, socket name), so an
    interface rebuild (which resets them) can be undone."""
    saved = []
    for mat in bpy.data.materials:
        if mat.node_tree is None:
            continue
        for n in mat.node_tree.nodes:
            if n.type == 'GROUP' and n.node_tree is ng:
                for s in n.inputs:
                    if hasattr(s, "default_value"):
                        v = s.default_value
                        saved.append((mat.name, n.name, s.name,
                                      tuple(v) if hasattr(v, "__len__") else v))
    return saved


def _restore_values(saved):
    for mat_name, node_name, sock_name, value in saved:
        mat = bpy.data.materials.get(mat_name)
        node = mat.node_tree.nodes.get(node_name) if mat and mat.node_tree else None
        sock = node.inputs.get(sock_name) if node else None
        if sock is not None:
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                pass


# --------------------------------------------------------------------------
# Material helpers
# --------------------------------------------------------------------------

def ensure_tree(mat):
    if mat.node_tree is None:
        mat.use_nodes = True
    return mat.node_tree


def find_principled(tree):
    for n in tree.nodes:
        if n.bl_idname == 'ShaderNodeBsdfPrincipled':
            return n
    out = None
    for n in tree.nodes:
        if n.bl_idname == 'ShaderNodeOutputMaterial':
            out = n
            break
    if out is None:
        out = tree.nodes.new('ShaderNodeOutputMaterial')
        out.location = (300, 0)
    bsdf = tree.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (out.location.x - 300, out.location.y)
    tree.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return bsdf


def ensure_bump_node(tree, bsdf):
    bump = tree.nodes.get(BUMP_NODE_NAME)
    if bump is None:
        bump = tree.nodes.new('ShaderNodeBump')
        bump.name = BUMP_NODE_NAME
        bump.label = BUMP_NODE_NAME
        bump.inputs['Distance'].default_value = 0.05
        bump.location = (bsdf.location.x - 250, bsdf.location.y - 700)
    if not bump.outputs['Normal'].is_linked:
        tree.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    return bump


def apply_normal_map(mat, image_node):
    """Wire a baked tangent-space normal image into the shader.

    image -> Normal Map -> Bump (Normal input) -> BSDF Normal, so the normal map is
    evaluated before the bump (layer height) as Blender requires. Returns the
    Normal Map node, whose Strength is the user-facing slider."""
    tree = mat.node_tree
    bsdf = find_principled(tree)
    bump = ensure_bump_node(tree, bsdf)

    nm = tree.nodes.get(NORMAL_NODE_NAME)
    if nm is None:
        nm = tree.nodes.new('ShaderNodeNormalMap')
        nm.name = NORMAL_NODE_NAME
        nm.label = NORMAL_NODE_NAME
        nm.space = 'TANGENT'
    nm.location = (bump.location.x - 250, bump.location.y - 150)
    image_node.location = (nm.location.x - 320, nm.location.y)

    _clear_links(tree, nm.inputs['Color'])
    tree.links.new(image_node.outputs['Color'], nm.inputs['Color'])
    _clear_links(tree, bump.inputs['Normal'])
    tree.links.new(nm.outputs['Normal'], bump.inputs['Normal'])
    return nm


def _clear_links(tree, sock):
    for l in list(sock.links):
        tree.links.remove(l)


def rebuild_stack(mat):
    """Wire the layer nodes of `mat` into a chain and into the shader."""
    tree = mat.node_tree
    layers = mat.essence.layers
    nodes = [tree.nodes.get(e.node_name) for e in layers]
    nodes = [n for n in nodes if n is not None]
    if not nodes:
        return

    bsdf = find_principled(tree)
    bump = ensure_bump_node(tree, bsdf)
    targets = {'BSDF': bsdf, 'BUMP': bump}

    px, py = bsdf.location.x, bsdf.location.y
    bump.location = (px - 250, py - 700)

    prev = None
    count = len(nodes)
    for i, node in enumerate(nodes):
        node.location = (px - 300 - (count - i) * 260, py)
        for ch in CHANNELS:
            a = node.inputs[f"{ch.label} A"]
            _clear_links(tree, a)
            if prev is not None:
                tree.links.new(prev.outputs[ch.label], a)
        prev = node

    for ch in CHANNELS:
        kind, sock_name = ch.target
        dst = targets[kind].inputs[sock_name]
        _clear_links(tree, dst)
        tree.links.new(prev.outputs[ch.label], dst)

    # Mask image nodes sit below their layer.
    for e in layers:
        layer_node = tree.nodes.get(e.node_name)
        mask_node = tree.nodes.get(e.mask_node_name) if e.mask_node_name else None
        if layer_node and mask_node:
            # Restores the link after a group upgrade rebuilt the interface.
            if not layer_node.inputs[MASK_SOCKET].is_linked:
                tree.links.new(mask_node.outputs['Color'], layer_node.inputs[MASK_SOCKET])
            mask_node.location = (layer_node.location.x - 40, layer_node.location.y - 420)


def add_layer_node(mat, name):
    tree = ensure_tree(mat)
    node = tree.nodes.new('ShaderNodeGroup')
    node.node_tree = ensure_layer_group()
    node.label = name
    node.width = 240
    return node


def add_mask_node(mat, layer_node, image):
    tree = mat.node_tree
    n = tree.nodes.new('ShaderNodeTexImage')
    n.image = image
    n.label = f"{layer_node.label} Mask"
    n.interpolation = 'Linear'
    tree.links.new(n.outputs['Color'], layer_node.inputs[MASK_SOCKET])
    return n
