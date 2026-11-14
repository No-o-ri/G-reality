# G-reality — System Architecture (Chunk 0)

Status: proposed, awaiting developer approval. Nothing here is implemented yet except the contract
scaffolding in `packages/contracts/` and the draft migration in `database/migrations/`.

Companion documents: [feasibility](../feasibility/FEASIBILITY.md) · [contracts](../contracts/CONTRACTS.md) ·
[test plan](../testing/TEST_PLAN.md) · [plan, ownership, risks, demo](../PLAN.md)

---

## 1. One-page summary

```text
                    ┌──────────────────────── browser (Vercel static build) ───────────────────────┐
                    │  React UI overlays      Region manager        Capture provider (offscreen)   │
                    │  ───────────────────────────────────────────────────────────────────────────  │
                    │  three.js r187 WebGL2 scene, one camera, one depth buffer                     │
                    │    pass A  SDF ray march  (GLSL generated from the geometry AST)  → color+depth│
                    │    pass B  composite quad writes gl_FragDepth                                 │
                    │    pass C  Spark SplatMesh per far region (depth-tested, alpha blended)       │
                    └───────────────▲──────────────────────────────────────┬────────────────────────┘
                                    │ /v1 JSON, bearer pairing token       │ view bundles (PNG + depth)
                                    │ 127.0.0.1 only                       ▼
┌───────────────────────────────────┴───────── local companion process ─────────────────────────────┐
│  world_api (FastAPI)                          gpu (FastAPI router + 1 worker subprocess)          │
│   interpreter → validator → generator          job scheduler → 3DGS optimizer (PyTorch CUDA)      │
│   simulation (actions, fire system, ticks)     PLY writer → validator → storage adapter           │
│   mutation transaction, revisions, CAS         local asset cache (%LOCALAPPDATA%\G-reality)       │
└───────────────┬───────────────────────────────────────────────────────────────────────────────────┘
                │ psycopg 3, TLS, server-side credentials only
                ▼
        Neon PostgreSQL (authoritative world state, events, revisions, job + asset manifests)
```

One authoritative model (`WorldState` + regions + entities, in Neon) is the only definition of the
universe. The SDF near field and the Gaussian far field are both **derived** from it, each tagged with
the revision it was derived from, and each discarded when that revision is no longer current.

## 2. Decisions

| # | Decision | Chosen | Main reason |
|---|----------|--------|-------------|
| D1 | Browser graphics | **three.js r187 + WebGL2 + GLSL ES 3.00** | Only stack where a custom ray-march pass and a maintained splat renderer share one scene, camera and depth buffer today |
| D2 | Splat renderer | **Spark 2.4 (`@sparkjsdev/spark`, MIT)** | `SplatMesh` is a `THREE.Object3D`; depth-tests against opaque geometry by default; loads PLY/SPZ from bytes; multiple meshes sorted together |
| D3 | Far-field generation | **Path B: 3DGS optimization from synthetic views of the SDF world** | Ground truth geometry and cameras are exact; no multi-GB checkpoint; fits 8 GB; directly demonstrates "same world state" |
| D4 | View provider | **Browser renders the training views with the same SDF shader**, uploads RGB + depth + cameras | Zero appearance drift between near and far field, no second shading implementation; a PyTorch provider fits behind the same contract later |
| D5 | Optimizer | **Own tile-based differentiable Gaussian rasterizer in pure PyTorch (baseline)**, `gsplat` as optional accelerator | `gsplat` has no Windows wheel for any PyTorch that supports RTX 50 (see feasibility); the baseline needs only the PyTorch wheel |
| D6 | State model | **Hybrid: current-state tables + append-only event log + periodic snapshots**, per-world row lock, monotonic revisions | Fast reads, full audit, simple recovery |
| D7 | Contracts | **Pydantic v2 is the single source → JSON Schema → generated TypeScript** | One hand-written definition, drift check in CI |
| D8 | Backend process | One local companion process, two packages (`world_api`, `gpu`), training in a worker subprocess | Simple start-up, clean VRAM release, DB credentials never reach the GPU code or browser |
| D9 | Simulation authority | Server side, deterministic, tick based | Renderer-independent consequences |
| D10 | Python runtime | **venv on the installed Python 3.15 + `torch==2.14.1+cu130`**; fallback uv-managed 3.12 with the same torch | cp315 Windows wheels verified for torch cu130, psycopg, numpy, pillow, pydantic-core; nothing new to install |

