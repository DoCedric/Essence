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


CHANNELS = (
    Channel('COLOR', 'Color', 'NodeSocketColor', 'MIX',
            (0.8, 0.8, 0.8, 1.0), target=('BSDF', 'Base Color')),
    Channel('ROUGHNESS', 'Roughness', 'NodeSocketFloat', 'MIX',
            0.5, 0.0, 1.0, 'FACTOR', target=('BSDF', 'Roughness')),
    Channel('METALLIC', 'Metallic', 'NodeSocketFloat', 'MIX',
            0.0, 0.0, 1.0, 'FACTOR', target=('BSDF', 'Metallic')),
    Channel('BUMP', 'Bump', 'NodeSocketFloat', 'ADD',
            0.0, -1.0, 1.0, 'NONE', target=('BUMP', 'Height')),
)

MASK_SOCKET = "Mask"
