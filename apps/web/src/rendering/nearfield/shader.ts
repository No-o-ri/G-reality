/**
 * Near-field ray-march program. The scene is: analytic terrain (terrain-v1) + optional water plane +
 * entity instances evaluated through the generated archetype functions, accelerated by an XZ grid.
 *
 * Output: linear HDR color (alpha 1 = surface hit, 0 = sky) and true perspective depth in gl_FragDepth,
 * computed with the same view-projection matrix as the scene camera so later passes (Gaussian splats)
 * depth-test correctly against this geometry.
 *
 * Capture mode (uCapture = 1, Gaussian reconstruction views): the ray is clipped to one region's box, fog and debug
 * views are off, and the output is the display-referred color exactly as the composite pass would encode it, with
 * linear depth along the camera's forward axis in alpha (0 = no surface inside the box, −1 = ray entered the box
 * inside a solid, i.e. it would look at a cut face; such rays are unobserved, not empty).
 */
import { NOISE_GLSL, SDF_GLSL, TERRAIN_GLSL } from "../../sdf/glsl";

export const FULLSCREEN_VERT = /* glsl */ `
in vec3 position;
out vec2 vUv;
void main() {
  vUv = position.xy * 0.5 + 0.5;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

export interface SceneMaterialSlots {
  ground: number;
  cliff: number;
  peak: number; // -1 when the world has no peak material
  water: number; // -1 when the world has no water
}

export function buildMarchFragment(sceneGlsl: string, slots: SceneMaterialSlots): string {
  return /* glsl */ `
precision highp float;
precision highp int;
precision highp sampler2D;

#define MAT_TERRAIN -1.0
#define MAT_WATER -2.0
#define MAT_BOUND -3.0
#define GROUND_MAT ${slots.ground}
#define CLIFF_MAT ${slots.cliff}
#define PEAK_MAT ${slots.peak}
#define WATER_MAT ${slots.water}
#define HARD_MAX_STEPS 256

in vec2 vUv;
out vec4 fragColor;

// Always 0 at runtime; loops starting at ZERO cannot be unrolled by the shader compiler (see sdf/glsl.ts).
uniform int uZero;
#define ZERO min(uZero, 0)

uniform mat4 uCamWorld;
uniform mat4 uProjInv;
uniform mat4 uViewProj;
uniform vec3 uCamPos;
uniform float uTime;
uniform float uFar;
uniform int uMaxSteps;
uniform int uShadowSteps;
uniform int uDebugView;      // 0 final, 1 normals, 2 step count, 3 depth, 4 material, 5 instance evaluations
uniform float uTerrainStep;  // Lipschitz correction for the heightfield distance bound
uniform float uPeakHeight;

uniform sampler2D uInst;     // ${"INSTANCE layout: see buffers.ts"}
uniform sampler2D uGrid;
uniform vec4 uGridInfo;      // origin.x, origin.z, cell size, instance count
uniform ivec2 uGridDim;

uniform vec3 uSunDir;
uniform vec3 uSunColor;
uniform vec3 uSkyColor;
uniform vec3 uFogColor;
uniform float uAmbient;
uniform vec3 uFog;           // density, height falloff, base height

uniform int uCapture;        // 1 = reconstruction capture (see header)
uniform vec3 uClipMin;
uniform vec3 uClipMax;
uniform vec3 uCamFwd;        // unit camera forward (world), for linear depth
uniform vec3 uSunRadiance;   // sun radiance above the atmosphere scaled for display (sky model input)
uniform float uDaylight;     // 0 night .. 1 day
uniform vec4 uCloud;         // coverage 0..1, wind x, wind z, unused
uniform vec4 uRipples[8];    // water ripples: x, z, start time, strength
uniform int uRippleCount;
uniform vec4 uFire[8];       // point lights (fires, glowing objects): world position, intensity
uniform vec3 uFireColor[8];  // their colors
uniform int uFireCount;

${NOISE_GLSL}
${SDF_GLSL}
${TERRAIN_GLSL}
${sceneGlsl}

// Gentle animated swell for the water surface (geometry) — normals add finer detail in waterNormal().
float waveHeight(vec2 xz) {
  float t = uTime;
  return 0.045 * sin(dot(xz, vec2(0.21, 0.13)) + t * 1.1) + 0.03 * sin(dot(xz, vec2(-0.17, 0.29)) + t * 1.4)
       + 0.015 * sin(dot(xz, vec2(0.53, -0.41)) + t * 2.3);
}

struct Hit {
  float d;
  float mat;
  int inst;
};

