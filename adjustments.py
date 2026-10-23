"""Adjustments: steps that transform the owner's own content before it is blended.

An adjustment is a stack entry (kind ADJUSTMENT) owned by a layer or a mask. For a layer it
names the channel it acts on (the fill of that channel, whatever its source is); for a mask
it acts on the mask value. The entries of one owner and channel apply bottom-to-top, between
the source and the blend:

    source -> adjustment 1 -> adjustment 2 -> ... -> the layer's B input (or the mask's Value)

An adjustment is a few ordinary nodes tagged `essence_source` "<adjustment uid>:ADJ" (role MAIN
and MIX), so it stays hand-editable like a source. MIX blends the original with the adjusted
value by the item's Opacity. All adjustments work on colors; float channels and masks go
through Blender's implicit float <-> color conversion. Adding an adjustment means adding an
entry to TYPES.
"""

import bpy
from dataclasses import dataclass, field
from typing import Tuple

ADJ_KEY = "ADJ"

# Baked filters (see filters.py): the compositor group and the result image of an item.
FILTER_GROUP_PREFIX = "Essence Filter "
FILTER_IMAGE_PREFIX = ".Essence Filter "


@dataclass(frozen=True)
class AdjType:
    id: str
    label: str
    description: str
    idname: str                 # the MAIN node
    input: str                  # identifier of the input the value goes into
    output: str                 # identifier of the output
    color_only: bool = False    # only makes sense on a color channel
    # (socket identifier, label) of the controls the panel shows; others are left alone
    params: Tuple = ()
    props: Tuple = ()           # (node property, value) set on creation
    widget: str = ""            # 'curve' or 'ramp': a native template the panel draws


# A fill's blend with the mask value below: the Math node operation, label, description.
FILL_BLENDS = [
    ('MULTIPLY', "Multiply", "Multiply with the value below (what stacked masks do)"),
    ('ADD', "Add", "Add to the value below"),
    ('SUBTRACT', "Subtract", "Subtract from the value below"),
    ('MINIMUM', "Darken", "The smaller of the two"),
    ('MAXIMUM', "Lighten", "The larger of the two"),
]


def new_fill_main(tree):
    """The blend node of a fill: input 0 is the value below, input 1 the fill's source."""
    n = tree.nodes.new('ShaderNodeMath')
    n.operation = 'MULTIPLY'
    n.use_clamp = True
    n.inputs[1].default_value = 1.0
    return n


LEVELS_GROUP = "Essence Adj Levels"
LEVELS_VERSION = 1

TYPES = (
    AdjType('LEVELS', "Levels", "Remap the input range, apply a gamma and remap the output",
            'ShaderNodeGroup', "Color", "Color",
            params=(("Input Black", "Input Black"), ("Input White", "Input White"),
                    ("Gamma", "Gamma"), ("Output Black", "Output Black"),
                    ("Output White", "Output White"))),
    AdjType('HUE_SAT', "Hue / Saturation / Value", "Shift hue, saturation and value",
            'ShaderNodeHueSaturation', "Color", "Color", color_only=True,
            params=(("Hue", "Hue"), ("Saturation", "Saturation"), ("Value", "Value"))),
    AdjType('BRIGHT_CONTRAST', "Brightness / Contrast", "Brightness and contrast",
            'ShaderNodeBrightContrast', "Color", "Color",
            params=(("Bright", "Brightness"), ("Contrast", "Contrast"))),
    AdjType('GAMMA', "Gamma", "Raise the value to the power gamma (Blender's Gamma node)",
            'ShaderNodeGamma', "Color", "Color", params=(("Gamma", "Gamma"),)),
    AdjType('INVERT', "Invert", "Invert the value",
            'ShaderNodeInvert', "Color", "Color"),
    AdjType('CURVES', "Curves", "Edit the response with RGB curves",
            'ShaderNodeRGBCurve', "Color", "Color", widget='curve'),
    AdjType('GRADIENT_MAP', "Gradient Map", "Replace the value with a color from a gradient",
            'ShaderNodeValToRGB', "Fac", "Color", widget='ramp'),
    AdjType('THRESHOLD', "Threshold", "Black below Low, white above High, a ramp in between",
            'ShaderNodeMapRange', "Value", "Result",
            params=(("From Min", "Low"), ("From Max", "High"))),
)
BY_ID = {t.id: t for t in TYPES}
TYPE_ITEMS = [(t.id, t.label, t.description) for t in TYPES]


