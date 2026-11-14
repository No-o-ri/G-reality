/**
 * WebGL2 SDF near-field renderer (three.js used for context, targets and the shared scene).
 *
 * World → program:   terrain uniforms + one generated GLSL function per archetype (compile on setWorld)
 * Entities → data:   instance texture + acceleration grid texture (setEntities, no recompilation)
 */
import * as THREE from "three";
import type { GeometryDefinition, MaterialDefinition, TerrainDefinition, WorldEntity, WorldState } from "@contracts";
import { compileScene, type CompiledScene } from "../../sdf/compiler";
import { compileCpu, type SdfFn } from "../../sdf/cpu";
import { measureArchetype } from "../../sdf/measure";
import { terrainHeight as cpuTerrainHeight } from "../../sdf/terrain";
import type { DebugView, NearFieldRenderer, NearFieldStats, QualityProfile } from "../hybrid/types";
import { DEBUG_VIEWS, QUALITY } from "../hybrid/types";
import { buildGrid, buildInstances, GRID_CAPACITY, INSTANCE_TEXELS, type ArchetypeRuntime, type GridData, type InstanceData } from "./buffers";
import { buildMarchFragment, COMPOSITE_FRAG, FULLSCREEN_VERT, TERRAIN_PROBE_FRAG } from "./shader";

function fullscreenTriangle(): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
  return g;
}

function dataTexture(data: Float32Array, width: number, height: number): THREE.DataTexture {
  const t = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.FloatType);
  t.minFilter = t.magFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

/** Sun direction (unit, +Y up) from EnvironmentState: uses the stored direction, lowered at dawn/dusk. */
function sunFrom(env: WorldState["environment"]): THREE.Vector3 {
  const h = env.time_of_day_h ?? 10;
  const elevation = Math.max(0.06, Math.sin(((h - 6) / 12) * Math.PI));
  const azimuth = ((h - 6) / 12) * Math.PI;
  return new THREE.Vector3(Math.cos(azimuth) * 0.8, elevation, 0.45).normalize();
}

export function terrainStepFactor(t: TerrainDefinition): number {
  const octaves = t.octaves ?? 5;
  const slope = (t.amplitude ?? 30) * (t.frequency ?? 0.008) * octaves * 1.5 * (1 + (t.ridge ?? 0));
  return Math.min(0.9, Math.max(0.25, 1 / Math.sqrt(1 + slope * slope)));
}

class GpuTimer {
  private ext: unknown;
  private pending: WebGLQuery[] = [];
  private active: WebGLQuery | null = null;
  ms: number | null = null;

  constructor(private gl: WebGL2RenderingContext) {
    this.ext = gl.getExtension("EXT_disjoint_timer_query_webgl2");
  }
  get supported(): boolean {
    return !!this.ext;
  }
  begin(): void {
    if (!this.ext || this.active || this.pending.length > 4) return;
    const q = this.gl.createQuery();
    if (!q) return;
    this.gl.beginQuery((this.ext as { TIME_ELAPSED_EXT: number }).TIME_ELAPSED_EXT, q);
    this.active = q;
  }
  end(): void {
    if (!this.ext || !this.active) return;
    this.gl.endQuery((this.ext as { TIME_ELAPSED_EXT: number }).TIME_ELAPSED_EXT);
    this.pending.push(this.active);
    this.active = null;
  }
  poll(): void {
    if (!this.ext || !this.pending.length) return;
    const gl = this.gl;
    const q = this.pending[0];
    if (!gl.getQueryParameter(q, gl.QUERY_RESULT_AVAILABLE)) return;
    const disjoint = gl.getParameter((this.ext as { GPU_DISJOINT_EXT: number }).GPU_DISJOINT_EXT);
    if (!disjoint) {
      const ns = gl.getQueryParameter(q, gl.QUERY_RESULT) as number;
      this.ms = this.ms === null ? ns / 1e6 : this.ms * 0.8 + (ns / 1e6) * 0.2;
    }
    gl.deleteQuery(q);
    this.pending.shift();
  }
}

