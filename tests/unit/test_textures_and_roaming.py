"""CC0 texture binding (no network: a fixed catalogue) and v5 roaming landmarks."""

from __future__ import annotations

import pytest

from greality_contracts import RegionCoord, TextureBinding
from world_api import textures
from world_api.regions import ROAMING_TILE_M, generate_region
from world_api.worldspec import FeatureDraft, MaterialDraft, ObjectDraft, WorldDraft, build_spec

CATALOG = {
    "rock_pitted_mossy": {"name": "Rock Pitted Mossy", "tags": ["rock", "moss"], "categories": ["natural", "rock"],
                          "dimensions": [1000, 1000], "authors": {"A": "All"}},
    "volcanic_rock_tiles": {"name": "Volcanic Rock Tiles", "tags": ["rock", "volcanic"], "categories": ["floor"],
                            "dimensions": [1800, 1800], "authors": {"B": "All"}},
    "rock_face_04": {"name": "Rock Face 04", "tags": ["rock", "cliff"], "categories": ["natural", "rock"],
                     "dimensions": [3000, 3000], "authors": {"C": "All"}},
    "dark_paneled_wood": {"name": "Dark Paneled Wood", "tags": ["wood", "dark"], "categories": ["wood"],
                          "dimensions": [2000, 2000], "authors": {"D": "All"}},
    "forest_ground_06": {"name": "Forest Ground 06", "tags": ["forest", "ground", "leaves"], "categories": ["natural"],
                         "dimensions": [2100, 2100], "authors": {"E": "All"}},
}


def test_search_prefers_natural_matches_and_rejects_adjective_only():
    assert textures.bind("mossy granite rock", CATALOG).asset_id == "polyhaven:rock_pitted_mossy"
    assert textures.bind("black volcanic rock", CATALOG).asset_id != "polyhaven:volcanic_rock_tiles"  # no laid tiles
    assert textures.bind("volcanic rock tiles", CATALOG).asset_id == "polyhaven:volcanic_rock_tiles"  # when asked
    # 'dark' alone does not pick dark wood; the generic rock class is used instead
    assert textures.bind("dark basalt", CATALOG).asset_id == "polyhaven:rock_face_04"
    assert textures.bind("glowing neon", CATALOG).asset_id is None
    b = textures.bind("forest floor", CATALOG)
    assert b.asset_id == "polyhaven:forest_ground_06" and b.scale_m == pytest.approx(2.1)
    assert b.license == "CC0-1.0" and b.page_url == "https://polyhaven.com/a/forest_ground_06" and b.authors == ["E"]


def test_binding_rejects_other_sources():
    with pytest.raises(ValueError):
        TextureBinding(query="x", asset_id="http://example.com/x.jpg")
    with pytest.raises(ValueError):
        TextureBinding(query="x", asset_id="polyhaven:x", license="CC-BY-4.0")


def _spec():
    draft = WorldDraft(title="t", relief_m=30, ground_texture_query="forest floor", cliff_texture_query="glass shine",
                       objects=[ObjectDraft(name="rock", geometry_json='{"op":"translate","params":{"offset":[0,1,0]},'
                                            '"children":[{"op":"sphere","params":{"radius":1}}]}', count=2,
                                            materials=[MaterialDraft(id="stone", base_color=[0.4, 0.4, 0.4],
                                                                     texture_query="mossy rock")])])
    return build_spec(draft, "p", 5)


def test_world_spec_keeps_texture_identity(monkeypatch):
    monkeypatch.setattr(textures, "catalog", lambda timeout=15.0: CATALOG)
    notes: list[str] = []
    spec = textures.resolve_spec(_spec(), notes)
    assert spec.terrain.ground_material.texture.asset_id == "polyhaven:forest_ground_06"
    assert spec.terrain.slope_material.texture is None  # no CC0 match: procedural surface
    assert spec.archetypes[0].materials[0].texture.asset_id == "polyhaven:rock_pitted_mossy"
    assert textures.asset_ids(spec) == ["polyhaven:forest_ground_06", "polyhaven:rock_pitted_mossy"]
    assert any("Forest Ground 06 (CC0, Poly Haven)" in n for n in notes)
    # the identity is part of the stored spec: a JSON round trip (Neon) keeps it exactly
    assert type(spec).model_validate_json(spec.model_dump_json()) == spec


def test_world_creation_never_blocks_on_texture_network(monkeypatch):
    def offline(timeout=15.0):
        raise TimeoutError("no network")
    monkeypatch.setattr(textures, "catalog", offline)
    notes: list[str] = []
    spec = textures.resolve_spec(_spec(), notes)
    assert textures.asset_ids(spec) == [] and spec.terrain.ground_material.texture is None
    assert any("procedural surfaces used" in n for n in notes)


def _landmark_spec():
    draft = WorldDraft(title="t", relief_m=10, features=[FeatureDraft(archetype="rock.boulder", count=6)])
    return build_spec(draft, "p", 11)


def _landmarks(spec, version, rx, rz):
    return [e for e in generate_region("w", spec, 11, RegionCoord(rx=rx, rz=rz), version) if ":n" in e.id or ":t" in e.id]


def test_v5_landmarks_recur_far_from_the_origin_and_v4_is_unchanged():
    spec = _landmark_spec()
    far = int(3 * ROAMING_TILE_M / 64)  # three tiles east
    v5 = [e for rx in range(far - 8, far + 8) for rz in range(-8, 8) for e in _landmarks(spec, 5, rx, rz)]
    v4 = [e for rx in range(far - 8, far + 8) for rz in range(-8, 8) for e in _landmarks(spec, 4, rx, rz)]
    assert v5 and not v4  # v4 keeps every landmark in one circle at the origin; v5 has them out here too
    assert len({e.id for e in v5}) == len(v5)
    assert _landmarks(spec, 5, far, 0) == _landmarks(spec, 5, far, 0)  # deterministic
    home4 = [e for rx in range(-2, 2) for rz in range(-2, 2) for e in _landmarks(spec, 4, rx, rz)]
    assert home4 and all(e.id.split(":")[-1].startswith("n") for e in home4)
