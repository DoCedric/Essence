"""Live generators: masks and fills that react to the mesh, computed by the shader for free.

A shader reads the surface normal and the position at no real cost, so these need no baking
(see ESSENCE_DESIGN.md, "Live versus baked generators"). A generator is a source kind like
Procedural: an ordinary node group "Essence Gen <name>" with a uniform interface that the
source machinery in sources.py places and wires. Adding a generator means adding a `Gen`
entry to GENERATORS with a builder.

Interface contract: every group has a float output `Fac` (0 to 1), named inputs, and a
`Contrast` / `Balance` where it makes sense. Groups that read a normal take `World Space`
(object space is the default so the result stays put when the object rotates), `Use Detail
Normal` and `Detail Normal`: Essence wires the Essence Normal Map node's output (a world
space normal that includes the baked high-poly detail) into `Detail Normal`, and the
switch chooses between it and the plain surface normal.
"""

from dataclasses import dataclass, field
from typing import Callable, Tuple

import bpy

VERSION_KEY = "essence_version"
DETAIL_INPUT = "Detail Normal"      # wired by Essence, not edited in the panel
INTERNAL_INPUTS = (DETAIL_INPUT,)


@dataclass(frozen=True)
class Gen:
    id: str
    label: str
    description: str
    version: int
    inputs: Tuple                       # (name, socket type, default, options)
    build: Callable = field(compare=False, default=None)

    @property
    def group_name(self):
        return f"Essence Gen {self.label}"


def _ident(sockets, ident):
    return next(s for s in sockets if s.identifier == ident)


def _interface(ng, gen):
    iface = ng.interface
    iface.clear()
    ng.nodes.clear()
    iface.new_socket("Fac", in_out='OUTPUT', socket_type='NodeSocketFloat')
    for name, socket_type, default, opts in gen.inputs:
        s = iface.new_socket(name, in_out='INPUT', socket_type=socket_type)
        s.default_value = default
        for k, v in opts.items():
            setattr(s, k, v)
    n_in = ng.nodes.new('NodeGroupInput')
    n_in.location = (-1100, 0)
    n_out = ng.nodes.new('NodeGroupOutput')
    n_out.location = (900, 0)
    return n_in, n_out


def _math(ng, op, a=None, b=None, c=None, x=0, y=0, clamp=False, label=""):
    n = ng.nodes.new('ShaderNodeMath')
    n.operation = op
    n.use_clamp = clamp
    n.location = (x, y)
    if label:
        n.label = label
    for i, v in enumerate((a, b, c)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            ng.links.new(v, n.inputs[i])
    return n


def _vector_math(ng, op, a, b=None, x=0, y=0):
    n = ng.nodes.new('ShaderNodeVectorMath')
    n.operation = op
    n.location = (x, y)
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (tuple, list)):
            n.inputs[i].default_value = v
        else:
            ng.links.new(v, n.inputs[i])
    return n


def _mix_vector(ng, factor, a, b, x=0, y=0, label=""):
    n = ng.nodes.new('ShaderNodeMix')
    n.data_type = 'VECTOR'
    n.location = (x, y)
    if label:
        n.label = label
    ng.links.new(factor, _ident(n.inputs, "Factor_Float"))
    ng.links.new(a, _ident(n.inputs, "A_Vector"))
    ng.links.new(b, _ident(n.inputs, "B_Vector"))
    return _ident(n.outputs, "Result_Vector")


def surface_normal(ng, n_in):
    """The normal the generator reads, normalized: object space or world space (`World Space`),
    the plain surface normal or the detail normal (`Use Detail Normal`)."""
    world = n_in.outputs["World Space"]
    obj_n = ng.nodes.new('ShaderNodeTexCoord')
    obj_n.location = (-900, 300)
    geo = ng.nodes.new('ShaderNodeNewGeometry')
    geo.location = (-900, 150)
    to_obj = ng.nodes.new('ShaderNodeVectorTransform')
    to_obj.vector_type = 'NORMAL'
    to_obj.convert_from, to_obj.convert_to = 'WORLD', 'OBJECT'
    to_obj.location = (-900, -100)
    ng.links.new(n_in.outputs[DETAIL_INPUT], to_obj.inputs['Vector'])
    base = _mix_vector(ng, world, obj_n.outputs['Normal'], geo.outputs['Normal'],
                       -650, 250, "Surface normal")
    detail = _mix_vector(ng, world, to_obj.outputs['Vector'], n_in.outputs[DETAIL_INPUT],
                         -650, -50, "Detail normal")
    chosen = _mix_vector(ng, n_in.outputs["Use Detail Normal"], base, detail,
                         -400, 150, "Use detail")
    return _vector_math(ng, 'NORMALIZE', chosen, x=-200, y=150).outputs['Vector']


