"""Layer sources: what drives a channel's B input.

A channel can be a plain value (the B socket's own value), an image, a procedural or a
generator. The image and procedural cases are ordinary shader nodes placed next to the layer
node and wired into its B input:

    Texture Coordinate -> Mapping -> Image Texture / Procedural group -> [Map Range | Color Ramp] -> B

A generator (see generators.py) reads the mesh itself, so it has no coordinates or mapping:

    Generator group -> [Map Range | Color Ramp] -> B

Every node made here carries two custom properties, `essence_source` ("<layer uid>:<channel id>")
and `essence_role`, so they can be found again however the user renames or moves them. Nothing
here is hidden: the user can edit, replace or extend these nodes in the node editor.
"""

import bpy

from . import adjustments, generators
from .channels import CHANNELS, SOURCE_BY_ID, active_channels

SRC_KEY = "essence_source"
ROLE_KEY = "essence_role"
PROC_VERSION_KEY = "essence_version"
PROC_VERSION = 1

KINDS = [('VALUE', "Value", "A plain value", 'NONE', 0),
         ('IMAGE', "Image", "An image texture", 'NONE', 1),
         ('PROCEDURAL', "Procedural", "A procedural texture", 'NONE', 2),
         ('GENERATOR', "Generator",
          "A live generator that reacts to the mesh: light direction, slope, gradients",
          'NONE', 3)]


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


def _set_gen_group(node, gen_id):
    """Point a group node at a generator and reset its inputs to that generator's defaults
    (see `_set_proc_group` for why)."""
    ng = generators.ensure_group(gen_id)
    node.node_tree = ng
    for item in ng.interface.items_tree:
        if item.in_out == 'INPUT' and item.name != generators.DETAIL_INPUT:
            sock = node.inputs.get(item.name)
            if sock is not None:
                sock.default_value = item.default_value


DETAIL_FALLBACK = "Essence Geometry Normal"


def wire_detail_normals(tree):
    """Feed the Essence Normal Map node's output (the world space normal with the baked detail)
    into every generator's `Detail Normal` input. Always linked: the generator's own switch
    decides whether it is used. Nothing depends on a layer, so this cannot make a cycle. With
    an empty Normal slot there is no Normal Map node, and the detail is just the surface
    normal: a Geometry node stands in for it."""
    from . import nodes
    users = [n.inputs[generators.DETAIL_INPUT] for n in tree.nodes
             if n.bl_idname == 'ShaderNodeGroup' and n.node_tree is not None
             and generators.DETAIL_INPUT in n.inputs]
    fallback = tree.nodes.get(DETAIL_FALLBACK)
    nm = tree.nodes.get(nodes.NORMAL_NODE_NAME)
    if nm is not None or not users:
        if fallback is not None:
            tree.nodes.remove(fallback)
        if not users:
            return
        out = nm.outputs['Normal']
    else:
        if fallback is None:
            fallback = tree.nodes.new('ShaderNodeNewGeometry')
            fallback.name = fallback.label = DETAIL_FALLBACK
            x, y = users[0].node.location
            fallback.location = (x - 300, y - 200)
        out = fallback.outputs['Normal']
    for sock in users:
        if not (sock.is_linked and sock.links[0].from_socket == out):
            _clear_links(tree, sock)
            tree.links.new(out, sock)


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


def source_index(tree):
    """{tag: {role: node}} for every source node, built in one pass over the tree. A rebuild
    lays out every layer, so it builds this once instead of scanning per channel."""
    index = {}
    for n in tree.nodes:
        key = n.get(SRC_KEY)
        if key is not None and ROLE_KEY in n:
            index.setdefault(key, {})[n[ROLE_KEY]] = n
    return index


def place(node, x, y):
    """Move a node only when it is not there already (a write costs a UI update)."""
    loc = node.location
    if loc.x != x or loc.y != y:
        node.location = (x, y)


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

