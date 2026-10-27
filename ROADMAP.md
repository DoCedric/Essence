# Essence roadmap

Work queue derived from the Master Roadmap in `ESSENCE_DESIGN.md`. Sprints run in order, one branch each (`sprint-N-name`). The asset library is deliberately the last sprint. Tick tasks as they finish. Rules are in `CLAUDE.md`. Sprints 0 to 5 are done and detailed below; later ones are outlines to be detailed when reached.

Legend: **[H]** verified headless, **[G]** needs the user's GUI check (milestone checklist).

## Status (2026-10-27)
Sprints 0 to 6 are done and merged into `main`. Sprint 7 is on `sprint-7-paint-fill-folders`: the paint fill modes first (pulled forward from Sprint 8 by the user), then folders and materials. Height from mesh is deferred to Sprint 9 as a nice-to-have (decided with the user: nothing reads it until export to Unity is tested). The asset library moved to the last sprint (11). `tests/run_all.py`: 346/346 pass headless; `tests/gui_fill_modes.py` (windowed, not in run_all) passes.

The user confirmed every implemented feature on 2026-10-27 (the earlier pending GUI checks: ID Select, map preview, rename, baking groups, texture sets, Sprint 1 and Sprint 0 leftovers).

**Pending GUI checks** (one):
- **Row menu and copy/paste**: right-click a row's name: under Blender's own entries are Rename, Duplicate, Copy, Paste, Delete for that row. Shift+click a row: the same menu. Duplicate a layer with a painted mask, paint on the copy's mask: the original's mask stays as it was. Copy a layer, select another object, Paste: it appears there and looks the same. The Duplicate, Copy and Paste buttons on the stack bar.

**Open decisions** (details in Open Questions of the design doc): the Mesh Map source kind and presets next to the dedicated generators; anchor points (postponed); the remedy for the shader recompile cost of deep stacks; pack or reference the bundled masks when they ship.

**Known limits to remember:** UVs in a second tile are not baked (mirrored islands relying on Repeat work); curvature within a few pixels of a UV border is refilled from further inside; baked filters and the curvature do not see hand-painted changes (press Apply or Derive again); Dilate / Erode on a color filters its brightness; thresholds of Edge Wear and Cavity are in curvature units and depend on the Strength used to derive it; a scene on EEVEE is still switched to Cycles and back for a bake (bake in a throwaway scene if a freeze is ever seen there).

---

## Sprint 0: Validate v0.1 (`sprint-0-validate`)
Goal: everything currently marked "built, untested" is either verified or fixed. No new features.

- [x] Test harness: `tests/harness.py` loads the repo as a package, registers it, helpers for scratch scenes (mesh with UVs, material, assert helpers, non-zero exit on failure).
- [x] Registration test: addon registers and unregisters cleanly in 5.2. [H]
- [x] Node group build: layer, folder and mask groups build; interface sockets, min/max, subtypes, Mix socket identifiers, Bool to float, Brightness/Contrast identifiers (design doc "Verify"). [H]
- [x] Stack operators: add layer, folder, mask (white, black, image), move (across folder borders), remove, rebuild; list data stays consistent with node links. [H]
- [x] Blend math: evaluate a 3-layer stack with masks by baking to pixels and compare against the formulas (mix, add, folder lerp, mask stack multiply, enable leak). [H]
- [x] Group version upgrade: open data made with older group versions, upgrade keeps socket values. [H]
- [x] Bake: AO, World Gradient, Normal tangent/world on a single object, then high to low with a High Poly collection; settings restored afterwards. [H]
- [x] Bump/normal check: unconnected Height leaves the baked normal unchanged; Normal Map before Bump wiring. [H]
- [x] Export Unity HDRP: files exist, channel values match, MaskMap packing, straight alpha, temp data cleaned up. [H]
- [x] Fix every (nothing needed fixing) failure found; bump versions where needed.
- [x] Update design doc "Verify" section and status markers with the results.
- [x] **Milestone checklist for the user** [G] (confirmed working by the user): panels render in both sidebars, layer list rows and thumbnails, mask picker grid, Paint Mask enters/exits Texture Paint and painting changes the mask, viewport looks right, bake panel flow.

Acceptance: `tests/run_all.py` passes headless; user checklist answered.

## Sprint 1: Layer sources, mapping, opacity (`sprint-1-sources`)
Goal: a layer channel can be driven by an image or procedural, mapped and opacity-controlled, all from the panel.

