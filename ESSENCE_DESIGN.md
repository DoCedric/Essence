# Essence

A Blender addon that brings a Substance Painter-style texturing workflow into Blender, built entirely on top of Blender's own shader graph.

> Status: v0.1 in development. This document lists what is decided and what is built. Anything uncertain lives under "Open Questions" or "Verify". Items marked *(built, untested)* are implemented but have not yet been validated in Blender.

**Target:** Blender 5.2.2 (latest). No support for older versions.

---

## Design Philosophy

### 1. The shader graph is the source of truth
Essence does not use a proprietary layer system hidden behind its own data model. Layers, masks and channel setups are expressed as ordinary Blender shader nodes. The addon's UI is a convenient front end over the node tree, not a replacement for it.

### 2. Deconstructable by anyone who knows Blender
A user with Blender knowledge must be able to open the shader editor, look at what Essence generated, understand it, and modify it by hand. Nothing the addon builds should be a black box.

### 3. Layers are a workflow concept built from nodes
We keep the familiar layer/mask mental model from Substance Painter, but "layers" are not traditional stacked raster layers. They are node groups/structures in the shader graph that influence PBR channels.

### 4. Per-channel control
Layers and masks can influence different channels of the PBR setup independently, rather than every layer affecting everything at once.

### 5. Channels are blended, not shaders
Layers blend PBR channel values (color, roughness, metallic, bump) and feed a single shader. We avoid Add Shader / Mix Shader for layering because of the expected performance cost.

### 6. Native Blender UI only
Essence uses Blender's own panels, lists and properties. We do not build a custom UI layer on top of Blender (no custom overlays, drawn widgets or modal interface in the style of RetopoFlow). If Blender's UI system limits what we can do, we work within the limit.

### 7. Build for scalability
v1 is deliberately small (four channels, one blend method per channel), but nothing in the code or the generated node structure should assume that stays true. Concretely:
- Channels are defined as data (name, socket type, blend method, default value), not hard-coded in the node-building logic. Adding a channel (emission, normal map, AO, ...) should mean adding a definition, not rewriting the layer builder.
- Blend method is a per-channel property of a layer, even though v1 only exposes the default one. Letting the user pick a blend mode later must not require restructuring layers.
- Generated node groups carry a version, so layers made by an older Essence can be detected and upgraded.
- The layer node has a stable, documented set of inputs and outputs so that added channels extend it rather than replace it.

---

## Features

### Baking
Ported from the Artistant addon's bake pipeline (Cycles; selected objects; one image per object named `<object>_<Map>`, reused and overwritten on re-bake so references such as mask slots stay valid; images are packed; a dedicated unconnected image node `Essence_Bake_<MAP>` holds the target in the material). All bake settings Essence touches are saved and restored.

Map types *(built, untested)*:
- **Ambient Occlusion**: native Cycles AO pass.
- **World Gradient**: black to white along world Z, spanning the combined world bounding box of the selected low-poly objects. Baked through a temporary Emission override of the surface.
- **Normal (Tangent)**: native Cycles normal pass, tangent space, OpenGL (+Y). Flat on a single object. After a bake it is wired into the shader (see Channel Blending > Normal map).
- **Normal (World)**: world-space mesh normal remapped to 0-1, baked through the Emission override. Ignores the material's bump.

**Bake on high** (single object, no high-poly) is the default when no High Poly collection is set.

**Bake from high to low** *(built, untested)*: set a **High Poly** collection (children included, meshes only) and bake the selected low-poly objects. Works for every map type.
- Selected-to-active, no cage. Ray start offset (Extrusion) defaults to 2% of the low-poly's bounding-box diagonal, overridable; max ray distance is twice the extrusion.
- High-poly objects are never bake targets, even if selected. Hidden or unselectable high-poly objects are made visible for the bake and restored afterwards; a high-poly excluded or hidden by its collection is a clear error.
- For emission-based maps Cycles reads the shader from the high-poly, so the temporary Emission override is applied to the high-poly materials too (a temporary material if one has none).

**Baking groups**: not started. Manage sets of objects and bake settings as groups.

