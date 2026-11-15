/**
 * Fire, smoke and ember particles for burning entities. Purely a rendering of authoritative state: which entities
 * burn and how far along they are come from the server (condition, burn_started_tick, fuel) and the synchronized
 * simulation tick. Particles live in the shared scene, depth-test against the SDF depth written by the composite pass
 * and fade softly where they meet geometry (soft particles, sampling the near-field depth texture).
 */
import * as THREE from "three";

export interface FireSource {
  id: string;
  /** base of the entity (world) */
  position: [number, number, number];
  /** height of the burning object and horizontal radius of its burning volume */
  height: number;
  radius: number;
  /** 0..1 fire strength now (ramps up after ignition, dies down near burn-out) */
  intensity: number;
  /** 0..1 burn progress (flames move from the canopy to the trunk as it burns) */
  progress: number;
  /** smoke only (smouldering after burn-out) */
  smoulder?: boolean;
}

const VERT = /* glsl */ `
attribute float aLife;     // 0 (born) .. 1 (dead), <0 = unused
attribute float aSize;
attribute float aSeed;
varying float vLife;
varying float vSeed;
varying float vViewZ;
uniform float uPixelScale;
void main() {
  vLife = aLife;
  vSeed = aSeed;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vViewZ = -mv.z;
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aLife < 0.0 ? 0.0 : aSize * uPixelScale / max(0.1, -mv.z);
}
`;

const COMMON_FRAG = /* glsl */ `
varying float vLife;
varying float vSeed;
varying float vViewZ;
uniform sampler2D tDepth;
uniform vec2 uResolution;
uniform float uNear;
uniform float uFar;
uniform float uTime;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float sceneLinearDepth() {
  float d = texture2D(tDepth, gl_FragCoord.xy / uResolution).r;
  float z = d * 2.0 - 1.0;
  return 2.0 * uNear * uFar / (uFar + uNear - z * (uFar - uNear));
}
// soft-particle fade where the particle approaches opaque geometry
float softness(float scale) {
  return clamp((sceneLinearDepth() - vViewZ) / scale, 0.0, 1.0);
}
`;

const FLAME_FRAG = /* glsl */ `
${COMMON_FRAG}
void main() {
  if (vLife < 0.0) discard;
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  uv.y = -uv.y;
  float t = uTime * 2.5 + vSeed * 17.0;
  // turbulent tongue: narrow towards the top, warped by animated noise
  vec2 q = uv;
  q.x += (noise(vec2(uv.y * 2.5 + vSeed * 9.0, t)) - 0.5) * 0.5 * (uv.y + 1.0);
  float width = mix(0.95, 0.25, clamp(uv.y * 0.5 + 0.5, 0.0, 1.0));
  float shape = 1.0 - smoothstep(0.0, 1.0, length(vec2(q.x / width, q.y * 0.85)));
  shape *= 0.65 + 0.7 * noise(uv * 3.0 + vec2(0.0, -t * 1.7));
  float heat = shape * (1.0 - vLife);
  vec3 col = mix(vec3(0.55, 0.05, 0.01), vec3(1.0, 0.42, 0.06), smoothstep(0.05, 0.35, heat));
  col = mix(col, vec3(1.0, 0.86, 0.5), smoothstep(0.35, 0.75, heat));
  float a = smoothstep(0.02, 0.25, shape) * (1.0 - smoothstep(0.55, 1.0, vLife)) * smoothstep(0.0, 0.08, vLife);
  a *= softness(0.6);
  gl_FragColor = vec4(col * a * 2.2, 1.0);   // additive (premultiplied); values > 1 feed the bloom pass
}
`;

const SMOKE_FRAG = /* glsl */ `
${COMMON_FRAG}
uniform vec3 uSunColor;
void main() {
  if (vLife < 0.0) discard;
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float t = uTime * 0.35 + vSeed * 11.0;
  float n = noise(uv * 2.2 + vec2(t, -t * 0.7)) * 0.6 + noise(uv * 5.0 - vec2(t * 1.3, t)) * 0.4;
  float shape = (1.0 - smoothstep(0.25, 1.0, length(uv) + (n - 0.5) * 0.55));
  float a = shape * smoothstep(0.0, 0.15, vLife) * (1.0 - smoothstep(0.45, 1.0, vLife)) * 0.55;
  a *= softness(2.5);
  // young smoke is lit orange from below by the fire, older smoke is grey and lit by the sky
  vec3 col = mix(vec3(0.42, 0.22, 0.1), mix(vec3(0.16, 0.15, 0.15), uSunColor * 0.45, 0.35), smoothstep(0.0, 0.35, vLife));
  col *= 0.75 + 0.5 * n;
  gl_FragColor = vec4(col, a);
}
`;

