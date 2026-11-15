import math

import pytest

from greality_contracts import (
    Condition, EntityState, EnvironmentState, EventType, RegionCoord, Transform, WorldEntity,
)
from world_api.simulation import (
    CHARRED_CANOPY, IGNITION_DELAY_TICKS, MAX_BURNING, MAX_CATCHUP_TICKS,
    MAX_IGNITIONS_PER_TICK, FireRules, advance, burn_duration_ticks, burn_out, ignite, visual_burn,
)


def tree(key="tree", x=0.0, z=0.0):
    return WorldEntity(id=key, world_id="w-test", kind="tree", geometry_id="tree.conifer",
        transform=Transform(position=(x, 0, z)),
        region=RegionCoord(rx=math.floor(x / 64), rz=math.floor(z / 64)),
        capabilities=["flammable"], state=EntityState(fuel=40, resources={"wood": 3}),
        geometry_params={"canopy": 1.0, "burn": 0.0}, bound_radius=6,
        revision=0, created_revision=0)


def dump(result):
    last, changes = result
    return last, [(tick, [(e.type, [t.model_dump() for t in e.entities], e.payload, e.player, e.message)
                         for e in effects]) for tick, effects in changes]


def test_determinism_and_input_order():
    burning = [ignite(tree("source"), 0), ignite(tree("other", -2), 0)]
    candidates = [tree(str(i), i - 5) for i in range(12)]
    env = EnvironmentState()
    result = advance(0, 100, burning, candidates, env, 12)
    assert dump(result) == dump(advance(0, 100, burning, candidates, env, 12))
    assert dump(result) == dump(advance(0, 100, burning[::-1], candidates[::-1], env, 12))


@pytest.mark.parametrize("fuel,expected", [(0, 25), (40, 48), (1000, 240), (26.25, 32)])
def test_duration(fuel, expected):
    e = tree()
    e.state.fuel = fuel
    assert burn_duration_ticks(e) == expected


def test_burn_out_exact_tick_and_state():
    source = ignite(tree(), 7)
    duration = burn_duration_ticks(source)
    assert advance(7, 7 + duration - 1, [source], [], EnvironmentState(), 0)[1] == []
    _, changes = advance(7, 7 + duration, [source], [], EnvironmentState(), 0)
    assert len(changes) == 1
    tick, effects = changes[0]
    assert tick == 7 + duration
    assert len(effects) == 1
    effect = effects[0]
    assert effect.type == EventType.entity_burned_out
    assert effect.payload == {"ids": [source.id]}
    result = effect.entities[0]
    assert result.state.condition == Condition.charred
    assert result.geometry_params == {"burn": 1.0, "canopy": CHARRED_CANOPY}
    assert result.state.fuel == result.state.burn_intensity == result.state.hazard == result.state.hazard_radius == 0
    assert result.state.integrity == 0.25
    assert result.state.resources == {}


def test_visual_burn_and_helpers():
    e = tree()
    source = ignite(e, 10)
    assert source.state.hazard == 0.05
    assert source.state.hazard_radius == pytest.approx(5.6)
    assert source.state.burn_intensity == 0.35
    assert visual_burn(source, 10) == 0.2
    assert visual_burn(source, 34) == pytest.approx(0.6)
    assert visual_burn(source, 58) == 1.0
    assert visual_burn(source, 100) == 1.0
    assert visual_burn(burn_out(source), 10) == 1.0
    assert visual_burn(e, 10) == 0.0
    e.geometry_params = {"other": 2.0}
    assert visual_burn(e, 10) == 0.0
    assert burn_out(e).geometry_params == {"other": 2.0, "burn": 1.0}


def test_dense_grid_spreads_with_caps():
    source = ignite(tree("source"), 0)
    candidates = [tree(f"t:{x}:{z}", x * 2, z * 2) for x in range(-10, 11) for z in range(-10, 11)]
    _, changes = advance(0, 200, [source], candidates, EnvironmentState(humidity=0.1), 7)
    active = {source.id}
    total = 0
    for tick, effects in changes:
        ignitions = 0
        for effect in effects:
            if effect.type == EventType.entity_burned_out:
                active.difference_update(e.id for e in effect.entities)
            else:
                assert tick >= IGNITION_DELAY_TICKS
                ids = [e.id for e in effect.entities]
                assert not active.intersection(ids)
                assert len(ids) == len(set(ids))
                active.update(ids)
                ignitions += len(ids)
        assert len(active) <= MAX_BURNING
        assert ignitions <= MAX_IGNITIONS_PER_TICK
        total += ignitions
    assert total > MAX_BURNING  # Requires later generations of sources.


