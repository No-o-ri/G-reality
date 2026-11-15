/**
 * HDR post-processing for the player view.
 *
 *   scene (SDF composite in linear HDR + particles) → hdr target (RGBA16F + depth)
 *   → bloom: bright pass, 5-level dual-filter down/up chain (energy-preserving soft glow, not a white blob)
 *   → light shafts: radial blur of sky pixels toward the sun's screen position (quarter resolution)
 *   → combine: exposure, ACES filmic tone curve, sRGB encode, subtle vignette and grain → LDR target
 *   → FXAA (antialiasing on the final image) → canvas
 *
 * Tone mapping happens exactly once, here; the SDF composite writes linear HDR when this pipeline is active.
 */
import * as THREE from "three";

const VERT = /* glsl */ `
in vec3 position;
out vec2 vUv;
void main() {
  vUv = position.xy * 0.5 + 0.5;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const BRIGHT = /* glsl */ `
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tSrc;
uniform float uThreshold;
void main() {
  vec3 c = texture(tSrc, vUv).rgb;
  float l = max(c.r, max(c.g, c.b));
  // soft knee: highlights fade into the bloom instead of switching on at the threshold
  float k = clamp((l - uThreshold * 0.6) / (uThreshold * 0.8), 0.0, 1.0);
  fragColor = vec4(min(c * k * k, vec3(40.0)), 1.0);
}
`;

const DOWN = /* glsl */ `
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tSrc;
uniform vec2 uTexel;
void main() {
  vec3 c = texture(tSrc, vUv).rgb * 4.0;
  c += texture(tSrc, vUv + uTexel * vec2(-1.0, -1.0)).rgb;
  c += texture(tSrc, vUv + uTexel * vec2(1.0, -1.0)).rgb;
  c += texture(tSrc, vUv + uTexel * vec2(-1.0, 1.0)).rgb;
  c += texture(tSrc, vUv + uTexel * vec2(1.0, 1.0)).rgb;
  fragColor = vec4(c / 8.0, 1.0);
}
`;

const UP = /* glsl */ `
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tSrc;
uniform sampler2D tPrev;
uniform vec2 uTexel;
void main() {
  vec3 c = vec3(0.0);
  c += texture(tSrc, vUv + uTexel * vec2(-1.0, 0.0)).rgb * 2.0;
  c += texture(tSrc, vUv + uTexel * vec2(1.0, 0.0)).rgb * 2.0;
  c += texture(tSrc, vUv + uTexel * vec2(0.0, -1.0)).rgb * 2.0;
  c += texture(tSrc, vUv + uTexel * vec2(0.0, 1.0)).rgb * 2.0;
  c += texture(tSrc, vUv + uTexel * vec2(-1.0, -1.0)).rgb;
  c += texture(tSrc, vUv + uTexel * vec2(1.0, -1.0)).rgb;
  c += texture(tSrc, vUv + uTexel * vec2(-1.0, 1.0)).rgb;
  c += texture(tSrc, vUv + uTexel * vec2(1.0, 1.0)).rgb;
  fragColor = vec4(c / 12.0 + texture(tPrev, vUv).rgb, 1.0);
}
`;

const SHAFTS = /* glsl */ `
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tSrc;
uniform sampler2D tDepth;
uniform vec2 uSun;        // sun position in UV space
uniform float uStrength;  // 0 when the sun is behind the camera / below the horizon
void main() {
  if (uStrength <= 0.0) { fragColor = vec4(0.0); return; }
  vec2 dir = (uSun - vUv) / 40.0;
  vec2 uv = vUv;
  float decay = 1.0;
  vec3 acc = vec3(0.0);
  for (int i = 0; i < 40; i++) {
    uv += dir;
    // only the open sky (depth = far plane) emits rays; anything solid in front of the sun casts a shaft
    float sky = step(0.99999, texture(tDepth, uv).r);
    vec3 c = texture(tSrc, uv).rgb;
    acc += min(c, vec3(6.0)) * sky * decay;
    decay *= 0.965;
  }
  float falloff = 1.0 - smoothstep(0.0, 0.75, length(vUv - uSun));
  fragColor = vec4(acc / 40.0 * uStrength * falloff, 1.0);
}
`;

const COMBINE = /* glsl */ `
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tHdr;
uniform sampler2D tBloom;
uniform sampler2D tShafts;
uniform float uExposure;
uniform float uBloom;
uniform float uShafts;
uniform float uTime;
uniform vec3 uShaftTint;
// Khronos PBR Neutral tone mapping: keeps material colours and mid-tones as authored (no crushed shadows, no
// oversaturation) and only compresses highlights, desaturating them gracefully towards white.
vec3 neutral(vec3 c) {
  const float startCompression = 0.8 - 0.04;
  const float desaturation = 0.15;
  float x = min(c.r, min(c.g, c.b));
  float offset = x < 0.08 ? x - 6.25 * x * x : 0.04;
  c -= offset;
  float peak = max(c.r, max(c.g, c.b));
  if (peak < startCompression) return c;
  float d = 1.0 - startCompression;
  float newPeak = 1.0 - d * d / (peak + d - startCompression);
  c *= newPeak / peak;
  float g = 1.0 - 1.0 / (desaturation * (peak - newPeak) + 1.0);
  return mix(c, vec3(newPeak), g);
}
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
void main() {
  vec3 hdr = texture(tHdr, vUv).rgb;
  vec3 bloom = texture(tBloom, vUv).rgb;
  vec3 shafts = texture(tShafts, vUv).rgb * uShaftTint;
  vec3 c = (hdr + bloom * uBloom + shafts * uShafts) * uExposure;
  c = neutral(c);
  // gentle split toning: cool shadows, warm highlights (cinematic without changing the scene's colours)
  float luma = dot(c, vec3(0.2126, 0.7152, 0.0722));
  c *= mix(vec3(0.97, 0.99, 1.04), vec3(1.03, 1.0, 0.96), smoothstep(0.05, 0.6, luma));
  c = mix(vec3(luma), c, 1.06);                            // a touch of saturation back after compression
  c = pow(max(c, vec3(0.0)), vec3(1.0 / 2.2));
  vec2 d = vUv - 0.5;
  c *= 1.0 - 0.18 * dot(d, d);                             // very soft vignette
  c += (hash(vUv * 1000.0 + uTime) - 0.5) / 512.0;         // sub-LSB dither against banding only (no visible grain)
  // luma in alpha for FXAA
  fragColor = vec4(c, dot(c, vec3(0.299, 0.587, 0.114)));
}
`;

const FXAA = /* glsl */ `
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tSrc;
uniform vec2 uTexel;
// FXAA 3.11 (console quality, simplified): edge-aware blend along the local luma gradient
void main() {
  vec3 rgbM = texture(tSrc, vUv).rgb;
  float lM = texture(tSrc, vUv).a;
  float lNW = texture(tSrc, vUv + vec2(-1.0, -1.0) * uTexel).a;
  float lNE = texture(tSrc, vUv + vec2(1.0, -1.0) * uTexel).a;
  float lSW = texture(tSrc, vUv + vec2(-1.0, 1.0) * uTexel).a;
  float lSE = texture(tSrc, vUv + vec2(1.0, 1.0) * uTexel).a;
  float lMin = min(lM, min(min(lNW, lNE), min(lSW, lSE)));
  float lMax = max(lM, max(max(lNW, lNE), max(lSW, lSE)));
  if (lMax - lMin < max(0.0312, lMax * 0.125)) { fragColor = vec4(rgbM, 1.0); return; }
  vec2 dir = vec2(-((lNW + lNE) - (lSW + lSE)), (lNW + lSW) - (lNE + lSE));
  float reduce = max((lNW + lNE + lSW + lSE) * 0.03125, 1.0 / 128.0);
  float rcp = 1.0 / (min(abs(dir.x), abs(dir.y)) + reduce);
  dir = clamp(dir * rcp, vec2(-8.0), vec2(8.0)) * uTexel;
  vec3 a = 0.5 * (texture(tSrc, vUv + dir * (1.0 / 3.0 - 0.5)).rgb + texture(tSrc, vUv + dir * (2.0 / 3.0 - 0.5)).rgb);
  vec3 b = a * 0.5 + 0.25 * (texture(tSrc, vUv + dir * -0.5).rgb + texture(tSrc, vUv + dir * 0.5).rgb);
  float lB = dot(b, vec3(0.299, 0.587, 0.114));
  fragColor = vec4((lB < lMin || lB > lMax) ? a : b, 1.0);
}
`;

function target(w: number, h: number, type: THREE.TextureDataType, depth = false): THREE.WebGLRenderTarget {
  const t = new THREE.WebGLRenderTarget(Math.max(1, w), Math.max(1, h), {
    type,
    format: THREE.RGBAFormat,
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    depthBuffer: depth,
    stencilBuffer: false,
  });
  if (depth) {
    t.depthTexture = new THREE.DepthTexture(Math.max(1, w), Math.max(1, h));
    t.depthTexture.type = THREE.FloatType;
  }
  return t;
}

export interface PostSettings {
  exposure: number;
  bloom: number;
  shafts: number;
  sunWorldDir: THREE.Vector3;
  sunColor: THREE.Vector3;
}

export class PostProcess {
  readonly hdr: THREE.WebGLRenderTarget;
  private readonly ldr: THREE.WebGLRenderTarget;
  private readonly bright: THREE.WebGLRenderTarget;
  private readonly down: THREE.WebGLRenderTarget[] = [];
  private readonly up: THREE.WebGLRenderTarget[] = [];
  private readonly shafts: THREE.WebGLRenderTarget;
  private readonly scene = new THREE.Scene();
  private readonly cam = new THREE.Camera();
  private readonly quad: THREE.Mesh;
  private readonly m: Record<string, THREE.RawShaderMaterial>;
  private readonly black: THREE.DataTexture;
  private w = 0;
  private h = 0;
  private time = 0;

  constructor(private readonly renderer: THREE.WebGLRenderer) {
    this.hdr = target(4, 4, THREE.HalfFloatType, true);
    this.ldr = target(4, 4, THREE.UnsignedByteType);
    this.bright = target(4, 4, THREE.HalfFloatType);
    for (let i = 0; i < 5; i++) {
      this.down.push(target(4, 4, THREE.HalfFloatType));
      this.up.push(target(4, 4, THREE.HalfFloatType));
    }
    this.shafts = target(4, 4, THREE.HalfFloatType);
    this.black = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
    this.black.needsUpdate = true;
    const mat = (fragmentShader: string, uniforms: Record<string, THREE.IUniform>) =>
      new THREE.RawShaderMaterial({ glslVersion: THREE.GLSL3, vertexShader: VERT, fragmentShader, uniforms, depthTest: false, depthWrite: false });
    this.m = {
      // only genuinely bright things bloom (sun, flames, glowing materials), not lit surfaces
      bright: mat(BRIGHT, { tSrc: { value: null }, uThreshold: { value: 3.0 } }),
      down: mat(DOWN, { tSrc: { value: null }, uTexel: { value: new THREE.Vector2() } }),
      up: mat(UP, { tSrc: { value: null }, tPrev: { value: null }, uTexel: { value: new THREE.Vector2() } }),
      shafts: mat(SHAFTS, { tSrc: { value: null }, tDepth: { value: null }, uSun: { value: new THREE.Vector2() }, uStrength: { value: 0 } }),
      combine: mat(COMBINE, {
        tHdr: { value: null }, tBloom: { value: null }, tShafts: { value: null }, uExposure: { value: 1 }, uBloom: { value: 0.6 },
        uShafts: { value: 1 }, uTime: { value: 0 }, uShaftTint: { value: new THREE.Vector3(1, 0.9, 0.75) },
      }),
      fxaa: mat(FXAA, { tSrc: { value: null }, uTexel: { value: new THREE.Vector2() } }),
    };
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
    this.quad = new THREE.Mesh(g, this.m.combine);
    this.quad.frustumCulled = false;
    this.scene.add(this.quad);
  }

  setSize(w: number, h: number): void {
    if (w === this.w && h === this.h) return;
    this.w = w;
    this.h = h;
    this.hdr.setSize(w, h);
    this.ldr.setSize(w, h);
    let bw = Math.max(1, w >> 1);
    let bh = Math.max(1, h >> 1);
    this.bright.setSize(bw, bh);
    for (let i = 0; i < 5; i++) {
      bw = Math.max(1, bw >> 1);
      bh = Math.max(1, bh >> 1);
      this.down[i].setSize(bw, bh);
      this.up[i].setSize(bw, bh);
    }
    this.shafts.setSize(Math.max(1, w >> 2), Math.max(1, h >> 2));
  }

  private pass(material: THREE.RawShaderMaterial, out: THREE.WebGLRenderTarget | null): void {
    this.quad.material = material;
    this.renderer.setRenderTarget(out);
    this.renderer.render(this.scene, this.cam);
  }

  /** Run the chain on what was rendered into `hdr` and write the result to the canvas. */
  render(camera: THREE.PerspectiveCamera, s: PostSettings, dt: number): void {
    this.time += dt;
    const m = this.m;
    // bloom
    m.bright.uniforms.tSrc.value = this.hdr.texture;
    this.pass(m.bright, this.bright);
    let src = this.bright;
    for (let i = 0; i < 5; i++) {
      m.down.uniforms.tSrc.value = src.texture;
      m.down.uniforms.uTexel.value.set(1 / src.width, 1 / src.height);
      this.pass(m.down, this.down[i]);
      src = this.down[i];
    }
    let prev: THREE.Texture = this.black;
    for (let i = 4; i >= 0; i--) {
      const from = i === 4 ? this.down[4] : this.up[i + 1];
      m.up.uniforms.tSrc.value = from.texture;
      m.up.uniforms.tPrev.value = i === 4 ? this.black : this.down[i].texture;
      m.up.uniforms.uTexel.value.set(1 / from.width, 1 / from.height);
      this.pass(m.up, this.up[i]);
      prev = this.up[i].texture;
    }
    // light shafts: project the sun direction to the screen
    const sunPos = camera.position.clone().addScaledVector(s.sunWorldDir, 1000).project(camera);
    const facing = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion).dot(s.sunWorldDir);
    const onScreen = 1 - THREE.MathUtils.smoothstep(Math.max(Math.abs(sunPos.x), Math.abs(sunPos.y)), 1.0, 1.8);
    const strength = s.shafts * Math.max(0, facing) * onScreen * THREE.MathUtils.smoothstep(s.sunWorldDir.y, -0.02, 0.12);
    m.shafts.uniforms.tSrc.value = this.hdr.texture;
    m.shafts.uniforms.tDepth.value = this.hdr.depthTexture;
    m.shafts.uniforms.uSun.value.set(sunPos.x * 0.5 + 0.5, sunPos.y * 0.5 + 0.5);
    m.shafts.uniforms.uStrength.value = strength;
    this.pass(m.shafts, this.shafts);
    // combine + tone map
    const c = m.combine.uniforms;
    c.tHdr.value = this.hdr.texture;
    c.tBloom.value = prev;
    c.tShafts.value = this.shafts.texture;
    c.uExposure.value = s.exposure;
    c.uBloom.value = s.bloom;
    c.uShafts.value = 0.35;
    c.uTime.value = this.time % 100;
    const sc = s.sunColor;
    const mx = Math.max(sc.x, sc.y, sc.z, 1e-3);
    c.uShaftTint.value.set(sc.x / mx, sc.y / mx, sc.z / mx);
    this.pass(m.combine, this.ldr);
    // antialiasing to the canvas
    m.fxaa.uniforms.tSrc.value = this.ldr.texture;
    m.fxaa.uniforms.uTexel.value.set(1 / this.w, 1 / this.h);
    this.pass(m.fxaa, null);
  }

  dispose(): void {
    for (const t of [this.hdr, this.ldr, this.bright, this.shafts, ...this.down, ...this.up]) t.dispose();
    Object.values(this.m).forEach((x) => x.dispose());
    this.black.dispose();
  }
}
