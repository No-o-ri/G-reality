import pytest

from greality_contracts import (
    ActionType, Capability, Condition, EntityState, EnvironmentState, EventType,
    PlayerState, RegionCoord, Transform, WorldEntity,
)
from world_api.actions import ACTIONS, ActionContext, evaluate


def context(action):
    entity = WorldEntity(id="e:0:0:f0:n0", world_id="world-test", kind="tree",
        geometry_id="tree.conifer", geometry_params={"canopy": 1.8, "burn": 0.0},
        transform=Transform(position=(1, 0, 0)), region=RegionCoord(rx=0, rz=0),
        bound_radius=2, capabilities=[Capability.flammable, Capability.removable, Capability.harvestable],
        state=EntityState(condition=Condition.burning if action == ActionType.extinguish else Condition.intact,
                          fuel=30, resources={"wood": 3}, blocks_path=True),
        revision=0, created_revision=0)
    return ActionContext(tick=7, environment=EnvironmentState(humidity=0.4),
        player=PlayerState(player_id="player-test", position=(0, 0, 0), inventory={"wood": 2}),
        player_position=(0, 0, 0), target=entity, distance_m=1)


@pytest.mark.parametrize("action", [ActionType.ignite, ActionType.extinguish, ActionType.remove, ActionType.inspect])
def test_evaluate_success_and_out_of_range(action):
    ctx = context(action)
    definition = ACTIONS[action]
    assert evaluate(definition, ctx) == (True, True, True, [])
    ctx.distance_m = definition.max_range_m + 1  # radius 2 adds one meter of reach
    assert evaluate(definition, ctx) == (True, True, True, [])
    ctx.distance_m += 0.001
    in_range, capable, state_ok, failures = evaluate(definition, ctx)
    assert (in_range, capable, state_ok) == (False, True, True)
    assert [f.code for f in failures] == ["out_of_range"]


@pytest.mark.parametrize("action", [ActionType.ignite, ActionType.remove])
def test_missing_capability(action):
    ctx = context(action)
    ctx.target.capabilities = []
    result = evaluate(ACTIONS[action], ctx)
    assert result[:3] == (True, False, True)
    assert [f.code for f in result[3]] == ["missing_capability"]


def test_ignite_already_burning():
    ctx = context(ActionType.ignite)
    ctx.target.state.condition = Condition.burning
    result = evaluate(ACTIONS[ActionType.ignite], ctx)
    assert result[:3] == (True, True, False)
    assert [f.code for f in result[3]] == ["invalid_state"]


@pytest.mark.parametrize("humidity,allowed", [(0.899, True), (0.9, False), (1.0, False)])
def test_ignite_humidity_boundary(humidity, allowed):
    ctx = context(ActionType.ignite)
    ctx.environment.humidity = humidity
    result = evaluate(ACTIONS[ActionType.ignite], ctx)
    assert result[:3] == (True, True, allowed)
    assert [f.code for f in result[3]] == ([] if allowed else ["environment"])


@pytest.mark.parametrize("action,event", [
    (ActionType.ignite, EventType.entity_ignited), (ActionType.extinguish, EventType.entity_extinguished),
    (ActionType.remove, EventType.entity_removed), (ActionType.inspect, EventType.resource_changed),
])
def test_apply_is_pure_and_has_expected_effect(action, event):
    ctx = context(action)
    before = ctx.target.model_dump()
    player_before = ctx.player.model_dump()
    effects = ACTIONS[action].apply(ctx)
    assert ctx.target.model_dump() == before
    assert ctx.player.model_dump() == player_before
    assert len(effects) == 1
    effect = effects[0]
    assert effect.type == event
    if action == ActionType.inspect:
        assert effect.entities == [] and effect.player is None
        assert "tree" in effect.message and "wood" in effect.message
        assert ACTIONS[action].mutates is False
        return
    changed = effect.entities[0]
    assert changed is not ctx.target and changed.state is not ctx.target.state
    if action == ActionType.ignite:
        assert changed.state.condition == Condition.burning
        assert changed.geometry_params["burn"] == 0.2
        assert changed.geometry_params["canopy"] == 1.8
        assert changed.state.burn_started_tick == 7
    elif action == ActionType.extinguish:
        assert changed.state.condition == Condition.intact
        assert changed.state.burn_intensity == changed.state.hazard == changed.state.hazard_radius == 0
    else:
        assert changed.state.condition == Condition.removed
        assert changed.state.resources == {}
        assert not changed.state.blocks_path
        assert effect.player is not ctx.player
        assert effect.player.inventory == {"wood": 5}
        assert effect.payload["gained"] == {"wood": 3}
