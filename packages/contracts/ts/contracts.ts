/* GENERATED from contracts.schema.json (source: greality_contracts/models.py). Do not edit. Regenerate: npm run contracts */

/**
 * @minItems 3
 * @maxItems 3
 */
export type Max = [unknown, unknown, unknown];
/**
 * @minItems 3
 * @maxItems 3
 */
export type Min = [unknown, unknown, unknown];
export type Code =
  | "no_target"
  | "out_of_range"
  | "missing_capability"
  | "invalid_state"
  | "blocked"
  | "insufficient_resources"
  | "unknown_action"
  | "environment";
export type Message = string;
export type Failures = PreconditionFailure[];
export type HasCapability = boolean;
export type InRange = boolean;
export type MaxRangeM = number;
export type StateOk = boolean;
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ActionType".
 */
export type ActionType = "interact" | "ignite" | "extinguish" | "remove" | "build" | "inspect";
export type Code1 = string;
export type Detail = {
  [k: string]: unknown;
} | null;
export type Message1 = string;
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "Capability".
 */
export type Capability = "flammable" | "extinguishable" | "removable" | "harvestable" | "build_site";
/**
 * column-major, OpenGL camera (-Z forward, +Y up)
 *
 * @minItems 16
 * @maxItems 16
 */
export type CameraToWorld = [
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown,
  unknown
];
export type Cx = number;
export type Cy = number;
export type Far = number;
export type Fx = number;
export type Fy = number;
export type Height = number;
/**
 * withheld from optimization, used for PSNR validation
 */
export type Holdout = boolean;
export type Index = number;
export type Near = number;
export type Width = number;
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "Condition".
 */
export type Condition = "intact" | "burning" | "charred" | "removed";
export type Rx = number;
export type Rz = number;
export type EventId = string;
/**
 * the new current revision
 */
export type RegionRevision = number;
export type StaleAssetId = string | null;
export type WorldId = string;
export type BlocksPath = boolean;
export type BurnIntensity = number;
export type BurnStartedTick = number | null;
export type Condition1 = "intact" | "burning" | "charred" | "removed";
/**
 * remaining burn ticks
 */
export type Fuel = number;
/**
 * damage per tick to a player within hazard_radius
 */
export type Hazard = number;
export type HazardRadius = number;
export type Integrity = number;
export type Ambient = number;
/**
 * 0 clear .. 1 overcast
 */
export type CloudCover = number;
export type FogBaseHeight = number;
/**
 * @minItems 3
 * @maxItems 3
 */
export type FogColor = [unknown, unknown, unknown];
/**
 * 1/m extinction at fog_base_height
 */
export type FogDensity = number;
export type FogHeightFalloff = number;
export type Humidity = number;
/**
 * @minItems 3
 * @maxItems 3
 */
export type SkyColor = [unknown, unknown, unknown];
/**
 * @minItems 3
 * @maxItems 3
 */
export type SunColor = [unknown, unknown, unknown];
/**
 * @minItems 3
 * @maxItems 3
 */
export type SunDirection = [unknown, unknown, unknown];
export type TemperatureC = number;
export type TimeOfDayH = number;
/**
 * XZ wind vector, m/s
 *
 * @minItems 2
 * @maxItems 2
 */
export type Wind = [unknown, unknown];
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "EventType".
 */
export type EventType =
  | "world_created"
  | "region_materialized"
  | "entity_ignited"
  | "fire_spread"
  | "entity_burned_out"
  | "entity_extinguished"
  | "entity_interacted"
  | "entity_removed"
  | "entity_built"
  | "resource_changed"
  | "environment_changed"
  | "player_damaged";
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "FeatureKind".
 */
export type FeatureKind = "forest" | "scatter" | "settlement" | "landmark";
export type Capabilities = Capability[];
export type Center = [unknown, unknown] | null;
/**
 * for settlement/landmark: exact instance count
 */
export type Count = number;
export type DensityPerHa = number;
export type EntityKind = string;
export type GeometryId = string;
export type Id = string;
export type MaxHeight = number;
export type MaxSlope = number;
export type MinHeight = number;
/**
 * 0 = everywhere
 */
export type Radius = number;
/**
 * @minItems 2
 * @maxItems 2
 */
