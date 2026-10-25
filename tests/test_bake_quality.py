import math

import bpy

from harness import baked, check, make_plane, texset


def _bake(map_type="NORMAL_WORLD", size="128"):
    sc = bpy.context.scene
    sc.essence_bake_map = map_type
    for o in bpy.context.selected_objects:
        if o.type == 'MESH':
            texset(o).bake_size = size
    sc.essence_bake_samples = 4
    sc.essence_bake_margin = 0
    return bpy.ops.essence.bake()


def _center(img):
    w, h = img.size
    i = ((h // 2) * w + w // 2) * 4
    return list(img.pixels)[i:i + 4]


def _high_collection(z, tilt):
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0, 0, z))
    high = bpy.context.active_object
    high.name = "High"
    high.rotation_euler = (math.radians(tilt), 0, 0)
    coll = bpy.data.collections.new("HighPoly")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(high)
    bpy.context.scene.collection.objects.unlink(high)
    return coll


def _select(ob):
    for o in bpy.context.view_layer.objects:
        o.select_set(o is ob)
    bpy.context.view_layer.objects.active = ob


def test_cage_reaches_distant_high_poly(addon):
    low = make_plane("Low", size=2.0)
    texset(low).bake_high = _high_collection(z=0.5, tilt=20)
    _select(low)
    # Auto extrusion is ~0.06, so the tilted plane 0.5 above is out of reach: no cage, no hit.
    check(_bake() == {'FINISHED'}, "bake without cage failed")
    miss = _center(baked("Low", "NormalWorld"))
    check(max(miss[:3]) < 0.01, f"expected a miss (untouched black), got {miss}")

    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0.8))
    cage = bpy.context.active_object
    cage.name = "Cage"
    _select(low)
    low.essence_bake_cage = cage
    check(_bake() == {'FINISHED'}, "bake with cage failed")
    hit = _center(baked("Low", "NormalWorld"))
    # World normal of a plane tilted 20 degrees about X: (0, -sin20, cos20) -> 0-1 remap.
    check(abs(hit[1] - (0.5 - math.sin(math.radians(20)) / 2)) < 0.05 or
          abs(hit[1] - (0.5 + math.sin(math.radians(20)) / 2)) < 0.05,
          f"cage bake did not capture the tilted high-poly normal: {hit}")
    check(abs(hit[2] - (0.5 + math.cos(math.radians(20)) / 2)) < 0.05, f"wrong z: {hit}")


def test_cage_settings_restored(addon):
    low = make_plane("Low", size=2.0)
    sc = bpy.context.scene
    texset(low).bake_high = _high_collection(z=0.0, tilt=20)
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0.3))
    low.essence_bake_cage = bpy.context.active_object
    _select(low)
    bake = sc.render.bake
    before = (bake.use_cage, bake.cage_object, bake.max_ray_distance)
    check(_bake() == {'FINISHED'}, "bake failed")
    after = (bake.use_cage, bake.cage_object, bake.max_ray_distance)
    check(before == after, f"bake settings not restored: {before} -> {after}")


def test_bake_image_is_float_and_switchable(addon):
    make_plane("P", size=2.0)
    sc = bpy.context.scene
    check(sc.essence_bake_float, "float should default on")
    check(_bake("AO") == {'FINISHED'}, "bake failed")
    img = baked("P", "AO")
    check(img.is_float, "baked image is not float")
    check(img.packed_file is not None, "baked image not packed")
    # Switching to 8-bit keeps the image name and its users.
    holder = bpy.data.materials.new("Holder")
    holder.use_nodes = True
    n = holder.node_tree.nodes.new('ShaderNodeTexImage')
    n.image = img
    sc.essence_bake_float = False
    check(_bake("AO") == {'FINISHED'}, "8-bit rebake failed")
    img2 = baked("P", "AO")
    check(not img2.is_float, "image should now be 8-bit")
    check(n.image is img2, "reference to the baked image was lost")


def test_new_paint_mask_is_float(addon):
    from harness import make_plane  # noqa: F811
    ob = make_plane("P", size=2.0)
    mat = bpy.data.materials.new("M")
    mat.use_nodes = True
    ob.data.materials.append(mat)
    bpy.ops.essence.add_layer()
    bpy.ops.essence.add_mask(source='PAINT', fill='WHITE')
    imgs = [i for i in bpy.data.images if "Mask" in i.name]
    check(imgs, "no mask image created")
    check(all(i.is_float for i in imgs), "paint mask is not a float image")
    check(imgs[0].colorspace_settings.name == 'Non-Color', "mask not Non-Color")