- [x] Design check: option 1 (Source row per channel in Layer Properties) chosen by the user; layer group interface needed only Opacity and Visible inputs, sources are nodes beside the layer.
- [x] Layer Opacity input (overall) and per-channel opacity; factor = mask * opacity * enable (also Visible). Group version bump with migration. [H]
- [x] Per-channel source: Value / Image / Procedural (Noise, Voronoi, Wave, Checker, Gradient); picker UI; colorspace check per channel. [H]
- [x] Mapping: UV, Triplanar and Object projection, tiling/offset/rotation exposed. [H]
- [x] Rebuild and group upgrades preserve hand-wired links and sources. [H]
- [x] Per-row eye toggle for layers and folders; opacity shown on the active layer. [H] (blend mode display waits for Sprint 2)
- [x] Design doc and roadmap updated.
- [x] **Milestone checklist** [G] (confirmed by the user, 2026-10-27): source picker usability, triplanar look, row toggles.
- [x] Disabled channels collapse automatically in Layer Properties.
- [x] Follow-up (branch `sprint-1b-mask-sources`): masks get the same Source options as channels (Image, Procedural, Value, projection, mapping, remap). [H]

## Sprint 2: Blend modes and channels (`sprint-2-blend-channels`)
Goal: per-channel blend modes and the channel set grows beyond four.

- [x] Decide blend wiring (Open Question 5): layer group variants chosen by the user. [ask]
- [x] Blend library for the basic modes (Multiply, Divide, Add, Subtract, Difference, Exclusion) and a dropdown per channel, built as group variants. [H]
- [x] Decide channel-list structure (Open Question 7): same variant key; checkbox list in the Material panel. [ask]
- [x] Channels: Alpha, Emission; per-material channel list. [H]
- [x] Normal channel with Whiteout/RNM blend (baked normal is the base). [H]
- [x] Export and panels follow the channel list (BaseMap alpha, Emission file). [H]
- [x] **Milestone checklist** [G] (confirmed by the user).

## Sprint 2b: Mesh map slots (`sprint-2-blend-channels`)
Added after the Sprint 2 GUI check.

- [x] Normal is a mesh map slot (Mesh Maps panel, Normal and AO slots) instead of a layer channel. [H]
- [x] UI polish from the check: Emission Strength, collapsible Transform, 3-column channel list.
- [x] Panels skip channels that no longer exist instead of crashing.
- Freeze to image: **removed again** after the first GUI test went wrong. The code is in git history (commit 8702a97, reverted in 4da21fa). The shader compile time when adding layers is still open; see the performance options (pause updates, trim per-layer cost) and revisit with a reproduction of what went wrong.
- [x] **Milestone checklist** [G] (confirmed by the user): Mesh Maps panel (bake a normal, pick a normal image).

## Sprint 3: Bake quality and performance baseline (`sprint-3-bake-perf`)
- [x] Cage object support (selected-to-active). [H]
- [x] Float image buffers for masks and baked maps (height images are user-picked). [H]
- [x] Performance measurement: compile time and render time for 5, 20, 50 layer stacks, EEVEE and Cycles (`tests/perf_stack.py`); guidance is in the design doc. [H]
- [x] Constant-folding check for disabled channels: folds in EEVEE. [H]
- [x] Bake anti-aliasing (Off, 2x, 4x supersampling, TexTools style). [H]
- [x] AO mesh map overlay with a Strength slider (darkens Base Color, lowers Specular), after the stack. [H]
- [x] Follow-up: stack build cost was cubic in Python (50 procedural layers with masks took 242 s). Rebuild now skips unchanged links and indexes links and source nodes once: 29 s, and 0.9 s for 50 plain layers. [H]
- [x] **Milestone checklist** [G] (confirmed by the user): Cage, Float and Anti-aliasing controls in the Bake panel, AO Strength slider (bake AO, then drag Strength and compare the viewport), a cage bake on a real high-poly.

## Sprint 4: Adjustments (`sprint-4-adjustments`)
Goal: live adjustments that transform the content of a layer channel or of a mask, as stack items under their owner (the mockup: a layer and its mask each have a small stack of items). Baked and compositor filters (Kuwahara, blur, ...) are **not** in this sprint (feasibility is verified, see Open Question 8). Decided with the user: adjustments act on the owner's own input (the fill of a channel, or the mask value) before it is blended, not on what is below; one channel per adjustment item; one mask per layer is the eventual structure (not this sprint, but nothing here may assume several masks).