float gEps = 0.002;
int gInstEvals = 0;

int terrainLod(float t) {
  int full = int(uTerrainB.y);
  if (t < 120.0) return full;
  if (t < 320.0) return max(full - 1, 1);
  return max(full - 2, 1);
}

bool insideGrid(vec2 g) {
  return g.x >= 0.0 && g.y >= 0.0 && g.x < float(uGridDim.x) && g.y < float(uGridDim.y);
}

Hit mapScene(vec3 p, int oct) {
  Hit h;
  h.inst = -1;
  h.mat = MAT_TERRAIN;
  h.d = (p.y - terrainHeightLod(p.xz, oct)) * uTerrainStep;
  if (WATER_MAT >= 0) {
    float dw = p.y - uTerrainB.w - waveHeight(p.xz);
    if (dw < h.d) { h.d = dw; h.mat = MAT_WATER; }
  }
  vec2 g = (p.xz - uGridInfo.xy) / uGridInfo.z;
  if (uGridInfo.w > 0.5 && insideGrid(g)) {
    ivec2 cell = ivec2(g);
    float skip = max(0.3, gEps * 2.0);
    for (int k = ZERO; k < 4; k++) {
      vec4 ids = texelFetch(uGrid, ivec2(cell.x * 4 + k, cell.y), 0);
      bool done = false;
      for (int j = ZERO; j < 4; j++) {
        float fid = ids[j];
        if (fid < 0.0) { done = true; break; }
        int i = int(fid + 0.5);
        vec4 bs = texelFetch(uInst, ivec2(2, i), 0);
        float db = length(p - bs.xyz) - bs.w;
        if (db >= h.d) continue;
        if (db > skip) {
          // Far from this instance: its bounding sphere distance is a valid lower bound.
          h.d = db; h.mat = MAT_BOUND; h.inst = i;
          continue;
        }
        vec4 ps = texelFetch(uInst, ivec2(0, i), 0);
        vec4 q = texelFetch(uInst, ivec2(1, i), 0);
        vec4 prm = texelFetch(uInst, ivec2(3, i), 0);
        vec4 fx = texelFetch(uInst, ivec2(4, i), 0);
        vec3 local = quatRotateInv(q, p - ps.xyz) / ps.w;
        vec2 r = evalArchetype(int(fx.x + 0.5), local, prm);
        gInstEvals++;
        float d = r.x * ps.w;
        if (d < h.d) { h.d = d; h.mat = r.y; h.inst = i; }
      }
      if (done) break;
    }
  }
  return h;
}

// Never step across a grid cell boundary (or into the grid from outside): instances registered in the
// next cell have not been considered yet.
float limitStep(vec3 p, vec3 rd, float stepLen) {
  if (uGridInfo.w < 0.5) return stepLen;
  vec2 g = (p.xz - uGridInfo.xy) / uGridInfo.z;
  vec2 inv = 1.0 / (abs(rd.xz) + 1e-6) * sign(rd.xz + 1e-9);
  if (insideGrid(g)) {
    vec2 cellMin = uGridInfo.xy + floor(g) * uGridInfo.z;
    vec2 target = cellMin + step(0.0, rd.xz) * uGridInfo.z;
    vec2 tx = (target - p.xz) * inv;
    return min(stepLen, max(min(tx.x, tx.y), 0.0) + 0.02);
  }
  vec2 lo = uGridInfo.xy;
  vec2 hi = uGridInfo.xy + vec2(uGridDim) * uGridInfo.z;
  vec2 t0 = (lo - p.xz) * inv;
  vec2 t1 = (hi - p.xz) * inv;
  vec2 tmin = min(t0, t1);
  vec2 tmax = max(t0, t1);
  float enter = max(tmin.x, tmin.y);
  float leave = min(tmax.x, tmax.y);
  if (leave > 0.0 && enter > 0.0 && enter < leave) return min(stepLen, enter + 0.02);
  return stepLen;
}

bool march(vec3 ro, vec3 rd, float tMin, float tMax, int maxSteps, out float t, out Hit hit, out int steps) {
  t = tMin;
  steps = 0;
  for (int i = ZERO; i < HARD_MAX_STEPS; i++) {
    if (i >= maxSteps) break;
    steps = i + 1;
    vec3 p = ro + rd * t;
    gEps = max(0.002, 0.0012 * t);
    hit = mapScene(p, terrainLod(t));
    if (hit.d < gEps && hit.mat != MAT_BOUND) return true;
    t += limitStep(p, rd, max(hit.d, gEps));
    if (t > tMax) return false;
  }
  // Step budget exhausted (typically a grazing ray): if we are already close to a surface relative to
  // the distance travelled, shade it rather than punching a sky-coloured hole through the silhouette.
  return hit.mat != MAT_BOUND && hit.d < 0.05 * t + 0.05;
}

