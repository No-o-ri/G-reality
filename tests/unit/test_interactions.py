"""Behavioral tests for declarative interactions, without persistence or randomness."""

import pytest

from greality_contracts import (
    Capability, Condition, EntityState, EventType, InteractionCondition, InteractionEffect,
    InteractionSpec, ObjectBehavior, PlayerState, RegionCoord, Transform, WorldEntity,
)
from world_api.interactions import InteractionError, available, check, perform
from world_api.simulation import ignite


def entity(name="actor", position=(0, 0, 0), kind="crystal", **kwargs):
    return WorldEntity(id=name, world_id="world", kind=kind, geometry_id="geometry",
                       transform=Transform(position=position), region=RegionCoord(rx=0, rz=0),
                       bound_radius=1, revision=0, created_revision=0, **kwargs)


def spec(*effects, **kwargs):
    return InteractionSpec(id="use", verb="touch", effects=[InteractionEffect(**e) for e in effects], **kwargs)


def run(interaction, actor=None, neighbours=None, behaviors=None, player=None):
    return perform(interaction, actor if actor is not None else entity(), behaviors or {},
                   neighbours or [], player or PlayerState(player_id="player", position=(0, 0, 0)), 42, 123)


@pytest.mark.parametrize("op,value,allowed", [
    ("<", 1, True), ("<=", 0, True), (">", 0, False), (">=", 0, True),
    ("==", 0, True), ("!=", 0, False), ("<", 0, False), ("<=", -1, False),
    (">", -1, True), (">=", 1, False), ("==", 1, False), ("!=", 1, True),
])
def test_missing_variable_comparisons(op, value, allowed):
    interaction = spec({"type": "emit"}, requires=[InteractionCondition(var="charge", op=op, value=value)])
    assert (check(interaction, entity()) == []) == allowed
    if not allowed:
        assert "charge" in check(interaction, entity())[0]
        with pytest.raises(InteractionError, match="charge"):
            run(interaction)


@pytest.mark.parametrize("op", ["==", "!=", "<", "<=", ">", ">="])
def test_condition_comparisons(op):
    interaction = spec({"type": "emit"}, requires=[
        InteractionCondition(condition="intact", op=op, var="ignored", value=99)])
    assert bool(check(interaction, entity())) == (op == "!=")
    charred = entity(state=EntityState(condition=Condition.charred))
    assert bool(check(interaction, charred)) == (op != "!=")


def test_available_preserves_order_and_filters():
    first = spec({"type": "emit"})
    hidden = first.model_copy(update={"id": "hidden", "player_initiated": False})
    locked = first.model_copy(update={"id": "locked", "requires": [InteractionCondition(var="charge", value=1)]})
    last = first.model_copy(update={"id": "last"})
    behavior = ObjectBehavior(interactions=[first, hidden, locked, last])
    assert available(None, entity()) == []
    assert available(behavior, entity()) == [first, last]
    assert available(behavior, entity(state=EntityState(vars={"charge": 1}))) == [first, locked, last]


def test_ordered_variables_parameters_and_clamps():
    interaction = spec(
        {"type": "set_var", "var": "charge", "value": 3},
        {"type": "add_var", "var": "charge", "value": 10, "max": 4},
        {"type": "add_var", "var": "new", "value": -9, "min": -2},
        {"type": "set_param", "param": "height", "value": 3},
        {"type": "scale_param", "param": "height", "value": 4, "max": 5},
        {"type": "scale_param", "param": "missing", "value": 0.1, "min": 0.3},
        {"type": "scale_param", "param": "zero", "value": 5})
    result, player, emits = run(interaction, entity(geometry_params={"zero": 0}))
    changed = result[0].entities[0]
    assert changed.state.vars == {"charge": 4, "new": -2}
    assert changed.geometry_params == {"height": 5, "missing": 0.3, "zero": 0}
    assert player is None and emits == []
    assert result[0].type == EventType.entity_interacted
    assert result[0].message == "You touch the crystal."


@pytest.mark.parametrize("effect_type", ["set_var", "add_var"])
def test_variable_limit_allows_updates_but_rejects_ninth(effect_type):
    actor = entity(state=EntityState(vars={f"v{i}": i for i in range(8)}))
    run(spec({"type": effect_type, "var": "v0", "value": 3}), actor)
    with pytest.raises(InteractionError, match="8 variables"):
        run(spec({"type": effect_type, "var": "ninth", "value": 1}), actor)
    assert actor.state.vars["v0"] == 0
    assert len(actor.state.vars) == 8


