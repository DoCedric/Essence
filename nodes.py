"""Builds the layer node group and wires layer stacks inside a material."""

import bpy

from . import sources, stack
from .channels import CHANNELS, MASK_SOCKET

MASK_INVERT = "Mask Invert"
MASK_BRIGHTNESS = "Mask Brightness"
MASK_CONTRAST = "Mask Contrast"

MASK_ENABLE = "Enable"
MASK_VALUE = "Value"
MASK_PREVIOUS = "Previous"

GROUP_NAME = "Essence Layer"
# 2: per-channel Enable inputs, 3: mask invert/brightness/contrast,
# 4: mask adjustments moved to the mask group (a layer has a stack of masks)
# 5: layer Opacity and per-channel Opacity inputs, Visible (eye toggle)
GROUP_VERSION = 5
OPACITY = "Opacity"
VISIBLE = "Visible"
FOLDER_GROUP_NAME = "Essence Folder"
# 3: Visible (eye toggle)
FOLDER_GROUP_VERSION = 3
MASK_GROUP_NAME = "Essence Mask"
MASK_GROUP_VERSION = 1
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


def _opacity_socket(iface, name):
    s = iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
    s.default_value = 1.0
    s.min_value = 0.0
    s.max_value = 1.0
    s.subtype = 'FACTOR'
    return s


def _build_group(ng, folder=False):
    """Build the layer group, or the folder group (`folder=True`).

    The folder group has the same A/B/Mask interface but no Enable inputs,
    and lerps every channel (A = stack below the folder, B = result of the folder's
    layers), so the folder mask limits the combined effect of everything inside."""
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
    vis_in = iface.new_socket(VISIBLE, in_out='INPUT', socket_type='NodeSocketBool')
    vis_in.default_value = True
    if not folder:
        _opacity_socket(iface, OPACITY)
    for ch in CHANNELS if not folder else ():
        _opacity_socket(iface, f"{ch.label} Opacity")
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

    # The mask arrives already adjusted and combined by the mask nodes feeding it.
    mask_out = n_in.outputs[MASK_SOCKET]
    # The eye toggle: a hidden layer or folder has no effect (factor 0).
    vis = nodes.new('ShaderNodeMath')
    vis.operation = 'MULTIPLY'
    vis.label = "Visible"
    vis.location = (-800, 150)
    links.new(mask_out, vis.inputs[0])
    links.new(n_in.outputs[VISIBLE], vis.inputs[1])
    mask_out = vis.outputs[0]
    if not folder:
        # Layer opacity scales the mask once for every channel.
        lop = nodes.new('ShaderNodeMath')
        lop.operation = 'MULTIPLY'
        lop.label = "Layer Opacity"
        lop.location = (-600, 150)
        links.new(mask_out, lop.inputs[0])
        links.new(n_in.outputs[OPACITY], lop.inputs[1])
        mask_out = lop.outputs[0]
        lop.location = (-600, 150)

    for i, ch in enumerate(CHANNELS):
        a = n_in.outputs[f"{ch.label} A"]
        b = n_in.outputs[f"{ch.label} B"]
        y = -i * 200
        # Effective factor = mask * enable. Disabled channel -> factor 0 -> A leaks through.
        blend = 'MIX' if folder else ch.blend
        if folder:
            factor = mask_out
        else:
            cop = nodes.new('ShaderNodeMath')
            cop.operation = 'MULTIPLY'
            cop.location = (-400, y)
            cop.label = f"{ch.label} Opacity"
            links.new(mask_out, cop.inputs[0])
            links.new(n_in.outputs[f"{ch.label} Opacity"], cop.inputs[1])
            fac = nodes.new('ShaderNodeMath')
            fac.operation = 'MULTIPLY'
            fac.location = (-200, y)
            fac.label = f"{ch.label} Factor"
            links.new(cop.outputs[0], fac.inputs[0])
            links.new(n_in.outputs[f"{ch.label} Enable"], fac.inputs[1])
            factor = fac.outputs[0]
        if blend == 'MIX':
            node = nodes.new('ShaderNodeMix')
            is_color = ch.socket_type == 'NodeSocketColor'
            node.data_type = 'RGBA' if is_color else 'FLOAT'
            node.blend_type = 'MIX'
            suffix = 'Color' if is_color else 'Float'
            links.new(factor, _by_ident(node.inputs, "Factor_Float"))
            links.new(a, _by_ident(node.inputs, f"A_{suffix}"))
            links.new(b, _by_ident(node.inputs, f"B_{suffix}"))
            result = _by_ident(node.outputs, f"Result_{suffix}")
        elif blend == 'ADD':
            # A + B * mask
            node = nodes.new('ShaderNodeMath')
            node.operation = 'MULTIPLY_ADD'
            links.new(b, node.inputs[0])
            links.new(factor, node.inputs[1])
            links.new(a, node.inputs[2])
            result = node.outputs[0]
        else:
            raise ValueError(f"Unknown blend method {blend}")
        node.location = (0, y)
        node.label = f"{ch.label} ({blend.title()})"
        links.new(result, n_out.inputs[ch.label])


