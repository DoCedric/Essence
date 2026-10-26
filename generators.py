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

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import bpy
from mathutils import Vector

VERSION_KEY = "essence_version"
DETAIL_INPUT = "Detail Normal"      # wired by Essence, not edited in the panel
BAKED_POSITION_INPUT = "Baked Position"   # likewise: the Position slot, or Generated coordinates
CURVATURE_INPUT = "Curvature"       # likewise: the Curvature slot, or flat grey (no curvature)
AO_INPUT = "AO"                     # likewise: the AO slot, or white (no occlusion)
ID_INPUT = "ID"                     # likewise: the ID slot, or black (no part)
COLOR_PREFIX = "ID Color "          # ID Select's palette: "ID Color 1" to "ID Color 16"
COUNT_INPUT = "Colors"              # how many of them are in use
MAX_ID_COLORS = 16


def id_color_name(i):
    return f"{COLOR_PREFIX}{i}"
INTERNAL_INPUTS = (DETAIL_INPUT, BAKED_POSITION_INPUT, CURVATURE_INPUT, AO_INPUT, ID_INPUT)
ROTATION_INPUT = "Rotation"         # an Euler input the temporary gizmo (gizmo.py) can drive


@dataclass(frozen=True)
class Gen:
    id: str
    label: str
    description: str
    version: int
    inputs: Tuple                       # (name, socket type, default, options)
    build: Callable = field(compare=False, default=None)
    # Turns the saved input values of an older version into this version's: {name: value}
    migrate: Optional[Callable] = field(compare=False, default=None)
    # The Mesh Maps slots it needs an image in to do anything (the panel says which are empty)
    needs: Tuple = field(compare=False, default=())

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
        for k, v in opts.items():       # subtype and range first: they decide what a value is
            setattr(s, k, v)
        s.default_value = default
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


def rotated_axis(ng, n_in, label, x=-400, y=-250):
    """The direction or axis a generator uses: +Z turned by the `Rotation` input (no rotation
    is straight up), normalized."""
    rotate = ng.nodes.new('ShaderNodeVectorRotate')
    rotate.rotation_type = 'EULER_XYZ'
    rotate.inputs['Vector'].default_value = (0.0, 0.0, 1.0)
    rotate.label = label
    rotate.location = (x, y)
    ng.links.new(n_in.outputs[ROTATION_INPUT], rotate.inputs['Rotation'])
    return _vector_math(ng, 'NORMALIZE', rotate.outputs['Vector'], x=x + 200,
                        y=y + 150).outputs['Vector']


def _build_directional(ng, gen):
    n_in, n_out = _interface(ng, gen)
    n = surface_normal(ng, n_in)
    direction = rotated_axis(ng, n_in, "Light direction")
    dot = _vector_math(ng, 'DOT_PRODUCT', n, direction, x=0, y=0)
    scaled = _math(ng, 'MULTIPLY', dot.outputs['Value'], n_in.outputs["Contrast"], x=200, y=0)
    # d in -1..1 -> 0..1 around 0.5, shifted by Balance
    half = _math(ng, 'MULTIPLY_ADD', scaled.outputs[0], 0.5, 0.5, x=400, y=0)
    out = _math(ng, 'ADD', half.outputs[0], n_in.outputs["Balance"], x=600, y=0, clamp=True,
                label="Balance")
    ng.links.new(out.outputs[0], n_out.inputs["Fac"])


def _build_slope(ng, gen):
    n_in, n_out = _interface(ng, gen)
    n = surface_normal(ng, n_in)
    axis = rotated_axis(ng, n_in, "Slope axis")
    dot = _vector_math(ng, 'DOT_PRODUCT', n, axis, x=0, y=0)
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
    # The live position, or the one baked from the high poly (the Position slot).
    where = _mix_vector(ng, n_in.outputs["Use Baked Position"], coords.outputs['Generated'],
                        n_in.outputs[BAKED_POSITION_INPUT], -800, 150, "Use baked position")
    centered = _vector_math(ng, 'SUBTRACT', where, (0.5, 0.5, 0.5), x=-650, y=200)
    axis = rotated_axis(ng, n_in, "Gradient axis", x=-900, y=-100)
    dot = _vector_math(ng, 'DOT_PRODUCT', centered.outputs['Vector'], axis, x=-400, y=100)
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
_C = 'NodeSocketColor'

_CURVATURE_IN = (CURVATURE_INPUT, _C, (0.5, 0.5, 0.5, 1.0), {"hide_value": True})
_AO_IN = (AO_INPUT, _C, (1.0, 1.0, 1.0, 1.0), {"hide_value": True})
_ID_IN = (ID_INPUT, _C, (0.0, 0.0, 0.0, 1.0), {"hide_value": True})