@pytest.mark.parametrize("name", [None, "", "Bad", "a-b", "x.y", "a\n", "eval()"])
@pytest.mark.parametrize("kind,field", [("set_var", "var"), ("add_var", "var"), ("give", "resource")])
def test_invalid_names(kind, field, name):
    with pytest.raises(InteractionError, match="name"):
        run(spec({"type": kind, field: name}))


def test_invalid_guard_name_and_unknown_effect():
    with pytest.raises(InteractionError, match="name"):
        check(spec({"type": "emit"}, requires=[InteractionCondition(var="Bad")]), entity())
    # The runtime remains closed even if a caller bypasses schema validation.
    interaction = spec({"type": "emit"})
    interaction.effects[0] = InteractionEffect.model_construct(type="execute")
    with pytest.raises(InteractionError, match="Unknown interaction effect"):
        run(interaction)


@pytest.mark.parametrize("condition,integrity,blocks", [("removed", 0, False), ("charred", 0.25, True)])
def test_condition_side_effects(condition, integrity, blocks):
    result, _, _ = run(spec({"type": "set_condition", "condition": condition}),
                       entity(state=EntityState(blocks_path=True)))
    state = result[0].entities[0].state
    assert state.condition == condition and state.integrity == integrity and state.blocks_path == blocks


def test_set_intact():
    result, _, _ = run(spec({"type": "set_condition", "condition": "intact"}),
                       entity(state=EntityState(condition=Condition.charred, integrity=0.25)))
    assert result[0].entities[0].state.condition == Condition.intact


@pytest.mark.parametrize("flammable", [False, True])
@pytest.mark.parametrize("condition", list(Condition))
def test_ignite_eligibility_and_shared_simulation(flammable, condition):
    actor = entity(capabilities=[Capability.flammable] if flammable else [],
                   state=EntityState(condition=condition))
    result, _, _ = run(spec({"type": "ignite"}), actor)
    if flammable and condition == Condition.intact:
        assert result[0].entities == [ignite(actor, 42)]
    else:
        assert result[0].entities == []
    assert actor.state.condition == condition


def test_visuals_inventory_and_no_entity_change():
    actor = entity(position=(1, 2, 3))
    player = PlayerState(player_id="player", position=(0, 0, 0), inventory={"spores": 2})
    interaction = spec({"type": "give", "resource": "spores", "amount": 1},
                       {"type": "give", "resource": "spores", "amount": 2},
                       {"type": "give", "resource": "wood", "amount": 0},
                       {"type": "emit", "particles": "sparks", "color": (1, 0.5, 0), "count": 3},
                       {"type": "emit", "particles": "dust", "count": 1}, message="Shiny!")
    result, updated, emits = run(interaction, actor, player=player)
    assert updated.inventory == {"spores": 5, "wood": 0}
    assert player.inventory == {"spores": 2}
    assert result[0].entities == [] and result[0].message == "Shiny!"
    assert emits == [
        {"entity_id": "actor", "particles": "sparks", "color": [1, 0.5, 0], "count": 3, "position": [1, 2, 3]},
        {"entity_id": "actor", "particles": "dust", "color": None, "count": 1, "position": [1, 2, 3]}]
    assert result[0].payload == {"interaction": "use", "verb": "touch", "emits": emits, "triggered": {}}


def test_crystal_resonance_and_depth_one():
    strike = spec({"type": "set_var", "var": "emission", "value": 2},
                  {"type": "emit", "particles": "sparks", "count": 20},
                  {"type": "trigger_nearby", "interaction": "resonate", "radius_m": 12, "max_targets": 3})
    strike.id, strike.verb = "strike", "strike"
    resonate = spec({"type": "add_var", "var": "emission", "value": 1},
                    {"type": "emit", "particles": "glow", "count": 2},
                    {"type": "give", "resource": "spores", "amount": 100},
                    {"type": "trigger_nearby", "interaction": "resonate", "radius_m": 30, "max_targets": 8},
                    player_initiated=False)
    resonate.id = "resonate"
    behavior = {"geometry": ObjectBehavior(interactions=[strike, resonate])}
    actor = entity()
    targets = [entity("c", (12, 0, 0)), entity("b", (1, 90, 0)), entity("a", (-1, 0, 0)),
               entity("extra", (11, 0, 0)), entity("outside", (12.01, 0, 0)),
               entity("rock", (0, 0, 0), kind="rock"),
               entity("removed", state=EntityState(condition=Condition.removed)), actor]
    before = [e.model_dump() for e in targets]
    first = run(strike, actor, targets, behavior)
    assert first == run(strike, actor, list(reversed(targets)), behavior)
    effects, player, emits = first
    assert player is None
    assert [e.id for e in effects[0].entities] == ["actor", "a", "b", "extra"]
    assert effects[0].entities[0].state.vars["emission"] == 2
    assert all(e.state.vars["emission"] == 1 for e in effects[0].entities[1:])
    assert effects[0].payload["triggered"] == {"a": "resonate", "b": "resonate", "extra": "resonate"}
    assert [e["entity_id"] for e in emits] == ["actor", "a", "b", "extra"]
    assert before == [e.model_dump() for e in targets]
    assert actor.state.vars == {}


