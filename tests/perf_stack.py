"""Performance measurement of deep stacks (not part of run_all: it takes minutes).

    blender --background --factory-startup --python tests/perf_stack.py [-- N,N,N [engines]]

For each stack depth and scenario it reports build time (Python), and the wall time of a
tiny render (cold = includes shader compile / scene sync; warm = same scene again; edit =
one structural change then render, i.e. what adding a layer costs). Engines: CYCLES (CPU),
BLENDER_EEVEE (needs a GPU context; skipped when rendering fails).
"""

import os
import sys
import tempfile
import time

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DEPTHS = [int(x) for x in argv[0].split(",")] if argv else [5, 20, 50]
ENGINES = argv[1].split(",") if len(argv) > 1 else ["CYCLES", "BLENDER_EEVEE"]
# plain: value layers; procedural: noise on color, bump, roughness plus a noise mask;
# *_disabled / *_hidden: the same with every channel Enable off / the layer's Visible off
# (the constant-folding question: do switched-off layers still cost anything?)
SCENARIOS = argv[2].split(",") if len(argv) > 2 else ["plain", "procedural"]


def build(depth, scenario):
    ob = harness.make_plane("P", size=2.0)
    mat = bpy.data.materials.new("M")
    mat.use_nodes = True
    ob.data.materials.append(mat)
    for i in range(depth):
        bpy.ops.essence.add_layer()
        e = mat.essence.layers[mat.essence.active_index]
        layer_node, layer_name = e.node_name, e.name  # `e` goes stale when the list grows
        if scenario.startswith('procedural'):
            for s in e.sources:
                if s.channel in ('BASE_COLOR', 'BUMP', 'ROUGHNESS'):
                    s.kind = 'PROCEDURAL'
            bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=64)
            m = mat.essence.layers[mat.essence.active_index]
            m.source.kind = 'PROCEDURAL'
            mat.essence.active_index = mat.essence.layers.find(layer_name)
            node = mat.node_tree.nodes[layer_node]
            if scenario == 'procedural_disabled':
                for sock in node.inputs:
                    if sock.name.endswith(" Enable"):
                        sock.default_value = False
            elif scenario == 'procedural_hidden':
                node.inputs["Visible"].default_value = False
    return ob, mat


def render(engine):
    sc = bpy.context.scene
    sc.render.engine = engine
    sc.render.resolution_x = sc.render.resolution_y = 512 if engine == 'CYCLES' else 128
    sc.render.resolution_percentage = 100
    if engine == 'CYCLES':
        sc.cycles.device = 'CPU'
        sc.cycles.samples = 8
        sc.cycles.use_denoising = False
    if not any(o.type == 'CAMERA' for o in sc.objects):
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        sc.collection.objects.link(cam)
        cam.location = (0, 0, 4)
        sc.camera = cam
    sc.render.filepath = os.path.join(tempfile.gettempdir(), "essence_perf.png")
    if os.path.exists(sc.render.filepath):
        os.remove(sc.render.filepath)
    bpy.context.view_layer.update()
    t = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    dt = time.perf_counter() - t
    if not os.path.exists(sc.render.filepath):
        raise RuntimeError("render produced no image")
    return dt


def main():
    print("engine,scenario,depth,build_s,cold_s,warm_s,add_layer_s")
    for engine in ENGINES:
        for scenario in SCENARIOS:
            for depth in DEPTHS:
                addon = harness.fresh_scene()
                try:
                    t = time.perf_counter()
                    ob, mat = build(depth, scenario)
                    built = time.perf_counter() - t
                    cold = render(engine)
                    warm = render(engine)
                    mat.essence.active_index = len(mat.essence.layers) - 1
                    bpy.ops.essence.add_layer()
                    added = render(engine)
                    print(f"{engine},{scenario},{depth},{built:.2f},{cold:.2f},{warm:.2f},{added:.2f}")
                except Exception as e:  # noqa: BLE001
                    print(f"{engine},{scenario},{depth},SKIPPED {type(e).__name__}: {e}")
                finally:
                    addon.unregister()
                sys.stdout.flush()


main()
os._exit(0)
