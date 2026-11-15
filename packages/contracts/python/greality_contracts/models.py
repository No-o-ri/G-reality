"""Canonical G-reality contracts (single authoritative schema source).

These Pydantic models are the only hand-written definition of the shared contracts.
`scripts/gen_contracts.py` exports them to JSON Schema (packages/contracts/schemas/contracts.schema.json)
and from there to TypeScript types (packages/contracts/ts/contracts.ts). Never hand-edit generated files.

Conventions (see docs/contracts/CONTRACTS.md):
- Right-handed world, +Y up, meters. Camera looks down -Z in camera space (three.js / OpenGL).
- Vec3 = (x, y, z). Quat = (x, y, z, w). Matrices are 16 floats, column-major (three.js order).
- Regions are square XZ cells of REGION_SIZE_M; RegionCoord(rx, rz) = floor(x / size), floor(z / size).
- Revisions are monotonic integers assigned only by the world-api inside a database transaction.
- All timestamps are UTC ISO-8601.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .geometry import GeometryValidationError, load_ops, validate_node

CONTRACT_VERSION = "0.1.0"
REGION_SIZE_M = 64.0
COORDINATE_FRAME = "world_rh_yup_m"

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Mat4 = tuple[
    float, float, float, float, float, float, float, float,
    float, float, float, float, float, float, float, float,
]

Id = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- spatial


class RegionCoord(Contract):
    rx: int = Field(ge=-4096, le=4096)
    rz: int = Field(ge=-4096, le=4096)


class Aabb(Contract):
    """World-space axis-aligned box, meters."""

    min: Vec3
    max: Vec3


class Transform(Contract):
    """Local-to-world: p_world = position + rotate(rotation, scale * p_local)."""

    position: Vec3
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    scale: float = Field(default=1.0, ge=0.01, le=100.0)


# --------------------------------------------------------------------------- geometry


class MaterialDefinition(Contract):
    id: str = Id
    base_color: Vec3 = Field(description="linear RGB, each 0..1")
    roughness: float = Field(default=0.8, ge=0.0, le=1.0)
    emissive: Vec3 = (0.0, 0.0, 0.0)
    emissive_strength: float = Field(default=0.0, ge=0.0, le=50.0)
    variation: float = Field(default=0.0, ge=0.0, le=1.0, description="procedural tint noise amplitude")
    variation_frequency: float = Field(default=1.0, ge=0.001, le=50.0)


class ParamSpec(Contract):
    """A named, bounded scalar an entity may bind (e.g. height, burn). Referenced as {"ref": name}."""

    default: float
    min: float
    max: float

    @model_validator(mode="after")
    def _ordered(self) -> "ParamSpec":
        if not self.min <= self.default <= self.max:
            raise ValueError("ParamSpec requires min <= default <= max")
        return self


class GeometryNode(Contract):
    """One node of the SDF scene language. Closed op set, see schemas/geometry_ops.json."""

    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    children: list["GeometryNode"] = Field(default_factory=list)
    material: Optional[str] = None


class GeometryDefinition(Contract):
    """A versioned, parameterised SDF archetype in local space (origin at its base, +Y up)."""

    id: str = Id
    version: int = Field(default=1, ge=1)
    language_version: int = Field(default=1, ge=1)
    root: GeometryNode
    parameters: dict[str, ParamSpec] = Field(default_factory=dict)
    materials: list[MaterialDefinition] = Field(min_length=1, max_length=8)
    bound_radius: float = Field(gt=0.0, le=200.0, description="conservative bounding-sphere radius at scale 1")

    @model_validator(mode="after")
    def _validate_tree(self) -> "GeometryDefinition":
        if self.language_version != load_ops()["language_version"]:
            raise ValueError(f"unsupported geometry language_version {self.language_version}")
        try:
            validate_node(
                self.root.model_dump(),
                parameters=set(self.parameters),
                materials={m.id for m in self.materials},
            )
        except GeometryValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class TerrainDefinition(Contract):
    """Global analytic heightfield `terrain-v1`: y = base + amplitude * shaped_fbm(xz * frequency).

    Evaluated identically by the GLSL compiler, the TS CPU evaluator and the Python evaluator
    (golden vectors in tests/golden/terrain_v1.json).
    """

    base_height: float = Field(default=0.0, ge=-500.0, le=500.0)
    amplitude: float = Field(default=30.0, ge=0.0, le=400.0)
    frequency: float = Field(default=0.008, ge=0.0005, le=0.2)
    octaves: int = Field(default=5, ge=1, le=8)
    ridge: float = Field(default=0.0, ge=0.0, le=1.0, description="0 = rolling hills, 1 = ridged mountains")
    warp: float = Field(default=0.0, ge=0.0, le=80.0, description="domain-warp amplitude in meters")
    water_level: Optional[float] = Field(default=None, ge=-500.0, le=500.0)
    basin_center: Optional[tuple[float, float]] = Field(default=None, description="XZ center of a carved lake basin")
    basin_radius: float = Field(default=0.0, ge=0.0, le=2000.0)
    basin_depth: float = Field(default=0.0, ge=0.0, le=200.0)
    ground_material: MaterialDefinition
    slope_material: MaterialDefinition
    peak_material: Optional[MaterialDefinition] = None
    peak_height: float = Field(default=1e9)
    water_material: Optional[MaterialDefinition] = None


class EnvironmentState(Contract):
    time_of_day_h: float = Field(default=10.0, ge=0.0, lt=24.0)
    sun_direction: Vec3 = (0.4, 0.8, 0.3)
    sun_color: Vec3 = (1.0, 0.95, 0.85)
    sky_color: Vec3 = (0.45, 0.62, 0.85)
    ambient: float = Field(default=0.25, ge=0.0, le=2.0)
    fog_density: float = Field(default=0.004, ge=0.0, le=0.2, description="1/m extinction at fog_base_height")
    fog_height_falloff: float = Field(default=0.02, ge=0.0, le=1.0)
    fog_base_height: float = 0.0
    fog_color: Vec3 = (0.7, 0.76, 0.82)
    wind: tuple[float, float] = Field(default=(1.0, 0.0), description="XZ wind vector, m/s")
    humidity: float = Field(default=0.4, ge=0.0, le=1.0)
    temperature_c: float = Field(default=18.0, ge=-60.0, le=60.0)


# --------------------------------------------------------------------------- entities


class Capability(str, Enum):
    flammable = "flammable"
    extinguishable = "extinguishable"
    removable = "removable"
    harvestable = "harvestable"
    build_site = "build_site"


class Condition(str, Enum):
    intact = "intact"
    burning = "burning"
    charred = "charred"
    removed = "removed"


class EntityState(Contract):
    """Authoritative simulated state of one entity. Rendering derives from this, never the reverse."""

    condition: Condition = Condition.intact
    integrity: float = Field(default=1.0, ge=0.0, le=1.0)
    fuel: float = Field(default=0.0, ge=0.0, le=1000.0, description="remaining burn ticks")
    burn_intensity: float = Field(default=0.0, ge=0.0, le=1.0)
    burn_started_tick: Optional[int] = None
    resources: dict[str, int] = Field(default_factory=dict, description="harvestable yield, e.g. {'wood': 4}")
    blocks_path: bool = False
    hazard: float = Field(default=0.0, ge=0.0, le=1.0, description="damage per tick to a player within hazard_radius")
    hazard_radius: float = Field(default=0.0, ge=0.0, le=100.0)


class WorldEntity(Contract):
    id: str = Id
    world_id: str = Id
    kind: str = Field(min_length=1, max_length=32, description="semantic type: tree, rock, house, campfire, ...")
    geometry_id: str = Id
    geometry_params: dict[str, float] = Field(default_factory=dict, description="bindings for GeometryDefinition.parameters")
    transform: Transform
    region: RegionCoord
    bound_radius: float = Field(gt=0.0, le=200.0, description="world-space bounding sphere radius")
    capabilities: list[Capability] = Field(default_factory=list)
    state: EntityState = Field(default_factory=EntityState)
    revision: int = Field(ge=0, description="world revision of the last change to this entity")
    created_revision: int = Field(ge=0)
    origin: Literal["generated", "player", "simulation"] = "generated"


class WorldRegion(Contract):
    world_id: str = Id
    coord: RegionCoord
    revision: int = Field(ge=0, description="bumped whenever anything that affects this region's appearance changes")
    materialized: bool = Field(description="true once the deterministic generator output was committed")
    generator_version: int = Field(ge=1)
    smoke_density: float = Field(default=0.0, ge=0.0, le=1.0)
    hazard_level: float = Field(default=0.0, ge=0.0, le=1.0)


class RegionRevision(Contract):
    world_id: str = Id
    coord: RegionCoord
    revision: int = Field(ge=0)
    world_revision: int = Field(ge=0, description="world revision that produced this region revision")
    event_id: Optional[str] = None
    changed_at: datetime


# --------------------------------------------------------------------------- world


class FeatureKind(str, Enum):
    forest = "forest"
    scatter = "scatter"
    settlement = "settlement"
    landmark = "landmark"


class FeatureSpec(Contract):
    """A placement rule the deterministic region generator expands into entities."""

    id: str = Id
    kind: FeatureKind
    entity_kind: str = Field(min_length=1, max_length=32)
    geometry_id: str = Id
    density_per_ha: float = Field(default=0.0, ge=0.0, le=400.0)
    count: int = Field(default=0, ge=0, le=200, description="for settlement/landmark: exact instance count")
    center: Optional[tuple[float, float]] = None
    radius: float = Field(default=0.0, ge=0.0, le=5000.0, description="0 = everywhere")
    min_height: float = -1e9
    max_height: float = 1e9
    max_slope: float = Field(default=1.0, ge=0.0, le=5.0)
    scale_range: tuple[float, float] = (0.8, 1.25)
    param_ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)
    capabilities: list[Capability] = Field(default_factory=list)
    initial_state: EntityState = Field(default_factory=EntityState)


class WorldSpec(Contract):
    """Validated semantic scene definition. Produced by the language interpreter, never executed as code."""

    title: str = Field(min_length=1, max_length=80)
    prompt: str = Field(max_length=2000)
    interpretation_notes: list[str] = Field(default_factory=list, description="what was constrained or dropped")
    terrain: TerrainDefinition
    environment: EnvironmentState = Field(default_factory=EnvironmentState)
    archetypes: list[GeometryDefinition] = Field(max_length=24)
    features: list[FeatureSpec] = Field(max_length=32)
    spawn: Vec3 = (0.0, 0.0, 0.0)


class GenerationStatus(str, Enum):
    requested = "requested"
    validating = "validating"
    geometry_ready = "geometry_ready"
    neural_pending = "neural_pending"
    neural_active = "neural_active"
    ready = "ready"
    partially_updated = "partially_updated"
    failed = "failed"


class PlayerState(Contract):
    player_id: str = Id
    position: Vec3
    yaw: float = 0.0
    pitch: float = Field(default=0.0, ge=-1.5708, le=1.5708)
    health: float = Field(default=1.0, ge=0.0, le=1.0)
    inventory: dict[str, int] = Field(default_factory=dict)


class GameplayState(Contract):
    tick: int = Field(default=0, ge=0, description="simulation tick, 1 tick = 1 s of simulated time")
    burned_count: int = Field(default=0, ge=0)
    built_count: int = Field(default=0, ge=0)
    flags: dict[str, bool] = Field(default_factory=dict)


class WorldState(Contract):
    """Authoritative world header. Regions and entities load through RegionSnapshot."""

    contract_version: str = CONTRACT_VERSION
    world_id: str = Id
    name: str = Field(min_length=1, max_length=80)
    seed: int = Field(ge=0, le=2**53 - 1)
    revision: int = Field(ge=0)
    generator_version: int = Field(default=1, ge=1)
    region_size_m: float = REGION_SIZE_M
    spec: WorldSpec
    environment: EnvironmentState
    gameplay: GameplayState = Field(default_factory=GameplayState)
    player: PlayerState
    status: GenerationStatus
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- commands and events


class ActionType(str, Enum):
    ignite = "ignite"
    extinguish = "extinguish"
    remove = "remove"
    build = "build"
    inspect = "inspect"


class WorldCommand(Contract):
    command_id: str = Field(min_length=8, max_length=64, description="client UUID, idempotency key")
    world_id: str = Id
    player_id: str = Id
    text: str = Field(min_length=1, max_length=500)
    target_hint: Optional[str] = Field(default=None, description="entity id under the crosshair, if any")
    player_position: Vec3
    view_direction: Vec3
    expected_world_revision: Optional[int] = Field(default=None, ge=0)
    action: Optional[ActionType] = Field(
        default=None, description="structured interaction (e.g. the E key); when set, no language interpretation runs")


class ParsedIntent(Contract):
    """Output of the interpreter (LLM or rules). Advisory only; the simulation validates everything."""

    action: ActionType
    target_kind: Optional[str] = None
    build_geometry_id: Optional[str] = None
    interpreter: Literal["llm", "rules", "direct"]
    confidence: float = Field(ge=0.0, le=1.0)


class ResolvedTarget(Contract):
    entity_id: Optional[str] = Field(default=None, description="None for build actions that target a location")
    kind: Optional[str] = None
    position: Vec3
    distance_m: float = Field(ge=0.0)
    region: RegionCoord
    resolved_by: Literal["hint", "aim", "nearest", "location"]


class PreconditionFailure(Contract):
    code: Literal[
        "no_target", "out_of_range", "missing_capability", "invalid_state",
        "blocked", "insufficient_resources", "unknown_action", "environment",
    ]
    message: str


class ActionPreconditions(Contract):
    max_range_m: float
    in_range: bool
    has_capability: bool
    state_ok: bool
    failures: list[PreconditionFailure] = Field(default_factory=list)


class ValidatedAction(Contract):
    command_id: str
    intent: ParsedIntent
    target: ResolvedTarget
    preconditions: ActionPreconditions
    base_world_revision: int = Field(ge=0)


class EventType(str, Enum):
    world_created = "world_created"
    region_materialized = "region_materialized"
    entity_ignited = "entity_ignited"
    fire_spread = "fire_spread"
    entity_burned_out = "entity_burned_out"
    entity_extinguished = "entity_extinguished"
    entity_removed = "entity_removed"
    entity_built = "entity_built"
    resource_changed = "resource_changed"
    environment_changed = "environment_changed"
    player_damaged = "player_damaged"


class WorldEvent(Contract):
    """Append-only fact. `revision` is the world revision this event created (one event per revision)."""

    event_id: str = Id
    world_id: str = Id
    revision: int = Field(ge=1)
    tick: int = Field(ge=0)
    type: EventType
    cause: Literal["player", "simulation", "system"]
    command_id: Optional[str] = None
    parent_event_id: Optional[str] = Field(default=None, description="causal chain, e.g. fire_spread -> entity_ignited")
    entity_ids: list[str] = Field(default_factory=list)
    regions: list[RegionCoord] = Field(default_factory=list, description="regions whose revision this event bumped")
    bounds: Optional[Aabb] = None
    payload: dict[str, Any] = Field(default_factory=dict, description="before/after patches; schema per EventType")
    created_at: datetime


class DirtyRegion(Contract):
    world_id: str = Id
    coord: RegionCoord
    region_revision: int = Field(ge=0, description="the new current revision")
    event_id: str
    bounds: Aabb
    stale_asset_id: Optional[str] = None


class WorldSync(Contract):
    """Poll response: the simulation is advanced to wall-clock time first, then events after a revision are returned."""

    world_id: str = Id
    tick: int = Field(ge=0, description="simulation tick after advancing (1 tick = 1 s)")
    revision: int = Field(ge=0)
    events: list[WorldEvent] = Field(default_factory=list)
    truncated: bool = Field(default=False, description="more events exist; reload regions instead of replaying")


class MutationResult(Contract):
    status: Literal["committed", "rejected", "conflict", "error"]
    command_id: str
    world_revision: int = Field(ge=0, description="current world revision after handling the command")
    message: str = Field(description="player-facing explanation")
    action: Optional[ValidatedAction] = None
    events: list[WorldEvent] = Field(default_factory=list)
    entities: list[WorldEntity] = Field(default_factory=list, description="full post-commit state of changed entities")
    dirty_regions: list[DirtyRegion] = Field(default_factory=list)
    failures: list[PreconditionFailure] = Field(default_factory=list)


# --------------------------------------------------------------------------- neural far field


class JobState(str, Enum):
    queued = "queued"
    awaiting_views = "awaiting_views"
    training = "training"
    validating = "validating"
    published = "published"
    superseded = "superseded"
    failed = "failed"
    cancelled = "cancelled"


class QualityProfile(str, Enum):
    ultra = "ultra"
    high = "high"
    balanced = "balanced"
    performance = "performance"


class CaptureView(Contract):
    """One synthetic observation of a region with exact pinhole camera parameters."""

    index: int = Field(ge=0, le=255)
    width: int = Field(ge=64, le=1024)
    height: int = Field(ge=64, le=1024)
    fx: float = Field(gt=0.0)
    fy: float = Field(gt=0.0)
    cx: float
    cy: float
    camera_to_world: Mat4 = Field(description="column-major, OpenGL camera (-Z forward, +Y up)")
    near: float = Field(gt=0.0)
    far: float = Field(gt=0.0)
    holdout: bool = Field(default=False, description="withheld from optimization, used for PSNR validation")


class ViewBundleManifest(Contract):
    """Describes an uploaded set of views. Images: sRGB8 PNG. Depth: float32 linear meters along -Z, 0 = miss,
    -1 = unobserved (the ray entered the region's clip box inside a solid; ignored by training and evaluation)."""

    job_id: str = Id
    world_id: str = Id
    coord: RegionCoord
    region_revision: int = Field(ge=0, description="region revision of the state the views were rendered from")
    provider: Literal["browser_sdf", "torch_sdf"]
    views: list[CaptureView] = Field(min_length=8, max_length=256)


class NeuralMetrics(Contract):
    rasterizer: Literal["gsplat", "torch"]
    iterations: int = Field(ge=0)
    gaussian_count: int = Field(ge=0)
    capture_seconds: float = Field(ge=0.0)
    train_seconds: float = Field(ge=0.0)
    psnr_holdout: Optional[float] = None
    peak_vram_mb: Optional[float] = None


class NeuralGenerationJob(Contract):
    job_id: str = Id
    world_id: str = Id
    coord: RegionCoord
    source_region_revision: int = Field(ge=0, description="the only revision this job may publish for")
    reason: Literal["explore", "mutation", "manual"]
    priority: float = Field(description="lower runs first; distance to player in meters")
    state: JobState
    quality: QualityProfile
    attempt: int = Field(default=1, ge=1)
    error: Optional[str] = None
    metrics: Optional[NeuralMetrics] = None
    asset_id: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class NeuralAssetManifest(Contract):
    asset_id: str = Id
    world_id: str = Id
    coord: RegionCoord
    region_revision: int = Field(ge=0, description="valid only while equal to WorldRegion.revision")
    state: Literal["active", "superseded"]
    method: Literal["3dgs-optim-v1"] = "3dgs-optim-v1"
    format: Literal["ply", "spz"]
    uri: str = Field(description="storage-adapter relative key, e.g. worlds/<id>/r_3_-2/rev7-<sha12>.ply")
    sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    gaussian_count: int = Field(ge=0)
    sh_degree: int = Field(ge=0, le=3)
    coordinate_frame: Literal["world_rh_yup_m"] = "world_rh_yup_m"
    origin: Vec3 = Field(description="world-space offset to add to stored Gaussian centers (region min corner)")
    bounds: Aabb
    metrics: NeuralMetrics
    job_id: str
    created_at: datetime


class RenderCacheEntry(Contract):
    """Client-side derived cache record. Valid only while source_revision matches authoritative state."""

    kind: Literal["sdf_program", "instance_buffer", "splat_asset"]
    key: str
    world_id: str = Id
    coord: Optional[RegionCoord] = None
    source_revision: int = Field(ge=0)
    asset_id: Optional[str] = None


class RegionSnapshot(Contract):
    """Everything a client needs to derive both renderings of one region."""

    region: WorldRegion
    entities: list[WorldEntity]
    neural_asset: Optional[NeuralAssetManifest] = Field(
        default=None, description="present only if an asset exists for exactly region.revision"
    )
    pending_job: Optional[NeuralGenerationJob] = None


# --------------------------------------------------------------------------- companion


class GpuInfo(Contract):
    name: str
    vram_total_mb: float
    vram_free_mb: float
    cuda_version: Optional[str] = None
    torch_version: Optional[str] = None


class HealthReport(Contract):
    service: Literal["greality-companion"] = "greality-companion"
    service_version: str
    contract_version: str = CONTRACT_VERSION
    gpu: Optional[GpuInfo] = None
    rasterizers: list[Literal["gsplat", "torch"]] = Field(default_factory=list)
    database: Literal["connected", "unavailable"]
    queue_depth: int = Field(ge=0)
    active_job_id: Optional[str] = None


class ApiError(Contract):
    code: str
    message: str
    detail: Optional[dict[str, Any]] = None


EXPORTED = [
    WorldState, WorldRegion, WorldEntity, GeometryDefinition, MaterialDefinition, EnvironmentState,
    PlayerState, GameplayState, WorldSpec, RegionSnapshot,
    WorldCommand, ResolvedTarget, ActionPreconditions, ValidatedAction, WorldEvent, MutationResult, WorldSync,
    RegionRevision, DirtyRegion, NeuralGenerationJob, NeuralAssetManifest, RenderCacheEntry,
    ViewBundleManifest, HealthReport, ApiError,
]
