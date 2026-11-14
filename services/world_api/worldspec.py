"""WorldDraft (the small, flat structure a language model is allowed to produce) → validated WorldSpec.

The draft only selects parameters and library archetypes. `build_spec` clamps every number into the
contract ranges and constructs the authoritative WorldSpec; nothing in a draft is ever executed.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from greality_contracts import (
    EnvironmentState,
    EntityState,
    FeatureSpec,
    MaterialDefinition,
    TerrainDefinition,
    WorldSpec,
)

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
    features: list[FeatureDraft] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list, description="requested things that cannot be represented")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _color(rgb) -> tuple[float, float, float]:
    padded = (list(rgb) + [0.3, 0.3, 0.3])[:3]
    return tuple(_clamp(c, 0.0, 1.0) for c in padded)  # type: ignore[return-value]


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
    )

    lib = library()
    used: dict[str, None] = {}
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
        features.append(FeatureSpec(
            id=f"f{i}",
            kind="scatter" if scattered else "settlement",
            entity_kind=profile["kind"],
            geometry_id=f.archetype,
            density_per_ha=density,
            count=0 if scattered else int(_clamp(f.count, 1, 40)),
            center=center,
            radius=radius,
            min_height=min_h,
            max_height=max_h,
            # buildings need near-level ground (their foundation hides the rest); props and rocks tolerate more
            max_slope=0.9 if profile["kind"] == "tree" else 0.22 if profile["kind"] == "house" else 0.5,
            scale_range=(0.8, 1.3) if scattered else (0.95, 1.1),
            capabilities=profile["capabilities"],
            initial_state=EntityState(**profile["state"]),
        ))
        used[f.archetype] = None

    spawn_y = terrain_height(terrain, seed, *spawn_xz) + 1.7
    return WorldSpec(
        title=(draft.title or "Untitled world")[:80],
        prompt=prompt[:2000],
        interpretation_notes=notes,
        terrain=terrain,
        environment=env,
        archetypes=[lib[a] for a in used],
        features=features,
        spawn=(spawn_xz[0], spawn_y, spawn_xz[1]),
    )