export type ScaleRange = [unknown, unknown];
export type BuiltCount = number;
export type BurnedCount = number;
/**
 * simulation tick, 1 tick = 1 s of simulated time
 */
export type Tick = number;
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "GenerationStatus".
 */
export type GenerationStatus =
  | "requested"
  | "validating"
  | "geometry_ready"
  | "neural_pending"
  | "neural_active"
  | "ready"
  | "partially_updated"
  | "failed";
/**
 * conservative bounding-sphere radius at scale 1
 */
export type BoundRadius = number;
export type Id1 = string;
export type LanguageVersion = number;
/**
 * @minItems 1
 * @maxItems 8
 */
export type Materials =
  | [MaterialDefinition]
  | [MaterialDefinition, MaterialDefinition]
  | [MaterialDefinition, MaterialDefinition, MaterialDefinition]
  | [MaterialDefinition, MaterialDefinition, MaterialDefinition, MaterialDefinition]
  | [MaterialDefinition, MaterialDefinition, MaterialDefinition, MaterialDefinition, MaterialDefinition]
  | [
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition
    ]
  | [
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition
    ]
  | [
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition,
      MaterialDefinition
    ];
/**
 * linear RGB, each 0..1
 *
 * @minItems 3
 * @maxItems 3
 */
export type BaseColor = [unknown, unknown, unknown];
/**
 * @minItems 3
 * @maxItems 3
 */
export type Emissive = [unknown, unknown, unknown];
export type EmissiveStrength = number;
export type Id2 = string;
export type Roughness = number;
export type AssetId = string | null;
/**
 * @maxItems 6
 */
export type Authors =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string];
export type License = "CC0-1.0";
export type Name = string;
export type NormalStrength = number;
export type PageUrl = string;
export type Query = string;
/**
 * real-world size of one texture tile
 */
export type ScaleM = number;
/**
 * 0 = photo colours, 1 = recoloured to base_color
 */
export type Tint = number;
/**
 * procedural tint noise amplitude
 */
export type Variation = number;
export type VariationFrequency = number;
export type Default = number;
export type Max1 = number;
export type Min1 = number;
export type Children = GeometryNode[];
export type Material = string | null;
export type Op = string;
export type Version = number;
export type CudaVersion = string | null;
export type Name1 = string;
export type TorchVersion = string | null;
export type VramFreeMb = number;
export type VramTotalMb = number;
export type ActiveJobId = string | null;
export type ContractVersion = string;
export type Database = "connected" | "unavailable";
export type QueueDepth = number;
export type Rasterizers = ("gsplat" | "torch")[];
export type Service = "greality-companion";
export type ServiceVersion = string;
export type Condition2 = ("intact" | "burning" | "charred" | "removed") | null;
export type Op1 = "<" | "<=" | ">" | ">=" | "==" | "!=";
export type Value = number;
/**
 * state variable; None = use `condition`
 */
export type Var = string | null;
export type Amount = number;
export type Color = [unknown, unknown, unknown] | null;
export type Condition3 = ("intact" | "charred" | "removed") | null;
export type Count1 = number;
export type Interaction = string | null;
export type Max2 = number;
export type MaxTargets = number;
export type Min2 = number;
export type Param = string | null;
export type Particles = ("sparks" | "spores" | "dust" | "smoke" | "embers" | "glow") | null;
export type RadiusM = number;
export type Resource = string | null;
export type SameKind = boolean;
export type Type =
  "set_var" | "add_var" | "set_param" | "scale_param" | "set_condition" | "ignite" | "emit" | "give" | "trigger_nearby";
export type Value1 = number;
export type Var1 = string | null;
/**
 * @minItems 1
 * @maxItems 8
 */
export type Effects =
  | [InteractionEffect]
  | [InteractionEffect, InteractionEffect]
  | [InteractionEffect, InteractionEffect, InteractionEffect]
  | [InteractionEffect, InteractionEffect, InteractionEffect, InteractionEffect]
  | [InteractionEffect, InteractionEffect, InteractionEffect, InteractionEffect, InteractionEffect]
  | [InteractionEffect, InteractionEffect, InteractionEffect, InteractionEffect, InteractionEffect, InteractionEffect]
  | [
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect
    ]
  | [
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect,
      InteractionEffect
    ];