def _build_mask_group(ng):
    """One mask of a layer or folder. Adjusts the image value (invert, then
    brightness/contrast, then clamp 0-1) and multiplies it onto the masks before it, so a
    stack of masks is a chain of these nodes. A disabled mask contributes 1 (no effect)."""
    iface = ng.interface
    iface.clear()
    ng.nodes.clear()

    def float_in(name, default, lo=None, hi=None, subtype='NONE'):
        s = iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
        s.default_value = default
        if lo is not None:
            s.min_value, s.max_value = lo, hi
        s.subtype = subtype
        return s

    float_in(MASK_VALUE, 1.0, 0.0, 1.0, 'FACTOR')
    float_in(MASK_PREVIOUS, 1.0, 0.0, 1.0, 'FACTOR')
    inv = iface.new_socket(MASK_INVERT, in_out='INPUT', socket_type='NodeSocketBool')
    inv.default_value = False
    float_in(MASK_BRIGHTNESS, 0.0, -100.0, 100.0)  # same range as the Brightness/Contrast node
    float_in(MASK_CONTRAST, 0.0, -100.0, 100.0)
    en = iface.new_socket(MASK_ENABLE, in_out='INPUT', socket_type='NodeSocketBool')
    en.default_value = True
    iface.new_socket(MASK_SOCKET, in_out='OUTPUT', socket_type='NodeSocketFloat')

    nodes, links = ng.nodes, ng.links
    n_in = nodes.new('NodeGroupInput')
    n_in.location = (-1100, 0)
    n_out = nodes.new('NodeGroupOutput')
    n_out.location = (500, 0)

    sub = nodes.new('ShaderNodeMath')
    sub.operation = 'SUBTRACT'
    sub.label = "Invert (|invert - mask|)"
    sub.location = (-850, 100)
    links.new(n_in.outputs[MASK_INVERT], sub.inputs[0])
    links.new(n_in.outputs[MASK_VALUE], sub.inputs[1])
    absn = nodes.new('ShaderNodeMath')
    absn.operation = 'ABSOLUTE'
    absn.location = (-650, 100)
    links.new(sub.outputs[0], absn.inputs[0])
    bc = nodes.new('ShaderNodeBrightContrast')
    bc.location = (-450, 100)
    links.new(absn.outputs[0], bc.inputs['Color'])
    links.new(n_in.outputs[MASK_BRIGHTNESS], bc.inputs['Bright'])
    links.new(n_in.outputs[MASK_CONTRAST], bc.inputs['Contrast'])
    clamp = nodes.new('ShaderNodeClamp')
    clamp.location = (-250, 100)
    links.new(bc.outputs['Color'], clamp.inputs['Value'])

    # Disabled -> 1.0, enabled -> adjusted value.
    gate = nodes.new('ShaderNodeMix')
    gate.data_type = 'FLOAT'
    gate.label = "Enable"
    gate.location = (-50, 100)
    gate.inputs[2].default_value = 1.0  # A_Float
    links.new(n_in.outputs[MASK_ENABLE], _by_ident(gate.inputs, "Factor_Float"))
    links.new(clamp.outputs['Result'], _by_ident(gate.inputs, "B_Float"))
    mul = nodes.new('ShaderNodeMath')
    mul.operation = 'MULTIPLY'
    mul.label = "Multiply with previous masks"
    mul.location = (200, 100)
    links.new(_by_ident(gate.outputs, "Result_Float"), mul.inputs[0])
    links.new(n_in.outputs[MASK_PREVIOUS], mul.inputs[1])
    links.new(mul.outputs[0], n_out.inputs[MASK_SOCKET])


