"""Deterministic region generation and region/bounds arithmetic.

`generate_region` is a pure function of (spec, seed, generator_version, rx, rz). Candidates live on a
world-global jittered grid, so an entity's identity and position never depend on which region asked.
"""

from __future__ import annotations

import math

from greality_contracts import (
    REGION_SIZE_M,
    Aabb,
    FeatureSpec,
    RegionCoord,
    Transform,
    WorldEntity,
    WorldSpec,
)

from .archetypes import library
from .terrain import stable_unit, terrain_height, terrain_slope

GENERATOR_VERSION = 1
MAX_ENTITIES_PER_REGION = 400


def region_of(x: float, z: float) -> RegionCoord:
    return RegionCoord(rx=math.floor(x / REGION_SIZE_M), rz=math.floor(z / REGION_SIZE_M))


def region_bounds(coord: RegionCoord) -> tuple[float, float, float, float]:
    x0, z0 = coord.rx * REGION_SIZE_M, coord.rz * REGION_SIZE_M
    return x0, z0, x0 + REGION_SIZE_M, z0 + REGION_SIZE_M


def sphere_aabb(center: tuple[float, float, float], radius: float) -> Aabb:
    x, y, z = center
    return Aabb(min=(x - radius, y - radius, z - radius), max=(x + radius, y + radius, z + radius))


def regions_touching(bounds: Aabb, margin: float = 2.0) -> list[RegionCoord]:
    """Regions whose XZ footprint intersects the box grown by `margin` (the capture overlap)."""
    rx0 = math.floor((bounds.min[0] - margin) / REGION_SIZE_M)
    rx1 = math.floor((bounds.max[0] + margin) / REGION_SIZE_M)
    rz0 = math.floor((bounds.min[2] - margin) / REGION_SIZE_M)
    rz1 = math.floor((bounds.max[2] + margin) / REGION_SIZE_M)
    return [RegionCoord(rx=rx, rz=rz) for rx in range(rx0, rx1 + 1) for rz in range(rz0, rz1 + 1)]


def _yaw_quat(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, math.sin(yaw * 0.5), 0.0, math.cos(yaw * 0.5))


def _accept(spec: WorldSpec, seed: int, f: FeatureSpec, x: float, z: float) -> float | None:
    if f.center is not None and f.radius > 0.0 and math.hypot(x - f.center[0], z - f.center[1]) > f.radius:
        return None
    h = terrain_height(spec.terrain, seed, x, z)
    if not f.min_height <= h <= f.max_height:
        return None
    if terrain_slope(spec.terrain, seed, x, z) > f.max_slope:
        return None
    return h


def _entity(world_id: str, seed: int, f: FeatureSpec, tag: str, x: float, h: float, z: float) -> WorldEntity:
    coord = region_of(x, z)
    scale = f.scale_range[0] + (f.scale_range[1] - f.scale_range[0]) * stable_unit(seed, f.id, tag, "scale")
    yaw = 2.0 * math.pi * stable_unit(seed, f.id, tag, "yaw")
    params = {
        name: lo + (hi - lo) * stable_unit(seed, f.id, tag, name) for name, (lo, hi) in sorted(f.param_ranges.items())
    }
    archetype = library().get(f.geometry_id)
    base_radius = archetype.bound_radius if archetype else 5.0
    return WorldEntity(
        id=f"e:{coord.rx}:{coord.rz}:{f.id}:{tag}",
        world_id=world_id,
        kind=f.entity_kind,
        geometry_id=f.geometry_id,
        geometry_params=params,
        transform=Transform(position=(x, h, z), rotation=_yaw_quat(yaw), scale=scale),
        region=coord,
        bound_radius=min(200.0, base_radius * scale),
        capabilities=list(f.capabilities),
        state=f.initial_state.model_copy(deep=True),
        revision=0,
        created_revision=0,
        origin="generated",
    )


def generate_region(world_id: str, spec: WorldSpec, seed: int, coord: RegionCoord) -> list[WorldEntity]:
    x0, z0, x1, z1 = region_bounds(coord)
    out: list[WorldEntity] = []
    for f in spec.features:
        if f.count > 0:
            # Discrete placements: golden-angle spiral around the feature center, jittered deterministically.
            cx, cz = f.center or (0.0, 0.0)
            radius = f.radius or 40.0
            for i in range(f.count):
                r = radius * math.sqrt((i + 0.5) / f.count) * 0.9
                a = i * 2.399963 + 2.0 * math.pi * stable_unit(seed, f.id, "spin")
                x, z = cx + r * math.cos(a), cz + r * math.sin(a)
                if not (x0 <= x < x1 and z0 <= z < z1):
                    continue
                h = _accept(spec, seed, f, x, z)
                if h is not None:
                    out.append(_entity(world_id, seed, f, f"n{i}", x, h, z))
            continue
        if f.density_per_ha <= 0.0:
            continue
        spacing = math.sqrt(10000.0 / f.density_per_ha)
        gx0, gx1 = math.floor(x0 / spacing), math.floor(x1 / spacing)
        gz0, gz1 = math.floor(z0 / spacing), math.floor(z1 / spacing)
        for gx in range(gx0, gx1 + 1):
            for gz in range(gz0, gz1 + 1):
                x = (gx + 0.15 + 0.7 * stable_unit(seed, f.id, gx, gz, "x")) * spacing
                z = (gz + 0.15 + 0.7 * stable_unit(seed, f.id, gx, gz, "z")) * spacing
                if not (x0 <= x < x1 and z0 <= z < z1):
                    continue
                h = _accept(spec, seed, f, x, z)
                if h is not None:
                    out.append(_entity(world_id, seed, f, f"g{gx}_{gz}", x, h, z))
    out.sort(key=lambda e: e.id)
    return out[:MAX_ENTITIES_PER_REGION]
