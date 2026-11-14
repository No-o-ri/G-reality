/**
 * Frame loop and world wiring. One WebGLRenderer, one scene, one camera, one depth buffer:
 * the near-field SDF pass renders into its target, the composite quad in `scene` writes color + depth
 * to the canvas, and (from Chunk 3) Gaussian-splat meshes added to the same scene depth-test against it.
 */
import * as THREE from "three";
import type { RegionCoord, RegionSnapshot, WorldEntity, WorldState } from "@contracts";
import { EYE_HEIGHT, Player } from "../controls/Player";
import type { DebugView, FarFieldRenderer, HybridFrame, QualityName } from "../rendering/hybrid/types";
import { QUALITY } from "../rendering/hybrid/types";
import { NearField, type PickResult } from "../rendering/nearfield/NearField";
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
  hovered: { entity: WorldEntity; distance: number } | null;
  previews: number;
  loadError: string | null;
}

const key = (rx: number, rz: number) => `${rx},${rz}`;

export class Engine implements HybridFrame {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(70, 1, 0.1, 4000);
  readonly near: NearField;
  far: FarFieldRenderer | null = null; // Gaussian far field arrives in Chunk 3; nothing stands in for it
  readonly player: Player;

  world: WorldState | null = null;
  private companion: Companion | null = null;
  private regions = new Map<string, RegionSnapshot>();
  private pending = new Set<string>();
  private entitiesDirty = false;
  private quality: QualityName = "high";
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
    this.near.setWorld(world);
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
        this.player.update(dt, (x, z) => this.near.groundHeight(x, z), water);
        this.player.apply(this.camera);
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

        this.near.renderToTarget(this.camera); // pass A
        this.renderer.render(this.scene, this.camera); // pass B (+ C once the far field exists)
        this.frames += 1;

        if (now - this.lastPick > 120) {
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
      farField: this.far ? "active" : "not implemented (Chunk 3)",
      hovered: hoveredEntity && this.hovered ? { entity: hoveredEntity, distance: this.hovered.distance } : null,
      previews: this.near.previewCount(),
      loadError: this.loadError,
    };
  }

  dispose(): void {
    this.stop();
    this.player.dispose();
    this.near.dispose();
    this.renderer.dispose();
  }
}
