/**
 * Frame loop and world wiring. One WebGLRenderer, one scene, one camera, one depth buffer:
 * the near-field SDF pass renders into its target, the composite quad in `scene` writes color + depth
 * to the canvas, and (from Chunk 3) Gaussian-splat meshes added to the same scene depth-test against it.
 */
import * as THREE from "three";
import type { CaptureView, NeuralAssetManifest, RegionCoord, RegionSnapshot, WorldEntity, WorldState } from "@contracts";
import { buildRig, type CaptureRig, type Vec3 } from "../capture/rig";
import { EYE_HEIGHT, Player } from "../controls/Player";
import type { DebugView, FarFieldRenderer, HybridFrame, QualityName } from "../rendering/hybrid/types";
import { QUALITY } from "../rendering/hybrid/types";
import { NearField, type PickResult } from "../rendering/nearfield/NearField";
import { GaussianDiagnostic, type GaussianAssetStatus, type GaussianMode } from "../rendering/farfield/GaussianDiagnostic";
import { FireSystem, type FireSource } from "../rendering/effects/FireSystem";
import { PostProcess } from "../rendering/post/PostProcess";
import { Grass } from "../rendering/effects/Grass";
import { MIN_RENDERABLE_GENERATOR_VERSION, type Companion } from "./api";

export const REGION_SIZE = 64;
const NEAR_REGION_RADIUS = 2; // regions kept loaded around the player (5 x 5)

export interface EngineStatus {
  fps: number;
  frameMs: { avg: number; p50: number; p95: number; max: number };
  nearGpuMs: number | null;
  internalSize: [number, number];
  canvasSize: [number, number];
  instances: number;
  archetypes: number;
  materials: number;
  grid: string;
  gridOverflow: number;
  shaderChars: number;
  compileMs: number;
  textures: string;
  compileErrors: string[];
  regionsLoaded: number;
  regionsPending: number;
  regionRevision: number | null;
  worldRevision: number | null;
  worldId: string | null;
  position: [number, number, number];
  region: [number, number];
  flying: boolean;
  pointerLocked: boolean;
  quality: QualityName;
  debugView: DebugView;
  gpuRenderer: string;
  terrainParityM: number | null;
  farField: string;
  gaussianMode: GaussianMode;
  gaussianAssets: GaussianAssetStatus[];
  gaussianSplats: number;
  hovered: { entity: WorldEntity; distance: number } | null;
  previews: number;
  loadError: string | null;
}

const key = (rx: number, rz: number) => `${rx},${rz}`;

/** Grass parameters derived from the world's generated terrain materials (how green the ground is). */
function grassFor(world: WorldState, groundHeight: (x: number, z: number) => number) {
  const t = world.spec.terrain;
  const g = (t.ground_material.base_color ?? [0.3, 0.3, 0.2]) as number[];
  const sum = g[0] + g[1] + g[2] + 1e-6;
  const greenness = g[1] / sum;
  // lush where the ground is green; dry worlds get sparse tufts tinted by their own ground colour; no grass on
  // near-white (snow) or very dark (lava rock) ground
  const brightness = sum / 3;
  let lushness = Math.min(1, Math.max(0, (greenness - 0.34) / 0.1));
  if (lushness < 0.25) lushness = 0.18;
  if (brightness > 0.75 || brightness < 0.04) lushness = 0;
  const tint: [number, number, number] = greenness > 0.36 ? [0.95, 1.08, 0.88] : [1.06, 1.0, 0.86];
  return {
    seed: world.seed >>> 0,
    groundHeight,
    waterLevel: (t.water_level ?? null) as number | null,
    peakHeight: t.peak_material ? (t.peak_height ?? null) as number | null : null,
    lushness,
    tint,
  };
}
const CLIP_MARGIN_M = 2;

/** Metadata of a capture session: everything of the view bundle except the pixels (see services/gpu/bundle.py). */
export interface CaptureSession {
  world_id: string;
  coord: RegionCoord;
  region_revision: number;
  provider: "browser_sdf";
  views: CaptureView[];
  capture: {
    region_origin: Vec3;
    clip_min: Vec3;
    clip_max: Vec3;
    supersample: number;
    renderer: string;
    fog: boolean;
    entities: number;
    rig: Omit<CaptureRig, "views">;
    gpu: string;
  };
}

function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