Rejected: PlayCanvas (would require porting the whole SDF pass into its engine and build model; SuperSplat
is an editor, not an embeddable runtime), WebGPU-only splat renderers (younger, and three.js WebGPU +
custom ray march + splats is not a proven combination), two stacked canvases (no shared depth),
pretrained feed-forward Gaussian models as the primary path (see feasibility §4).

## 3. Coordinates and regions

- Right-handed, **+Y up**, meters. Camera space is OpenGL/three.js (−Z forward). Stored as float64 in
  Postgres, float32 on the GPU. Playable extent is bounded to ±4 km so float32 precision stays ≤ 0.5 mm.
- Region = square XZ cell, `REGION_SIZE_M = 64`. `rx = floor(x / 64)`, `rz = floor(z / 64)`; region origin
  (min corner) = `(rx * 64, 0, rz * 64)`. Regions are unbounded in Y.
- Entity local space: origin at the base of the object, +Y up. `p_world = position + R(q) · (scale · p_local)`.
- Depth buffer: standard WebGL `[0,1]` perspective depth, no logarithmic or reversed depth. The SDF pass
  writes `gl_FragDepth = 0.5 * clip.z / clip.w + 0.5` using the same projection matrix as the camera.
- Splat assets store Gaussian centers relative to the region origin in the same frame; the manifest carries
  `origin` and `coordinate_frame = "world_rh_yup_m"`. Training uses OpenCV cameras internally:
  `world_to_cam_cv = diag(1, −1, −1, 1) · inverse(camera_to_world_gl)`. This is the only conversion and it
  lives in one function with a round-trip test.

These constants are defined once in `greality_contracts` and exported; no subsystem redefines them.

## 4. Mathematical near field

### 4.1 Scene language
A `GeometryDefinition` is a typed AST over a closed op set (`packages/contracts/schemas/geometry_ops.json`):
9 primitives, 6 boolean/smooth combinators, 8 domain ops (translate, rotate, uniform scale, limited
repetition, mirror, twist, bend, noise warp) and 3 field ops (displace, round, shell). Every numeric
parameter has a range; depth ≤ 12, ≤ 128 nodes. A scalar may be a literal or `{"ref": "name"}` pointing at a
declared `ParamSpec`, which is how entity state drives geometry (a tree's `burn` parameter shrinks the
canopy and switches to the charred material). Terrain is a separate analytic heightfield
(`TerrainDefinition`, `terrain-v1`) so it can be evaluated in closed form everywhere.

Model output is never compiled or executed directly: it is parsed as JSON, validated by Pydantic and
`validate_node`, and only then handed to compilers that emit code from a fixed template per op.

### 4.2 Three evaluators, one definition
| Evaluator | Where | Used for |
|-----------|-------|----------|
| AST → GLSL | `apps/web/src/rendering/nearfield/compiler` | rendering, capture |
| AST → TS closure (float32) | `apps/web/src/rendering/nearfield/cpu` | player collision / ground height, picking, unit tests |
| Terrain + bounds in Python | `services/world_api/regions` | deterministic placement, mutation bounds |

All three are tested against shared golden vectors (`tests/golden/*.json`, tolerance 1e-3).

### 4.3 Renderer
- Full-screen triangle, fragment shader sphere-traces from the camera.
- Scene = terrain + water plane + entity instances. One GLSL function per archetype
  (`float sd_<id>(vec3 p, vec4 params, out int mat)`), generated at world load.
- **Instances are data, not code**: an RGBA32F instance texture holds position, yaw/quaternion, scale,
  archetype id, bound radius and 4 state-driven params per entity. A mutation rewrites one row; the shader
  is not recompiled.
