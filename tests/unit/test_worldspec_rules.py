import pytest

from greality_contracts import WorldSpec
from world_api.interpreter.base import CommandContext, IntentDraft
from world_api.interpreter.rules import RulesInterpreter
from world_api.worldspec import FeatureDraft, WorldDraft, build_spec


@pytest.mark.parametrize("prompt", ["mountain forest lake village", "flat desert", ""])
def test_rules_world_builds_valid_spec(prompt):
    draft = RulesInterpreter().world(prompt)
    assert isinstance(draft, WorldDraft)
    spec = build_spec(draft, prompt, 42)
    assert WorldSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.features and spec.archetypes
    if "lake" in prompt:
        assert spec.terrain.water_level is not None
        assert {f.entity_kind for f in spec.features} >= {"tree", "house", "rock"}


@pytest.mark.parametrize("high", [False, True])
def test_draft_numbers_clamped(high):
    value = 10000 if high else -10000
    draft = WorldDraft(title="clamp", relief_m=value, feature_size_m=value, ruggedness=value,
        has_lake=True, lake_radius_m=value, ground_color=(value, value, value),
        time_of_day_h=value, fog_density=value, humidity=value, wind_east_mps=value,
        wind_south_mps=value, temperature_c=value, features=[
            FeatureDraft(archetype="tree.conifer", density_per_hectare=value),
            FeatureDraft(archetype="building.house", count=10000),
        ])
    spec = build_spec(draft, "clamp", 42)
    assert WorldSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.terrain.amplitude == (150 if high else 0)
    assert spec.terrain.frequency == pytest.approx(1 / (800 if high else 40))
    assert spec.terrain.ridge == int(high)
    assert spec.terrain.basin_radius == pytest.approx((400 if high else 20) * 1.6)
    assert spec.terrain.ground_material.base_color == (float(high),) * 3
    assert spec.environment.time_of_day_h == (23.99 if high else 0)
    assert spec.environment.fog_density == (0.2 if high else 0)
    assert spec.environment.humidity == int(high)
    assert spec.environment.wind == ((20, 20) if high else (-20, -20))
    assert spec.environment.temperature_c == (60 if high else -60)
    # generator v4 splits a requested family over its generated variants; the total density is preserved
    first = [f for f in spec.features if f.id.startswith("f0v")]
    assert sum(f.density_per_ha for f in first) == pytest.approx(300 if high else 40)
    assert sum(f.count for f in spec.features if f.id.startswith("f1v")) == 40


@pytest.mark.parametrize("placement", ["lakeshore", "east_shore", "west_shore"])
def test_shore_without_lake_reports_fallback(placement):
    draft = WorldDraft(title="shore", relief_m=10, features=[
        FeatureDraft(archetype="building.house", placement=placement, count=1)])
    spec = build_spec(draft, "shore", 42)
    assert any("no lake" in note and placement in note for note in spec.interpretation_notes)
    assert spec.features[0].center == (0, 0)
    assert spec.features[0].radius == 60


def test_unsupported_items_reported():
    draft = RulesInterpreter().world("river castle animals bridge")
    for word in ("river", "castle", "animal", "bridge"):
        assert any(word in item for item in draft.unsupported)
        assert any(word in note and "not representable" in note
                   for note in build_spec(draft, "river castle animals bridge", 42).interpretation_notes)


@pytest.mark.parametrize("text,action,kind", [
    ("Set this tree on fire", "ignite", "tree"), ("put out the fire", "extinguish", None),
    ("chop down the tree", "remove", "tree"), ("look at that rock", "inspect", "rock"),
    ("dance", "unknown", None),
])
def test_rules_command(text, action, kind):
    intent = RulesInterpreter().command(text, CommandContext())
    assert isinstance(intent, IntentDraft)
    assert (intent.action, intent.target_kind) == (action, kind)
