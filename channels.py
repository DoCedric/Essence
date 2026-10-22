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
)

BY_ID = {c.id: c for c in CHANNELS}

MASK_SOCKET = "Mask"

# A mask's source works like a channel's (see sources.py) and feeds the mask group's Value
# input. It is not a layer channel, so it is not part of CHANNELS.
MASK_SOURCE = Channel('MASK', 'Mask', 'NodeSocketFloat', 'MIX', 1.0, 0.0, 1.0, 'FACTOR')
SOURCE_BY_ID = {**BY_ID, MASK_SOURCE.id: MASK_SOURCE}