- **Acceleration**: a 2D uniform grid over XZ (4 m cells, R32UI index texture + packed index list) covering
  the near field. Per march step the shader evaluates terrain analytically and only the instances
  registered in the current cell; the step is clamped to the cell exit distance so nothing is skipped.
  Instances are first tested against their bounding sphere.
- Normals by tetrahedral finite differences; soft shadows (32 steps, sun only); height fog; ambient + sky
  term; hit threshold proportional to distance (`eps = 0.0015 * t`); step scale per op from the op table.
- Budgets per quality profile: max steps 64–160, shadow steps 0–32, internal resolution 0.5–1.0.
- Instrumentation: `EXT_disjoint_timer_query_webgl2` per pass, step-count heat-map debug view.

## 5. Neural far field

### 5.1 Representation
Standard 3DGS parameters per Gaussian: center (3), log-scale (3), rotation quaternion (4), opacity logit
(1), SH degree-0 color (3). Serialized as standard 3DGS binary PLY (property names of the reference
implementation), one file per region revision, content-addressed. `NeuralAssetManifest` records region,
`region_revision`, bounds, count, sha256, metrics and the producing job.

### 5.2 Generation algorithm (`3dgs-optim-v1`)
1. **Schedule** — world_api inserts a `NeuralGenerationJob(source_region_revision = current)`.
2. **Capture** — the browser's capture provider renders the region *in isolation* (march clipped to the
   region AABB plus a 2 m margin, fog off, transparent background) from 28 cameras on two rings and a top
   cap (distance 1.5–3 region sizes, looking at the region center), 24 train + 4 holdout, at 256² (512² on
   Ultra). Each view yields sRGB8 color and float32 linear depth with exact intrinsics/extrinsics
   (`ViewBundleManifest`, tagged with the region revision it rendered).
3. **Initialize** — unproject train depths to a colored point cloud, voxel-downsample (0.4–1.0 m by
   profile) to 15k–60k points; scale from voxel size, identity rotation, opacity 0.5.
4. **Optimize** — Adam over all parameters, loss = L1 + 0.2·(1 − SSIM) on RGB plus L1 on alpha, random
   background color per step, one view per step, 300–1500 iterations by profile; prune opacity < 0.02
   every 200 steps. No densification in the baseline (the depth init already covers the surface).
5. **Validate** — finite parameters, all centers inside padded bounds, count > 0, holdout PSNR ≥ 20 dB
   (threshold tuned in Chunk 3). Failure ⇒ job `failed`, region stays on the fallback path.
6. **Publish** — write PLY to the storage adapter, then the compare-and-set transaction in §7.4.

Step 4 is real gradient-based 3DGS fitting through a differentiable rasterizer; it is honest to call it
*optimization-based neural/learned reconstruction*, and it must not be described as a pretrained
generative model.

### 5.3 Rasterizer tiers (same `Rasterizer` interface)
1. `torch` (baseline, guaranteed): EWA projection, 16-px tiles, per-tile depth sort, front-to-back alpha
   compositing with cumulative transmittance, tiles processed in chunks to bound memory.
2. `gsplat` (accelerator, time-boxed 45 min, needs the CUDA Toolkit installed): same inputs/outputs.
3. Reductions if tier 1 is too slow: 128² views, ≤ 10k Gaussians, optimize color/opacity/scale only.
4. Last resort: bake regions ahead of the demo with tier 1 and serve from cache — still genuinely optimized.

### 5.4 Region manager (client) and scheduler (server)
Per region the client holds exactly one of: `near_sdf`, `far_splat` (valid asset whose
`region_revision == region.revision`), `far_fallback` (no asset, stale asset, or job running),
`unloaded`. Server job states: `queued → awaiting_views → training → validating → published | failed |
superseded | cancelled`. Concurrency is 1 GPU job; priority is distance to the player; jobs for regions
that still contain burning entities are held until the region is quiescent (so one regeneration captures
the final charred state rather than chasing every tick).

## 6. Hybrid compositing

Per frame, one `renderer.render(scene, camera)`:

1. **Pass A — SDF** into `nearTarget` (RGBA16F + `DepthTexture`) at the internal resolution.
2. **Pass B — composite quad** (opaque, drawn first): samples `nearTarget`, upscales, writes color and
   `gl_FragDepth`; misses write sky color at depth 1.
