"""Persistence tests against real Neon (dev branch). Every assertion reads back through a new connection."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from greality_contracts import (
    Aabb,
    JobState,
    NeuralMetrics,
    RegionCoord,
    WorldCommand,
    WorldEntity,
)
from world_api.config import ROOT
from world_api.regions import region_of

pytestmark = pytest.mark.neon


def fresh(settings):
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)


def spawn_regions(service, world):
    here = region_of(world.player.position[0], world.player.position[2])
    coords = [RegionCoord(rx=here.rx + dx, rz=here.rz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    return service.load_regions(world.world_id, coords)


def trees(service, world) -> list[WorldEntity]:
    out = [e for snap in spawn_regions(service, world) for e in snap.entities if e.kind == "tree"]
    assert len(out) >= 3, "test world should contain trees near spawn"
    return out


def command(world, text, entity: WorldEntity, offset=2.0, **kw) -> WorldCommand:
    p = entity.transform.position
    return WorldCommand(
        command_id=str(uuid.uuid4()), world_id=world.world_id, player_id="p1", text=text,
        target_hint=kw.pop("hint", entity.id), player_position=(p[0] + offset, p[1] + 1.7, p[2]),
        view_direction=(-1.0, 0.0, 0.0), **kw)


def test_migration_is_idempotent(settings, pool):
    from world_api.migrate import migrate

    assert migrate(settings.database_url_direct) == []
    with fresh(settings) as conn:
        tables = {r["tablename"] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert {"worlds", "world_regions", "entities", "world_events", "region_revisions", "commands",
            "neural_jobs", "neural_assets", "world_snapshots", "players", "geometry_definitions"} <= tables


def test_world_created_and_rows_exist(service, world, settings):
    with fresh(settings) as conn:
        w = conn.execute("SELECT * FROM worlds WHERE world_id=%s", (world.world_id,)).fetchone()
        ev = conn.execute("SELECT * FROM world_events WHERE world_id=%s", (world.world_id,)).fetchall()
    assert w["revision"] == 1 and w["seed"] == 1234
    assert [e["type"] for e in ev] == ["world_created"] and ev[0]["revision"] == 1
    assert service.get_world(world.world_id) == world


def test_region_materialization_is_deterministic_and_persistent(service, world):
    first = spawn_regions(service, world)
    second = spawn_regions(service, world)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]
    assert all(s.region.materialized and s.region.revision == 0 for s in first)
    assert sum(len(s.entities) for s in first) > 20


def test_ignite_commits_all_rows(service, world, settings):
    tree = trees(service, world)[0]
    result = service.handle_command(command(world, "Set this tree on fire", tree))
    assert result.status == "committed", result.message
    assert result.world_revision == 2
    assert [e.type.value for e in result.events] == ["entity_ignited"]
    assert result.entities[0].state.condition.value == "burning"
    assert result.dirty_regions and all(d.region_revision >= 1 for d in result.dirty_regions)

    with fresh(settings) as conn:
        w = conn.execute("SELECT revision FROM worlds WHERE world_id=%s", (world.world_id,)).fetchone()
        e = conn.execute("SELECT condition, state, revision, geometry_params FROM entities WHERE world_id=%s AND entity_id=%s",
                         (world.world_id, tree.id)).fetchone()
        ev = conn.execute("SELECT revision, type, cause, command_id, entity_ids FROM world_events WHERE world_id=%s ORDER BY revision",
                          (world.world_id,)).fetchall()
        rr = conn.execute("SELECT * FROM region_revisions WHERE world_id=%s", (world.world_id,)).fetchall()
        reg = conn.execute("SELECT revision FROM world_regions WHERE world_id=%s AND rx=%s AND rz=%s",
                           (world.world_id, tree.region.rx, tree.region.rz)).fetchone()
        cmd = conn.execute("SELECT status, world_revision FROM commands WHERE world_id=%s", (world.world_id,)).fetchall()
    assert w["revision"] == 2
    assert e["condition"] == "burning" and e["state"]["condition"] == "burning" and e["revision"] == 2
    assert e["geometry_params"]["burn"] > 0
    assert [(x["revision"], x["type"]) for x in ev] == [(1, "world_created"), (2, "entity_ignited")]
    assert ev[1]["cause"] == "player" and ev[1]["entity_ids"] == [tree.id]
    assert reg["revision"] == 1
    assert {(r["rx"], r["rz"]) for r in rr} >= {(tree.region.rx, tree.region.rz)}
    assert all(r["world_revision"] == 2 for r in rr)
    assert [(c["status"], c["world_revision"]) for c in cmd] == [("committed", 2)]


def test_state_survives_a_new_process(service, world):
    tree = trees(service, world)[0]
    assert service.handle_command(command(world, "set this tree on fire", tree)).status == "committed"
    code = (
        "import json,sys\n"
        "from world_api.config import get_settings\n"
        "from world_api.service import WorldService, make_pool\n"
        "from world_api.interpreter import RulesInterpreter\n"
        "from greality_contracts import RegionCoord\n"
        "s=WorldService(make_pool(get_settings().database_url, 2), RulesInterpreter())\n"
        f"w=s.get_world({world.world_id!r})\n"
        f"snap=s.load_regions(w.world_id,[RegionCoord(rx={tree.region.rx},rz={tree.region.rz})])[0]\n"
        f"e=[x for x in snap.entities if x.id=={tree.id!r}][0]\n"
        "ev=s.events(w.world_id)\n"
        "print(json.dumps({'rev':w.revision,'cond':e.state.condition.value,'erev':e.revision,"
        "'region_rev':snap.region.revision,'events':[(x.revision,x.type.value) for x in ev]}))\n"
    )
    env_path = ";".join(str(ROOT / p) for p in ("services", "packages/contracts/python"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         env={**__import__("os").environ, "PYTHONPATH": env_path})
    assert out.returncode == 0, out.stderr[-600:]
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data == {"rev": 2, "cond": "burning", "erev": 2, "region_rev": 1,
                    "events": [[1, "world_created"], [2, "entity_ignited"]]}


def test_rejections_change_nothing(service, world, settings):
    tree = trees(service, world)[0]
    far = service.handle_command(command(world, "set this tree on fire", tree, offset=30.0))
    assert far.status == "rejected" and far.failures[0].code == "out_of_range"
    nonsense = service.handle_command(command(world, "sing a song", tree))
    assert nonsense.status == "rejected" and nonsense.failures[0].code == "unknown_action"
    assert service.handle_command(command(world, "set this tree on fire", tree)).status == "committed"
    again = service.handle_command(command(world, "burn this tree", tree))
    assert again.status == "rejected" and again.failures[0].code == "invalid_state"
    with fresh(settings) as conn:
        w = conn.execute("SELECT revision FROM worlds WHERE world_id=%s", (world.world_id,)).fetchone()
        n = conn.execute("SELECT count(*) AS n FROM world_events WHERE world_id=%s", (world.world_id,)).fetchone()
        statuses = [r["status"] for r in conn.execute(
            "SELECT status FROM commands WHERE world_id=%s ORDER BY created_at", (world.world_id,))]
    assert w["revision"] == 2 and n["n"] == 2
    assert statuses == ["rejected", "rejected", "committed", "rejected"]


def test_failed_transaction_rolls_back_everything(service, world, settings):
    tree = trees(service, world)[0]

    def boom():
        raise RuntimeError("injected failure after entity write")

    service.fault_hook = boom
    result = service.handle_command(command(world, "set this tree on fire", tree))
    service.fault_hook = None
    assert result.status == "error" and result.world_revision == 1
    with fresh(settings) as conn:
        w = conn.execute("SELECT revision FROM worlds WHERE world_id=%s", (world.world_id,)).fetchone()
        e = conn.execute("SELECT condition, revision FROM entities WHERE world_id=%s AND entity_id=%s",
                         (world.world_id, tree.id)).fetchone()
        counts = conn.execute(
            """SELECT (SELECT count(*) FROM world_events WHERE world_id=%(w)s) AS events,
                      (SELECT count(*) FROM region_revisions WHERE world_id=%(w)s) AS region_revisions,
                      (SELECT count(*) FROM commands WHERE world_id=%(w)s) AS commands,
                      (SELECT coalesce(max(revision),0) FROM world_regions WHERE world_id=%(w)s) AS max_region_rev""",
            {"w": world.world_id}).fetchone()
    assert w["revision"] == 1
    assert (e["condition"], e["revision"]) == ("intact", 0)
    assert dict(counts) == {"events": 1, "region_revisions": 0, "commands": 0, "max_region_rev": 0}
    # the same command id can be retried and now commits
    assert service.handle_command(command(world, "set this tree on fire", tree)).status == "committed"


def test_idempotent_retry(service, world, settings):
    tree = trees(service, world)[0]
    cmd = command(world, "set this tree on fire", tree)
    first = service.handle_command(cmd)
    second = service.handle_command(cmd)
    assert first.status == "committed" and second.model_dump() == first.model_dump()
    with fresh(settings) as conn:
        n = conn.execute("SELECT count(*) AS n FROM world_events WHERE world_id=%s", (world.world_id,)).fetchone()
    assert n["n"] == 2


def test_concurrent_commands_get_consecutive_revisions(service, world, settings):
    targets = trees(service, world)[:4]
    results = []

    def run(t):
        results.append(service.handle_command(command(world, "set this tree on fire", t)))

    threads = [threading.Thread(target=run, args=(t,)) for t in targets]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r.status == "committed" for r in results)
    assert sorted(r.world_revision for r in results) == [2, 3, 4, 5]
    with fresh(settings) as conn:
        revs = [r["revision"] for r in conn.execute(
            "SELECT revision FROM world_events WHERE world_id=%s ORDER BY revision", (world.world_id,))]
    assert revs == [1, 2, 3, 4, 5]


def test_conflict_when_acting_on_outdated_state(service, world):
    tree = trees(service, world)[0]
    assert service.handle_command(command(world, "set this tree on fire", tree)).status == "committed"
    stale = service.handle_command(command(world, "set this tree on fire", tree, expected_world_revision=1))
    assert stale.status == "conflict"


def test_remove_transfers_resources_and_persists(service, world, settings):
    tree = trees(service, world)[0]
    result = service.handle_command(command(world, "chop down this tree", tree))
    assert result.status == "committed" and result.entities[0].state.condition.value == "removed"
    reloaded = service.get_world(world.world_id)
    assert reloaded.player.inventory.get("wood", 0) == tree.state.resources["wood"]
    gone = service.handle_command(command(world, "set this tree on fire", tree))
    assert gone.status == "rejected"


def test_replay_reproduces_entity_table(service, world):
    ts = trees(service, world)
    service.handle_command(command(world, "set this tree on fire", ts[0]))
    service.handle_command(command(world, "chop down this tree", ts[1]))
    service.handle_command(command(world, "put out the fire", ts[0]))
    table = service.entities(world.world_id)
    replayed = service.replay(world.world_id)
    assert set(table) == set(replayed)
    assert {k: v.model_dump() for k, v in table.items()} == {k: v.model_dump() for k, v in replayed.items()}


def _publish(service, job_id, tag):
    return service.publish_asset(
        job_id, uri=f"test/{tag}.ply", sha256=(tag * 64)[:64], byte_size=10, gaussian_count=1, fmt="ply",
        sh_degree=0, origin=(0.0, 0.0, 0.0), bounds=Aabb(min=(0, 0, 0), max=(64, 64, 64)),
        metrics=NeuralMetrics(rasterizer="torch", iterations=1, gaussian_count=1, capture_seconds=0, train_seconds=0))


def test_stale_job_cannot_publish_and_mutation_invalidates_asset(service, world, settings):
    tree = trees(service, world)[0]
    coord = tree.region
    # job A is built from region revision 0
    job_a = service.enqueue_job(world.world_id, coord)
    assert job_a.source_region_revision == 0 and job_a.state == JobState.queued
    assert service.enqueue_job(world.world_id, coord).job_id == job_a.job_id  # one live job per region
    assert service.set_job_state(job_a.job_id, JobState.training)

    # the world changes while A is "training": A is superseded and a new job is queued for revision 1
    assert service.handle_command(command(world, "set this tree on fire", tree)).status == "committed"
    assert not service.set_job_state(job_a.job_id, JobState.validating)
    status, manifest = _publish(service, job_a.job_id, "a")
    assert (status, manifest) == ("superseded", None)
    jobs = {j.job_id: j for j in service.jobs(world.world_id)}
    assert jobs[job_a.job_id].state == JobState.superseded
    job_b = next(j for j in jobs.values() if j.state == JobState.queued)
    assert job_b.source_region_revision == 1 and job_b.reason == "mutation"

    # B publishes for the current revision and becomes visible
    status, manifest = _publish(service, job_b.job_id, "b")
    assert status == "published" and manifest.region_revision == 1
    snap = service.load_regions(world.world_id, [coord])[0]
    assert snap.neural_asset is not None and snap.neural_asset.asset_id == manifest.asset_id
    assert snap.region.revision == snap.neural_asset.region_revision == 1

    # another mutation: the asset must disappear from the read path immediately, and a new job is queued
    other = next(t for t in trees(service, world) if t.region == coord and t.id != tree.id)
    res = service.handle_command(command(world, "chop down this tree", other))
    assert res.status == "committed"
    dirty = next(d for d in res.dirty_regions if d.coord == coord)
    assert dirty.stale_asset_id == manifest.asset_id and dirty.region_revision == 2
    snap = service.load_regions(world.world_id, [coord])[0]
    assert snap.neural_asset is None and snap.region.revision == 2
    assert snap.pending_job is not None and snap.pending_job.source_region_revision == 2
    with fresh(settings) as conn:
        states = [r["state"] for r in conn.execute(
            "SELECT state FROM neural_assets WHERE world_id=%s", (world.world_id,))]
    assert states == ["superseded"]
