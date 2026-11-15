/**
 * Procedural grass: GPU-instanced blades generated mathematically, placed deterministically on the analytic terrain.
 *
 * - Geometry: one parametric blade (a tapered strip of 4 segments) shaped per instance in the vertex shader from its
 *   own height, width, curvature, orientation and wind phase; no fixed foliage models.
 * - Placement: world-space 8 m patches around the camera, blades hashed from (world seed, patch, index), so the same
 *   world always grows the same grass. Density falls off by ring (LOD) and blades shrink to nothing at the edge.
 * - Exclusion: water and the shoreline, slopes steep enough to show rock, snow above the peak line; overall density
 *   follows how "grassy" the world's ground material is (dry worlds get sparse, ground-tinted tufts).
 * - Shading: a blade takes the lit, shadowed and fogged colour of the terrain under its root from the SDF pass, so it
 *   sits in tree shade and haze exactly like the ground; tips are brightened, bases darkened, and blades glow when
 *   backlit by the sun. Blades bend with the wind and away from the player.
 * GPU cost is bounded by MAX_BLADES; CPU placement is amortised over frames (a few patches per frame).
 */
import * as THREE from "three";

const PATCH = 8;
const RINGS = 4; // patches in each direction from the camera's patch → (2R+1)² patches, ~72 m across
const PER_RING = [2400, 2400, 1000, 420, 180]; // blades per 8 m patch by ring (≈53k at full lushness)
const MAX_BLADES = 60000;
const SEGMENTS = 4;

const VERT = /* glsl */ `
attribute vec2 aBlade;     // x: side -1..1, y: 0 (root) .. 1 (tip)
attribute vec4 aRoot;      // world root position, yaw
attribute vec4 aShape;     // height, width, curvature, phase
uniform float uTime;
uniform vec2 uWind;
uniform vec3 uCam;
uniform vec3 uPlayer;
uniform float uMaxDist;
varying float vT;
varying vec2 vRootUv;
varying float vTint;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  float h = aShape.x;
  float w = aShape.y;
  float t = aBlade.y;
  float dist = length(aRoot.xz - uCam.xz);
  h *= 1.0 - smoothstep(uMaxDist * 0.62, uMaxDist, dist);              // fade out at the edge of the grass area
  float width = w * (1.0 - t * 0.88) * (1.0 + dist * 0.018);           // wider far away keeps coverage at low density
  float c = cos(aRoot.w), s = sin(aRoot.w);
  vec3 side = vec3(c, 0.0, s);
  vec3 fwd = vec3(-s, 0.0, c);
  float windSpeed = length(uWind);
  vec3 windDir = windSpeed > 0.01 ? vec3(uWind.x, 0.0, uWind.y) / windSpeed : vec3(1.0, 0.0, 0.0);
  float gust = 0.5 + 0.5 * sin(uTime * 1.1 + dot(aRoot.xz, windDir.xz) * 0.12);
  float sway = (0.08 + 0.05 * min(windSpeed, 6.0)) * gust + 0.04 * sin(uTime * 2.6 + aShape.w * 6.28);
  vec3 away = aRoot.xyz - uPlayer;
  float pd = length(away.xz);
  vec3 push = pd < 1.1 && abs(away.y) < 2.0 ? normalize(vec3(away.x, 0.0, away.z) + 1e-4) * (1.1 - pd) * 1.1 : vec3(0.0);
  vec3 lean = fwd * aShape.z + windDir * sway + push;
  vec3 pos = aRoot.xyz + side * aBlade.x * width * 0.5 + vec3(0.0, t * h, 0.0) + lean * (t * t) * h;
  pos.y -= dot(lean, lean) * t * t * h * 0.35;                         // bending shortens the blade's height
  vec3 tangent = vec3(0.0, h, 0.0) + lean * (2.0 * t) * h;
  vNormal = normalize(cross(side, tangent));
  vT = t;
  vTint = fract(aShape.w * 7.13);
  vec4 rc = projectionMatrix * viewMatrix * vec4(aRoot.xyz + vec3(0.0, 0.03, 0.0), 1.0);
  vRootUv = rc.xy / max(rc.w, 1e-3) * 0.5 + 0.5;
  vView = normalize(cameraPosition - pos);
  gl_Position = projectionMatrix * viewMatrix * vec4(pos, 1.0);
}
`;

