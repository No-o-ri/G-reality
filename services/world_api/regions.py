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
# v5: count-placed features without a fixed centre (landmarks and settlements placed 'everywhere', 'lowlands' or
# 'highlands') recur on a grid of ROAMING_TILE_M tiles instead of all standing in one 40 m circle at the origin, so a
# world keeps revealing its landmarks however far the player walks. Worlds keep the version they were created with.
# v6: count-placed objects are laid out jointly with measured footprints: nothing overlaps (they spiral outward until
# they fit), nothing stands on the spawn point, grounded objects sit on the lowest ground under them (and spots that
# would bury them are rejected), floating objects clear the highest ground under them; roaming tiles stay inside
# their tile.
# v7: landmarks anchored at the spawn (placement 'near_spawn') also recur: the spawn cluster stays, and every other
# 480 m tile gets its own collision-free layout of the same stored geometry (at most MAX_TILE_OBJECTS per tile), so a
# world whose objects were all placed near the spawn does not turn empty a few hundred metres away. Shore-anchored
# features stay where they are.
GENERATOR_VERSION = 7
ROAMING_TILE_M = 480.0
MAX_TILE_OBJECTS = 24
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


# ---------------------------------------------------------------------------------------------- v6 layout

_SHAPE_CACHE: dict = {}
_SPAWN_CLEAR_M = 12.0
_GAP_M = 3.0


def _shape(spec: WorldSpec, geometry_id: str) -> tuple:
    """(horizontal footprint radius, lowest y, highest y) of an archetype in its local space, from its geometry."""
    from .geometry_bounds import bounds

    a = _archetypes(spec).get(geometry_id) or library().get(geometry_id)
    key = (geometry_id, a.model_dump_json() if a else "")
    hit = _SHAPE_CACHE.get(key)
    if hit is None:
        box = None
        if a is not None:
            box = bounds(a.root.model_dump(mode="json"), {k: v.model_dump() for k, v in (a.parameters or {}).items()})
        if box is None:
            r = a.bound_radius if a else 5.0
            hit = (r * _DISCRETE_FOOTPRINT, 0.0, r)
        else:
            (x0, y0, z0), (x1, y1, z1) = box
            hit = (max(abs(x0), abs(x1), abs(z0), abs(z1)), y0, y1)
        if len(_SHAPE_CACHE) > 512:
            _SHAPE_CACHE.clear()
        _SHAPE_CACHE[key] = hit
    return hit


def _site(spec: WorldSpec, seed: int, f: FeatureSpec, x: float, z: float, foot: float, y0: float, y1: float):
    """Base height for an object at (x, z), or None if the spot is unusable: the centre must pass the feature's
    height / slope rules; a grounded object sits on the lowest terrain under its footprint and is rejected where the
    ground would bury a large part of it; a floating object's base clears the highest terrain under it."""
    free = f.model_copy(update={"center": None, "radius": 0.0})
    if _accept(spec, seed, free, x, z) is None:
        return None
    r = foot * 0.6
    heights = [terrain_height(spec.terrain, seed, x + dx, z + dz)
               for dx, dz in ((0.0, 0.0), (r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r))]
    lo, hi = min(heights), max(heights)
    scale = f.scale_range[1]
    if y0 * scale > 1.0:  # floats: its lowest part must stay above the ground under it
        return hi
    if hi - lo > 0.25 * (y1 - y0) * scale + 1.5:
        return None
    return lo


