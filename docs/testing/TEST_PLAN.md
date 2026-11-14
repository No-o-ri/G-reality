# Test plan

Tooling: `pytest` (Python), `vitest` (TypeScript), Playwright (browser, headed Chrome with the dGPU),
one PowerShell entry point `scripts/test.ps1 [-Suite unit|persistence|neural|renderer|e2e]`.
Tests that need Neon run only against the `dev` branch (`DATABASE_URL` from `.env`) and create worlds
with a `test-` prefix that they delete afterwards. A test is reported as passed only if it ran.

## Unit

| Area | Tests | Chunk |
|------|-------|-------|
| Contracts | schema export is current (`gen_contracts.py --check`); TS types compile; round-trip JSON for every exported model | 1 |
| Geometry language | validator accepts library archetypes, rejects malformed trees (unknown op, range, arity, depth, node count, bad ref) | 1 |
| SDF values | analytic distances for every primitive at fixed points; CSG identities (`union(a,a)=a`, `subtract` sign); transform invariance (translate/rotate/scale); bounding sphere is conservative (random points: `d ≥ |p| − bound_radius`) | 2 |
| Noise / terrain | GLSL, TS and Python agree with `tests/golden/noise_v1.json` and `terrain_v1.json` within 1e-3 | 2 |
| Shader compilation | every library archetype and 50 fuzzed valid ASTs compile and link in headless WebGL2 | 2 |
| Region generation | same `(seed, rx, rz)` → identical entities across two processes; neighbor regions do not share ids; changing seed changes output | 1 |
| Actions | each `ActionDef`: preconditions pass/fail matrix (range, capability, state); `apply` is pure and deterministic | 1 (ignite), 5 (all) |
| Fire system | fixed seed → identical spread sequence; humidity 1.0 → no spread; fuel exhaustion → charred with wood 0 | 5 |
| Camera conversion | GL camera_to_world ↔ OpenCV world_to_cam round trip; project a known world point to the expected pixel | 3 |
| Rasterizer | single Gaussian renders at the projected pixel with expected footprint; gradients finite; front-to-back order respected | 3 |
| PLY writer | write → parse → identical arrays; header matches the 3DGS layout Spark accepts | 3 |

## Persistence (real Neon `dev` branch)

1. Migration applies to an empty branch and is idempotent on rerun.
2. Create world → rows exist; reload from a **new process** → equal `WorldState`.
3. Commit a mutation → entity row, one `world_events` row per revision, region revision +1,
   `region_revisions` row, active asset superseded, new job queued — all visible after reconnect.
4. Forced failure inside the transaction (injected exception after the entity update) → nothing persisted,
   world revision unchanged.
5. Two concurrent commands on the same world → both commit with distinct consecutive revisions, no gaps.
6. Same `command_id` twice → one set of events, identical `MutationResult`.
7. Publish CAS: job for region revision N while region is at N+1 → job `superseded`, no asset row.
8. Event replay: snapshot + events reproduces the entity table.

## Neural

1. Analytic scene (ground plane + colored spheres rendered by a tiny Python reference) → optimize →
   holdout PSNR above threshold; loss decreases monotonically on average.
2. Real region bundle from the browser → asset passes validation; metrics recorded (seconds, VRAM peak).
3. Alignment: a red sphere entity at a known world position → mean of red Gaussians within 0.5 m.
4. **Consistency scenario (§5.5 of the directive):** generate region → asset A at revision N → remove or
   burn a large structure → revision N+1 → A is not returned by the read path → job runs → asset B
   references N+1 → rendered image of B differs from A in the changed area (image diff over the
   structure's screen bounds) and holdout PSNR is measured against post-change views. Latency logged.
5. VRAM guard: worker honours the memory cap; an oversized job fails cleanly without killing the API.

## Renderer and hybrid (Playwright, screenshots + pixel probes)

1. SDF pass: known sphere at a known place covers the expected pixels; depth read-back matches the
   analytic depth within tolerance.
2. Depth compositing: a splat region placed behind an SDF wall is fully occluded; in front, it is visible.
3. Ownership: for every region state, exactly one representation contributes (debug view tints SDF red,
   splats blue; no pixel is both; no region is neither unless unloaded).
4. Alignment: the same landmark rendered by SDF and by its splat projects within 2 px at 150 m.
5. Stale handling: mark a region stale → its splat disappears within one frame, fallback coverage shows.
6. Temporal stability: 120-frame fly-through, frame-to-frame difference below a threshold at the
   transition band; no disappearing geometry when crossing region borders.
7. Performance capture: 30 s scripted path per profile → average FPS, p50/p95/p99 frame time, pass
   timings, written to `tests/performance/results/*.json`.

## End-to-end scenario (`tests/scenarios/full_loop`)

```text
create world (rules interpreter, fixed seed) → regions materialized → far regions generated
→ scripted walk to a tree → "set this tree on fire" → proximity + precondition checks pass
→ committed (revision R+1) → Neon rows verified → region asset superseded → fire spreads (≥ 1 fire_spread event)
→ burn out → regeneration job published for the final revision → restart companion
→ reload world → tree charred, wood = 0, region asset revision == region revision
```
Each arrow logs a stage record `{stage, ok, ms, detail}` so a failure names the stage.

## Manual acceptance

Every chunk report ends with numbered manual steps and expected observations for the developer; the
chunk is not complete until the developer has run them.
