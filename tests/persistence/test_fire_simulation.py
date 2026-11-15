"""Authoritative fire on Neon: a direct ignite command, wall-clock driven simulation, persistence, catch-up, replay."""

from __future__ import annotations

import uuid

import pytest
from greality_contracts import ActionType, Condition, EventType, WorldCommand

from world_api import simulation

pytestmark = pytest.mark.neon


def _set_clock(service, world_id, seconds_ago: float) -> None:
    """Pretend the world's clock was last advanced `seconds_ago` (the simulation then catches up that much)."""
    with service.pool.connection() as conn:
        conn.execute("UPDATE worlds SET sim_clock_at = now() - make_interval(secs => %s) WHERE world_id=%s",
                     (seconds_ago, world_id))


def _ignite(service, world, tree):
    p = tree.transform.position
    return service.handle_command(WorldCommand(
        command_id=str(uuid.uuid4()), world_id=world.world_id, player_id="p1", text="Burn it",
        action=ActionType.ignite, target_hint=tree.id, player_position=(p[0] + 2.0, p[1] + 0.7, p[2]),
        view_direction=(-1.0, 0.0, 0.0)))


def test_direct_ignite_skips_interpreter_and_fire_runs_to_aftermath(service, world):
    from world_api.regions import region_of

    here = region_of(world.player.position[0], world.player.position[2])
    coords = [type(here)(rx=here.rx + dx, rz=here.rz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    trees = [e for s in service.load_regions(world.world_id, coords) for e in s.entities if e.kind == "tree"]
    assert len(trees) >= 5
    tree = trees[0]
    _set_clock(service, world.world_id, 0)

    result = _ignite(service, world, tree)
    assert result.status == "committed", result.message
    assert result.action.intent.interpreter == "direct"
    ignited = next(e for e in result.entities if e.id == tree.id)
    assert ignited.state.condition == Condition.burning
    start_tick = ignited.state.burn_started_tick
    duration = simulation.burn_duration_ticks(ignited)

    # 20 s later the clock has moved, the tree is still burning
    _set_clock(service, world.world_id, 20)
    tick, rev = service.advance_simulation(world.world_id)
    assert tick >= start_tick + 20
    assert service.entities(world.world_id)[tree.id].state.condition == Condition.burning

    # the world is "closed" for 15 minutes: catch-up is bounded but long enough for the fire to finish
    _set_clock(service, world.world_id, 15 * 60)
    sync = service.sync(world.world_id, rev)
    stored = service.entities(world.world_id)
    assert stored[tree.id].state.condition == Condition.charred
    assert stored[tree.id].geometry_params["burn"] == 1.0
    assert sync.tick >= start_tick + duration
    types = {e.type for e in sync.events}
    assert EventType.entity_burned_out in types
    assert all(e.cause == "simulation" for e in sync.events)
    # nothing is still burning beyond the cap, and every event bumped the world revision by one
    assert [e.revision for e in sync.events] == list(range(rev + 1, rev + 1 + len(sync.events)))
    assert sum(1 for e in stored.values() if e.state.condition == Condition.burning) <= simulation.MAX_BURNING

    # authoritative history: generator output + event log reproduces the stored table (fire included)
    replayed = service.replay(world.world_id)
    assert {k: v.model_dump() for k, v in stored.items()} == {k: v.model_dump() for k, v in replayed.items()}

    # a charred tree cannot be ignited again
    again = _ignite(service, world, stored[tree.id])
    assert again.status == "rejected"


def test_clock_does_not_move_backwards_or_double_count(service, world):
    _set_clock(service, world.world_id, 5)
    t1, _ = service.advance_simulation(world.world_id)
    t2, _ = service.advance_simulation(world.world_id)
    assert t2 == t1  # second call within the same second adds nothing
    later = service.get_world(world.world_id)
    assert later.gameplay.tick == t1