def _ensure_group(name, version, folder):
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get(VERSION_KEY) == version:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    elif name != MASK_GROUP_NAME:
        # Upgrading rebuilds the interface for every material using the group, which drops
        # the old per-layer mask inputs. Convert those to mask entries first.
        for mat in bpy.data.materials:
            migrate(mat)
    saved = _snapshot_values(ng)
    saved_links = _snapshot_links(ng)
    if name == MASK_GROUP_NAME:
        _build_mask_group(ng)
    else:
        _build_group(ng, folder)
    ng[VERSION_KEY] = version
    _restore_values(saved)
    _restore_links(saved_links)
    return ng


def ensure_layer_group():
    """Return the shared layer node group, (re)building it if missing or outdated."""
    return _ensure_group(GROUP_NAME, GROUP_VERSION, False)


def ensure_folder_group():
    """Return the shared folder node group, (re)building it if missing or outdated."""
    return _ensure_group(FOLDER_GROUP_NAME, FOLDER_GROUP_VERSION, True)


def ensure_mask_group():
    """Return the shared mask node group, (re)building it if missing or outdated."""
    return _ensure_group(MASK_GROUP_NAME, MASK_GROUP_VERSION, False)


def group_for(entry):
    if entry.kind == 'MASK':
        return ensure_mask_group()
    return ensure_folder_group() if entry.kind == 'FOLDER' else ensure_layer_group()


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


def _snapshot_links(ng):
    """Links into every instance of `ng`, so the ones the user or a layer source wired by
    hand (e.g. into a B input) survive an interface rebuild, which drops them."""
    saved = []
    for mat in bpy.data.materials:
        if mat.node_tree is None:
            continue
        for l in mat.node_tree.links:
            n = l.to_node
            if n.type == 'GROUP' and n.node_tree is ng:
                saved.append((mat.name, l.from_node.name, l.from_socket.name,
                              n.name, l.to_socket.name))
    return saved


def _restore_links(saved):
    for mat_name, from_node, from_sock, to_node, to_sock in saved:
        mat = bpy.data.materials.get(mat_name)
        tree = mat.node_tree if mat else None
        a = tree.nodes.get(from_node) if tree else None
        b = tree.nodes.get(to_node) if tree else None
        if a is None or b is None:
            continue
        out = a.outputs.get(from_sock)
        inp = b.inputs.get(to_sock)
        if out is not None and inp is not None and not inp.is_linked:
            tree.links.new(out, inp)


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
    entries = [(e, tree.nodes.get(e.node_name)) for e in layers if e.kind != 'MASK']
    entries = [(e, n) for e, n in entries if n is not None]
    sources.cleanup(tree, {e.uid for e in layers})
    if not entries:
        return

    bsdf = find_principled(tree)
    bump = ensure_bump_node(tree, bsdf)
    targets = {'BSDF': bsdf, 'BUMP': bump}

    px, py = bsdf.location.x, bsdf.location.y
    bump.location = (px - 250, py - 700)

    # The list is bottom-to-top and a folder entry sits directly above its children.
    # Children chain from the stack below the folder; the folder node then lerps
    # between that stack (A) and the end of the children's chain (B).
    def link_chain(node, a_src, b_src=None):
        for ch in CHANNELS:
            a = node.inputs[f"{ch.label} A"]
            _clear_links(tree, a)
            if a_src is not None:
                tree.links.new(a_src.outputs[ch.label], a)
            if b_src is not None:
                b = node.inputs[f"{ch.label} B"]
                _clear_links(tree, b)
                tree.links.new(b_src.outputs[ch.label], b)

    prev = None
    base = None        # chain tail below the folder currently being filled
    open_uid = ""
    count = len(entries)
    for i, (e, node) in enumerate(entries):
        node.location = (px - 300 - (count - i) * 260, py)
        if e.kind == 'FOLDER':
            if open_uid == e.uid and e.uid:
                link_chain(node, base, prev)
            else:  # empty folder: passes the stack through
                link_chain(node, prev, prev)
            open_uid = ""
        else:
            if e.parent != open_uid:
                open_uid = e.parent
                base = prev
            link_chain(node, prev)
        prev = node

    for ch in CHANNELS:
        kind, sock_name = ch.target
        dst = targets[kind].inputs[sock_name]
        _clear_links(tree, dst)
        tree.links.new(prev.outputs[ch.label], dst)

    for m in layers:
        if m.kind == 'MASK' and m.uid and tree.nodes.get(m.node_name) is not None:
            sources.ensure_mask_source(mat, m)
    for e, node in entries:
        _link_masks(tree, layers, e, node)
        if e.kind == 'LAYER' and e.uid:
            sources.ensure_entries(e)
            sources.layout(tree, e, node)


