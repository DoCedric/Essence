"""Builds the layer node group and wires layer stacks inside a material."""

import json

import bpy

from . import blends, sources, stack
from .persist import PAINT_KEY
from .channels import BY_ID, CHANNELS, MASK_SOCKET, active_channels


MASK_ENABLE = "Enable"
MASK_VALUE = "Value"

GROUP_NAME = "Essence Layer"
# 2: per-channel Enable inputs, 3: mask invert/brightness/contrast,
# 4: mask adjustments moved to the mask group (a layer has a stack of masks)
# 5: layer Opacity and per-channel Opacity inputs, Visible (eye toggle)
GROUP_VERSION = 5
OPACITY = "Opacity"
VISIBLE = "Visible"
FOLDER_GROUP_NAME = "Essence Folder"
# 3: Visible (eye toggle), 4: Opacity input and a Factor output (Passthrough folders)
FOLDER_GROUP_VERSION = 4
FACTOR = "Factor"
# A Passthrough folder's child gets its mask multiplied by the folder's Factor in this node.
FOLDER_FACTOR_PREFIX = "Essence Folder Factor "
MASK_GROUP_NAME = "Essence Mask"
MASK_GROUP_VERSION = 2
VERSION_KEY = "essence_version"
BUMP_NODE_NAME = "Essence Bump"
NORMAL_NODE_NAME = "Essence Normal Map"
BAKED_NORMAL_NODE = "Essence_Bake_NORMAL"  # the Normal slot, also the bake target
AO_STRENGTH_NODE = "Essence AO Strength"   # Value node, the slider
AO_COLOR_NODE = "Essence AO Color"         # Base Color * AO
AO_SPEC_FACTOR_NODE = "Essence AO Specular Factor"
AO_SPEC_NODE = "Essence AO Specular"       # Specular IOR Level * AO
AO_COMBINE_NODE = "Essence AO Combine"     # the AO slot * the stack's AO channel


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


def default_key():
    """The channel set and blend modes of a plain material: every default-enabled channel,
    every channel on its default mode."""
    return {"ch": [c.id for c in CHANNELS if c.default_enabled], "modes": {}}


def _build_group(ng, folder=False, key=None):
    """Build the layer group, or the folder group (`folder=True`).

    `key` says which channels the group has ("ch") and, for layers, which blend mode each
    uses ("modes", channel id -> mode; missing means the channel's default). Every distinct
    key is its own group, so one layer node stays one node whatever its blend modes.

    The folder group has the same A/B/Mask interface but no Enable inputs,
    and lerps every channel (A = stack below the folder, B = result of the folder's
    layers), so the folder mask limits the combined effect of everything inside. Its Factor
    output (mask * visible * opacity) is what a Passthrough folder multiplies into the masks
    of its children instead; the channels are then left unused."""
    key = key or default_key()
    active = [BY_ID[i] for i in key["ch"]]
    modes = key.get("modes", {})
    paint = set(key.get("paint", ()))
    iface = ng.interface
    iface.clear()
    ng.nodes.clear()

    for ch in active:
        _new_socket(iface, f"{ch.label} A", 'INPUT', ch)
        _new_socket(iface, f"{ch.label} B", 'INPUT', ch)
    mask = iface.new_socket(MASK_SOCKET, in_out='INPUT', socket_type='NodeSocketFloat')
    mask.default_value = 1.0
    mask.min_value = 0.0
    mask.max_value = 1.0
    mask.subtype = 'FACTOR'
    vis_in = iface.new_socket(VISIBLE, in_out='INPUT', socket_type='NodeSocketBool')
    vis_in.default_value = True
    _opacity_socket(iface, OPACITY)
    for ch in active if not folder else ():
        _opacity_socket(iface, f"{ch.label} Opacity")
        en = iface.new_socket(f"{ch.label} Enable", in_out='INPUT',
                              socket_type='NodeSocketBool')
        en.default_value = True
        if ch.id in paint:      # a Paint channel: its image's alpha, so unpainted shows below
            _opacity_socket(iface, sources.nodes_alpha_name(ch))
    for ch in active:
        _new_socket(iface, ch.label, 'OUTPUT', ch)
    if folder:
        iface.new_socket(FACTOR, in_out='OUTPUT', socket_type='NodeSocketFloat')

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
    # Opacity scales the mask once for every channel.
    lop = nodes.new('ShaderNodeMath')
    lop.operation = 'MULTIPLY'
    lop.label = "Folder Opacity" if folder else "Layer Opacity"
    lop.location = (-600, 150)
    links.new(mask_out, lop.inputs[0])
    links.new(n_in.outputs[OPACITY], lop.inputs[1])
    mask_out = lop.outputs[0]
    if folder:
        links.new(mask_out, n_out.inputs[FACTOR])

    for i, ch in enumerate(active):
        a = n_in.outputs[f"{ch.label} A"]
        b = n_in.outputs[f"{ch.label} B"]
        y = -i * 300
        # Effective factor = mask * opacity * enable. A disabled channel has factor 0, so
        # A leaks through.
        if folder:
            factor = mask_out
            mode = 'MIX'
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
            if ch.id in paint:
                pa = nodes.new('ShaderNodeMath')
                pa.operation = 'MULTIPLY'
                pa.location = (-200, y - 150)
                pa.label = f"{ch.label} Paint Alpha"
                links.new(factor, pa.inputs[0])
                links.new(n_in.outputs[sources.nodes_alpha_name(ch)], pa.inputs[1])
                factor = pa.outputs[0]
            mode = modes.get(ch.id, ch.blend)
        result = blends.build(nodes, links, ch, mode, a, b, factor, (0, y))
        links.new(result, n_out.inputs[ch.label])


