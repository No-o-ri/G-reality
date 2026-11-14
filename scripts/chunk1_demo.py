"""Manual verification of Chunk 1 against the Neon dev branch.

    .venv\\Scripts\\python scripts\\chunk1_demo.py create "a pine forest on rolling hills"   # prints WORLD_ID
    .venv\\Scripts\\python scripts\\chunk1_demo.py ignite WORLD_ID
    .venv\\Scripts\\python scripts\\chunk1_demo.py show   WORLD_ID      # run again in a new terminal = "restart"
    .venv\\Scripts\\python scripts\\chunk1_demo.py delete WORLD_ID

`create` uses the configured interpreter (Gemini when GREALITY_INTERPRETER=gemini); add --rules to force
the offline keyword interpreter. `show` reads everything back with plain SQL over a fresh connection, so
what it prints is what is stored in Neon, not process memory.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services"), str(ROOT / "packages" / "contracts" / "python")]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from greality_contracts import RegionCoord, WorldCommand  # noqa: E402
from world_api.config import get_settings  # noqa: E402
from world_api.interpreter import RulesInterpreter, make_interpreter  # noqa: E402
from world_api.regions import region_of  # noqa: E402
from world_api.service import WorldService, make_pool  # noqa: E402


def service(force_rules: bool = False) -> WorldService:
    settings = get_settings()
    interpreter = RulesInterpreter() if force_rules else make_interpreter(settings)
    return WorldService(make_pool(settings.database_url, 2), interpreter)


def around(world) -> list[RegionCoord]:
    here = region_of(world.player.position[0], world.player.position[2])
    return [RegionCoord(rx=here.rx + dx, rz=here.rz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]


def create(prompt: str, force_rules: bool) -> None:
    svc = service(force_rules)
    t = time.perf_counter()
    world = svc.create_world(prompt)
    print(f"created in {time.perf_counter() - t:.1f}s  WORLD_ID={world.world_id}")
    print(f"  title: {world.spec.title!r}  seed: {world.seed}  revision: {world.revision}")
    print(f"  notes: {world.spec.interpretation_notes}")
    print(f"  features: {[(f.geometry_id, f.density_per_ha or f.count) for f in world.spec.features]}")
    t = time.perf_counter()
    snaps = svc.load_regions(world.world_id, around(world))
    kinds: dict[str, int] = {}
    for s in snaps:
        for e in s.entities:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print(f"  materialized {len(snaps)} regions in {time.perf_counter() - t:.1f}s: {kinds}")


def ignite(world_id: str) -> None:
    svc = service()
    world = svc.get_world(world_id)
    candidates = [e for s in svc.load_regions(world_id, around(world)) for e in s.entities
                  if e.kind == "tree" and e.state.condition.value == "intact"]
    if not candidates:
        print("no intact tree near spawn")
        return
    tree = candidates[0]
    p = tree.transform.position

    def cmd(distance: float) -> WorldCommand:
        return WorldCommand(command_id=str(uuid.uuid4()), world_id=world_id, player_id="p1",
                            text="Set this tree on fire.", target_hint=tree.id,
                            player_position=(p[0] + distance, p[1] + 1.7, p[2]), view_direction=(-1.0, 0.0, 0.0),
                            expected_world_revision=world.revision)

    for label, distance in (("from 25 m away", 25.0), ("from 2 m away", 2.0)):
        t = time.perf_counter()
        r = svc.handle_command(cmd(distance))
        print(f"{label}: {r.status.upper()} in {(time.perf_counter() - t) * 1000:.0f} ms -> {r.message}")
        if r.action:
            print(f"   interpreter={r.action.intent.interpreter} action={r.action.intent.action.value} "
                  f"target={r.action.target.entity_id} distance={r.action.target.distance_m:.1f} m")
        if r.status == "committed":
            print(f"   world revision {world.revision} -> {r.world_revision}; events {[e.type.value for e in r.events]}")
            print(f"   dirty regions {[(d.coord.rx, d.coord.rz, 'rev', d.region_revision) for d in r.dirty_regions]}")


def show(world_id: str) -> None:
    url = get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as c:
        w = c.execute("SELECT world_id, name, seed, revision, status, updated_at FROM worlds WHERE world_id=%s",
                      (world_id,)).fetchone()
        if not w:
            print("world not found")
            return
        branch = c.execute("SELECT current_database() AS db, split_part(inet_server_addr()::text, '/', 1) AS addr").fetchone()
        print(f"[Neon] database={branch['db']}  world={w['world_id']} {w['name']!r} seed={w['seed']} "
              f"revision={w['revision']} status={w['status']}")
        print("events:")
        for e in c.execute("SELECT revision, type, cause, entity_ids, created_at FROM world_events WHERE world_id=%s ORDER BY revision",
                           (world_id,)):
            print(f"  rev {e['revision']:>3}  {e['type']:<18} {e['cause']:<10} {e['entity_ids']}  {e['created_at']:%H:%M:%S}")
        print("changed entities:")
        for e in c.execute("""SELECT entity_id, kind, condition, revision, geometry_params, rx, rz FROM entities
                              WHERE world_id=%s AND revision > 0 ORDER BY revision""", (world_id,)):
            print(f"  {e['entity_id']}  {e['kind']}  condition={e['condition']}  entity_rev={e['revision']}  "
                  f"params={e['geometry_params']}  region=({e['rx']},{e['rz']})")
        print("regions with revision > 0:")
        for r in c.execute("SELECT rx, rz, revision FROM world_regions WHERE world_id=%s AND revision > 0 ORDER BY rx, rz",
                           (world_id,)):
            print(f"  ({r['rx']},{r['rz']}) revision={r['revision']}")
        counts = c.execute(
            """SELECT (SELECT count(*) FROM entities WHERE world_id=%(w)s) AS entities,
                      (SELECT count(*) FROM world_regions WHERE world_id=%(w)s AND materialized) AS regions,
                      (SELECT count(*) FROM commands WHERE world_id=%(w)s) AS commands,
                      (SELECT count(*) FROM neural_jobs WHERE world_id=%(w)s) AS neural_jobs""", {"w": world_id}).fetchone()
        print(f"totals: {dict(counts)}")
        cmds = c.execute("SELECT status, text, world_revision FROM commands WHERE world_id=%s ORDER BY created_at", (world_id,)).fetchall()
        for x in cmds:
            print(f"  command {x['status']:<9} at world rev {x['world_revision']}: {x['text']!r}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2 or args[0] not in ("create", "ignite", "show", "delete"):
        print(__doc__)
        return 1
    if args[0] == "create":
        create(args[1], "--rules" in sys.argv)
    elif args[0] == "ignite":
        ignite(args[1])
    elif args[0] == "show":
        show(args[1])
    else:
        service(True).delete_world(args[1])
        print("deleted", args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
