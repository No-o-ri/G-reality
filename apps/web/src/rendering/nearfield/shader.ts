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
uniform vec4 uFire[8];       // point lights (fires, glowing objects): world position, intensity
uniform vec3 uFireColor[8];  // their colors
uniform int uFireCount;

${NOISE_GLSL}
${SDF_GLSL}
${TERRAIN_GLSL}
${sceneGlsl}

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
    float dw = p.y - uTerrainB.w;
    if (dw < h.d) { h.d = dw; h.mat = MAT_WATER; }
  }
  vec2 g = (p.xz - uGridInfo.xy) / uGridInfo.z;
  if (uGridInfo.w > 0.5 && insideGrid(g)) {
    ivec2 cell = ivec2(g);
    float skip = max(0.3, gEps * 2.0);
    for (int k = 0; k < 4; k++) {
      vec4 ids = texelFetch(uGrid, ivec2(cell.x * 4 + k, cell.y), 0);
      bool done = false;
      for (int j = 0; j < 4; j++) {
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
  for (int i = 0; i < HARD_MAX_STEPS; i++) {
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
  vec2 k = vec2(1.0, -1.0);
  return normalize(
    k.xyy * mapScene(p + k.xyy * e, oct).d +
    k.yyx * mapScene(p + k.yyx * e, oct).d +
    k.yxy * mapScene(p + k.yxy * e, oct).d +
    k.xxx * mapScene(p + k.xxx * e, oct).d);
}

// Cheap ambient occlusion: how much closer than expected is the nearest surface along the normal.
float ambientOcclusion(vec3 p, vec3 n) {
  float occ = 0.0;
  float scale = 1.0;
  for (int i = 1; i <= 3; i++) {
    float dist = 0.18 * float(i * i);
    gEps = 0.0;
    float d = mapScene(p + n * dist, int(uTerrainB.y)).d;
    occ += (dist - min(d, dist)) * scale;
    scale *= 0.55;
  }
  return clamp(1.0 - 0.9 * occ, 0.0, 1.0);
}

float softShadow(vec3 ro, vec3 rd, float tMax) {
  if (uShadowSteps <= 0) return 1.0;
  float res = 1.0;
  float t = 0.08;
  for (int i = 0; i < 48; i++) {
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

vec3 skyColor(vec3 rd) {
  float up = clamp(rd.y, 0.0, 1.0);
  vec3 col = mix(uFogColor, uSkyColor, pow(up, 0.45));
  float sun = max(dot(rd, uSunDir), 0.0);
  col += uSunColor * (pow(sun, 350.0) * 6.0 + pow(sun, 12.0) * 0.18);
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
  vec3 fogCol = mix(uFogColor, uSunColor, pow(sun, 8.0) * 0.35);
  return mix(col, fogCol, f);
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
    n = sceneNormal(p, t);
    matId = hit.mat;
    vec3 albedo;
    float rough = 0.9;
    vec3 glow = vec3(0.0);
    float emissive = 0.0;
    if (hit.mat == MAT_WATER) {
      vec4 w = materialColor(WATER_MAT);
      float fres = pow(1.0 - max(dot(-rd, vec3(0.0, 1.0, 0.0)), 0.0), 4.0);
      vec3 refl = skyColor(reflect(rd, normalize(vec3(0.02 * sin(p.x * 0.7 + uTime), 1.0, 0.02 * cos(p.z * 0.6 + uTime * 0.8)))));
      albedo = w.rgb;
      col = mix(w.rgb * (uAmbient + 0.4 * max(uSunDir.y, 0.0)), refl, 0.25 + 0.6 * fres);
    } else {
      int mi;
      if (hit.mat == MAT_TERRAIN) {
        float slope = 1.0 - n.y;
        mi = GROUND_MAT;
        float cliff = smoothstep(0.22, 0.42, slope);
        vec4 g = materialColor(GROUND_MAT);
        vec4 c = materialColor(CLIFF_MAT);
        albedo = mix(g.rgb, c.rgb, cliff);
        vec4 gx = materialExtra(GROUND_MAT);
        float v = vnoise2(p.xz * gx.y, uSeedOffset) * 0.65 + vnoise2(p.xz * gx.y * 7.3, uSeedOffset) * 0.35;
        albedo *= 1.0 + gx.x * (2.0 * v - 1.0);
        albedo = mix(albedo, albedo * vec3(1.18, 1.08, 0.72), smoothstep(0.45, 0.8, vnoise2(p.xz * 0.021, uSeedOffset + 37.0)) * 0.5);
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
        albedo *= 1.0 + mx.x * (2.0 * vnoise3(p * mx.y, 3.0) - 1.0);
        if (hit.inst >= 0) {
          vec4 fx = texelFetch(uInst, ivec2(4, hit.inst), 0);
          // burn visual is derived from authoritative entity state (condition / burn parameter)
          albedo = mix(albedo, vec3(0.025, 0.022, 0.02), clamp(fx.z, 0.0, 1.0) * 0.92);
          if (fx.y > 0.5) {
            float flick = 0.6 + 0.4 * sin(uTime * 9.0 + p.y * 2.5 + p.x * 1.7);
            float lick = vnoise3(p * 1.8 + vec3(0.0, -uTime * 2.2, 0.0), 5.0);
            emissive += 2.2 * flick * smoothstep(0.52, 0.86, lick);
          }
        }
      }
      float ndl = max(dot(n, uSunDir), 0.0);
      float sh = ndl > 0.0 ? softShadow(p + n * 0.06, uSunDir, 90.0) : 0.0;
      float ao = ambientOcclusion(p, n);
      vec3 hemi = mix(uFogColor * 0.35, uSkyColor, 0.5 + 0.5 * n.y) * uAmbient * ao;
      vec3 h = normalize(uSunDir - rd);
      float spec = pow(max(dot(n, h), 0.0), mix(64.0, 4.0, rough)) * (1.0 - rough) * 0.5;
      col = albedo * (uSunColor * ndl * sh * 1.6 + hemi) + uSunColor * spec * sh;
      col += vec3(1.0, 0.42, 0.08) * emissive + glow * (0.8 + 0.2 * vnoise3(p * 3.0 + vec3(uTime * 0.4), 9.0));
      // light from fires (derived from burning entities): warm, flickering, unshadowed point lights
      for (int i = 0; i < 8; i++) {
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

vec3 aces(vec3 x) {
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}
void main() {
  vec4 c = texture(tColor, vUv);
  vec3 col = uRaw == 1 ? clamp(c.rgb, 0.0, 1.0) : aces(c.rgb * uExposure);
  fragColor = vec4(pow(col, vec3(1.0 / 2.2)), 1.0);
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
