// @ts-ignore -- Node supplies fs in Vitest; this browser package has no @types/node.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { TerrainDefinition } from '@contracts';
import { hash12, seedOffset, vnoise2 } from '../src/sdf/noise';
import { terrainHeight, terrainSlope } from '../src/sdf/terrain';

const golden = JSON.parse(readFileSync(new URL('../../../tests/golden/terrain_v1.json', import.meta.url), 'utf8')) as {
  cases: { terrain: TerrainDefinition; seed: number; points: [number, number][]; heights: number[]; slopes: number[] }[];
  hash12: { x: number; y: number; value: number }[];
  vnoise: { x: number; y: number; seed: number; value: number }[];
};
describe('terrain-v1 Python golden vectors', () => {
  it('matches every raw float32 hash and noise sample', () => {
    for (const v of golden.hash12) expect(Math.abs(hash12(v.x, v.y) - v.value)).toBeLessThanOrEqual(1e-6);
    for (const v of golden.vnoise) expect(Math.abs(vnoise2(v.x, v.y, ...seedOffset(v.seed)) - v.value)).toBeLessThanOrEqual(1e-6);
  });
  for (const [index, c] of golden.cases.entries()) it(`matches heights and central-difference slopes for case ${index}`, () => {
    c.points.forEach(([x, z], i) => {
      expect(Math.abs(terrainHeight(c.terrain, c.seed, x, z) - c.heights[i])).toBeLessThanOrEqual(1e-3);
      expect(Math.abs(terrainSlope(c.terrain, c.seed, x, z) - c.slopes[i])).toBeLessThanOrEqual(1e-3);
    });
  });
  it('uses contract defaults and leaves water out of the solid height', () => {
    const t = golden.cases[0].terrain;
    const minimal = { ground_material: t.ground_material, slope_material: t.slope_material };
    expect(terrainHeight(minimal, 1, 20, -30)).toBe(terrainHeight({ ...minimal, base_height: 0, amplitude: 30, frequency: 0.008, octaves: 5, ridge: 0, warp: 0 }, 1, 20, -30));
    expect(terrainHeight({ ...t, water_level: 400 }, 1, 20, -30)).toBe(terrainHeight(t, 1, 20, -30));
  });
});
