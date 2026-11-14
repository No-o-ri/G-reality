import type { TerrainDefinition } from '@contracts';
import { seedOffset, vnoise2 } from './noise';
const F = Math.fround;

export function terrainHeight(t: TerrainDefinition, seed: number, x: number, z: number): number {
  const frequency = t.frequency ?? 0.008, ridge = t.ridge ?? 0, warp = t.warp ?? 0;
  const [sx, sy] = seedOffset(seed);
  const noise = (a: number, b: number) => vnoise2(a, b, sx, sy);
  let px = F(x), pz = F(z);
  if (warp > 0) {
    const wf = F(frequency * 0.5);
    const wx = F(F(2 * noise(F(F(px * wf) + F(31.7)), F(F(pz * wf) + F(11.3)))) - 1);
    const wz = F(F(2 * noise(F(F(px * wf) + F(7.9)), F(F(pz * wf) + F(53.1)))) - 1);
    px = F(px + F(F(warp) * wx)); pz = F(pz + F(F(warp) * wz));
  }
  const fx = F(px * F(frequency)), fz = F(pz * F(frequency));
  let smooth = 0, ridged = 0, norm = 0, amp = 1, scale = 1;
  for (let i = 0; i < (t.octaves ?? 5); i++) {
    const n = noise(F(F(fx * scale) + F(17 * i)), F(F(fz * scale) + F(17 * i)));
    const s = F(F(2 * n) - 1);
    smooth = F(smooth + F(amp * s)); ridged = F(ridged + F(amp * F(1 - F(2 * Math.abs(s)))));
    norm = F(norm + amp); amp = F(amp * 0.5); scale = F(scale * 2);
  }
  const v = F(F(F(smooth * F(1 - ridge)) + F(ridged * F(ridge))) / norm);
  let h = F(F(t.base_height ?? 0) + F(F(t.amplitude ?? 30) * v));
  if (t.basin_center != null && (t.basin_radius ?? 0) > 0) {
    const d = Math.hypot(x - Number(t.basin_center[0]), z - Number(t.basin_center[1])) / t.basin_radius!;
    const u = Math.min(1, Math.max(0, d));
    h -= (t.basin_depth ?? 0) * (1 - u * u * (3 - 2 * u));
  }
  return h;
}
export function terrainSlope(t: TerrainDefinition, seed: number, x: number, z: number): number {
  const dx = terrainHeight(t, seed, x + 1, z) - terrainHeight(t, seed, x - 1, z);
  const dz = terrainHeight(t, seed, x, z + 1) - terrainHeight(t, seed, x, z - 1);
  return Math.hypot(dx, dz) * 0.5;
}
