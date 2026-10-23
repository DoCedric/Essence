"""Run the panel draw functions against a recording layout. Headless Blender has no UI, so this
cannot judge how things look, but it catches logic errors: missing sockets, bad property names,
wrong keys for a channel set."""

from types import SimpleNamespace

import bpy

from harness import check, make_plane


class Rec:
    """Stands in for a UILayout and records what is drawn."""

    def __init__(self, log=None):
        self.log = log if log is not None else []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.log.append((name, args, kwargs))
            if name == "prop" and len(args) >= 2:
                data, prop = args[0], args[1]
                props = data.bl_rna.properties
                check(prop in props, f"{type(data).__name__} has no property {prop!r}")
            if name == "operator" and args:
                check(hasattr(bpy.ops.essence, args[0].split(".")[1]) or "." not in args[0],
                      f"unknown operator {args[0]}")
            return self if name in ("box", "column", "row", "split", "grid_flow") else Rec(self.log)
        return call

    def __setattr__(self, name, value):
        if name == "log":
            object.__setattr__(self, name, value)


def _ctx(ob):
    return SimpleNamespace(active_object=ob, scene=bpy.context.scene)


def _draw(fn, ob):
    panel = SimpleNamespace(layout=Rec())
    fn(panel, _ctx(ob))
    return panel.layout.log


def _setup(addon):
    ob = make_plane()
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    return ob, ob.active_material


def _select(mat, name):
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(name)


def test_draw_all_panels(addon):
    from essence import ui
    ob, mat = _setup(addon)
    for fn in (ui._stack, ui._material, ui._bake, ui._export):
        _draw(fn, ob)
    _select(mat, "Layer 1")
    _draw(ui._props, ob)
    _select(mat, mat.essence.layers[0].name)
    _draw(ui._mask, ob)


def test_draw_layer_with_every_source_and_channel(addon):
    from essence import ui
    ob, mat = _setup(addon)
    for ch in mat.essence.channels:
        ch.enabled = True
    layer = next(e for e in mat.essence.layers if e.kind == 'LAYER')
    for s in layer.sources:
        for kind in ('VALUE', 'IMAGE', 'PROCEDURAL'):
            s.kind = kind
            _select(mat, layer.name)
            _draw(ui._props, ob)
    s = next(x for x in layer.sources if x.channel == 'COLOR')
    s.kind = 'PROCEDURAL'
    s.projection = 'TRIPLANAR'
    _select(mat, layer.name)
    _draw(ui._props, ob)


def test_draw_mask_with_every_source(addon):
    from essence import ui
    ob, mat = _setup(addon)
    m = next(e for e in mat.essence.layers if e.kind == 'MASK')
    for kind in ('IMAGE', 'PROCEDURAL', 'VALUE', 'IMAGE'):
        m.source.kind = kind
        _select(mat, m.name)
        _draw(ui._mask, ob)
    m.source.projection = 'TRIPLANAR'
    _draw(ui._mask, ob)


def test_draw_folder_and_list_rows(addon):
    from essence import ui
    ob, mat = _setup(addon)
    bpy.ops.essence.add_folder()
    _draw(ui._props, ob)
    _draw(ui._stack, ob)
    # list rows
    for item in mat.essence.layers:
        ui.ESSENCE_UL_layers.draw_item(None, _ctx(ob), Rec(), mat.essence, item, 0,
                                       mat.essence, "active_index", 0)


def test_material_panel_lists_channels(addon):
    from essence import ui
    ob, mat = _setup(addon)
    log = _draw(ui._material, ob)
    shown = [a[0].id for n, a, k in log if n == "prop" and a[1] == "enabled"]
    check(shown == [c.id for c in addon.channels.CHANNELS], f"channel checkboxes: {shown}")


def test_material_panel_emission_strength_and_transform(addon):
    from essence import ui
    ob, mat = _setup(addon)
    log = _draw(ui._material, ob)
    check(not any(k.get("text") == "Emission Strength" for n, a, k in log),
          "emission strength shown without the channel")
    next(c for c in mat.essence.channels if c.id == 'EMISSION').enabled = True
    log = _draw(ui._material, ob)
    check(any(k.get("text") == "Emission Strength" for n, a, k in log),
          "emission strength missing with the channel on")
    # the transform settings stay hidden until expanded
    layer = next(e for e in mat.essence.layers if e.kind == 'LAYER')
    src = next(s for s in layer.sources if s.channel == 'ROUGHNESS')
    src.kind = 'PROCEDURAL'
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layer.name)
    log = _draw(ui._props, ob)
    check(not any(k.get("text") == "Tiling" for n, a, k in log), "transform shown collapsed")
    src.show_transform = True
    log = _draw(ui._props, ob)
    check(any(k.get("text") == "Tiling" for n, a, k in log), "transform missing when expanded")


def test_panels_survive_stale_channel_data(addon):
    """A file from before Normal stopped being a channel still lists it until a rebuild."""
    from essence import ui
    ob, mat = _setup(addon)
    state = mat.essence.channels.add()
    state.id = 'NORMAL'
    layer = next(e for e in mat.essence.layers if e.kind == 'LAYER')
    s = layer.sources.add()
    s.channel = 'NORMAL'
    s.uid = layer.uid
    mat.essence.active_index = [e.name for e in mat.essence.layers].index(layer.name)
    for fn in (ui._material, ui._props, ui._stack, ui._maps):
        _draw(fn, ob)


def test_maps_panel_draws_the_ao_strength_slider(addon):
    from essence import ui
    ob, mat = _setup(addon)
    mat.node_tree.nodes[addon.nodes.map_slot_name('AO')].image = bpy.data.images.new("ao", 8, 8)
    bpy.ops.essence.rebuild()
    log = _draw(ui._maps, ob)
    check(any(c[0] == "prop" and c[2].get("text") == "Strength" and c[2].get("slider")
              for c in log), "AO strength slider not drawn")
