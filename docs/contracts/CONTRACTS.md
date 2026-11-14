# Contracts

**Source of truth:** `packages/contracts/python/greality_contracts/models.py` (Pydantic v2) and
`packages/contracts/schemas/geometry_ops.json` (SDF op table). Field-level types, ranges and
descriptions live there and are not repeated here. Generated artifacts:
`packages/contracts/schemas/contracts.schema.json` (`python scripts/gen_contracts.py`, `--check` in CI)
and `packages/contracts/ts/contracts.ts` (`npm run contracts`; `npm run contracts:check` verifies both). Generated files are never edited by hand.

## Global rules

- **Frame:** right-handed, +Y up, meters; quaternions `(x, y, z, w)`; matrices column-major; camera
  −Z forward. `REGION_SIZE_M = 64`. Colors in contracts are linear RGB 0..1.
- **Serialization:** JSON, snake_case, UTC ISO-8601 timestamps, enums as strings. Unknown fields are
  rejected (`extra = "forbid"`) on both sides.
- **Versioning:** `CONTRACT_VERSION` (semver) is returned by `/v1/health` and stored per world. A client
  refuses to talk to a companion with a different major version. Geometry has its own
  `language_version`; changing the meaning of an op or of `terrain-v1`/`value-fbm-v1` requires a new
  version, never an in-place edit, because stored worlds depend on it.
- **Revisions:** `WorldState.revision` increases by exactly one per `WorldEvent`. `WorldRegion.revision`
  increases whenever an event's bounds touch the region. Only world_api assigns revisions, inside the
  mutation transaction.
- **Errors:** HTTP 4xx/5xx bodies are `ApiError {code, message, detail}`. Command outcomes are never
  HTTP errors: they are a `MutationResult` with `status` committed / rejected / conflict / error.
- **Change control:** only the orchestrator (Claude) edits `models.py` and `geometry_ops.json`. Workers
  who need a change report it; they do not patch contracts.

## Ownership, producers, consumers

| Contract | Class | Owner / producer | Consumers | Persisted in |
|----------|-------|------------------|-----------|--------------|
| `WorldState` | authoritative | world_api | web, gpu scheduler | `worlds` |
| `WorldSpec`, `TerrainDefinition`, `FeatureSpec` | authoritative | interpreter → validator (world_api) | region generator, SDF compiler | `worlds.spec` |
| `EnvironmentState` | authoritative | world_api (simulation) | SDF shader uniforms, fire system | `worlds.environment` |
| `GameplayState`, `PlayerState` | authoritative | world_api | web UI, simulation | `worlds.gameplay`, `players` |
| `WorldRegion`, `RegionRevision` | authoritative | world_api | region manager, scheduler | `world_regions`, `region_revisions` |
| `WorldEntity`, `EntityState` | authoritative | region generator, actions, systems | instance buffer builder, capture, simulation | `entities` |
| `GeometryDefinition`, `GeometryNode`, `MaterialDefinition` | authoritative | archetype library, interpreter (validated) | AST→GLSL compiler, CPU evaluator | `geometry_definitions` |
| `WorldCommand` | request | web command UI | world_api | `commands` |
| `ParsedIntent` | advisory | interpreter | action validator | `commands.intent` |
| `ResolvedTarget`, `ActionPreconditions`, `ValidatedAction` | derived per command | action validator | `MutationResult`, UI feedback | inside `commands.result` |
| `WorldEvent` | authoritative, append-only | actions, systems | web (event feed), audit, replay | `world_events` |
| `MutationResult` | response | world_api | web | `commands.result` |
| `DirtyRegion` | derived | mutation transaction | region manager, scheduler | implied by `region_revisions` |
| `NeuralGenerationJob`, `NeuralMetrics` | derived | scheduler / worker | web overlay, capture provider | `neural_jobs` |
| `ViewBundleManifest`, `CaptureView` | derived | view provider (browser) | optimizer | not persisted (job temp dir) |
| `NeuralAssetManifest` | derived | worker publish step | region manager, Spark loader | `neural_assets` |
| `RenderCacheEntry` | derived, client only | web renderers | diagnostics | memory |
| `RegionSnapshot` | response | world_api | region manager | assembled from tables |
| `HealthReport`, `GpuInfo`, `ApiError` | transport | companion | web status indicator | — |

## Semantics worth stating once

- **Entity ids** are stable strings: generated `e:{rx}:{rz}:{feature}:{index}`, player-built
  `p:{uuid}`. An entity is never renumbered or reused; removal sets `condition = removed`.
- **Geometry parameters:** `WorldEntity.geometry_params` binds values for the archetype's
  `ParamSpec`s. Reserved names set by the simulation: `burn` (0..1), `integrity` (0..1). Values outside
  the declared range are clamped by the instance-buffer builder and rejected by world_api.
- **`MutationResult.entities`** always carries the full post-commit entity, so clients replace rather
  than patch and cannot drift.
- **`WorldEvent.payload`** carries `{before, after}` state fragments for the listed entities; replaying
  events over a snapshot must reproduce the state tables (tested).
- **Asset validity** is a comparison, not a flag: an asset is current iff
  `NeuralAssetManifest.region_revision == WorldRegion.revision`.
- **Capture depth** is linear distance along the camera −Z axis in meters, float32, `0` meaning no hit.
- **API surface (v1):** `GET /health` · `POST /worlds` · `GET /worlds` · `GET /worlds/{id}` ·
  `POST /worlds/{id}/regions:batch` · `POST /worlds/{id}/commands` ·
  `GET /worlds/{id}/events?after_revision=` · `PUT /worlds/{id}/player` ·
  `GET /jobs?world_id=` · `GET /jobs/next-capture` · `POST /jobs/{id}/views` ·
  `POST /worlds/{id}/regions/{rx}/{rz}/regenerate` · `GET /assets/{key}`.

## Verified in Chunk 0

Run in a throwaway virtualenv (Python 3.15.0, Pydantic 2.14.3): a parameterised conifer archetype
validates; five malformed trees (unknown op, out-of-range value, unknown parameter reference, wrong
arity, unexpected parameter) are rejected with a path-qualified message; JSON Schema export succeeds
and `--check` reports up to date. No other contract behaviour has been exercised yet.