@pytest.mark.parametrize("humidity", [0.9, 0.95])
def test_humidity_prevents_spread(humidity):
    _, changes = advance(0, 100, [ignite(tree(), 0)], [tree("near", 1)],
                         EnvironmentState(humidity=humidity), 0, FireRules(base_chance_per_tick=100))
    assert all(e.type == EventType.entity_burned_out for _, effects in changes for e in effects)


def test_ineligible_candidates_and_distance():
    candidates = [tree(key, 1) for key in ("nonflammable", "charred", "removed", "empty")]
    candidates[0].capabilities = []
    candidates[1].state.condition = Condition.charred
    candidates[2].state.condition = Condition.removed
    candidates[3].state.fuel = 0
    candidates.append(tree("far", 30))
    _, changes = advance(0, 100, [ignite(tree(), 0)], candidates, EnvironmentState(humidity=0),
                         0, FireRules(base_chance_per_tick=100))
    assert all(e.type == EventType.entity_burned_out for _, effects in changes for e in effects)


def test_downwind_ignites_earlier_on_average():
    times = {"downwind": [], "upwind": []}
    for seed in range(40):
        # Separate trials prevent either neighbour igniting the other.
        for key, x in (("downwind", 4), ("upwind", -4)):
            _, changes = advance(0, 48, [ignite(tree(), 0)], [tree(key, x)],
                                 EnvironmentState(humidity=0, wind=(5, 0)), seed)
            times[key].append(next((tick for tick, effects in changes
                                    if any(e.type == EventType.fire_spread for e in effects)), 48))
    assert sum(times["downwind"]) / 40 < sum(times["upwind"]) / 40


def test_catchup_and_empty_interval():
    assert advance(0, 10_000, [], [], EnvironmentState(), 0) == (MAX_CATCHUP_TICKS, [])
    assert advance(10, 9, [], [], EnvironmentState(), 0) == (10, [])


def test_inputs_not_mutated_and_effects_are_independent():
    burning = [ignite(tree(), 0)]
    candidates = [tree("near", 1)]
    env = EnvironmentState(humidity=0)
    before = ([e.model_dump() for e in burning], [e.model_dump() for e in candidates], env.model_dump())
    ignite(candidates[0], 0)
    burn_out(burning[0])
    _, changes = advance(0, 100, burning, candidates, env, 0, FireRules(base_chance_per_tick=100))
    spread = next(e for _, effects in changes for e in effects if e.type == EventType.fire_spread)
    assert spread.entities[0].state.condition == Condition.burning
    assert spread.entities[0].geometry_params["burn"] == 0.2
    spread.entities[0].state.resources.clear()
    assert before == ([e.model_dump() for e in burning], [e.model_dump() for e in candidates], env.model_dump())


def test_burning_candidates_deduplicate_and_delay():
    source = ignite(tree("source", -0.1), 0)
    target = tree("target", 0.1)
    _, changes = advance(0, 60, [source], [source, target], EnvironmentState(humidity=0),
                         0, FireRules(base_chance_per_tick=100))
    spread = [(tick, e) for tick, effects in changes for e in effects if e.type == EventType.fire_spread]
    assert len(spread) == 1
    assert spread[0][0] == IGNITION_DELAY_TICKS
    assert spread[0][1].payload == {"sources": {"target": "source"}}
    assert dump(advance(0, 60, [], [source, target], EnvironmentState(humidity=0),
                        0, FireRules(base_chance_per_tick=100))) == dump((60, changes))


def test_caps_and_burnout_before_spread():
    burning = [ignite(tree(f"s:{i:02}"), 0) for i in range(MAX_BURNING)]
    burning[0] = ignite(tree("s:00"), -44)  # Burns out at tick 4.
    candidates = [tree(f"t:{i:02}", 1) for i in range(20)]
    _, changes = advance(3, 4, burning, candidates, EnvironmentState(humidity=0),
                         0, FireRules(base_chance_per_tick=100))
    assert [e.type for e in changes[0][1]] == [EventType.entity_burned_out, EventType.fire_spread]
    assert len(changes[0][1][1].entities) == 1
    _, changes = advance(3, 4, [ignite(tree(), 0)], candidates, EnvironmentState(humidity=0),
                         0, FireRules(base_chance_per_tick=100))
    assert len(changes[0][1][0].entities) == MAX_IGNITIONS_PER_TICK
