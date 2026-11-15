"""WorldDraft (the small, flat structure a language model is allowed to produce) → validated WorldSpec.

The draft only selects parameters and library archetypes. `build_spec` clamps every number into the
contract ranges and constructs the authoritative WorldSpec; nothing in a draft is ever executed.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from greality_contracts import (
    EnvironmentState,
    EntityState,
    FeatureSpec,
    GeometryDefinition,
    InteractionCondition,
    InteractionEffect,
    InteractionSpec,
    ObjectBehavior,
    MaterialDefinition,
    TerrainDefinition,
    WorldSpec,
)

from . import procgen
from . import geometry_bounds
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


class ConditionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    var: Optional[str] = Field(default=None, description="state variable to test, or null to test `condition`")
    op: Literal["<", "<=", ">", ">=", "==", "!="] = ">="
    value: float = 0.0
    condition: Optional[Literal["intact", "burning", "charred", "removed"]] = None


class EffectDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["set_var", "add_var", "set_param", "scale_param", "set_condition", "ignite", "emit", "give",
                  "trigger_nearby"]
    var: Optional[str] = None
    param: Optional[str] = Field(default=None, description="a geometry parameter declared in `parameters`")
    value: float = 0.0
    min: float = -1000.0
    max: float = 1000.0
    condition: Optional[Literal["intact", "charred", "removed"]] = None
    particles: Optional[Literal["sparks", "spores", "dust", "smoke", "embers", "glow"]] = None
    color: Optional[list[float]] = None
    count: int = 0
    resource: Optional[str] = None
    amount: int = 0
    radius_m: float = 0.0
    max_targets: int = 0
    same_kind: bool = True
    interaction: Optional[str] = None


class InteractionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(description="lowercase identifier, e.g. harvest, strike, resonate")
    verb: str = Field(description="1-3 word prompt shown as 'E - <verb>?', e.g. 'Harvest spores'")
    range_m: float = 4.0
    player_initiated: bool = True
    requires: list[ConditionDraft] = Field(default_factory=list)
    effects: list[EffectDraft] = Field(default_factory=list)
    message: str = ""


class VarDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    value: float


class ObjectShapeDraft(BaseModel):
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
    physical_material: str = Field(default="", description="e.g. wood, crystal, stone, fungus, metal")
    vars: list[VarDraft] = Field(default_factory=list, description="initial state variables (0-6), e.g. emission 1")
    interactions: list[InteractionDraft] = Field(default_factory=list, description="0-3 player interactions")


class ObjectDraft(ObjectShapeDraft):
    """An object kind placed across a new world."""

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
    cloud_cover: float = Field(default=0.35, description="0 clear sky .. 1 overcast")
    requested_structures: list[str] = Field(
        default_factory=list,
        description="every distinct structure or object kind the user explicitly asks for, as short noun phrases "
                    "(e.g. 'floating ring', 'spiral tower', 'crystal arch'); each needs its own entry in objects")
    features: list[FeatureDraft] = Field(default_factory=list)
    objects: list[ObjectDraft] = Field(default_factory=list, description="newly constructed object kinds")
    unsupported: list[str] = Field(default_factory=list, description="requested things that cannot be represented")


class CreationObjectDraft(ObjectShapeDraft):
    """One object added to an existing world by a player's creation request, laid out around an anchor point in front
    of the player (+forward = the player's view direction on the ground, +right = to the player's right)."""

    forward_m: float = Field(default=0.0, description="offset from the group's anchor along the view direction, -80..80")
    right_m: float = Field(default=0.0, description="offset from the anchor to the player's right, -80..80")
    height_m: float = Field(default=0.0, description="how far its base floats above the ground; 0 = standing on it")
    yaw_deg: float = Field(default=0.0, description="rotation about the vertical axis")
    scale: float = Field(default=1.0, description="0.2-5")


class CreationDraft(BaseModel):
    """Language-model facing schema for adding objects to an existing world (flat, like WorldDraft)."""

    model_config = ConfigDict(extra="ignore")
    title: str = Field(description="short name of what is being created, e.g. 'Crystal ring gate'")
    requested_structures: list[str] = Field(
        default_factory=list, description="every distinct structure the request asks for, as short noun phrases")
    distance_m: float = Field(default=0.0, description="how far ahead of the player the group's anchor is; 0 = automatic")
    objects: list[CreationObjectDraft] = Field(default_factory=list, description="1-4 constructed objects")
    unsupported: list[str] = Field(default_factory=list)


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
MAX_OBJECT_BOUND_M = 150.0


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


def object_geometry(obj: ObjectShapeDraft, index: int, id_prefix: str = "obj") -> GeometryDefinition:
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
    geom = GeometryDefinition.model_validate({
        "id": f"{id_prefix}.{_slug(obj.name)}.{index}", "version": 1,
        "bound_radius": _clamp(obj.size_m, 0.5, 120.0) * 1.15,
        "parameters": params, "materials": mats, "root": root,
    })
    # The model's size_m is only an estimate: derive the bound from the geometry itself (never smaller than claimed).
    box = geometry_bounds.bounds(geom.root.model_dump(mode="json"), _param_specs(geom))
    if box is None:
        raise ValueError("the geometry is unbounded (a bare plane); intersect planes with a bounded shape")
    measured = geometry_bounds.radius_from_origin(box) * 1.05
    if measured > geom.bound_radius:
        geom = geom.model_copy(update={"bound_radius": _clamp(measured, 0.5, MAX_OBJECT_BOUND_M)})
    return geom


def _param_specs(geom: GeometryDefinition) -> dict[str, dict]:
    return {k: v.model_dump() for k, v in (geom.parameters or {}).items()}


def _refs(node, out: set) -> set:
    if isinstance(node, dict):
        if set(node) == {"ref"}:
            out.add(str(node["ref"]))
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)
    return out


def _stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ies", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return w


_FILLER = {"giant", "enormous", "huge", "large", "small", "tiny", "big", "with", "the", "and", "floating",
           "hovering", "levitating", "glowing", "luminous", "twisted", "crystal", "crystalline", "geometric", "alien",
           "some", "many", "few", "three", "two", "several", "tall", "massive", "strange", "ancient"}
_FILLER_STEMS = {_stem(w) for w in _FILLER}


def _words(text: str) -> set:
    return {_stem(w) for w in "".join(c if c.isalnum() else " " for c in text).split() if len(w) > 2}


def object_report(obj: ObjectShapeDraft, geom: GeometryDefinition) -> tuple[list[str], list[str], str]:
    """Geometric checks of one validated object -> (hard issues, soft hints, one-line measurement summary).

    Hard issues are worth a repair round with the model: the object is buried, degenerate, or declares parameters it
    never uses. Soft hints (flat, sparse) are passed along only when a repair happens anyway."""
    hard: list[str] = []
    soft: list[str] = []
    root = geom.root.model_dump(mode="json")
    nodes = _count_nodes(geom.root)
    (x0, y0, z0), (x1, y1, z1) = geometry_bounds.bounds(root, _param_specs(geom))
    w, h = max(x1 - x0, z1 - z0), y1 - y0
    summary = f"{nodes} nodes, spans x {x0:.1f}..{x1:.1f}, y {y0:.1f}..{y1:.1f}, z {z0:.1f}..{z1:.1f} m"
    unused = [k for k in geom.parameters if k != "burn" and k not in _refs(root, set())]
    if unused:
        hard.append(f"declares parameters {unused} but never uses them as {{\"ref\": name}} in its geometry")
    if max(w, h) < 0.3:
        hard.append("it is smaller than 0.3 m: check units (meters)")
    if y0 < -(0.35 * h) - 0.25:
        hard.append(f"{min(100, round(100 * -y0 / max(h, 1e-6)))}% of its height is below its base (y = 0), so it is "
                    f"buried in the ground: centred primitives (box, cylinder, cone, sphere, torus, polyhedron) extend "
                    f"below y = 0 unless translated up; translate it so its lowest point is at y = 0 "
                    f"(objects that float are raised further)")
    if nodes <= 3:
        hard.append(f"only {nodes} geometry nodes: too simple to be recognizable as '{obj.name[:40]}'")
    elif nodes < 7:
        soft.append(f"only {nodes} geometry nodes; add the characteristic parts that make it recognizable")
    if w > 4.0 and h < 0.12 * w:
        soft.append(f"it is flat ({w:.1f} m wide, {h:.1f} m tall); if it should stand upright (ring, arch, portal, "
                    f"wall) rotate it: a torus lies in the XZ plane, 90 degrees about X is quat [0.7071, 0, 0, 0.7071]")
    return hard, soft, summary


def draft_report(objects: list, requested: list) -> tuple[list[str], list[str]]:
    """Validation + geometric checks of a draft's objects -> (hard issues, every line incl. soft hints and
    measurements). A requested structure with no object whose name or kind shares a significant word is hard."""
    hard: list[str] = []
    every: list[str] = []
    covered: set = set()
    for i, obj in enumerate(objects[:8]):
        label = f"object {i} ({obj.name[:40]})"
        try:
            geom = object_geometry(obj, i)
        except ValueError as exc:
            msg = f"{label}: failed geometry validation: {str(exc)[:300]}"
            hard.append(msg)
            every.append(msg)
            continue
        covered |= _words(f"{obj.name} {obj.kind}")
        h, soft, summary = object_report(obj, geom)
        every.append(f"{label}: {summary}")
        hard += [f"{label}: {m}" for m in h]
        every += [f"{label}: {m}" for m in h + soft]
    for item in requested[:10]:
        key = _words(item) - _FILLER_STEMS
        if key and not key & covered:
            msg = f"requested '{item[:60]}' has no object of its own; construct it (one object per requested structure)"
            hard.append(msg)
            every.append(msg)
    return hard, every


def object_behavior(obj: ObjectShapeDraft, geom: GeometryDefinition, notes: list[str]) -> ObjectBehavior:
    """Validate a model-composed behaviour against its own geometry. Invalid interactions are dropped with a note;
    nothing in a behaviour is code: the server executes it with world_api.interactions (a closed grammar)."""
    vars_ = {}
    for v in obj.vars[:6]:
        name = _slug(v.name)
        vars_[name] = _clamp(v.value, -1000, 1000)
    if any(m.emissive_strength > 0 for m in geom.materials) and "emission" not in vars_:
        vars_["emission"] = 1.0  # the renderer multiplies an entity's glow and light by this variable
    ids = {_slug(i.id) for i in obj.interactions[:3]}
    out: list[InteractionSpec] = []
    for it in obj.interactions[:3]:
        try:
            effects = []
            for e in it.effects[:8]:
                d = e.model_dump()
                for key in ("var", "param", "resource", "interaction"):
                    if d.get(key):
                        d[key] = _slug(d[key])
                if d["type"] in ("set_param", "scale_param") and d.get("param") not in geom.parameters:
                    raise ValueError(f"effect changes '{d.get('param')}' which the geometry does not declare")
                if d["type"] == "trigger_nearby" and d.get("interaction") not in ids:
                    raise ValueError(f"trigger_nearby names unknown interaction '{d.get('interaction')}'")
                if d.get("color") is not None:
                    d["color"] = _color(d["color"])
                d["count"] = int(_clamp(d.get("count") or 0, 0, 400))
                d["amount"] = int(_clamp(d.get("amount") or 0, 0, 100))
                d["radius_m"] = _clamp(d.get("radius_m") or 0, 0, 30)
                d["max_targets"] = int(_clamp(d.get("max_targets") or 0, 0, 8))
                effects.append(InteractionEffect.model_validate(d))
            requires = []
            for c in it.requires[:4]:
                cd = c.model_dump()
                if cd.get("var"):
                    cd["var"] = _slug(cd["var"])
                requires.append(InteractionCondition.model_validate(cd))
            out.append(InteractionSpec(id=_slug(it.id), verb=it.verb.strip()[:24] or "Use",
                                       range_m=_clamp(it.range_m, 0.5, 10.0), player_initiated=it.player_initiated,
                                       requires=requires, effects=effects, message=it.message[:160]))
        except (ValueError, ValidationError) as exc:
            notes.append(f"interaction '{it.id}' of '{obj.name[:30]}' dropped: {str(exc)[:140]}")
    return ObjectBehavior(vars=vars_, interactions=out, material=_slug(obj.physical_material)[:32] if obj.physical_material else "")


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
        cloud_cover=_clamp(draft.cloud_cover, 0.0, 1.0),
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
    behaviors: dict[str, ObjectBehavior] = {}
    for j, obj in enumerate(draft.objects[:8]):
        try:
            geom = object_geometry(obj, j)
        except ValueError as exc:
            notes.append(f"object '{obj.name[:40]}' rejected by the geometry validator: {str(exc)[:160]}")
            continue
        behavior = object_behavior(obj, geom, notes)
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
                                      blocks_path=geom.bound_radius > 2.0, vars=behavior.vars),
        ))
        generated[geom.id] = geom
        if behavior.interactions or behavior.vars:
            behaviors[geom.id] = behavior
        notes.append(f"constructed '{obj.name[:40]}' from {_count_nodes(geom.root)} geometry nodes")

    spawn_y = terrain_height(terrain, seed, *spawn_xz) + 1.7
    return WorldSpec(
        title=(draft.title or "Untitled world")[:80],
        prompt=prompt[:2000],
        interpretation_notes=notes,
        terrain=terrain,
        environment=env,
        archetypes=list(generated.values()),
        behaviors=behaviors,
        features=features,
        spawn=(spawn_xz[0], spawn_y, spawn_xz[1]),
    )