3. **Pass C — splats**: Spark draws every visible `SplatMesh` in the transparent pass with
   `depthTest = true`, `depthWrite = false`, premultiplied alpha. Near SDF geometry therefore occludes
   distant splats per pixel with the real depth buffer.
4. Tone mapping and sRGB conversion happen exactly once, at output; the SDF pass and the capture pass
   both produce the same display-referred colors the splats were trained on.

**No double rendering by construction.** A 2D *region-state texture* (one texel per region around the
camera) tells the SDF shader which representation owns each region. An SDF hit inside a `far_splat`
region is discarded (the splat shows); a hit inside `near_sdf` is shaded fully; a hit inside
`far_fallback` is shaded by the *coverage march* (terrain plus bounding-ellipsoid impostors colored from
current entity state, ≤ 48 steps, no shadows). Ownership switches at region granularity with hysteresis
(`R_near` = 96 m enter, 112 m leave) and a 0.5 s opacity cross-fade.

Enhancements once the baseline works (Chunk 4): per-splat distance fade and per-splat fog through
Spark's modifier pipeline, dithered SDF fade in the transition band, temporal reprojection of the SDF
target for dynamic resolution.

**Volumetrics**: analytic exponential height fog integrated along the ray (closed form), sun in-scatter
lobe, plus region `smoke_density` raising local extinction; optional 8–24 step volumetric march for light
shafts on High/Ultra. Fog hides fidelity loss; it is never the reason a region is considered "rendered".

**Diagnostics overlay** (toggle with F3): FPS, frame-time percentiles, pass A/B/C GPU ms, loaded splat
count (sum of manifest counts of visible meshes), active splat regions, fallback regions, stale regions,
pending/active jobs, world revision, current region revision vs asset revision, camera position,
`performance.memory` and companion-reported VRAM.

## 7. Authoritative state and mutation protocol

### 7.1 Classes of data
- **Authoritative** (Neon): worlds, world_regions, entities, players, world_events, region_revisions,
  commands, geometry_definitions.
- **Derived** (regenerable): compiled GLSL, instance/grid textures, neural assets + manifests, jobs.
- **Transient** (client only): flame flicker animation phase, UI toasts, debug views. Flames and smoke
  are *drawn from* `EntityState.condition/burn_intensity` and `WorldRegion.smoke_density`; an effect with
  no backing state is not allowed.

### 7.2 World and region generation
`WorldSpec` (terrain, environment, archetypes, feature placement rules) + `seed` fully determine the
pristine world. Region generation is a pure function
`generate_region(spec, seed, generator_version, rx, rz) → entities`: jittered-grid candidate points from
a counter-based RNG keyed by `(seed, rx, rz, feature_id, index)`, filtered by terrain height, slope and
feature masks. Entity ids are `e:{rx}:{rz}:{feature}:{index}` — stable forever. The first time a region
is requested it is **materialized**: generator output is inserted in one transaction and from then on
the database rows, not the generator, are authoritative (so mutations and generator upgrades cannot
conflict).

### 7.3 Command → committed mutation
```text
POST /v1/worlds/{id}/commands (WorldCommand)
 1 validate schema; idempotency lookup by command_id → return stored MutationResult if present
 2 interpret text → ParsedIntent (LLM or rules; advisory only)                       [outside txn]
 3 BEGIN; SELECT … FROM worlds WHERE world_id = $1 FOR UPDATE                         [serializes writers]
 4 resolve target (hint → aim ray → nearest matching kind) against current rows
 5 evaluate ActionDef.preconditions (range, capability, state, environment)
      fail → store command as rejected, COMMIT, return failures (no revision change)
 6 effects = ActionDef.apply(state) → entity patches + events (pure function)
 7 write entities; one world_events row per event with revision = ++worlds.revision;
   ++revision for every region intersecting the event bounds; insert region_revisions;
   mark active neural_assets of those regions superseded; supersede live jobs, insert new queued jobs
 8 store command result; COMMIT
 9 return MutationResult {events, full post-commit entities, dirty_regions}
```
The client is **not optimistic**: it shows "committing…" and applies the returned entities only after
step 9, so a failed commit can never leave the screen ahead of the database. Typical round trip is
dominated by Neon latency (to be measured in Chunk 1). On transport or database failure the result is
`status: "error"`, nothing is applied, and retrying with the same `command_id` is safe.