const EMBER_FRAG = /* glsl */ `
${COMMON_FRAG}
void main() {
  if (vLife < 0.0) discard;
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float a = (1.0 - smoothstep(0.2, 1.0, length(uv))) * (1.0 - vLife);
  a *= 0.6 + 0.4 * sin(uTime * 23.0 + vSeed * 40.0);
  vec3 col = mix(vec3(1.0, 0.75, 0.3), vec3(1.0, 0.25, 0.04), vLife);
  gl_FragColor = vec4(col * a * 3.0, 1.0);
}
`;

// Coloured motes for generated interactions (`emit` effects): sparks, spores, dust, glow. Additive, soft, HDR.
const MOTE_VERT = /* glsl */ `
attribute float aLife;
attribute float aSize;
attribute vec3 aColor;
varying float vLife;
varying vec3 vColor;
varying float vViewZ;
uniform float uPixelScale;
void main() {
  vLife = aLife;
  vColor = aColor;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vViewZ = -mv.z;
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aLife < 0.0 ? 0.0 : aSize * uPixelScale / max(0.1, -mv.z);
}
`;
const MOTE_FRAG = /* glsl */ `
varying float vLife;
varying vec3 vColor;
varying float vViewZ;
uniform sampler2D tDepth;
uniform vec2 uResolution;
uniform float uNear;
uniform float uFar;
void main() {
  if (vLife < 0.0) discard;
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float r = length(uv);
  float core = exp(-r * r * 6.0);
  float d = texture2D(tDepth, gl_FragCoord.xy / uResolution).r * 2.0 - 1.0;
  float scene = 2.0 * uNear * uFar / (uFar + uNear - d * (uFar - uNear));
  float soft = clamp((scene - vViewZ) / 0.4, 0.0, 1.0);
  float a = core * smoothstep(0.0, 0.1, vLife) * (1.0 - smoothstep(0.6, 1.0, vLife)) * soft;
  gl_FragColor = vec4(vColor * a, 1.0);
}
`;

export type MoteKind = "sparks" | "spores" | "dust" | "smoke" | "embers" | "glow";

interface Pool {
  points: THREE.Points;
  pos: Float32Array;
  vel: Float32Array;
  life: Float32Array;
  lifespan: Float32Array;
  size: Float32Array;
  baseSize: Float32Array;
  seed: Float32Array;
  next: number;
  capacity: number;
}

function makePool(capacity: number, fragment: string, blending: THREE.Blending, uniforms: Record<string, THREE.IUniform>): Pool {
  const g = new THREE.BufferGeometry();
  const pos = new Float32Array(capacity * 3);
  const life = new Float32Array(capacity).fill(-1);
  const size = new Float32Array(capacity);
  const seed = new Float32Array(capacity).map(() => Math.random());
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
  g.setAttribute("aLife", new THREE.BufferAttribute(life, 1).setUsage(THREE.DynamicDrawUsage));
  g.setAttribute("aSize", new THREE.BufferAttribute(size, 1).setUsage(THREE.DynamicDrawUsage));
  g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
  const m = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: fragment,
    uniforms,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending,
  });
  if (blending === THREE.CustomBlending) {
    m.blendSrc = THREE.OneFactor;
    m.blendDst = THREE.OneFactor;
  }
  const points = new THREE.Points(g, m);
  points.frustumCulled = false;
  return {
    points, pos, vel: new Float32Array(capacity * 3), life, lifespan: new Float32Array(capacity).fill(1), size,
    baseSize: new Float32Array(capacity), seed, next: 0, capacity,
  };
}