def _build_mask_group(ng):
    """The mask of a layer or folder: clamps the mask value to 0-1 and gates it with Enable
    (a disabled mask is 1, no effect). Invert, brightness and the like are adjustment items on
    the mask (see adjustments.py), so their order against fills and filters is the user's."""
    iface = ng.interface
    iface.clear()
    ng.nodes.clear()
    v = iface.new_socket(MASK_VALUE, in_out='INPUT', socket_type='NodeSocketFloat')
    v.default_value = 1.0
    v.min_value, v.max_value = 0.0, 1.0
    v.subtype = 'FACTOR'
    en = iface.new_socket(MASK_ENABLE, in_out='INPUT', socket_type='NodeSocketBool')
    en.default_value = True
    iface.new_socket(MASK_SOCKET, in_out='OUTPUT', socket_type='NodeSocketFloat')

    nodes, links = ng.nodes, ng.links
    n_in = nodes.new('NodeGroupInput')
    n_in.location = (-500, 0)
    n_out = nodes.new('NodeGroupOutput')
    n_out.location = (300, 0)
    clamp = nodes.new('ShaderNodeClamp')
    clamp.location = (-250, 100)
    links.new(n_in.outputs[MASK_VALUE], clamp.inputs['Value'])
    # Disabled -> 1.0, enabled -> the value.
    gate = nodes.new('ShaderNodeMix')
    gate.data_type = 'FLOAT'
    gate.label = "Enable"
    gate.location = (0, 100)
    gate.inputs[2].default_value = 1.0  # A_Float
    links.new(n_in.outputs[MASK_ENABLE], _by_ident(gate.inputs, "Factor_Float"))
    links.new(clamp.outputs['Result'], _by_ident(gate.inputs, "B_Float"))
    links.new(_by_ident(gate.outputs, "Result_Float"), n_out.inputs[MASK_SOCKET])


KEY_PROP = "essence_key"


def ensure_channel_states(mat):
    """One entry per channel in the material's channel list."""
    have = {c.id for c in mat.essence.channels}
    stale = [i for i, c in enumerate(mat.essence.channels) if c.id not in BY_ID]
    if all(c.id in have for c in CHANNELS) and not stale:
        return
    sources.SUSPEND = True
    try:
        for i in reversed(stale):  # channels that no longer exist
            mat.essence.channels.remove(i)
        for ch in CHANNELS:
            if ch.id not in have:
                s = mat.essence.channels.add()
                s.id = ch.id
                s.enabled = ch.default_enabled
    finally:
        sources.SUSPEND = False


# Mesh map slots: an image node per map, named like the bake panel's target node. A bake fills
# it, and the user can pick any other image. The Normal slot feeds the shader (see
# rebuild_stack); AO is read by export; generators read Normal and Position (see
# sources.wire_baked_inputs); more maps come with the mesh-map sprint.
MAP_SLOTS = (('NORMAL', "Normal"), ('AO', "Ambient Occlusion"), ('POSITION', "Position"),
             ('CURVATURE', "Curvature"), ('ID', "ID"), ('THICKNESS', "Thickness"))


