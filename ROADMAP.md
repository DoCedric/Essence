# Essence roadmap

Work queue derived from the Master Roadmap in `ESSENCE_DESIGN.md`. Sprints run in order, one branch each (`sprint-N-name`). Tick tasks as they finish. Rules are in `CLAUDE.md`. Sprints 0 to 2 are detailed; later ones are outlines to be detailed when reached.

Legend: **[H]** verified headless, **[G]** needs the user's GUI check (milestone checklist).

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
- [ ] **Milestone checklist** [G]: source picker usability, triplanar look, row toggles.
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
- [ ] Freeze to image: **removed again** after the first GUI test went wrong. The code is in git history (commit 8702a97, reverted in 4da21fa). The shader compile time when adding layers is still open; see the performance options (pause updates, trim per-layer cost) and revisit with a reproduction of what went wrong.
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
- [x] Apply froze Blender in the GUI several times (a timer-driven refresh, and manual Applies in Texture Paint / over procedurals / above applied filters) although the same steps take under a second headless. Fixed in practice (confirmed by the user, 2026-10-23) by the changes made on the way: no timer, CPU-only bake and compositor, the Solid-viewport switch removed again. Which one was decisive is not known. Every Apply step is still logged to `%TEMP%\essence_filter.log` (see `filters.log`), so a recurrence can be diagnosed from the file. One more freeze on 2026-10-24 (Kuwahara on a mask, Texture Paint, 86-node material) stopped in the first step of the bake (entering the bake state); it did not happen headless on a copy of the same file (0.8 s) and did not happen on the retry (a second or two, in Texture Paint and in Object Mode). Suspected but unconfirmed: a one-off EEVEE shader recompile when the mode and the scene engine are switched with the viewport in Material Preview. The bake state now logs every sub-step, so the next occurrence shows which one.
- [x] Decided: filters never refresh by themselves (no timer, no handler). The user wants agency and awareness of background operations, so staleness is shown and Apply stays an explicit action.
- [x] Baked filters on layer channels (`sprint-5-generators` branch, done before Sprint 5): a filter item can be owned by a layer and acts on one channel (Channel dropdown like an adjustment), each channel its own chain for dependencies, Apply All and staleness; Dilate/Erode filters a color's brightness (panel warns); a switched-off channel is refused. [H] `tests/test_channel_filters.py`. [G] pending: add Blur to a layer's Roughness or Base Color image source and Apply, check the viewport and the red/green dots; adding from a selected item takes its channel; the UV-baking note in the panel.
- [x] **Milestone checklist** [G] (confirmed by the user): tree view and rows, adjustments and fills on a mask, the mask button, Add Mask limits, a baked filter (add Kuwahara or Blur to a mask, press Refresh, check the result, the dot-hidden image in the image pickers, how long it takes), and the Apply button states (red when nothing is baked or something below changed, green dot when current; the row dot follows), and the new Normal fill blend (a new fill overrides what is below).

## Sprint 5: Live generators, then more baked maps (`sprint-5-generators`)
Goal: generators that react to the mesh, live where the shader can do it for free and from baked maps only where it cannot. Decided with the user: a shader reads normals and positions at no real cost, so direction, slope and gradient generators are **live** and need no bake; baking is for what needs the neighbourhood of a point (curvature, thickness) or information a shader cannot read (IDs, high-poly data). The Normal (World) bake stays a utility but gets no mesh map slot. Texture paint brushes are **not** in this sprint (open: a brush asset pack, or painting helpers such as the bucket modes).

What a shader reads live: Geometry Normal and Position (world space), Texture Coordinate Normal (object space, the better default because the mask stays put when the object rotates), Texture Coordinate Generated (position across the bounding box, 0 to 1), and the `Essence Normal Map` node's output (a world-space normal that includes the baked high-poly detail; never the Bump node, which includes the layer heights and would make a cycle).