export type Id3 = string;
export type Message2 = string;
/**
 * False = only reachable through trigger_nearby
 */
export type PlayerInitiated = boolean;
export type RangeM = number;
/**
 * @maxItems 4
 */
export type Requires =
  | []
  | [InteractionCondition]
  | [InteractionCondition, InteractionCondition]
  | [InteractionCondition, InteractionCondition, InteractionCondition]
  | [InteractionCondition, InteractionCondition, InteractionCondition, InteractionCondition];
/**
 * player prompt: 'E - <verb>?'
 */
export type Verb = string;
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "JobState".
 */
export type JobState =
  "queued" | "awaiting_views" | "training" | "validating" | "published" | "superseded" | "failed" | "cancelled";
export type BaseWorldRevision = number;
export type CommandId = string;
export type BuildGeometryId = string | null;
export type Confidence = number;
export type Interpreter = "llm" | "rules" | "direct";
export type TargetKind = string | null;
export type DistanceM = number;
/**
 * None for build actions that target a location
 */
export type EntityId = string | null;
export type Kind = string | null;
/**
 * @minItems 3
 * @maxItems 3
 */
export type Position = [unknown, unknown, unknown];
export type ResolvedBy = "hint" | "aim" | "nearest" | "location";
export type CommandId1 = string;
export type DirtyRegions = DirtyRegion[];
/**
 * world-space bounding sphere radius
 */
export type BoundRadius1 = number;
export type Capabilities1 = Capability[];
export type CreatedRevision = number;
export type GeometryId1 = string;
export type Id4 = string;
/**
 * semantic type: tree, rock, house, campfire, ...
 */
export type Kind1 = string;
export type Origin = "generated" | "player" | "simulation";
/**
 * world revision of the last change to this entity
 */
export type Revision = number;
/**
 * @minItems 3
 * @maxItems 3
 */
export type Position1 = [unknown, unknown, unknown];
/**
 * @minItems 4
 * @maxItems 4
 */
export type Rotation = [unknown, unknown, unknown, unknown];
export type Scale = number;
export type WorldId1 = string;
/**
 * full post-commit state of changed entities
 */
export type Entities = WorldEntity[];
export type Cause = "player" | "simulation" | "system";
export type CommandId2 = string | null;
export type CreatedAt = string;
export type EntityIds = string[];
export type EventId1 = string;
/**
 * causal chain, e.g. fire_spread -> entity_ignited
 */
export type ParentEventId = string | null;
/**
 * regions whose revision this event bumped
 */
export type Regions = RegionCoord[];
export type Revision1 = number;
export type Tick1 = number;
export type WorldId2 = string;
export type Events = WorldEvent[];
export type Failures1 = PreconditionFailure[];
/**
 * player-facing explanation
 */
export type Message3 = string;
export type Status = "committed" | "rejected" | "conflict" | "error";
/**
 * current world revision after handling the command
 */
export type WorldRevision = number;
export type AssetId1 = string;
export type ByteSize = number;
export type CoordinateFrame = "world_rh_yup_m";
export type CreatedAt1 = string;
export type Format = "ply" | "spz";
export type GaussianCount = number;
export type JobId = string;
export type Method = "3dgs-optim-v1";
export type CaptureSeconds = number;
export type GaussianCount1 = number;
export type Iterations = number;
export type PeakVramMb = number | null;
export type PsnrHoldout = number | null;
export type Rasterizer = "gsplat" | "torch";
export type TrainSeconds = number;
/**
 * world-space offset to add to stored Gaussian centers (region min corner)
 *
 * @minItems 3
 * @maxItems 3
 */
export type Origin1 = [unknown, unknown, unknown];
/**
 * valid only while equal to WorldRegion.revision
 */
export type RegionRevision1 = number;
export type ShDegree = number;
export type Sha256 = string;
export type State = "active" | "superseded";
/**
 * storage-adapter relative key, e.g. worlds/<id>/r_3_-2/rev7-<sha12>.ply
 */