export class FireSystem {
  readonly group = new THREE.Group();
  private readonly uniforms: Record<string, THREE.IUniform>;
  private readonly flames: Pool;
  private readonly smoke: Pool;
  private readonly embers: Pool;
  private readonly accum = new Map<string, { flame: number; smoke: number; ember: number }>();
  private readonly motes: {
    points: THREE.Points; pos: Float32Array; vel: Float32Array; life: Float32Array; span: Float32Array;
    size: Float32Array; color: Float32Array; drag: Float32Array; lift: Float32Array; next: number; capacity: number;
  };
  wind: [number, number] = [1, 0];
  activeSources = 0;

  constructor(depth: THREE.Texture) {
    this.uniforms = {
      tDepth: { value: depth },
      uResolution: { value: new THREE.Vector2(1, 1) },
      uNear: { value: 0.1 },
      uFar: { value: 4000 },
      uTime: { value: 0 },
      uPixelScale: { value: 400 },
      uSunColor: { value: new THREE.Vector3(1, 0.95, 0.85) },
    };
    this.flames = makePool(3000, FLAME_FRAG, THREE.CustomBlending, this.uniforms);
    this.smoke = makePool(2400, SMOKE_FRAG, THREE.NormalBlending, this.uniforms);
    this.embers = makePool(1200, EMBER_FRAG, THREE.CustomBlending, this.uniforms);
    this.smoke.points.renderOrder = 10;
    this.flames.points.renderOrder = 11;
    this.embers.points.renderOrder = 12;
    this.group.add(this.smoke.points, this.flames.points, this.embers.points);
    const cap = 2000;
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(cap * 3);
    const life = new Float32Array(cap).fill(-1);
    const size = new Float32Array(cap);
    const color = new Float32Array(cap * 3);
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("aLife", new THREE.BufferAttribute(life, 1).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("aSize", new THREE.BufferAttribute(size, 1).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("aColor", new THREE.BufferAttribute(color, 3).setUsage(THREE.DynamicDrawUsage));
    const m = new THREE.ShaderMaterial({ vertexShader: MOTE_VERT, fragmentShader: MOTE_FRAG, uniforms: this.uniforms,
      transparent: true, depthWrite: false, depthTest: true, blending: THREE.CustomBlending });
    m.blendSrc = THREE.OneFactor;
    m.blendDst = THREE.OneFactor;
    const points = new THREE.Points(g, m);
    points.frustumCulled = false;
    points.renderOrder = 13;
    this.group.add(points);
    this.motes = { points, pos, vel: new Float32Array(cap * 3), life, span: new Float32Array(cap).fill(1), size, color,
      drag: new Float32Array(cap), lift: new Float32Array(cap), next: 0, capacity: cap };
  }

  /**
   * A burst for a committed `emit` effect at a world position. Behaviour per kind is generic physics (speed, drag,
   * buoyancy, lifetime, glow); the colour comes from the generated behaviour (or a default for the kind).
   */
  burst(kind: MoteKind, at: [number, number, number], color: [number, number, number] | null, count: number, height = 1.5): void {
    if (kind === "smoke" || kind === "embers") {
      for (let i = 0; i < Math.min(count, 120); i++) {
        const a = Math.random() * Math.PI * 2;
        if (kind === "smoke") this.spawn(this.smoke, at[0] + Math.cos(a) * 0.5, at[1] + height * Math.random(), at[2] + Math.sin(a) * 0.5, 0, 1 + Math.random(), 0, 4 + Math.random() * 3, 1.8);
        else this.spawn(this.embers, at[0], at[1] + height * Math.random(), at[2], Math.cos(a) * 2, 2 + Math.random() * 3, Math.sin(a) * 2, 1.5 + Math.random(), 0.12);
      }
      return;
    }
    const defaults: Record<string, [number, number, number]> = {
      sparks: [1.0, 0.85, 0.5], spores: [0.55, 1.0, 0.7], dust: [0.55, 0.5, 0.45], glow: [0.6, 0.8, 1.0],
    };
    const c = color ?? defaults[kind];
    const intensity = kind === "dust" ? 0.25 : kind === "glow" ? 4 : kind === "sparks" ? 6 : 2.5;
    const M = this.motes;
    for (let k = 0; k < Math.min(count, 400); k++) {
      const i = M.next;
      M.next = (M.next + 1) % M.capacity;
      const a = Math.random() * Math.PI * 2;
      const el = Math.random() * Math.PI - Math.PI / 2;
      const sp = kind === "sparks" ? 4 + Math.random() * 6 : kind === "glow" ? 0.4 : kind === "dust" ? 1 + Math.random() : 0.3 + Math.random() * 0.7;
      M.pos.set([at[0] + (Math.random() - 0.5) * 0.6, at[1] + height * (0.3 + Math.random() * 0.7), at[2] + (Math.random() - 0.5) * 0.6], i * 3);
      M.vel.set([Math.cos(a) * Math.cos(el) * sp, Math.abs(Math.sin(el)) * sp + (kind === "spores" ? 0.4 : 0), Math.sin(a) * Math.cos(el) * sp], i * 3);
      M.life[i] = 0;
      M.span[i] = kind === "sparks" ? 0.6 + Math.random() * 0.6 : kind === "spores" ? 5 + Math.random() * 5 : kind === "glow" ? 1.2 : 2.5;
      M.size[i] = kind === "glow" ? 1.8 + Math.random() * 1.5 : kind === "dust" ? 0.5 : kind === "spores" ? 0.12 : 0.07;
      M.color.set([c[0] * intensity, c[1] * intensity, c[2] * intensity], i * 3);
      M.drag[i] = kind === "sparks" ? 0.4 : kind === "spores" ? 1.5 : 1.0;
      M.lift[i] = kind === "sparks" ? -9.0 : kind === "spores" ? 0.15 : kind === "dust" ? -0.6 : 0.0;
    }
  }

  setView(camera: THREE.PerspectiveCamera, depthWidth: number, depthHeight: number, sunColor?: THREE.Vector3): void {
    this.uniforms.uResolution.value.set(depthWidth, depthHeight);
    this.uniforms.uNear.value = camera.near;
    this.uniforms.uFar.value = camera.far;
    // point size in pixels for a 1 m particle at 1 m distance, measured in the depth target's pixels
    this.uniforms.uPixelScale.value = depthHeight / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
    if (sunColor) this.uniforms.uSunColor.value.copy(sunColor);
  }

  private spawn(pool: Pool, x: number, y: number, z: number, vx: number, vy: number, vz: number, life: number, size: number): void {
    const i = pool.next;
    pool.next = (pool.next + 1) % pool.capacity;
    pool.pos.set([x, y, z], i * 3);
    pool.vel.set([vx, vy, vz], i * 3);
    pool.life[i] = 0;
    pool.lifespan[i] = life;
    pool.baseSize[i] = size;
    pool.size[i] = size;
  }

  update(dt: number, time: number, sources: FireSource[]): void {
    this.uniforms.uTime.value = time;
    this.activeSources = sources.length;
    const [wx, wz] = this.wind;
    for (const s of sources) {
      const acc = this.accum.get(s.id) ?? { flame: 0, smoke: 0, ember: 0 };
      this.accum.set(s.id, acc);
      const scale = Math.max(0.6, s.radius / 2);
      const k = s.intensity;
      acc.flame += dt * (s.smoulder ? 0 : 70 * k * scale);
      acc.smoke += dt * (s.smoulder ? 4 : 14 * (0.3 + k) * scale);
      acc.ember += dt * (s.smoulder ? 0.5 : 14 * k * scale);
      // the burning zone moves down from the crown to the trunk as the fire consumes the foliage
      const lo = s.height * (0.08 + 0.25 * (1 - s.progress));
      const hi = s.height * (0.35 + 0.55 * (1 - s.progress * 0.8));
      const r = s.radius * (0.35 + 0.65 * (1 - s.progress));
      while (acc.flame >= 1) {
        acc.flame -= 1;
        const a = Math.random() * Math.PI * 2;
        const rr = r * Math.sqrt(Math.random()) * 0.8;
        const y = lo + (hi - lo) * Math.random();
        this.spawn(this.flames, s.position[0] + Math.cos(a) * rr, s.position[1] + y, s.position[2] + Math.sin(a) * rr,
          wx * 0.15, 1.6 + Math.random() * 1.6, wz * 0.15, 0.55 + Math.random() * 0.5, (1.0 + Math.random() * 1.4) * (0.6 + 0.6 * k) * Math.min(1.6, scale));
      }
      while (acc.smoke >= 1) {
        acc.smoke -= 1;
        const a = Math.random() * Math.PI * 2;
        const rr = r * 0.5 * Math.random();
        this.spawn(this.smoke, s.position[0] + Math.cos(a) * rr, s.position[1] + hi * (s.smoulder ? 0.4 : 0.95), s.position[2] + Math.sin(a) * rr,
          wx * 0.4, (s.smoulder ? 0.8 : 2.2) + Math.random(), wz * 0.4, 6 + Math.random() * 5, s.smoulder ? 1.5 : 2.6 * scale);
      }
      while (acc.ember >= 1) {
        acc.ember -= 1;
        const a = Math.random() * Math.PI * 2;
        this.spawn(this.embers, s.position[0] + Math.cos(a) * r * 0.5, s.position[1] + lo + (hi - lo) * Math.random(), s.position[2] + Math.sin(a) * r * 0.5,
          (Math.random() - 0.5) * 2 + wx * 0.3, 2.5 + Math.random() * 3.5, (Math.random() - 0.5) * 2 + wz * 0.3, 1.5 + Math.random() * 2.5, 0.09 + Math.random() * 0.07);
      }
    }
    for (const id of [...this.accum.keys()]) if (!sources.some((s) => s.id === id)) this.accum.delete(id);
    this.step(this.flames, dt, 0.25, 0.6, 1.4);
    this.step(this.smoke, dt, 0.6, 0.12, 4.5);
    this.step(this.embers, dt, 0.2, -0.9, 1.0);
    const M = this.motes;
    const [wx2, wz2] = this.wind;
    for (let i = 0; i < M.capacity; i++) {
      if (M.life[i] < 0) continue;
      M.life[i] += dt / M.span[i];
      if (M.life[i] >= 1) {
        M.life[i] = -1;
        continue;
      }
      const o = i * 3;
      const drag = M.drag[i];
      M.vel[o] += (wx2 * 0.2 - M.vel[o]) * drag * dt + (Math.random() - 0.5) * 0.8 * dt;
      M.vel[o + 1] += M.lift[i] * dt - M.vel[o + 1] * drag * 0.3 * dt;
      M.vel[o + 2] += (wz2 * 0.2 - M.vel[o + 2]) * drag * dt + (Math.random() - 0.5) * 0.8 * dt;
      M.pos[o] += M.vel[o] * dt;
      M.pos[o + 1] += M.vel[o + 1] * dt;
      M.pos[o + 2] += M.vel[o + 2] * dt;
    }
    const mg = M.points.geometry;
    for (const name of ["position", "aLife", "aSize", "aColor"]) (mg.getAttribute(name) as THREE.BufferAttribute).needsUpdate = true;
  }

  /** Integrate one pool: buoyancy (`lift`, m/s² up), drag, size growth over life. */
  private step(p: Pool, dt: number, drag: number, lift: number, grow: number): void {
    const [wx, wz] = this.wind;
    for (let i = 0; i < p.capacity; i++) {
      if (p.life[i] < 0) continue;
      p.life[i] += dt / p.lifespan[i];
      if (p.life[i] >= 1) {
        p.life[i] = -1;
        continue;
      }
      const o = i * 3;
      p.vel[o] += (wx * 0.35 - p.vel[o]) * drag * dt + (Math.random() - 0.5) * 2.5 * dt;
      p.vel[o + 1] += lift * dt;
      p.vel[o + 2] += (wz * 0.35 - p.vel[o + 2]) * drag * dt + (Math.random() - 0.5) * 2.5 * dt;
      p.pos[o] += p.vel[o] * dt;
      p.pos[o + 1] += p.vel[o + 1] * dt;
      p.pos[o + 2] += p.vel[o + 2] * dt;
      p.size[i] = p.baseSize[i] * (1 + grow * p.life[i]);
    }
    const g = p.points.geometry;
    (g.getAttribute("position") as THREE.BufferAttribute).needsUpdate = true;
    (g.getAttribute("aLife") as THREE.BufferAttribute).needsUpdate = true;
    (g.getAttribute("aSize") as THREE.BufferAttribute).needsUpdate = true;
  }

  particleCount(): number {
    let n = 0;
    for (const p of [this.flames, this.smoke, this.embers]) for (let i = 0; i < p.capacity; i++) if (p.life[i] >= 0) n++;
    return n;
  }

  dispose(): void {
    for (const p of [this.flames, this.smoke, this.embers]) {
      p.points.geometry.dispose();
      (p.points.material as THREE.Material).dispose();
    }
  }
}