def new_paint_image(name, size, rgba, alpha=False):
    """A float image to paint on, filled with `rgba` and really packed. Image.pack() does
    nothing on a generated image that has no pixels of its own, so the pixels are written first:
    otherwise the image would be a generated blank again after a save and a reload, and what
    was painted on it would be gone."""
    import array
    img = bpy.data.images.new(name, size, size, alpha=alpha, float_buffer=True)
    img.colorspace_settings.name = 'Non-Color'     # a mask value, not a color
    # Blender's brush mixes the color with what was there by the stroke's alpha only, which is
    # premultiplied maths, whatever the image is declared as; so the pixels are kept raw (no
    # conversion by Blender in either direction) and a paint item divides by the alpha itself.
    img.alpha_mode = 'CHANNEL_PACKED' if alpha else 'STRAIGHT'
    img[PAINT_KEY] = True       # persist.py packs it again before a save if it was painted on
    img.pixels.foreach_set(array.array('f', rgba) * (size * size))
    img.update()
    img.pack()
    return img


def map_slot_name(map_id):
    return f"Essence_Bake_{map_id}"


def ensure_map_slots(mat):
    tree = mat.node_tree
    for i, (map_id, label) in enumerate(MAP_SLOTS):
        node = tree.nodes.get(map_slot_name(map_id))
        if node is None or node.bl_idname != 'ShaderNodeTexImage':
            node = tree.nodes.new('ShaderNodeTexImage')
            node.name = map_slot_name(map_id)
            node.label = map_slot_name(map_id)
            node.location = (-400, 300 + i * 320)
        if map_id == 'ID':
            # Linear would blend two neighbouring IDs into a color that is neither of them
            if node.interpolation != 'Closest':
                node.interpolation = 'Closest'


def layer_modes(entry):
    """{channel id: blend mode} of a layer entry (only the ones that were set up)."""
    return {s.channel: s.blend for s in entry.sources if s.blend_init and s.blend}


def variant_key(mat, entry):
    """The group key (channel set and blend modes) a layer or folder entry needs."""
    key = {"ch": [c.id for c in active_channels(mat)], "modes": {}}
    if entry.kind == 'LAYER':
        for ch_id, mode in layer_modes(entry).items():
            if ch_id in key["ch"] and mode != BY_ID[ch_id].blend:
                key["modes"][ch_id] = mode
        paint = [c for c in key["ch"] if any(s.channel == c and s.kind == 'PAINT'
                                              for s in entry.sources)]
        if paint:
            key["paint"] = paint
    return key


def variant_name(base, key):
    """A readable group name: the base name plus what differs from a plain material, e.g.
    "Essence Layer +Emission Color:Multiply". Falls back to a hash when it gets too long."""
    parts = []
    for c in CHANNELS:
        on = c.id in key["ch"]
        if on and not c.default_enabled:
            parts.append(f"+{c.label}")
        elif not on and c.default_enabled:
            parts.append(f"-{c.label}")
        if on and key["modes"].get(c.id, c.blend) != c.blend:
            parts.append(f"{c.label}:{blends.MODES[key['modes'][c.id]][0]}")
        if on and c.id in key.get("paint", ()) and set(key["paint"]) != set(key["ch"]):
            parts.append(f"{c.label}:Paint")
    if key.get("paint") and set(key["paint"]) == set(key["ch"]):
        parts.append("Paint")       # a paint layer: every channel painted
    if not parts:
        return base
    name = f"{base} {' '.join(parts)}"
    if len(name) > 60:
        import hashlib
        digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:10]
        name = f"{base} {digest}"
    return name


def _canon(key):
    canon = {"ch": list(key["ch"]), "modes": dict(sorted(key["modes"].items()))}
    if key.get("paint"):    # only then, so every group made before Paint keeps its key
        canon["paint"] = list(key["paint"])
    return json.dumps(canon, sort_keys=True)


def _ensure_group(name, version, folder, key=None):
    ng = bpy.data.node_groups.get(name)
    mask = name == MASK_GROUP_NAME
    canon = None if mask else _canon(key or default_key())
    if ng is not None and ng.get(VERSION_KEY) == version:
        stored = ng.get(KEY_PROP)
        # A group from before variants existed has no key and is the default one.
        if mask or stored == canon or (stored is None and canon == _canon(default_key())):
            return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    saved = _snapshot_values(ng)
    saved_links = _snapshot_links(ng)
    if mask:
        _build_mask_group(ng)
    else:
        _build_group(ng, folder, json.loads(canon))
        ng[KEY_PROP] = canon
    ng[VERSION_KEY] = version
    _restore_values(saved, ng)
    _restore_links(saved_links)
    return ng