def _layout(spec: WorldSpec, seed: int, items: list, spin_key: str, box=None) -> list:
    """Joint, deterministic placement of discrete objects. items = (feature, tag, centre, spread); they are placed
    largest first, each on a golden-angle spiral around its centre, at the first spot that keeps its footprint clear of
    every object already placed (and of the spawn point) and passes the terrain checks. `spread` widens the spiral (to
    distribute landmarks over a tile); `box` (x0, z0, x1, z1) keeps objects inside a roaming tile."""
    sx, _, sz = spec.spawn
    placed: list = []
    out = []
    sized = []
    for f, tag, centre, spread in items:
        foot, y0, y1 = _shape(spec, f.geometry_id)
        sized.append((foot * f.scale_range[1], f, tag, centre, spread, y0, y1))
    sized.sort(key=lambda it: (-it[0], it[1].id, it[2]))
    for size, f, tag, (cx, cz), spread, y0, y1 in sized:
        step = max(4.0, size * 1.3, spread)
        spin = 2.0 * math.pi * stable_unit(seed, spin_key, f.id, tag, "spin")
        for k in range(700):
            r = step * math.sqrt(k) * 0.9
            a = k * 2.399963 + spin
            x, z = cx + r * math.cos(a), cz + r * math.sin(a)
            if box is not None and not (box[0] + size <= x <= box[2] - size and box[1] + size <= z <= box[3] - size):
                if r > 2.0 * ROAMING_TILE_M:
                    break
                continue
            if math.hypot(x - sx, z - sz) < size + _SPAWN_CLEAR_M:
                continue
            if any(math.hypot(x - px, z - pz) < size + ps + _GAP_M for px, pz, ps in placed):
                continue
            h = _site(spec, seed, f, x, z, size, y0, y1)
            if h is None:
                continue
            placed.append((x, z, size))
            out.append((f, tag, x, h, z))
            break
    return out


def _discrete_v6(spec: WorldSpec, seed: int, version: int) -> list:
    key = ("v6", seed, version, hash(spec.model_dump_json()))
    hit = _DISCRETE_CACHE.get(key)
    if hit is None:
        items = [(f, f"n{i}", f.center or (0.0, 0.0), 0.0)
                 for f in spec.features if f.count > 0 and not _roaming(f, version, spec) for i in range(f.count)]
        hit = _layout(spec, seed, items, "discrete")
        if len(_DISCRETE_CACHE) > 64:
            _DISCRETE_CACHE.clear()
        _DISCRETE_CACHE[key] = hit
    return hit


def _tile_v6(spec: WorldSpec, seed: int, tx: int, tz: int, version: int) -> list:
    """All roaming features of one tile, laid out jointly and kept inside the tile. Landmarks spread over it,
    settlements stay a compact cluster; the home tile is centred on the origin."""
    t = ROAMING_TILE_M
    home = tx == 0 and tz == 0
    jitter = 0.0 if home else 0.15 * t
    cx = tx * t + jitter * (2.0 * stable_unit(seed, "tile-x", tx, tz) - 1.0)
    cz = tz * t + jitter * (2.0 * stable_unit(seed, "tile-z", tx, tz) - 1.0)
    if home and version >= 7:  # the home cluster stays around the spawn point
        cx, cz = spec.spawn[0], spec.spawn[2]
    roam = [f for f in spec.features if _roaming(f, version, spec)]
    total = sum(f.count for f in roam)
    # away from the spawn, bound the number of large objects per tile (rendering budget), keeping every kind present
    cap = 1.0 if home or version < 7 or total <= MAX_TILE_OBJECTS else MAX_TILE_OBJECTS / total
    counts = {f.id: max(1, round(f.count * cap)) for f in roam}
    n = sum(c for f in roam for c in [counts[f.id]] if f.kind != "settlement") or 1
    spread = (0.4 if version >= 7 else 0.35) * t / math.sqrt(n)
    items = [(f, f"t{tx}_{tz}_{i}", (cx, cz), 0.0 if f.kind == "settlement" else spread)
             for f in roam for i in range(counts[f.id])]
    return _layout(spec, seed, items, f"tile{tx}_{tz}",
                   (tx * t - 0.5 * t, tz * t - 0.5 * t, tx * t + 0.5 * t, tz * t + 0.5 * t))


def _roaming(f: FeatureSpec, version: int, spec: WorldSpec | None = None) -> bool:
    if version < 5 or f.count <= 0:
        return False
    if f.center is None:
        return True
    # v7: features placed around the spawn point recur too (their home-tile cluster stays at the spawn)
    return version >= 7 and spec is not None and math.hypot(f.center[0] - spec.spawn[0], f.center[1] - spec.spawn[2]) < 1.0


