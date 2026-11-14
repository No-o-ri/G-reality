import pytest

from greality_contracts import MaterialDefinition, TerrainDefinition
from world_api.terrain import hash12, stable_unit, terrain_height, vnoise


@pytest.fixture
def terrain():
    material = MaterialDefinition(id="ground", base_color=(0.2, 0.3, 0.1))
    return TerrainDefinition(ground_material=material, slope_material=material)


@pytest.mark.parametrize("x,z", [(0, 0), (-1.25, 3.75), (64, -128), (0.999, 1.001), (3999, -3999)])
def test_noise_range(x, z):
    assert 0 <= hash12(x, z) < 1
    for seed in (0, 42, 2026):
        assert 0 <= vnoise(x, z, seed) < 1


def test_height_seed_determinism(terrain):
    points = [(12.5, -32.25), (0, 0), (75, 91)]
    heights = [terrain_height(terrain, 42, *p) for p in points]
    assert heights == [terrain_height(terrain, 42, *p) for p in points]
    assert heights != [terrain_height(terrain, 43, *p) for p in points]


@pytest.mark.parametrize("x,z", [(0, 0), (12.5, -32.25), (125, 250)])
def test_height_continuity(terrain, x, z):
    # A millimeter step should change this default rolling terrain by less than a centimeter.
    assert abs(terrain_height(terrain, 42, x, z) - terrain_height(terrain, 42, x + 0.001, z + 0.001)) < 0.01


def test_basin_lowers_center(terrain):
    basin = terrain.model_copy(update={"basin_center": (12.0, -8.0), "basin_radius": 40.0, "basin_depth": 15.0})
    assert terrain_height(basin, 42, 12, -8) == pytest.approx(terrain_height(terrain, 42, 12, -8) - 15)


def test_stable_unit_known_key():
    assert stable_unit(42, "forest", -1, 2, "x") == 0.525197650572972
    for key in ((), (0,), ("tree",), (42, "forest", -1, 2, "x")):
        assert 0 <= stable_unit(*key) < 1