def test_nearby_skips_failed_missing_and_removed_and_includes_radius_boundary():
    follow = spec({"type": "add_var", "var": "charge", "value": 1},
                  requires=[InteractionCondition(var="ready", value=1)])
    interaction = spec({"type": "trigger_nearby", "interaction": "use", "radius_m": 2,
                        "same_kind": False, "max_targets": 8})
    targets = [entity("fails", (0, 0, 0)), entity("ok", (2, 100, 0), kind="rock",
               state=EntityState(vars={"ready": 1})), entity("no_behavior"), entity("no_interaction"),
               entity("removed", state=EntityState(condition=Condition.removed, vars={"ready": 1})),
               entity("outside", (2.001, 0, 0), state=EntityState(vars={"ready": 1}))]
    targets[2].geometry_id = "missing"
    targets[3].geometry_id = "empty"
    result, _, _ = run(interaction, neighbours=targets,
                       behaviors={"geometry": ObjectBehavior(interactions=[follow]), "empty": ObjectBehavior()})
    assert [e.id for e in result[0].entities] == ["ok"]
    assert result[0].payload["triggered"] == {"ok": "use"}


def test_repeated_triggers_keep_latest_target_once_and_actor_first():
    trigger = {"type": "trigger_nearby", "interaction": "use", "radius_m": 1, "max_targets": 1}
    interaction = spec(trigger, {"type": "add_var", "var": "charge", "value": 1}, trigger)
    follow = spec({"type": "add_var", "var": "charge", "value": 1})
    result, _, _ = run(interaction, neighbours=[entity("target")],
                       behaviors={"geometry": ObjectBehavior(interactions=[follow])})
    assert [(e.id, e.state.vars["charge"]) for e in result[0].entities] == [("actor", 1), ("target", 2)]


def test_nearby_zero_targets_and_guard_failure_consumes_candidate_slot():
    follow = spec({"type": "add_var", "var": "charge", "value": 1},
                  requires=[InteractionCondition(var="ready", value=1)])
    targets = [entity("fails"), entity("passes", (1, 0, 0), state=EntityState(vars={"ready": 1}))]
    for limit in (0, 1):
        interaction = spec({"type": "trigger_nearby", "interaction": "use", "radius_m": 2, "max_targets": limit})
        result, _, _ = run(interaction, neighbours=targets,
                           behaviors={"geometry": ObjectBehavior(interactions=[follow])})
        assert result[0].entities == [] and result[0].payload["triggered"] == {}


def test_mushroom_harvest_and_input_isolation():
    harvest = spec({"type": "add_var", "var": "spores", "value": -1},
                   {"type": "scale_param", "param": "cap", "value": 0.8, "min": 0.3},
                   {"type": "give", "resource": "spores", "amount": 1},
                   {"type": "emit", "particles": "spores", "count": 10},
                   requires=[InteractionCondition(var="spores", value=1)])
    actor = entity(kind="mushroom", state=EntityState(vars={"spores": 1}), geometry_params={"cap": 0.35})
    player = PlayerState(player_id="player", position=(0, 0, 0), inventory={"spores": 4})
    behavior = {"geometry": ObjectBehavior(interactions=[harvest])}
    before = (actor.model_dump(), player.model_dump(), behavior["geometry"].model_dump())
    result, updated, emits = run(harvest, actor, behaviors=behavior, player=player)
    changed = result[0].entities[0]
    assert changed.state.vars == {"spores": 0} and changed.geometry_params == {"cap": 0.3}
    assert updated.inventory == {"spores": 5} and emits[0]["particles"] == "spores"
    assert (actor.model_dump(), player.model_dump(), behavior["geometry"].model_dump()) == before
    assert check(harvest, changed)
    assert (result, updated, emits) == run(harvest, actor, behaviors=behavior, player=player)
    changed.state.vars["spores"] = 99
    updated.inventory["spores"] = 99
    assert actor.state.vars["spores"] == 1 and player.inventory["spores"] == 4
