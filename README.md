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
| 2 | Mathematical near-field SDF renderer (web app) | implemented and tested (see `docs/testing/CHUNK2_RESULTS.md`) |
| 3–7 | Gaussian pipeline, hybrid rendering, gameplay, sync, deployment | not started |

There is no Gaussian far field yet; the app says so in its status bar. CUDA PyTorch runs under WSL2 on
the development machine because Windows Smart App Control blocks it natively (`docs/feasibility/FEASIBILITY.md` §3).

## Run what exists

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env.local        # fill in DATABASE_URL, DATABASE_URL_DIRECT, GEMINI_API_KEY
$env:PYTHONPATH = "services;packages/contracts/python"
.venv\Scripts\python -m world_api.migrate          # apply SQL migrations
.venv\Scripts\python -m pytest tests               # 92 tests (Neon + one live Gemini call)
powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1   # local API on 127.0.0.1:8765, prints an "open:" link

cd apps\web
npm install
npm run dev          # then open the "open:" link (or http://localhost:5173/?sample=1 without the companion)
npm test             # 49 unit tests
npm run build; npm run visual   # automated visual check in Chrome on the GPU
```

## Known compatibility note

World generation rules are versioned per world. Worlds created before the Chunk 2 terrain change
(generator v1) keep their database rows but are hidden by the web app, because their entity heights were
computed with the old terrain function; generate a new world instead. See
`docs/testing/CHUNK2_RESULTS.md` and `docs/testing/KNOWN_ISSUES.md`.

## Layout

- `docs/` — architecture, contracts, feasibility, test plan, implementation plan
- `packages/contracts/` — canonical Pydantic contracts, SDF op table, generated JSON Schema and TypeScript
- `apps/web/` — SDF compiler, ray-march renderer, player controls, diagnostics, inspector
- `services/world_api/` — world state, deterministic region generation, actions, mutation transaction, interpreter providers, HTTP API
- `database/migrations/` — numbered SQL migrations
- `tests/` — unit, persistence (real Neon), integration (HTTP, Gemini)
- `scripts/` — contract generation, companion start, manual verification, CUDA smoke test

## Acknowledgments

G-reality was built with AI-assisted development. See [CREDITS.md](CREDITS.md).
