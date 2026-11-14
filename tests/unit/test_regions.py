import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from greality_contracts import Aabb, RegionCoord
from world_api.regions import generate_region, region_bounds, region_of, regions_touching
from world_api.terrain import terrain_height
from world_api.worldspec import FeatureDraft, WorldDraft, build_spec


@pytest.fixture
def spec():
    return build_spec(WorldDraft(title="Fixture", relief_m=8, features=[
        FeatureDraft(archetype="tree.conifer", density_per_hectare=70),
        FeatureDraft(archetype="building.house", count=7, placement="near_spawn"),
    ]), "fixture", 42)


@pytest.mark.parametrize("x,z,expected", [(0, 0, (0, 0)), (63.999, 64, (0, 1)),
    (-0.001, -64, (-1, -1)), (-64.001, -128.001, (-2, -3)), (64, 128, (1, 2))])
def test_region_of_floors(x, z, expected):
    coord = region_of(x, z)
    assert (coord.rx, coord.rz) == expected


@pytest.mark.parametrize("margin", [0, 2])
def test_regions_touching_border(margin):
    box = Aabb(min=(63, -10, -1), max=(65, 10, 1))
    assert {(c.rx, c.rz) for c in regions_touching(box, margin)} == {(0, -1), (0, 0), (1, -1), (1, 0)}


@pytest.mark.parametrize("rx,rz", [(0, 0), (-1, -1), (1, 0)])
def test_generation_invariants(spec, rx, rz):
    coord = RegionCoord(rx=rx, rz=rz)
    entities = generate_region("world-test", spec, 42, coord)
    assert entities  # Keep containment and uniqueness assertions non-vacuous.
    assert entities == generate_region("world-test", spec, 42, coord)
    assert entities != generate_region("world-test", spec, 43, coord)
    ids = {e.id for e in entities}
    assert len(ids) == len(entities)
    adjacent = generate_region("world-test", spec, 42, RegionCoord(rx=rx + 1, rz=rz))
    assert adjacent
    assert ids.isdisjoint(e.id for e in adjacent)
    x0, z0, x1, z1 = region_bounds(coord)
    for entity in entities:
        x, y, z = entity.transform.position
        assert x0 <= x < x1 and z0 <= z < z1
        assert entity.region == coord == region_of(x, z)
        assert re.fullmatch(rf"e:{rx}:{rz}:f\d+:(?:n\d+|g-?\d+_-?\d+)", entity.id)
        assert y == pytest.approx(terrain_height(spec.terrain, 42, x, z), abs=1e-9)


def test_generation_matches_separate_process(spec):
    root = Path(__file__).resolve().parents[2]
    script = """
import hashlib, json, sys
from greality_contracts import RegionCoord, WorldSpec
from world_api.regions import generate_region
spec = WorldSpec.model_validate_json(sys.stdin.read())
data = [e.model_dump(mode='json') for e in generate_region('world-test', spec, 42, RegionCoord(rx=-1, rz=0))]
print(hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
"""
    entities = generate_region("world-test", spec, 42, RegionCoord(rx=-1, rz=0))
    assert entities
    dump = json.dumps([e.model_dump(mode="json") for e in entities], sort_keys=True, separators=(",", ":"))
    env = dict(os.environ, PYTHONHASHSEED="137", PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = os.pathsep.join(str(root / p) for p in ("services", "packages/contracts/python"))
    result = subprocess.run([sys.executable, "-c", script], input=spec.model_dump_json(),
                            text=True, capture_output=True, check=True, cwd=root, env=env, timeout=30)
    assert result.stdout.strip() == hashlib.sha256(dump.encode()).hexdigest()
