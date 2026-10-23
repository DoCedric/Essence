import bpy

from harness import check
from test_filters import _add_filter, _entry, _setup


def _remap(addon, mat, mask_uid):
    return addon.sources.source_nodes(mat.node_tree, mask_uid, 'MASK')['REMAP']


def _stale_or_missing(addon, mat, uid):
    return addon.filters.needs_apply(mat, _entry(mat, uid))


def _two_filters(mat, mask_uid):
    f1 = _add_filter(mat, mask_uid, 'BLUR')
    f2 = _add_filter(mat, mask_uid, 'SHARPEN')
    return f1, f2


def test_applying_the_upper_filter_applies_the_lower_one_first(addon):
    ob, mat, top, mask_uid, img = _setup()
    f1, f2 = _two_filters(mat, mask_uid)
    check(_stale_or_missing(addon, mat, f1) and _stale_or_missing(addon, mat, f2), "setup")
    check(bpy.ops.essence.filter_apply(uid=f2) == {'FINISHED'}, "apply failed")
    check(not _stale_or_missing(addon, mat, f1), "the lower filter must have been applied too")
    check(not _stale_or_missing(addon, mat, f2), "and the one asked for")
    # f2 was baked from f1's result, not from the raw value.
    check(addon.filters.result_image(f1) is not None, "lower result missing")


def test_a_stale_lower_filter_is_reapplied_before_the_upper_one(addon):
    ob, mat, top, mask_uid, img = _setup()
    f1, f2 = _two_filters(mat, mask_uid)
    bpy.ops.essence.filter_apply(uid=f2)
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(_stale_or_missing(addon, mat, f1) and _stale_or_missing(addon, mat, f2),
          "both depend on the base")
    sig1 = _entry(mat, f1)["essence_sig"]
    check(bpy.ops.essence.filter_apply(uid=f2) == {'FINISHED'}, "apply failed")
    check(_entry(mat, f1)["essence_sig"] != sig1, "the lower filter was baked again")
    check(not _stale_or_missing(addon, mat, f1) and not _stale_or_missing(addon, mat, f2),
          "both current")


def test_applying_the_lower_filter_makes_the_upper_one_stale(addon):
    ob, mat, top, mask_uid, img = _setup()
    f1, f2 = _two_filters(mat, mask_uid)
    bpy.ops.essence.filter_apply(uid=f2)
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    bpy.ops.essence.filter_apply(uid=f1)
    check(not _stale_or_missing(addon, mat, f1), "lower current")
    check(_stale_or_missing(addon, mat, f2), "the filter above is out of date: its input changed")


def test_apply_all_applies_everything_in_order(addon):
    ob, mat, top, mask_uid, img = _setup()
    f1, f2 = _two_filters(mat, mask_uid)
    bpy.ops.essence.filter_apply(uid=f1)                   # f2 not applied yet
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5   # f1 stale too
    f3 = _add_filter(mat, mask_uid, 'BLUR')                # never applied
    check(bpy.ops.essence.filter_apply_all() == {'FINISHED'}, "apply all failed")
    for f in (f1, f2, f3):
        check(not _stale_or_missing(addon, mat, f), f"{_entry(mat, f).name} should be current")
    check(not addon.filters.any_unapplied(mat), "nothing left to apply")
    # Nothing to do the second time.
    check(bpy.ops.essence.filter_apply_all() == {'FINISHED'}, "second apply all failed")


def test_apply_all_needs_a_filter_and_the_button_shows_the_state(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, top, mask_uid, img = _setup()
    check(not bpy.ops.essence.filter_apply_all.poll(), "no filters: nothing to apply")

    def button():
        log = _draw(ui._stack, ob)
        return [c for c in log if c[0] == "operator" and c[1][0] == "essence.filter_apply_all"]

    check(not button(), "no button without filters")
    f = _add_filter(mat, mask_uid, 'BLUR')
    check(button()[0][2].get("icon") == 'STRIP_COLOR_01', "unapplied: red")
    bpy.ops.essence.filter_apply_all()
    check(button()[0][2].get("icon") == 'STRIP_COLOR_04', "applied: green")
    mat.essence.use_tree = False
    check(button()[0][2].get("icon") == 'STRIP_COLOR_04', "the list view has it too")
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(button()[0][2].get("icon") == 'STRIP_COLOR_01', "out of date: red")


def test_a_failed_apply_reports_and_changes_nothing_else(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _add_filter(mat, mask_uid, 'BLUR')
    # No UV map: the operator refuses with an error instead of baking.
    while ob.data.uv_layers:
        ob.data.uv_layers.remove(ob.data.uv_layers[0])
    try:
        bpy.ops.essence.filter_apply(uid=f)
        raise AssertionError("expected an error without a UV map")
    except RuntimeError:
        pass
    check(not addon.filters.is_baked(_entry(mat, f)), "nothing should have been stored")
