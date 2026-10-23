"""Layer sources: what drives a channel's B input.

A channel can be a plain value (the B socket's own value), an image or a procedural. The
image and procedural cases are ordinary shader nodes placed next to the layer node and wired
into its B input:

    Texture Coordinate -> Mapping -> Image Texture / Procedural group -> [Map Range | Color Ramp] -> B

Every node made here carries two custom properties, `essence_source` ("<layer uid>:<channel id>")
and `essence_role`, so they can be found again however the user renames or moves them. Nothing
here is hidden: the user can edit, replace or extend these nodes in the node editor.
"""

import bpy

from .channels import CHANNELS, SOURCE_BY_ID, active_channels

SRC_KEY = "essence_source"
ROLE_KEY = "essence_role"
PROC_VERSION_KEY = "essence_version"
PROC_VERSION = 1

KINDS = [('VALUE', "Value", "A plain value", 'NONE', 0),
         ('IMAGE', "Image", "An image texture", 'NONE', 1),
         ('PROCEDURAL', "Procedural", "A procedural texture", 'NONE', 2)]


def kind_items(src):
    return KINDS
PROJECTIONS = [('UV', "UV", "Use the UV map"),
               ('TRIPLANAR', "Triplanar",
                "Project along the object's axes and blend the sides, with no UV map needed. "
                "Procedurals are 3D already, so for them this equals Object"),
               ('OBJECT', "Object", "Use the object's local coordinates")]

# id -> (label, node type, node properties, [(input socket, default)], output socket)
PROCEDURALS = {
    'NOISE': ("Noise", 'ShaderNodeTexNoise',
              {'noise_dimensions': '3D', 'noise_type': 'FBM', 'normalize': True},
              [("Scale", 5.0), ("Detail", 2.0), ("Roughness", 0.5), ("Distortion", 0.0)],
              "Factor"),
    'VORONOI': ("Voronoi", 'ShaderNodeTexVoronoi',
                {'voronoi_dimensions': '3D', 'feature': 'F1'},
                [("Scale", 5.0), ("Randomness", 1.0)], "Distance"),
    'WAVE': ("Wave", 'ShaderNodeTexWave',
             {'wave_type': 'BANDS'},
             [("Scale", 5.0), ("Distortion", 0.0), ("Detail", 0.0)], "Factor"),
    'CHECKER': ("Checker", 'ShaderNodeTexChecker', {}, [("Scale", 5.0)], "Factor"),
    'GRADIENT': ("Gradient", 'ShaderNodeTexGradient', {'gradient_type': 'LINEAR'}, [],
                 "Factor"),
}
PROC_ITEMS = [(k, v[0], "") for k, v in PROCEDURALS.items()]


def proc_group_name(proc_id):
    return f"Essence Proc {PROCEDURALS[proc_id][0]}"


# --------------------------------------------------------------------------
# Procedural node groups
# --------------------------------------------------------------------------

def _build_proc_group(ng, proc_id):
    """A procedural wrapped in a uniform interface: Vector, its own controls, Contrast, and a
    single Fac output (0 to 1). The contrast pivots around 0.5 and clamps."""
    label, idname, props, extras, out_name = PROCEDURALS[proc_id]
    iface = ng.interface
    iface.clear()
    ng.nodes.clear()
    iface.new_socket("Vector", in_out='INPUT', socket_type='NodeSocketVector')
    for name, default in extras:
        s = iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
        s.default_value = default
    c = iface.new_socket("Contrast", in_out='INPUT', socket_type='NodeSocketFloat')
    c.default_value = 1.0
    c.min_value = 0.0
    c.max_value = 10.0
    iface.new_socket("Fac", in_out='OUTPUT', socket_type='NodeSocketFloat')

    nodes, links = ng.nodes, ng.links
    n_in = nodes.new('NodeGroupInput')
    n_in.location = (-700, 0)
    n_out = nodes.new('NodeGroupOutput')
    n_out.location = (500, 0)
    tex = nodes.new(idname)
    tex.location = (-400, 0)
    for k, v in props.items():
        setattr(tex, k, v)
    links.new(n_in.outputs["Vector"], tex.inputs["Vector"])
    for name, _ in extras:
        links.new(n_in.outputs[name], tex.inputs[name])
    centered = nodes.new('ShaderNodeMath')
    centered.operation = 'SUBTRACT'
    centered.inputs[1].default_value = 0.5
    centered.location = (-150, 0)
    links.new(tex.outputs[out_name], centered.inputs[0])
    contrast = nodes.new('ShaderNodeMath')
    contrast.operation = 'MULTIPLY_ADD'
    contrast.use_clamp = True
    contrast.inputs[2].default_value = 0.5
    contrast.label = "Contrast"
    contrast.location = (150, 0)
    links.new(centered.outputs[0], contrast.inputs[0])
    links.new(n_in.outputs["Contrast"], contrast.inputs[1])
    links.new(contrast.outputs[0], n_out.inputs["Fac"])


