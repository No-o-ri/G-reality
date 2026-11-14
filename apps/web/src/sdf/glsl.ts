/**
 * GLSL ES 3.00 library for the G-reality SDF scene language.
 *
 * This file is the reference semantics of every op in packages/contracts/schemas/geometry_ops.json.
 * The CPU evaluator (cpu.ts) and the Python terrain reference (services/world_api/terrain.py) must
 * reproduce these formulas; golden vectors in tests/golden keep them in agreement.
 */

/** Hashes and value noise. hash12/vnoise2 are `terrain-v1`; hash13/vnoise3/fbm3 are `value-fbm-v1`. */
export const NOISE_GLSL = /* glsl */ `
// Integer lattice hash: exact on every platform (GLSL uint, Python, TypeScript). A float hash such as
// fract(sin(..)) or Hoskins' hash amplifies 1-ulp rounding differences between GPU and CPU into
// ~1e-3 output differences, which showed up as a 15 cm terrain mismatch; integer arithmetic does not.
float hash12(ivec2 cell) {
  uvec2 u = uvec2(cell);
  uint h = u.x * 374761393u + u.y * 668265263u;
  h = (h ^ (h >> 13u)) * 1274126177u;
  h = h ^ (h >> 16u);
  return float(h >> 8u) / 16777216.0;
}
float vnoise2(vec2 p, vec2 seedOffset) {
  vec2 i = floor(p);
  vec2 f = p - i;
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  ivec2 l = ivec2(i) + ivec2(seedOffset);
  float a = hash12(l);
  float b = hash12(l + ivec2(1, 0));
  float c = hash12(l + ivec2(0, 1));
  float d = hash12(l + ivec2(1, 1));
  float top = a + (b - a) * u.x;
  float bottom = c + (d - c) * u.x;
  return top + (bottom - top) * u.y;
}
float hash13(vec3 p) {
  vec3 p3 = fract(p * 0.1031);
  p3 += dot(p3, p3.zyx + 31.32);
  return fract((p3.x + p3.y) * p3.z);
}
float vnoise3(vec3 p, float seed) {
  vec3 i = floor(p);
  vec3 f = p - i;
  vec3 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  vec3 l = i + vec3(seed * 17.0);
  float n000 = hash13(l);
  float n100 = hash13(l + vec3(1.0, 0.0, 0.0));
  float n010 = hash13(l + vec3(0.0, 1.0, 0.0));
  float n110 = hash13(l + vec3(1.0, 1.0, 0.0));
  float n001 = hash13(l + vec3(0.0, 0.0, 1.0));
  float n101 = hash13(l + vec3(1.0, 0.0, 1.0));
  float n011 = hash13(l + vec3(0.0, 1.0, 1.0));
  float n111 = hash13(l + vec3(1.0, 1.0, 1.0));
  float x00 = mix(n000, n100, u.x);
  float x10 = mix(n010, n110, u.x);
  float x01 = mix(n001, n101, u.x);
  float x11 = mix(n011, n111, u.x);
  return mix(mix(x00, x10, u.y), mix(x01, x11, u.y), u.z);
}
// sum_{i<octaves} 0.5^i * (2 * noise(p * 2^i) - 1)
float fbm3(vec3 p, int octaves, float seed) {
  float sum = 0.0;
  float amp = 1.0;
  float scale = 1.0;
  for (int i = 0; i < 5; i++) {
    if (i >= octaves) break;
    sum += amp * (2.0 * vnoise3(p * scale, seed) - 1.0);
    amp *= 0.5;
    scale *= 2.0;
  }
  return sum;
}
vec3 warp3(vec3 p, float amplitude, float frequency, float seed) {
  vec3 q = p * frequency;
  vec3 n = vec3(vnoise3(q, seed), vnoise3(q + vec3(19.1), seed), vnoise3(q + vec3(47.7), seed));
  return p + amplitude * (2.0 * n - 1.0);
}
`;

