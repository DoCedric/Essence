"""Blend modes for the layer group.

Every mode has the same shape: `result = lerp(A, f(A, B), factor)`, where `f` is the mode's
formula and `factor` is mask * opacity * enable. Mix is `f = B`, so a mode only has to say
what `f` is. Color channels use the Mix node's own blend types (which also does the lerp),
scalar channels (roughness, metallic, height, alpha) use Math nodes. Modes the Mix node does
not have (Hard Light, Pin Light, Linear Burn) are formulas on colors too, then a lerp. The
scalar formulas follow Blender's Mix node, so a mode gives the same result on a color and on
a float where the factor is 1. (A few Mix node modes, like Overlay and the dodges, put the
factor inside the formula, so partly masked texels differ slightly.)

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
    # The numbers are stored in files: new modes get new numbers, never reused ones.
    'DARKEN': ("Darken", 7, "The darker of the layer and what is below"),
    'LIGHTEN': ("Lighten", 8, "The lighter of the layer and what is below"),
    'SCREEN': ("Screen", 9, "Lighten: the inverse of multiplying the inverses"),
    'DODGE': ("Color Dodge", 10, "Brighten what is below by the layer"),
    'BURN': ("Color Burn", 11, "Darken what is below by the layer"),
    'LINEAR_BURN': ("Linear Burn", 12, "Add the layer to what is below, minus one"),
    'OVERLAY': ("Overlay", 13, "Multiply the darks and screen the lights of what is below"),
    'SOFT_LIGHT': ("Soft Light", 14, "A gentle Overlay"),
    'HARD_LIGHT': ("Hard Light", 15, "Overlay driven by the layer instead of what is below"),
    'LINEAR_LIGHT': ("Linear Light", 16, "Burn or dodge linearly by the layer"),
    'PIN_LIGHT': ("Pin Light", 17, "Keep what is below unless the layer is darker (dark half) "
                  "or lighter (light half)"),
    'HUE': ("Hue", 18, "The layer's hue on what is below"),
    'SATURATION': ("Saturation", 19, "The layer's saturation on what is below"),
    'COLOR': ("Color", 20, "The layer's hue and saturation on what is below"),
    'VALUE': ("Value", 21, "The layer's value (brightness) on what is below"),
}

_SIMPLE = ('MIX', 'ADD', 'MULTIPLY', 'DIVIDE', 'SUBTRACT', 'DIFFERENCE', 'EXCLUSION',
           'DARKEN', 'LIGHTEN', 'SCREEN', 'DODGE', 'BURN', 'LINEAR_BURN', 'OVERLAY',
           'SOFT_LIGHT', 'HARD_LIGHT', 'LINEAR_LIGHT', 'PIN_LIGHT')
_COLOR_ONLY = ('HUE', 'SATURATION', 'COLOR', 'VALUE')     # meaningless on a single value
_FORMULA = ('HARD_LIGHT', 'PIN_LIGHT', 'LINEAR_BURN')     # not a Mix node blend type
_cache = {}


def family(ch):
    return 'COLOR' if ch.socket_type == 'NodeSocketColor' else 'SCALAR'


def modes_for(ch):
    """Mode ids a channel offers, its default first."""
    modes = _SIMPLE + (_COLOR_ONLY if family(ch) == 'COLOR' else ())
    return [ch.blend] + [m for m in modes if m != ch.blend]


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
    if factor is not None:
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
    if mode == 'DARKEN':
        return _math(nodes, links, 'MINIMUM', a, b, loc=loc)
    if mode == 'LIGHTEN':
        return _math(nodes, links, 'MAXIMUM', a, b, loc=loc)
    if mode == 'SCREEN':  # 1 - (1 - a)(1 - b)
        ia = _math(nodes, links, 'SUBTRACT', 1.0, a, loc=(x - 360, y + 60))
        ib = _math(nodes, links, 'SUBTRACT', 1.0, b, loc=(x - 360, y - 60))
        p = _math(nodes, links, 'MULTIPLY', ia, ib, loc=(x - 180, y))
        return _math(nodes, links, 'SUBTRACT', 1.0, p, loc=loc)
    if mode == 'DODGE':  # a / (1 - b), capped at 1; where b is 1: 1, unless a is 0 (Blender)
        ib = _math(nodes, links, 'SUBTRACT', 1.0, b, loc=(x - 540, y))
        q = _math(nodes, links, 'DIVIDE', a, ib, loc=(x - 360, y))   # safe: 0 where ib is 0
        one = _math(nodes, links, 'MULTIPLY',
                    _math(nodes, links, 'LESS_THAN', ib, 1e-6, loc=(x - 540, y - 120)),
                    _math(nodes, links, 'GREATER_THAN', a, 0.0, loc=(x - 540, y - 240)),
                    loc=(x - 360, y - 120))
        q1 = _math(nodes, links, 'MAXIMUM', q, one, loc=(x - 180, y))
        return _math(nodes, links, 'MINIMUM', q1, 1.0, loc=loc)
    if mode == 'BURN':  # 1 - (1 - a) / b, 0 where b is 0
        ia = _math(nodes, links, 'SUBTRACT', 1.0, a, loc=(x - 540, y))
        zero = _math(nodes, links, 'LESS_THAN', b, 1e-6, loc=(x - 540, y - 120))
        q = _math(nodes, links, 'DIVIDE', ia, b, loc=(x - 360, y))
        q1 = _math(nodes, links, 'MAXIMUM', q, zero, loc=(x - 180, y))
        return _math(nodes, links, 'MAXIMUM', _math(nodes, links, 'SUBTRACT', 1.0, q1,
                                                       loc=(x - 90, y + 60)), 0.0, loc=loc)
    if mode == 'LINEAR_BURN':  # a + b - 1
        s = _math(nodes, links, 'ADD', a, b, loc=(x - 180, y))
        return _math(nodes, links, 'SUBTRACT', s, 1.0, loc=loc)
    if mode == 'LINEAR_LIGHT':  # a + 2b - 1
        return _math(nodes, links, 'MULTIPLY_ADD', b, 2.0,
                     _math(nodes, links, 'SUBTRACT', a, 1.0, loc=(x - 180, y)), loc=loc)
    if mode in ('OVERLAY', 'HARD_LIGHT'):
        # Overlay tests the base (a), Hard Light the layer (b): 2ab below half, else screen.
        test = a if mode == 'OVERLAY' else b
        low = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', a, b,
                                                     loc=(x - 540, y + 120)), 2.0,
                    loc=(x - 360, y + 120))
        ia = _math(nodes, links, 'SUBTRACT', 1.0, a, loc=(x - 720, y - 60))
        ib = _math(nodes, links, 'SUBTRACT', 1.0, b, loc=(x - 720, y - 180))
        p = _math(nodes, links, 'MULTIPLY', ia, ib, loc=(x - 540, y - 120))
        high = _math(nodes, links, 'MULTIPLY_ADD', p, -2.0, 1.0, loc=(x - 360, y - 120))
        step = _math(nodes, links, 'GREATER_THAN', test, 0.5 - 1e-7, loc=(x - 360, y))
        mix = nodes.new('ShaderNodeMix')
        mix.data_type = 'FLOAT'
        mix.location = loc
        links.new(step, _ident(mix.inputs, "Factor_Float"))
        links.new(low, _ident(mix.inputs, "A_Float"))
        links.new(high, _ident(mix.inputs, "B_Float"))
        return _ident(mix.outputs, "Result_Float")
    if mode == 'SOFT_LIGHT':
        # Blender's: (1 - a) * b * a + a * screen(a, b)
        ia = _math(nodes, links, 'SUBTRACT', 1.0, a, loc=(x - 720, y + 60))
        ib = _math(nodes, links, 'SUBTRACT', 1.0, b, loc=(x - 720, y - 60))
        scr = _math(nodes, links, 'SUBTRACT', 1.0, _math(nodes, links, 'MULTIPLY', ia, ib,
                                                          loc=(x - 540, y - 60)),
                    loc=(x - 360, y - 60))
        t1 = _math(nodes, links, 'MULTIPLY', _math(nodes, links, 'MULTIPLY', ia, b,
                                                    loc=(x - 540, y + 60)), a,
                   loc=(x - 360, y + 60))
        return _math(nodes, links, 'MULTIPLY_ADD', a, scr, t1, loc=loc)
    if mode == 'PIN_LIGHT':
        # b below half: min(a, 2b); else max(a, 2b - 1)
        b2 = _math(nodes, links, 'MULTIPLY', b, 2.0, loc=(x - 540, y))
        low = _math(nodes, links, 'MINIMUM', a, b2, loc=(x - 360, y + 80))
        high = _math(nodes, links, 'MAXIMUM', a, _math(nodes, links, 'SUBTRACT', b2, 1.0,
                                                        loc=(x - 360, y - 160)),
                     loc=(x - 180, y - 80))
        step = _math(nodes, links, 'GREATER_THAN', b, 0.5 - 1e-7, loc=(x - 360, y))
        mix = nodes.new('ShaderNodeMix')
        mix.data_type = 'FLOAT'
        mix.location = loc
        links.new(step, _ident(mix.inputs, "Factor_Float"))
        links.new(low, _ident(mix.inputs, "A_Float"))
        links.new(high, _ident(mix.inputs, "B_Float"))
        return _ident(mix.outputs, "Result_Float")
    raise ValueError(f"Unknown scalar blend mode {mode}")


def _color_formula(nodes, links, mode, a, b, loc):
    """f(A, B) of a mode the Mix node lacks, per color component (vector maths)."""
    x, y = loc
    if mode == 'LINEAR_BURN':  # a + b - 1
        s = _vec(nodes, links, 'ADD', a, b, loc=(x - 180, y))
        return _vec(nodes, links, 'SUBTRACT', s, (1.0, 1.0, 1.0), loc=loc)
    if mode == 'HARD_LIGHT':
        # Overlay with the roles swapped: the layer is the base (Blender's Overlay tests A).
        n, out = _mix(nodes, links, 'RGBA', b, a, None, loc, blend_type='OVERLAY')
        _ident(n.inputs, "Factor_Float").default_value = 1.0
        return out
    if mode == 'PIN_LIGHT':
        b2 = _vec(nodes, links, 'SCALE', b, loc=(x - 540, y), scale=None)
        b2.node.inputs['Scale'].default_value = 2.0
        low = _vec(nodes, links, 'MINIMUM', a, b2, loc=(x - 360, y + 80))
        high = _vec(nodes, links, 'MAXIMUM', a,
                    _vec(nodes, links, 'SUBTRACT', b2, (1.0, 1.0, 1.0), loc=(x - 360, y - 160)),
                    loc=(x - 180, y - 80))
        # Per component: 1 where b >= 0.5 (floor(2b), capped at 1), 0 below.
        step = _vec(nodes, links, 'MINIMUM', _vec(nodes, links, 'FLOOR', b2,
                                                  loc=(x - 360, y - 300)),
                    (1.0, 1.0, 1.0), loc=(x - 180, y - 300))
        mix = nodes.new('ShaderNodeMix')
        mix.data_type = 'VECTOR'
        mix.factor_mode = 'NON_UNIFORM'
        mix.location = loc
        links.new(step, _ident(mix.inputs, "Factor_Vector"))
        links.new(low, _ident(mix.inputs, "A_Vector"))
        links.new(high, _ident(mix.inputs, "B_Vector"))
        return _ident(mix.outputs, "Result_Vector")
    raise ValueError(f"Unknown color formula {mode}")


def build(nodes, links, ch, mode, a, b, factor, loc):
    """Add the nodes that blend `b` over `a` by `factor` for channel `ch`; return the result
    socket. `loc` is where the last node goes, the formula nodes sit to its left."""
    fam = family(ch)
    x, y = loc
    if fam == 'COLOR':
        if mode in _FORMULA:
            f = _color_formula(nodes, links, mode, a, b, (x - 200, y))
            n, out = _mix(nodes, links, 'RGBA', a, f, factor, loc)
            n.label = f"{ch.label} ({MODES[mode][0]})"
            return out
        n, out = _mix(nodes, links, 'RGBA', a, b, factor, loc, blend_type=mode)
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
