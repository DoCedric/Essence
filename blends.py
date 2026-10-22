"""Blend modes for the layer group.

Every mode has the same shape: `result = lerp(A, f(A, B), factor)`, where `f` is the mode's
formula and `factor` is mask * opacity * enable. Mix is `f = B`, so a mode only has to say
what `f` is. Color channels use the Mix node's own blend types (which also does the lerp),
scalar channels (roughness, metallic, height, alpha) use Math nodes, and normal channels
decode the 0-1 color to a vector, combine and encode again.

`build` adds the nodes of one channel's blend to a node group and returns the result socket.
"""

# id -> (label, enum number, description)
MODES = {
    'MIX': ("Normal", 0, "The layer replaces what is below, by the mask"),
    'ADD': ("Add", 1, "Add the layer to what is below (Linear Dodge)"),
    'MULTIPLY': ("Multiply", 2, "Darken by multiplying with what is below"),
    'DIVIDE': ("Divide", 3, "Divide what is below by the layer"),
    'SUBTRACT': ("Subtract", 4, "Subtract the layer from what is below"),
    'DIFFERENCE': ("Difference", 5, "Absolute difference with what is below"),
    'EXCLUSION': ("Exclusion", 6, "Like Difference, with lower contrast"),
    'WHITEOUT': ("Combine (Whiteout)", 7,
                 "Add the layer's slopes to the normal below (Normal Map Combine)"),
    'DETAIL': ("Detail (RNM)", 8,
               "Lay the layer's normal over the normal below, following its surface "
               "(Reoriented Normal Mapping)"),
}

_SIMPLE = ('MIX', 'ADD', 'MULTIPLY', 'DIVIDE', 'SUBTRACT', 'DIFFERENCE', 'EXCLUSION')
_NORMAL = ('MIX', 'WHITEOUT', 'DETAIL')
_cache = {}


def family(ch):
    if ch.id == 'NORMAL':
        return 'NORMAL'
    return 'COLOR' if ch.socket_type == 'NodeSocketColor' else 'SCALAR'


def modes_for(ch):
    """Mode ids a channel offers, its default first."""
    ids = _NORMAL if family(ch) == 'NORMAL' else _SIMPLE
    return [ch.blend] + [m for m in ids if m != ch.blend] if ch.blend in ids else list(ids)


def items_for(ch):
    """Enum items for a channel. The list is cached because Blender keeps pointers into it."""
    if ch.id not in _cache:
        _cache[ch.id] = [(m, MODES[m][0], MODES[m][2], 'NONE', MODES[m][1])
                         for m in modes_for(ch)]
    return _cache[ch.id]


def _ident(sockets, ident):
    for s in sockets:
        if s.identifier == ident:
            return s
    raise KeyError(ident)


def _mix(nodes, links, data_type, a, b, factor, loc, blend_type='MIX'):
    n = nodes.new('ShaderNodeMix')
    n.data_type = data_type
    n.blend_type = blend_type
    n.clamp_result = data_type == 'RGBA'  # colors stay in 0-1 (Subtract would go negative)
    n.location = loc
    suffix = 'Color' if data_type == 'RGBA' else 'Float'
    links.new(factor, _ident(n.inputs, "Factor_Float"))
    links.new(a, _ident(n.inputs, f"A_{suffix}"))
    links.new(b, _ident(n.inputs, f"B_{suffix}"))
    return n, _ident(n.outputs, f"Result_{suffix}")


def _math(nodes, links, op, x, y=None, z=None, loc=(0, 0), clamp=False):
    n = nodes.new('ShaderNodeMath')
    n.operation = op
    n.use_clamp = clamp
    n.location = loc
    for i, v in enumerate((x, y, z)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            links.new(v, n.inputs[i])
    return n.outputs[0]


def _vec(nodes, links, op, x, y=None, z=None, loc=(0, 0), scale=None):
    n = nodes.new('ShaderNodeVectorMath')
    n.operation = op
    n.location = loc
    for i, v in enumerate((x, y, z)):
        if v is None:
            continue
        if isinstance(v, tuple):
            n.inputs[i].default_value = v
        else:
            links.new(v, n.inputs[i])
    if scale is not None:
        links.new(scale, n.inputs['Scale'])
    return n.outputs['Value'] if op == 'DOT_PRODUCT' else n.outputs['Vector']


def _scalar_formula(nodes, links, mode, a, b, loc):
    x, y = loc
    if mode == 'ADD':
        return _math(nodes, links, 'ADD', a, b, loc=loc)
    if mode == 'SUBTRACT':
        return _math(nodes, links, 'SUBTRACT', a, b, loc=loc)
    if mode == 'MULTIPLY':
        return _math(nodes, links, 'MULTIPLY', a, b, loc=loc)
    if mode == 'DIVIDE':
        return _math(nodes, links, 'DIVIDE', a, b, loc=loc)
    if mode == 'DIFFERENCE':
        d = _math(nodes, links, 'SUBTRACT', a, b, loc=(x - 180, y))
        return _math(nodes, links, 'ABSOLUTE', d, loc=loc)
    if mode == 'EXCLUSION':  # a + b - 2ab
        s = _math(nodes, links, 'ADD', a, b, loc=(x - 180, y + 60))
        p = _math(nodes, links, 'MULTIPLY', a, b, loc=(x - 180, y - 60))
        return _math(nodes, links, 'MULTIPLY_ADD', p, -2.0, s, loc=loc)
    raise ValueError(f"Unknown scalar blend mode {mode}")


def _decode(nodes, links, color, loc):
    """0-1 color to a -1..1 vector."""
    return _vec(nodes, links, 'MULTIPLY_ADD', color, (2.0, 2.0, 2.0), (-1.0, -1.0, -1.0),
                loc=loc)


def _encode(nodes, links, vec, loc):
    n = _vec(nodes, links, 'NORMALIZE', vec, loc=loc)
    return _vec(nodes, links, 'MULTIPLY_ADD', n, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5),
                loc=(loc[0] + 180, loc[1]))


