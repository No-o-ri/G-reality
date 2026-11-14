# Implementation plan, ownership, risks, demo

## 1. Repository layout

```text
G-reality/
  apps/web/                       Vite + React + TypeScript (Vercel target)
    src/app/                      shell, state store, companion client
    src/rendering/nearfield/      AST→GLSL compiler, CPU evaluator, ray-march pass, instance/grid buffers
    src/rendering/farfield/       Spark integration, asset loader, splat region meshes
    src/rendering/hybrid/         frame graph, region-state texture, quality profiles, dynamic resolution
    src/rendering/volumetrics/    fog / scattering GLSL chunks
    src/regions/                  client region manager, capture provider
    src/controls/  src/commands/  src/diagnostics/  src/interface/
  services/
    world_api/                    FastAPI app: state, regions, simulation, interactions, persistence, interpreter
    gpu/                          job scheduler, view ingestion, rasterizers, optimizer, PLY, storage, profiling
    companion/                    launcher that mounts both apps, auth, CORS, health
    pyproject.toml                one uv project for all Python code
  packages/contracts/
    python/greality_contracts/    canonical models (source of truth)
    schemas/                      geometry_ops.json (hand-written), contracts.schema.json (generated)
    ts/                           generated TypeScript types
  database/migrations/            numbered SQL
  tests/                          unit/ integration/ renderer/ persistence/ scenarios/ performance/ golden/
  scripts/                        gen_contracts.py, setup, start-companion.ps1, test.ps1, migrate.py
  docs/                           architecture/ contracts/ feasibility/ testing/ deployment/ judging/
```

## 2. Module ownership

| Module | Owner | Notes |
|--------|-------|-------|
| `packages/contracts/**`, `database/migrations/**` | Claude only | frozen for workers |
| `services/world_api/**` | Claude | Codex may add tests under `tests/` |
| `apps/web/src/rendering/hybrid/**`, `regions/**`, `app/**` | Claude | integration-critical |
| `apps/web/src/rendering/nearfield/cpu/**`, `tests/golden/**` | Codex (worktree) | pure functions, golden vectors |
| `services/gpu/rasterizers/**`, `optimizer/**`, `ply/**` | Codex (worktree) | behind `Rasterizer` / optimizer interface defined by Claude |
| `tests/performance/**`, benchmark scripts | Codex | read/measure, no engine edits |
| Reviews of each chunk diff | Codex, read-only | findings triaged by Claude |

## 3. Milestones and schedule (Pacific time; commit window closes Sun 12:00)

| Chunk | Window | Deliverable | Relative effort |
|-------|--------|-------------|-----------------|
| 0 | Sat 11:15–12:15 | this document set, contracts, draft migration | — |
| 1 | Sat 12:30–15:00 | world_api + Neon: create world, materialize regions, ignite mutation, events, rehydration, job rows | 15% |
| 2 | Sat 15:00–18:30 | SDF compiler + ray marcher + navigation + diagnostics | 20% |
| 3 | Sat 18:30–22:30 | capture → optimize → PLY → Spark; measured latency and VRAM | 25% |
| 4 | Sat 22:30–Sun 01:00 | shared-depth compositing, region ownership, fog, quality profiles | 15% |
| 5 | Sun 01:00–04:00 | NL world generation, command UI, actions, fire system | 12% |
| 6 | Sun 04:00–06:30 | invalidation → regeneration loop, exploration-driven generation, stale guards | 8% |
| 7 | Sun 06:30–10:00 | companion start script, production build, Vercel preview, README, demo rehearsal | 5% |
| Submit | Sun 10:00–12:00 | video, Devpost (initial submission by 11:00), **no feature work** | — |

The schedule has no slack for sleep and every chunk has a human gate, so it will slip. Pre-agreed cut
order, least valuable first: (1) Ultra/Performance profiles (keep High + Balanced), (2) per-splat fade
and volumetric light shafts, (3) LLM-authored new archetypes (keep library + parameters),
(4) `extinguish` action, (5) hosted Vercel deployment (demo locally, keep the build green),
(6) `build` action. Never cut: Neon persistence, real splat optimization, revision-safe invalidation,
the ignite → spread → persist → regenerate loop.

Parallel track (starts on Chunk 0 approval, does not cross gates): Codex prototypes the PyTorch
rasterizer and optimizer on an analytic test scene so that Chunk 3 starts from a measured baseline.

## 4. Codex delegation plan

Invocation: `codex exec -c windows.sandbox=mxc --sandbox workspace-write "<task>"` run from a dedicated
worktree (`git worktree add ../G-reality-wt/<name> -b codex/<name>`); read-only reviews use
`--sandbox read-only` in the main checkout. Every task prompt contains: objective, architecture
pointers (file paths in `docs/`), owned paths, frozen contracts, required outputs, tests to run,
constraints, definition of done, and the report format from the directive §13.4. Claude reviews each
branch diff (contracts, coordinate conventions, state ownership, races, security, leaks, build) and
merges manually; a worker's "success" is not sufficient.

