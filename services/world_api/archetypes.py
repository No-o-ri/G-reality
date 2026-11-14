"""Built-in SDF archetype library. Every entry is a validated GeometryDefinition (data, not code).

Local space: origin at the base of the object, +Y up, meters. Reserved parameters driven by the
simulation: `burn` (0 intact .. 1 charred) and `canopy` (foliage radius, shrinks as a tree burns).
"""

from __future__ import annotations

from functools import lru_cache

from greality_contracts import GeometryDefinition

_BURN = {"default": 0.0, "min": 0.0, "max": 1.0}


def _mat(id_: str, rgb: tuple[float, float, float], rough: float = 0.85, variation: float = 0.15) -> dict:
    return {"id": id_, "base_color": rgb, "roughness": rough, "variation": variation, "variation_frequency": 2.0}


def _at(y: float, node: dict, x: float = 0.0, z: float = 0.0) -> dict:
    return {"op": "translate", "params": {"offset": [x, y, z]}, "children": [node]}


_RAW: list[dict] = [
    {
        "id": "tree.conifer",
        "version": 2,
        "bound_radius": 9.0,
        "parameters": {"canopy": {"default": 1.8, "min": 0.0, "max": 3.0}, "burn": _BURN},
        "materials": [_mat("bark", (0.24, 0.16, 0.10)), _mat("needles", (0.07, 0.22, 0.10), variation=0.3)],
        "root": {
            "op": "union", "params": {}, "children": [
                # trunk reaches 0.8 m below the base so it stays rooted on steep ground
                _at(1.6, {"op": "cone", "material": "bark",
                          "params": {"half_height": 2.4, "radius_bottom": 0.3, "radius_top": 0.14}}),
                _at(4.2, {"op": "cone", "material": "needles",
                          "params": {"half_height": 1.6, "radius_bottom": {"ref": "canopy"}, "radius_top": 0.25}}),
                _at(6.3, {"op": "cone", "material": "needles",
                          "params": {"half_height": 1.5, "radius_bottom": {"ref": "canopy"}, "radius_top": 0.0}}),
            ],
        },
    },
    {
        "id": "tree.broadleaf",
        "version": 2,
        "bound_radius": 8.0,
        "parameters": {"canopy": {"default": 2.4, "min": 0.0, "max": 4.0}, "burn": _BURN},
        "materials": [_mat("bark", (0.30, 0.22, 0.14)), _mat("leaves", (0.16, 0.36, 0.10), variation=0.35)],
        "root": {
            "op": "smooth_union", "params": {"k": 0.4}, "children": [
                {"op": "capsule", "material": "bark",
                 "params": {"a": [0, -0.8, 0], "b": [0, 3.4, 0], "radius": 0.26}},
                _at(4.6, {"op": "displace", "material": "leaves",
                          "params": {"amplitude": 0.35, "frequency": 1.3, "octaves": 3, "seed": 11},
                          "children": [{"op": "sphere", "params": {"radius": {"ref": "canopy"}}}]}),
            ],
        },
    },
    {
        "id": "rock.boulder",
        "bound_radius": 2.5,
        "parameters": {},
        "materials": [_mat("stone", (0.42, 0.41, 0.39), rough=0.95, variation=0.25)],
        "root": _at(0.5, {"op": "displace", "material": "stone",
                          "params": {"amplitude": 0.25, "frequency": 1.7, "octaves": 3, "seed": 5},
                          "children": [{"op": "ellipsoid", "params": {"radii": [1.2, 0.8, 1.0]}}]}),
    },
    {
        "id": "building.house",
        "version": 2,
        "bound_radius": 6.5,
        "parameters": {"burn": _BURN},
        "materials": [_mat("wall", (0.72, 0.66, 0.55), variation=0.08), _mat("roof", (0.45, 0.17, 0.12))],
        "root": {
            "op": "union", "params": {}, "children": [
                {"op": "subtract", "material": "wall", "params": {}, "children": [
                    # walls continue 2 m below the floor as a foundation, hidden by the terrain on level ground
                    _at(0.4, {"op": "box", "params": {"half": [2.6, 2.4, 2.0]}}),
                    _at(0.95, {"op": "box", "params": {"half": [0.5, 0.95, 0.3]}}, z=2.0),
                ]},
                _at(3.5, {"op": "intersect", "material": "roof", "params": {}, "children": [
                    {"op": "rotate", "params": {"quat": [0.0, 0.0, 0.3827, 0.9239]},
                     "children": [{"op": "box", "params": {"half": [2.2, 2.2, 2.3]}}]},
                    _at(1.1, {"op": "box", "params": {"half": [3.2, 1.8, 2.3]}}),
                ]}),
            ],
        },
    },
    {
        "id": "prop.campfire",
        "bound_radius": 1.2,
        "parameters": {"burn": _BURN},
        "materials": [_mat("stone", (0.36, 0.35, 0.34), rough=0.95), _mat("log", (0.22, 0.14, 0.08))],
        "root": {
            "op": "union", "params": {}, "children": [
                _at(0.12, {"op": "torus", "material": "stone", "params": {"major": 0.6, "minor": 0.14}}),
                {"op": "capsule", "material": "log", "params": {"a": [-0.35, 0.15, 0], "b": [0.35, 0.3, 0], "radius": 0.08}},
                {"op": "capsule", "material": "log", "params": {"a": [0, 0.15, -0.35], "b": [0, 0.3, 0.35], "radius": 0.08}},
            ],
        },
    },
]


@lru_cache(maxsize=1)
def library() -> dict[str, GeometryDefinition]:
    return {raw["id"]: GeometryDefinition.model_validate(raw) for raw in _RAW}


ARCHETYPE_IDS = tuple(raw["id"] for raw in _RAW)

# Default semantic kind, capabilities and initial simulated state per archetype.
ARCHETYPE_PROFILE: dict[str, dict] = {
    "tree.conifer": {"kind": "tree", "capabilities": ["flammable", "removable", "harvestable"],
                     "state": {"fuel": 30.0, "resources": {"wood": 3}}},
    "tree.broadleaf": {"kind": "tree", "capabilities": ["flammable", "removable", "harvestable"],
                       "state": {"fuel": 40.0, "resources": {"wood": 4}}},
    "rock.boulder": {"kind": "rock", "capabilities": ["removable"],
                     "state": {"blocks_path": True, "resources": {"stone": 2}}},
    "building.house": {"kind": "house", "capabilities": ["flammable"], "state": {"fuel": 90.0, "blocks_path": True}},
    "prop.campfire": {"kind": "campfire", "capabilities": ["flammable", "extinguishable", "removable"],
                      "state": {"fuel": 600.0}},
}