def _wanted(src, ch, adjusted=False):
    """{role: node idname} the source needs, in chain order. A Value source only has nodes
    when an adjustment needs something to adjust: a constant (CONST) in the channel's type."""
    if src.kind == 'VALUE':
        if not adjusted:
            return {}
        return {'CONST': 'ShaderNodeValue' if ch.socket_type == 'NodeSocketFloat'
                else 'ShaderNodeRGB'}
    roles = ({} if src.kind == 'GENERATOR'
             else {'COORD': 'ShaderNodeTexCoord', 'MAPPING': 'ShaderNodeMapping'})
    roles['TEXTURE'] = 'ShaderNodeTexImage' if src.kind == 'IMAGE' else 'ShaderNodeGroup'
    if ch.socket_type == 'NodeSocketFloat':
        roles['REMAP'] = 'ShaderNodeMapRange'
    elif src.kind in ('PROCEDURAL', 'GENERATOR'):
        roles['RAMP'] = 'ShaderNodeValToRGB'
    return roles


def _clear_links(tree, sock):
    for l in list(sock.links):
        tree.links.remove(l)


def apply_owner(mat, owner):
    """Re-apply the sources of a layer (every channel) or a mask, after its adjustments
    changed."""
    if owner.kind == 'MASK':
        for f in adjustment_entries(mat, owner.uid, 'MASK'):  # fills feed their blend node first
            if f.kind == 'FILL':
                ensure_mask_source(mat, f)
                apply(mat, f, 'MASK')
        apply(mat, owner, 'MASK')
    elif owner.kind == 'LAYER':
        for ch in CHANNELS:
            apply(mat, owner, ch.id)


def adjustment_entries(mat, owner_uid, ch_id):
    """The adjustments of an owner for one channel (all of a mask's), bottom-to-top."""
    return [a for a in mat.essence.layers if a.parent == owner_uid and (
            (a.kind in ('ADJUSTMENT', 'FILTER') and (ch_id == 'MASK' or a.adj_channel == ch_id))
            or (a.kind == 'FILL' and ch_id == 'MASK'))]


def _chain_fill(tree, a, out):
    """Wire a fill into a mask's chain: blend(value so far, the fill's source), then the
    Opacity mix. The fill's own source nodes are wired by `apply` on the fill."""
    have = source_nodes(tree, a.uid, adjustments.ADJ_KEY)
    main, mix = have.get('MAIN'), have.get('MIX')
    if main is None:
        main = adjustments.new_fill_main(tree)
        main[SRC_KEY] = tag(a.uid, adjustments.ADJ_KEY)
        main[ROLE_KEY] = 'MAIN'
        main.label = f"{a.name} Blend"
        a.node_name = main.name
    if mix is None:
        mix = adjustments.new_mix(tree)
        mix[SRC_KEY] = tag(a.uid, adjustments.ADJ_KEY)
        mix[ROLE_KEY] = 'MIX'
        mix.width = 240
    mix.label = f"{a.name} Opacity"
    normal = a.fill_blend == 'NORMAL'
    # Normal: the blend node passes the fill through (0 + fill), whatever is below.
    main.operation = 'ADD' if normal else a.fill_blend
    a_in = adjustments.mix_socket(mix, "A_Color")
    b_in = adjustments.mix_socket(mix, "B_Color")
    for sock in (a_in, b_in, main.inputs[0]):
        _clear_links(tree, sock)
    main.inputs[0].default_value = 0.0
    if out is not None and a.adj_enabled:
        if not normal:
            tree.links.new(out, main.inputs[0])
        tree.links.new(out, a_in)
        tree.links.new(main.outputs[0], b_in)
        out = adjustments.mix_output(mix)
    return out


def _chain_filter(tree, a, out):
    """A baked filter in a mask's or a layer channel's chain. The value so far goes into a reroute (the point that
    Apply bakes); once a result image exists the chain continues from that image sampled
    with the UV map, before that the filter passes the value through."""
    have = source_nodes(tree, a.uid, adjustments.ADJ_KEY)
    node_in, tex = have.get('IN'), have.get('OUT')
    if node_in is None:
        node_in = tree.nodes.new('NodeReroute')
        node_in[SRC_KEY] = tag(a.uid, adjustments.ADJ_KEY)
        node_in[ROLE_KEY] = 'IN'
    if tex is None:
        tex = tree.nodes.new('ShaderNodeTexImage')
        tex[SRC_KEY] = tag(a.uid, adjustments.ADJ_KEY)
        tex[ROLE_KEY] = 'OUT'
        tex.interpolation = 'Linear'
        tex.width = 240
    # Repeat, like every other texture: islands moved to another UV tile show the same
    # filtered result as the island in the 0-1 tile.
    tex.extension = 'REPEAT'
    tex.label = f"{a.name} (baked)"
    img = bpy.data.images.get(adjustments.FILTER_IMAGE_PREFIX + a.uid)
    if img is not None and tex.image is not img:
        tex.image = img
    _clear_links(tree, node_in.inputs[0])
    if out is not None:
        tree.links.new(out, node_in.inputs[0])
        if a.adj_enabled and tex.image is not None:
            out = tex.outputs['Color']
    return out