def _separate(nodes, links, vec, loc):
    n = nodes.new('ShaderNodeSeparateXYZ')
    n.location = loc
    links.new(vec, n.inputs[0])
    return n.outputs['X'], n.outputs['Y'], n.outputs['Z']


def _normal_formula(nodes, links, mode, a, b, loc):
    """Combine two encoded tangent-space normals. Returns an encoded color."""
    x, y = loc
    da = _decode(nodes, links, a, (x - 900, y + 100))
    db = _decode(nodes, links, b, (x - 900, y - 100))
    if mode == 'WHITEOUT':  # (a.xy + b.xy, a.z * b.z)
        ax, ay, az = _separate(nodes, links, da, (x - 700, y + 100))
        bx, by, bz = _separate(nodes, links, db, (x - 700, y - 100))
        sx = _math(nodes, links, 'ADD', ax, bx, loc=(x - 500, y + 150))
        sy = _math(nodes, links, 'ADD', ay, by, loc=(x - 500, y + 50))
        sz = _math(nodes, links, 'MULTIPLY', az, bz, loc=(x - 500, y - 50))
        c = nodes.new('ShaderNodeCombineXYZ')
        c.location = (x - 300, y + 50)
        links.new(sx, c.inputs['X'])
        links.new(sy, c.inputs['Y'])
        links.new(sz, c.inputs['Z'])
        return _encode(nodes, links, c.outputs['Vector'], (x - 100, y + 50))
    if mode == 'DETAIL':  # t = a + (0,0,1), u = b * (-1,-1,1), r = t * dot(t,u) / t.z - u
        t = _vec(nodes, links, 'ADD', da, (0.0, 0.0, 1.0), loc=(x - 700, y + 100))
        u = _vec(nodes, links, 'MULTIPLY', db, (-1.0, -1.0, 1.0), loc=(x - 700, y - 100))
        d = _vec(nodes, links, 'DOT_PRODUCT', t, u, loc=(x - 500, y))
        tz = _separate(nodes, links, t, (x - 500, y + 150))[2]
        k = _math(nodes, links, 'DIVIDE', d, tz, loc=(x - 300, y))
        s = _vec(nodes, links, 'SCALE', t, scale=k, loc=(x - 120, y))
        r = _vec(nodes, links, 'SUBTRACT', s, u, loc=(x + 60, y))
        return _encode(nodes, links, r, (x + 240, y))
    raise ValueError(f"Unknown normal blend mode {mode}")


def build(nodes, links, ch, mode, a, b, factor, loc):
    """Add the nodes that blend `b` over `a` by `factor` for channel `ch`; return the result
    socket. `loc` is where the last node goes, the formula nodes sit to its left."""
    fam = family(ch)
    x, y = loc
    if fam == 'COLOR':
        n, out = _mix(nodes, links, 'RGBA', a, b, factor, loc, blend_type=mode)
        n.label = f"{ch.label} ({MODES[mode][0]})"
        return out
    if fam == 'NORMAL':
        if mode == 'MIX':
            n, out = _mix(nodes, links, 'RGBA', a, b, factor, loc)
            n.label = f"{ch.label} ({MODES[mode][0]})"
            return out
        f = _normal_formula(nodes, links, mode, a, b, (x - 300, y))
        n, out = _mix(nodes, links, 'RGBA', a, f, factor, loc)
        n.label = f"{ch.label} ({MODES[mode][0]})"
        return out
    # scalar
    if mode == 'MIX':
        n, out = _mix(nodes, links, 'FLOAT', a, b, factor, loc)
        n.label = f"{ch.label} ({MODES[mode][0]})"
        return out
    if mode == 'ADD':
        # lerp(A, A + B, factor) is A + B * factor, one node
        out = _math(nodes, links, 'MULTIPLY_ADD', b, factor, a, loc=loc)
        out.node.label = f"{ch.label} ({MODES[mode][0]})"
        return out
    f = _scalar_formula(nodes, links, mode, a, b, (x - 200, y))
    n, out = _mix(nodes, links, 'FLOAT', a, f, factor, loc)
    n.label = f"{ch.label} ({MODES[mode][0]})"
    return out
