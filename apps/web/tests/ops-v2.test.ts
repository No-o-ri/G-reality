import { describe, expect, it } from 'vitest';
import type { GeometryDefinition, GeometryNode } from '@contracts';
import { compileArchetype } from '../src/sdf/compiler';
import { compileCpu, stepScale, estimateBounds } from '../src/sdf/cpu';
import { SDF_GLSL } from '../src/sdf/glsl';
import { measureArchetype } from '../src/sdf/measure';

const sphere = { op: 'sphere', params: { radius: 0.25 } };
const translated = { op: 'translate', params: { offset: [2, 0, 0] }, children: [sphere] };
const def = (root: unknown): GeometryDefinition => ({
  id: 'ops-v2', version: 1, language_version: 1, root: root as GeometryNode,
  parameters: {
    count: { default: 6, min: 1, max: 64 }, offset: { default: 0, min: 0, max: 6.2832 },
    rate: { default: 0.5, min: -0.9, max: 0.9 }, factor: { default: 2, min: 0.05, max: 20 },
  },
  materials: [{ id: 'a', base_color: [0.5, 0.5, 0.5], roughness: 0.8 }], bound_radius: 5,
});
const radial = (count: unknown = 6, offset: unknown = 0) => ({ op: 'radial_repeat', params: { count, offset }, children: [translated] });

describe('additive domain operations', () => {
  it('repeats around Y, including negative angles, and leaves gaps between copies', () => {
    const fn = compileCpu(def(radial()));
    for (let k = -6; k < 6; k++) {
      const angle = k * Math.PI / 3;
      expect(fn([2 * Math.cos(angle), 0, 2 * Math.sin(angle)], {}).d).toBeCloseTo(-0.25, 8);
      expect(fn([2 * Math.cos(angle), 0.25, 2 * Math.sin(angle)], {}).d).toBeCloseTo(0, 8);
      expect(fn([2 * Math.cos(angle + Math.PI / 6), 0, 2 * Math.sin(angle + Math.PI / 6)], {}).d).toBeGreaterThan(0);
    }
  });

  it('rounds referenced counts and applies referenced offsets per instance', () => {
    const fn = compileCpu(def(radial({ ref: 'count' }, { ref: 'offset' })));
    const angle = -0.2 + 2 * Math.PI / 5;
    expect(fn([2 * Math.cos(angle), 0, 2 * Math.sin(angle)], { count: 5.4, offset: 0.2 }).d).toBeCloseTo(-0.25, 8);
    expect(fn([2, 0, 0], {}).d).toBeCloseTo(-0.25, 8);
  });

  it('widens a cylinder upward and clamps the taper cross-section', () => {
    const fn = compileCpu(def({ op: 'taper', params: { rate: { ref: 'rate' } }, children: [
      { op: 'cylinder', params: { radius: 1, half_height: 4 } },
    ] }));
    const radius = (y: number) => {
      let lo = 0, hi = 4;
      for (let i = 0; i < 40; i++) {
        const mid = (lo + hi) / 2;
        if (fn([mid, y, 0], {}).d < 0) lo = mid; else hi = mid;
      }
      return (lo + hi) / 2;
    };
    expect(radius(1) / radius(0)).toBeCloseTo(1.5, 8);
    expect(radius(-3)).toBeCloseTo(0.05, 8);
    expect(fn([0.5, 1, 0], { rate: -0.5 }).d).toBeCloseTo(0, 8);
  });

  it('stretches sphere surfaces and scales distance by the smallest component', () => {
    const fn = compileCpu(def({ op: 'stretch', params: { factor: [{ ref: 'factor' }, 1, 1] }, children: [
      { op: 'sphere', params: { radius: 1 } },
    ] }));
    expect(fn([2, 0, 0], {}).d).toBeCloseTo(0, 8);
    expect(fn([0, 1, 0], {}).d).toBeCloseTo(0, 8);
    expect(fn([0, 2, 0], { factor: 0.5 }).d).toBeCloseTo(0.5, 8);
    expect(fn([3, 0, 0], { factor: 3 }).d).toBeCloseTo(0, 8);
  });

  it('compiles all three operations with parameter references and matching step scales', () => {
    const d = def({ op: 'stretch', params: { factor: [{ ref: 'factor' }, 1, 1] }, children: [
      { op: 'taper', params: { rate: { ref: 'rate' } }, children: [radial({ ref: 'count' }, { ref: 'offset' })] },
    ] });
    const compiled = compileArchetype(d, 0, 0);
    expect(compiled.glsl).toContain('opRadial(');
    expect(compiled.glsl).toContain('opTaper(');
    expect(compiled.glsl).toContain('vec3(prm.w, 1.0, 1.0)');
    expect(SDF_GLSL).toContain('vec3 opRadial(');
    expect(SDF_GLSL).toContain('vec3 opTaper(');
    expect(compiled.stepScale).toBe(0.6);
    for (const [root, scale] of [[radial(), 0.9], [{ op: 'taper', params: { rate: 0.5 }, children: [sphere] }, 0.6],
      [{ op: 'stretch', params: { factor: [2, 1, 1] }, children: [sphere] }, 1]] as const) {
      const single = def(root);
      expect(stepScale(single)).toBe(scale);
      expect(compileArchetype(single, 0, 0).stepScale).toBe(scale);
    }
    const bounds = estimateBounds(d);
    expect(bounds.min.every((v, i) => Number.isFinite(v) && v < bounds.max[i])).toBe(true);
    expect(measureArchetype(d).sphereRadius).toBeGreaterThan(0);
  });
});
