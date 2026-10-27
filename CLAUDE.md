# Essence: working agreement

Blender 5.2 addon for Substance Painter-class PBR texturing, built on the shader graph. Read `ESSENCE_DESIGN.md` (what and why, priorities) and `ROADMAP.md` (the sprint queue) before starting work. Design decisions live in the design doc; tasks live in the roadmap.

## Layout
The repo root is the addon (`blender_manifest.toml`, `__init__.py` and one module per area: `channels.py`, `blends.py`, `nodes.py`, `stack.py`, `sources.py`, `adjustments.py`, `filters.py`, `generators.py`, `gizmo.py`, `curvature.py`, `fill.py`, `clipboard.py`, `smart.py`, `preview.py`, `persist.py`, `bundled.py`, `ops.py`, `ui.py`, `props.py`, `bake.py`, `export.py`). `assets/masks` holds the bundled test masks and stays untracked. Tests and harness scripts go in `tests/` (never in the addon modules).

## Autonomy
Implement, test, debug, commit and push to the sprint branch without asking.

**Stop and ask the user when:**
- you hit an open design question (Open Questions in the design doc) or any real fork not already decided there. Do not pick silently; present the options with a recommendation.
- an action is destructive to user data (their `.blend` files, scenes, images) or touches anything outside this repo and scratch files.
- a sprint's acceptance checks need GUI judgement. Give the user a short checklist of exactly what to click and look at, then wait.
- a Blender API behaves differently from the design assumption in a way that changes the design.

## Testing
- **Headless first.** Blender: `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`. Run `blender --background --factory-startup --python tests/<script>.py`. The harness must load the repo as a package (the folder is named `Essence`, imports are relative) and register it, then exit non-zero on failure. Print results as plain pass/fail lines.
- Use scratch `.blend` files and scripts (scratchpad directory or `tests/`); never modify the user's open scene or their `.blend` files. The Blender MCP (`execute_blender_code`, screenshots) is for inspecting the user's live GUI session only when asked, or for checking things headless cannot show.
- Test what the change claims: node links exist and evaluate, values read back from baked/exported pixels, operators leave the data consistent, save/reload round trips, group version upgrades keep values.
- Headless cannot judge panels, thumbnails, Texture Paint mode or EEVEE viewport look. Those go on the user's milestone checklist.
- A status marker in the design doc only changes from "built, untested" to verified after a test actually ran. Report failures with the real output.

## Git
- One branch per sprint: `sprint-N-short-name`, branched from `main`. Never commit to `main` directly. The user merges.
- Commit small and often, imperative one-line messages like the existing history ("Add layer folders with their own mask"). Push the branch after each commit batch.
- Commit attribution line follows the session's system reminder.
- Do not commit `.mcp.json`, `__pycache__`, zips (already ignored), or large generated images from tests.

## Definition of done (per task)
1. Implemented following the design principles (Blender infrastructure first, generated graph stays hand-editable, channels/blends as data, node group versions bumped with migration).
2. A headless test exists and passes, or the task is explicitly marked "needs user GUI check".
3. `ESSENCE_DESIGN.md` status markers updated, and `ROADMAP.md` task ticked.
4. Committed and pushed.

## Code conventions
Match surrounding code: sparse comments that explain why, `ESSENCE_` prefixes for Blender classes and idnames, shared node groups named "Essence ..." with a `essence_version` key, new channels via `channels.py` data. Operators that edit data use REGISTER and UNDO.
