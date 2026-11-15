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

# Generator versions. A world keeps the version it was created with, and generation for that world always
# follows that version's rules, so stored regions, new regions and event replay stay consistent.
#   1: original (float lattice hash). Entity heights do not match the current terrain; hidden by the client.
#   2: exact integer lattice hash (Chunk 2).
#   3: buildings are kept clear of scattered objects, and discrete placements retry nearby when the first
#      spot is too steep or under water (Chunk 2 QA).
#   4: archetypes are generated per world (procgen.py) instead of taken from the fixed library, and trees get a
#      per-entity tilt (lean) in addition to yaw, scale and crown fullness (Chunk 4).
GENERATOR_VERSION = 4
MIN_RENDERABLE_GENERATOR_VERSION = 2
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


def _archetypes(spec: WorldSpec) -> dict:
    return {a.id: a for a in spec.archetypes}


def _lean_quat(yaw: float, lean: float, lean_dir: float) -> tuple[float, float, float, float]:
    """Yaw about +Y, then a small tilt `lean` (radians) about a horizontal axis at angle `lean_dir`."""
    ax, az = math.cos(lean_dir), math.sin(lean_dir)
    s, c = math.sin(lean * 0.5), math.cos(lean * 0.5)
    tx, ty, tz, tw = ax * s, 0.0, az * s, c  # tilt
    yx, yy, yz, yw = 0.0, math.sin(yaw * 0.5), 0.0, math.cos(yaw * 0.5)  # yaw
    # q = tilt * yaw
    return (tw * yx + tx * yw + ty * yz - tz * yy,
            tw * yy - tx * yz + ty * yw + tz * yx,
            tw * yz + tx * yy - ty * yx + tz * yw,
            tw * yw - tx * yx - ty * yy - tz * yz)


def _entity(world_id: str, seed: int, f: FeatureSpec, tag: str, x: float, h: float, z: float,
            spec: WorldSpec | None = None, version: int = 3) -> WorldEntity:
    coord = region_of(x, z)
    scale = f.scale_range[0] + (f.scale_range[1] - f.scale_range[0]) * stable_unit(seed, f.id, tag, "scale")
    yaw = 2.0 * math.pi * stable_unit(seed, f.id, tag, "yaw")
    rotation = _yaw_quat(yaw)
    if version >= 4 and f.entity_kind == "tree":
        lean = 0.11 * stable_unit(seed, f.id, tag, "lean") ** 1.5
        rotation = _lean_quat(yaw, lean, 2.0 * math.pi * stable_unit(seed, f.id, tag, "lean-dir"))
    params = {
        name: lo + (hi - lo) * stable_unit(seed, f.id, tag, name) for name, (lo, hi) in sorted(f.param_ranges.items())
    }
    archetype = (_archetypes(spec) if spec is not None else {}).get(f.geometry_id) or library().get(f.geometry_id)
    base_radius = archetype.bound_radius if archetype else 5.0
    return WorldEntity(
        id=f"e:{coord.rx}:{coord.rz}:{f.id}:{tag}",
        world_id=world_id,
        kind=f.entity_kind,
        geometry_id=f.geometry_id,
        geometry_params=params,
        transform=Transform(position=(x, h, z), rotation=rotation, scale=scale),
        region=coord,
        bound_radius=min(200.0, base_radius * scale),
        capabilities=list(f.capabilities),
        state=f.initial_state.model_copy(deep=True),
        revision=0,
        created_revision=0,
        origin="generated",
    )


# Rough horizontal extent as a fraction of an archetype's bounding radius (which is a tall sphere).
_DISCRETE_FOOTPRINT = 0.55
_SCATTER_FOOTPRINT = 0.3
_RETRIES = 12


