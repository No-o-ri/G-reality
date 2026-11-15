"""WorldDraft (the small, flat structure a language model is allowed to produce) → validated WorldSpec.

The draft only selects parameters and library archetypes. `build_spec` clamps every number into the
contract ranges and constructs the authoritative WorldSpec; nothing in a draft is ever executed.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from greality_contracts import (
    EnvironmentState,
    EntityState,
    FeatureSpec,
    GeometryDefinition,
    MaterialDefinition,
    TerrainDefinition,
    WorldSpec,
)

from . import procgen
from .geometry_normalize import NormalizeError, normalize_graph
from .archetypes import ARCHETYPE_PROFILE, library
from .terrain import terrain_height

Placement = Literal["everywhere", "lowlands", "highlands", "lakeshore", "east_shore", "west_shore", "near_spawn"]
ArchetypeId = Literal["tree.conifer", "tree.broadleaf", "rock.boulder", "building.house", "prop.campfire"]


class FeatureDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    archetype: ArchetypeId
    placement: Placement = "everywhere"
    density_per_hectare: float = Field(default=0.0, description="for scattered things like trees and rocks; 0-300")
    count: int = Field(default=0, description="for discrete things like houses; 0-40")


class MaterialDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(description="short identifier referenced by geometry nodes' material field")
    base_color: list[float] = Field(description="exactly three numbers: linear RGB 0-1")
    roughness: float = 0.8
    emissive_color: list[float] = Field(default=[0.0, 0.0, 0.0], description="three numbers 0-1; light the surface emits")
    emissive_strength: float = Field(default=0.0, description="0 = none, 1-8 = glowing")
    variation: float = Field(default=0.15, description="0-1 procedural tint noise")


class ParamDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(description="identifier used as {\"ref\": name} inside geometry_json numbers")
    min: float
    max: float


class ObjectDraft(BaseModel):
    """A new kind of object whose geometry the model constructs from the SDF operation language."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(description="what this object is, e.g. 'glowing mushroom' (free text)")
    kind: str = Field(default="object", description="one lowercase word for the player, e.g. mushroom, arch, tree")
    geometry_json: str = Field(description="JSON of ONE root geometry node tree (see the op table)")
    materials: list[MaterialDraft] = Field(default_factory=list)
    parameters: list[ParamDraft] = Field(default_factory=list,
                                         description="0-3 per-entity shape parameters, sampled uniformly per entity")
    size_m: float = Field(default=4.0, description="radius of a sphere around the base origin that contains the object")
    flammable: bool = False
    placement: Placement = "everywhere"
    density_per_hectare: float = Field(default=0.0, description="for scattered objects, 1-200")
    count: int = Field(default=0, description="for a fixed number of large objects, 1-40")
    scale_min: float = 0.8
    scale_max: float = 1.25


class WorldDraft(BaseModel):
    """Language-model facing schema. Keep it flat: no recursion, no free-form dictionaries."""

    model_config = ConfigDict(extra="ignore")
    title: str
    relief_m: float = Field(description="height difference between valleys and peaks in meters, 0-300")
    feature_size_m: float = Field(default=180.0, description="horizontal size of hills/mountains in meters, 40-800")
    ruggedness: float = Field(default=0.3, description="0 = smooth rolling hills, 1 = sharp ridged mountains")
    has_lake: bool = False
    lake_radius_m: float = Field(default=70.0, description="20-400")
    # A list, not a tuple: Gemini's response schema does not accept tuple (prefixItems) types.
    ground_color: list[float] = Field(default=[0.20, 0.32, 0.14], description="exactly three numbers: linear RGB, each 0-1")
    snow_peaks: bool = False
    time_of_day_h: float = Field(default=10.0, description="0-24")
    fog_density: float = Field(default=0.004, description="0 = clear, 0.02 = thick fog")
    humidity: float = Field(default=0.4, description="0 dry .. 1 saturated; affects how easily fire spreads")
    wind_east_mps: float = 1.0
    wind_south_mps: float = 0.0
    temperature_c: float = 18.0
    ambient_light: float = Field(default=0.25, description="0.03 = very dark night, 0.25 = day")
    features: list[FeatureDraft] = Field(default_factory=list)
    objects: list[ObjectDraft] = Field(default_factory=list, description="newly constructed object kinds")
    unsupported: list[str] = Field(default_factory=list, description="requested things that cannot be represented")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _color(rgb) -> tuple[float, float, float]:
    padded = (list(rgb) + [0.3, 0.3, 0.3])[:3]
    return tuple(_clamp(c, 0.0, 1.0) for c in padded)  # type: ignore[return-value]


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
    return (out or "object")[:24]