vec3 sceneNormal(vec3 p, float t) {
  float e = max(0.015, 0.0015 * t);
  int oct = terrainLod(t * 0.5);
  gEps = 0.0; // force exact evaluation near the surface (skip threshold stays at 0.3 m)
  vec3 n = vec3(0.0);
  for (int i = ZERO; i < 4; i++) {
    vec3 k = 0.5773 * (2.0 * vec3(float(((i + 3) >> 1) & 1), float((i >> 1) & 1), float(i & 1)) - 1.0);
    n += k * mapScene(p + k * e, oct).d;
  }
  return normalize(n);
}

// Cheap ambient occlusion: how much closer than expected is the nearest surface along the normal.
float ambientOcclusion(vec3 p, vec3 n) {
  float occ = 0.0;
  float scale = 1.0;
  for (int i = ZERO + 1; i <= 5; i++) {
    float dist = 0.12 * float(i * i);
    gEps = 0.0;
    float d = mapScene(p + n * dist, int(uTerrainB.y)).d;
    occ += (dist - min(d, dist)) * scale;
    scale *= 0.55;
  }
  return clamp(1.0 - 0.75 * occ, 0.0, 1.0);
}

float softShadow(vec3 ro, vec3 rd, float tMax) {
  if (uShadowSteps <= 0) return 1.0;
  float res = 1.0;
  float t = 0.08;
  for (int i = ZERO; i < 48; i++) {
    if (i >= uShadowSteps) break;
    vec3 p = ro + rd * t;
    gEps = 0.004;
    Hit h = mapScene(p, int(uTerrainB.y));
    if (h.mat != MAT_BOUND) {
      if (h.d < 0.004) return 0.0;
      res = min(res, 10.0 * h.d / t);
    }
    t += limitStep(p, rd, clamp(max(h.d, 0.03 + 0.025 * t), 0.05, 8.0));
    if (t > tMax) break;
  }
  return clamp(res, 0.0, 1.0);
}

