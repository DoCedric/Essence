# Essence

A Blender addon that brings a Substance Painter-class texturing workflow into Blender, built entirely on top of Blender's own shader graph, baking, texture paint, asset and compositing systems.

> Status: v0.1 in development. Everything marked *built* has been **verified headless in Blender 5.2.2** by the test suite in `tests/` (237 tests: stack operators, layer and mask sources, blend modes and channels checked against real baked pixels, bake, export, group upgrades, baked filters, live generators, the position, curvature and ID maps, paint items and saving, and the panel draw logic against a recording layout). What needs the GUI (how panels look and feel, thumbnails, Texture Paint, the viewport) is checked by the user's milestone lists in `ROADMAP.md`; each section says whether its GUI check is done. This document describes the full "dream" scope (a complete PBR workflow in Blender), marks what exists, and ranks what is left from essential to trivial.

**Target:** Blender 5.2.2 (latest). No support for older versions.

---

## How to read this document

**Status markers**

| Marker | Meaning |
|---|---|
| ✅ | Built in the addon (untested in Blender unless stated) |
| 🟡 | Partly built, gap described |
| ⬜ | Not started |
| 🚫 | Deliberately out of scope (see "Out of scope") |

**Priority tiers** (used in every feature table and in the Master Roadmap)

| Tier | Meaning |
|---|---|
| **P0 Essential** | Without it the addon is not a usable PBR texturing workflow, or the foundation breaks as it grows |
| **P1 Core** | Expected by anyone coming from Substance Painter; the "full workflow" line |
| **P2 Important** | Large productivity or quality gain, but a workable workflow exists without it |
| **P3 Nice to have** | Polish and convenience |
| **P4 Trivial** | Small extras, do when passing by |

**Cost class** (see principle 8): **L** live shader nodes (evaluated by Blender per pixel), **B** baked or cached image (Cycles bake or compositor, evaluated on demand), **O** orchestration only (Python builds or edits Blender data, never touches pixels).

---

## Design Philosophy

### 1. The shader graph is the source of truth
Essence does not use a proprietary layer system hidden behind its own data model. Layers, masks and channel setups are expressed as ordinary Blender shader nodes. The addon's UI is a convenient front end over the node tree, not a replacement for it.