def ensure_proc_group(proc_id):
    name = proc_group_name(proc_id)
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get(PROC_VERSION_KEY) == PROC_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    _build_proc_group(ng, proc_id)
    ng[PROC_VERSION_KEY] = PROC_VERSION
    return ng


# --------------------------------------------------------------------------
# Source entries and node lookup
# --------------------------------------------------------------------------

def _set_proc_group(node, proc_id):
    """Point a group node at a procedural and reset its inputs to that procedural's defaults.
    Blender matches sockets by position when the group changes, so values of the previous
    type would otherwise leak into the new one (a Noise scale becoming a Contrast)."""
    ng = ensure_proc_group(proc_id)
    node.node_tree = ng
    for item in ng.interface.items_tree:
        if item.in_out == 'INPUT' and item.socket_type == 'NodeSocketFloat':
            sock = node.inputs.get(item.name)
            if sock is not None:
                sock.default_value = item.default_value


def tag(uid, ch_id):
    return f"{uid}:{ch_id}"


# True while Essence itself sets source properties, so their update callbacks stay quiet.
SUSPEND = False


def get_source(entry, ch_id):
    if ch_id == 'MASK':
        return entry.source if entry.source.channel == 'MASK' else None
    return next((s for s in entry.sources if s.channel == ch_id), None)


def ensure_entries(entry):
    """Give a layer entry a source entry per channel. Returns True if anything was added."""
    changed = False
    for ch in CHANNELS:
        if get_source(entry, ch.id) is None:
            s = entry.sources.add()
            s.channel = ch.id
            s.uid = entry.uid
            changed = True
    for i in reversed(range(len(entry.sources))):  # channels that no longer exist
        if entry.sources[i].channel not in SOURCE_BY_ID:
            entry.sources.remove(i)
            changed = True
    global SUSPEND
    SUSPEND = True
    try:
        for s in entry.sources:  # layers from before blend modes get their channel default
            if not s.blend_init and s.channel in SOURCE_BY_ID:
                s.blend = SOURCE_BY_ID[s.channel].blend
                s.blend_init = True
                changed = True
    finally:
        SUSPEND = False
    return changed


def source_nodes(tree, uid, ch_id):
    """{role: node} of the source nodes of one channel of one layer."""
    key = tag(uid, ch_id)
    return {n[ROLE_KEY]: n for n in tree.nodes if n.get(SRC_KEY) == key and ROLE_KEY in n}


def remove_nodes(tree, uid, ch_id=None):
    for n in list(tree.nodes):
        key = str(n.get(SRC_KEY, ""))
        if key.split(":")[0] == uid and (ch_id is None or key == tag(uid, ch_id)):
            tree.nodes.remove(n)


def cleanup(tree, live_uids):
    """Delete source nodes whose layer no longer exists."""
    for n in [n for n in tree.nodes if SRC_KEY in n
              and str(n[SRC_KEY]).split(":")[0] not in live_uids]:
        tree.nodes.remove(n)


# --------------------------------------------------------------------------
# Building a channel's source
# --------------------------------------------------------------------------

def _wanted(src, ch):
    """{role: node idname} the source needs, in chain order."""
    if src.kind == 'VALUE':
        return {}
    roles = {'COORD': 'ShaderNodeTexCoord', 'MAPPING': 'ShaderNodeMapping'}
    roles['TEXTURE'] = 'ShaderNodeTexImage' if src.kind == 'IMAGE' else 'ShaderNodeGroup'
    if ch.socket_type == 'NodeSocketFloat':
        roles['REMAP'] = 'ShaderNodeMapRange'
    elif src.kind == 'PROCEDURAL':
        roles['RAMP'] = 'ShaderNodeValToRGB'
    return roles


def _clear_links(tree, sock):
    for l in list(sock.links):
        tree.links.remove(l)


def _discard(tree, node, src):
    """Remove a source node. An image it held is remembered on the source and kept alive, so
    switching a channel or mask away from Image and back never loses (painted) pixels."""
    if node.bl_idname == 'ShaderNodeTexImage' and node.image is not None:
        node.image.use_fake_user = True
        src.image_memory = node.image
    tree.nodes.remove(node)