def ensure_layer_group(key=None):
    """Return the layer node group for a key, (re)building it if missing or outdated."""
    key = key or default_key()
    return _ensure_group(variant_name(GROUP_NAME, key), GROUP_VERSION, False, key)


def ensure_folder_group(key=None):
    """Return the folder node group for a channel set, (re)building it if missing or
    outdated."""
    key = {"ch": (key or default_key())["ch"], "modes": {}}
    return _ensure_group(variant_name(FOLDER_GROUP_NAME, key), FOLDER_GROUP_VERSION, True, key)


def ensure_mask_group():
    """Return the shared mask node group, (re)building it if missing or outdated."""
    return _ensure_group(MASK_GROUP_NAME, MASK_GROUP_VERSION, False)


def group_for(mat, entry):
    if entry.kind == 'MASK':
        return ensure_mask_group()
    key = variant_key(mat, entry)
    return ensure_folder_group(key) if entry.kind == 'FOLDER' else ensure_layer_group(key)


def swap_group(node, ng):
    """Point a group node at another group, keeping its values and links by socket name.
    Blender matches sockets by position when the group changes, which scrambles them as soon
    as the two groups have different channels."""
    if node.node_tree is ng:
        return
    values = {}
    for s in node.inputs:
        if hasattr(s, "default_value"):
            v = s.default_value
            values[s.name] = tuple(v) if hasattr(v, "__len__") else v
    incoming = [(l.from_socket, l.to_socket.name) for s in node.inputs for l in s.links]
    outgoing = [(l.from_socket.name, l.to_socket) for s in node.outputs for l in s.links]
    tree = node.id_data
    node.node_tree = ng
    for name, v in values.items():
        s = node.inputs.get(name)
        if s is not None:
            try:
                s.default_value = v
            except (TypeError, ValueError):
                pass
    for from_sock, to_name in incoming:
        s = node.inputs.get(to_name)
        if s is not None and not s.is_linked:
            tree.links.new(from_sock, s)
    for from_name, to_sock in outgoing:
        o = node.outputs.get(from_name)
        if o is not None and not to_sock.is_linked:
            tree.links.new(o, to_sock)


def refresh_entry(mat, entry):
    """Give a layer or folder the group variant its channel set and blend modes need."""
    node = mat.node_tree.nodes.get(entry.node_name)
    if node is not None and entry.kind in ('LAYER', 'FOLDER'):
        swap_group(node, group_for(mat, entry))


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


def _restore_values(saved, ng=None):
    """Put the saved values back. Inputs a node did not have before get the group's default:
    an existing node makes a new socket before the interface sets its default, so it would
    start at 0 (a new Opacity of 0 hid every upgraded folder)."""
    had = {}
    for mat_name, node_name, sock_name, value in saved:
        had.setdefault((mat_name, node_name), set()).add(sock_name)
        mat = bpy.data.materials.get(mat_name)
        node = mat.node_tree.nodes.get(node_name) if mat and mat.node_tree else None
        sock = node.inputs.get(sock_name) if node else None
        if sock is not None:
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                pass
    if ng is None:
        return
    defaults = {i.name: i.default_value for i in ng.interface.items_tree
                if getattr(i, "in_out", None) == 'INPUT' and hasattr(i, "default_value")}
    for (mat_name, node_name), names in had.items():
        mat = bpy.data.materials.get(mat_name)
        node = mat.node_tree.nodes.get(node_name) if mat and mat.node_tree else None
        for s in (node.inputs if node is not None else ()):
            if s.name not in names and s.name in defaults and hasattr(s, "default_value"):
                try:
                    s.default_value = defaults[s.name]
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


def ensure_normal_node(tree, bump):
    """The Normal Map node in front of the Bump node's Normal input (created if missing)."""
    nm = tree.nodes.get(NORMAL_NODE_NAME)
    if nm is None:
        nm = tree.nodes.new('ShaderNodeNormalMap')
        nm.name = NORMAL_NODE_NAME
        nm.label = NORMAL_NODE_NAME
        nm.space = 'TANGENT'
    nm.location = (bump.location.x - 250, bump.location.y - 150)
    _clear_links(tree, bump.inputs['Normal'])
    tree.links.new(nm.outputs['Normal'], bump.inputs['Normal'])
    return nm