### 2. Deconstructable by anyone who knows Blender
A user with Blender knowledge must be able to open the shader editor, look at what Essence generated, understand it, and modify it by hand. Nothing the addon builds should be a black box. Anything a user wires by hand (a Noise node into a layer's color input) must keep working through Rebuild.

### 3. Layers are a workflow concept built from nodes
We keep the familiar layer/mask mental model from Substance Painter, but "layers" are node groups/structures in the shader graph that influence PBR channels.

### 4. Per-channel control
Layers and masks can influence different channels of the PBR setup independently, rather than every layer affecting everything at once.

### 5. Channels are blended, not shaders
Layers blend PBR channel values (color, roughness, metallic, height, normal, ...) and feed a single shader. We avoid Add Shader / Mix Shader for layering because of the expected performance cost.

### 6. Native Blender UI only
Essence uses Blender's own panels, lists, properties, asset browser and editors. We do not build a custom UI layer on top of Blender (no custom overlays, drawn widgets or modal interface in the style of RetopoFlow). If Blender's UI system limits what we can do, we work within the limit.

### 7. Build for scalability
Nothing in the code or the generated node structure should assume today's feature set stays true. Concretely:
- Channels are defined as data (name, socket type, blend method, default value, target socket), not hard-coded in the node-building logic. Adding a channel means adding a definition, not rewriting the layer builder.
- Blend mode is a per-channel property of a layer. Letting the user pick one must not require restructuring layers.
- Generated node groups carry a version, so a changed group definition is rebuilt on Rebuild (values and links kept by socket name). While the addon is unreleased there is no migration of older data formats, no "Upgrade" buttons: only Rebuild Links.
- The layer node has a stable, documented set of inputs and outputs so that added channels extend it rather than replace it.
- Export targets, mask generators and layer presets are data (presets and assets), not code paths.

### 8. Use Blender's infrastructure; never build a program inside the program
This is the rule for deciding *how* every feature below is built. Before writing code for a feature, ask which Blender system already does it:

| Need | Use Blender's | Never |
|---|---|---|
| Per-pixel evaluation | Shader nodes (GPU, EEVEE/Cycles) | Python loops over pixels, own GPU shaders |
| Painting | Texture Paint mode, brush assets, stencils, symmetry, undo | Own brush engine or modal paint tool |
| Baking | Cycles bake (selected-to-active, cage, margin) | Own ray caster |
| Image filters (blur, sharpen, edge) | Compositor, run as an explicit bake step | Per-frame Python filters |
| Presets, smart materials, mask generators, grunge, alphas | Asset Browser / asset shelf / catalogs, node group assets | Own library browser or file format |
| Lighting and look dev | World, HDRIs, Material Preview, Cycles | Own viewport or lighting rig |
| Color management, file formats, UDIM | Image datablocks and colorspaces | Own image IO |
| Undo, save, project | `.blend`, global undo, operators with REGISTER+UNDO | Own project file or undo stack |
| Library of procedural patterns | Texture nodes, node group assets | Re-implementing Designer graphs |

Corollaries:
- **Python orchestrates, Blender computes.** Python creates nodes, sets properties, calls bake and export operators. It never touches pixels, with the single exception of small packing/readback steps in export (already the case today).
- **Every feature declares its cost class** (L / B / O). Live (L) features must be cheap enough to evaluate per pixel in a deep stack. Anything that needs neighbouring pixels (blur, sharpen, edge detect, dilation, slope blur) cannot be live in a shader graph and is a **B** feature: an explicit "bake/refresh" step that writes an image, never a hidden recompute.
- **No macro-button clones.** A UI element is only added when it exposes a property or operator of real Blender data. If a feature cannot be expressed as nodes, assets or a Blender operator, it is out of scope rather than faked.
- **Viewport mesh-dependent generators read baked maps.** The AO node, Pointiness and Bevel node are Cycles-only. Generators that react to geometry (edge wear, dirt, curvature) read baked mesh maps (Essence's bake section), exactly as Substance does, so they work live in EEVEE.

---

## Part A. What exists today (v0.1)

### Baking
Ported from the Artistant addon's bake pipeline (Cycles; selected objects; one image per object named `<object>_<Map>`, reused and overwritten on re-bake so references such as mask slots stay valid; images are packed; a dedicated unconnected image node `Essence_Bake_<MAP>` holds the target in the material). All bake settings Essence touches are saved and restored. Samples, margin and size are exposed in the Bake panel, and so is the **Device**: **Auto** (the default) bakes on the GPU when Cycles has one switched on in Preferences > System (Cycles' own `has_active_device()` test, the one that enables "GPU Compute" in the render settings) and on the CPU otherwise; **CPU** always uses the CPU. The panel says which it will use and why ("Bakes on the GPU: NVIDIA GeForce RTX 3060 Laptop GPU", or "the CPU: no GPU is enabled in Preferences > System"). The scene's own device setting is only changed for the bake and put back, and only when it differs (see the render engine note under Baked filters). If a bake fails on the GPU (memory, a driver) the object is baked again on the CPU, with a warning. Measured here: AO at 1024 px and 128 samples, 8.1 s on the CPU and 2.2 s on the RTX 3060. The small baked filters, the curvature and its coverage bake always use the CPU on purpose (they take under a second, and an earlier GPU variant hung Blender).

Map types ✅ *(built, untested)*:
- **Ambient Occlusion**: native Cycles AO pass.
- **World Gradient**: black to white along world Z, spanning the combined world bounding box of the selected low-poly objects. Baked through a temporary Emission override of the surface.
- **Normal (Tangent)**: native Cycles normal pass, tangent space, OpenGL (+Y). Flat on a single object. After a bake it is wired into the shader (see Normal map).
- **Normal (World)**: world-space mesh normal remapped to 0-1, baked through the Emission override. Ignores the material's bump.

**Bake on high** (single object, no high-poly) is the default when no High Poly collection is set.

**Bake from high to low** ✅: set a **High Poly** collection (children included, meshes only) and bake the selected low-poly objects. Works for every map type.
- Selected-to-active, no cage. Ray start offset (Extrusion) defaults to 2% of the low-poly's bounding-box diagonal, overridable; max ray distance is twice the extrusion.
- High-poly objects are never bake targets, even if selected. Hidden or unselectable high-poly objects, and the collections that are excluded, hidden in the viewport or unselectable around them, are switched on for the bake and put back exactly as they were afterwards (the same temporary-and-restored approach as the bake settings), so the user never has to prepare the outliner. The scene's root collection is never touched. Verified headless for an excluded collection, an excluded parent, and hidden or unselectable flags.
- For emission-based maps Cycles reads the shader from the high-poly, so the temporary Emission override is applied to the high-poly materials too (a temporary material if one has none).

### Layers and Masks
- Layers influence different channels of the PBR shader; masks control where they apply. All are built from shader graph nodes.
- A mask has a **source** ✅ (Image, Procedural or Value, with the same projection, mapping and Min/Max controls as a channel source, see Layer sources). An **image mask** is an image slot: either a new blank paint image (white reveals, black hides; size set in the redo panel) or any image already in the file, picked from Blender's thumbnail grid (`template_ID_preview`, with Open and unlink). The two are interchangeable at any time.
- Masks should be Non-Color. If a picked image is not, the panel warns and offers a one-click Set Non-Color instead of changing the image silently.
- Mask adjustments ✅: the mask has no built-in Invert, Brightness or Contrast. They are adjustment items on the mask (Invert, Brightness/Contrast, Levels, Curves, ...), added only when wanted and placed where the user wants them in the mask's stack, for example a contrast before a later blur. The mask node itself ("Essence Mask" group, version 2) only clamps the value to 0-1 and applies Enable.
- **One mask per layer or folder** ✅: the mask is a row under its owner with its thumbnail and an eye toggle (a disabled mask counts as white). More than one value is stacked on that mask as Fill items (see Mask fills), not as extra masks.
- **Selection decides the panel.** Selecting a layer or folder shows its properties and an Add Mask menu (white, black or image mask). Selecting a mask shows only the mask properties. A layer never shows both at once.
- Masks can be painted in Texture Paint mode (Image source with the UV projection only; procedural, value and triplanar masks are generated and not paintable). Switching a mask away from Image remembers its image (kept alive with a fake user) and restores it when you switch back. A Paint Mask button makes the selected mask's image the active image node and enters Texture Paint in Material mode; an Exit button leaves it.

### PBR Channels (v1)
Color, Roughness, Metallic and Bump (height) are on by default. Alpha and Emission exist and are switched on in the material's **Channels** list (Material panel). All are defined in `channels.py` as data. There is deliberately no Normal channel: a material has one normal map, which is a **mesh map slot** (see Mesh maps below), and fine surface detail that tiles (fabric, scratches) goes through the Height channel, as in Substance. See "Group variants" below for how the channel set and blend modes reach the layer node.

### Layer Structure
- One **layer node** (a node group) per layer, chained: each layer's output feeds the next layer's A inputs, and the layer's own channel values are the B inputs.
- Inputs per layer: A and B for each channel, a Mask (the already combined result of the layer's mask stack), a per-channel Enable, a per-channel Opacity, a layer Opacity and a Visible toggle (the eye). The blend factor of a channel is `mask * Visible * layer Opacity * channel Opacity * Enable`.
- **Per-channel enable** ✅: effective blend factor for a channel is `mask * enable`, so a disabled channel passes the layers below straight through, like Substance Painter.
- **Mask nodes** ✅: each mask is one instance of the shared "Essence Mask" node group (version 2). Inputs: Value, Enable. Output: the value clamped to 0-1, or 1 when disabled. The mask's items and sources are visible nodes below the layer.
- Outputs per layer: one per channel. The end of the stack has a single Bump node (see Channel Blending).
- The shared "Essence Layer" node group carries a version (currently 5: 5 added Opacity, per-channel Opacity and Visible). Outdated groups upgrade on Rebuild or Add Layer, preserving each layer's input values by socket name. Pre-mask-stack layers are converted first.
- Blender node groups have no variable number of inputs, so a stack of N layers is N group instances in a chain. This is accepted.
- **Layer sources** ✅ *(verified headless)*: each channel of a layer has a source, chosen in the Layer Properties panel: **Value** (the B socket), **Image** or **Procedural**. Image and procedural sources are ordinary nodes placed above the layer node and wired into its B input: `Texture Coordinate -> Mapping -> Image Texture or procedural group -> Map Range (float channels) or Color Ramp (color, procedurals) -> B`. They are tagged with custom properties (`essence_source`, `essence_role`) so Essence finds them again, and everything stays editable in the node editor. Float channels get a Min/Max (the Map Range output range; bump images map black = -1, mid grey = 0, white = 1). Images warn when their color space does not suit the channel (sRGB for color, Non-Color for data) and offer a one-click fix. **Projection:** UV, Triplanar (box projection on object coordinates) and Object. **Mapping:** tiling, offset and rotation from the Mapping node. **Procedurals** are wrapped node groups with a uniform interface (`Vector`, their own controls, `Contrast`, output `Fac`): Noise, Voronoi, Wave, Checker, Gradient (shared groups "Essence Proc ...", version 1). A hand-wired link into any B input survives Rebuild and group upgrades.

### Adjustments ✅ *(verified headless and in the GUI)*
An **adjustment** is a stack entry (kind `ADJUSTMENT`) owned by a layer or a mask. It transforms the owner's **own content before it is blended**, not what is below it: for a layer, the input of one channel (the fill, whatever the source is), chosen in the item's Channel dropdown (Color by default); for a mask, the mask value. Items of one owner and channel apply bottom-to-top, between the source and the blend:

`source -> adjustment 1 -> adjustment 2 -> ... -> the layer's B input (or the mask group's Value)`

Each item is a few ordinary nodes tagged `essence_source` `<uid>:ADJ`, so it stays hand-editable: a MAIN node (Blender's own Hue/Saturation, Brightness/Contrast, Gamma, Invert, RGB Curves, Color Ramp, Map Range, or the shared group "Essence Adj Levels") and a MIX node that blends original and adjusted by the item's **Opacity**. All of them work on colors; float channels and masks go through Blender's implicit float/color conversion. A Value source (no nodes) gets a constant node to adjust, and hands the value back to the socket when nothing needs it. The eye (`adj_enabled`) keeps the nodes but unlinks them. Live set: Levels, Hue/Saturation/Value (color channels only), Brightness/Contrast, Gamma (Blender's node: value to the power gamma), Invert, Curves, Gradient Map, Threshold. There is no Color Balance because Blender has no shader node for it. Adding a type means adding an entry to `adjustments.TYPES`.

**Tree view** *(built, verified in the GUI; the default, switched with the outliner icon next to the stack buttons)*: the Layers panel draws the stack from native widgets instead of a `UIList`: one box per layer with the layer and its adjustments on the left and its masks (each with its fills and adjustments) on the right, a folder as a box with the folder on the left of its header row, its masks on the right (like a layer), and its layers nested inside. Every entry is a button that selects it (drawn pressed while active) plus its eye; names are edited in the properties panels. The flat `UIList` stays as the other view. In the list view the items sit indented under their owner (a layer's items, then its masks, each mask followed by its items). Add Adjustment is a menu in the list column and in the layer and mask panels; the Adjustment Properties panel shows type, channel, opacity, the node's controls and the curve or gradient widget. The target layout (layer and mask side by side, each with an item column) needs native widgets instead of a `UIList` and is a separate task.

### Mask fills ✅ *(verified headless and in the GUI)*
A mask is a stack: its running value starts at the mask's own source and the items of the mask apply **bottom to top**, adjustments and fills mixed. An **adjustment** transforms the value built so far; a **fill** (entry kind `FILL`, owned by a mask) blends a source of its own onto it. A fill's source has the same options as a mask source (Value, Image, Procedural, projection, mapping, remap) and a blend mode: **Normal** (default: it replaces the value below, blended by Opacity), Multiply (what stacked masks do), Add, Subtract, Darken (min) and Lighten (max), plus an Opacity and an eye. A new fill is a white Value in Normal mode, so it starts by overriding what came before.

Nodes: a fill is a Math node (the blend: input 0 the value below, input 1 the fill's source, clamped; for Normal it is an Add with nothing on input 0, which passes the fill through) tagged like an adjustment, followed by the Opacity mix. Its source nodes are the usual tagged source nodes of the fill's uid, wired into the Math node's second input. A mask that is a plain Value gets a constant to run the stack on. Items reorder with the up/down arrows, so an adjustment can act on everything below it or only on one fill.

With fills a second mask is a Fill item set to Multiply, so a layer or folder has **one** mask (Add Mask is unavailable once it has one). The mask's own invert, brightness and contrast come last, after the items. A fill can be a **paint item** (see Paint items): a transparent image you paint, whose alpha is the fill's factor.

### Baked filters ✅ *(verified headless and in the GUI)*
Neighbour-based filters cannot be live shader nodes (a shader sees one pixel, and sampling around each pixel in a deep stack is far too expensive), so they are **opt-in items** on a mask or on one channel of a layer (entry kind `FILTER`, from the Add Adjustment menu on a mask or a layer): **Kuwahara**, **Blur**, **Sharpen** and **Dilate / Erode**, the compositor's own nodes. Nothing is baked unless the user adds one.

**Apply** (button in the filter's panel) does three things: it bakes the mask value built so far (everything below the filter in the mask's stack, so a contrast placed before a blur is inside the blur) to a float image in UV space with the export's emission trick, runs the compositor on it in a throwaway scene, and stores the result. The compositor settings are the filter node's own inputs, drawn in the panel; they live in a compositor group "Essence Filter <uid>" that can be edited by hand (add more nodes). The result is **one image per item**, ".Essence Filter <uid>" (a leading dot hides it from Blender's image pickers, to be confirmed in the GUI), packed, replaced on every Apply and deleted with the item along with its group. The chain samples it with the UV map; before the first Apply, or while the eye is off, the item passes the value through.

**The state is shown, not automated.** The item stores a digest of what it was baked from (the mask's source nodes, every item below it with its switches, the filter's own compositor settings; moving or renaming nodes does not count, and a filter below counts by the digest it was baked with, so stacked filters need applying in order). The Apply button is **red** (Blender's alert color) while nothing is baked or the digest differs, and carries a **green dot** when the result is current; the filter's row in the stack shows the same red or green dot. Painting changes pixels no digest can see, so after painting a mask the user presses Apply. There is deliberately no automatic refresh: a timer-driven bake from the background deadlocked Blender in Texture Paint, and an always-running clock adds complexity and one more thing for the user to be aware of. **Order and dependencies.** A filter bakes everything below it, so it is only right if the filters below it are current. **Apply** on a filter first applies any filter below it in the same mask that is not applied or out of date, in order, then itself. Applying a lower filter changes its result, so every baked filter above it becomes out of date and turns red again. **Apply All Filters** (a button under the stack in both views, red while any filter needs applying, a green dot when all are current) walks every mask bottom to top and applies whatever needs it; because of the cascade, the filters above a re-applied one come up for re-application later in the same pass. The compositor group of an item has a **fake user**: only the throwaway scene ever uses it, so without one Blender drops it when the file is saved and the filter loses its settings (found when a reloaded filter showed as out of date; verified with a save and reload).

**On a layer channel** a filter works on the layer's own fill of that channel (the source and the adjustments below it on that channel), before it is masked and blended, and has a Channel dropdown like an adjustment; a new one takes the channel of the selected item, else Color. Each channel of a layer is its own chain: applying, dependencies, Apply All and staleness never cross channels. Color channels are baked to a float RGB image and keep their color (Blur, Sharpen and Kuwahara). Dilate / Erode takes one value, so on a color it filters the brightness (the panel warns, and a new one starts on a non-color channel). The result is baked in UV space, so a **tiling or triplanar source loses its tiling** under a baked filter (the panel says so). A filter on a switched-off channel is refused with a message. Filters are not offered on folders.

Resolution is per item (256 to 4096). While a filter is applied, 3D viewports in Material Preview or Rendered are switched to Solid and put back afterwards, so the viewport is not rebuilding the shader of the material being baked from. Apply runs entirely on the CPU (a one-sample Cycles bake, and a Cycles CPU render with the CPU compositor in the throwaway scene; no GPU, no Workbench) because GPU-based variants hung Blender in the GUI, and it prints its stages to the system console (Window > Toggle System Console) so a freeze shows where it stopped. On a real 48-layer file it takes about 0.4 s at 1024 px. Every Cycles setting a bake needs (engine, samples, device, the bake options) is assigned only when it differs and put back only when it was changed: assigning the render engine again, even to the same value, resets every viewport and deadlocked Blender with a heavy material in Material Preview (see the roadmap). **UV layouts:** the value is baked in the 0-1 UV tile like any bake, and the result is sampled with **Repeat**, so islands offset by whole tiles (repeating texture layouts) show the same filtered result. Islands that overlap inside the tile are baked on top of each other, and a filter smears across island borders and does not wrap around the tile edge (the bake margin helps), so filters work best on a non-overlapping layout inside the 0-1 tile.

### Bundled masks 🟡 *(verified headless and with a real startup)*
`assets/masks` holds premade texture masks (31 ambientCG roughness maps, CC0, 1K JPG). `bundled.py` loads them into the open file as image datablocks named "Essence Mask <name>", Non-Color, with a fake user, as references to the files in the addon folder (not packed, so a file stays small). They appear in every image picker and mask grid. Loading is idempotent: each image remembers its file (`essence_bundled`), so one that is in the file already is never loaded again, even if renamed, while a deleted one comes back on the next load; a moved addon folder is repaired. It runs when the addon is enabled with a file open, after every file load, and once shortly after Blender starts (a one-shot deferred call, because Blender blocks `bpy.data` while addons register and does not fire `load_post` for the startup file). Opening any file therefore adds these images to it. This is a stopgap for the asset library of Sprint 6 (catalogs, previews).

### Live generators ✅ *(verified headless and in the GUI)*
A fourth source kind next to Value, Image and Procedural: **Generator**, for channels, masks and fills (`generators.py`). A generator reads the mesh in the shader (normals and positions cost nothing), so it needs no bake and works in EEVEE and Cycles. It is an ordinary node group "Essence Gen <name>" (version key `essence_version`) with a `Fac` output, named inputs and no texture coordinates or mapping, followed by the usual Min/Max remap (float channels and masks) or color ramp (color channels). Changing a group's version rebuilds it in place and puts every node's values and links back by socket name. Adding a generator means adding a `Gen` entry with a builder.

| Generator | Inputs | Result |
|---|---|---|
| **Directional Light** | Rotation (degrees), World Space, Use Detail Normal, Contrast, Balance | `clamp((dot(normal, direction) * Contrast) / 2 + 0.5 + Balance)`: 1 facing the light, 0.5 sideways, 0 away. The direction is +Z turned by the Rotation, so no rotation is straight up |
| **Slope** | Rotation (degrees), World Space, Use Detail Normal, Angle, Softness | 1 where the surface is within Angle degrees of the axis (+Z turned by the Rotation, so up by default), a ramp Softness wide around the edge (never a hard divide by zero) |
| **Gradient** | Rotation (degrees), Start, End | The Generated coordinate along the axis (+Z turned by the Rotation; the object's bounding box, 0 to 1), remapped by Start and End. No rotation is a height gradient (Z); turning the arrow onto X or Y gives a position gradient, and any direction works. It is always in object space, so its arrow is always a child of the object |

**Rotation and the gizmo.** The Directional Light, Slope and Gradient are aimed with a **Rotation** (an Euler vector socket, shown in degrees, limited to 0 to 360 on each axis; version 1 had a Direction or Axis vector, and a rebuild converts it). **Show Gizmo** in the Source row makes a temporary arrow, an **Empty** with the Single Arrow display, that drives the Rotation: select it and use Blender's own rotate tool, gizmo or numeric input; it points where the light comes from (or along the slope or gradient axis). Nothing is drawn by Essence (design principle 6). The link is three drivers with simple expressions that wrap the angles into 0 to 360 (`fmod(fmod(a, 2*pi) + 2*pi, 2*pi)`), so the material keeps working without the addon or Python. In object space (World Space off) the arrow is a child of the object, so the light turns with the object like the mask does; in world space it is free (World Space is greyed while the arrow exists). **Remove Gizmo** deletes the arrow and the drivers and keeps the rotation it had. Deleting the source, switching it to another kind or removing the layer deletes the arrow too; an arrow deleted by hand is simply made again by the next Show Gizmo.

**Generators that read mesh maps** 🟡 *(verified headless and on the user's `BG01` parts; the user is testing them, and found that a generator type switch kept a stale link that hid Dirt's Noise Scale, fixed)*. Three more groups read the Mesh Maps slots through the same always-linked wiring as the detail normal (`wire_baked_inputs`): the Curvature slot into `Curvature`, the AO slot into `AO`. An empty slot is replaced by a stand-in node (flat grey 0.5, white) so nothing is left open, and the panel names the slots that are empty ("Needs the Curvature map (see Mesh Maps)"). They were built as dedicated generators on purpose, to be compared with the alternative of presets made of ordinary mask items (see below).

| Generator | Inputs | Result |
|---|---|---|
| **Edge Wear** | Threshold, Softness, Noise Scale, Noise Strength | The convex part of the curvature (`curvature - 0.5`) above Threshold, a ramp Softness wide (0.5 at the threshold), with 3D noise on the object's coordinates added to the curvature to break the edge up |
| **Cavity** | Threshold, Softness, AO Amount | The concave part of the curvature (`0.5 - curvature`) plus `AO Amount * (1 - AO) / 2`, above Threshold; AO Amount starts at 0, because the AO of a hard surface high poly has large dark areas that would turn whole panels white |
| **Dirt** | Amount, Softness, Curvature Amount, Noise Scale, Noise Strength | `(1 - AO) + 2 * Curvature Amount * concave + noise` against a bar that Amount moves from above the darkest dirt (0: nothing) to below zero (1: everything) |
| **ID Select** | ID Color, Tolerance, Softness, Invert | 1 where the ID map's color is within Tolerance of ID Color (a soft step Softness wide, the distance measured in color space), or the opposite with Invert |

**Choosing the ID.** ID Select needs the color of the part. Three ways: the color swatch with Blender's own eyedropper (or typing it); **Pick from Selected Face**, where the user selects a face of the part in Edit Mode and Essence reads the ID map's color at that face's UV center (one pixel of the image, the face's UV taken modulo 1 so mirrored islands in a second tile work; no modal tool of our own, only Blender's face selection); and **Show ID Map**, which puts the map in the open Image Editors. Tolerance and Softness absorb the blended pixels at part borders. Tested on the user's `BG01` parts: a picked Object ID selected 13.1% of the map against 12.2% of texels of exactly that color (98.7% agreement).

Thresholds are in curvature units (0.5 is flat), so the values depend on the Strength used when the curvature was derived. **What Substance users run into** (searched 2026-10-25; there is no wishlist thread, but the pain points are consistent): Metal Edge Wear needs four baked maps (position, curvature, AO, world normal) and silently does nothing or misses edges when one is wrong or empty; there are two curvature bakes (from the mesh, and from a normal map, the older one, which people switch back to when the mesh one misbehaves); edge wear does not apply to a polygon-fill selection or to a user's own mask (the workaround is an anchor point); modular walls and floors have no curvature at their borders; and Adobe dropped many very specific generators in favor of smart masks, which are saved presets of the mask editors. Essence's answers so far: one derived curvature from the normal map, a named message for every empty slot, and an open question whether Edge Wear, Cavity and Dirt are better as presets of ordinary items than as generators (compare after testing).

**Normal space.** Normal-reading generators default to **object space** (Texture Coordinate Normal), so the mask stays put when the object rotates; **World Space** switches to the Geometry normal. **Use Detail Normal** reads the `Essence Normal Map` node's output instead (the world space normal including the baked high-poly detail), converted to object space when World Space is off. Essence wires that output into each generator's `Detail Normal` input on every rebuild, always linked so the switch is a plain input; with an empty Normal slot there is no such node and a Geometry node ("Essence Geometry Normal") stands in, since the detail is then just the surface normal. Nothing in the layer stack feeds the Normal Map node, so this cannot make a cycle (the Bump node would, which is why it is not used).

### Folders ✅
A folder holds layers and has its own mask (with the same fills and adjustments), which limits the combined effect of everything inside. One level only.
- **Node structure:** layers inside a folder chain as normal, starting from the stack below. A **folder node** (shared node group "Essence Folder", version 3: 3 added Visible) lerps every channel between the stack below (A) and the end of the inner chain (B) by the folder mask: `A + (B - A) * mask`. Bump included, since the inner chain's bump is `A + sum of inner heights`. An empty folder passes the stack through.
- **Data:** the layer list stays flat and bottom-to-top. Every entry has a `uid`, a `kind` (layer, folder or mask) and a `parent` (the owner's uid). All reordering goes through the tree helpers in `stack.py`.
- **UI:** children indented under the folder, masks under their owner. Add Folder creates an empty folder above the active entry. Add Layer with a folder active adds into it. Up/down arrows move a layer within and across folder borders; a folder moves as a block. Removing a folder ungroups its layers; removing a layer or folder removes its masks.

### Layer Properties
- The layer's Bump value is a signed slider from -1 to 1: positive raises, negative carves, 0 has no effect.
- For height textures the same range maps to black = -1, mid grey = 0, white = 1 (as in Substance Painter). Height images are Non-Color, and a new height image starts mid grey.

### User Interface
- All Essence UI lives in the sidebar (N panel) using standard Blender panels, UI lists and properties, in both the 3D Viewport and the UV/Image Editor.
- Panel order: Layers, then Layer Properties or Mask Properties depending on selection, then Mesh Maps (the Normal and AO slots), Material (channels, Emission Strength, Bump Strength and Distance), Bake, Export.
- No custom workspace, no custom drawn UI. A left-hand panel layout is not pursued because addons may not be able to place panels there.

### Painting
- Painting is only done on masks; layer channel values are set through the properties panel. (This changes in the dream scope, see Part B, Painting.)
- While painting, the viewport shows the final material, not the mask alone.

### Channel Blending (v1)

Each layer blends its channels onto the result of the layers below it, with the layer's mask as the factor. A is the stack below, B is this layer.

| Channel | Data | Blend in v1 | Per-layer formula |
|---|---|---|---|
| Color | color | Mix (lerp) | `A + (B - A) * mask` |
| Roughness | float | Mix (lerp) | `A + (B - A) * mask` |
| Metallic | float | Mix (lerp) | `A + (B - A) * mask` |
| Bump | float height | Add | `A + B * mask` |

Notes:
- **Color, roughness, metallic: Mix.** Where the mask is 1 the layer replaces what is below, 0 it has no effect.
- **Masks are data, not color.** Mask images use Non-Color. Only color-carrying images use sRGB.
- **Bump: Add, not Mix.** Height accumulates across layers (Substance's height defaults to Linear Dodge/Add, mid grey is zero). A mask of 0 leaves the height untouched.
- **One Bump node, at the end of the stack.** All heights are summed into a single height value feeding one Bump node (Strength, Distance), whose Normal output goes to the shader's Normal input. Chained Bump nodes would re-evaluate the height network and compound. See "Verify".
- A layer's height can be scaled per layer (`B * strength * mask`) before it is added; overall intensity is controlled once, at the end of the stack.

### Mesh maps and the Normal slot ✅
The **Mesh Maps** panel has one slot per map (Normal, Ambient Occlusion, Position, Curvature, ID and Thickness; generators read them). A slot is an image node named `Essence_Bake_<MAP>`: a bake fills it, or the user picks any image. The **Normal** slot is wired as **image, Normal Map node (Tangent), Bump node's Normal input, BSDF Normal**: the normal map is evaluated before the bump, as Blender requires, and layer height feeds the Bump node's Height, so both combine in the single Bump node. An empty slot feeds nothing. The Normal Map **Strength** sits in the slot. Export reads the AO slot for the MaskMap.

**AO overlay** ✅ *(verified headless)*: an image in the AO slot is also used at the end of the shading tree, like the AO pass of a PBR renderer. A Strength slider (the `Essence AO Strength` Mix node, whose Factor is limited to 0 to 1) controls `lerp(1, AO, Strength)`, which multiplies **Base Color** (`Essence AO Color`, a Multiply Mix node) and **Specular IOR Level** (`Essence AO Specular`, a Math node carrying the BSDF's own specular value). Strength 0 changes nothing; an empty slot unhooks the overlay and keeps the nodes and the strength. It sits after the layer stack and Export bakes the layer outputs, so exported maps never carry it (the MaskMap still gets the AO in its own channel). Blender cannot restrict AO to indirect light only (Substance does), so the multiplier also dims direct light in the viewport: that is the accepted approximation. A future Specular layer channel feeds the Math node's input, so the order stays stack, then AO.

**ID** ✅ *(verified headless and in the GUI)* is baked like the other maps (Bake panel, "ID by"; see B8) into the ID slot. It is read by the **ID Select** generator (see Generators that read mesh maps), which chooses one part of it by color. Anti-aliasing blends IDs at their edges, so the panel warns when it is on.

**Curvature** ✅ *(verified headless and in the GUI)* is a derived slot (`curvature.py`): **Derive from Normal** runs a compositor group on the Normal slot's image in a throwaway scene (the same runner as the baked filters) and puts a packed float image called `<object>_Curvature` into the Curvature slot. Convex edges are bright, concave crevices dark, flat areas 0.5. The group "Essence Curvature <uid>" (fake user, hand-editable) blurs the normal by **Width** pixels, takes the divergence `nx(x+1) - nx(x-1) + ny(y+1) - ny(y-1)` from four shifted copies, multiplies it by **Strength** scaled by `size / 1024` (the derivative is per pixel, so the same shape gives the same value at any resolution) and adds 0.5. The result has the size of the normal map, so there is no size setting. It works on any normal image in the slot, baked or picked. The button is red while the result is missing or out of date, with a green dot when current; staleness is a digest of the normal image (which one, its size and the stamp every Essence bake leaves on its image as `essence_stamp`), the two settings and the group's nodes. Painting a normal by hand is not seen, like painting a mask under a filter. **Island borders.** Where two UV islands are close, their bake margins meet and the normal jumps from one island's tangent frame to the other's; the derivative reads that jump as a sharp edge, and the blur and the one pixel neighbour lookup carry it into the island's own border pixels (a margin of the right size cannot prevent it: no margin gives a rim against the flat unbaked color, a margin larger than half the gap gives a line where the margins meet). So Derive also bakes the island coverage (the same bake with margin 0, a fraction of a second), and the group keeps the curvature only inside the islands, shrunk by the reach of the filter (`Width` rounded up, plus 2 px), and refills the rest, the padding and the contaminated band, from the nearest island interior with the compositor's Inpaint node (32 px), flat grey beyond. The cost is that curvature within a few pixels of a UV border is continued from further inside; a UV border is a seam, where curvature is not meaningful anyway. Checked on the user's `BG01` layout (about 84% island coverage): the black pixels in the 3 px band inside the island borders fell from 2.2% to 0.1% and the interior stayed the same (mean difference 0.0026). Texels that were never baked inside an island (a ray that missed the high poly leaves the flat normal) still make a real step where the high poly ends, which is data and stays.

It took 0.07 s on a 1024 px bake of a hard-surface high poly, and about a second more for the coverage. Files made before the slot existed get it from Derive or from a rebuild (the panel offers one).

While a Normal (Tangent) bake runs, the BSDF's Normal input is temporarily unlinked, so the bake does not capture the material's own bump or normal map. A low-poly-only tangent bake is therefore flat.

### Export ✅
v1 exports for **Unity HDRP** only, from the active object's active material (needs at least one layer and a UV map). Settings: Format (PNG, TGA, EXR), Resolution (512 to 4096), Folder (default `//Export/`).

| File | Content |
|---|---|
| `<Material>_BaseMap` | Top layer's color. sRGB (Non-Color for EXR). |
| `<Material>_MaskMap` | R Metallic, G Ambient Occlusion, B Detail Mask (white), A Smoothness (`1 - roughness`). Non-Color. |
| `<Material>_Normal` | Tangent space, OpenGL (+Y). A real bake of the whole material, so the baked normal map and the layer bump are combined. |

Values are baked from the top layer node's outputs with the Emission trick, so they are exact and unlit. AO comes from an existing `<object>_AO` bake (scaled to the export resolution) and is white if none exists. Temporary images and nodes are removed afterwards.

---

## Part B. The dream scope

Full parity target: everything a Substance Painter user does in a texturing session, expressed in Blender's own systems. Each area lists the feature, the Blender infrastructure it should use, its cost class, its status and its priority.

### B1. Channels

Channels are data (`channels.py`). The Principled BSDF already exposes nearly every channel Substance has, so the work is adding definitions plus a few special targets. Only channels enabled for the material are generated (see B12, performance), so unused channels cost nothing.

| Channel | Blender target | Blend | Status | Pri |
|---|---|---|---|---|
| Base Color | BSDF Base Color | Mix + blend modes | ✅ | |
| Roughness | BSDF Roughness | Mix + blend modes | ✅ | |
| Metallic | BSDF Metallic | Mix + blend modes | ✅ | |
| Height | Bump node (Height) | Add | ✅ | |
| Normal | Not a layer channel: one normal map per material, see Mesh maps. Layer detail goes through Bump | | 🚫 as a channel (decided; avoids a per-layer normal blend cost) | |
| Opacity / Alpha | BSDF Alpha | Mix + blend modes | ✅ BaseMap alpha on export | |
| Emission (color + strength) | BSDF Emission Color; strength set to 1 when the channel is switched on | Mix + blend modes | ✅ own file on export | |
| Ambient Occlusion | not a BSDF input; kept as an exported channel and a viewport overlay with a Strength slider (Base Color and Specular), see Mesh maps | Multiply | 🟡 baked, exported and overlaid, not a layer channel | P1 |
| Specular level / Specular tint | BSDF Specular IOR Level / Tint | Mix | ⬜ | P1 |
| Coat (weight, roughness, tint, normal) | BSDF Coat inputs | Mix | ⬜ | P2 |
| Sheen (weight, roughness, tint) | BSDF Sheen inputs | Mix | ⬜ | P2 |
| Anisotropy (level, rotation) | BSDF Anisotropic / Rotation | Mix | ⬜ | P2 |
| Subsurface / Translucency (weight, radius, color) | BSDF Subsurface inputs | Mix | ⬜ | P2 |
| Transmission / IOR / Absorption | BSDF Transmission Weight, IOR | Mix | ⬜ | P2 |
| True displacement | Displacement node into Material Output (fed by the same accumulated height) | Add | ⬜ | P2 |
| Thin film | BSDF Thin Film inputs | Mix | ⬜ | P4 |
| User channels (custom, exported only; e.g. packed masks) | no shader target, exported via packing presets | Mix | ⬜ | P2 |
| Glossiness / Specular-glossiness workflow | derived on export (`1 - roughness`) | n/a | 🟡 smoothness only in Unity export | P3 |

Channel-set UI ✅: a per-material **Channels** list (checkbox per channel, in the Material panel) decides which channels exist in layers, panels and export. Switching a channel off removes its sockets and source nodes from every layer, unlinks it from the shader, and switching it on again brings its sources back. If the list turns out to be too much it can be removed later without touching the layer builder.

### B2. Layer content (what a layer *is*)

Today a layer is "a value per channel". The dream layer has a **source per channel**, like Substance's Fill layer with per-channel textures, and the source kinds below. Sources are shader nodes feeding the B inputs of the layer node, so they stay hand-editable.

| Feature | Blender infra | Cost | Status | Pri |
|---|---|---|---|---|
| Uniform value per channel | Socket value | L | ✅ | |
| **Image texture source per channel** (picker like masks) | Image Texture node, colorspace per channel | L | ✅ | |
| **Procedural source per channel** | Any node group with a color/float output, picked from the asset browser or pointer | L | ✅ (built-in procedurals as wrapped groups; asset-browser picking comes with the asset library) | |
| **Projection modes**: UV, Triplanar (box blend), Object/Generated coords, Planar from an Empty, Spherical, Cylindrical | Texture Coordinate + Mapping + Image Texture projection (Box, Flat, Sphere, Tube) | L | 🟡 UV, Triplanar and Object done | P2 (planar from an Empty, spherical, cylindrical) |
| Mapping controls (tiling, offset, rotation, scale; uniform and per channel) | Mapping node, exposed as layer inputs | L | ✅ tiling, offset, rotation | |
| **Layer opacity** (overall, plus per-channel opacity beyond the enable checkbox) | Factor multiplier in the layer group | L | ✅ | |
| **Blend mode per channel** (see B3) | Mix node blend types, Math formulas | L | ✅ | |
| Paint layer (channel images painted directly) | Per-channel image + paint slots | B/L | ⬜ | P1 |
| Layer duplicate / copy / paste / hide / solo | Operators, node group copy | O | ⬜ | P1 |
| Layer instances (shared content, linked) | Shared node group / shared image datablock | L | ⬜ | P3 |
| Height blending (layer height drives where it shows, "material layering by height") | Mask generator: Math Compare of heights | L | ⬜ | P2 |
| Anchor points (use an intermediate stack result as a generator input) | Link from a chosen layer's output socket into a generator node | L | ⬜ | P2 |
| Decals (image projected from an Empty, with alpha) | Layer with Object-coordinate projection from an Empty + mask | L | ⬜ | P2 |
| Layer lock (protect from edits) | Flag in `EssenceLayer`, UI disables properties | O | ⬜ | P4 |

**Paint layers vs fill + painted mask.** Blender's brush paints one image at a time, whereas Substance paints all channels in one stroke. The accepted Essence equivalent is a **fill layer (values or textures) with a painted mask**: you "paint with a material" by painting the mask. This is already built and covers most hand-painting. Direct per-channel painting (paint layer) is P1 as an addition, with the stroke going to one channel at a time (selected in the panel), which is Blender's native limit.

### B3. Blending modes

Substance blend modes map almost one to one onto the Mix node (Color type), which already has them. The factor is `mask * opacity * enable`.

| Group | Modes | Status | Pri |
|---|---|---|---|
| Basic | Normal (Mix), Multiply, Divide, Add (Linear Dodge), Subtract, Difference, Exclusion | ✅ | |
| Contrast | Overlay, Soft Light, Hard Light (via Linear/Vivid Light), Linear Light, Pin Light | ⬜ | P1 |
| Dodge/burn | Color Dodge, Color Burn, Linear Burn, Screen | ⬜ | P1 |
| Compare | Darken, Lighten | ⬜ | P1 |
| Color | Hue, Saturation, Color, Value/Luminosity, Tint | ⬜ | P2 |
| Special | Replace (ignores layers below), Passthrough (folder), Inverse Divide, Hard Mix | ⬜ | P2 |
| Normal blends (Whiteout, Detail RNM) | Dropped together with the Normal channel. A normal map combine node can come back if a use appears | | 🚫 | |

Float channels (roughness, metallic, height) use the same modes through the Mix node's RGBA type with grey packing, or Math nodes, whichever is cheaper per mode.

**Group variants** ✅. The Mix node's `blend_type` is a node property, not a socket, so it cannot be a layer input. The layer node therefore uses a **group variant**: one generated node group per distinct combination of channel set and blend modes, swapped on the node when the dropdown changes (values and links are kept by socket name). A layer is still one node and only the combinations in use exist. Naming is readable: the plain material uses "Essence Layer", others add what differs, e.g. "Essence Layer +Emission Roughness:Multiply" (a hash when it would be too long). Every mode has the shape `lerp(A, f(A, B), factor)`: color channels use the Mix node's own blend types (clamped to 0-1), scalar channels use Math formulas, normal channels decode, combine and re-encode. The group key is stored on the group (`essence_key`) so upgrades rebuild every variant. Folders use a variant per channel set only (they always lerp).

### B4. Masks

| Feature | Blender infra | Cost | Status | Pri |
|---|---|---|---|---|
| Image mask (blank or picked), invert, brightness, contrast, stack, eye toggle, reorder | Image Texture + math nodes | L | ✅ | |
| Folder masks | same | L | ✅ | |
| Mask stack blend modes (add, subtract, ...) beyond multiply | Mask group extension | L | ⬜ | P1 |
| **Mask sources with the same options as a channel source** (Image, Procedural, later Gradient and Baked map): projection (UV, Triplanar, Object), tiling/offset/rotation, Min/Max remap, built on the same `sources.py` machinery and followed by the existing invert/brightness/contrast. Only an Image mask in UV projection can be painted; a procedural or triplanar mask is generated, not paintable (as in Substance) | Source nodes feeding the mask group's Value input | L | ✅ Image, Procedural and Value; Gradient and Baked map come with the asset library and mesh maps | |
| Mask adjustments: **Levels** (in/out, gamma), **Curves** (RGB Curves), **Threshold/Contrast**, **Color Ramp / Gradient remap**, **Smooth step** | Map Range, Float Curve, Color Ramp nodes | L | 🟡 brightness/contrast only | P1 |
| Mask **filters needing neighbours**: Blur, Sharpen, Edge detect, Dilate/Erode, Slope blur | Compositor on an image, run as an explicit bake | B | ⬜ | P2 |
| Mask by selection: Material ID / Color ID / Object ID | Baked ID map + Math Compare, color picker in the panel | L | ⬜ | P1 |
| Mask by UV island / UV tile | UV-driven node setup or baked island ID | L | ⬜ | P3 |
| Mask by polygon / mesh face selection ("polygon fill") | Face Maps / vertex group / attribute read through Attribute node | L | ⬜ | P2 |
| Mask from **vertex group / attribute** | Attribute node | L | ⬜ | P2 |
| View mask on its own (solo the mask in the viewport) | Debug view, see B9 | O | ⬜ | P1 |
| Mask → Selection → paint only inside a region (paint masking) | Blender's Face Selection / Mask paint masking in Texture Paint | O | ⬜ | P2 |
| Channel select for image masks (R/G/B/A, e.g. packed ORM) | Separate Color | L | ⬜ | P2 |
| Mask copy/paste between layers; mask as a linked (instanced) image | Operators | O | ⬜ | P3 |
| Mask resolution change / resample | Image scale | O | ⬜ | P3 |

### B5. Folders

| Feature | Status | Pri |
|---|---|---|
| One-level folder with mask stack and normal (lerp) blend | ✅ | |
| **Nested folders** (tree helpers already use `parent`; the chain builder and UI must recurse) | ⬜ | P1 |
| Folder **Passthrough** mode (inner layers blend straight onto the stack, with the folder mask multiplied into each inner layer's mask: cheaper and what Substance does by default) | ⬜ | P1 |
| Folder opacity | ⬜ | P1 |
| Folder blend mode (Normal vs Passthrough only; arbitrary blend modes per folder are P3) | ⬜ | P1 / P3 |
| Per-channel enable on folders | ⬜ | P3 |
| Folder collapse / expand in the tree view (a triangle on entries that have something under them; a folder hides its layers, a layer hides its own and its mask's items; stored per entry, defaults to expanded) | ✅ | |
| **Smart material** = a folder (with its masks and layers) saved as an asset and dropped onto an object | Node-group-based assets + Asset Browser; operator to instantiate | O | ⬜ | P1 |

### B6. Generators, procedurals and the asset library

Substance ships thousands of Designer procedurals; Essence does not rebuild that. The strategy is **Blender's texture nodes + a curated asset library**.

| Feature | Blender infra | Cost | Status | Pri |
|---|---|---|---|---|
| Procedural noise sources | Noise (fBm, ridged, multifractal, hybrid, hetero), Voronoi (F1, F2, smooth, edge distance), Gabor, Wave, Magic, Gradient, Checker, Brick, White Noise | L | ⬜ (usable by hand) | P0 via picker, see B2 |
| **Essence asset library**: node group assets with a uniform interface (Value in, Mask/Color out, sliders) in catalogs: Noises, Patterns, Grunge, Gradients, Generators | Asset Browser, asset shelf, catalogs, "Mark as Asset" | L | ⬜ | **P1** |
| **Grunge / dirt maps** (a bundled image set, 16-bit, Non-Color) | Image datablocks as assets | L | 🟡 31 ambientCG (CC0) roughness maps ship in `assets/masks` and load into every file as images (see Bundled masks); not yet catalogued assets | P1 |
| **Smart masks / generators**: Edge Wear, Dirt, Dust, Leaks, Curvature Wear, Cavity Dirt, Height Gradient, World Gradient/Position Gradient, Ambient Occlusion Mask, Thickness Mask | Node groups consuming baked mesh maps (see B8) + noise + Color Ramp/Levels | L | 🟡 live ones done (Directional Light, Slope, Gradient, see Live generators); the ones that read baked maps (Edge Wear, Dirt, Cavity, Curvature, Thickness, AO mask) wait for the mesh maps | P1 |
| Tile generator / Splatter / Shape / Weave | Voronoi with per-cell random offset/rotation as the shader-node approximation; or exact versions as imported images | L/B | ⬜ | P3 |
| Warp / distortion of any texture | Perturb the vector input with a Noise node before the texture | L | ⬜ | P2 |
| Randomization (color or value per tile) | White Noise on cell id, Object Info Random | L | ⬜ | P2 |
| Layer presets (a layer with its channels and masks) | Node group asset | O | ⬜ | P2 |
| Importing Substance (`.sbsar`) | no Blender infra | 🚫 | |

**Procedural cost.** Procedural nodes are evaluated live at every shading point (every fragment in EEVEE, every sample and bounce in Cycles), unlike Substance, which computes a procedural once into a cached bitmap. The coordinate space (UV vs Object) is not the cost driver; the node is: Noise detail (octaves) scales roughly linearly, Dimensions 2D < 3D < 4D, Voronoi Smooth F1 and Distance to Edge cost more than F1, and distortion adds on top. Guidance for generator assets: default to 2D where the result is flat, keep default detail low, and note the cost in the asset description. For heavy procedural stacks the remedy is **Freeze to image** (B12), a later performance step, not a P0 requirement.

**Live versus baked generators.** A generator is live (L) when everything it needs is something a shader reads at no real cost: the surface normal (Geometry node in world space, Texture Coordinate in object space, or the Essence Normal Map node's output for a normal that includes the baked high-poly detail), the position (Geometry, or Texture Coordinate Generated across the bounding box), and plain math on them. Directional light, slope, up and gradient masks are live, work in EEVEE and Cycles alike and export correctly (the export bakes the stack's outputs). A generator reads a **baked mesh map** (B) when it needs the neighbourhood of a point or something a shader cannot read in both renderers: curvature, thickness and AO (the Pointiness, Bevel and AO nodes are Cycles-only or screen-space), ID maps (a shader cannot read a material index) and anything derived from a high-poly. Edge wear, dirt and cavity masks therefore read baked maps; light and slope masks do not. The object-space normal is the default for light masks so they do not change when the object is rotated.

**Baked inputs for live generators.** A generator that reads the mesh can also read a baked map instead, so it follows the high poly without costing anything live. The pattern is the one the detail normal already uses: the generator group has an image-derived input that Essence wires from the matching Mesh Maps slot on every rebuild (always linked), and a `Use <map>` switch on the group chooses between the live value and the baked one. Done: `Use Detail Normal` (the Normal slot) on Directional Light and Slope. Done: `Use Baked Position` on Gradient (the Position slot, sampled with the UV map: one image lookup; the stand-in is the Generated coordinates, node "Essence Generated Coordinates"). A switch is a plain input, so turning it needs no recompile beyond the usual, and an empty slot falls back to the live value.

**Interface contract for assets.** A generator asset exposes: `Mask` or `Color` output, inputs named `Scale`, `Contrast`, `Balance`, `Seed`, mesh-map inputs where relevant (`Curvature`, `AO`, `Thickness`, `Position`). Because they are normal node groups, users author their own and drop them in their library.

### B7. Filters and adjustments

Substance has **adjustment layers/filters** (Levels, HSL, Color Balance, Curves, Brightness/Contrast, Invert, Blur, Sharpen, Gradient Map, Threshold, Warp, Directional Warp, Slope Blur, Histogram Scan, ...). Essence treats them as nodes applied at one of three points:

| Where | Meaning | Status | Pri |
|---|---|---|---|
| **Adjustment** items on a layer or mask: transform the owner's own content before it is blended (see Adjustments). A stack-wide adjustment that transforms everything below (`A + (f(A) - A) * mask`) is not built; a folder would host it | tagged node chain in the source | ✅ | |
| **Layer filter**: transforms a layer's own content before blending | an adjustment item on a layer | ✅ | |
| **Mask filter**: transforms a mask value | an adjustment item on a mask (plus the built-in invert/brightness/contrast) | ✅ | |

Live (L) adjustments, all single Blender nodes: Levels (Map Range), Brightness/Contrast, Hue/Saturation/Value, Color Balance, RGB Curves, Invert Color, Gamma, Color Ramp (gradient map), Math clamp/threshold, Separate/Combine Color (channel mixing).

Neighbour-based filters (Blur, Sharpen, Edge Detect, Dilate/Erode, Slope Blur, Bevel, Emboss) are **B**: the compositor processes a layer's or mask's image and writes the result to an image that the layer then samples. Presented as a "Filter (baked)" entry with an explicit Refresh button, so the cost is visible. P2.

| Feature | Cost | Status | Pri |
|---|---|---|---|
| Adjustment items | L | ✅ | |
| Filter stack on layer / mask | L | ✅ (several items stack) | |
| Live adjustment set (levels, HSV, brightness/contrast, gamma, curves, invert, gradient map, threshold) | L | ✅ (no color balance: no shader node) | |
| Baked filter set via compositor (blur, sharpen, Kuwahara, dilate/erode) on masks and layer channels | B | ✅ (edge detect and slope blur not built) | P2 |
| Normal-aware filters (normal blend, normal from height, height from normal, normal intensity) | L/B | ⬜ | P2 |

### B8. Baking (mesh maps)

Substance's bakers, mapped to Cycles and emission-override tricks. All result images are stored per object as `<object>_<Map>` and are the inputs of generators (B6).

| Map | Method | Status | Pri |
|---|---|---|---|
| Ambient Occlusion | Cycles AO pass | ✅ | |
| Normal (tangent) | Cycles NORMAL | ✅ | |
| Normal (world) | Emission override | ✅ | |
| World Gradient / Position Gradient | Emission override | ✅ | |
| **Curvature** (convex/concave) | **Decided (tested on a real hard-surface high and low poly, 2026-10-24): derived from the Normal slot** with the compositor, as the divergence of the tangent space normal (the d(nx)/du + d(ny)/dv of the normal map, signed: convex bright, concave dark), so it needs no bake of its own and works on any normal map the user picks (built and verified, see Mesh maps). Cycles' Pointiness was almost flat on hard surface (it is per vertex, so it needs dense meshes) and Bevel gave only an unsigned edge mask, took 3 times longer to bake (15 s against 4.3 s for the normal) and needs samples to be clean. Limits of the chosen method: the line width is in pixels (it depends on the map size, so it has a Width setting), and UV island borders can leave a faint artifact (the bake margin helps) | ✅ verified headless | **P1** |
| **Position** (object-space XYZ) | Emission override of Geometry Position, remapped to the low poly's **Generated coordinates** (0 to 1 across its bounding box in object space; an axis with no extent reads 0.5), so the live Gradient and the baked map are the same quantity: the object's transform is undone by a Mapping node in Texture mode (location, rotation and scale; a sheared object is not handled), because a high-poly hit belongs to another object. Baked from a high poly it is the position of the **high poly's** surface; the Gradient has a `Use Baked Position` switch (see "Baked inputs for live generators"), and it is the base of Height from mesh | ✅ verified headless | P1 |
| **Thickness** | Ambient occlusion from the inside (the AO node with Inside on, 16 samples) through the emission override: rays go into the mesh, and the fewer reach the other side within a **Distance**, the thicker it is (bright), the more, the thinner (dark). Distance 0 is automatic: a tenth of the biggest low-poly object, so the same shape gives the same map at any size. Baked from a high poly it is the high poly's thickness (the shader runs on the surface that is hit). The Bake panel shows the Distance for this map only | ✅ verified headless | P1 |
| **ID maps**: a flat color per part, with a choice of what a part is (the "ID by" dropdown in the Bake panel): **Object**, **Material**, **Mesh Island** or **UV Island**. Object, Material and Mesh Island also work from a high poly (the color is that of the part the ray hits, so each high-poly part or loose piece gets its own); UV Island is the low poly's own and ignores a High Poly collection. Colors are stable (an object's from Object Info Random, a material's from a hash of its name, a mesh island's from Random Per Island, a UV island's from its number). Material IDs read the material the ray hits, so high-poly materials need to differ for the high poly to be told apart | Emission override. A shader cannot tell UV islands apart, so Python numbers the islands (faces that share a UV vertex are one island), writes them as a temporary face attribute `essence_uv_island` on the low poly and the bake reads it with an Attribute node; the attribute is removed again whatever happens | ✅ verified headless, and on a real hard-surface high and low poly | P1 |
| **Height from mesh** | Position delta high vs low, along the normal | ⬜ | P1 |
| World-space direction (up/down/side masks) | **Live** from the Geometry normal (Sprint 5), no bake | L | ⬜ | P1 |
| Bent normals | Cycles with custom shader | ⬜ | P3 |
| Opacity (from alpha geometry) | Emission override | ⬜ | P3 |
| Baked **diffuse/color** (Cycles DIFFUSE, color only) for transferring vertex colors or textures high to low | Cycles bake | ⬜ | P2 |
| **Cage** object support | Cycles `use_cage`, `cage_object`; a Cage field next to High Poly replaces the extrusion offset and ray distance limit | ✅ verified headless | |
| Match by name (`_low` / `_high` suffix) as an alternative to a High Poly collection | Collection or name pairing | ⬜ | P2 |
| **Baking groups** (sets of objects and bake settings) | Scene collection of bake settings | ⬜ | P1 |
| Multi-object bake into one texture set (shared UV) | Cycles bake to one image | 🟡 one image per object today | P1 |
| Anti-aliasing / bake supersampling | Anti-aliasing option (Off, 2x, 4x): bake at that multiple of the size (margin scaled too), then halve repeatedly with `Image.scale` so each step is an exact 2x2 average. Capped at 8192 px baked. Cycles samples cannot do this: they smooth shading, not the hard edges between hit and miss or between islands and background | ✅ verified headless | |
| Margin / dilation | Cycles margin | ✅ | |
| **UDIM** bake and paint | Blender UDIM image support | ⬜ | P2 |
| Bake presets (save settings) | Asset or Scene property | ⬜ | P3 |
| Mesh map **stale warning** (baked map older than mesh edits) | Mesh modification time check | O | ⬜ | P4 |

**Limitation to document:** a mesh map is per object and per UV layout. A material shared by several objects with different UVs cannot use one baked map. The v1 rule: baked-map-driven features need one object per material (a "texture set"), or objects sharing one UV atlas.

### B9. Painting and viewport

Painting is Blender's Texture Paint. Essence only chooses the target image and configures the mode.

| Feature | Blender infra | Status | Pri |
|---|---|---|---|
| Paint a mask | Texture Paint, active image node | ✅ (untested) | |
| **Enter paint automatically on selecting a mask** | Selection callback (Open Question 1) | ⬜ | P2 |
| **Paint a layer channel directly** (paint layer) | Active paint slot per channel image | ⬜ | P1 |
| **Paint item on a mask** ("paint over the stack"): a Fill whose image you paint, so painting sits at one place in the mask's stack: above the items below it and under the ones above it, like a paint layer inside a mask. Decided design in "Paint items" below | Texture Paint on an image with alpha; the fill uses the image's alpha as its factor | O | ✅ verified headless | **P1** |
| **Fill (bucket) modes** for painting a mask: **Full** (the whole image, today's Fill tool), **Mesh** (only the geometry connected to the one under the cursor, like L in Edit Mode), **UV island** (the island under the cursor) and **Face** (the face under the cursor). Built on the native Fill tool and Blender's own face-selection paint masking: Essence only selects the faces for the mode (linked pick, UV island select, a single face) and lets the native tool fill inside the mask, then restores the user's selection. No own fill algorithm | Texture Paint Fill tool + `Mesh.use_paint_mask` + face selection | O | ⬜ | P1 |
| Brush library: soft, hard, noise, stencils, textures | Blender brush assets (native), no Essence brushes | native | |
| Symmetry, stencil/texture masks, projection paint, clone, smudge, fill, blur | Native Texture Paint tools | native | |
| Paint with material (paint a fill layer's mask) | Fill + mask, see B2 | ✅ | |
| **Paint-through / stencil projection from camera** | Native Texture Paint stencil | native | |
| Paint on float images for height (no banding) | New paint masks and baked maps are float buffers (baked maps have a Float toggle). Height images are picked by the user, so their format is theirs | ✅ masks and bakes | |
| **Viewport channel solo**: view Color, Roughness, Metallic, Height, Normal, AO, a mask, or any layer's output alone | An "Essence View" switch node group in front of the Material Output (Emission of the chosen socket) | ✅ as the channel buttons of the Material panel | **P1** |
| Viewport isolate layer ("show only this layer") | same switch | ⬜ | P2 |
| **Preview a mask or layer by alt-clicking its thumbnail** in the tree view: the viewport shows that mask (or the layer's output) on its own, alt-click again to return. Another entry point to the same "Essence View" switch node group as the channel solo, so no extra shader machinery | same switch | O | ⬜ | P1 |
| **Preview button** next to the eye on layer and mask rows: a toggle that stays on until clicked again and shows that mask as flat black and white, or that layer's own color flat, with no shading. Decided design in "Preview (solo)" below | the same switch, wired through the material | O | ✅ verified headless | P1 |
| Texture set / material list with resolution per set | Material slots + per-material essence settings | ⬜ | P1 |
| Lookdev: HDRI, lights, matcap, Material Preview, Cycles | Native | native | |
| View transform warning (Standard is accurate for texturing; AgX/Filmic shift colors) | Info label | ⬜ | P4 |
| Display of UV overlap / texel density | Native UV overlays; Texel density through native Mesh analysis | native | |

**Preview (solo)** ✅ *(verified headless, GUI check pending)*: built as designed. The viewport can only show what the material shows, so the preview is **an Emission node wired into the Material Output**, not Solid shading with flat lighting: the Workbench engine ignores the node graph, so procedurals, generators, adjustments and mask stacks would not show at all, and Solid's "flat lighting" only flattens the shading of a plain color.
- **Wiring.** A tagged Emission node (`essence_preview`) takes the chosen socket and replaces the Surface input of the Material Output; the original Surface link (node and socket names) is stored on that node and put back when the preview ends. The state lives in the graph, so the button's pressed look is derived from it and survives a save, a reload and Rebuild. One preview at a time: clicking another row moves it, clicking the same row ends it. Removing the previewed entry ends it.
- **What it shows.** A mask: the mask's output after its fills, adjustments, filters and Enable, as grey. A layer: only that layer's own **Color** fill (the Color channel's input after its source and adjustments, before the mask, opacity and blend), flat (decided with the user: the layer button shows only that layer).
- **Viewport.** It needs Material Preview or Rendered (Solid ignores nodes). Essence does not switch the viewport shading by itself (user agency, and switching shading around bakes froze Blender before): the panel shows a hint while a preview is on and the viewport is in Solid.
- **Color.** Emission goes through the view transform, so previews are exact with Standard and shifted with AgX or Filmic (see the view transform hint, P4).
- **Cost and safety.** One rewire is one shader recompile, and the Principled BSDF is out of the compiled graph while previewing, so it is cheaper than the full material. Painting a mask works while it is previewed (the use case: paint with only the mask visible). Bakes already put the Surface link back as they found it, so a bake during a preview keeps the preview. Export bakes the layer outputs and is unaffected.
- **Same mechanism.** Preview buttons for the **whole stack's** result per channel (Color, Roughness, Metallic, Height, Alpha, Emission, the Normal and AO slots; decided with the user) and the alt-click on a thumbnail use the same preview state, with a different source socket (the stack output instead of one layer's fill).
- **As built.** `preview.py`. A toggle (the shading-texture icon) sits next to the eye on every layer and mask row, in the tree and the list view; the Material panel has a *Preview the whole stack* box with a button per active channel; while a preview runs the Layers panel shows "Previewing: <name>" with a stop button, and a hint when the viewport is in Solid. The Emission node (`Essence Preview`, with the shown thing in `essence_preview` and the former Surface node and socket in `essence_prev_node` and `essence_prev_socket`) feeds the Material Output; `stop` removes it and links the old source back, or the Principled BSDF if that node is gone. A layer whose Color is a plain value has no socket to show, so a color node follows that input live through four drivers (a driver on a node socket of the same tree, simple expressions, no Python at run time) and goes away with the preview; a layer's color that is linked shows what is linked, so its adjustments are included. A height channel is remapped (`h * 0.5 + 0.5`, grey is flat). `nodes.rebuild_stack` calls `preview.refresh` afterwards, so a rebuild, adding a layer or a changed channel set keep the preview pointed at the right socket, and removing the previewed entry ends it. A bake during a preview keeps it (the bake puts the link back as it found it); a channel that is off or has nothing feeding it reports instead of previewing. The alt-click on a thumbnail is not built.
- **Tests.** The Emission carries the right value (a bake of the real Material Output): a mask as grey, a layer's own color whatever its mask, with an Invert adjustment, a plain color followed live, the stack's Roughness, Color and Bump (0.75 for a height of 0.5); the original link back exactly after one, after switching between kinds, after removing the entry and after a reload; a bake during a preview; every row, the channel buttons, the notice and the Solid hint draw with valid icons.

**Paint items** ✅ *(verified headless and in the GUI)* (raised by the user 2026-10-25: "we cannot paint on top of an adjustment stack in a mask"). Today a mask's paintable image is its **source**, at the bottom of the stack: every adjustment, fill and filter above it changes the painted pixels, so painting cannot correct the result of the stack. The roadmap had only "paint layers" for channels and the note that painting a fill's image is not wired. The design:
- **A paint item is a Fill with a transparent painted image.** *Add Paint* in the mask's Add Adjustment menu makes a fill whose source is a new blank image with an **alpha** channel (float, size from the redo panel). The fill's factor becomes `Opacity * image alpha` (a switch on image sources of fills), so unpainted texels are transparent and change nothing, and a painted texel blends the painted value onto the value built so far by the fill's blend mode. With Normal blend, painting white reveals and black hides, and the brush's soft edge is the alpha; Add and Subtract paint relative to what is below. Erasing is Blender's own Erase Alpha brush mode.
- **Where it sits decides what it affects.** Items apply bottom to top, so a paint item above a Levels sees the leveled value, and an adjustment above the paint item treats the paint like everything else below it. Several paint items are fine.
- **Painting it** reuses Paint Mask: a Paint button on the selected paint item makes its image node the active image and enters Texture Paint in Material mode. The viewport shows the whole stack while painting.
- **Same machinery for channels** (the P1 "paint layer"): an image with alpha blended onto a layer channel by the channel's blend mode; the painting target is chosen per channel, which is Blender's native limit of one image per stroke.
- **Built as designed**, with one difference: *Add Paint* makes the fill and applies twice, because a fill only gets its image node on the second pass. The ALPHA node is a second Mix node (`lerp(below, blend, alpha)`) in front of the Opacity mix, so the Opacity slider stays what it was. The row icon is a brush, the panel has the image picker, Paint and Exit buttons and no source dropdown.
- **Soft brush edges (premultiplied alpha).** Reported from the GUI: a white line round the edge of a black stroke. Blender's brush mixes the color with what was there by the stroke's alpha alone (`dst * (1 - a) + color * a`, premultiplied maths) whatever the image is declared as, so where a texel is only partly covered it keeps `color * alpha`, and the old color (white, on the first version) leaks into the edge: black at alpha 0.3 over a white start read 0.91 where 0.70 was meant. Image Texture's own premultiplied mode did not undo it in the Cycles test. So the paint image is **transparent black, kept raw** (alpha mode Channel Packed: no conversion by Blender in either direction) and the paint item divides the stored color by the stored alpha itself (the `UNPREM` Math node, a division by zero is 0 and a texel with no alpha has no effect) before the blend and the alpha factor. Checked against the brush's mix maths for black, white and grey strokes at alphas 0.1 to 1, over three different masks, and for two strokes on top of each other (they read as one after the other); the real brush could not be run headless, so that part is a GUI check. Paint images from the first version are corrected by a rebuild; their old strokes keep the white leak. An image the user picked is never changed.
- **Saving what is painted.** Found while building it: `Image.pack()` does nothing on a freshly generated image, so the blank images Essence made for masks were never really packed, and a save does not pack a painted buffer again (tested: a pixel painted on a packed image was gone after a save and a reload). New mask and paint images are now made with real pixels and packed (`nodes.new_paint_image`), and `persist.py` packs the painted images of masks and paint items again in a save handler (it runs when the user saves, nothing else), which also rescues the generated blanks of masks made by older versions.
- **Costs and limits.** One more image sample in the mask per paint item. A baked filter above a paint item needs Apply again after painting (staleness does not see painted pixels, as with any painted mask under a filter).

### B10. Export

| Feature | Status | Pri |
|---|---|---|
| Unity HDRP (BaseMap, MaskMap, Normal) | ✅ | |
| **Export preset engine**: a preset is data: a list of files, each with a name pattern, channel sources per R/G/B/A, color space, bit depth, invert flags | ⬜ | **P1** |
| Presets: Unity URP (RGB metallic + A smoothness), Unreal (ORM), glTF (ORM + emissive + normal), generic PBR metal/rough, spec/gloss, Godot, Blender (round trip) | ⬜ | P1 (URP, Unreal, glTF), P3 (rest) |
| Export all channels (emission, opacity, height, AO, specular, coat, user channels) | ⬜ | P1 (follows B1) |
| Bit depth (8 / 16 / 32) and formats (PNG, TGA, EXR, TIFF, JPEG) | 🟡 PNG/TGA/EXR 8-bit | P1 |
| Dilation (padding) on export | ⬜ | P1 |
| Export bakes AO itself | ⬜ | P2 |
| Export all materials / all objects / selection | ⬜ | P1 |
| UDIM export | ⬜ | P2 |
| Re-export on save, "live link" to the engine folder | Save handler | ⬜ | P3 |
| Export directly through Blender's glTF exporter with the material wired for it | Native exporter | ⬜ | P2 |
| Export a Blender-native packed material (flatten the whole stack to a simple image-based Principled material) | Bake + node build | ⬜ | **P1** |

### B11. Layer stack UI

All Blender UIList and panel features only.

| Feature | Status | Pri |
|---|---|---|
| Stack list with indent, masks, folders, thumbnails | ✅ | |
| Per-row eye toggle (masks) | ✅ | |
| **Per-row eye toggle for layers and folders** | ✅ | |
| **Blend mode and opacity visible on the active layer** | 🟡 opacity shown, blend mode not built yet | **P0** (with blend modes) |
| Duplicate, copy/paste across materials | ⬜ | P1 |
| Drag to reorder | ⬜ (UIList has no drag reorder in the API; arrows only) | P4 |
| Multi-select | ⬜ (native UIList has none) | 🚫 |
| Search/filter by name | native UIList filter | ⬜ | P3 |
| Rename in place | ✅ (list name field) | |
| Layer color tags | ⬜ | P4 |
| Add menu grouping (Fill, Paint, Adjustment, Generator, Folder, Smart Material) | ⬜ | P1 |
| Context help text / tooltips on every property | partial | P3 |

### B12. Performance and scalability

Blender evaluates the whole material per pixel, so very deep stacks cost shader compile time (EEVEE) and per-sample cost (Cycles). Essence declares this up front.

| Feature | Blender infra | Status | Pri |
|---|---|---|---|
| **Measure** compile time and viewport cost of a 5, 20, 50-layer stack (decide guidance) | EEVEE and Cycles timing, `tests/perf_stack.py` | ✅ see "Performance guidance" below | |
| Only generate enabled channels (smaller groups) | Per-material channel list | ⬜ | P0 / P1 |
| **Freeze layer / folder / whole stack to image** (bake the result of a layer or folder to images, swap in an Image Texture, keep the live version for unfreezing). The main remedy for procedural-heavy stacks, since procedurals are evaluated live per shading point (see B6, Procedural cost). Deliberately later: not needed until stacks get heavy. | Emission bake (as export already does) | ⬜ | **P1** (late in the tier) **Status:** a first version (bake the stack up to a layer or folder, frozen entries leave the chain) was built and removed again after its first GUI test went wrong; it is in git history (commit 8702a97). The underlying problem is real: adding a layer to a 14-layer stack took about 7 seconds to recompile, because Blender recompiles the whole material on any structural change and the Bump node evaluates its height network three times. Options still open: freeze (retry), a pause-live-updates toggle, a lighter per-layer shader. |
| Draft mode: lower texture resolution, disable heavy generators | Image scale + group toggle | ⬜ | P3 |
| Disabled layers do not compile (muted nodes, `mute`) | Node mute | ⬜ | P1 |
| Constant-folding check (are disabled-channel Math nodes with constant inputs free?) | Verify in EEVEE/Cycles | ✅ yes in EEVEE, see below | |
| Group version migration, rebuild | ✅ | |
| Python-free runtime: the generated material works without the addon installed | Principle 2 | ✅ | |

### B13. Project, texture sets and data

| Feature | Blender infra | Status | Pri |
|---|---|---|---|
| Project = `.blend`, autosave, undo | Native | native | |
| Texture set = a material; per-set resolution, channel list, export preset | `Material.essence` | 🟡 stack only | P1 |
| Multiple materials / objects, switching sets | Active material | ✅ | |
| UDIM texture sets | Native | ⬜ | P2 |
| Link/append a layer stack or material from another `.blend` | Native Append plus rebuild | ⬜ | P2 |
| Color management per image (sRGB / Non-Color / ACEScg) | Image colorspace | ✅ partly | P2 |

---

## Out of scope

- **Substance Designer graph authoring or `.sbsar` import.** Blender node groups and the asset library cover this; no own graph engine.
- **Own brush engine, own painting viewport, own modal tools.** Native Texture Paint only.
- **Own renderer / Iray.** Cycles and EEVEE.
- **Own project file, library format or undo stack.** `.blend`, assets, global undo.
- **Multi-select and drag-and-drop in the layer list** beyond what the UIList API offers.
- **Per-frame Python image processing.** All neighbour-based filtering is an explicit baked step.

---

## Master Roadmap

Ordered from essential to trivial. Within a tier, work top to bottom. Status in brackets.

### P0 Essential
1. **Validate v0.1 in Blender**: run through the "Verify" list; fix what breaks. Nothing below is safe until this passes. ⬜
2. **Layer sources per channel**: image texture and procedural/node-group picker for each channel's B input, with colorspace set per channel. ✅
3. **Projection and mapping**: UV and triplanar, tiling/offset/rotation as layer inputs. ✅
4. **Layer opacity and per-channel opacity** next to the enable checkbox. ✅
5. **Blend mode per channel** (dropdown per channel, one group variant per combination, see B3). ✅
6. **Channels beyond four**: Alpha, Emission and the channel list per material. ✅ (Normal became the Normal mesh map slot instead of a channel.)
7. **Per-row eye toggle for layers and folders**; blend mode and opacity shown on the active layer. ✅
8. **Cage support and float image buffers** for baking and for height/mask images. ✅
9. **Measure performance** of deep stacks and set guidance. ✅ (see Performance guidance)

### P1 Core (the "full workflow" line)
10. **Adjustment layers and filter stack**: levels, HSV, color balance, curves, invert, gradient map, threshold, channel mix. ⬜
11. **Mask generators and asset library**: noise, gradient, grunge, edge wear, dirt, curvature wear, height gradient, position gradient, as node group assets in catalogs. ⬜
12. **More baked maps**: Curvature, Position, Thickness, Material/Object/Color ID, Height from mesh. ⬜
13. **ID and Color Selection masks.** ⬜
14. **Nested folders; Passthrough folder mode; folder opacity.** ⬜
15. **Smart materials**: folder presets as assets, drop onto an object. ⬜
16. **Paint layers** (direct per-channel painting). ⬜
17. **Viewport channel/mask/layer solo.** ⬜
18. **Export preset engine** with URP, Unreal, glTF, all channels, bit depth, dilation, export all materials. ⬜
19. **Export as a flattened Blender material** (stack baked into a simple image-based Principled material). ⬜
20. **Remaining blend modes** (contrast, dodge/burn, compare). ⬜
21. **Freeze to image** for layers, folders and the whole stack (performance step for procedural-heavy stacks; do late in P1, once real stacks show the need). ⬜
22. **Baking groups; multi-object bake into one image.** ⬜
23. **Mask stack blend modes; levels/curves on masks; layer duplicate/copy/paste; Add menu grouping.** ⬜
24. **Specular, AO channels; texture set settings (resolution, channels, export preset).** ⬜

### P2 Important
25. Compositor-based baked filters (blur, sharpen, edge, dilate/erode, slope blur). ⬜
26. Decals and Empty-driven projection; spherical/cylindrical/planar. ⬜
27. Height blending; anchor points. ⬜
28. Coat, sheen, anisotropy, subsurface, transmission channels; true displacement; user channels. ⬜
29. Vertex group/attribute and polygon-selection masks; image mask channel select. ⬜
30. UDIM bake, paint and export. ⬜
31. Automatic paint entry on mask selection; paint masking by face selection. ⬜
32. Match-by-name bake pairing; AA supersampling; world-direction and diffuse bake; export bakes AO itself. ⬜
33. Vector warp and randomization generators; layer presets; per-folder collapse. ⬜
34. Glossiness/spec workflows; export via Blender's glTF exporter; append stacks from other files; ACES color spaces. ⬜

### P3 Nice to have
35. Tile generator, splatter, shape, weave approximations. ⬜
36. Bent normals; opacity bake; UV island masks. ⬜
37. Layer instances; mask copy/paste; resample masks. ⬜
38. Draft mode; bake presets; re-export on save (live link); list search; per-folder channel enable; arbitrary folder blend modes. ⬜
39. Glossiness export for other engines. ⬜

### P4 Trivial
40. Layer lock; layer color tags; drag reorder if the API allows. ⬜
41. Stale bake warning; view transform hint. ⬜

---

## Open Questions

1. **Painting entry**: currently an explicit Paint Mask / Exit button. Automatic entry on selecting a mask is not built.
2. **Painting plumbing**: built as active image node plus Material paint mode; not validated. The mask-solo toggle is not built.
3. **Sidebar details**: how the panels should be refined as the feature count grows. Likely: collapsible subpanels per area (Sources, Filters, Masks), Layers panel stays on top.
4. **Baking groups** and the final shape of the bake section.
5. ~~**Blend mode wiring**~~ Decided: layer group variants (see B3, "Group variants").
6. **Layer sources UI**: how the picker for image/procedural per channel stays compact (one popover per channel, or a "Sources" subpanel with a row per channel).
7. ~~**Channel list vs fixed layer group interface**~~ Decided: the channel set is part of the same group variant key, so a material only has the sockets of its channels.
8. ~~**Compositor use for filters**~~ Answered (verified headless, `tests/test_compositor.py`): a filter runs in a **throwaway scene** (`bpy.data.scenes.new`) with a compositor node group (`scene.compositing_node_group`): Image node, filter nodes, Group Output. Rendered with the Workbench engine at the image's size with `write_still` to a temporary EXR, which is loaded and packed (the Render Result image has no pixels in background mode). The user's own scene and compositor are never touched, and the temp scene, group and folder are removed. Tested with the Kuwahara node: noise in flat areas drops about 20x and a hard edge stays hard. About 1 to 2 s per run, mostly engine start-up.
9. **Texture set model** for objects sharing a material with different UVs (see the B8 limitation).
10. **Mesh maps vs live generators**: which generators have a live (Cycles-free) form and which need a baked mesh map. Decide per generator.

## Verify

**Verified headless (Blender 5.2.2, `tests/run_all.py`, Sprint 0):**
- Node group interface calls, Mix socket identifiers, Bool sockets converting to float, Brightness/Contrast identifiers (and that brightness brightens, negative darkens).
- Layer, folder and mask operators (add, move across folder borders, remove, ungroup), list stays in canonical order, save/reload round trip.
- Blend math against real baked pixel values: mask lerp, bump add, mask invert, mask fill multiply, disabled mask = white, channel enable leak, folder lerp (incl. bump).
- Group version upgrade keeps socket values.
- Bake: AO, World Gradient (cube and zero-height plane), Normal (world, tangent), settings restored afterwards, tangent bake wires Normal Map into Bump into BSDF, high-to-low normal bake with a High Poly collection (tilted plane leaves the expected mark, high-poly is not a target).
- Export (Unity HDRP, EXR): BaseMap, MaskMap (R metallic, G white without AO, B white, A smoothness) and Normal values, temp images and nodes cleaned up, and the exported normal equals the baked normal when layer height is zero.

- Cage: a high-poly 0.5 above a low-poly is missed with the automatic extrusion and captured (correct tilted world normal) once a cage plane sits above it; bake settings are restored. Float buffers: bakes and new paint masks are float, switching the Float toggle keeps the image name and its users.

- Anti-aliasing: a high-poly edge falling mid-pixel is hard at Off and gets intermediate values at 4x, the image keeps its final size and float type, and a size times factor over 8192 px is refused before anything is created.
- Re-baking at a different size replaces the image (found in the GUI: `Image.scale` is silently ignored on a packed image, so a re-bake kept the old resolution). The bake now makes a fresh image and moves the users over.
- AO overlay: Base Color and Specular IOR Level follow `lerp(1, AO, Strength)` at strengths 1, 0.5 and 0 (real baked pixels), rebuild is idempotent, an empty slot hands Base Color back to the stack, an AO bake wires itself, and the Unity export is unaffected.
- Adjustments: invert, gamma, levels (input and output range), saturation, brightness, identity curves, gradient map and threshold against baked pixels, on color and float channels, on a Value, an Image source and a mask; Opacity, the eye, chain order and moving an item, switching an item's type, hand edits surviving a rebuild, removal of owners and items, and every panel and list row drawing for every type.
- Mask fills: Multiply, Add, Subtract, Darken and Lighten against baked pixels on a Value mask, Opacity, the eye, image and procedural fill sources, items applying in order (an Invert before or after a fill), removal of a fill restoring the mask, a fill only on masks, and the panel and list rows drawing for every source kind.
- Baked filters: an unbaked filter passes the value through, a refreshed Blur lowers the mask near a hard edge, every filter type refreshes, the result is one packed float image at the chosen size, refreshing again replaces it, removing the item deletes its image and compositor group, and a refresh leaves render settings, selection, scenes, the user's compositor and the material graph exactly as they were.
- Filter staleness: a fresh result is current, layout changes and renames are ignored, value changes below, switching an item below and the filter's own settings make it stale, items above do not, stacked filters go stale together and are applied in order, and the Apply button and the row show a red or green dot.

**Still to confirm in the GUI (milestone checklist):**
- `template_ID_preview(..., open="image.open")`, and the layer-row thumbnail via `preview_ensure`.
- Panels, bake panel flow, Texture Paint entry and exit, normal orientation judged by eye (no flipped green) on a real high-poly.
- Emission-baked colors matching the viewport (sRGB conversion on the byte-image bake) and straight-alpha MaskMap saving in PNG/TGA (EXR verified).

Researched but not confirmed against the Blender 5.2 documentation:
- Exact current inputs of the Bump node in 5.2 (Strength, Distance, Height, Normal, Invert, Filter Width if it still exists).
- That a Bump node evaluates its Height input multiple times per shading point in both Cycles and EEVEE (basis for "one Bump node at the end"). Test by comparing render time of a per-layer Bump chain versus one Bump node.
- Whether a UIList supports drag reordering in 5.2, and what the asset shelf API allows an addon to register (for the library and smart materials).
- Compositor access from an addon in 5.2 (Open Question 8), UDIM bake support, and whether constant-input Math/Mix nodes are folded away when compiling the GPU shader (disabled channels free or not).

## Performance guidance

Measured with `tests/perf_stack.py` (Blender 5.2.2, Windows, Cycles CPU and EEVEE headless, 1 plane). "Procedural" layers have Noise sources on Color, Bump and Roughness plus a Noise mask; "plain" layers only hold values. Times are seconds.

| Stack | Cycles render (512 px, 8 spp) | EEVEE shader compile, first render | EEVEE compile after adding one layer |
|---|---|---|---|
| 5 plain / 20 plain / 50 plain | 0.15 / 0.15 / 0.16 | 2.9 / ~0.3 / ~0.4 (constant values fold away) | ~0.3 |
| 5 procedural | 0.3 | about 3 | about 2.5 |
| 20 procedural | 0.32 | about 19 | about 8 |
| 50 procedural | 0.41 | 73 | 24 |

- **Cycles on CPU is not the problem.** Render time barely grows with depth (SVM has no compile step). The cost is EEVEE's GPU shader compile, which grows steeply with the number of procedural nodes and recompiles the whole material on every structural change (adding, removing or reordering a layer, switching a source).
- **Practical limit:** about 10 procedural layers stays comfortable (a few seconds per edit); 20 is tolerable; 50 is not (24 s per edit). Plain value layers are almost free. Guidance for users: keep procedural sources to the layers that need them, and use image sources for the rest. Freeze to image and a pause-live-updates toggle remain the planned remedies.
- **Constant folding works for disabled content.** With all channel Enables switched off, or a layer's Visible off, 20 procedural layers compile in 2 to 3 s (versus about 19 s enabled), so switched-off layers and channels are effectively free in EEVEE. No node muting is needed for that. (EEVEE timings are only comparable within one run: Blender caches compiled shaders between runs.)
- **Stack build cost in Python (improved).** Every operator rebuilds the whole stack, and the rebuild used to relink every socket and scan the whole tree per layer (`Socket.links` is itself a scan of all links), so building was roughly cubic. A rebuild now skips links that are already right, indexes links and source nodes once, and only moves nodes that are not in place. Adding layers one at a time through the operators: 50 procedural layers with a mask each went from 242 s to 29 s (20 layers: 14 s to 2.5 s); 50 plain layers from 10.7 s to 0.9 s. Still superlinear for large procedural stacks (source nodes are rebuilt per change); fine up to about 20 layers.
- The per-layer Bump node cost (the Bump node evaluating its height network several times) was not isolated separately.

## Sources
- [Artistant addon](https://github.com/Cedricvdk/Artistant) (bake pipeline, thumbnails and bake panel layout that Essence's baking is ported from)
- Unity HDRP Mask Map channel layout (R metallic, G occlusion, B detail mask, A smoothness)
- [Substance 3D Painter: Height map painting](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/painting/advanced-channel-painting/height-map-painting) (height defaults to Linear Dodge (Add), HDR range -1 to 1, mid grey is zero)
- [Substance 3D Painter: Blending modes](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/blending-modes) (Linear Dodge for accumulating height, Normal Map Combine / Detail / Inverse Detail)
- [Substance 3D Painter: Normal map painting](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/painting/advanced-channel-painting/normal-map-painting)
- [Blender commit adding the Normal input to the Bump node](https://projects.blender.org/archive/blender-archive/commit/3e763d7e4dbbc4acb3deb7afb95e936ce950ebb5) (normal mapping has to come before bump)
- [Blender addon UI scripting guide](https://blog.cg-wire.com/blender-addon-ui-scripting-guide/) (panel space and region types)
- [DevTalk: addon panels and the Tool tab](https://devtalk.blender.org/t/add-on-panel-missing-from-active-tool-workspace-settings/7349)
- [Blender 5.2 manual: Bump node](https://docs.blender.org/manual/en/5.2/render/shader_nodes/vector/bump.html)
- Substance 3D Painter feature set (layer stack, channels, baking, filters, smart materials, export presets), from the Adobe documentation and general knowledge. The Substance documentation was not re-fetched while writing this revision, so feature coverage should be re-checked against it before the roadmap is frozen.