export interface PickResult {
  entityId: string;
  distance: number;
  point: [number, number, number];
}

export class NearField implements NearFieldRenderer {
  readonly target: THREE.WebGLRenderTarget;
  /** Opaque full-screen mesh to add to the shared scene; draws first and writes depth. */
  readonly compositeMesh: THREE.Mesh;
  compileErrors: string[] = [];

  private readonly marchScene = new THREE.Scene();
  private readonly marchCamera = new THREE.Camera();
  private marchMaterial: THREE.RawShaderMaterial | null = null;
  private readonly marchMesh: THREE.Mesh;
  private readonly compositeMaterial: THREE.RawShaderMaterial;
  private readonly timer: GpuTimer;

  private world: WorldState | null = null;
  private scene: CompiledScene | null = null;
  private runtimes = new Map<string, ArchetypeRuntime>();
  private cpuFns = new Map<string, SdfFn>();
  private defs = new Map<string, GeometryDefinition>();
  private entities: WorldEntity[] = [];
  private inst: InstanceData | null = null;
  private grid: GridData | null = null;
  private instTex: THREE.DataTexture | null = null;
  private gridTex: THREE.DataTexture | null = null;
  private quality: QualityProfile = QUALITY.high;
  private debugView: DebugView = "final";
  private compileMs = 0;
  private shaderChars = 0;
  private readonly viewProj = new THREE.Matrix4();
  private startTime = performance.now();
  /** preview-only overrides of entity geometry parameters (inspector); never sent to the server */
  private overrides = new Map<string, Record<string, number>>();

