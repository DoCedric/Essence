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
- [ ] **Milestone checklist for the user** [G]: panels render in both sidebars, layer list rows and thumbnails, mask picker grid, Paint Mask enters/exits Texture Paint and painting changes the mask, viewport looks right, bake panel flow.

Acceptance: `tests/run_all.py` passes headless; user checklist answered.

## Sprint 1: Layer sources, mapping, opacity (`sprint-1-sources`)
Goal: a layer channel can be driven by an image or procedural, mapped and opacity-controlled, all from the panel.

- [ ] Design check: layer group interface for sources and opacity (stop and ask if it needs an Open Question decision, 6 or 7).
- [ ] Layer Opacity input (overall) and per-channel opacity; factor = mask * opacity * enable. Group version bump with migration. [H]
- [ ] Per-channel source: Value / Image / Procedural node group; picker UI; colorspace set per channel (sRGB for color, Non-Color for data). [H]
- [ ] Mapping: UV and triplanar projection, tiling/offset/rotation as layer inputs. [H]
- [ ] Rebuild preserves hand-wired source nodes. [H]
- [ ] Per-row eye toggle for layers and folders; opacity shown on the active layer. [H]/[G]
- [ ] Design doc and roadmap updated.
- [ ] **Milestone checklist** [G]: source picker usability, triplanar look, row toggles.

## Sprint 2: Blend modes and channels (`sprint-2-blend-channels`)
Goal: per-channel blend modes and the channel set grows beyond four.

- [ ] Decide blend wiring (Open Question 5): measure node count and compile time of both options headless, then ask the user. [ask]
- [ ] Blend node groups for the basic modes (Multiply, Divide, Add, Subtract, Difference, Exclusion) and dropdown per channel. [H]
- [ ] Decide channel-list structure (Open Question 7). [ask]
- [ ] Channels: Opacity (Alpha), Emission; per-material channel list. [H]
- [ ] Normal channel with Whiteout/RNM blend as math node groups. [H]
- [ ] Export and panels follow the channel list. [H]
- [ ] **Milestone checklist** [G].

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
