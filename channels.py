"""Channel definitions. Adding a channel means adding an entry here.

The layer node group, the stack wiring and the UI are all generated from CHANNELS.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Channel:
    id: str
    label: str               # socket base name: "<label> A", "<label> B", output "<label>"
    socket_type: str         # NodeSocketColor / NodeSocketFloat
    blend: str               # 'MIX' (lerp by mask) or 'ADD' (A + B * mask)
    default: object          # default for A (neutral) and B (layer value)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    subtype: str = 'NONE'
    # (node kind, socket name) the end of the stack is wired into.
    target: Tuple[str, str] = ('BSDF', '')
    # Layer sources (see sources.py): color space of an image feeding this channel, and the
    # value range that a float channel's image (0 to 1) or procedural (0 to 1) is mapped to.
    colorspace: str = 'Non-Color'
    image_range: Tuple[float, float] = (0.0, 1.0)
    proc_range: Tuple[float, float] = (0.0, 1.0)
    ramp_color: Optional[Tuple[float, float, float, float]] = None  # procedural color ramp end
    # Channels the material has by default. The others are switched on in the Channels list.
    default_enabled: bool = True
    # BSDF inputs set when the channel is switched on or off: (socket, on value, off value).
    enable_values: Tuple = ()


CHANNELS = (
    Channel('COLOR', 'Color', 'NodeSocketColor', 'MIX',
            (0.8, 0.8, 0.8, 1.0), target=('BSDF', 'Base Color'), colorspace='sRGB'),
    Channel('ROUGHNESS', 'Roughness', 'NodeSocketFloat', 'MIX',
            0.5, 0.0, 1.0, 'FACTOR', target=('BSDF', 'Roughness')),
    Channel('METALLIC', 'Metallic', 'NodeSocketFloat', 'MIX',
            0.0, 0.0, 1.0, 'FACTOR', target=('BSDF', 'Metallic')),
    # Height images follow Substance: black = -1, mid grey = 0, white = 1.
    Channel('BUMP', 'Bump', 'NodeSocketFloat', 'ADD',
            0.0, -1.0, 1.0, 'NONE', target=('BUMP', 'Height'),
            image_range=(-1.0, 1.0), proc_range=(0.0, 1.0)),
    Channel('ALPHA', 'Alpha', 'NodeSocketFloat', 'MIX',
            1.0, 0.0, 1.0, 'FACTOR', target=('BSDF', 'Alpha'), default_enabled=False),
    Channel('EMISSION', 'Emission', 'NodeSocketColor', 'MIX',
            (0.0, 0.0, 0.0, 1.0), target=('BSDF', 'Emission Color'), colorspace='sRGB',
            ramp_color=(1.0, 1.0, 1.0, 1.0), default_enabled=False,
            enable_values=(('Emission Strength', 1.0, 0.0),)),
    Channel('SPECULAR', 'Specular', 'NodeSocketFloat', 'MIX',
            0.5, 0.0, 1.0, 'FACTOR', target=('BSDF', 'Specular IOR Level'),
            default_enabled=False),
    Channel('SPECULAR_TINT', 'Specular Tint', 'NodeSocketColor', 'MIX',
            (1.0, 1.0, 1.0, 1.0), target=('BSDF', 'Specular Tint'), colorspace='sRGB',
            ramp_color=(1.0, 1.0, 1.0, 1.0), default_enabled=False),
    # Painted or filled occlusion, multiplied with the AO mesh map: the product drives the AO
    # overlay and is what export writes as AO (see nodes.apply_ao).
    Channel('AO', 'Ambient Occlusion', 'NodeSocketFloat', 'MIX',
            1.0, 0.0, 1.0, 'FACTOR', target=('AO', ''), default_enabled=False),
)

BY_ID = {c.id: c for c in CHANNELS}

MASK_SOCKET = "Mask"

# A mask's source works like a channel's (see sources.py) and feeds the mask group's Value
# input. It is not a layer channel, so it is not part of CHANNELS.
MASK_SOURCE = Channel('MASK', 'Mask', 'NodeSocketFloat', 'MIX', 1.0, 0.0, 1.0, 'FACTOR')
SOURCE_BY_ID = {**BY_ID, MASK_SOURCE.id: MASK_SOURCE}


def active_channels(mat):
    """The channels a material has, in channel order. A material that never chose has the
    default ones."""
    state = {c.id: c.enabled for c in mat.essence.channels}
    return [c for c in CHANNELS if state.get(c.id, c.default_enabled)]
