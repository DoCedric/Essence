import bpy

from harness import check
from test_filters import _add_filter, _entry, _setup


def _remap(addon, mat, mask_uid):
    """The Map Range node of the mask's image source: a value below any filter."""
    return addon.sources.source_nodes(mat.node_tree, mask_uid, 'MASK')['REMAP']


def _applied(mat, mask_uid, filter_type='BLUR'):
    f = _add_filter(mat, mask_uid, filter_type)
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    return f


def _stale(addon, mat, uid):
    return addon.filters.is_stale(mat, _entry(mat, uid))


def test_a_fresh_result_is_current_and_layout_changes_do_not_matter(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _applied(mat, mask_uid)
    check(not _stale(addon, mat, f), "a freshly applied filter must be current")
    node = _remap(addon, mat, mask_uid)
    node.location = (node.location.x + 500, node.location.y)
    node.label = "renamed"
    _entry(mat, f).name = "Soft edge"
    check(not _stale(addon, mat, f), "moving or renaming nodes must not make it stale")


def test_changes_below_make_it_stale_and_apply_clears_it(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _applied(mat, mask_uid)
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(_stale(addon, mat, f), "a value change below the filter should make it stale")
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    check(not _stale(addon, mat, f), "apply brings it up to date")


def test_items_below_count_and_items_above_do_not(addon):
    ob, mat, top, mask_uid, img = _setup()
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')        # below the filter
    adj = mat.essence.layers[mat.essence.active_index].uid
    f = _applied(mat, mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='GAMMA')         # above the filter
    above = mat.essence.layers[mat.essence.active_index].uid
    check(not _stale(addon, mat, f), "adding an item above must not stale the filter")
    addon.sources.source_nodes(mat.node_tree, above, 'ADJ')['MAIN'].inputs['Gamma'] \
        .default_value = 3.0
    check(not _stale(addon, mat, f), "changing an item above must not stale the filter")
    _entry(mat, adj).adj_enabled = False
    check(_stale(addon, mat, f), "switching off an item below makes it stale")
    _entry(mat, adj).adj_enabled = True
    check(not _stale(addon, mat, f), "back to the baked state: current again")


def test_own_settings_make_it_stale(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _applied(mat, mask_uid)
    addon.filters.filter_node(_entry(mat, f)).inputs['Size'].default_value = (3.0, 3.0)
    check(_stale(addon, mat, f), "the filter's own settings are part of what it baked")
    check(bpy.ops.essence.filter_apply(uid=f) == {'FINISHED'}, "apply failed")
    check(not _stale(addon, mat, f), "current after apply")
    _entry(mat, f).filter_resolution = '512'
    check(_stale(addon, mat, f), "resolution change")


def test_stacked_filters_cascade(addon):
    ob, mat, top, mask_uid, img = _setup()
    f1 = _applied(mat, mask_uid, 'BLUR')
    f2 = _applied(mat, mask_uid, 'SHARPEN')
    check(not _stale(addon, mat, f1) and not _stale(addon, mat, f2), "both applied")
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(_stale(addon, mat, f1) and _stale(addon, mat, f2), "both depend on the base")
    bpy.ops.essence.filter_apply(uid=f1)
    check(not _stale(addon, mat, f1), "the lower filter is current")
    check(_stale(addon, mat, f2), "the filter above it is out of date until it is applied too")
    bpy.ops.essence.filter_apply(uid=f2)
    check(not _stale(addon, mat, f2), "both current")


def test_panel_row_and_button_show_the_state(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, top, mask_uid, img = _setup()
    f = _add_filter(mat, mask_uid, 'BLUR')
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == f)

    def button():
        log = _draw(ui._adjustment, ob)
        return next(c for c in log if c[0] == "operator" and c[1][0] == "essence.filter_apply")

    def row_icon():
        return [c[2].get("icon") for c in _draw(ui._stack, ob) if c[0] == "label"]

    check(button()[2].get("icon") == 'SEQUENCE_COLOR_01', "not applied: red dot")
    check('SEQUENCE_COLOR_01' in row_icon(), "not applied: the row shows a red dot")
    bpy.ops.essence.filter_apply(uid=f)
    check(button()[2].get("icon") == 'SEQUENCE_COLOR_04', "current: green dot")
    check('SEQUENCE_COLOR_04' in row_icon(), "current: the row shows a green dot")
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(button()[2].get("icon") == 'SEQUENCE_COLOR_01', "out of date: red dot")
    check('SEQUENCE_COLOR_01' in row_icon(), "out of date: red row")
