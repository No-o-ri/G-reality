// @ts-ignore -- Node supplies fs in Vitest; this browser package has no @types/node.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { GeometryDefinition, GeometryNode } from '@contracts';
import { compileCpu, estimateBounds, stepScale, type Vec3 } from '../src/sdf/cpu';
import { fbm3, hash13, vnoise3, warp3 } from '../src/sdf/noise';

const node = (op: GeometryNode['op'], params: Record<string, unknown> = {}, children?: GeometryNode[], material?: string): GeometryNode => ({ op, params, children, material });
const sphere = (radius = 1, material?: string) => node('sphere', { radius }, undefined, material);
const def = (root: GeometryNode): GeometryDefinition => ({ id: 'test', bound_radius: 10, materials: [
  { id: 'a', base_color: [1, 1, 1] }, { id: 'b', base_color: [1, 1, 1] }, { id: 'c', base_color: [1, 1, 1] },
], root });
const evaluate = (root: GeometryNode, p: Vec3, params = {}) => compileCpu(def(root))(p, params);
const distance = (root: GeometryNode, p: Vec3) => evaluate(root, p).d;
const near = (actual: number, expected: number) => expect(actual).toBeCloseTo(expected, 9);

describe('analytic primitives (inside, surface, outside)', () => {
  const cases: [GeometryNode, [Vec3, number][]][] = [
    [sphere(2), [[[0, 0, 0], -2], [[2, 0, 0], 0], [[0, 0, 5], 3]]],
    [node('ellipsoid', { radii: [2, 3, 4] }), [[[1, 0, 0], -1], [[0, 3, 0], 0], [[0, 0, 6], 2], [[0, 0, 0], 0]]],
    [node('box', { half: [1, 2, 3] }), [[[0, 0, 0], -1], [[0, 2, 0], 0], [[2, 3, 4], Math.sqrt(3)]]],
    [node('round_box', { half: [1, 2, 3], radius: 0.5 }), [[[0, 0, 0], -1.5], [[1.5, 0, 0], 0], [[2, 0, 0], 0.5]]],
    [node('capsule', { a: [0, -1, 0], b: [0, 1, 0], radius: 0.5 }), [[[0, 0, 0], -0.5], [[0, 1.5, 0], 0], [[0, 3, 0], 1.5]]],
    [node('cylinder', { half_height: 2, radius: 1 }), [[[0, 0, 0], -1], [[1, 1, 0], 0], [[2, 3, 0], Math.sqrt(2)]]],
    [node('cone', { half_height: 2, radius_bottom: 2, radius_top: 0 }), [[[0, 0, 0], -2 / Math.sqrt(5)], [[1, 0, 0], 0], [[0, 3, 0], 1], [[2, -2, 0], 0]]],
    [node('torus', { major: 2, minor: 0.5 }), [[[2, 0, 0], -0.5], [[2, 0.5, 0], 0], [[0, 0, 0], 1.5]]],
    [node('plane', { normal: [0, 0.5, 0], offset: 2 }), [[[0, 0, 0], -2], [[0, 2, 0], 0], [[1, 5, 3], 3]]],
  ];
  for (const [shape, points] of cases) it(shape.op, () => { for (const [p, expected] of points) near(distance(shape, p), expected); });
  it('handles a zero-length capsule', () => near(distance(node('capsule', { a: [0, 0, 0], b: [0, 0, 0], radius: 1 }), [3, 0, 0]), 2));
});