def _red(ng, color_socket, x, y):
    """The value (red channel) of a map read from an image."""
    sep = ng.nodes.new('ShaderNodeSeparateColor')
    sep.location = (x, y)
    ng.links.new(color_socket, sep.inputs['Color'])
    return sep.outputs['Red']


def _noise_term(ng, n_in, x, y):
    """Noise around zero, in units of the map it breaks up: (noise - 0.5) * Noise Strength.
    3D noise on the object's own coordinates, so it does not need a UV map."""
    coords = ng.nodes.new('ShaderNodeTexCoord')
    coords.location = (x, y)
    noise = ng.nodes.new('ShaderNodeTexNoise')
    noise.noise_dimensions = '3D'
    noise.inputs['Detail'].default_value = 4.0
    noise.location = (x + 200, y)
    ng.links.new(coords.outputs['Object'], noise.inputs['Vector'])
    ng.links.new(n_in.outputs["Noise Scale"], noise.inputs['Scale'])
    centered = _math(ng, 'SUBTRACT', noise.outputs['Fac'], 0.5, x=x + 450, y=y)
    return _math(ng, 'MULTIPLY', centered.outputs[0], n_in.outputs["Noise Strength"],
                 x=x + 650, y=y).outputs[0]


def _soft_step(ng, value, softness, x, y):
    """0 well below zero, 1 well above, 0.5 at zero, a ramp `softness` wide (never zero)."""
    soft = _math(ng, 'MAXIMUM', softness, 0.001, x=x, y=y - 200)
    div = _math(ng, 'DIVIDE', value, soft.outputs[0], x=x + 200, y=y)
    return _math(ng, 'ADD', div.outputs[0], 0.5, x=x + 400, y=y, clamp=True)


def _build_edge_wear(ng, gen):
    n_in, n_out = _interface(ng, gen)
    c = _red(ng, n_in.outputs[CURVATURE_INPUT], -900, 200)
    convex = _math(ng, 'SUBTRACT', c, 0.5, x=-700, y=200, label="Convex part")
    above = _math(ng, 'SUBTRACT', convex.outputs[0], n_in.outputs["Threshold"], x=-450, y=200)
    noise = _noise_term(ng, n_in, -900, -150)
    value = _math(ng, 'ADD', above.outputs[0], noise, x=-200, y=100)
    out = _soft_step(ng, value.outputs[0], n_in.outputs["Softness"], 50, 100)
    ng.links.new(out.outputs[0], n_out.inputs["Fac"])


def _build_cavity(ng, gen):
    n_in, n_out = _interface(ng, gen)
    c = _red(ng, n_in.outputs[CURVATURE_INPUT], -900, 250)
    concave = _math(ng, 'SUBTRACT', 0.5, c, x=-700, y=250, label="Concave part")
    ao = _red(ng, n_in.outputs[AO_INPUT], -900, -50)
    dark = _math(ng, 'SUBTRACT', 1.0, ao, x=-700, y=-50, label="Occlusion")
    ao_part = _math(ng, 'MULTIPLY', dark.outputs[0], n_in.outputs["AO Amount"], x=-450, y=-50)
    half = _math(ng, 'MULTIPLY', ao_part.outputs[0], 0.5, x=-250, y=-50)
    both = _math(ng, 'ADD', concave.outputs[0], half.outputs[0], x=-50, y=100)
    above = _math(ng, 'SUBTRACT', both.outputs[0], n_in.outputs["Threshold"], x=150, y=100)
    out = _soft_step(ng, above.outputs[0], n_in.outputs["Softness"], 350, 100)
    ng.links.new(out.outputs[0], n_out.inputs["Fac"])