Concurrency: the per-world row lock makes mutations linearizable; `expected_world_revision` lets a
client learn that it acted on outdated information (`status: "conflict"` when the action is no longer
valid against current state). This already supports multiple sessions on one world.

### 7.4 Asset publication (stale jobs cannot win)
```sql
BEGIN;
SELECT revision FROM world_regions WHERE world_id=$1 AND rx=$2 AND rz=$3 FOR UPDATE;
-- if revision <> job.source_region_revision: UPDATE neural_jobs SET state='superseded'; COMMIT; delete file
UPDATE neural_assets SET state='superseded' WHERE world_id=$1 AND rx=$2 AND rz=$3 AND state='active';
INSERT INTO neural_assets (… region_revision = job.source_region_revision, state='active' …);
UPDATE neural_jobs SET state='published', asset_id=… WHERE job_id=…;
COMMIT;
```
The same check exists on the read side: `RegionSnapshot.neural_asset` is only populated when
`asset.region_revision = region.revision`, and the client re-checks before activating a mesh. Three
independent guards (unique live-job index, CAS at publish, equality at read) protect Invariant E.

### 7.5 Simulation
- Tick = 1 s of simulated time. The world_api advances ticks while at least one client is polling
  `/events`; with no observer the clock is frozen and persisted (`worlds.sim_clock_at`), so nothing
  happens "off the books" and reloads are deterministic.
- `ActionDef { type, target capabilities, max_range_m, preconditions[], apply() }` registry:
  `ignite` (flammable, intact, humidity < 0.9, range 6 m), `extinguish` (burning), `remove`
  (removable; yields resources to the player inventory), `build` (places a new entity from an archetype
  at a location, costs inventory resources), `inspect` (read-only).
- Systems run each tick inside one transaction and emit `cause: "simulation"` events with
  `parent_event_id` chains:
  - **Fire**: burning entities consume `fuel`; each flammable intact neighbor within
    `6 m + wind bias` ignites with probability `p = 0.05 · intensity · (1 − humidity) · wind_alignment`
    decided by a hash of `(seed, tick, source, target)` (reproducible). Fuel exhausted → `charred`:
    geometry param `burn = 1`, `resources.wood = 0`, loses `flammable`/`harvestable`.
  - **Hazard**: burning entities carry a hazard radius; a player inside loses health
    (`player_damaged`), region `smoke_density`/`hazard_level` rise and decay, which increases fog
    extinction (visibility) on every client.
- Gameplay consequences demonstrated: fire spreads to neighbors, charred trees can no longer be
  harvested for wood, wood is needed to `build`, smoke reduces visibility, and standing in fire hurts.

## 8. Persistence (Neon)

Schema: `database/migrations/0001_init.sql` (12 tables). Highlights: `world_events` primary key
`(world_id, revision)` enforces exactly one event per revision; partial unique indexes enforce one live
job and one active asset per region; `entities_burning_idx` keeps the tick query cheap; splat bytes are
never stored in Postgres, only `uri` + `sha256`.

Plan for Chunk 1 (nothing has touched Neon yet): install the Neon CLI, link project
`sparkling-unit-65426005`, create a `dev` branch from `production`, read the generated `neon.ts`
before any `neon deploy`, pull credentials into a git-ignored `.env`, apply migrations to `dev` with a
small psycopg runner recorded in `schema_migrations`, run the persistence acceptance test, and only
then ask for approval to apply the same migration to `production`.

Storage adapter: `AssetStore { put(key, bytes) → uri, open(uri), delete(uri) }`; first implementation is
the local cache directory served read-only by the companion at `/v1/assets/{key}`; an S3-compatible
implementation can be added without touching simulation or jobs.

## 9. Natural language

