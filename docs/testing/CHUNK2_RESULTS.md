# Chunk 2 results — mathematical near-field SDF renderer (2026-11-14)

Status: accepted by the developer for the Chunk 2 scope after manual testing and a final QA pass.
Open items are tracked in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## What exists

- `apps/web` — Vite + React + TypeScript + three.js r187 (WebGL2). Static build, no secrets in the bundle.
- **AST → GLSL compiler** (`src/sdf/compiler.ts`): every op of `geometry_ops.json` (9 primitives, 6 CSG
  combinators, 8 domain ops, 3 field ops), material tracking through CSG, parameter references mapped to
  a per-instance `vec4`. Only numbers and generated identifiers reach the shader.
- **Ray-march program** (`src/rendering/nearfield/shader.ts`): sphere tracing of analytic terrain
  (`terrain-v1`), water plane and entity instances; distance-proportional hit threshold; tetrahedral
  normals; soft sun shadows; ambient occlusion; analytic height fog; sky; true perspective depth in
  `gl_FragDepth`; six debug views. There is no mesh geometry in the scene other than the two full-screen
  triangles the passes are drawn on, and no hard-coded demo geometry: every surface comes from the
  terrain function or a compiled archetype of the loaded world.
- **Instances are data** (`buffers.ts`): one RGBA32F row per entity + an 8 m XZ acceleration grid;
  footprints are measured from each archetype's own SDF. Changing an entity never recompiles the shader
  (checked in the browser: a parameter preview left the compile timestamp unchanged).
- **Frame graph for the hybrid pipeline** (`src/rendering/hybrid/types.ts`, `src/app/Engine.ts`): pass A
  renders SDF color + depth into a target at the internal resolution; pass B is an opaque quad in the
  shared `THREE.Scene` that writes color and depth to the canvas. Gaussian splat meshes (Chunk 3) join
  the same scene and depth-test against it. `FarFieldRenderer` is declared, not implemented or faked;
  the UI reports "far field: not implemented (Chunk 3)" and "splats 0".