- [x] Feasibility of compositor filters from an addon (Kuwahara through a throwaway scene). [H]
- [x] Data: an `ADJUSTMENT` entry kind owned by a layer or a mask (`parent`), with type, channel, enabled. `stack.Tree` orders them directly below their owner (layer items above its masks in the list, a mask's items under that mask). Removing an owner removes its items; items reorder among their siblings. [H]
- [x] Nodes: an item is a small tagged node chain (main node plus an Opacity mix) inserted between the source and the blend (layer channel) or between the source and the mask group (mask). A Value source gets a constant node to adjust. Rebuild keeps hand edits. [H]
- [x] Live adjustment set: Levels, Hue/Saturation/Value, Brightness/Contrast, RGB Curves, Invert, Gamma, Gradient Map, Threshold (no Color Balance: Blender has no shader node for it). [H] (values checked against baked pixels)
- [x] Operators: Add Adjustment (menu on the selected layer or mask), remove, move within the owner, eye toggle. [H]
- [x] Panel rows: items indented under their owner in the Layers list; an adjustment properties panel (type, channel, opacity, the node's own controls, the curve widget). [G]
- [x] Mask fills: a Fill item on a mask (own source, blend Normal (default)/Multiply/Add/Subtract/Darken/Lighten, opacity), items apply in order on the running mask value. Several masks per layer still work until the layout task retires them. [H]
- [x] The mask's built-in Invert/Brightness/Contrast are gone: they are ordinary adjustment items, so their order against fills and later filters is the user's choice. Mask group version 2. [H]
- Known cosmetic issue (not fixing: the list is replaced by the mockup layout): items nested deepest in the list (for example under a mask inside a folder) sometimes get an empty line below them, probably from the row width at that indent.
- [x] Collapsible tree view: a triangle on entries that have something under them (folders hide their layers, layers hide their own and their mask's items). [H]
- [x] Tree view: layer and mask side by side, each with its item column, built from native widgets (the list stays as the other view). [H] [G] confirmed by the user
- [x] Retire several masks per layer: one mask per layer or folder, further values stacked as fills. Add Mask is unavailable once an owner has a mask. [H]
- [x] Baked filters, opt-in: Kuwahara, Blur, Sharpen, Dilate/Erode as items on a mask with a Refresh button; one hidden packed image per item, deleted with it. [H]
- [x] Filters show when they are out of date: the Apply button is red until applied and has a green dot when current, the row shows the same dot. No automatic refresh (removed: a timer-driven bake deadlocked Blender in Texture Paint, and a clock adds complexity). [H] [G] confirmed by the user
- [x] Dependencies between stacked filters: Apply applies stale lower filters first, Apply All Filters applies everything in order (the cascade is picked up in the same pass). A filter's compositor group now has a fake user so its settings survive a save. [H]
- [x] Apply froze Blender in the GUI several times (a timer-driven refresh, and manual Applies in Texture Paint / over procedurals / above applied filters) although the same steps take under a second headless. Fixed in practice (confirmed by the user, 2026-10-23) by the changes made on the way: no timer, CPU-only bake and compositor, the Solid-viewport switch removed again. Which one was decisive is not known. Every Apply step is still logged to `%TEMP%\essence_filter.log` (see `filters.log`), so a recurrence can be diagnosed from the file. One more freeze on 2026-10-24 (Kuwahara on a mask, Texture Paint, 86-node material) stopped in the first step of the bake (entering the bake state); it did not happen headless on a copy of the same file (0.8 s) and did not happen on the retry (a second or two, in Texture Paint and in Object Mode). Suspected but unconfirmed: a one-off EEVEE shader recompile when the mode and the scene engine are switched with the viewport in Material Preview. The bake state now logs every sub-step, so the next occurrence shows which one. **Diagnosed 2026-10-25** from the next freeze (Kuwahara on an Edge Wear mask, 187 nodes in the material, a Material Preview viewport): the log stopped at the assignment of `scene.render.engine` while the scene was already on Cycles, and the Blender process was idle (its CPU time moved 1 s in 40 s) and unresponsive for minutes: a deadlock, not a long compile. Assigning an RNA property runs its update even for the same value, and for the render engine that resets the engine and GPU data of every viewport. Fix: `bake.set_if_changed`, so every bake (the Bake panel, Export, the baked filters, the curvature coverage) only assigns a setting that differs and only puts back one it changed; with a scene already on Cycles the engine is no longer touched. A scene on EEVEE still has to be switched to Cycles for a bake and back, which resets the viewports twice; baking in a throwaway scene would avoid that and is the next step if a freeze is ever seen with an EEVEE scene. The previous freeze (log stopped at the start of the bake) was most likely this too.
- [x] Decided: filters never refresh by themselves (no timer, no handler). The user wants agency and awareness of background operations, so staleness is shown and Apply stays an explicit action.
- [x] Baked filters on layer channels (`sprint-5-generators` branch, done before Sprint 5): a filter item can be owned by a layer and acts on one channel (Channel dropdown like an adjustment), each channel its own chain for dependencies, Apply All and staleness; Dilate/Erode filters a color's brightness (panel warns); a switched-off channel is refused. [H] `tests/test_channel_filters.py`. [G] confirmed by the user: Blur on a layer's Roughness or Base Color image source, Apply, the viewport and the red/green dots, adding from a selected item, the UV-baking note.
- [x] **Milestone checklist** [G] (confirmed by the user): tree view and rows, adjustments and fills on a mask, the mask button, Add Mask limits, a baked filter (add Kuwahara or Blur to a mask, press Refresh, check the result, the dot-hidden image in the image pickers, how long it takes), and the Apply button states (red when nothing is baked or something below changed, green dot when current; the row dot follows), and the new Normal fill blend (a new fill overrides what is below).

## Sprint 5: Live generators, then more baked maps (`sprint-5-generators`)
Goal: generators that react to the mesh, live where the shader can do it for free and from baked maps only where it cannot. Decided with the user: a shader reads normals and positions at no real cost, so direction, slope and gradient generators are **live** and need no bake; baking is for what needs the neighbourhood of a point (curvature, thickness) or information a shader cannot read (IDs, high-poly data). The Normal (World) bake stays a utility but gets no mesh map slot. Texture paint brushes are **not** in this sprint (open: a brush asset pack, or painting helpers such as the bucket modes).

What a shader reads live: Geometry Normal and Position (world space), Texture Coordinate Normal (object space, the better default because the mask stays put when the object rotates), Texture Coordinate Generated (position across the bounding box, 0 to 1), and the `Essence Normal Map` node's output (a world-space normal that includes the baked high-poly detail; never the Bump node, which includes the layer heights and would make a cycle).

**Part 1, live generators (no baking)**
- [x] A `GENERATOR` source kind next to Procedural, for channel sources, mask sources and fills: node groups "Essence Gen ..." with a uniform interface (`Fac` out, named inputs), version key with a migration that keeps values and links, built through the same `sources.py` machinery; data and builders in `generators.py`. A generator has no coordinates or mapping. [H]
- [x] **Directional Light**: Direction, Space (object or world), Contrast, Balance, and a switch to use the detail normal from the Normal slot. [H] (values against baked pixels on a cube)
- [x] **Slope / Up mask**: how much a surface faces a chosen axis, with a threshold and softness. [H]
- [x] **Height Gradient and Position Gradient** (one generator, "Gradient": Z is a height gradient, X and Y a position gradient), live from Generated coordinates: axis, range, remap. Replaces the baked World Gradient for masks (the bake stays for export-style uses). [H]
- [x] Panel: generator picker and controls in the Source rows of layers, masks and fills. [H] draws; [G] confirmed by the user
- [x] Directional Light, Slope and Gradient aimed by a Rotation in degrees (0 to 360 per axis, Direction and Axis migrate) and a temporary gizmo: an Empty arrow that drives it through drivers, parented in object space, removed with the source. [H] `tests/test_generators.py`; [G] confirmed by the user: the arrow in the 3D view, rotating it, the Rotation fields and the Show/Remove Gizmo button.

**Part 2, more baked maps (Mesh Maps slots)**
- [x] **Curvature** (convex and concave). Island borders: black lines where close islands meet (reported from the GUI, found with the user's own layout) are removed by baking the island coverage in Derive, shrinking it by the reach of the filter and refilling the rest with Inpaint; `test_a_jump_between_close_islands_leaves_no_line`. Built as decided, `tests/test_curvature.py`: signed on both axes, the same value at 64 and 128 px, a bevelled box from a real high-poly bake, staleness (new bake, other image, settings, hand edits), save and reload, old files without the slot; [G] confirmed by the user (the Derive button and its colors, Width and Strength, the island borders). Method decided after a test on the user's high and low poly (`BG01_low`, 19 high-poly parts, 1024 px; images in the scratchpad, not committed): **derived from the Normal slot with the compositor**, the signed divergence of the tangent normal (convex bright, concave dark). It needs no bake of its own, works on any normal image the user picks, and is a derived map with an explicit Derive/Apply step and a red/green state like the baked filters (it reuses that machinery: a compositor group, a result image, a signature of the normal image and the settings). Settings: Width (pixels, so it depends on the map size) and Strength. Rejected: Pointiness (nearly flat on hard surface) and Bevel (unsigned, 3 times slower, noisy). To verify when built: the sign on a box with a bevel (convex edge bright), and the UV island border artifact. [H]
- [x] **Position**: the low poly's Generated coordinates, baked from a high poly too; a Mesh Maps slot, a bake entry with a thumbnail, the same float, anti-aliasing and cage options. [H] baked values equal the live Generated coordinates on a moved, rotated and scaled object; from a high poly the z of its surface. [G] confirmed by the user.
- [x] **ID maps**: one map with an "ID by" choice of object, material, mesh island or UV island; the first three also from a high poly, the UV island from the low poly (computed by Python, baked through a temporary face attribute that is always removed). [H] `tests/test_mesh_id.py`, and on the user's `BG01` parts (distinct colors per high-poly part, about 4 s at 1024 px). A Mesh Maps slot and a bake thumbnail. [G] confirmed by the user.
- [x] **Thickness** (inside AO): a slot, a bake entry with a thumbnail and a Distance (0 = automatic, a tenth of the biggest object); from a high poly too. [H] `tests/test_mesh_thickness.py`: a thick box against a thin slab, the distance, the automatic distance at two sizes, a thin and a thick high poly. [G] confirmed by the user.
- [x] (Position, Curvature and ID: done) A Mesh Maps slot, bake entry and thumbnail for each; they follow the existing `Essence_Bake_<MAP>` slot pattern and the float/anti-aliasing/cage options. [H]
- [x] **Generators that read them**: **Edge Wear** (curvature), **Cavity** (curvature, AO) and **Dirt** (AO, curvature), built as dedicated generators to test against the preset route; `tests/test_generators_maps.py`, and on the user's `BG01` parts. [H] [G] confirmed by the user (they work well on real parts).
- [x] **Mask by ID**: the **ID Select** generator: a palette of up to 16 colors (Add Color, a remove button on each swatch, a count), Tolerance (default 0.01), Softness, Invert, reading the ID slot; the colors are chosen with Blender's eyedropper on the mesh (Show ID on Mesh, then E over a swatch) or by hand; Show ID Map. The first version's Pick from Selected Face is removed. A version 1 node migrates (its color becomes color 1). [H] `tests/test_generators_id.py`. [G] confirmed by the user (2026-10-27): the palette on a real ID map, removing a color, picking colors from the mesh with the eyedropper (the workflow fully works).
- Open idea, moved to Open Questions (11) in the design doc: a **Mesh Map source kind** and presets of ordinary items next to the dedicated generators. The user compared them; the generators work well.
- [x] **Baked inputs for generators**: `Use Baked Position` on Gradient, wired from the Position slot like the detail normal (see "Baked inputs for live generators" in the design doc). [H] `tests/test_mesh_position.py`
- [x] Decided: the ID picker is Blender's eyedropper on a map preview (see ID Select above). Baking groups and multi-object bake move to Sprint 6.

- [x] **Milestone checklist** [G] (confirmed by the user, ID Select on 2026-10-27): the generator picker and the gizmo, Position, Curvature (including the island borders), ID maps, Thickness, Edge Wear, Cavity and Dirt on real parts, paint items, the filters on layer channels, the bundled masks at Blender's start and the GPU bake. Anchor points are postponed by the user.

## Renaming (`sprint-5-generators` branch, interim)
- [x] **Ctrl+click on a row of the tree view renames it** (a popup with the name; a plain click only selects; works on folders, layers, masks, fills, adjustments and filters; the node labels follow). [H] `tests/test_rename.py`. [G] confirmed by the user, except that the field did not take focus by itself: fixed (the field is drawn with `activate_init`), [G] confirmed by the user (2026-10-27): the cursor is in the field when the popup opens.

## Preview buttons (`sprint-5-generators` branch, interim)
- [x] **Preview (solo)**: a toggle next to the eye on layer and mask rows (a mask as grey, a layer as its own Color, flat), channel buttons for the whole stack's result in the Material panel, a notice with Stop and a hint for Solid shading; an Emission node wired into the Material Output, the old link remembered and put back exactly; rebuilds keep it, removing the entry ends it, a bake during it keeps it, it survives a save. [H] `tests/test_preview.py`. [G] confirmed by the user.
- [x] Dropped by the user: the alt-click on a thumbnail (the toggle does the job).
- [x] **Map previews and a color picker from the mesh**: a `MAP` preview shows a Mesh Maps slot on the mesh (Show on Mesh in each slot, Show ID on Mesh in the ID Select panel) so the swatch's eyedropper can pick an ID from the model; color management is switched to Standard (no look, exposure 0, gamma 1) while a map preview runs and put back exactly afterwards, also after a save and reload. [H] `tests/test_preview_map.py`. [G] confirmed by the user (2026-10-27): Show ID on Mesh, the eyedropper on a part, ending the preview (the view transform comes back).

## Bake device (`sprint-5-generators` branch, interim)
- [x] **Bakes use the GPU when available**: Device in the Bake panel, Auto by default (GPU if Cycles has one enabled in Preferences, else CPU) or CPU; the panel shows what it will use; the scene's device is restored; a failed GPU bake is retried on the CPU. The filter and curvature bakes stay on the CPU. [H] `tests/test_bake_quality.py` (including a real GPU bake on this machine's RTX 3060: 2.2 s against 8.1 s on the CPU). [G] confirmed by the user: a high-poly bake on the GPU and its time.

## Paint items (`sprint-5-generators` branch, interim)
- [x] **Paint item on a mask**: Add Paint in the mask's Add Adjustment menu makes a fill with a transparent float image whose alpha is its factor (white reveals, black hides, unpainted does nothing; Add and Subtract paint relative to what is below), with Paint and Exit buttons, a brush row icon, Opacity and the eye. It sits at one place in the stack: adjustments below change what you paint over, adjustments above treat the paint like the rest. [H] `tests/test_paint_items.py`: values from straight alpha, blends and opacity, stack position, eye, removal, switching the source, the Paint button, save and reload, panels. [G] confirmed by the user: painting with the brush, soft edges, erasing, the row and panel look.
- [x] **No fringe on soft brush edges** (reported from the GUI: a white line round a black stroke): premultiplied alpha. The paint image is transparent black and kept raw, and the paint item unpremultiplies in its nodes; tested against the brush's mix maths for black, white and grey strokes and for overlapping strokes. [H] [G] confirmed by the user.
- [x] **Painted pixels survive a save**: found while building this; `Image.pack()` is a no-op on a generated blank and a save does not repack a painted buffer. New mask and paint images are really packed, and a save handler (`persist.py`) packs the painted ones again, including the generated blanks of older masks. [H] [G] confirmed by the user.

## Bundled masks (`sprint-5-generators` branch, interim)
- [x] 31 ambientCG masks in `assets/masks` load into the file once (checked by an `essence_bundled` key), Non-Color, fake user, at addon enable, after file loads and at Blender start. [H] `tests/test_bundled.py`, and a real start of Blender with the addon checked by hand.
- [x] [G] confirmed by the user: Blender started with the addon, the masks are in the mask picker grid and the image pickers.
- [x] Decided: no preference to turn the loading off, and the masks in `assets/masks` stay untracked in git while they are only for testing. Open: whether to pack instead of reference when they ship for real (see the note at the top of `bundled.py`).

## Sprint 6: Texture sets, baking groups, mesh-map leftovers (`sprint-6-texture-sets`)
- [x] Decided with the user: **texture set = material** (option 1 of three: texture sets, a scene list of baking groups, or both). Size, High Poly and Extrusion move to the material, the Cage to the low-poly object.
- [x] **Texture set bake**: every object using a selected object's material bakes into `<material>_<Map>`; hidden objects of the set are baked and hidden again; other materials of those objects bake into a throwaway image; an object-named image from before is renamed so its users follow. [H] `tests/test_texture_sets.py`
- [x] **Margin of a multi-object set**: found that Cycles' margin overwrites the other objects' islands (one call or several). Several objects bake with margin 0 and the compositor adds the margin (coverage + Inpaint); one object keeps Cycles' margin. [H]
- [x] Baked filters, the curvature's coverage and Export work on the whole texture set; the curvature result is `<material>_Curvature`. [H]
- [x] UV island IDs are numbered across the objects of a set, so they get different colors. [H]
- [x] Bake panel: a Texture set box for the active material (Size, High Poly, the active object's Cage, Extrusion, a note when several objects share it). [H] draws
- [x] **Baking groups**: High Poly, Floaters and Cage per low-poly object (the material's High Poly as the default), Pair by Name, the panel lists each object's group. Floaters in every map but AO and Thickness. [H] `tests/test_baking_groups.py`
- [x] **One merged high poly per group** (modifiers at render settings, curves included, ID hues kept as an attribute): identical to Blender's separate bake, 10 to 25 times faster. On the user's file a set's AO went from 198 s to 6.8 s. [H]
- [x] **Only the bake's geometry renders** (other low polys and props switched off for rendering and put back). [H]
- [x] **AO across groups** [ask]: every group's high poly occludes. On the user's file that turns 34.5% of the islands black against 4.5% with each group alone; the user chose to keep every part occluding.
- [x] **ID Select fix** (reported from the GUI: the picked part was bright grey, not white): the fade starts at the tolerance instead of being centred on it, so an exact match is 1 with the default settings. Group version 3, values kept. [H] `tests/test_generators_id.py`
- [x] **Bake panel: a baking group box per object** (reported from the GUI): every object of the texture set has its own box with High Poly, Floaters and Cage dropdowns, Pair by Name still fills them; the "N objects bake into one image / UVs must not overlap" notes and the per-object text lines are gone. [H] `tests/test_baking_groups.py`, `tests/test_texture_sets.py` (panel draws)
- [x] **ID map margin and borders** (reported from the GUI: black gaps and teeth at island borders, and a thin line between two selected colors): found three causes. The ID slot's image node sampled with Linear interpolation, which blends two neighbouring IDs into a third color (now Closest); the supersampling average did the same (an ID map ignores Anti-aliasing now); and rays that missed the high poly left black texels inside the islands that no margin touches, while the compositor's Inpaint blends two colors across a gap. An ID map now gets its margin from `filters.dilate_colors`: black texels inside the islands are refilled and the colors continue outwards by copying the nearest one. [H] `tests/test_mesh_id.py` (a 0.2 gap between two parts holds only the two colors; the slot interpolation). [G] confirmed by the user (2026-10-27).
- [x] **ID colors and exact select** (reported from the GUI: a wall of near-identical pinks, and a part one 8-bit step away selected at Tolerance 0): ID Select version 4 is exact at Tolerance 0 and Softness 0 (the softness floor was 0.001, now 1e-5 plus a float-noise margin). ID colors are numbered (objects and materials alphabetically, islands as met) and taken from a 256-color farthest-point sequence instead of a name hash, in every ID mode, as a face color attribute. Rebaking unchanged parts keeps the colors. [H] `tests/test_mesh_id.py`, `tests/test_generators_id.py`. [G] confirmed by the user (2026-10-27).
- [x] **Sample button for ID Select** (the user asked for it to speed up picking): click parts of the model, each adds its exact ID color from the image (ray, UV, texel) to the palette; right-click or Esc ends. [H] `tests/test_generators_id.py`: the UV and texel under a ray on a scaled, rotated object, palette rules, a refusal without an ID map, the panel. [G] confirmed by the user (2026-10-27).
- [x] **Milestone checklist** [G] (confirmed by the user, 2026-10-27): baking groups (Pair by Name links the collections correctly), a texture set of several objects into one image (works, with the ID map issue above, now fixed), the Texture set box, ID Select.
- [x] Decided with the user: **Height from mesh is deferred** to Sprint 9 as a nice-to-have, to build when the export to Unity is tested there (nothing reads the map before that). The plan moved with it.

## Sprint 7: Paint fill modes, then folders and materials (`sprint-7-paint-fill-folders`)
- [x] Decided with the user: the paint fill modes come first (moved here from Sprint 8); this answers Open Question 15 for now (the fill modes; a brush pack stays open).
- [x] Spike (windowed Blender): the native Fill brush in the 3D view fills every selected face under face selection masking, faces hidden from the view included, and leaves the others; a script can activate the Fill brush asset and run a click with `paint.image_paint`.
- [x] **Fill modes** (`fill.py`): a Fill box under Exit Paint Mode on masks and paint items: a fill color and a button per mode (Mesh, UV Island, Face, Full) that starts filling (redesigned after the first GUI check, where the user painted with Blender's own Fill brush because the separate Fill button was missed; a warning shows when that brush is active); filling turns the cursor into a brush, each click picks the face under it, selects the mode's faces of that material (hidden faces skipped), runs one click of Blender's Fill brush through face masking and puts back the face selection, masking, brush and unified color. [H] `tests/test_fill.py` (the faces of each mode, the ray through a Subdivision modifier and behind another object, the state put back also when the click fails, poll, panels); `tests/gui_fill_modes.py` with the real brush in a window (every mode on a two-part mesh, the hidden bottom face filled, grey 0.5 stored exactly; with `--enable-event-simulate` also the buttons and a real click through the modal operator). [G] confirmed by the user (2026-10-27), second version.
- [x] Decided with the user: new folders are **Passthrough**, existing ones stay Normal.
- [x] **Nested folders, Passthrough, folder opacity**: folders nest (moving in and out like layers, Add Folder inside the active folder), a Mode (Passthrough or Normal) and an Opacity in Layer Properties, folder group version 4 (Opacity input, Factor output). Passthrough scales each child's mask by the folder's Factor. Fixed on the way: new group sockets started at 0 on existing nodes (upgraded folders would have been invisible). [H] `tests/test_folders.py`; the user's file bakes identical before and after. [G] confirmed by the user (2026-10-27): Passthrough, folder opacity and nesting work.
- [x] **The tree view is the only stack view** (the user's choice): the flat list, its toggle and the folder mode's info line (it read as confusing) are gone. [H]
- [x] **Per-row buttons moved off the rows** (the user's request, to keep rows uncluttered): the preview toggle is on the stack bar and works on the selected layer or mask; the per-row Add Mask button is gone (the bar's mask menu does it). [H] `tests/test_preview.py`.
- [ ] Smart materials (folder presets as assets, drop onto an object).
- [x] **Duplicate / copy / paste of layers and folders** (`clipboard.py`): with masks, items, contents, copied paint images and filter groups, across materials; buttons on the stack bar and a row menu (right-click or Shift+click: Rename, Duplicate, Copy, Paste, Delete). [H] `tests/test_clipboard.py`. [G] pending: see Pending GUI checks.
- [ ] Add menu grouping.

## Later sprints (outline, to be detailed)
- **Sprint 8, painting and viewport:** paint layers (per-channel painting, the paint item machinery on a channel), the fill modes on paint layers (built for masks and paint items in Sprint 7), texture set settings. Already built: paint items on masks and the preview buttons (layer, mask, whole-stack channels, mesh maps).
- **Sprint 9, export:** preset engine, URP/Unreal/glTF, all channels, bit depth, dilation, export all, flattened Blender material; tested in Unity.
  - [ ] Nice-to-have, **Height from mesh** (designed, see "Height from mesh" in the design doc; three decisions are open, Open Question 12). A new Mesh Maps slot `HEIGHT`, a bake entry with a thumbnail, a Range setting. Plan:
    - [ ] Decide with the user: method (position delta, recommended, or Ray Length), Range (automatic by default, recommended), and that the slot does not feed the Bump node (recommended). [ask]
    - [ ] Optional spike: is Light Path Ray Length readable in a bake, and does it equal the distance to the high poly with the extrusion subtracted? (It does not work with a cage, so it can only ever be a fast path.)
    - [ ] Bake pieces: the high Position (exists), a hit mask (white emission), the low poly's own Position and object-space Normal, all float, on the same size as the set and through the baking groups (floaters and cage included). [H]
    - [ ] Combine in numpy: `dot(P_high - P_low, N_low)` with the Generated coordinates scaled back by the dimensions, divided by the Range, 0.5 + 0.5 * h / range, 0.5 where nothing was hit; written to `<material>_Height` (float, Non-Color, packed, stamped, `essence_height_range` on the image). [H]
    - [ ] Bake panel: a Range field for this map (0 = automatic), the map in the picker with a thumbnail; the slot in Mesh Maps with Show on Mesh. [H] draws
    - [ ] Tests (headless): a flat plane at +0.1 and at -0.1, a tilted plane (distance along the normal), a scaled and rotated low poly gives the same distances, a sphere pair, floaters, a cage, automatic range, a miss is 0.5, a texture set of two objects, save and reload, an old file without the slot. [H]
    - [ ] [G] the look and the Show on Mesh preview on a real high and low poly.
- **Sprint 10, remaining blend modes, specular/AO channels, then P2 and below per the design doc.**
- **Sprint 11, Essence asset library (last):** catalogs, noise/gradient/grunge assets (the bundled masks move there; decide pack or reference then), generators and smart masks as assets, the Mesh Map source kind and presets if still wanted (Open Question 11).
