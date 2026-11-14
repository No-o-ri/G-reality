type V3 = [number, number, number];
const F = Math.fround;
const fract = (x: number) => F(x - Math.floor(x));
const add = (a: number, b: number) => F(a + b);
const mul = (a: number, b: number) => F(a * b);
const fade = (x: number) => mul(mul(mul(x, x), x), add(mul(x, add(mul(x, 6), -15)), 10));
const mix = (a: number, b: number, t: number) => add(a, mul(F(b - a), t));

/** Integer lattice hash, bit-identical to GLSL `hash12(ivec2)` and Python `hash12`. x, y are whole numbers. */
export function hash12(x: number, y: number): number {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h = (h ^ (h >>> 16)) >>> 0;
  return F((h >>> 8) / 16777216);
}
export function seedOffset(seed: number): [number, number] { return [seed & 1023, (seed >> 10) & 1023]; }
export function vnoise2(x: number, y: number, seedOffsetX: number, seedOffsetY: number): number {
  x = F(x); y = F(y);
  const ix = Math.floor(x), iy = Math.floor(y), ux = fade(F(x - ix)), uy = fade(F(y - iy));
  const lx = ix + seedOffsetX, ly = iy + seedOffsetY;
  return mix(mix(hash12(lx, ly), hash12(lx + 1, ly), ux), mix(hash12(lx, ly + 1), hash12(lx + 1, ly + 1), ux), uy);
}
export function hash13(p: V3): number {
  let a = fract(mul(F(p[0]), F(0.1031))), b = fract(mul(F(p[1]), F(0.1031))), c = fract(mul(F(p[2]), F(0.1031)));
  const d = add(add(mul(a, add(c, F(31.32))), mul(b, add(b, F(31.32)))), mul(c, add(a, F(31.32))));
  a = add(a, d); b = add(b, d); c = add(c, d);
  return fract(mul(add(a, b), c));
}
export function vnoise3(p: V3, seed: number): number {
  const i = p.map(v => Math.floor(F(v))), u = p.map((v, j) => fade(F(F(v) - i[j])));
  const h = (x: number, y: number, z: number) => hash13([add(add(i[0], mul(F(seed), 17)), x), add(add(i[1], mul(F(seed), 17)), y), add(add(i[2], mul(F(seed), 17)), z)]);
  return mix(mix(mix(h(0, 0, 0), h(1, 0, 0), u[0]), mix(h(0, 1, 0), h(1, 1, 0), u[0]), u[1]), mix(mix(h(0, 0, 1), h(1, 0, 1), u[0]), mix(h(0, 1, 1), h(1, 1, 1), u[0]), u[1]), u[2]);
}
export function fbm3(p: V3, octaves: number, seed: number): number {
  let sum = 0, amp = 1, scale = 1;
  for (let i = 0; i < Math.min(5, octaves); i++) {
    sum = add(sum, mul(amp, F(mul(2, vnoise3([mul(p[0], scale), mul(p[1], scale), mul(p[2], scale)], seed)) - 1)));
    amp = mul(amp, 0.5); scale = mul(scale, 2);
  }
  return sum;
}
export function warp3(p: V3, amplitude: number, frequency: number, seed: number): V3 {
  const q: V3 = [mul(p[0], frequency), mul(p[1], frequency), mul(p[2], frequency)];
  return [0, 19.1, 47.7].map((offset, j) => add(p[j], mul(amplitude, F(mul(2, vnoise3([add(q[0], F(offset)), add(q[1], F(offset)), add(q[2], F(offset))], seed)) - 1)))) as V3;
}