export class Engine implements HybridFrame {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(70, 1, 0.1, 4000);
  readonly near: NearField;
  far: FarFieldRenderer | null = null; // hybrid far field (region ownership, SDF discard) is Chunk 4; nothing stands in
  /** Chunk 3 diagnostic: optimized Gaussian assets shown beside / instead of the SDF output, never mixed with it. */
  readonly gaussians: GaussianDiagnostic;
  private gaussianMode: GaussianMode = "sdf";
  private assetsLoading = new Set<string>();
  private offlineIndex: NeuralAssetManifest[] | null = null;
  private readonly gaussianBackground = new THREE.Color(0x1b1e24);
  readonly player: Player;
  readonly fire: FireSystem;
  readonly post: PostProcess;
  readonly grass: Grass;
  /** HDR post-processing (bloom, light shafts, tone mapping, FXAA) for the normal view; off for debug views */
  postEnabled = true;
  private lastRipple = 0;
  private fireSources: FireSource[] = [];
  /** developer mode: fly camera, diagnostics and neural tools */
  devMode = false;
  /** landing-page camera orbit around this point (null = normal first-person play) */
  attract: THREE.Vector3 | null = null;
  /** progress messages while a world opens (shown on the loading screen) */
  onProgress: ((stage: string) => void) | null = null;
  /** called once per frame before rendering (gameplay layer) */
  onFrame: ((dt: number) => void) | null = null;
  /** called for every entity replaced by a server-confirmed state */
  onEntitiesChanged: ((before: WorldEntity | undefined, after: WorldEntity) => void) | null = null;
  private time = 0;

  world: WorldState | null = null;
  private companion: Companion | null = null;
  private regions = new Map<string, RegionSnapshot>();
  private pending = new Set<string>();
  private entitiesDirty = false;
  private quality: QualityName = "balanced"; // player default; the SDF cost grew with generated geometry (Chunk 4)
  private debugView: DebugView = "final";
  private frameTimes: number[] = [];
  private last = performance.now();
  private raf = 0;
  private hovered: PickResult | null = null;
  private lastPick = 0;
  private terrainParity: number | null = null;
  private loadError: string | null = null;
  private lastRegionAttempt = 0;
  private frames = 0;
  private gpuRenderer = "unknown";
  onStatus: ((s: EngineStatus) => void) | null = null;
  private lastStatus = 0;
  private captureState: { session: CaptureSession; rig: CaptureRig; camera: THREE.PerspectiveCamera; size: number } | null = null;