def _chain(tree, adjs, out):
    """Create or update the nodes of `adjs` and wire the enabled ones in series after `out`
    (None: nothing to feed, so every adjustment stays unlinked). Returns the new end."""
    for a in adjs:
        if a.kind == 'FILL':
            out = _chain_fill(tree, a, out)
            continue
        if a.kind == 'FILTER':
            out = _chain_filter(tree, a, out)
            continue
        t = adjustments.BY_ID[a.adj_type]
        have = source_nodes(tree, a.uid, adjustments.ADJ_KEY)
        main = have.get('MAIN')
        if main is not None and (main.bl_idname != t.idname
                                 or (t.id == 'LEVELS' and main.node_tree is not
                                     adjustments.levels_group())
                                 or main.get('essence_adj_type') != t.id):
            tree.nodes.remove(main)
            main = None
        if main is None:
            main = adjustments.new_main(tree, t)
            main[SRC_KEY] = tag(a.uid, adjustments.ADJ_KEY)
            main[ROLE_KEY] = 'MAIN'
            main['essence_adj_type'] = t.id
            main.width = 240
            main.label = f"{a.name}"
        mix = have.get('MIX')
        if mix is None:
            mix = adjustments.new_mix(tree)
            mix[SRC_KEY] = tag(a.uid, adjustments.ADJ_KEY)
            mix[ROLE_KEY] = 'MIX'
            mix.width = 240
        mix.label = f"{a.name} Opacity"
        a_in = adjustments.mix_socket(mix, "A_Color")
        b_in = adjustments.mix_socket(mix, "B_Color")
        main_in = main.inputs[t.input]
        for sock in (a_in, b_in, main_in):
            _clear_links(tree, sock)
        if out is not None and a.adj_enabled:
            tree.links.new(out, main_in)
            tree.links.new(out, a_in)
            tree.links.new(main.outputs[t.output], b_in)
            out = adjustments.mix_output(mix)
    return out


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
    adjs = adjustment_entries(mat, entry.uid, ch_id)
    wanted = _wanted(src, ch, any(a.adj_enabled for a in adjs))
    have = source_nodes(tree, entry.uid, ch_id)

    switched = False
    # A mask's value goes to its group's Value; a fill's to the second input of its blend node.
    b_in = (layer_node.inputs[1] if entry.kind == 'FILL'
            else layer_node.inputs["Value" if is_mask else f"{ch.label} B"])
    for role, node in list(have.items()):
        if role not in wanted or node.bl_idname != wanted[role]:
            if role == 'CONST':  # the value goes back to the socket it was adjusting
                b_in.default_value = node.outputs[0].default_value
            _discard(tree, node, src)
            del have[role]
            switched = True
    if switched:
        # The range of a Map Range is tied to the kind of source, so start fresh.
        for role in ('REMAP', 'RAMP'):
            if role in have:
                tree.nodes.remove(have.pop(role))

    if not wanted:
        for l in list(b_in.links):  # a leftover link from an adjustment chain
            if SRC_KEY in l.from_node:
                tree.links.remove(l)
        _chain(tree, adjs, None)
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
        elif role == 'TEXTURE' and src.kind == 'GENERATOR':
            _set_gen_group(n, src.gen_type)
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
        elif role == 'CONST':
            n.outputs[0].default_value = (tuple(b_in.default_value)
                                          if ch.socket_type != 'NodeSocketFloat'
                                          else b_in.default_value)

    if src.kind == 'VALUE':  # only here with an adjustment to feed
        out = _chain(tree, adjs, have['CONST'].outputs[0])
        _clear_links(tree, b_in)
        links = tree.links
        links.new(out, b_in)
        if is_mask:
            entry.mask_node_name = ""
        else:
            layout(tree, entry, layer_node)
        return

    tex = have['TEXTURE']
    if src.kind == 'PROCEDURAL' and tex.node_tree is not ensure_proc_group(src.proc_type):
        _set_proc_group(tex, src.proc_type)
    if src.kind == 'GENERATOR':
        if tex.node_tree is not generators.ensure_group(src.gen_type):
            _set_gen_group(tex, src.gen_type)
        wire_detail_normals(tree)
    if src.kind == 'IMAGE':
        tex.projection = 'BOX' if src.projection == 'TRIPLANAR' else 'FLAT'
        tex.projection_blend = 0.2

    # Wiring. Only links between the nodes made here are replaced.
    links = tree.links
    if src.kind != 'GENERATOR':
        coord = 'UV' if src.projection == 'UV' else 'Object'
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
    out = _chain(tree, adjs, out)
    _clear_links(tree, b_in)
    links.new(out, b_in)
    if is_mask:
        entry.mask_node_name = tex.name if src.kind == 'IMAGE' else ""
    else:
        layout(tree, entry, layer_node)