def _discrete_spot(spec: WorldSpec, seed: int, f: FeatureSpec, i: int, version: int) -> tuple[float, float, float] | None:
    """Position of the i-th discrete placement of a feature, or None if no acceptable ground was found."""
    cx, cz = f.center or (0.0, 0.0)
    radius = f.radius or 40.0
    r = radius * math.sqrt((i + 0.5) / f.count) * 0.9
    a = i * 2.399963 + 2.0 * math.pi * stable_unit(seed, f.id, "spin")
    x, z = cx + r * math.cos(a), cz + r * math.sin(a)
    h = _accept(spec, seed, f, x, z)
    if h is not None or version < 3:
        return (x, h, z) if h is not None else None
    step = max(4.0, radius / max(1.0, math.sqrt(f.count)) * 0.5)
    for k in range(_RETRIES):
        ang = 2.0 * math.pi * stable_unit(seed, f.id, i, "retry-a", k)
        dist = step * (0.4 + 0.6 * stable_unit(seed, f.id, i, "retry-d", k)) * (1 + k // 4)
        nx, nz = x + dist * math.cos(ang), z + dist * math.sin(ang)
        h = _accept(spec, seed, f, nx, nz)
        if h is not None:
            return nx, h, nz
    return None


_DISCRETE_CACHE: dict[tuple, list[tuple[FeatureSpec, int, float, float, float]]] = {}


def discrete_placements(spec: WorldSpec, seed: int, version: int) -> list[tuple[FeatureSpec, int, float, float, float]]:
    """All discrete placements of a world as (feature, index, x, height, z). Deterministic; cached per world."""
    key = (seed, version, hash(spec.model_dump_json()))
    cached = _DISCRETE_CACHE.get(key)
    if cached is None:
        cached = []
        for f in spec.features:
            for i in range(f.count):
                spot = _discrete_spot(spec, seed, f, i, version)
                if spot is not None:
                    cached.append((f, i, *spot))
        if len(_DISCRETE_CACHE) > 64:
            _DISCRETE_CACHE.clear()
        _DISCRETE_CACHE[key] = cached
    return cached


def _bound(geometry_id: str, spec: WorldSpec | None = None) -> float:
    archetype = (_archetypes(spec) if spec is not None else {}).get(geometry_id) or library().get(geometry_id)
    return archetype.bound_radius if archetype else 5.0


def generate_region(world_id: str, spec: WorldSpec, seed: int, coord: RegionCoord,
                    generator_version: int = GENERATOR_VERSION) -> list[WorldEntity]:
    x0, z0, x1, z1 = region_bounds(coord)
    out: list[WorldEntity] = []
    placed = discrete_placements(spec, seed, generator_version)
    for f, i, x, h, z in placed:
        if x0 <= x < x1 and z0 <= z < z1:
            out.append(_entity(world_id, seed, f, f"n{i}", x, h, z, spec, generator_version))
    # From version 3, scattered objects keep clear of discrete ones (no trees growing through houses).
    keep_clear = [
        (x, z, _bound(f.geometry_id, spec) * _DISCRETE_FOOTPRINT * f.scale_range[1])
        for f, _, x, _, z in placed
        if x0 - 40.0 <= x < x1 + 40.0 and z0 - 40.0 <= z < z1 + 40.0
    ] if generator_version >= 3 else []
    for f in spec.features:
        if f.count > 0 or f.density_per_ha <= 0.0:
            continue
        own = _bound(f.geometry_id, spec) * _SCATTER_FOOTPRINT * f.scale_range[1]
        spacing = math.sqrt(10000.0 / f.density_per_ha)
        gx0, gx1 = math.floor(x0 / spacing), math.floor(x1 / spacing)
        gz0, gz1 = math.floor(z0 / spacing), math.floor(z1 / spacing)
        for gx in range(gx0, gx1 + 1):
            for gz in range(gz0, gz1 + 1):
                x = (gx + 0.15 + 0.7 * stable_unit(seed, f.id, gx, gz, "x")) * spacing
                z = (gz + 0.15 + 0.7 * stable_unit(seed, f.id, gx, gz, "z")) * spacing
                if not (x0 <= x < x1 and z0 <= z < z1):
                    continue
                if any(math.hypot(x - bx, z - bz) < br + own for bx, bz, br in keep_clear):
                    continue
                h = _accept(spec, seed, f, x, z)
                if h is not None:
                    out.append(_entity(world_id, seed, f, f"g{gx}_{gz}", x, h, z, spec, generator_version))
    out.sort(key=lambda e: e.id)
    return out[:MAX_ENTITIES_PER_REGION]