def apply(mat, entry, ch_id):
    """Reconcile the source nodes of one channel (or of a mask, `ch_id` 'MASK') with its
    settings: create what is missing, delete what no longer fits, set the projection and the
    wiring. Settings the user changed on nodes that stay (mapping, image, procedural values)
    are kept."""
    tree = mat.node_tree
    ch = SOURCE_BY_ID[ch_id]
    is_mask = ch_id == 'MASK'
    src = get_source(entry, ch_id)
    layer_node = tree.nodes.get(entry.node_name)
    if src is None or layer_node is None:
        return
    if not is_mask and ch_id not in {c.id for c in active_channels(mat)}:
        remove_nodes(tree, entry.uid, ch_id)  # a switched-off channel has no source nodes
        return
    wanted = _wanted(src, ch)
    have = source_nodes(tree, entry.uid, ch_id)

    switched = False
    for role, node in list(have.items()):
        if role not in wanted or node.bl_idname != wanted[role]:
            _discard(tree, node, src)
            del have[role]
            switched = True
    if switched:
        # The range of a Map Range is tied to the kind of source, so start fresh.
        for role in ('REMAP', 'RAMP'):
            if role in have:
                tree.nodes.remove(have.pop(role))

    b_in = layer_node.inputs["Value" if is_mask else f"{ch.label} B"]
    if not wanted:
        if is_mask:
            entry.mask_node_name = ""
        return

    key = tag(entry.uid, ch_id)
    for role, idname in wanted.items():
        if role in have:
            continue
        n = tree.nodes.new(idname)
        n[SRC_KEY] = key
        n[ROLE_KEY] = role
        n.label = f"{entry.name} {ch.label} {role.title()}"
        n.width = 240
        have[role] = n
        if role == 'TEXTURE' and src.kind == 'PROCEDURAL':
            _set_proc_group(n, src.proc_type)
        elif role == 'TEXTURE':
            n.interpolation = 'Linear'
            if src.image_memory is not None:
                n.image = src.image_memory
        elif role == 'REMAP':
            lo, hi = ch.image_range if src.kind == 'IMAGE' else ch.proc_range
            n.data_type = 'FLOAT'
            n.clamp = True
            n.inputs['To Min'].default_value = lo
            n.inputs['To Max'].default_value = hi
        elif role == 'RAMP':
            els = n.color_ramp.elements
            els[0].color = (0.0, 0.0, 0.0, 1.0)
            els[1].color = ch.ramp_color or ch.default

    tex = have['TEXTURE']
    if src.kind == 'PROCEDURAL' and tex.node_tree is not ensure_proc_group(src.proc_type):
        _set_proc_group(tex, src.proc_type)
    if src.kind == 'IMAGE':
        tex.projection = 'BOX' if src.projection == 'TRIPLANAR' else 'FLAT'
        tex.projection_blend = 0.2

    # Wiring. Only links between the nodes made here are replaced.
    coord = 'UV' if src.projection == 'UV' else 'Object'
    links = tree.links
    _clear_links(tree, have['MAPPING'].inputs['Vector'])
    links.new(have['COORD'].outputs[coord], have['MAPPING'].inputs['Vector'])
    _clear_links(tree, tex.inputs['Vector'])
    links.new(have['MAPPING'].outputs['Vector'], tex.inputs['Vector'])

    if src.kind == 'IMAGE':
        out = tex.outputs['Color']
    else:
        out = tex.outputs['Fac']
    if 'REMAP' in have:
        _clear_links(tree, have['REMAP'].inputs['Value'])
        links.new(out, have['REMAP'].inputs['Value'])
        out = have['REMAP'].outputs['Result']
    elif 'RAMP' in have:
        _clear_links(tree, have['RAMP'].inputs['Fac'])
        links.new(out, have['RAMP'].inputs['Fac'])
        out = have['RAMP'].outputs['Color']
    _clear_links(tree, b_in)
    links.new(out, b_in)
    if is_mask:
        entry.mask_node_name = tex.name if src.kind == 'IMAGE' else ""
    else:
        layout(tree, entry, layer_node)


_ROLE_ORDER = ('COORD', 'MAPPING', 'TEXTURE', 'REMAP', 'RAMP')
_STEP = {'COORD': 230, 'MAPPING': 330, 'TEXTURE': 330, 'REMAP': 290, 'RAMP': 320}


def layout(tree, entry, layer_node):
    """Stack the source nodes of a layer in a column above its layer node."""
    x = layer_node.location.x
    y = layer_node.location.y + 480
    for ch in CHANNELS:
        nodes = source_nodes(tree, entry.uid, ch.id)
        for role in _ROLE_ORDER:
            n = nodes.get(role)
            if n is None:
                continue
            n.location = (x, y)
            y += _STEP[role]
        if nodes:
            y += 60


def layout_mask(tree, entry, mask_node):
    """Stack a mask's source nodes in a column below its mask node. Returns the y where the
    next mask can start."""
    x = mask_node.location.x
    y = mask_node.location.y - 320
    for role in _ROLE_ORDER:
        n = source_nodes(tree, entry.uid, 'MASK').get(role)
        if n is not None:
            n.location = (x, y)
            y -= _STEP[role]
    return y - 80


def ensure_mask_source(mat, entry):
    """Give a mask entry its source. A new mask has its image node linked straight into the
    mask group; that node becomes the Image source's texture node."""
    src = entry.source
    if src.channel == 'MASK':
        return False
    global SUSPEND
    SUSPEND = True
    try:
        src.channel = 'MASK'
        src.uid = entry.uid
        img = mat.node_tree.nodes.get(entry.mask_node_name) if entry.mask_node_name else None
        if img is not None and img.bl_idname == 'ShaderNodeTexImage':
            img[SRC_KEY] = tag(entry.uid, 'MASK')
            img[ROLE_KEY] = 'TEXTURE'
            src.kind = 'IMAGE'
        else:
            src.kind = 'VALUE'
    finally:
        SUSPEND = False
    apply(mat, entry, 'MASK')
    return True