- **World wiring**: live worlds through the companion (5×5 regions around the player, streamed as the
  player moves, failed requests retried) or the bundled offline snapshot (`public/sample-world.json`,
  produced by `scripts/export_sample_world.py` with the service's own code paths).
- **Player**: pointer-lock mouse look, WASD, sprint, jump, fly; walking follows the CPU evaluation of
  the same terrain function.
- **Inspector**: CPU sphere-tracing pick under the crosshair (blocked by terrain); sliders edit an
  entity's geometry parameters as a local, labelled preview stored per entity.
- **Diagnostics overlay (F3)**: FPS, frame-time percentiles, SDF pass GPU time, internal resolution,
  instance / archetype / material counts, grid stats and overflow, shader size and compile time,
  GPU↔CPU terrain error, regions, world and region revision, camera, GPU name.

## Changes to Chunk 1 definitions

1. **Terrain lattice hash (generator v2).** The first GPU↔CPU comparison showed a 150 mm mismatch: the
   float lattice hash amplified 1-ulp rounding differences. `terrain-v1` now uses a 32-bit integer hash
   that is bit-identical in GLSL, Python and TypeScript. Measured mismatch: 0.009 mm (sample),
   0.028 mm (Gemini world), 0.057 mm (steep mountain world).
2. **Placement rules (generator v3, from the QA pass).** Scattered objects keep clear of buildings;
   discrete placements retry nearby when the first spot is too steep or under water; buildings require
   slope ≤ 0.22; house walls continue 2 m below the floor as a foundation and tree trunks 0.8 m below the
   base (archetype version 2).

**Generator versions are per world.** A world stores the version it was created with and generation for
that world always follows that version, so stored regions, newly visited regions and event replay stay
consistent (verified: the existing v2 world on Neon replays to exactly its stored rows after the v3
change). The client renders versions ≥ 2.

### Legacy worlds

Worlds with generator v1 (created before the hash change; two exist on the Neon `dev` branch) have
entity heights from the old terrain. Nothing was deleted or altered. They are hidden from the world list
(the panel says how many) and refused if opened by URL.

Proposed migration, not implemented: a one-off script that, per v1 world, regenerates every materialized
region with the current generator, keeps entity ids, copies each stored entity's `state`,
`geometry_params` and `revision` onto the regenerated entity with the same id, drops entities whose spot
is no longer valid, sets `generator_version`, and records one `system` event. It rewrites authoritative
rows, so it needs explicit approval and a branch backup first; for hackathon test data, regenerating the
world is simpler.

## QA audit (final pass)

Confirmed and fixed:

| Defect | Evidence | Fix |
|--------|----------|-----|
| Buildings floated on slopes | Sample: house corners up to 1.70 m above ground (slope 0.44) | Foundation in the archetype, slope limit 0.22, retries; now lowest corner ≤ 0.85 m below floor, covered by the 2 m foundation |
| Trees inside or touching buildings | Sample: trees 2.5–4.7 m from house centers (house half-diagonal 3.3 m) | Clearance rule in generator v3; nearest tree/rock now ≥ 7.6 m |
| Light rings inside shadows, banding on far slopes | Mountain render | Shadow rays ran out of steps; distance-proportional minimum step, more shadow steps |
| Parameter previews leaked between entities; pinned entity survived a world change | Code review | Previews keyed by entity id; inspector state cleared on world open |
| Failed region request never retried until the player changed region | Code review | Retry every 3 s while an error is outstanding |
| Entities behind a hill could be picked | Code review | Terrain / water occlusion test on the pick ray |
| Zero plane normal would produce NaN in the shader | Code review | Compiler rejects it |
| Legacy world could be opened by URL and rendered misaligned | Code review | Engine refuses generator versions < 2 |

Checked and found correct: negative coordinates and region borders in the grid (unit test registers 300
instances across ±150 m and verifies every touched cell); instance row indexing; texture disposal on
entity / world change; revisions shown match the database after reload; persistent `condition` drives
burn visuals while previews only override geometry parameters; no shader compile errors or console
errors in any run.

## Manual test results (developer, Chrome on the RTX 5070)

Confirmed by the developer: offline sample renders terrain, trees, rocks and shadows; inspector
identifies entities and exposes parameter previews; companion connects to Neon; a Gemini-generated world
with forests and village buildings renders from persistent data; region loading and the grid work;
ignition was rejected at 25 m and committed at 2 m; the world went from revision 2 to 3 and Chrome
showed revision 3 after reload; roughly 152–180 FPS with varying SDF pass times.

**Not manually confirmed:** the appearance of the ignited tree — the developer did not locate that exact
tree. The burning look has only been seen in automated screenshots of the offline sample (which contains
one entity stored as `burning`); the live world's instance data was not visually checked for it.

## Tests executed (final run)

| Command | Result |
|---------|--------|
| `.venv\Scripts\python -m pytest tests` | **92 passed** (68 unit, 13 Neon persistence, 3 HTTP API, 8 Gemini incl. 1 live call) |
| `npm test` in `apps/web` | **49 passed** (terrain/noise golden parity with Python, CPU SDF, compiler, bounds, instances, grid) |
| `npx tsc --noEmit` | clean |
| `npm run build` | 809 kB JS (223 kB gzip); bundle scanned for keys / connection strings: none |
| `npm run visual` | **9/9** in Chrome on the RTX 5070; terrain GPU↔CPU 0.009 mm |
| Live world through companion + Neon | rendered world `w-27d032b20096` at revision 3, 25 regions, 770 instances |
| Replay check of the stored v2 world | generator + event log reproduce all 2,174 stored entities exactly |

`npm audit` (apps/web): 3 advisories — `tinypool` (2 critical), `@vitest/mocker` (1 moderate), all reached
only through `vitest`, the test runner. `npm audit --omit=dev` reports **0**; the root package reports 0.
They do not ship in the production bundle. The fix is a breaking upgrade to vitest 5 and was not applied.

Codex wrote `src/sdf/cpu.ts`, `noise.ts`, `terrain.ts`, `scripts/gen_golden.py`, the golden vectors and
35 of the web tests in its own set of files; reviewed and integrated (its float hash was replaced).

## Measured performance

Chrome headless, ANGLE D3D11, NVIDIA GeForce RTX 5070 Laptop GPU, 1600×900 canvas, offline sample
(49 regions, 1,697 SDF instances, 5 archetypes, 12 materials):

| View | Profile | Internal resolution | SDF pass (GPU timer) | Frame p50 / p95 |
|------|---------|---------------------|----------------------|-----------------|
| spawn, in forest | high | 1280×720 | 5.0 ms | 5.6 / 7.0 ms |
| aerial overview | high | 1280×720 | 5.1 ms | 5.6 / 6.5 ms |
| village | high | 1280×720 | 4.3 ms | 5.6 / 5.8 ms |
| lake, through trees | ultra | 1600×900 | 7.6 ms | 7.9 / 11.3 ms |
| spawn | performance | 800×450 | 1.6 ms | 5.6 / 6.0 ms |

A steep mountain world with 2,275 instances measured 5.1–6.2 ms at 1280×720. Frame times of 5.6 ms are
the display refresh cap. These are the only measurements: nothing is claimed for 1080p or 1440p
full-screen, for weaker GPUs, or for the hybrid pipeline, which does not exist yet.

## Risks carried into Chunks 3 and 4

- **Frame budget.** The SDF pass already uses 5–8 ms. Splat sorting and blending, and a coverage march
  for stale regions, come on top; expect to lower the internal SDF resolution or step counts.
- **VRAM.** 8,151 MiB total, ~980 MiB used by the desktop. The browser's render targets are small
  (a 1600×900 RGBA16F + depth target is ~20 MB), but splat data for dozens of regions and the optimizer
  in WSL2 share the same card; the 3.5 GB training cap is still unmeasured.
- **Capture path.** Training views are to be rendered by this shader in the browser; `gl.readPixels` of
  float depth at 256² × 28 views per region has not been timed.
- **WSL2 bridge.** CUDA PyTorch runs only under WSL2 (`/home/sidno/greality-gpu/.venv`, torch
  2.14.1+cu130, verified by the developer). The GPU service must be reachable from the Windows browser
  through localhost forwarding and must write assets where the Windows companion can serve them; neither
  is tested.
- **Step-budget artifacts** (known issue 1) will be more visible once splats sit behind SDF silhouettes.

## Manual acceptance test (as run)

```powershell
# terminal 1 – companion (prints an "open:" link containing the pairing token)
powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1
# terminal 2 – web app
cd apps\web
npm install
npm run dev
```

Open the printed `open:` link in Chrome (`http://localhost:5173/?sample=1` for the offline snapshot).
Navigate with WASD / mouse / F; toggle diagnostics with F3; switch debug views; pin an entity and move
its sliders; generate a world; run `.venv\Scripts\python scripts\chunk1_demo.py ignite <WORLD_ID>` and
reload to see the new revision. Automated: `npm run build` then `npm run visual`.