_ROLE_ORDER = ('CONST', 'COORD', 'MAPPING', 'TEXTURE', 'REMAP', 'RAMP')
_STEP = {'CONST': 200, 'COORD': 230, 'MAPPING': 330, 'TEXTURE': 330, 'REMAP': 290,
         'RAMP': 320, 'MAIN': 330, 'MIX': 250, 'IN': 60, 'OUT': 330}


def _place_adjustments(tree, adjs, index, x, y, step):
    """Stack the nodes of the adjustments `adjs` from (x, y) in direction `step` (+1 up the
    graph, -1 down). Returns the next free y."""
    for a in adjs:
        nodes = (index.get(tag(a.uid, adjustments.ADJ_KEY), {}) if index is not None
                 else source_nodes(tree, a.uid, adjustments.ADJ_KEY))
        for role in ('MAIN', 'MIX', 'IN', 'OUT'):
            n = nodes.get(role)
            if n is not None:
                place(n, x, y)
                y += step * _STEP[role]
        if a.kind == 'FILL':  # the fill's own source nodes continue the column
            src_nodes = (index.get(tag(a.uid, 'MASK'), {}) if index is not None
                         else source_nodes(tree, a.uid, 'MASK'))
            for role in _ROLE_ORDER:
                n = src_nodes.get(role)
                if n is not None:
                    place(n, x, y)
                    y += step * _STEP[role]
    return y


def layout(tree, entry, layer_node, index=None, adjs=()):
    """Stack the source nodes of a layer in a column above its layer node. `adjs` are the
    adjustment entries of the whole stack: each channel's go after that channel's source."""
    x = layer_node.location.x
    y = layer_node.location.y + 480
    for ch in CHANNELS:
        nodes = (index.get(tag(entry.uid, ch.id), {}) if index is not None
                 else source_nodes(tree, entry.uid, ch.id))
        for role in _ROLE_ORDER:
            n = nodes.get(role)
            if n is None:
                continue
            place(n, x, y)
            y += _STEP[role]
        mine = [a for a in adjs if a.parent == entry.uid and a.adj_channel == ch.id]
        if mine:
            y = _place_adjustments(tree, mine, index, x, y, 1)
        if nodes or mine:
            y += 60


def layout_mask(tree, entry, mask_node, index=None, adjs=()):
    """Stack a mask's source nodes in a column below its mask node. Returns the y where the
    next mask can start."""
    x = mask_node.location.x
    y = mask_node.location.y - 320
    nodes = (index.get(tag(entry.uid, 'MASK'), {}) if index is not None
             else source_nodes(tree, entry.uid, 'MASK'))
    for role in _ROLE_ORDER:
        n = nodes.get(role)
        if n is not None:
            place(n, x, y)
            y -= _STEP[role]
    mine = [a for a in adjs if a.parent == entry.uid]
    if mine:
        y = _place_adjustments(tree, mine, index, x, y, -1)
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