_NORMAL_INPUTS = (
    ("World Space", 'NodeSocketBool', False, {}),
    ("Use Detail Normal", 'NodeSocketBool', False, {}),
    (DETAIL_INPUT, 'NodeSocketVector', (0.0, 0.0, 1.0), {"hide_value": True}),
)


def _build_directional(ng, gen):
    n_in, n_out = _interface(ng, gen)
    n = surface_normal(ng, n_in)
    direction = _vector_math(ng, 'NORMALIZE', n_in.outputs["Direction"], x=-200, y=-100)
    dot = _vector_math(ng, 'DOT_PRODUCT', n, direction.outputs['Vector'], x=0, y=0)
    scaled = _math(ng, 'MULTIPLY', dot.outputs['Value'], n_in.outputs["Contrast"], x=200, y=0)
    # d in -1..1 -> 0..1 around 0.5, shifted by Balance
    half = _math(ng, 'MULTIPLY_ADD', scaled.outputs[0], 0.5, 0.5, x=400, y=0)
    out = _math(ng, 'ADD', half.outputs[0], n_in.outputs["Balance"], x=600, y=0, clamp=True,
                label="Balance")
    ng.links.new(out.outputs[0], n_out.inputs["Fac"])


def _build_slope(ng, gen):
    n_in, n_out = _interface(ng, gen)
    n = surface_normal(ng, n_in)
    axis = _vector_math(ng, 'NORMALIZE', n_in.outputs["Axis"], x=-200, y=-100)
    dot = _vector_math(ng, 'DOT_PRODUCT', n, axis.outputs['Vector'], x=0, y=0)
    clamp = ng.nodes.new('ShaderNodeClamp')
    clamp.inputs['Min'].default_value = -1.0
    clamp.inputs['Max'].default_value = 1.0
    clamp.location = (150, 0)
    ng.links.new(dot.outputs['Value'], clamp.inputs['Value'])
    angle = _math(ng, 'ARCCOSINE', clamp.outputs['Result'], x=300, y=0)
    degrees = _math(ng, 'DEGREES', angle.outputs[0], x=450, y=0)
    # 1 inside Angle, 0 outside, a ramp of width Softness (never zero) around it
    soft = _math(ng, 'MAXIMUM', n_in.outputs["Softness"], 0.02, x=300, y=-200)
    half = _math(ng, 'MULTIPLY', soft.outputs[0], 0.5, x=450, y=-200)
    lo = _math(ng, 'ADD', n_in.outputs["Angle"], half.outputs[0], x=600, y=-200)
    hi = _math(ng, 'SUBTRACT', n_in.outputs["Angle"], half.outputs[0], x=600, y=-350)
    rng = ng.nodes.new('ShaderNodeMapRange')
    rng.data_type = 'FLOAT'
    rng.clamp = True
    rng.location = (750, 0)
    ng.links.new(degrees.outputs[0], rng.inputs['Value'])
    ng.links.new(lo.outputs[0], rng.inputs['From Min'])   # angle at the soft edge -> 0
    ng.links.new(hi.outputs[0], rng.inputs['From Max'])   # angle inside -> 1
    ng.links.new(rng.outputs['Result'], n_out.inputs["Fac"])


