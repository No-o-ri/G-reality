# G-reality

A persistent, explorable 3D universe rendered by two representations of one world state: a
ray-marched mathematical (SDF) near field and an optimization-based 3D Gaussian splat far field,
with natural-language world creation, stateful interactions and Neon PostgreSQL persistence.

Built at SanD Hacks 2026.

## Status

**Chunk 0 (architecture and contracts) — design only.** No renderer, simulation, database or neural
code exists yet. What is in the repository today:

- `docs/architecture/ARCHITECTURE.md` — system design and decisions
- `docs/feasibility/FEASIBILITY.md` — what was verified, what is an estimate
- `docs/contracts/CONTRACTS.md` — contract rules and ownership
- `docs/testing/TEST_PLAN.md` — test strategy
- `docs/PLAN.md` — milestones, ownership, risks, demo script
- `packages/contracts/` — canonical Pydantic contracts, SDF op table, generated JSON Schema
- `database/migrations/0001_init.sql` — draft schema (not applied)