`POST /v1/worlds {prompt, seed?}` → interpreter → `WorldSpec` → validation → world header committed
(`geometry_ready`) → regions materialize on demand → far jobs queue (`neural_pending/active`) → `ready`.
The interpreter must choose terrain parameters, environment, and features, referencing a built-in
archetype library (conifer, broadleaf, rock, boulder, house, tower, campfire, bridge segment, wall) and
may add new archetypes as geometry ASTs. Anything it cannot express goes into
`interpretation_notes` (shown in the UI) or yields a structured `ApiError`.

Interpreter providers behind one interface (`services/world_api/interpreter/`), selected by
`GREALITY_INTERPRETER`:

- **`gemini` (primary, decided by the developer at the Chunk 0 gate)** — Google Gemini through the
  official `google-genai` SDK with structured output (`response_schema`). The model fills a small flat
  `WorldDraft` / `IntentDraft`; `build_spec` clamps every value and constructs the validated
  `WorldSpec`. Replies are re-validated with Pydantic, unknown fields are dropped, and a draft cannot
  reference entity ids or state, so the model can neither execute code nor bypass simulation rules.
  A model chain is tried in order (fast model first); overloaded (503), slow (504/timeout) and retired
  (404) models are skipped, 429 backs off, 401/403 fails immediately. The key is read from the
  git-ignored `.env.local` and never logged.
- **`rules`** — deterministic keyword parser: offline fallback, and the provider used by automated
  tests. When Gemini fails during a *command*, the service falls back to rules and says so in the
  player-facing message; world creation surfaces the provider error instead.
- A local model can be added as a third provider without touching the service.

## 10. Companion service, security, deployment

- Started explicitly with `scripts/start-companion.ps1`; binds `127.0.0.1:8765`.
- **Pairing token**: 256-bit random token generated at start, printed with a pairing link
  (`<frontend>/#companion=http://127.0.0.1:8765&token=…`). Every route except `/v1/health` requires
  `Authorization: Bearer <token>`; health returns only version and capability flags without it.
- **CORS**: explicit allow-list from `GREALITY_ALLOWED_ORIGINS` (dev server and the deployed origin), no
  wildcard, no credentials/cookies (so CSRF via ambient authority does not apply); `Origin` is also
  checked server side; preflight answers `Access-Control-Allow-Private-Network: true`.
- **Chrome Local Network Access**: an HTTPS page calling loopback triggers a one-time permission prompt
  (Chrome 142+); `http://127.0.0.1` is exempt from mixed-content blocking. The UI explains the prompt
  and shows exact start-up instructions when the companion is unreachable. Job status uses polling over
  `fetch` (no WebSocket) to stay inside the simplest permission path.
- Database credentials live only in the companion's `.env`; the browser bundle contains none.
- Frontend: Vite static build, `VITE_COMPANION_URL` default, deployable to Vercel (and GitHub Pages
  with a base path). Without a companion the app can still explore a world from cached snapshots and
  published assets if they are hosted, and otherwise reports "neural far field unavailable" and uses
  the SDF coverage path.
- A hosted GPU provider would implement the same `/v1/jobs` routes; nothing else changes.

## 11. Performance and quality scaling

| Profile | Internal res | March steps | Shadows | Volumetric steps | R_near | Far range | Splats/region | Capture | Iterations |
|---------|-------------|-------------|---------|------------------|--------|-----------|---------------|---------|------------|
| Ultra | 1.0 | 160 | 32 | 24 | 128 m | 384 m | 60k | 512² | 1500 |
| High | 0.8 | 128 | 24 | 12 | 96 m | 320 m | 40k | 256² | 800 |
| Balanced | 0.66 | 96 | 16 | 0 | 96 m | 256 m | 25k | 256² | 500 |
| Performance | 0.5 | 64 | 0 | 0 | 64 m | 192 m | 15k | 128² | 300 |

Dynamic resolution adjusts the internal scale ±0.1 to hold the frame-time target; upscaling is bilinear
plus a sharpening pass (no DLSS claim; a native renderer could swap pass A behind the same
`NearFieldRenderer` interface). VRAM budget on the measured 8151 MiB card (idle desktop uses ~980 MiB):
desktop + browser ≤ 2.5 GB, training worker ≤ 3.5 GB hard cap via
`torch.cuda.set_per_process_memory_fraction`, headroom ≥ 1.5 GB. Only the idle figure is measured.
While a job trains, the browser drops one quality step if frame time exceeds budget. All numbers above
are targets; none has been measured yet.