const FRAG = /* glsl */ `
uniform sampler2D tGround;   // linear HDR SDF pass: lit, shadowed, fogged terrain
uniform vec3 uSunDir;
uniform vec3 uGrassTint;
varying float vT;
varying vec2 vRootUv;
varying float vTint;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vec3 ground = texture2D(tGround, clamp(vRootUv, vec2(0.002), vec2(0.998))).rgb;
  float tip = mix(0.5, 1.22, smoothstep(0.0, 1.0, vT));                // self-occlusion at the base, lighter tips
  vec3 col = ground * uGrassTint * tip * (0.88 + 0.24 * vTint);
  // light through the blade when the sun is behind it (scaled by how lit the ground is, so shade stays shade)
  float back = pow(max(dot(-vView, uSunDir), 0.0), 3.0) * vT;
  col += ground * uGrassTint * back * 0.8;
  gl_FragColor = vec4(col, 1.0);
}
`;

function hash(a: number, b: number, c: number, d: number): number {
  let h = (a * 374761393 + b * 668265263 + c * 2147483647 + d * 1274126177) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

export interface GrassWorld {
  seed: number;
  groundHeight(x: number, z: number): number;
  waterLevel: number | null;
  peakHeight: number | null;
  /** 0 (no grass: sand, snow, rock) .. 1 (lush) from the ground material colour */
  lushness: number;
  tint: [number, number, number];
}

export class Grass {
  readonly mesh: THREE.Mesh;
  private readonly geometry: THREE.InstancedBufferGeometry;
  private readonly root: THREE.InstancedBufferAttribute;
  private readonly shape: THREE.InstancedBufferAttribute;
  private readonly material: THREE.ShaderMaterial;
  private patches = new Map<string, { ring: number; data: Float32Array; count: number }>();
  private world: GrassWorld | null = null;
  private center = "";
  private dirty = false;
  blades = 0;

  constructor(ground: THREE.Texture) {
    const g = new THREE.InstancedBufferGeometry();
    const verts: number[] = [];
    for (let i = 0; i <= SEGMENTS; i++) {
      const t = i / SEGMENTS;
      if (i < SEGMENTS) verts.push(-1, t, 1, t);
      else verts.push(0, 1);
    }
    const idx: number[] = [];
    for (let i = 0; i < SEGMENTS - 1; i++) {
      const a = i * 2;
      idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    const last = (SEGMENTS - 1) * 2;
    idx.push(last, last + 1, last + 2);
    g.setAttribute("aBlade", new THREE.Float32BufferAttribute(verts, 2));
    g.setIndex(idx);
    // three needs a position attribute to draw; the vertex shader builds positions itself
    g.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array((verts.length / 2) * 3), 3));
    this.root = new THREE.InstancedBufferAttribute(new Float32Array(MAX_BLADES * 4), 4).setUsage(THREE.DynamicDrawUsage);
    this.shape = new THREE.InstancedBufferAttribute(new Float32Array(MAX_BLADES * 4), 4).setUsage(THREE.DynamicDrawUsage);
    g.setAttribute("aRoot", this.root);
    g.setAttribute("aShape", this.shape);
    g.instanceCount = 0;
    this.geometry = g;
    this.material = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      side: THREE.DoubleSide,
      uniforms: {
        tGround: { value: ground },
        uTime: { value: 0 },
        uWind: { value: new THREE.Vector2(1, 0) },
        uCam: { value: new THREE.Vector3() },
        uPlayer: { value: new THREE.Vector3() },
        uMaxDist: { value: PATCH * (RINGS + 0.5) },
        uSunDir: { value: new THREE.Vector3(0, 1, 0) },
        uGrassTint: { value: new THREE.Vector3(1, 1, 1) },
      },
    });
    this.mesh = new THREE.Mesh(g, this.material);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 1; // after the SDF composite (which writes depth), before transparent effects
  }

  setWorld(world: GrassWorld | null): void {
    this.world = world;
    this.patches.clear();
    this.center = "";
    this.dirty = true;
    if (world) this.material.uniforms.uGrassTint.value.set(...world.tint);
  }

  private buildPatch(px: number, pz: number, ring: number): { ring: number; data: Float32Array; count: number } {
    const w = this.world!;
    const n = Math.round(PER_RING[Math.min(ring, PER_RING.length - 1)] * w.lushness);
    const data = new Float32Array(n * 8);
    let k = 0;
    for (let i = 0; i < n; i++) {
      const x = (px + hash(w.seed, px, pz, i * 4)) * PATCH;
      const z = (pz + hash(w.seed, px, pz, i * 4 + 1)) * PATCH;
      const y = w.groundHeight(x, z);
      if (w.waterLevel !== null && y < w.waterLevel + 0.18) continue;
      if (w.peakHeight !== null && y > w.peakHeight - 3) continue;
      const sx = w.groundHeight(x + 0.5, z) - y;
      const sz = w.groundHeight(x, z + 0.5) - y;
      const slope = Math.hypot(sx, sz) / 0.5;
      // no grass where the shader shows rock; thinning out towards it
      if (slope > 0.62 || hash(w.seed, px, pz, i * 4 + 2) < (slope - 0.3) * 3) continue;
      const r = hash(w.seed, px, pz, i * 4 + 3);
      const clump = 0.6 + 0.8 * hash(w.seed, Math.floor(x / 2.3), Math.floor(z / 2.3), 77); // patches of taller grass
      data.set([x, y - 0.03, z, r * Math.PI * 2, (0.2 + 0.4 * hash(w.seed, i, px, pz)) * clump, 0.032 + 0.026 * r,
        (hash(w.seed, pz, i, px) - 0.5) * 0.7, r], k * 8);
      k++;
    }
    return { ring, data, count: k };
  }

  /** Per frame: follow the camera, build at most a few new patches, refresh the instance buffers if needed. */
  update(camera: THREE.Camera, player: THREE.Vector3, time: number, wind: [number, number], sunDir: THREE.Vector3): void {
    const u = this.material.uniforms;
    u.uTime.value = time;
    u.uWind.value.set(wind[0], wind[1]);
    u.uCam.value.copy(camera.position);
    u.uPlayer.value.set(player.x, player.y - 1.7, player.z);
    u.uSunDir.value.copy(sunDir);
    if (!this.world) {
      this.geometry.instanceCount = 0;
      return;
    }
    const cx = Math.floor(camera.position.x / PATCH);
    const cz = Math.floor(camera.position.z / PATCH);
    const key = `${cx},${cz}`;
    if (key !== this.center) {
      this.center = key;
      for (const k of [...this.patches.keys()]) {
        const [px, pz] = k.split(",").map(Number);
        if (Math.max(Math.abs(px - cx), Math.abs(pz - cz)) > RINGS) this.patches.delete(k);
      }
      this.dirty = true;
    }
    // build missing patches nearest first, a few per frame (keeps placement off the frame-time budget)
    let built = 0;
    for (let ring = 0; ring <= RINGS && built < 3; ring++) {
      for (let dx = -ring; dx <= ring && built < 3; dx++) {
        for (let dz = -ring; dz <= ring && built < 3; dz++) {
          if (Math.max(Math.abs(dx), Math.abs(dz)) !== ring) continue;
          const k = `${cx + dx},${cz + dz}`;
          const existing = this.patches.get(k);
          if (existing && existing.ring === ring) continue;
          this.patches.set(k, this.buildPatch(cx + dx, cz + dz, ring));
          built++;
          this.dirty = true;
        }
      }
    }
    if (!this.dirty) return;
    this.dirty = false;
    const root = this.root.array as Float32Array;
    const shape = this.shape.array as Float32Array;
    let n = 0;
    for (const p of this.patches.values()) {
      for (let i = 0; i < p.count && n < MAX_BLADES; i++, n++) {
        root.set(p.data.subarray(i * 8, i * 8 + 4), n * 4);
        shape.set(p.data.subarray(i * 8 + 4, i * 8 + 8), n * 4);
      }
    }
    this.geometry.instanceCount = n;
    this.blades = n;
    this.root.needsUpdate = true;
    this.shape.needsUpdate = true;
  }

  dispose(): void {
    this.geometry.dispose();
    this.material.dispose();
  }
}