// Physically based single scattering (Rayleigh + Mie, Nishita-style) through a spherical atmosphere.
// The same model runs on the CPU (rendering/atmosphere.ts) to derive sun, zenith and horizon colors for lighting and fog.
const float EARTH_R = 6360e3;
const float ATMO_R = 6420e3;
const vec3 BETA_R = vec3(5.8e-6, 13.5e-6, 33.1e-6);
const float BETA_M = 21e-6;
vec2 raySphere(vec3 ro, vec3 rd, float r) {
  float b = dot(ro, rd);
  float c = dot(ro, ro) - r * r;
  float h = b * b - c;
  if (h < 0.0) return vec2(1e9, -1e9);
  h = sqrt(h);
  return vec2(-b - h, -b + h);
}
vec3 atmosphere(vec3 rd) {
  vec3 ro = vec3(0.0, EARTH_R + 50.0, 0.0);
  vec2 hit = raySphere(ro, rd, ATMO_R);
  float tMax = hit.y;
  vec2 ground = raySphere(ro, rd, EARTH_R);
  if (ground.x > 0.0) tMax = min(tMax, ground.x);
  float seg = tMax / 8.0;
  float mu = dot(rd, uSunDir);
  float phaseR = 3.0 / (16.0 * 3.14159) * (1.0 + mu * mu);
  float g = 0.76;
  float phaseM = 3.0 / (8.0 * 3.14159) * ((1.0 - g * g) * (1.0 + mu * mu)) / ((2.0 + g * g) * pow(1.0 + g * g - 2.0 * g * mu, 1.5));
  vec3 sumR = vec3(0.0), sumM = vec3(0.0);
  float odR = 0.0, odM = 0.0;
  for (int i = ZERO; i < 8; i++) {
    vec3 x = ro + rd * (seg * (float(i) + 0.5));
    float h = length(x) - EARTH_R;
    float hr = exp(-h / 8000.0) * seg;
    float hm = exp(-h / 1200.0) * seg;
    odR += hr;
    odM += hm;
    float segL = raySphere(x, uSunDir, ATMO_R).y / 4.0;
    float lR = 0.0, lM = 0.0;
    for (int j = ZERO; j < 4; j++) {
      vec3 y = x + uSunDir * (segL * (float(j) + 0.5));
      float hy = length(y) - EARTH_R;
      lR += exp(-hy / 8000.0) * segL;
      lM += exp(-hy / 1200.0) * segL;
    }
    vec3 tau = BETA_R * (odR + lR) + BETA_M * 1.1 * (odM + lM);
    vec3 att = exp(-tau);
    sumR += att * hr;
    sumM += att * hm;
  }
  return uSunRadiance * (sumR * BETA_R * phaseR + sumM * BETA_M * phaseM);
}
float cloudField(vec2 uv) {
  float n = 0.0, a = 0.5;
  for (int i = ZERO; i < 4; i++) {
    n += a * vnoise2(uv, uSeedOffset + 91.0);
    uv = uv * 2.03 + 17.1;
    a *= 0.5;
  }
  return n;
}
// Normal (4 tetrahedral taps), ambient occlusion (5 taps) and the soft sun shadow in ONE loop. Each separate loop
// that samples the scene makes the shader compiler emit another full copy of the scene function (terrain, water and
// every generated object); merging them keeps world shaders small enough to compile in seconds.
void surfaceQueries(vec3 p, float t, out vec3 nOut, out float aoOut, out float shOut) {
  float e = max(0.015, 0.0015 * t);
  // the same terrain detail level the march hit, so the taps probe the surface actually shaded
  int octFull = terrainLod(t);
  vec3 nacc = vec3(0.0);
  vec3 n = vec3(0.0, 1.0, 0.0);
  vec3 ro = p;
  float occ = 0.0;
  float scale = 1.0;
  float res = 1.0;
  float ts = 0.08;
  for (int i = ZERO; i < 57; i++) {
    vec3 q;
    vec3 k = vec3(0.0);
    int oct = octFull;
    float dist = 0.0;
    if (i < 4) {
      k = 0.5773 * (2.0 * vec3(float(((i + 3) >> 1) & 1), float((i >> 1) & 1), float(i & 1)) - 1.0);
      q = p + k * e;
      gEps = 0.0;
    } else if (i < 9) {
      if (i == 4) {
        n = normalize(nacc);
        ro = p + n * 0.06;
      }
      float a = float(i - 3);
      dist = 0.12 * a * a;
      q = p + n * dist;
      gEps = 0.0;
    } else {
      // sun shadow: skipped for surfaces facing away from the sun and when shadows are disabled
      if (i - 9 >= uShadowSteps || dot(n, uSunDir) <= 0.0) {
        if (dot(n, uSunDir) <= 0.0) res = 0.0;
        break;
      }
      q = ro + uSunDir * ts;
      gEps = 0.004;
    }
    Hit h = mapScene(q, oct);
    if (i < 4) {
      nacc += k * h.d;
    } else if (i < 9) {
      occ += (dist - min(h.d, dist)) * scale;
      scale *= 0.55;
    } else {
      if (h.mat != MAT_BOUND) {
        if (h.d < 0.004) { res = 0.0; break; }
        res = min(res, 7.0 * h.d / ts);  // softer penumbrae
      }
      ts += limitStep(q, uSunDir, clamp(max(h.d, 0.03 + 0.025 * ts), 0.05, 8.0));
      if (ts > 90.0) break;
    }
  }
  nOut = n;
  aoOut = mix(clamp(1.0 - 0.55 * occ, 0.0, 1.0), 1.0, smoothstep(40.0, 160.0, t));
  shOut = clamp(res, 0.0, 1.0);
}

