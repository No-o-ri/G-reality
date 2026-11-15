# Known issues

Tracked defects and limitations that are not fixed yet. Severity: **high** blocks a demo path, **medium**
is visible or misleading, **low** is cosmetic or rare. "Phase" is the chunk expected to address it.

| # | Severity | Issue | How to see it | Phase |
|---|----------|-------|---------------|-------|
| 1 | medium | Grazing rays run out of march steps: speckled edge along near-hill silhouettes and noise toward the far horizon. | Offline sample at spawn, look along the top of the nearest hill; `steps` debug view is red there. | 4 (step budget / temporal filtering during optimisation) |
| 2 | medium | Entities end 160–190 m from the player (only regions within 2–3 cells are loaded) while terrain continues, so forests stop abruptly and pop in as you move. | Fly up 50 m and look at the horizon. | 3–4 (Gaussian far field) |
| 3 | resolved | ~~The browser does not refresh after a backend mutation.~~ During play the client polls `/v1/worlds/{id}/sync` and applies new revisions (`Gameplay.syncOnce`). Changes made while on the landing page appear when the world is opened. | — | done |
| 4 | medium | Fewer buildings than requested can be placed: a discrete placement that finds no level, dry ground within its retries is dropped without a note. Sample world: 5 of 7 houses. | `scripts\export_sample_world.py` output. | 5 (report in `interpretation_notes`) |
| 5 | low | Scattered objects of *different* features can overlap (tree inside a boulder): 24 pairs closer than 1.5 m among 1,700 entities in the sample. Buildings are kept clear (generator v3). | Walk the sample forest. | 5 |
| 6 | low | Worlds created with generator v2 (before the QA fixes) keep their stored archetypes: houses there have no foundation and may float on slopes; trees may stand inside houses. | Any world created before commit "Chunk 2". | none planned (regenerate) |
| 7 | resolved | ~~No collision with entities.~~ The player collides with the SDF geometry (`NearField.collide`). | — | done |
| 8 | low | Picking reaches only 60 m and treats the water surface as opaque. | Aim at distant trees or at something under water. | 5 |
| 9 | low | Instance texture height equals the entity count; more than the GPU's maximum texture height (≥ 8,192, typically 16,384) entities would fail to upload. Largest tested: 2,275. | Would need ~5× denser worlds. | 4 (wrap rows) |
| 10 | low | Grid capacity is 16 instances per 8 m cell; overflowing instances are not drawn in that cell. Counted and shown in the overlay; 0 in every tested world (max 6 per cell). | Overlay "grid … max N/cell". | 4 |
| 11 | low | WebGL context loss is not handled; the page must be reloaded. | GPU driver reset. | 7 |
| 12 | low | The ellipsoid primitive is an approximation that returns 0 at its exact center; `repeat` rounding at half-cell ties is unspecified between GPU and CPU. Neither is visible from outside a shape. | Unit-test notes. | none planned |
| 13 | low | Opening a legacy (generator v1) world by URL shows an error chip and an empty view instead of falling back to the sample. | `?world=<v1 id>`. | 7 |
| 14 | low | Python geometry validator accepts a plane with a zero normal; the GLSL compiler rejects it, so such an archetype fails at world load rather than at creation. | Craft an archetype by hand. | 5 |
| 15 | low | Development-only npm advisories (vitest → tinypool, @vitest/mocker). Not in the production bundle; fix requires a breaking vitest major upgrade. | `npm audit` in `apps/web`. | 7 |
| 16 | medium | Gaussian reconstructions are weakest at grazing angles, which is how a player sees the far field: held-out views at 21° elevation reach 14–23 dB PSNR on covered pixels (steeper views 18–28 dB). Water is the worst surface (view-dependent reflection, SH degree 1 only). | `data/train/<job>/holdout_*.png`; Gaussian / split view of a region from low altitude. | 4 (more low-angle views, larger captures, densification) |
| 17 | medium | gsplat is unavailable: no wheel for torch 2.14 / CUDA 13 / Python 3.14 and no nvcc or gcc in WSL. The pure-PyTorch tile rasterizer is used (~90 ms per 256² iteration with 20–25k Gaussians, 1500 iterations ≈ 2.3 min per region). | `services/gpu/rasterizers/gsplat_backend.py`. | developer: `sudo apt install build-essential`, then retry |
| 18 | medium | Objects cut by a region boundary are reconstructed as open shells in each region (the cut face is unobserved by design); seen from a region's outside, part of such an object is missing until the neighbouring region's asset is also shown. | Gaussian-only view of a region with trees on its edge. | 4 (both neighbours rendered; region ownership) |
| 19 | low | Region jobs run only when started by hand (`scripts/chunk3_reconstruct.py`); there is no background scheduler, no hold for still-burning regions, and the companion does not run GPU jobs itself. | Mutate a region: a `queued` job appears and stays queued. | 4 |
| 20 | low | The browser checks the asset's revision when it loads regions; an open page does not notice a later mutation (see #3), so a superseded asset stays visible until reload. The server never returns it again. | Mutate while the page is open. | 5 (event polling) |
| 21 | low | The capture renders flames at animation time 0 and water without animation; reconstructions of burning regions show one frozen flame state. | Reconstruct a region with a burning tree. | none planned |
| 22 | low | The production JS bundle grew from 815 kB to 3.5 MB (1.17 MB gzip) because Spark inlines its WASM; it is loaded even when no Gaussian asset exists. | `npm run build`. | 4 (lazy `import()` of Spark on first asset) |
| 23 | low | The `high` / `ultra` profiles are worse than `balanced` at 256² captures (more, smaller Gaussians without densification leave gaps); they need larger captures. | `data/train/bench-high/metrics.json`. | 4 |
| 24 | medium | The public website offers only the read-only sample world: generation, interactions and persistence need the Windows companion and a user-provided PostgreSQL URL (no embedded database). | Press E on the website: a toast explains it. | none planned for the hackathon |
| 25 | low | The launcher's interactive first-run setup (hidden prompts for the database URL / Gemini key) and the `winget` Python install are verified only by script parsing and code review; the automated release smoke test uses `-EnvFile` and an installed Python. | `scripts	est_release.ps1` | manual check on a clean Windows account |
| 26 | low | The Windows package is not code-signed; SmartScreen may warn about `G-reality.cmd`. | First run of a downloaded ZIP. | none planned |
| 27 | low | `GREALITY_GPU_WORKER` exists in the configuration but is not wired to a background worker (see #19). | `services/world_api/config.py` | 4 |