def _link_masks(tree, layers, owner, owner_node):
    """Chain the owner's mask nodes (image -> mask node -> next mask node) into its Mask
    input, and lay them out in a column below the owner."""
    dst = owner_node.inputs[MASK_SOCKET]
    _clear_links(tree, dst)
    prev = None
    x, y = owner_node.location.x, owner_node.location.y - 420
    for m in layers:
        if m.kind != 'MASK' or m.parent != owner.uid:
            continue
        node = tree.nodes.get(m.node_name)
        if node is None:
            continue
        prev_in = node.inputs[MASK_PREVIOUS]
        _clear_links(tree, prev_in)
        if prev is not None:
            tree.links.new(prev.outputs[MASK_SOCKET], prev_in)
        node.location = (x, y)
        if m.source.channel == 'MASK':
            # The source nodes (see sources.py) feed Value; they sit below the mask node.
            y = sources.layout_mask(tree, m, node)
        else:
            image = tree.nodes.get(m.mask_node_name) if m.mask_node_name else None
            value_in = node.inputs[MASK_VALUE]
            _clear_links(tree, value_in)
            if image is not None:
                tree.links.new(image.outputs['Color'], value_in)
                image.location = (x, y - 250)
            y -= 650
        prev = node
    if prev is not None:
        tree.links.new(prev.outputs[MASK_SOCKET], dst)


def add_layer_node(mat, name, folder=False):
    tree = ensure_tree(mat)
    node = tree.nodes.new('ShaderNodeGroup')
    node.node_tree = ensure_folder_group() if folder else ensure_layer_group()
    node.label = name
    node.width = 240
    return node


def add_mask_node(mat, label, image):
    """Create a mask's group node and its image node (Non-Color). Returns (group, image)."""
    tree = mat.node_tree
    img_node = tree.nodes.new('ShaderNodeTexImage')
    img_node.image = image
    img_node.label = label
    img_node.interpolation = 'Linear'
    img_node.width = 240
    node = tree.nodes.new('ShaderNodeGroup')
    node.node_tree = ensure_mask_group()
    node.label = label
    node.width = 240
    return node, img_node


def needs_migration(mat):
    """True if the material has entries from before uids and mask entries existed."""
    return any(not e.uid or (e.kind != 'MASK' and e.mask_node_name)
               for e in mat.essence.layers)


def migrate(mat):
    """Turn the old single mask stored on a layer or folder into a mask entry. Must run
    before the layer groups are upgraded, since that drops the old adjustment inputs."""
    if mat.node_tree is None or not needs_migration(mat):
        return False
    tree = mat.node_tree
    layers = mat.essence.layers
    stack.ensure_uids(layers)
    for i in range(len(layers)):
        e = layers[i]
        if e.kind == 'MASK' or not e.mask_node_name:
            continue
        owner_node = tree.nodes.get(e.node_name)
        old_image = e.mask_node_name
        img = tree.nodes.get(old_image)
        node = tree.nodes.new('ShaderNodeGroup')
        node.node_tree = ensure_mask_group()
        node.width = 240
        node.label = node.name
        if owner_node is not None:
            for sock in (MASK_INVERT, MASK_BRIGHTNESS, MASK_CONTRAST):
                old = owner_node.inputs.get(sock)
                if old is not None:
                    node.inputs[sock].default_value = old.default_value
        owner_uid = e.uid
        owner_name = e.name
        m = layers.add()
        m.kind = 'MASK'
        m.uid = stack.new_uid()
        m.parent = owner_uid
        m.name = "Mask"
        m.node_name = node.name
        m.mask_node_name = old_image
        node.label = f"{owner_name} Mask"
        if img is not None:
            img.label = f"{owner_name} Mask"
        layers[i].mask_node_name = ""
    stack.Tree(layers).apply()
    return True