vec3 skyColor(vec3 rd) {
  vec3 dir = normalize(vec3(rd.x, max(rd.y, -0.02) + 0.02, rd.z));
  vec3 col = atmosphere(dir);
  float mu = dot(rd, uSunDir);
  // sun disk (HDR; feeds the bloom), dimmed by its own colour near the horizon
  col += uSunColor * 30.0 * smoothstep(0.99985, 0.99995, mu) * step(0.0, rd.y);
  // night: faint blue sky glow and stars
  float night = 1.0 - uDaylight;
  if (night > 0.0 && rd.y > 0.0) {
    vec3 c = floor(rd * 420.0);
    float h = fract(sin(dot(c, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
    col += vec3(0.8, 0.85, 1.0) * step(0.9985, h) * night * 2.0 * smoothstep(0.0, 0.15, rd.y);
    col += vec3(0.008, 0.012, 0.025) * night;
  }
  // cloud layer at 1.8 km, drifting with the wind; lit by the sun with a self-shadowing sample toward it
  if (rd.y > 0.0 && uCloud.x > 0.01) {
    float tc = (1800.0 - uCamPos.y) / rd.y;
    vec2 uv = (uCamPos.xz + rd.xz * tc) * 0.00045 + uCloud.yz * uTime * 0.00004;
    float n = cloudField(uv);
    float d = smoothstep(1.0 - uCloud.x * 0.75 - 0.15, 1.0 - uCloud.x * 0.75 + 0.25, n);
    float shade = cloudField(uv + uSunDir.xz * 0.04);
    float lit = clamp(1.0 - (shade - n) * 3.0, 0.25, 1.2);
    vec3 cc = uSunColor * lit * 1.6 + uSkyColor * 0.9;
    float fade = smoothstep(0.02, 0.25, rd.y);
    col = mix(col, cc * mix(0.35, 1.0, uDaylight) + vec3(0.01), d * fade * 0.92);
  }
  return col;
}

// Analytic exponential height fog integrated along the ray.
float fogAmount(vec3 ro, vec3 rd, float t) {
  float density = uFog.x;
  float falloff = max(uFog.y, 1e-4);
  float a = density * exp(-falloff * (ro.y - uFog.z));
  float k = falloff * rd.y;
  float integral = abs(k) < 1e-4 ? t : (1.0 - exp(-k * t)) / k;
  return 1.0 - exp(-a * integral);
}

vec3 applyFog(vec3 col, vec3 ro, vec3 rd, float t) {
  float f = fogAmount(ro, rd, t);
  float sun = max(dot(rd, uSunDir), 0.0);
  vec3 fogCol = mix(uFogColor, uFogColor + uSunColor * 0.6, pow(sun, 6.0));
  return mix(col, fogCol, f);
}

vec3 waterNormal(vec2 xz, float t) {
  float time = uTime;
  vec2 g = vec2(0.0);
  // analytic gradients of the swell plus finer moving wavelets
  g += 0.045 * cos(dot(xz, vec2(0.21, 0.13)) + time * 1.1) * vec2(0.21, 0.13);
  g += 0.03 * cos(dot(xz, vec2(-0.17, 0.29)) + time * 1.4) * vec2(-0.17, 0.29);
  g += 0.015 * cos(dot(xz, vec2(0.53, -0.41)) + time * 2.3) * vec2(0.53, -0.41);
  g += 0.012 * cos(dot(xz, vec2(1.7, 0.9)) + time * 3.1) * vec2(1.7, 0.9);
  g += 0.008 * cos(dot(xz, vec2(-1.3, 2.1)) + time * 3.7) * vec2(-1.3, 2.1);
  float detail = 1.0 / (1.0 + t * 0.02);
  vec2 q = xz * 0.9 + vec2(time * 0.3, -time * 0.2);
  g += detail * 0.06 * vec2(vnoise2(q, uSeedOffset) - vnoise2(q + vec2(0.5, 0.0), uSeedOffset),
                            vnoise2(q, uSeedOffset) - vnoise2(q + vec2(0.0, 0.5), uSeedOffset));
  // interaction ripples: expanding damped rings around where the player moved through the water
  for (int i = ZERO; i < 8; i++) {
    if (i >= uRippleCount) break;
    vec2 d = xz - uRipples[i].xy;
    float r = length(d);
    float age = time - uRipples[i].z;
    if (age < 0.0 || age > 4.0) continue;
    float front = r - age * 1.6;
    float amp = uRipples[i].w * exp(-age * 0.9) * exp(-front * front * 1.5) / (1.0 + r);
    g += amp * cos(front * 9.0) * 9.0 * d / max(r, 1e-3) * 0.02;
  }
  return normalize(vec3(-g.x, 1.0, -g.y));
}

// GGX specular with Schlick Fresnel (single sample, analytic light)
vec3 specularGGX(vec3 n, vec3 v, vec3 l, float rough, vec3 f0) {
  vec3 h = normalize(v + l);
  float a = max(rough * rough, 0.002);
  float nh = max(dot(n, h), 0.0);
  float nv = max(dot(n, v), 1e-3);
  float nl = max(dot(n, l), 0.0);
  float d = a * a / (3.14159 * pow(nh * nh * (a * a - 1.0) + 1.0, 2.0));
  float k = a * 0.5;
  float vis = 1.0 / ((nv * (1.0 - k) + k) * (nl * (1.0 - k) + k));
  vec3 f = f0 + (1.0 - f0) * pow(1.0 - max(dot(h, v), 0.0), 5.0);
  return d * vis * f * nl * 0.25;
}

// small-scale surface relief from the gradient of value noise (procedural normal map)
vec3 bump(vec3 n, vec3 p, float freq, float strength) {
  float e = 0.35 / freq;
  float c = vnoise3(p * freq, 7.0);
  vec3 g = vec3(vnoise3((p + vec3(e, 0.0, 0.0)) * freq, 7.0) - c,
                vnoise3((p + vec3(0.0, e, 0.0)) * freq, 7.0) - c,
                vnoise3((p + vec3(0.0, 0.0, e)) * freq, 7.0) - c) / e;
  return normalize(n - strength * (g - n * dot(g, n)) / freq);
}

// same tone curve as COMPOSITE_FRAG
vec3 acesTone(vec3 x) {
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

vec3 heat(float x) {
  x = clamp(x, 0.0, 1.0);
  return clamp(vec3(1.5 * x, 2.0 * x * (1.0 - x) * 2.0, 1.5 * (1.0 - x)) , 0.0, 1.0);
}

void main() {
  vec4 ndc = vec4(vUv * 2.0 - 1.0, 1.0, 1.0);
  vec4 viewDir = uProjInv * ndc;
  vec3 rd = normalize((uCamWorld * vec4(viewDir.xyz / viewDir.w, 0.0)).xyz);
  vec3 ro = uCamPos;

  float t = 0.0;
  Hit hit;
  hit.d = 1e9; hit.mat = MAT_BOUND; hit.inst = -1;
  int steps = 0;
  float tStart = 0.05;
  float tEnd = uFar;
  bool inClip = true;
  float enter = 0.0;
  if (uCapture == 1) {
    vec3 inv = 1.0 / (abs(rd) + 1e-9) * sign(rd + 1e-12);
    vec3 t0 = (uClipMin - ro) * inv;
    vec3 t1 = (uClipMax - ro) * inv;
    vec3 tlo = min(t0, t1);
    vec3 thi = max(t0, t1);
    enter = max(max(tlo.x, tlo.y), tlo.z);
    float leave = min(min(thi.x, thi.y), thi.z);
    inClip = leave > max(enter, 0.0);
    tStart = max(tStart, enter);
    tEnd = min(tEnd, leave);
  }
  bool found = inClip && march(ro, rd, tStart, tEnd, uMaxSteps, t, hit, steps);
  // A ray that enters the clip box inside a solid (terrain or an object cut by the box wall) would see the cut face,
  // which is not a surface of the world. In the world that ray is blocked by the neighbouring region, so it carries
  // no information about this region: report it as unobserved (training and evaluation ignore it).
  bool cut = uCapture == 1 && found && steps <= 1 && enter > 0.05 && hit.d < 0.0;
  if (cut) found = false;
  int marchEvals = gInstEvals;

  vec3 col;
  float depth = 1.0;
  vec3 n = vec3(0.0, 1.0, 0.0);
  float matId = -9.0;
  if (found) {
    vec3 p = ro + rd * t;
    float aoQ;
    float shQ;
    surfaceQueries(p, t, n, aoQ, shQ);
    matId = hit.mat;
    vec3 albedo;
    float rough = 0.9;
    vec3 glow = vec3(0.0);
    float emissive = 0.0;
    if (hit.mat == MAT_WATER) {
      vec4 w = materialColor(WATER_MAT);
      vec3 wn = waterNormal(p.xz, t);
      float depthW = max(0.0, uTerrainB.w - terrainHeight(p.xz));
      vec3 v = -rd;
      float fres = 0.02 + 0.98 * pow(1.0 - max(dot(v, wn), 0.0), 5.0);
      vec3 rdir = reflect(rd, wn);
      rdir.y = abs(rdir.y);
      vec3 refl = skyColor(rdir);
      // water body: shallow water shows the (wet) ground through it, deep water absorbs to its own colour
      vec3 bed = materialColor(GROUND_MAT).rgb * 0.45;
      float absorb = 1.0 - exp(-depthW * 0.45);
      vec3 body = mix(bed, w.rgb, absorb) * (uSunColor * max(uSunDir.y, 0.0) * 0.9 + uSkyColor * uAmbient * 2.0);
      float shW = shQ;
      col = mix(body, refl, fres) + specularGGX(wn, v, uSunDir, 0.08, vec3(0.02)) * uSunColor * 6.0 * shW;
      // shoreline foam where the water is shallow, broken up by noise
      float foam = (1.0 - smoothstep(0.0, 0.45, depthW)) * smoothstep(0.35, 0.75, vnoise2(p.xz * 1.7 + uTime * 0.4, uSeedOffset));
      col = mix(col, vec3(0.85) * (uSunColor * 0.7 + uSkyColor * 0.5), foam * 0.6);
      albedo = w.rgb;
    } else {
      int mi;
      if (hit.mat == MAT_TERRAIN) {
        n = bump(n, p, 1.3, 0.3 * (1.0 - smoothstep(15.0, 70.0, t)));
        float slope = 1.0 - n.y;
        mi = GROUND_MAT;
        // rock shows through where the ground is steep, with a noisy boundary instead of a hard slope cut
        float edge = vnoise2(p.xz * 0.35, uSeedOffset + 11.0);
        float cliff = smoothstep(0.2 + 0.08 * edge, 0.38 + 0.08 * edge, slope);
        vec4 g = materialColor(GROUND_MAT);
        vec4 c = materialColor(CLIFF_MAT);
        // sedimentary strata on rock faces: height bands, warped so they follow the terrain irregularly
        float band = p.y * 0.55 + 3.0 * vnoise2(p.xz * 0.04, uSeedOffset + 5.0);
        float strata = 0.5 + 0.5 * sin(band) * (0.6 + 0.4 * sin(band * 2.7 + 1.3));
        vec3 rock = c.rgb * mix(0.72, 1.18, strata) * (0.9 + 0.2 * vnoise3(p * 2.1, 3.0));
        albedo = mix(g.rgb, rock, cliff);
        vec4 gx = materialExtra(GROUND_MAT);
        float v = vnoise2(p.xz * gx.y, uSeedOffset) * 0.65 + vnoise2(p.xz * gx.y * 7.3, uSeedOffset) * 0.35;
        albedo *= 1.0 + gx.x * (2.0 * v - 1.0);
        // macro variation: drier / lusher patches over tens of meters, so wide views are not one flat colour
        float macro = vnoise2(p.xz * 0.012, uSeedOffset + 37.0);
        albedo = mix(albedo, albedo * vec3(1.22, 1.08, 0.7), smoothstep(0.5, 0.85, macro) * 0.55 * (1.0 - cliff));
        albedo = mix(albedo, albedo * vec3(0.78, 0.92, 0.82), smoothstep(0.5, 0.15, macro) * 0.4 * (1.0 - cliff));
        rough = mix(0.95, 0.75, cliff);
        if (PEAK_MAT >= 0) {
          float snow = smoothstep(uPeakHeight - 6.0, uPeakHeight + 10.0, p.y + 8.0 * (v - 0.5)) * (1.0 - smoothstep(0.35, 0.6, slope));
          albedo = mix(albedo, materialColor(PEAK_MAT).rgb, snow);
        }
        if (WATER_MAT >= 0) {
          // darker, wetter ground right at the waterline
          albedo *= mix(0.55, 1.0, smoothstep(0.0, 1.2, p.y - uTerrainB.w));
        }
      } else {
        mi = int(hit.mat + 0.5);
        vec4 m = materialColor(mi);
        vec4 mx = materialExtra(mi);
        albedo = m.rgb;
        rough = m.a;
        glow = materialEmissive(mi);
        n = bump(n, p, max(mx.y, 0.5) * 2.0, mx.x * 1.0 * (1.0 - smoothstep(10.0, 60.0, t)));
        albedo *= 1.0 + mx.x * (2.0 * vnoise3(p * mx.y, 3.0) - 1.0);
        if (hit.inst >= 0) {
          vec4 fx = texelFetch(uInst, ivec2(4, hit.inst), 0);
          // burn visual is derived from authoritative entity state (condition / burn parameter)
          albedo = mix(albedo, vec3(0.025, 0.022, 0.02), clamp(fx.z, 0.0, 1.0) * 0.92);
          glow *= fx.w;  // per-entity emission (state variable 'emission'; 1 by default)
          if (fx.y > 0.5) {
            float flick = 0.6 + 0.4 * sin(uTime * 9.0 + p.y * 2.5 + p.x * 1.7);
            float lick = vnoise3(p * 1.8 + vec3(0.0, -uTime * 2.2, 0.0), 5.0);
            emissive += 2.2 * flick * smoothstep(0.52, 0.86, lick);
          }
        }
      }
      float ndl = max(dot(n, uSunDir), 0.0);
      float sh = ndl > 0.0 ? shQ : 0.0;
      float ao = aoQ;
      // sky fill from above, warm bounce light from the sunlit ground below (n.y < 0 faces down)
      vec3 bounce = materialColor(GROUND_MAT).rgb * uSunColor * max(uSunDir.y, 0.0) * 0.35;
      vec3 skyFill = mix(uFogColor, uSkyColor, 0.5 + 0.5 * n.y) * (0.9 + 1.6 * uAmbient);
      vec3 hemi = (skyFill * (0.6 + 0.4 * n.y) + bounce * (0.5 - 0.5 * n.y)) * ao;
      float roughFar = mix(max(rough, 0.08), 1.0, smoothstep(30.0, 180.0, t));
      vec3 spec = specularGGX(n, -rd, uSunDir, roughFar, vec3(0.04)) * (1.0 - smoothstep(60.0, 220.0, t));
      col = albedo * (uSunColor * ndl * sh * 1.6 + hemi) + uSunColor * spec * sh * 3.0;
      col += vec3(1.0, 0.42, 0.08) * emissive + glow * 0.7 * (0.8 + 0.2 * vnoise3(p * 3.0 + vec3(uTime * 0.4), 9.0));
      // light from fires (derived from burning entities): warm, flickering, unshadowed point lights
      for (int i = ZERO; i < 8; i++) {
        if (i >= uFireCount) break;
        vec3 L = uFire[i].xyz - p;
        float d2 = max(dot(L, L), 0.25);
        float wrap = max(dot(n, L * inversesqrt(d2)), 0.0) * 0.75 + 0.25;
        col += albedo * uFireColor[i] * uFire[i].w * wrap / (1.0 + d2 * 0.12);
      }
    }
    if (uCapture == 0) col = applyFog(col, ro, rd, t);
    vec4 clip = uViewProj * vec4(p, 1.0);
    depth = clamp(0.5 * clip.z / clip.w + 0.5, 0.0, 0.9999999);
  } else {
    col = skyColor(rd);
  }

  if (uCapture == 1) {
    fragColor = cut ? vec4(0.0, 0.0, 0.0, -1.0)
              : found ? vec4(pow(acesTone(col), vec3(1.0 / 2.2)), t * dot(rd, uCamFwd)) : vec4(0.0);
    gl_FragDepth = depth;
    return;
  }

  if (uDebugView == 1) col = found ? n * 0.5 + 0.5 : vec3(0.0);
  else if (uDebugView == 2) col = heat(float(steps) / float(uMaxSteps));
  else if (uDebugView == 3) col = found ? vec3(1.0 - exp(-t * 0.01)) : vec3(1.0);
  else if (uDebugView == 4) {
    col = !found ? vec3(0.0)
        : matId == MAT_TERRAIN ? vec3(0.35, 0.3, 0.2)
        : matId == MAT_WATER ? vec3(0.1, 0.3, 0.8)
        : 0.5 + 0.5 * cos(6.2831 * (matId * 0.37 + vec3(0.0, 0.33, 0.67)));
  }
  else if (uDebugView == 5) col = heat(float(marchEvals) / 64.0);

  fragColor = vec4(col, found ? 1.0 : 0.0);
  gl_FragDepth = depth;
}
`;
}

/** Composite pass: tone map + sRGB encode the near-field target and re-emit its depth to the canvas. */
export const COMPOSITE_FRAG = /* glsl */ `
precision highp float;
precision highp sampler2D;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D tColor;
uniform sampler2D tDepth;
uniform float uExposure;
uniform int uRaw; // debug views are shown without tone mapping
uniform int uLinearOut; // 1 = write linear HDR (the post-processing chain tone maps)

vec3 aces(vec3 x) {
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}
void main() {
  vec4 c = texture(tColor, vUv);
  if (uLinearOut == 1 && uRaw == 0) {
    fragColor = vec4(c.rgb, 1.0);
  } else {
    vec3 col = uRaw == 1 ? clamp(c.rgb, 0.0, 1.0) : aces(c.rgb * uExposure);
    fragColor = vec4(pow(col, vec3(1.0 / 2.2)), 1.0);
  }
  gl_FragDepth = texture(tDepth, vUv).r;
}
`;

/** Evaluates terrainHeight on the GPU at given points so it can be compared with the CPU evaluator. */
export const TERRAIN_PROBE_FRAG = /* glsl */ `
precision highp float;
precision highp sampler2D;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D uPoints; // N x 1, rg = world x, z
${NOISE_GLSL}
${TERRAIN_GLSL}
void main() {
  vec2 xz = texelFetch(uPoints, ivec2(int(gl_FragCoord.x), 0), 0).rg;
  fragColor = vec4(terrainHeight(xz), 0.0, 0.0, 1.0);
}
`;
