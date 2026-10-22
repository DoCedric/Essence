"""Headless test harness. Run through run_all.py:

    blender --background --factory-startup --python tests/run_all.py [-- name_filter]

Loads the repo folder as the package `essence`, registers it into a clean scene and gives
tests helpers for building scratch objects and reading real pixel values back out of
Cycles (the same Emission trick the export uses).
"""

import importlib.util
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "essence"


def load_addon():
    """Import (or re-import) the repo as package `essence` and return it."""
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        PKG, os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


class Failure(AssertionError):
    pass


def check(cond, msg="check failed"):
    if not cond:
        raise Failure(msg)


def close(a, b, tol=0.02, msg=""):
    if abs(a - b) > tol:
        raise Failure(f"{msg} expected {b}, got {a} (tol {tol})")


def fresh_scene():
    """Empty file with the addon registered. Returns the addon module."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = load_addon()
    addon.register()
    return addon


def make_plane(name="Plane", size=2.0, location=(0, 0, 0)):
    """A UV-unwrapped plane, active and selected."""
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.name = name
    check(len(ob.data.uv_layers) > 0, "plane has no UV map")
    for o in bpy.context.view_layer.objects:
        o.select_set(o is ob)
    return ob


def setup_cycles(samples=1):
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.cycles.samples = samples
    sc.cycles.use_denoising = False


def sample_socket(ob, mat, socket, size=8):
    """Bake what `socket` (of a node in mat.node_tree) outputs and return the mean RGBA of
    the center pixel region. Temporary nodes are removed again. Float sockets read as grey."""
    tree = mat.node_tree
    setup_cycles()
    out = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
    old_links = [(l.from_socket, l.to_socket) for l in out.inputs['Surface'].links]

    emit = tree.nodes.new('ShaderNodeEmission')
    tree.links.new(socket, emit.inputs['Color'])
    tree.links.new(emit.outputs['Emission'], out.inputs['Surface'])
    img = bpy.data.images.new("_sample", size, size, float_buffer=True)
    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tree.nodes.active = tex
    for o in bpy.context.view_layer.objects:
        o.select_set(o is ob)
    bpy.context.view_layer.objects.active = ob
    bpy.context.scene.render.bake.margin = 0
    bpy.ops.object.bake(type='EMIT')
    px = list(img.pixels)
    # Mean of the 2x2 block around the exact center, so UV 0.5 reads as 0.5.
    c = size // 2
    block = [px[((y * size) + x) * 4:((y * size) + x) * 4 + 4]
             for y in (c - 1, c) for x in (c - 1, c)]
    value = tuple(sum(p[i] for p in block) / 4 for i in range(4))

    tree.nodes.remove(tex)
    tree.nodes.remove(emit)
    for a, b in old_links:
        tree.links.new(a, b)
    bpy.data.images.remove(img)
    return value


def run(tests):
    """Run (name, fn) pairs; each gets a fresh scene. Returns the failure count."""
    failed = 0
    for name, fn in tests:
        try:
            addon = fresh_scene()
            fn(addon)
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - report everything
            failed += 1
            import traceback
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            try:
                bpy.context.scene  # noqa: B018
                import essence  # noqa: F401
                essence.unregister()
            except Exception:  # noqa: BLE001
                pass
    return failed
