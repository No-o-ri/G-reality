"""Generator rules are tied to the version a world was created with (Chunk 2 QA)."""

import math

from greality_contracts import RegionCoord
from world_api.archetypes import library
from world_api.regions import (
    GENERATOR_VERSION,
    MIN_RENDERABLE_GENERATOR_VERSION,
    discrete_placements,
    generate_region,
)
from world_api.terrain import terrain_height, terrain_slope
from world_api.worldspec import FeatureDraft, WorldDraft, build_spec

SEED = 20261114


def village_spec(relief=40.0, ruggedness=0.25):
    return build_spec(WorldDraft(title="t", relief_m=relief, ruggedness=ruggedness, has_lake=True, features=[
        FeatureDraft(archetype="tree.conifer", density_per_hectare=140),
        FeatureDraft(archetype="rock.boulder", density_per_hectare=30),
        FeatureDraft(archetype="building.house", count=8, placement="east_shore"),
    ]), "p", SEED)


def all_entities(spec, version):
    out = []
    for rx in range(-1, 5):
        for rz in range(-3, 3):
            out.extend(generate_region("w", spec, SEED, RegionCoord(rx=rx, rz=rz), version))
    return out


def test_versions_are_ordered():
    assert GENERATOR_VERSION >= 3 and MIN_RENDERABLE_GENERATOR_VERSION == 2


def test_default_version_is_current():
    spec = village_spec()
    coord = RegionCoord(rx=1, rz=0)
    assert generate_region("w", spec, SEED, coord) == generate_region("w", spec, SEED, coord, GENERATOR_VERSION)


def test_v3_keeps_scatter_clear_of_buildings_and_v2_does_not_change():
    spec = village_spec()
    v2, v3 = all_entities(spec, 2), all_entities(spec, 3)
    houses3 = [e for e in v3 if e.kind == "house"]
    assert len(houses3) >= 4
    house_extent = library()["building.house"].bound_radius * 0.55

    def crowding(entities):
        houses = [e for e in entities if e.kind == "house"]
        n = 0
        for e in entities:
            if e.kind == "house":
                continue
            for h in houses:
                d = math.hypot(e.transform.position[0] - h.transform.position[0],
                               e.transform.position[2] - h.transform.position[2])
                if d < house_extent * h.transform.scale:
                    n += 1
        return n

    assert crowding(v3) == 0
    assert crowding(v2) > 0, "version 2 must keep its original (uncleared) placement so stored worlds replay identically"
    # every version-2 house that passed the slope test is at the same spot in version 3
    spots2 = {e.id: e.transform.position for e in v2 if e.kind == "house"}
    spots3 = {e.id: e.transform.position for e in houses3}
    assert all(spots3.get(k) == v for k, v in spots2.items())
    assert len(spots3) >= len(spots2)


def test_buildings_stand_on_gentle_ground_and_foundation_covers_the_slope():
    spec = village_spec(relief=120.0, ruggedness=0.6)
    house = library()["building.house"]
    assert house.version == 2
    placed = [p for p in discrete_placements(spec, SEED, 3) if p[0].entity_kind == "house"]
    assert placed, "retries should find level ground even in hilly terrain"
    for f, _, x, h, z in placed:
        assert terrain_slope(spec.terrain, SEED, x, z) <= f.max_slope <= 0.25
        assert h == terrain_height(spec.terrain, SEED, x, z)
        # lowest ground under the footprint corners is above the bottom of the foundation (2 m below floor)
        corners = [terrain_height(spec.terrain, SEED, x + dx * 2.6 * 1.1, z + dz * 2.0 * 1.1)
                   for dx in (-1, 1) for dz in (-1, 1)]
        assert h - min(corners) < 2.0


def test_discrete_placements_are_deterministic_and_unique():
    spec = village_spec()
    a = [(f.id, i, x, h, z) for f, i, x, h, z in discrete_placements(spec, SEED, 3)]
    spec2 = village_spec()
    b = [(f.id, i, x, h, z) for f, i, x, h, z in discrete_placements(spec2, SEED, 3)]
    assert a == b and len({(fid, i) for fid, i, *_ in a}) == len(a)
    ids = [e.id for e in all_entities(spec, 3)]
    assert len(ids) == len(set(ids))
