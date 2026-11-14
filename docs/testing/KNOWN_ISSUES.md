# Known issues

Tracked defects and limitations that are not fixed yet. Severity: **high** blocks a demo path, **medium**
is visible or misleading, **low** is cosmetic or rare. "Phase" is the chunk expected to address it.

| # | Severity | Issue | How to see it | Phase |
|---|----------|-------|---------------|-------|
| 1 | medium | Grazing rays run out of march steps: speckled edge along near-hill silhouettes and noise toward the far horizon. | Offline sample at spawn, look along the top of the nearest hill; `steps` debug view is red there. | 4 (step budget / temporal filtering during optimisation) |
| 2 | medium | Entities end 160–190 m from the player (only regions within 2–3 cells are loaded) while terrain continues, so forests stop abruptly and pop in as you move. | Fly up 50 m and look at the horizon. | 3–4 (Gaussian far field) |
| 3 | medium | The browser does not refresh after a backend mutation; a reload is needed to see a new revision or changed entity. | Run `scripts\chunk1_demo.py ignite <id>` while the page is open. | 5 (event polling `/v1/worlds/{id}/events`) |
| 4 | medium | Fewer buildings than requested can be placed: a discrete placement that finds no level, dry ground within its retries is dropped without a note. Sample world: 5 of 7 houses. | `scripts\export_sample_world.py` output. | 5 (report in `interpretation_notes`) |
| 5 | low | Scattered objects of *different* features can overlap (tree inside a boulder): 24 pairs closer than 1.5 m among 1,700 entities in the sample. Buildings are kept clear (generator v3). | Walk the sample forest. | 5 |
| 6 | low | Worlds created with generator v2 (before the QA fixes) keep their stored archetypes: houses there have no foundation and may float on slopes; trees may stand inside houses. | Any world created before commit "Chunk 2". | none planned (regenerate) |
| 7 | low | No collision with entities: the player walks through trees, rocks and houses. | Walk into a tree. | 5 |
| 8 | low | Picking reaches only 60 m and treats the water surface as opaque. | Aim at distant trees or at something under water. | 5 |
| 9 | low | Instance texture height equals the entity count; more than the GPU's maximum texture height (≥ 8,192, typically 16,384) entities would fail to upload. Largest tested: 2,275. | Would need ~5× denser worlds. | 4 (wrap rows) |
| 10 | low | Grid capacity is 16 instances per 8 m cell; overflowing instances are not drawn in that cell. Counted and shown in the overlay; 0 in every tested world (max 6 per cell). | Overlay "grid … max N/cell". | 4 |
| 11 | low | WebGL context loss is not handled; the page must be reloaded. | GPU driver reset. | 7 |
| 12 | low | The ellipsoid primitive is an approximation that returns 0 at its exact center; `repeat` rounding at half-cell ties is unspecified between GPU and CPU. Neither is visible from outside a shape. | Unit-test notes. | none planned |
| 13 | low | Opening a legacy (generator v1) world by URL shows an error chip and an empty view instead of falling back to the sample. | `?world=<v1 id>`. | 7 |
| 14 | low | Python geometry validator accepts a plane with a zero normal; the GLSL compiler rejects it, so such an archetype fails at world load rather than at creation. | Craft an archetype by hand. | 5 |
| 15 | low | Development-only npm advisories (vitest → tinypool, @vitest/mocker). Not in the production bundle; fix requires a breaking vitest major upgrade. | `npm audit` in `apps/web`. | 7 |