describe('CSG and material rules', () => {
  it('satisfies union/intersect identity and subtraction signs', () => {
    for (const p of [[0, 0, 0], [1, 0, 0], [2, 3, -1]] as Vec3[]) {
      for (const op of ['union', 'intersect'] as const) near(distance(node(op, {}, [sphere(), sphere()]), p), distance(sphere(), p));
      near(distance(node('subtract', {}, [sphere(), sphere()]), p), Math.abs(distance(sphere(), p)));
    }
    const cut = node('subtract', {}, [sphere(2), sphere(1)]);
    expect(distance(cut, [0, 0, 0])).toBeGreaterThan(0);
    expect(distance(cut, [1.5, 0, 0])).toBeLessThan(0);
  });
  it('implements all smooth formulas and left folds', () => {
    near(distance(node('smooth_union', { k: 0.4 }, [sphere(), sphere()]), [2, 0, 0]), 0.9);
    near(distance(node('smooth_intersect', { k: 0.4 }, [sphere(), sphere()]), [2, 0, 0]), 1.1);
    near(distance(node('smooth_subtract', { k: 0.4 }, [sphere(), sphere()]), [1, 0, 0]), 0.1);
    for (const op of ['union', 'intersect', 'smooth_union'] as const) {
      const a = sphere(1), b = sphere(1.2), c = sphere(0.8), p: Vec3 = [1.5, 0, 0];
      near(distance(node(op, { k: 1 }, [a, b, c]), p), distance(node(op, { k: 1 }, [node(op, { k: 1 }, [a, b]), c]), p));
    }
  });
  it('inherits materials and lets descendants override them', () => {
    expect(evaluate(node('translate', { offset: [0, 0, 0] }, [sphere()], 'b'), [0, 0, 0]).material).toBe(1);
    expect(evaluate(node('round', { radius: 0.1 }, [sphere(1, 'c')], 'b'), [0, 0, 0]).material).toBe(2);
    expect(evaluate(sphere(), [0, 0, 0]).material).toBe(0);
    for (const op of ['union', 'smooth_union', 'intersect', 'smooth_intersect', 'subtract', 'smooth_subtract'] as const) {
      const result = evaluate(node(op, { k: 1 }, [sphere(1, 'b'), sphere(2, 'c')], 'a'), [3, 0, 0]);
      expect(result.material).toBe(op.endsWith('union') ? 2 : 1);
    }
    // The third raw child is farther than child two, but nearer than the smoothed fold.
    expect(evaluate(node('smooth_union', { k: 4 }, [sphere(1, 'a'), sphere(1.1, 'b'), sphere(1.05, 'c')]), [3, 0, 0]).material).toBe(1);
  });
  it('returns independently retainable public results', () => {
    const f = compileCpu(def(sphere())); const a = f([0, 0, 0], {}); f([3, 0, 0], {}); expect(a.d).toBe(-1);
  });
});

describe('domain and field operations', () => {
  const shape = node('box', { half: [1, 2, 3] });
  it('preserves translation and normalized quaternion rotation; scales distances', () => {
    const p: Vec3 = [1.5, 0.7, -0.2], d = distance(shape, p);
    near(distance(node('translate', { offset: [3, -2, 1] }, [shape]), [p[0] + 3, p[1] - 2, p[2] + 1]), d);
    near(distance(node('rotate', { quat: [0, 0, Math.SQRT2, Math.SQRT2] }, [shape]), [-p[1], p[0], p[2]]), d);
    near(distance(node('scale', { factor: 2 }, [shape]), [p[0] * 2, p[1] * 2, p[2] * 2]), 2 * d);
  });
  it('mirrors every axis and repeats with limited cells and disabled axes', () => {
    for (const axis of [0, 1, 2]) {
      const mirrored = node('mirror', { axis }, [node('translate', { offset: [1, 2, 3] }, [sphere()])]);
      const p: Vec3 = [1.2, 2.3, 3.4], q: Vec3 = [...p]; q[axis] *= -1;
      near(distance(mirrored, p), distance(mirrored, q));
    }
    const repeated = node('repeat', { period: [4, 4, 4], count: [1, 0, 2] }, [sphere()]);
    for (const x of [-4, 0, 4]) near(distance(repeated, [x + 0.2, 0, 0]), -0.8);
    near(distance(repeated, [8, 0, 0]), 3); near(distance(repeated, [0, 4, 0]), 3);
    near(distance(repeated, [0, 0, -8]), -1);
  });
  it('uses the GLSL twist and bend signs and axes', () => {
    const p: Vec3 = [1.3, 0.8, -0.4], rate = 0.7;
    for (const op of ['twist', 'bend'] as const) {
      const a = rate * p[op === 'twist' ? 1 : 0], c = Math.cos(a), s = Math.sin(a);
      const q: Vec3 = op === 'twist' ? [c * p[0] - s * p[2], p[1], s * p[0] + c * p[2]] : [c * p[0] - s * p[1], s * p[0] + c * p[1], p[2]];
      near(distance(node(op, { rate }, [shape]), p), distance(shape, q));
    }
  });
  it('evaluates round, shell, warp and multi-octave displacement', () => {
    const p: Vec3 = [0.3, 0.5, -0.7];
    near(distance(node('round', { radius: 0.2 }, [shape]), p), distance(shape, p) - 0.2);
    near(distance(node('shell', { thickness: 0.2 }, [shape]), p), Math.abs(distance(shape, p)) - 0.2);
    near(distance(node('warp', { amplitude: 0.4, frequency: 1.5, seed: 7 }, [shape]), p), distance(shape, warp3(p, 0.4, 1.5, 7)));
    near(distance(node('displace', { amplitude: 0.4, frequency: 1.5, octaves: 3, seed: 7 }, [shape]), p), distance(shape, p) + 0.4 * fbm3(p.map(x => x * 1.5) as Vec3, 3, 7));
  });
  it('resolves scalar and per-component refs with defaults on each evaluation', () => {
    const d = def(node('translate', { offset: [{ ref: 'x' }, 0, 0] }, [node('sphere', { radius: { ref: 'r' } })]));
    d.parameters = { x: { min: -2, default: 1, max: 2 }, r: { min: 0.1, default: 2, max: 3 } };
    const f = compileCpu(d);
    near(f([1, 0, 0], {}).d, -2); near(f([1, 0, 0], { x: 2, r: 0.5 }).d, 0.5); near(f([1, 0, 0], {}).d, -2);
  });
});