def _build_id_select(ng, gen):
    """The union of the palette: for each of the 16 colors, how close the ID map is to it (1 within
    Tolerance in color space, fading to 0 over the next Softness), counted only while it is among
    the first `Colors`; the mask is the largest of them. Version 2 centred the fade on the
    tolerance, so with a Softness wider than twice the Tolerance an exact match was grey."""
    n_in, n_out = _interface(ng, gen)
    soft = _math(ng, 'MAXIMUM', n_in.outputs["Softness"], 0.001, x=-450, y=-200,
                 label="Softness, never zero")
    best = None
    for i in range(1, MAX_ID_COLORS + 1):
        y = 250 - i * 160
        dist = _vector_math(ng, 'DISTANCE', n_in.outputs[ID_INPUT],
                            n_in.outputs[id_color_name(i)], x=-700, y=y)
        margin = _math(ng, 'SUBTRACT', n_in.outputs["Tolerance"], dist.outputs['Value'],
                       x=-500, y=y, label=f"Inside the tolerance of color {i}")
        ratio = _math(ng, 'DIVIDE', margin.outputs[0], soft.outputs[0], x=-300, y=y)
        step = _math(ng, 'ADD', ratio.outputs[0], 1.0, x=-150, y=y, clamp=True)
        used = _math(ng, 'GREATER_THAN', n_in.outputs[COUNT_INPUT], i - 0.5, x=-150, y=y - 70,
                     label=f"Color {i} is in use")
        mine = _math(ng, 'MULTIPLY', step.outputs[0], used.outputs[0], x=50, y=y)
        best = mine if best is None else _math(ng, 'MAXIMUM', best.outputs[0], mine.outputs[0],
                                               x=250, y=y)
    # Invert is 0 or 1: |Invert - mask| is the mask, or 1 - the mask
    flip = _math(ng, 'SUBTRACT', n_in.outputs["Invert"], best.outputs[0], x=500, y=100)
    out = _math(ng, 'ABSOLUTE', flip.outputs[0], x=700, y=100, label="Invert")
    ng.links.new(out.outputs[0], n_out.inputs["Fac"])


def _migrate_id_select(values):
    """Version 1 had one color, "ID Color": it is the first color of the palette."""
    if "ID Color" in values and id_color_name(1) not in values:
        values[id_color_name(1)] = values.pop("ID Color")
    return values


def _build_dirt(ng, gen):
    n_in, n_out = _interface(ng, gen)
    ao = _red(ng, n_in.outputs[AO_INPUT], -1000, 300)
    dark = _math(ng, 'SUBTRACT', 1.0, ao, x=-800, y=300, label="Occlusion")
    c = _red(ng, n_in.outputs[CURVATURE_INPUT], -1000, 0)
    concave = _math(ng, 'SUBTRACT', 0.5, c, x=-800, y=0, label="Concave part")
    crevice = _math(ng, 'MULTIPLY', concave.outputs[0], n_in.outputs["Curvature Amount"],
                    x=-600, y=0)
    crevice2 = _math(ng, 'MULTIPLY', crevice.outputs[0], 2.0, x=-400, y=0)
    base = _math(ng, 'ADD', dark.outputs[0], crevice2.outputs[0], x=-200, y=150)
    noise = _noise_term(ng, n_in, -1000, -300)
    dirty = _math(ng, 'ADD', base.outputs[0], noise, x=0, y=100)
    # The bar runs from just above the darkest dirt (Amount 0: nothing) to just below zero
    # (Amount 1: everything), each end clear of the ramp: lerp(1 + S/2, -S/2, Amount).
    half = _math(ng, 'MULTIPLY', n_in.outputs["Softness"], 0.5, x=-200, y=-250)
    top = _math(ng, 'ADD', half.outputs[0], 1.0, x=0, y=-250)
    span = _math(ng, 'ADD', n_in.outputs["Softness"], 1.0, x=-200, y=-400)
    taken = _math(ng, 'MULTIPLY', n_in.outputs["Amount"], span.outputs[0], x=0, y=-400)
    bar = _math(ng, 'SUBTRACT', top.outputs[0], taken.outputs[0], x=200, y=-300)
    over = _math(ng, 'SUBTRACT', dirty.outputs[0], bar.outputs[0], x=400, y=100)
    out = _soft_step(ng, over.outputs[0], n_in.outputs["Softness"], 600, 100)
    ng.links.new(out.outputs[0], n_out.inputs["Fac"])


# The input every direction or axis uses: Euler angles in degrees, one turn at most.
_ROTATION = (ROTATION_INPUT, _V, (0.0, 0.0, 0.0),
             {"subtype": 'EULER', "min_value": 0.0, "max_value": 2 * math.pi})


def _migrate_vector(old_name):
    """Version 1 had a direction or axis vector (`old_name`); version 2 has a Rotation (Euler
    angles) that turns +Z into that vector."""
    def migrate(values):
        d = values.pop(old_name, None)
        if d is not None and ROTATION_INPUT not in values and Vector(d).length > 1e-6:
            e = Vector((0.0, 0.0, 1.0)).rotation_difference(Vector(d)).to_euler('XYZ')
            values[ROTATION_INPUT] = tuple(a % (2 * math.pi) for a in e)
        return values
    return migrate


