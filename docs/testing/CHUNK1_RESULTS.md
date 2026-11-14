# Chunk 1 results — authoritative world state and Neon (2026-11-14)

All commands run from the repository root in the project virtualenv. Database tests ran against the
Neon branch **`dev`** (`br-lingering-paper-are1legn`) of project `sparkling-unit-65426005`;
`production` has not been touched (no schema, no data).

## Automated tests (executed)

```text
.venv\Scripts\python -m pytest tests
87 passed in 73.12s
```

| Suite | Tests | Needs | What it proves |
|-------|-------|-------|----------------|
| `tests/unit` (written by Codex, reviewed) | 63 | nothing | terrain determinism, region generation invariants incl. a second process, geometry validator rejections, draft → spec clamping, rules interpreter, action preconditions and purity |
| `tests/persistence` | 13 | Neon dev | migration idempotent; world rows; deterministic materialization; ignite writes entity + event + region revisions + command; state identical from a **new process**; rejections change nothing; injected failure rolls back everything; idempotent retry; 4 concurrent writers get revisions 2,3,4,5; conflict status; remove → inventory; event replay equals entity table; stale job cannot publish, mutation hides the asset and queues a new job |
| `tests/integration/test_api.py` | 3 | Neon dev | health; 401 without token; 403 for a foreign origin; command round trip over HTTP |
| `tests/integration/test_gemini.py` | 7 + 1 live | live test needs the Gemini key | recorded real response → valid spec; out-of-schema reply rejected; extra fields dropped; model fallback on 503/504; coded errors; bad key not retried; service falls back to rules; **live** world + four commands against the real API |

`npm run contracts:check` → schema up to date, generated TypeScript compiles under `--strict`.

## Measured (Neon us-west-2 from this machine, warm pool, rules interpreter)

| Operation | Result |
|-----------|--------|
| Committed `ignite` mutation, n = 8 | median 772 ms (765–782) |
| `get_world` (2 queries) | 138 ms |
| Materialize 3×3 regions (509 entities) | 2.72 s |
| Reload the same 3×3 regions | 1.69 s |
| Gemini command interpretation (`gemini-3.5-flash-lite`) | 0.8–1.7 s |
| Gemini world draft (`gemini-3.5-flash-lite`) | ~2 s; 46 s once when two larger models were overloaded |

A single round trip costs ~65–70 ms, so latency is proportional to statement count; the mutation is
11 statements. Region loading is 4 statements per region and should be batched before Chunk 4.

## Not done / not verified

- CUDA smoke test: **not run** — Smart App Control blocks PyTorch DLLs (docs/feasibility §3).
- Simulation ticks (fire spread, burn-out), `build` action: Chunk 5 by plan.
- `neon init` / `neon.ts` / `neon deploy` were not used: the schema is managed by numbered SQL
  migrations applied with `python -m world_api.migrate`; no policy file was created.
- Graphify: still blocked by the same Windows policy; no graph.

## Manual verification

See the Chunk 1 report in the session, or run:

```powershell
.venv\Scripts\python scripts\chunk1_demo.py create "a pine forest on rolling hills with boulders"
.venv\Scripts\python scripts\chunk1_demo.py ignite <WORLD_ID>
.venv\Scripts\python scripts\chunk1_demo.py show <WORLD_ID>     # new process = restart
```