| When | Task | Owned paths | Done when |
|------|------|-------------|-----------|
| C1 ∥ | Differentiable tile rasterizer + optimizer on an analytic scene; benchmark it/s and peak VRAM at 128²/256², 10k/40k Gaussians | `services/gpu/rasterizers/`, `services/gpu/optimizer/`, `tests/unit/gpu/` | tests pass; benchmark JSON committed; fits 3.5 GB cap |
| C1 ∥ | 3DGS PLY writer/reader + validators | `services/gpu/ply/` | round-trip test; file loads in Spark (checked by Claude) |
| C2 ∥ | TS CPU SDF evaluator + golden vectors for noise/terrain/primitives | `apps/web/src/rendering/nearfield/cpu/`, `tests/golden/` | vitest green; vectors consumed by Python test |
| C2 | Read-only review of the GLSL compiler for unbounded loops / precision / step-scale errors | — | findings list |
| C3 | gsplat build attempt (45 min box) behind the same interface, only if the CUDA Toolkit is installed | `services/gpu/rasterizers/gsplat_backend.py` | parity test vs torch backend or a written failure diagnosis |
| C4 | Scripted performance path + results JSON per profile | `tests/performance/` | numbers reproducible twice within 10% |
| C5 | Fire-system property tests (determinism, bounds) | `tests/unit/simulation/` | tests pass against Claude's implementation |
| each gate | Read-only review of the chunk diff | — | findings triaged |

Caveat to verify at first use: the Codex sandbox may block network access, in which case dependency
installation is done by Claude in the main session and Codex only writes and runs code.

## 5. Risks

| # | Risk | Likelihood | Impact | Mitigation / trigger |
|---|------|------------|--------|----------------------|
| R1 | ~~dGPU unavailable~~ resolved 11:39 PT (Discrete mode; RTX 5070, 8151 MiB, driver 592.82). Residual: CUDA PyTorch not yet installed or run | low | C3 | first step of the neural track: install `torch==2.14.1+cu130`, run a CUDA smoke test; confirm `chrome://gpu` |
| R0 | Smart App Control blocks every PyTorch DLL (and Graphify) on Windows. **Mitigated:** developer verified CUDA PyTorch in WSL2; the GPU service will run there | resolved for torch | C3 must bridge Windows browser ↔ WSL service (localhost forwarding) | verify from repo tooling at the start of C3 |
| R2 | Pure-PyTorch rasterizer too slow or memory-hungry | medium | long regeneration, small regions | measure during C1; tiers in architecture §5.3; reduce region count / far range; pre-bake demo world |
| R3 | gsplat cannot be built (no CUDA Toolkit, VS 2026) | high | none for baseline | optional accelerator only, 45 min box |
| R4 | Spark + custom depth pass integration surprises (sorting, color, depth) | low–medium | hybrid visuals | 20-line spike at the start of C3; fallback: region-granular ownership needs only default Spark behaviour |
| R5 | Splats trained from 28 views look poor from the player's distance/angle | medium | visual quality | cameras placed along plausible player sightlines; fog; raise iterations for demo regions |
| R6 | SDF shader too slow with many instances — **measured in Chunk 2: 4–5 ms at 720p internal, 7 ms at 900p for 1,708 instances** | low | FPS | grid acceleration, bound tests, step budgets, internal resolution; untested on weaker GPUs and high-relief worlds |
| R7 | Three evaluators of noise/terrain diverge | medium | misplaced entities, floating trees | golden vectors from C2; sin-free hash |
| R8 | Neon latency makes commands feel slow | low | UX | explicit "committing" state; pooled connection kept warm; measure in C1 |
| R9 | Schedule: 7 gated chunks in ~22 h with one human | high | incomplete features | cut order in §3; end-to-end loop prioritized over polish |
| R10 | Chrome LNA prompt / corporate policy blocks HTTPS → localhost at the venue | low–medium | hosted demo | demo from `http://localhost` build, which is exempt |
| R11 | Gemini models intermittently overloaded/slow (observed 503/504 on three flash models; one world request took 46 s through fallbacks) | medium | NL features | model chain with the fast model first (measured ~2 s world, ~1 s command), rules fallback for commands, `GEMINI_MODEL` override |
| R12 | Lighting baked into splats (time of day cannot change without regeneration) | certain | limitation | documented; environment changes bump all region revisions |

## 6. What Chunk 1 must prove

1. Contracts generate TS types and both sides validate the same payloads.
2. Migration applied to a Neon `dev` branch; real reads/writes.
3. World creation (fixed seed, rules interpreter stub) and deterministic region materialization.
4. `ignite` command end to end through the transaction in architecture §7.3, including region
   revision bump, asset supersede and job row.
5. Restart the process, reload, and observe identical state, revision order and event history.
6. Transaction rollback, idempotent retry and concurrent-writer tests (test plan, persistence 4–6).
7. Measured command round-trip latency to Neon.

## 7. Three-minute demo specification

| Time | Action | Proof on screen |
|------|--------|-----------------|
| 0:00–0:30 | Type "a misty conifer forest around a lake with a small village on the eastern shore". | Status chips walk requested → validating → geometry ready; the validated `WorldSpec` JSON is expandable; world appears. |
| 0:30–1:10 | Walk and look around. Toggle F3 and the debug tint. | Overlay: SDF pass ms, splat pass ms, splat count, active splat regions; tint shows near = SDF, far = Gaussians, with real occlusion between them. |
| 1:10–2:00 | Approach a tree, press E, type "set this tree on fire". Try it once from too far away first. | Rejection message (out of range), then commit: revision increments, tree burns, fire spreads to a neighbor, smoke thickens, wood count on charred trees is 0 when inspected. |
| 2:00–2:35 | Open the state panel; reload the page; restart the companion. | Same revision, events listed from Neon, trees still charred. Region panel: asset revision N superseded → job → asset revision N+k with measured seconds; far view of that region now shows the burnt patch. If regeneration exceeds the time slot, show the completed job record and say so. |
| 2:35–3:00 | One architecture slide. | Typed SDF language, optimization-based Gaussian far field, one revisioned world state in Neon, local GPU companion. |

Reliability: fixed seed and a pre-generated (genuinely optimized, cached) copy of the demo world; runs
fully on localhost; rules interpreter as the fallback if the language model is unreachable; rehearse a
cold start twice during Chunk 7.
