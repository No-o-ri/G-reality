# G-reality

A persistent, explorable 3D universe rendered by two representations of one world state: a
ray-marched mathematical (SDF) near field and an optimization-based 3D Gaussian splat far field,
with natural-language world creation (Gemini), stateful interactions and Neon PostgreSQL persistence.

Built at SanD Hacks 2026.

## Status

| Chunk | Scope | State |
|-------|-------|-------|
| 0 | Architecture, contracts, feasibility | done |
| 1 | Authoritative world state, Neon persistence, Gemini interpreter | implemented and tested (see `docs/testing/CHUNK1_RESULTS.md`) |
| 2–7 | SDF renderer, Gaussian pipeline, hybrid rendering, gameplay, sync, deployment | not started |

There is no renderer or neural pipeline yet. Known blocker for the neural work: Windows Smart App
Control blocks PyTorch on the development machine (`docs/feasibility/FEASIBILITY.md` §3).

## Run what exists

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env.local        # fill in DATABASE_URL, DATABASE_URL_DIRECT, GEMINI_API_KEY
$env:PYTHONPATH = "services;packages/contracts/python"
.venv\Scripts\python -m world_api.migrate          # apply SQL migrations
.venv\Scripts\python -m pytest tests               # 87 tests (Neon + one live Gemini call)
powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1   # local API on 127.0.0.1:8765
```

## Layout

- `docs/` — architecture, contracts, feasibility, test plan, implementation plan
- `packages/contracts/` — canonical Pydantic contracts, SDF op table, generated JSON Schema and TypeScript
- `services/world_api/` — world state, deterministic region generation, actions, mutation transaction, interpreter providers, HTTP API
- `database/migrations/` — numbered SQL migrations
- `tests/` — unit, persistence (real Neon), integration (HTTP, Gemini)
- `scripts/` — contract generation, companion start, manual verification, CUDA smoke test

## Acknowledgments

G-reality was built with AI-assisted development. See [CREDITS.md](CREDITS.md).