def apply_normal_map(mat, image_node):
    """Wire a baked tangent-space normal image into the shader.

    image -> Normal Map -> Bump (Normal input) -> BSDF Normal, so the normal map is
    evaluated before the bump (layer height) as Blender requires. Returns the
    Normal Map node, whose Strength is the user-facing slider."""
    tree = mat.node_tree
    bsdf = find_principled(tree)
    bump = ensure_bump_node(tree, bsdf)
    nm = ensure_normal_node(tree, bump)
    image_node.location = (nm.location.x - 320, nm.location.y)
    _clear_links(tree, nm.inputs['Color'])
    tree.links.new(image_node.outputs['Color'], nm.inputs['Color'])
    return nm


def _ao_node(tree, name, idname, loc, **props):
    node = tree.nodes.get(name)
    if node is None or node.bl_idname != idname:
        node = tree.nodes.new(idname)
        node.name = node.label = name
        for k, v in props.items():
            setattr(node, k, v)
    node.location = loc
    return node


def _insert_before(tree, node, a_sock, dst):
    """Put `node` (whose `a_sock` input takes the original value) in front of `dst`. Safe to
    repeat: if `dst` is already fed by `node`, the original source is rediscovered through
    `a_sock`. An unlinked destination hands its default over to `a_sock` the first time."""
    src = None
    if dst.is_linked:
        src = dst.links[0].from_socket
        if src.node == node:
            src = a_sock.links[0].from_socket if a_sock.is_linked else None
            _clear_links(tree, dst)
            if src is not None:
                tree.links.new(src, a_sock)
            return
    else:
        a_sock.default_value = dst.default_value
    _clear_links(tree, a_sock)
    if src is not None:
        tree.links.new(src, a_sock)


def _remove_before(tree, node, a_sock, dst):
    """Undo `_insert_before`: feed `dst` from whatever fed `a_sock` again."""
    if dst.is_linked and dst.links[0].from_node == node:
        src = a_sock.links[0].from_socket if a_sock.is_linked else None
        _clear_links(tree, dst)
        if src is not None:
            tree.links.new(src, dst)


def ao_channel_output(mat):
    """The stack's Ambient Occlusion output when the channel is on, else None."""
    if 'AO' not in {c.id for c in active_channels(mat)}:
        return None
    top = stack_top(mat)
    return top.outputs.get(BY_ID['AO'].label) if top is not None else None


def ao_output(mat):
    """The AO the overlay and export use: the channel times the AO mesh map once `apply_ao`
    has combined them, else the channel alone (None while it is off)."""
    channel = ao_channel_output(mat)
    combine = mat.node_tree.nodes.get(AO_COMBINE_NODE)
    if channel is not None and combine is not None:
        return combine.outputs[0]
    return channel