### Layers and Masks
- Create layers that influence different channels of the PBR shader.
- Create masks that control where those layers apply.
- All layers and masks are built from shader graph nodes (see Design Philosophy).
- A mask is an **image slot** *(built, untested)*: either a new blank paint image (white reveals, black hides; size set in the redo panel) or any image already in the file, picked from Blender's thumbnail grid (`template_ID_preview`, with Open and unlink). The two are interchangeable at any time.
- Masks should be Non-Color. If a picked image is not, the panel warns and offers a one-click Set Non-Color instead of changing the image silently (it may be used elsewhere).
- Mask adjustments *(built, untested)*: **Invert**, **Brightness** and **Contrast** (same ranges as Blender's Brightness/Contrast node, -100 to 100), per mask. Order inside the mask node: invert, then brightness/contrast, then clamp to 0-1, so Brightness always brightens the mask the user sees.
- **Mask stack** *(built, untested)*: a layer or folder can have any number of masks, stacked on top of each other. Each mask is a row in the layer list, indented under its owner and shown with its thumbnail, like Substance Painter. Masks multiply together (the effect shows only where every enabled mask is white). Each mask row has an eye toggle that disables it (a disabled mask counts as white), and masks reorder with the up/down arrows within their owner. More blend modes between masks (add, subtract, ...) are a future extension of the mask node.
- **Selection decides the panel.** Selecting a layer or folder shows its properties (channel values and enables) and an Add Mask menu (white, black or image mask). Selecting a mask shows only the mask properties (image, invert, brightness, contrast, paint, remove). A layer never shows both at once.
- Masks can be painted in Blender's Texture Paint mode. A Paint Mask button makes the selected mask's image the active image node and enters Texture Paint in Material mode; an Exit button leaves it. Painting on a picked image edits that image.

### PBR Channels (v1)
- Color
- Roughness
- Metallic
- Bump (height)

### Layer Structure
- One **layer node** (a node group) per layer, chained: each layer's output feeds the next layer's A inputs, and the layer's own channel values are the B inputs.
- Inputs per layer: A and B for Color, Roughness, Metallic and Bump, plus a Mask (the already combined result of the layer's mask stack) and the settings below.
- **Per-channel enable** *(built, untested)*: each channel has an Enable checkbox input. The effective blend factor for a channel is `mask * enable`, so a disabled channel passes the layers below straight through ("leaks"), like Substance Painter. A layer can therefore affect only roughness, only color, and so on.
- **Mask nodes** *(built, untested)*: each mask is one instance of the shared "Essence Mask" node group (version 1). Inputs: Value (the mask image), Previous (the masks before it in the stack), Mask Invert, Mask Brightness, Mask Contrast, Enable. Output: Mask = adjusted value (or 1 when disabled) * Previous. The image node feeds Value and the last mask node of the stack feeds the layer's Mask input, so the whole stack is a visible chain of nodes below the layer.
- Outputs per layer: Color, Roughness, Metallic and Bump (height). Each layer node therefore has a Bump output, and the end of the stack has a single Bump node (see Channel Blending).
- The shared "Essence Layer" node group carries a version (currently 4: 2 added the Enable inputs, 3 the mask adjustments, 4 moved the mask adjustments to the mask nodes). Outdated groups upgrade on Rebuild or Add Layer, and the upgrade preserves each layer's input values by socket name. Layers made before mask stacks existed (one mask stored on the layer) are converted to a mask entry first, so their adjustments are kept.
- Blender node groups have no variable number of inputs, so a stack of N layers is N group instances in a chain. This is accepted.

### Folders *(built, untested)*
A folder holds layers and has its own mask stack, which limits the combined effect of everything inside (like a Substance Painter folder). One level only: folders do not nest.
- **Node structure:** the layers inside a folder chain as normal, starting from the stack below the folder. A **folder node** (shared node group "Essence Folder", version 1) then lerps every channel between the stack below (A) and the end of the inner chain (B) by the folder mask: `A + (B - A) * mask`. Bump included, since the inner chain's bump is already `A + sum of inner heights`, so a folder mask of 0 leaves the height below untouched. The folder node's outputs continue the chain like a layer's. An empty folder passes the stack through.
- **Folder node inputs:** A and B per channel, Mask. No per-channel Enable (decided for v1; can be added later). The folder's masks are mask nodes exactly like a layer's.
- **Data:** the layer list stays flat and bottom-to-top. Every entry has a `uid`, a `kind` (layer, folder or mask) and a `parent` (the owner's uid). Everything an entry owns sits directly below it in the list, so with the list drawn reversed an owner is followed by its masks and, for a folder, its layers. All reordering goes through the tree helpers in `stack.py`.
- **UI:** children are indented under the folder and masks under their owner. Add Folder creates an empty folder above the active entry. Add Layer with a folder active adds into it. The up/down arrows move a layer within its folder and across folder borders (up into a folder joins as its bottom child, down into one as its top child, past the end leaves it); a folder moves as a block and masks reorder within their owner. Removing a folder ungroups its layers; removing a layer or folder removes its masks.

### Layer Properties
- The layer's Bump value is a slider from -1 to 1: positive raises the surface, negative carves into it, 0 has no effect. Height is therefore signed.
- For height textures the same range maps to black = -1, mid grey = 0, white = 1 (as in Substance Painter). Height images use the Non-Color color space, and a new height image starts as mid grey (a flat surface).

### User Interface
- All Essence UI lives in the sidebar (N panel), using standard Blender panels, UI lists and properties.
- It aims to be as close to Substance Painter's workflow as that allows.
- Panel order: Layers (stack list with masks, add layer/folder/mask, remove, move, rebuild), then either Layer Properties (value and enable checkbox per channel, Add Mask) or Mask Properties (image picker, invert/brightness/contrast, paint), depending on what is selected, then Material (Normal Map Strength, Bump Strength and Distance), Bake, Export.
- Available in both the 3D Viewport sidebar and the UV/Image Editor sidebar.
- No custom workspace, no custom drawn UI (see Design Philosophy). A left-hand panel layout is not pursued for now because addons may not be able to place panels there; it can be revisited later if Blender allows it.

### Painting
- Painting is only done on masks. Layer channel values are set through the properties panel, not painted.
- Selecting a mask and pressing paint enters Texture Paint mode targeting that mask. Whether this happens through a button or automatically on selection depends on how the UI ends up.
- While painting, the viewport shows the final material, not the mask alone.
- A toggle to view the mask itself can be added.

### Channel Blending (v1)

Each layer blends its channels onto the result of the layers below it, with the layer's mask as the factor. A is the stack below, B is this layer.

| Channel | Data | Blend in v1 | Per-layer formula |
|---|---|---|---|
| Color | color | Mix (lerp) | `A + (B - A) * mask` |
| Roughness | float | Mix (lerp) | `A + (B - A) * mask` |
| Metallic | float | Mix (lerp) | `A + (B - A) * mask` |
| Bump | float height | Add | `A + B * mask` |

Notes:
- **Color, roughness, metallic: Mix.** Where the mask is 1 the layer replaces what is below, where it is 0 the layer has no effect, and values in between blend. This is the straightforward "layer with a mask" behavior. Use the Mix node (Color type for color, Float type for roughness and metallic) with the mask as Factor.
- **Masks are data, not color.** Mask images use the Non-Color color space. Only color-carrying images (base color textures) use sRGB.
- **Bump: Add, not Mix.** Bump is treated as a height value that accumulates across layers, not as something a layer replaces. Substance Painter does the same: its height channel defaults to the Linear Dodge (Add) blend mode, and height is stored as signed values where mid grey is zero, so a layer can raise or lower the surface. A mask of 0 therefore leaves the height untouched, which is what we want. Mix would instead pull the height toward the layer's value and fight with the layers below.
- **Bump value range.** A layer's Bump slider runs from -1 to 1 (negative carves, positive raises).
- **Bump scale is a separate multiplier.** A layer's height contribution is `B * mask` and can be scaled per layer (`B * strength * mask`) before it is added. This can be expressed with a Math node (Multiply Add) or with the Mix node's Add mode as in the current prototype; both give the same result. Overall height intensity is controlled once, at the end of the stack.
- **One Bump node, at the end of the stack.** All layers' heights are summed into a single height value, which feeds one Bump node (Strength, Distance), whose Normal output goes to the shader's Normal input. We do not put a Bump node in every layer. Chained Bump nodes would each re-evaluate the height network feeding them and compound with each other, so a single node after accumulation is both more correct and cheaper. See "Verify".
- The Mix node's blend-mode options (Add, Multiply, ...) exist only for the Color type, which is why the prototype's bump channel uses a Color-type Add. It works, but a Math node is cleaner for a float channel.

### Normal map *(built, untested)*
A baked tangent-space normal is wired in as: **baked image, Normal Map node (Tangent), Bump node's Normal input, BSDF Normal**. The normal map is evaluated before the bump, as Blender requires, and layer height still feeds the Bump node's Height, so both combine in the single Bump node. A **Normal Map Strength** slider (the Normal Map node's Strength) sits in the Material panel next to the bump settings.

While a Normal (Tangent) bake runs, the BSDF's Normal input is temporarily unlinked, so the bake does not capture the material's own bump or normal map (which would then be applied twice). A low-poly-only tangent bake is therefore flat.

### Export *(built, untested)*
v1 exports for **Unity HDRP** only, from the active object's active material (needs at least one layer and a UV map). Settings: Format (PNG, TGA, EXR), Resolution (512 to 4096), Folder (default `//Export/`).

| File | Content |
|---|---|
| `<Material>_BaseMap` | Top layer's color. sRGB (Non-Color for EXR). |
| `<Material>_MaskMap` | R Metallic, G Ambient Occlusion, B Detail Mask (white), A Smoothness (`1 - roughness`). Non-Color. |
| `<Material>_Normal` | Tangent space, OpenGL (+Y). A real bake of the whole material, so the baked normal map and the layer bump are combined. |

Values are baked from the top layer node's outputs with the Emission trick, so they are exact and unlit. AO comes from an existing `<object>_AO` bake (scaled to the export resolution) and is white if none exists; export does not bake AO itself. Temporary images and nodes are removed afterwards.

### Future (noted, not committed)
- Other export targets (Unity URP: RGB metallic plus alpha smoothness; Unreal, glTF).
- Bake: Mesh ID and Object Gradient (Artistant has them), a cage object, baking AO from the export action.
- Image mask channel selection (R/G/B/Alpha) and levels-style remapping.
- Choosing the blend mode per layer per channel (Multiply, Overlay, Add, Subtract, ...). Covered by the scalability principle above.
- Normal map channel for baked or imported normals. When added, Blender's rule applies: a Normal Map node has to be evaluated before the Bump node and fed into the Bump node's Normal input, because normal mapping is tied to the mesh's tangents and the bump is applied on top of it. Substance Painter offers Normal Map Combine (Whiteout), Detail (Reoriented Normal Mapping) and Inverse Detail for blending normals; those would be the reference.

---

## Open Questions

1. **Painting entry**: currently an explicit Paint Mask / Exit Paint Mode button on the selected mask. Automatic entry on selecting a mask is not built.
2. **Painting plumbing**: built as active image node plus Material paint mode (see Painting); not yet validated. The toggle to view the mask on its own is not built.
3. **Sidebar details**: layer list, properties, mask, material, bake and export panels exist in both sidebars; how they should be refined (and how both sidebars share state, which they do through Material and Scene properties) is still open.
4. **Baking groups** and the final shape of the bake section.

## Verify

Built but never run in Blender (no Blender was available while writing). To confirm on first run:
- Node group interface calls (`interface.new_socket`, `min_value`, `subtype`) and Mix node socket identifiers in 5.2; Bool sockets converting implicitly to float; the Brightness/Contrast identifiers (`Color`, `Bright`, `Contrast`).
- `template_ID_preview(..., open="image.open")`, and the layer-row thumbnail via `preview_ensure`.
- Selected-to-active with the High Poly collection, default extrusion quality, and normal orientation (no flipped green).
- Emission-baked colors matching the viewport (sRGB conversion on the byte-image bake), straight-alpha MaskMap saving, and an unconnected-Height Bump node leaving the baked normal unchanged.

Items researched but not confirmed against the Blender 5.2 documentation:
- Exact current inputs of the Bump node in 5.2 (Strength, Distance, Height, Normal, Invert, and Filter Width if it still exists) and whether anything changed from 4.x.
- That a Bump node evaluates its Height input multiple times per shading point in both Cycles and EEVEE, which is the basis for "one Bump node at the end". Test by comparing render time with a per-layer Bump chain versus one Bump node.

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
