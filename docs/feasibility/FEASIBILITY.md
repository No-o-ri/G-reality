# Feasibility findings (Chunk 0, 2026-11-14)

Legend: **[V]** verified on this machine or against a primary source today · **[K]** known from
documentation/experience, not re-verified · **[E]** estimate, must be measured.

## 1. Local environment

| Item | Finding | |
|------|---------|--|
| Repo | `C:\Users\sidno\Documents\G-reality`, branch `main`, origin `No-o-ri/G-reality`, initial commit 10:37 PST (inside the commit window) | [V] |
| Node / npm | 24.22.0 / 11.19.1 | [V] |
| Python | only 3.15.0 (`C:\Python315`); no `uv`, no conda; no torch | [V] |
| MSVC | Visual Studio Community 2026 (18), MSVC 14.51.36231 present; `cl` not on PATH | [V] |
| CUDA Toolkit | **not installed** (`nvcc` absent, `CUDA_PATH` unset) | [V] |
| Codex | `codex-cli 0.169.0`, read-only MXC sandbox delegation works | [V] |
| Graphify | 0.9.105 on PATH; `query / explain / path / update` available; no graph built (no source yet) | [V] |
| **NVIDIA GPU** | **Detected** (re-checked 11:39 PT after the developer switched to Discrete GPU mode and restarted): NVIDIA GeForce RTX 5070 Laptop GPU, driver 592.82, driver CUDA compatibility 13.1, 8151 MiB total, 979 MiB in use at idle desktop, 6913 MiB free | [V] `nvidia-smi` |

History: at 11:15 PT the GPU was a phantom PnP device (integrated-only mode) and `nvidia-smi` failed;
that blocker is resolved. What is still **not** measured: any CUDA workload, VRAM under training, and
browser frame rate. Before Chunk 2, confirm at `chrome://gpu` that WebGL2 reports the RTX 5070 (in
Discrete mode this should be automatic).

VRAM budget from the measured idle figure: 8151 MiB total − ~980 MiB desktop = ~6.9 GB for the browser
renderer and the training worker together (architecture §11).

## 2. Browser rendering stack

| Question | Finding | |
|----------|---------|--|
| Splat renderer inside a three.js scene | Spark `@sparkjsdev/spark` 2.4.0, MIT, peer dependency `three >= 0.180`; current three is 0.187.0 | [V] npm registry |
| Depth against opaque geometry | Spark docs: splats render in the transparent pass with `depthTest` on, `depthWrite` off, premultiplied alpha, radial or Z sorting | [V] docs |
| Runtime loading | `new SplatMesh({ url | fileBytes, fileType })`, also `packedSplats`/`constructSplats`; `dispose()` frees buffers; formats PLY, SPZ, SPLAT, KSPLAT, SOG | [V] docs |
| Per-splat modification | `objectModifiers` / `worldModifiers` (dyno), `SplatEdit` SDF edits, `opacity`, `recolor` | [V] docs; usage untested |
| Custom ray-march pass writing `gl_FragDepth` | Core WebGL2 feature; standard three.js `ShaderMaterial` | [K] |
| Combined: ray-march depth + Spark splats | Expected to work because both use the default framebuffer depth; **not yet run** — first task of Chunk 4, with a 20-line spike in Chunk 3 | [E] |
| three.js native splat renderer (r186+) | Reported to exist; not evaluated. Spark stays primary because of its documented loading/modifier API | [K] |
| PlayCanvas / SuperSplat | Capable engine, but SuperSplat is an editor, and adopting PlayCanvas means writing the SDF pipeline inside a second engine model; no advantage for this design | [K] |
| Vercel | Vite static output; no server functions required | [K] |

Pitfalls to test explicitly: tone mapping applied twice, sRGB vs linear capture, sort popping at region
borders, antialiasing (Spark recommends `antialias: false`), precision of `gl_FragDepth` at 300 m+
(near plane 0.1 m, far 2000 m is fine for 24-bit depth at the distances where splats start).

## 3. Neural pipeline on this hardware

| Question | Finding | |
|----------|---------|--|
| PyTorch for RTX 50 on Windows | cu128 wheels exist for cp312–cp314 (2.10/2.11); **cu130 wheels exist for cp312–cp315 (2.14.0/2.14.1)** on download.pytorch.org | [V] wheel index |
| Blackwell (sm_120) needs | CUDA 12.8+ builds of PyTorch | [K] PyTorch forums |
| `gsplat` wheels | PyPI 1.5.3 is a source/pure wheel (compiles CUDA at first import). Prebuilt Windows wheels on docs.gsplat.studio are **cp310 only, PyTorch ≤ 2.4, CUDA ≤ 12.4** — none usable with a Blackwell-capable PyTorch | [V] wheel index |
| Building `gsplat` here | Needs CUDA Toolkit (absent, ~3 GB, admin install) and `cl.exe` activated; compatibility of `nvcc` with MSVC 14.51 (VS 2026) is unknown | [V]/[E] |
| Baseline without compilation | A tile-based differentiable Gaussian rasterizer in pure PyTorch needs only the torch wheel | [K] |
| VRAM for baseline | 40k Gaussians × 14 params × (value + grad + 2 Adam moments) ≈ 9 MB. Dominant cost is per-tile pixel×Gaussian intermediates; chunked tiles keep this to an estimated 1–3 GB at 256² | [E] |
| Latency for baseline | Estimated 5–20 it/s at 256² ⇒ 30 s – 3 min per region at 500–800 iterations, plus < 2 s capture and upload | [E] |
| Depth-initialized Gaussians | Already a coarse reconstruction at iteration 0, so short schedules are reasonable; quality vs iterations to be plotted in Chunk 3 | [E] |