# GPU complexity budget for model-constructed geometry (on top of the language limits of 128 nodes / depth 12):
# every node is evaluated per ray-march step near the object, and noise ops are the expensive ones.
OBJECT_MAX_NODES = 72
OBJECT_MAX_NOISE_OPS = 3
OBJECT_MAX_OCTAVES = 3
SCENE_MAX_OBJECT_NODES = 360


def _enforce_budget(root) -> None:
    nodes = noise = 0
    stack = [root]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            raise ValueError("every node must be an object with an 'op'")
        nodes += 1
        if n.get("op") in ("displace", "warp"):
            noise += 1
            prm = n.get("params") or {}
            if isinstance(prm.get("octaves"), (int, float)):
                prm["octaves"] = int(min(OBJECT_MAX_OCTAVES, max(1, prm["octaves"])))
        stack.extend(n.get("children") or [])
    if nodes > OBJECT_MAX_NODES:
        raise ValueError(f"{nodes} nodes exceeds the per-object GPU budget of {OBJECT_MAX_NODES}")
    if noise > OBJECT_MAX_NOISE_OPS:
        raise ValueError(f"{noise} displace/warp nodes exceeds the budget of {OBJECT_MAX_NOISE_OPS}")


def object_geometry(obj: ObjectDraft, index: int) -> GeometryDefinition:
    """Validate a model-constructed object: closed op set, numeric ranges, node/depth limits (raises ValueError)."""
    try:
        root = json.loads(obj.geometry_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"geometry_json is not valid JSON: {exc}") from None
    try:
        root = normalize_graph(root)  # synonyms, ranges, arity, reusable definitions ("defs" / "use")
    except NormalizeError as exc:
        raise ValueError(str(exc)) from None
    params = {}
    for prm in obj.parameters[:3]:
        lo, hi = sorted((float(prm.min), float(prm.max)))
        params[_slug(prm.name)] = {"default": (lo + hi) / 2, "min": lo, "max": hi}
    if obj.flammable:
        params["burn"] = {"default": 0.0, "min": 0.0, "max": 1.0}
    # names in refs must match the slugged parameter names
    def fix_refs(node):
        if isinstance(node, dict):
            if set(node) == {"ref"} and isinstance(node["ref"], str):
                return {"ref": _slug(node["ref"])}
            return {k: fix_refs(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fix_refs(v) for v in node]
        return node
    root = fix_refs(root)
    mats = []
    for m in obj.materials[:8]:
        e = _color(m.emissive_color) if m.emissive_strength > 0 else (0.0, 0.0, 0.0)
        mats.append({"id": _slug(m.id), "base_color": _color(m.base_color), "roughness": _clamp(m.roughness, 0, 1),
                     "emissive": e, "emissive_strength": _clamp(m.emissive_strength, 0.0, 12.0),
                     "variation": _clamp(m.variation, 0, 1), "variation_frequency": 1.5})
    if not mats:
        mats = [{"id": "surface", "base_color": (0.5, 0.5, 0.5), "roughness": 0.8}]
    known = {m["id"] for m in mats}

    def fix_materials(node):
        if isinstance(node, dict):
            out = {k: fix_materials(v) for k, v in node.items() if k != "material"}
            if node.get("material") is not None:
                mid = _slug(str(node["material"]))
                out["material"] = mid if mid in known else mats[0]["id"]
            return out
        if isinstance(node, list):
            return [fix_materials(v) for v in node]
        return node
    root = fix_materials(root)
    _enforce_budget(root)
    if isinstance(root, dict) and "material" not in root:
        root["material"] = mats[0]["id"]  # default for any primitive that does not name one
    return GeometryDefinition.model_validate({
        "id": f"obj.{_slug(obj.name)}.{index}", "version": 1,
        "bound_radius": _clamp(obj.size_m, 0.5, 120.0) * 1.15,
        "parameters": params, "materials": mats, "root": root,
    })


def _state_for(profile: dict, geom) -> dict:
    """Initial state; fuel of generated trees grows with their size (bigger trees burn longer)."""
    state = dict(profile["state"])
    if profile["kind"] == "tree":
        state["fuel"] = round(min(80.0, 18.0 + geom.bound_radius * 4.5), 1)
    return state


def _count_nodes(node) -> int:
    return 1 + sum(_count_nodes(c) for c in node.children)


def build_spec(draft: WorldDraft, prompt: str, seed: int) -> WorldSpec:
    notes = [f"not representable: {u[:120]}" for u in draft.unsupported[:8]]
    relief = _clamp(draft.relief_m, 0.0, 300.0)
    size = _clamp(draft.feature_size_m, 40.0, 800.0)
    lake_r = _clamp(draft.lake_radius_m, 20.0, 400.0)
    water_level: Optional[float] = None
    basin = None
    if draft.has_lake:
        basin = (0.0, 0.0)
        water_level = -relief * 0.15 - 2.0

    def mat(id_: str, rgb, rough=0.9, variation=0.2) -> MaterialDefinition:
        return MaterialDefinition(id=id_, base_color=_color(rgb), roughness=rough, variation=variation,
                                  variation_frequency=0.35)

    terrain = TerrainDefinition(
        base_height=0.0,
        amplitude=relief * 0.5,
        frequency=1.0 / size,
        octaves=5,
        ridge=_clamp(draft.ruggedness, 0.0, 1.0),
        warp=_clamp(size * 0.08, 0.0, 80.0),
        water_level=water_level,
        basin_center=basin,
        basin_radius=lake_r * 1.6 if draft.has_lake else 0.0,
        basin_depth=(relief * 0.5 + 10.0) if draft.has_lake else 0.0,
        ground_material=mat("ground", draft.ground_color),
        slope_material=mat("cliff", (0.36, 0.34, 0.31)),
        peak_material=mat("snow", (0.9, 0.92, 0.95), rough=0.6, variation=0.05) if draft.snow_peaks else None,
        peak_height=relief * 0.3 if draft.snow_peaks else 1e9,
        water_material=mat("water", (0.05, 0.16, 0.22), rough=0.05, variation=0.0) if draft.has_lake else None,
    )
    wind = (_clamp(draft.wind_east_mps, -20, 20), _clamp(draft.wind_south_mps, -20, 20))
    env = EnvironmentState(
        time_of_day_h=_clamp(draft.time_of_day_h, 0.0, 23.99),
        fog_density=_clamp(draft.fog_density, 0.0, 0.2),
        humidity=_clamp(draft.humidity, 0.0, 1.0),
        wind=wind,
        temperature_c=_clamp(draft.temperature_c, -60.0, 60.0),
        ambient=_clamp(draft.ambient_light, 0.02, 1.0),
    )

    lib = library()
    used: dict[str, None] = {}
    generated: dict = {}
    features: list[FeatureSpec] = []
    shore_r = lake_r + 30.0
    spawn_xz = (-(lake_r + 45.0), 0.0) if draft.has_lake else (0.0, 0.0)
    for i, f in enumerate(draft.features[:32]):
        profile = ARCHETYPE_PROFILE[f.archetype]
        scattered = f.count <= 0
        density = _clamp(f.density_per_hectare, 0.0, 300.0) if scattered else 0.0
        if scattered and density <= 0.0:
            density = 40.0
        placement = f.placement
        if placement in ("lakeshore", "east_shore", "west_shore") and not draft.has_lake:
            notes.append(f"{f.archetype}: no lake in this world, '{placement}' placed near spawn instead")
            placement = "near_spawn"
        center, radius = None, 0.0
        min_h = (water_level + 0.4) if water_level is not None else -1e9
        max_h = 1e9
        if placement == "lowlands":
            max_h = relief * 0.05
        elif placement == "highlands":
            min_h = max(min_h, relief * 0.1)
        elif placement == "lakeshore":
            center, radius = (0.0, 0.0), shore_r + 40.0
        elif placement == "east_shore":
            center, radius = (shore_r, 0.0), 45.0
        elif placement == "west_shore":
            center, radius = (-shore_r, 0.0), 45.0
        elif placement == "near_spawn":
            center, radius = spawn_xz, 60.0
        variants = procgen.generate_family(f.archetype, seed, i)
        if not variants:  # families without a generator (campfire) keep their library definition
            variants = [lib[f.archetype]]
        for k, geom in enumerate(variants):
            n = len(variants)
            total = 0 if scattered else int(_clamp(f.count, 1, 40))  # the cap applies to the family, not per variant
            share = 0 if scattered else total // n + (1 if k < total % n else 0)
            if not scattered and share == 0:
                continue
            features.append(FeatureSpec(
                id=f"f{i}v{k}",
                kind="scatter" if scattered else "settlement",
                entity_kind=profile["kind"],
                geometry_id=geom.id,
                density_per_ha=density / n,
                count=share,
                center=center,
                radius=radius,
                min_height=min_h,
                max_height=max_h,
                max_slope=0.9 if profile["kind"] == "tree" else 0.22 if profile["kind"] == "house" else 0.5,
                scale_range=(0.75, 1.3) if scattered else (0.95, 1.1),
                # per-entity crown fullness (also what fire consumes)
                param_ranges={k: v for k, v in procgen.PER_ENTITY_RANGES.items() if k in geom.parameters},
                capabilities=profile["capabilities"],
                initial_state=EntityState(**_state_for(profile, geom)),
            ))
            generated[geom.id] = geom
        used[f.archetype] = None

    # objects constructed by the language model from the geometry language (no predefined families involved)
    scene_nodes = 0
    for j, obj in enumerate(draft.objects[:8]):
        try:
            geom = object_geometry(obj, j)
        except ValueError as exc:
            notes.append(f"object '{obj.name[:40]}' rejected by the geometry validator: {str(exc)[:160]}")
            continue
        scene_nodes += _count_nodes(geom.root)
        if scene_nodes > SCENE_MAX_OBJECT_NODES:
            notes.append(f"object '{obj.name[:40]}' skipped: scene geometry budget of {SCENE_MAX_OBJECT_NODES} nodes reached")
            continue
        placement = obj.placement
        if placement in ("lakeshore", "east_shore", "west_shore") and not draft.has_lake:
            placement = "near_spawn"
        center, radius = None, 0.0
        min_h = (water_level + 0.3) if water_level is not None else -1e9
        max_h = 1e9
        if placement == "lowlands":
            max_h = relief * 0.05
        elif placement == "highlands":
            min_h = max(min_h, relief * 0.1)
        elif placement == "lakeshore":
            center, radius = (0.0, 0.0), shore_r + 40.0
        elif placement == "east_shore":
            center, radius = (shore_r, 0.0), 45.0
        elif placement == "west_shore":
            center, radius = (-shore_r, 0.0), 45.0
        elif placement == "near_spawn":
            center, radius = spawn_xz, 60.0
        scattered = obj.count <= 0
        density = _clamp(obj.density_per_hectare or 30.0, 0.5, 300.0) if scattered else 0.0
        lo, hi = sorted((_clamp(obj.scale_min, 0.2, 5.0), _clamp(obj.scale_max, 0.2, 5.0)))
        caps = ["flammable", "removable"] if obj.flammable else ["removable"]
        features.append(FeatureSpec(
            id=f"o{j}", kind="scatter" if scattered else "landmark", entity_kind=_slug(obj.kind)[:32] or "object",
            geometry_id=geom.id, density_per_ha=density, count=0 if scattered else int(_clamp(obj.count, 1, 40)),
            center=center, radius=radius, min_height=min_h, max_height=max_h,
            max_slope=0.8 if scattered else 0.5, scale_range=(lo, hi),
            param_ranges={k: (v.min, v.max) for k, v in geom.parameters.items() if k != "burn"},
            capabilities=caps,
            initial_state=EntityState(fuel=round(min(80.0, 15.0 + geom.bound_radius * 4.0), 1) if obj.flammable else 0.0,
                                      blocks_path=geom.bound_radius > 2.0),
        ))
        generated[geom.id] = geom
        notes.append(f"constructed '{obj.name[:40]}' from {_count_nodes(geom.root)} geometry nodes")

    spawn_y = terrain_height(terrain, seed, *spawn_xz) + 1.7
    return WorldSpec(
        title=(draft.title or "Untitled world")[:80],
        prompt=prompt[:2000],
        interpretation_notes=notes,
        terrain=terrain,
        environment=env,
        archetypes=list(generated.values()),
        features=features,
        spawn=(spawn_xz[0], spawn_y, spawn_xz[1]),
    )
