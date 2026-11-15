# Chunk 3 results: Gaussian reconstruction from the SDF world (2026-11-14)

Status: **implemented, awaiting manual acceptance.** Nothing is committed yet. Open items are in
[KNOWN_ISSUES.md](KNOWN_ISSUES.md) (#16–#21).

## What exists

| Part | Where | Notes |
|------|-------|-------|
| Differentiable rasterizer | `services/gpu/rasterizers/torch_tiles.py` | Pure PyTorch, CUDA forward and backward through autograd. EWA projection (Σ = R S Sᵀ Rᵀ, perspective Jacobian, 0.3 px² dilation), 16-px tiles binned by 3σ footprint, stable per-tile depth order, front-to-back compositing with transmittance in log space, tiles grouped by length and rendered in chunks under activation checkpointing (bounded memory). Per-tile cap 2048, with dropped pairs counted. |
| Reference renderer | `services/gpu/rasterizers/reference.py` | Dense: every Gaussian against every pixel, no tiles. Test oracle only. |
| gsplat probe | `services/gpu/rasterizers/gsplat_backend.py` | Diagnosis and unblock steps; `make_rasterizer("gsplat")` raises `RasterizerUnavailable`. |
| Optimizer `3dgs-optim-v1` | `services/gpu/optimizer/train.py`, `losses.py` | Depth-unprojected, voxel-downsampled initialization. Adam over means, log-scales, quaternions, opacity logits, SH DC and SH degree 1. Loss: 0.8·L1 + 0.2·(1−SSIM) + 0.5·alpha L1 + 0.1·relative depth L1, random background per step. Pruning, hard VRAM cap, held-out evaluation, PLY serialization and a reload check. |
| Worker CLI | `services/gpu/reconstruct.py` | `python -m gpu.reconstruct --bundle … --out … --profile …` (WSL2, through `scripts/wsl-gpu.sh`). |
| PLY, cameras, bundle (Codex) | `services/gpu/ply.py`, `cameras.py`, `bundle.py` | Standard INRIA 3DGS binary PLY. The one GL↔OpenCV camera conversion. View-bundle loader. Numpy only. |
| Capture provider | `apps/web/src/rendering/nearfield/shader.ts` (capture mode), `NearField.captureView`, `Engine.beginCapture/captureView`, `apps/web/src/capture/rig.ts`, `apps/web/scripts/capture-region.mjs` | The Chunk 2 SDF ray marcher itself renders the views in Chrome on the RTX 5070. The march is clipped to the region box +2 m, fog is off, and the output is display-referred color plus linear depth, 2× supersampled. 24 training + 4 held-out cameras. Pixels where the ray enters the box inside a solid are **unobserved** (depth −1). |
| Orchestrator | `scripts/chunk3_reconstruct.py` | `live WORLD RX RZ`: job states `queued → awaiting_views → training → validating → published/failed/superseded`. Starts a private companion so the capture reads the authoritative region, checks that the captured revision equals the job's, trains in WSL, re-validates the PLY on Windows, stores it, publishes it with compare-and-set. `offline RX RZ`: sample world, no database. |
| Asset store and route | `services/world_api/assets.py`, `GET /v1/assets/{asset_id}` | Content-addressed keys `worlds/<id>/r_<rx>_<rz>/rev<rev>-<sha12>.ply` under `data/assets/` (git-ignored). Atomic writes; sha256 is verified on every read. The route requires the pairing token. |
| Browser diagnostic | `apps/web/src/rendering/farfield/GaussianDiagnostic.ts`, Engine `renderFrame`, App panel | Spark 2.4.0 `SplatMesh` in the shared scene. Modes: **SDF only**, **Gaussian only** (dark background, no SDF pass), **split** (same camera, SDF left, Gaussian right, labelled). Key **G** or the panel; `?gaussians=split`. An asset is shown only if its sha256 matches and its `region_revision` equals the loaded region's revision. |
| Parity tooling | `apps/web/scripts/spark-parity.mjs`, `scripts/spark_parity.py` | Renders the published asset with Spark from the exact held-out cameras and compares it with the capture and with the training rasterizer. |

The hybrid composition (region ownership, SDF discard inside splat regions, cross-fade) is **not** part of this
chunk. The overlay says "far field (hybrid): not implemented (Chunk 4)", and the diagnostic modes never mix the two
representations in one pixel.

## Environment facts established

- **Graphify hooks:** after the `MSYS_NO_PATHCONV=1` correction, all three hooks (`hook-guard search`, `read`,
  `compact`) exit 0 when run by hand with realistic payloads. The search and read hooks were also seen injecting
  context into this session. The compact hook was not triggered live, because no compaction happened.
- **CUDA:** WSL2 Ubuntu, Python 3.14.4, torch 2.14.1+cu130, RTX 5070 Laptop (sm_120), 8151 MiB. Verified from repo
  tooling by `tests/cuda`.
- **torch 2.14 quirk:** some eager CUDA ops (e.g. `bmm` backward) are routed to Triton JIT kernels, which need a
  C compiler that the distribution lacks. `TORCH_DISABLE_NATIVE_JIT=1` (set in `scripts/wsl-gpu.sh` and in the
  rasterizer module) keeps the stock ATen kernels.
- **gsplat:** not usable. Prebuilt wheels stop at torch 2.4 / CUDA 12.4 / CPython 3.10. The PyPI wheel compiles
  CUDA sources at first use and needs nvcc plus g++; WSL has no gcc and `sudo` needs a password. Nothing was
  installed system-wide. Added to the WSL venv: `pytest`, `pillow`.
- **Windows ↔ WSL bridge:** not needed as a network service. The Windows orchestrator runs the WSL worker as a
  subprocess on `/mnt/c/...` paths, and the browser talks only to the Windows companion.

## Verification of the rasterizer (`tests/cuda/test_rasterizer.py`, CUDA)

| Test | Result |
|------|--------|
| Tile renderer vs dense reference, 400 Gaussians, 96×80 (not a tile multiple), multiple chunks | max |Δrgb| < 2e-5, |Δalpha| < 2e-5 |
| Gradients vs dense reference (means, scales, quats, opacities, colors) | relative max error < 1e-3, all on CUDA, finite, non-zero |
| `torch.autograd.gradcheck` in float64 against finite differences | passes |
| Single isotropic Gaussian: projected center, depth, conic = 1/(σ²+0.3) | exact to 1e-4 |
| Per-tile truncation | reported in `stats["dropped"]` |
| Fitting 150 Gaussians to 4 target views | loss falls below half |
| gsplat request | raises `RasterizerUnavailable` with the diagnosis |

## Reconstruction measurements (RTX 5070 Laptop, 256² views, 24 train + 4 held-out)

PSNR is computed over observed pixels against the capture composited on black. "Covered" restricts it to pixels
where the capture or the reconstruction has alpha > 0.05; that is the stricter number, because empty background
inflates full-image PSNR. GPU memory is the PyTorch allocator peak; the 3.5 GiB cap was never approached.

| Run | Region | Iter. | Gaussians (init → final) | Train time | ms / iter | Held-out PSNR (full / covered) | SSIM | Train PSNR | Peak reserved | Asset |
|-----|--------|-------|--------------------------|-----------|-----------|--------------------------------|------|-----------|---------------|-------|
| offline, balanced | sample (0,0) lake shore, 219 entities nearby | 1500 | 24,414 → 20,299 | 139.5 s | 93 | 32.00 / 22.12 dB | 0.988 | 33.73 dB | 530 MiB | 2.11 MB |
| live rev 0, balanced | `w-cf427314a57a` (−2,−1), house + 31 trees | 1500 | 24,369 → 14,936 | 133.9 s | 89 | 29.28 / 21.05 dB | 0.978 | 29.77 dB | 530 MiB | 1.55 MB |
| live rev 1 (after ignite), balanced | same region, one tree burning | 1500 | → 14,943 | 133.2 s | 89 | 29.27 dB / — | — | — | 530 MiB | 1.55 MB |
| offline, performance | sample (0,0) | 600 | 14,092 → 11,088 | 50.0 s | 83 | 33.07 / 23.20 dB | — | 33.99 dB | 532 MiB | 1.15 MB |
| offline, high | sample (0,0) | 2500 | 37,465 → 30,968 | 256.4 s | 103 | 27.84 / 17.96 dB | — | 31.93 dB | 712 MiB | 3.22 MB |

Capture: 28 views in 4.5–12 s, including Chrome start-up (about 14–19 ms per supersampled view). Whole live job,
from enqueue to publish: about 2 min 30 s. Device memory in use at peak (all processes, including the desktop):
1.73 GB of 8.15 GB.

**Convergence (offline balanced).** Mean loss over iterations 1–100: 0.0287; 100–300: 0.0118; 300–700: 0.0077;
1100–1500: 0.0071. Held-out PSNR at iterations 250 / 500 / 750 / 1000 / 1250 / 1500: 29.84 / 30.53 / 31.01 /
31.53 / 31.75 / 32.00 dB. It is still rising slowly at the end. Pruning removed 1,715, then 1,039, then 475 …
Gaussians at successive 200-iteration checkpoints. The live job's curve has the same shape (26.51 → 29.28 dB).

**Per held-out view (offline balanced, covered PSNR):** 13.97 and 18.48 dB for the two views looking across
the lake at grazing angle; 28.37 and 27.68 dB for the two views looking at the wooded slope. Grazing views and
water are the weak cases (known issue #16).

**Profiles, finding.** More Gaussians did *not* help at 256²: `high` (0.46 m init voxels) is worse than
`balanced` from the first evaluation onward, on training views as well. So this is under-coverage of small splats
without densification, not overfitting. `performance` is the best of the three on this region. The Gaussian
budget only pays off together with higher capture resolution (planned 512² for Ultra) or densification. Until that
is measured, `balanced` is the default and `high`/`ultra` should not be used.

**Serialization and reload.** Every run decodes the written PLY and renders a held-out view from the reloaded
Gaussians: max |Δ| = 0.0 in all runs. The Windows side independently decodes, validates bounds and finiteness,
and checks sha256 and count before publication.

**Browser fidelity (Spark ↔ training rasterizer, display path, same held-out cameras):** 49.6 and 48.7 dB on the
two slope views (mean |Δ| ≈ 0.25 / 255). On the two grazing lake views it is 28.2 and 33.3 dB; there Spark is
closer to the capture than the training rasterizer is (33.1 vs 26.5 dB). Mean Spark ↔ capture: 34.3 dB. Geometry,
scale, rotation, coordinate frame and SH convention therefore survive the PLY → Spark path. (A first measurement
through an offscreen render target showed a uniform −7.5/255 shift. That was Spark's sRGB handling for
untagged render targets, not a defect of the asset; the measurement now uses the canvas framebuffer that users
see.)

## Revision contract, verified live on Neon `dev`

On `w-cf427314a57a` (a test world created this session with the rules interpreter; no existing world was touched):

1. `job-4289f2284cf84a74` captured revision 0 of region (−2,−1). The captured revision was checked to equal the
   job's, and the job published `asset-7c5fcab1286942e0` (region_revision 0). The browser loaded it through
   `GET /v1/assets/…` (sha256 verified) and showed it.
2. Igniting one tree in that region bumped the region to revision 1. In the same transaction the asset became
   `superseded` and `job-3867975d2ddc4cb3` (reason `mutation`, source revision 1) was queued. The read path
   returned `neural_asset: null`. The browser, reloaded, showed **no** Gaussian content for the region (the
   visual check fails with "no active Gaussian asset", which is the intended outcome).
3. Running the orchestrator again picked up the queued mutation job, captured revision 1 (the burning tree is
   visible in the reconstruction) and published `asset-8497796e31e242c6`. The browser then showed it with asset
   revision = region revision = 1.

The compare-and-set path for a job whose region changes *during* training (it ends `superseded`, and the file is
deleted) is covered by the existing Neon test `test_stale_job_cannot_publish_and_mutation_invalidates_asset`. The
orchestrator has that branch, but it was not exercised live.

## Tests executed (final run)

| Command | Result |
|---------|--------|
| `.venv\Scripts\python -m pytest tests -m "not gemini"` (Windows) | **129 passed, 3 skipped** (2 CUDA modules: torch is blocked by Smart App Control; 1 PNG test: no Pillow on Windows). Includes 13+ Neon tests and the new asset-route test (publish → serve → truncated file refused with 500 `asset_corrupt`). |
| `bash scripts/wsl-gpu.sh python -m pytest tests/cuda tests/unit/neural -q --noconftest` (WSL, CUDA) | **37 passed**: 7 rasterizer, 2 optimizer (end-to-end on a synthetic bundle: convergence, PSNR, reload, bounds, unobserved band; VRAM cap enforced → clean failure), 28 PLY / camera / bundle |
| `npx vitest run` (apps/web) | **52 passed** (49 from Chunk 2 + 3 capture-rig tests) |
| `npx tsc --noEmit` | clean |
| `npm run build` | 3.50 MB JS (1.17 MB gzip). Up from 815 kB because Spark inlines its WASM; see known issue #22 |
| `npm run visual -- --gaussians=0,0` | **12/12** (9 Chunk 2 shots + SDF / Gaussian / split of the offline asset), terrain GPU↔CPU 0.009 mm. Spawn SDF pass 4.9–5.4 ms on reruns (8.3 ms on the very first page of a run = warm-up). Gaussian-only and split views at 180 fps (display cap) with 20,299 Gaussians. |
| Live visual check (`--query=world=w-cf427314a57a --gaussians=-2,-1`) | 3/3 at rev 0; 3/3 after regeneration at rev 1 |

The Gemini integration test was not run this chunk (unchanged code, avoids an API call).

Codex wrote `services/gpu/ply.py`, `cameras.py`, `bundle.py` and their 26 tests in its own file set. They were
reviewed and integrated; I removed a `sys.path` workaround by renaming the test directories, and added the
unobserved-depth marker to the bundle loader afterwards.

## Engineering decisions made while measuring

1. **Unobserved pixels (capture).** At first, rays that entered the clip box inside the ground were written as
   transparent. A surface-only reconstruction cannot reproduce that: the same ray crosses the back of the ground
   sheet. It produced a ground band below the region edge at low angles. These rays carry no information (in the
   world the neighbour region blocks them), so they are now marked depth −1 and ignored. Covered-pixel held-out PSNR
   went from 20.0 to 22.1 dB; full-image PSNR went from 30.1 to 32.0 dB.
2. **SH degree 1.** Water color depends on view direction (Fresnel reflection of the sky), which degree 0 cannot
   represent. Degree 1 adds 9 floats per Gaussian (asset 1.56 → 2.06 MB).
3. **Tried and removed:** a flattening regularizer (penalty on the smallest scale). It made the Gaussians disk-shaped
   (5 cm) but did not improve held-out PSNR (19.84 vs 20.00 dB), so it was dropped.
4. **Spark settings match the trainer:** `preBlurAmount 0.3`, `blurAmount 0`, `maxStdDev 3`, `sortRadial false`.

## Manual acceptance test

Prerequisites: `npm install` and `npm run build` in `apps\web` (Spark 2.4.0 is a new dependency); WSL2 Ubuntu with
the GPU venv.

```powershell
# 1. CUDA tests in WSL (rasterizer, gradients, optimizer, memory cap)
wsl.exe -d Ubuntu --cd /mnt/c/Users/sidno/Documents/G-reality -- bash scripts/wsl-gpu.sh python -m pytest tests/cuda tests/unit/neural -q --noconftest

# 2. Offline: reconstruct sample region (0,0) (about 2.5 min), then view it
.venv\Scripts\python scripts\chunk3_reconstruct.py offline 0 0
cd apps\web; npm run build; npm run preview      # open http://localhost:4173/?sample=1&viewRegion=0,0&gaussians=split

# 3. Live (Neon dev): reconstruct a region of a world, then open the world through the companion
.venv\Scripts\python scripts\chunk3_reconstruct.py live <WORLD_ID> <RX> <RZ>
powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1     # then the printed open: link
```

In the page: press **G** to cycle SDF / Gaussian / split, or use the "Gaussian reconstruction (diagnostic)" panel.
The panel's **view** button flies to a viewpoint that frames the asset's region. F3 shows active and stale assets
and the Gaussian count. To check invalidation, mutate an entity in a reconstructed region and reload. The asset must
disappear; the panel shows nothing for that region, and `/v1/jobs?world_id=…` shows a new `queued` job. Running
the orchestrator again publishes for the new revision.

Automated: `npm run visual -- --gaussians=0,0` (offline asset present) adds three diagnostic shots to the nine
Chunk 2 shots.