export type Uri = string;
export type WorldId3 = string;
export type AssetId2 = string | null;
export type Attempt = number;
export type CreatedAt2 = string;
export type Error = string | null;
export type FinishedAt = string | null;
export type JobId1 = string;
/**
 * lower runs first; distance to player in meters
 */
export type Priority = number;
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "QualityProfile".
 */
export type QualityProfile = "ultra" | "high" | "balanced" | "performance";
export type Reason = "explore" | "mutation" | "manual";
/**
 * the only revision this job may publish for
 */
export type SourceRegionRevision = number;
export type StartedAt = string | null;
export type WorldId4 = string;
/**
 * @maxItems 4
 */
export type Interactions =
  | []
  | [InteractionSpec]
  | [InteractionSpec, InteractionSpec]
  | [InteractionSpec, InteractionSpec, InteractionSpec]
  | [InteractionSpec, InteractionSpec, InteractionSpec, InteractionSpec];
/**
 * physical material, e.g. wood, crystal, stone
 */
export type Material1 = string;
export type Health = number;
export type Pitch = number;
export type PlayerId = string;
/**
 * @minItems 3
 * @maxItems 3
 */
export type Position2 = [unknown, unknown, unknown];
export type Yaw = number;
export type ChangedAt = string;
export type EventId2 = string | null;
export type Revision2 = number;
export type WorldId5 = string;
/**
 * world revision that produced this region revision
 */
export type WorldRevision1 = number;
export type Entities1 = WorldEntity[];
export type GeneratorVersion = number;
export type HazardLevel = number;
/**
 * true once the deterministic generator output was committed
 */
export type Materialized = boolean;
/**
 * bumped whenever anything that affects this region's appearance changes
 */
export type Revision3 = number;
export type SmokeDensity = number;
export type WorldId6 = string;
export type AssetId3 = string | null;
export type Key = string;
export type Kind2 = "sdf_program" | "instance_buffer" | "splat_asset";
export type SourceRevision = number;
export type WorldId7 = string;
export type Amplitude = number;
export type BaseHeight = number;
/**
 * XZ center of a carved lake basin
 */
export type BasinCenter = [unknown, unknown] | null;
export type BasinDepth = number;
export type BasinRadius = number;
export type Frequency = number;
export type Octaves = number;
export type PeakHeight = number;
/**
 * 0 = rolling hills, 1 = ridged mountains
 */
export type Ridge = number;
/**
 * domain-warp amplitude in meters
 */
export type Warp = number;
export type WaterLevel = number | null;
export type JobId2 = string;
export type Provider = "browser_sdf" | "torch_sdf";
/**
 * region revision of the state the views were rendered from
 */
export type RegionRevision3 = number;
/**
 * @minItems 8
 * @maxItems 256
 */
export type Views = [
  CaptureView,
  CaptureView,
  CaptureView,
  CaptureView,
  CaptureView,
  CaptureView,
  CaptureView,
  CaptureView,
  ...CaptureView[]
];
export type WorldId8 = string;
/**
 * client UUID, idempotency key
 */
export type CommandId3 = string;
export type ExpectedWorldRevision = number | null;
/**
 * with action=interact: id of the generated interaction to perform
 */
export type Interaction1 = string | null;
export type PlayerId1 = string;
/**
 * @minItems 3
 * @maxItems 3
 */
export type PlayerPosition = [unknown, unknown, unknown];
/**
 * entity id under the crosshair, if any
 */
export type TargetHint = string | null;
export type Text = string;
/**
 * @minItems 3
 * @maxItems 3
 */
export type ViewDirection = [unknown, unknown, unknown];
export type WorldId9 = string;
/**
 * @maxItems 64
 */
export type Archetypes = GeometryDefinition[];
/**
 * @maxItems 96
 */
export type Features = FeatureSpec[];
/**
 * what was constrained or dropped
 */
export type InterpretationNotes = string[];
export type Prompt = string;
/**
 * @minItems 3
 * @maxItems 3
 */
export type Spawn = [unknown, unknown, unknown];
export type Title = string;
export type ContractVersion1 = string;
export type CreatedAt3 = string;
export type GeneratorVersion1 = number;
export type Name2 = string;
export type RegionSizeM = number;
export type Revision4 = number;
export type Seed = number;
export type UpdatedAt = string;
export type WorldId10 = string;
export type Events1 = WorldEvent[];
export type Revision5 = number;
/**
 * simulation tick after advancing (1 tick = 1 s)
 */