  constructor(private readonly renderer: THREE.WebGLRenderer) {
    const gl = renderer.getContext() as WebGL2RenderingContext;
    if (!gl.getExtension("EXT_color_buffer_float")) {
      throw new Error("This GPU/browser lacks EXT_color_buffer_float, which the near-field target requires.");
    }
    this.timer = new GpuTimer(gl);
    this.target = new THREE.WebGLRenderTarget(4, 4, {
      type: THREE.HalfFloatType,
      format: THREE.RGBAFormat,
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      depthBuffer: true,
      stencilBuffer: false,
    });
    this.target.depthTexture = new THREE.DepthTexture(4, 4);
    this.target.depthTexture.type = THREE.FloatType;
    this.target.depthTexture.minFilter = this.target.depthTexture.magFilter = THREE.NearestFilter;

    const tri = fullscreenTriangle();
    this.marchMesh = new THREE.Mesh(tri);
    this.marchMesh.frustumCulled = false;
    this.marchScene.add(this.marchMesh);

    this.compositeMaterial = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: FULLSCREEN_VERT,
      fragmentShader: COMPOSITE_FRAG,
      uniforms: {
        tColor: { value: this.target.texture },
        tDepth: { value: this.target.depthTexture },
        uExposure: { value: 1.0 },
        uRaw: { value: 0 },
      },
      depthTest: true,
      depthFunc: THREE.AlwaysDepth,
      depthWrite: true,
    });
    this.compositeMesh = new THREE.Mesh(tri, this.compositeMaterial);
    this.compositeMesh.frustumCulled = false;
    this.compositeMesh.renderOrder = -1000;
  }

  // ---------------------------------------------------------------- world / program

  setWorld(world: WorldState): void {
    const t0 = performance.now();
    this.world = world;
    const terrain = world.spec.terrain;
    const defs = world.spec.archetypes as GeometryDefinition[];
    const extra: MaterialDefinition[] = [terrain.ground_material, terrain.slope_material];
    if (terrain.peak_material) extra.push(terrain.peak_material);
    if (terrain.water_material && terrain.water_level != null) extra.push(terrain.water_material);
    const scene = compileScene(defs, extra);
    this.scene = scene;
    const base = scene.materials.length - extra.length;
    const slots = {
      ground: base,
      cliff: base + 1,
      peak: terrain.peak_material ? base + 2 : -1,
      water: terrain.water_material && terrain.water_level != null ? base + (terrain.peak_material ? 3 : 2) : -1,
    };

    this.runtimes.clear();
    this.cpuFns.clear();
    this.defs.clear();
    for (const def of defs) {
      const compiled = scene.archetypes.get(def.id)!;
      const specs = def.parameters ?? {};
      const widest = Object.fromEntries(Object.entries(specs).map(([k, v]) => [k, v.max]));
      this.runtimes.set(def.id, {
        compiled,
        base: measureArchetype(def),
        widest: measureArchetype(def, widest),
        paramDefaults: compiled.paramNames.map((n) => specs[n].default),
        paramMin: compiled.paramNames.map((n) => specs[n].min),
        paramMax: compiled.paramNames.map((n) => specs[n].max),
      });
      this.cpuFns.set(def.id, compileCpu(def));
      this.defs.set(def.id, def);
    }

    const fragment = buildMarchFragment(scene.glsl, slots);
    this.shaderChars = fragment.length;
    const env = world.environment;
    const seed = world.seed;
    const octaves = terrain.octaves ?? 5;
    let norm = 0;
    for (let i = 0; i < octaves; i++) norm += 0.5 ** i;
    const basin = (terrain.basin_center ?? null) as number[] | null;
    const material = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: FULLSCREEN_VERT,
      fragmentShader: fragment,
      depthTest: true,
      depthFunc: THREE.AlwaysDepth,
      depthWrite: true,
      uniforms: {
        uCamWorld: { value: new THREE.Matrix4() },
        uProjInv: { value: new THREE.Matrix4() },
        uViewProj: { value: new THREE.Matrix4() },
        uCamPos: { value: new THREE.Vector3() },
        uTime: { value: 0 },
        uFar: { value: this.quality.farDistance },
        uMaxSteps: { value: this.quality.maxSteps },
        uShadowSteps: { value: this.quality.shadowSteps },
        uDebugView: { value: DEBUG_VIEWS.indexOf(this.debugView) },
        uTerrainStep: { value: terrainStepFactor(terrain) },
        uPeakHeight: { value: Math.min(terrain.peak_height ?? 1e9, 1e9) },
        uInst: { value: null },
        uGrid: { value: null },
        uGridInfo: { value: new THREE.Vector4(0, 0, 8, 0) },
        uGridDim: { value: new THREE.Vector2(1, 1) },
        uTerrainA: { value: new THREE.Vector4(terrain.base_height ?? 0, terrain.amplitude ?? 30, terrain.frequency ?? 0.008, terrain.ridge ?? 0) },
        uTerrainB: { value: new THREE.Vector4(terrain.warp ?? 0, octaves, norm, terrain.water_level ?? -1e9) },
        uTerrainBasin: { value: new THREE.Vector4(basin ? basin[0] : 0, basin ? basin[1] : 0, basin ? (terrain.basin_radius ?? 0) : 0, terrain.basin_depth ?? 0) },
        uSeedOffset: { value: new THREE.Vector2(seed & 1023, (seed >> 10) & 1023) },
        uSunDir: { value: sunFrom(env) },
        uSunColor: { value: new THREE.Vector3(...((env.sun_color ?? [1, 0.95, 0.85]) as [number, number, number])) },
        uSkyColor: { value: new THREE.Vector3(...((env.sky_color ?? [0.45, 0.62, 0.85]) as [number, number, number])) },
        uFogColor: { value: new THREE.Vector3(...((env.fog_color ?? [0.7, 0.76, 0.82]) as [number, number, number])) },
        uAmbient: { value: env.ambient ?? 0.25 },
        uFog: { value: new THREE.Vector3(env.fog_density ?? 0.004, env.fog_height_falloff ?? 0.02, env.fog_base_height ?? 0) },
      },
    });
    this.marchMaterial?.dispose();
    this.marchMaterial = material;
    this.marchMesh.material = material;
    // uGridDim is an ivec2: three uploads Vector2 with uniform2iv only for integer typed uniforms it detects
    // from the program, which it does (it inspects the active uniform type).
    this.compileNow();
    this.compileMs = performance.now() - t0;
    this.setEntities(this.entities);
  }

  /** Force shader compilation now and collect any errors instead of failing silently at first draw. */
  private compileNow(): void {
    this.compileErrors = [];
    const previous = this.renderer.debug.onShaderError;
    this.renderer.debug.onShaderError = (gl, program, vs, fs) => {
      const log = [gl.getProgramInfoLog(program), gl.getShaderInfoLog(vs), gl.getShaderInfoLog(fs)]
        .filter((s): s is string => !!s && s.trim().length > 0)
        .join("\n");
      this.compileErrors.push(log || "unknown shader error");
    };
    try {
      this.renderer.compile(this.marchScene, this.marchCamera);
    } finally {
      this.renderer.debug.onShaderError = previous;
    }
  }

  // ---------------------------------------------------------------- entities / data

  setEntities(entities: WorldEntity[]): void {
    this.entities = entities;
    if (!this.marchMaterial) return;
    const withOverrides = this.overrides.size
      ? entities.map((e) => {
          const o = this.overrides.get(e.id);
          return o ? { ...e, geometry_params: { ...(e.geometry_params ?? {}), ...o } } : e;
        })
      : entities;
    const inst = buildInstances(withOverrides, this.runtimes);
    const grid = buildGrid(inst);
    this.inst = inst;
    this.grid = grid;
    this.instTex?.dispose();
    this.gridTex?.dispose();
    this.instTex = dataTexture(inst.data, INSTANCE_TEXELS, Math.max(1, entities.length));
    this.gridTex = dataTexture(grid.data, grid.dimX * (GRID_CAPACITY / 4), grid.dimZ);
    const u = this.marchMaterial.uniforms;
    u.uInst.value = this.instTex;
    u.uGrid.value = this.gridTex;
    u.uGridInfo.value.set(grid.originX, grid.originZ, grid.cell, inst.count);
    u.uGridDim.value.set(grid.dimX, grid.dimZ);
  }

  /** Inspector preview: change geometry parameters of one entity locally. Not persisted, not authoritative. */
  setPreviewParams(entityId: string, params: Record<string, number> | null): void {
    if (params) this.overrides.set(entityId, params);
    else this.overrides.delete(entityId);
    this.setEntities(this.entities);
  }

  clearPreviews(): void {
    if (!this.overrides.size) return;
    this.overrides.clear();
    this.setEntities(this.entities);
  }

  previewCount(): number {
    return this.overrides.size;
  }

  setEnvironment(patch: { fogDensity?: number; timeOfDay?: number }): void {
    if (!this.marchMaterial || !this.world) return;
    const u = this.marchMaterial.uniforms;
    if (patch.fogDensity !== undefined) u.uFog.value.x = patch.fogDensity;
    if (patch.timeOfDay !== undefined) u.uSunDir.value.copy(sunFrom({ ...this.world.environment, time_of_day_h: patch.timeOfDay }));
  }

  setQuality(profile: QualityProfile): void {
    this.quality = profile;
    if (!this.marchMaterial) return;
    const u = this.marchMaterial.uniforms;
    u.uFar.value = profile.farDistance;
    u.uMaxSteps.value = profile.maxSteps;
    u.uShadowSteps.value = profile.shadowSteps;
  }

  setDebugView(view: DebugView): void {
    this.debugView = view;
    if (this.marchMaterial) this.marchMaterial.uniforms.uDebugView.value = DEBUG_VIEWS.indexOf(view);
    this.compositeMaterial.uniforms.uRaw.value = view === "final" ? 0 : 1;
  }

  setSize(width: number, height: number): void {
    const w = Math.max(2, Math.round(width * this.quality.internalScale));
    const h = Math.max(2, Math.round(height * this.quality.internalScale));
    if (this.target.width !== w || this.target.height !== h) this.target.setSize(w, h);
  }

  // ---------------------------------------------------------------- frame

  /** Pass A: ray march into the near-field target using the given scene camera. */
  renderToTarget(camera: THREE.PerspectiveCamera): void {
    if (!this.marchMaterial) return;
    const u = this.marchMaterial.uniforms;
    camera.updateMatrixWorld();
    u.uCamWorld.value.copy(camera.matrixWorld);
    u.uProjInv.value.copy(camera.projectionMatrixInverse);
    this.viewProj.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    u.uViewProj.value.copy(this.viewProj);
    u.uCamPos.value.setFromMatrixPosition(camera.matrixWorld);
    u.uTime.value = (performance.now() - this.startTime) / 1000;
    this.timer.poll();
    this.timer.begin();
    this.renderer.setRenderTarget(this.target);
    this.renderer.render(this.marchScene, this.marchCamera);
    this.renderer.setRenderTarget(null);
    this.timer.end();
  }

  stats(): NearFieldStats {
    return {
      instances: this.inst?.count ?? 0,
      archetypes: this.scene?.archetypes.size ?? 0,
      materials: this.scene?.materials.length ?? 0,
      gridDim: [this.grid?.dimX ?? 0, this.grid?.dimZ ?? 0],
      gridCell: this.grid?.cell ?? 0,
      gridOverflow: this.grid?.overflow ?? 0,
      gridMaxPerCell: this.grid?.maxPerCell ?? 0,
      skippedEntities: this.inst?.skipped ?? 0,
      internalSize: [this.target.width, this.target.height],
      gpuMs: this.timer.ms,
      shaderChars: this.shaderChars,
      compileMs: this.compileMs,
    };
  }

  generatedGlsl(): string {
    return this.scene?.glsl ?? "";
  }

  // ---------------------------------------------------------------- CPU queries (same definitions)

  groundHeight(x: number, z: number): number {
    if (!this.world) return 0;
    return cpuTerrainHeight(this.world.spec.terrain, this.world.seed, x, z);
  }

  /** Sphere-trace the entity SDFs on the CPU along a ray (used for the crosshair inspector). */
  pick(origin: [number, number, number], dir: [number, number, number], maxDistance = 60): PickResult | null {
    if (!this.inst) return null;
    const byId = new Map(this.entities.map((e) => [e.id, e]));
    let best: PickResult | null = null;
    for (let i = 0; i < this.inst.count; i++) {
      const cx = this.inst.spheres[i * 4] - origin[0];
      const cy = this.inst.spheres[i * 4 + 1] - origin[1];
      const cz = this.inst.spheres[i * 4 + 2] - origin[2];
      const r = this.inst.spheres[i * 4 + 3];
      const along = cx * dir[0] + cy * dir[1] + cz * dir[2];
      const perp2 = cx * cx + cy * cy + cz * cz - along * along;
      if (perp2 > r * r || along < -r || along - r > (best?.distance ?? maxDistance)) continue;
      const entity = byId.get(this.inst.ids[i]);
      const fn = entity && this.cpuFns.get(entity.geometry_id);
      if (!entity || !fn) continue;
      const rt = this.runtimes.get(entity.geometry_id)!;
      const merged = { ...(entity.geometry_params ?? {}), ...(this.overrides.get(entity.id) ?? {}) } as Record<string, number>;
      const pos = entity.transform.position as number[];
      const q = (entity.transform.rotation ?? [0, 0, 0, 1]) as number[];
      const s = entity.transform.scale ?? 1;
      let t = Math.max(0, along - r);
      const tEnd = Math.min(along + r, best?.distance ?? maxDistance);
      for (let step = 0; step < 96 && t < tEnd; step++) {
        const wx = origin[0] + dir[0] * t - pos[0];
        const wy = origin[1] + dir[1] * t - pos[1];
        const wz = origin[2] + dir[2] * t - pos[2];
        // inverse rotation: rotate by the conjugate quaternion
        const [x, y, z, w] = [-q[0], -q[1], -q[2], q[3]];
        const ax = y * wz - z * wy + w * wx;
        const ay = z * wx - x * wz + w * wy;
        const az = x * wy - y * wx + w * wz;
        const lx = (wx + 2 * (y * az - z * ay)) / s;
        const ly = (wy + 2 * (z * ax - x * az)) / s;
        const lz = (wz + 2 * (x * ay - y * ax)) / s;
        const d = fn([lx, ly, lz], merged).d * s * rt.compiled.stepScale;
        if (d < 0.01) {
          best = { entityId: entity.id, distance: t, point: [origin[0] + dir[0] * t, origin[1] + dir[1] * t, origin[2] + dir[2] * t] };
          break;
        }
        t += d;
      }
    }
    if (best && this.terrainBlocks(origin, dir, best.distance)) return null;
    return best;
  }

  /** True if the terrain (or water surface) is hit along the ray before `distance`. */
  private terrainBlocks(origin: [number, number, number], dir: [number, number, number], distance: number): boolean {
    const water = (this.world?.spec.terrain.water_level ?? null) as number | null;
    for (let t = 0.5; t < distance - 0.5; t += Math.max(0.5, t * 0.04)) {
      const x = origin[0] + dir[0] * t;
      const y = origin[1] + dir[1] * t;
      const z = origin[2] + dir[2] * t;
      if (y < this.groundHeight(x, z) || (water !== null && y < water)) return true;
    }
    return false;
  }

  archetypeDef(id: string): GeometryDefinition | undefined {
    return this.defs.get(id);
  }

  /**
   * Evaluate terrain-v1 on the GPU at sample points and compare with the CPU evaluator.
   * Returns the maximum absolute difference in meters (the entity placement error budget).
   */
  terrainParity(samples = 64, extent = 600): { maxError: number; samples: number } {
    if (!this.world || !this.marchMaterial) return { maxError: NaN, samples: 0 };
    const points = new Float32Array(samples * 4);
    const cpu = new Float32Array(samples);
    for (let i = 0; i < samples; i++) {
      // deterministic low-discrepancy points
      const x = (((i * 0.61803398875) % 1) * 2 - 1) * extent;
      const z = (((i * 0.41421356237) % 1) * 2 - 1) * extent;
      points[i * 4] = x;
      points[i * 4 + 1] = z;
      cpu[i] = this.groundHeight(Math.fround(x), Math.fround(z));
    }
    const pointsTex = dataTexture(points, samples, 1);
    const target = new THREE.WebGLRenderTarget(samples, 1, { type: THREE.FloatType, format: THREE.RGBAFormat, depthBuffer: false });
    const u = this.marchMaterial.uniforms;
    const material = new THREE.RawShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: FULLSCREEN_VERT,
      fragmentShader: TERRAIN_PROBE_FRAG,
      depthTest: false,
      depthWrite: false,
      uniforms: {
        uPoints: { value: pointsTex },
        uTerrainA: u.uTerrainA,
        uTerrainB: u.uTerrainB,
        uTerrainBasin: u.uTerrainBasin,
        uSeedOffset: u.uSeedOffset,
      },
    });
    const scene = new THREE.Scene();
    const mesh = new THREE.Mesh(this.marchMesh.geometry, material);
    mesh.frustumCulled = false;
    scene.add(mesh);
    this.renderer.setRenderTarget(target);
    this.renderer.render(scene, this.marchCamera);
    const out = new Float32Array(samples * 4);
    this.renderer.readRenderTargetPixels(target, 0, 0, samples, 1, out);
    this.renderer.setRenderTarget(null);
    let maxError = 0;
    for (let i = 0; i < samples; i++) maxError = Math.max(maxError, Math.abs(out[i * 4] - cpu[i]));
    material.dispose();
    target.dispose();
    pointsTex.dispose();
    return { maxError, samples };
  }

  dispose(): void {
    this.marchMaterial?.dispose();
    this.compositeMaterial.dispose();
    this.target.dispose();
    this.instTex?.dispose();
    this.gridTex?.dispose();
  }
}
