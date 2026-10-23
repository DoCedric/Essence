import bpy

from harness import check
from test_filters import _add_filter, _entry, _setup


def _remap(addon, mat, mask_uid):
    """The Map Range node of the mask's image source: a value below any filter."""
    return addon.sources.source_nodes(mat.node_tree, mask_uid, 'MASK')['REMAP']


def _baked(addon, mat, mask_uid, filter_type='BLUR'):
    f = _add_filter(mat, mask_uid, filter_type)
    check(bpy.ops.essence.filter_refresh(uid=f) == {'FINISHED'}, "refresh failed")
    return f


def _stale(addon, mat, uid):
    return addon.filters.is_stale(mat, _entry(mat, uid))


def _tick(addon, n=1):
    for _ in range(n):
        addon.filters.tick()


def test_a_fresh_result_is_up_to_date_and_layout_changes_do_not_matter(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    check(not _stale(addon, mat, f), "a freshly baked filter must be up to date")
    node = _remap(addon, mat, mask_uid)
    node.location = (node.location.x + 500, node.location.y)
    node.label = "renamed"
    _entry(mat, f).name = "Soft edge"
    check(not _stale(addon, mat, f), "moving or renaming nodes must not make it stale")


def test_changes_below_make_it_stale_and_it_refreshes_once_settled(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    sig = _entry(mat, f)["essence_sig"]
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(_stale(addon, mat, f), "a value change below the filter should make it stale")
    _tick(addon)
    check(_stale(addon, mat, f), "the first tick only notices: the user may still be dragging")
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.6   # still changing
    _tick(addon)
    check(_stale(addon, mat, f), "a value that keeps changing must not bake")
    _tick(addon)                                                         # settled
    check(not _stale(addon, mat, f), "once settled the filter refreshes by itself")
    check(_entry(mat, f)["essence_sig"] != sig, "the baked signature should have moved on")
    check(len([i for i in bpy.data.images if i.name.startswith(".Essence Filter ")
               and not i.name.startswith(".Essence Filter Input")]) == 1, "one result image")


def test_items_below_count_and_items_above_do_not(addon):
    ob, mat, top, mask_uid, img = _setup()
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers)
                                    if e.uid == mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='INVERT')        # below the filter
    adj = mat.essence.layers[mat.essence.active_index].uid
    f = _baked(addon, mat, mask_uid)
    bpy.ops.essence.add_adjustment(adj_type='GAMMA')         # above the filter
    above = mat.essence.layers[mat.essence.active_index].uid
    check(not _stale(addon, mat, f), "adding an item above must not stale the filter")
    addon.sources.source_nodes(mat.node_tree, above, 'ADJ')['MAIN'].inputs['Gamma'] \
        .default_value = 3.0
    check(not _stale(addon, mat, f), "changing an item above must not stale the filter")
    _entry(mat, adj).adj_enabled = False
    check(_stale(addon, mat, f), "switching off an item below makes it stale")
    _entry(mat, adj).adj_enabled = True
    check(not _stale(addon, mat, f), "back to the baked state: up to date again")


def test_own_settings_make_it_stale(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    addon.filters.filter_node(_entry(mat, f)).inputs['Size'].default_value = (3.0, 3.0)
    check(_stale(addon, mat, f), "the filter's own settings are part of what it baked")
    _tick(addon, 2)
    check(not _stale(addon, mat, f), "and it refreshes by itself")
    _entry(mat, f).filter_resolution = '512'
    check(_stale(addon, mat, f), "resolution change")


def test_auto_refresh_can_be_switched_off(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    _entry(mat, f).filter_auto = False
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    _tick(addon, 4)
    check(_stale(addon, mat, f), "auto refresh off: it stays stale")
    check(bpy.ops.essence.filter_refresh(uid=f) == {'FINISHED'}, "manual refresh")
    check(not _stale(addon, mat, f), "manual refresh brings it up to date")


def test_no_auto_refresh_outside_object_mode(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    bpy.ops.object.mode_set(mode='EDIT')
    _tick(addon, 4)
    check(_stale(addon, mat, f), "must not bake while the object is in Edit Mode")
    bpy.ops.object.mode_set(mode='OBJECT')
    _tick(addon, 2)
    check(not _stale(addon, mat, f), "back in Object Mode it catches up")


def test_stacked_filters_refresh_in_order(addon):
    ob, mat, top, mask_uid, img = _setup()
    f1 = _baked(addon, mat, mask_uid, 'BLUR')
    f2 = _baked(addon, mat, mask_uid, 'SHARPEN')
    check(not _stale(addon, mat, f1) and not _stale(addon, mat, f2), "both baked")
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(_stale(addon, mat, f1) and _stale(addon, mat, f2), "both depend on the base")
    _tick(addon, 6)
    check(not _stale(addon, mat, f1) and not _stale(addon, mat, f2),
          "the lower filter refreshes, then the one on top of it")


def test_leaving_texture_paint_marks_filters_stale(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    addon.filters._last_mode[ob.name] = 'TEXTURE_PAINT'     # as if painting just ended
    _tick(addon)
    check(_stale(addon, mat, f), "painting changes pixels: the filter must be refreshed")
    _tick(addon, 2)
    check(not _stale(addon, mat, f), "and it is, once settled")


def test_panel_and_row_show_the_state(addon):
    from essence import ui
    from test_ui import _draw
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    _entry(mat, f).filter_auto = False
    mat.essence.active_index = next(i for i, e in enumerate(mat.essence.layers) if e.uid == f)
    labels = lambda: [c[2].get("text") for c in _draw(ui._adjustment, ob)  # noqa: E731
                      if c[0] == "label"]
    check("Up to date" in labels(), labels())
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    check(any(t and t.startswith("Out of date") for t in labels()), labels())
    icons = [c[2].get("icon") for c in _draw(ui._stack, ob) if c[0] == "label"]
    check('FILE_REFRESH' in icons, "the row of a stale filter should show the refresh icon")


def test_auto_refresh_works_in_texture_paint_and_returns_to_it(addon):
    ob, mat, top, mask_uid, img = _setup()
    f = _baked(addon, mat, mask_uid)
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    check(ob.mode == 'TEXTURE_PAINT', "setup: not in Texture Paint")
    _remap(addon, mat, mask_uid).inputs['To Max'].default_value = 0.5
    _tick(addon, 3)
    check(not _stale(addon, mat, f), "a filter must refresh while the object is in Texture Paint")
    check(ob.mode == 'TEXTURE_PAINT', f"the object must be back in Texture Paint: {ob.mode}")
    check(addon.filters._last_mode.get(ob.name) == 'TEXTURE_PAINT', "mode bookkeeping")
    bpy.ops.object.mode_set(mode='OBJECT')