GENERATORS = {g.id: g for g in (
    Gen('DIRECTIONAL_LIGHT', "Directional Light",
        "How much each surface faces a light direction, like a light from that side",
        2,
        (_ROTATION,) + _NORMAL_INPUTS + (
            ("Contrast", _F, 1.0, {"min_value": 0.0, "max_value": 10.0}),
            ("Balance", _F, 0.0, {"min_value": -1.0, "max_value": 1.0})),
        _build_directional, _migrate_vector("Direction")),
    Gen('SLOPE', "Slope",
        "Surfaces that face an axis within an angle (up for snow, down for drips)",
        2,
        (_ROTATION,) + _NORMAL_INPUTS + (
            ("Angle", _F, 45.0, {"min_value": 0.0, "max_value": 180.0}),
            ("Softness", _F, 10.0, {"min_value": 0.0, "max_value": 90.0})),
        _build_slope, _migrate_vector("Axis")),
    Gen('GRADIENT', "Gradient",
        "A gradient across the object's bounding box along an axis: Z is a height gradient, "
        "X and Y a position gradient",
        3,
        (_ROTATION,
         ("Start", _F, 0.0, {}),
         ("End", _F, 1.0, {}),
         ("Use Baked Position", 'NodeSocketBool', False, {}),
         (BAKED_POSITION_INPUT, 'NodeSocketVector', (0.5, 0.5, 0.5), {"hide_value": True})),
        _build_gradient, _migrate_vector("Axis")),
    Gen('EDGE_WEAR', "Edge Wear",
        "Worn paint on convex edges, from the Curvature map, with noise to break the edge up",
        1,
        (("Threshold", _F, 0.05, {"min_value": 0.0, "max_value": 0.5}),
         ("Softness", _F, 0.05, {"min_value": 0.0, "max_value": 0.5}),
         ("Noise Scale", _F, 6.0, {"min_value": 0.0}),
         ("Noise Strength", _F, 0.05, {"min_value": 0.0, "max_value": 0.5}),
         _CURVATURE_IN),
        _build_edge_wear, None, ("CURVATURE",)),
    Gen('CAVITY', "Cavity",
        "Dark crevices, from the concave part of the Curvature map and optionally the AO map",
        1,
        (("Threshold", _F, 0.04, {"min_value": 0.0, "max_value": 0.5}),
         ("Softness", _F, 0.05, {"min_value": 0.0, "max_value": 0.5}),
         ("AO Amount", _F, 0.0, {"min_value": 0.0, "max_value": 1.0}),
         _CURVATURE_IN, _AO_IN),
        _build_cavity, None, ("CURVATURE", "AO")),
    Gen('DIRT', "Dirt",
        "Dirt that collects where the AO map is dark and in crevices, broken up by noise",
        1,
        (("Amount", _F, 0.4, {"min_value": 0.0, "max_value": 1.0}),
         ("Softness", _F, 0.3, {"min_value": 0.0, "max_value": 1.0}),
         ("Curvature Amount", _F, 0.5, {"min_value": 0.0, "max_value": 2.0}),
         ("Noise Scale", _F, 4.0, {"min_value": 0.0}),
         ("Noise Strength", _F, 0.3, {"min_value": 0.0, "max_value": 1.0}),
         _CURVATURE_IN, _AO_IN),
        _build_dirt, None, ("AO", "CURVATURE")),
    Gen('ID_SELECT', "ID Select",
        "Parts of the ID map: everything whose color is within a tolerance of one of the "
        "colors of a palette you add to and remove from. Pick the colors with the eyedropper "
        "on the mesh",
        3,
        tuple((id_color_name(i), _C, (1.0, 0.0, 0.0, 1.0) if i == 1 else (0.5, 0.5, 0.5, 1.0), {})
              for i in range(1, MAX_ID_COLORS + 1))
        + ((COUNT_INPUT, 'NodeSocketInt', 1, {"min_value": 1, "max_value": MAX_ID_COLORS}),
           ("Tolerance", _F, 0.01, {"min_value": 0.0, "max_value": 2.0}),
           ("Softness", _F, 0.03, {"min_value": 0.0, "max_value": 1.0}),
           ("Invert", 'NodeSocketBool', False, {}),
           _ID_IN),
        _build_id_select, _migrate_id_select, ("ID",)),
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
    # An input the old version did not have starts at the interface's default (a rebuilt group
    # gives its node's new sockets 0, which for a count of colors would select nothing).
    for item in node.node_tree.interface.items_tree:
        if item.in_out == 'INPUT' and item.name not in values and hasattr(item, "default_value"):
            sock = node.inputs.get(item.name)
            if sock is not None and not sock.is_linked and hasattr(sock, "default_value"):
                sock.default_value = item.default_value
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
        if gen.migrate is not None:
            saved = (gen.migrate(dict(saved[0])), saved[1], saved[2])
        _restore(tree, n, saved)
    return ng