def _edge_row(aa):
    """Bake the world normal of a high-poly plane whose edge falls mid-pixel; return one
    row of the green channel."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    from harness import load_addon
    addon = load_addon()
    addon.register()
    try:
        low = make_plane("Low", size=2.0)
        coll = _high_collection(z=0.01, tilt=0)
        high = coll.objects[0]
        # 2 units wide, left edge at x = -0.9766: halfway into the second pixel of 128.
        high.scale = (0.5, 0.5, 1.0)
        high.location.x = 0.0234
        _select(low)
        sc = bpy.context.scene
        texset(low).bake_high = coll
        sc.essence_bake_aa = aa
        check(_bake() == {'FINISHED'}, f"bake with aa={aa} failed")
        img = baked("Low", "NormalWorld")
        check(tuple(img.size) == (128, 128), f"final size should stay 128, got {tuple(img.size)}")
        check(img.is_float, "image should be float")
        w = img.size[0]
        row = list(img.pixels)[(w // 2) * w * 4:(w // 2 + 1) * w * 4]
        return [row[i * 4 + 1] for i in range(w)]
    finally:
        addon.unregister()


def test_bake_antialiasing_smooths_edges(addon):
    hard = _edge_row('1')
    soft = _edge_row('4')
    full = max(hard)
    partial = lambda row: [v for v in row if 0.02 * full < v < 0.98 * full]  # noqa: E731
    check(full > 0.3, f"high-poly left no mark: {full}")
    check(not partial(hard), f"1x bake should have only hard edges: {partial(hard)}")
    check(len(partial(soft)) >= 1, "4x bake should have an intermediate value on the edge")
    check(abs(max(soft) - full) < 0.02, "interior value changed with anti-aliasing")


def test_bake_aa_size_limit(addon):
    ob = make_plane("P", size=2.0)
    sc = bpy.context.scene
    texset(ob).bake_size = "4096"
    sc.essence_bake_aa = '4'
    sc.essence_bake_map = 'AO'
    try:
        bpy.ops.essence.bake()
        raise AssertionError("expected the size limit to cancel the bake")
    except RuntimeError as e:
        check("limit" in str(e), f"unexpected error: {e}")
    check(baked("P", "AO") is None, "no image should be created when over the limit")


def test_rebake_at_a_new_size_replaces_the_image(addon):
    ob = make_plane("P", size=2.0)
    sc = bpy.context.scene
    sc.essence_bake_map = 'AO'
    sc.essence_bake_samples = 2
    sc.essence_bake_margin = 0
    holder = bpy.data.materials.new("Holder")
    holder.use_nodes = True
    node = holder.node_tree.nodes.new('ShaderNodeTexImage')
    for size, aa, expect in (("128", '1', 128), ("256", '1', 256), ("256", '2', 256),
                             ("128", '2', 128)):
        texset(ob).bake_size = size
        sc.essence_bake_aa = aa
        check(bpy.ops.essence.bake() == {'FINISHED'}, f"bake {size} aa{aa} failed")
        img = baked("P", "AO")
        check(tuple(img.size) == (expect, expect), f"{size} aa{aa}: image is {tuple(img.size)}")
        check(len(img.pixels) == expect * expect * 4, "pixel buffer size differs from image size")
        node.image = img
    check(node.image is baked("P", "AO"), "reference to the baked image was lost")
    check(len([i for i in bpy.data.images if i.name.startswith(baked("P", "AO").name)]) == 1,
          "old bake images were left behind")


def test_bakes_leave_the_scene_engine_alone_when_it_is_already_cycles(addon):
    """Assigning the render engine, even to the value it has, resets every viewport (it
    deadlocked Blender with a heavy material in Material Preview), so a bake must not."""
    from essence.bake import set_if_changed

    class Probe:
        def __init__(self):
            self.value, self.sets = 'CYCLES', 0

        def __setattr__(self, name, v):
            if name == 'engine':
                object.__setattr__(self, 'sets', self.sets + 1)
            object.__setattr__(self, name, v)

    p = Probe()
    p.engine = 'CYCLES'
    p.sets = 0
    check(set_if_changed(p, 'engine', 'CYCLES') is False and p.sets == 0,
          "the same value must not be assigned")
    check(set_if_changed(p, 'engine', 'BLENDER_EEVEE') is True and p.sets == 1, "a change is")
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    ob = make_plane("Probe", size=2.0)
    bpy.ops.essence.add_layer()
    mat = ob.active_material
    from essence import filters
    log = []
    original = filters.log
    filters.log = lambda m: (log.append(m), original(m))[0]
    try:
        bpy.ops.essence.add_layer()
        bpy.ops.essence.add_mask(source='PAINT', fill='WHITE', size=64)
        bpy.ops.essence.add_filter(filter_type='BLUR')
        f = mat.essence.layers[mat.essence.active_index]
        bpy.ops.essence.filter_apply(uid=f.uid)
    finally:
        filters.log = original
    check(any("Cycles already, left alone" in m for m in log),
          f"the filter bake should leave an engine that is already Cycles alone: {log[:12]}")
    check(not any("switching the scene engine" in m for m in log), "no engine switch was made")
    check(sc.render.engine == 'CYCLES', "and it is still Cycles")


def _with_gpu_prefs(fn):
    """Run `fn` with Cycles' Preferences set to a GPU type if this machine has one, and put the
    Preferences back. Returns False when there is no GPU to test with."""
    prefs = bpy.context.preferences.addons['cycles'].preferences
    old = prefs.compute_device_type
    for kind in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.refresh_devices()
        if prefs.has_active_device():
            try:
                fn()
            finally:
                prefs.compute_device_type = old
            return True
    prefs.compute_device_type = old
    return False


def test_bakes_run_on_the_gpu_when_there_is_one(addon):
    from essence import bake
    sc = bpy.context.scene
    ob = make_plane("Gpu", size=2.0)
    check(sc.essence_bake_device == 'AUTO', "the default is Auto")
    # no GPU switched on (factory preferences): the CPU, and the panel says why
    device, note = bake.resolve_device(sc)
    check(device == 'CPU' and "no GPU" in note, f"{device}: {note}")
    seen = []
    original = bake.ESSENCE_OT_bake._bake_object

    def spy(self, context, obj, *args, **kwargs):
        seen.append(context.scene.cycles.device)
        return original(self, context, obj, *args, **kwargs)

    bake.ESSENCE_OT_bake._bake_object = spy
    try:
        sc.cycles.device = 'CPU'
        check(_bake('AO') == {'FINISHED'}, "bake failed")
        check(seen == ['CPU'], f"no GPU enabled: baked on {seen}")
        check(sc.cycles.device == 'CPU', "the scene's device is as it was")

        def on_gpu():
            check(bake.gpu_device_name() is not None, "the GPU should be found")
            d, n = bake.resolve_device(sc)
            check(d == 'GPU' and n.startswith("the GPU"), f"{d}: {n}")
            seen.clear()
            sc.cycles.device = 'CPU'
            _select(ob)
            check(_bake('AO') == {'FINISHED'}, "bake on the GPU failed")
            check(seen == ['GPU'], f"Auto should use the GPU: {seen}")
            check(sc.cycles.device == 'CPU', "and put the scene's device back")
            sc.essence_bake_device = 'CPU'
            seen.clear()
            _select(ob)
            check(_bake('AO') == {'FINISHED'}, "bake on the CPU failed")
            check(seen == ['CPU'], f"CPU means CPU: {seen}")
            sc.essence_bake_device = 'AUTO'
        if not _with_gpu_prefs(on_gpu):
            print("note: no GPU on this machine, the GPU path was not run")
    finally:
        bake.ESSENCE_OT_bake._bake_object = original


def test_a_failed_gpu_bake_is_retried_on_the_cpu(addon):
    from essence import bake
    sc = bpy.context.scene
    ob = make_plane("Retry", size=2.0)
    original_name = bake.gpu_device_name
    original = bake.ESSENCE_OT_bake._bake_object
    devices = []

    def flaky(self, context, obj, *args, **kwargs):
        devices.append(context.scene.cycles.device)
        if devices[-1] == 'GPU':                 # the GPU always fails, the CPU works
            raise RuntimeError("out of memory")
        return original(self, context, obj, *args, **kwargs)

    bake.gpu_device_name = lambda: "Pretend GPU"
    bake.ESSENCE_OT_bake._bake_object = flaky
    try:
        sc.cycles.device = 'CPU'
        check(_bake('AO') == {'FINISHED'}, "the bake should succeed on the CPU")
        check(devices[:2] == ['GPU', 'CPU'], f"GPU first, then the CPU: {devices}")
        check(sc.cycles.device == 'CPU', "the scene's device is back")
        check(baked("Retry", "AO") is not None, "the image was made")
        # choosing the CPU never tries the GPU
        devices.clear()
        sc.essence_bake_device = 'CPU'
        _select(ob)
        _bake('AO')
        check(devices[0] == 'CPU', f"CPU chosen: {devices}")
    finally:
        bake.gpu_device_name = original_name
        bake.ESSENCE_OT_bake._bake_object = original


def test_the_bake_panel_shows_the_device(addon):
    from essence import bake, ui
    from test_ui import _draw
    ob = make_plane("Panel", size=2.0)
    log = _draw(ui._bake, ob)
    check(any(c[0] == "prop" and c[1][1] == "essence_bake_device" for c in log), "no Device option")
    said = [c[2].get("text", "") for c in log if c[0] == "label"]
    check(any(t.startswith("Bakes on the CPU: no GPU") for t in said), f"no device line: {said}")
    original = bake.gpu_device_name
    bake.gpu_device_name = lambda: "Some GPU"
    try:
        said = [c[2].get("text", "") for c in _draw(ui._bake, ob) if c[0] == "label"]
        check(any(t == "Bakes on the GPU: Some GPU" for t in said), f"{said}")
    finally:
        bake.gpu_device_name = original