export type Tick2 = number;
/**
 * more events exist; reload regions instead of replaying
 */
export type Truncated = boolean;
export type WorldId11 = string;

export interface GRealityContracts {
  [k: string]: unknown;
}
/**
 * World-space axis-aligned box, meters.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "Aabb".
 */
export interface Aabb {
  max: Max;
  min: Min;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ActionPreconditions".
 */
export interface ActionPreconditions {
  failures?: Failures;
  has_capability: HasCapability;
  in_range: InRange;
  max_range_m: MaxRangeM;
  state_ok: StateOk;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "PreconditionFailure".
 */
export interface PreconditionFailure {
  code: Code;
  message: Message;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ApiError".
 */
export interface ApiError {
  code: Code1;
  detail?: Detail;
  message: Message1;
}
/**
 * One synthetic observation of a region with exact pinhole camera parameters.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "CaptureView".
 */
export interface CaptureView {
  camera_to_world: CameraToWorld;
  cx: Cx;
  cy: Cy;
  far: Far;
  fx: Fx;
  fy: Fy;
  height: Height;
  holdout?: Holdout;
  index: Index;
  near: Near;
  width: Width;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "DirtyRegion".
 */
export interface DirtyRegion {
  bounds: Aabb;
  coord: RegionCoord;
  event_id: EventId;
  region_revision: RegionRevision;
  stale_asset_id?: StaleAssetId;
  world_id: WorldId;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "RegionCoord".
 */
export interface RegionCoord {
  rx: Rx;
  rz: Rz;
}
/**
 * Authoritative simulated state of one entity. Rendering derives from this, never the reverse.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "EntityState".
 */
export interface EntityState {
  blocks_path?: BlocksPath;
  burn_intensity?: BurnIntensity;
  burn_started_tick?: BurnStartedTick;
  condition?: Condition1;
  fuel?: Fuel;
  hazard?: Hazard;
  hazard_radius?: HazardRadius;
  integrity?: Integrity;
  resources?: Resources;
  vars?: Vars;
}
/**
 * harvestable yield, e.g. {'wood': 4}
 */
export interface Resources {
  [k: string]: number;
}
/**
 * named state of generated interactions, e.g. emission, charge, spores
 */
export interface Vars {
  [k: string]: number;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "EnvironmentState".
 */
export interface EnvironmentState {
  ambient?: Ambient;
  cloud_cover?: CloudCover;
  fog_base_height?: FogBaseHeight;
  fog_color?: FogColor;
  fog_density?: FogDensity;
  fog_height_falloff?: FogHeightFalloff;
  humidity?: Humidity;
  sky_color?: SkyColor;
  sun_color?: SunColor;
  sun_direction?: SunDirection;
  temperature_c?: TemperatureC;
  time_of_day_h?: TimeOfDayH;
  wind?: Wind;
}
/**
 * A placement rule the deterministic region generator expands into entities.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "FeatureSpec".
 */
export interface FeatureSpec {
  capabilities?: Capabilities;
  center?: Center;
  count?: Count;
  density_per_ha?: DensityPerHa;
  entity_kind: EntityKind;
  geometry_id: GeometryId;
  id: Id;
  initial_state?: EntityState;
  kind: FeatureKind;
  max_height?: MaxHeight;
  max_slope?: MaxSlope;
  min_height?: MinHeight;
  param_ranges?: ParamRanges;
  radius?: Radius;
  scale_range?: ScaleRange;
}
export interface ParamRanges {
  /**
   * @minItems 2
   * @maxItems 2
   */
  [k: string]: [unknown, unknown];
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "GameplayState".
 */
export interface GameplayState {
  built_count?: BuiltCount;
  burned_count?: BurnedCount;
  flags?: Flags;
  tick?: Tick;
}
export interface Flags {
  [k: string]: boolean;
}
/**
 * A versioned, parameterised SDF archetype in local space (origin at its base, +Y up).
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "GeometryDefinition".
 */
export interface GeometryDefinition {
  bound_radius: BoundRadius;
  id: Id1;
  language_version?: LanguageVersion;
  materials: Materials;
  parameters?: Parameters;
  root: GeometryNode;
  version?: Version;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "MaterialDefinition".
 */
export interface MaterialDefinition {
  base_color: BaseColor;
  emissive?: Emissive;
  emissive_strength?: EmissiveStrength;
  id: Id2;
  roughness?: Roughness;
  texture?: TextureBinding | null;
  variation?: Variation;
  variation_frequency?: VariationFrequency;
}
/**
 * A CC0 photo texture set bound to a material when the world (or creation) is made, so the world always shows
 * the same surface. `query` is what the language model asked for; the asset fields are filled when a match was
 * found online (otherwise the material keeps its procedural surface). Image bytes are cached in Neon on first use.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "TextureBinding".
 */
export interface TextureBinding {
  asset_id?: AssetId;
  authors?: Authors;
  license?: License;
  name?: Name;
  normal_strength?: NormalStrength;
  page_url?: PageUrl;
  query: Query;
  scale_m?: ScaleM;
  tint?: Tint;
}
export interface Parameters {
  [k: string]: ParamSpec;
}
/**
 * A named, bounded scalar an entity may bind (e.g. height, burn). Referenced as {"ref": name}.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ParamSpec".
 */
export interface ParamSpec {
  default: Default;
  max: Max1;
  min: Min1;
}
/**
 * One node of the SDF scene language. Closed op set, see schemas/geometry_ops.json.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "GeometryNode".
 */
export interface GeometryNode {
  children?: Children;
  material?: Material;
  op: Op;
  params?: Params;
}
export interface Params {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "GpuInfo".
 */
export interface GpuInfo {
  cuda_version?: CudaVersion;
  name: Name1;
  torch_version?: TorchVersion;
  vram_free_mb: VramFreeMb;
  vram_total_mb: VramTotalMb;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "HealthReport".
 */
export interface HealthReport {
  active_job_id?: ActiveJobId;
  contract_version?: ContractVersion;
  database: Database;
  gpu?: GpuInfo | null;
  queue_depth: QueueDepth;
  rasterizers?: Rasterizers;
  service?: Service;
  service_version: ServiceVersion;
}
/**
 * Guard on the acting entity: a state variable compared with a value, or its condition.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "InteractionCondition".
 */
export interface InteractionCondition {
  condition?: Condition2;
  op?: Op1;
  value?: Value;
  var?: Var;
}
/**
 * One state transition. Fields used depend on `type` (unused fields are ignored).
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "InteractionEffect".
 */
export interface InteractionEffect {
  amount?: Amount;
  color?: Color;
  condition?: Condition3;
  count?: Count1;
  interaction?: Interaction;
  max?: Max2;
  max_targets?: MaxTargets;
  min?: Min2;
  param?: Param;
  particles?: Particles;
  radius_m?: RadiusM;
  resource?: Resource;
  same_kind?: SameKind;
  type: Type;
  value?: Value1;
  var?: Var1;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "InteractionSpec".
 */
export interface InteractionSpec {
  effects: Effects;
  id: Id3;
  message?: Message2;
  player_initiated?: PlayerInitiated;
  range_m?: RangeM;
  requires?: Requires;
  verb: Verb;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "MutationResult".
 */
export interface MutationResult {
  action?: ValidatedAction | null;
  command_id: CommandId1;
  dirty_regions?: DirtyRegions;
  entities?: Entities;
  events?: Events;
  failures?: Failures1;
  message: Message3;
  status: Status;
  world_revision: WorldRevision;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ValidatedAction".
 */
export interface ValidatedAction {
  base_world_revision: BaseWorldRevision;
  command_id: CommandId;
  intent: ParsedIntent;
  preconditions: ActionPreconditions;
  target: ResolvedTarget;
}
/**
 * Output of the interpreter (LLM or rules). Advisory only; the simulation validates everything.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ParsedIntent".
 */
export interface ParsedIntent {
  action: ActionType;
  build_geometry_id?: BuildGeometryId;
  confidence: Confidence;
  interpreter: Interpreter;
  target_kind?: TargetKind;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ResolvedTarget".
 */
export interface ResolvedTarget {
  distance_m: DistanceM;
  entity_id?: EntityId;
  kind?: Kind;
  position: Position;
  region: RegionCoord;
  resolved_by: ResolvedBy;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldEntity".
 */
export interface WorldEntity {
  bound_radius: BoundRadius1;
  capabilities?: Capabilities1;
  created_revision: CreatedRevision;
  geometry_id: GeometryId1;
  geometry_params?: GeometryParams;
  id: Id4;
  kind: Kind1;
  origin?: Origin;
  region: RegionCoord;
  revision: Revision;
  state?: EntityState;
  transform: Transform;
  world_id: WorldId1;
}
/**
 * bindings for GeometryDefinition.parameters
 */
export interface GeometryParams {
  [k: string]: number;
}
/**
 * Local-to-world: p_world = position + rotate(rotation, scale * p_local).
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "Transform".
 */
export interface Transform {
  position: Position1;
  rotation?: Rotation;
  scale?: Scale;
}
/**
 * Append-only fact. `revision` is the world revision this event created (one event per revision).
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldEvent".
 */
export interface WorldEvent {
  bounds?: Aabb | null;
  cause: Cause;
  command_id?: CommandId2;
  created_at: CreatedAt;
  entity_ids?: EntityIds;
  event_id: EventId1;
  parent_event_id?: ParentEventId;
  payload?: Payload;
  regions?: Regions;
  revision: Revision1;
  tick: Tick1;
  type: EventType;
  world_id: WorldId2;
}
/**
 * before/after patches; schema per EventType
 */
export interface Payload {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "NeuralAssetManifest".
 */
export interface NeuralAssetManifest {
  asset_id: AssetId1;
  bounds: Aabb;
  byte_size: ByteSize;
  coord: RegionCoord;
  coordinate_frame?: CoordinateFrame;
  created_at: CreatedAt1;
  format: Format;
  gaussian_count: GaussianCount;
  job_id: JobId;
  method?: Method;
  metrics: NeuralMetrics;
  origin: Origin1;
  region_revision: RegionRevision1;
  sh_degree: ShDegree;
  sha256: Sha256;
  state: State;
  uri: Uri;
  world_id: WorldId3;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "NeuralMetrics".
 */
export interface NeuralMetrics {
  capture_seconds: CaptureSeconds;
  gaussian_count: GaussianCount1;
  iterations: Iterations;
  peak_vram_mb?: PeakVramMb;
  psnr_holdout?: PsnrHoldout;
  rasterizer: Rasterizer;
  train_seconds: TrainSeconds;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "NeuralGenerationJob".
 */
export interface NeuralGenerationJob {
  asset_id?: AssetId2;
  attempt?: Attempt;
  coord: RegionCoord;
  created_at: CreatedAt2;
  error?: Error;
  finished_at?: FinishedAt;
  job_id: JobId1;
  metrics?: NeuralMetrics | null;
  priority: Priority;
  quality: QualityProfile;
  reason: Reason;
  source_region_revision: SourceRegionRevision;
  started_at?: StartedAt;
  state: JobState;
  world_id: WorldId4;
}
/**
 * Generated behaviour of one archetype: initial state variables and the interactions it supports.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ObjectBehavior".
 */
export interface ObjectBehavior {
  interactions?: Interactions;
  material?: Material1;
  vars?: Vars1;
}
export interface Vars1 {
  [k: string]: number;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "PlayerState".
 */
export interface PlayerState {
  health?: Health;
  inventory?: Inventory;
  pitch?: Pitch;
  player_id: PlayerId;
  position: Position2;
  yaw?: Yaw;
}
export interface Inventory {
  [k: string]: number;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "RegionRevision".
 */
export interface RegionRevision2 {
  changed_at: ChangedAt;
  coord: RegionCoord;
  event_id?: EventId2;
  revision: Revision2;
  world_id: WorldId5;
  world_revision: WorldRevision1;
}
/**
 * Everything a client needs to derive both renderings of one region.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "RegionSnapshot".
 */
export interface RegionSnapshot {
  entities: Entities1;
  /**
   * present only if an asset exists for exactly region.revision
   */
  neural_asset?: NeuralAssetManifest | null;
  pending_job?: NeuralGenerationJob | null;
  region: WorldRegion;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldRegion".
 */
export interface WorldRegion {
  coord: RegionCoord;
  generator_version: GeneratorVersion;
  hazard_level?: HazardLevel;
  materialized: Materialized;
  revision: Revision3;
  smoke_density?: SmokeDensity;
  world_id: WorldId6;
}
/**
 * Client-side derived cache record. Valid only while source_revision matches authoritative state.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "RenderCacheEntry".
 */
export interface RenderCacheEntry {
  asset_id?: AssetId3;
  coord?: RegionCoord | null;
  key: Key;
  kind: Kind2;
  source_revision: SourceRevision;
  world_id: WorldId7;
}
/**
 * Global analytic heightfield `terrain-v1`: y = base + amplitude * shaped_fbm(xz * frequency).
 *
 * Evaluated identically by the GLSL compiler, the TS CPU evaluator and the Python evaluator
 * (golden vectors in tests/golden/terrain_v1.json).
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "TerrainDefinition".
 */
export interface TerrainDefinition {
  amplitude?: Amplitude;
  base_height?: BaseHeight;
  basin_center?: BasinCenter;
  basin_depth?: BasinDepth;
  basin_radius?: BasinRadius;
  frequency?: Frequency;
  ground_material: MaterialDefinition;
  octaves?: Octaves;
  peak_height?: PeakHeight;
  peak_material?: MaterialDefinition | null;
  ridge?: Ridge;
  slope_material: MaterialDefinition;
  warp?: Warp;
  water_level?: WaterLevel;
  water_material?: MaterialDefinition | null;
}
/**
 * Describes an uploaded set of views. Images: sRGB8 PNG. Depth: float32 linear meters along -Z, 0 = miss,
 * -1 = unobserved (the ray entered the region's clip box inside a solid; ignored by training and evaluation).
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "ViewBundleManifest".
 */
export interface ViewBundleManifest {
  coord: RegionCoord;
  job_id: JobId2;
  provider: Provider;
  region_revision: RegionRevision3;
  views: Views;
  world_id: WorldId8;
}
/**
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldCommand".
 */
export interface WorldCommand {
  /**
   * structured interaction (e.g. the E key); when set, no language interpretation runs
   */
  action?: ActionType | null;
  command_id: CommandId3;
  expected_world_revision?: ExpectedWorldRevision;
  interaction?: Interaction1;
  player_id: PlayerId1;
  player_position: PlayerPosition;
  target_hint?: TargetHint;
  text: Text;
  view_direction: ViewDirection;
  world_id: WorldId9;
}
/**
 * Validated semantic scene definition. Produced by the language interpreter, never executed as code.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldSpec".
 */
export interface WorldSpec {
  archetypes: Archetypes;
  behaviors?: Behaviors;
  environment?: EnvironmentState;
  features: Features;
  interpretation_notes?: InterpretationNotes;
  prompt: Prompt;
  spawn?: Spawn;
  terrain: TerrainDefinition;
  title: Title;
}
/**
 * geometry_id -> generated behaviour
 */
export interface Behaviors {
  [k: string]: ObjectBehavior;
}
/**
 * Authoritative world header. Regions and entities load through RegionSnapshot.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldState".
 */
export interface WorldState {
  contract_version?: ContractVersion1;
  created_at: CreatedAt3;
  environment: EnvironmentState;
  gameplay?: GameplayState;
  generator_version?: GeneratorVersion1;
  name: Name2;
  player: PlayerState;
  region_size_m?: RegionSizeM;
  revision: Revision4;
  seed: Seed;
  spec: WorldSpec;
  status: GenerationStatus;
  updated_at: UpdatedAt;
  world_id: WorldId10;
}
/**
 * Poll response: the simulation is advanced to wall-clock time first, then events after a revision are returned.
 *
 * This interface was referenced by `GRealityContracts`'s JSON-Schema
 * via the `definition` "WorldSync".
 */
export interface WorldSync {
  events?: Events1;
  revision: Revision5;
  tick: Tick2;
  truncated?: Truncated;
  world_id: WorldId11;
}