def _build_levels(ng):
    ng.interface.clear()
    ng.nodes.clear()
    iface = ng.interface
    iface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
    iface.new_socket("Color", in_out='INPUT', socket_type='NodeSocketColor')
    for name, default in (("Input Black", 0.0), ("Input White", 1.0), ("Gamma", 1.0),
                          ("Output Black", 0.0), ("Output White", 1.0)):
        s = iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
        s.default_value = default
        if name == "Gamma":
            s.min_value, s.max_value = 0.01, 10.0
    nodes, links = ng.nodes, ng.links
    n_in = nodes.new('NodeGroupInput')
    n_in.location = (-900, 0)
    n_out = nodes.new('NodeGroupOutput')
    n_out.location = (900, 0)
    sep = nodes.new('ShaderNodeSeparateColor')
    sep.location = (-700, 0)
    comb = nodes.new('ShaderNodeCombineColor')
    comb.location = (700, 0)
    links.new(n_in.outputs["Color"], sep.inputs["Color"])
    for i, comp in enumerate(("Red", "Green", "Blue")):
        y = 250 - i * 250
        a = nodes.new('ShaderNodeMapRange')       # input range -> 0..1
        a.location = (-450, y)
        a.clamp = True
        a.inputs['To Min'].default_value = 0.0
        a.inputs['To Max'].default_value = 1.0
        links.new(sep.outputs[comp], a.inputs['Value'])
        links.new(n_in.outputs["Input Black"], a.inputs['From Min'])
        links.new(n_in.outputs["Input White"], a.inputs['From Max'])
        g = nodes.new('ShaderNodeMath')            # gamma
        g.operation = 'POWER'
        g.location = (-200, y)
        inv = None
        if i == 0:
            inv = nodes.new('ShaderNodeMath')      # 1 / gamma, shared by the three channels
            inv.operation = 'DIVIDE'
            inv.inputs[0].default_value = 1.0
            inv.location = (-450, 400)
            links.new(n_in.outputs["Gamma"], inv.inputs[1])
            shared_inv = inv
        links.new(a.outputs['Result'], g.inputs[0])
        links.new(shared_inv.outputs[0], g.inputs[1])
        b = nodes.new('ShaderNodeMapRange')       # 0..1 -> output range
        b.location = (150, y)
        b.clamp = True
        links.new(g.outputs[0], b.inputs['Value'])
        links.new(n_in.outputs["Output Black"], b.inputs['To Min'])
        links.new(n_in.outputs["Output White"], b.inputs['To Max'])
        links.new(b.outputs['Result'], comb.inputs[comp])
    links.new(comb.outputs["Color"], n_out.inputs["Color"])


def levels_group():
    ng = bpy.data.node_groups.get(LEVELS_GROUP)
    if ng is not None and ng.get("essence_version") == LEVELS_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(LEVELS_GROUP, 'ShaderNodeTree')
    _build_levels(ng)
    ng["essence_version"] = LEVELS_VERSION
    return ng


def new_main(tree, t):
    """Create the MAIN node of adjustment type `t`."""
    n = tree.nodes.new(t.idname)
    if t.id == 'LEVELS':
        n.node_tree = levels_group()
    elif t.id == 'THRESHOLD':
        n.data_type = 'FLOAT'
        n.clamp = True
        n.inputs['From Min'].default_value = 0.45
        n.inputs['From Max'].default_value = 0.55
    elif t.id == 'GRADIENT_MAP':
        els = n.color_ramp.elements
        els[0].color = (0.0, 0.0, 0.0, 1.0)
        els[1].color = (1.0, 1.0, 1.0, 1.0)
    for prop, value in t.props:
        setattr(n, prop, value)
    return n


def new_mix(tree):
    """The Opacity blend: lerp(original, adjusted, Factor), as a color mix."""
    n = tree.nodes.new('ShaderNodeMix')
    n.data_type = 'RGBA'
    n.blend_type = 'MIX'
    n.clamp_result = True
    n.inputs[0].default_value = 1.0
    return n


def mix_socket(node, ident):
    return next(s for s in node.inputs if s.identifier == ident)


def mix_output(node):
    return next(o for o in node.outputs if o.identifier == "Result_Color")