  constructor(readonly canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance", alpha: false });
    this.renderer.setPixelRatio(1);
    this.renderer.autoClear = true;
    const gl = this.renderer.getContext();
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    if (info) this.gpuRenderer = String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL));
    this.near = new NearField(this.renderer);
    this.scene.add(this.near.compositeMesh);
    this.player = new Player(canvas);
    this.near.setQuality(QUALITY[this.quality]);
    this.gaussians = new GaussianDiagnostic(this.renderer, this.scene);
    this.fire = new FireSystem(this.near.target.depthTexture!);
    this.scene.add(this.fire.group);
    this.post = new PostProcess(this.renderer);
    this.grass = new Grass(this.near.target.texture);
    this.scene.add(this.grass.mesh);
  }

  setDevMode(on: boolean): void {
    this.devMode = on;
    this.player.allowFly = on;
    if (!on) this.player.flying = false;
  }

  // ---------------------------------------------------------------- authoritative updates

  /** Replace loaded entities with server-confirmed states (command results, simulation events). */
  applyEntities(entities: WorldEntity[]): void {
    if (!entities.length) return;
    for (const e of entities) {
      const snap = this.regions.get(key(e.region.rx, e.region.rz));
      if (!snap) continue;
      const i = snap.entities.findIndex((x) => x.id === e.id);
      const before = i >= 0 ? snap.entities[i] : undefined;
      if (i >= 0) snap.entities[i] = e;
      else snap.entities.push(e);
      this.onEntitiesChanged?.(before, e);
    }
    this.entitiesDirty = true;
  }

  /** Re-read regions from the server (null = every loaded region): entities, region revisions, neural assets. */
  async reloadRegions(coords: RegionCoord[] | null): Promise<void> {
    if (!this.world || !this.companion) return;
    const wanted = (coords ?? [...this.regions.values()].map((s) => s.region.coord)).filter((c) => this.regions.has(key(c.rx, c.rz)));
    if (!wanted.length) return;
    const worldId = this.world.world_id;
    for (let i = 0; i < wanted.length; i += 49) {
      const snaps = await this.companion.loadRegions(worldId, wanted.slice(i, i + 49));
      if (this.world?.world_id !== worldId) return;
      for (const s of snaps) {
        const k = key(s.region.coord.rx, s.region.coord.rz);
        const old = this.regions.get(k);
        const byId = new Map((old?.entities ?? []).map((e) => [e.id, e]));
        for (const e of s.entities) {
          const prev = byId.get(e.id);
          if (!prev || prev.revision !== e.revision) this.onEntitiesChanged?.(prev, e);
        }
        this.regions.set(k, s);
      }
    }
    this.entitiesDirty = true;
    void this.loadAssets();
  }

  /** Entities of the loaded regions (live state as last confirmed by the server). */
  loadedEntities(): WorldEntity[] {
    const out: WorldEntity[] = [];
    for (const s of this.regions.values()) out.push(...s.entities);
    return out;
  }

  setFireSources(sources: FireSource[]): void {
    this.fireSources = sources;
  }

  /** The entity under the crosshair (CPU SDF pick, blocked by terrain) and its distance. */
  hoveredHit(): { entity: WorldEntity; distance: number } | null {
    const e = this.hoveredEntity();
    return e && this.hovered ? { entity: e, distance: this.hovered.distance } : null;
  }

  // ---------------------------------------------------------------- world

  async openWorld(world: WorldState, regions: RegionSnapshot[] | null, companion: Companion | null): Promise<void> {
    if ((world.generator_version ?? 1) < MIN_RENDERABLE_GENERATOR_VERSION) {
      throw new Error(
        `World ${world.world_id} was generated with terrain function v${world.generator_version ?? 1}; ` +
          `its entities would not line up with the current terrain. Generate a new world.`,
      );
    }
    this.world = world;
    this.companion = companion;
    this.regions.clear();
    this.pending.clear();
    this.loadError = null;
    this.near.clearPreviews();
    this.gaussians.clear();
    this.assetsLoading.clear();
    this.onProgress?.("Compiling the world's geometry for the GPU…");
    this.near.setTextureSource(companion ? (asset, map) => companion.fetchTexture(asset, map) : null);
    await this.near.setWorld(world);
    this.grass.setWorld(grassFor(world, (x, z) => this.near.groundHeight(x, z)));
    const parity = this.near.terrainParity();
    this.terrainParity = Number.isFinite(parity.maxError) ? parity.maxError : null;
    const p = world.player.position as number[];
    this.player.position.set(p[0], this.near.groundHeight(p[0], p[2]) + EYE_HEIGHT, p[2]);
    this.player.yaw = world.player.yaw ?? 0;
    this.player.pitch = world.player.pitch ?? 0;
    if (regions) {
      for (const r of regions) this.regions.set(key(r.region.coord.rx, r.region.coord.rz), r);
      this.entitiesDirty = true;
    }
    await this.ensureRegions();
    this.pushEntities();
    this.placeSafely(!(world.player.yaw || world.player.pitch));
    void this.loadAssets();
  }

  /**
   * Make the spawn walkable: dry ground, a gentle slope and no overlap with solid objects, searching outward from the
   * stored position. A first visit (no saved view) also turns the player towards the most open view instead of
   * whatever the stored yaw of 0 happens to face.
   */
  private placeSafely(firstVisit: boolean): void {
    const water = (this.world?.spec.terrain.water_level ?? null) as number | null;
    const ok = (x: number, z: number) => {
      const g = this.near.groundHeight(x, z);
      if (water !== null && g < water + 0.3) return false;
      const slope = Math.hypot(this.near.groundHeight(x + 1, z) - this.near.groundHeight(x - 1, z),
        this.near.groundHeight(x, z + 1) - this.near.groundHeight(x, z - 1)) * 0.5;
      if (slope > 0.45) return false;
      const [cx, cz] = this.near.collide(x, g, z, 0.45, 1.85);
      return Math.hypot(cx - x, cz - z) < 0.05;
    };
    const p = this.player.position;
    search: for (let r = 0; r <= 120; r += 4) {
      const n = r === 0 ? 1 : Math.max(8, Math.round(r / 2));
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        const x = p.x + Math.cos(a) * r;
        const z = p.z + Math.sin(a) * r;
        if (ok(x, z)) {
          p.set(x, this.near.groundHeight(x, z) + EYE_HEIGHT, z);
          break search;
        }
      }
    }
    if (!firstVisit) return;
    let best = -Infinity;
    for (let i = 0; i < 24; i++) {
      const yaw = (i / 24) * Math.PI * 2;
      const dx = -Math.sin(yaw), dz = -Math.cos(yaw);
      let score = 0;
      for (let d = 15; d <= 240; d += 15) {
        const g = this.near.groundHeight(p.x + dx * d, p.z + dz * d);
        const above = Math.max(g, water ?? -Infinity) - p.y; // terrain rising above eye level blocks the view
        score -= Math.max(0, above + d * 0.03) / d;
        if (water !== null && g < water) score += 0.02; // a lake in view is worth facing
      }
      if (score > best) {
        best = score;
        this.player.yaw = yaw;
      }
    }
    this.player.pitch = -0.04;
  }

  // ---------------------------------------------------------------- Gaussian assets (diagnostic)

  /**
   * Load every Gaussian asset that belongs to a loaded region at exactly its current revision: from the companion
   * (RegionSnapshot.neural_asset, already revision-filtered by the server) or, offline, from neural-offline/index.json.
   */
  private async loadAssets(): Promise<void> {
    if (!this.world) return;
    const worldId = this.world.world_id;
    const revisionOf = (c: RegionCoord) => this.regions.get(key(c.rx, c.rz))?.region.revision ?? null;
    this.gaussians.syncRevisions(revisionOf);
    let manifests: NeuralAssetManifest[];
    if (this.companion) {
      manifests = [...this.regions.values()].map((s) => s.neural_asset).filter((m): m is NeuralAssetManifest => !!m);
    } else {
      if (this.offlineIndex === null) {
        try {
          const r = await fetch(`${import.meta.env.BASE_URL}neural-offline/index.json`);
          this.offlineIndex = r.ok ? ((await r.json()).assets as NeuralAssetManifest[]) : [];
        } catch {
          this.offlineIndex = [];
        }
      }
      manifests = this.offlineIndex.filter((m) => m.world_id === worldId);
    }
    for (const m of manifests) {
      const rev = revisionOf(m.coord);
      if (rev === null || this.gaussians.has(m.asset_id) || this.assetsLoading.has(m.asset_id)) continue;
      this.assetsLoading.add(m.asset_id);
      try {
        const bytes = this.companion
          ? await this.companion.fetchAsset(m.asset_id)
          : await (await fetch(`${import.meta.env.BASE_URL}neural-offline/${m.uri}`)).arrayBuffer();
        if (this.world?.world_id !== worldId) return;
        await this.gaussians.load(m, bytes, rev);
      } catch (error) {
        this.loadError = `Gaussian asset ${m.asset_id}: ${(error as Error).message}`;
      } finally {
        this.assetsLoading.delete(m.asset_id);
      }
    }
  }

  /**
   * Render only the loaded Gaussian assets from an exact pinhole camera (square, vertical fov) on black, through the
   * real display path (the canvas framebuffer, so Spark's color handling is the one users see), for parity checks
   * against the training rasterizer. Spark sorts asynchronously, so several frames are rendered before the pixels
   * are read back in the same task as the last draw. Returns base64 RGBA8, row 0 at the top.
   */
  async renderAssetView(cameraToWorld: number[], fovDeg: number, size: number): Promise<string> {
    this.stop();
    const camera = new THREE.PerspectiveCamera(fovDeg, 1, 0.1, 4000);
    camera.matrixAutoUpdate = false;
    camera.matrix.fromArray(cameraToWorld);
    camera.matrix.decompose(camera.position, camera.quaternion, camera.scale);
    camera.updateMatrixWorld(true);
    const composite = this.near.compositeMesh;
    const background = this.scene.background;
    composite.visible = false;
    this.gaussians.setVisible(true);
    this.scene.background = new THREE.Color(0, 0, 0);
    this.renderer.setSize(size, size, false);
    const pixels = new Uint8Array(size * size * 4);
    const gl = this.renderer.getContext();
    try {
      for (let i = 0; i < 12; i++) {
        this.renderer.render(this.scene, camera);
        await new Promise((r) => setTimeout(r, 30));
      }
      this.renderer.render(this.scene, camera);
      gl.readPixels(0, 0, size, size, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    } finally {
      composite.visible = true;
      this.scene.background = background;
      this.start(); // the loop restores the canvas size on its first tick
    }
    const flipped = new Uint8Array(pixels.length);
    const row = size * 4;
    for (let y = 0; y < size; y++) flipped.set(pixels.subarray((size - 1 - y) * row, (size - y) * row), y * row);
    return toBase64(flipped);
  }

  setGaussianMode(mode: GaussianMode): void {
    this.gaussianMode = mode;
  }

  /** Fly the camera to a viewpoint that frames a region (21 degrees elevation, like the held-out views). */
  viewAsset(coord: RegionCoord): void {
    const cx = (coord.rx + 0.5) * REGION_SIZE;
    const cz = (coord.rz + 0.5) * REGION_SIZE;
    const cy = this.near.groundHeight(cx, cz);
    const d = 120;
    const az = (200 * Math.PI) / 180;
    const el = (21 * Math.PI) / 180;
    const eye = new THREE.Vector3(cx + d * Math.cos(el) * Math.cos(az), cy + d * Math.sin(el), cz + d * Math.cos(el) * Math.sin(az));
    const dir = new THREE.Vector3(cx, cy, cz).sub(eye).normalize();
    this.player.flying = true;
    this.player.position.copy(eye);
    this.player.yaw = Math.atan2(-dir.x, -dir.z);
    this.player.pitch = Math.asin(dir.y);
  }

  private currentRegion(): RegionCoord {
    return { rx: Math.floor(this.player.position.x / REGION_SIZE), rz: Math.floor(this.player.position.z / REGION_SIZE) };
  }

  /** Request any region within the near radius that is not loaded yet (live worlds only). */
  private async ensureRegions(): Promise<void> {
    if (!this.world || !this.companion) return;
    const here = this.currentRegion();
    const wanted: RegionCoord[] = [];
    for (let dx = -NEAR_REGION_RADIUS; dx <= NEAR_REGION_RADIUS; dx++) {
      for (let dz = -NEAR_REGION_RADIUS; dz <= NEAR_REGION_RADIUS; dz++) {
        const k = key(here.rx + dx, here.rz + dz);
        if (!this.regions.has(k) && !this.pending.has(k)) wanted.push({ rx: here.rx + dx, rz: here.rz + dz });
      }
    }
    if (!wanted.length) return;
    this.lastRegionAttempt = performance.now();
    wanted.forEach((c) => this.pending.add(key(c.rx, c.rz)));
    const worldId = this.world.world_id;
    try {
      const snaps = await this.companion.loadRegions(worldId, wanted);
      if (this.world?.world_id !== worldId) return;
      for (const s of snaps) this.regions.set(key(s.region.coord.rx, s.region.coord.rz), s);
      this.entitiesDirty = true;
      this.loadError = null;
      void this.loadAssets();
    } catch (error) {
      this.loadError = (error as Error).message;
    } finally {
      wanted.forEach((c) => this.pending.delete(key(c.rx, c.rz)));
    }
  }

  private pushEntities(): void {
    const here = this.currentRegion();
    const keep = NEAR_REGION_RADIUS + 1;
    const entities: WorldEntity[] = [];
    for (const [k, snap] of this.regions) {
      const c = snap.region.coord;
      if (this.companion && (Math.abs(c.rx - here.rx) > keep + 1 || Math.abs(c.rz - here.rz) > keep + 1)) {
        this.regions.delete(k); // far behind the player: drop, it will be re-requested when needed
        continue;
      }
      if (Math.abs(c.rx - here.rx) <= keep && Math.abs(c.rz - here.rz) <= keep) entities.push(...snap.entities);
    }
    this.near.setEntities(entities);
    this.entitiesDirty = false;
  }

  // ---------------------------------------------------------------- settings

  setQuality(q: QualityName): void {
    this.quality = q;
    this.near.setQuality(QUALITY[q]);
  }
  setDebugView(v: DebugView): void {
    this.debugView = v;
    this.near.setDebugView(v);
  }

  hoveredEntity(): WorldEntity | null {
    if (!this.hovered) return null;
    for (const snap of this.regions.values()) {
      const e = snap.entities.find((x) => x.id === this.hovered!.entityId);
      if (e) return e;
    }
    return null;
  }

  // ---------------------------------------------------------------- loop

  start(): void {
    this.last = performance.now();
    let lastRegion = "";
    const tick = () => {
      this.raf = requestAnimationFrame(tick);
      const now = performance.now();
      const dt = Math.min(0.1, (now - this.last) / 1000);
      this.frameTimes.push(now - this.last);
      if (this.frameTimes.length > 240) this.frameTimes.shift();
      this.last = now;

      const w = this.canvas.clientWidth;
      const h = this.canvas.clientHeight;
      if (this.canvas.width !== w || this.canvas.height !== h) {
        this.renderer.setSize(w, h, false);
        this.camera.aspect = w / Math.max(1, h);
        this.camera.updateProjectionMatrix();
      }
      this.near.setSize(w, h);

      if (this.world) {
        const water = (this.world.spec.terrain.water_level ?? null) as number | null;
        if (this.attract) {
          // landing-page backdrop: slow cinematic orbit over the world, no player
          const t = this.time * 0.025;
          const r = 150;
          const cx = this.attract.x + Math.cos(t) * r;
          const cz = this.attract.z + Math.sin(t) * r;
          const ground = Math.max(this.near.groundHeight(cx, cz), water ?? -Infinity);
          this.camera.position.set(cx, Math.max(ground + 28, this.attract.y + 34), cz);
          this.camera.lookAt(this.attract.x, this.attract.y + 6, this.attract.z);
        } else {
          this.player.update(dt, (x, z) => this.near.groundHeight(x, z), water,
            (x, feet, z, r, h) => this.near.collide(x, feet, z, r, h));
          this.player.apply(this.camera);
        }
        const here = this.currentRegion();
        const hk = key(here.rx, here.rz);
        if (hk !== lastRegion) {
          lastRegion = hk;
          this.entitiesDirty = true;
          void this.ensureRegions();
        }
        // a failed region request is retried every 3 s instead of waiting for the player to change region
        if (this.loadError && this.pending.size === 0 && now - this.lastRegionAttempt > 3000) void this.ensureRegions();
        if (this.entitiesDirty) this.pushEntities();

        this.time += dt;
        // ripples where the player moves through water
        const pl = this.player;
        if (pl.waterContact !== null && pl.speed > 0.6 && now - this.lastRipple > 280) {
          this.lastRipple = now;
          this.near.addRipple(pl.position.x, pl.position.z, pl.locomotion === "swimming" ? 1.2 : 0.7);
        }
        this.onFrame?.(dt);
        this.fire.wind = (this.world.environment.wind ?? [1, 0]) as [number, number];
        this.fire.setView(this.camera, w, h);
        this.fire.update(dt, this.time, this.fireSources);
        this.grass.update(this.camera, this.player.position, this.time, (this.world.environment.wind ?? [1, 0]) as [number, number], this.near.sky.sunDir);
        this.renderFrame(w, h);
        this.frames += 1;

        if (!this.attract && now - this.lastPick > 120) {
          this.lastPick = now;
          const f = this.player.forward();
          this.hovered = this.near.pick(this.player.position.toArray() as [number, number, number], [f.x, f.y, f.z], 60);
        }
      }
      if (this.onStatus && now - this.lastStatus > 250) {
        this.lastStatus = now;
        this.onStatus(this.status());
      }
    };
    tick();
  }

  stop(): void {
    cancelAnimationFrame(this.raf);
  }

  /**
   * sdf:      pass A (ray march) + pass B (composite), the Chunk 2 output; Gaussian meshes hidden.
   * gaussian: Gaussian assets only, on a flat dark background; no SDF pass at all.
   * split:    same camera, left half SDF, right half Gaussian assets (scissored), for direct comparison.
   */
  private renderFrame(w: number, h: number): void {
    const composite = this.near.compositeMesh;
    if (this.gaussianMode === "sdf") {
      this.gaussians.setVisible(false);
      composite.visible = true;
      this.scene.background = null;
      this.near.renderToTarget(this.camera);
      const post = this.postEnabled && this.debugView === "final";
      this.near.setLinearOutput(post);
      if (!post) {
        this.renderer.render(this.scene, this.camera);
        return;
      }
      this.post.setSize(w, h);
      this.grass.mesh.visible = this.near.ready;
      this.renderer.setRenderTarget(this.post.hdr);
      this.renderer.render(this.scene, this.camera);
      this.renderer.setRenderTarget(null);
      const sky = this.near.sky;
      // night scenes are exposed up (eyes adapt) so emissive objects read as glowing against a dark world
      const exposure = 1.05 + 1.4 * (1 - sky.daylight);
      // the bloom chain sums five scales, so a small weight already gives a soft halo
      this.post.render(this.camera, { exposure, bloom: 0.045, shafts: 0.6, sunWorldDir: sky.sunDir, sunColor: sky.sun }, 1 / 60);
      return;
    }
    this.near.setLinearOutput(false);
    this.grass.mesh.visible = false;
    if (this.gaussianMode === "gaussian") {
      composite.visible = false;
      this.gaussians.setVisible(true);
      this.scene.background = this.gaussianBackground;
      this.renderer.render(this.scene, this.camera);
      return;
    }
    const half = Math.floor(w / 2);
    this.near.renderToTarget(this.camera);
    this.renderer.setScissorTest(true);
    this.renderer.setScissor(0, 0, half, h);
    composite.visible = true;
    this.gaussians.setVisible(false);
    this.scene.background = null;
    this.renderer.render(this.scene, this.camera);
    this.renderer.setScissor(half, 0, w - half, h);
    composite.visible = false;
    this.gaussians.setVisible(true);
    this.scene.background = this.gaussianBackground;
    this.renderer.render(this.scene, this.camera);
    this.renderer.setScissorTest(false);
    composite.visible = true;
  }

  frameCount(): number {
    return this.frames;
  }

  status(): EngineStatus {
    const sorted = [...this.frameTimes].sort((a, b) => a - b);
    const at = (q: number) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] : 0);
    const avg = sorted.length ? sorted.reduce((a, b) => a + b, 0) / sorted.length : 0;
    const s = this.near.stats();
    const here = this.currentRegion();
    const hoveredEntity = this.hoveredEntity();
    return {
      fps: avg > 0 ? 1000 / avg : 0,
      frameMs: { avg, p50: at(0.5), p95: at(0.95), max: sorted[sorted.length - 1] ?? 0 },
      nearGpuMs: s.gpuMs,
      internalSize: s.internalSize,
      canvasSize: [this.canvas.width, this.canvas.height],
      instances: s.instances,
      archetypes: s.archetypes,
      materials: s.materials,
      grid: `${s.gridDim[0]}x${s.gridDim[1]} @ ${s.gridCell} m, max ${s.gridMaxPerCell}/cell`,
      gridOverflow: s.gridOverflow,
      shaderChars: s.shaderChars,
      compileMs: s.compileMs,
      textures: s.textures,
      compileErrors: this.near.compileErrors,
      regionsLoaded: this.regions.size,
      regionsPending: this.pending.size,
      regionRevision: this.regions.get(key(here.rx, here.rz))?.region.revision ?? null,
      worldRevision: this.world?.revision ?? null,
      worldId: this.world?.world_id ?? null,
      position: [this.player.position.x, this.player.position.y, this.player.position.z],
      region: [here.rx, here.rz],
      flying: this.player.flying,
      pointerLocked: this.player.locked,
      quality: this.quality,
      debugView: this.debugView,
      gpuRenderer: this.gpuRenderer,
      terrainParityM: this.terrainParity,
      farField: this.far ? "active" : "SDF only (Gaussian far field: experimental, not in gameplay)",
      gaussianMode: this.gaussianMode,
      gaussianAssets: this.gaussians.assets(),
      gaussianSplats: this.gaussians.stats().splats,
      hovered: hoveredEntity && this.hovered ? { entity: hoveredEntity, distance: this.hovered.distance } : null,
      previews: this.near.previewCount(),
      loadError: this.loadError,
    };
  }

  // ---------------------------------------------------------------- reconstruction capture

  /**
   * Prepare a reconstruction capture of one region: pause the frame loop, load the region and its 8 neighbours
   * (fresh from the companion for live worlds, so the recorded revision is the authoritative one at capture time),
   * put their entities into the near field, and compute the clip box and camera rig.
   */
  async beginCapture(coord: RegionCoord, size = 256, supersample = 2): Promise<CaptureSession> {
    if (!this.world) throw new Error("no world loaded");
    this.stop();
    const coords: RegionCoord[] = [];
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) coords.push({ rx: coord.rx + dx, rz: coord.rz + dz });
    const snaps = this.companion
      ? await this.companion.loadRegions(this.world.world_id, coords)
      : coords.map((c) => this.regions.get(key(c.rx, c.rz))).filter((s): s is RegionSnapshot => !!s);
    const target = snaps.find((s) => s.region.coord.rx === coord.rx && s.region.coord.rz === coord.rz);
    if (!target) throw new Error(`region ${coord.rx},${coord.rz} is not available`);
    this.near.setEntities(snaps.flatMap((s) => s.entities));

    const x0 = coord.rx * REGION_SIZE - CLIP_MARGIN_M;
    const x1 = (coord.rx + 1) * REGION_SIZE + CLIP_MARGIN_M;
    const z0 = coord.rz * REGION_SIZE - CLIP_MARGIN_M;
    const z1 = (coord.rz + 1) * REGION_SIZE + CLIP_MARGIN_M;
    let lo = Infinity;
    let hi = -Infinity;
    const N = 32;
    for (let i = 0; i <= N; i++) {
      for (let j = 0; j <= N; j++) {
        const h = this.near.groundHeight(x0 + ((x1 - x0) * i) / N, z0 + ((z1 - z0) * j) / N);
        lo = Math.min(lo, h);
        hi = Math.max(hi, h);
      }
    }
    const water = (this.world.spec.terrain.water_level ?? null) as number | null;
    if (water !== null) {
      lo = Math.min(lo, water);
      hi = Math.max(hi, water);
    }
    hi = Math.max(hi, this.near.instanceTop(x0, x1, z0, z1));
    const clipMin: Vec3 = [x0, lo - 6, z0];
    const clipMax: Vec3 = [x1, hi + 1.5, z1];
    const rig = buildRig(clipMin, clipMax);
    const camera = new THREE.PerspectiveCamera(rig.fovDeg, 1, rig.near, rig.far);
    const f = size / 2 / Math.tan(((rig.fovDeg / 2) * Math.PI) / 180);
    const views: CaptureView[] = rig.views.map((v) => {
      camera.position.set(...v.eye);
      camera.lookAt(...v.target);
      camera.updateMatrixWorld();
      return {
        index: v.index, width: size, height: size, fx: f, fy: f, cx: size / 2, cy: size / 2,
        camera_to_world: [...camera.matrixWorld.elements], near: rig.near, far: rig.far, holdout: v.holdout,
      };
    });
    const { views: _omit, ...rigInfo } = rig;
    const session: CaptureSession = {
      world_id: this.world.world_id,
      coord,
      region_revision: target.region.revision,
      provider: "browser_sdf",
      views,
      capture: {
        region_origin: [coord.rx * REGION_SIZE, 0, coord.rz * REGION_SIZE],
        clip_min: clipMin,
        clip_max: clipMax,
        supersample,
        renderer: "near-field ray marcher (capture mode)",
        fog: false,
        entities: snaps.reduce((n, s) => n + s.entities.length, 0),
        rig: rigInfo,
        gpu: this.gpuRenderer,
      },
    };
    this.captureState = { session, rig, camera, size };
    return session;
  }

  /** Render one view of the prepared capture; pixels are returned base64-encoded for transport out of the page. */
  captureView(index: number): { index: number; rgba: string; depth: string; hitFraction: number; unobservedFraction: number; ms: number } {
    const st = this.captureState;
    if (!st) throw new Error("beginCapture first");
    const v = st.rig.views[index];
    const t0 = performance.now();
    st.camera.position.set(...v.eye);
    st.camera.lookAt(...v.target);
    const c = st.session.capture;
    const out = this.near.captureView(st.camera, st.size, new THREE.Vector3(...c.clip_min), new THREE.Vector3(...c.clip_max), c.supersample);
    return {
      index,
      rgba: toBase64(out.rgba),
      depth: toBase64(new Uint8Array(out.depth.buffer)),
      hitFraction: out.hitFraction,
      unobservedFraction: out.unobservedFraction,
      ms: performance.now() - t0,
    };
  }

  /** Restore the player's entity set and resume the frame loop. */
  endCapture(): void {
    this.captureState = null;
    this.entitiesDirty = true;
    this.start();
  }

  dispose(): void {
    this.stop();
    this.player.dispose();
    this.near.dispose();
    this.renderer.dispose();
  }
}