def _build_gradient(ng, gen):
    n_in, n_out = _interface(ng, gen)
    coords = ng.nodes.new('ShaderNodeTexCoord')
    coords.location = (-900, 200)
    centered = _vector_math(ng, 'SUBTRACT', coords.outputs['Generated'], (0.5, 0.5, 0.5),
                            x=-650, y=200)
    axis = _vector_math(ng, 'NORMALIZE', n_in.outputs["Axis"], x=-650, y=-50)
    dot = _vector_math(ng, 'DOT_PRODUCT', centered.outputs['Vector'], axis.outputs['Vector'],
                       x=-400, y=100)
    # Generated coordinates run 0..1 across the bounding box: along an axis this is that
    # coordinate, along any other direction it is the signed distance from the center.
    pos = _math(ng, 'ADD', dot.outputs['Value'], 0.5, x=-200, y=100)
    rng = ng.nodes.new('ShaderNodeMapRange')
    rng.data_type = 'FLOAT'
    rng.clamp = True
    rng.location = (100, 100)
    ng.links.new(pos.outputs[0], rng.inputs['Value'])
    ng.links.new(n_in.outputs["Start"], rng.inputs['From Min'])
    ng.links.new(n_in.outputs["End"], rng.inputs['From Max'])
    ng.links.new(rng.outputs['Result'], n_out.inputs["Fac"])


_V = 'NodeSocketVector'
_F = 'NodeSocketFloat'

GENERATORS = {g.id: g for g in (
    Gen('DIRECTIONAL_LIGHT', "Directional Light",
        "How much each surface faces a light direction, like a light from that side",
        1,
        (("Direction", _V, (0.0, 0.0, 1.0), {}),) + _NORMAL_INPUTS + (
            ("Contrast", _F, 1.0, {"min_value": 0.0, "max_value": 10.0}),
            ("Balance", _F, 0.0, {"min_value": -1.0, "max_value": 1.0})),
        _build_directional),
    Gen('SLOPE', "Slope",
        "Surfaces that face an axis within an angle (up for snow, down for drips)",
        1,
        (("Axis", _V, (0.0, 0.0, 1.0), {}),) + _NORMAL_INPUTS + (
            ("Angle", _F, 45.0, {"min_value": 0.0, "max_value": 180.0}),
            ("Softness", _F, 10.0, {"min_value": 0.0, "max_value": 90.0})),
        _build_slope),
    Gen('GRADIENT', "Gradient",
        "A gradient across the object's bounding box along an axis: Z is a height gradient, "
        "X and Y a position gradient",
        1,
        (("Axis", _V, (0.0, 0.0, 1.0), {}),
         ("Start", _F, 0.0, {}),
         ("End", _F, 1.0, {})),
        _build_gradient),
)}
ITEMS = [(g.id, g.label, g.description) for g in GENERATORS.values()]


def _users(ng):
    """Every group node in a material that uses `ng`."""
    return [(m.node_tree, n) for m in bpy.data.materials if m.node_tree is not None
            for n in m.node_tree.nodes if n.bl_idname == 'ShaderNodeGroup' and n.node_tree is ng]


def _snapshot(node):
    """Values and links of a group node by socket name, to put back after its group is rebuilt
    (a rebuilt interface has new socket identifiers, which would reset everything)."""
    values, ins, outs = {}, {}, {}
    for s in node.inputs:
        if s.is_linked:
            ins[s.name] = [l.from_socket for l in s.links]
        elif hasattr(s, "default_value"):
            v = s.default_value
            values[s.name] = v if isinstance(v, (bool, int, float)) else tuple(v)
    for s in node.outputs:
        outs[s.name] = [l.to_socket for l in s.links]
    return values, ins, outs


def _restore(tree, node, saved):
    values, ins, outs = saved
    for name, v in values.items():
        sock = node.inputs.get(name)
        if sock is not None:
            sock.default_value = v
    for name, froms in ins.items():
        sock = node.inputs.get(name)
        for f in froms:
            if sock is not None:
                tree.links.new(f, sock)
    for name, tos in outs.items():
        sock = node.outputs.get(name)
        for t in tos:
            if sock is not None:
                tree.links.new(sock, t)


def ensure_group(gen_id):
    """The shared node group of a generator, (re)built when missing or outdated. A rebuild keeps
    the values and links of every node that uses it, by socket name."""
    gen = GENERATORS[gen_id]
    ng = bpy.data.node_groups.get(gen.group_name)
    if ng is not None and ng.get(VERSION_KEY) == gen.version:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(gen.group_name, 'ShaderNodeTree')
    users = [(tree, n, _snapshot(n)) for tree, n in _users(ng)]
    gen.build(ng, gen)
    ng[VERSION_KEY] = gen.version
    for tree, n, saved in users:
        _restore(tree, n, saved)
    return ng
