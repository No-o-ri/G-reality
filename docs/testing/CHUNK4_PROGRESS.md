# Chunk 4 — mid-chunk checkpoint (2026-11-14 16:23 PT)

**Status: incomplete. This is a checkpoint, not `CHUNK4_RESULTS.md`.** Nothing is committed.

## Implemented and tested

| Area | Where | Evidence |
|------|-------|----------|
| Authoritative fire simulation: ignition spread (distance, wind, humidity), burn-out, caps, bounded 900-tick catch-up | `services/world_api/simulation.py` (Codex), `WorldService.advance_simulation`, `sync` | 16 unit tests; 2 Neon tests (direct ignite → 20 s → 15-min catch-up → charred; contiguous revisions; replay = stored table; charred tree cannot re-ignite) |
| One clock for all clients: Neon `now()`, `sim_clock_at` advanced by whole simulated seconds | `service.advance_simulation` | the Neon test caught and fixed a local-vs-DB clock / fraction-loss bug |
| Structured E-key action (no LLM round trip) | `WorldCommand.action`, `ParsedIntent.interpreter = "direct"` | Neon test |
| `GET /v1/worlds/{id}/sync` (advance + events), `POST /v1/worlds {offline}` | `app.py` | playtests |
| Landing page: prompt, examples, Gemini generation with stages and timer, actionable errors with offline fallback, saved worlds, live backdrop | `apps/web/src/interface/Landing.tsx`, `App.tsx` | screenshot `p0-00-landing.png` |
| Player HUD: crosshair, **E — Burn it?**, toasts, pause menu, developer tools behind a toggle (\` key or pause menu) | `Hud.tsx`, `App.tsx` | playtests |
| Grounded player: gravity, jump, sprint, slope limit, SDF collision with objects, wading, swimming, fly only in developer mode, safe walkable spawn facing an open view | `controls/Player.ts`, `NearField.collide`, `Engine.placeSafely` | playtests |
| Fire visuals: flame / smoke / ember particles (soft, depth-tested), up to 8 flickering fire lights in the SDF shader, charring and crown loss derived from authoritative timestamps, smouldering after burn-out | `rendering/effects/FireSystem.ts`, `app/Gameplay.ts`, `shader.ts` | screenshots `c4alpine-04-fire-*.png` |
| Generator v4: per-world procedural archetypes (seeded conifer / broadleaf trees with jointed trunks, branch limbs, tier / lobe structure; rocks as boulders / slabs / stacks; houses with footprint, storeys, gable / hip / flat roofs, door, window rows, chimney, porch) + per-entity shape parameters (crown fullness, lower / upper crown or lobe-set scales, tilt, yaw, scale) | `services/world_api/procgen.py`, `worldspec.py`, `regions.py` | offline tests updated to the v4 contract; screenshots `c4alpine-02-*`, `c4canyon2-02-*` |
| Release package: companion serves the built frontend; launcher with preflight, per-requirements venv, logs, smoke test | `packaging/`, `scripts/build_release.ps1`, `scripts/test_release.ps1` (Codex), `world_api/__main__.py` | release smoke test PASS (fresh extract → venv → Neon health 1.4 s → `/`, `/v1/worlds` → clean stop) |
| End-to-end playtest through the real UI | `apps/web/scripts/play-test.mjs` | `c4alpine`: PASS (generate → enter → prompt → E → spread to 5 trees → charred → leave / return → close / reopen: 5 of 5 persisted) |

Test totals at the checkpoint: Python **147 passed, 3 skipped** (Windows, incl. Neon); web **52 passed**; `tsc` clean;
build OK; release smoke test PASS.

## Measured

- Gemini world design: **79–95 s per prompt** in all five runs today (8–12 s earlier this afternoon). Region
  generation itself takes 0.47 s for 25 regions. The cause (model latency / fallback order in `gemini.py`) is not yet
  investigated.
- Alpine v4 world, eye level, Chrome / ANGLE / RTX 5070, 1600×900: `balanced` (new player default) 109 fps,
  SDF pass 8.1 ms; during a 3-tree fire 83–98 fps (p95 12–22 ms). At `high` the same kind of world measured
  17.5 ms SDF pass (50 fps). Shader 43 KB, compiled in 1.5 s.

## Not done (requirements still open)

1. **Per-entity topology.** Trees have 5 generated structures per family per world plus per-entity continuous shape
   parameters. Every tree does *not* yet have its own branching. Planned: seeded procedural SDF operators evaluated
   per instance (branch / tier layout from an instance seed).
2. **Prompt-driven terrain and families.** Terrain is still one fBm / ridge / basin function (canyon, volcanic and
   coastal prompts differ in colour, relief and fog only). Gemini reports cacti, dead trees, mesas, spires, craters and
   stilt huts as not representable. Planned: terrain v2 shaping (terraces, canyons, volcano / crater, dunes, coast,
   erosion) in Python / GLSL / TS with parity tests; new families (dead tree, cactus, palm, shrub, spire, stilt hut);
   prompt-driven colours per family.
3. **Graphics:** physically based sky with clouds, water (waves, Fresnel, depth colour, shoreline, ripples), HDR
   bloom / AA / exposure, light shafts, fog quality, terrain step-budget artifacts (known issue #1, visible near the
   camera).
4. **Neural far field** integration (region ownership, transitions, entity-only captures, background worker): not
   started in Chunk 4.
5. Gemini latency; `CHUNK4_RESULTS.md`; manual acceptance in a headed browser (all evidence so far is headless).

## Update 16:31 PT — language-to-geometry path (untested by me at the developer's request)

Gemini now writes geometry: `WorldDraft.objects[]` (name, kind, `geometry_json` = an SDF AST, materials with emissive
color/strength, 0–3 per-entity parameters used as `{"ref"}` in shape numbers, size, placement, density/count,
flammable, scale range) plus `ambient_light`. Pipeline: Gemini → `geometry_normalize.normalize_graph` (op synonyms,
range clamping from the op table, arity repair, `defs`/`use` reuse expansion) → GPU budget (≤72 nodes, ≤3 noise ops,
≤3 octaves per object; ≤360 nodes per scene) → strict `GeometryDefinition` validation → one validator-driven Gemini
repair round → world spec / Neon → GLSL compiler → ray marcher. New ops: `radial_repeat`, `taper`, `stretch` (Codex),
`curve_tube` (quadratic-Bézier swept tube), `superquadric`. Emissive materials glow (material emissive color) and the
nearest glowing entities light their surroundings; night times darken sun/sky/fog. Developer panel lists every
constructed object with its AST. Known limits: no translucency; offline keyword interpreter cannot construct objects;
Gemini world design took 80–95 s earlier today and may take longer with geometry.