### Python / PyTorch environment (re-checked 11:39 PT)

Only Python 3.15.0 is installed (`C:\Python315`); no `uv`, no conda, no CUDA Toolkit. Wheel indexes
checked directly:

| Build | cp312 | cp313 | cp315 |
|-------|-------|-------|-------|
| torch + cu130 (CUDA 13.0 runtime bundled) | 2.14.1 | 2.14.1 | **2.14.1** |
| torch + cu128 | 2.11.0 | 2.11.0 | none |

`psycopg-binary` 3.3.9, `numpy` 2.5.4, `pillow` 13.0.0 and `pydantic-core` 2.50.4 all publish cp315
Windows wheels. [V]

**Chosen environment:** a project virtualenv on the existing **Python 3.15** with
**`torch==2.14.1+cu130`** from `https://download.pytorch.org/whl/cu130`. CUDA 13.0 wheels bundle their
own runtime and are within the driver's CUDA 13.1 compatibility, they are the current build line for
Blackwell (sm_120), and no new interpreter or tool has to be installed. No CUDA Toolkit is needed for
the baseline rasterizer.

**Fallback** if a cp315 wheel misbehaves at runtime: install `uv`, create a Python 3.12 virtualenv,
same `torch==2.14.1+cu130`. The contracts were already verified on 3.15 with Pydantic 2.14.3.

Not yet verified: that `torch.cuda.is_available()` is true and a kernel runs on this GPU — nothing has
been installed. This is the first step of the neural track (about a 2–3 GB download).

## 4. Path A (pretrained feed-forward) vs Path B (optimization from SDF views)

| Criterion | Path A: LGM / MVSplat / DepthSplat / AnySplat-class models | Path B: `3dgs-optim-v1` |
|-----------|-----------------------------------------------------------|--------------------------|
| Input | Object-centric multi-view (LGM) or posed/unposed real-scene images | Exact synthetic views + depth + cameras from our own SDF |
| Weights | Multi-GB checkpoints, research licences vary per repo (not audited) | None |
| VRAM | Published feed-forward scene models report well above 8 GB at modest resolutions; unverified for each model | Small, tunable |
| Domain fit | Trained on real photographs; stylized procedural renders are out of distribution | Exact by construction |
| Windows / RTX 50 setup | Custom CUDA extensions and pinned old PyTorch versions are common | torch wheel only |
| Latency | Sub-second to seconds once running | Tens of seconds to minutes |
| Alignment to world coordinates | Needs scale/pose registration | Exact (our cameras) |

Decision: **Path B**. Path A's only advantage is latency, and it carries the highest setup and
out-of-distribution risk; its licence and VRAM claims above are [K], not individually audited. A
feed-forward model can later be added as a `Rasterizer`-independent *initializer* without changing any
contract.

## 5. Independent Codex review (read-only, `gpt-6.1-sol`)

Codex was given the plan and asked to probe the machine and rank risks. Its conclusions, with my review:

| Codex finding | My assessment |
|---------------|---------------|
| Environment probe matches §1; GPU model/VRAM unverifiable from its sandbox | Agrees; my `Get-PnpDevice` result explains why |
| Binary stack (Python × torch × gsplat) is the highest risk; choose Python 3.12 | Partly adopted: after verifying cp315 wheels for torch cu130 and every other dependency, 3.15 is primary and 3.12 the fallback (D10) |
| gsplat Windows automation targets Python 3.10 and architectures without `sm_120`; time-box to one hour | Adopted, tightened: gsplat is an optional accelerator, time-boxed 45 min |
| 8 GB workable: one region, one view per step, 256 px, SH0, capped growth, images on CPU, 3–4 GB training budget | Adopted in §5.2/§11 of the architecture |
| Latency: tens of seconds to minutes; benchmark 100 steps first | Adopted as the first Chunk 3 task |
| Depth compositing feasible with Spark defaults; write standard depth, disable log/reversed depth, single tone-map | Adopted (§3, §6) |
| Prefers reduced Path B; fallbacks: capped gsplat → optimize colors/opacity/scale on fixed geometry → small pure-PyTorch renderer (64–128 px, 1–5k Gaussians) → offline bake | Adopted as tiers. Codex is more pessimistic than I am about the pure-PyTorch renderer's capacity; neither estimate is measured, so the Chunk 3 benchmark decides |

Raw output is kept outside the repository (session scratchpad); it was a read-only run and changed no files.

## 6. Browser → localhost

Chrome gates public-origin → loopback requests behind a Local Network Access permission prompt
(fetch since Chrome 142; WebSockets later). `http://127.0.0.1` targets are not blocked as mixed content.
Local development from `http://localhost:5173` is unaffected. Details in architecture §10. [V] Chrome
developer documentation via search; behaviour on the developer's exact Chrome version untested.

## 7. Sources

- Spark: https://sparkjs.dev/docs/overview/ · https://sparkjs.dev/docs/splat-mesh/ · https://sparkjs.dev/docs/spark-renderer/ · https://github.com/sparkjsdev/spark
- gsplat: https://github.com/nerfstudio-project/gsplat · https://docs.gsplat.studio/whl/gsplat/ · https://github.com/nerfstudio-project/gsplat/blob/main/docs/INSTALL_WIN.md
- PyTorch wheels: https://download.pytorch.org/whl/cu130/torch/ · https://download.pytorch.org/whl/cu128/torch/ · https://discuss.pytorch.org/t/pytorch-support-for-sm120/216099
- Chrome LNA: https://developer.chrome.com/blog/local-network-access · https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access
- Feed-forward models: https://arxiv.org/abs/2403.14627 (MVSplat) · https://arxiv.org/abs/2410.13862 (DepthSplat) · https://github.com/OpenRobotLab/AnySplat