**Part 1, live generators (no baking)**
- [x] A `GENERATOR` source kind next to Procedural, for channel sources, mask sources and fills: node groups "Essence Gen ..." with a uniform interface (`Fac` out, named inputs), version key with a migration that keeps values and links, built through the same `sources.py` machinery; data and builders in `generators.py`. A generator has no coordinates or mapping. [H]
- [x] **Directional Light**: Direction, Space (object or world), Contrast, Balance, and a switch to use the detail normal from the Normal slot. [H] (values against baked pixels on a cube)
- [x] **Slope / Up mask**: how much a surface faces a chosen axis, with a threshold and softness. [H]
- [x] **Height Gradient and Position Gradient** (one generator, "Gradient": Z is a height gradient, X and Y a position gradient), live from Generated coordinates: axis, range, remap. Replaces the baked World Gradient for masks (the bake stays for export-style uses). [H]
- [x] Panel: generator picker and controls in the Source rows of layers, masks and fills. [H] draws; [G] look and usability pending
- [x] Directional Light, Slope and Gradient aimed by a Rotation in degrees (0 to 360 per axis, Direction and Axis migrate) and a temporary gizmo: an Empty arrow that drives it through drivers, parented in object space, removed with the source. [H] `tests/test_generators.py`; [G] pending: the arrow in the 3D view, rotating it with R and the gizmo, the Rotation fields and the Show/Remove Gizmo button.

**Part 2, more baked maps (Mesh Maps slots)**
- [ ] **Curvature** (convex and concave). Open: method, either the baked normal run through the compositor, or Pointiness/Bevel through an emission override (Cycles-only, noisier). Decide with a quick test of both on a real mesh. [H]
- [ ] **Position** (object-space XYZ with bounding-box remap), **Thickness** (inside AO), **ID maps** (material, object, UV island). [H]
- [ ] A Mesh Maps slot, bake entry and thumbnail for each; they follow the existing `Essence_Bake_<MAP>` slot pattern and the float/anti-aliasing/cage options. [H]
- [ ] **Generators that read them**: Edge Wear (curvature), Dirt and Cavity (AO, curvature, thickness), mask by ID (a color or index picker). [H]
- [ ] Open decisions to settle with the user when reached: the curvature method, the ID picker UI, whether baking groups and multi-object bake fit in this sprint or the next.

- [ ] **Milestone checklist** [G]: generator picker usability, how a Directional Light looks on a real mesh (object versus world space, with and without detail normal), the new bake maps.

## Bundled masks (`sprint-5-generators` branch, interim)
- [x] 31 ambientCG masks in `assets/masks` load into the file once (checked by an `essence_bundled` key), Non-Color, fake user, at addon enable, after file loads and at Blender start. [H] `tests/test_bundled.py`, and a real start of Blender with the addon checked by hand.
- [ ] [G] Check in the GUI: start Blender with the addon, the masks are in the mask picker grid and in the image pickers; the file's save prompt and size are acceptable.
- [x] Decided: no preference to turn the loading off, and the masks in `assets/masks` stay untracked in git while they are only for testing. Open: whether to pack instead of reference when they ship for real (see the note at the top of `bundled.py`).

## Later sprints (outline, to be detailed)
- **Sprint 6, asset library and mesh-map leftovers:** Essence asset library (catalogs), noise/gradient/grunge assets, generators and smart masks as assets, height from mesh, baking groups, multi-object bake.
- **Sprint 7, folders and materials:** nested folders, Passthrough, folder opacity, smart materials, duplicate/copy/paste, Add menu grouping.
- **Sprint 8, painting and viewport (preview button design is decided, see "Preview (solo)" in the design doc: an Emission node wired into the Material Output, a toggle next to the eye on layer and mask rows, mask as grey and layer as its own flat color):** paint layers, paint bucket modes (Full, Mesh, UV island, Face: native Fill tool restricted by face-selection paint masking), channel/mask/layer solo (including alt-click on a mask or layer thumbnail in the tree view to preview it), texture set settings.
- **Sprint 9, export:** preset engine, URP/Unreal/glTF, all channels, bit depth, dilation, export all, flattened Blender material.
- **Sprint 10, remaining blend modes, specular/AO channels, then P2 and below per the design doc.**