## 12. Renderer-independent API boundaries

| Boundary | Interface | Implementations |
|----------|-----------|-----------------|
| State access | `/v1` REST + contracts | world_api |
| Near field | `NearFieldRenderer { setWorld, setRegionStates, updateEntities, render, captureView }` | WebGL2 ray marcher |
| Far field | `FarFieldRenderer { activate(manifest, bytes), deactivate(coord), stats }` | Spark |
| Views | `ViewProvider → ViewBundleManifest + blobs` | browser SDF (now), PyTorch SDF (later) |
| Rasterizer | `Rasterizer.render(gaussians, camera) → rgb, alpha` | torch, gsplat |
| Storage | `AssetStore` | local dir, S3-compatible (later) |
| Language | `Interpreter.world(prompt)`, `Interpreter.command(text, context)` | claude-cli, anthropic, rules |

## 13. Answers to the mandatory questions

1. **New mathematical region?** `generate_region(spec, seed, generator_version, rx, rz)` (§7.2), run by
   world_api on first request, materialized transactionally, returned as a `RegionSnapshot`.
2. **How does the neural renderer derive from the same state?** The capture provider renders the
   region's current entities and terrain with the near-field shader, the optimizer fits Gaussians to
   those views, and the asset is stamped with that `region_revision` (§5.2).
3. **Which algorithm produces Gaussians?** 3D Gaussian Splatting optimization (Adam through a
   differentiable tile rasterizer) initialized from unprojected SDF depth — `3dgs-optim-v1`.
4. **Why viable on 8 GB?** No pretrained weights; state is tens of thousands of Gaussians (a few MB plus
   Adam moments); one 256² view per step; tile chunks bound activation memory; worker capped at 3.5 GB.
   This is an engineering estimate to be confirmed by the Chunk 3 benchmark.
5. **Generation slower than movement?** Regions without a valid asset render through the SDF coverage
   march under fog; jobs are prioritized by distance and the queue is bounded; the overlay shows it.
6. **Unavailable or stale region?** Never drawn as a splat: the read path only returns assets whose
   revision matches, and the client state machine puts the region in `far_fallback` (§5.4, §6).
7. **How does an interaction modify state?** Only through the transaction in §7.3.
8. **How does it produce gameplay consequences?** `ActionDef.apply` plus tick systems (fire spread,
   resource loss, smoke visibility, hazard damage), all persisted as events (§7.5).
9. **Durable commit?** Single Postgres transaction on Neon holding the world row lock; client applies
   results only after commit (§7.3).
10. **Regenerating a changed region?** The same transaction bumps the region revision, supersedes the
    asset and queues a job; capture → optimize → validate → CAS publish (§5.2, §7.4).
11. **Outdated jobs overwriting newer state?** Impossible: unique live job per region, publish
    compare-and-set on `source_region_revision`, read-side equality check (§7.4).
12. **Spatial and depth consistency?** One camera, one projection, one depth buffer; SDF writes true
    depth; splats depth-test; a single coordinate frame and one documented camera conversion (§3, §6).
13. **Weaker GPUs?** Quality profiles and dynamic resolution (§11); without a capable local GPU the
    far field falls back to SDF coverage or previously published assets.
14. **Secure localhost access?** Loopback bind, pairing bearer token, origin allow-list, no cookies,
    LNA-aware UI (§10).
15. **Smallest complete demonstration?** One world; 3×3 near regions; a ring of splat regions;
    walk to a tree; "set this tree on fire"; fire spreads to a neighbor; wood becomes unavailable;
    region revision bumps; splat for that region is replaced after regeneration; reload shows the
    same charred trees from Neon (see PLAN §7).
16. **What should Codex own?** Leaf packages with frozen interfaces: the PyTorch rasterizer/optimizer,
    PLY writer and validators, TS CPU evaluator + golden tests, benchmarks, and read-only reviews
    (PLAN §4).