describe('noise invariants', () => {
  it('interpolates lattice points and uses the specified seed offset', () => {
    for (const p of [[0, 0, 0], [-3, 4, 10], [20, -7, -9]] as Vec3[]) {
      expect(vnoise3(p, 2)).toBe(hash13(p.map(v => v + 34) as Vec3));
      expect(vnoise3(p, 2)).toBeGreaterThanOrEqual(0); expect(vnoise3(p, 2)).toBeLessThan(1);
    }
    expect(fbm3([1, 2, 3], 0, 1)).toBe(0);
    expect(warp3([1, 2, 3], 0, 1, 1)).toEqual([1, 2, 3]);
  });
});

describe('step scales and sampled bounds', () => {
  it('uses the minimum scale over the entire tree', () => {
    expect(stepScale(def(sphere()))).toBe(1);
    expect(stepScale(def(node('ellipsoid', { radii: [1, 1, 1] })))).toBe(0.8);
    for (const op of ['warp', 'displace', 'twist', 'bend'] as const) expect(stepScale(def(node('union', {}, [sphere(), node(op, {}, [sphere()])])))).toBe(op === 'twist' || op === 'bend' ? 0.6 : 0.7);
  });
  it('uses parameter maxima, pads cells, clamps and falls back to the full cube', () => {
    const d = def(node('sphere', { radius: { ref: 'r' } })); d.parameters = { r: { min: 0.1, default: 1, max: 3 } };
    const b = estimateBounds(d); expect(b.min[0]).toBeLessThanOrEqual(-3); expect(b.max[0]).toBeGreaterThanOrEqual(3);
    expect(estimateBounds(d, { r: 1 }).max[0]).toBeLessThan(b.max[0]);
    expect(estimateBounds(def(node('translate', { offset: [100, 0, 0] }, [sphere()])))).toEqual({ min: [-10, -10, -10], max: [10, 10, 10] });
  });
  const archetypes = JSON.parse(readFileSync(new URL('../../../tests/golden/archetypes.json', import.meta.url), 'utf8')) as GeometryDefinition[];
  for (const d of archetypes) it(`contains deterministic interior samples of ${d.id}`, () => {
    let state = 1234567;
    const rand = () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 4294967296; };
    const bounds = estimateBounds(d), f = compileCpu(d);
    let inside = 0;
    for (const useMax of [false, true]) {
      const params = Object.fromEntries(Object.entries(d.parameters ?? {}).map(([k, v]) => [k, useMax ? v.max : v.default]));
      for (let n = 0; n < 10000; n++) {
        const p: Vec3 = [(2 * rand() - 1) * d.bound_radius, (2 * rand() - 1) * d.bound_radius, (2 * rand() - 1) * d.bound_radius];
        if (f(p, params).d < 0) { inside++; for (let i = 0; i < 3; i++) { expect(p[i]).toBeGreaterThanOrEqual(bounds.min[i]); expect(p[i]).toBeLessThanOrEqual(bounds.max[i]); } }
      }
    }
    expect(inside).toBeGreaterThan(0);
  });
});