def _tile_spots(spec: WorldSpec, seed: int, f: FeatureSpec, tx: int, tz: int, version: int
                ) -> list[tuple[str, float, float, float]]:
    """Placements (tag, x, height, z) of a roaming feature in one tile. The home tile (0, 0) is centred on the origin
    and seeded like the single v4 cluster; other tiles get a jittered centre and their own seed. Settlements stay a
    40 m cluster (a village per tile), landmarks spread over most of the tile."""
    home = tx == 0 and tz == 0
    jitter = 0.0 if home else 0.25 * ROAMING_TILE_M
    cx = tx * ROAMING_TILE_M + jitter * (2.0 * stable_unit(seed, f.id, "tile-x", tx, tz) - 1.0)
    cz = tz * ROAMING_TILE_M + jitter * (2.0 * stable_unit(seed, f.id, "tile-z", tx, tz) - 1.0)
    radius = 40.0 if f.kind == "settlement" else 0.42 * ROAMING_TILE_M
    local = f.model_copy(update={"center": (cx, cz), "radius": radius,
                                 "id": f.id if home else f"{f.id}.t{tx}_{tz}"})
    out = []
    for i in range(f.count):
        spot = _discrete_spot(spec, seed, local, i, version)
        if spot is not None:
            out.append((f"t{tx}_{tz}_{i}", *spot))
    return out


def roaming_placements(spec: WorldSpec, seed: int, version: int, x0: float, z0: float, x1: float, z1: float
                       ) -> list[tuple[FeatureSpec, str, float, float, float]]:
    """Placements of roaming features from every tile that can reach the box (x0, z0)-(x1, z1). Deterministic."""
    out = []
    t = ROAMING_TILE_M
    if version >= 6:
        if not any(_roaming(f, version, spec) for f in spec.features):
            return out
        for tx in range(math.floor((x0 + 0.5 * t) / t) - 1, math.floor((x1 + 0.5 * t) / t) + 1):
            for tz in range(math.floor((z0 + 0.5 * t) / t) - 1, math.floor((z1 + 0.5 * t) / t) + 1):
                key = ("v6", seed, version, hash(spec.model_dump_json()), tx, tz)
                spots = _TILE_CACHE.get(key)
                if spots is None:
                    spots = _tile_v6(spec, seed, tx, tz, version)
                    if len(_TILE_CACHE) > 4096:
                        _TILE_CACHE.clear()
                    _TILE_CACHE[key] = spots
                out += spots
        return out
    for f in spec.features:
        if not _roaming(f, version):
            continue
        for tx in range(math.floor((x0 - t) / t), math.floor((x1 + t) / t) + 1):
            for tz in range(math.floor((z0 - t) / t), math.floor((z1 + t) / t) + 1):
                key = (seed, version, hash(spec.model_dump_json()), f.id, tx, tz)
                spots = _TILE_CACHE.get(key)
                if spots is None:
                    spots = _tile_spots(spec, seed, f, tx, tz, version)
                    if len(_TILE_CACHE) > 4096:
                        _TILE_CACHE.clear()
                    _TILE_CACHE[key] = spots
                out += [(f, tag, x, h, z) for tag, x, h, z in spots]
    return out


_TILE_CACHE: dict[tuple, list[tuple[str, float, float, float]]] = {}


def discrete_placements(spec: WorldSpec, seed: int, version: int) -> list[tuple[FeatureSpec, int, float, float, float]]:
    """All discrete placements of a world as (feature, index, x, height, z). Deterministic; cached per world.
    From v5, roaming features are excluded here (see roaming_placements)."""
    key = (seed, version, hash(spec.model_dump_json()))
    cached = _DISCRETE_CACHE.get(key)
    if cached is None:
        cached = []
        for f in spec.features:
            if _roaming(f, version, spec):
                continue
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
    if generator_version >= 6:
        placed = list(_discrete_v6(spec, seed, generator_version))
    else:
        placed = [(f, f"n{i}", x, h, z) for f, i, x, h, z in discrete_placements(spec, seed, generator_version)]
    placed += roaming_placements(spec, seed, generator_version, x0 - 40.0, z0 - 40.0, x1 + 40.0, z1 + 40.0)
    for f, tag, x, h, z in placed:
        if x0 <= x < x1 and z0 <= z < z1:
            out.append(_entity(world_id, seed, f, tag, x, h, z, spec, generator_version))
    # From version 3, scattered objects keep clear of discrete ones (no trees growing through houses).
    if generator_version >= 6:  # measured footprints, and a window wide enough for large landmarks
        keep_clear = [(x, z, _shape(spec, f.geometry_id)[0] * f.scale_range[1]) for f, _, x, _, z in placed
                      if x0 - 160.0 <= x < x1 + 160.0 and z0 - 160.0 <= z < z1 + 160.0]
    else:
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