def apply_ao(mat):
    """Use the AO at the end of the shading tree, like the AO pass of a PBR renderer: it
    darkens Base Color and lowers Specular IOR Level by `lerp(1, AO, Strength)`. The AO is
    the AO mesh map slot's image times the stack's Ambient Occlusion channel (either alone
    when the other is missing). It sits after the layer stack and Export reads the layer
    outputs, so exported maps are not affected. No AO unhooks it again (the nodes and the
    strength stay). Idempotent."""
    tree = mat.node_tree
    bsdf = find_principled(tree)
    slot = tree.nodes.get(map_slot_name('AO'))
    if bsdf is None or slot is None or slot.bl_idname != 'ShaderNodeTexImage':
        return None
    x, y = bsdf.location.x - 250, bsdf.location.y + 450
    strength = tree.nodes.get(AO_STRENGTH_NODE)
    channel = ao_channel_output(mat)
    combine = tree.nodes.get(AO_COMBINE_NODE)
    if channel is None and combine is not None:
        tree.nodes.remove(combine)
    has_image = slot.image is not None or channel is not None
    if not has_image and strength is None:
        return None
    # A Mix node (A=0, B=1) passes its Factor through, so the slider is a real 0 to 1 input.
    # (A Value node's output has no range.)
    fresh = strength is None
    strength = _ao_node(tree, AO_STRENGTH_NODE, 'ShaderNodeMix', (x - 500, y),
                        data_type='FLOAT')
    if fresh:
        _by_ident(strength.inputs, "A_Float").default_value = 0.0
        _by_ident(strength.inputs, "B_Float").default_value = 1.0
        _by_ident(strength.inputs, "Factor_Float").default_value = 1.0
    strength_out = _by_ident(strength.outputs, "Result_Float")
    color = _ao_node(tree, AO_COLOR_NODE, 'ShaderNodeMix', (x, y),
                     data_type='RGBA', blend_type='MULTIPLY')
    factor = _ao_node(tree, AO_SPEC_FACTOR_NODE, 'ShaderNodeMix', (x - 250, y - 250),
                      data_type='FLOAT')
    spec = _ao_node(tree, AO_SPEC_NODE, 'ShaderNodeMath', (x, y - 250), operation='MULTIPLY')
    factor_in = _by_ident(color.inputs, "Factor_Float")
    a_color = _by_ident(color.inputs, "A_Color")
    base_color = bsdf.inputs['Base Color']
    spec_level = bsdf.inputs['Specular IOR Level']
    if not has_image:
        _remove_before(tree, color, a_color, base_color)
        _remove_before(tree, spec, spec.inputs[1], spec_level)
        return strength
    slot.location = (x - 500, y - 250)
    ao = slot.outputs['Color']
    if channel is not None:
        combine = _ao_node(tree, AO_COMBINE_NODE, 'ShaderNodeMath', (x - 500, y - 450),
                           operation='MULTIPLY')
        for sock in combine.inputs[:2]:
            _clear_links(tree, sock)
        if slot.image is not None:
            tree.links.new(slot.outputs['Color'], combine.inputs[0])
        else:
            combine.inputs[0].default_value = 1.0
        tree.links.new(channel, combine.inputs[1])
        ao = combine.outputs[0]
    _insert_before(tree, color, a_color, base_color)
    _insert_before(tree, spec, spec.inputs[1], spec_level)
    b_color = _by_ident(color.inputs, "B_Color")
    for sock in (factor_in, b_color, _by_ident(factor.inputs, "Factor_Float")):
        _clear_links(tree, sock)
    tree.links.new(strength_out, factor_in)
    tree.links.new(ao, b_color)
    tree.links.new(strength_out, _by_ident(factor.inputs, "Factor_Float"))
    _by_ident(factor.inputs, "A_Float").default_value = 1.0
    _clear_links(tree, _by_ident(factor.inputs, "B_Float"))
    tree.links.new(ao, _by_ident(factor.inputs, "B_Float"))
    _clear_links(tree, spec.inputs[0])
    tree.links.new(_by_ident(factor.outputs, "Result_Float"), spec.inputs[0])
    _clear_links(tree, base_color)
    tree.links.new(_by_ident(color.outputs, "Result_Color"), base_color)
    _clear_links(tree, spec_level)
    tree.links.new(spec.outputs[0], spec_level)
    return strength


# While a rebuild wires many sockets, {destination key: source key} of the tree's links.
# `Socket.links` scans every link in the tree, which made a rebuild quadratic in the stack
# size. None outside those phases. Only _set_link and _clear_links may change links while it
# is active.
_LINKS = None


def _key(sock):
    return (sock.node.name, sock.identifier)


def _begin_links(tree):
    global _LINKS
    _LINKS = {_key(l.to_socket): _key(l.from_socket) for l in tree.links}


def _end_links():
    global _LINKS
    _LINKS = None


def _set_link(tree, dst, src):
    """Make `src` the only link into `dst`, touching nothing when it already is (relinking
    an unchanged graph is what made every rebuild slow)."""
    if _LINKS is not None:
        if _LINKS.get(_key(dst)) == _key(src):
            return
        _clear_links(tree, dst)
        tree.links.new(src, dst)
        _LINKS[_key(dst)] = _key(src)
        return
    if dst.is_linked and len(dst.links) == 1 and dst.links[0].from_socket == src:
        return
    _clear_links(tree, dst)
    tree.links.new(src, dst)


def _clear_links(tree, sock):
    if _LINKS is not None:
        if _LINKS.pop(_key(sock), None) is None:
            return
    for l in list(sock.links):
        tree.links.remove(l)


def rebuild_stack(mat):
    """Wire the layer nodes of `mat` into a chain and into the shader. A running preview is
    pointed at the new nodes afterwards (or ended, if what it showed is gone)."""
    _rebuild_stack(mat)
    from . import preview
    preview.refresh(mat)