/** Primitive, boolean and domain operations. Units are meters; all primitives are centered unless noted. */
export const SDF_GLSL = /* glsl */ `
float sdSphere(vec3 p, float r) { return length(p) - r; }
// Bound-corrected approximation (not an exact distance).
float sdEllipsoid(vec3 p, vec3 r) {
  float k0 = length(p / r);
  float k1 = length(p / (r * r));
  return k0 * (k0 - 1.0) / max(k1, 1e-6);
}
float sdBox(vec3 p, vec3 b) {
  vec3 q = abs(p) - b;
  return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}
float sdRoundBox(vec3 p, vec3 b, float r) { return sdBox(p, b) - r; }
float sdCapsule(vec3 p, vec3 a, vec3 b, float r) {
  vec3 pa = p - a;
  vec3 ba = b - a;
  float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-8), 0.0, 1.0);
  return length(pa - ba * h) - r;
}
// Axis +Y, half height h.
float sdCylinder(vec3 p, float h, float r) {
  vec2 d = abs(vec2(length(p.xz), p.y)) - vec2(r, h);
  return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}
// Capped cone, axis +Y, half height h, radius r1 at y = -h and r2 at y = +h.
float sdCone(vec3 p, float h, float r1, float r2) {
  vec2 q = vec2(length(p.xz), p.y);
  vec2 k1 = vec2(r2, h);
  vec2 k2 = vec2(r2 - r1, 2.0 * h);
  vec2 ca = vec2(q.x - min(q.x, (q.y < 0.0) ? r1 : r2), abs(q.y) - h);
  vec2 cb = q - k1 + k2 * clamp(dot(k1 - q, k2) / dot(k2, k2), 0.0, 1.0);
  float s = (cb.x < 0.0 && ca.y < 0.0) ? -1.0 : 1.0;
  return s * sqrt(min(dot(ca, ca), dot(cb, cb)));
}
// Ring in the XZ plane.
float sdTorus(vec3 p, float major, float minor) {
  vec2 q = vec2(length(p.xz) - major, p.y);
  return length(q) - minor;
}
float sdPlane(vec3 p, vec3 n, float offset) { return dot(p, normalize(n)) - offset; }

float opSmoothUnion(float a, float b, float k) {
  float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
  return mix(b, a, h) - k * h * (1.0 - h);
}
// a minus b
float opSmoothSubtract(float a, float b, float k) {
  float h = clamp(0.5 - 0.5 * (a + b) / k, 0.0, 1.0);
  return mix(a, -b, h) + k * h * (1.0 - h);
}
float opSmoothIntersect(float a, float b, float k) {
  float h = clamp(0.5 - 0.5 * (b - a) / k, 0.0, 1.0);
  return mix(b, a, h) + k * h * (1.0 - h);
}

vec3 quatRotate(vec4 q, vec3 v) { return v + 2.0 * cross(q.xyz, cross(q.xyz, v) + q.w * v); }
vec3 quatRotateInv(vec4 q, vec3 v) { return quatRotate(vec4(-q.xyz, q.w), v); }
// Limited repetition: cell index clamped to [-count, count]; count 0 leaves that axis untouched.
vec3 opRepeat(vec3 p, vec3 period, vec3 count) {
  return p - period * clamp(round(p / period), -count, count);
}
// Twist about +Y by rate radians per meter.
vec3 opTwist(vec3 p, float rate) {
  float a = rate * p.y;
  float c = cos(a);
  float s = sin(a);
  return vec3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
}
// Bend in the XY plane by rate radians per meter along X.
vec3 opBend(vec3 p, float rate) {
  float a = rate * p.x;
  float c = cos(a);
  float s = sin(a);
  return vec3(c * p.x - s * p.y, s * p.x + c * p.y, p.z);
}
`;

/**
 * terrain-v1. Uniform block layout is filled by the renderer from TerrainDefinition + world seed.
 * `octaves` may be lower than uTerrainOctaves for distant samples: the normalisation always uses the
 * full octave count, so dropping octaves only removes fine detail and never shifts the large shape.
 */
export const TERRAIN_GLSL = /* glsl */ `
uniform vec4 uTerrainA;      // base_height, amplitude, frequency, ridge
uniform vec4 uTerrainB;      // warp, octaves, norm (sum of 0.5^i), water_level (or -1e9)
uniform vec4 uTerrainBasin;  // center.x, center.z, radius (0 = none), depth
uniform vec2 uSeedOffset;    // seed & 1023, (seed >> 10) & 1023

float terrainHeightLod(vec2 xz, int octaves) {
  vec2 p = xz;
  float warp = uTerrainB.x;
  if (warp > 0.0) {
    float wf = uTerrainA.z * 0.5;
    float wx = 2.0 * vnoise2(p * wf + vec2(31.7, 11.3), uSeedOffset) - 1.0;
    float wz = 2.0 * vnoise2(p * wf + vec2(7.9, 53.1), uSeedOffset) - 1.0;
    p += warp * vec2(wx, wz);
  }
  vec2 f = p * uTerrainA.z;
  float smoothSum = 0.0;
  float ridgedSum = 0.0;
  float amp = 1.0;
  float scale = 1.0;
  for (int i = 0; i < 8; i++) {
    if (i >= octaves) break;
    float n = vnoise2(f * scale + vec2(17.0 * float(i)), uSeedOffset);
    float s = 2.0 * n - 1.0;
    smoothSum += amp * s;
    ridgedSum += amp * (1.0 - 2.0 * abs(s));
    amp *= 0.5;
    scale *= 2.0;
  }
  float v = (smoothSum * (1.0 - uTerrainA.w) + ridgedSum * uTerrainA.w) / uTerrainB.z;
  float h = uTerrainA.x + uTerrainA.y * v;
  if (uTerrainBasin.z > 0.0) {
    float d = length(xz - uTerrainBasin.xy) / uTerrainBasin.z;
    h -= uTerrainBasin.w * (1.0 - smoothstep(0.0, 1.0, d));
  }
  return h;
}
float terrainHeight(vec2 xz) { return terrainHeightLod(xz, int(uTerrainB.y)); }
`;
