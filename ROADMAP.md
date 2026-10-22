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
- [ ] **Milestone checklist** [G].

## Sprint 2b: Mesh map slots (`sprint-2-blend-channels`)
Added after the Sprint 2 GUI check.

- [x] Normal is a mesh map slot (Mesh Maps panel, Normal and AO slots) instead of a layer channel. [H]
- [x] UI polish from the check: Emission Strength, collapsible Transform, 3-column channel list.
- [x] Panels skip channels that no longer exist instead of crashing.
- [ ] Freeze to image: **removed again** after the first GUI test went wrong. The code is in git history (commit 8702a97, reverted in 4da21fa). The shader compile time when adding layers is still open; see the performance options (pause updates, trim per-layer cost) and revisit with a reproduction of what went wrong.
- [ ] **Milestone checklist** [G]: Mesh Maps panel (bake a normal, pick a normal image).

## Sprint 3: Bake quality and performance baseline (`sprint-3-bake-perf`)
- [ ] Cage object support (selected-to-active). [H]
- [ ] Float image buffers for height and masks. [H]
- [ ] Performance measurement: compile time and render time for 5, 20, 50 layer stacks, EEVEE and Cycles (headless where possible); write guidance into the design doc. [H]
- [ ] Constant-folding check for disabled channels. [H]

## Later sprints (outline, to be detailed)
- **Sprint 4, adjustments and filters:** adjustment layers, filter stack on layer/mask, live adjustment set (levels, HSV, balance, curves, invert, gradient map, threshold), mask levels/curves, mask stack blend modes.
- **Sprint 5, generators and assets:** Essence asset library (catalogs), noise/gradient/grunge assets, smart masks consuming baked maps.
- **Sprint 6, mesh maps:** curvature, position, thickness, ID maps, height from mesh, baking groups, multi-object bake.
- **Sprint 7, folders and materials:** nested folders, Passthrough, folder opacity, smart materials, duplicate/copy/paste, Add menu grouping.
- **Sprint 8, painting and viewport:** paint layers, channel/mask/layer solo, texture set settings.
- **Sprint 9, export:** preset engine, URP/Unreal/glTF, all channels, bit depth, dilation, export all, flattened Blender material.
- **Sprint 10, remaining blend modes, specular/AO channels, freeze to image (late), then P2 and below per the design doc.**