def stack_top(mat):
    """The group node whose outputs are the stack's result (what the BSDF reads), or None.
    It is the end of the chain `_rebuild_stack` links, not the last stored entry: a folder is
    stored after its children, and a Passthrough folder is not in the chain at all."""
    tree = mat.node_tree
    layers = mat.essence.layers
    by_uid = {e.uid: e for e in layers if e.kind in ('LAYER', 'FOLDER')
              and tree.nodes.get(e.node_name) is not None}
    shape = stack.Tree(layers)

    def end(uids, below):
        prev = below
        for u in uids:
            e = by_uid.get(u)
            if e is None:
                continue
            if e.kind == 'FOLDER' and e.folder_mode == 'PASSTHROUGH':
                prev = end(shape.kids.get(u, []), prev)
            else:
                prev = e
        return prev

    top = end(shape.roots, None)
    return tree.nodes[top.node_name] if top is not None else None


def _rebuild_stack(mat):
    _end_links()  # in case an earlier rebuild stopped on an error
    tree = mat.node_tree
    layers = mat.essence.layers
    ensure_channel_states(mat)
    ensure_map_slots(mat)
    sources.cleanup(tree, {e.uid for e in layers})
    for e in layers:
        if e.kind == 'LAYER' and e.uid:
            sources.ensure_entries(e)
    for e in layers:
        refresh_entry(mat, e)  # the group variant must match the channel set and blend modes
    entries = [(e, tree.nodes.get(e.node_name)) for e in layers
               if e.kind in ('LAYER', 'FOLDER')]
    entries = [(e, n) for e, n in entries if n is not None]
    if not entries:
        apply_ao(mat)
        return

    active = active_channels(mat)
    active_ids = {c.id for c in active}
    bsdf = find_principled(tree)
    bump = ensure_bump_node(tree, bsdf)
    targets = {'BSDF': bsdf, 'BUMP': bump}
    baked = tree.nodes.get(BAKED_NORMAL_NODE)  # the Normal slot of the Mesh Maps
    if baked is not None and (baked.bl_idname != 'ShaderNodeTexImage' or baked.image is None):
        baked = None

    px, py = bsdf.location.x, bsdf.location.y
    bump.location = (px - 250, py - 700)

    # Children chain from the stack below their folder. A Normal folder then lerps between
    # that stack (A) and the end of its children's chain (B); a Passthrough folder leaves the
    # chain to its children and scales their masks by its Factor (see _link_masks).
    def link_chain(node, a_src, b_src=None, clear_b=False):
        for ch in active:
            a = node.inputs[f"{ch.label} A"]
            if a_src is not None:
                _set_link(tree, a, a_src.outputs[ch.label])
            else:
                _clear_links(tree, a)
            if b_src is not None:
                _set_link(tree, node.inputs[f"{ch.label} B"], b_src.outputs[ch.label])
            elif clear_b:
                _clear_links(tree, node.inputs[f"{ch.label} B"])

    count = len(entries)
    for i, (e, node) in enumerate(entries):
        node.location = (px - 300 - (count - i) * 260, py)
    by_uid = {e.uid: (e, n) for e, n in entries}
    shape = stack.Tree(layers)
    through = {}       # child uid -> the group node of its Passthrough folder

    def chain(uids, below):
        prev = below
        for u in uids:
            if u not in by_uid:
                continue
            e, node = by_uid[u]
            if e.kind != 'FOLDER':
                link_chain(node, prev)
                prev = node
                continue
            kids = shape.kids.get(u, [])
            if e.folder_mode == 'PASSTHROUGH':
                for k in kids:
                    through[k] = node
                link_chain(node, None, None, clear_b=True)
                prev = chain(kids, prev)
            else:
                inner = chain(kids, prev)     # an empty folder passes the stack through
                link_chain(node, prev, inner, clear_b=True)
                prev = node
        return prev

    _begin_links(tree)
    prev = chain(shape.roots, None)

    for ch in CHANNELS:
        kind, sock_name = ch.target
        if kind not in targets:
            continue
        dst = targets[kind].inputs[sock_name]
        if ch.id in active_ids and prev is not None:
            _set_link(tree, dst, prev.outputs[ch.label])
        else:
            _clear_links(tree, dst)
    _end_links()
    if baked is not None:
        # The Normal slot: a baked or picked tangent normal map feeds the Normal Map node,
        # which is evaluated before the bump (the layer height).
        nm = ensure_normal_node(tree, bump)
        _clear_links(tree, nm.inputs['Color'])
        tree.links.new(baked.outputs['Color'], nm.inputs['Color'])
    elif tree.nodes.get(NORMAL_NODE_NAME) is not None:
        _clear_links(tree, tree.nodes[NORMAL_NODE_NAME].inputs['Color'])

    apply_ao(mat)
    sources.wire_baked_inputs(tree)

    for m in layers:
        if m.kind in ('MASK', 'FILL') and m.uid and tree.nodes.get(m.node_name) is not None:
            sources.ensure_mask_source(mat, m)
    index = sources.source_index(tree)
    adjs = [a for a in layers if a.kind in stack.ITEM_KINDS]
    _begin_links(tree)
    try:
        for e, node in entries:
            _link_masks(tree, layers, e, node, index, adjs, through.get(e.uid))
            if e.kind == 'LAYER' and e.uid:
                sources.layout(tree, e, node, index, adjs)
    finally:
        _end_links()
    wanted = {FOLDER_FACTOR_PREFIX + u for u in through}
    for n in [n for n in tree.nodes
              if n.name.startswith(FOLDER_FACTOR_PREFIX) and n.name not in wanted]:
        tree.nodes.remove(n)


