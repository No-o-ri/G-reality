# G-reality

Describe a world, then walk into it. G-reality turns a text description into a persistent 3D world whose terrain,
trees, rocks and buildings are **mathematical geometry**: signed-distance programs (a validated geometry AST)
ray-marched on your GPU. The world lives as server-authoritative state in PostgreSQL (Neon), so what you change is
still there next session.

Built at SanD Hacks 2026.

## Try it

- **In the browser, no install:** open the public website and press **Play the sample world**. It is a read-only
  snapshot of a generated world, rendered by the same engine.
- **Full experience on Windows:** install the companion with pip into its own Python environment and run `-m greality`, which works with Smart
  App Control. The ZIP with a double-click `G-reality.cmd` works only when Smart App Control is off. It needs your
  own PostgreSQL/Neon database. See [docs/INSTALL.md](docs/INSTALL.md).

| Feature | Public website | Windows companion |
|---------|----------------|-------------------|
| Explore the sample world (walk, swim, look) | yes | yes |
| Generate a new world from a description | — | yes: your PostgreSQL / Neon URL; Gemini key optional (keyword interpreter without one) |
| World-specific interactions (E), fire simulation | — | yes |
| Persistent revisions, reopen saved worlds | — | yes (your database) |
| Gaussian far field (experimental) | — | developer tools only; needs WSL2 + CUDA |

No credentials ship with either. The website loads only static files and never contacts a world server; the companion binds to `127.0.0.1` and
requires a per-launch pairing token.

## What is implemented

- **Language → geometry.** Gemini (or the offline keyword interpreter) designs a world spec; the server generates
  per-world procedural archetypes as geometry ASTs, validates them against the op table
  (`packages/contracts/schemas/geometry_ops.json`), and places entities deterministically per region.
- **Mathematical renderer.** The web app compiles each archetype to GLSL and ray-marches the near field, with
  terrain, water, sky, procedural grass, fire effects and post-processing (`apps/web/src/rendering`, `src/sdf`).
- **Persistence.** Every mutation runs in one transaction that assigns the next world revision
  (`services/world_api/service.py`, `database/migrations`). Player state is saved; worlds reopen from Neon.
- **Generated interactions and simulation.** Each world gets its own interaction set (E key); the server applies
  them and runs the environmental simulation (fire spread with distance, wind and humidity; burn-out; catch-up on
  reopen) on one database clock.
- **Gameplay.** Grounded player with gravity, jump, sprint, collision against the SDF geometry, wading and swimming.

### Experimental, not part of the default experience

- **Gaussian reconstruction** (`services/gpu`, `scripts/chunk3_reconstruct.py`): renders views of a region and fits
  3D Gaussians under WSL2/CUDA. It runs only when started by hand.
- **Neural / Gaussian far field:** reconstructed assets can be displayed in developer tools (`` ` `` → Gaussian
  diagnostic modes), but they are not integrated into normal rendering. The player view is the SDF renderer.

Known limitations are listed in [docs/testing/KNOWN_ISSUES.md](docs/testing/KNOWN_ISSUES.md).

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env.local        # fill DATABASE_URL, DATABASE_URL_DIRECT, GEMINI_API_KEY (never commit)
$env:PYTHONPATH = "services;packages/contracts/python"
.venv\Scripts\python -m world_api.migrate          # apply SQL migrations
.venv\Scripts\python -m pytest tests/unit          # fast unit tests (tests/ also has Neon + Gemini suites)
powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1   # API on 127.0.0.1:8765, prints an "open:" link

cd apps\web
npm install
npm run dev          # open the "open:" link, or http://localhost:5173/?sample=1 without the companion
npm test
npm run build
```

### Deployment

- **Website (Vercel):** import the repository with the project root at the repository root. `vercel.json` installs
  and builds `apps/web` (which imports `packages/contracts`) and publishes `apps/web/dist`. No environment variables
  are needed. Optional: `VITE_DOWNLOAD_URL` overrides the Windows download link.
- **Windows release:** `powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1` builds the web app,
  stages `release\G-reality-win64` and zips it. It fails if a secrets file is staged or if any value from your
  `.env.local` appears in the package. `scripts\test_release.ps1 -EnvFile .env.local` extracts the ZIP and runs a
  launch smoke test (migrations, Neon health, frontend, authenticated API). Then
  `.venv\Scripts\python scripts\build_wheel.py` packages the same staged app with the `greality` launcher
  (`packaging/greality`) as `release\greality-<version>-py3-none-any.whl` for `pip install` + `py -m greality`.

## Layout

- `apps/web/` — React/Three.js client: SDF compiler, ray-march renderer, player, HUD, landing page
- `services/world_api/` — FastAPI companion: world state, generation, interpreters, interactions, simulation
- `services/gpu/` — experimental Gaussian reconstruction (WSL2/CUDA)
- `packages/contracts/` — canonical Pydantic contracts, SDF op table, generated JSON Schema and TypeScript
- `database/migrations/` — numbered SQL migrations (applied automatically by the Windows launcher)
- `packaging/`, `scripts/build_release.ps1` — Windows release
- `docs/` — architecture, contracts, feasibility, install guide, test results

## Acknowledgments

G-reality was built with AI-assisted development. See [CREDITS.md](CREDITS.md).