def refresh_all(mat, changed=()):
    """The channel set changed: move every layer to the right group variant, rebuild the
    sources (a disabled channel loses its source nodes, an enabled one gets them back) and
    rewire. `changed` is the ids of channels that were just switched, whose BSDF defaults
    (like the emission strength) are set to match."""
    tree = mat.node_tree
    if tree is None:
        return
    for e in mat.essence.layers:
        refresh_entry(mat, e)
    for e in mat.essence.layers:
        if e.kind == 'LAYER':
            for ch in CHANNELS:
                sources.apply(mat, e, ch.id)
    if changed and mat.essence.layers:
        bsdf = find_principled(tree)
        on = {c.id for c in active_channels(mat)}
        for ch in CHANNELS:
            if ch.id in changed:
                for sock, on_value, off_value in ch.enable_values:
                    bsdf.inputs[sock].default_value = on_value if ch.id in on else off_value
    rebuild_stack(mat)


def _link_masks(tree, layers, owner, owner_node, index=None, adjs=(), folder=None):
    """Wire the owner's mask node into its Mask input, and lay the mask and its source nodes
    out in a column below the owner. Inside a Passthrough folder (`folder`, its group node)
    the mask is multiplied by the folder's Factor first; nested Passthrough folders compound,
    because a folder's own Mask input gets its parent's Factor the same way."""
    dst = owner_node.inputs[MASK_SOCKET]
    prev = None
    x, y = owner_node.location.x, owner_node.location.y - 420
    for m in layers:
        if m.kind != 'MASK' or m.parent != owner.uid:
            continue
        node = tree.nodes.get(m.node_name)
        if node is None:
            continue
        sources.place(node, x, y)
        if m.source.channel == 'MASK':
            # The source nodes (see sources.py) feed Value; they sit below the mask node.
            y = sources.layout_mask(tree, m, node, index, adjs)
        else:
            image = tree.nodes.get(m.mask_node_name) if m.mask_node_name else None
            value_in = node.inputs[MASK_VALUE]
            if image is not None:
                _set_link(tree, value_in, image.outputs['Color'])
                sources.place(image, x, y - 250)
            else:
                _clear_links(tree, value_in)
            y -= 650
        prev = node
    src = prev.outputs[MASK_SOCKET] if prev is not None else None
    if folder is not None:
        name = FOLDER_FACTOR_PREFIX + owner.uid
        mul = tree.nodes.get(name)
        if mul is None:
            mul = tree.nodes.new('ShaderNodeMath')
            mul.name = name
            mul.operation = 'MULTIPLY'
            mul.label = "Folder Factor"
            mul.inputs[0].default_value = 1.0   # no mask of its own: the folder's alone
            mul.hide = True
        mul.location = (owner_node.location.x - 200, owner_node.location.y - 300)
        if src is not None:
            _set_link(tree, mul.inputs[0], src)
        else:
            _clear_links(tree, mul.inputs[0])
        _set_link(tree, mul.inputs[1], folder.outputs[FACTOR])
        src = mul.outputs[0]
    if src is not None:
        _set_link(tree, dst, src)
    else:
        _clear_links(tree, dst)


def add_layer_node(mat, name, folder=False):
    tree = ensure_tree(mat)
    node = tree.nodes.new('ShaderNodeGroup')
    key = {"ch": [c.id for c in active_channels(mat)], "modes": {}}
    node.node_tree = ensure_folder_group(key) if folder else ensure_layer_group(key)
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
